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

**A "must be absent" control in the package probe cannot be a word the code
talks about.** The probe inflates the PYZ out of the frozen exe and looks for
names the new work introduced, with two or three never-written names as the
control -- if those "match" too, it is matching noise. 16 Sep 2026 the control
was `SHQueryUserNotificationState`, the API that release had just *removed*,
and it was found: the name survives in the docstring explaining why it went,
and docstrings compile into the bytecode. A control has to be a name that
appears nowhere in the repo at all, comments and prose included; `grep -rn`
for it before trusting the run. The removal itself is checked by grepping the
source for the call, which is the right tool for that question anyway.

**To check what an install actually did, diff the installed tree against the
portable tree the same build produced** -- relative path and size, both ways.
The only difference should be Inno's own `unins000.exe` and `unins000.dat`.
Everything else it can tell you is a corollary, and this catches the one thing
nothing else does: **a file in the install that is not in the package is left
over from an older version.** Inno removes only what it is installing now,
under the names it is installing now, so a DLL or a licence text that stops
shipping survives every upgrade for the life of the machine. 15 Sep 2026 that
found `glew32.dll` and `GLEW-LICENSE.txt` still sitting in a 2.1.6 install,
dropped when the projectM pin moved off GLEW weeks earlier. `[InstallDelete]`
therefore clears `_internal` and `licenses` **wholesale** rather than naming
files: both directories are entirely ours, so anything in them the package
does not carry is by definition stale, and naming things only catches the ones
somebody remembered. The install needs a UAC click and there is no
non-interactive elevation on this box, so this check is one thing an agent
cannot do alone -- ask.

**The version lives in `VERSION` and nowhere else, bar one guarded line.**
Both build scripts read that file. `installer/licenses/EULA.txt` also names the
version, in prose, and nothing reads it back — so both scripts refuse to build
unless the licence says "Applies to version `<VERSION>`". The other half of
that line, the date the *terms* were written, deliberately does not move with
the version.

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
`vsync`, `audio_import`, `lock`, `menu`, `live_textures`, `dual`, `keymaps`,
`browser_toggle`. All twenty-six must pass before a release.

**Driving the shipped build, not the source.** A source checkout ignores
`XDG_CONFIG_HOME` (`config.py:_base_dir` keeps state beside the code unless
`sys.frozen`), but the *frozen* build honours it -- so inflating the tarball
into the scratchpad and pointing `XDG_CONFIG_HOME` and `XDG_DATA_HOME` there
drives the real artifact without going anywhere near the repo's config.json or
the user's `~/.config`. That is how 15 Sep 2026's pass was run. Seed the
config it writes on first run rather than clicking through Settings.

**Under Xephyr, a Qt window that stops repainting is usually starvation, not a
hang.** llvmpipe has no GPU behind it, so one *high*-quality visual at
1600x950 already costs about 600% CPU and the render thread starves the Qt
main thread: the preset browser froze showing stale framebuffer garbage while
the app went on logging preset changes. `quality: low`, `fps: 30` and a small
mesh make the rig usable for UI work. The same number with two visuals at
1920x1080 is recorded above as "not a hang" -- it is the same cause.

**Reviewing by mutation.** Break the shipped code one way at a time, run every
suite, and a green suite is a gap in the tests rather than a pass. Script it,
restore in a `finally`, and print with `flush` -- a redirected python buffers,
and a trailing `| tail` swallows the lot until the pipe closes. Two things
that make the results lies:

- **A killed rig leaves its mutation in the file**, and then every mutation
  after it reads as caught because the suite was already failing. `finally`
  does not run on SIGTERM. Handle the signal *and* check the baseline is green
  before the first mutation -- both are in `scratchpad/mutate.py`. This
  happened on 14 Sep 2026 and voided fourteen results before the rig's own
  "0 matches" line gave it away.
- **Check what the harness counts as a failure before trusting a green run.**
  15 Sep 2026: a mutation rig scored each run with `grep -c "^  FAIL"` and
  reported **every** mutation caught by nothing — because `check()` in
  `test_dual.py` does not catch `AssertionError`, it lets it out, so a failing
  suite dies with a traceback and prints no `FAIL` line at all. Five fixes
  read as untested and were not. Score on the **exit code**, which is the one
  signal every suite here agrees on, and sanity-check the harness by mutating
  something you are certain is covered before believing a zero.
- **A mutation that yields a `SyntaxError`** fails every suite and proves
  nothing. So does one whose original text does not appear exactly once:
  report that as SKIPPED rather than silently doing nothing.
- **On Windows a rig that reads and writes text mode rewrites every line
  ending.** Python's default `newline=None` translates `\n` to `os.linesep` on
  write, so restoring the original string in a `finally` hands back a file that
  is byte-for-byte different: 377 lines of `main.py` turned CRLF that way on
  15 Sep 2026. Git normalises it on commit so the diff still looks right, which
  is exactly why it goes unnoticed. Read and write the file as **bytes**, or
  pass `newline=""`.

There is no display in an agent shell and SDL needs one, so:

- Qt dialogs: `QT_QPA_PLATFORM=offscreen` and construct the widget directly.
- Logic buried inside long methods: pull the real source out with
  `ast.get_source_segment` and `exec` it against controlled inputs. Tests the shipped
  expression instead of a copy that can drift from it.
- Anything touching the GL loop or live audio has to be run by hand. Say so rather
  than claiming it passed.
