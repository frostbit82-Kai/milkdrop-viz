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
floor. The archive also ships **Install / Uninstall BomsAI Visualizer.desktop**
so the whole thing is a double-click; both are chmod +x by the build script,
because a .desktop without the bit set is a text file to every file manager.

`installer/build_windows.ps1` produces `BomsAIVisualizer-<version>-win64-setup.exe`
through PyInstaller and Inno Setup 6, plus the portable tree beside it.
`-SkipInstaller` stops after freezing. Inno Setup installs per-user under winget,
so the script looks in `%LOCALAPPDATA%\Programs` as well as Program Files.

**`installer/PROJECTM_COMMIT` pins the exact libprojectM commit both platforms
build.** It is a SHA on master, not a tag, because the API we want has never
been released — upstream's newest tag is v4.1.7 and master declares 4.2.0 with
nothing tagged. Both `build_linux_docker.sh` and `setup_windows.ps1` read that
one file and **refuse to build from a tree that is not at it**, because
checking only that a checkout exists is how the wrong library gets built: this
machine has projectM trees at three different commits, the first one found used
to win, and a 4.1.0 build came out reporting success. Windows also stamps the
commit into `C:\dev\projectm-deploy\PROJECTM_COMMIT`, so a pin bump rebuilds
instead of reusing stale DLLs. Bump the pin and re-run both.

**The vendored `installer/vendor/linux/libprojectM-4*.so.4` are committed on
purpose.** Upstream release links have gone dead before, and rebuilding needs a
toolchain the packaging box may not have. `PROJECTM_LIB_DIR` overrides them.
projectM is LGPL-2.1 and stays a separate `ctypes`-loaded library with its
licence in `installer/licenses/` — that is what makes shipping it in a paid app
legitimate, so do not link it statically. `installer/vendor/windows/` holds
`projectM-4.dll` and `projectM-4-playlist.dll` for the same reason. There is no
third: `glew32.dll` was dropped at the pin below, where projectM stopped using
GLEW.

**The Windows release build is windowed, so it prints nothing.** `console` in
the spec is False on win32 — a black console box behind a visualiser is a
product defect — and `print()` is a no-op when `sys.stdout` is None, so nothing
raises. Build with `MILKDROP_CONSOLE=1` to get a console build back; that is
the only way to see why a frozen build failed.

## Testing

`test_*.py` in the repo root, each runnable on its own with
`QT_QPA_PLATFORM=offscreen`: `browser_search`, `browser_title`, `display_picker`,
`recording` (needs ffmpeg and ffprobe), `clip_audio` (same), `thumbnails`,
`screensaver`, `wallpaper`, `packs`, `settings_diff`, `shuffle`, `theme`,
`idle_pause`, `collections`, `backup`, `tweak`, `sprites`, `code_editor`,
`vsync`, `audio_import`. All twenty must pass before a release.

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
- **Kill background helpers by PID, listed in a separate call.** Any
  `pkill -f` / `pgrep -f` / `ps | grep` whose pattern text also appears
  anywhere in the same command line matches the agent's own shell and kills
  it (exit 144). Bracketing the pattern is not enough, and this has now
  happened three ways: `pkill -f 'scratchpad/.../BomsAIVisualizer'`;
  `pgrep -f 'main[.]py'` where a `grep ... main.py` argument sat later on the
  same line; and `ps | grep 'Xephyr :7'` in a command that went on to launch
  Xephyr. List the PIDs in one call, kill the numbers in the next.
- **Never build a test rig out of symlinks to the source files.** Python resolves
  symlinks in `__file__` for *imported* modules but not for `__main__`, so a rig
  whose `main.py` and `config.py` are links reads and writes the real repo's
  `config.json` while looking isolated. Copy the files.
- **Re-copy the rig after every edit.** A rig running last hour's `viz_common.py`
  reports a feature missing that is sitting finished in the working tree.
- **A Qt panel must never rebuild itself inside a signal it is handling.** The
  rebuild replaces every widget in the tab, the sender included, and the binding
  frees a live sender under itself: the process vanishes with no traceback,
  because no Python exception is involved. Defer with `QTimer.singleShot(0, ...)`
  -- `TweakPanel._rebuild_later`. Measured: changing a sprite's movement killed
  the app every time, and only the live run could see it.
