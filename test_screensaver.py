#!/usr/bin/env python3
"""Screensaver mode: the Windows argument contract, and what counts as "back".

A screensaver that quits on the first stray mouse event never stays up — a
mouse resting on a soft surface reports motion on its own — so movement has to
clear a slack threshold, and the very first motion event is where the pointer
already was rather than a move.
Run: QT_QPA_PLATFORM=offscreen python test_screensaver.py
"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sdl2
from viz_common import Renderer
import main as app_main


def motion(x, y):
    e = sdl2.SDL_Event()
    e.type = sdl2.SDL_MOUSEMOTION
    e.motion.x, e.motion.y = x, y
    return e


def event(kind):
    e = sdl2.SDL_Event()
    e.type = kind
    return e


def saver():
    r = object.__new__(Renderer)
    r._screensaver = True
    r._ss_origin = None
    return r


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    print(f"  ok  {name}")
    return True


results = []


def a_keypress_ends_it():
    assert saver()._ss_should_quit(event(sdl2.SDL_KEYDOWN))

def a_click_ends_it():
    assert saver()._ss_should_quit(event(sdl2.SDL_MOUSEBUTTONDOWN))

def a_window_event_does_not():
    assert not saver()._ss_should_quit(event(sdl2.SDL_WINDOWEVENT))

def the_first_motion_event_is_not_a_move():
    """SDL reports where the pointer already is; taking that as movement
    closes the screensaver the instant it opens."""
    r = saver()
    assert not r._ss_should_quit(motion(640, 400))
    assert r._ss_origin == (640, 400)

def a_jitter_inside_the_slack_is_ignored():
    r = saver()
    r._ss_should_quit(motion(640, 400))
    slack = Renderer.SS_SLACK_PX
    assert not r._ss_should_quit(motion(640 + slack, 400))
    assert not r._ss_should_quit(motion(640, 400 - slack))

def a_real_move_ends_it():
    r = saver()
    r._ss_should_quit(motion(640, 400))
    assert r._ss_should_quit(motion(640 + Renderer.SS_SLACK_PX + 1, 400))

def a_real_move_on_the_other_axis_ends_it():
    r = saver()
    r._ss_should_quit(motion(640, 400))
    assert r._ss_should_quit(motion(640, 400 + Renderer.SS_SLACK_PX + 1))


def the_windows_arguments_are_recognised():
    for argv, want in ((["/s"], "s"), (["/S"], "s"), (["-s"], "s"),
                       (["/p", "12345"], "p"), (["/c:12345"], "c"),
                       (["/c"], "c")):
        got = app_main._screensaver_arg(argv)
        assert got == want, f"{argv} gave {got!r}, wanted {want!r}"

def our_own_long_options_are_not_mistaken_for_them():
    """--preset-dir and --screensaver both start with a dash; a naive scan of
    the second character would read them as /p and /s."""
    for argv in (["--preset-dir", "/tmp/x"], ["--screensaver"],
                 ["--borderless"], ["--geometry", "800x600"], []):
        got = app_main._screensaver_arg(argv)
        assert got == "", f"{argv} was read as screensaver mode {got!r}"


class _FakeToast:
    def __init__(self, log):   self._log = log
    def show_message(self, *a): self._log.append("toast")


class _FakeApp:
    """Only what _on_no_presets touches."""
    def __init__(self, screensaver):
        self.log = []
        self._screensaver = screensaver
        self._asked_for_presets = False
        self._toast = _FakeToast(self.log)

    def _open_packs(self):
        self.log.append("packs")


def a_screensaver_does_not_open_the_get_presets_dialog():
    """Any input ends a screensaver, so a modal there cannot be answered and
    covers the thing it is meant to be showing. A fresh install that ticked
    "use as screen saver" before choosing a preset folder is the ordinary way
    to hit it -- measured on Windows, where Get Presets came up over the
    fullscreen visualiser."""
    app = _FakeApp(screensaver=True)
    app_main.App._on_no_presets(app, "/nowhere")
    assert app.log == [], f"screensaver opened {app.log}"


def the_app_proper_still_asks():
    """The guard must not silence the one dialog a first run depends on."""
    app = _FakeApp(screensaver=False)
    app_main.App._on_no_presets(app, "/nowhere")
    assert "packs" in app.log, app.log


for fn in (a_keypress_ends_it,
           a_click_ends_it,
           a_window_event_does_not,
           the_first_motion_event_is_not_a_move,
           a_jitter_inside_the_slack_is_ignored,
           a_real_move_ends_it,
           a_real_move_on_the_other_axis_ends_it,
           the_windows_arguments_are_recognised,
           our_own_long_options_are_not_mistaken_for_them,
           a_screensaver_does_not_open_the_get_presets_dialog,
           the_app_proper_still_asks):
    results.append(check(fn.__name__, fn))

print("all screensaver checks passed" if all(results) else "FAILURES")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
