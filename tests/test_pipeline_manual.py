"""手动识别（capture_once）与采集节奏的测试。

不打开真实微信窗口、不请求网络（Mock 模型）。
"""

from __future__ import annotations

import time

import cv2
import numpy as np
import pytest

import wxassist.pipeline as pipeline_mod
from wxassist.capture import CaptureError
from wxassist.config import Config
from wxassist.engine import SuggestEngine
from wxassist.llm import LLMClient
from wxassist.models import OcrLine
from wxassist.pipeline import Pipeline
from wxassist.store import Store

WIDTH, HEIGHT = 841, 716


class FakeWindow:
    hwnd = 4242
    title = "微信"
    exe = "Weixin.exe"
    minimized = False

    def client_bbox(self):
        return (0, 0, WIDTH, HEIGHT)

    def client_size(self):
        return WIDTH, HEIGHT

    def dpi_scale(self) -> float:
        return 1.0


def base_image() -> np.ndarray:
    image = np.full((HEIGHT, WIDTH, 3), 245, dtype=np.uint8)
    image[:64, :] = 235
    return image


def header_line(session: str) -> OcrLine:
    return OcrLine(text=session, score=0.99, x0=310, y0=30, x1=380, y1=56)


def bubble(image: np.ndarray, text: str, y: int, is_me: bool) -> tuple[OcrLine, np.ndarray]:
    if is_me:
        color = (149, 236, 105)
        x0, x1 = 560, 800
    else:
        color = (255, 255, 255)
        x0, x1 = 320, 520
    cv2.rectangle(image, (x0, y), (x1, y + 40), color, -1)
    line = OcrLine(text=text, score=0.99, x0=x0 + 8, y0=y + 8, x1=x1 - 8, y1=y + 32)
    return line, image


def make_pipeline(monkeypatch, tmp_path) -> tuple[Pipeline, list, Store]:
    cfg = Config.load(None)
    cfg.llm.provider = "mock"
    cfg.capture.interval_ms = 700
    cfg.capture.startup_warmup_sec = 0.0
    cfg.capture.analysis_debounce_sec = 0.0
    cfg.capture.require_send_button = False  # 合成帧没有「发送」按钮，测试里跳过聊天页门禁
    store = Store(tmp_path / "manual.db")
    engine = SuggestEngine(LLMClient(cfg.llm), cfg, store)

    monkeypatch.setattr(pipeline_mod, "find_window", lambda: FakeWindow())
    monkeypatch.setattr(pipeline_mod, "is_minimized", lambda win: False)

    analyses: list = []
    pipeline = Pipeline(
        cfg=cfg,
        store=store,
        engine=engine,
        on_analysis=analyses.append,
        on_status=lambda *_: None,
        on_debug=lambda *_: None,
    )
    return pipeline, analyses, store


def feed_manual(pipeline: Pipeline, image, lines: list[OcrLine]) -> None:
    pipeline._manual_frame = lambda win: (image, "wgc")  # type: ignore[method-assign]
    pipeline._ocr.recognize = lambda image, bbox=None: lines  # type: ignore[method-assign]
    pipeline._last_manual_ts = 0.0  # 绕过连点冷却


def test_manual_capture_stores_context_and_generates(monkeypatch, tmp_path) -> None:
    pipeline, analyses, store = make_pipeline(monkeypatch, tmp_path)
    frame = base_image()
    line, frame = bubble(frame, "明天有空吗", 300, is_me=False)
    feed_manual(pipeline, frame, [header_line("测试会话"), line])

    assert pipeline.capture_once() is True
    assert len(analyses) == 1
    assert analyses[0].session == "测试会话"
    assert analyses[0].incoming[-1].text == "明天有空吗"
    assert len(analyses[0].candidates) == 3
    assert store.count_messages("测试会话") == 1