- **Windows will not composite anything over a window whose rectangle is
  exactly the monitor's.** It hands that window the display and the context
  menu is never drawn -- while genuinely being in front: z-order puts it at
  the top, it is the foreground window, it holds the pointer grab and it takes
  clicks. Reported as "the menu flashes once, disappears, and still works as
  if hidden behind the visuals", plus the whole screen flashing as the
  compositor changed mode around it. `_toggle_fullscreen` now calls
  `_overhang_display`, which sits the window `_FS_OVERHANG` pixels past every
  edge: the same pixels are covered and the rectangle is no longer the
  monitor's. Two pixels, not one -- 1920x1081 and 1920x1082 still covered the
  monitor exactly and stayed uncomposited. SDL is not told; the resize comes
  back as a normal `SDL_WINDOWEVENT_RESIZED` and the viewport follows.
  Disabling "fullscreen optimizations" on the exe is *not* a fix -- it reduced
  the flashing to one and left the menu just as invisible.
- **A screen grab cannot see fullscreen or wallpaper mode, and lies
  convincingly.** `Graphics.CopyFromScreen` on a window that covers the screen
  exactly -- SDL's `FULLSCREEN_DESKTOP`, or a borderless window sized to the
  display -- trips DWM's fullscreen optimisation: it returns black, or the last
  frame composited *before* the transition, which reads as "the render stayed
  at the old size in the corner". A layered child of WorkerW is stale for the
  same reason as the note further down. An afternoon went into a resize defect
  that was not there; the renderer, instrumented, had `viewport`, projectM's
  window size, `SDL_GetWindowSize` and `SDL_GL_GetDrawableSize` all correct the
  whole time. Verify those two modes by eye or with a recorded clip. And if a
  clip is black, suspect the preset before the code: ten sampled at random off
  the library gave nine under 26/255 mean brightness.
- **Do not read an unfamiliar tray icon as an absent one.** Windows 11 hides
  new tray icons in the chevron flyout, and this app's 16x16 logo there is a
  small dark square with a gold mark that looks like half a dozen other
  utilities. It was read as "another app's icon", which turned "the tray icon
  never appears" into a confident and completely wrong defect report --
  wallpaper mode's only way out, supposedly broken. What settled it in one
  step: start a **second** process showing the same `app_icon()` and count the
  icons. Two identical ones appeared where there had been one. `HKCU\Control
  Panel\NotifyIconSettings` also lists every icon Windows has registered, by
  executable path, which is the cheaper check and should have been first.

- **vsync can be accepted, reported back, and still not pace anything.**
  10 Sep 2026, this project's Windows box: the app ran at 3.5 fps. A probe with
  none of the app's code in it -- create a window, swap, count -- gave 4.0 fps
  with `SDL_GL_SetSwapInterval(1)` and 4,472 with it off, on a card and display
  that are fine. Foreground or background, 902x632 or 640x480, alone or not: the
  same 4 fps. Fullscreen was the mirror image, ignoring the interval completely
  and running uncapped at 2,648 on a 59 Hz panel. **An hour later the same probe
  read 59.9 fps with no driver change and no reboot**, so it is an intermittent
  stall in the compositing stack and not a driver trait -- the suspicion, not
  established, is DWM left degraded by an afternoon of windows parented into
  Explorer's WorkerW. `_vsync_paces` therefore *measures* six swaps at startup
  and turns vsync off when they do not keep up with the refresh rate, leaving
  the software frame cap to hold the rate; `vsync_paces` is the policy half and
  `test_vsync.py` covers it. Nothing else in the app can see this: SDL reports
  the interval it was asked for and every frame is correct, only late. If the
  frame rate is mysteriously terrible, measure a bare swap loop before
  suspecting anything in this repo.

- **Take the screenshot and look at it.** Driving the app for the site's pictures
  on 2026-09-08 found three defects that seventeen passing suites could not: the
  Backup menu read "Back up ratings _collections…" because Qt eats a lone `&` in
  an action label; every thumbnail in the browser overflowed the row below it;
  and the overlay's blend modes were named for behaviour they do not have. None
  of them is visible from the source, and all three were on screen the whole time.

## Traps already paid for

- **`import sounddevice` raises `OSError`, not `ImportError`, when PortAudio is
  missing — so the usual guard does not catch it and the app will not start.**
  Found on Mint, 10 Sep 2026. sounddevice is a ctypes binding: the *package*
  imports fine and then fails at module scope with `OSError: PortAudio library
  not found`. `except ImportError` around it therefore guards the case that
  never happens (the package is pinned in `requirements.txt`) and misses the
  one that does. Reproduce without touching the system: poison
  `ctypes.util.find_library` for "portaudio" in a subprocess and import
  `viz_common` — that is what `test_audio_import.py` does. The frozen bundle
  carrying `libportaudio.so.2` does **not** save it: `find_library` reads the
  system ldconfig cache and then asks `ld`, and never looks in the bundle, and
  the only name `ld` would resolve out of the bundle's `LD_LIBRARY_PATH` is an
  unversioned `libportaudio.so`, which is not shipped. On Linux this backend
  is only the fallback — parec leads — so losing it should cost the fallback
  and nothing else, which is now what happens.

