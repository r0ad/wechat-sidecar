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
from .text_quality import (
    looks_like_garbled,
    looks_like_non_dialogue,
    looks_like_non_dialogue_line,
    quote_echo_content,
)

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
    "以下是新消息",
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
    "拍了拍",
    "加入了群聊",
    "加入群聊",
    "退出了群聊",
    "修改群名为",
    "现在可以开始聊天了",
    "提到了你",
    "条新消息",
    "你撤回了",
)
# 系统提示的变体（破折号/换行会让子串匹配失效）
SYSTEM_PATTERNS = (
    re.compile(r"撤回了.{0,4}条消息"),
    re.compile(r"^你已添加了"),
    re.compile(r"^以下(?:是|为)新消息$"),
    re.compile(r"^\d+\s*条新消息$"),
)
# 悬浮窗/工具栏自己的文字：旧版 GDI 兜底截到悬浮窗时会被当成消息
UI_HINTS = (
    "对方最新消息",
    "回复候选",
    "立即识别",
    "暂停采集",
    "读取更多",
    "换一批",
    "总结会话",
    "学我说话",
    "生成候选",
)


@dataclass(frozen=True)
class ChatRegion:
    """聊天面板在截图中的像素范围。"""

    chat_left: int
    header_bottom: int
    input_top: int
    # 聊天面板右边界；0 表示未知（按截图宽度）。微信分屏打开公众号文章时，
    # 右侧文章面板不是聊天区，必须裁掉，否则文章正文会被当成消息。
    chat_right: int = 0

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
        # 分屏打开公众号文章时，右侧文章面板不是聊天区；只解析聊天面板。
        # 配置里没给右边界时按截图自己找分屏分隔条（找不到就当整宽）。
        right = region.chat_right if region.chat_right > chat_left else width
        if right >= width:
            detected = self._detect_chat_right(
                image, chat_left, header_bottom, input_top, lines
            )
            if detected:
                right = detected

        session = self._extract_session(lines, chat_left, header_bottom, right)
        body = [
            ln
            for ln in lines
            if header_bottom <= ln.cy <= input_top
            and ln.x0 >= chat_left - 4
            and ln.x1 <= right + 6
        ]
        body = [ln for ln in body if not self._is_noise(ln.text)]
        # 语音时长（6"）、[图片] 占位符这类行不是聊天内容，先逐行丢掉
        if self.cfg.filter_non_dialogue:
            body = [ln for ln in body if not looks_like_non_dialogue_line(ln.text)]
        # 图片/表情里的字太小，OCR 会读成 "TE088n m877a" 这类乱码：
        # 必须整行丢掉，否则会被当成对方的原话发进模型
        body = [ln for ln in body if not self._is_garbled(ln.text, ln.score)]
        if not body:
            return ParseResult(session=session, bubbles=[], dropped=len(lines))
        # 正文行高基准要在丢弃图片/大图之前算：图片里的字更小，会把基准拉低
        font_h = _median_height(body)
        panel_slice = image[header_bottom:input_top, chat_left:right]
        panel_bg = float(np.median(panel_slice.mean(axis=2))) if panel_slice.size else 0.0
        # 长截图/大图消息：轮廓高度超过普通气泡，_detect_boxes 会直接丢掉，
        # 但框里的文字行还在；不在这里整体丢弃，它们会逐行进 leftovers 变消息。
        dropped_media: list[BubbleBox] = []
        if self.cfg.filter_media:
            body, dropped_media = self._drop_large_media(
                body, image, chat_left, header_bottom, input_top, right
            )
            if not body:
                return ParseResult(session=session, bubbles=[], dropped=len(lines))

        boxes = self._detect_boxes(image, chat_left, header_bottom, input_top, right)
        groups: list[tuple[BubbleBox | None, list[OcrLine]]] = []
        leftovers: list[OcrLine] = []
        if boxes:
            groups, leftovers = self._assign_to_boxes(body, boxes)
        if not any(g[0] is not None for g in groups):
            groups = [(box, ln_list) for box, ln_list in self._fallback_groups(body)]

        # 群聊发言人要在大图/卡片被丢掉之前认出来：名字行通常贴在图片框上方，
        # 等图片框先被丢掉，那行名字就成了孤儿消息。
        group_chat = is_group_name(session)
        sender_map: dict[int, str] = {}
        if group_chat and groups and leftovers:
            extra = [(box, []) for box in dropped_media]
            sender_map, leftovers = self._extract_senders(leftovers, groups + extra)

        # 图片/卡片消息：整框的文字都不是对方原话，直接丢弃；
        # 框边缘漏出来的行也不能进 leftovers（否则会变成 fallback 消息）。
        if self.cfg.filter_media and boxes and groups:
            kept_groups: list[tuple[BubbleBox | None, list[OcrLine]]] = []
            media_boxes: list[BubbleBox] = []
            for box, ln_list in groups:
                if box is not None and self._looks_like_media(box, ln_list, image, font_h):
                    media_boxes.append(box)
                    continue
                kept_groups.append((box, ln_list))
            if media_boxes:
                groups = kept_groups
                leftovers = [
                    ln
                    for ln in leftovers
                    if not any(_box_contains(box, ln, margin=10) for box in media_boxes)
                ]

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
            # 引用块（微信 4.x 画在气泡下方，灰字、无气泡底色）不是新消息。
            if self.cfg.filter_quote and self._looks_like_grey_note(
                ln_list, image, panel_bg
            ):
                continue
            if self.cfg.filter_quote and self._is_quote_block(ln_list, font_h):
                continue
            # 系统提示（「你领取了…的红包」「X撤回了一条消息」）居中、字号比正文小、
            # 没有气泡底色；真人消息永远靠左或靠右，不会居中。
            if self._looks_like_centered_system(
                ln_list, chat_left, right, font_h, image, panel_bg
            ):
                continue
            if self.cfg.filter_media and self._looks_like_card(
                ln_list, image, panel_bg
            ):
                continue
            # 头像/装饰被 OCR 读出来的单字符碎片（如 "0"），高宽都远小于正文
            if self._looks_like_ocr_fragment(ln_list, font_h):
                continue
            # 引用块：微信把被引用的原文画在气泡顶部的小字块里；它不是新消息。
            # 只保留下面真正回复的正文，整条都是引用则丢弃。
            if self.cfg.filter_quote:
                ln_list, quote_only = self._strip_quote_block(ln_list)
                if quote_only:
                    continue
                text = self._join_lines(ln_list)
                if not text:
                    continue
            # 语音时长/占位符/文件卡/文章卡：合并成整条后再判一次
            if self.cfg.filter_non_dialogue and looks_like_non_dialogue(text):
                continue
            # 逐行过滤后仍可能漏网（每行只有一个机器串），合并后再判一次
            if self._is_garbled(text, _mean_score(ln_list)):
                continue
            if box is not None:
                is_me = box.is_me
                x0, y0, x1, y1 = box.x0, box.y0, box.x1, box.y1
            else:
                is_me = self._is_me_by_alignment(ln_list, chat_left, right)
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
        # 聊天标题紧贴面板左边界；页面（搜一搜/文章/朋友圈）的标题跑在右侧，
        # 认成会话名会把可见聊天错记到页面名下，造成跨会话污染。
        # 200px 是 100% DPI 下的经验值，按 chat_left 的缩放比例跟着 DPI 放大。
        base_left = max(1, self.cfg.chat_left_px)
        scale = max(1.0, chat_left / base_left)
        margin = int(200 * scale)
        close = [ln for ln in head if ln.x0 <= chat_left + margin]
        if not close:
            return ""
        head = close
        head.sort(key=lambda ln: (ln.y0, ln.x0))
        # 标题通常字号最大：取最高的一行，太短则并入相邻行
        best = max(head, key=lambda ln: ln.height)
        parts = [ln.text for ln in head if abs(ln.cy - best.cy) <= best.height]
        name = " ".join(p.strip() for p in parts if p.strip())
        name = re.sub(r"\s+", " ", name).strip()
        if name in {"微信", "WeChat"}:
            return ""
        return name[:40]

    # ---------- 分屏右边界 ----------

    def _detect_chat_right(
        self,
        image: np.ndarray,
        chat_left: int,
        top: int,
        bottom: int,
        lines: list[OcrLine],
    ) -> int:
        """分屏打开公众号文章时，找出聊天面板右边界。

        微信分屏的分隔结构很固定：聊天背景（≈250）→ 会话滚动条（≈221）→
        一条 16px 左右的纯灰分隔条（≈237）→ 文章面板纯白（255）。只认
        「连续多列颜色稳定在浅灰 + 右侧紧接着整片纯白 + 右侧确有文字」这一种，
        找不到就返回 0 当整宽处理——宁可多读，不能把正常聊天的右侧消息裁掉。
        """
        height, width = image.shape[:2]
        if width < 900 or bottom - top < 100:
            return 0
        band = image[top:bottom].astype(np.int16)
        if band.size == 0:
            return 0
        lum = band.mean(axis=2)
        col_std = lum.std(axis=0)
        col_mean = lum.mean(axis=0)
        x = max(chat_left + 200, int(width * 0.45))
        while x < width - 60:
            if not (col_std[x] < 3.0 and 225 <= col_mean[x] <= 248):
                x += 1
                continue
            start = x
            while x + 1 < width - 60 and col_std[x + 1] < 3.0 and (
                225 <= col_mean[x + 1] <= 248
            ):
                x += 1
            end = x
            x += 1
            if end - start + 1 < 5:
                continue
            right = end + 1
            if right >= width - 60:
                continue
            # 分隔条右侧紧贴的十几列必须是整片纯白（文章面板）；
            # 再往右会有正文文字，所以只看紧邻的这一小段。
            strip = (band[:, right : right + 16].min(axis=2) >= 252).mean(axis=0)
            if strip.min() < 0.85:
                continue
            # 右侧必须真有文字（文章正文），否则只是背景色差别，不能裁
            outside = [
                ln for ln in lines if ln.x0 >= right and top <= ln.cy <= bottom
            ]
            if len(outside) < 3:
                continue
            return start
        return 0

    # ---------- 气泡检测 ----------

    def _detect_boxes(
        self,
        image: np.ndarray,
        chat_left: int,
        top: int,
        bottom: int,
        right: int | None = None,
    ) -> list[BubbleBox]:
        region = image[top:bottom, chat_left : right or image.shape[1]]
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

    def _large_regions(
        self,
        image: np.ndarray,
        chat_left: int,
        top: int,
        bottom: int,
        right: int | None = None,
    ) -> list[BubbleBox]:
        """找出「大图」候选：尺寸超过普通气泡上限、会被 _detect_boxes 丢掉的区域。

        长截图就是靠这个漏洞漏进来的：轮廓被尺寸上限过滤掉，框里的文字行
        却还在 OCR 结果里，最后被当成一条条消息。这里只找候选，
        是否算图片/卡片交给 _looks_like_media 判断。
        """
        region = image[top:bottom, chat_left : right or image.shape[1]]
        if region.size == 0:
            return []
        rgb = region.astype(np.int16)
        background = np.median(rgb.reshape(-1, 3), axis=0)
        mask = (np.abs(rgb - background).max(axis=2) > 8).astype(np.uint8)
        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
        region_h, region_w = region.shape[:2]
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates: list[BubbleBox] = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w < 120 or h < region_h * 0.4:
                continue
            # 几乎铺满面板的多半是背景/整页，不能当一张图整个丢
            if w > region_w * 0.95 or h > region_h * 0.95:
                continue
            if w * h < region_w * region_h * 0.12:
                continue
            candidates.append(
                BubbleBox(
                    x0=x + chat_left,
                    y0=y + top,
                    x1=x + w + chat_left,
                    y1=y + h + top,
                    is_me=False,
                )
            )
        return candidates

    def _drop_large_media(
        self,
        body: list[OcrLine],
        image: np.ndarray,
        chat_left: int,
        top: int,
        bottom: int,
        right: int | None = None,
    ) -> tuple[list[OcrLine], list[BubbleBox]]:
        regions = self._large_regions(image, chat_left, top, bottom, right)
        if not regions:
            return body, []
        font_h = _median_height(body)
        media: list[BubbleBox] = []
        for region in regions:
            inside = [ln for ln in body if _box_contains(region, ln, margin=8)]
            if not inside:
                continue
            if self._looks_like_media(region, inside, image, font_h):
                media.append(region)
        if not media:
            return body, []
        kept = [
            ln
            for ln in body
            if not any(_box_contains(region, ln, margin=8) for region in media)
        ]
        return kept, media

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
        if any(pattern.search(t) for pattern in SYSTEM_PATTERNS):
            return True
        if any(hint in t for hint in SYSTEM_HINTS):
            return True
        return any(hint in t for hint in UI_HINTS)

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


    def _strip_quote_block(self, lines: list[OcrLine]) -> tuple[list[OcrLine], bool]:
        """剥离引用块，返回（真正回复的行, 是否整条都是引用）。

        微信引用消息长这样：气泡顶部一个灰底小字块，第一行是「发言人：原消息」，
        下面才是本次回复。引用的字号比正文小、位置略右缩进；两个特征满足一个
        才动手，避免把「注意：明天带伞」这种正常多行消息的第一行误删。
        """
        ordered = sorted(lines, key=lambda ln: (ln.y0, ln.x0))
        if len(ordered) < 2:
            return lines, False
        first = ordered[0]
        if not quote_echo_content(first.text):
            return lines, False
        rest = ordered[1:]
        rest_h = _median_height(rest)
        if rest_h <= 0:
            return lines, False
        rest_x0 = min(ln.x0 for ln in rest)
        smaller = first.height <= rest_h * 0.9
        indented = first.x0 - rest_x0 >= 8 and first.height <= rest_h * 1.05
        if not (smaller or indented):
            return lines, False
        consumed = 0
        for ln in rest:
            if ln.height <= first.height * 1.2 and ln.x0 >= first.x0 - 6:
                consumed += 1
                continue
            break
        remainder = rest[consumed:]
        if not remainder:
            return [], True
        return remainder, False

    def _is_quote_block(self, lines: list[OcrLine], font_h: float) -> bool:
        """整组都是引用块：首行「名字：被引用内容」，且每组行都比正文小一号。"""
        ordered = sorted(lines, key=lambda ln: (ln.y0, ln.x0))
        if not ordered:
            return False
        if not quote_echo_content(ordered[0].text):
            return False
        if font_h <= 0:
            return False
        return all(ln.height <= font_h * 0.78 for ln in ordered)

    def _looks_like_card(
        self, lines: list[OcrLine], image: np.ndarray, panel_bg: float
    ) -> bool:
        """链接卡片/视频号卡片/书封：多行文字里混着灰色小字（摘要、公众号名）。

        真人的文字消息每一行都是同一种深色；卡片一定同时有「黑色标题」和
        「灰色摘要/来源」，或者整块都是灰字（图片里的说明文字）。逐行量
        「这一行墨色离背景多远」，只要出现灰字行就不是一段人说的话。
        """
        if len(lines) < 2:
            return False
        x0 = max(0, min(ln.x0 for ln in lines) - 4)
        y0 = max(0, min(ln.y0 for ln in lines) - 4)
        x1 = min(image.shape[1], max(ln.x1 for ln in lines) + 4)
        y1 = min(image.shape[0], max(ln.y1 for ln in lines) + 4)
        crop = image[y0:y1, x0:x1]
        if crop.size < 200:
            return False
        bg = float(np.median(crop.mean(axis=2)))
        if bg <= 20:
            bg = panel_bg
        if bg <= 0:
            return False
        grey_limit = bg - 115.0
        dark_limit = bg - 135.0
        ink_medians: list[float] = []
        for ln in lines:
            piece = image[ln.y0 : ln.y1, max(0, ln.x0) : ln.x1]
            if piece.size == 0:
                continue
            lum = piece.mean(axis=2)
            ink = lum[lum < bg - 20.0]
            if ink.size < 20:
                continue
            ink_medians.append(float(np.median(ink)))
        if len(ink_medians) < 2:
            return False
        grey = sum(1 for med in ink_medians if med >= grey_limit)
        dark = sum(1 for med in ink_medians if med <= dark_limit)
        if grey == len(ink_medians):
            return True
        return grey > 0 and dark > 0

    def _looks_like_grey_note(
        self, lines: list[OcrLine], image: np.ndarray, panel_bg: float
    ) -> bool:
        """灰字提示块：引用块（微信 4.x 画在气泡下方）、系统提示、撤回提示。

        判据是像素而不是文字：这类块没有气泡底色（取色等于聊天背景），
        文字是灰色（与背景的明暗差远小于正文的黑字）。真人消息必有气泡底色，
        所以不会误杀；背景为浅色/深色主题都成立，因为比较的是「有没有底色」
        和「对比度高低」，不是固定色值。
        """
        if not lines or panel_bg <= 0:
            return False
        x0 = max(0, min(ln.x0 for ln in lines) - 2)
        y0 = max(0, min(ln.y0 for ln in lines) - 2)
        x1 = min(image.shape[1], max(ln.x1 for ln in lines) + 2)
        y1 = min(image.shape[0], max(ln.y1 for ln in lines) + 2)
        crop = image[y0:y1, x0:x1].mean(axis=2)
        if crop.size < 40:
            return False
        bg = float(np.median(crop))
        ink = float(np.percentile(crop, 3))
        has_fill = abs(bg - panel_bg) > 4.0
        if has_fill:
            return False
        # 正文接近纯黑/纯白，明暗差大；灰字提示的差值明显小。
        # 也要排除整块没有文字（差值≈0）的情况——纯背景不是提示。
        contrast = abs(bg - ink)
        return 15.0 <= contrast < 120.0

    def _looks_like_centered_system(
        self,
        lines: list[OcrLine],
        chat_left: int,
        right: int,
        font_h: float,
        image: np.ndarray,
        panel_bg: float,
    ) -> bool:
        """系统提示：单行、居中、字号比正文小，且没有气泡底色。"""
        if len(lines) != 1:
            return False
        ln = lines[0]
        panel = max(1, right - chat_left)
        center = (chat_left + right) / 2.0
        if abs(ln.cx - center) > panel * 0.08:
            return False
        if font_h > 0 and ln.height > font_h * 0.95:
            return False
        # 有气泡底色的居中行是真人消息，不能按系统提示丢。
        img_h, img_w = image.shape[:2]
        crop = image[
            max(0, ln.y0 - 2) : min(img_h, ln.y1 + 2),
            max(0, ln.x0 - 2) : min(img_w, ln.x1 + 2),
        ].mean(axis=2)
        if crop.size and abs(float(np.median(crop)) - panel_bg) > 4.0:
            return False
        return True

    def _looks_like_ocr_fragment(self, lines: list[OcrLine], font_h: float) -> bool:
        """单字符小碎片（头像角标、图标里的字），高宽都远小于正文行。"""
        if len(lines) != 1 or font_h <= 0:
            return False
        ln = lines[0]
        return ln.height <= font_h * 0.62 and (ln.x1 - ln.x0) <= font_h * 1.0

    def _looks_like_media(
        self, box: BubbleBox, lines: list[OcrLine], image: np.ndarray, font_h: float
    ) -> bool:
        """判断一个「气泡框」其实是图片/名片/文章卡，而不是文字气泡。

        微信文字气泡 = 一块纯色底 + 深色文字：四边颜色稳定、主色占比高、
        文字大小和聊天正文一致。图片缩略图/卡片则相反：边上就是画面内容、
        颜色杂，图里的字通常被缩得更小。至少两个信号成立才丢，避免误杀
        正常长消息，或把几个相邻气泡粘连成的大框当图片。
        """
        img_h, img_w = image.shape[:2]
        x0, y0 = max(0, box.x0), max(0, box.y0)
        x1, y1 = min(img_w, box.x1), min(img_h, box.y1)
        w, h = x1 - x0, y1 - y0
        if w < 90 or h < 90 or not lines:
            return False
        crop = image[y0:y1, x0:x1]
        if crop.size == 0:
            return False
        top = min(ln.y0 for ln in lines)
        bottom = max(ln.y1 for ln in lines)
        v_fill = (bottom - top) / float(h)
        line_h = _median_height(lines)
        small_font = bool(font_h > 0 and line_h < font_h * 0.78)
        flat = _dominant_ratio(crop)
        ring_std = _ring_std(crop)
        # 图片/大图：整块没有稳定的底色、边缘就是画面内容
        if flat < 0.35 and ring_std > 40:
            return True
        if ring_std > 26 and flat < 0.62 and (small_font or v_fill < 0.8):
            return True
        if small_font and v_fill < 0.85:
            return True
        if h >= 130 and v_fill < 0.45 and (ring_std > 16 or flat < 0.75):
            return True
        return False


