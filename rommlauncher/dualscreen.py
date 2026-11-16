"""Two-screen systems (DS, DSi, 3DS): the core options for each screen layout, and
how to use the touch screen with a controller. Values come from each core's source code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .retroarch import core_id

if TYPE_CHECKING:
    from .games import Game


# Cores that show two screens (DS, DSi, 3DS): the core options for showing only the main
# (top) screen, both screens, or the main screen with the touch screen small in the
# bottom-right corner ("corner"; only where the core's code puts it there). Names and
# values are from each core's source code.
SCREEN_CHOICES = {
    "main": "Main screen only",
    "both": "Both screens",
    "corner": "Main screen + small touch screen in the bottom-right corner",
}
SCREEN_LAYOUTS = {
    # Hybrid layout, "Show Opposite Screen": the bottom screen alone, small, bottom right.
    # A 3:1 ratio is the smallest it gets, and makes the whole picture almost exactly 16:9.
    "melondsds": {"main": {"melonds_screen_layout1": "top"},
                  "both": {"melonds_screen_layout1": "top-bottom"},
                  "corner": {"melonds_screen_layout1": "hybrid-top", "melonds_hybrid_ratio": "3",
                             "melonds_hybrid_small_screen": "one"}},
    "melonds": {"main": {"melonds_screen_layout": "Top Only"},
                "both": {"melonds_screen_layout": "Top/Bottom"}},
    "desmume": {"main": {"desmume_screens_layout": "top only"},
                "both": {"desmume_screens_layout": "top/bottom"}},
    # "Large Screen, Small Screen" puts the small screen bottom right, at a fixed 1/4 size.
    "citra": {"main": {"citra_layout_option": "Single Screen Only", "citra_swap_screen": "Top"},
              "both": {"citra_layout_option": "Default Top-Bottom Screen"},
              "corner": {"citra_layout_option": "Large Screen, Small Screen", "citra_swap_screen": "Top"}},
}


# Playing the touch screen with a controller (from each core's source): the right stick
# moves a pointer and a button touches. "pointer" is the core option that shows the
# pointer: (name, value when shown, value when hidden), set from config
# "show_touch_pointer" whenever the layout shows the touch screen.
TOUCH_CONTROLS = {
    "melondsds": {"pointer": ("melonds_show_cursor", "timeout", "disabled"),
                  "help": "Touch screen: right stick moves the cursor, R2 touches (hold L2 to move faster)."},
    "citra": {"pointer": ("citra_render_touchscreen", "enabled", "disabled"),
              "help": "Touch screen: right stick moves the pointer, R3 touches."},
}


def screen_choices(game: Game) -> list[str]:
    """The screen layouts this game's core offers ([] if it isn't a two-screen core)."""
    layouts = SCREEN_LAYOUTS.get(core_id(game.core or ""))
    return [c for c in SCREEN_CHOICES if c in layouts] if layouts else []
