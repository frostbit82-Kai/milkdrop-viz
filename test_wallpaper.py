#!/usr/bin/env python3
"""Wallpaper mode: pick the right WorkerW, and fill the right rectangle.

Windows-only at runtime, so the parts with decisions in them are split out and
checked here: which of Explorer's two WorkerW windows to draw into, and the
geometry that follows. Parenting into the FIRST one is the classic mistake —
it covers the desktop icons instead of sitting behind them.
Run: QT_QPA_PLATFORM=offscreen python test_wallpaper.py
"""
import os, sys, ctypes
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_common
from viz_common import QApplication, Renderer, pick_workerw

TWO = [(0, "DP-1", 0, 0, 1920, 1080), (1, "HDMI-2", 1920, 0, 3840, 2160)]
#: A monitor above and to the left of the primary — the virtual desktop then
#: starts at a negative origin, which is where naive positioning goes wrong.
OFFSET = [(0, "DP-1", 0, 0, 1920, 1080), (1, "LEFT", -1280, -200, 1280, 1024)]


class FakeUser32:
    """Explorer's desktop as EnumWindows sees it.

    `icons` is the WorkerW that owns SHELLDLL_DefView; `behind` is its next
    sibling, the one a wallpaper belongs in.
    """
    def __init__(self, tops, icons=None, behind=None):
        self.tops, self.icons, self.behind = tops, icons, behind

    def FindWindowExW(self, parent, after, cls, _name):
        if cls == "SHELLDLL_DefView":
            return 0x1 if parent == self.icons else 0
        if cls == "WorkerW" and parent is None:
            nxt = [h for h in self.tops if h > (after or 0)
                   and h in self.workers]
            return nxt[0] if nxt else 0
        return 0

    workers = ()


class FakeSDL:
    SDL_FALSE, SDL_TRUE = 0, 1

    def __init__(self):
        self.pos = self.size = None
        self.bordered = None
    def SDL_SetWindowPosition(self, _w, x, y):  self.pos = (x, y)
    def SDL_SetWindowSize(self, _w, w, h):      self.size = (w, h)
    def SDL_SetWindowBordered(self, _w, v):     self.bordered = v


class FakeBridge:
    def __init__(self): self.toasts = []
    class _Sig:
        def __init__(self, out): self.out = out
        def emit(self, *a): self.out.append(a)
    def __getattr__(self, _n):
        return FakeBridge._Sig(self.toasts)


def bare(cfg=None, displays=TWO):
    r = object.__new__(Renderer)
    r._cfg = cfg or {}
    r._displays = list(displays)
    r._window = object()
    r._pm = None
    r._wallpaper = False
    r._borderless = False
    r._wp_restore = None
    r._win_rect = (10, 20, 640, 480)
    r._bridge = FakeBridge()
    r._refresh_displays = lambda: None      # SDL is faked out from under it
    r._track_window = lambda: None
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


def the_worker_behind_the_icons_is_chosen():
    """Not the one holding SHELLDLL_DefView — that one is in front."""
    u = FakeUser32(tops=[0x10, 0x20, 0x30], icons=0x20, behind=0x30)
    u.workers = (0x20, 0x30)
    assert pick_workerw(u, u.tops) == 0x30

def a_desktop_with_no_defview_gives_nothing():
    """Explorer not drawing the desktop — a third-party shell, say."""
    u = FakeUser32(tops=[0x10, 0x20], icons=None)
    u.workers = (0x20,)
    assert pick_workerw(u, u.tops) == 0

def a_defview_with_no_sibling_gives_nothing():
    """0x052C not delivered, so the second WorkerW was never created."""
    u = FakeUser32(tops=[0x10, 0x20], icons=0x20)
    u.workers = (0x20,)          # itself only; no later sibling
    assert pick_workerw(u, u.tops) == 0


def non_windows_refuses_with_a_reason():
    r = bare()
    real = viz_common.sys.platform
    try:
        viz_common.sys.platform = "linux"
        r._set_wallpaper_mode(True)
    finally:
        viz_common.sys.platform = real
    assert r._wallpaper is False, "claimed wallpaper mode off Windows"
    assert any("Windows only" in " ".join(map(str, t)) for t in r._bridge.toasts), \
        r._bridge.toasts

def asking_for_the_mode_it_is_already_in_does_nothing():
    r = bare()
    r._wallpaper = True
    r._set_wallpaper_mode(True)
    assert r._bridge.toasts == [], r._bridge.toasts


def a_chosen_monitor_is_the_one_filled():
    r = bare({"fullscreen_display": 1})
    fake = FakeSDL()
    viz_common.sdl2, real = fake, viz_common.sdl2
    try:
        r._fit_to_wallpaper()
    finally:
        viz_common.sdl2 = real
    assert fake.size == (3840, 2160), fake.size
    assert fake.pos == (1920, 0), fake.pos
    assert fake.bordered == FakeSDL.SDL_FALSE, "wallpaper kept its border"
    assert r._borderless is True

def with_no_choice_it_spans_every_monitor():
    r = bare({"fullscreen_display": -1})
    fake = FakeSDL()
    viz_common.sdl2, real = fake, viz_common.sdl2
    try:
        r._fit_to_wallpaper()
    finally:
        viz_common.sdl2 = real
    assert fake.size == (5760, 2160), fake.size
    assert fake.pos == (0, 0), fake.pos

def positions_are_relative_to_the_virtual_desktop_origin():
    """WorkerW starts at the top-left of the virtual desktop, which is not
    (0, 0) when a monitor sits above or left of the primary one."""
    r = bare({"fullscreen_display": 0}, displays=OFFSET)
    fake = FakeSDL()
    viz_common.sdl2, real = fake, viz_common.sdl2
    try:
        r._fit_to_wallpaper()
    finally:
        viz_common.sdl2 = real
    # primary is at (0, 0) in screen coordinates, but the desktop starts at
    # (-1280, -200), so inside WorkerW the primary begins at (1280, 200)
    assert fake.pos == (1280, 200), fake.pos
    assert fake.size == (1920, 1080), fake.size


for fn in (the_worker_behind_the_icons_is_chosen,
           a_desktop_with_no_defview_gives_nothing,
           a_defview_with_no_sibling_gives_nothing,
           non_windows_refuses_with_a_reason,
           asking_for_the_mode_it_is_already_in_does_nothing,
           a_chosen_monitor_is_the_one_filled,
           with_no_choice_it_spans_every_monitor,
           positions_are_relative_to_the_virtual_desktop_origin):
    results.append(check(fn.__name__, fn))

print("all wallpaper checks passed" if all(results) else "FAILURES")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
