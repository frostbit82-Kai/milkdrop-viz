#!/usr/bin/env python3
"""
main.py — MilkDrop Visualizer
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
"""

import sys
import os
import signal
import threading
import argparse

os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ.setdefault("SDL_AUDIODRIVER", "pulse")
# Force Qt onto X11/xcb (not Wayland). The SDL window is an X11 surface, and
# Wayland refuses to grab the right-click popup ("Failed to create grabbing
# popup") because it can't anchor to a foreign surface. xcb runs on XWayland.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
os.environ["LD_LIBRARY_PATH"] = "/usr/local/lib:" + os.environ.get("LD_LIBRARY_PATH", "")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import pyqtSignal

from config import load_config, load_ratings, load_keymaps
from renderer import Renderer
from ui import Bridge, ContextMenu, ToastLabel


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
    show_context_menu = pyqtSignal()


class App:
    def __init__(self, cfg_overrides: dict = None, start_borderless: bool = False):
        signal.signal(signal.SIGINT, signal.SIG_DFL)

        self._cfg     = load_config()
        if cfg_overrides:
            self._cfg.update(cfg_overrides)

        self._ratings = load_ratings()
        self._keymaps = load_keymaps()

        self._qt_app = QApplication.instance() or QApplication(sys.argv)
        self._qt_app.setApplicationName("MilkDrop Visualizer")
        self._qt_app.setQuitOnLastWindowClosed(False)

        self._bridge  = AppBridge()
        self._toast   = ToastLabel()
        self._browser = None   # created lazily on first toggle

        self._renderer = Renderer(
            cfg=self._cfg,
            bridge=self._bridge,
            ratings=self._ratings,
            keymaps=self._keymaps,
        )

        # Apply borderless immediately if requested
        if start_borderless:
            self._renderer._borderless_on_start = True

        self._bridge.request_quit.connect(self._on_quit)
        self._bridge.show_toast.connect(self._toast.show_message)
        self._bridge.show_context_menu.connect(self._show_context_menu)
        self._bridge.toggle_preset_browser.connect(self._toggle_browser)
        self._bridge.preset_changed.connect(self._on_preset_changed)
        self._bridge.lock_changed.connect(self._on_lock_changed)
        self._bridge.status_message.connect(self._on_status)

        self._render_thread = threading.Thread(
            target=self._renderer.run, daemon=True
        )

    def run(self) -> int:
        self._render_thread.start()
        return self._qt_app.exec()

    def _on_quit(self):
        self._renderer._running = False
        self._qt_app.quit()

    def _show_context_menu(self):
        menu = ContextMenu(
            renderer=self._renderer,
            cfg=self._cfg,
            ratings=self._ratings,
            keymaps=self._keymaps,
        )
        menu.popup_at_cursor()

    def _toggle_browser(self):
        if self._browser is None:
            from preset_browser import PresetPlaylistWindow
            self._browser = PresetPlaylistWindow(host=self._renderer)
        if self._browser.isVisible():
            self._browser.hide()
        else:
            self._browser.show()
            self._browser.raise_()
            self._browser.activateWindow()

    def _on_preset_changed(self, name: str, rating: int):
        pass

    def _on_lock_changed(self, locked: bool):
        pass

    def _on_status(self, msg: str):
        pass


def main():
    parser = argparse.ArgumentParser(description="MilkDrop Visualizer")
    parser.add_argument("--geometry", default=None,
                        help="Window geometry: WxH+X+Y  e.g. 800x600+0+0")
    parser.add_argument("--borderless", action="store_true",
                        help="Start without window decorations")
    parser.add_argument("--no-audio", action="store_true",
                        help="Disable audio capture")
    parser.add_argument("--preset-dir", default=None,
                        help="Override preset directory")
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

    app = App(cfg_overrides=cfg_overrides, start_borderless=args.borderless)
    exit_code = app.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