- **The one shader in the application cannot be compiled by any suite**, so a
  broken blend would ship looking fine — there is no GL context offscreen.
  What *is* checkable without one is the arithmetic: pull the weight each
  visual is multiplied by straight out of `_BLEND_FRAG` with a regex and
  evaluate it as Python at both ends of the fader
  (`test_dual.the_shader_reaches_both_ends_of_the_fader`). That catches the
  shape the shader had until 2.1.7, which scaled only the second visual and
  therefore could reach "visual 1" and "both" but never "visual 2 alone".
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

  **The precondition is being the FOREGROUND window, and 2.1.7 proves it.**
  Visual 2's own window is borderless at exactly a monitor's rectangle, which
  is the shape described above -- and it photographs perfectly: two grabs
  three seconds apart differed in 23,213 of 24,069 sampled pixels, live and
  changing, with the main window drawn over the top of it. `_do_set_window2`
  calls `SDL_RaiseWindow` on the *main* window straight after creating it, so
  the full-screen one never becomes foreground, and DWM's fullscreen
  optimisation never engages. So do not read "borderless and the size of a
  monitor" as "cannot be captured": it is that plus the foreground. Which
  also means the overhang trick is not needed for that window, and should not
  be added -- on a real second monitor it would spill two pixels onto the
  neighbour.

  **There IS a capture that sees the real screen: the Desktop Duplication
  API, through ffmpeg's `ddagrab` filter.** It is what OBS uses, it reads the
  composed output rather than the desktop DC, and it settled in one shot a
  defect that three rounds of `CopyFromScreen` had been lying about:

      ffmpeg -f lavfi -i "ddagrab=0:framerate=1" -frames:v 4              -vf "hwdownload,format=bgra,scale=960:540" -y shot_%02d.png

  (`ddagrab` is a *filter*, not a demuxer -- `-f lavfi`, and the hwdownload is
  not optional, the frames arrive on the GPU.) 16 Sep 2026 the GDI grab of a
  wallpaper-mode desktop showed the visuals frozen in the pre-transition
  window rectangle with the icons drawn over them, and four separate "fixes"
  appeared to change it; `ddagrab` showed the truth every time -- the visuals
  covering the whole screen, taskbar and icons and every other window gone.
  Use it for anything that has to be seen rather than measured, and keep the
  GDI grab only for ordinary windows.

  **Do not reach for PrintScreen on this box.** `SendKeys {PRTSC}` leaves the
  clipboard empty and opens the Snipping Tool overlay over whatever the user
  is doing -- ESC closes it, but it is a full-screen interruption on someone
  else's desktop for no result.
- **Wallpaper mode trips the same fullscreen-optimisation trap as fullscreen
  mode, and the report reads completely differently.** Reported 16 Sep 2026 as
  *"it starts out fullscreen at first, but when I click or right-click
  anywhere, it snaps behind icons and widgets"* -- which sounds like a
  stacking bug and is not one. The window is a child of Explorer's WorkerW,
  below `SHELLDLL_DefView` in the tree the whole time, structurally perfect:
  what fails is composition. Its rectangle is exactly the monitor's and it is
  the foreground window at the moment it is parented (the user clicked the
  menu entry), so DWM hands it the display and stops compositing everything
  else -- icons, taskbar, Rainmeter widgets, other applications' windows. The
  fix is `_FS_OVERHANG` again, two pixels past every edge, applied to the
  wallpaper box: `wallpaper_box(displays, want, overhang)` takes it as an
  argument because **X11 must not have it** -- two pixels off the screen there
  is a window manager clamping the window back and `_hold_wallpaper_box`
  correcting it, for ever. Measured A/B/A/B with `ddagrab` (see above), which
  is the only reason this was settled rather than guessed at: four plausible
  fixes -- clearing the `WS_POPUP` SDL puts back, handing the foreground to
  Progman, hiding and re-showing, a real resize -- all changed the GDI grab
  and none of them changed the screen.

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

- **PowerShell unrolls an array of arrays on the way into a pipeline, so one
  matching window comes back as six scalars.** `[A]::Windows()` returns rows
  of `object[]`; `... | Where-Object {...}` piped into `ForEach-Object` hands
  each *field* to `$_` when exactly one row matched, so `$_[2]` then indexes a
  **string** and the output is single characters where coordinates should be
  ("popup m,s AxI"). It reads as the window list being wrong rather than as
  the shell taking it apart. `| Select-Object -First 1` happens to survive it,
  which is why the older helpers never hit it. Use a `foreach` statement over
  `[A]::Windows()` and never a pipeline -- `Popups` in `scratchpad/lib.ps1` is
  that shape, and returns `,$array` so the return value does not unroll either.

- **A window-listing helper that reads titles takes the harness down with a
  hung app.** `GetWindowTextW` is a `SendMessage` of `WM_GETTEXT`, so calling
  it on a window whose thread has stopped pumping blocks **forever** — and
  `Show-Wins` / `Get-Win` call it on every visible window, this app's
  included. 15 Sep 2026 that turned a frozen app into a frozen PowerShell and
  a two-minute tool timeout with no output at all, which reads like the script
  being wrong rather than the app being stuck. Ask
  `(Get-Process ...).Responding` first — it is a five-second
  `SendMessageTimeout` and it answers — and only then go looking by title.
  `Focus` is worse than a read: `AttachThreadInput` shares an input queue with
  the app, and Microsoft's own documentation says that can hang the caller.

- **A window-rectangle screen grab can come back stale, intermittently.**
  15 Sep 2026: `CopyFromScreen` over a freshly shown dialog's rectangle
  returned the *dark* theme twice for a dialog that was demonstrably light —
  its stylesheet, its palette and `QWidget.grab()` all said `#f5f5f5`, and a
  full-screen grab of the same window a minute later agreed with them. Two
  runs with identical timing disagreed, so it is a race and not a procedure.
  A wrong screenshot is the worst possible evidence here, because the whole
  method is looking at pictures: when one shows something surprising, take a
  second one — a full-screen grab and a crop — before believing it.

- **Take the screenshot and look at it.** Driving the app for the site's pictures
  on 2026-09-08 found three defects that seventeen passing suites could not: the
  Backup menu read "Back up ratings _collections…" because Qt eats a lone `&` in
  an action label; every thumbnail in the browser overflowed the row below it;
  and the overlay's blend modes were named for behaviour they do not have. None
  of them is visible from the source, and all three were on screen the whole time.

## Traps already paid for

- **The Qt binding is PySide6, and a test venv built on PyQt6 fails two suites
  for reasons that are not the code.** `viz_common` prefers PySide6 and falls
  back to PyQt6 for Bomsai Player's sake, so a PyQt6 venv imports fine and runs
  almost everything — then `test_theme` dies on *"no access to protected
  functions or signals for objects not created from Python"* and
  `test_thumbnails` on a `QImage.save` unbound-method signature. Both pass on
  PySide6. Measured 16 Sep 2026: the same tree that read 20/22 under PyQt6
  reads 26/26 under PySide6. Install from `requirements.txt`, and if a pin will
  not resolve, fix the pin rather than substituting the binding.

- **`requirements.txt` is pinned for the Python the bundle is built with
  (3.14), not for the one a host distribution ships.** On Mint 21.3 (Python
  3.10) `numpy==2.4.6` does not exist — 2.2.6 is the newest 3.10 wheel — and
  the whole install aborts on that one line, leaving a venv with nothing in it.
  Pin-for-pin fidelity is not available off the build image; what matters for
  the suites is the *binding*, so install PySide6 at its pin and let the rest
  float.

