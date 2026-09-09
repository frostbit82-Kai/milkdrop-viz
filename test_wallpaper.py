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
from viz_common import (QApplication, Renderer, pick_workerw,
                        pick_desktop_host)

TWO = [(0, "DP-1", 0, 0, 1920, 1080), (1, "HDMI-2", 1920, 0, 3840, 2160)]
#: A monitor above and to the left of the primary — the virtual desktop then
#: starts at a negative origin, which is where naive positioning goes wrong.
OFFSET = [(0, "DP-1", 0, 0, 1920, 1080), (1, "LEFT", -1280, -200, 1280, 1024)]


class FakeUser32:
    """Explorer's desktop as EnumWindows sees it.

    `icons` is the WorkerW that owns SHELLDLL_DefView; `behind` is its next
    sibling, the one a wallpaper belongs in.
    """
    def __init__(self, tops, icons=None, behind=None, progman=None,
                 child_worker=0):
        self.tops, self.icons, self.behind = tops, icons, behind
        #: Windows 11 hangs the wallpaper WorkerW off Progman instead of
        #: creating a top-level one.
        self.progman, self.child_worker = progman, child_worker

    def FindWindowExW(self, parent, after, cls, _name):
        if cls == "SHELLDLL_DefView":
            return 0x1 if parent == self.icons else 0
        if cls == "WorkerW" and parent is None:
            nxt = [h for h in self.tops if h > (after or 0)
                   and h in self.workers]
            return nxt[0] if nxt else 0
        if cls == "WorkerW" and parent == self.progman:
            return self.child_worker
        return 0

    workers = ()


class FakeSDL:
    SDL_FALSE, SDL_TRUE = 0, 1

    def __init__(self, log=None):
        self.pos = self.size = None
        self.bordered = None
        #: Shared with FakeXlib when both are in play, so the order of the
        #: unmap / property change / map dance can be checked across the two.
        self.order = [] if log is None else log
    def SDL_SetWindowPosition(self, _w, x, y):  self.pos = (x, y)
    def SDL_SetWindowSize(self, _w, w, h):
        self.size = (w, h)
        self.order.append("geom")
    def SDL_SetWindowBordered(self, _w, v):     self.bordered = v
    def SDL_HideWindow(self, _w):               self.order.append("hide")
    def SDL_ShowWindow(self, _w):               self.order.append("show")
    def SDL_RaiseWindow(self, _w):              self.order.append("raise")


class FakeBridge:
    def __init__(self): self.toasts = []
    class _Sig:
        def __init__(self, out): self.out = out
        def emit(self, *a): self.out.append(a)
    def __getattr__(self, _n):
        return FakeBridge._Sig(self.toasts)


import contextlib


@contextlib.contextmanager
def faked_sdl(fake):
    """Swap SDL *and* glViewport out for the duration.

    _fit_to_wallpaper resizes the GL viewport along with the window. There is
    no GL context here, and while that call quietly no-ops on Linux, on Windows
    PyOpenGL's error checker raises GL_INVALID_OPERATION -- so this suite could
    not run on the platform the feature is actually for.
    """
    real_sdl, real_vp = viz_common.sdl2, viz_common.glViewport
    viz_common.sdl2 = fake
    viz_common.glViewport = lambda *a: None
    try:
        yield fake
    finally:
        viz_common.sdl2, viz_common.glViewport = real_sdl, real_vp


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


@contextlib.contextmanager
def as_platform(name):
    real = viz_common.sys.platform
    viz_common.sys.platform = name
    try:
        yield
    finally:
        viz_common.sys.platform = real


@contextlib.contextmanager
def as_env(env):
    real = viz_common.os.environ
    viz_common.os.environ = env
    try:
        yield
    finally:
        viz_common.os.environ = real


X11_ENV = {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11",
           "XDG_CURRENT_DESKTOP": "X-Cinnamon"}
WAYLAND_ENV = {"DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland",
               "WAYLAND_DISPLAY": "wayland-0"}


