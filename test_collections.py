#!/usr/bin/env python3
"""Checks named preset collections. Run: python test_collections.py

A collection is a named list of preset stems, and playing one is a playlist
rebuild -- which is what makes the browser, the search box, sort, shuffle and
delete-0★ all narrow to it without any of them knowing collections exist. That
last part is the thing worth testing: _all_preset_files is the single choke
point they all go through.

No display needed. XDG_DATA_HOME is redirected first, so this never touches the
real collections.json.
"""
import json
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="viz-collections-")
# Before viz_common is imported, and before anything calls user_data_dir():
# collections.json lives under it, and a test that writes the real one is a
# test that eats somebody's library.
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP, "data")
os.environ["LOCALAPPDATA"] = os.path.join(_TMP, "data")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_common as v                                # noqa: E402

assert _TMP in v.collections_path(), v.collections_path()
os.makedirs(os.path.dirname(v.collections_path()), exist_ok=True)


def check(name, fn):
    fn()
    print(f"  ok  {name}")


# ── the file ──────────────────────────────────────────────────────────────

def a_missing_file_is_no_collections():
    assert v.load_collections_from(os.path.join(_TMP, "nope.json")) == {}


def a_round_trip_keeps_the_lists():
    p = os.path.join(_TMP, "rt.json")
    v.save_collections_to(p, {"chill": ["a", "b"], "party": []})
    assert v.load_collections_from(p) == {"chill": ["a", "b"], "party": []}


def rubbish_in_the_file_does_not_take_the_app_down():
    """It is small, hand-editable JSON in a directory people poke at."""
    p = os.path.join(_TMP, "bad.json")
    with open(p, "w") as f:
        f.write("{not json")
    assert v.load_collections_from(p) == {}
    with open(p, "w") as f:
        json.dump({"ok": ["a"], "wrong": "not a list", "n": 5}, f)
    assert v.load_collections_from(p) == {"ok": ["a"]}


# ── the choke point ───────────────────────────────────────────────────────

class FakeHost:
    """Just what Renderer._all_preset_files touches."""

    def __init__(self, preset_dir, data, active=""):
        self._cfg = {"preset_dir": preset_dir}
        self._data = data
        self._collection = active

    def collections(self):
        return self._data

    files = v.Renderer._all_preset_files


def _library():
    root = tempfile.mkdtemp(prefix="viz-presets-", dir=_TMP)
    os.makedirs(os.path.join(root, "Deep"), exist_ok=True)
    for rel in ("one.milk", "two.milk", "Deep/three.milk"):
        with open(os.path.join(root, rel), "w") as f:
            f.write("x")
    return root


def no_collection_means_the_whole_library():
    root = _library()
    stems = sorted(s for _, s in FakeHost(root, {}).files())
    assert stems == ["one", "three", "two"], stems


def an_active_collection_narrows_the_library():
    """Everything downstream -- sort, shuffle, the browser, delete-0★ -- reads
    this list, so narrowing here is the whole feature."""
    root = _library()
    host = FakeHost(root, {"chill": ["one", "three"]}, active="chill")
    stems = sorted(s for _, s in host.files())
    assert stems == ["one", "three"], stems


def a_member_that_is_no_longer_on_disk_is_simply_absent():
    """Presets get deleted from underneath a collection -- delete-0★ does it
    from inside this very app -- and a stale name must not become a path."""
    root = _library()
    host = FakeHost(root, {"chill": ["one", "gone"]}, active="chill")
    assert sorted(s for _, s in host.files()) == ["one"]


def an_unknown_collection_name_falls_back_to_everything():
    """collections.json can be edited or restored from a backup while a name
    is active. Showing the whole library beats showing nothing."""
    root = _library()
    host = FakeHost(root, {"chill": ["one"]}, active="deleted-since")
    assert len(host.files()) == 3


def no_preset_directory_is_no_files_not_a_crash():
    assert FakeHost("", {}).files() == []


# ── editing ───────────────────────────────────────────────────────────────

class FakeRenderer:
    """The collection editing methods, which only touch the file and a lock."""
    collections        = v.Renderer.collections
    _write_collections = v.Renderer._write_collections
    collection         = v.Renderer.collection
    create_collection  = v.Renderer.create_collection
    delete_collection  = v.Renderer.delete_collection
    add_to_collection  = v.Renderer.add_to_collection
    remove_from_collection = v.Renderer.remove_from_collection
    play_collection    = v.Renderer.play_collection

    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self._collections = {}
        self._collection = ""
        self._req_collection = None


def creating_is_refused_for_empty_and_duplicate_names():
    r = FakeRenderer()
    assert r.create_collection("chill") is True
    assert r.create_collection("chill") is False
    assert r.create_collection("   ") is False
    assert list(r.collections()) == ["chill"]


def adding_the_same_preset_twice_says_so():
    """The menu greys out an entry on the strength of this answer."""
    r = FakeRenderer()
    r.create_collection("chill")
    assert r.add_to_collection("chill", "one") is True
    assert r.add_to_collection("chill", "one") is False
    assert r.collections()["chill"] == ["one"]


def removing_from_the_collection_being_played_rebuilds_it():
    r = FakeRenderer()
    r.create_collection("chill")
    r.add_to_collection("chill", "one")
    r._collection = "chill"
    assert r.remove_from_collection("chill", "one") is True
    assert r._req_collection == "chill"


def removing_from_a_collection_not_being_played_leaves_the_screen_alone():
    """Otherwise filing a preset away interrupts the one you are watching."""
    r = FakeRenderer()
    r.create_collection("chill")
    r.add_to_collection("chill", "one")
    assert r.remove_from_collection("chill", "one") is True
    assert r._req_collection is None


def deleting_the_collection_being_played_goes_back_to_everything():
    r = FakeRenderer()
    r.create_collection("chill")
    r._collection = "chill"
    r.delete_collection("chill")
    assert r._req_collection == ""
    assert r.collections() == {}


def edits_reach_the_file():
    r = FakeRenderer()
    r.create_collection("party")
    r.add_to_collection("party", "two")
    assert v.load_collections_from(v.collections_path()) == {"party": ["two"]}


for name, fn in list(globals().items()):
    if callable(fn) and not name.startswith(("_", "check", "Fake")) \
            and getattr(fn, "__module__", None) == "__main__":
        check(name, fn)
print("all collections checks passed")
sys.stdout.flush()
os._exit(0)
