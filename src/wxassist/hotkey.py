"""全局热键（可选）。键盘钩子失败时静默降级，不影响主流程。"""

from __future__ import annotations

from typing import Callable


class HotkeyManager:
    def __init__(self) -> None:
        self._registered: list[str] = []

    def register(self, combo: str, callback: Callable[[], None]) -> bool:
        try:
            import keyboard
        except Exception:
            return False
        try:
            keyboard.add_hotkey(combo, callback, suppress=False)
            self._registered.append(combo)
            return True
        except Exception:
            return False

    def unregister_all(self) -> None:
        try:
            import keyboard
        except Exception:
            return
        for combo in self._registered:
            try:
                keyboard.remove_hotkey(combo)
            except Exception:
                pass
        self._registered.clear()
