"""
renderer.py — milkdrop-viz standalone renderer.
Core logic lives in viz_common.Renderer; this subclass wires up the local
config/keymaps/ratings save paths and the local projectm_wrapper config_dir.
"""

import sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Before importing viz_common, which imports sdl2: PySDL2 reads PYSDL2_DLL_PATH
# once, at ITS import, so setting it afterwards did nothing. Harmless on Linux,
# where the loader finds libSDL2 on its own, and a hard failure on Windows where
# the DLL sits beside the executable and there is no system path to fall back on.
# Frozen builds come first, on every platform: SDL2 travels inside the bundle, so
# an installed copy must not fall through to whatever the host happens to have.
# NOT for a normal install — the pysdl2-dll wheel points PySDL2 at its own bundled
# binaries, but only while PYSDL2_DLL_PATH is still unset, so setting it for a
# source checkout would suppress the wheel and leave Windows with no SDL2 at all.
# setdefault is what lets this sit above the Linux block instead of inside it.
if getattr(sys, "frozen", False):
    # PyInstaller unpacks to _MEIPASS (the _internal directory for a onedir
    # build); a plain "beside the executable" is only right when there is none.
    os.environ.setdefault(
        "PYSDL2_DLL_PATH",
        getattr(sys, "_MEIPASS", str(Path(sys.executable).parent)),
    )

if sys.platform.startswith("linux"):
    os.environ.setdefault("SDL_VIDEODRIVER", "x11")
    os.environ.setdefault("PYSDL2_DLL_PATH", "/usr/lib/x86_64-linux-gnu")

from viz_common import Renderer as _BaseRenderer, load_projectm, _build_key_lookup, _token_matches

# Import local config helpers so callers can do: from renderer import ...
from config import (
    BASE_DIR, QUALITY_MESH, save_config, save_ratings, save_keymaps,
    set_preset_rating, get_preset_rating, is_excluded, load_keymaps,
    CONFIG_PATH, RATINGS_PATH, KEYMAPS_PATH,
)

_CONFIG_DIR = BASE_DIR   # config.json lives wherever config.py decided


class Renderer(_BaseRenderer):
    """milkdrop-viz renderer — saves config/keymaps/ratings to the local dir."""

    def _save_config(self, cfg: dict):
        save_config(cfg)

    def _save_keymaps(self, km: dict):
        save_keymaps(km)

    def _save_ratings(self, ratings: dict):
        save_ratings(ratings)

    def _create_projectm(self):
        return load_projectm(config_dir=_CONFIG_DIR)

    def state_paths(self) -> dict:
        """Everything a backup should carry. Collections come from the base.

        Named by basename inside the zip rather than by full path, so a backup
        taken on Windows restores on Linux and a source checkout's backup
        restores into an installed build.
        """
        paths = super().state_paths()
        paths.update({
            "config.json":  str(CONFIG_PATH),
            "ratings.json": str(RATINGS_PATH),
            "keymaps.json": str(KEYMAPS_PATH),
        })
        return paths


__all__ = ["Renderer"]
