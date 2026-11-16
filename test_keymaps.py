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

The defaults moved on 15 Nov 2026 -- hard cuts now ships on "c" and snapshot
on "s" -- so the checks below name the contested key as "whatever snapshot
defaults to" rather than spelling a letter that stops being true.
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

#: The key a saved file spends before `snapshot` gets to it.
SPENT = v.DEFAULT_KEYMAPS["snapshot"]


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
    assert v.DEFAULT_KEYMAPS["toggle_hard_cuts"] != "q"
    km = _load({"toggle_hard_cuts": "q"})
    assert km["toggle_hard_cuts"] == "q", km["toggle_hard_cuts"]


def a_default_the_file_already_spent_comes_up_unbound():
    assert v.DEFAULT_KEYMAPS["toggle_hard_cuts"] != SPENT, \
        "this check needs hard cuts to be taking a key that is not its own"
    km = _load({"toggle_hard_cuts": SPENT})
    assert km["snapshot"] == "", \
        f"snapshot kept a key it can never receive: {km['snapshot']!r}"


def the_match_ignores_case():
    km = _load({"toggle_hard_cuts": SPENT.upper()})
    assert km["snapshot"] == "", km["snapshot"]


def an_uncontested_default_survives():
    km = _load({"toggle_hard_cuts": SPENT})
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
    assert km["snapshot"] == SPENT, km["snapshot"]


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


def the_defaults_are_the_set_asked_for_on_15_nov_2026():
    """The author's own bindings, settled through use and asked for as the
    shipped defaults. Seven moved; a fresh install gets these and a saved
    keymaps.json is not touched."""
    moved = {"hard_cut_next": "h", "random": "j", "toggle_hard_cuts": "c",
             "rate_up": "UP", "rate_down": "DOWN", "toggle_mode": "r",
             "snapshot": "s"}
    for action, key in moved.items():
        assert v.DEFAULT_KEYMAPS[action] == key, \
            f"{action} ships on {v.DEFAULT_KEYMAPS[action]!r}, not {key!r}"
    stayed = {"next": "RIGHT", "prev": "LEFT", "lock": "SCROLLLOCK",
              "fullscreen": "f", "fullscreen_alt": "F11", "quit": "ESCAPE",
              "toggle_borderless": "b", "toggle_on_top": "t",
              "fullscreen_mouse": "DBLCLICK", "record": "F9",
              "toggle_wallpaper": "d", "exclude": "x", "toggle_dual": "2",
              "toggle_visual1": "1", "crossfade_back": "[",
              "crossfade_fwd": "]", "swap_focus": "TAB"}
    for action, key in stayed.items():
        assert v.DEFAULT_KEYMAPS[action] == key, action
    assert set(v.DEFAULT_KEYMAPS) == set(moved) | set(stayed), \
        "an action this check has not heard of"


def a_file_saved_under_the_old_defaults_keeps_every_key_it_had():
    """Somebody who opened the dialog on 2.1.13 and pressed OK has all 24
    bindings on disk. The new defaults must not move one of them."""
    old = dict(v.DEFAULT_KEYMAPS, hard_cut_next="SPACE", random="r",
               toggle_hard_cuts="h", rate_up="+", rate_down="-",
               toggle_mode="m", snapshot="c")
    assert _load(old) == old


# ── Windows: a virtual key, as SDL would have named it ───────────────────

def _us_layout(vk):
    """MapVirtualKeyW(vk, MAPVK_VK_TO_CHAR) on a US keyboard, near enough:
    letters and digits are their own codes, and the OEM keys are these."""
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return vk
    return {0xDB: ord("["), 0xDD: ord("]"), 0xBD: ord("-"), 0xBB: ord("="),
            0x6B: ord("+"), 0x6D: ord("-")}.get(vk, 0)


#: The virtual key behind each token the defaults use, on that keyboard.
_VK_OF = {"RIGHT": 0x27, "LEFT": 0x25, "UP": 0x26, "DOWN": 0x28,
          "SCROLLLOCK": 0x91, "ESCAPE": 0x1B, "TAB": 0x09, "F9": 0x78,
          "F11": 0x7A, "[": 0xDB, "]": 0xDD, "1": 0x31, "2": 0x32}


