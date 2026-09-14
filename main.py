#!/usr/bin/env python3
"""
main.py — BomsAI Visualizer
==============================
Entry point. Wires together config, renderer, audio, and UI.

Launch:  python main.py
Or:      bash run.sh

Extra CLI args accepted by this launcher:
  --geometry WxH+X+Y   — override window size and screen position
                          e.g. --geometry 800x600+100+50
  --borderless          — start with window decorations removed
  --no-audio            — skip audio capture (useful when embedded)
  --preset-dir PATH     — override preset directory
  --screensaver         — fullscreen, and any real input quits
  --wallpaper           — draw on the desktop, behind the icons
                          (Windows, or Linux with an X server — XWayland
                          counts, see wallpaper_support)

Windows screensaver arguments are accepted too: /s runs it, /c opens settings,
/p draws the little preview. They arrive with a slash rather than a dash and
argparse would silently ignore them, so they are read before it runs.
"""

import sys
import os
import signal
import threading
import argparse

# All Linux-only. Every name here is either an X11/PulseAudio driver or an ELF
# loader path, and setting any of them on Windows or macOS stops SDL or Qt from
# starting at all — so the whole block is gated rather than each line guarded.
if sys.platform.startswith("linux"):
    os.environ.setdefault("SDL_VIDEODRIVER", "x11")
    os.environ.setdefault("SDL_AUDIODRIVER", "pulse")
    # Force Qt onto X11/xcb (not Wayland). The SDL window is an X11 surface, and
    # Wayland refuses to grab the right-click popup ("Failed to create grabbing
    # popup") because it can't anchor to a foreign surface. xcb runs on XWayland.
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    os.environ["LD_LIBRARY_PATH"] = "/usr/local/lib:" + os.environ.get("LD_LIBRARY_PATH", "")

from config import load_config, load_ratings, load_keymaps
from renderer import Renderer
# Qt comes through the ui shim, which gets it from viz_common -- that is where
# the PySide6/PyQt6 choice is made, and naming a binding here would undo it.
from ui import (Bridge, ContextMenu, ToastLabel, SettingsDialog, QDialog, QMessageBox,
                APP_NAME, app_icon,
                QApplication, QSystemTrayIcon, Signal, set_app_theme, browser_qss,
                PresetPackDialog, keep_dialogs_above_visuals)


def _parse_geometry(geo_str: str) -> tuple[int, int, int, int] | None:
    """Parse 'WxH+X+Y' or 'WxH' into (w, h, x, y). Returns None on failure."""
    try:
        if '+' in geo_str:
            size_part, rest = geo_str.split('+', 1)
            parts = rest.split('+')
            x, y = int(parts[0]), int(parts[1])
        else:
            size_part = geo_str
            x, y = None, None
        w, h = size_part.split('x')
        return int(w), int(h), x, y
    except Exception:
        return None


class AppBridge(Bridge):
    show_context_menu = Signal()