- **A source run keeps its config in the repo directory, so `XDG_CONFIG_HOME`
  cannot isolate one.** `config._base_dir` returns `Path(__file__).parent`
  unless `sys.frozen`, and only the frozen build looks at `XDG_CONFIG_HOME`.
  So running `main.py` reads and *writes* the repo's own `config.json` —
  gitignored, so nothing shows up in `git status` to warn you. Back it up and
  put it back, or drive a frozen build instead.

- **Mint's system SDL2 is too old for the pinned PySDL2, and the symptom names
  the wrong thing.** Jammy ships libSDL2 2.0.20; PySDL2 0.9.17 expects the 2.32
  series, and `SDL_GetWindowWMInfo` fails the version check, so there is no
  native handle and wallpaper mode reports *"no X11 window to make the
  desktop"* — which reads as a wallpaper bug and is a binding mismatch.
  `viz_common` does `setdefault("PYSDL2_DLL_PATH", "/usr/lib/x86_64-linux-gnu")`
  on Linux, which wins over the `pysdl2-dll` wheel, so a host with an old SDL2
  has to set `PYSDL2_DLL_PATH` at the wheel's `dll` directory explicitly.

- **A reparenting window manager ignores where `SDL_CreateWindow` was told to
  put a borderless window.** 2.1.7's second visual gets an SDL window of its
  own on another monitor; created at (1100, 0) under xfwm4 it was *mapped at
  (0, 0)*, on top of the main window — which reads as the second visual having
  replaced the first rather than joined it. Two corrections, and both are
  needed: ask SDL again (and move its cache off the target first, because
  **SDL drops a position request matching what it already believes** — the
  same trap the wallpaper hold records further down), then go straight to
  `XMoveResizeWindow` on the **toplevel**, since whatever the window manager
  wrapped around the window is what the root's stack holds. `x11_align_frame`,
  which the wallpaper path uses, does *not* transfer here — it is written for
  a desktop-type window and measuring frame-against-client sent the window to
  x=29 instead of x=1100. Verify with `xwininfo -id` on the real window, and
  **do not read a residual offset in a rig as a bug**: under Xephyr the window
  manager clamps to a screen that genuinely ends at 1100, so it parks the
  window at 1071 (= 1100 − a title bar) every time. Asking for a position
  *inside* the screen the WM knows about lands exactly, which is what proves
  the code rather than the rig.

- **SDL does not see a fake second monitor, so a two-monitor feature cannot be
  driven that way.** Xephyr `+xinerama` with two `-screen` arguments gives
  XINERAMA two heads and `xrandr --setmonitor` adds virtual monitors, and
  `SDL_GetNumVideoDisplays` still answers **1** for both — SDL 2.32's X11
  backend enumerates real XRandR *outputs*. The menu is gated on
  `len(displays) < 2`, so "Send visual to" and "Fullscreen display" both
  vanish and the feature looks unbuilt. What works is wrapping
  `Renderer._refresh_displays` from outside — a six-line script that imports
  `viz_common`, appends a second entry to `self._displays`, and `runpy`s
  `main.py`. Nothing in the app is modified, the real menu and the real SDL
  window creation run, and the fake monitor is placed inside the rig's real
  screen so the window manager will not clamp it.

  **The same wrapper is the only option on Windows, where there is no fake
  head to try in the first place** -- no Xinerama, no `xrandr --setmonitor`,
  and adding a virtual display needs a signed driver. It drives the whole
  feature: the menu gains both multi-monitor submenus, `SDL_CreateWindow`
  lands the second window exactly where it was told (no reparenting, so the
  X11 correction in `_place_second_window` is rightly a no-op there), one GL
  context serves both windows, and teardown is clean. Point `FAKE_RECT` at
  the real monitor's own rectangle and it also exercises the full-screen
  case, which is how the foreground precondition above was settled.

- **A `QFormLayout` set straight on a `QDialog` makes the form's height the
  window's MINIMUM, and a window manager then refuses to shrink it.** Twenty-one
  rows of Settings came to about 860px, which is taller than a 1366x768 laptop
  screen -- the smallest the Microsoft Store itself accepts a screenshot at --
  so OK and Cancel were below the bottom edge with no way to reach them, and
  the preset folder a new install has to set was inside that dialog. Found on
  15 Sep 2026 by opening it under xfwm4 and dragging the bottom edge up: it
  would not move. The rows go in a `QScrollArea` and the buttons stay outside
  it. Two things that are not obvious once you do that:
  - **A `QScrollArea`'s own size hint ignores how big its widget wants to be.**
    Asking the dialog gave 628x476 for a form needing 810x838, so it opened
    narrower than the rows and raised a horizontal scrollbar it never needed.
    Size the window off `form.minimumSizeHint()`, not off `self.sizeHint()`.
  - **A scroll area only makes shrinking possible.** The window still opens at
    whatever it is told, so cap the opening height against
    `screen().availableGeometry()` as well.
  - **And an unactivated layout under-reports what it needs**, so sizing off
    it opens the window a few pixels short of its own rows and raises the very
    scroll bar the numbers were computed to avoid. Found on Windows
    15 Sep 2026: `form.minimumSizeHint()` answered 782 inside `__init__` and
    786 once the window existed, and Settings opened with a vertical scroll
    bar down every row whose whole range was **four pixels**. `layout.activate()`
    before measuring is the fix; `ensurePolished()` is not — measured, it
    changes nothing. The gap is font-sized, not constant (8px under the
    offscreen fallback), so what `test_settings_diff` pins is that the number
    the dialog read equals the number the layout settles on, which is the same
    assertion in any font.

- **`QT_QPA_PLATFORM=offscreen` honours any `resize()` you ask for**, so
  `resize(w, h); assert height() <= h` is not a probe for a minimum-size bug --
  it passes on the broken build. Compare `minimumSizeHint().height()` against
  `sizeHint().height()` instead: equal means the layout is the floor. Ratios
  travel, pixels do not -- the offscreen fallback font is nothing like the
  shipping one (see the browser toolbar note under Testing).

- **Qt indents a list row that carries an icon and draws one without it flush
  against the edge.** Most presets in a fresh library have no thumbnail, so
  once any row on screen had a picture the names stepped in and out by 96px
  down the whole list. Rows with no picture get a shared transparent pixmap of
  `ICON_SIZE`; one pixmap, not ten thousand. The row *height* half of this was
  fixed a week earlier and read as the whole problem.