- **`desktop-file-validate` and GLib disagree about `Exec`, and GLib is the one
  that runs it.** The validator demands `\"` and `\\$` inside a double-quoted
  argument; `GDesktopAppInfo` (GNOME Files, Thunar — anything GLib) refuses to
  load a file containing `\"` at all, returning NULL with no message, so the
  launcher silently does nothing. Measured both ways. The two shipped launchers
  use single quotes, which GLib parses and the validator merely complains about,
  and they take the archive's location from `%k` **passed as an argument**
  (`sh -c '...' sh %k`) rather than substituted into the command, so a path with
  a space in it survives. Verified end to end from the built tarball: launch,
  install, uninstall.

- **projectM's sprite blend modes do not do what their MilkDrop names say.**
  Mode 0 ("alpha") ignores the texture's alpha channel entirely and draws the
  whole rectangle; mode 4 ("colour key") is the only one that honours it —
  upstream's own comment reads "Milkdrop actually changed color keying to using
  texture alpha. The color key is ignored." Mode 2 is additive, which is what
  makes a black background vanish. `SPRITE_BLENDS` is named for the behaviour,
  not the MilkDrop label.
- **projectM already scales a sprite quad by the picture's own aspect ratio**
  (`MilkdropSprite.cpp`, "First aspect ratio"), before the screen-aspect pass.
  Correcting for it in the generated code as well squashed a 400x200 picture to
  630x160 on screen where 630x320 was right. `sy` is set equal to `sx` and left
  alone.
- **`uniformItemSizes` on a `QListWidget` measures once and never again.**
  Not on a size-hint change, not on `doItemsLayout`, not on clear-and-refill.
  The only thing that gets through is a stylesheet `min-height` on
  `QListWidget::item`, which is how the browser makes room for thumbnails that
  arrive after the first layout. Turning uniform sizing off instead costs 0.5s
  per rebuild on a ten-thousand-row list — measured.
- **The welcome preset is what stops projectM drawing its own "M" logo.**
  `ProjectM::LoadIdlePreset()` fires whenever no preset is active, which on a
  fresh install is until the user picks a folder. `assets/welcome.milk` is
  loaded through `projectm_load_preset_data` *before* the library, and it
  samples `assets/textures/bomsai.png` through the texture search path — so
  that directory has to be on it, and it goes last, after the sprite directory,
  for the same reason the sprite directory is late.

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
- **GLEW is gone, and removing it was not optional.** Up to 4.1.0 libprojectM
  linked GLEW on Windows and never called `glewInit()` (upstream only does that
  in the SDL developer UI, which this build excludes), so the host had to call
  it or every GL pointer stayed NULL and `projectm_create()` wrote to address 0.
  At the pinned commit projectM loads OpenGL itself through **glad** —
  `dumpbin /exports` shows no `glew32.dll` and no `OPENGL32.dll` import, and the
  only DLL names inside it are `opengl32.dll`, `libEGL.dll` and the GLES ones it
  resolves with `LoadLibrary`. That made the old `_init_glew()` a startup
  failure rather than a safety net: nothing pulled `glew32.dll` into the process
  any more, so `CDLL("glew32.dll")` raised and the renderer refused to start on
  a machine where everything actually worked. The call and the vendored DLL are
  both gone. If a pin is ever moved *back* to a GLEW-linking projectM, the trap
  comes back with it.