def every_default_key_can_be_named_from_a_windows_virtual_key():
    """The desktop hook sees virtual keys, the bindings are SDL tokens, and a
    default the translation cannot reach is a key that silently does nothing
    from the desktop while working everywhere else."""
    for action, key in v.DEFAULT_KEYMAPS.items():
        if key.upper() == "DBLCLICK":
            continue
        vk = _VK_OF.get(key.upper(), ord(key.upper()) if len(key) == 1 else 0)
        assert vk, f"this check has no virtual key for {key}"
        pair = v.win_vk_to_sdl(vk, _us_layout)
        assert pair is not None, f"{action}: {key} (vk {vk:#x}) has no name"
        assert v._token_matches(key, *pair), \
            f"{action}: vk {vk:#x} came back as {pair}, which is not {key}"


def a_letter_is_its_lower_case_self_whatever_the_layout_calls_it():
    assert v.win_vk_to_sdl(0x48, _us_layout) == (ord("h"), 0)
    # The same physical key on a layout that puts another letter on it is
    # that letter, as it is for SDL.
    assert v.win_vk_to_sdl(0x51, lambda _vk: ord("A")) == (ord("a"), 0)


def caps_lock_and_the_modifiers_are_never_named():
    """Naming one is how the hook would come to swallow it -- and Caps Lock
    is the key that takes the hook away again."""
    for vk in (0x14, 0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA2, 0xA4):
        assert v.win_vk_to_sdl(vk, _us_layout) is None, hex(vk)


def a_dead_key_is_its_character_and_a_key_with_none_is_nothing():
    dead = 0x8000_0000 | ord("`")         # bit 31: MapVirtualKey's dead-key flag
    assert v.win_vk_to_sdl(0xC0, lambda _vk: dead) == (ord("`"), 0)
    assert v.win_vk_to_sdl(0xFF, lambda _vk: 0) is None


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
    _type_into(dlg, "toggle_hard_cuts", SPENT)     # snapshot's default
    assert dlg._table.item(_row_of(dlg, "snapshot"), 1).text() == "", \
        "the other row still shows a key it will never receive"
    assert dlg.get_keymaps()["snapshot"] == ""
    assert dlg.get_keymaps()["toggle_hard_cuts"] == SPENT


def typing_a_taken_key_ignores_case():
    dlg = _dialog()
    _type_into(dlg, "toggle_hard_cuts", SPENT.upper())
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
    _type_into(dlg, "toggle_hard_cuts", SPENT)
    dlg._reset_defaults()
    assert dlg.get_keymaps()["snapshot"] == v.DEFAULT_KEYMAPS["snapshot"]


def the_dialog_opens_wide_enough_for_its_own_notes():
    """It used to open at a flat 560px with both text columns set to Stretch,
    which splits the leftover EVENLY -- so the Notes column got 179px against
    the 359 it asks for and every note in the table was cut off mid-word,
    including the lock one, the only place the app says that next and
    previous still work while a preset is locked.

    Pixel-free: both sides of the comparison are measured in whatever font is
    running, so the same assertion holds in Segoe UI (359px) and under the
    offscreen fallback (787px). What it pins is that no column is narrower
    than the text in it."""
    dlg = _dialog()
    dlg.show()
    v.QApplication.processEvents()
    table = dlg._table
    for col, what in ((0, "Action"), (2, "Notes")):
        assert table.columnWidth(col) >= table.sizeHintForColumn(col), \
            f"{what} column is {table.columnWidth(col)}px for text needing " \
            f"{table.sizeHintForColumn(col)}px -- it will be elided"


def the_dialog_opens_tall_enough_for_every_row():
    """It opened at its 480px minimum, which showed 9 of 24 rows. Either every
    row fits with no scroll range, or the dialog is as tall as the screen lets
    it be -- the offscreen screen is 800px, so this run takes the second."""
    dlg = _dialog()
    dlg.show()
    v.QApplication.processEvents()
    cap = v.SettingsDialog._screen_height(dlg) - 80
    scroll = dlg._table.verticalScrollBar().maximum()
    assert scroll == 0 or dlg.height() >= cap, \
        f"opened {dlg.height()}px tall with {scroll}px still to scroll " \
        f"and {cap}px available"


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
