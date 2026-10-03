#!/usr/bin/env python3
"""Checks for the preset lock. Run: python test_lock.py

A lock holds the *clock*, not the controls. Next, previous, random and a jump
from the browser all still move while it is on; the only thing that stops is
the preset changing by itself. That distinction is one `if` in four methods
and one `if` in the render loop, so it is exactly the kind of thing that goes
back the way it was in a later edit -- hence a test per direction.

The real methods are run against stubs rather than copies of their logic, and
the auto-advance block is pulled out of Renderer.run with ast, so both halves
fail if the shipped source drifts.
"""
import ast
import inspect
import os
import sys
import textwrap

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_common as v                                # noqa: E402

v._random.seed(4321)


class FakeSignal:
    def __init__(self):
        self.sent = []

    def emit(self, *a):
        self.sent.append(a[0] if len(a) == 1 else a)


class FakeBridge:
    def __init__(self):
        self.status_message = FakeSignal()
        self.lock_changed = FakeSignal()


class FakeNativePlaylist:
    """_do_random goes at the native playlist directly, so the stub has to
    carry the same three C entry points rather than the Python wrapper."""
    def __init__(self, owner):
        self._owner = owner

    def projectm_playlist_size(self, pl):
        return len(self._owner.names)

    def projectm_playlist_item(self, pl, i):
        return self._owner.names[i].encode()

    def projectm_playlist_set_position(self, pl, i, hard):
        self._owner.pos = int(i) % len(self._owner.names)

    def projectm_playlist_get_position(self, pl):
        return self._owner.pos


class FakePM:
    """The playlist surface the four navigation methods touch."""
    def __init__(self, count=40):
        self.names = [f"p{i:03d}" for i in range(count)]
        self.pos = 0
        self._pl, self._playlist = object(), FakeNativePlaylist(self)

    def next_preset(self, hard_cut=True):
        self.pos = (self.pos + 1) % len(self.names)

    def prev_preset(self, hard_cut=True):
        self.pos = (self.pos - 1) % len(self.names)

    def set_playlist_position(self, i, hard_cut=True):
        self.pos = int(i) % len(self.names)

    def playlist_size(self):
        return len(self.names)

    def set_preset_duration(self, s):
        pass

    def playlist_item(self, i):
        return self.names[i]

    def playlist_position(self):
        return self.pos

    def current_preset_name(self):
        return self.names[self.pos]


class StubRenderer(v.Renderer):
    """The real _do_next / _do_prev / _do_random / _do_goto over a fake
    playlist. Nothing else of the renderer is built."""
    def __init__(self, locked=True):
        self._pm = FakePM()
        # Two visuals: the second is what auto-advance also steps forward.
        self._pms = [self._pm, None]
        self._focus = 0
        self._bridge = FakeBridge()
        self._locked = locked
        self._rated = False
        self._rated_history = []
        self._ratings = {}
        self._cfg = {"preset_duration": 30, "preset_random_extra": 0}
        self._preset_start = 0.0
        self._preset_duration = 0.0
        self._emitted = 0

    def _emit_current_preset(self):
        self._emitted += 1


def check(name, fn):
    fn()
    print(f"  ok  {name}")


# --- the controls stay live ----------------------------------------------
def a_locked_preset_still_steps_forward():
    """The whole request: locking is "stop changing on your own", and a user
    who has locked one they like still wants to look at the next."""
    r = StubRenderer(locked=True)
    r._do_next(hard=True)
    assert r._pm.pos == 1, f"next did nothing while locked (pos {r._pm.pos})"
    assert r._emitted == 1, "the new preset was never announced"


def a_locked_preset_still_steps_back():
    r = StubRenderer(locked=True)
    r._pm.pos = 5
    r._do_prev(hard=True)
    assert r._pm.pos == 4, f"previous did nothing while locked (pos {r._pm.pos})"


def a_locked_preset_still_answers_random():
    r = StubRenderer(locked=True)
    moved = False
    for _ in range(20):          # a random pick can land where it already is
        before = r._pm.pos
        r._do_random()
        moved = moved or r._pm.pos != before
    assert moved, "random never moved while locked"


def a_locked_preset_still_answers_a_jump_from_the_browser():
    """Double-clicking a name in the preset browser goes through _do_goto. A
    lock that swallowed it would look like the browser was broken."""
    r = StubRenderer(locked=True)
    r._do_goto(12)
    assert r._pm.pos == 12, f"the browser jump was swallowed (pos {r._pm.pos})"


def the_wrapper_moves_the_playlist_while_the_core_is_locked():
    """The guard used to sit in ProjectM4 as well, so the renderer could be
    fixed and the app still not move. Measured against libprojectM 4.1.0:
    play_next, play_previous and set_position all advance under the core lock.
    """
    class FakeCore:
        def __init__(self):
            self.locked = None

        def projectm_set_preset_locked(self, pm, locked):
            self.locked = locked

    class FakePlaylist:
        def __init__(self):
            self.pos = 0

        def projectm_playlist_play_next(self, pl, hard):
            self.pos += 1

        def projectm_playlist_play_previous(self, pl, hard):
            self.pos -= 1

    pm = object.__new__(v.ProjectM4)      # the real methods, no ctypes setup
    pm._core, pm._playlist = FakeCore(), FakePlaylist()
    pm._pm, pm._pl = object(), object()
    pm._preset_files, pm._preset_index = [], 0
    pm._locked = False

    pm.set_locked(True)
    assert pm._core.locked is True, "the core was never told about the lock"
    pm.next_preset()
    assert pm._playlist.pos == 1, "next_preset was swallowed by the lock"
    pm.prev_preset()
    assert pm._playlist.pos == 0, "prev_preset was swallowed by the lock"


