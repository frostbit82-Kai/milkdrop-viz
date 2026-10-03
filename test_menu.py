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
    def can_dual(self):       return True
    def dual_on(self):        return False
    def focus(self):          return 0
    def visual_on(self, which):   return not which
    def crossfade(self):      return 0.5
    def second_window_display(self): return -1
    def request_visual(self, which, on): pass
    def request_crossfade(self, x):      pass
    def request_second_window(self, d):  pass
    def request_fullscreen_on(self, d):  pass
    def list_displays(self):
        return [(0, "DP-1", 0, 0, 1920, 1080), (1, "HDMI-1", 1920, 0, 1920, 1080)]
    def collections(self):    return {"Chill": ["p001"]}
    def collection(self):     return ""
    def current_preset_stem(self): return "p001"
    def audio_devices(self):  return []
    def audio_device(self):   return ""
    def _save_config(self, cfg): self.saved.append(dict(cfg))
    # What the rows themselves call, counted, for the stay-open checks.
    def __getattr__(self, name):
        if not name.startswith("request_"):
            raise AttributeError(name)
        return lambda *a: self.asked.append((name,) + a)
    asked = []


def build(hidden=None, groups=None):
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
        if groups is not None:
            cfg["menu_groups"] = groups
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
#: How many rows an entry draws, where it is not the usual one. Written out
#: rather than inferred, because the count is the point: a group that grows
#: or loses a row without this being updated is a row that quietly stopped
#: being hideable, which is exactly what the test below is for.
#:
#: "dual" draws four -- a switch each for the two visuals, the crossfader,
#: and the "Send visual to" submenu.
MENU_ROWS = {"dual": 4}


