#!/usr/bin/env python3
"""Checks backup/restore and the login entry. Run: python test_backup.py

Ratings and collections are the one part of this app that cannot be downloaded
again -- they are hours of somebody deciding what they like -- so the round trip
is worth a test, and so is the rule that a restore only writes files the caller
named. A backup from a later version may hold files this one knows nothing
about, and guessing where those go is how a restore corrupts a config.

The login entry is checked on Linux, where it is a file in a redirected
XDG_CONFIG_HOME. The Windows branch is registry work and is left to the machine
that has one.
"""
import json
import os
import sys
import tempfile
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="viz-backup-")
# Redirected before the import, so set_autostart writes here and never into a
# real login directory.
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "config")
# user_data_dir() reads LOCALAPPDATA on Windows and XDG_DATA_HOME everywhere
# else, so setting only the Linux one isolates nothing there -- the same trap
# test_thumbnails was fixed for.
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP, "data")
os.environ["LOCALAPPDATA"] = os.path.join(_TMP, "data")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_common as v                                # noqa: E402


def check(name, fn):
    fn()
    print(f"  ok  {name}")


def _state(**contents):
    """Write files into a fresh directory. Returns {backup name: path}."""
    d = tempfile.mkdtemp(dir=_TMP)
    paths = {}
    for name, data in contents.items():
        name = name.replace("_", ".")
        p = os.path.join(d, name)
        with open(p, "w") as f:
            json.dump(data, f)
        paths[name] = p
    return paths


# ── backup ────────────────────────────────────────────────────────────────

def a_round_trip_brings_everything_back():
    src = _state(ratings_json={"a": 9}, collections_json={"chill": ["a"]})
    zip_path = os.path.join(_TMP, "one.zip")
    assert v.backup_state(zip_path, src) == ""

    dst = _state(ratings_json={}, collections_json={})
    done = v.restore_state(zip_path, dst)
    assert sorted(done) == ["collections.json", "ratings.json"], done
    with open(dst["ratings.json"]) as f:
        assert json.load(f) == {"a": 9}


def a_file_that_does_not_exist_yet_is_skipped_not_fatal():
    """A fresh install has no keymaps.json until something rebinds a key."""
    src = _state(ratings_json={"a": 1})
    src["keymaps.json"] = os.path.join(_TMP, "never-written.json")
    zip_path = os.path.join(_TMP, "two.zip")
    assert v.backup_state(zip_path, src) == ""
    with zipfile.ZipFile(zip_path) as z:
        assert z.namelist() == ["ratings.json"], z.namelist()


def the_backup_is_named_by_basename_not_by_path():
    """So a backup taken on Windows restores on Linux, and one taken from a
    source checkout restores into an installed build."""
    src = _state(config_json={"fps": 30})
    zip_path = os.path.join(_TMP, "three.zip")
    v.backup_state(zip_path, src)
    with zipfile.ZipFile(zip_path) as z:
        assert z.namelist() == ["config.json"]


def a_backup_to_somewhere_unwritable_reports_why():
    src = _state(ratings_json={})
    why = v.backup_state(os.path.join(_TMP, "no-such-dir", "x.zip"), src)
    assert why, "an unwritable destination has to say so"


# ── restore ───────────────────────────────────────────────────────────────

def restore_writes_only_the_names_the_caller_asked_for():
    """A zip from a later version carries files this one cannot place."""
    zip_path = os.path.join(_TMP, "future.zip")
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("ratings.json", '{"a": 3}')
        z.writestr("something-new.json", "{}")
        z.writestr("../escaped.json", "{}")
    dst = _state(ratings_json={})
    done = v.restore_state(zip_path, dst)
    assert done == ["ratings.json"], done
    holder = os.path.dirname(dst["ratings.json"])
    assert sorted(os.listdir(holder)) == ["ratings.json"], os.listdir(holder)


def a_zip_holding_none_of_our_files_restores_nothing():
    """The caller shows a message rather than claiming a silent success."""
    zip_path = os.path.join(_TMP, "unrelated.zip")
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("holiday.jpg", "x")
    assert v.restore_state(zip_path, _state(ratings_json={})) == []


# ── start at login ────────────────────────────────────────────────────────

def the_autostart_command_quotes_a_path_with_spaces():
    """Program Files is the normal case on Windows, and an unquoted Run value
    there launches "C:\\Program" with arguments."""
    real_exe, real_frozen = sys.executable, getattr(sys, "frozen", None)
    try:
        sys.executable = r"C:\Program Files\BomsAI\Visualizer.exe"
        sys.frozen = True
        assert v.autostart_command() == \
            '"C:\\Program Files\\BomsAI\\Visualizer.exe"', v.autostart_command()
        assert v.autostart_command(True).endswith('.exe" --wallpaper')
    finally:
        sys.executable = real_exe
        if real_frozen is None:
            del sys.frozen
        else:
            sys.frozen = real_frozen


def the_wallpaper_flag_is_only_added_when_asked():
    assert "--wallpaper" not in v.autostart_command(False)
    assert v.autostart_command(True).endswith("--wallpaper")


def turning_it_on_and_off_leaves_no_entry():
    if sys.platform == "win32":
        return          # the registry is the machine's, not this test's
    assert v.autostart_enabled() is False
    assert v.set_autostart(True) == ""
    assert v.autostart_enabled() is True
    with open(v._autostart_desktop_path()) as f:
        body = f.read()
    assert "Exec=" in body and v.APP_NAME in body, body
    assert v.set_autostart(False) == ""
    assert v.autostart_enabled() is False


def turning_it_off_twice_is_not_an_error():
    """The settings dialog writes whichever state the box is in, and the entry
    may already have been removed from outside."""
    if sys.platform == "win32":
        return
    assert v.set_autostart(False) == ""
    assert v.set_autostart(False) == ""


def the_wallpaper_flag_reaches_the_desktop_file():
    if sys.platform == "win32":
        return
    try:
        v.set_autostart(True, wallpaper=True)
        with open(v._autostart_desktop_path()) as f:
            assert "--wallpaper" in f.read()
    finally:
        v.set_autostart(False)


for name, fn in list(globals().items()):
    if callable(fn) and not name.startswith(("_", "check")) \
            and getattr(fn, "__module__", None) == "__main__":
        check(name, fn)
print("all backup checks passed")
sys.stdout.flush()
os._exit(0)