class App:
    def __init__(self, cfg_overrides: dict = None, start_borderless: bool = False,
                 screensaver: bool = False, wallpaper: bool = False):
        signal.signal(signal.SIGINT, signal.SIG_DFL)

        self._cfg     = load_config()
        if cfg_overrides:
            self._cfg.update(cfg_overrides)

        self._ratings = load_ratings()
        self._keymaps = load_keymaps()

        self._qt_app = QApplication.instance() or QApplication(sys.argv)
        self._qt_app.setApplicationName(APP_NAME)
        self._qt_app.setWindowIcon(app_icon())
        self._qt_app.setQuitOnLastWindowClosed(False)
        # Before any dialog exists, so Qt's palette and the OS-drawn title
        # bars match the sheet the dialogs are about to use.
        set_app_theme(self._cfg.get("theme", "dark"))

        self._bridge  = AppBridge()
        self._toast   = ToastLabel()
        self._browser = None   # created lazily on first toggle

        self._renderer = Renderer(
            cfg=self._cfg,
            bridge=self._bridge,
            ratings=self._ratings,
            keymaps=self._keymaps,
        )

        # Kept on self so the filter is not garbage collected: Qt holds only
        # a borrowed reference to it.
        self._dialog_owner = keep_dialogs_above_visuals(self._qt_app,
                                                        self._renderer)

        # Apply borderless immediately if requested
        if start_borderless:
            self._renderer._borderless_on_start = True

        self._screensaver = screensaver
        if screensaver:
            self._renderer._screensaver = True
        self._start_wallpaper = wallpaper
        self._tray = None

        self._menu = None
        self._asked_for_presets = False

        # Toasts sit in the top-right of the visualiser window, not the
        # screen: the window is usually not fullscreen, and a status message
        # about it belongs over it.
        self._toast.set_anchor(self._renderer.window_rect)

        self._bridge.request_quit.connect(self._on_quit)
        self._bridge.show_toast.connect(self._toast.show_message)
        self._bridge.show_context_menu.connect(self._show_context_menu)
        self._bridge.close_context_menu.connect(self._close_context_menu)
        self._bridge.toggle_preset_browser.connect(self._toggle_browser)
        # status_message carries every "Mode: random", "Hard cuts: ON",
        # "Sorted by rating" the renderer produces. It used to land on an empty
        # method, so none of them were ever shown. preset_changed is left off
        # the toast on purpose: it fires on the automatic rotation as well, and
        # a banner every thirty seconds is not a status update.
        self._bridge.status_message.connect(self._toast.show_message)
        self._bridge.no_presets.connect(self._on_no_presets)
        self._bridge.wallpaper_changed.connect(self._on_wallpaper_changed)
        self._bridge.lock_changed.connect(
            lambda locked: self._toast.show_message(
                # Says what the lock does, because the obvious reading of
                # "locked" is that the controls have gone too, and they have not.
                "Preset locked — it will not change on its own"
                if locked else "Preset unlocked"))

        self._render_thread = threading.Thread(
            target=self._renderer.run, daemon=True
        )

    def run(self) -> int:
        self._warn_photosensitivity()
        self._render_thread.start()
        if self._start_wallpaper:
            self._renderer.request_wallpaper(True)
        return self._qt_app.exec()

    def _on_wallpaper_changed(self, on: bool):
        """Raise a tray icon while the window is the desktop.

        A window parented into Explorer's WorkerW receives no keyboard or
        mouse input at all — the desktop keeps it — so without this there is
        no way to reach the menu, change preset, or quit.
        """
        if on:
            if self._tray is None:
                self._tray = QSystemTrayIcon(app_icon(), self._qt_app)
                self._tray.setToolTip(f"{APP_NAME} — click for the menu")
                self._tray.activated.connect(
                    lambda _reason: self._bridge.show_context_menu.emit())
            self._tray.show()
        elif self._tray is not None:
            self._tray.hide()

    def _warn_photosensitivity(self):
        """Shown once per install, before the first frame.

        The visualiser strobes -- that is the product, not a defect -- and the
        store age-rating questionnaires ask whether it does. Acknowledgement is
        a config flag rather than a marker file so that wiping config.json
        genuinely resets the app.
        """
        if self._cfg.get("seizure_warning_ack"):
            return
        if self._screensaver:
            # A screensaver that opens a modal dialog is a screensaver that
            # never starts. Left unacknowledged on purpose, so the app proper
            # still asks the first time it is opened.
            return
        QMessageBox.warning(
            None, "Photosensitivity warning",
            f"{APP_NAME} reacts to sound with rapidly changing, "
            "flashing and strobing images.\n\n"
            "A small number of people can have seizures when exposed to "
            "flashing lights or patterns, including people with no history "
            "of epilepsy. If you or anyone who will be watching has ever had "
            "a seizure, consult a doctor before using it.\n\n"
            "Stop watching immediately if you feel dizzy, disoriented, or "
            "notice altered vision or involuntary movement.")
        self._cfg["seizure_warning_ack"] = True
        self._renderer._save_config(self._cfg)

    def _on_quit(self):
        self._renderer._running = False
        self._qt_app.quit()

    def _show_context_menu(self):
        self._menu = ContextMenu(
            renderer=self._renderer,
            cfg=self._cfg,
            ratings=self._ratings,
            keymaps=self._keymaps,
        )
        self._renderer.set_menu_open(True)
        try:
            self._menu.popup_at_cursor()      # blocks until the menu closes
        finally:
            self._menu = None
            self._renderer.set_menu_open(False)

    def _close_context_menu(self):
        """The render loop saw a click or key that Qt's popup grab missed."""
        if self._menu is not None:
            self._menu.close()

    def _on_no_presets(self, tried_dir: str):
        """Nothing loaded — first run, or the preset folder has moved.

        The visualiser is black and there is nothing to click inside it, so
        open the one dialog that fixes it. Once per launch: cancelling is an
        answer, not a reason to ask again.
        """
        if self._screensaver:
            # Same reason the photosensitivity warning is skipped above: any
            # input ends a screensaver, so a modal here cannot be answered and
            # covers the thing it is meant to be showing. Measured on Windows
            # with a fresh config -- ticking "use as screen saver" in the
            # installer before choosing a preset folder is the ordinary way in.
            # The app proper still asks the first time it is opened.
            return
        if self._asked_for_presets:
            return
        self._asked_for_presets = True
        self._toast.show_message("No presets loaded", 6000)
        # Get Presets rather than Settings: on a first run there is no folder
        # to point at yet, so a directory picker asks a question the user
        # cannot answer. This dialog answers it and fills the field in.
        self._open_packs()

    def _open_packs(self):
        def applied(updates):
            self._renderer._save_config(self._cfg)
            self._renderer.apply_settings(updates)
        PresetPackDialog(self._cfg, applied).exec()

    def _open_settings(self):
        dlg = SettingsDialog(self._cfg)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            updates = dlg.changed_config()
            self._cfg.update(updates)
            set_app_theme(self._cfg.get("theme", "dark"))
            self._renderer._save_config(self._cfg)
            self._renderer.apply_settings(updates)

    def _toggle_browser(self):
        if self._browser is None:
            from preset_browser import PresetPlaylistWindow
            self._browser = PresetPlaylistWindow(host=self._renderer)
        if self._browser.isVisible():
            self._browser.hide()
        else:
            # The window is built once and reused, so a theme changed since
            # then would leave it in the old one.
            self._browser.setStyleSheet(browser_qss())
            self._browser.show()
            self._browser.raise_()
            self._browser.activateWindow()


