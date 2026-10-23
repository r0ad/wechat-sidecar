"""布局解析：用合成截图验证气泡检测与行分组，不依赖真实微信。"""

from __future__ import annotations

import cv2
import numpy as np

from wxassist.config import CaptureConfig
from wxassist.layout import BubbleParser
from wxassist.models import OcrLine


def make_line(text: str, x0: int, y0: int, x1: int, y1: int) -> OcrLine:
    return OcrLine(text=text, score=0.99, x0=x0, y0=y0, x1=x1, y1=y1)


def blank_image(width: int = 800, height: int = 600, color=(245, 245, 245)) -> np.ndarray:
    img = np.full((height, width, 3), color, dtype=np.uint8)
    return img


def test_detects_green_and_white_bubbles() -> None:
    cfg = CaptureConfig()
    img = blank_image()
    # 会话标题
    cv2.putText(img, "Alice", (300, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (20, 20, 20), 1)
    # 对方白色气泡（左）
    cv2.rectangle(img, (215, 300), (450, 350), (255, 255, 255), -1)
    # 己方绿色气泡（右）
    cv2.rectangle(img, (500, 380), (740, 430), (149, 236, 105), -1)

    lines = [
        make_line("Alice", 300, 20, 360, 48),
        make_line("晚上一起吃饭吗", 230, 312, 430, 338),
        make_line("好啊，几点", 515, 392, 700, 418),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert result.session == "Alice"
    assert len(result.bubbles) == 2
    left, right = result.bubbles
    assert left.text == "晚上一起吃饭吗"
    assert left.is_me is False
    assert right.text == "好啊，几点"
    assert right.is_me is True


def test_multiline_bubble_joined() -> None:
    cfg = CaptureConfig()
    img = blank_image()
    cv2.rectangle(img, (215, 300), (480, 380), (255, 255, 255), -1)
    lines = [
        make_line("我明天要去", 230, 308, 400, 330),
        make_line("上海出差", 230, 340, 380, 362),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert len(result.bubbles) == 1
    assert result.bubbles[0].text == "我明天要去\n上海出差"
    assert result.bubbles[0].is_me is False


def test_fallback_grouping_without_bubble_colors() -> None:
    cfg = CaptureConfig()
    img = blank_image(color=(30, 30, 30))  # 深色模式：颜色分割失效
    lines = [
        make_line("在吗", 220, 300, 300, 320),
        make_line("帮我看个东西", 220, 360, 420, 380),
        make_line("你说", 600, 420, 720, 440),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["在吗", "帮我看个东西", "你说"]
    assert [b.is_me for b in result.bubbles] == [False, False, True]


def test_filters_time_separators_and_system_hints() -> None:
    cfg = CaptureConfig()
    img = blank_image(color=(30, 30, 30))
    lines = [
        make_line("12:30", 380, 100, 440, 120),
        make_line("昨天 21:15", 350, 140, 470, 160),
        make_line("以下为新消息", 330, 180, 470, 200),
        make_line("真实消息", 220, 260, 340, 280),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["真实消息"]


def test_session_empty_when_only_wechat_title() -> None:
    cfg = CaptureConfig()
    img = blank_image(color=(30, 30, 30))
    lines = [make_line("微信", 300, 20, 360, 46)]
    result = BubbleParser(cfg).parse(lines, img)
    assert result.session == ""
    assert result.bubbles == []


def test_body_region_filters_input_area_and_sidebar() -> None:
    cfg = CaptureConfig()
    img = blank_image(color=(30, 30, 30))
    lines = [
        make_line("侧边栏会话", 20, 300, 180, 320),   # x 太小 → 丢弃
        make_line("输入框里的字", 250, 560, 420, 580),  # y 太靠下 → 丢弃
        make_line("正文消息", 220, 300, 340, 320),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["正文消息"]
