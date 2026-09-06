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
are currently **byte-identical** — they were converged after drifting 730 lines apart,
with this copy ahead on every axis. Nothing keeps them in step: they are still two
files, and changing one does not change the other, so they will drift again.

**Run `python check_sync.py` after touching this file** — it is the only thing that
notices the drift, and they have already drifted twice: once by 730 lines, once by a
one-line bug fix that reached the standalone and not the player. `--fix` copies this
copy over the player's; the commit in that repo is still yours to make. A checkout with
no player beside it skips and passes, so it is safe to run anywhere.

When you copy it across, re-check the two things that made convergence non-trivial:

- The player relies on `cfg["embedded"]` to park the SDL window off-screen; its
  `DEFAULT_CONFIG` sets it. Standalone leaves it unset and stays visible.
- The player's shims import a fixed set of names from `viz_common`. Removing a
  public name breaks them silently until import time.

## Layout

`viz_common.py` is the whole application — 2,400 lines holding audio capture, the
projectM ctypes binding, the SDL render loop, and every Qt dialog. The rest are thin
per-install shims: `main.py` (entry point, CLI flags), `renderer.py` (save paths),
`config.py` (defaults), `audio.py` / `projectm_wrapper.py` / `ui.py` (re-exports),
`preset_browser.py`.

## Packaging

`installer/` builds the release. `bash installer/build_linux.sh` produces a
tarball whose `install.sh` puts the app in `~/.local` with no root. See
`installer/README.md` for the build, the vendored libprojectM and the glibc
floor.

**The vendored `installer/vendor/linux/libprojectM-4*.so.4` are committed on
purpose.** Upstream release links have gone dead before, and rebuilding needs a
toolchain the packaging box may not have. `PROJECTM_LIB_DIR` overrides them.
projectM is LGPL-2.1 and stays a separate `ctypes`-loaded library with its
licence in `installer/licenses/` — that is what makes shipping it in a paid app
legitimate, so do not link it statically.

## Testing

There is no display in an agent shell and SDL needs one, so:

- Qt dialogs: `QT_QPA_PLATFORM=offscreen` and construct the widget directly.
- Logic buried inside long methods: pull the real source out with
  `ast.get_source_segment` and `exec` it against controlled inputs. Tests the shipped
  expression instead of a copy that can drift from it.
- Anything touching the GL loop or live audio has to be run by hand. Say so rather
  than claiming it passed.
- **The UI can be driven headlessly**: Xvfb plus `xdotool` for clicks and keys,
  and `xwininfo -id <id>` for what is actually on screen. Two things will mislead
  you. `xwininfo -root -children` lists *unmapped* windows, so a window in that
  list proves nothing — grep its `Map State: IsViewable`. And
  `xdotool search --onlyvisible` finds nothing at all under a WM-less Xvfb.
- **Kill background helpers by PID.** `pkill -f "Xvfb :77"` also matches the
  agent's own shell command line, which contains that string, so it kills the
  shell running the test.

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

- **A signal connected to an empty method looks wired and is not.**
  `status_message` — every "Mode: Random", "Hard cuts: ON", "Sorted by rating"
  the renderer produces — was connected to `App._on_status`, whose body was
  `pass`. Nothing was broken, nothing logged, and none of it ever reached the
  toast. When a UI element is reported missing, check what the signal is
  connected *to* before checking whether it is emitted.
- **A source run and an installed run do not read the same settings.** From a
  checkout, `BASE_DIR` is the repo, so the app uses the developer's own
  `config.json` and `keymaps.json`; frozen, it uses a clean per-user config
  directory and therefore `DEFAULT_KEYMAPS`. The repo's keymaps.json rebinds
  `toggle_mode` from `m` to `r`, so the same keypress does different things in
  the two builds. A UI difference between source and installed is not
  automatically a packaging bug.
- **The context menu is 236x473** — most of a 640x480 window. An automated
  "click outside the menu" has to be outside the *menu's* rectangle, which is
  not where it was opened. Clicking at what looked like empty visualiser landed
  on the menu and produced a convincing false reproduction of "it will not
  close".

- **`_do_reload_playlist` is for delete-zero, not for a new preset folder.**
  It rebuilds the playlist through `projectm_playlist_add_presets`, which some
  projectM builds do not export; there it leaves an empty playlist and every
  name reads "Unknown". A directory change goes through
  `_reload_preset_dir()`, which takes the same route startup does.
