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
# os._exit, not a normal return: PySide6 6.11 segfaults tearing an offscreen
# QApplication down at interpreter exit, which turned every clean run of this
# file into exit code 135. Measured on the commit before any of this session's
# work, so it is Qt's teardown and not anything here. Failures still exit
# non-zero -- check() lets the AssertionError out.
sys.stdout.flush()
os._exit(0)