- **A lazy loader driven by the scroll bar is not driven by a resize.**
  `_load_visible_icons` was wired to `valueChanged` and to a rebuild, so
  dragging the browser's bottom edge down revealed rows that never asked for
  their thumbnails at all. Anything that fills "the rows on screen" needs
  `resizeEvent` too.

- **SDL drops a position or size request that matches what it already
  believes, so a window the *window manager* moved cannot be put back through
  SDL.** `SDL_SetWindowPosition` compares against its own cached rectangle
  first. After `_fit_to_wallpaper` asks for (0, 0) SDL records (0, 0), and
  when the window manager then places the window at y=-32 SDL is never told —
  so the obvious correction, asking SDL for (0, 0) again, puts **nothing on
  the wire**. Measured 14 Sep 2026 on Muffin: the window sat at y=-32 through
  thirteen seconds of two-second correction attempts without moving.
  `_hold_wallpaper_box` goes straight to `XMoveResizeWindow` instead, and only
  falls back to `_fit_to_wallpaper` when the *size* changed and the GL viewport
  and projectM need telling too. The general rule: SDL is the right way to ask
  for a change, and the wrong way to undo somebody else's.

- **`SHQueryUserNotificationState` sees YOUR OWN full-screen window, so a
  wallpaper asking it "is another app full-screen?" pauses itself for ever.**
  Reported 16 Sep 2026 as *"displays a static box, can't interact any
  further"* and it is the worst kind of bug: the app is healthy, the tray menu
  opens, nothing is in the log, and it looks completely dead. In wallpaper
  mode the visualiser is by definition a full-screen OpenGL window covering
  the display, so the shell answers `QUNS_RUNNING_D3D_FULL_SCREEN` about *us*
  — measured, state **3** with the wallpaper up and state **5**
  (ACCEPTS_NOTIFICATIONS) with the app shut. `should_idle` pauses for a
  full-screen app **whatever the silence is**, so the visuals parked within
  one 2-second poll of the mode starting and no amount of music brought them
  back. The API cannot answer this question here and has been dropped: ask
  what is actually in front instead — `GetForegroundWindow`, its pid against
  ours, its class against the shell's, its rectangle against its monitor's.
  A wallpaper is a child of WorkerW and never takes focus, so the foreground
  window can never be the one that trips it.

  **And the QUNS constants were off by one, which is why it read as
  plausible.** The real values are `NOT_PRESENT` 1, `BUSY` 2,
  `RUNNING_D3D_FULL_SCREEN` 3, `PRESENTATION_MODE` 4; the code had BUSY at 1,
  so it was treating NOT_PRESENT (locked, screensaver, fast user switch) as
  busy and missing PRESENTATION_MODE entirely, while its "presentation mode"
  value of 3 happened to land on the real D3D-fullscreen one. Every constant
  in a ctypes shim is worth reading off the SDK header once.

- **A paused wallpaper shows whatever was on the buffer when the loop
  stopped, so the frame it parks on has to be one drawn since the last
  resize.** On Windows the visualiser is a layered child of WorkerW and DWM
  only recomposites it when it swaps — so with no swap, the desktop keeps the
  last frame for as long as the room is quiet. Entering wallpaper mode
  *resizes* the window, and parking on that same pass left the frame from the
  old windowed size sitting in a corner of the desktop: the "static box"
  above. `_check_idle` therefore returns False on the pass that goes idle —
  draw one more, then park — and `_set_wallpaper_mode` restarts the silence
  clock on the way in, so the mode gets its whole window rather than
  inheriting one that expired during startup on a machine nobody has played
  anything on.

- **Everything below the idle-pause `continue` in the render loop stops for as
  long as the desktop is quiet, which for a wallpaper is nearly always.** The
  pause is on twenty seconds of silence, not on input, so a wallpaper spends
  most of its life parked — and until 14 Sep 2026 both wallpaper holds sat
  under that `continue`. Measured: a window shoved off the bottom edge while
  parked stayed there indefinitely, and a lower that lost the race at map time
  left the visuals over the desktop icons until somebody played something.
  Anything that has to keep holding while the visuals are still belongs
  **above** the idle check, not below it.

- **A system program run from inside the frozen bundle loads the bundle's
  libraries, and for `gsettings` that is silent corruption rather than a
  crash.** PyInstaller points `LD_LIBRARY_PATH` at `_internal`, so
  `/usr/bin/gsettings` picks up the bundled `libgio-2.0.so.0`, which cannot
  load the system's dconf GIO module and **falls back to the keyfile
  backend**. Every call then succeeds against the wrong store: `get` returns
  the schema defaults, `set` returns 0, the value persists across runs in
  `~/.config/glib-2.0/settings/keyfile` — and nothing else on the desktop can
  see any of it. Measured 14 Sep 2026: the app logged that it had written
  Nemo's ignore list while Nemo went on reading a dconf entry that had never
  moved, and the giveaway was that a `gsettings get` in a normal shell and the
  same one under the bundle's `LD_LIBRARY_PATH` returned **different lists**.
  `child_env()` hands subprocesses `LD_LIBRARY_PATH_ORIG`, which PyInstaller
  saves for exactly this — the **same** helper the bundled-libstdc++ ffmpeg
  trap further down already needed, which is why there is one of it and not
  two. Use it for any system binary the app shells out to, whether or not that
  binary is known to link anything the bundle carries, and never trust a frozen
  build's word that a desktop setting was written — read it back from a shell
  that is not the app's.

- **Nemo gives up the desktop icons entirely when another window claims to be
  the desktop — and nothing about the stacking is wrong when it does.**
  Measured on Cinnamon 6.0.5, 14 Sep 2026. `nemo-desktop` checks for other
  `_NET_WM_WINDOW_TYPE_DESKTOP` windows **at startup**, and if it finds one it
  prints *"Desktop already managed by another application, skipping desktop
  setup"* and exits without drawing a single icon. So wallpaper mode reads as
  the visuals *covering* the icons while the desklets still show over the top:
  the desklets are drawn by the Cinnamon shell and never went away, and the
  icons were never drawn at all. `_NET_CLIENT_LIST_STACKING` says the
  visualiser is at index 0, below nemo-desktop, the whole time — so
  `x11_lower_to_bottom` and `_hold_wallpaper_bottom`, which are right about
  everything they measure, cannot see this and do not help. **Check whether
  `nemo-desktop` is running before believing a stacking bug.**

  The order is what decides it. Turning wallpaper mode on while Nemo is
  already up is fine, because Nemo only looks once. It is the sessions where
  Nemo starts *second* that lose the icons — a login with
  autostart-as-wallpaper, a Cinnamon restart, Nemo restarting for any reason.
  Nemo names the escape hatch in that same warning:
  `org.nemo.desktop ignored-desktop-handlers`, which ships holding `conky` and
  `csd-background`. `nemo_allow_desktop_window` puts the app on it on the way
  into wallpaper mode and leaves it there — taking it back off on the way out
  would lose the icons again at the next restart. **The list matches the
  WM_CLASS *instance*, not the class**: the stock entries are `conky` and
  `csd-background`, whose classes are `Conky` and `Csd-background`.

