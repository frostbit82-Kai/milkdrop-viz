#!/usr/bin/env python3
"""Checks the idle pause. Run: python test_idle_pause.py

A wallpaper that keeps a GPU busy behind a full-screen game is the complaint
this answers, and the way to get it wrong is to freeze a visualiser somebody is
actually watching. So the policy is a plain function, tested here as a matrix,
and the loop's state machine is tested for the two things that are easy to
break: the preset clock running on while nothing is drawn, and pausing in the
middle of a recording.

No display and no audio device needed.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_common as v                                # noqa: E402


def check(name, fn):
    fn()
    print(f"  ok  {name}")


# ── the policy ────────────────────────────────────────────────────────────

def the_default_scope_is_wallpaper_only():
    """projectM animates without audio. Freezing that in a window someone is
    watching reads as a crash, which is why "always" is not the default."""
    assert not v.should_idle("wallpaper", False, 999, 20, False)
    assert v.should_idle("wallpaper", True, 999, 20, False)


def never_means_never():
    assert not v.should_idle("never", True, 999, 20, True)


def always_ignores_wallpaper_mode():
    assert v.should_idle("always", False, 999, 20, False)


def a_fullscreen_app_pauses_without_waiting_for_silence():
    """A game is usually making noise, so the silence timer would never fire."""
    assert v.should_idle("wallpaper", True, 0, 20, True)


def silence_has_to_last_the_configured_time():
    assert not v.should_idle("always", False, 19.9, 20, False)
    assert v.should_idle("always", False, 20.0, 20, False)


def zero_seconds_disables_the_silence_rule_only():
    """The spin box's "Never on silence" row. A full-screen app still pauses."""
    assert not v.should_idle("always", False, 99999, 0, False)
    assert v.should_idle("always", False, 0, 0, True)


def off_windows_nothing_is_ever_fullscreen():
    """X11 has no cheap cross-process answer, so the silence timer is the
    whole mechanism there. Asserted so the Windows-only branch stays that."""
    if sys.platform != "win32":
        assert v.fullscreen_app_running() is False


# ── the loop's state machine ──────────────────────────────────────────────

class FakeLoop:
    """Just the attributes Renderer._check_idle touches."""
    FS_APP_POLL_S = v.Renderer.FS_APP_POLL_S

    def __init__(self, **cfg):
        self._rec = None
        self._wallpaper = True
        self._cfg = dict(pause_when_idle="wallpaper",
                         idle_silence_seconds=20, **cfg)
        self._last_sound = 0.0
        self._idle = False
        self._idle_since = 0.0
        self._fs_app = False
        self._fs_app_checked = 0.0
        self._preset_start = 0.0

    idle = v.Renderer._check_idle


def _at(monotonic):
    """Pin time.monotonic for the duration of one call."""
    real = v._time.monotonic
    v._time.monotonic = lambda: monotonic
    return real


def the_first_pass_is_never_idle():
    """_last_sound starts at 0, which as an epoch would read as decades of
    silence and pause the very first frame."""
    loop = FakeLoop()
    real = _at(1000.0)
    try:
        assert loop.idle() is False
        assert loop._last_sound == 1000.0
    finally:
        v._time.monotonic = real


def silence_pauses_and_sound_resumes():
    loop = FakeLoop()
    real = v._time.monotonic
    try:
        v._time.monotonic = lambda: 1000.0
        loop.idle()                       # seeds _last_sound
        v._time.monotonic = lambda: 1025.0
        assert loop.idle() is True
        loop._last_sound = 1025.0         # the render loop's own update
        assert loop.idle() is False
    finally:
        v._time.monotonic = real


def a_pause_does_not_burn_through_the_preset():
    """Without this a wallpaper paused overnight comes back having "played"
    hundreds of presets it never showed, and the one on screen is stale."""
    loop = FakeLoop()
    real = v._time.monotonic
    try:
        v._time.monotonic = lambda: 1000.0
        loop.idle()
        loop._preset_start = 995.0
        v._time.monotonic = lambda: 1025.0
        assert loop.idle() is True        # paused at t=1025
        v._time.monotonic = lambda: 1325.0
        loop._last_sound = 1325.0
        assert loop.idle() is False       # 300s later
        assert loop._preset_start == 1295.0, loop._preset_start
    finally:
        v._time.monotonic = real


def recording_is_never_paused():
    """The sound tap keeps running whatever the render loop does, so a pause
    here leaves a clip whose audio is minutes longer than its video."""
    loop = FakeLoop()
    loop._rec = object()
    real = _at(1000.0)
    try:
        loop._last_sound = 900.0          # a hundred seconds of silence
        assert loop.idle() is False
        assert loop._idle is False
    finally:
        v._time.monotonic = real


def the_fullscreen_check_is_not_run_every_frame():
    """It is a shell call, and at 60fps that is 60 of them a second."""
    calls = []
    real_fn, real_time = v.fullscreen_app_running, v._time.monotonic
    try:
        v.fullscreen_app_running = lambda: (calls.append(1), False)[1]
        loop = FakeLoop()
        for t in (1000.0, 1000.5, 1001.0, 1001.5):
            v._time.monotonic = lambda t=t: t
            loop.idle()
        assert len(calls) == 1, calls
        v._time.monotonic = lambda: 1003.0
        loop.idle()
        assert len(calls) == 2, calls
    finally:
        v.fullscreen_app_running, v._time.monotonic = real_fn, real_time


def a_fullscreen_app_pauses_the_loop():
    real_fn, real_time = v.fullscreen_app_running, v._time.monotonic
    try:
        v.fullscreen_app_running = lambda: True
        loop = FakeLoop()
        v._time.monotonic = lambda: 1000.0
        assert loop.idle() is True
    finally:
        v.fullscreen_app_running, v._time.monotonic = real_fn, real_time


# ── where the check sits in the loop ──────────────────────────────────────

def the_idle_check_comes_before_the_auto_advance():
    """Measured live: with the check below auto-advance, a paused wallpaper
    kept rotating presets nobody could see, and came back on a preset that had
    never been drawn. The order in the loop is the whole fix, and nothing else
    in this file can see it -- there is no GPU here to run the loop on."""
    import inspect
    body = inspect.getsource(v.Renderer.run)
    idle = body.index("── Idle pause ─")
    advance = body.index("── Auto-advance ─")
    render = body.index("── Render ─")
    assert idle < advance < render, (idle, advance, render)


for name, fn in list(globals().items()):
    if callable(fn) and not name.startswith(("_", "check", "Fake")) \
            and getattr(fn, "__module__", None) == "__main__":
        check(name, fn)
print("all idle-pause checks passed")
sys.stdout.flush()
os._exit(0)
