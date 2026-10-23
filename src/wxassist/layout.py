"""把 OCR 行还原成「会话名 + 消息气泡」。

策略：
1. 用颜色分割找气泡（己方微信绿 / 对方白），把落在气泡里的 OCR 行归组；
2. 找不到气泡时退回按行间距 + 左/右对齐分组（深色模式、自定义主题等）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import CaptureConfig
from .models import Bubble, OcrLine

TIME_SEPARATOR = re.compile(
    r"^(?:(?:昨天|今天|前天|星期[一二三四五六日天]|周[一二三四五六日天])?\s*)?"
    r"(?:\d{1,2}[:：]\d{2}|\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)$"
)
SYSTEM_HINTS = (
    "以下为新消息",
    "你已添加",
    "撤回了一条消息",
    "消息已发出",
    "对方正在输入",
    "查看更多消息",
    "以上是打招呼",
    "开启了朋友验证",
    "你们已经是好友",
    "该消息已被用户删除",
    "群公告",
)


@dataclass
class BubbleBox:
    x0: int
    y0: int
    x1: int
    y1: int
    is_me: bool
    lines: list[OcrLine] = field(default_factory=list)

    @property
    def width(self) -> int:
        return max(1, self.x1 - self.x0)

    @property
    def height(self) -> int:
        return max(1, self.y1 - self.y0)


@dataclass
class ParseResult:
    session: str
    bubbles: list[Bubble]
    dropped: int = 0

    @property
    def digest(self) -> str:
        return "|".join(b.digest() for b in self.bubbles)


class BubbleParser:
    def __init__(self, cfg: CaptureConfig) -> None:
        self.cfg = cfg

    # ---------- 对外入口 ----------

    def parse(self, lines: list[OcrLine], image: np.ndarray) -> ParseResult:
        height, width = image.shape[:2]
        chat_left = int(width * self.cfg.chat_left_ratio)
        header_bottom = int(height * self.cfg.header_bottom_ratio)
        input_top = int(height * self.cfg.input_top_ratio)
        if input_top <= header_bottom:
            return ParseResult(session="", bubbles=[])

        session = self._extract_session(lines, chat_left, header_bottom, width)
        body = [
            ln
            for ln in lines
            if header_bottom <= ln.cy <= input_top and ln.x0 >= chat_left - 4
        ]
        body = [ln for ln in body if not self._is_noise(ln.text)]
        if not body:
            return ParseResult(session=session, bubbles=[], dropped=len(lines))

        boxes = self._detect_boxes(image, chat_left, header_bottom, input_top)
        groups: list[tuple[BubbleBox | None, list[OcrLine]]] = []
        if boxes:
            groups = self._assign_to_boxes(body, boxes)
        if not any(g[0] is not None for g in groups):
            groups = [(box, ln_list) for box, ln_list in self._fallback_groups(body)]

        bubbles: list[Bubble] = []
        for box, ln_list in groups:
            text = self._join_lines(ln_list)
            if not text:
                continue
            if box is not None:
                is_me = box.is_me
                x0, y0, x1, y1 = box.x0, box.y0, box.x1, box.y1
            else:
                is_me = self._is_me_by_alignment(ln_list, chat_left, width)
                x0 = min(ln.x0 for ln in ln_list)
                y0 = min(ln.y0 for ln in ln_list)
                x1 = max(ln.x1 for ln in ln_list)
                y1 = max(ln.y1 for ln in ln_list)
            bubbles.append(
                Bubble(text=text, is_me=is_me, x0=x0, y0=y0, x1=x1, y1=y1, lines=ln_list)
            )
        bubbles.sort(key=lambda b: b.y0)
        return ParseResult(session=session, bubbles=bubbles, dropped=0)

    # ---------- 会话名 ----------

    def _extract_session(
        self, lines: list[OcrLine], chat_left: int, header_bottom: int, width: int
    ) -> str:
        head = [
            ln
            for ln in lines
            if ln.cy < header_bottom and ln.x0 >= chat_left and ln.x1 <= width
        ]
        if not head:
            return ""
        head.sort(key=lambda ln: (ln.y0, ln.x0))
        # 标题通常字号最大：取最高的一行，太短则并入相邻行
        best = max(head, key=lambda ln: ln.height)
        parts = [ln.text for ln in head if abs(ln.cy - best.cy) <= best.height]
        name = " ".join(p.strip() for p in parts if p.strip())
        name = re.sub(r"\s+", " ", name).strip()
        if name in {"微信", "WeChat"}:
            return ""
        return name[:40]

    # ---------- 气泡检测 ----------

    def _detect_boxes(
        self, image: np.ndarray, chat_left: int, top: int, bottom: int
    ) -> list[BubbleBox]:
        region = image[top:bottom, chat_left:]
        if region.size == 0:
            return []
        rgb = region
        r = rgb[:, :, 0].astype(np.int16)
        g = rgb[:, :, 1].astype(np.int16)
        b = rgb[:, :, 2].astype(np.int16)

        # 微信己方气泡：绿色系（浅绿 #95EC69 / 深绿 #07C160 / 深色模式下的暗绿）
        green = ((g > 120) & (g - r > 18) & (g - b > 18)).astype(np.uint8)
        # 对方气泡：纯白（聊天背景是浅灰，白气泡更亮）
        white = ((r > 246) & (g > 246) & (b > 246)).astype(np.uint8)

        boxes: list[BubbleBox] = []
        region_h, region_w = region.shape[:2]
        for mask, is_me in ((green, True), (white, False)):
            if int(mask.sum()) < 160:
                continue
            kernel = np.ones((3, 3), np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                x, y, w, h = cv2.boundingRect(cnt)
                if w < 24 or h < 16:
                    continue
                if w > region_w * 0.92 or h > region_h * 0.5:
                    continue
                if w * h < 500:
                    continue
                boxes.append(
                    BubbleBox(
                        x0=x + chat_left,
                        y0=y + top,
                        x1=x + w + chat_left,
                        y1=y + h + top,
                        is_me=is_me,
                    )
                )
        # 去掉互相包含的小框（同一气泡被两个 mask 各抓一次时保留较大的）
        boxes.sort(key=lambda bx: bx.width * bx.height, reverse=True)
        kept: list[BubbleBox] = []
        for bx in boxes:
            if any(_overlap_ratio(bx, k) > 0.6 for k in kept):
                continue
            kept.append(bx)
        return kept

    def _assign_to_boxes(
        self, lines: list[OcrLine], boxes: list[BubbleBox]
    ) -> list[tuple[BubbleBox | None, list[OcrLine]]]:
        groups: dict[int, list[OcrLine]] = {i: [] for i in range(len(boxes))}
        leftovers: list[OcrLine] = []
        for ln in lines:
            hit = None
            for idx, box in enumerate(boxes):
                if (
                    box.x0 - 6 <= ln.cx <= box.x1 + 6
                    and box.y0 - 4 <= ln.cy <= box.y1 + 4
                ):
                    hit = idx
                    break
            if hit is None:
                leftovers.append(ln)
            else:
                groups[hit].append(ln)

        result: list[tuple[BubbleBox | None, list[OcrLine]]] = []
        for idx, box in enumerate(boxes):
            if groups[idx]:
                result.append((box, groups[idx]))
        # 没被任何气泡覆盖的行，退回行间距分组
        if leftovers:
            for _, ln_list in self._fallback_groups(leftovers):
                result.append((None, ln_list))
        return result

    def _fallback_groups(self, lines: list[OcrLine]) -> list[tuple[None, list[OcrLine]]]:
        ordered = sorted(lines, key=lambda ln: (ln.y0, ln.x0))
        heights = sorted(ln.height for ln in ordered)
        median_h = heights[len(heights) // 2] if heights else 20
        gap_threshold = max(self.cfg.min_line_gap_px, int(median_h * 0.55))
        groups: list[list[OcrLine]] = []
        for ln in ordered:
            if not groups:
                groups.append([ln])
                continue
            prev = groups[-1][-1]
            gap = ln.y0 - prev.y1
            indent = abs(ln.x0 - groups[-1][0].x0)
            if gap > gap_threshold or (gap > 2 and indent > median_h * 1.2):
                groups.append([ln])
            else:
                groups[-1].append(ln)
        return [(None, g) for g in groups]

    # ---------- 分类 / 文本 ----------

    def _is_me_by_alignment(self, lines: list[OcrLine], chat_left: int, width: int) -> bool:
        chat_w = max(1, width - chat_left)
        right_gap = min(abs(width - ln.x1) for ln in lines)
        left_gap = min(abs(ln.x0 - chat_left) for ln in lines)
        if right_gap < chat_w * 0.06:
            return True
        if left_gap < chat_w * 0.06:
            return False
        cx = sum(ln.cx for ln in lines) / len(lines)
        return cx > chat_left + chat_w * self.cfg.me_x_ratio

    @staticmethod
    def _is_noise(text: str) -> bool:
        t = text.strip()
        if not t:
            return True
        if TIME_SEPARATOR.match(t):
            return True
        return any(hint in t for hint in SYSTEM_HINTS)

    @staticmethod
    def _join_lines(lines: list[OcrLine]) -> str:
        ordered = sorted(lines, key=lambda ln: (ln.y0, ln.x0))
        parts = [ln.text.strip() for ln in ordered if ln.text.strip()]
        text = "\n".join(parts)
        return re.sub(r"[ \t]+", " ", text).strip()


def _overlap_ratio(a: BubbleBox, b: BubbleBox) -> float:
    ix0, iy0 = max(a.x0, b.x0), max(a.y0, b.y0)
    ix1, iy1 = min(a.x1, b.x1), min(a.y1, b.y1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    return inter / float(min(a.width * a.height, b.width * b.height))