- **The first run opens a modal photosensitivity warning, and a piped run then
  prints absolutely nothing.** 2026-09-13. `App._warn_photosensitivity` is a
  `QMessageBox.warning` gated on `cfg["seizure_warning_ack"]`, and it runs
  before the render thread starts -- so the app sits there with fifteen live
  threads, an X connection, no SDL window and an empty log, which reads exactly
  like a hang somewhere deep in projectM. It is not. `faulthandler` settled it
  in one shot: wrap `main.py` in a script that calls
  `faulthandler.dump_traceback_later(18, exit=True)` and `exec`s it, and the
  dump names the line. (`py-spy` cannot help -- attaching to a process that is
  not its own child needs ptrace permission this box does not give.) To drive
  the app from a shell, set `seizure_warning_ack` in `config.json` first, and
  **take it back out afterwards**: it is a safety acknowledgement, and ticking
  it on someone's behalf is not ours to do.

- **A preset lock is a hold on the clock, not on the controls.** 2026-09-13,
  and this is the second time the obvious reading has been written into the
  code. Next, previous, random and a jump from the browser all still move while
  a preset is locked; the *only* thing lock stops is the auto-advance timer in
  `Renderer.run`. libprojectM agrees and needs no help: measured against 4.1.0
  with `projectm_set_preset_locked(true)`, `projectm_playlist_play_next`,
  `play_previous` and `set_position` each moved the playlist, and ten rendered
  frames later the new preset was still the one on screen -- so nothing has to
  be unlocked around an explicit jump. `test_lock.py` holds both directions,
  and pulls the auto-advance `if` out of `Renderer.run` with `ast` so that
  losing the guard fails the test rather than a copy of it.

- **A stylesheet-drawn scroll bar handle has no minimum size, and on ten
  thousand presets it comes out one pixel tall.** Reported from Mint on
  10 Sep 2026 as "there either isn't a vertical scrollbar adjuster, or the
  adjuster is the same colour as the bar". It was the first: styling
  `QScrollBar::handle` at all takes the sub-control away from the native
  style, and `PM_ScrollBarSliderMin` — the ~20px floor Qt would otherwise
  apply — goes with it. The handle is then sized purely by page/range, which
  at 9,795 rows in a 560px list is **1px**, and `border-radius: 4px` rounds
  that away to nothing. Both sheets now set `min-height` on the vertical
  handle and `min-width` on the horizontal one. Measure it with
  `QStyle.subControlRect(CC_ScrollBar, opt, SC_ScrollBarSlider)` on a real
  widget with a real row count — a sheet that *looks* complete tells you
  nothing, and twenty rows of test data hides it completely.
  The second half of the report was real too, just secondary: the handle
  composited to 2.0:1 against its own trough in both themes, under WCAG
  1.4.11's 3:1 for a control you have to see to use. `scroll_handle` is now
  alpha 85 (dark) and 109 (light), which is 3.0:1 in both.

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

  **Linux is the only platform exposed, and it is worth knowing why before
  anyone goes looking for the same hole on the other two.** sounddevice's own
  `except OSError` falls back to a PortAudio shipped inside the wheel --
  `_sounddevice_data/portaudio-binaries/libportaudio64bit.dll` on Windows, the
  matching `.dylib` on macOS -- and the branch for every other platform is a
  bare `raise`. The wheel carries a working library on Windows, so the import
  cannot fail there; on Linux it carries none, and the import is one
  `find_library` miss away from taking the process down.

  **Proven against the shipped artifact, and it was shipping broken.** The
  2.0.0 tarball built before the fix was extracted in a stock `ubuntu:24.04`
  container, which has no libportaudio2: `BomsAIVisualizer --help` died with
  `OSError: PortAudio library not found` out of `viz_common.py` line 67, before
  any window, on the download the site offers. The rebuild prints
  `[audio] sounddevice unavailable (...) -- the PortAudio capture backend is
  off` and carries on to exit 0. Any Linux package built from a commit before
  `add5384` is unshippable for that reason alone. The check is one container
  run and is worth repeating whenever an audio dependency moves -- extract the
  tarball on a clean image and run `--help`; add `libgl1 libglib2.0-0 libegl1`
  and `xvfb` to get past the graphics libraries a bare image also lacks.

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

- **`projectm_opengl_burn_texture`'s `left` and `top` do not work at the
  pinned commit, and the failure looks like a bug in our code.** CopyTexture's
  vertex shader multiplies the wrong way round --
  `gl_Position = vec4(position, 0.0, 1.0) * vertex_transformation` -- so with
  glm's column-major storage `left/m_width` and `top/m_height` land in
  `gl_Position.w` instead of the translation column. The result is a
  projective shear: measured 14 Sep 2026, asking for a 173x173 patch 560px
  left of centre drew a trapezoid across half the screen. `width` and
  `height` are on the diagonal and survive it. So the only call this API gets
  right is **(0, 0, whole window)**, and `burn_canvas` composites the picture
  into a window-sized RGBA buffer here instead. Two things measured rather
  than assumed while getting there: the surround's **alpha is honoured**,
  which is what stops the burn wiping the preset's feedback buffer everywhere
  it does not cover; and the texture is **upside down** relative to a QImage,
  because a GL texture's first row is the bottom of the picture.

