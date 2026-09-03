"""projectm_wrapper.py — re-exported from viz_common.py"""
import sys, os as _os
from pathlib import Path as _Path

sys.path.insert(0, _os.path.dirname(__file__))
from viz_common import ProjectM4, _find_lib, _load_libraries, get_projectm_candidates

_CONFIG_DIR = _Path(__file__).parent


def load_projectm():
    """
    Load libprojectM-4 using this directory's config.json for overrides.
    """
    from viz_common import load_projectm as _load
    return _load(config_dir=_CONFIG_DIR)


__all__ = ["ProjectM4", "load_projectm"]