# --- the clock still stops ------------------------------------------------
def _auto_advance_block():
    """The shipped auto-advance `if`, lifted out of Renderer.run.

    It lives in the middle of a method that needs SDL, a GL context and an
    audio device, so the statement is pulled out and run on its own rather
    than reimplemented here -- a copy would keep passing after the real one
    lost its guard.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(v.Renderer.run)))
    for node in ast.walk(tree):
        if (isinstance(node, ast.If)
                and isinstance(node.test, ast.UnaryOp)
                and isinstance(node.test.op, ast.Not)
                and ast.unparse(node.test.operand) == "self._locked"):
            return compile(ast.Module(body=[node], type_ignores=[]),
                           "<auto-advance>", "exec")
    raise AssertionError("no `if not self._locked:` left in Renderer.run — "
                         "the auto-advance guard is gone")


class Clock:
    """Stands in for the module's _time, so the test owns the wall clock."""
    def __init__(self, now):
        self.now = now

    def monotonic(self):
        return self.now


def the_timer_is_the_one_thing_the_lock_holds():
    code = _auto_advance_block()
    r = StubRenderer(locked=True)
    r._preset_duration = 10.0
    fired = []
    r._do_next = lambda hard: fired.append(hard)

    exec(code, {"_time": Clock(999.0)}, {"self": r})
    assert not fired, "the timer fired while the preset was locked"

    r._locked = False
    exec(code, {"_time": Clock(999.0)}, {"self": r})
    assert fired == [False], f"the timer did not fire once unlocked: {fired}"


# --- the app is the only clock --------------------------------------------
# 3 Oct 2026, shipped defaults: projectM's own 30 s timer asked its playlist
# for the next preset, the playlist played it, and nothing above heard -- then
# the app's 30-45 s timer moved again. Every other preset was up for a few
# seconds, unnamed, and outside rated play. Hard cuts took the same road.
class Settable(FakePM):
    """Takes every setter _apply_pm_settings calls, and keeps the two that
    matter here."""
    def __init__(self):
        super().__init__()
        self.duration, self.handler = None, None

    def set_preset_duration(self, s):
        self.duration = s

    def take_switch_requests(self, handler):
        self.handler = handler

    def __getattr__(self, name):
        if name.startswith("set_"):
            return lambda *a, **k: None
        raise AttributeError(name)


def projectms_own_clock_never_moves_the_playlist():
    r = StubRenderer(locked=False)
    r._note_switch_request(r._pm, False)
    r._take_beat_cuts()
    assert r._pm.pos == 0 and r._emitted == 0, "projectM's timer moved it"


def a_hard_cut_is_the_apps_next_and_is_announced():
    r = StubRenderer(locked=False)
    r._note_switch_request(r._pm, True)
    assert r._pm.pos == 0, "acted inside projectM's render call"
    r._take_beat_cuts()
    assert r._pm.pos == 1 and r._emitted == 1, (r._pm.pos, r._emitted)
    assert r._preset_start > 0, "the app's clock was not restarted"
    r._take_beat_cuts()
    assert r._pm.pos == 1, "one beat cut taken twice"


def a_hard_cut_on_the_other_visual_moves_only_that_one():
    r = StubRenderer(locked=False)
    other = FakePM()
    r._pms[1] = other
    r._note_switch_request(other, True)
    r._take_beat_cuts()
    assert other.pos == 1 and r._pm.pos == 0 and r._emitted == 0


def both_visuals_hear_through_the_app_with_its_duration():
    r = StubRenderer(locked=False)
    one, two = Settable(), Settable()
    r._pms = [one, two]
    r._next_duration()
    assert one.duration == two.duration == r._preset_duration, \
        (one.duration, two.duration, r._preset_duration)
    r._pm = one
    r._apply_pm_settings(r._cfg, two)
    assert two.handler is not None, "the second visual's requests go to its playlist"
    two.handler(True)
    r._take_beat_cuts()
    assert two.pos == 1 and one.pos == 0
    assert "self._take_beat_cuts()" in inspect.getsource(v.Renderer.run), \
        "the render loop no longer makes the beat cuts it was told about"


def the_wrapper_takes_the_request_from_the_playlist():
    class FakeCore:
        got = None
        def projectm_set_preset_switch_requested_event_callback(self, pm, cb, u):
            FakeCore.got = cb
    pm = object.__new__(v.ProjectM4)
    pm._core, pm._pm = FakeCore(), object()
    heard = []
    pm.take_switch_requests(heard.append)
    assert FakeCore.got is not None, "nothing registered with projectM"
    FakeCore.got(True, None)
    FakeCore.got(False, None)
    assert heard == [True, False], heard


results = []
for fn in (a_locked_preset_still_steps_forward,
           a_locked_preset_still_steps_back,
           a_locked_preset_still_answers_random,
           a_locked_preset_still_answers_a_jump_from_the_browser,
           the_wrapper_moves_the_playlist_while_the_core_is_locked,
           the_timer_is_the_one_thing_the_lock_holds,
           projectms_own_clock_never_moves_the_playlist,
           a_hard_cut_is_the_apps_next_and_is_announced,
           a_hard_cut_on_the_other_visual_moves_only_that_one,
           both_visuals_hear_through_the_app_with_its_duration,
           the_wrapper_takes_the_request_from_the_playlist):
    check(fn.__name__, fn)
    results.append(True)

print("all lock checks passed" if all(results) else "FAILURES")
sys.exit(0 if all(results) else 1)