- **The Qt binding is PySide6, and viz_common picks it at import.** PySide6 is
  LGPL, which is what lets it ship inside a paid app; PyQt6 is
  GPL-or-commercial. `viz_common` falls back to PyQt6 when PySide6 is absent,
  because the same file is Bomsai Player's visualiser and that app is PyQt6
  throughout -- and it prefers whichever binding is **already imported**, since
  two bindings in one process means two copies of Qt. Nothing else in this repo
  may `import PyQt6` or `import PySide6` directly: take Qt names from
  `viz_common` (or `ui.py`, which re-exports them). The spec excludes PyQt6 so
  a build machine that happens to have it cannot leak it into the package.
- **`pyqtSignal` is `Signal` in PySide6**; `viz_common` exports both names. The
  scoped enums, `exec()`, and everything else this app touches are spelled the
  same. Keyword arguments are not: `addWidget(w, alignment=...)` works in PyQt6
  and not in PySide6, so pass those positionally.
- **A stylesheet replaces native widget drawing, arrows included.** Styling a
  dialog dark leaves QSpinBox and QComboBox as empty boxes with no arrows, and
  Qt does not honour the CSS border-triangle trick (it fills the block). The
  arrows are real 9x6 PNGs in `assets/`, referenced from `DIALOG_QSS` by
  absolute path with backslashes normalised -- Qt's stylesheet parser treats a
  backslash as an escape. A Fusion style plus a dark QPalette was tried first
  and left every child widget light; the stylesheet is what works.
- **Files that ship with the app go through `_asset_path()`** -- the logo, the
  arrows, VERSION. Frozen, that is `sys._MEIPASS`; the spec has to list them in
  `datas` or the About dialog comes up with no logo and version "dev".
- **SDL calls belong to the thread that made the window.** The toast is
  placed by the Qt thread but has to sit inside the SDL window, so the
  renderer caches `(x, y, w, h)` on every `SDL_WINDOWEVENT` and `window_rect()`
  hands back the cache. Do not "simplify" that into a `SDL_GetWindowPosition`
  call from the UI side.
- **The config dict is shared, so a "did it change?" guard can lie.**
  `apply_settings()` hands the render loop the *same* dict object the menu and
  the settings dialog hold, and the loop does `self._cfg.update(updates)`. The
  sensitivity slider previews live through that path, so by the time its save
  timer fired, `self._cfg["audio_gain"]` already held the new value and the
  "unchanged, skip the write" test skipped every write. Nothing errored and the
  sound was correct -- only the persistence was missing. Compare against a value
  you know reached disk, not against the shared config.
- **`sliderReleased` fires for a handle drag and nothing else.** A click on the
  groove or an arrow key changes the value without it. And a submenu closed by
  ESCAPE does not emit `aboutToHide` either -- both measured. A short
  single-shot timer restarted on `valueChanged` is what actually covers every
  way a slider moves.
- **A frozen build changes where three things live, and each broke separately.**
  `config.py` moves `BASE_DIR` to the OS config directory when `sys.frozen` is
  set, because the install directory is replaced wholesale on upgrade.
  `_bundled_libs()` puts `sys._MEIPASS` at the head of the projectM candidate
  lists. And `PYSDL2_DLL_PATH` has to point at `_MEIPASS`, not at
  `dirname(sys.executable)` — for a PyInstaller onedir build those are different
  directories (`_internal` and its parent).
- **The PySDL2 env block in `viz_common.py` runs before the one in
  `renderer.py`.** `main.py` imports `config`, which imports `viz_common`, one
  line before it imports `renderer`. Both blocks now test `sys.frozen` first and
  the platform second, so whichever wins the race sets the same thing. Fixing
  only `renderer.py` looks right and does nothing.
- **`sdl2dll` is excluded from frozen builds and SDL2 is bundled by hand.** The
  wheel's `dll/` directory does not survive the freeze, so `sdl2dll` decides it
  is a source-only install and warns that PySDL2 will ignore it. The spec copies
  `libSDL2-2.0.so` / `SDL2.dll` to the bundle root instead, which is exactly
  where `PYSDL2_DLL_PATH` points.
