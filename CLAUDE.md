# BomsAI Visualizer

Standalone MilkDrop-preset visualizer. Python + projectM 4 + SDL2 + PySide6
(PyQt6 only as the fallback Bomsai Player needs -- see the binding note below).
Ships on Linux and Windows. Sold, not free: Microsoft Store first, the site
alongside it, Steam last. Renamed from "MilkDrop Visualizer" on 2026-09-07 --
that name now appears only as a description of the presets it plays.

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

`viz_common.py` is the whole application — about 5,300 lines holding audio capture,
the projectM ctypes binding, the SDL render loop, frame capture (recording and
thumbnails), the Windows wallpaper plumbing, and every Qt dialog. The rest are thin
per-install shims: `main.py` (entry point, CLI flags, the Windows screensaver
arguments), `renderer.py` (save paths), `config.py` (defaults), `audio.py` /
`projectm_wrapper.py` / `ui.py` (re-exports), `preset_browser.py`.

**ffmpeg is an optional runtime dependency**, for recording only. Found beside the
executable first and on PATH second; absent, the menu says so and nothing else
changes. The encoder is chosen from what that ffmpeg reports rather than named
outright — see `video_codec_args`, and `store/listing.md` for why an LGPL build is
the only one to bundle.

**One clone per machine**: `~/Projects/milkdrop-viz` on Linux,
`C:\Projects\milkdrop-viz` on Windows, and they meet on GitHub — commit, push,
pull. A second copy on the same machine is how the two trees drift apart and how
work gets stranded uncommitted in a folder nobody looks at again. Presets and
textures live outside the repo entirely (`C:\Projects\milkdrop-assets` on
Windows, `~/Downloads` on Linux); `C:\dev` holds only the libprojectM build
toolchain. `installer/README.md` has the full table.

## Packaging

`installer/` builds the release. `bash installer/build_linux.sh` produces a
tarball whose `install.sh` puts the app in `~/.local` with no root. See
`installer/README.md` for the build, the vendored libprojectM and the glibc
floor.

`installer/build_windows.ps1` produces `BomsAIVisualizer-<version>-win64-setup.exe`
through PyInstaller and Inno Setup 6, plus the portable tree beside it.
`-SkipInstaller` stops after freezing. Inno Setup installs per-user under winget,
so the script looks in `%LOCALAPPDATA%\Programs` as well as Program Files.

**The vendored `installer/vendor/linux/libprojectM-4*.so.4` are committed on
purpose.** Upstream release links have gone dead before, and rebuilding needs a
toolchain the packaging box may not have. `PROJECTM_LIB_DIR` overrides them.
projectM is LGPL-2.1 and stays a separate `ctypes`-loaded library with its
licence in `installer/licenses/` — that is what makes shipping it in a paid app
legitimate, so do not link it statically. `installer/vendor/windows/` holds the
same three DLLs for the same reason: `projectM-4.dll`, `projectM-4-playlist.dll`
and the `glew32.dll` the core links.

**The Windows release build is windowed, so it prints nothing.** `console` in
the spec is False on win32 — a black console box behind a visualiser is a
product defect — and `print()` is a no-op when `sys.stdout` is None, so nothing
raises. Build with `MILKDROP_CONSOLE=1` to get a console build back; that is
the only way to see why a frozen build failed.

## Testing

`test_*.py` in the repo root, each runnable on its own with
`QT_QPA_PLATFORM=offscreen`: `browser_search`, `browser_title`, `display_picker`,
`recording` (needs ffmpeg and ffprobe), `thumbnails`, `screensaver`, `wallpaper`,
`packs`, `settings_diff`, `shuffle`, `theme`. All eleven must pass before a release.

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
- **Never build a test rig out of symlinks to the source files.** Python resolves
  symlinks in `__file__` for *imported* modules but not for `__main__`, so a rig
  whose `main.py` and `config.py` are links reads and writes the real repo's
  `config.json` while looking isolated. Copy the files.
