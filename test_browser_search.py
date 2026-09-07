#!/usr/bin/env python3
"""The preset browser's search box filters the list without breaking jumps.

A library is ~10,000 presets and the browser had no way to find one by name.
Filtering hides rows rather than rebuilding the list, so a row index still
equals its playlist position -- these checks are what says that stayed true.
Run: QT_QPA_PLATFORM=offscreen python test_browser_search.py
"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from viz_common import QApplication
from preset_browser import PresetPlaylistWindow

ENTRIES = [("Geiss - Reaction Diffusion", 7),
           ("Flexi - mother-of-pearl", 8),
           ("Rovastar - Altars of Madness", 3),
           ("Geiss - Cosmic Dust 2", 5),
           ("Aderrasi - Airhandler", 6),
           ("GEISS - THE ORB OF LIGHT", 9)]
GEISS_ROWS = [0, 3, 5]          # case-insensitive, so row 5 counts


class FakeHost:
    """current_order_pos has to move -- _on_preset_changed ends by calling
    _highlight(current_order_pos()), and that is what sets the title."""
    def __init__(self):
        self.entries = list(ENTRIES)
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
host = win._host
results = []


def search(text: str):
    """Type into the box and let the debounce fire, through the real wiring.

    Not a direct _apply_filter() call: the box is connected to a timer, and a
    test that skipped it would still pass with the signal disconnected.
    """
    win._filter_timer.setInterval(0)
    win._search.setText(text)
    app.processEvents()
    app.processEvents()


def visible_rows():
    return [i for i in range(win._list.count()) if not win._list.isRowHidden(i)]


def everything_shows_when_the_box_is_empty():
    search("")
    assert visible_rows() == list(range(len(ENTRIES))), visible_rows()

def a_query_hides_the_rest():
    search("geiss")
    assert visible_rows() == GEISS_ROWS, f"{visible_rows()} != {GEISS_ROWS}"

def matching_is_case_insensitive():
    search("ORB")
    assert visible_rows() == [5], visible_rows()

def a_miss_hides_everything():
    search("zzzzzz-no-such-preset")
    assert visible_rows() == [], visible_rows()

def clearing_restores_every_row():
    search("geiss")
    search("")
    assert visible_rows() == list(range(len(ENTRIES))), visible_rows()

def a_filtered_row_still_jumps_to_the_right_preset():
    """The whole reason rows are hidden and not removed."""
    search("geiss")
    row = visible_rows()[-1]                  # 5 -- "GEISS - THE ORB OF LIGHT"
    win._on_activate(win._list.item(row))
    assert host.jumped == 5, f"jumped to {host.jumped}, wanted 5"

def the_window_title_carries_the_count():
    search("geiss")
    assert "3 of 6" in win.windowTitle(), win.windowTitle()
    search("")
    assert win.windowTitle() == "Preset Browser", win.windowTitle()

def typing_is_debounced():
    """Hiding 10,000 rows per keystroke lands a letter behind without this."""
    search("")
    win._filter_timer.setInterval(150)
    win._search.setText("geiss")
    app.processEvents()
    assert visible_rows() == list(range(len(ENTRIES))), \
        "the filter ran before the debounce expired"
    win._filter_timer.setInterval(0)
    app.processEvents(); app.processEvents()
    assert visible_rows() == GEISS_ROWS, "the debounced filter never ran"

def refresh_reapplies_the_filter():
    """refresh() replaces every row, and replacements start out visible."""
    search("geiss")
    win.refresh()
    app.processEvents()
    assert visible_rows() == GEISS_ROWS, \
        f"refresh un-filtered the list: {visible_rows()}"

def a_rotation_onto_a_hidden_preset_keeps_the_scroll():
    """scrollToItem on a hidden row throws the view away from the matches."""
    search("geiss")
    host.pos = 1                              # Flexi -- filtered out
    win._on_preset_changed(*ENTRIES[1])
    app.processEvents()
    assert visible_rows() == GEISS_ROWS, "the rotation dropped the filter"
    assert "mother-of-pearl" in win._title.toolTip(), \
        "the title stopped following the preset while filtered"


for fn in (everything_shows_when_the_box_is_empty,
           a_query_hides_the_rest,
           matching_is_case_insensitive,
           a_miss_hides_everything,
           clearing_restores_every_row,
           a_filtered_row_still_jumps_to_the_right_preset,
           the_window_title_carries_the_count,
           typing_is_debounced,
           refresh_reapplies_the_filter,
           a_rotation_onto_a_hidden_preset_keeps_the_scroll):
    results.append(check(fn.__name__, fn))

print("all browser-search checks passed" if all(results) else "FAILURES")
sys.exit(0 if all(results) else 1)
