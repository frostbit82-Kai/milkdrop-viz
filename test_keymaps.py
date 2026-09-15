#!/usr/bin/env python3
"""Checks for key bindings. Run: python test_keymaps.py

One key, one action. The handler is a single elif chain, so two actions on
one key never tie -- the earlier branch wins and the later one is dead while
still reading as bound. There are two ways to get there and both are covered
here: a keymaps.json written before an action existed (its key was free at
the time, and a later version spends it on something new), and the Key
bindings dialog, where somebody types a key another row already holds.

Found in the real app: this repo's own keymaps.json gives "c" to hard cuts,
which is the default `snapshot` arrived on in 2.1, so pressing "c" toggled
hard cuts and the snapshot key looked broken.
"""
import json
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_common as v                                # noqa: E402

_app = v.QApplication.instance() or v.QApplication([])

_failed = []


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        _failed.append(f"{name}: {e}")
        print(f"FAIL {name}: {e}")
    except Exception as e:                             # noqa: BLE001
        _failed.append(f"{name}: {type(e).__name__}: {e}")
        print(f"ERROR {name}: {type(e).__name__}: {e}")


def _load(saved):
    """load_keymaps_from over a temporary file holding `saved`."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "keymaps.json")
        with open(p, "w", encoding="utf-8") as f:
            if isinstance(saved, str):
                f.write(saved)
            else:
                json.dump(saved, f)
        return v.load_keymaps_from(p)


# ── What the file says wins ──────────────────────────────────────────────

def the_saved_binding_is_kept():
    km = _load({"toggle_hard_cuts": "c"})
    assert km["toggle_hard_cuts"] == "c", km["toggle_hard_cuts"]


def a_default_the_file_already_spent_comes_up_unbound():
    assert v.DEFAULT_KEYMAPS["snapshot"] == "c", \
        "this check is about the key hard cuts was given in the wild"
    km = _load({"toggle_hard_cuts": "c"})
    assert km["snapshot"] == "", \
        f"snapshot kept a key it can never receive: {km['snapshot']!r}"


def the_match_ignores_case():
    km = _load({"toggle_hard_cuts": "C"})
    assert km["snapshot"] == "", km["snapshot"]


def an_uncontested_default_survives():
    km = _load({"toggle_hard_cuts": "c"})
    assert km["next"] == v.DEFAULT_KEYMAPS["next"]
    assert km["toggle_dual"] == v.DEFAULT_KEYMAPS["toggle_dual"]


def dblclick_is_not_a_key_and_is_not_contested():
    km = _load({"fullscreen_mouse": "DBLCLICK", "record": "DBLCLICK"})
    assert km["record"] == "DBLCLICK", km["record"]
    assert km["fullscreen_mouse"] == "DBLCLICK"


def two_saved_actions_on_one_key_are_left_alone():
    """Not ours to overrule: both were typed in on purpose, and clearing one
    would silently undo a choice the file records."""
    km = _load({"next": "n", "prev": "n"})
    assert km["next"] == "n" and km["prev"] == "n", km


def an_empty_saved_binding_claims_nothing():
    km = _load({"toggle_hard_cuts": ""})
    assert km["snapshot"] == "c", km["snapshot"]


# ── A file that is not a file ────────────────────────────────────────────

def a_missing_file_is_the_defaults():
    with tempfile.TemporaryDirectory() as d:
        km = v.load_keymaps_from(os.path.join(d, "nothing.json"))
    assert km == dict(v.DEFAULT_KEYMAPS)


def a_corrupt_file_is_the_defaults():
    assert _load("{not json") == dict(v.DEFAULT_KEYMAPS)


def a_file_that_is_a_list_is_the_defaults():
    assert _load(["next", "RIGHT"]) == dict(v.DEFAULT_KEYMAPS)


# ── The defaults themselves ──────────────────────────────────────────────

def no_two_defaults_share_a_key():
    """The whole failure needs one key on two actions; a fresh install must
    not ship one."""
    seen = {}
    for action, key in v.DEFAULT_KEYMAPS.items():
        if not key or key.upper() == "DBLCLICK":
            continue
        clash = seen.get(key.upper())
        assert clash is None, f"{action} and {clash} both use {key}"
        seen[key.upper()] = action


def every_default_token_is_one_the_matcher_knows():
    for action, key in v.DEFAULT_KEYMAPS.items():
        if not key or key.upper() == "DBLCLICK":
            continue
        assert key.upper() in v._KEY_LOOKUP, f"{action}: {key}"


# ── The dialog ───────────────────────────────────────────────────────────

def _dialog(keymaps=None):
    return v.KeymapDialog(dict(keymaps or v.DEFAULT_KEYMAPS))


def _row_of(dlg, action):
    for row in range(dlg._table.rowCount()):
        item = dlg._table.item(row, 1)
        if item is not None and item.data(v.Qt.ItemDataRole.UserRole) == action:
            return row
    raise AssertionError(f"no row for {action}")


def _type_into(dlg, action, token):
    row = _row_of(dlg, action)
    dlg._table.setCurrentCell(row, 1)
    item = dlg._table.item(row, 1)
    item.setText(token)
    dlg._keymaps[action] = token
    dlg._release_elsewhere(action, token)


def typing_a_taken_key_clears_the_other_row():
    dlg = _dialog()
    _type_into(dlg, "toggle_hard_cuts", "c")       # snapshot's default
    assert dlg._table.item(_row_of(dlg, "snapshot"), 1).text() == "", \
        "the other row still shows a key it will never receive"
    assert dlg.get_keymaps()["snapshot"] == ""
    assert dlg.get_keymaps()["toggle_hard_cuts"] == "c"


def typing_a_taken_key_ignores_case():
    dlg = _dialog()
    _type_into(dlg, "toggle_hard_cuts", "C")
    assert dlg._table.item(_row_of(dlg, "snapshot"), 1).text() == ""


def typing_dblclick_clears_nothing():
    dlg = _dialog()
    _type_into(dlg, "record", "DBLCLICK")
    assert dlg.get_keymaps()["fullscreen_mouse"] == "DBLCLICK"


def a_free_key_disturbs_nobody():
    dlg = _dialog()
    before = dlg.get_keymaps()
    _type_into(dlg, "snapshot", "q")
    after = dlg.get_keymaps()
    assert after["snapshot"] == "q"
    for action in before:
        if action != "snapshot":
            assert after[action] == before[action], action


def reset_to_defaults_puts_a_cleared_row_back():
    dlg = _dialog()
    _type_into(dlg, "toggle_hard_cuts", "c")
    dlg._reset_defaults()
    assert dlg.get_keymaps()["snapshot"] == v.DEFAULT_KEYMAPS["snapshot"]


# ── The handler this is all about ────────────────────────────────────────

def an_unbound_action_matches_no_key():
    assert not v._token_matches("", v.sdl2.SDLK_c, 6)


def a_bound_action_still_matches_its_key():
    assert v._token_matches("c", v.sdl2.SDLK_c, 6)
    assert v._token_matches("C", v.sdl2.SDLK_c, 6)


for name, fn in list(globals().items()):
    if callable(fn) and not isinstance(fn, type) \
            and not name.startswith(("_", "check")) \
            and getattr(fn, "__module__", None) == "__main__":
        check(name, fn)

if _failed:
    print(f"\n{len(_failed)} keymap check(s) failed")
    sys.exit(1)
print("all keymap checks passed")
sys.stdout.flush()
os._exit(0)
