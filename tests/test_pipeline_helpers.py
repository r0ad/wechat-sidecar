"""采集辅助函数与填入坐标的测试（不打开真实窗口）。"""

from __future__ import annotations

import numpy as np
import pytest

from wxassist.capture import WeChatWindow
from wxassist.config import Config, FillerConfig
from wxassist.filler import FillError, copy_to_clipboard, input_box_point
from wxassist.pipeline import Pipeline, _signature, _similar


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
    assert y == 200 + int(600 * 0.93)


def test_ocr_region_excludes_sidebar_and_input() -> None:
    cfg = Config.load(None)
    cfg.capture.chat_left_ratio = 0.25
    cfg.capture.input_top_ratio = 0.9
    pipeline = Pipeline(
        cfg=cfg,
        store=None,  # type: ignore[arg-type]
        engine=None,  # type: ignore[arg-type]
        on_analysis=lambda *_: None,
        on_status=lambda *_: None,
    )
    region = pipeline._ocr_region(np.zeros((100, 400, 3), dtype=np.uint8))
    assert region == (100, 0, 400, 90)


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

    win32clipboard.OpenClipboard()
    try:
        assert win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT) == (
            "wxassist-clipboard-test"
        )
    finally:
        win32clipboard.CloseClipboard()
