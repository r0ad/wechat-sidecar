"""悬浮窗跟随位置的纯函数测试。"""

from __future__ import annotations

from wxassist.app import compute_overlay_position

SCREEN = (0, 0, 1920, 1080)
SIZE = (440, 730)


def test_dock_right_when_enough_space() -> None:
    x, y = compute_overlay_position((100, 100, 900, 900), SCREEN, SIZE)
    assert x == 908
    assert y == 100


def test_dock_left_when_right_overflow() -> None:
    x, _y = compute_overlay_position((1200, 100, 1900, 900), SCREEN, SIZE)
    assert x == 1200 - 440 - 8


def test_follow_offset() -> None:
    x, y = compute_overlay_position((300, 200, 1100, 900), SCREEN, SIZE, offset=(12, 34))
    assert (x, y) == (312, 234)


def test_clamped_to_screen() -> None:
    x, y = compute_overlay_position((-500, -500, 100, 200), SCREEN, SIZE, offset=(-900, -900))
    assert (x, y) == (4, 4)


def test_vertical_clamp_near_bottom() -> None:
    _x, y = compute_overlay_position((100, 900, 900, 1400), SCREEN, SIZE)
    assert y == 1080 - 730 - 8
