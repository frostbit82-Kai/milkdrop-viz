#!/usr/bin/env python3
"""Checks for the theme setting. Run: python test_theme.py

No display needed -- QApplication under QT_QPA_PLATFORM=offscreen is enough,
and the one thing worth testing is the resolution logic, not how it looks.
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_common as v                              # noqa: E402
from config import DEFAULT_CONFIG                   # noqa: E402

app = v.QApplication.instance() or v.QApplication([])
hints = app.styleHints()


def check(name, fn):
    fn()
    print(f"  ok  {name}")


# --- resolution ---------------------------------------------------------
def explicit_themes_ignore_the_system():
    assert v.resolve_theme("dark") == "dark"
    assert v.resolve_theme("light") == "light"


class _FakeHints:
    def __init__(self, scheme):
        self._scheme = scheme

    def colorScheme(self):
        return self._scheme


class _FakeApp:
    def __init__(self, scheme):
        self._hints = _FakeHints(scheme)

    def styleHints(self):
        return self._hints

    def instance(self):
        return self


def system_follows_qts_colour_scheme():
    """"system" must read Qt's hint, not fall back to a fixed value.

    The hint is fed by the Windows app-theme setting and the freedesktop
    colour-scheme portal, so reading it is how the app follows the desktop.
    It is faked here rather than driven for real: the offscreen platform
    plugin accepts setColorScheme and goes on reporting Unknown, so a test
    that set it would pass by accident on a real desktop and fail in CI.
    """
    real = v.QApplication
    try:
        for scheme, want in ((v.Qt.ColorScheme.Light, "light"),
                             (v.Qt.ColorScheme.Dark, "dark"),
                             (v.Qt.ColorScheme.Unknown, "dark")):
            v.QApplication = _FakeApp(scheme)
            got = v.resolve_theme("system")
            assert got == want, f"{scheme} -> {got}, wanted {want}"
    finally:
        v.QApplication = real


def the_real_platform_reports_a_scheme():
    """Not an assertion about which one -- just that the plumbing is live, so
    a Qt too old to have colorScheme() shows up here rather than silently
    pinning every "system" user to dark."""
    scheme = hints.colorScheme()
    print(f"      (this platform reports {scheme}; "
          f"resolve_theme('system') -> {v.resolve_theme('system')})")


def junk_falls_back_to_dark():
    """Dark is what this app looked like before the setting existed, so an
    unreadable value must not leave a user staring at a half-styled dialog."""
    for bad in ("", "  ", "purple", "SYSTEM ", None, "0"):
        assert v.resolve_theme(bad) in ("dark", "light"), bad
    assert v.resolve_theme("purple") == "dark"


def set_app_theme_rejects_junk():
    v.set_app_theme("light")
    assert v._app_theme == "light"
    v.set_app_theme("nonsense")
    assert v._app_theme == "dark", "a bad config value became the live theme"


# --- stylesheets --------------------------------------------------------
def every_token_is_substituted():
    """A leftover @token@ is invalid CSS: Qt drops the whole rule silently and
    the dialog comes back half-styled with no error anywhere."""
    for theme in ("dark", "light"):
        for name, qss in (("dialog", v.dialog_qss(theme)),
                          ("menu", v.menu_qss(theme)),
                          ("browser", v.browser_qss(theme))):
            left = set(re.findall(r"@[A-Za-z_]+@", qss))
            assert not left, f"{name}/{theme} left {left}"


def arrow_images_exist_and_differ():
    """The shipped arrows are #cbcbcb, invisible on a light spin button, so
    the light sheet has to point at its own pair -- and both must exist, or
    Qt draws an empty box where the arrow was."""
    urls = {}
    for theme in ("dark", "light"):
        urls[theme] = re.findall(r"url\(([^)]+)\)", v.dialog_qss(theme))
        assert len(urls[theme]) == 2, urls[theme]
        for path in urls[theme]:
            assert os.path.exists(path), path
            assert "\\" not in path, f"Qt reads a backslash as an escape: {path}"
    assert urls["dark"] != urls["light"], "both themes point at the same arrows"


def palettes_carry_the_same_keys():
    """A key present in one palette and not the other leaves that theme with
    an unsubstituted token in whichever sheet uses it."""
    dark, light = v._THEME_PALETTES["dark"], v._THEME_PALETTES["light"]
    assert set(dark) == set(light), set(dark) ^ set(light)


def dialog_qss_constant_is_still_the_dark_sheet():
    """Bomsai Player's shims import DIALOG_QSS by name."""
    assert isinstance(v.DIALOG_QSS, str)
    assert v.DIALOG_QSS == v.dialog_qss("dark")


# --- config -------------------------------------------------------------
def default_config_ships_a_valid_theme():
    assert DEFAULT_CONFIG["theme"] in v.THEMES
    assert DEFAULT_CONFIG["theme"] == "dark", "changing this changes every existing install"


def an_old_config_without_a_theme_stays_dark():
    """Existing config.json files predate the setting; they must not suddenly
    render light because the desktop is."""
    merged = dict(DEFAULT_CONFIG)
    merged.update({"preset_dir": "/somewhere", "fps": 60})
    assert v.resolve_theme(merged.get("theme")) == "dark"


if __name__ == "__main__":
    for fn in (explicit_themes_ignore_the_system,
               system_follows_qts_colour_scheme,
               the_real_platform_reports_a_scheme,
               junk_falls_back_to_dark,
               set_app_theme_rejects_junk,
               every_token_is_substituted,
               arrow_images_exist_and_differ,
               palettes_carry_the_same_keys,
               dialog_qss_constant_is_still_the_dark_sheet,
               default_config_ships_a_valid_theme,
               an_old_config_without_a_theme_stays_dark):
        check(fn.__name__, fn)
    print("all theme checks passed")
