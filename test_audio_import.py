#!/usr/bin/env python3
"""The app has to import on a machine with no PortAudio.

`sounddevice` is a ctypes binding, not a pure-Python module: it imports and
*then* looks for the PortAudio shared library at module scope, raising
`OSError: PortAudio library not found` when there isn't one. So the guard
around it has to catch OSError as well as ImportError, or a clean Linux box --
one that never installed libportaudio2 -- cannot start the app at all.

The bundle does ship libportaudio.so.2, which is why this hid for so long: it
does not help, because `sounddevice` resolves the soname through
`ctypes.util.find_library`, and that searches the system, never the bundle. On
Linux the backend is the *fallback* anyway; parec leads. Losing it should cost
the fallback and nothing else.

Checked in a subprocess with `find_library` poisoned, because viz_common is
already imported by the time a test could ask, and because the failure is at
import time -- there is nothing to call.
Run: QT_QPA_PLATFORM=offscreen python test_audio_import.py
"""
import os, sys, subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

results = []


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    print(f"  ok  {name}")
    return True


#: Import viz_common in a fresh interpreter with PortAudio made unfindable,
#: exactly as it is on a box that never installed libportaudio2.
_WITHOUT_PORTAUDIO = f"""
import ctypes.util, sys
_real = ctypes.util.find_library
ctypes.util.find_library = lambda n: None if "portaudio" in n else _real(n)
sys.path.insert(0, {HERE!r})
import viz_common
print("SD_AVAILABLE", viz_common._SD_AVAILABLE)
"""


def _run(src):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    return subprocess.run([sys.executable, "-c", src], capture_output=True,
                          text=True, env=env, timeout=180)


def a_machine_without_portaudio_can_still_start_the_app():
    r = _run(_WITHOUT_PORTAUDIO)
    assert r.returncode == 0, (
        f"viz_common will not import without PortAudio:\n{r.stderr[-700:]}")


def and_it_says_so_rather_than_pretending_the_backend_is_there():
    r = _run(_WITHOUT_PORTAUDIO)
    assert "SD_AVAILABLE False" in r.stdout, r.stdout[-400:]
    assert "sounddevice unavailable" in r.stdout, r.stdout[-400:]


def the_guard_names_oserror_and_not_just_importerror():
    """Pins the fix itself. `except ImportError` alone reads as sufficient --
    it is the failure a missing *package* gives -- and this one is a missing
    *library*, which is a different exception from the same line."""
    src = open(os.path.join(HERE, "viz_common.py"), encoding="utf-8").read()
    at = src.index("import sounddevice as sd")
    guard = src[at:at + 200]
    assert "OSError" in guard, guard.splitlines()[:6]


for name, fn in list(globals().items()):
    if callable(fn) and not name.startswith(("check", "_")) \
            and getattr(fn, "__module__", None) == "__main__":
        results.append(check(name, fn))

print("all audio import checks passed" if all(results) else "FAILURES")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
