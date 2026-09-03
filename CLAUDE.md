# MilkDrop Visualizer

Standalone MilkDrop-preset visualizer. Python + projectM 4 + SDL2 + PyQt6.
Runs on Linux today; being ported to Windows to sell on Steam.

## Rules

**Never bundle presets or textures.** They are other people's work — 8,414 texture
files with no license and artist names in the filenames. Nothing ships. The app asks
where they live (Settings → Preset directory / Texture directory) and the user
downloads them separately. `textures/` and `projectm-presets/` are gitignored and
must stay that way. Do not propose bundling them "just for the demo".

**No personal paths in defaults.** `DEFAULT_CONFIG` ships empty strings for both
directories. A `/home/<name>/...` or `C:\Users\<name>\...` default is a bug.

**`viz_common.py` exists twice.** This copy and `Bomsai Player/visualizer/viz_common.py`
have drifted ~372 lines apart; this one is ahead (preset browser, sort, delete-0★,
reset-ratings, uncapped FPS). Changing one does not change the other. Converge them
before porting anything shared, or the work gets done twice.

## Layout

`viz_common.py` is the whole application — 2,400 lines holding audio capture, the
projectM ctypes binding, the SDL render loop, and every Qt dialog. The rest are thin
per-install shims: `main.py` (entry point, CLI flags), `renderer.py` (save paths),
`config.py` (defaults), `audio.py` / `projectm_wrapper.py` / `ui.py` (re-exports),
`preset_browser.py`.

## Testing

There is no display in an agent shell and SDL needs one, so:

- Qt dialogs: `QT_QPA_PLATFORM=offscreen` and construct the widget directly.
- Logic buried inside long methods: pull the real source out with
  `ast.get_source_segment` and `exec` it against controlled inputs. Tests the shipped
  expression instead of a copy that can drift from it.
- Anything touching the GL loop or live audio has to be run by hand. Say so rather
  than claiming it passed.

## Traps already paid for

- **`PYSDL2_DLL_PATH` must be set before `sdl2` is imported** — PySDL2 reads it once,
  at its own import. It was being set afterwards and silently doing nothing.
- **Do not set `PYSDL2_DLL_PATH` on Windows for a normal install.** The `pysdl2-dll`
  wheel points PySDL2 at its own binaries, but only while the variable is unset.
  Setting it suppresses the wheel. Frozen builds are the exception.
- **`sounddevice` 0.5.5 has no WASAPI loopback.** `WasapiSettings` takes only
  `exclusive`, `auto_convert`, `explicit_sample_format`. Windows capture needs
  `PyAudioWPatch` or `soundcard`.
- Env vars naming X11/PulseAudio drivers or ELF loader paths are Linux-only and are
  gated on `sys.platform`. Adding an ungated one breaks Windows at startup.
- **MSVC emits no `lib` prefix.** projectM's CMake sets `OUTPUT_NAME` to `projectM-4`
  and `projectM-4-playlist`, so a Visual Studio build produces `projectM-4.dll`, not
  `libprojectM-4.dll`. The core candidate list happened to carry both spellings; the
  playlist list only had the prefixed one, so the playlist library silently failed to
  load and the app fell back to direct file loading — losing the playlist features
  with no error. Both spellings are now listed. MinGW/MSYS2 does emit the prefix, so
  keep both.
- **The Windows console is cp1252, and these log messages are full of `←`, `→` and
  `—`.** `print()` raises `UnicodeEncodeError` on them, which is how a *working* WASAPI
  backend got misreported as a failed one: the capture had already started and the log
  line took it down. `_log()` now degrades unprintable characters instead of raising.
  Do not wrap a backend's whole `start()` in `try/except` and infer failure from it —
  an exception there may be about the logging, not the audio.
- **libprojectM does not call `glewInit()`.** On Windows it links GLEW but only
  ever initialises it in the SDL developer UI, which this build excludes. The host
  must call `glewInit()` itself, with a current GL context, before the first
  projectM call — otherwise every GL pointer is NULL and `projectm_create()` writes
  to address 0. This depends on projectM being built against the *shared*
  glew32.dll so both share one pointer table; a static GLEW would break it silently.
- **A wrong ctypes `argtypes` can work on Linux and crash on Windows.** The ABIs
  pass the first argument in different registers (RDI vs RCX), so a missing
  argument picks up different garbage. `projectm_playlist_create` was declared
  no-args for exactly this reason. When a `ctypes` call faults on Windows, check
  the declaration against the installed headers before suspecting the library.
- **"Could not resolve hostname" on Windows is usually broken IPv6, not the network.**
  A host can advertise IPv6 without it working; `getaddrinfo` then returns AAAA records
  that never connect and `curl`/`git` fail most of the time with a DNS-shaped error.
  Split the two before believing it is flaky wifi: `curl -4` vs `curl -6`. `.NET`'s
  resolver caches, so PowerShell's `Resolve-DnsName` will report success and mislead
  you. `setup_windows.ps1` forces IPv4 via `CURL_HOME`, plus `VCPKG_KEEP_ENV_VARS`
  because vcpkg sanitises the environment for port builds. `git.exe` uses libcurl and
  ignores `_curlrc`, so clone in WSL and copy the tree across if a submodule fetch fails.

See PORTING.md for port status and the Windows setup recipe.