def every_key_in_the_table_switches_a_real_entry_off():
    """A key that matches nothing in _build is a tick box that does nothing,
    and the dialog gives no hint which one it was."""
    full = rows(build())
    for key, label, _action in v.MENU_ENTRIES:
        want = MENU_ROWS.get(key, 1)
        got = rows(build([key]))
        assert len(got) == len(full) - want, (
            f"hiding {key!r} ({label}) changed the menu by "
            f"{len(full) - len(got)} entries, not {want}: "
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



# --- categories (2 Oct 2026) ----------------------------------------------
def _sub(menu, name):
    hits = [a.menu() for a in menu.actions() if a.menu() is not None
            and a.text().endswith(name)]
    assert len(hits) == 1, (name, [a.text() for a in menu.actions()])
    return hits[0]


def a_category_is_a_submenu_where_its_first_entry_was():
    """On a low-resolution screen the full menu outgrows the display and Qt
    draws it in two columns; a category is how it gets short again."""
    menu = build(groups=[{"name": "Player", "items": ["next", "prev", "random"]}])
    top = rows(menu)
    assert top[0].endswith("Player"), top[:3]
    assert not any("Next visual" in t for t in top), "left a copy behind"
    inner = rows(_sub(menu, "Player"))
    assert [t.split("   ")[-1] for t in inner] == \
        ["Next visual", "Previous visual", "Random visual"], inner
    assert len(top) == len(rows(build())) - 3 + 1


def an_entry_takes_every_row_it_draws_into_its_category():
    """Visuals and crossfade is four rows: two switches, the fader -- a
    widget, not a plain action -- and a submenu. All four move."""
    menu = build(groups=[{"name": "Visuals", "items": ["dual"]}])
    inner = _sub(menu, "Visuals").actions()
    assert len([a for a in inner if not a.isSeparator()]) == MENU_ROWS["dual"]
    assert any(isinstance(a, v.QWidgetAction) for a in inner), \
        "the crossfader did not come with it"
    assert len(rows(menu)) == len(rows(build())) - MENU_ROWS["dual"] + 1


def a_category_with_nothing_to_show_is_not_drawn():
    menu = build(["record"], groups=[{"name": "Clips", "items": ["record"]},
                                     {"name": "Empty", "items": []}])
    assert not any(t.endswith(("Clips", "Empty")) for t in rows(menu)), \
        rows(menu)
    assert not _separator_faults(menu), _separator_faults(menu)


def customize_and_quit_never_go_into_a_category():
    keys = [k for k, _l, _a in v.MENU_ENTRIES]
    menu = build(groups=[{"name": "Everything", "items": keys}])
    top = rows(menu)
    assert len(top) == 3, top
    assert any("customize" in t.lower() for t in top), top
    assert any("quit" in t.lower() for t in top), top
    assert not _separator_faults(menu), _separator_faults(menu)


def an_ampersand_in_a_category_name_survives():
    """Qt eats a lone & in a label -- the Backup menu once read "Back up
    ratings _collections"."""
    menu = build(groups=[{"name": "Rock & roll", "items": ["next"]}])
    act = next(a for a in menu.actions() if a.menu() is not None
               and "Rock" in a.text())
    assert "&&" in act.text(), act.text()


def a_hand_edited_category_list_is_read_defensively():
    cfg = {"menu_groups": [
        {"name": "A", "items": ["next", "nonsense", "next"]},
        {"name": "B", "items": ["next", "prev"]},     # next is A's already
        {"name": "   ", "items": ["random"]},          # no name
        "not even a dict",
    ]}
    assert v.menu_groups(cfg) == [("A", {"next"}), ("B", {"prev"})], \
        v.menu_groups(cfg)
    assert v.menu_groups({}) == []


def the_dialog_round_trips_categories():
    cfg = {"menu_groups": [{"name": "Player", "items": ["next", "prev"]},
                           {"name": "Later", "items": []}]}
    dlg = v.MenuCustomiseDialog(cfg, dict(v.DEFAULT_KEYMAPS))
    assert dlg.groups() == [{"name": "Player", "items": ["next", "prev"]},
                            {"name": "Later", "items": []}], dlg.groups()
    assert dlg._where["next"].currentText() == "Player"
    assert dlg._where["random"].currentText() == dlg.TOP


def a_new_category_gets_a_free_name_and_is_offered_everywhere():
    dlg = v.MenuCustomiseDialog({}, dict(v.DEFAULT_KEYMAPS))
    dlg._new_category()
    dlg._new_category()
    names = [n for _u, n in dlg._cats]
    assert names == ["New list", "New list 2"], names
    combo = dlg._where["random"]
    combo.setCurrentIndex(combo.findText("New list 2"))
    assert dlg.groups()[1] == {"name": "New list 2", "items": ["random"]}


def renaming_keeps_the_entries_and_refuses_a_clash():
    dlg = v.MenuCustomiseDialog(
        {"menu_groups": [{"name": "Player", "items": ["next"]},
                         {"name": "Other", "items": []}]},
        dict(v.DEFAULT_KEYMAPS))
    item = dlg._cat_list.item(0)
    item.setText("Playback")
    assert dlg.groups()[0] == {"name": "Playback", "items": ["next"]}
    assert dlg._where["next"].currentText() == "Playback"
    item.setText("other")                       # case-blind clash
    assert item.text() == "Playback", "two categories may now share a name"
    item.setText("  ")
    assert item.text() == "Playback", "a category lost its name"


def removing_a_category_puts_its_entries_back_on_the_menu():
    dlg = v.MenuCustomiseDialog(
        {"menu_groups": [{"name": "Player", "items": ["next", "prev"]}]},
        dict(v.DEFAULT_KEYMAPS))
    dlg._cat_list.setCurrentRow(0)
    dlg._remove_category()
    assert dlg.groups() == []
    assert dlg._where["next"].currentText() == dlg.TOP
    assert dlg._where["next"].count() == 1


# --- staying open ---------------------------------------------------------
def _click(menu, act, press=True):
    """A real press and release on a row, through the menu itself, so QMenu's
    own close-on-release is in play rather than a model of it."""
    QME, P = v.QtGui.QMouseEvent, v.QtCore.QPointF
    at = P(menu.actionGeometry(act).center())
    glob = P(menu.mapToGlobal(menu.actionGeometry(act).center()))
    left, none = v.Qt.MouseButton.LeftButton, v.Qt.MouseButton.NoButton
    mods = v.Qt.KeyboardModifier.NoModifier
    if press:
        v.QApplication.sendEvent(menu, QME(
            v.QEvent.Type.MouseButtonPress, at, glob, left, left, mods))
    v.QApplication.sendEvent(menu, QME(
        v.QEvent.Type.MouseButtonRelease, at, glob, left, none, mods))
    _app.processEvents()


def _row(menu, text):
    return next(a for a in menu.actions() if text in a.text())


def next_visual_leaves_the_menu_open():
    MenuHost.asked.clear()
    menu = build()
    menu.popup(v.QPoint(40, 40))
    _app.processEvents()
    for _ in range(3):
        _click(menu, _row(menu, "Next visual"))
    assert MenuHost.asked.count(("request_next",)) == 3, MenuHost.asked
    assert menu.isVisible(), "closed on a row meant to keep it open"
    menu.close()


def a_row_that_changes_the_window_still_closes_it():
    MenuHost.asked.clear()
    menu = build()
    menu.popup(v.QPoint(40, 40))
    _app.processEvents()
    _click(menu, _row(menu, "Fullscreen"))
    assert ("request_fullscreen",) in MenuHost.asked, MenuHost.asked
    assert not menu.isVisible(), "stayed up over a window going fullscreen"


def the_release_of_the_click_that_opened_it_fires_nothing():
    """The menu opens under the pointer on the right button's press. Its
    release then lands on a row, and must not trigger it."""
    MenuHost.asked.clear()
    menu = build()
    menu.popup(v.QPoint(40, 40))
    _app.processEvents()
    _click(menu, _row(menu, "Next visual"), press=False)
    assert not MenuHost.asked, MenuHost.asked
    menu.close()


def a_row_in_a_category_stays_open_too():
    MenuHost.asked.clear()
    menu = build(groups=[{"name": "Player", "items": ["next", "prev"]}])
    sub = _sub(menu, "Player")
    sub.popup(v.QPoint(60, 60))
    _app.processEvents()
    _click(sub, _row(sub, "Previous visual"))
    assert ("request_prev",) in MenuHost.asked, MenuHost.asked
    assert sub.isVisible(), "a category row closed the menu"
    sub.close()


def the_lock_row_says_what_the_next_click_does():
    MenuHost.asked.clear()
    menu = build()
    menu.popup(v.QPoint(40, 40))
    _app.processEvents()
    lock = _row(menu, "Lock visual")
    _click(menu, lock)
    assert "Unlock" in lock.text(), lock.text()
    _click(menu, lock)
    assert "Unlock" not in lock.text(), lock.text()
    assert MenuHost.asked.count(("request_toggle_lock",)) == 2
    menu.close()

results = []
def quit_from_the_menu_stops_the_render_thread_and_waits_for_it():
    """The row is QApplication.quit and nothing else, and the render thread is
    a daemon: a clip being recorded was left as two unmuxed halves, and a quit
    from the desktop menu printed a bare "TypeError" from the teardown."""
    import ast, inspect, textwrap, main
    src = ast.unparse(ast.parse(textwrap.dedent(inspect.getsource(main.App.run))))
    after = src[src.index("exec()"):]
    assert "self._renderer._running = False" in after, "the loop is never told"
    assert "self._render_thread.join(" in after, "nobody waits for it"


for fn in (every_key_in_the_table_switches_a_real_entry_off,
           nothing_is_hidden_by_default,
           a_key_the_menu_has_never_heard_of_is_ignored,
           hiding_everything_still_leaves_the_way_back_and_the_way_out,
           the_full_menu_has_no_stray_rules,
           switching_a_whole_group_off_takes_its_rule_with_it,
           the_dialog_shows_what_the_config_hid,
           show_everything_clears_the_lot,
           a_row_carries_the_key_that_does_the_same_job,
           a_rebound_key_is_the_one_shown,
           a_category_is_a_submenu_where_its_first_entry_was,
           an_entry_takes_every_row_it_draws_into_its_category,
           a_category_with_nothing_to_show_is_not_drawn,
           customize_and_quit_never_go_into_a_category,
           an_ampersand_in_a_category_name_survives,
           a_hand_edited_category_list_is_read_defensively,
           the_dialog_round_trips_categories,
           a_new_category_gets_a_free_name_and_is_offered_everywhere,
           renaming_keeps_the_entries_and_refuses_a_clash,
           removing_a_category_puts_its_entries_back_on_the_menu,
           next_visual_leaves_the_menu_open,
           a_row_that_changes_the_window_still_closes_it,
           the_release_of_the_click_that_opened_it_fires_nothing,
           a_row_in_a_category_stays_open_too,
           the_lock_row_says_what_the_next_click_does,
           quit_from_the_menu_stops_the_render_thread_and_waits_for_it):
    results.append(check(fn.__name__, fn))

print("all menu checks passed" if all(results) else "FAILURES")
sys.exit(0 if all(results) else 1)