def _screensaver_arg(argv) -> str:
    """Which Windows screensaver mode was asked for: "s", "p", "c" or "".

    Windows passes /s, /p <hwnd> or /c[:hwnd], and some callers use a dash
    instead of a slash. argparse treats a slash argument as a positional and
    drops it, so this runs first.
    """
    for a in argv:
        if len(a) >= 2 and a[0] in "/-" and a[1] != "-" and a[1].lower() in "spc":
            return a[1].lower()
    return ""


def _run_settings_only() -> int:
    """The /c branch: show Settings, save, and exit without a visualiser."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    cfg = load_config()
    set_app_theme(cfg.get("theme", "dark"))
    dlg = SettingsDialog(cfg)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        from config import save_config
        cfg.update(dlg.changed_config())
        save_config(cfg)
    return 0


def main():
    mode = _screensaver_arg(sys.argv[1:])
    if mode == "c":
        sys.exit(_run_settings_only())
    if mode == "p":
        # The preview pane. Drawing into the handle Windows passes means
        # reparenting an SDL/OpenGL window into a 150x100 box on somebody
        # else's message loop, for a thumbnail nobody chooses a screensaver
        # from. A blank preview is what several shipped screensavers do.
        sys.exit(0)

    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--geometry", default=None,
                        help="Window geometry: WxH+X+Y  e.g. 800x600+0+0")
    parser.add_argument("--borderless", action="store_true",
                        help="Start without window decorations")
    parser.add_argument("--no-audio", action="store_true",
                        help="Disable audio capture")
    parser.add_argument("--preset-dir", default=None,
                        help="Override preset directory")
    parser.add_argument("--screensaver", action="store_true",
                        help="Fullscreen; any key or real mouse movement quits")
    parser.add_argument("--wallpaper", action="store_true",
                        help="Draw on the desktop behind the icons "
                             "(Windows, or Linux with an X server)")
    args, _unknown = parser.parse_known_args()

    cfg_overrides = {}

    if args.geometry:
        parsed = _parse_geometry(args.geometry)
        if parsed:
            w, h, x, y = parsed
            cfg_overrides["width"]  = w
            cfg_overrides["height"] = h
            if x is not None:
                cfg_overrides["window_x"] = x
                cfg_overrides["window_y"] = y

    if args.no_audio:
        cfg_overrides["audio_device"] = "__disabled__"

    if args.preset_dir:
        cfg_overrides["preset_dir"] = args.preset_dir

    app = App(cfg_overrides=cfg_overrides, start_borderless=args.borderless,
              screensaver=args.screensaver or mode == "s",
              wallpaper=args.wallpaper)
    exit_code = app.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
