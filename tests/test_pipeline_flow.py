"""端到端流程测试：用合成帧走完 截屏→解析→去重→建议 的整条链路。

不依赖真实微信窗口、不请求网络（Mock 模型）。
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

import wxassist.pipeline as pipeline_mod
from wxassist.config import Config
from wxassist.engine import SuggestEngine
from wxassist.layout import compute_region
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

    def dpi_scale(self) -> float:
        return 1.0


def base_image() -> np.ndarray:
    image = np.full((HEIGHT, WIDTH, 3), 245, dtype=np.uint8)
    # 顶部标题栏背景
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


def make_pipeline(monkeypatch, tmp_path) -> tuple[Pipeline, list]:
    cfg = Config.load(None)
    cfg.llm.provider = "mock"
    cfg.capture.interval_ms = 200
    cfg.capture.startup_warmup_sec = 0.0
    cfg.capture.analysis_debounce_sec = 0.0
    cfg.capture.require_send_button = False  # 合成帧没有「发送」按钮，测试里跳过聊天页门禁
    store = Store(tmp_path / "flow.db")
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
    return pipeline, analyses


def test_pipeline_emits_candidates_for_new_incoming(monkeypatch, tmp_path) -> None:
    pipeline, analyses = make_pipeline(monkeypatch, tmp_path)

    frame1 = base_image()
    line_old, frame1 = bubble(frame1, "在吗", 300, is_me=False)
    lines1 = [header_line("测试会话"), line_old]
    pipeline._frame = lambda win: (frame1, "wgc")  # type: ignore[method-assign]
    pipeline._ocr.recognize = lambda image, bbox=None: lines1  # type: ignore[method-assign]
    pipeline._tick()
    assert analyses == []  # 首帧只建立基线

    frame2 = base_image()
    line_old2, frame2 = bubble(frame2, "在吗", 300, is_me=False)
    line_new, frame2 = bubble(frame2, "明天有空吗", 400, is_me=False)
    lines2 = [header_line("测试会话"), line_old2, line_new]
    pipeline._frame = lambda win: (frame2, "wgc")  # type: ignore[method-assign]
    pipeline._ocr.recognize = lambda image, bbox=None: lines2  # type: ignore[method-assign]
    pipeline._tick()
    pipeline._flush_pending()

    assert len(analyses) == 1
    analysis = analyses[0]
    assert analysis.session == "测试会话"
    assert [m.text for m in analysis.incoming] == ["明天有空吗"]
    assert len(analysis.candidates) == 3
    assert analysis.candidates[0].selected


def test_pipeline_ignores_own_messages_and_session_switch(monkeypatch, tmp_path) -> None:
    pipeline, analyses = make_pipeline(monkeypatch, tmp_path)

    frame = base_image()
    line1, frame = bubble(frame, "在吗", 300, is_me=False)
    pipeline._frame = lambda win: (frame, "wgc")  # type: ignore[method-assign]
    pipeline._ocr.recognize = lambda image, bbox=None: [header_line("A"), line1]  # type: ignore[method-assign]
    pipeline._tick()

    # 自己发出一条：不应触发建议
    frame2 = base_image()
    line1b, frame2 = bubble(frame2, "在吗", 300, is_me=False)
    line_mine, frame2 = bubble(frame2, "在的", 400, is_me=True)
    pipeline._frame = lambda win: (frame2, "wgc")  # type: ignore[method-assign]
    pipeline._ocr.recognize = lambda image, bbox=None: [header_line("A"), line1b, line_mine]  # type: ignore[method-assign]
    pipeline._tick()
    pipeline._flush_pending()
    assert analyses == []

    # 切换会话：只重置基线，不分析历史
    frame3 = base_image()
    hist, frame3 = bubble(frame3, "历史消息", 300, is_me=False)
    pipeline._frame = lambda win: (frame3, "wgc")  # type: ignore[method-assign]
    pipeline._ocr.recognize = lambda image, bbox=None: [header_line("B"), hist]  # type: ignore[method-assign]
    pipeline._tick()
    pipeline._flush_pending()
    assert analyses == []
    assert pipeline._tracker is not None and pipeline._tracker.session == "B"


def test_region_matches_synthetic_layout() -> None:
    cfg = Config.load(None)
    region = compute_region(WIDTH, HEIGHT, 1.0, cfg.capture)
    assert region.chat_left == 300
    assert region.input_top == HEIGHT - 160


def test_session_switch_persists_visible_context(monkeypatch, tmp_path) -> None:
    pipeline, analyses = make_pipeline(monkeypatch, tmp_path)

    frame = base_image()
    old, frame = bubble(frame, "旧会话消息", 300, is_me=False)
    pipeline._frame = lambda win: (frame, "wgc")  # type: ignore[method-assign]
    pipeline._ocr.recognize = lambda image, bbox=None: [header_line("A"), old]  # type: ignore[method-assign]
    pipeline._tick()

    # 切换到会话 B，画面上有 3 条历史（应全部进上下文）
    frame2 = base_image()
    b1, frame2 = bubble(frame2, "第一句", 260, is_me=False)
    b2, frame2 = bubble(frame2, "我的回复", 330, is_me=True)
    b3, frame2 = bubble(frame2, "第三句", 400, is_me=False)
    pipeline._frame = lambda win: (frame2, "wgc")  # type: ignore[method-assign]
    pipeline._ocr.recognize = lambda image, bbox=None: [header_line("B"), b1, b2, b3]  # type: ignore[method-assign]
    pipeline._tick()
    assert pipeline.store.count_messages("B") == 3

    # B 里来一条新消息 → 分析应带上刚才 3 条历史
    frame3 = base_image()
    c1, frame3 = bubble(frame3, "第一句", 240, is_me=False)
    c2, frame3 = bubble(frame3, "我的回复", 310, is_me=True)
    c3, frame3 = bubble(frame3, "第三句", 380, is_me=False)
    c4, frame3 = bubble(frame3, "新问题", 450, is_me=False)
    pipeline._frame = lambda win: (frame3, "wgc")  # type: ignore[method-assign]
    pipeline._ocr.recognize = lambda image, bbox=None: [header_line("B"), c1, c2, c3, c4]  # type: ignore[method-assign]
    pipeline._tick()
    pipeline._flush_pending()

    assert len(analyses) == 1
    assert analyses[0].context_count >= 4
    assert analyses[0].incoming[-1].text == "新问题"


def test_session_name_ocr_jitter_keeps_same_session(monkeypatch, tmp_path) -> None:
    pipeline, analyses = make_pipeline(monkeypatch, tmp_path)

    frame = base_image()
    m1, frame = bubble(frame, "在吗", 300, is_me=False)
    pipeline._frame = lambda win: (frame, "wgc")  # type: ignore[method-assign]
    pipeline._ocr.recognize = lambda image, bbox=None: [header_line("卢恒"), m1]  # type: ignore[method-assign]
    pipeline._tick()

    # 标题 OCR 抖动（多识别出一个“口”），不应被当成新会话
    frame2 = base_image()
    m1b, frame2 = bubble(frame2, "在吗", 300, is_me=False)
    m2, frame2 = bubble(frame2, "在的", 380, is_me=True)
    pipeline._frame = lambda win: (frame2, "wgc")  # type: ignore[method-assign]
    pipeline._ocr.recognize = lambda image, bbox=None: [header_line("卢恒 口"), m1b, m2]  # type: ignore[method-assign]
    pipeline._tick()

    assert pipeline._tracker is not None
    assert pipeline._tracker.session == "卢恒"
    assert pipeline.store.sessions() == ["卢恒"]


def test_empty_session_header_keeps_current(monkeypatch, tmp_path) -> None:
    pipeline, _analyses = make_pipeline(monkeypatch, tmp_path)
    frame = base_image()
    m1, frame = bubble(frame, "在吗", 300, is_me=False)
    pipeline._frame = lambda win: (frame, "wgc")  # type: ignore[method-assign]
    pipeline._ocr.recognize = lambda image, bbox=None: [header_line("Alice"), m1]  # type: ignore[method-assign]
    pipeline._tick()

    frame2 = base_image()
    m1b, frame2 = bubble(frame2, "在吗", 300, is_me=False)
    pipeline._frame = lambda win: (frame2, "wgc")  # type: ignore[method-assign]
    pipeline._ocr.recognize = lambda image, bbox=None: [m1b]  # type: ignore[method-assign]
    pipeline._tick()

    assert pipeline._tracker is not None
    assert pipeline._tracker.session == "Alice"


def test_chat_view_gate_blocks_non_chat_page(monkeypatch, tmp_path) -> None:
    cfg = Config.load(None)
    store = Store(tmp_path / "gate.db")
    engine = SuggestEngine(LLMClient(cfg.llm), cfg, store)
    monkeypatch.setattr(pipeline_mod, "find_window", lambda: FakeWindow())
    monkeypatch.setattr(pipeline_mod, "is_minimized", lambda win: False)
    cfg.capture.require_send_button = True
    pipeline = Pipeline(cfg, store, engine, lambda a: None, lambda s, m: None)
    frame = base_image()
    line, frame = bubble(frame, "在吗", 300, is_me=False)
    pipeline._frame = lambda win: (frame, "wgc")  # type: ignore[method-assign]
    # 底部没有「发送」→ 判定不是聊天页，不读任何消息
    pipeline._ocr.recognize = lambda image, bbox=None: [header_line("文章页"), line]  # type: ignore[method-assign]
    pipeline._tick()
    assert pipeline._tracker is None
    assert store.sessions() == []
    store.close()