- **An animated texture inside a preset IS possible, through
  `projectm_set_texture_load_event_callback`** -- which contradicts the
  obvious reading of the texture manager, and contradicts what was written
  here first. The callback fires when a preset asks for a texture the manager
  has not got, and it accepts **an existing GL texture id**, which projectM
  wraps *without taking ownership* (`TextureManager.cpp`, "App-provided
  textures are not owned by projectM") and caches. The preset then samples
  our texture id every frame, so anything we write into that texture with
  `glTexSubImage2D` is live inside the preset. It also sidesteps the
  hard-coded extension list in `TextureManager.hpp` entirely, since the
  filesystem is never consulted for a name the callback answers. Exported by
  both shipped binaries; no patch and no pin move. **Built and seen on screen
  14 Sep 2026**: a `.gif` sampled by a preset's own composite shader, a `.gif`
  as a sprite, and an `.mp4` as a sprite, each caught twice seconds apart
  showing different frames. Sprites come free -- `MilkdropSprite.cpp` resolves
  `img=` through the same `TextureManager::GetTexture`.

- **`projectm_opengl_render_frame` ignores whatever framebuffer is bound.**
  It is `RenderFrame(0)`: the final copy goes to framebuffer 0 however the
  caller left the binding. Dual-preset mode bound its own framebuffer and
  called it, so both visuals drew straight to the window and the blend pass
  then painted two empty textures over the top -- a black window, no GL
  error, and nothing in the log. `projectm_opengl_render_frame_fbo(handle,
  fbo)` is the one that takes a target. Found by reading our own call, not
  the driver.

- **glBlitFramebuffer cannot blend.** It ignores blending entirely, which is
  why two visuals over each other needs a shader of its own -- the only
  hand-written OpenGL in the application (`_Blender`). It is also one draw
  call rather than two, and leaves no blend state switched on behind it.
  projectM leaves its own program, framebuffer and blend state bound, so the
  blend pass sets all of that rather than assuming it.

- **The public header for that callback is wrong about ownership.**
  `callbacks.h` carries an `@warning` saying projectM takes ownership of a
  supplied texture id and will `glDeleteTextures` it. `TextureManager.cpp`
  passes `false` for that argument and comments "App-provided textures are not
  owned by projectM", and `Texture::~Texture` only deletes when `m_owned`. The
  code is what runs. Ours are therefore never given back while projectM is
  alive, which is also why there is a ceiling (`LIVE_MAX_TEXTURES`) instead of
  an eviction policy: a preset on screen still holds a `shared_ptr` to the
  wrapper around our id, so deleting one is a sampler reading a texture that
  is not there.

- **A dialog owned by the SDL window can hang the whole application, because
  that window's message pump is the render thread.** 2026-09-14, reported from
  a real install as "went to change preset duration, app froze, had to
  force-close". It is not preset duration and not the Settings dialog: it is
  *closing any owned window*. `keep_dialogs_above_visuals` sets
  `GWLP_HWNDPARENT` to the SDL window, and closing an owned window makes
  Windows transfer activation to the owner **synchronously, on the owner's
  thread**. The render thread only pumps between frames -- the rest of the
  time it is inside `projectm_opengl_render_frame`, which on an unlucky preset
  is a long time, and the driver call it is inside can in turn wait on the
  window state the Qt thread now holds. Circular wait, no log, no traceback.
  Caught with `faulthandler.dump_traceback_later(10, repeat=True)`: main
  thread in `QDialog.accept`, render thread in `render_frame`, **identical
  across two dumps ten seconds apart**. Confirmed the ownership is genuinely
  cross-thread by reading it off a live process -- SDL window on thread A,
  dialog on thread B, dialog's `GWLP_HWNDPARENT` = the SDL window.
  `_OwnedByVisuals` now clears the owner on `QEvent.Type.Close`, which is
  measured to arrive **while the native window is still visible** (the order
  on accept is ActivationChange, Close, WindowDeactivate, then Hide, and Hide
  is already too late). Setting an owner is safe; closing one is not.
  **It reproduced 3/3 by hand and 0/20 scripted**, because it needs the render
  thread to be inside a slow frame at the instant of the close -- so do not
  read a run that did not hang as evidence the hazard is gone.

  **A third sighting, 15 Sep 2026, and it is NOT settled.** Driving the source
  build on Windows, the app froze while opening Settings from the right-click
  menu with the preset browser already up: `Responding` false, CPU flat at
  477s across two samples, the render thread stopped advancing presets one
  change after the click, and no dialog ever appeared on screen. It fits this
  family -- showing a window owned by the SDL window moves activation, and
  Windows moves activation on the owner's thread, which is the render thread
  -- and the precondition would be *another* owned window already showing,
  which is why this was the first time it happened. But **it did not
  reproduce in 20 scripted rounds** of exactly that sequence (browser open,
  panel open, menu, Settings, theme combo, Cancel), and there is a second
  candidate that cannot be ruled out from one occurrence: the driving harness
  calls `AttachThreadInput`, which shares an input queue with the app and
  which Microsoft documents as able to hang the caller. So this is recorded as
  observed and unexplained, not as a known defect with a known fix -- and
  nothing was changed on the strength of it, because loosening the ownership
  at release time to chase an unreproduced freeze is how the dialogs end up
  behind the visuals again. **Next time it happens it has to be caught under
  faulthandler**, which is what settled the 2026-09-14 one: a six-line wrapper
  that calls `faulthandler.enable(file=log)` and
  `dump_traceback_later(10, repeat=True, exit=False, file=log)` and then
  `runpy.run_path("main.py", run_name="__main__")`. It costs nothing while the
  app is healthy and it names the line in every thread the moment it is not.
  Keep a copy in the session scratchpad; rebuild it from this sentence if the
  scratchpad has gone.

- **A minimised window is still `isVisible()`, so a show/hide toggle throws it
  away.** 2026-09-15, found by driving the shipped 2.1.3 build on Windows.
  Minimised is a window *state*, not a visibility, so `QWidget.isVisible()`
  answers True for a window sitting in the taskbar. `App._toggle_browser` read
  that as "showing" and called `hide()`; the window then left the taskbar
  **still carrying `WindowMinimized`**, and every later pick of the menu entry
  called `show()`, which restores a window to the state it still holds -- the
  minimised one. Measured: minimise the preset browser with its own titlebar
  button, pick "Preset browser" three more times, and the window reads
  `vis=False min=True` every time. The browser is gone for the rest of the
  session and the menu entry appears to do nothing. Not Windows-only -- the
  semantics are Qt's -- but only a real window manager can produce the state,
  so no offscreen suite could have seen it. The toggle now asks
  `isVisible() and not isMinimized()`, and un-minimises with `showNormal()`
  **only** on that branch, because `showNormal()` clears maximised as well and
  an ordinary hide/show must not un-maximise a window the user maximised.
  `test_browser_toggle.py` runs the shipped method against a fake with Qt's
  semantics, including the already-broken hidden-and-minimised state, which is
  what anyone who hit this under 2.1.3 is sitting in. **Any other
  show/hide toggle over a reused top-level window needs the same two lines.**

  **And that fix on its own hung the application, because un-minimising an
  owned window is as dangerous as closing one.** Same mechanism as the entry
  above: the browser's owner is the SDL window, whose pump is the render
  thread, and Windows hands activation back to the owner synchronously.
  Measured -- with the owner still set, `showNormal()` **never returned**: the
  Qt thread stopped inside it, the render thread stopped advancing presets,
  and the window was left at the iconic placeholder rectangle (`0,1004
  160x28`, a title bar and no content, still there seconds later). The same
  sequence against a probe whose owner is on the *calling* thread restores
  correctly every time, which is what puts the blame on the ownership rather
  than on Qt. `_OwnedByVisuals` now also watches `WindowStateChange` and hands
  the owner back the moment a window is minimised -- immediately, not
  deferred, for the same reason the `Close` branch is not deferred.

  **The disown alone is not enough either, and this is the part that looks
  like nothing is happening.** Clearing the owner of a freshly minimised
  window makes Windows show it again, so Qt delivers a **`Show` while
  `isMinimized()` is still true** -- and the `Show` branch owned it straight
  back, one line later, every time. The refusal therefore lives in
  `_set_owner`'s own guard (`or w.isMinimized()`) rather than at the call
  sites, so no future branch can reintroduce it. Read the ownership off the
  live process (`GetWindowLongPtrW(hwnd, GWLP_HWNDPARENT)`) before believing
  any of this; the sequence that settled it was one log line per own and
  disown, tagged with which branch asked.

- **A bare PyOpenGL call on a teardown path is a Windows-only crash.**
  PyOpenGL resolves an entry point on first use and raises
  `NullFunctionError` when it cannot -- and "cannot" includes having no
  current context, which Linux quietly no-ops through. Same split as the
  `glViewport` one further down, but this one was in shipped code rather than
  a test: `Renderer._stop_dual` ended with a bare `glBindFramebuffer`, and the
  route that reaches it is `_render_visuals` calling `_do_set_dual(False)`
  when the blend program will not build -- so the machine with no framebuffer
  entry points is exactly the machine that runs that line, and the exception
  kills the render thread with a black window and nothing in the log. Guarded
  with `if bool(glBindFramebuffer)`, which is the idiom PyOpenGL's own error
  message names. `_Blender.drop()` and `_LiveTexture.stop()` were checked at
  the same time and are fine -- the first wraps everything, the second touches
  no GL at all. **Any new GL call on a stop/destroy/fallback path needs one of
  those two shapes.**

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
- **Qt will not open a window narrower than the widest thing in it, so a
  `resize()` below a toolbar's minimum is a number that describes nothing.**
  The preset browser said `resize(470, 620)` with a comment claiming it had
  been measured against the buttons it carried. It had stopped being true some
  buttons ago: the row's own minimum was **522**, Qt was silently widening the
  window to fit, and every button added since had widened it a little more
  without anyone noticing, because the source still said 470 and nobody
  measures a window that opens. Read it with
  `layout().itemAt(1).minimumSize().width()` against a shown window, not from
  the source. `PresetPlaylistWindow.WIDTH` now carries the measured number and
  `test_browser_title` fails if a new button outgrows it.

  **And then the measured number was wrong too, because a row of labelled
  buttons is a *font* measurement and the suite does not run in the app's
  font.** 2026-09-14, on Windows. The same ten buttons need **602px on Linux,
  599 on Windows in Segoe UI, and 818 under `QT_QPA_PLATFORM=offscreen`** --
  the offscreen plugin falls back to "Sans Serif" and reports 818 for a window
  that opens at 612 on that machine in real life. So the check failed on
  Windows over a defect that was not there, and had only ever passed on Linux
  because that box's offscreen fallback happens to sit near its real font.
  A pixel threshold measured under offscreen is not the app's threshold: to
  get the real number, run the probe with `QT_QPA_PLATFORM` **unset** and
  confirm the font it prints is the system UI font. The window now opens at
  `max(WIDTH, bar.minimumSize().width())`, which is right in any font, and
  what the suite pins is the **button count** -- the thing that was actually
  being watched, and the same number everywhere.

- **Two `Stretch` columns split the leftover EVENLY, so widening a table for
  the long column feeds half of every pixel to the short one.** The Key
  bindings table had Action and Notes both stretching inside a window opened
  at a flat 560px: each got 179px against the 237 and 359 they ask for, and
  every note in the table was clipped mid-word — including the lock one, which
  is the only place the app says that next and previous still work while a
  preset is locked. Action is `ResizeToContents` now and the dialog is sized
  from `sizeHintForColumn`, which answers correctly *before* the window
  exists, so it can size rather than correct.

  **And the sheet costs the stretch column its own padding, twice.**
  `dialog_qss` styles `QAbstractItemView`, which a `QTableWidget` is, so
  `padding: 4px 6px` lands on the table: `frameWidth()` reads **7** where the
  style's own answer is 2, and the sections then come to `viewport - 14` —
  measured 726 against 712, with a bare table under the same sheet giving a
  gap of 0. That is why the width adds `4 * frameWidth()` and not two. The
  suite pins `columnWidth(i) >= sizeHintForColumn(i)`, which is font-free:
  359px in Segoe UI, 787 under the offscreen fallback, same assertion.

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

  5. **Getting to the bottom once is not enough.** 2026-09-13, reported from
     Mint: wallpaper mode switched on, no toast, and the visuals over the Nemo
     icons anyway. The toggle could not have known -- it measured the stack and
     was right at the time. A window manager restacks on its own schedule (a
     workspace switch, the icon manager restarting, another desktop-layer
     window mapping) and a compositing one paints from a stack of its own, so
     the lower at map time is an invariant that quietly stops holding.
     `_hold_wallpaper_bottom` re-runs the same measurement from the render loop
     every `WP_HOLD_EVERY` (2 s): one `XGetWindowProperty` off the root per
     pass, and nothing on the wire at all unless the answer is wrong. A window
     manager that publishes no client stack is left alone -- there is nothing to
     measure against, and lowering on a hunch every two seconds is how a window
     ends up fighting whatever is above it. A failure backs off 60 s rather
     than repeating for the life of the process.

  **ctypes signatures are spelled out in `_libx11` because both defaults are
  wrong.** A returned `Atom` through the default `c_int` restype loses its top
  half on 64-bit; a `Window` passed as a bare Python int goes out as `c_int`
  and addresses some other window. `_NET_WM_STATE` is format 32, which in Xlib
  means *an array of `long`* -- `c_ulong`, not `c_uint32`.

  **Wayland does host this, through XWayland -- the old blanket refusal was
  wrong.** 2026-09-13. The claim used to be that an X client under a Wayland
  compositor is an ordinary toplevel with no below-the-desktop layer to ask
  for. Measured on Ubuntu GNOME / mutter under Wayland and it is not true: a
  desktop-type XWayland window went straight to index 0 of
  `_NET_CLIENT_LIST_STACKING` and stayed there across other windows being
  mapped and unmapped over it, and the real app entered with `--wallpaper` and
  held index 0 for the whole run. Mutter classifies X11 clients by window type
  like any other window manager, and its desktop layer sits above the shell's
  own background actor -- which is exactly where a wallpaper goes. Refusing
  every Wayland session turned the feature off on the most common Linux desktop
  there is, on the author's own machine.

  `wallpaper_support()` is still the one gate for the menu entry, the key, the
  toast and the settings checkbox, but the only thing it refuses on Linux now
  is **no `DISPLAY` at all** -- no Xorg and no XWayland means there is no
  window to set a type on. A compositor with no desktop layer is deliberately
  *not* guessed at: `x11_lower_to_bottom` measures where the window actually
  landed and toasts when it did not go under, which does not go stale the way a
  list of compositor names would.

  **The menu entry has a second gate: a system tray.** A desktop window gets no
  keyboard and no mouse -- whatever draws the icons takes both -- so the tray
  icon is the only way back out. `QSystemTrayIcon.isSystemTrayAvailable()` is
  false on a GNOME session with no AppIndicator extension, and offering the
  mode there is a one-way trip ending in `kill`. Windows always has one.

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

- **A new action's default key can be dead on arrival, because a saved
  keymaps.json shadows it and the handler is one elif chain.** `snapshot`
  arrived on "c" in 2.1; this repo's own keymaps.json had already given "c" to
  hard cuts, and `load_keymaps_from` merges the file *over* the defaults — so
  both actions held "c", the earlier branch won, and the snapshot key looked
  broken while the Key bindings dialog went on showing it as bound. Found by
  pressing "c" in the rig and reading `[renderer] Hard cuts: OFF` in the log.
  The loader now drops a default the file has already spent (saying so on
  stdout) and `KeymapDialog` clears the other row when a key is typed into
  one. `test_keymaps` covers both, plus the invariant that no two entries in
  DEFAULT_KEYMAPS share a key in the first place. Note the two are *separate*
  files: a source checkout reads `keymaps.json` beside the code, an installed
  build reads the one under `~/.config/BomsAIVisualizer` — they drift, and the
  repo's is the older of the two. **Re-checked 15 Sep 2026 against the user's
  real `~/.config/BomsAIVisualizer/keymaps.json`**, which gives "c" to hard
  cuts and has no `snapshot` entry at all: the loader prints `[config] snapshot
  has no key: c is already toggle_hard_cuts in keymaps.json` and hands back
  `snapshot: ""`. Working as intended. What is *not* reconciled is a file that
  names the same key twice **explicitly** — both rows then show it, and the
  later one is dead. That is hand-edit only (typing into the dialog clears the
  other row), so it is left alone deliberately: blanking one would need the
  elif chain's order, which is not the table's order, and guessing wrong is
  worse than showing both. A fixture with both keys written out is how this was
  briefly mistaken for a live bug.
- **Two switches that are each right on their own can be wrong together, and
  a suite with a test for each will not see it.** 2.1.7 shipped a crossfader
  and "give visual 2 a monitor of its own", with a check for each: switching
  visual 1 off pins the fader to visual 2, and a screen each draws both
  whatever the fader says. The *pair* was not checked, and the pair is where
  the bug was -- a screen each makes the main window draw `crossfade 0.0`,
  which is "visual 1 alone", and it did that whether or not visual 1's own
  switch was on. So visual 1 sat on screen at full strength with the tick
  beside it saying off. There is nothing to blend in that state: the window
  is cleared instead, which is also the honest thing for the recorder and the
  thumbnail to read out of it.

  The same session found the other half of the same shape: `visual2_display`
  is a setting, `_stop_dual` drops the window without clearing it, and
  **nothing read it back** -- so the menu, which ticks the monitor straight
  out of config, claimed visual 2 was on a screen it was not on after any
  switch off and on, and on every launch after the feature had been used
  once. `_do_set_dual` queues `_req_win2` from config when it brings the
  second visual up, which covers both routes in one line because startup is
  just another caller. **When a feature adds a second independent switch, the
  test to write is the combination, not the switch.**

- **Wallpaper mode and a visual on another monitor do not compose.** Entering
  wallpaper mode with the pair split sends the *main* window to the desktop
  layer and leaves visual 2 as an ordinary window floating over everything on
  its own monitor, taskbar button and all -- half the pair behind the icons
  and half in front of the work. Measured on Windows 16 Sep 2026. It is
  recoverable rather than a trap: the tray menu is complete in that state, so
  "Leave wallpaper mode" and "Send visual to -> Same window" both work, and
  the round trip puts both windows back exactly where they were. Left alone
  deliberately -- the two ways out are giving the second window a desktop
  host of its own on that monitor, or folding it back into the wallpaper with
  a toast saying so, and that is a decision about what the feature *is*.

- **The second window is letterboxed to the MAIN window's aspect, not to its
  own monitor's.** Both visuals draw into targets built at the main window's
  drawable size, so a 900x600 window sends a 3:2 picture to a 16:9 monitor and
  gets 150px bars down each side -- measured, and they vanish the moment the
  main window is made 16:9. Deliberate (a stretched visual reads as a broken
  one) and invisible in the ordinary case, where the main window is fullscreen
  on a monitor the same shape as the other. Worth knowing before reading the
  bars as a bug.

- **One frame that arrives with no drawable size used to end dual mode for the
  session.** `_render_visuals` fell through to `_do_set_dual(False)` whenever
  `_blend.ready(w, h)` was false, and `ready` answers false for `w <= 0` as
  well as for a blender that failed — so a window mid-way through a fullscreen
  change, or minimised, took the second visual down permanently and printed
  only `[dual] off`, which reads exactly like the user having turned it off.
  Seen in the rig on 14 Sep 2026 after a fullscreen toggle while a clip was
  recording, and it is racy: the next three attempts did not reproduce it. The
  sizeless frame is now skipped. **The distinguishing evidence is the missing
  reason line** — a real failure prints `[dual] <why>` first, so `[dual] off`
  on its own was never the user.

See PORTING.md for port status and the Windows setup recipe.
