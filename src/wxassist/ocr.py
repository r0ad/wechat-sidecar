"""本地离线 OCR（RapidOCR / ONNXRuntime）。

懒加载：模型只在第一次识别时加载，避免拖慢启动。
"""

from __future__ import annotations

import threading

import numpy as np

from .models import OcrLine


class OcrError(RuntimeError):
    pass


class OcrEngine:
    def __init__(self) -> None:
        self._engine = None
        self._lock = threading.Lock()
        self._import_error = ""

    # ---------- 初始化 ----------

    def _ensure(self):
        if self._engine is not None:
            return self._engine
        with self._lock:
            if self._engine is not None:
                return self._engine
            try:
                from rapidocr_onnxruntime import RapidOCR
            except Exception as exc:  # pragma: no cover - 依赖缺失
                self._import_error = f"RapidOCR 未安装或加载失败: {exc}"
                raise OcrError(self._import_error) from exc
            self._engine = RapidOCR()
        return self._engine

    @property
    def available(self) -> bool:
        return self._engine is not None or not self._import_error

    def warmup(self) -> bool:
        """提前加载模型；失败返回 False 而不是抛异常。"""
        try:
            self._ensure()
            return True
        except OcrError:
            return False

    # ---------- 识别 ----------

    def recognize(self, image: np.ndarray) -> list[OcrLine]:
        """image: RGB ndarray → OCR 行列表（坐标基于传入图像）。"""
        engine = self._ensure()
        # RapidOCR 内部按 OpenCV 习惯处理 ndarray（BGR），这里转一下保证颜色通道正确
        bgr = np.ascontiguousarray(image[:, :, ::-1])
        try:
            result, _elapse = engine(bgr)
        except Exception as exc:  # pragma: no cover - 运行时异常
            raise OcrError(f"OCR 识别失败: {exc}") from exc
        if not result:
            return []
        lines: list[OcrLine] = []
        for item in result:
            box, text, score = item[0], item[1], item[2]
            if not text or not str(text).strip():
                continue
            xs = [int(p[0]) for p in box]
            ys = [int(p[1]) for p in box]
            lines.append(
                OcrLine(
                    text=str(text).strip(),
                    score=float(score),
                    x0=min(xs),
                    y0=min(ys),
                    x1=max(xs),
                    y1=max(ys),
                )
            )
        return lines
