"""采集辅助函数与填入坐标的测试（不打开真实窗口）。"""

from __future__ import annotations

import numpy as np
import pytest

from wxassist.config import CaptureConfig, Config
from wxassist.filler import FillError, copy_to_clipboard, input_box_point
from wxassist.layout import ChatRegion, compute_region
from wxassist.models import OcrLine
from wxassist.pipeline import _reply_lines_after_quote, _signature, _similar, is_chat_view


class FakeWindow:
    def __init__(self, bbox=(100, 200, 900, 800)) -> None:
        self._bbox = bbox

    def client_bbox(self):
        return self._bbox


def test_input_box_point_center_of_chat_pane() -> None:
    win = FakeWindow((100, 200, 900, 800))  # 宽 800 高 600
    x, y = input_box_point(win, chat_left_ratio=0.26)  # type: ignore[arg-type]
    chat_left = 100 + int(800 * 0.26)
    assert x == chat_left + (900 - chat_left) // 2
    assert y == 800 - int(600 * 0.12) // 2


def test_input_box_point_with_dpi_and_pixels() -> None:
    win = FakeWindow((100, 200, 900, 800))
    x, y = input_box_point(  # type: ignore[arg-type]
        win, chat_left_px=300, dpi_scale=1.0, input_px=160
    )
    assert x == 100 + 300 + (800 - 300) // 2
    assert y == 800 - 80


def test_region_uses_pixels_scaled_by_dpi() -> None:
    # 用配置类默认值，避免本机 config.json 里的 input_px 让断言"假通过"
    capture = CaptureConfig()
    region = compute_region(841, 716, 1.0, capture)
    assert region.chat_left == 300
    assert region.header_bottom == 64
    assert region.input_top == 716 - capture.input_px
    scaled = compute_region(1051, 895, 1.25, capture)
    assert scaled.chat_left == 375
    assert scaled.header_bottom == 80
    assert scaled.input_top == 895 - int(capture.input_px * 1.25)


def test_region_falls_back_to_ratios_without_dpi() -> None:
    cfg = Config.load(None)
    region = compute_region(1000, 700, 0.0, cfg.capture)
    assert region.chat_left == 260
    assert region.header_bottom == 70
    assert region.input_top == 700 - int(700 * 0.14)


def test_region_clamped_for_tiny_windows() -> None:
    cfg = Config.load(None)
    region = compute_region(300, 200, 1.0, cfg.capture)
    assert region.valid
    assert region.chat_left >= int(300 * 0.12)


def test_signature_and_similarity() -> None:
    a = np.zeros((80, 120, 3), dtype=np.uint8)
    same = a.copy()
    changed = a.copy()
    changed[40:, :] = 200
    assert _similar(_signature(a), _signature(same))
    assert not _similar(_signature(a), _signature(changed))


def test_chat_view_needs_send_button_in_footer() -> None:
    cfg = Config.load(None)
    region = compute_region(841, 716, 1.0, cfg.capture)
    send = OcrLine(text="发送", score=0.99, x0=700, y0=620, x1=760, y1=650)
    assert is_chat_view(region, 716, [send])
    assert not is_chat_view(region, 716, [])  # 文章/小程序没有「发送」

    # 窗口太矮时输入区被挤没（input_top 贴到底部），判定为不是聊天页
    squashed = ChatRegion(chat_left=180, header_bottom=32, input_top=200)
    assert not is_chat_view(squashed, 200, [send])


def test_chat_view_requires_exact_send_line() -> None:
    """底部有含「发送」的长句（悬浮窗免责声明）不算聊天页；发送(S) 可以。"""
    cfg = Config.load(None)
    region = compute_region(841, 716, 1.0, cfg.capture)
    disclaimer = OcrLine(
        text="只读屏幕 OCR，永不自动发送，发消息前请自己核对",
        score=0.99,
        x0=320,
        y0=640,
        x1=820,
        y1=660,
    )
    assert not is_chat_view(region, 716, [disclaimer])

    hotkey = OcrLine(text="发送(S)", score=0.99, x0=700, y0=620, x1=780, y1=650)
    assert is_chat_view(region, 716, [hotkey])

    # 文章页底部：有「发送」评论按钮，但也有「写留言/阅读原文」这类词
    article = OcrLine(text="写留言", score=0.99, x0=600, y0=640, x1=680, y1=660)
    assert not is_chat_view(region, 716, [hotkey, article])


def test_copy_to_clipboard_roundtrip() -> None:
    try:
        copy_to_clipboard("wxassist-clipboard-test")
    except FillError as exc:  # 剪贴板被其他程序占用时跳过
        pytest.skip(str(exc))
    import win32clipboard

    try:
        win32clipboard.OpenClipboard()
    except Exception as exc:  # 无交互桌面 / 沙箱环境拿不到剪贴板
        pytest.skip(f"剪贴板不可用：{exc}")
    try:
        try:
            data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        except Exception as exc:  # 同上，环境限制不是代码问题
            pytest.skip(f"剪贴板不可用：{exc}")
        assert data == "wxassist-clipboard-test"
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def _ocr_line(text: str, x0: int, y0: int, x1: int, y1: int) -> OcrLine:
    return OcrLine(text=text, score=0.99, x0=x0, y0=y0, x1=x1, y1=y1)


def test_reply_lines_after_quote_keeps_reply_with_same_geometry() -> None:
    """引用和回复的 OCR 坐标/字号相同时，不能把回复正文当引用一起删掉。"""
    lines = [
        _ocr_line("冯智：数字游民吗？", 320, 300, 500, 322),
        _ocr_line("服药时最好不要吃柚子", 320, 324, 500, 346),
    ]
    kept = _reply_lines_after_quote(lines)
    assert [line.text for line in kept] == ["服药时最好不要吃柚子"]


def test_reply_lines_after_quote_eats_indented_quote_body() -> None:
    """引用正文本来就缩进，可以跟着首行一起剥掉，后面的回复保留。"""
    lines = [
        _ocr_line("随遇而安：经济师是怎么个说法呢", 320, 300, 520, 320),
        _ocr_line("中级经济师分两个专业", 330, 322, 520, 342),
        _ocr_line("我今年准备考", 320, 346, 430, 368),
    ]
    kept = _reply_lines_after_quote(lines)
    assert [line.text for line in kept] == ["我今年准备考"]
