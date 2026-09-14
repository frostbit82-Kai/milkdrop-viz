#!/usr/bin/env python3
"""Wallpaper mode: pick the right WorkerW, and fill the right rectangle.

Windows-only at runtime, so the parts with decisions in them are split out and
checked here: which of Explorer's two WorkerW windows to draw into, and the
geometry that follows. Parenting into the FIRST one is the classic mistake —
it covers the desktop icons instead of sitting behind them.
Run: QT_QPA_PLATFORM=offscreen python test_wallpaper.py
"""
import os, sys, ctypes, time
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
        self.bordered = self.on_top = None
        #: Shared with FakeXlib when both are in play, so the order of the
        #: unmap / property change / map dance can be checked across the two.
        self.order = [] if log is None else log
    def SDL_SetWindowPosition(self, _w, x, y):  self.pos = (x, y)
    def SDL_SetWindowSize(self, _w, w, h):
        self.size = (w, h)
        self.order.append("geom")
    def SDL_SetWindowBordered(self, _w, v):     self.bordered = v
    def SDL_SetWindowAlwaysOnTop(self, _w, v):  self.on_top = v
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
    r._on_top = False
    r._wp_restore = None
    r._wp_hold_at = 0.0
    r._wp_fixes = 0
    r._wp_box_moaned = False
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


#: Window ids for the fake X server: the root, the icon manager's desktop
#: window, our client, and the frame a reparenting window manager wraps it in.
ROOT, ICONS, APP_WIN, FRAME = 0x1, 0x200029, 0x2000001, 0x2000002
#: Unmapped utility windows. A real root has dozens below everything --
#: 81 children on the Mint box -- and no lower gets under them.
JUNK_A, JUNK_B = 0x900001, 0x900002

X11_ENV = {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11",
           "XDG_CURRENT_DESKTOP": "X-Cinnamon"}
WAYLAND_ENV = {"DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland",
               "WAYLAND_DISPLAY": "wayland-0"}


def an_x11_session_can_host_wallpaper_mode():
    with as_platform("linux"):
        ok, why = viz_common.wallpaper_support(X11_ENV)
    assert ok, why

def a_wayland_session_with_xwayland_is_offered_it_too():
    """This used to be a flat refusal, and it turned the feature off on the
    most common Linux desktop there is. The window is an X11 surface either
    way, and mutter puts a desktop-type XWayland window at the bottom of
    _NET_CLIENT_LIST_STACKING and keeps it there -- measured on GNOME. What a
    compositor with no desktop layer does instead is measured after the fact
    by x11_lower_to_bottom, which is a better answer than a guess per
    compositor that goes stale."""
    with as_platform("linux"):
        ok, why = viz_common.wallpaper_support(WAYLAND_ENV)
    assert ok, why