def _mean_score(lines: list[OcrLine]) -> float:
    if not lines:
        return 1.0
    return sum(line.score for line in lines) / len(lines)


def _median_height(lines: list[OcrLine]) -> float:
    if not lines:
        return 0.0
    heights = sorted(max(1.0, float(line.height)) for line in lines)
    return heights[len(heights) // 2]


def _box_contains(box: BubbleBox, line: OcrLine, margin: int = 0) -> bool:
    return (
        box.x0 - margin <= line.cx <= box.x1 + margin
        and box.y0 - margin <= line.cy <= box.y1 + margin
    )


def _dominant_ratio(crop: np.ndarray) -> float:
    """量化后最大单一颜色占比；文字气泡很高，图片/卡片很低。"""
    if crop.size == 0:
        return 1.0
    small = (crop.astype(np.int16) >> 3).astype(np.int32)
    codes = small[:, :, 0] * 1024 + small[:, :, 1] * 32 + small[:, :, 2]
    counts = np.bincount(codes.ravel(), minlength=32768)
    return float(counts.max()) / float(codes.size)


def _ring_std(crop: np.ndarray) -> float:
    """框四边一圈像素的颜色标准差；纯色气泡底接近 0，图片边缘波动很大。"""
    if crop.size == 0:
        return 0.0
    ring = max(2, min(5, min(crop.shape[:2]) // 12))
    parts = [
        crop[:ring, :, :].reshape(-1, 3),
        crop[-ring:, :, :].reshape(-1, 3),
        crop[:, :ring, :].reshape(-1, 3),
        crop[:, -ring:, :].reshape(-1, 3),
    ]
    pixels = np.concatenate(parts, axis=0)
    return float(pixels.std(axis=0).mean())


def _overlap_ratio(a: BubbleBox, b: BubbleBox) -> float:
    ix0, iy0 = max(a.x0, b.x0), max(a.y0, b.y0)
    ix1, iy1 = min(a.x1, b.x1), min(a.y1, b.y1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    return inter / float(min(a.width * a.height, b.width * b.height))
