#!/usr/bin/env python3
"""Fullscreen goes to the monitor you picked, and the menu only asks when
there is a choice.

SDL has no "fullscreen on display N" call — it uses whichever display the
window is on — so choosing one means moving the window there first. That move
is the logic worth checking.
Run: QT_QPA_PLATFORM=offscreen python test_display_picker.py
"""
import os, sys, ctypes
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sdl2 as _real_sdl2
import viz_common
from viz_common import QApplication, Renderer, ContextMenu

# (index, name, x, y, w, h) — a 1080p primary with a 4K to its right.
TWO = [(0, "DP-1", 0, 0, 1920, 1080), (1, "HDMI-2", 1920, 0, 3840, 2160)]


class FakeSDL:
    """Only the calls _move_to_chosen_display and _refresh_displays make.

    SDL_Rect is the real one: the code under test hands it to ctypes.byref(),
    which will not take a stand-in object.
    """
    SDL_WINDOW_FULLSCREEN_DESKTOP = 0x1001
    SDL_Rect = _real_sdl2.SDL_Rect

    def __init__(self, win_size=(1280, 720), displays=TWO):
        self.win_size = win_size
        self.moved_to = None
        self.displays = list(displays)

    def SDL_GetNumVideoDisplays(self):
        return len(self.displays)

    def SDL_GetDisplayBounds(self, i, ref):
        r = ref._obj
        _idx, _name, r.x, r.y, r.w, r.h = self.displays[i]
        return 0

    def SDL_GetDisplayName(self, i):
        return self.displays[i][1].encode()

    def SDL_GetWindowSize(self, _win, wp, hp):
        wp._obj.value, hp._obj.value = self.win_size

    def SDL_SetWindowPosition(self, _win, x, y):
        self.moved_to = (x, y)

    def SDL_GetWindowFlags(self, _win):
        return 0

    def SDL_SetWindowFullscreen(self, _win, flags):
        self.fullscreen = flags


def bare_renderer(cfg):
    """A Renderer without __init__ — it starts audio capture and an IPC
    thread, and none of that is what these checks are about."""
    r = object.__new__(Renderer)
    r._cfg = cfg
    r._displays = list(TWO)
    r._window = object()
    r._fullscreen = False
    return r


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    print(f"  ok  {name}")
    return True


app = QApplication.instance() or QApplication([])
results = []


def follow_the_window_moves_nothing():
    fake = FakeSDL()
    r = bare_renderer({"fullscreen_display": -1})
    viz_common.sdl2, real = fake, viz_common.sdl2
    try:
        r._move_to_chosen_display()
    finally:
        viz_common.sdl2 = real
    assert fake.moved_to is None, f"window was moved to {fake.moved_to}"

