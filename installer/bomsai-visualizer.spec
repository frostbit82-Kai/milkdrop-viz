# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for BomsAI Visualizer.

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
    # No glew32.dll: libprojectM used to link GLEW here and leave the host to
    # call glewInit(), but at the pinned commit it loads opengl32 itself
    # through glad and imports GLEW nowhere. See CLAUDE.md.
    _pm_globs = ("projectM-4*.dll", "libprojectM-4*.dll")
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
    # Everything _asset_path() reaches for: the About dialog's logo, the
    # spin and combo arrows for both themes, and the version it prints. It
    # all lands at the bundle root, which is what _asset_path() looks at
    # when frozen.
    datas=[(str(REPO / "assets"), "assets"), (str(REPO / "VERSION"), ".")],
    hiddenimports=hiddenimports,
    # PyQt6 is excluded deliberately: viz_common falls back to it when
    # PySide6 is missing, so PyInstaller sees the import and would bundle it
    # if it were installed on the build machine -- and a GPL binding is
    # exactly what this app must not ship.
    excludes=["tkinter", "test", "unittest", "pydoc_data", "sdl2dll", "PyQt6"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="BomsAIVisualizer",
    # Linux spawns no terminal either way, so the console build keeps the logs
    # readable. Windows would put a black console box behind the visualiser, so
    # the release build is windowed there -- which also means no log output at
    # all. print() is a no-op when sys.stdout is None, so nothing raises.
    # MILKDROP_CONSOLE=1 forces a console build on Windows too -- the only way
    # to see why a frozen build failed once the release build stopped printing.
    console=sys.platform != "win32" or os.environ.get("MILKDROP_CONSOLE") == "1",
    icon=str(REPO / "installer" / "windows" / "bomsai-visualizer.ico")
         if sys.platform == "win32" else None,
    strip=False,
    upx=False,
)
COLLECT(
    exe, a.binaries, a.datas,
    strip=False,
    upx=False,
    name="BomsAIVisualizer",
)