def test_manual_capture_works_while_paused(monkeypatch, tmp_path) -> None:
    pipeline, analyses, _store = make_pipeline(monkeypatch, tmp_path)
    pipeline.pause(True)

    frame = base_image()
    line, frame = bubble(frame, "在吗", 300, is_me=False)
    feed_manual(pipeline, frame, [header_line("A"), line])

    assert pipeline.capture_once() is True
    assert len(analyses) == 1


def test_manual_capture_ignores_cooldown(monkeypatch, tmp_path) -> None:
    pipeline, analyses, _store = make_pipeline(monkeypatch, tmp_path)
    frame = base_image()
    line, frame = bubble(frame, "在吗", 300, is_me=False)
    feed_manual(pipeline, frame, [header_line("A"), line])

    assert pipeline.capture_once() is True
    pipeline._last_manual_ts = time.time()  # 刚点过
    assert pipeline.capture_once() is False
    assert len(analyses) == 1


def test_manual_capture_rejects_non_chat_page(monkeypatch, tmp_path) -> None:
    pipeline, analyses, store = make_pipeline(monkeypatch, tmp_path)
    pipeline.cfg.capture.require_send_button = True

    frame = base_image()
    line, frame = bubble(frame, "这是一篇公众号文章", 300, is_me=False)
    feed_manual(pipeline, frame, [header_line("文章页"), line])

    with pytest.raises(CaptureError):
        pipeline.capture_once()
    assert analyses == []
    assert store.sessions() == []


def test_manual_capture_accepts_chat_page_with_send_button(monkeypatch, tmp_path) -> None:
    pipeline, analyses, _store = make_pipeline(monkeypatch, tmp_path)
    pipeline.cfg.capture.require_send_button = True

    frame = base_image()
    line, frame = bubble(frame, "在吗", 300, is_me=False)
    send = OcrLine(text="发送", score=0.99, x0=700, y0=620, x1=760, y1=650)
    feed_manual(pipeline, frame, [header_line("A"), line, send])

    assert pipeline.capture_once() is True
    assert len(analyses) == 1


def test_auto_suggest_manual_reads_but_never_generates(monkeypatch, tmp_path) -> None:
    pipeline, analyses, store = make_pipeline(monkeypatch, tmp_path)
    pipeline.cfg.ui.auto_suggest = "manual"

    frame = base_image()
    old, frame = bubble(frame, "在吗", 300, is_me=False)
    pipeline._frame = lambda win: (frame, "wgc")  # type: ignore[method-assign]
    pipeline._ocr.recognize = lambda image, bbox=None: [header_line("A"), old]  # type: ignore[method-assign]
    pipeline._tick()

    frame2 = base_image()
    old2, frame2 = bubble(frame2, "在吗", 300, is_me=False)
    new, frame2 = bubble(frame2, "明天有空吗", 380, is_me=False)
    pipeline._frame = lambda win: (frame2, "wgc")  # type: ignore[method-assign]
    pipeline._ocr.recognize = lambda image, bbox=None: [header_line("A"), old2, new]  # type: ignore[method-assign]
    pipeline._tick()
    pipeline._tick()  # 两帧确认后才入库
    pipeline._flush_pending()

    assert analyses == []  # 不自动花模型调用
    assert store.count_messages("A") >= 2  # 但消息已经进上下文


def test_auto_suggest_session_skips_new_messages(monkeypatch, tmp_path) -> None:
    pipeline, analyses, _store = make_pipeline(monkeypatch, tmp_path)
    pipeline.cfg.ui.auto_suggest = "session"

    frame = base_image()
    old, frame = bubble(frame, "在吗", 300, is_me=False)
    pipeline._frame = lambda win: (frame, "wgc")  # type: ignore[method-assign]
    pipeline._ocr.recognize = lambda image, bbox=None: [header_line("A"), old]  # type: ignore[method-assign]
    pipeline._tick()

    frame2 = base_image()
    old2, frame2 = bubble(frame2, "在吗", 300, is_me=False)
    new, frame2 = bubble(frame2, "明天有空吗", 380, is_me=False)
    pipeline._frame = lambda win: (frame2, "wgc")  # type: ignore[method-assign]
    pipeline._ocr.recognize = lambda image, bbox=None: [header_line("A"), old2, new]  # type: ignore[method-assign]
    pipeline._tick()
    pipeline._flush_pending()

    assert analyses == []


