#!/usr/bin/env python3
"""The preset browser's toolbar must not move when the preset name is long.

A wrapping title grew to two lines and pushed the toolbar (and every list row)
down by a line, so buttons slid out from under the pointer as presets rotated.
Run: QT_QPA_PLATFORM=offscreen python test_browser_title.py
"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from viz_common import QApplication
from preset_browser import PresetPlaylistWindow, _stars

SHORT = "couldn't not"
LONG = ("NeW Adam Master Mashup FX 2 Geiss - Reaction Diffusion 34 + Swelling "
        "Spiral + Liquid Fire + Geiss an13 + another very long tail indeed")


class FakeHost:
    """Only the surface preset_browser actually touches.

    current_order_pos has to move: _on_preset_changed ends by calling
    _highlight(current_order_pos()), and that is what sets the title. A host
    that always answers 0 makes every preset change snap back to entry 0 --
    which is a broken fake, not a broken browser.
    """
    def __init__(self):
        self.entries = [(SHORT, 7), (LONG, 8), (LONG, 3), (LONG, 5),
                        (LONG, 6), (SHORT, 9)]
        self.pos = 0
        self.jumped = None
    def is_rated(self):            return False
    def is_shuffle(self):          return False
    def get_preset_playlist(self): return list(self.entries)
    def current_order_pos(self):   return self.pos
    def jump_to_order(self, i):    self.jumped = i


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    print(f"  ok  {name}")
    return True


app = QApplication.instance() or QApplication([])
win = PresetPlaylistWindow(host=FakeHost())
win.resize(380, 580)
win.show()

results = []
host = win._host


def play(idx):
    """What the renderer does: advance, then emit preset_changed."""
    host.pos = idx
    win._on_preset_changed(*host.entries[idx])
    app.processEvents()


def toolbar_y():
    return win._btn_shuffle.mapTo(win, win._btn_shuffle.rect().topLeft()).y()

def the_toolbar_does_not_move_for_a_long_name():
    play(0)
    short_y, short_h = toolbar_y(), win._title.height()
    play(1)
    long_y, long_h = toolbar_y(), win._title.height()
    assert short_y == long_y, f"toolbar moved {short_y} -> {long_y}"
    assert short_h == long_h, f"title grew {short_h} -> {long_h}"

def the_title_stays_one_line():
    play(1)
    fm = win._title.fontMetrics()
    assert win._title.height() <= fm.lineSpacing() + 20, \
        f"title is {win._title.height()}px, taller than one line plus padding"

def the_rating_survives_elision():
    play(2)
    text = win._title.text()
    assert text.endswith("3/10"), f"rating lost: {text!r}"
    assert _stars(3) in text, f"stars lost: {text!r}"
    assert "…" in text or "..." in text, f"long name was not elided: {text!r}"

def the_composed_line_actually_fits():
    """Reserving exactly the suffix width left 1px spare and still clipped."""
    for idx in (1, 2, 3, 4):
        play(idx)
        fm = win._title.fontMetrics()
        box = win._title.contentsRect().width() or (win._title.width() - 20)
        used = fm.horizontalAdvance(win._title.text())
        assert used <= box, (
            f"entry {idx}: line is {used}px in a {box}px label")


def a_short_name_is_not_elided():
    play(5)
    assert SHORT in win._title.text(), f"short name mangled: {win._title.text()!r}"

def the_full_name_is_still_reachable():
    play(3)
    assert win._title.toolTip() == LONG, "tooltip does not carry the full name"

def narrowing_re_elides():
    """The label is resized directly, not the window.

    Under QT_QPA_PLATFORM=offscreen, resize() on a window is a no-op and no
    resizeEvent ever fires, so driving this through the window would pass
    without testing anything. The elision budget is read from the label's own
    width, which is the part with logic in it; resizeEvent just calls
    _render_title().
    """
    play(4)
    wide = win._title.text()
    was = win._title.width()
    win._title.resize(200, win._title.height())
    win._render_title()
    narrow = win._title.text()
    assert narrow != wide, "a narrower label did not re-elide the title"
    assert len(narrow) < len(wide), f"narrower label produced more text: {narrow!r}"
    assert narrow.endswith("6/10"), f"rating lost when narrowed: {narrow!r}"
    win._title.resize(was, win._title.height())
    win._render_title()

def highlight_shows_stars_too():
    """_highlight used to set a title with no stars, so they came and went."""
    win.refresh(); app.processEvents()
    win._highlight(1); app.processEvents()
    assert _stars(8) in win._title.text(), \
        f"_highlight produced a starless title: {win._title.text()!r}"

def the_window_opens_at_least_as_wide_as_its_own_toolbar():
    """Qt will not open a window narrower than the widest thing in it, so a
    resize() below the toolbar's minimum is a number that describes nothing.

    This used to compare the toolbar against a hand-measured WIDTH, and that
    check could only ever be right on one machine: the width of a row of
    labelled buttons is a *font* measurement. The same ten buttons need 602px
    on Linux, 599 on Windows in Segoe UI, and **818 under the offscreen
    plugin's fallback font** -- which is the font this suite runs in and the
    one the app never uses. It was failing on Windows for a window that opens
    at 612 there in real life.

    What is asserted now is the construction: the window opens at the
    toolbar's own minimum when that is the larger, so it cannot be a lie in
    any font. Deleting that max() fails here."""
    # A window of its own: the one above is deliberately resized to 380 by
    # the elision checks, so it cannot answer what the constructor chose.
    fresh = PresetPlaylistWindow(host=FakeHost())
    bar = fresh.layout().itemAt(1)
    need = bar.minimumSize().width()
    assert fresh.width() >= need, (
        f"the toolbar needs {need}px and the window opened at "
        f"{fresh.width()}px, so the resize() is not following the toolbar")
    assert fresh.width() >= fresh.WIDTH, (
        f"WIDTH is meant to be a floor and the window opened at "
        f"{fresh.width()}px, under it")
    fresh.deleteLater()


def the_toolbar_is_still_a_row_somebody_can_read():
    """The check the pixel one was really standing in for.

    A button count is the same number in every font, which a width is not.
    Ten is what this row was designed around; an eleventh is a decision about
    the layout -- a second row, or a menu -- and not something to absorb by
    letting the window grow another 60px beside the visuals.
    """
    bar = win.layout().itemAt(1)
    buttons = [bar.itemAt(i).widget() for i in range(bar.count())
               if bar.itemAt(i).widget() is not None]
    assert len(buttons) <= 10, (
        f"{len(buttons)} buttons in the toolbar: "
        + ", ".join(b.text() or b.objectName() or type(b).__name__
                    for b in buttons))


for fn in (the_window_opens_at_least_as_wide_as_its_own_toolbar,
           the_toolbar_is_still_a_row_somebody_can_read,
           the_toolbar_does_not_move_for_a_long_name,
           the_composed_line_actually_fits,
           the_title_stays_one_line,
           the_rating_survives_elision,
           a_short_name_is_not_elided,
           the_full_name_is_still_reachable,
           narrowing_re_elides,
           highlight_shows_stars_too):
    results.append(check(fn.__name__, fn))

print("all browser-title checks passed" if all(results) else "FAILURES")
sys.exit(0 if all(results) else 1)