- **Re-copy the rig after every edit.** A rig running last hour's `viz_common.py`
  reports a feature missing that is sitting finished in the working tree.

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
- **The theme setting drives two different mechanisms.** `theme` in config.json
  is `dark`, `light` or `system`, and one control has to move both:
  `dialog_qss()`/`menu_qss()`/`browser_qss()` restyle the Qt widgets, and
  `apply_window_theme()` handles the frame Windows draws around the SDL window,
  through DWM's immersive-dark-mode attribute — a stylesheet cannot reach that,
  and Qt's `setColorScheme()` only covers windows Qt created. Linux has no
  equivalent for the frame: the window manager owns it. Everything themed is
  built fresh on open, so there is nothing to restyle live — except the preset
  browser, which is cached and gets its sheet re-applied on show.
- **`resolve_theme("system")` reads Qt's colour-scheme hint**, which Qt fills
  in from the Windows app-theme setting and the freedesktop portal, so no
  registry or gsettings code is needed. It cannot be tested under
  `QT_QPA_PLATFORM=offscreen`: that plugin accepts `setColorScheme()` and goes
  on reporting `Unknown`. `test_theme.py` fakes `QApplication` instead.
- **Shuffle reorders the playlist; it does not randomise `next`.** projectM's
  own shuffle (`projectm_playlist_set_shuffle`) picks a random item on every
  advance, so the list is never walked — left and right both land somewhere
  new and there is no way back to what just played. `_do_shuffle_playlist`
  permutes the playlist instead and `_rebuild_playlist` leaves the native
  shuffle off, so next/prev walk a sequence that happens to be random. Do not
  reintroduce `set_shuffle(True)`; the only caller left passes False.
  Turning shuffle *off* deliberately keeps the order — you carry on walking
  the sequence you are in — so turning it on again is also how you reshuffle.
  Anything that rebuilds the playlist has to say what happens to the flag:
  delete-zero reshuffles when it was on, a new preset folder clears it.
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

- **Preset loading is recursive, and it has to stay that way.**
  `_all_preset_files()` has always globbed with `**`, but the native
  `projectm_playlist_add_path` was called with `recurse=False`, so the playlist
  held only the top level while sort, delete-zero and the browser saw every
  nested preset. It matters because packs are nested: Cream of the Crop's zip
  has no `All/` folder at all — its 9,795 presets sit two deep under category
  names, and pointing at the root loaded **four**. A flat `All/` directory is
  someone's repackaging, not what the repository ships.
- **Settings applies only the fields that changed.** `SettingsDialog.get_config()`
  returns all of them; `changed_config()` diffs against a *copy* of the config the
  dialog opened with, because every caller does `cfg.update(...)` the moment
  `exec()` returns and the renderer shares that same dict. Handing the whole
  config to `apply_settings` made every OK look like a preset-directory change —
  full rescan, back to preset 0, shuffle order and rated history gone, audio
  capture restarted. `_apply_pm_settings` still gets the *merged* config, not the
  diff: it reads every projectM setting with a default, so a partial dict would
  silently reset whatever it did not mention.
- **A texture directory change reloads the preset on screen.** projectM resolves
  textures when it loads a preset, so on a long duration or a locked preset the
  old behaviour was indistinguishable from needing a restart.
- **Google Drive will not hand a large file to a program.** It answers with a
  virus-scan interstitial (`<title>Google Drive - Virus scan warning</title>`)
  and needs a confirm token scraped from it, then throttles once enough people
  have pulled the file — which would surface as this app being broken. Packs
  with no direct URL offer the download page plus a local-zip import instead.
  Do not add Drive-scraping code.
- **`_safe_extract` refuses members that resolve outside the destination.** The
  user picks these archives from wherever they downloaded them, and `zipfile`
  will happily follow `../`.
