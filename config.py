"""
config.py — milkdrop-viz standalone config.
Shared logic lives in viz_common.py; this file supplies the local file paths
and the DEFAULT_CONFIG.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from viz_common import (
    QUALITY_MESH,
    DEFAULT_KEYMAPS,
    load_config_from,
    save_config_to,
    load_ratings_from,
    save_ratings_to,
    load_keymaps_from,
    save_keymaps_to,
    get_preset_rating,
    set_preset_rating,
    is_excluded,
)


def _base_dir() -> Path:
    """Where config.json, ratings.json and keymaps.json live.

    A source checkout keeps them beside the code, which is where every existing
    one already sits. An installed build must not: that directory is replaced
    wholesale on upgrade and can be read-only, so state goes to the OS's own
    per-user config location instead.
    """
    if not getattr(sys, "frozen", False):
        return Path(__file__).parent
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming")
    elif sys.platform == "darwin":
        root = Path.home() / "Library/Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    d = root / "MilkDropVisualizer"
    d.mkdir(parents=True, exist_ok=True)
    return d


BASE_DIR = _base_dir()

CONFIG_PATH  = BASE_DIR / "config.json"
RATINGS_PATH = BASE_DIR / "ratings.json"
KEYMAPS_PATH = BASE_DIR / "keymaps.json"

# Presets and textures are not bundled — they are other people's work and are
# downloaded separately — so both default to empty and the settings dialog asks.
DEFAULT_CONFIG = {
    "preset_dir":           "",
    "texture_dir":          "",
    "audio_device":         "",  # Empty = auto-detect at runtime
    "audio_gain":           1.0,  # Input sensitivity multiplier (0.1-8.0)
    "preset_duration":      30,
    "preset_random_extra":  15,
    "fps":                  60,
    "width":                1280,
    "height":               720,
    "quality":              "high",
    # Dark, light, or "system" to follow the desktop. Dark was the only
    # look this app had, so it stays the default.
    "theme":                "dark",
    "mesh_x":               64,
    "mesh_y":               48,
    "hard_cuts_enabled":    True,
    "hard_cut_sensitivity": 1.0,
    "beat_sensitivity":     1.0,
    "soft_cut_duration":    3.0,
    "projectm_lib":         "",
    "projectm_version":     4,
}


def load_config() -> dict:
    return load_config_from(CONFIG_PATH, DEFAULT_CONFIG)


def save_config(cfg: dict):
    save_config_to(CONFIG_PATH, cfg)


def load_ratings() -> dict:
    return load_ratings_from(RATINGS_PATH)


def save_ratings(ratings: dict):
    save_ratings_to(RATINGS_PATH, ratings)


def load_keymaps() -> dict:
    return load_keymaps_from(KEYMAPS_PATH)


def save_keymaps(km: dict):
    save_keymaps_to(KEYMAPS_PATH, km)