def a_session_with_no_x_server_at_all_is_refused():
    """DISPLAY unset is the one case that cannot work: no Xorg and no
    XWayland means there is nothing to set a window type on."""
    with as_platform("linux"):
        ok, why = viz_common.wallpaper_support(
            {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"})
    assert not ok, "offered wallpaper mode with no X server to ask"
    assert "xwayland" in why.lower(), why

def no_display_at_all_is_refused():
    with as_platform("linux"):
        ok, _why = viz_common.wallpaper_support({})
    assert not ok

def windows_needs_no_session_check():
    with as_platform("win32"):
        ok, _why = viz_common.wallpaper_support({})
    assert ok


def the_on_top_key_flips_it_and_tells_sdl():
    r = bare()
    with faked_sdl(FakeSDL()) as sdl:
        r._toggle_on_top()
        assert r.is_on_top() is True
        assert sdl.on_top == FakeSDL.SDL_TRUE, sdl.on_top
        r._toggle_on_top()
        assert r.is_on_top() is False
        assert sdl.on_top == FakeSDL.SDL_FALSE, sdl.on_top

def parking_as_the_desktop_drops_on_top_first():
    """A desktop window that is also above everything is a contradiction, and
    both platforms wipe the state that carries it on the way back out. It is
    dropped here so the menu never offers to un-set something already gone."""
    r = bare()
    r._on_top = True
    r._native_handle, r._x11_display = 0, None    # parking itself will fail
    with faked_sdl(FakeSDL()) as sdl, as_platform("linux"), as_env(X11_ENV):
        r._set_wallpaper_mode(True)
    assert r.is_on_top() is False, "went to the desktop still claiming to be on top"
    assert sdl.on_top == FakeSDL.SDL_FALSE, sdl.on_top


def a_session_that_cannot_host_it_refuses_with_a_reason():
    r = bare()
    with as_platform("linux"), as_env({"XDG_SESSION_TYPE": "wayland"}):
        r._set_wallpaper_mode(True)
    assert r._wallpaper is False, "claimed wallpaper mode where it cannot work"
    assert any("XWayland" in " ".join(map(str, t)) for t in r._bridge.toasts), \
        r._bridge.toasts


class FakeXlib:
    """Enough of Xlib to record what would have gone on the wire."""
    def __init__(self, log=None):
        self.atoms, self.props, self.deleted = {}, {}, []
        self.lowered = self.synced = 0
        self.order = [] if log is None else log
        #: The root's children, bottom first.
        self.stack = []
        #: What _NET_CLIENT_LIST_STACKING holds: only the windows the window
        #: manager manages, bottom first. Empty means it publishes no such
        #: property, and the code falls back to the root's own children.
        self.clients = []
        #: (window, sibling, detail) for every _NET_RESTACK_WINDOW sent.
        self.restacks = []
        #: client -> frame, for a window manager that reparents.
        self.parent = {}
        #: How many XLowerWindow calls the window manager will undo.
        self.reraises = 0
        #: How many windows sit below anything a lower can reach.
        self.floor = 0
        #: A window manager that publishes a client stack and ignores
        #: _NET_RESTACK_WINDOW all the same -- xfwm4 does exactly this.
        self.refuses_restack = False

    #: Atoms sit above what a signed 32-bit int holds, so a ctypes signature
    #: that narrows one to c_int is caught here rather than sailing past on
    #: small numbers. How far above depends on the C ABI running the test: an
    #: X11 format-32 property is an array of *long*, 8 bytes on Linux and 4 on
    #: Windows, so a base over 2**32 is not something a Windows ctypes can
    #: hold -- it truncates, and five tests then fail on the fixture rather
    #: than on the shipped code. The top bit is what the guard actually needs.
    _ATOM_BASE = (0x1_0000_0001 if ctypes.sizeof(ctypes.c_ulong) >= 8
                  else 0x8000_0001)

    @property
    def bottom(self):
        return self.stack[0] if self.stack else 0
    def atom(self, name):
        return self.atoms.setdefault(name, self._ATOM_BASE + len(self.atoms))
    def XInternAtom(self, _dpy, name, _exists):
        return self.atom(name.decode())
    def XChangeProperty(self, _dpy, _win, prop, typ, fmt, _mode, buf, n):
        self.props[prop] = (typ, fmt, list(buf)[:n])
    def XDeleteProperty(self, _dpy, _win, prop):
        self.deleted.append(prop)
    def XLowerWindow(self, _dpy, win):
        self.lowered += 1
        if win not in self.stack:
            return                      # lowering something not in the stack
        self.stack.remove(win)
        # A window manager restacks a newly mapped window on its own event
        # loop, after ours -- so the first few lowers get undone.
        if self.reraises > 0:
            self.reraises -= 1
            self.stack.append(win)
        else:
            self.stack.insert(self.floor, win)
    def XDefaultRootWindow(self, _dpy):  return ROOT
    def tree(self, win):
        """(parent, children bottom-first) -- what _x11_query_tree extracts."""
        if win == ROOT:
            kids = [w for w in self.stack]
            return 0, kids
        return self.parent.get(win, ROOT), []
    def XSendEvent(self, _dpy, _win, _prop, mask, ev):
        """A window manager that honours _NET_RESTACK_WINDOW.

        `byref` keeps the event reachable through `_obj`, so the message is
        checked as the shipped code built it rather than reimplemented here --
        which is the point, since a wrong ctypes signature is exactly the bug
        a test written against a copy would sail past.
        """
        e = ev._obj.xclient
        assert e.format == 32, e.format
        assert mask == viz_common._X11_WM_EVENT_MASK, mask
        win, sibling, detail = e.window, e.data.l[1], e.data.l[2]
        self.restacks.append((win, sibling, detail))
        if e.message_type != self.atom("_NET_RESTACK_WINDOW"):
            return 0
        if self.refuses_restack:
            return 1
        if detail == 1 and win in self.clients and sibling in self.clients:
            self.clients.remove(win)
            self.clients.insert(self.clients.index(sibling), win)
        return 1

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
    lib.stack = [ICONS, APP_WIN]
    real_x11, real_tree = viz_common._libx11, viz_common._x11_query_tree
    real_origin = viz_common.x11_window_origin
    real_stack = viz_common.x11_client_stack
    real_class = viz_common.x11_wm_class
    real_nemo = viz_common.nemo_allow_desktop_window
    # The fake Xlib has no property reader and gsettings is not this
    # test's business; record what the wallpaper path asked for instead.
    viz_common.x11_wm_class = lambda _l, _dpy, _w: "BomsAIVisualizer"
    viz_common.nemo_allow_desktop_window = (
        lambda c, run=None: log.append(("nemo_ignore", c)) or "")
    viz_common.x11_client_stack = lambda l, _dpy: list(l.clients)
    viz_common._libx11 = lambda: lib
    # XQueryTree's out-parameters are not worth faking; the tree it reports is.
    viz_common._x11_query_tree = lambda l, _dpy, w: l.tree(w)
    # A window manager that places the window exactly where it was asked, so
    # the frame correction is a no-op here. It has its own checks below.
    viz_common.x11_window_origin = lambda l, _dpy, _w: (
        r._wallpaper_box() or (0, 0, 0, 0))[:2]
    fake = FakeSDL(log)
    try:
        with faked_sdl(fake):
            r._set_x11_wallpaper(on)
    finally:
        viz_common._libx11 = real_x11
        viz_common._x11_query_tree = real_tree
        viz_common.x11_window_origin = real_origin
        viz_common.x11_client_stack = real_stack
        viz_common.x11_wm_class = real_class
        viz_common.nemo_allow_desktop_window = real_nemo
    return r, lib, fake


def _lower(reraises, timeout=1.0, reparented=False, clients=None, floor=0,
           refuses=False):
    lib = FakeXlib()
    lib.reraises = reraises
    lib.floor = floor
    lib.refuses_restack = refuses
    if clients is not None:
        lib.clients = list(clients)
    if reparented:
        # xfwm4 puts a frame of its own around the client; the frame is what
        # sits in the root's stack.
        lib.parent[APP_WIN] = FRAME
        lib.stack = [ICONS, FRAME]
    else:
        lib.stack = [ICONS, APP_WIN]
    real = viz_common._x11_query_tree
    real_stack = viz_common.x11_client_stack
    viz_common._x11_query_tree = lambda l, _dpy, w: l.tree(w)
    # XGetWindowProperty's out-parameters are not worth faking either; the
    # list it reports is.
    viz_common.x11_client_stack = lambda l, _dpy: list(l.clients)
    try:
        return lib, viz_common.x11_lower_to_bottom(lib, 1, APP_WIN,
                                                   timeout=timeout)
    finally:
        viz_common._x11_query_tree = real
        viz_common.x11_client_stack = real_stack

def lowering_keeps_at_it_until_the_window_is_really_at_the_bottom():
    """One lower straight after the map is thrown away -- measured under
    muffin, where the visualiser came back up above the icons and a late lower
    stuck. So lower, then check, rather than lower and hope."""
    lib, ok = _lower(reraises=2)
    assert ok, "gave up while the window was still above the icons"
    assert lib.lowered == 3, lib.lowered

def a_reparenting_window_manager_has_its_frame_lowered_not_the_client():
    """xfwm4 wraps even a desktop window in a frame of its own, and the frame
    is what sits in the root's stack. Lowering the client there moves nothing
    and the visuals stay in front of the icons -- measured, before this."""
    lib, ok = _lower(reraises=0, reparented=True)
    assert ok, "never reached the bottom on a reparenting window manager"
    assert lib.stack[0] == FRAME, lib.stack
    assert APP_WIN not in lib.stack, "lowered the client instead of the frame"

def a_compositing_window_manager_is_asked_rather_than_told():
    """XLowerWindow moves the window in the X server's stack. A compositing
    window manager keeps its own and paints from that, so the lower is either
    undone on its next restack or never reaches the screen at all. Measured on
    Cinnamon 6.0.5: the raw X stack had the visualiser below nemo-desktop
    while _NET_CLIENT_LIST_STACKING -- and the screen -- had it above, with
    the icons covered. Asking through _NET_RESTACK_WINDOW is what moves it."""
    lib, ok = _lower(reraises=999, clients=[ICONS, APP_WIN])
    assert ok, "gave up instead of asking the window manager"
    assert lib.clients[0] == APP_WIN, lib.clients
    assert (APP_WIN, ICONS, 1) in lib.restacks, lib.restacks

def the_bottom_of_the_roots_children_is_the_wrong_thing_to_test():
    """The regression this pair exists for. A real desktop's root has dozens
    of children and the lowest are unmapped utility windows nothing can get
    under, so `kids[0]` is never the visualiser however far it is lowered --
    the old test could only time out and toast, on a session where the visuals
    were in fact behind the icons. The managed-client stack has only real
    windows, and answers the question actually being asked."""
    lib, ok = _lower(reraises=0, floor=2, clients=[ICONS, APP_WIN],
                     timeout=0.2)
    assert lib.bottom != APP_WIN, "the junk floor did not hold; test is void"
    assert ok, "reported failure on a desktop where the icons are on top"
    assert lib.clients[0] == APP_WIN, lib.clients

def _align(border, reparented, want=(0, 0)):
    """x11_align_frame against a window manager with a known frame border."""
    lib = FakeXlib()
    if reparented:
        lib.parent[APP_WIN] = FRAME
        lib.stack = [ICONS, FRAME]
    else:
        lib.stack = [ICONS, APP_WIN]
    at = {"client": (0, 0)}
    asked = []
    def move(x, y):
        at["client"] = (x, y); asked.append((x, y))
    def origin(_lib, _dpy, w):
        cx, cy = at["client"]
        if w == FRAME:
            return (cx - border[0], cy - border[1])
        return (cx, cy)
    real_t, real_o = viz_common._x11_query_tree, viz_common.x11_window_origin
    viz_common._x11_query_tree = lambda l, _d, w: l.tree(w)
    viz_common.x11_window_origin = origin
    try:
        ok = viz_common.x11_align_frame(lib, 1, APP_WIN, want, move)
    finally:
        viz_common._x11_query_tree = real_t
        viz_common.x11_window_origin = real_o
    return ok, asked, origin(lib, 1, FRAME if reparented else APP_WIN)

def a_reparented_window_is_nudged_until_its_frame_lands_on_the_box():
    """xfwm4 frames even a desktop window and positions the frame, so asking
    for (0, 0) put the top-left pixel at (5, 29) and left a black strip down
    the right of the screen and along the bottom. Those are the measured
    numbers."""
    ok, asked, frame_at = _align(border=(5, 29), reparented=True)
    assert ok, "never got the frame onto the box"
    assert asked[-1] == (5, 29), asked
    assert frame_at == (0, 0), frame_at

def a_window_that_is_already_placed_is_left_alone():
    ok, asked, _ = _align(border=(0, 0), reparented=True)
    assert ok
    assert asked == [], f"moved a window that was already right: {asked}"

def an_unframed_window_is_simply_asked_again():
    """muffin does not reparent a desktop window: there the client is the frame
    and the correction would double-count a border that does not exist."""
    ok, asked, _ = _align(border=(0, 0), reparented=False, want=(40, 60))
    assert ok
    assert asked == [(40, 60)], asked


def a_desktop_that_never_lets_go_is_reported_rather_than_pretended():
    lib, ok = _lower(reraises=999, timeout=0.2)
    assert not ok, "claimed the window reached the bottom when it never did"
    assert lib.lowered > 1, "did not even retry"

def a_window_manager_that_refuses_to_restack_is_given_up_on_quickly():
    """xfwm4 publishes a client stack and ignores _NET_RESTACK_WINDOW anyway,
    so the answer is settled after the first ask -- but the loop used to keep
    asking until the timeout, freezing the render thread for a whole second on
    every toggle. Unchanged position twice running is a refusal, not slowness.
    The timeout here is long on purpose: it is what the test would run for if
    the early exit were removed."""
    t0 = time.monotonic()
    lib, ok = _lower(reraises=999, clients=[ICONS, APP_WIN], timeout=5.0,
                     refuses=True)
    spent = time.monotonic() - t0
    assert not ok, "claimed success against a window manager that refused"
    assert spent < 1.0, f"waited out {spent:.2f}s of the timeout"
    assert lib.restacks, "gave up without ever asking"

def a_desktop_that_keeps_the_icons_below_says_so():
    """The visuals are up either way; only the stacking failed. A silent
    success there looks exactly like the feature working."""
    r = bare({"fullscreen_display": 0}, TWO)
    r._native_handle, r._x11_display = APP_WIN, 1
    lib = FakeXlib(); lib.reraises = 999; lib.stack = [ICONS, APP_WIN]
    real_x11, real_tree = viz_common._libx11, viz_common._x11_query_tree
    real_origin = viz_common.x11_window_origin
    real_stack = viz_common.x11_client_stack
    viz_common.x11_client_stack = lambda l, _dpy: list(l.clients)
    viz_common._libx11 = lambda: lib
    viz_common._x11_query_tree = lambda l, _dpy, w: l.tree(w)
    viz_common.x11_window_origin = lambda l, _dpy, _w: (
        r._wallpaper_box() or (0, 0, 0, 0))[:2]
    try:
        with faked_sdl(FakeSDL()):
            r._set_x11_wallpaper(True)
    finally:
        viz_common._libx11 = real_x11
        viz_common._x11_query_tree = real_tree
        viz_common.x11_window_origin = real_origin
        viz_common.x11_client_stack = real_stack
    assert r._wallpaper is True, "gave up on the mode over the stacking alone"
    assert any("behind it" in " ".join(map(str, t)) for t in r._bridge.toasts), \
        r._bridge.toasts

# --- staying the right size -----------------------------------------------
FULL = (0, 0, 1920, 1080)


def _box(rect, fixes=0):
    """Run the real _hold_wallpaper_box against a window at `rect`.

    Returns (renderer, moves, fits) -- what it decided to put back.
    """
    r = bare({"fullscreen_display": 0}, TWO)
    r._wallpaper = True
    r._wp_fixes = fixes
    r._native_handle, r._x11_display = APP_WIN, 1
    moves, fits = [], []
    r._fit_to_wallpaper = lambda box=None: fits.append(box)
    lib = type("L", (), {
        "XMoveResizeWindow": staticmethod(
            lambda _d, w, x, y, ww, hh: moves.append((w, x, y, ww, hh))),
        "XSync": staticmethod(lambda _d, _f: None)})()
    real_rect = viz_common.x11_window_rect
    real_origin = viz_common.x11_window_origin
    real_top = viz_common.x11_toplevel_of
    viz_common.x11_window_rect = lambda _l, _d, _w: rect
    viz_common.x11_window_origin = lambda _l, _d, _w: rect[:2]
    viz_common.x11_toplevel_of = lambda _l, _d, w: w
    try:
        r._hold_wallpaper_box(lib, 1, APP_WIN)
    finally:
        viz_common.x11_window_rect = real_rect
        viz_common.x11_window_origin = real_origin
        viz_common.x11_toplevel_of = real_top
    return r, moves, fits


def a_wallpaper_shoved_off_the_bottom_edge_is_put_back():
    """The bug this was written for. x11_align_frame corrects for the window
    manager's frame once, while the window manager is still deciding whether a
    desktop window gets one -- measured on Muffin, it read a 32px frame that
    was gone a moment later and left the window at y=-32 for the rest of the
    session. Nobody sees the strip of real wallpaper that leaves along the
    bottom until the panel covering it is hidden, which is how it was found."""
    r, moves, _f = _box((0, -32, 1920, 1080))
    assert moves == [(APP_WIN, 0, 0, 1920, 1080)], f"never put back: {moves}"


def the_window_is_moved_through_x_and_not_through_sdl():
    """Pins the second half of the fix. SDL drops a request that matches its
    own cached position, and it does match: SDL asked for this box once and
    believes it got it -- the window manager moved the window afterwards and
    SDL never heard. Measured: SDL_SetWindowPosition(0, 0) on a window sitting
    at y=-32 put nothing on the wire at all, and the strip stayed."""
    r, moves, fits = _box((0, -32, 1920, 1080))
    assert moves, "nothing went to the X server"
    assert fits == [], f"went back through SDL, which will ignore it: {fits}"


def a_wallpaper_the_right_size_is_left_alone():
    """Nothing on the wire when the answer is already right -- the same bargain
    the stacking hold makes, because this runs every two seconds too."""
    r, moves, fits = _box(FULL)
    assert moves == [] and fits == [], (moves, fits)


def a_wallpaper_the_panel_shrank_is_grown_back():
    """A panel hidden, shown, or set to auto-hide moves the work area, and a
    window manager that clamped the window to the old one never revisits it.
    The one-shot fit could not see this; re-measuring can. A size change needs
    the GL viewport and projectM told as well, which a move does not."""
    r, moves, fits = _box((0, 0, 1920, 1040))
    assert moves == [(APP_WIN, 0, 0, 1920, 1080)], moves
    assert fits == [FULL], f"resized the window and not the viewport: {fits}"


def a_window_manager_that_insists_is_not_fought_forever():
    """A window manager entitled to place a desktop window its own way will
    undo every correction. Without a stop, that is a fight every two seconds
    for the life of the app, and the window flickers between the two answers."""
    r, moves, _f = _box((0, -32, 1920, 1080),
                        fixes=viz_common.Renderer.WP_FIX_LIMIT)
    assert moves == [], "kept pushing after the limit"


def getting_it_right_again_buys_back_the_budget():
    """The limit is on a *losing streak*, not on the life of the process: a
    panel toggled twenty times is twenty corrections that each stuck."""
    r, _m, _f = _box(FULL, fixes=viz_common.Renderer.WP_FIX_LIMIT)
    assert r._wp_fixes == 0, r._wp_fixes


def the_geometry_cannot_stop_the_stacking_hold():
    """They share a timer but not a fate. The stacking hold backs off for a
    minute when it throws, and a window that cannot be measured is not a
    reason to stop keeping it under the icons for the next sixty seconds."""
    r = bare({"fullscreen_display": 0}, TWO)
    r._wallpaper = True
    r._native_handle, r._x11_display = APP_WIN, 1
    lib = FakeXlib()
    lib.clients = [ICONS, APP_WIN]
    lib.stack = [ICONS, APP_WIN]
    real_x11, real_tree = viz_common._libx11, viz_common._x11_query_tree
    real_stack, real_time = viz_common.x11_client_stack, viz_common._time
    viz_common._libx11 = lambda: lib
    viz_common._x11_query_tree = lambda l, _dpy, w: l.tree(w)
    viz_common.x11_client_stack = lambda l, _dpy: list(l.clients)
    viz_common._time = type("C", (), {"monotonic": staticmethod(lambda: 1000.0)})
    def boom(*_a, **_k):
        raise RuntimeError("no geometry for you")
    r._hold_wallpaper_box = boom
    try:
        r._hold_wallpaper_bottom()
    finally:
        viz_common._libx11 = real_x11
        viz_common._x11_query_tree = real_tree
        viz_common.x11_client_stack = real_stack
        viz_common._time = real_time
    assert lib.restacks, "a broken geometry read stopped the stacking hold"
    assert r._wp_hold_at == 1000.0, f"backed off anyway: {r._wp_hold_at}"


# --- staying at the bottom ------------------------------------------------
def _hold(clients, wallpaper=True, hold_at=0.0, now=1000.0):
    """Run the real _hold_wallpaper_bottom against a fake server.

    Returns (renderer, lib) so a test can look at what went on the wire.
    """
    r = bare({"fullscreen_display": 0}, TWO)
    r._wallpaper = wallpaper
    r._wp_hold_at = hold_at
    r._native_handle, r._x11_display = APP_WIN, 1
    lib = FakeXlib()
    lib.clients = list(clients)
    lib.stack = list(clients)
    real_x11, real_tree = viz_common._libx11, viz_common._x11_query_tree
    real_stack, real_time = viz_common.x11_client_stack, viz_common._time
    viz_common._libx11 = lambda: lib
    viz_common._x11_query_tree = lambda l, _dpy, w: l.tree(w)
    viz_common.x11_client_stack = lambda l, _dpy: list(l.clients)
    viz_common._time = type("C", (), {"monotonic": staticmethod(lambda: now)})
    try:
        r._hold_wallpaper_bottom()
    finally:
        viz_common._libx11 = real_x11
        viz_common._x11_query_tree = real_tree
        viz_common.x11_client_stack = real_stack
        viz_common._time = real_time
    return r, lib


def a_wallpaper_that_has_been_pushed_back_up_is_lowered_again():
    """Getting to the bottom once is not enough. A window manager restacks on
    its own schedule -- a workspace switch, the icon manager restarting -- and
    the toggle cannot know: it measured the stack and was right at the time.
    Reported on Cinnamon as wallpaper mode covering the Nemo icons with no
    toast to say anything had gone wrong."""
    r, lib = _hold([ICONS, APP_WIN])
    assert lib.restacks, "never asked the window manager to put it back"
    win, sibling, detail = lib.restacks[0]
    assert (win, sibling, detail) == (APP_WIN, ICONS, 1), lib.restacks[0]
    assert lib.clients[0] == APP_WIN, lib.clients

def a_wallpaper_already_at_the_bottom_says_nothing():
    """One property read per pass and nothing on the wire unless the answer is
    wrong -- otherwise this is two restack messages a second, for ever."""
    r, lib = _hold([APP_WIN, ICONS])
    assert not lib.restacks, lib.restacks
    assert lib.lowered == 0, lib.lowered

def a_window_manager_with_no_client_stack_is_left_alone():
    """Nothing to measure against, and lowering on a hunch every two seconds
    is how a window ends up fighting the thing above it."""
    r, lib = _hold([])
    assert not lib.restacks, lib.restacks
    assert lib.lowered == 0, lib.lowered

def the_check_is_throttled_rather_than_run_every_frame():
    """At sixty frames a second an unthrottled check is sixty round trips to
    the X server a second, for a thing that changes once an hour."""
    r, lib = _hold([ICONS, APP_WIN], hold_at=999.5, now=1000.0)
    assert not lib.restacks, "checked again 0.5s after the last one"
    assert r._wp_hold_at == 999.5, "the clock was reset by a skipped pass"

def a_failure_backs_off_instead_of_repeating_every_two_seconds():
    r = bare({"fullscreen_display": 0}, TWO)
    r._wallpaper = True
    r._wp_hold_at = 0.0
    r._native_handle, r._x11_display = APP_WIN, 1
    real_x11, real_time = viz_common._libx11, viz_common._time
    viz_common._libx11 = lambda: (_ for _ in ()).throw(OSError("no libX11"))
    viz_common._time = type("C", (), {"monotonic": staticmethod(lambda: 1000.0)})
    try:
        r._hold_wallpaper_bottom()
    finally:
        viz_common._libx11 = real_x11
        viz_common._time = real_time
    assert r._wp_hold_at > 1000.0 + 30, r._wp_hold_at


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
    def is_on_top(self):      return False
    def is_fullscreen(self):  return False
    def is_recording(self):   return False
    def is_wallpaper(self):   return False
    def list_displays(self):  return list(TWO)
    def _save_config(self, cfg): pass


class _FakeTray:
    """QSystemTrayIcon, for the one static the menu asks about. The offscreen
    Qt platform has no tray, so the real class answers False here whatever the
    test is about."""
    available = True

    @classmethod
    def isSystemTrayAvailable(cls):
        return cls.available


def _wallpaper_entries(host, layered=False, support=(True, ""), tray=True):
    """Build the menu with the desktop lookup, the session check and the tray
    stubbed; its wallpaper items, as (text, enabled, tooltip).

    `support` is stubbed rather than driven through the environment because the
    menu is being tested here, not the session check -- and a replaced os.environ
    takes HOME and XDG_* away from everything else the menu builds.
    """
    real_host, real_sup = viz_common.find_desktop_host, viz_common.wallpaper_support
    real_tray = viz_common.QSystemTrayIcon
    viz_common.find_desktop_host = lambda *a, **k: (host, layered)
    viz_common.wallpaper_support = lambda *a, **k: support
    _FakeTray.available = tray
    viz_common.QSystemTrayIcon = _FakeTray
    try:
        menu = viz_common.ContextMenu(renderer=_MenuHost(), cfg={},
                                      ratings={}, keymaps={})
        return [(a.text(), a.isEnabled(), a.toolTip()) for a in menu.actions()
                if "wallpaper" in a.text().lower()]
    finally:
        viz_common.find_desktop_host = real_host
        viz_common.wallpaper_support = real_sup
        viz_common.QSystemTrayIcon = real_tray


def a_desktop_with_no_tray_is_not_offered_a_one_way_trip():
    """Wallpaper mode gets no keyboard and no mouse: the icons above it take
    both. The tray icon is the only way back, so on a desktop without one --
    a GNOME session with no AppIndicator extension, say -- switching it on
    ends in killing the process."""
    items = _wallpaper_entries(1, support=(True, ""), tray=False)
    assert items, "no wallpaper entry at all"
    text, enabled, tip = items[0]
    assert not enabled, f"offered a one-way trip: {text!r}"
    assert "tray" in tip.lower(), tip


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


# ── Nemo gives up the desktop when another window claims it ──────────────────

class FakeGSettings:
    """`gsettings` as a Nemo session answers it.

    `value` None means the schema is not there at all, which is every desktop
    that is not Cinnamon and is the only signal this has to go on.
    """

    def __init__(self, value=None, set_rc=0):
        self.value = value
        self.set_rc = set_rc
        self.calls = []
        self.envs = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        self.envs.append(kw.get("env"))
        assert argv[0] == "gsettings", argv

        class R:
            pass
        r = R()
        if argv[1] == "get":
            r.returncode = 0 if self.value is not None else 1
            # An empty array prints with its type spelled out, because `[]`
            # on its own does not say what is in it.
            r.stdout = "" if self.value is None else (
                "@as []\n" if not self.value else repr(self.value) + "\n")
        else:
            r.returncode = self.set_rc
            r.stdout = ""
            if self.set_rc == 0:
                import ast as _ast
                self.value = _ast.literal_eval(argv[4])
        return r

    def sets(self):
        return [c for c in self.calls if c[1] == "set"]


STOCK = ["conky", "csd-background"]


def nemo_is_told_to_keep_the_icons():
    """The one that matters. Without this Nemo finds a desktop-type window at
    startup, prints "Desktop already managed by another application", and draws
    no icons at all -- which looks exactly like the visuals covering them."""
    gs = FakeGSettings(list(STOCK))
    said = viz_common.nemo_allow_desktop_window("BomsAIVisualizer", run=gs)
    assert said, "nothing was reported and nothing was written"
    assert gs.value == STOCK + ["BomsAIVisualizer"], gs.value


def the_entries_already_there_are_not_trampled():
    """conky and csd-background are on that list because somebody else needs
    them. Writing the key is a read-modify-write, not a set."""
    gs = FakeGSettings(list(STOCK))
    viz_common.nemo_allow_desktop_window("BomsAIVisualizer", run=gs)
    for stock in STOCK:
        assert stock in gs.value, gs.value


def being_on_the_list_already_writes_nothing():
    """This runs on every entry into wallpaper mode, so the steady state has
    to be a single read."""
    gs = FakeGSettings(STOCK + ["BomsAIVisualizer"])
    assert viz_common.nemo_allow_desktop_window("BomsAIVisualizer", run=gs) == ""
    assert gs.sets() == [], gs.sets()


def a_desktop_that_is_not_nemo_is_left_alone():
    """No schema, no Nemo. Nothing else publishes this key, and writing to a
    schema that is not installed is an error, not a no-op."""
    gs = FakeGSettings(None)
    assert viz_common.nemo_allow_desktop_window("BomsAIVisualizer", run=gs) == ""
    assert gs.sets() == [], gs.sets()


def a_machine_with_no_gsettings_at_all_says_nothing():
    def explode(argv, **kw):
        raise FileNotFoundError("gsettings")
    assert viz_common.nemo_allow_desktop_window("BomsAIVisualizer",
                                                run=explode) == ""


def a_wm_class_that_is_not_a_wm_class_is_refused():
    """The value is pasted into a gsettings argument. Nothing that is not a
    plain WM_CLASS gets that far -- and a window with no WM_CLASS at all reads
    back as the empty string, which must not append an empty entry."""
    for junk in ("", "'; rm -rf /", "two words", "a" * 80, None):
        gs = FakeGSettings(list(STOCK))
        assert viz_common.nemo_allow_desktop_window(junk, run=gs) == "", junk
        assert gs.calls == [], (junk, gs.calls)


def a_refused_write_is_not_reported_as_done():
    gs = FakeGSettings(list(STOCK), set_rc=1)
    assert viz_common.nemo_allow_desktop_window("BomsAIVisualizer", run=gs) == ""


def the_instance_name_is_what_nemo_matches():
    """WM_CLASS is instance then class, and the stock entries are instance
    names -- conky's class is `Conky`. Reading the class half would put a name
    on the list that Nemo never compares against."""
    import viz_common as v
    src = v.x11_wm_class.__doc__ or ""
    assert "instance" in src.lower(), src



def a_list_somebody_has_emptied_is_still_written_to():
    """`gsettings get` prints an empty array as `@as []`, not as `[]` -- the
    type has to be spelled out where there are no members to infer it from.
    Parsing that as a plain literal fails, and failing quietly here would skip
    the fix for the one person who had cleared the key."""
    gs = FakeGSettings([])
    assert viz_common.nemo_allow_desktop_window("BomsAIVisualizer", run=gs)
    assert gs.value == ["BomsAIVisualizer"], gs.value



def gsettings_is_run_outside_the_bundles_library_path():
    """The one that made the fix look like it worked when it did not. A system
    binary run from inside a PyInstaller bundle inherits LD_LIBRARY_PATH
    pointing at `_internal`; the bundled libgio then cannot load the system
    dconf module, falls back to the keyfile backend, and every call succeeds
    against a store nothing else on the desktop reads. Measured: the app
    reported writing Nemo's list while the value in dconf never moved."""
    import os as _os
    saved = dict(_os.environ)
    try:
        _os.environ["LD_LIBRARY_PATH"] = "/somewhere/_internal"
        _os.environ["LD_LIBRARY_PATH_ORIG"] = "/usr/lib/real"
        gs = FakeGSettings(list(STOCK))
        viz_common.nemo_allow_desktop_window("BomsAIVisualizer", run=gs)
        assert gs.envs, "no environment was passed to gsettings at all"
        for env in gs.envs:
            assert env is not None, "gsettings inherited the bundle's env"
            assert env.get("LD_LIBRARY_PATH") == "/usr/lib/real", env.get(
                "LD_LIBRARY_PATH")
            assert "LD_LIBRARY_PATH_ORIG" not in env, env.keys()
    finally:
        _os.environ.clear()
        _os.environ.update(saved)


def an_unfrozen_run_keeps_the_environment_it_was_given():
    """The seam must not start stripping a developer's own LD_LIBRARY_PATH on a
    source run, where there is no bundle and nothing to undo."""
    import os as _os
    saved = dict(_os.environ)
    try:
        _os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
        _os.environ["LD_LIBRARY_PATH"] = "/home/someone/lib"
        assert viz_common.system_env().get(
            "LD_LIBRARY_PATH") == "/home/someone/lib"
    finally:
        _os.environ.clear()
        _os.environ.update(saved)



def becoming_the_desktop_asks_nemo_to_keep_the_icons():
    """The wiring, not the logic -- the logic is checked directly above. What
    this pins is that the ask happens on the way *in* and not on the way out:
    taking the app back off that list when wallpaper mode ends would lose the
    icons again on the next restart, for a window that is no longer there."""
    _r, lib_on, _f = _x11_run(True)
    assert ("nemo_ignore", "BomsAIVisualizer") in lib_on.order, lib_on.order
    _r2, lib_off, _f2 = _x11_run(False)
    assert not [e for e in lib_off.order if e[0] == "nemo_ignore"], lib_off.order



for fn in (the_worker_behind_the_icons_is_chosen,
           a_desktop_with_no_defview_gives_nothing,
           a_defview_with_no_sibling_gives_nothing,
           an_x11_session_can_host_wallpaper_mode,
           a_wayland_session_with_xwayland_is_offered_it_too,
           a_session_with_no_x_server_at_all_is_refused,
           no_display_at_all_is_refused,
           windows_needs_no_session_check,
           a_session_that_cannot_host_it_refuses_with_a_reason,
           the_state_property_is_a_32_bit_atom_list,
           an_empty_list_deletes_the_property,
           becoming_the_desktop_asks_for_the_desktop_type_and_the_bottom_layer,
           the_window_is_lowered_under_the_icons,
           a_wallpaper_that_has_been_pushed_back_up_is_lowered_again,
           a_wallpaper_already_at_the_bottom_says_nothing,
           a_window_manager_with_no_client_stack_is_left_alone,
           the_check_is_throttled_rather_than_run_every_frame,
           a_failure_backs_off_instead_of_repeating_every_two_seconds,
           lowering_keeps_at_it_until_the_window_is_really_at_the_bottom,
           a_reparenting_window_manager_has_its_frame_lowered_not_the_client,
           a_reparented_window_is_nudged_until_its_frame_lands_on_the_box,
           a_window_that_is_already_placed_is_left_alone,
           an_unframed_window_is_simply_asked_again,
           a_desktop_that_never_lets_go_is_reported_rather_than_pretended,
           a_compositing_window_manager_is_asked_rather_than_told,
           the_bottom_of_the_roots_children_is_the_wrong_thing_to_test,
           a_window_manager_that_refuses_to_restack_is_given_up_on_quickly,
           a_desktop_that_keeps_the_icons_below_says_so,
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
           a_desktop_with_no_tray_is_not_offered_a_one_way_trip,
           a_machine_with_a_workerw_gets_the_real_entry,
           windows_10_is_still_offered_the_layer_behind_the_icons,
           windows_11_uses_progmans_own_worker_and_asks_to_be_layered,
           a_desktop_with_neither_gives_nothing,
           the_default_d_key_asks_for_wallpaper_mode,
           the_wallpaper_action_is_in_the_editor,
           the_on_top_key_flips_it_and_tells_sdl,
           parking_as_the_desktop_drops_on_top_first,
           nemo_is_told_to_keep_the_icons,
           the_entries_already_there_are_not_trampled,
           being_on_the_list_already_writes_nothing,
           a_desktop_that_is_not_nemo_is_left_alone,
           a_machine_with_no_gsettings_at_all_says_nothing,
           a_wm_class_that_is_not_a_wm_class_is_refused,
           a_refused_write_is_not_reported_as_done,
           the_instance_name_is_what_nemo_matches,
           a_list_somebody_has_emptied_is_still_written_to,
           gsettings_is_run_outside_the_bundles_library_path,
           an_unfrozen_run_keeps_the_environment_it_was_given,
           becoming_the_desktop_asks_nemo_to_keep_the_icons,
           a_wallpaper_shoved_off_the_bottom_edge_is_put_back,
           the_window_is_moved_through_x_and_not_through_sdl,
           a_wallpaper_the_right_size_is_left_alone,
           a_wallpaper_the_panel_shrank_is_grown_back,
           a_window_manager_that_insists_is_not_fought_forever,
           getting_it_right_again_buys_back_the_budget,
           the_geometry_cannot_stop_the_stacking_hold):
    results.append(check(fn.__name__, fn))

print("all wallpaper checks passed" if all(results) else "FAILURES")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