def test_tick_ocr_runs_once_per_frame(monkeypatch, tmp_path) -> None:
    """聊天页门禁与气泡解析共用一趟 OCR（原来是两趟）。"""
    pipeline, _analyses, _store = make_pipeline(monkeypatch, tmp_path)
    calls: list = []

    frame = base_image()
    line, frame = bubble(frame, "在吗", 300, is_me=False)
    pipeline._frame = lambda win: (frame, "wgc")  # type: ignore[method-assign]

    def fake_ocr(image, bbox=None):
        calls.append(bbox)
        return [header_line("A"), line]

    pipeline._ocr.recognize = fake_ocr  # type: ignore[method-assign]
    pipeline._tick()

    assert len(calls) == 1
    bbox = calls[0]
    assert bbox is not None and bbox[3] == HEIGHT  # 一次读完整高度


def test_force_frame_bypasses_unchanged_picture(monkeypatch, tmp_path) -> None:
    pipeline, _analyses, _store = make_pipeline(monkeypatch, tmp_path)
    frame = base_image()
    line, frame = bubble(frame, "在吗", 300, is_me=False)

    class FakeCapturer:
        seq = 0

        def __init__(self, image):
            self.image = image

        def grab(self):
            return self.image, self.seq

        def stop(self):
            pass

    pipeline._capturer = FakeCapturer(frame)  # type: ignore[assignment]
    pipeline._capture_mode = "wgc"
    pipeline._capture_hwnd = FakeWindow.hwnd
    pipeline._capture_interval = 700
    pipeline._last_seq = -1
    pipeline._last_signature = None

    win = FakeWindow()
    assert pipeline._frame(win) is not None  # 首帧
    assert pipeline._frame(win) is None  # 画面没变 + 非强制 → 跳过
    forced = pipeline._frame(win, force=True)
    assert forced is not None and forced[0].shape == frame.shape


def test_idle_backoff_only_when_wechat_not_foreground(monkeypatch, tmp_path) -> None:
    pipeline, _analyses, _store = make_pipeline(monkeypatch, tmp_path)
    pipeline.cfg.capture.interval_ms = 700
    pipeline.cfg.capture.idle_backoff_sec = 10.0
    pipeline.cfg.capture.idle_interval_ms = 1500
    pipeline._last_window = FakeWindow()

    pipeline._last_frame_ts = time.time()
    assert pipeline._next_interval() == pytest.approx(0.7)  # 刚有画面变化，不降频

    pipeline._last_frame_ts = time.time() - 20
    monkeypatch.setattr(pipeline_mod, "is_foreground", lambda hwnd: True)
    assert pipeline._next_interval() == pytest.approx(0.7)  # 微信在前台，保持灵敏

    monkeypatch.setattr(pipeline_mod, "is_foreground", lambda hwnd: False)
    assert pipeline._next_interval() == pytest.approx(1.5)  # 不在前台且静默 → 降频


def test_capture_mode_switch_to_manual_releases_capturer(monkeypatch, tmp_path) -> None:
    pipeline, _analyses, _store = make_pipeline(monkeypatch, tmp_path)

    class FakeCapturer:
        stopped = False

        def grab(self):
            return None

        def stop(self):
            self.stopped = True

    capturer = FakeCapturer()
    pipeline._capturer = capturer  # type: ignore[assignment]
    pipeline._capture_hwnd = FakeWindow.hwnd
    pipeline._capture_mode = "wgc"

    pipeline.cfg.capture.auto_capture = False
    pipeline.apply_capture_mode()

    assert pipeline._capturer is None
    assert capturer.stopped is True
