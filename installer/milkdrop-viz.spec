# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MilkDrop Visualizer.

Driven by installer/build_linux.sh, which sets the two environment variables
below. It is not run directly: PROJECTM_LIB_DIR has to point at real .so files
and there is no sensible default for it that works on someone else's machine.

Presets and textures are deliberately absent. They are not ours to redistribute
and the app asks where they live at runtime.
"""
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

REPO = Path(os.environ["MILKDROP_REPO"]).resolve()
PROJECTM_DIR = Path(os.environ["PROJECTM_LIB_DIR"]).resolve()

# The one thing this build cannot regenerate. projectM is LGPL-2.1 and is loaded
# with ctypes at runtime, so it ships as a plain shared library the user can
# swap — which is also what keeps the licence satisfied for a paid app.
if sys.platform == "win32":
    # glew32.dll travels with them. libprojectM links GLEW on Windows and never
    # initialises it, so the host calls glewInit() itself — which only works
    # while both are looking at the same shared copy.
    _pm_globs = ("projectM-4*.dll", "libprojectM-4*.dll", "glew32.dll")
else:
    _pm_globs = ("libprojectM-4*.so*",)
binaries = [(str(p), ".") for g in _pm_globs for p in sorted(PROJECTM_DIR.glob(g))]
if not binaries:
    raise SystemExit(f"No {' / '.join(_pm_globs)} found in {PROJECTM_DIR}")

if sys.platform.startswith("linux"):
    # sounddevice dlopens PortAudio by soname and finds it via the bundle's own
    # library path. It is only the fallback capture backend here — parec leads —
    # but without this the fallback is silently unavailable on a host that never
    # installed libportaudio2.
    for cand in ("/usr/lib/x86_64-linux-gnu/libportaudio.so.2",
                 "/usr/lib64/libportaudio.so.2"):
        if os.path.exists(cand):
            binaries.append((cand, "."))
            break

# SDL2 comes from the pysdl2-dll wheel rather than the host, so the bundle does
# not depend on the machine it lands on having libsdl2 installed. It goes to the
# bundle root because that is what PYSDL2_DLL_PATH points at (see viz_common),
# and sdl2dll itself is excluded below: with the wheel's dll/ directory absent
# from the bundle it would only warn that it has no binaries.
import sdl2dll
_sdl_glob = "SDL2.dll" if sys.platform == "win32" else "libSDL2-2.0.so*"
_sdl = sorted(Path(sdl2dll.get_dllpath()).glob(_sdl_glob))
if not _sdl:
    raise SystemExit(f"No {_sdl_glob} in {sdl2dll.get_dllpath()}")
binaries += [(str(p), ".") for p in _sdl]

# PyOpenGL picks its platform backend and array handlers by importing them by
# name at runtime, so the analysis never sees them and the frozen app dies with
# "TypeError: 'NoneType' object is not callable" out of OpenGL/platform.
hiddenimports = ["sounddevice", "preset_browser"]
hiddenimports += collect_submodules("OpenGL.platform")
hiddenimports += collect_submodules("OpenGL.arrays")

a = Analysis(
    [str(REPO / "main.py")],
    pathex=[str(REPO)],
    binaries=binaries,
    # The About dialog's logo, and the version it prints. Both land at the
    # bundle root, which is what _asset_path() looks at when frozen.
    datas=[(str(REPO / "assets"), "assets"), (str(REPO / "VERSION"), ".")],
    hiddenimports=hiddenimports,
    excludes=["tkinter", "test", "unittest", "pydoc_data", "sdl2dll"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="MilkDropVisualizer",
    console=True,          # Linux: no terminal is spawned either way, and logs stay readable
    strip=False,
    upx=False,
)
COLLECT(
    exe, a.binaries, a.datas,
    strip=False,
    upx=False,
    name="MilkDropVisualizer",
)
