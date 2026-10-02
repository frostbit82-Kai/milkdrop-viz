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
# The kept shuffle is a file in the user's data directory, so this suite has
# to point that somewhere of its own -- both variables, because Windows reads
# LOCALAPPDATA and ignores the other one.
import tempfile                                       # noqa: E402
_TMP = tempfile.mkdtemp(prefix="viz-shuffle-")
os.environ["XDG_DATA_HOME"] = os.environ["LOCALAPPDATA"] = _TMP
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
        self.preset_changed = FakeSignal()


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
        self._shuffled_paths = []
        self._rated_history = []
        self._emitted = 0
        self._cfg = {"preset_dir": "/presets"}
        self._collection = ""
        self._ratings = {}
        self._pms = [self._pm, None]
        self.saves = 0

    def _save_config(self, cfg):
        self.saves += 1

    def collections(self):
        return {}

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



# --- kept across a restart (2 Oct 2026) -----------------------------------
def _kept():
    try:
        with open(v.shuffle_path(), encoding="utf-8") as f:
            return v.json.load(f)
    except FileNotFoundError:
        return None


def turning_it_on_keeps_the_order_for_next_time():
    """Reported: the shuffle reset every time the app was closed."""
    r = StubRenderer()
    r._do_toggle_mode()
    kept = _kept()
    assert kept is not None, "nothing written"
    assert kept["order"] == [os.path.relpath(p, "/presets") for p in order(r)]
    assert kept["preset_dir"] == "/presets"


def turning_it_off_forgets_it():
    r = StubRenderer()
    r._do_toggle_mode()
    r._do_toggle_mode()
    assert _kept() is None, "a shuffle that is off came back next run"


def the_next_run_gets_the_same_order_and_the_same_place():
    first = StubRenderer()
    first._do_toggle_mode()
    first._pm.pos = 17
    v.Renderer._emit_current_preset(first)
    assert first._cfg["shuffle_at"] == first._pm.current_preset_name()

    nxt = StubRenderer()
    nxt._cfg = dict(first._cfg)
    assert nxt._restore_shuffle() is True
    assert order(nxt) == order(first), "a different order came back"
    assert nxt._shuffle is True, "the browser would say shuffle is off"
    assert nxt._pm.current_preset_name() == first._cfg["shuffle_at"], \
        "came back at the top rather than where it was"


def presets_gone_since_are_dropped_and_new_ones_go_on_the_end():
    first = StubRenderer(count=50)
    first._do_toggle_mode()
    nxt = StubRenderer(count=60)
    gone = order(first)[3]
    nxt.files = [f for f in nxt.files if f[0] != gone]
    nxt._cfg = dict(first._cfg)
    nxt._restore_shuffle()
    got = order(nxt)
    assert gone not in got
    assert got[:49] == [p for p in order(first) if p != gone], \
        "the old part of the order was not kept"
    assert sorted(got[49:]) == [f"/presets/p{i:03d}.milk"
                                for i in range(50, 60)]


def another_preset_folder_starts_fresh():
    first = StubRenderer()
    first._do_toggle_mode()
    nxt = StubRenderer()
    nxt._cfg = dict(first._cfg, preset_dir="/elsewhere")
    assert nxt._restore_shuffle() is False
    assert nxt._shuffle is False


def a_sort_forgets_the_shuffle_too():
    r = StubRenderer()
    r._do_toggle_mode()
    r._do_sort("name")
    assert r._shuffle is False and _kept() is None


def an_old_shuffle_key_in_config_is_not_taken_for_a_kept_order():
    """Configs that went through older builds carry "shuffle": true, which
    meant something else. Without a kept order there is nothing to restore."""
    r = StubRenderer()
    r._do_toggle_mode()
    r._do_toggle_mode()                      # off: the file is gone
    nxt = StubRenderer()
    nxt._cfg = {"preset_dir": "/presets", "shuffle": True}
    assert nxt._restore_shuffle() is False
    assert nxt._shuffle is False


def only_visual_one_moves_the_bookmark():
    """With two visuals up, the focus can be on visual 2, whose playlist is
    its own. The kept shuffle is visual 1's."""
    r = StubRenderer()
    r._do_toggle_mode()
    r._cfg.pop("shuffle_at", None)
    r._pms = [FakePM(["/presets/x.milk"]), r._pm]
    v.Renderer._emit_current_preset(r)
    assert "shuffle_at" not in r._cfg, r._cfg

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
               a_fresh_renderer_does_not_claim_to_be_shuffled,
               turning_it_on_keeps_the_order_for_next_time,
               turning_it_off_forgets_it,
               the_next_run_gets_the_same_order_and_the_same_place,
               presets_gone_since_are_dropped_and_new_ones_go_on_the_end,
               another_preset_folder_starts_fresh,
               a_sort_forgets_the_shuffle_too,
               an_old_shuffle_key_in_config_is_not_taken_for_a_kept_order,
               only_visual_one_moves_the_bookmark):
        check(fn.__name__, fn)
    print("all shuffle checks passed")
