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
from .text_quality import looks_like_garbled

TIME_SEPARATOR = re.compile(
    r"^(?:(?:昨天|今天|前天|星期[一二三四五六日天]|周[一二三四五六日天])\s*)?"
    r"(?:\d{1,2}[:：]\d{2}"
    r"|\d{1,2}月\d{1,2}日(?:\s*\d{1,2}[:：]\d{2})?"
    r"|(?:\d{4}[-/年])?\d{1,2}[-/月]\d{1,2}日?)$"
)
GROUP_NAME = re.compile(r"[（(]\s*\d+\s*[)）]")


def is_group_name(name: str) -> bool:
    return bool(GROUP_NAME.search(name or ""))
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


@dataclass(frozen=True)
class ChatRegion:
    """聊天面板在截图中的像素范围。"""

    chat_left: int
    header_bottom: int
    input_top: int

    @property
    def valid(self) -> bool:
        return self.chat_left > 0 and self.input_top > self.header_bottom


def compute_region(
    width: int, height: int, dpi_scale: float, cfg: CaptureConfig
) -> ChatRegion:
    """按「固定像素 × DPI 缩放」计算聊天面板；DPI 未知时退回比例。"""
    if dpi_scale and dpi_scale > 0:
        chat_left = int(cfg.chat_left_px * dpi_scale)
        header = int(cfg.header_px * dpi_scale)
        input_height = int(cfg.input_px * dpi_scale)
    else:
        chat_left = int(width * cfg.chat_left_ratio)
        header = int(height * cfg.header_bottom_ratio)
        input_height = int(height * (1.0 - cfg.input_top_ratio))
    chat_left = _clamp(chat_left, int(width * 0.12), int(width * 0.60))
    header = _clamp(header, int(height * 0.04), int(height * 0.16))
    input_height = _clamp(input_height, int(height * 0.08), int(height * 0.35))
    input_top = max(header + 10, height - input_height)
    return ChatRegion(chat_left=chat_left, header_bottom=header, input_top=input_top)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


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

    def parse(
        self, lines: list[OcrLine], image: np.ndarray, region: ChatRegion | None = None
    ) -> ParseResult:
        height, width = image.shape[:2]
        region = region or compute_region(width, height, 0.0, self.cfg)
        if not region.valid:
            return ParseResult(session="", bubbles=[])
        chat_left = region.chat_left
        header_bottom = region.header_bottom
        input_top = region.input_top

        session = self._extract_session(lines, chat_left, header_bottom, width)
        body = [
            ln
            for ln in lines
            if header_bottom <= ln.cy <= input_top and ln.x0 >= chat_left - 4
        ]
        body = [ln for ln in body if not self._is_noise(ln.text)]
        # 图片/表情里的字太小，OCR 会读成 "TE088n m877a" 这类乱码：
        # 必须整行丢掉，否则会被当成对方的原话发进模型
        body = [ln for ln in body if not self._is_garbled(ln.text, ln.score)]
        if not body:
            return ParseResult(session=session, bubbles=[], dropped=len(lines))

        boxes = self._detect_boxes(image, chat_left, header_bottom, input_top)
        groups: list[tuple[BubbleBox | None, list[OcrLine]]] = []
        leftovers: list[OcrLine] = []
        if boxes:
            groups, leftovers = self._assign_to_boxes(body, boxes)
        if not any(g[0] is not None for g in groups):
            groups = [(box, ln_list) for box, ln_list in self._fallback_groups(body)]

        group_chat = is_group_name(session)
        sender_map: dict[int, str] = {}
        if group_chat and groups and leftovers:
            sender_map, leftovers = self._extract_senders(leftovers, groups)
        if leftovers:
            for _, ln_list in self._fallback_groups(leftovers):
                groups.append((None, ln_list))

        bubbles: list[Bubble] = []
        for box, ln_list in groups:
            sender = sender_map.get(id(box), "") if box is not None else ""
            if group_chat and not sender:
                leading, remaining = self._split_leading_sender(ln_list)
                if leading:
                    sender = leading
                    ln_list = remaining
            text = self._join_lines(ln_list)
            if not text:
                continue
            # 逐行过滤后仍可能漏网（每行只有一个机器串），合并后再判一次
            if self._is_garbled(text, _mean_score(ln_list)):
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
                Bubble(
                    text=text,
                    is_me=is_me,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    lines=ln_list,
                    sender=sender,
                )
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
        rgb = region.astype(np.int16)
        # 用中位色估计聊天背景；气泡和背景的差值（浅色/深色主题都适用）
        background = np.median(rgb.reshape(-1, 3), axis=0)
        diff = np.abs(rgb - background).max(axis=2)
        mask = (diff > 8).astype(np.uint8)
        # 填掉气泡里的文字笔画，再清掉噪点
        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)

        r = rgb[:, :, 0]
        g = rgb[:, :, 1]
        b = rgb[:, :, 2]
        green_mask = (g > 120) & (g - r > 15) & (g - b > 15)

        boxes: list[BubbleBox] = []
        region_h, region_w = region.shape[:2]
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w < 50 or h < 18:
                continue
            if w > region_w * 0.92 or h > region_h * 0.55:
                continue
            if w * h < 900:
                continue
            # 头像/表情图：接近正方形且尺寸偏小，跳过（里面没有聊天气泡文字）
            if w < 90 and h < 90 and abs(w - h) < 12:
                continue
            cell = green_mask[y : y + h, x : x + w]
            is_me = bool(cell.size and cell.mean() > 0.15)
            boxes.append(
                BubbleBox(
                    x0=x + chat_left,
                    y0=y + top,
                    x1=x + w + chat_left,
                    y1=y + h + top,
                    is_me=is_me,
                )
            )
        boxes.sort(key=lambda bx: bx.width * bx.height, reverse=True)
        kept: list[BubbleBox] = []
        for bx in boxes:
            if any(_overlap_ratio(bx, k) > 0.6 for k in kept):
                continue
            kept.append(bx)
        return kept

    def _assign_to_boxes(
        self, lines: list[OcrLine], boxes: list[BubbleBox]
    ) -> tuple[list[tuple[BubbleBox | None, list[OcrLine]]], list[OcrLine]]:
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
        return result, leftovers

    def _split_leading_sender(
        self, lines: list[OcrLine]
    ) -> tuple[str, list[OcrLine]]:
        """群聊里名字行被并进气泡时，把开头那行短名字拆出来。"""
        ordered = sorted(lines, key=lambda ln: (ln.y0, ln.x0))
        if len(ordered) < 2:
            return "", lines
        first, second = ordered[0], ordered[1]
        text = first.text.strip()
        if not text or len(text) > 12:
            return "", lines
        if any(ch in text for ch in "。！？!?，,、；;：:"):
            return "", lines
        if TIME_SEPARATOR.match(text):
            return "", lines
        # 名字行通常比正文左一点，且比正文行高更小
        if first.x0 > second.x0 - 4:
            return "", lines
        heights = sorted(line.height for line in ordered)
        median = heights[len(heights) // 2] if heights else first.height
        if first.height > median * 1.15:
            return "", lines
        return text, ordered[1:]

    def _extract_senders(
        self,
        leftovers: list[OcrLine],
        groups: list[tuple[BubbleBox | None, list[OcrLine]]],
    ) -> tuple[dict[int, str], list[OcrLine]]:
        """群聊：把气泡上方那行短文字识别成发言人。"""
        heights = sorted(line.height for _box, lines in groups for line in lines)
        median = heights[len(heights) // 2] if heights else 20
        boxes = [(id(box), box) for box, _lines in groups if box is not None]
        senders: dict[int, str] = {}
        consumed: set[int] = set()
        for line in sorted(leftovers, key=lambda item: item.y0):
            text = line.text.strip()
            if not text or len(text) > 16:
                continue
            if TIME_SEPARATOR.match(text) or self._is_noise(text):
                continue
            if line.height > median * 1.3:
                continue
            best_key = None
            best_gap = 10**9
            for key, box in boxes:
                if key in senders:
                    continue
                if not (box.x0 - 16 <= line.x0 <= box.x0 + 40):
                    continue
                gap = box.y0 - line.y1
                if 0 <= gap <= max(18, int(median * 2.0)) and gap < best_gap:
                    best_key, best_gap = key, gap
            if best_key is not None:
                senders[best_key] = text
                consumed.add(id(line))
        remaining = [line for line in leftovers if id(line) not in consumed]
        return senders, remaining

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

    def _is_garbled(self, text: str, score: float | None = None) -> bool:
        """图片/表情里被硬读出来的乱码：不能当聊天内容。"""
        if not self.cfg.filter_garbled:
            return False
        return looks_like_garbled(text, score)

    @staticmethod
    def _join_lines(lines: list[OcrLine]) -> str:
        ordered = sorted(lines, key=lambda ln: (ln.y0, ln.x0))
        parts = [ln.text.strip() for ln in ordered if ln.text.strip()]
        text = "\n".join(parts)
        return re.sub(r"[ \t]+", " ", text).strip()


def _mean_score(lines: list[OcrLine]) -> float:
    if not lines:
        return 1.0
    return sum(line.score for line in lines) / len(lines)


def _overlap_ratio(a: BubbleBox, b: BubbleBox) -> float:
    ix0, iy0 = max(a.x0, b.x0), max(a.y0, b.y0)
    ix1, iy1 = min(a.x1, b.x1), min(a.y1, b.y1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    return inter / float(min(a.width * a.height, b.width * b.height))