def a_chosen_display_centres_the_window_on_it():
    fake = FakeSDL(win_size=(1280, 720))
    r = bare_renderer({"fullscreen_display": 1})
    viz_common.sdl2, real = fake, viz_common.sdl2
    try:
        r._move_to_chosen_display()
    finally:
        viz_common.sdl2 = real
    # display 1 is 3840x2160 at x=1920
    want = (1920 + (3840 - 1280) // 2, 0 + (2160 - 720) // 2)
    assert fake.moved_to == want, f"{fake.moved_to} != {want}"

def a_window_bigger_than_the_display_is_not_pushed_off_it():
    """max(0, ...) — a negative offset would land it on the monitor to the left."""
    fake = FakeSDL(win_size=(2560, 1440))
    r = bare_renderer({"fullscreen_display": 0})
    viz_common.sdl2, real = fake, viz_common.sdl2
    try:
        r._move_to_chosen_display()
    finally:
        viz_common.sdl2 = real
    assert fake.moved_to == (0, 0), f"{fake.moved_to} != (0, 0)"

def an_unplugged_display_is_ignored():
    fake = FakeSDL(displays=[TWO[0]])
    r = bare_renderer({"fullscreen_display": 7})
    r._displays = [TWO[0]]
    viz_common.sdl2, real = fake, viz_common.sdl2
    try:
        r._move_to_chosen_display()
    finally:
        viz_common.sdl2 = real
    assert fake.moved_to is None, "moved onto a display that is not there"

def a_monitor_plugged_in_since_launch_is_picked_up():
    """_move_to_chosen_display re-enumerates first, so the menu written before
    the cable went in still finds the display."""
    fake = FakeSDL()
    r = bare_renderer({"fullscreen_display": 1})
    r._displays = [TWO[0]]                    # what startup saw
    viz_common.sdl2, real = fake, viz_common.sdl2
    try:
        r._move_to_chosen_display()
    finally:
        viz_common.sdl2 = real
    assert len(r._displays) == 2, r._displays
    assert fake.moved_to is not None, "never found the new monitor"

def enumeration_failure_keeps_the_last_good_list():
    """A momentary SDL failure must not empty the menu."""
    class Broken(FakeSDL):
        def SDL_GetNumVideoDisplays(self):
            raise RuntimeError("no video subsystem")
    r = bare_renderer({"fullscreen_display": -1})
    viz_common.sdl2, real = Broken(), viz_common.sdl2
    try:
        r._refresh_displays()
    finally:
        viz_common.sdl2 = real
    assert r._displays == TWO, r._displays


class MenuHost:
    def __init__(self, displays):
        self._d = displays
        self.saved = None
    def list_displays(self):      return list(self._d)
    def is_locked(self):          return False
    def is_borderless(self):      return False
    def is_fullscreen(self):      return False
    def is_recording(self):       return False
    # The menu asks for this on every platform now. It used to be Windows
    # only, and a stub without it built fine on Linux and crashed there.
    def is_wallpaper(self):       return False
    def _save_config(self, cfg):  self.saved = dict(cfg)


def menu_labels(cfg, displays):
    m = ContextMenu(renderer=MenuHost(displays), cfg=cfg, ratings={}, keymaps={})
    sub = next((a.menu() for a in m.actions()
                if a.menu() and "Fullscreen display" in a.text()), None)
    return m, sub


def one_display_gets_no_submenu():
    _m, sub = menu_labels({}, [TWO[0]])
    assert sub is None, "asked which monitor on a single-monitor machine"

def two_displays_get_one_row_each_plus_follow():
    _m, sub = menu_labels({}, TWO)
    assert sub is not None, "no display submenu with two monitors"
    texts = [a.text() for a in sub.actions() if a.text()]
    assert len(texts) == 3, texts
    assert "Follow the window" in texts[0], texts
    assert "DP-1" in texts[1] and "1920×1080" in texts[1], texts
    assert "HDMI-2" in texts[2] and "3840×2160" in texts[2], texts

def the_configured_display_is_the_ticked_one():
    _m, sub = menu_labels({"fullscreen_display": 1}, TWO)
    checked = [a.text() for a in sub.actions() if a.isChecked()]
    assert len(checked) == 1 and "HDMI-2" in checked[0], checked

def picking_a_display_writes_it_to_config():
    cfg = {"fullscreen_display": -1}
    m, sub = menu_labels(cfg, TWO)
    act = [a for a in sub.actions() if "HDMI-2" in a.text()][0]
    act.trigger()
    assert cfg["fullscreen_display"] == 1, cfg
    assert m._renderer.saved is not None, "the choice was never saved to disk"

for fn in (follow_the_window_moves_nothing,
           a_chosen_display_centres_the_window_on_it,
           a_window_bigger_than_the_display_is_not_pushed_off_it,
           an_unplugged_display_is_ignored,
           a_monitor_plugged_in_since_launch_is_picked_up,
           enumeration_failure_keeps_the_last_good_list,
           one_display_gets_no_submenu,
           two_displays_get_one_row_each_plus_follow,
           the_configured_display_is_the_ticked_one,
           picking_a_display_writes_it_to_config):
    results.append(check(fn.__name__, fn))

print("all display-picker checks passed" if all(results) else "FAILURES")
sys.exit(0 if all(results) else 1)
