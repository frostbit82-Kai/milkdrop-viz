#!/usr/bin/env python3
"""Checks for shuffle. Run: python test_shuffle.py

Shuffle reorders the playlist instead of making next/prev jump around it, so
what matters is the resulting *order* and the fact that projectM's own shuffle
stays off -- both testable without a GL context. The real Renderer methods are
run against a stub, rather than a copy of their logic, so the test fails if the
shipped versions drift.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_common as v                                # noqa: E402

v._random.seed(1234)          # a flake here would be indistinguishable from a bug


class FakeSignal:
    def __init__(self):
        self.sent = []

    def emit(self, *a):
        self.sent.append(a[0] if len(a) == 1 else a)


class FakeBridge:
    def __init__(self):
        self.status_message = FakeSignal()
        self.playlist_changed = FakeSignal()


class FakePM:
    """Only the playlist surface _rebuild_playlist and shuffle actually touch."""
    def __init__(self, paths):
        self.order = list(paths)
        self.pos = 0
        self.shuffle_calls = []
        self._pl, self._playlist = object(), object()

    def clear_playlist(self):
        self.order = []

    def add_presets(self, paths):
        self.order = list(paths)
        return len(self.order)

    def set_shuffle(self, on):
        self.shuffle_calls.append(bool(on))

    def playlist_size(self):
        return len(self.order)

    def playlist_item(self, i):
        return os.path.splitext(os.path.basename(self.order[i]))[0]

    def set_playlist_position(self, i, hard_cut=True):
        self.pos = int(i)

    def current_preset_name(self):
        return self.playlist_item(self.pos) if self.order else ""


class StubRenderer(v.Renderer):
    """The real _do_toggle_mode / _do_shuffle_playlist / _rebuild_playlist over
    a fake playlist. Only the disk scan and the Qt emit are replaced."""
    def __init__(self, count=200):
        self.files = [(f"/presets/p{i:03d}.milk", f"p{i:03d}") for i in range(count)]
        self._pm = FakePM([p for p, _ in self.files])
        self._bridge = FakeBridge()
        self._shuffle = False
        self._rated_history = []
        self._emitted = 0

    def _all_preset_files(self):
        return list(self.files)

    def _emit_current_preset(self):
        self._emitted += 1


def check(name, fn):
    fn()
    print(f"  ok  {name}")


def order(r):
    return list(r._pm.order)


# --- the reorder itself --------------------------------------------------
def shuffle_permutes_rather_than_dropping():
    """A reorder that loses or duplicates presets would shrink the rotation
    silently -- the count is the only thing the user would ever see."""
    r = StubRenderer()
    before = order(r)
    assert r._do_shuffle_playlist() is True
    after = order(r)
    assert sorted(after) == sorted(before), "playlist contents changed"
    assert len(after) == len(set(after)), "duplicates introduced"
    assert after != before, "order unchanged"


def projectm_shuffle_is_never_turned_on():
    """This is the whole fix. With projectM's internal shuffle on, play_next
    picks a random item every time and the list is never walked -- which is
    exactly the jumping this replaces."""
    r = StubRenderer()
    r._do_shuffle_playlist()
    assert r._pm.shuffle_calls, "_rebuild_playlist never set the native shuffle"
    assert not any(r._pm.shuffle_calls), r._pm.shuffle_calls


def the_current_preset_keeps_playing():
    """Reordering what comes next must not change what is on screen now."""
    r = StubRenderer()
    r._pm.pos = 42
    playing = r._pm.current_preset_name()
    r._do_shuffle_playlist()
    assert r._pm.current_preset_name() == playing, "shuffle jumped the visual"


def rated_history_is_dropped():
    """It holds native indices, and every one of them now points elsewhere."""
    r = StubRenderer()
    r._rated_history = [1, 2, 3]
    r._do_shuffle_playlist()
    assert r._rated_history == [], r._rated_history


# --- the toggle ----------------------------------------------------------
def turning_it_on_shuffles():
    r = StubRenderer()
    before = order(r)
    r._do_toggle_mode()
    assert r._shuffle is True
    assert order(r) != before


def turning_it_off_leaves_the_order_alone():
    """The point of the change: you can keep walking the sequence you are in
    the middle of, rather than being thrown back to load order."""
    r = StubRenderer()
    r._do_toggle_mode()                       # on
    shuffled = order(r)
    r._do_toggle_mode()                       # off
    assert r._shuffle is False
    assert order(r) == shuffled, "turning shuffle off reordered the playlist"


def turning_it_on_again_reshuffles():
    r = StubRenderer()
    r._do_toggle_mode()
    first = order(r)
    r._do_toggle_mode()
    r._do_toggle_mode()
    assert r._shuffle is True
    assert order(r) != first, "second shuffle produced the same order"


def with_no_presets_the_flag_does_not_move():
    """Otherwise the browser button reads ON over a playlist that was never
    shuffled, and turning it 'off' then becomes the thing that shuffles."""
    r = StubRenderer(count=0)
    r._do_toggle_mode()
    assert r._shuffle is False
    assert any("no presets" in str(m) for m in r._bridge.status_message.sent)


def the_toggle_reports_itself():
    r = StubRenderer()
    r._do_toggle_mode()
    assert r._bridge.playlist_changed.sent, "browser never told the order changed"
    assert r._emitted, "current preset never re-emitted (browser highlight)"


# --- startup -------------------------------------------------------------
def a_fresh_renderer_does_not_claim_to_be_shuffled():
    """load_preset_dir leaves the native shuffle off, so the list starts in
    load order; the flag used to say otherwise from the first frame."""
    import ast
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "viz_common.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "Renderer")
    init = next(n for n in cls.body
                if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    found = [n.value.value for n in ast.walk(init)
             if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
             and any(isinstance(t, ast.Attribute) and t.attr == "_shuffle"
                     for t in n.targets)]
    assert found == [False], f"Renderer.__init__ sets _shuffle to {found}"


if __name__ == "__main__":
    for fn in (shuffle_permutes_rather_than_dropping,
               projectm_shuffle_is_never_turned_on,
               the_current_preset_keeps_playing,
               rated_history_is_dropped,
               turning_it_on_shuffles,
               turning_it_off_leaves_the_order_alone,
               turning_it_on_again_reshuffles,
               with_no_presets_the_flag_does_not_move,
               the_toggle_reports_itself,
               a_fresh_renderer_does_not_claim_to_be_shuffled):
        check(fn.__name__, fn)
    print("all shuffle checks passed")
