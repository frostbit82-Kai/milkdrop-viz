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

See PORTING.md for port status and the Windows setup recipe.