- **Windows MAX_PATH is what breaks preset packs, and it breaks them by
  default.** A path of 260 characters or more needs long paths switched on in
  the registry, which they are not; `zipfile` raises a bare `ENOENT` partway
  through and leaves a half-installed pack that looks like a working one.
  Cream of the Crop's longest member is 207 characters and the GitHub wrapper
  `presets-cream-of-the-crop-master/` adds 33 more, so **two files overflowed
  under the default install folder** — not an artefact of a deep test path.
  `_safe_extract` drops the archive's single wrapper directory (buying those 33
  characters back, and giving 230 with 29 to spare) and checks every target
  before writing anything, so what still will not fit is a message naming the
  folder length instead of a mess on disk.
- **`_zip_root` must never strip `..`, `.`, or a drive.** Stripping a wrapper
  rewrites member paths, so treating `..` as one turns `../escaped.milk` into
  `escaped.milk`, which then passes the containment check that exists to reject
  it. It also must not give up on the wrapper's own directory entry: `"pack/"`
  splits to `("pack", "")`, and reading that empty half as "no wrapper" left
  every real archive unstripped — which is how the MAX_PATH overflow survived
  its first fix.

- **`threading.Thread.__init__` sets `self._args`.** A `Thread` subclass with a
  method called `_args` never sees it — the instance attribute shadows it, and the
  failure is `TypeError: 'tuple' object is not callable` at the call site rather
  than anywhere near the definition. `_target`, `_kwargs` and `_name` are taken too.
- **glReadPixels must run before `SDL_GL_SwapWindow`,** and needs
  `glPixelStorei(GL_PACK_ALIGNMENT, 1)`. After the swap the back buffer holds the
  frame before last; with the default alignment of 4, any width whose row length is
  not a multiple of it comes back padded and the image shears.
- **A strictly non-blocking hand-off to ffmpeg loses the start of every clip.**
  ffmpeg needs a couple of hundred milliseconds to begin reading and one 720p frame
  nearly fills the OS pipe buffer, so a `put_nowait` behind an 8-slot queue dropped
  13 of the first 24 frames. The recorder waits for room during its first two
  seconds and drops after that.
- **PySide6 6.11 segfaults tearing an offscreen `QApplication` down.** A test file
  that runs to completion still exits 135. `sys.stdout.flush()` then `os._exit(code)`
  is the fix; it predates this session's work, so do not go looking for a cause in
  the test.

- **A subprocess spawned from the frozen build inherits the bundle's loader
  path.** The Linux package is deliberately built old to keep the glibc floor
  at 2.36, so its `libstdc++.so.6` is older than a modern host's — and the
  system ffmpeg, started as a child, loaded that one and died with
  `GLIBCXX_3.4.32 not found`. The app reported "broken pipe", which is what the
  parent saw, not what happened. `child_env()` restores `LD_LIBRARY_PATH_ORIG`
  (or drops the variable when frozen) for every helper process. **Test any new
  subprocess against a real frozen build, not a source checkout.**
- **Always read a dead helper's stderr.** The recorder set its error from the
  write failure and then skipped reading ffmpeg's output, so the one line that
  explained the failure above was thrown away every time.

- **A test that isolates itself with `XDG_DATA_HOME` isolates nothing on
  Windows.** `user_data_dir()` reads `LOCALAPPDATA` there, so `test_thumbnails`
  was writing into the real `%LOCALAPPDATA%\BomsAIVisualizer	humbs` and its
  results depended on what earlier runs had left behind — the gate tests failed
  from run two onward. Set both variables when a suite redirects that directory.
- **Thumbnails are written under a temporary name and moved into place.** The
  preset browser reads that directory while the writer is filling it, and
  `QImage.save` writes in place, so a reader could open a half-encoded JPEG and
  draw nothing. `os.replace` is atomic on both platforms.
