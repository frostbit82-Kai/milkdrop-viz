#!/usr/bin/env python3
"""Checks that Settings applies only what changed. Run: python test_settings_diff.py

Handing the renderer every field on each Save made an unchanged OK look like a
preset-directory change: it rescanned the whole tree, jumped to the first preset
and dropped the shuffle order. No display needed -- offscreen Qt is enough.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_common as v                              # noqa: E402
from config import DEFAULT_CONFIG                   # noqa: E402

app = v.QApplication.instance() or v.QApplication([])


def check(name, fn):
    fn()
    print(f"  ok  {name}")


def cfg_and_dialog():
    cfg = dict(DEFAULT_CONFIG)
    cfg["preset_dir"] = "/tmp/presets"
    cfg["texture_dir"] = "/tmp/textures"
    return cfg, v.SettingsDialog(cfg)


def an_untouched_dialog_reports_no_changes():
    _, dlg = cfg_and_dialog()
    assert dlg.changed_config() == {}, dlg.changed_config()


def only_the_edited_field_comes_back():
    _, dlg = cfg_and_dialog()
    dlg._dur.setValue(dlg._dur.value() + 7)
    assert set(dlg.changed_config()) == {"preset_duration"}


def the_directories_are_not_reported_unless_edited():
    """The whole point: a full config always carries preset_dir, and the
    renderer keys its expensive rescan off that key being present."""
    _, dlg = cfg_and_dialog()
    dlg._beat.setValue(2.5)
    changed = dlg.changed_config()
    assert "preset_dir" not in changed and "texture_dir" not in changed, changed


def editing_a_directory_does_report_it():
    _, dlg = cfg_and_dialog()
    dlg._preset_dir.setText("/tmp/other-presets")
    assert dlg.changed_config() == {"preset_dir": "/tmp/other-presets"}


def the_snapshot_survives_the_caller_mutating_its_dict():
    """Every caller does cfg.update(...) after exec(); comparing against the
    caller's live dict would then find nothing changed, ever."""
    cfg, dlg = cfg_and_dialog()
    dlg._preset_dir.setText("/tmp/other-presets")
    cfg.update(dlg.get_config())          # what the menu does next
    assert dlg.changed_config() == {"preset_dir": "/tmp/other-presets"}


def the_dialog_fits_on_a_short_screen():
    """Twenty-one rows in a QFormLayout set straight on the dialog make their
    own height a MINIMUM, and the window manager then refuses to shrink it.
    At about 860px that put OK and Cancel off the bottom of a 1366x768
    laptop, with the preset folder -- the first thing a new install has to
    set -- inside the dialog they could not finish. The rows are in a scroll
    area now.

    Pixel-free on purpose: the offscreen plugin's fallback font is nothing
    like the one this ships in, so the absolute numbers mean nothing. What is
    pinned is the RATIO. Unfixed, the minimum hint and the natural hint were
    the same number -- the form's own height was the floor. resize() is no
    probe either: the offscreen plugin honours any size asked for, so it
    passed on the broken build."""
    _, dlg = cfg_and_dialog()
    want = dlg.sizeHint().height()
    floor = dlg.minimumSizeHint().height()
    assert floor <= want // 2, \
        f"the dialog cannot shrink: {floor}px floor against a {want}px form"
    # And the buttons are still reachable: they live outside the scroll area,
    # so they keep their own height whatever the rows do.
    assert dlg.findChild(v.QDialogButtonBox) is not None, "no button box"


def the_dialog_measures_a_settled_layout():
    """It sizes itself off the form's minimum hint, and an unactivated
    QFormLayout under-reports that -- so the dialog opened shorter than its
    own rows and came up with a vertical scroll bar it did not need, down
    every row of a dialog that fits.

    Pixel-free like the check above: what is pinned is that the number the
    dialog read is the number the layout settles on, whatever the font makes
    that. Unfixed it was 782 against 786 in Segoe UI and 711 against 719
    under the offscreen fallback -- different gaps, same bug."""
    _, dlg = cfg_and_dialog()
    form = dlg.findChild(v.QScrollArea).widget()
    as_built = form.minimumSizeHint().height()
    form.layout().activate()
    assert as_built == form.minimumSizeHint().height(), \
        f"the dialog sized itself off a stale layout: {as_built} against " \
        f"{form.minimumSizeHint().height()}"


def the_quality_row_follows_the_ticked_visual():
    """One pair of boxes doing two jobs: a tick switches that visual on AND
    points the rows at it. The bug this guards is the obvious implementation
    -- one quality widget writing one key -- which silently gives visual 2
    visual 1's quality, and the pair costs about twice one of them to draw."""
    cfg = dict(DEFAULT_CONFIG)
    cfg["quality"] = "high"
    cfg["dual_quality"] = "low"
    dlg = v.SettingsDialog(cfg)

    # Opens on visual 1, showing visual 1's quality.
    assert dlg._quality.currentData() == "high", dlg._quality.currentData()
    assert "1" in dlg._quality_label.text(), dlg._quality_label.text()

    # Tick visual 2 and the same row is now visual 2's.
    dlg._visual_box[1].setChecked(True)
    dlg._pick_visual(1, True)
    assert dlg._quality.currentData() == "low", dlg._quality.currentData()
    assert "2" in dlg._quality_label.text(), dlg._quality_label.text()

    # Change it there, and visual 1 keeps what it had.
    dlg._quality.setCurrentIndex(dlg._quality.findData("ultra"))
    out = dlg.get_config()
    assert out["dual_quality"] == "ultra", out["dual_quality"]
    assert out["quality"] == "high", out["quality"]