- **PyOpenGL loads its platform backend and array handlers by name at runtime,**
  so PyInstaller's analysis never sees them and the frozen app dies with
  `TypeError: 'NoneType' object is not callable` inside `OpenGL/platform`. The
  spec adds `collect_submodules("OpenGL.platform")` and `("OpenGL.arrays")`.
- **The app's lifetime belongs to the SDL loop, not to Qt.** The visualiser is
  an SDL window, so Qt's window count hits zero every time a menu or dialog
  closes, and the default `quitOnLastWindowClosed` would end `exec()` there.
  `main.py` has turned it off since before the Windows port; the render loop
  asks to quit through `_on_quit`. Do not remove it, and do not add a second
  call — a Windows session spent a while blaming this for a "clicking Settings
  quits the app" symptom that turned out to be the synthetic-mouse trap below.
- **Enumerating audio devices used to kill the app on Windows.**
  `_WasapiLoopbackBackend.list_devices()` built its own `PyAudio()` and called
  `terminate()` on it, and `Pa_Terminate` tears the WASAPI host API down under
  the capture stream still running on it — an access violation inside
  `_portaudiowpatch`, no Python traceback. It fired on the one dialog a new
  install has to use: no presets → Settings opens → Settings lists devices →
  dead. Every PortAudio user now shares `_WasapiLoopbackBackend._shared_pa`,
  which is never terminated; `stop()` closes the stream and leaves the instance.
  Do not add another `PyAudio()`.
- **JSON config is read with `utf-8-sig`.** Every Windows text editor and
  `Set-Content -Encoding utf8` writes a BOM, and the locale-default `open()`
  then fails on character one. `_load_libraries` also reads config.json, on the
  render thread, and used to do it unguarded — a config error there killed the
  thread before the first frame while every other reader warned and carried on.
- **`sys.stdout` is line-buffered from `viz_common` import.** A frozen build's
  stdout is block-buffered whenever it is redirected and `PYTHONUNBUFFERED` does
  not reliably reach it, so a crash used to take the whole buffer with it and
  the log stopped several seconds before the fault. Without this, a native crash
  points at the wrong place entirely.
- **A native crash names its module in the Windows event log.** `Get-WinEvent
  -LogName Application` filtered to `Application Error` gives the faulting DLL
  and offset. That is what separated "projectM crashed" from "PyAudioWPatch
  crashed" after an afternoon of guessing at the Python side.
- **Synthetic mouse input needs a real move event.** `SetCursorPos` alone does
  not update Qt's idea of the pointer, so a click on a menu item lands where Qt
  last saw the cursor — the menu closes without triggering and it looks exactly
  like the item being broken. Follow every `SetCursorPos` with
  `mouse_event(MOUSEEVENTF_MOVE)`, or drive the menu with arrow keys.
- **A frozen app's stdout is block-buffered when redirected, and `PYTHONUNBUFFERED`
  does not always reach it.** A test that redirects to a file and sees only the
  first line has proved nothing: the app may be running fine and holding the rest.
  Check a side effect instead — `pgrep -a parec`, or `grep projectM /proc/<pid>/maps`.
  Loading 11,223 presets under Xvfb and llvmpipe takes the better part of a minute,
  which is long enough to look like a hang.

- **An empty `audio_device` means "follow the system default", not "no audio".**
  `AudioCapture` runs a 4 s watchdog thread that reopens capture when the stream
  dies and, in follow mode only, when the default output changes. It runs off the
  render thread deliberately: enumeration is a `pactl` subprocess on Linux and a
  fresh `PyAudio()` on Windows, tens of milliseconds either way, and at 120 fps
  that is a visible stutter.
- **Detecting the default and picking the endpoint are two separate queries, so
  they can disagree.** If `default_device_name()` names an endpoint that
  `_pick_device()` will not actually open, the watchdog would reopen every tick
  forever. `_follow_target` caps it at one attempt per distinct target. Keep both
  paths going through `_default_loopback()` so they cannot drift apart.
- **In Bomsai Player this watchdog overlaps `AudioSourceManager`.** That manager
  resolves "auto" to a concrete monitor name *before* handing it over, so
  `is_following()` is False there and only the reconnect half runs — which the
  manager also does. Worst case is one redundant reopen, not a fight. Do not
  "fix" it by deleting the watchdog: standalone milkdrop-viz has no manager.

See PORTING.md for port status and the Windows setup recipe.
