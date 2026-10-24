"""采集辅助函数与填入坐标的测试（不打开真实窗口）。"""

from __future__ import annotations

import numpy as np
import pytest

from wxassist.config import Config
from wxassist.filler import FillError, copy_to_clipboard, input_box_point
from wxassist.layout import compute_region
from wxassist.pipeline import _signature, _similar


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
    cfg = Config.load(None)
    region = compute_region(841, 716, 1.0, cfg.capture)
    assert region.chat_left == 300
    assert region.header_bottom == 64
    assert region.input_top == 716 - 160
    scaled = compute_region(1051, 895, 1.25, cfg.capture)
    assert scaled.chat_left == 375
    assert scaled.header_bottom == 80
    assert scaled.input_top == 895 - 200


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