def an_edit_survives_moving_to_the_other_visual_and_back():
    """The half of this that get_config cannot cover: it flushes whatever is
    on screen when OK is pressed, so a value only goes missing if you leave
    the visual you set it on. Set visual 1 to ultra, look at visual 2, come
    back -- without the flush in _set_editing, visual 1 is quietly back at
    high and the dialog shows the old value as if you never typed it."""
    cfg = dict(DEFAULT_CONFIG)
    cfg["quality"] = "high"
    cfg["dual_quality"] = "low"
    dlg = v.SettingsDialog(cfg)
    dlg._quality.setCurrentIndex(dlg._quality.findData("ultra"))

    dlg._visual_box[1].setChecked(True)
    dlg._pick_visual(1, True)
    assert dlg._quality.currentData() == "low", dlg._quality.currentData()

    dlg._pick_visual(0, True)
    assert dlg._quality.currentData() == "ultra", \
        f"visual 1's edit was lost going to visual 2 and back: " \
        f"{dlg._quality.currentData()}"
    assert dlg.get_config()["quality"] == "ultra"
    assert dlg.get_config()["dual_quality"] == "low"


def unticking_the_last_visual_puts_it_straight_back():
    """Both off is a black window. The box springing back under the cursor
    says so more plainly than a message box would."""
    _, dlg = cfg_and_dialog()
    assert dlg._visual_box[0].isChecked() and not dlg._visual_box[1].isChecked()
    dlg._visual_box[0].setChecked(False)
    dlg._pick_visual(0, False)
    assert dlg._visual_box[0].isChecked(), \
        "the only visual on screen was allowed off"
    assert dlg.get_config()["visual1_enabled"] is True


def switching_a_visual_off_moves_the_rows_to_the_one_left():
    """Nothing to set up on a visual that is not there."""
    cfg = dict(DEFAULT_CONFIG)
    cfg["dual_enabled"] = True
    dlg = v.SettingsDialog(cfg)
    dlg._visual_box[1].setChecked(True)
    dlg._pick_visual(1, True)
    assert "2" in dlg._quality_label.text()
    dlg._visual_box[1].setChecked(False)
    dlg._pick_visual(1, False)
    assert "1" in dlg._quality_label.text(), dlg._quality_label.text()


def the_second_visual_settings_the_crossfader_replaced_are_gone():
    """Strength moved to the right-click menu, where you can see what it does
    while you move it, and quality moved into the row above. Leaving either
    behind means two controls for one thing, disagreeing."""
    dlg = cfg_and_dialog()[1]
    assert not hasattr(dlg, "_dual_opacity"), "the strength spin box is back"
    assert not hasattr(dlg, "_dual_quality"), \
        "there are two quality controls for visual 2 again"
    out = dlg.get_config()
    assert "dual_opacity" not in out, "Settings is still writing the old key"


def a_key_absent_from_the_old_config_counts_as_changed():
    cfg = dict(DEFAULT_CONFIG)
    cfg.pop("theme", None)
    dlg = v.SettingsDialog(cfg)
    assert "theme" in dlg.changed_config()


for name, fn in list(globals().items()):
    if callable(fn) and not name.startswith(("_", "check", "cfg_")) \
            and getattr(fn, "__module__", None) == "__main__":
        check(name, fn)
print("all settings-diff checks passed")
# Not a normal return: PySide6 6.11 segfaults tearing an offscreen QApplication
# down at interpreter exit, which turned every clean run of this file into exit
# code 135 on Linux. Measured on the commit before any of that session's work,
# so it is Qt's teardown and not anything here.
#
# On Windows os._exit is not enough on its own -- it still runs DLL detach, and
# that faults once a SettingsDialog has been built, so a passing run reported
# 139. Closing and deleting the dialogs first does not help: by the end of the
# file they have already been collected. TerminateProcess skips detach
# entirely. Failures still exit non-zero either way -- check() lets the
# AssertionError out, well before this line.
sys.stdout.flush()
if sys.platform == "win32":
    import ctypes
    k32 = ctypes.windll.kernel32
    # Declared, not left to ctypes' int default: GetCurrentProcess returns a
    # HANDLE, and handing a truncated one to TerminateProcess faults on x64 --
    # the same argtypes trap this project has paid for before.
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    k32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    k32.TerminateProcess(k32.GetCurrentProcess(), 0)
os._exit(0)
