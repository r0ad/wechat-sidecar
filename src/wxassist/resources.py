"""应用图标等静态资源。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon

_ASSETS = Path(__file__).resolve().parent / "assets"
_icon: QIcon | None = None


def icon_path() -> Path:
    ico = _ASSETS / "wxassist.ico"
    if ico.exists():
        return ico
    return _ASSETS / "wxassist.png"


def app_icon() -> QIcon:
    global _icon
    if _icon is None:
        _icon = QIcon(str(icon_path()))
    return _icon