- **vcpkg builds ports with its own cmake, and finds one in
  `C:\Program Files\CMake\bin` before it looks anywhere else.** Taking that
  directory off PATH does not move it — the path is a built-in candidate. It
  matters because this box's standalone CMake has null-filled module files
  (crash damage: right sizes, all zero bytes), so every port build died with
  "Parse error. Expected a command name" pointing inside CMake's own
  `Modules/`. `VCPKG_FORCE_DOWNLOADED_BINARIES=1` makes vcpkg fetch a known-good
  copy instead, and is the way past it without touching the machine's install.
  Check a suspect CMake with `head -c 8 <module>.cmake | od -c`, not with
  `grep`: in bash `$'\0'` is the empty string, so a NUL scan written that way
  matches every file and proves nothing.
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
- **A Qt dialog with no native owner falls behind the SDL window.** Windows
  keeps an owned window above its owner; with `owner=0` there is no
  relationship at all, and one click on the visuals drops the dialog behind
  them with the taskbar entry the only way back. Every dialog had this: they
  are parented to `ContextMenu._tmp_parent`, a QWidget that is never shown, so
  it has no HWND for Qt to make the owner. Worst on a fresh install, where Get
  Presets opens behind the window it exists to explain. `keep_dialogs_above_
  visuals` sets `GWLP_HWNDPARENT` to the SDL window for every top-level Qt
  window, through one application event filter rather than a line at each of
  the nine places a dialog is made. It has to be **deferred by a
  `QTimer.singleShot(0, ...)`**: setting the owner inside the show event does
  take -- reading it straight back returns the handle just written -- and then
  Qt finishes raising the dialog modally and the owner is 0 again. Linux needs
  none of it; the window manager already keeps a dialog above its parent.
  **It must also skip popups.** `isWindow()` is true for a `QMenu` as well
  (`WindowType_Mask` gives 0x9 for Popup against 0x1 Window and 0x3 Dialog),
  and owning the context menu fights the pointer grab that holds it open: it
  flickered open and shut under the cursor and then stopped appearing at all
  until the app was restarted -- reported from a real session, not seen in any
  suite. The toast is a Tool (0xb) and already sits on top by its own flag.
  Note that walking the z-order with `GetTopWindow`/`GetWindow` misreports
  owned windows: check with a screenshot, not with an enumeration.
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
  down, so anything system-shaped inside it is older than a modern host's — and
  the system ffmpeg, started as a child, loaded the bundled `libstdc++.so.6`
  and died with `GLIBCXX_3.4.32 not found`. The app reported "broken pipe",
  which is what the parent saw, not what happened. `child_env()` restores
  `LD_LIBRARY_PATH_ORIG` (or drops the variable when frozen) for every helper
  process. **Test any new subprocess against a real frozen build, not a source
  checkout.** The spec no longer bundles libstdc++ at all (see below), which
  removes the cause; `child_env()` stays, because every other bundled library
  shadows the host's the same way.

- **The bundled GCC runtime stopped the app rendering, and said nothing about
  C++.** 2026-09-09, moving the build container to AlmaLinux 9. The host's
  OpenGL driver is dlopened into this process and Mesa 26 needs
  `GLIBCXX_3.4.32`; AlmaLinux 9 ships 3.4.29, so radeonsi failed to load and
  SDL reported **"Couldn't find matching GLX visual"**. Nothing named
  libstdc++, and the obvious suspects (bundled libX11, libxcb-glx, libXext)
  were each ruled out first. Deleting `_internal/libstdc++.so.6` from a built
  bundle made the same binary render immediately — that is the test. The spec
  now filters `libgcc_s.so*` and `libstdc++.so*` out of `a.binaries`: both are
  on every Linux, both are backward compatible, and the builder's are older
  than the host's by construction. `libgcc_s` was also costing a whole glibc
  version — AlmaLinux 9's is built against 2.34 and still references a 2.35
  symbol.

