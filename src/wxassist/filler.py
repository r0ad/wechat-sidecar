"""把候选回复填进微信输入框。

两种模式：
- copy：只写剪贴板（默认，零风险）；
- paste：激活微信 → 点输入框 → Ctrl+V。**永不按回车、永不点发送**。
"""

from __future__ import annotations

import time

from .capture import WeChatWindow, bring_to_front, is_foreground

try:  # pragma: no cover - 仅 Windows
    import win32api
    import win32clipboard
    import win32con
except ImportError:  # pragma: no cover
    win32api = None  # type: ignore[assignment]
    win32clipboard = None  # type: ignore[assignment]
    win32con = None  # type: ignore[assignment]


class FillError(RuntimeError):
    pass


def copy_to_clipboard(text: str) -> None:
    if win32clipboard is None:
        raise FillError("缺少 pywin32，无法操作剪贴板")
    if not text:
        raise FillError("候选内容为空")
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def input_box_point(
    win: WeChatWindow,
    chat_left_px: int = 300,
    dpi_scale: float = 0.0,
    chat_left_ratio: float = 0.26,
) -> tuple[int, int]:
    """估算输入框中心点（客户区坐标 → 屏幕坐标）。

    微信输入区在聊天面板底部；取面板水平中点、窗口高度 93% 处。
    chat_left 与采集层保持一致：优先「固定像素 × DPI 缩放」，否则用比例。
    """
    left, top, right, bottom = win.client_bbox()
    width = right - left
    height = bottom - top
    if dpi_scale and dpi_scale > 0:
        chat_left = int(chat_left_px * dpi_scale)
    else:
        chat_left = int(width * chat_left_ratio)
    chat_left = max(0, min(width - 1, chat_left))
    x = left + chat_left + (width - chat_left) // 2
    y = top + int(height * 0.93)
    return x, y


def _click(x: int, y: int) -> None:
    win32api.SetCursorPos((x, y))
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.03)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def _ctrl_v() -> None:
    # 只发 Ctrl+V，绝不发 Enter
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(0x56, 0, 0, 0)
    win32api.keybd_event(0x56, 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)


def fill(
    text: str,
    win: WeChatWindow,
    mode: str = "copy",
    chat_left_px: int = 300,
    dpi_scale: float = 0.0,
    chat_left_ratio: float = 0.26,
) -> str:
    """返回实际执行的动作：copied / pasted / 失败抛 FillError。"""
    copy_to_clipboard(text)
    if mode != "paste":
        return "copied"
    bring_to_front(win.hwnd)
    time.sleep(0.15)
    if not is_foreground(win.hwnd):
        raise FillError("微信窗口没有切到前台，已复制到剪贴板")
    x, y = input_box_point(win, chat_left_px, dpi_scale, chat_left_ratio)
    _click(x, y)
    time.sleep(0.12)
    _ctrl_v()
    return "pasted"
