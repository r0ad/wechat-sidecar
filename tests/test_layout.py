"""布局解析：用合成截图验证气泡检测与行分组，不依赖真实微信。"""

from __future__ import annotations

import cv2
import numpy as np

from wxassist.config import CaptureConfig
from wxassist.layout import BubbleParser, is_group_name
from wxassist.models import OcrLine


def make_line(text: str, x0: int, y0: int, x1: int, y1: int, score: float = 0.99) -> OcrLine:
    return OcrLine(text=text, score=score, x0=x0, y0=y0, x1=x1, y1=y1)


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


def test_group_sender_name_attached_to_bubble() -> None:
    cfg = CaptureConfig()
    img = blank_image()
    cv2.rectangle(img, (215, 300), (470, 350), (255, 255, 255), -1)
    cv2.rectangle(img, (215, 420), (470, 470), (255, 255, 255), -1)
    lines = [
        make_line("吟游诗人(7)", 300, 20, 450, 50),
        make_line("冯敏娟", 218, 272, 280, 292),
        make_line("情绪激动了吗？", 230, 312, 420, 338),
        make_line("杨文品", 218, 392, 280, 412),
        make_line("现在好了", 230, 432, 360, 458),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert result.session == "吟游诗人(7)"
    assert [b.sender for b in result.bubbles] == ["冯敏娟", "杨文品"]
    assert [b.text for b in result.bubbles] == ["情绪激动了吗？", "现在好了"]


def test_month_day_time_separator_filtered() -> None:
    cfg = CaptureConfig()
    img = blank_image(color=(30, 30, 30))
    lines = [
        make_line("9月21日 15:05", 360, 100, 500, 122),
        make_line("真实消息", 220, 260, 340, 280),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["真实消息"]


def test_group_name_detection() -> None:
    assert is_group_name("吟游诗人(7)")
    assert is_group_name("项目群 (12)")
    assert not is_group_name("卢恒")
    assert not is_group_name("")


def test_split_leading_sender() -> None:
    parser = BubbleParser(CaptureConfig())
    name = make_line("彭乐", 349, 188, 385, 204)
    text = make_line("你们啥时候来杭州", 362, 218, 530, 235)
    sender, remaining = parser._split_leading_sender([name, text])
    assert sender == "彭乐"
    assert [line.text for line in remaining] == ["你们啥时候来杭州"]

    wrapped_first = make_line("我倒是想来，就是没有理由出来", 362, 373, 560, 387)
    wrapped_second = make_line("过节", 360, 391, 400, 409)
    sender2, _ = parser._split_leading_sender([wrapped_first, wrapped_second])
    assert sender2 == ""


# ---------- 图片消息乱码（真实故障：图里的字被 OCR 读成 "TE088n m877a"） ----------


def test_image_garbled_text_is_dropped() -> None:
    cfg = CaptureConfig()
    img = blank_image()
    # 真实文字气泡
    cv2.rectangle(img, (215, 210), (455, 250), (255, 255, 255), -1)
    # 图片消息缩略图：里面只有几个像素高的字，OCR 读出来是乱码
    cv2.rectangle(img, (215, 300), (455, 470), (255, 255, 255), -1)
    lines = [
        make_line("合并相似的问题", 230, 218, 430, 242),
        make_line("TE088n m877a, e38i8nM", 230, 312, 430, 330, score=0.55),
        make_line("HEERA8", 230, 340, 300, 356, score=0.56),
        make_line("ORFRA.", 230, 365, 300, 381, score=0.60),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["合并相似的问题"]
    assert [b.is_me for b in result.bubbles] == [False]


def test_garbled_bubble_dropped_even_with_high_scores() -> None:
    """每行只有一个机器串时行级过滤会漏，合并成气泡后要再判一次。"""
    cfg = CaptureConfig()
    img = blank_image()
    cv2.rectangle(img, (215, 300), (455, 380), (255, 255, 255), -1)
    lines = [
        make_line("TE088n", 230, 312, 330, 332, score=0.99),
        make_line("m877a", 230, 344, 330, 364, score=0.99),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert result.bubbles == []


def test_confident_latin_messages_are_kept() -> None:
    cfg = CaptureConfig()
    img = blank_image()
    cv2.rectangle(img, (215, 300), (455, 350), (255, 255, 255), -1)
    lines = [
        make_line("OK", 230, 310, 300, 332),
        make_line("5pm", 230, 336, 310, 348),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["OK\n5pm"]


def test_low_confidence_chinese_message_is_kept() -> None:
    cfg = CaptureConfig()
    img = blank_image()
    cv2.rectangle(img, (215, 300), (455, 350), (255, 255, 255), -1)
    lines = [make_line("好的", 230, 312, 300, 336, score=0.52)]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["好的"]


def test_garbled_filter_can_be_disabled() -> None:
    cfg = CaptureConfig()
    cfg.filter_garbled = False
    img = blank_image()
    cv2.rectangle(img, (215, 300), (455, 380), (255, 255, 255), -1)
    lines = [
        make_line("TE088n m877a, e38i8nM", 230, 312, 430, 330, score=0.55),
        make_line("HEERA8", 230, 344, 300, 360, score=0.56),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert len(result.bubbles) == 1
    assert "TE088n" in result.bubbles[0].text