- **PySide6 sets the Linux glibc floor, not the build container.** Its wheels
  are tagged `manylinux_2_34` from 6.10 onward, so 2.34 is as low as the floor
  goes while shipping this Qt: on a glibc 2.28 image pip offers 6.9.3 as its
  newest. Going lower is a Qt downgrade, not a packaging change. Two dead ends
  worth not repeating: `python:3.13-slim-bullseye` (2.31) cannot even
  `apt-get update` any more — Debian 11 left security support on 2026-08-31 and
  the release file no longer verifies — and the `manylinux` images build their
  CPythons static, which PyInstaller refuses ("Python was built without a
  shared library").

- **The build container's compiler is worth a quarter of the frame rate.**
  AlmaLinux 9's own gcc is 11.5, and libprojectM built with it ran the same
  preset at **~640 fps** against **~890** for a gcc 12 or gcc 14 build —
  uncapped, 1280x720, Radeon Pitcairn, one fixed preset, medians of eight
  five-second samples. Isolated by swapping only the two libprojectM `.so`
  files between two otherwise identical bundles. The Dockerfile installs
  `gcc-toolset-14` and puts it first on PATH; it links the newer libstdc++
  symbols statically, so the built library still needs only `GLIBCXX_3.4.29`
  and the floor stays at 2.34. Re-measure after any toolset or base change —
  `BOMSAI_FPS=1` prints a frame rate every five seconds from any build.
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
- **A feature that is live on one platform and skipped on the other hides
  stub gaps.** `_build_wallpaper_entry` used to return early on Linux, so a
  stub renderer without `is_wallpaper()` built the menu fine there and crashed
  on Windows. It builds everywhere now, but the shape recurs: anything standing
  in for the renderer has to carry every host method, not the ones the local
  platform happens to call. The same shape caught `_fit_to_wallpaper`: it calls
  `glViewport`, which quietly no-ops without a context on Linux and raises
  `GL_INVALID_OPERATION` on Windows, so that suite could not run on the
  platform the feature was for.

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
- **Wallpaper mode on Linux is EWMH, X11 only, and the two non-obvious parts
  are when the property is read and where the window lands.** 2026-09-09.
  `_set_x11_wallpaper` sets `_NET_WM_WINDOW_TYPE` to
  `_NET_WM_WINDOW_TYPE_DESKTOP`, plus `_NET_WM_STATE` = BELOW / SKIP_TASKBAR /
  SKIP_PAGER / STICKY.

  1. **A window manager reads `_NET_WM_WINDOW_TYPE` when the window is
     mapped.** Setting it on a window that is already up changes nothing.
     The window has to go away and come back -- `SDL_HideWindow` /
     `SDL_ShowWindow` rather than `XUnmapWindow`, so SDL's own bookkeeping
     stays true -- with an `XSync` between the property change and the map.
  2. **The desktop type does not say where in the bottom layer you land**, and
     the window holding the desktop icons (xfdesktop, Nemo, plasmashell) is
     already there. The window has to be lowered under it -- but **one
     `XLowerWindow` right after the map is thrown away**: the window manager
     restacks a newly mapped window on its own event loop, which runs after
     ours. Measured under muffin: a single lower left the visualiser above
     nemo-desktop, and a late one stuck. `x11_lower_to_bottom` therefore lowers
     and *checks*, up to a second. **What it checks against is
     `_NET_CLIENT_LIST_STACKING`, not `children[0]`** -- see the two entries
     below, which is where the first version of this went wrong. A desktop that
     never lets go gets a toast rather than a silent failure that looks exactly
     like success, and a window manager that leaves us at the same index twice
     running has refused: it is given up on after ~200 ms rather than the full
     second, because xfwm4 does exactly that and the wait is a frozen render
     thread on every toggle.
  3. **Lower the frame, not the client.** xfwm4 reparents even a desktop window
     into a frame of its own; muffin does not. What sits in the root's stack is
     the frame, so `XLowerWindow` on the client handle moves nothing there --
     measured: the visualiser stayed above xfdesktop and the toast fired.
     `x11_toplevel_of` walks up to the root's child, and it is re-read each
     time round because a window manager can reparent late.
  4. **A reparenting window manager positions the frame, not the client**, so
     asking for (0, 0) does not put the top-left pixel there. xfwm4 framed the
     window at (-5, -29) and left a black strip down the right of the screen
     and along the bottom. The border is a theme setting, not a constant, so
     `x11_align_frame` measures where the frame actually landed and corrects by
     the difference.

  **ctypes signatures are spelled out in `_libx11` because both defaults are
  wrong.** A returned `Atom` through the default `c_int` restype loses its top
  half on 64-bit; a `Window` passed as a bare Python int goes out as `c_int`
  and addresses some other window. `_NET_WM_STATE` is format 32, which in Xlib
  means *an array of `long`* -- `c_ulong`, not `c_uint32`.

  **Wayland cannot host this at all**, XWayland included: an X client under a
  Wayland compositor is an ordinary toplevel and the protocol has nothing
  corresponding to a below-the-desktop layer. `wallpaper_support()` is the one
  gate for the menu entry, the key, the toast and the settings checkbox, and it
  names Xorg in the refusal because that is the only thing the user can do.
  Verified 2026-09-09 against a real X server under Xvfb: properties land with
  type ATOM and format 32, the app enters with `--wallpaper` at full screen
  geometry, and `d` round-trips it back to `_NET_WM_WINDOW_TYPE_NORMAL` with
  `_NET_WM_STATE` deleted and the old geometry restored. **Stacking against
  real desktop icons is now verified on Mint 21.3 / Cinnamon 6.0.5**, on real
  hardware (AMD Pitcairn, Mesa 23.2.1) rather than in the rig -- see the two
  entries below, both of which the rig could not have found.

  **The icon layer has to be composited for any of this to show.** Verified in
  Xephyr with muffin + nemo-desktop: after the lower fix the icons are on top,
  which is the whole point -- but the visuals stayed black. nemo-desktop's
  window is **depth 32** (ARGB), and an ARGB window with **no compositing
  manager running** paints opaque black over whatever is beneath it. The bare
  rig has none (`_NET_WM_CM_S0` has no owner); real Cinnamon does, because
  muffin composites and cinnamon draws the wallpaper rather than nemo-desktop.
  So the rig can prove stacking and cannot prove visibility. Check
  `_NET_WM_CM_S0` before reading anything into a black screen.

  **Telling X is not enough on a compositing window manager.** 2026-09-09,
  Mint 21.3 / Cinnamon 6.0.5. `XLowerWindow` moves the window in the X
  server's stack; muffin keeps its own and paints from that, so the lower is
  either undone on its next restack or simply never reaches the screen. The
  two stacking sources disagreed and the screen followed the window manager:
  `XQueryTree` had the visualiser at index 6 and nemo-desktop at 12 -- below,
  correctly -- while `_NET_CLIENT_LIST_STACKING`, and the picture, had it
  above with every icon covered. `x11_restack_below` sends
  `_NET_RESTACK_WINDOW` to ask the window manager instead; muffin honours it
  and the icons come back on top. Keep the `XLowerWindow` too: it is what a
  window manager with no EWMH restack still understands.

  **`kids[0]` is the wrong test, and it fails on every real desktop.** The
  check was "am I the bottom of the root's children", which held in the rig
  and cannot hold on a session that has been up for a while: the Mint box's
  root had **81** children and the lowest are unmapped utility windows nothing
  can get under, so the loop could only ever spend its second and toast --
  including on a desktop where the visuals were correctly behind the icons.
  `x11_client_stack` reads `_NET_CLIENT_LIST_STACKING`, which holds only
  managed windows in the order they are drawn, and being its first entry is
  the real question. Falls back to the old test where no such property exists.

  **`_NET_WM_CM_S0` is not a reliable compositing check, at least not on
  Cinnamon.** The entry below says to look at it before reading anything into
  a black screen. On Mint 21.3 / Cinnamon 6.0.5 it has **no owner** while
  muffin is plainly compositing -- the visuals showed through and the icons
  composited over them in the same session. An empty result there means
  nothing; believe the screen.

  **Verified end to end in Xephyr, 2026-09-09**, with `xcompmgr -c` supplying
  the compositor:
  - **muffin + nemo-desktop (Cinnamon, the Mint case): correct.** Visuals fill
    the screen as the wallpaper, desktop icons draw on top, `d` round-trips
    back to a normal window and in again. This is the shipped target.
  - **xfwm4 + xfdesktop (XFCE): visuals fill the screen, icons are hidden.**
    Not a bug that can be fixed here -- xfdesktop draws the wallpaper *and* the
    icons on one window, so there is no transparent icon layer to slot beneath.
    Getting under it would mean being invisible. The toast says the icons are
    below the visuals, which is the truth.

  The rig is `xephyr_test.sh` in the session scratchpad; rebuild it from this
  entry if needed. Xvfb alone cannot find any of this: with no window manager
  there is nothing to restack against and nothing to reparent. Three things
  the rig needs that are easy to get wrong:
  - **`XDG_SESSION_TYPE` leaks as well as `WAYLAND_DISPLAY`.** Unsetting only
    the latter still leaves `wayland` in the former, and the app correctly
    refuses — which reads as a broken feature. Every child wants
    `env -u WAYLAND_DISPLAY XDG_SESSION_TYPE=x11`.
  - **`muffin --replace` alone aborts under a Wayland session** — it tries the
    DRM backend and gets `EBUSY` from the real compositor. `muffin --x11
    --replace`. It also needs `cinnamon-settings-daemon` installed, purely for
    its gsettings schemas, or it dies on a missing one. Once muffin is up it
    composites itself and xcompmgr is redundant.
  - **Test against the extracted tarball, not `build/dist`.** Same tree, but it
    is the thing that ships.

  **Verified against the shipped 1.7.0 tarball, 2026-09-09.** muffin + nemo:
  `_NET_CLIENT_LIST_STACKING` = [visualiser, nemo-desktop] with the visualiser
  first, type `_NET_WM_WINDOW_TYPE_DESKTOP`, full screen, visuals on screen.
  xfwm4 + xfdesktop: same properties, xfwm4 declines the restack, the app stays
  second and toasts "could not get below the desktop icons".
- **A screensaver must not open a dialog, and that includes the no-presets
  one.** `_warn_photosensitivity` already skips for this reason;
  `_on_no_presets` did not, so a fresh install that ticked "use as the Windows
  screen saver" before choosing a preset folder got the Get Presets dialog over
  the fullscreen visualiser — unanswerable, because any input ends a
  screensaver.
- **The idle pause has to be checked above auto-advance, not below it.** Below
  it, a paused wallpaper stops *drawing* but goes on rotating presets — it
  comes back on a preset it never showed, having burned through a hundred
  nobody saw. Nothing in the offscreen tests can see loop order, and it only
  showed up under Xvfb with the log open, so `test_idle_pause` asserts the
  order of the three section markers in `Renderer.run` as well as the policy.
- **The render loop sees about a third of the captured audio.** It pops one
  chunk per frame and the backend pushes roughly 86 a second, so at 60fps the
  ring buffer (`deque(maxlen=8)`) drops the rest — which is fine for driving
  projectM and useless for recording. Sound for a clip comes off
  `AudioCapture.set_tap`, straight from the capture thread. A recording built
  out of `get_chunk()` would be silently short, and short in a way that only
  shows up as drift near the end.
- **A recorded clip's video is a constant frame rate and its audio is not.**
  ffmpeg is told `-framerate`, so a clip that dropped frames is shorter than
  the wall clock it covered and the sound runs ahead of the picture.
  `_timestamp_scale()` measures the two against each other and the mux applies
  it with `-itsscale`, which retimes without re-encoding — the video is
  stream-copied. A ratio outside 0.5-4.0 is ignored: that is a stalled encoder
  or a clock jump, and stretching to match it makes things worse.
- **A clip's sound is muxed on at the end, not piped in alongside the frames.**
  Two pipes into one ffmpeg means passing an extra file descriptor, which is
  POSIX-only, or a named pipe on Windows. The WAV-then-remux route costs one
  fast pass and works the same on both.
- **Idle pause never engages while recording.** The sound tap keeps running
  whatever the render loop does, so a pause mid-clip leaves audio minutes
  longer than the video.
- **"Start at login" is applied in `SettingsDialog.accept()`.** A deliberate
  side effect in a dialog: the checkbox shows the state of the registry (or
  `~/.config/autostart`), not of config.json, so `_opened_with["autostart"]` is
  seeded from `autostart_enabled()` — otherwise every OK rewrites the entry.
  Both callers of the dialog get it for free, including the screensaver's `/c`
  branch, which has no renderer to route it through.
- **Collections narrow `_all_preset_files()`, and that is the whole feature.**
  Sort, shuffle, delete-0★ and the browser all go through that one method, so
  none of them has to know collections exist. A member that is no longer on
  disk is simply absent — delete-0★ removes files from inside this very app.
- **Preset tweaking rides on `projectm_load_preset_data`, and half the sliders
  are inert without `preset_driven_keys`.** A preset that sets `zoom` from its
  own per-frame code overwrites the file's value before the first pixel is
  drawn — true of **49% of the 9,795-preset library, measured** — so a slider
  for it would move and do nothing, which reads as a broken feature. The panel
  greys those and says why. Note the ini name and the shader name differ:
  `fDecay` in the file is `decay` in the equations, and `PRESET_TWEAKS` carries
  both because looking for the ini spelling in the code would never match.
- **Swapping a texture never writes to the texture pack.** The picture goes in
  `user_data_dir()/texture-overrides`, which `_apply_texture_paths()` puts
  **first**; libprojectM scans its search paths in order and the first file
  whose *stem* matches wins, so the override stands in front of the stock file
  and Restore is one `os.remove`. The pack is 8,000 files of other people's
  work that the user downloaded separately — overwriting into it would be
  undoable only by re-downloading 2.5 GB. Matching is by stem, so a `.png`
  replaces a stock `.jpg`; never leave two files with one stem in there.
- **Textures resolve when a preset loads, so a swap shows nothing until it
  reloads.** `_do_reset_textures` re-applies the paths (the override directory
  does not exist until the first swap, and non-existent paths are filtered
  out), calls `projectm_reset_textures`, then reloads the preset text. Miss the
  reload and a swap looks like it needs a restart.
- **`load_preset_data` compiles shaders, so it is render-thread only.** The
  browser's panel hands text over through `_req_preset_data`, debounced at
  120 ms — one recompile per drag step is far too many — and the reload is a
  hard cut, because blending each step turns a drag into a smear.
- **A rig restarted without re-exporting `XDG_DATA_HOME` writes to the real
  data directory.** The isolation is in the launcher script, not in the app, so
  restarting the app by hand from another shell silently drops it — a texture
  override landed in the live `~/.local/share/BomsAIVisualizer` that way. Same
  family as the symlink trap above: check where the app actually wrote before
  believing a rig is isolated.
- **Git Bash rewrites a lone `/p` or `/c` into a Windows path.** MSYS2 path
  conversion turns `app.scr /c` into `app.scr C:/`, so the screensaver argument
  tests appear to fail when the app is fine. Drive those from PowerShell, or
  hard-code the flag inside the test script rather than passing it through the
  shell. It catches MSVC tools too: `dumpbin /imports foo.dll` becomes
  `dumpbin 'C:\Program Files\Git\imports' foo.dll` and reports LNK1181 on a
  file you never named.

- **`ffmpeg -f x11grab -i :0` captures black on this machine.** The desktop is
  Wayland and `:0` is Xwayland, whose root window is not what is on screen. The
  app runs there fine and `xdotool` drives it fine — only the screenshot is
  empty, which reads as "the app drew nothing". Nested Xephyr is the only way
  to see the UI: `Xephyr :7 -screen 1280x820 -ac -noreset`, `xfwm4` inside it,
  then grab `:7`.
- **`xdotool type` swallows newlines.** A multi-line string typed into a
  `QPlainTextEdit` arrives as one line, which for a `.milk` file means a preset
  that parses into nothing and reports no error. Send each line separately with
  `xdotool key Return` between them.
- **A source checkout ignores `XDG_CONFIG_HOME`.** `config.py:_base_dir` keeps
  `config.json` beside the code unless `sys.frozen`, so a rig that redirects
  that variable silently runs on the repo's own config — the photosensitivity
  dialog then blocks startup and the app prints nothing at all for the whole
  run. The bench script works because it runs the *frozen* build. From source,
  edit the repo's `config.json` and restore it afterwards.
- **A preset with no `PSVERSION` is read as version 1 and its shader lines are
  never compiled.** Testing shader-error reporting with a hand-written preset
  therefore reports success no matter how broken the shader is. Any fixture
  meant to fail needs `MILKDROP_PRESET_VERSION`, `PSVERSION` and
  `PSVERSION_WARP`. Same reason `mashup_preset` carries PSVERSION across with
  the shader it belongs to.
- **projectM reports a preset it cannot parse and a shader it cannot compile
  through two different channels.** `projectm_set_preset_switch_failed_event_callback`
  fires only from the `catch` around `CreatePresetFromStream`, so it covers
  parse failures alone; a shader that will not compile is merely logged, and
  projectM renders on with that stage dropped. The editor needs both, and the
  log one does not arrive until the preset is *drawn* — `_edit_error_in` waits
  three frames rather than reading back the previous answer.

- **And libprojectM picks that log level by *stage*, not by severity — so no
  level threshold catches all of it.** Found on Mint, 9 Sep 2026: a comp shader
  with a bad type logs `[FinalComposite] Error compiling composite warp shader
  code` at level **4**, and the capture kept `level >= 5`, so `_LAST_PM_ERROR`
  never filled and the editor showed nothing for the one failure the whole of
  A1 exists to surface. It reads as a *timing* bug and is not one; 400 ms was
  always long enough. Read off the 4.2.0 source at the pinned commit, and not
  guessable from either machine alone — a warp break here reports at ERROR, a
  comp break on Mint at WARN:

  | failure | site | level |
  |---|---|---|
  | warp shader load and compile | `PerPixelMesh.cpp:61,80` | `LOG_ERROR` (5) |
  | composite load and compile | `FinalComposite.cpp:49,87` | `LOG_WARN` (4) |
  | the GLSL compiler's own text | `Shader.cpp:215` | `LOG_ERROR` (5) |
  | per-pixel, per-frame, shape, wave | thrown as `MilkdropCompileException` | never above `LOG_DEBUG` — they arrive through the failed-preset callback instead |

  So `_PM_COMPILE_FAIL` matches the message text as well as the level. Do not
  widen it back to the whole of WARN: most of that level is
  `[TextureManager] Failed to find requested texture`, which a preset pack
  triggers constantly and which stops nothing being drawn, and a red line
  under the editor on every other preset is worse than the silence it
  replaced.

- **`xdotool key --window <id>` does not reach SDL.** It sends the event with
  XSendEvent, and SDL ignores synthetic key events; the app sits there while the
  test reports success. Activate the window and send the key with plain
  `xdotool key`. `--window` is fine for pointer clicks, which is why half a rig
  can look like it works.
- **An input source can be open, delivering chunks, and still read exactly
  0.000000** — this machine's analog input sits at 2% (-101 dB), and nothing
  about the capture path says so. Raise it with
  `pactl set-source-volume <src> 100%` before concluding anything about a
  microphone, and put it back afterwards.
- **A parec backend outliving its source proves nothing.** Unload the module
  behind a monitor and `is_alive()` still says True, because parec stays up and
  reconnects to a source of the same name when it returns. Liveness is not audio
  — test with a tone and a peak, never with a process check.

See PORTING.md for port status and the Windows setup recipe.