- **`_build_wallpaper_entry` returns early on Linux, so a stub renderer without
  `is_wallpaper()` builds the menu fine there and crashes here.** Anything
  standing in for the renderer has to carry the Windows-only host methods too.
  The same shape caught `_fit_to_wallpaper`: it calls `glViewport`, which
  quietly no-ops without a context on Linux and raises `GL_INVALID_OPERATION`
  on Windows, so that suite could not run on the platform the feature is for.

- **Wallpaper mode: two desktop layouts, and Windows 11 needs a layered
  child.** Measured on 11 Pro build 26200 with screen captures as the check.

  Windows 7-10 answer Progman's `0x052C` by creating a second **top-level**
  WorkerW behind the icons; a plain `WS_CHILD` window parented there is drawn,
  which is what every wallpaper app (and `find_workerw`/`pick_workerw`) used.
  `pick_desktop_host` still takes that path first and returns `layered=False`.

  Windows 11 (build 26200) creates no such window. Progman instead has a
  WorkerW **child**, and *that* is the one painting the wallpaper -- confirmed
  by asking each desktop window to `PrintWindow` itself onto a known
  background: only the Progman-child WorkerW came back with wallpaper pixels;
  `SHELLDLL_DefView` and `SysListView32` are transparent (icons only). Two
  things stand between that WorkerW and a visible child, and missing either is
  why this first looked impossible:
  1. The WorkerW has **no `WS_CLIPCHILDREN`**, so it paints the wallpaper
     straight over anything parented into it. Add the style (and restore it on
     leave -- `_wp_host` remembers the old value; harmless to leave set, since
     with no child there is nothing to clip).
  2. A plain child of it is **never composited** -- you can `SwapWindow` into
     it forever and nothing reaches the screen. `SHELLDLL_DefView`, the one
     window in that tree that shows, is `WS_EX_LAYERED`; give our window the
     same (plus `SetLayeredWindowAttributes(..., 255, LWA_ALPHA)` for full
     opacity) and it composites too -- **behind the icons, taskbar and
     widgets**, which is exactly what the store sells. `pick_desktop_host`
     returns `layered=True` for this path.

  Order that works in `_set_wallpaper_mode` (the two obvious rearrangements
  were measured *not* to): set the layer + LWA, `SetParent`, clear
  `WS_POPUP`/frame to make it a real child, then move the **HWND** with
  `SetWindowPos` -- SDL updates its own bookkeeping but will not move a window
  it did not parent (`SDL_GetWindowSize` reported 1920x1080 while the HWND was
  still 900x600, and the visualiser drew in the top-left corner). `_fit_to_
  wallpaper` then does SDL's side (border, GL viewport, projectM size).

  `find_desktop_host` **polls** for the WorkerW: Explorer creates it on its own
  message loop, seconds after `0x052C` returns (longest after an Explorer
  restart). Looking once reported the mode unavailable on a machine that
  supports it.

  **A solid-colour test window will lie here.** DWM composites a layered
  surface once at park time; a test that clears to one colour and never redraws
  shows that first frame frozen forever and looks like failure. The real app
  swaps every frame and stays live -- verify with the actual renderer (or a
  test that keeps drawing *changing* frames), not a static clear.
  `test_wallpaper` exercises `pick_desktop_host` for both the Win10 top-level
  and the Win11 layered-child layouts against hand-built window lists.
- **A screensaver must not open a dialog, and that includes the no-presets
  one.** `_warn_photosensitivity` already skips for this reason;
  `_on_no_presets` did not, so a fresh install that ticked "use as the Windows
  screen saver" before choosing a preset folder got the Get Presets dialog over
  the fullscreen visualiser — unanswerable, because any input ends a
  screensaver.
- **Git Bash rewrites a lone `/p` or `/c` into a Windows path.** MSYS2 path
  conversion turns `app.scr /c` into `app.scr C:/`, so the screensaver argument
  tests appear to fail when the app is fine. Drive those from PowerShell, or
  hard-code the flag inside the test script rather than passing it through the
  shell.

See PORTING.md for port status and the Windows setup recipe.
