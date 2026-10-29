"""Where the panel goes: the black bar DuckStation leaves beside the game picture.

Kept apart from overlay.py so it can be tested without a display.
"""

from __future__ import annotations

MIN_BAR_WIDTH = 150     # narrower than this and the text won't fit sideways
MIN_BAR_HEIGHT = 90


def black_bar(client: tuple[int, int, int, int], aspect: float, top_ui: int, bottom_ui: int,
              screen_h: int, side: str = "right") -> tuple[int, int, int, int, str] | None:
    """Where the empty strip beside (or below) the game picture is: (x, y, w, h, kind).

    top_ui / bottom_ui are DuckStation's menu and status bars inside the window;
    in fullscreen there are none.
    """
    left, top, right, bottom = client
    if bottom - top >= screen_h:          # fullscreen: no menu or status bar
        top_ui = bottom_ui = 0
    area_top, area_bottom = top + top_ui, bottom - bottom_ui
    w, h = right - left, area_bottom - area_top
    if w <= 0 or h <= 0:
        return None
    pic_w = min(w, int(h * aspect))
    bar_w = (w - pic_w) // 2
    if bar_w >= MIN_BAR_WIDTH:
        x = left if side == "left" else right - bar_w
        return x, area_top, bar_w, h, "side"
    pic_h = min(h, int(w / aspect))
    bar_h = (h - pic_h) // 2
    if bar_h >= MIN_BAR_HEIGHT:
        return left, area_bottom - bar_h, w, bar_h, "bottom"
    return None


def picture_rect(client: tuple[int, int, int, int], aspect: float, top_ui: int, bottom_ui: int,
                 screen_h: int) -> tuple[int, int, int, int] | None:
    """Where the game picture itself is: (x, y, w, h), centred in the window's display area."""
    left, top, right, bottom = client
    if bottom - top >= screen_h:
        top_ui = bottom_ui = 0
    area_top, area_bottom = top + top_ui, bottom - bottom_ui
    w, h = right - left, area_bottom - area_top
    if w <= 0 or h <= 0:
        return None
    pic_w = min(w, int(h * aspect))
    pic_h = min(h, int(pic_w / aspect))
    return left + (w - pic_w) // 2, area_top + (h - pic_h) // 2, pic_w, pic_h