def an_x11_session_can_host_wallpaper_mode():
    with as_platform("linux"):
        ok, why = viz_common.wallpaper_support(X11_ENV)
    assert ok, why

def wayland_is_refused_and_told_the_one_thing_that_would_fix_it():
    """An X11 client under a Wayland compositor is an ordinary toplevel: there
    is no below-the-desktop layer to ask for, on XWayland or natively. The only
    thing the user can do is log in on Xorg, so the reason has to say so."""
    with as_platform("linux"):
        ok, why = viz_common.wallpaper_support(WAYLAND_ENV)
    assert not ok, "offered wallpaper mode on Wayland"
    assert "xorg" in why.lower(), why

def wayland_is_caught_without_xdg_session_type_too():
    """That variable is not set on every login manager. WAYLAND_DISPLAY is."""
    with as_platform("linux"):
        ok, _why = viz_common.wallpaper_support(
            {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"})
    assert not ok

def no_display_at_all_is_refused():
    with as_platform("linux"):
        ok, _why = viz_common.wallpaper_support({})
    assert not ok

def windows_needs_no_session_check():
    with as_platform("win32"):
        ok, _why = viz_common.wallpaper_support({})
    assert ok


def a_session_that_cannot_host_it_refuses_with_a_reason():
    r = bare()
    with as_platform("linux"), as_env(WAYLAND_ENV):
        r._set_wallpaper_mode(True)
    assert r._wallpaper is False, "claimed wallpaper mode where it cannot work"
    assert any("X11" in " ".join(map(str, t)) for t in r._bridge.toasts), \
        r._bridge.toasts


class FakeXlib:
    """Enough of Xlib to record what would have gone on the wire."""
    def __init__(self, log=None):
        self.atoms, self.props, self.deleted = {}, {}, []
        self.lowered = self.synced = 0
        self.order = [] if log is None else log
    def atom(self, name):
        return self.atoms.setdefault(name, 0x1_0000_0001 + len(self.atoms))
    def XInternAtom(self, _dpy, name, _exists):
        return self.atom(name.decode())
    def XChangeProperty(self, _dpy, _win, prop, typ, fmt, _mode, buf, n):
        self.props[prop] = (typ, fmt, list(buf)[:n])
    def XDeleteProperty(self, _dpy, _win, prop):
        self.deleted.append(prop)
    def XLowerWindow(self, _dpy, _win):  self.lowered += 1
    def XSync(self, _dpy, _discard):
        self.synced += 1
        self.order.append("sync")


def the_state_property_is_a_32_bit_atom_list():
    """Format 32 with type ATOM is what a window manager reads. Atom values are
    deliberately above 2^32 here: a signature that lets ctypes narrow one to
    c_int passes every test written with small numbers and fails on a real
    server."""
    lib = FakeXlib()
    wanted = ["_NET_WM_STATE_BELOW", "_NET_WM_STATE_STICKY"]
    viz_common.x11_set_atoms(lib, 1, 2, "_NET_WM_STATE", wanted)
    typ, fmt, vals = lib.props[lib.atom("_NET_WM_STATE")]
    assert fmt == 32, fmt
    assert typ == viz_common._XA_ATOM, typ
    assert vals == [lib.atom(v) for v in wanted], vals

def an_empty_list_deletes_the_property():
    """Leaving _NET_WM_STATE behind on the way out keeps the window below."""
    lib = FakeXlib()
    viz_common.x11_set_atoms(lib, 1, 2, "_NET_WM_STATE", [])
    assert lib.deleted == [lib.atom("_NET_WM_STATE")], lib.deleted
    assert lib.props == {}, lib.props


def _x11_run(on, displays=TWO, cfg=None):
    """_set_x11_wallpaper against a fake Xlib and a fake SDL."""
    r = bare(cfg or {"fullscreen_display": 0}, displays)
    r._native_handle, r._x11_display = 0x2000001, 1
    r._wallpaper = not on
    if not on:
        r._wp_restore = ((10, 20, 640, 480), False, 0, 0)
    log = []
    lib = FakeXlib(log)
    real_x11 = viz_common._libx11
    viz_common._libx11 = lambda: lib
    fake = FakeSDL(log)
    try:
        with faked_sdl(fake):
            r._set_x11_wallpaper(on)
    finally:
        viz_common._libx11 = real_x11
    return r, lib, fake

def becoming_the_desktop_asks_for_the_desktop_type_and_the_bottom_layer():
    r, lib, fake = _x11_run(True)
    typ = lib.props[lib.atom("_NET_WM_WINDOW_TYPE")][2]
    assert typ == [lib.atom("_NET_WM_WINDOW_TYPE_DESKTOP")], typ
    state = lib.props[lib.atom("_NET_WM_STATE")][2]
    for want in viz_common._X11_STATES:
        assert lib.atom(want) in state, want
    assert r._wallpaper is True
    assert fake.size == (1920, 1080), fake.size
    assert fake.bordered == FakeSDL.SDL_FALSE, "the desktop kept its border"

def the_window_is_lowered_under_the_icons():
    """The desktop type puts the window in the bottom layer; it does not say
    where among the windows already there it lands, and the one holding the
    desktop icons is one of them."""
    _r, lib, _fake = _x11_run(True)
    assert lib.lowered == 1, lib.lowered

def the_property_change_is_flushed_before_the_window_comes_back():
    """A window manager reads _NET_WM_WINDOW_TYPE when the window is mapped.
    Map first and it reads the old one, and nothing appears to happen."""
    _r, lib, fake = _x11_run(True)
    assert fake.order.index("sync") < fake.order.index("show"), fake.order
    assert fake.order.index("hide") < fake.order.index("sync"), fake.order

def the_geometry_is_set_again_once_the_window_is_back_up():
    """A window manager places a desktop window itself, and what it decides
    overwrites anything asked for while the window was unmapped."""
    _r, _lib, fake = _x11_run(True)
    assert "show" in fake.order, fake.order
    after = fake.order[fake.order.index("show"):]
    assert "geom" in after, fake.order

def leaving_puts_the_window_back_where_it_was():
    r, lib, fake = _x11_run(False)
    typ = lib.props[lib.atom("_NET_WM_WINDOW_TYPE")][2]
    assert typ == [lib.atom("_NET_WM_WINDOW_TYPE_NORMAL")], typ
    assert lib.deleted == [lib.atom("_NET_WM_STATE")], lib.deleted
    assert r._wallpaper is False
    assert fake.pos == (10, 20) and fake.size == (640, 480), (fake.pos, fake.size)
    assert fake.bordered == FakeSDL.SDL_TRUE, "came back without a border"
    assert lib.lowered == 0, "lowered the window on the way out"

def a_window_that_does_not_exist_yet_is_an_error_not_a_lie():
    r = bare()
    r._native_handle, r._x11_display = 0, None
    try:
        r._set_x11_wallpaper(True)
    except RuntimeError:
        pass
    else:
        assert False, "claimed to make a window that does not exist the desktop"
    assert r._wallpaper is False

def asking_for_the_mode_it_is_already_in_does_nothing():
    r = bare()
    r._wallpaper = True
    r._set_wallpaper_mode(True)
    assert r._bridge.toasts == [], r._bridge.toasts


def a_chosen_monitor_is_the_one_filled():
    r = bare({"fullscreen_display": 1})
    fake = FakeSDL()
    with faked_sdl(fake):
        r._fit_to_wallpaper()
    assert fake.size == (3840, 2160), fake.size
    assert fake.pos == (1920, 0), fake.pos
    assert fake.bordered == FakeSDL.SDL_FALSE, "wallpaper kept its border"
    assert r._borderless is True

def with_no_choice_it_spans_every_monitor():
    r = bare({"fullscreen_display": -1})
    fake = FakeSDL()
    with faked_sdl(fake):
        r._fit_to_wallpaper()
    assert fake.size == (5760, 2160), fake.size
    assert fake.pos == (0, 0), fake.pos

def positions_are_relative_to_the_virtual_desktop_origin():
    """WorkerW starts at the top-left of the virtual desktop, which is not
    (0, 0) when a monitor sits above or left of the primary one."""
    r = bare({"fullscreen_display": 0}, displays=OFFSET)
    fake = FakeSDL()
    with faked_sdl(fake):
        r._fit_to_wallpaper()
    # primary is at (0, 0) in screen coordinates, but the desktop starts at
    # (-1280, -200), so inside WorkerW the primary begins at (1280, 200)
    assert fake.pos == (1280, 200), fake.pos
    assert fake.size == (1920, 1080), fake.size


class _MenuHost:
    """Only what ContextMenu asks a renderer for."""
    def is_locked(self):      return False
    def is_borderless(self):  return False
    def is_fullscreen(self):  return False
    def is_recording(self):   return False
    def is_wallpaper(self):   return False
    def list_displays(self):  return list(TWO)
    def _save_config(self, cfg): pass


def _wallpaper_entries(host, layered=False, support=(True, "")):
    """Build the menu with the desktop lookup and the session check stubbed;
    its wallpaper items, as (text, enabled, tooltip).

    `support` is stubbed rather than driven through the environment because the
    menu is being tested here, not the session check -- and a replaced os.environ
    takes HOME and XDG_* away from everything else the menu builds.
    """
    real_host, real_sup = viz_common.find_desktop_host, viz_common.wallpaper_support
    viz_common.find_desktop_host = lambda *a, **k: (host, layered)
    viz_common.wallpaper_support = lambda *a, **k: support
    try:
        menu = viz_common.ContextMenu(renderer=_MenuHost(), cfg={},
                                      ratings={}, keymaps={})
        return [(a.text(), a.isEnabled(), a.toolTip()) for a in menu.actions()
                if "wallpaper" in a.text().lower()]
    finally:
        viz_common.find_desktop_host = real_host
        viz_common.wallpaper_support = real_sup


def a_session_that_cannot_host_it_gets_a_dead_entry_carrying_the_reason():
    """The menu line has no room for the explanation, and a live entry that
    only ever reaches a toast spends a click to say no. The tooltip carries it."""
    why = "Wallpaper mode needs an X11 session."
    items = _wallpaper_entries(1, support=(False, why))
    assert items, "no wallpaper entry at all"
    text, enabled, tip = items[0]
    assert not enabled, f"offered an entry that cannot work: {text!r}"
    assert "not available" in text.lower(), text
    assert tip == why, tip

def an_x11_desktop_is_offered_the_real_entry():
    """Windows takes a second gate (is Explorer drawing the desktop?); nothing
    else does, so on X11 a supported session is the whole test."""
    if sys.platform == "win32":
        return
    items = _wallpaper_entries(0, support=(True, ""))
    assert items, "no wallpaper entry"
    text, enabled, _tip = items[0]
    assert enabled, f"the entry was disabled on a session that can host it: {text!r}"
    assert "set as desktop wallpaper" in text.lower(), text


def a_machine_with_no_desktop_is_told_so_in_the_menu():
    """No Explorer desktop to draw into -- a third-party shell, say. Offering
    the entry anyway spends a click to reach a toast that vanishes."""
    if sys.platform != "win32":
        return                      # the entry is Windows-only
    items = _wallpaper_entries(0)
    assert items, "no wallpaper entry at all"
    text, enabled, _tip = items[0]
    assert not enabled, f"offered a wallpaper entry that cannot work: {text!r}"
    assert "not available" in text.lower(), text


def a_machine_with_a_workerw_gets_the_real_entry():
    if sys.platform != "win32":
        return
    items = _wallpaper_entries(12345)
    assert items, "no wallpaper entry"
    text, enabled, _tip = items[0]
    assert enabled, f"the entry was disabled despite a WorkerW: {text!r}"
    assert "set as desktop wallpaper" in text.lower(), text

def windows_10_is_still_offered_the_layer_behind_the_icons():
    """A WorkerW behind the icons beats a DefView over them, wherever both
    could be had."""
    u = FakeUser32(tops=[0x10, 0x20, 0x30], icons=0x20, behind=0x30)
    u.workers = (0x20, 0x30)
    assert pick_desktop_host(u, u.tops, 0x99) == (0x30, False)

def windows_11_uses_progmans_own_worker_and_asks_to_be_layered():
    """No second top-level WorkerW is created there. The wallpaper is painted
    by a WorkerW *child* of Progman, and a plain child window parented into it
    is never composited -- only a layered one is."""
    u = FakeUser32(tops=[0x10, 0x20], icons=None, progman=0x99,
                   child_worker=0x77)
    u.workers = (0x20,)
    assert pick_desktop_host(u, u.tops, 0x99) == (0x77, True)

def a_desktop_with_neither_gives_nothing():
    u = FakeUser32(tops=[0x10, 0x20], icons=None, progman=0x99)
    u.workers = (0x20,)
    assert pick_desktop_host(u, u.tops, 0x99) == (0, False)


def the_default_d_key_asks_for_wallpaper_mode():
    """The keybinding the menu entry mirrors. Driven through the real
    _handle_key and _KEY_LOOKUP so it is the shipped dispatch, not a copy."""
    assert viz_common.DEFAULT_KEYMAPS.get("toggle_wallpaper") == "d", \
        "default binding moved -- update the test and the docs together"
    r = object.__new__(Renderer)
    r._keymaps = dict(viz_common.DEFAULT_KEYMAPS)
    r._km = Renderer._km.__get__(r)         # bound, but nothing else is touched
    calls = []
    r.request_wallpaper = lambda *a: calls.append(a)
    sym, _sc = viz_common._KEY_LOOKUP["D"]
    r._handle_key(sym, 0)
    assert calls == [()], f"pressing d did not toggle wallpaper: {calls}"


def the_wallpaper_action_is_in_the_editor():
    """So it can be rebound like every other key."""
    assert "toggle_wallpaper" in viz_common.ACTION_LABELS
    assert "toggle_wallpaper" in viz_common.ACTION_NOTES


for fn in (the_worker_behind_the_icons_is_chosen,
           a_desktop_with_no_defview_gives_nothing,
           a_defview_with_no_sibling_gives_nothing,
           an_x11_session_can_host_wallpaper_mode,
           wayland_is_refused_and_told_the_one_thing_that_would_fix_it,
           wayland_is_caught_without_xdg_session_type_too,
           no_display_at_all_is_refused,
           windows_needs_no_session_check,
           a_session_that_cannot_host_it_refuses_with_a_reason,
           the_state_property_is_a_32_bit_atom_list,
           an_empty_list_deletes_the_property,
           becoming_the_desktop_asks_for_the_desktop_type_and_the_bottom_layer,
           the_window_is_lowered_under_the_icons,
           the_property_change_is_flushed_before_the_window_comes_back,
           the_geometry_is_set_again_once_the_window_is_back_up,
           leaving_puts_the_window_back_where_it_was,
           a_window_that_does_not_exist_yet_is_an_error_not_a_lie,
           asking_for_the_mode_it_is_already_in_does_nothing,
           a_chosen_monitor_is_the_one_filled,
           with_no_choice_it_spans_every_monitor,
           positions_are_relative_to_the_virtual_desktop_origin,
           a_machine_with_no_desktop_is_told_so_in_the_menu,
           a_session_that_cannot_host_it_gets_a_dead_entry_carrying_the_reason,
           an_x11_desktop_is_offered_the_real_entry,
           a_machine_with_a_workerw_gets_the_real_entry,
           windows_10_is_still_offered_the_layer_behind_the_icons,
           windows_11_uses_progmans_own_worker_and_asks_to_be_layered,
           a_desktop_with_neither_gives_nothing,
           the_default_d_key_asks_for_wallpaper_mode,
           the_wallpaper_action_is_in_the_editor):
    results.append(check(fn.__name__, fn))

print("all wallpaper checks passed" if all(results) else "FAILURES")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
