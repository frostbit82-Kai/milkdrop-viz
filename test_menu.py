#!/usr/bin/env python3
"""Checks for hiding right-click entries. Run: python test_menu.py

The menu has twenty-odd lines and most of them are also keys, so Customize
lets each one be switched off. Two things have to hold for that to be safe:
every key in MENU_ENTRIES really gates its entry (a typo in one would be a
tick box that does nothing), and the separators written for the full menu do
not survive as double or leading rules once a whole group is gone.

The real ContextMenu is built against a stub renderer -- these are the
shipped strings and the shipped separators, not a model of them.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_common as v                                # noqa: E402

_app = v.QApplication.instance() or v.QApplication([])


class FakeTray:
    @staticmethod
    def isSystemTrayAvailable():
        return True


class MenuHost:
    """Only what ContextMenu asks a renderer for."""
    def __init__(self):
        self.saved = []

    def is_locked(self):      return False
    def is_borderless(self):  return False
    def is_on_top(self):      return False
    def is_fullscreen(self):  return False
    def is_recording(self):   return False
    def is_wallpaper(self):   return False
    def list_displays(self):
        return [(0, "DP-1", 0, 0, 1920, 1080), (1, "HDMI-1", 1920, 0, 1920, 1080)]
    def collections(self):    return {"Chill": ["p001"]}
    def collection(self):     return ""
    def current_preset_stem(self): return "p001"
    def audio_devices(self):  return []
    def audio_device(self):   return ""
    def _save_config(self, cfg): self.saved.append(dict(cfg))


def build(hidden=None):
    """The real menu, with the session checks stubbed out.

    wallpaper_support and the tray are pinned so the wallpaper row is the live
    entry on every machine this runs on -- the point here is whether the row
    is there at all, not whether this desktop could host it.
    """
    real_sup, real_tray = v.wallpaper_support, v.QSystemTrayIcon
    v.wallpaper_support = lambda *a, **k: (True, "")
    v.QSystemTrayIcon = FakeTray
    try:
        cfg = {"menu_hidden": list(hidden or ())}
        return v.ContextMenu(renderer=MenuHost(), cfg=cfg, ratings={},
                             keymaps=dict(v.DEFAULT_KEYMAPS))
    finally:
        v.wallpaper_support = real_sup
        v.QSystemTrayIcon = real_tray


def rows(menu):
    """Visible entry texts, separators dropped."""
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    print(f"  ok  {name}")
    return True


# --- every key gates exactly its own entry --------------------------------
def every_key_in_the_table_switches_a_real_entry_off():
    """A key that matches nothing in _build is a tick box that does nothing,
    and the dialog gives no hint which one it was."""
    full = rows(build())
    for key, label, _action in v.MENU_ENTRIES:
        got = rows(build([key]))
        assert len(got) == len(full) - 1, (
            f"hiding {key!r} ({label}) changed the menu by "
            f"{len(full) - len(got)} entries, not 1: "
            f"{sorted(set(full) - set(got))}")


def nothing_is_hidden_by_default():
    """Hiding is opt-in, so a config with no menu_hidden at all -- every
    config written before this feature existed -- shows the whole menu."""
    real_sup, real_tray = v.wallpaper_support, v.QSystemTrayIcon
    v.wallpaper_support = lambda *a, **k: (True, "")
    v.QSystemTrayIcon = FakeTray
    try:
        bare = v.ContextMenu(renderer=MenuHost(), cfg={}, ratings={},
                             keymaps=dict(v.DEFAULT_KEYMAPS))
    finally:
        v.wallpaper_support = real_sup
        v.QSystemTrayIcon = real_tray
    assert len(rows(bare)) == len(rows(build())), rows(bare)


def a_key_the_menu_has_never_heard_of_is_ignored():
    """A config hand-edited, or written by a later version that had an entry
    this one does not."""
    assert len(rows(build(["a_thing_that_does_not_exist"]))) == len(rows(build()))


def hiding_everything_still_leaves_the_way_back_and_the_way_out():
    """Customize is not in MENU_ENTRIES on purpose: switch off every row and
    the dialog that turns them on again has to still be reachable."""
    keys = [k for k, _l, _a in v.MENU_ENTRIES]
    left = rows(build(keys))
    assert len(left) == 2, left
    assert any("customize" in t.lower() for t in left), left
    assert any("quit" in t.lower() for t in left), left


# --- the rules between the groups -----------------------------------------
def _separator_faults(menu):
    acts = menu.actions()
    faults = []
    if acts and acts[0].isSeparator():
        faults.append("a rule at the very top")
    if acts and acts[-1].isSeparator():
        faults.append("a rule at the very bottom")
    for a, b in zip(acts, acts[1:]):
        if a.isSeparator() and b.isSeparator():
            faults.append("two rules in a row")
    return faults


def the_full_menu_has_no_stray_rules():
    assert not _separator_faults(build()), _separator_faults(build())

def switching_a_whole_group_off_takes_its_rule_with_it():
    """Every separator here is written for the full menu. Hide the group it
    was dividing and it is left as a double line, which reads as a rendering
    fault rather than a setting."""
    for hidden in (["next", "prev", "random"],                      # the top group
                   ["lock"],                                        # a group of one
                   ["record", "wallpaper"],
                   ["audio"],
                   [k for k, _l, _a in v.MENU_ENTRIES]):            # all of it
        menu = build(hidden)
        faults = _separator_faults(menu)
        assert not faults, f"hiding {hidden}: {faults}"


# --- the dialog -----------------------------------------------------------
def the_dialog_shows_what_the_config_hid():
    dlg = v.MenuCustomiseDialog({"menu_hidden": ["record", "backup"]},
                                dict(v.DEFAULT_KEYMAPS))
    assert dlg.hidden() == ["record", "backup"], dlg.hidden()
    assert dlg._boxes["next"].isChecked()
    assert not dlg._boxes["record"].isChecked()

def show_everything_clears_the_lot():
    dlg = v.MenuCustomiseDialog({"menu_hidden": ["record", "backup"]},
                                dict(v.DEFAULT_KEYMAPS))
    dlg._show_everything()
    assert dlg.hidden() == [], dlg.hidden()

def a_row_carries_the_key_that_does_the_same_job():
    """The reason to hide an entry is that it is already a key, so the key is
    the fact the dialog has to put in front of the user."""
    dlg = v.MenuCustomiseDialog({}, {"record": "F9", "toggle_on_top": "t"})
    assert "F9" in dlg._boxes["record"].text(), dlg._boxes["record"].text()
    assert "(t)" in dlg._boxes["on_top"].text(), dlg._boxes["on_top"].text()
    # No key for this one, so no empty brackets either.
    assert "(" not in dlg._boxes["backup"].text(), dlg._boxes["backup"].text()

def a_rebound_key_is_the_one_shown():
    dlg = v.MenuCustomiseDialog({}, {"record": "F4"})
    assert "F4" in dlg._boxes["record"].text(), dlg._boxes["record"].text()


results = []
for fn in (every_key_in_the_table_switches_a_real_entry_off,
           nothing_is_hidden_by_default,
           a_key_the_menu_has_never_heard_of_is_ignored,
           hiding_everything_still_leaves_the_way_back_and_the_way_out,
           the_full_menu_has_no_stray_rules,
           switching_a_whole_group_off_takes_its_rule_with_it,
           the_dialog_shows_what_the_config_hid,
           show_everything_clears_the_lot,
           a_row_carries_the_key_that_does_the_same_job,
           a_rebound_key_is_the_one_shown):
    results.append(check(fn.__name__, fn))

print("all menu checks passed" if all(results) else "FAILURES")
sys.exit(0 if all(results) else 1)
