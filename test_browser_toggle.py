#!/usr/bin/env python3
"""The preset browser can always be got back. Run: python test_browser_toggle.py

One menu entry shows and hides the browser, and the window it drives is built
once and reused -- so a state the toggle reads wrongly is not a wrong frame,
it is a window that never comes back until the app is restarted. That is what
happened: `isVisible()` is True for a MINIMISED window, because minimised is a
window state and not a visibility, so minimising the browser and then picking
"Preset browser" hid it, and every pick after that showed it straight back
into the minimised state it still carried. Measured on Windows 2026-09-15
against the shipped 2.1.3 build: three picks after the minimise, the window
read vis=False min=True every time.

Getting it back is only half of it, and the half a suite can see least of.
On Windows the browser is owned by the SDL window, whose message pump is the
render thread, and **un-minimising an owned window is as dangerous as closing
one**: `showNormal()` with the owner still set never returned -- Qt thread
inside the call, render thread stopped, window left at the iconic placeholder
rectangle. `_OwnedByVisuals` therefore hands the owner back on the way down,
and the guard in `_set_owner` refuses to own a minimised window at all --
because clearing the owner of a freshly minimised window makes Windows show
it again, Qt reports a `Show`, and the `Show` branch owned it straight back.
Both of those are checked here against the shipped source.

The expressions under test are pulled out of `main.py` and `viz_common.py`
with `ast`, so what is checked is the shipped code rather than a copy of it
that can drift.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
COMMON = os.path.join(HERE, "viz_common.py")


def _shipped_toggle():
    """`App._toggle_browser`, lifted out of main.py and made callable."""
    src = open(MAIN, encoding="utf-8").read()
    tree = ast.parse(src)
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
            if fn.name == "_toggle_browser":
                ns = {"browser_qss": lambda *a, **k: ""}
                exec(compile(ast.Module([fn], []), MAIN, "exec"), ns)
                return ns["_toggle_browser"]
    raise AssertionError("no App._toggle_browser in main.py")


def _shipped_own_guard():
    """The `if own and (...)` test out of `_OwnedByVisuals._set_owner`.

    Returns a callable (own, hwnd, w) -> bool: True when the shipped code
    refuses to set the owner.
    """
    src = open(COMMON, encoding="utf-8").read()
    tree = ast.parse(src)
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        if cls.name != "_OwnedByVisuals":
            continue
        for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
            if fn.name != "_set_owner":
                continue
            for node in fn.body:
                if isinstance(node, ast.If):
                    text = ast.get_source_segment(src, node.test)
                    return eval(f"lambda own, hwnd, w: bool({text})")
    raise AssertionError("no guard found in _OwnedByVisuals._set_owner")


class FakeBrowser:
    """Qt's visibility semantics, which are the whole point of the check.

    `show()` marks a widget shown and leaves its window state alone;
    `hide()` unmarks it and *also* leaves the state alone; `showNormal()`
    clears the minimised state as well. `isVisible()` answers True for a
    minimised window -- that is the trap.
    """
    def __init__(self):
        self.shown = False
        self.minimised = False
        self.calls = []

    # what the toggle calls
    def isVisible(self):    return self.shown
    def isMinimized(self):  return self.minimised
    def show(self):         self.calls.append("show"); self.shown = True
    def showNormal(self):
        self.calls.append("showNormal"); self.shown = True; self.minimised = False
    def hide(self):         self.calls.append("hide"); self.shown = False
    def setStyleSheet(self, _): pass
    def raise_(self):       pass
    def activateWindow(self): pass

    # what the user does
    def user_minimises(self):
        """The titlebar button. Qt keeps the widget 'shown'."""
        self.minimised = True

    def on_screen(self):
        return self.shown and not self.minimised


class FakeApp:
    def __init__(self, browser):
        self._browser = browser
        self._renderer = None


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        print(f"FAIL  {name}\n      {e}", flush=True)
        return False
    print(f"ok    {name}", flush=True)
    return True


toggle = _shipped_toggle()


def the_entry_still_shows_and_hides():
    """The ordinary case, which must keep working."""
    b = FakeBrowser()
    app = FakeApp(b)
    toggle(app)
    assert b.on_screen(), "first pick did not put the browser on screen"
    toggle(app)
    assert not b.on_screen(), "second pick did not hide the browser"
    toggle(app)
    assert b.on_screen(), "third pick did not bring it back"


def a_minimised_browser_is_restored_not_hidden():
    """The defect. Minimised is not showing, whatever isVisible() says."""
    b = FakeBrowser()
    app = FakeApp(b)
    toggle(app)
    b.user_minimises()
    toggle(app)
    assert "hide" not in b.calls, (
        "the toggle hid a browser that was minimised, which is how it "
        f"disappears for the session (calls: {b.calls})")
    assert b.on_screen(), (
        "a minimised browser was not restored by the menu entry "
        f"(shown={b.shown} minimised={b.minimised} calls={b.calls})")


def the_broken_state_recovers():
    """Hidden *and* minimised at once -- what an app in the wild is already in.

    Anyone who minimised the browser under 2.1.3 and then picked the entry is
    sitting in this state, so the fix has to climb out of it rather than only
    avoid entering it.
    """
    b = FakeBrowser()
    b.shown, b.minimised = False, True
    app = FakeApp(b)
    toggle(app)
    assert b.on_screen(), (
        "a browser left hidden-and-minimised never came back "
        f"(shown={b.shown} minimised={b.minimised} calls={b.calls})")


def hiding_and_showing_leaves_a_maximised_window_maximised():
    """showNormal() is for the minimised branch only.

    It clears maximised as well as minimised, so using it for every show
    would quietly un-maximise a window the user had maximised.
    """
    b = FakeBrowser()
    app = FakeApp(b)
    toggle(app)                     # shown
    toggle(app)                     # hidden, never minimised
    toggle(app)                     # shown again
    assert "showNormal" not in b.calls, (
        "showNormal() was used on a window that was never minimised, which "
        f"would also un-maximise it (calls: {b.calls})")


guard = _shipped_own_guard()


class OwnCandidate:
    def __init__(self, visible=True, minimised=False):
        self._v, self._m = visible, minimised

    def isVisible(self):   return self._v
    def isMinimized(self): return self._m


def a_minimised_window_is_never_given_the_owner():
    """The line that stops the Show branch owning it straight back.

    Clearing the owner of a window that has just been minimised makes Windows
    show it again, so Qt delivers a Show while `isMinimized()` is still True.
    Without this the disown is undone one line later and `showNormal()` goes
    back to hanging.
    """
    assert guard(True, 12345, OwnCandidate(minimised=True)), (
        "the guard would own a MINIMISED window, which undoes the disown and "
        "brings the hang back")
    assert not guard(True, 12345, OwnCandidate()), (
        "the guard refuses an ordinary visible window, so no dialog would "
        "ever be owned and they would all fall behind the visuals")


def the_guard_still_covers_what_it_covered_before():
    """No SDL window yet, and a window already dismissed."""
    assert guard(True, None, OwnCandidate()), "owned with no SDL window yet"
    assert guard(True, 0, OwnCandidate()), "owned with a null SDL handle"
    assert guard(True, 12345, OwnCandidate(visible=False)), (
        "owned a window that is not visible")


def _event_filter_source():
    src = open(COMMON, encoding="utf-8").read()
    tree = ast.parse(src)
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        if cls.name != "_OwnedByVisuals":
            continue
        for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
            if fn.name == "eventFilter":
                return ast.get_source_segment(src, fn)
    raise AssertionError("no _OwnedByVisuals.eventFilter in viz_common.py")


def the_filter_hands_the_owner_back_on_the_way_down():
    """The disown has to happen at minimise time, and it has to be immediate.

    Deferring it is no good for the same reason the Close branch is not
    deferred: by the time a queued call ran, the restore could already be
    under way and inside it. The guard above is what keeps the disown from
    being undone; this is the disown itself.
    """
    body = _event_filter_source()
    assert "WindowStateChange" in body, (
        "the filter no longer looks at WindowStateChange, so nothing hands "
        "the owner back when a window is minimised and showNormal() hangs")
    branch = body.split("WindowStateChange", 1)[1].split("QEvent.Type.Close")[0]
    assert "isMinimized()" in branch, (
        "the WindowStateChange branch does not ask whether the window is "
        f"minimised: {branch.strip()[:200]}")
    assert "self._set_owner(obj, False)" in branch, (
        "the minimised path does not hand the owner back directly -- a "
        f"deferred one is too late: {branch.strip()[:200]}")


def handing_the_owner_back_is_never_refused():
    """Disowning has no guard, on purpose: it is the safe direction."""
    for w in (OwnCandidate(), OwnCandidate(minimised=True),
              OwnCandidate(visible=False)):
        assert not guard(False, None, w), (
            "the guard blocked a disown, which is the call that has to happen "
            "before the window is closed or restored")


results = [
    check("the entry still shows and hides",
          the_entry_still_shows_and_hides),
    check("a minimised browser is restored, not hidden",
          a_minimised_browser_is_restored_not_hidden),
    check("a browser left hidden and minimised recovers",
          the_broken_state_recovers),
    check("an ordinary hide/show does not un-maximise",
          hiding_and_showing_leaves_a_maximised_window_maximised),
    check("a minimised window is never given the visualiser as owner",
          a_minimised_window_is_never_given_the_owner),
    check("the owner guard still covers no-window and dismissed",
          the_guard_still_covers_what_it_covered_before),
    check("the filter hands the owner back when a window is minimised",
          the_filter_hands_the_owner_back_on_the_way_down),
    check("handing the owner back is never refused",
          handing_the_owner_back_is_never_refused),
]

print("all browser-toggle checks passed" if all(results) else "FAILURES",
      flush=True)
sys.stdout.flush()
os._exit(0 if all(results) else 1)
