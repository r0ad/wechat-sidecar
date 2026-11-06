"""布局解析：用合成截图验证气泡检测与行分组，不依赖真实微信。"""

from __future__ import annotations

import cv2
import numpy as np

from wxassist.config import CaptureConfig
from wxassist.layout import BubbleParser, ChatRegion, is_group_name
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
    assert [ln.text for ln in result.bubbles[0].lines] == ["我明天要去", "上海出差"]
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


# ---------- 非对话内容：语音 / 文件卡 / 文章卡 / 图片 / 引用 ----------


def test_voice_duration_line_is_dropped() -> None:
    cfg = CaptureConfig()
    img = blank_image(color=(30, 30, 30))
    lines = [
        make_line('10"', 610, 300, 650, 330),
        make_line("真实消息", 220, 380, 360, 404),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["真实消息"]


def test_file_card_bubble_is_dropped() -> None:
    cfg = CaptureConfig()
    img = blank_image(color=(30, 30, 30))
    lines = [
        make_line("调研报告.md", 230, 300, 400, 322),
        make_line("MD", 230, 324, 260, 340),
        make_line("29.2K", 230, 344, 300, 360),
        make_line("收到", 620, 420, 700, 444),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["收到"]


def test_article_card_bubble_is_dropped() -> None:
    cfg = CaptureConfig()
    img = blank_image(color=(30, 30, 30))
    lines = [
        make_line("芯和半导体Xpeedic", 230, 300, 430, 322),
        make_line("公众号名片", 230, 326, 330, 346),
        make_line("在看视频", 230, 420, 330, 444),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["在看视频"]


def test_image_box_text_is_dropped_by_pixels() -> None:
    """图片缩略图里的中文不是对方原话：整框文字都要丢。"""
    cfg = CaptureConfig()
    img = blank_image()
    # 图片缩略图：彩色噪点，缩略图里 OCR 出来的字比聊天正文小
    noise = np.random.default_rng(7).integers(0, 255, size=(220, 220, 3), dtype=np.uint8)
    img[150:370, 300:520] = noise
    # 正常文字气泡
    cv2.rectangle(img, (230, 420), (470, 460), (255, 255, 255), -1)
    lines = [
        make_line("截图里的中文", 315, 190, 500, 212, score=0.99),
        make_line("真实消息", 245, 430, 380, 452),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["真实消息"]


def test_image_filter_can_be_disabled() -> None:
    cfg = CaptureConfig()
    cfg.filter_media = False
    img = blank_image()
    noise = np.random.default_rng(7).integers(0, 255, size=(220, 220, 3), dtype=np.uint8)
    img[150:370, 300:520] = noise
    lines = [
        make_line("截图里的中文", 315, 190, 500, 212),
        make_line("真实消息", 245, 430, 380, 452),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["截图里的中文", "真实消息"]


def test_quote_block_is_stripped_and_reply_kept() -> None:
    cfg = CaptureConfig()
    img = blank_image()
    cv2.rectangle(img, (215, 300), (500, 400), (255, 255, 255), -1)
    lines = [
        make_line("老王：今晚吃啥呢", 240, 312, 450, 328),  # 引用：字号更小、右缩进
        make_line("要不吃火锅吧", 240, 330, 430, 346),
        make_line("好的我想吃火锅", 230, 360, 480, 384),  # 回复正文：正常字号、更靠左
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["好的我想吃火锅"]


def test_normal_colon_message_is_not_stripped() -> None:
    """正常多行消息首行带冒号不能被当成引用剥掉。"""
    cfg = CaptureConfig()
    img = blank_image()
    cv2.rectangle(img, (215, 300), (500, 400), (255, 255, 255), -1)
    lines = [
        make_line("注意：明天带伞", 230, 312, 430, 336),
        make_line("下午有暴雨", 230, 342, 400, 366),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [ln.text for ln in result.bubbles[0].lines] == ["注意：明天带伞", "下午有暴雨"]


def test_quote_filter_can_be_disabled() -> None:
    cfg = CaptureConfig()
    cfg.filter_quote = False
    img = blank_image()
    cv2.rectangle(img, (215, 300), (500, 400), (255, 255, 255), -1)
    lines = [
        make_line("老王：今晚吃啥呢", 240, 312, 450, 328),
        make_line("好的我想吃火锅", 230, 360, 480, 384),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert "老王" in result.bubbles[0].text


def test_ui_overlay_text_is_filtered() -> None:
    cfg = CaptureConfig()
    img = blank_image(color=(30, 30, 30))
    lines = [
        make_line("对方最新消息", 200, 300, 380, 324),
        make_line("真实消息", 220, 380, 360, 404),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["真实消息"]


def test_system_message_variants_are_filtered() -> None:
    """撤回/新消息/拍一拍的真实文案带破折号或数字，子串匹配会漏。"""
    cfg = CaptureConfig()
    img = blank_image(color=(30, 30, 30))
    lines = [
        make_line("你撤回了—条消息", 330, 120, 560, 140),
        make_line("3条新消息", 360, 160, 470, 180),
        make_line("张三拍了拍你", 340, 200, 480, 220),
        make_line("真实消息", 220, 300, 360, 324),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["真实消息"]


def test_large_image_region_text_is_dropped() -> None:
    """长截图比普通气泡高，会被 _detect_boxes 的尺寸上限丢掉；框里的字不能漏成消息。"""
    cfg = CaptureConfig()
    img = blank_image()
    noise = np.random.default_rng(11).integers(0, 255, size=(380, 300, 3), dtype=np.uint8)
    img[90:470, 250:550] = noise
    cv2.rectangle(img, (600, 430), (790, 500), (255, 255, 255), -1)
    lines = [
        make_line("截图第一行", 265, 140, 430, 156),
        make_line("截图第二行", 265, 180, 430, 196),
        make_line("截图第三行", 265, 220, 430, 236),
        make_line("真实消息", 620, 450, 760, 476),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["真实消息"]


def test_tall_text_bubble_is_not_treated_as_image() -> None:
    """很高的纯色文字气泡不能被大图判定误杀。"""
    cfg = CaptureConfig()
    img = blank_image()
    cv2.rectangle(img, (215, 90), (520, 420), (255, 255, 255), -1)
    lines = [
        make_line("第一行文字", 230, 120, 460, 144),
        make_line("第二行文字", 230, 146, 460, 170),
        make_line("第三行文字", 230, 172, 460, 196),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert len(result.bubbles) == 1
    assert [ln.text for ln in result.bubbles[0].lines] == ["第一行文字", "第二行文字", "第三行文字"]


def test_page_title_on_the_right_is_not_used_as_session() -> None:
    """页面（搜一搜/文章）标题在右侧，不能认成会话名造成跨会话污染。"""
    cfg = CaptureConfig()
    img = blank_image(color=(30, 30, 30))
    lines = [
        make_line("搜一搜：Vue4 就这么定", 640, 20, 780, 44),
        make_line("真实消息", 220, 300, 360, 324),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert result.session == ""
    assert [b.text for b in result.bubbles] == ["真实消息"]


def test_session_title_margin_scales_with_dpi() -> None:
    """150% DPI 下 chat_left 会变大，标题允许的横向范围也要跟着放大。"""
    cfg = CaptureConfig()  # chat_left_px=300 → 450 表示 150% 缩放
    img = blank_image(1000, 700)
    region = ChatRegion(chat_left=450, header_bottom=64, input_top=560)
    lines = [make_line("Alice", 680, 20, 740, 48)]
    result = BubbleParser(cfg).parse(lines, img, region)
    assert result.session == "Alice"

def _draw_text_strokes(
    img: np.ndarray, x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]
) -> None:
    """在框里画几道横线，模拟「有文字」的墨色统计（不需要真字形）。"""
    mid = (y0 + y1) // 2
    cv2.line(img, (x0 + 3, mid), (x1 - 3, mid), color, 3)
    cv2.line(img, (x0 + 3, y0 + 3), (x1 - 3, y0 + 3), color, 2)


def test_quote_block_below_bubble_is_ignored() -> None:
    """微信 4.x 的引用块画在气泡下方：无底色、灰字，不是一条新消息。"""
    cfg = CaptureConfig()
    img = blank_image(color=(245, 245, 245))
    cv2.rectangle(img, (215, 300), (520, 348), (255, 255, 255), -1)
    _draw_text_strokes(img, 230, 308, 500, 336, (20, 20, 20))
    _draw_text_strokes(img, 230, 360, 620, 390, (155, 155, 155))
    _draw_text_strokes(img, 230, 392, 560, 420, (155, 155, 155))
    lines = [
        make_line("晚上一起吃饭吗", 230, 308, 500, 336),
        make_line("小王：上次说的那家店几点开门来着，我忘了", 230, 360, 620, 390),
        make_line("好像是六点吧，要不我打电话问一下", 230, 392, 560, 420),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["晚上一起吃饭吗"]


def test_link_card_with_grey_footer_is_ignored() -> None:
    """文章/视频号卡片：黑色标题 + 灰色来源，整块都不是人说的话。"""
    cfg = CaptureConfig()
    img = blank_image(color=(250, 250, 250))
    cv2.rectangle(img, (215, 300), (700, 420), (238, 238, 238), -1)
    cv2.rectangle(img, (215, 300), (700, 420), (206, 206, 206), 1)
    _draw_text_strokes(img, 230, 310, 680, 336, (20, 20, 20))
    _draw_text_strokes(img, 230, 340, 640, 366, (20, 20, 20))
    _draw_text_strokes(img, 230, 392, 330, 412, (155, 155, 155))
    lines = [
        make_line("47岁医生没去健身房，一年血脂恢复正常", 230, 310, 680, 336),
        make_line("马甲线清晰，做对了一件事", 230, 340, 640, 366),
        make_line("四川日报", 230, 392, 330, 412),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert result.bubbles == []


def test_split_screen_article_panel_is_not_read() -> None:
    """分屏打开公众号文章时，右侧文章面板的文字不能当成聊天消息。"""
    cfg = CaptureConfig()
    img = blank_image(1267, 696, color=(250, 250, 250))
    img[80:530, 823:839] = (237, 237, 237)  # 分隔条
    img[80:530, 839:] = (255, 255, 255)  # 文章面板纯白
    cv2.rectangle(img, (445, 406), (696, 530), (255, 255, 255), -1)
    _draw_text_strokes(img, 460, 420, 680, 448, (20, 20, 20))
    lines = [
        make_line("你好呀", 460, 420, 680, 448),
        make_line("这个视频主要介绍了名为 html-explainer 的开源工具", 860, 300, 1200, 328),
        make_line("它是一款专为 AI Agent 设计的神器", 860, 340, 1200, 368),
        make_line("支持一键生成带字幕的讲解视频", 860, 380, 1200, 408),
    ]
    region = ChatRegion(chat_left=375, header_bottom=80, input_top=530)
    result = BubbleParser(cfg).parse(lines, img, region)
    assert result.session == ""
    assert [b.text for b in result.bubbles] == ["你好呀"]


def test_centered_system_notice_is_ignored() -> None:
    """「你领取了…的红包」这类系统提示居中、字号略小，不是聊天消息。"""
    cfg = CaptureConfig()
    img = blank_image(color=(245, 245, 245))
    cv2.rectangle(img, (215, 300), (520, 350), (255, 255, 255), -1)
    _draw_text_strokes(img, 230, 308, 500, 340, (20, 20, 20))
    _draw_text_strokes(img, 440, 380, 568, 398, (120, 120, 120))
    lines = [
        make_line("晚上一起吃饭吗", 230, 308, 500, 340),
        make_line("你领取了晓潇的红包", 440, 380, 568, 398),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["晚上一起吃饭吗"]


def test_tiny_ocr_fragment_is_ignored() -> None:
    """头像/图标被 OCR 读出的单字符碎片（如 "0"）不能当消息。"""
    cfg = CaptureConfig()
    img = blank_image(color=(245, 245, 245))
    cv2.rectangle(img, (500, 300), (740, 350), (149, 236, 105), -1)
    _draw_text_strokes(img, 515, 312, 720, 340, (20, 20, 20))
    lines = [
        make_line("好的呀", 515, 312, 720, 340),
        make_line("0", 415, 380, 431, 391),
    ]
    result = BubbleParser(cfg).parse(lines, img)
    assert [b.text for b in result.bubbles] == ["好的呀"]
