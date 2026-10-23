"""用 QPainter 绘制的矢量小图标，避免 emoji 在部分字体下变形/看不清。"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

CANVAS = 32


def _pen(color: QColor, width: float = 2.4) -> QPen:
    pen = QPen(color)
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _draw_pin(painter: QPainter, color: QColor, filled: bool) -> None:
    """地图针/置顶图钉。"""
    path = QPainterPath()
    path.moveTo(16, 27)
    path.lineTo(9.5, 17.5)
    path.arcTo(QRectF(8, 4, 16, 16), 200, 140)
    path.lineTo(16, 27)
    path.closeSubpath()
    if filled:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(path)
    else:
        painter.setPen(_pen(color))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(16, 11.5), 2.4, 2.4)


def _draw_settings(painter: QPainter, color: QColor) -> None:
    """三条滑杆 = 设置。"""
    painter.setPen(_pen(color, 2.2))
    for y, knob_x in ((9, 21), (16, 11), (23, 23)):
        painter.drawLine(QPointF(6, y), QPointF(26, y))
        painter.setBrush(QColor(32, 38, 50))
        painter.setPen(_pen(color, 2.2))
        painter.drawEllipse(QPointF(knob_x, y), 3.2, 3.2)


def _draw_minimize(painter: QPainter, color: QColor) -> None:
    painter.setPen(_pen(color, 2.4))
    painter.drawLine(QPointF(9, 16), QPointF(23, 16))


def _draw_copy(painter: QPainter, color: QColor) -> None:
    pen = _pen(color, 2.0)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(11, 11, 12, 14), 2, 2)
    painter.drawRoundedRect(QRectF(8, 7, 12, 14), 2, 2)


def _draw_check(painter: QPainter, color: QColor) -> None:
    painter.setPen(_pen(color, 2.8))
    painter.drawPolyline(QPolygonF([QPointF(8, 17), QPointF(14, 23), QPointF(25, 10)]))


def _pixmap(kind: str, color_hex: str, size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / CANVAS, size / CANVAS)
    color = QColor(color_hex)
    if kind in ("pin", "pin_on"):
        _draw_pin(painter, color, filled=kind == "pin_on")
    elif kind == "settings":
        _draw_settings(painter, color)
    elif kind == "minimize":
        _draw_minimize(painter, color)
    elif kind == "copy":
        _draw_copy(painter, color)
    elif kind == "check":
        _draw_check(painter, color)
    painter.end()
    return pixmap


def icon(kind: str, color: str = "#D7DAE0") -> QIcon:
    result = QIcon()
    for size in (16, 20, 24, 32, 48):
        result.addPixmap(_pixmap(kind, color, size))
    return result
