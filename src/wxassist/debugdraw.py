"""调试可视化：把解析出的气泡画到截图上，便于校准区域参数。"""

from __future__ import annotations

import cv2
import numpy as np

from .models import Bubble

ME_COLOR = (0, 200, 90)
HER_COLOR = (60, 90, 240)


def annotate(image: np.ndarray, bubbles: list[Bubble], header: int = 0) -> np.ndarray:
    """image: RGB；返回 BGR 标注图（不修改原图）。"""
    canvas = cv2.cvtColor(image, cv2.COLOR_RGB2BGR).copy()
    if header:
        cv2.line(canvas, (0, header), (canvas.shape[1], header), (120, 120, 120), 1)
    for bubble in bubbles:
        color = ME_COLOR if bubble.is_me else HER_COLOR
        cv2.rectangle(canvas, (bubble.x0, bubble.y0), (bubble.x1, bubble.y1), color, 2)
        label = "ME" if bubble.is_me else "HER"
        cv2.putText(
            canvas,
            label,
            (bubble.x0, max(12, bubble.y0 - 3)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            color,
            1,
            cv2.LINE_AA,
        )
    return canvas


def save(path: str, image_bgr: np.ndarray) -> None:
    ok = cv2.imwrite(path, image_bgr)
    if not ok:
        raise RuntimeError(f"截图保存失败：{path}")
