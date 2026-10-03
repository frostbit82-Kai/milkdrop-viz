# Windows port

Goal: ship this on Steam as a paid MilkDrop visualizer. Linux stays working
throughout — every change so far is behaviour-neutral there.

## Why this product

projectM, the free competitor (96% of 859 Steam reviews), **fails to load MilkDrop 2
textures**, so the best-looking presets fall back to plain waveforms, and its
end-user frontends are "outdated or unavailable". This app's texture handling,
system-wide audio capture and preset rating/culling are exactly those gaps.

Wallpaper Engine — two people, ~$4, 20-50M owners — is the proof the category pays.
Overture ($3.99, 45 reviews in seven years, no preset support) is the proof that a
shallow entry dies. The preset and texture story has to be the pitch.

## Status

Done, on Linux, verified:

- [x] Git repo, assets and venv ignored
- [x] `texture_dir` setting with a Browse button on both directory rows; the
      configured directory leads projectM's texture search path
- [x] No bundled assets, no personal paths in `DEFAULT_CONFIG`
- [x] Linux-only env vars gated on `sys.platform`
- [x] `PYSDL2_DLL_PATH` ordering bug fixed (it was set after `sdl2` was imported)
- [x] Dead X11 window mover (`_xdotool_move`, `_target_x`) deleted
- [x] Embedded-host handle emits an HWND on Windows instead of reading the X11 union
- [x] `requirements.txt`

Done, on Windows, verified:

- [x] Toolchain: VS 2022 Build Tools 17.14.39 (MSVC 14.44, Windows SDK 10.0.26100),
      vcpkg, Python 3.14.7 x64
- [x] **libprojectM built with `ENABLE_PLAYLIST=ON`** — `projectM-4.dll` and
      `projectM-4-playlist.dll`, Release, x64, dynamic CRT. First built at
      4.1.0; the DLLs under `installer/vendor/windows/` are **4.2.0**, at the
      commit in `installer/PROJECTM_COMMIT`, and export the three calls the
      newer work needs (`projectm_load_preset_data`,
      `projectm_set_preset_switch_failed_event_callback`,
      `projectm_opengl_burn_texture`). Do not rebuild 4.1.0 over them.
- [x] Both DLLs load under `ctypes` and all 38 `projectm_*` symbols the app binds
      resolve; the app's own candidate-name lookup finds both
- [x] `setup_windows.ps1` — the Windows counterpart to `setup.sh`
- [x] Playlist DLL name fix (see Traps in `CLAUDE.md`)
- [x] Repo lives at `C:\Projects\milkdrop-viz`; venv on Python 3.14.7 — every pin
      in `requirements.txt` has a cp314 wheel, so no fall back to 3.13 was needed
- [x] Two ungated Linux env vars removed from `viz_common.py` (`SDL_VIDEODRIVER`,
      `PYSDL2_DLL_PATH`) — they broke `import sdl2` on Windows outright
- [x] **Audio capture: WASAPI loopback**, via `PyAudioWPatch`. `AudioCapture` is now
      a backend interface (`parec` / `sounddevice` / `wasapi-loopback`); capture
      verified against a generated tone — captured peak 0.2500 and RMS 0.1767 against
      an emitted amplitude of 0.25, i.e. the actual signal, not a noise floor

- [x] **Runs end to end on Windows.** 11,223 presets loaded, WASAPI loopback
      capturing, presets rendering and switching with textures resolving — not the
      white that a missing texture path gives. Four Windows-only bugs had to be
      fixed to get there; all four are commits on this branch
- [x] **Texture fidelity confirmed.** Tested against 8 presets each sampling a
      different real texture file (the library references 63 across 11,223 presets).
      Texture sprites render correctly; pure-white coverage was 0.02–0.27%, i.e. no
      white-out
- [x] **Beat response confirmed.** Same preset, audio off vs real music: 2.53x more
      hot pixels and brightness variance rising 14.3 → 31.2, i.e. the image pulsing.
      Aggregate inter-frame motion does *not* show this — both states animate hard —
      so measure energy and its variance, not frame difference
- [x] Converged the two `viz_common.py` copies (they are byte-identical; see
      `CLAUDE.md` for what keeps them working), guarded by `check_sync.py`

Done, packaging:

- [x] **Linux release package.** `installer/build_linux.sh` produces an 88 MB
      `BomsAIVisualizer-<version>-linux-x86_64.tar.gz`; the user extracts it and
      runs `./install.sh`, which installs to `~/.local` with no root, adds a menu
      entry and a `bomsai-viz` command, and smoke-tests the app before saying it
      worked. Verified end to end: installed copy loads its own bundled
      libprojectM, 11,223 presets, parec bound to the sink monitor, presets
      rendering and switching. `uninstall.sh` reverses it and keeps settings
      and ratings
- [x] libprojectM vendored at `installer/vendor/linux/` rather than rebuilt, with
      projectM's LGPL-2.1 text alongside it in `installer/licenses/`
- [x] **Old-system build**: `installer/build_linux_docker.sh`, glibc floor 2.43 -> 2.34
- [x] **Double-click install**: the archive ships Install / Uninstall
      `.desktop` launchers beside the scripts, so Linux is as close to the
      Windows setup.exe as a tarball gets. Verified from the built tarball
      through `GDesktopAppInfo` — install, then uninstall, then reinstall
- [x] **Desktop wallpaper mode**, so the feature list is the same on both
      platforms. EWMH rather than Explorer's WorkerW:
      `_NET_WM_WINDOW_TYPE_DESKTOP` plus BELOW/SKIP_TASKBAR/SKIP_PAGER/STICKY,
      set while the window is unmapped and followed by `XLowerWindow` so the
      desktop icons stay above it. **X11 only** -- Wayland has no
      below-the-desktop layer to ask for, XWayland included, so
      `wallpaper_support()` greys the entry out and names Xorg in the reason
      rather than failing silently. Properties and the round trip verified
      against a real X server under Xvfb. **Stacking under real desktop icons
      is still unverified** -- it needs a window manager and an icon manager,
      neither of which is installed on the dev box; the check is queued for
      Mint (Cinnamon on X11)
- [x] **Status toasts actually appear.** `status_message` was connected to an
      empty method, so every "Mode: Random" / "Hard cuts: ON" / "Sorted by
      rating" was dropped on the floor. Verified with xdotool driving the frozen
      build: mode and hard-cut toasts now render. Locking a preset toasts too
- [x] **Audio source picker and input sensitivity**, both ported from Bomsai
      Player. A new "Audio source" submenu on the right-click menu switches
      capture device live and carries a sensitivity slider (0.1x-8x, soft
      limited with tanh); the settings dialog gets the same slider. The device
      list now comes from `pactl` rather than sounddevice, which also fixes a
      silent bug: sounddevice reports ALSA device names, parec cannot match
      them, and it fell back to the default monitor -- so picking a device
      appeared to work and did nothing. Verified live under Xvfb: sensitivity
      previews, persists and logs; picking the analog input actually reopened
      parec on it
- [x] **Toasts appear over the visualiser window**, top-right, instead of the
      top-right of the screen. The toast is its own top-level window (the
      visuals are an SDL surface with no Qt widget above them), so the renderer
      caches its window rect on every SDL window event and the toast asks for
      it. Fullscreen still lands in the screen corner, because that is then the
      window. Verified: window at 100,80 700x500 put the toast at 630,100;
      moving the window to 250,200 moved it to 794,220
- [x] **Borderless has a key binding** (`b` by default) and appears in the key
      bindings dialog like every other action. `load_keymaps_from` merges over
      DEFAULT_KEYMAPS, so an existing keymaps.json picks it up
- [x] **First run says something.** With no presets loaded -- a fresh install,
      or a folder that has moved -- the renderer emits `no_presets` and the app
      opens Settings with a toast, once per launch, instead of showing a black
      window and a warning on stdout that nobody launching from a menu entry
      will read. Changing the preset or texture directory now takes effect
      immediately: the texture search paths are re-applied and the new folder
      is loaded the way startup does
- [x] **Qt binding swapped to PySide6 (LGPL).** PyQt6 is GPL-or-commercial,
      which a paid closed binary cannot ship. `viz_common` now selects the
      binding at import and falls back to PyQt6 for Bomsai Player; the
      installer spec excludes PyQt6 outright. Verified: source and frozen runs
      on PySide6, the player's 88 tests still pass on PyQt6, and the package
      contains no PyQt6. Qt and PySide6 ship as separate shared libraries with
      their LGPL-3.0 text in `licenses/`. Package went 74 MB -> 82 MB.
      **Windows: re-run `pip install -r requirements.txt`** -- the pins changed
- [x] **Dark dialogs and an About box.** Settings, key bindings and the
      confirm boxes share `DIALOG_QSS` instead of the system theme, which was
      a light flashbang beside a black visualiser. The right-click menu gains
      "About…": BomsAI logo, created by Adam Bom, a link to
      bomsaisoftware.com, the version, the projectM LGPL-2.1 notice, and the
      reminder that presets and textures are other people's work
- [~] **Context menu dismissal** — reported not to close when clicking the
      visualiser behind it; **could not be reproduced here**, windowed or
      fullscreen, source or frozen, with xdotool clicking genuinely outside the
      menu rectangle. Qt's popup grab does cover SDL's window on X11. Added a
      fallback anyway: while the host says its menu is open, the render loop
      forwards any key or click it *does* see as a close request, which also
      stops an ESCAPE meant for the menu quitting the app. If it still happens,
      the next thing to learn is whether the session is X11 or Wayland

Done, Windows packaging:

- [x] **Windows installer.** `installer/build_windows.ps1` freezes the app with
      the same cross-platform spec and hands it to Inno Setup 6, producing a
      43.7 MB `BomsAIVisualizer-<version>-win64-setup.exe` plus the portable
      tree. Installed to `C:\Program Files\BomsAI Visualizer`, Start Menu
      entry, optional desktop icon, uninstaller; settings and ratings live in
      `%APPDATA%\BomsAIVisualizer` and survive uninstall. Verified end to end:
      silent install, launch from the installed path, bundled projectM loaded,
      11,223 presets, WASAPI loopback, presets rendering and switching, and the
      About dialog showing its logo and the `VERSION` file out of `sys._MEIPASS`
- [x] The release build is **windowed** (no console box behind the visualiser)
      and therefore silent. `MILKDROP_CONSOLE=1` builds a console version, which
      is the only way to debug a frozen failure
- [x] libprojectM vendored at `installer/vendor/windows/` — the same **two** DLLs
      `setup_windows.ps1` gathers. `glew32.dll` was the third until the pinned
      commit stopped linking GLEW; see the GLEW trap in CLAUDE.md
- [x] App icon generated from the BomsAI logo (`installer/windows/*.ico`)

Done, Windows parity with Linux — every feature added after the port branch,
driven on Windows and confirmed:

- [x] PySide6 (LGPL) is the binding in use; PyQt6 uninstalled from the venv and
      absent from the package
- [x] Settings, Key bindings and About dialogs render dark, with the spin-box
      and combo arrows resolving through the backslash-normalised QSS paths.
      The audio row reads "Output to capture:" and lists WASAPI loopback
      endpoints
- [x] Context menu, Audio source submenu (live device switch verified — capture
      reopened on the new endpoint), sensitivity slider, Preset browser,
      Borderless, About
- [x] Status toasts render inside the visualiser window, top-right
- [x] Keyboard and mouse bindings reach the SDL window
- [x] **Theme setting: dark, light, or follow the system.** One `theme` value
      in config.json moves both halves of the UI — the Qt surface (settings,
      key bindings, About, right-click menu, preset browser) through the
      palette-driven sheets in `viz_common`, and the frame Windows draws
      around the visualiser through DWM. Verified live on Windows: switching
      to Light with the desktop in dark mode turns the dialogs, the menu and
      the title bar light, and switching back turns them dark without a
      restart. `test_theme.py` covers the resolution logic. On Linux the Qt
      half works the same; the frame belongs to the window manager there and
      is left alone
- [x] **Shuffle reorders the playlist instead of randomising `next`.**
      projectM's own shuffle picks a random item on every advance, so prev
      never returned to what just played. The playlist is permuted instead and
      the native shuffle stays off. Driven on Windows against all 11,223
      presets: five forward then five back returns to the preset it started
      on and the names mirror exactly, no preset repeats going forward, and
      turning shuffle off keeps the order — walking the same stretch again
      yields the identical sequence. `test_shuffle.py` covers the toggle

**Three Windows-only bugs were found and fixed doing this**, all in code that
was correct on Linux; see Traps in `CLAUDE.md`:

- Enumerating audio devices terminated the PortAudio instance the capture
  stream was running on. Opening Settings killed the app — and the no-presets
  first run opens Settings, so a fresh install died within seconds.
- `config.json` was read with the locale encoding, so a BOM — which every
  Windows editor writes — made it unreadable; and the copy `_load_libraries`
  reads on the render thread was unguarded, killing the thread outright.
- Frozen stdout was block-buffered, so every crash log stopped several seconds
  before the fault and pointed at the wrong subsystem.

`setQuitOnLastWindowClosed(False)` was briefly counted as a fourth. It was
already in `main.py` and always had been; the symptom blamed on it came from
driving the menu with `SetCursorPos` and no mouse-move event, so Qt placed the
click outside the popup.

Not started:
- [ ] Code signing (an unsigned installer gets a SmartScreen warning)
- [ ] Steam page

### 1.7.0 — the Windows pass, done

Every one of the seventeen suites passes on Windows, twice in a row (see the
sprites entry below for why twice). The five items the Linux session left here:

- [x] **Idle pause, full-screen half.** `SHQueryUserNotificationState` answers 5
      (accepts notifications) on an idle desktop and 3 while a full-screen
      OpenGL window is up, and `fullscreen_app_running()` flips both ways with
      it — measured with a real full-screen window in a child process, sampled
      16 times across 8 seconds. No game needed: any exclusive-full-screen
      window trips it.
- [x] **Start at login.** Written, read back through `winreg` directly rather
      than through the app's own reader, and removed again; removing one that is
      not there is silent, which is the path every OK takes. The command line is
      parsed with `CommandLineToArgvW` — Windows' own splitter, not `shlex` —
      for both the source and the frozen forms, because the frozen one lives
      under `C:\Program Files\...` and the quoting is the only thing that can
      go wrong there. Not done: an actual log out and back in.
- [x] **Sound in recorded clips with the bundled ffmpeg.** `test_recording` and
      `test_clip_audio` both pass with `installer\vendor\windows\ffmpeg.exe`
      first on PATH, and the suite asserts the muxed stream's codec is `aac`.
      That build has native `aac` and `h264_mf` and is configured
      `--disable-libx264 --disable-libx265 --disable-libfdk-aac`, so the licence
      invariant holds.
- [x] **libprojectM rebuilt at the pin**, vendored, and checked with
      `verify_dlls.ps1`: all 47 bound symbols present, sprite API included.
      Read the GLEW trap in CLAUDE.md before touching this — the rebuild took
      `glew32.dll` out of the picture, and the app would not start until the
      `glewInit()` call went with it.
- [x] **The picture overlay.** Driven live against the new DLL: `can_sprites` is
      True, `add_sprite_image` copies the file in under a prefixed stem,
      `sprite_code` is accepted by `projectm_sprite_create`, and the count is
      still 1 after 90 rendered frames.

Two Windows-only defects found and fixed in the process:

- `test_sprites` and `test_backup` redirected only `XDG_DATA_HOME`, so on
  Windows they wrote into the real `%LOCALAPPDATA%\BomsAIVisualizer`. Both now
  set `LOCALAPPDATA` as well — the same trap `test_thumbnails` was fixed for.
  It hides on a first run: `test_sprites` passed once and then failed on the
  names it had itself left behind.
- `verify_dlls.ps1` only matched a single-capital ctypes receiver (`C.`, `P.`),
  so `projectm_set_log_callback` and `projectm_set_log_level` — bound through
  `core.` — were never checked. Exactly the new-on-master symbols it exists to
  catch.

Also new and cross-platform, so Windows only needed a look: named collections
(right-click → Collections), backup/restore of ratings and collections, an audio
level meter in the audio submenu, `x` to exclude the visual on screen, and the
Settings row "Start each visual on a black frame". libprojectM's own warnings
and errors now reach stdout prefixed `[projectm:...]` — on Windows that needs
`MILKDROP_CONSOLE=1` to be visible at all. A 45-second live run on the new DLL
loaded all 11,223 presets and cycled them with no `[projectm:...]` line at all.

## Packaging

`installer/README.md` covers the build. The parts that cost time:

**The glibc of the build machine becomes the floor.** Measured, not guessed:
a package built on this box needs **glibc 2.43** — Ubuntu 26.04 and nothing
else. `installer/build_linux_docker.sh` builds the whole thing (libprojectM
included) inside AlmaLinux 9 instead and takes the floor to **2.34**: Ubuntu
22.04+, Mint 21+, Debian 12+, RHEL 9+, Fedora 35+, current Arch and SteamOS.
Use it for anything released. **2.34 is PySide6's floor** — its wheels are
tagged `manylinux_2_34` from 6.10 on — so going lower costs two Qt minor
versions rather than a different base image. `installer/README.md` has the
three container details that each cost a measured failure. For Steam the
eventual target is the Steam Runtime 3 "sniper" SDK.

**libprojectM is pinned to a commit, in `installer/PROJECTM_COMMIT`.** Not a
tag: the sprite, texture-load-callback and log-callback API exists only on
master, which declares 4.2.0 with no release behind it. The Linux and Windows
scripts both read that file and stop if their source tree is somewhere else,
which is the fix for a trap that cost a build: several projectM checkouts live
on this machine at different commits, and picking the first one that merely
exists produced a silent 4.1.0 build. Moving off 4.1.0 also picks up the whole
4.1.x line of rendering fixes — inverted Y and wrong angle into per-pixel
expressions, warp texel drift, a null dereference in the texture sampler,
sampler keywords misparsed inside shader comments, several HLSL transpiler
gaps — which change how existing presets look, with no app change at all.

**Three things move when `sys.frozen` is set**: the config directory, the
projectM search path, and `PYSDL2_DLL_PATH`. All three are handled and all three
are listed in `CLAUDE.md`; the one to remember is that `sys._MEIPASS` and
`dirname(sys.executable)` are *different directories* for a onedir build.

**Windows needs one extra file in `PROJECTM_LIB_DIR`**: `glew32.dll`, beside
`projectM-4.dll` and `projectM-4-playlist.dll`. The spec already globs for it.
`setup_windows.ps1` gathers all three into `C:\dev\projectm-deploy`.

## Building libprojectM on Windows

`setup_windows.ps1` does all of this. What follows is why it does it that way.

**Dependencies are almost all vendored.** `glm`, `SOIL2`, `hlslparser` and
`projectm-eval` ship inside the source tree; bison and flex are optional because
the generated parsers are committed. On Windows the only `find_package(... REQUIRED)`
the Windows SDK does not already satisfy is **GLEW**. That is the single thing vcpkg
supplies.

**The manifest is trimmed.** projectM's `vcpkg.json` lists `glew`, `gtest` and `sdl2`,
because upstream CI builds the test suite and the SDL developer UI. We build neither
(`BUILD_TESTING=OFF`, `ENABLE_SDL_UI=OFF`), so the script rewrites the manifest to
`glew` alone and keeps the original as `vcpkg.json.upstream`. sdl2 in particular drags
in an msys2 runtime download for no benefit here.

**The build matches upstream CI otherwise** — `Visual Studio 17 2022` / `x64`, the vcpkg
toolchain file, triplet `x64-windows`, `BUILD_SHARED_LIBS=ON`, and the dynamic
(`MD`) MSVC runtime.

**Three DLLs have to travel together**: `projectM-4.dll`, `projectM-4-playlist.dll` and
`glew32.dll`, which the core links. Everything else they need is the VC++ redistributable
and the OS's own `OPENGL32.dll` and Universal CRT. `setup_windows.ps1` gathers the three
into `C:\dev\projectm-deploy`; put that on PATH or copy them beside the app.

**If downloads fail with "Could not resolve hostname", it is IPv6, not the network.**
See the trap in `CLAUDE.md`. The script already works around it.

## Next

### How old a Windows can this run on — settled 2026-09-08

**Windows 10 version 1607 (build 14393), x64.** The store page's "Windows 10 /
11 (x64)" is accurate. The floor is set entirely by the dependencies; nothing
this app calls itself needs more than Vista:

| what | oldest Windows | evidence |
|---|---|---|
| `Qt6Core.dll` | **10 1607** | imports `SetThreadDescription`, added in 1607 |
| `python314.dll` | 8 | imports `PathCchCombineEx` from `api-ms-win-core-path` |
| Python 3.14 itself | 10 | python.org dropped 8.1 at 3.13 |
| SDL2 2.32, projectM 4 | Vista or older | no gating imports |
| our own Win32 calls | Vista | see below |

Both of those are **load-time** imports, so an older machine does not get a
message — the process dies with an entry-point error naming a DLL.

Our own Windows API use is `EnumWindows`, `FindWindowW`/`FindWindowExW`,
`SendMessageTimeoutW`, `SetParent`, `SetWindowPos`, `InvalidateRect`,
`GetWindowLongPtrW`/`SetWindowLongPtrW`, `SetLayeredWindowAttributes`,
`SHQueryUserNotificationState` (Vista) and `winreg` under HKCU — all Vista or
older. The one modern call is DWM's immersive-dark-mode attribute, and
`apply_window_theme` already tries 20 then 19 and checks the return, so an
older Windows just gets a light title bar. Wallpaper mode picks its desktop
host by probing rather than by version check, so it needs no floor of its own.

`bomsai-visualizer.iss` now says `MinVersion=10.0.14393`. Without it Inno's own
default is Windows 7 SP1: the install completed on a machine the app cannot
start on, and the user saw a DLL entry-point error instead of a refusal.

**Going lower is not worth it.** Windows 8.1 would need Python 3.12 or older
*and* Qt 5 — PySide2, since PyQt5 is GPL and cannot ship here — and PySide2 has
no wheels for a current Python and is end-of-life. Windows 7 would need Python
3.9. Either is a rewrite of the Qt layer for an out-of-support platform.

### Fullscreen and wallpaper mode: a defect that was not there — 2026-09-08

Driving the app for screenshots, both modes *appeared* to leave the picture at
the previous window size in the top-left corner of an otherwise black screen.
That was written up here as a defect. **It was the measuring instrument, not
the app.** Recorded so nobody spends the afternoon on it again.

Instrumenting the running renderer shows every value correct the moment the
resize lands, and still correct at draw time a second later:

    just after RESIZED 1920x1080:
      viewport=0,0,1920x1080  pm=1920x1080  sdlwin=1920x1080  drawable=1920x1080

Wallpaper mode likewise — `_wallpaper_box()` returns `(0, 0, 1920, 1080)`,
`SetWindowPos` returns 1 with `GetLastError` 0, and `GetWindowRect` on the
HWND straight afterwards answers `0,0 1920x1080`.

Two independent probes agree: `SDL_SetWindowFullscreen` does fire
`SDL_WINDOWEVENT_RESIZED` with the new size, and projectM resizes correctly at
the pinned commit — create at 640x480, `projectm_set_window_size(1280, 720)`,
and the render fills the whole 1280x720 readback.

**What lied.** GDI capture (`Graphics.CopyFromScreen`) cannot see either mode:

- A window that covers the screen exactly — SDL's `FULLSCREEN_DESKTOP`, or a
  borderless window sized to the display — trips DWM's fullscreen
  optimisation. The capture comes back black, or shows the *last composited
  frame from before the transition*, which is what "old size in the corner"
  actually was.
- A layered child of WorkerW has the same problem for the reason already in
  CLAUDE.md: DWM composites that surface on its own schedule and a reader gets
  a stale one.

**So verify these two modes by eye, or with a recorded clip — never with a
screen grab.** And when a clip does come back black, check the preset first:
sampling ten at random off this library gave nine with a mean brightness under
26/255 and one at 95, so an all-black five seconds is unremarkable.

Still genuinely unconfirmed either way: whether fullscreen looks right to a
person sitting in front of it. Every measurement says it should. Worth one
glance before it is called done.

**Audio — done, but only the capture half is proven.** `AudioCapture` keeps its
public API (`start`/`stop`/`is_alive`/`get_chunk`/`update_device`) and delegates to a
backend chosen per platform: `parec` then `sounddevice` on Linux, `wasapi-loopback`
then `sounddevice` on Windows. Backends emit `(frames, 2)` float32 and `AudioCapture`
owns the ring buffer, so nothing above the capture layer changed.

The device picker changes meaning on Windows and the UI follows it: PulseAudio exposes
a monitor **source** per sink, WASAPI has no such object and loopback attaches to a
**render** endpoint instead. So the settings dialog asks the capture layer
(`AudioCapture.list_devices()`) rather than sounddevice, and the row is captioned
"Output to capture:" on Windows and "Audio device:" on Linux
(`AudioCapture.device_label()`).

Loopback runs at the endpoint's native rate (48 kHz here) rather than resampling to the
44.1 kHz the Linux path requests. projectM is fed PCM either way; if beat detection ever
looks off on Windows, that is the first thing to check.

Audio through the render loop is proven — see "Beat response confirmed" above.

**It runs.** `main.py` launches, projectM renders, presets switch and system audio
is captured. Getting there took four fixes that Linux never needed, each of which
failed in a way that pointed somewhere unhelpful:

- libprojectM links GLEW on Windows but never calls `glewInit()` — upstream only
  does it in the SDL developer UI we do not build. Every GL pointer was NULL, so
  `projectm_create()` wrote to address 0. The host has to do it, and it only works
  because we build against the *shared* glew32.dll.
- `projectm_playlist_create` takes the projectM instance; it was declared and
  called with no arguments. Benign on the SysV ABI, fatal on Windows x64.
- cp1252 console encoding turned every `★`/`←` into a thread-killing exception,
  once disguised as a failed audio backend.
- Two SDL hints naming X11 were set unconditionally.

To run it: put `C:\dev\projectm-deploy` on PATH and launch with the venv python.
`--geometry WxH+X+Y` keeps it windowed.

**Presets and textures now live on the native drive**, at
`C:\Projects\milkdrop-assets\{projectm-presets,textures}` -- 11,223 presets and
8,408 textures. They are gitignored and never ship; the app is pointed at them
through Settings.

### Handing over to Windows — 2026-09-09

Linux is finished for 2.0's section A and for C1. Windows now has to match it.
What follows is the whole of what changed since Windows was last opened, and
what to look at rather than re-derive.

**Pull the checkout that is already there — do not copy one, and do not clone
a second.** Each machine keeps its own checkout and they meet on GitHub:
Windows at `Projects\milkdrop-viz` on the NTFS drive, Mint at
`~/mintdrop-viz`, Linux at `~/Projects/milkdrop-viz`. The Windows one carries
about 1.4 GB that is **not** in git and is expensive to rebuild — a native
`.venv\Scripts` and the 115 MB LGPL `ffmpeg.exe` that `build_windows.ps1`
fetches once — so `git pull` there and leave the rest alone. A fresh clone
throws both away; a folder copied off a Linux box is worse, bringing a `.venv`
full of Linux wheels. `config.json`, `ratings.json` and `keymaps.json` are
gitignored on purpose: they are each machine's own state, not something to
inherit.

One thing a pull does not fix. Files that predate `.gitattributes` in a
checkout keep whatever line endings they were committed with, because an
unchanged file never goes through the filter — the three `.ps1` files arrived
here as LF that way. `git ls-files '*.ps1' '*.bat' | xargs rm` then
`git checkout -- .` re-runs the filter, and the tree stays clean afterwards
because the index side is unchanged.

**What landed, newest first**

| commit | what |
|---|---|
| `49c7bd6` | `MilkHighlighter` — the Code tab is syntax-coloured |
| `0158ca7` | why the compile failure was missed, corrected; `"error loading"` added |
| `9afa718` | **from Mint** — read the compile failure off WARN as well as ERROR |
| `6835110` | Bluetooth output verified; half the microphone item |
| `359f202` | C1 always on top: the `t` key and a menu entry |
| `7362428` … `20c81a4` | section A: the Preset Studio |

**What to check there, in order.** Everything below already behaves on Linux
and on Mint's own hardware, so a difference is a Windows difference.

1. **The Code tab at all.** Open a preset, edit it, Ctrl+Enter. Then break a
   shader on purpose and look for the red line under the box — find
   `shader_body`, replace with `shader_bodyz`, All, Apply.
2. **Which log level the failure arrives at.** This is the one thing Mint
   caught and it cost the trip. The level is picked per stage, not per
   severity — see the table in `CLAUDE.md` — and the code now matches on both
   the level and the message text, so it should hold. Confirm rather than
   assume: the same 4.2.0 library is vendored for both platforms, which is an
   argument, not a measurement.
3. **`t`, always on top.** SDL sets `WS_EX_TOPMOST`. Then press `t`, go to
   wallpaper mode, and come back: it must be off, and the menu must agree.
   That ordering is deliberate — on-top is dropped *before* the exstyle is
   saved, so the restore cannot bring `WS_EX_TOPMOST` back with it — but
   Windows is where the saved exstyle actually gets used, so it is worth the
   two clicks.
4. **Wallpaper mode still parents into the right WorkerW.** Unchanged by any
   of the above, but it is the feature that shares the code path with 3.
5. **The microphone.** The Linux path opens a real input and delivers samples;
   nobody has spoken into one, and the Windows and sounddevice backends have
   not been tried at all. A headset and one sentence closes roadmap D.

**VERSION is 2.1.0.** 2.0.0 was bumped on 2026-09-09 once section A was
complete, 2.1.0 on 2026-09-13 — what landed between them is three features (a
lock that no longer takes the controls away, wallpaper mode on Wayland, the
Customize menu), not repairs to 2.0.0, so the minor moves rather than the
patch. Both build scripts read the `VERSION` file, and both now refuse to build
unless `installer/licenses/EULA.txt` names the same version: that line is the
only place the number is written out in prose, nothing reads it back, and a
line that has drifted looks exactly like one that has not. The Inno `AppId`
GUID does **not** move with any of it — that GUID is what makes the installer
an upgrade rather than a second copy, and it never changes.

**`wallpaper-mode.png` still needs re-shooting on Windows** for the store
listing.


### Windows caught up with 2.0 — 2026-09-10

Everything in the handover above was run on this box against the real
library, and then again against the built 2.0.0 package. Section A and C1
behave the same as they do on Linux and on Mint; what differed was one defect
of our own and two of my own mistakes, all below.

**All nineteen suites pass**, including a new `test_vsync.py`.

**What the handover asked for, answered**

1. **The Code tab** — opens, loads the preset's own text, highlights it, and
   Ctrl+Enter / Apply compiles. Find-and-replace, `+ per-frame`, `Shift
   colours` and `Mash up` are all wired; the mashup names what it borrowed
   ("Both shaders from …") exactly as it does on Mint.
2. **The log level** — both halves arrive here, and the fix from Mint holds:
   breaking `shader_body` gave `[projectm:error] [PerPixelMesh] …` at ERROR
   **and** `[projectm:warn] [FinalComposite] … Using fallback shader.` at
   WARN, with the WARN one being the text that reached the red line under the
   editor. Revert put the preset back (6633 bytes to 6631, the two inserted
   characters gone).
3. **`t`, always on top** — `WS_EX_TOPMOST` goes on (exstyle `0x110` to
   `0x118`) and the toast says so. Into wallpaper mode and back: on-top is
   dropped *first* (`Always on top: OFF` in the log, before the reparent), the
   window returns to its exact original geometry, `WS_EX_TOPMOST` is **not**
   restored with the saved exstyle, and the menu agrees. That ordering is
   correct on Windows.
4. **Wallpaper mode** — still takes the Windows 11 layered-child path
   (`WS_EX_LAYERED` set, `WS_CHILD` set, no longer a top-level window), the
   desktop icons stay on top, the tray icon appears, its menu leaves the mode,
   and the window comes back bordered at its old size.
5. **The microphone** — still not done, and not doable from here. It needs a
   person, a headset and a sentence. Unchanged from the roadmap.

**One real defect, fixed: the frame rate.** The app opened at **3.5 fps**. It
is not the app -- a probe with none of its code in it swapped at 4.0 fps with
`SDL_GL_SetSwapInterval(1)` and 4,472 with it off, on a Radeon HD 7800 and a
59 Hz panel, reproducing foreground and background and at two window sizes.
An hour later the same probe read a healthy 59.9 fps with no driver change and
no reboot, so it is an intermittent stall in the compositing stack rather than
a driver trait, and the cause is not established. `Renderer._vsync_paces` now
measures six swaps at startup and hands the number to `vsync_paces`, which
turns vsync off when it cannot keep up with the refresh rate and lets the
software frame cap hold the rate instead: 3.5 fps became 111. Where vsync is
healthy it costs six swaps and changes nothing, which is what the machine now
does. `test_vsync.py` covers the policy; the full write-up is in `CLAUDE.md`.

**One cross-platform test bug, fixed.** `test_wallpaper.py`'s `FakeXlib.atom`
issued atom values above 2**32 -- deliberately, to catch a ctypes signature
that narrows one to `c_int`. A Windows `c_ulong` is four bytes, not eight, so
those values simply truncated and five X11 checks failed on the fixture rather
than on the shipped code. The base is now sized to the platform's `long` and
keeps the top bit set, so the guard still bites. The X11 code itself is
correct: format 32 really is an array of `long`.

**A flake made deterministic.** `test_thumbnails`'s
`the_browser_shows_a_cached_thumbnail_and_skips_the_rest` failed on the second
full run of the day and passed 3/3 alone -- the same flake as the previous
session, always `assert img.save(...)` with no message. It now retries once
and says what failed. Worth knowing separately: the app's own `_ThumbWriter`
was seen logging `[WinError 5] Access is denied` on its `.part` to `.jpg`
`os.replace` while the browser had the old file open. It degrades to a log
line and no thumbnail, which is acceptable, but `os.replace` being atomic on
Windows does not make it immune to a sharing violation.

**Two things I got wrong, recorded so the next session does not repeat them.**
I reported the tray icon as never appearing in wallpaper mode -- it was there
the whole time and I had read it as another app's icon; see the note in
`CLAUDE.md`. And the first version of the vsync write-up called it a property
of the AMD legacy driver, which the later healthy reading disproves.

**Still open for Linux**

- ~~Run `python check_sync.py --fix` on the box that has Bomsai Player beside
  it.~~ **Done 10 Sep**, Player commit `0a7cc8f`; its 107 tests pass with the
  vsync work in.
- ~~The first Linux run is worth watching for the `vsync is not pacing frames
  here` line.~~ **Run 10 Sep: silent.** Source build under a nested X server
  with llvmpipe, `BOMSAI_FPS=1`, steady 44–45 fps against a 60 fps cap — the
  probe judged vsync healthy and left it alone, which is the intended no-op.
  The threshold has not been exercised on a real Linux GPU where vsync is
  genuinely broken, because nothing here reproduces that.
- ~~`installer/licenses/EULA.txt` says "Version 2.0.0 — 8 September 2026" while
  the bump landed on the 9th.~~ **Answered 10 Sep.** The date was deliberate —
  a licence is dated by when its *terms* were written, and they have not
  changed — but the line read as one thing dated wrongly rather than two facts
  side by side. It now says "Applies to version 2.0.0 · terms of 8 September
  2026", which is both, unambiguously.


### Handing over to Mint — 2026-09-10

**The checkout was brought up to date from Linux.** `~/mintdrop-viz` was
fast-forwarded on 2026-09-10 while its drive was mounted, from six commits
behind to level with `origin/master`, tree clean; both machines are uid 1000,
so nothing changed owner on the way in. Run `git pull --ff-only` there anyway
before starting — it costs nothing and Linux may have pushed since. Either
drive can be updated this way whenever it is mounted, which is cheaper than
booting the box to run one command.

The trip is one screenshot the other two boxes cannot take, and a smoke test
of the six commits Mint has not run.

**1. Shoot wallpaper mode on the real session.** Four site screenshots were
re-shot on Linux on 2026-09-10 — the Code tab, a compile failure caught, the
right-click menu with *Always on top*, and the key list with the new row —
and they are already in the site repo. The gap left is wallpaper mode: the
guide pairs every feature with a Linux shot and a Windows one, and wallpaper
mode has only `win-wallpaper.webp`, because the Linux half of the feature
landed in `3e994ae` *after* the last shot run.

Cinnamon is the shipped target and the only desktop where this is verified end
to end, so no rig is wanted: run the built package with `--wallpaper` on the
ordinary session, let a preset settle, and grab the whole screen with the
desktop icons drawing over the visuals. That picture is the feature.

Do not try this in a nested Xephyr. It was attempted on Linux first and the
stacking was right — `_NET_CLIENT_LIST_STACKING` had the visualiser below
nemo-desktop's window — but nemo-desktop stops drawing icons the moment a
second `_NET_WM_WINDOW_TYPE_DESKTOP` window appears, and restarting it refuses
outright with *"Desktop already managed by another application, skipping
desktop setup."* On a real Cinnamon session nemo-desktop is already up and
managing before the app ever starts, so the case never arises. That is the
whole reason the shot belongs on Mint.

**Read it before it ships.** The usual rule is about preset names; here the
risk is the desktop itself — icon filenames, a panel, a notification, anything
in a window title. Tidy the desktop first rather than crop around it after.

Commit it as `store/screenshots/linux-wallpaper.png`. It is a **courier for
the site guide, not a Store shot** — Linux is not in the Microsoft Store
listing — and it gets moved into `bomsai-software` from the Linux box, where
that repo lives. Mint has no checkout of the site.

**2. Smoke-test what Mint has not seen run.** Mint was last on `9afa718`, its
own commit. Six landed after it, three of them worth a minute each:

- **`49c7bd6`, the syntax highlighter.** Open the Code tab on a preset with a
  real shader and check three things colour differently: keywords, numbers and
  `//` comments. A plain-looking editor means the `QSyntaxHighlighter` was
  collected — it is held on the panel deliberately for that reason.
- **`8bcf446`, the vsync probe.** Never run on that hardware (AMD Pitcairn,
  Mesa 23.2.1). It times six swaps at startup and turns vsync off if they pace
  below half the refresh rate. **Silence is the pass** — Windows only ever saw
  it speak because that box was stalling at 3.5 fps. `BOMSAI_FPS=1` prints a
  rate every five seconds if you want the number.
- **`0158ca7`.** Mint's fix was right and its *reason* was wrong: the level is
  chosen per stage, not per severity, so a comp failure at WARN and a warp
  failure at ERROR are both normal. Composite *load* failure spells it
  `Error loading…` and was matching only by accident; that word is in the list
  now. The table is in `CLAUDE.md`.

All nineteen suites should pass, `test_vsync.py` among them.

**3. The microphone, if that box has a headset.** The last open item on
roadmap D. The Linux path opens a real input and delivers samples; nobody has
spoken into one. A headset and one sentence closes it.

**VERSION is 2.0.0** and does not move again unless asked.


### What Mint found — 2026-09-10

Answering the three items above, in order. The checkout was already level with
`origin/master` on arrival; `git pull --ff-only` changed nothing.

**Everything was run from a package built here, not from source.**
`installer/build_linux_docker.sh` found no projectM tree at the pin and reused
`installer/vendor/linux/` as it says it will, and produced
`BomsAIVisualizer-2.0.0-linux-x86_64.tar.gz`, 86 MB, floor **GLIBC_2.34**. The
tarball ships a portable `app/` tree, so it was extracted and run in place --
the installed copy on this box is still 1.7.0 and was left alone.

**1. The screenshot is `store/screenshots/linux-wallpaper.png`** (1920x1080,
2.0.0, Cinnamon 6.0.5 on X11). It had to be **force-added**: `.gitignore:36`
excludes `store/screenshots/` because store media is uploaded by hand and does
not normally travel. This one does travel -- it is the courier for the site
guide -- so it is in the tree deliberately and should be **deleted from this
repo once it is in `bomsai-software`**, or the ignore rule stops meaning
anything.

Read before shipping, as instructed: the six desktop icons are `dataset_1MM`,
`Downloads`, and launchers for Chrome Remote Desktop, Firefox, Chrome and
VirtualBox; the two desklets show CPU and disk *percentages*; the panel is
launchers, tray and a clock. No filenames, no titles, no notifications, nothing
personal. The Claude window was minimised for the shot and restored after, so
the panel's window list is empty, which reads cleaner than it would otherwise.

The stacking held on the real session exactly as `c6378b4` intended --
`_NET_CLIENT_LIST_STACKING` bottom to top was **visualiser, nemo-desktop,
everything else**, and nemo-desktop went on drawing icons throughout, which is
the failure the Xephyr attempt hit. The window carried
`_NET_WM_WINDOW_TYPE_DESKTOP` plus SKIP_PAGER, SKIP_TASKBAR and STICKY. Worth
knowing: **muffin does not keep `_NET_WM_STATE_BELOW` on the window** -- it is
asked for and is not in `_NET_WM_STATE` afterwards. The stack is right anyway,
because the EWMH restack is what does the work; a check written against BELOW
would report a failure that is not there.

**2. All twenty suites pass** on Python 3.10 / PySide6 6.11.2 -- a different
interpreter and numpy (2.2.6) from the box they were written on, which is worth
something on its own. `check_sync.py` skips here: no Bomsai Player copy beside
this clone, so the two `viz_common.py` copies are still unverified from Mint
and must be checked on Linux after pulling.

- **`49c7bd6`, the highlighter: works.** Read back off the document rather than
  eyeballed -- key `#e0b44a`, number `#b5c46a`, `tex2D` inside `warp_7`
  `#7fb7d9`, `//Geiss's Motion Blur` `#9a9a9a` italic, blank line untouched.
  Four roles, four colours, and the `QSyntaxHighlighter` was not collected.
- **`8bcf446`, the vsync probe: silent, which is the pass.** AMD Pitcairn,
  Mesa 23.2.1, nothing printed at startup and no swap-interval change. The
  Windows stall does not reproduce on this card under Mesa.
- **`0158ca7`: confirmed against Mint's own binary, not just the source.** A
  probe broke one `float2` declaration at a time in a preset carrying both
  shader blocks and read the level the callback was handed:

  | what was broken | level | message |
  |---|---|---|
  | `warp_4` | **5 ERROR** | `[PerPixelMesh] Error compiling warp shader code.` |
  | `comp_6` | **4 WARN** | `[FinalComposite] Error compiling composite warp shader code - Using fallback shader.` |
  | nothing (control) | — | nothing logged |

  Both filled `_LAST_PM_ERROR` through the shipped predicate, so the editor
  reports either kind. Per stage, not per severity, exactly as written down.
  The `[TextureManager] Failed to find requested texture` noise that argues
  against widening to the whole of WARN showed up unprompted in the wallpaper
  run, for `"grad3"`.

**3. The microphone cannot be closed here — this box has no microphone.**
`pactl` reports one input card whose Front Microphone and Rear Microphone ports
are both **"not available"**, with the active port on **Line In**; nothing is
plugged into either jack. Roadmap D's last item needs a machine with a headset,
and there is nothing about the Linux path Mint can add without one.

**And one found after installing.** The preset browser's vertical scroll bar
had no visible handle. Styling `QScrollBar::handle` takes the sub-control away
from the native style, `PM_ScrollBarSliderMin` goes with it, and the handle is
then sized purely by page/range -- **1px** at 9,795 rows, which the 4px radius
rounds away to nothing. Both sheets now floor it, and while measuring it the
handle turned out to be 2.0:1 against its own trough in both themes, so
`scroll_handle` went to 3.0:1 as well. `test_theme.py` measures the real
widget at a real row count; twenty rows of test data hides this completely.

**And one thing that was not on the list.** `import sounddevice` raises
`OSError`, not `ImportError`, when PortAudio is missing, and the guard in
`viz_common` caught only `ImportError` -- so a Linux box that never installed
`libportaudio2` could not start the app at all, over a capture backend that
Linux does not even lead with. It hid because every machine that has run this
also has the system package; reproduced by poisoning
`ctypes.util.find_library` in a subprocess. Fixed, with `test_audio_import.py`
covering it, and the comment in `bomsai-visualizer.spec` claiming the bundled
`libportaudio.so.2` is what sounddevice finds is corrected -- it is not, and
shipping an unversioned `libportaudio.so` beside it is the untested change that
would actually switch the fallback back on.


### The Windows installer needs rebuilding — 2026-09-10

**Done, 2026-09-13** — rebuilt at 2.1.0, which supersedes everything
measured below. Left in place because how the stale package was *read*
is the reusable part.

**`BomsAIVisualizer-2.0.0-win64-setup.exe` on the NTFS drive is stale, and no
longer only cosmetically.** It was built at 03:52, six commits before the tree
it now sits in. Checked against the artifact rather than assumed, by scanning
the zlib blobs inside `app/BomsAIVisualizer.exe` for the stylesheet:

| string | in the shipped exe |
|---|---|
| `rgba(255, 255, 255, 55)` — the old handle alpha | **present** |
| `rgba(255, 255, 255, 85)` — the new one | absent |
| `min-height: 28px` — the handle floor | absent |

So the installed Windows build has exactly the defect Mint reported after
installing: a scroll bar whose handle is one pixel tall and the colour of the
trough. It is a shared stylesheet, so Windows was never a separate bug — only
a separate package. `licenses/EULA.txt` in the same tree is the old wording
too (`Version 2.0.0 — 8 September 2026` rather than "Applies to version 2.0.0
· terms of 8 September 2026").

The checkout there is already at `0bc88dc`, so the rebuild is just
`installer/build_windows.ps1` — nothing to merge first. **The Inno `AppId`
GUID does not move**, so the new package upgrades the installed one rather
than sitting beside it.

Worth checking while the tree is open, because it is one line and it settles a
question this session raised and could not answer from Linux: the Windows
bundle **does** carry
`_internal/_sounddevice_data/portaudio-binaries/libportaudio64bit.dll`.
That is why `add5384` is a Linux-only story — sounddevice's own `except
OSError` loads that copy on Windows, and the branch for every other platform
is a bare `raise`. Nothing to fix there; just do not go looking for it.

~~**The Linux tarball is current**: rebuilt at 14:19 from `0bc88dc`, 86 MB,
md5 `97fdba8f19dc2f21fdf8550019537570`, floor GLIBC_2.34, and verified to
start on a stock `ubuntu:24.04` with no libportaudio2.~~ **Superseded by the
2.1.0 package, 2026-09-13** — see the last section. That 2.0.0 tarball has
been deleted; there is only ever one package in `installer/dist/`.


### Three changes from a few days of using it — 2026-09-13

Reported after living with the 2.0.0 build for a few days. All three are done
and all three are verified against the running app, not only against tests.

**1. Cycling while locked.** Lock used to stop the controls as well as the
clock, which is not what anyone means by it. Now the only thing it holds is the
auto-advance timer in `Renderer.run`; next, previous, random and a jump from
the preset browser all still move. The guard came out of four renderer methods
and out of `ProjectM4.next_preset` / `prev_preset`. libprojectM needs no help
here — measured against 4.1.0 with the core lock on, `play_next`,
`play_previous` and `set_position` each moved the playlist and ten rendered
frames later the new preset was still on screen. Confirmed on the real app over
its stdin IPC: lock, then three `next_preset` walked through three presets and
`prev_preset` came back one.

**2. Wallpaper mode on Wayland — the refusal was wrong.** "X11 mode not
enabled" on the main Linux box was `wallpaper_support()` refusing every Wayland
session on the grounds that XWayland has no below-the-desktop layer. Measured
on Ubuntu GNOME / mutter and that is simply not so: a desktop-type XWayland
window goes to index 0 of `_NET_CLIENT_LIST_STACKING` and stays there, and the
real app entered with `--wallpaper` and held index 0 for the whole run. The
only Linux refusal left is having no `DISPLAY` at all. A compositor with no
desktop layer is not guessed at — `x11_lower_to_bottom` already measures where
the window landed and says so, and that does not go stale the way a list of
compositor names would.

The menu entry gained a second gate on the way past: **no system tray, no
offer.** A desktop window gets no keyboard and no mouse, so the tray icon is
the only way back out, and a GNOME session without an AppIndicator extension
has no tray — switching it on there is a one-way trip ending in `kill`.

**3. Covering the desktop icons on Mint.** Same session, different half: the
mode switched on, said nothing, and sat over the Nemo icons. The toggle was not
lying — it measured the stack and was right at the time. A window manager
restacks on its own schedule and a compositing one paints from a stack of its
own, so the one lower at map time is an invariant that quietly stops holding.
`_hold_wallpaper_bottom` re-runs the measurement from the render loop every two
seconds: one property read off the root, and nothing on the wire unless the
answer is wrong. **This one is the only change here that has not been seen on
the machine that reported it** — it needs a Cinnamon session. What can be shown
from Linux is that the mechanism works and that it does nothing when it should
not; whether it closes the Mint report is for Mint to say.

**Plus: a Customize entry, above About.** The right-click menu has grown past a
comfortable screenful and nearly every line on it is also a key, so each of the
twenty-three entries can now be switched off — `MENU_ENTRIES` is the table,
`config["menu_hidden"]` is the state, and the dialog puts each entry's key
beside it because that is the answer to "can I lose this one?". Hidden entries
are stored rather than shown ones, so an entry added in a later version turns
up visible instead of vanishing on an old config. Customize and Quit are not in
the table: one is the way back for anyone who switches everything else off, the
other is the way out. The separators are written for the full menu, so
`_tidy_separators` drops the rules left with nothing on one side of them.

Two new suites, `test_lock.py` and `test_menu.py` — twenty-two now, not twenty.
The menu one is the useful one: it hides each key in `MENU_ENTRIES` in turn and
requires the menu to get exactly one row shorter, which is what catches a tick
box wired to nothing.


### 2.1.0, and a fresh Linux package — 2026-09-13

**The version is 2.1.0, not 2.0.1.** The three changes above are features — a
lock that no longer takes the controls away, wallpaper mode working on Wayland,
a menu that can be trimmed — so the minor moves. A patch number would have said
"2.0.0 was broken and here is the repair", which is not what happened.

`VERSION` is the only place the number lives and both build scripts read it,
with one exception that is now guarded: `installer/licenses/EULA.txt` names the
version in prose, nothing reads that line back, and its other half — the date
the *terms* were written — deliberately does not move with it. Both
`build_linux.sh` and `build_windows.ps1` now refuse to build unless the licence
says "Applies to version `<VERSION>`". Proved both ways on the bash side; the
PowerShell half has not been run, because there is no pwsh on this box.

**`BomsAIVisualizer-2.1.0-linux-x86_64.tar.gz`** — built 23:11 from a working
tree at `460fbc9` plus this bump, 86 MB, md5
`069d7ac86101cd35e171792785a3075a`, floor **GLIBC_2.34**, libprojectM rebuilt
in the container from `/home/fr0st/projectm` at the pin. The 2.0.0 tarball is
deleted.

Verified against the artifact, not the tree:

- Extracted and started on a **stock `ubuntu:24.04`** with no libportaudio2
  (`libgl1 libglib2.0-0t64 libegl1` and nothing else added). It prints the
  `sounddevice unavailable` line and carries on to `--help` and exit 0, which
  is the `add5384` behaviour, and `--wallpaper` is in the help text.
- Both `VERSION` files in the package say 2.1.0, and so does the licence.
- **The new code is in the binary.** None of it is greppable from the extracted
  tree — the app's own modules are one zlib stream each inside the PYZ in the
  executable — so the blobs were decompressed and searched, the same way the
  stale Windows installer was read. All six of `_hold_wallpaper_bottom`,
  `WP_HOLD_EVERY`, `MENU_ENTRIES`, `menu_hidden`, `_tidy_separators` and the
  new lock toast are present. The same scan over the 2.0.0 executable finds
  **none** of the six, which is what makes the first result mean anything.

Not done here: the app was not driven through its IPC from the package. The
features were confirmed on the running app at `460fbc9` when they were written,
and what a package can add to that is whether it carries the same code, which
is the scan above. ~~The Windows installer is still the 10 Sep 2.0.0
build.~~ **Rebuilt at 2.1.0 the same day — see the next section.**

### Windows caught up with 2.1.0 — 2026-09-13

The Windows clone was already at `6dd8bb7`; nothing to merge. Everything
below is verification of the Linux work on this platform, plus one test fix.

**Twenty-two suites, twenty-one green on the first run.** `test_audio_import`
failed its middle check, and it was the test that was wrong, not the app. The
suite poisons `ctypes.util.find_library` for "portaudio" and requires
`_SD_AVAILABLE` to come back False — which is the Linux story. On Windows
sounddevice catches its own `OSError` and loads the PortAudio in its own wheel:
measured, the import comes up bound to
`_sounddevice_data/portaudio-binaries/libportaudio64bit.dll`, so the backend
genuinely is there and reporting otherwise would be the lie. The check now asks
`_wheel_portaudio()` — does the *installed package* carry one — rather than
`sys.platform`, and asserts True where it does. That keeps it a real check on
both platforms: the day a wheel stops shipping the file, Windows inherits the
hole Linux had and the suite says so. Twenty-two green after.

**The PowerShell EULA guard is now proven, which Linux could not do.** With
`VERSION` temporarily at 9.9.9, `build_windows.ps1` exits 1 with
`installer\licenses\EULA.txt does not say "Applies to version 9.9.9"` before it
touches the build directory; at 2.1.0 it passes. Both halves of that guard have
now been run.

**The three changes were driven on the running app, not only tested.**

- *Cycling while locked.* Scroll Lock, then three `Next` walked 107 → 108 → 109
  and `Previous` came back to 108, with the log reading
  `Preset locked (the timer, not the controls)`. Same behaviour Linux measured.
- *Customize menu.* The dialog comes up themed, two columns, twenty-three rows
  with each entry's key beside it. Clearing Fullscreen display, Record clip,
  Desktop wallpaper and Audio source wrote
  `"menu_hidden": ["display", "record", "wallpaper", "audio"]` to config.json in
  menu order, and the reopened menu was exactly those four rows shorter with
  the two separators that had bounded the record/wallpaper group collapsed to
  one — no double rule, no leading or trailing rule. "Show everything" put them
  all back and emptied the list.
- *Wallpaper mode* needs no Windows work: `_hold_wallpaper_bottom` is gated
  `sys.platform != "win32"` and the new tray gate on the menu entry is always
  true here. Not re-driven — the Windows wallpaper path is unchanged in this
  range.

**The scroll bar handle, measured rather than eyeballed.** This is the defect
Mint reported and the one the stale 2.0.0 installer was shipping. In the
browser over the real library the vertical handle is **27 px** of grey 102 on a
trough of grey 26 — the `min-height: 28px` floor holding, against the 1 px it
was, and 3.03:1 contrast against its own trough.

**`BomsAIVisualizer-2.1.0-win64-setup.exe`** — built 23:45 from a clean tree at
`6dd8bb7`, 78.3 MB, sha256
`710b0a4a1f66ab0e1be25fa83119c4088377ca60c5ed406c8059f54cc675a4de`.

Verified against the artifact:

- Both `VERSION` files say 2.1.0, and the licence says
  "Applies to version 2.1.0 · terms of 8 September 2026".
- Seven licence files, both projectM DLLs and SDL2, no `glew32.dll`, the `.scr`
  screensaver present.
- The bundled ffmpeg reports `--disable-libx264` with no `--enable-gpl` and
  lists no x264 encoder.
- **The new code is in the executable**, by the same PYZ-blob scan the Linux
  package got: `MENU_ENTRIES`, `menu_hidden`, `_tidy_separators`,
  `MenuCustomiseDialog`, `_hold_wallpaper_bottom`, `WP_HOLD_EVERY`,
  `min-height: 28px` and the new lock log line are all present. The same scan
  over the 2.0.0 executable beside it finds **none of the eight**, which is
  what makes the first result mean anything.

Not done: the Inno installer itself. Running it trips UAC and there is no
non-interactive elevation on this box, so a fresh install, the shortcuts and
the screensaver registration are unverified — the payload is not, via the
portable tree above. The stale `BomsAIVisualizer-2.0.0-win64-setup.exe` is
still sitting in `installer/dist/` beside the new one; Linux keeps one package
in that directory and Windows should too, but deleting the thing the site may
be linking is not a call to make from here.

### Mint, 2026-09-14 — why the icons were gone

`_hold_wallpaper_bottom` works. It was checked on the Cinnamon session it was
written for and could not be tested on: the visualiser went to index 0 of
`_NET_CLIENT_LIST_STACKING`, below `nemo-desktop`, and **stayed** there through
a workspace switch, a window mapping and unmapping over it, and Show Desktop —
75 seconds of watching the property, no drift. Entering wallpaper mode from the
`d` key put it there in 2.2 s. Nothing in this part was wrong.

The report was real anyway, and it is not a stacking bug at all.
`nemo-desktop` looks for other `_NET_WM_WINDOW_TYPE_DESKTOP` windows **when it
starts**, and gives up completely if it finds one:

    ** (nemo-desktop:9809): WARNING **: Desktop already managed by another
    application, skipping desktop setup.
    To change this, modify org.nemo.desktop 'ignored-desktop-handlers'.

It then exits. No icons are drawn, so the visuals look like they have covered
them — while the desklets, which the Cinnamon shell draws and which never went
anywhere, still sit over the top. That is exactly the shape of the report:
*covers icons, but remains below widgets*. Every property involved reads
correct throughout, which is why nothing that measures the stack could catch
it.

Only the sessions where Nemo starts **second** lose the icons: a login with
autostart-as-wallpaper, a Cinnamon restart, Nemo restarting for any reason.
Turning wallpaper mode on by hand while Nemo is already up has always worked,
which is why it kept passing.

`nemo_allow_desktop_window` adds the app to the list Nemo names, once, on the
way into wallpaper mode — and leaves it there, because taking it off again at
the end would lose the icons at the next restart. The list matches the WM_CLASS
*instance*; `x11_wm_class` reads that off the live window rather than
hard-coding it, since a source run is not called `BomsAIVisualizer`. Verified
against the running app: `x11_wm_class` returned `BomsAIVisualizer`,
`nemo-desktop` and `com.anthropic.claude` for the three managed windows, the
write was idempotent and left `conky` and `csd-background` alone, and with it
in place `nemo-desktop` started cleanly underneath a running wallpaper-mode
visualiser and drew the icons over it.

Written on the way *in* and not at launch, because nowhere else knows the
window is about to claim the desktop, and a machine that never uses wallpaper
mode should not have its desktop settings touched. It is wrapped in its own
try: a convenience for the next session is not a reason to refuse to be the
wallpaper in this one.

**The first build of this said it had written the setting and had not.** A
system binary run from inside the PyInstaller bundle inherits an
`LD_LIBRARY_PATH` pointing at `_internal`, so `/usr/bin/gsettings` loaded the
bundled `libgio-2.0.so.0`, which cannot load the system's dconf GIO module and
quietly fell back to the keyfile backend. Both calls returned 0, the value
persisted across runs, and it all went into
`~/.config/glib-2.0/settings/keyfile` where Nemo never looks. The tell was
running the same `gsettings get` twice -- bare, and under the bundle's
`LD_LIBRARY_PATH` -- and getting two different lists. `system_env()` restores
`LD_LIBRARY_PATH_ORIG`, which PyInstaller saves for this, and any other system
binary the app shells out to should use it. *(`system_env` was deleted the
next day: `child_env`, written for the ffmpeg version of this same trap, was
already doing exactly that and covers `DYLD_LIBRARY_PATH` as well. The call
site uses `child_env`.)*

**A session that has already lost its icons does not get them back until Nemo
restarts** — the setting is read at Nemo's startup. The toast says so. Logging
out and back in is the fix; so is `nemo-desktop &` from a terminal.


### Mint, 2026-09-14 — the strip along the bottom, and why the first run covered the icons

Reported: with the Cinnamon panel hidden, a band of the real desktop wallpaper
showed under the visualiser. Measured with `xwininfo`: the window was the right
**size** (1920x1080) and in the wrong **place** — `Absolute upper-left Y: -32`.
A 32px strip at the bottom, which is exactly the panel that had been covering
it. So this was never about the panel; hiding it only removed the lid.

`x11_align_frame` corrects for the window manager's frame once, on the way into
wallpaper mode, while Muffin is still deciding whether a desktop window gets a
frame at all. It read a 32px frame, corrected by it, and the frame was gone a
moment later. Nothing re-measured afterwards, so the one bad reading stood for
the rest of the session.

Two things had to change. `_hold_wallpaper_box` re-measures geometry on the
same two-second timer that already re-measures the stacking, and it moves the
window with `XMoveResizeWindow` rather than through SDL — SDL drops a request
matching its own cache, and it does match, which is why the first attempt at
this fix changed nothing at all. Both traps are written up in CLAUDE.md.

The second change is the one that also answers the icons question. Both holds
used to sit *below* the idle-pause `continue` in the render loop, and the idle
pause is on twenty seconds of silence — so on a quiet desktop the wallpaper
stopped defending its position and its stacking within half a minute of being
switched on. If `x11_lower_to_bottom` loses the race at map time, the two-second
timer that would have fixed it is asleep before it gets a chance, and the visuals
stay over the desktop icons until somebody plays something. That is almost
certainly the "first run covered the icons, second run was fine" report: not a
fluke, nothing that needed registering, just a race whose safety net had gone to
sleep. The holds now run above the idle check.

Verified on this box: shoved to (0, -40) at 1920x1040 **while parked**, back to
(0, 0) at 1920x1080 within five seconds; stack unchanged underneath
`nemo-desktop`. Twenty of the twenty-two suites pass here — `test_theme` and
`test_thumbnails` fail identically at a clean `6e7006c` under a stock-latest
PyQt6, so they need the pinned Qt to mean anything on Mint.

Wayland, for Ubuntu 26.04: nothing needed. `wallpaper_support()` has accepted
Wayland since 13 Sep as long as `DISPLAY` is set, `SDL_VIDEODRIVER=x11` is
forced on Linux so the window is an XWayland surface either way, and
`nemo_allow_desktop_window` returns "" without writing anything on a box with no
`org.nemo.desktop` schema (checked: a non-zero `gsettings get` is the
no-op path). One tarball serves both desktops. What is **not** verified from
here is whether GNOME's desktop-icons extension has its own version of Nemo's
"another application is already managing the desktop" refusal — that needs
someone on the 26.04 box to turn wallpaper mode on and say whether the icons
survive.

### Reviewing the Mint fixes — 2026-09-14

Read back over `d87dfee..4dc8258` and mutation-tested the new code: six ways of
breaking it, four caught by the suites as they stood. The two that were not are
the point of this pass.

**The holds sitting above the idle-pause `continue` was not pinned.** Moving
them back below it -- which is the tidy-up somebody makes while cleaning, since
they *look* like they belong with the other wallpaper work further down -- left
all twenty-two suites green. That single edit reinstates the Mint report: a
wallpaper is parked nearly always, and below the `continue` neither hold
defends the window until sound comes back. `the_holds_run_before_the_idle_pause_parks_the_loop`
pulls both nodes out of `Renderer.run` with `ast` and compares their lines, the
same trick `test_lock.py` uses on the auto-advance guard.

**`x11_wm_class` reading the instance rather than the class was not pinned
either.** `the_instance_name_is_what_nemo_matches` asserted that the word
"instance" appeared in the function's own docstring -- a check only an editor
can fail. Swapping `split(b"\0")[0]` for `[1]` left every suite green. It now
runs the real function against a window whose `WM_CLASS` is `conky\0Conky\0`
and requires `conky`, plus a window with no `WM_CLASS` at all, which must come
back "" rather than a name invented from somewhere else and written into
somebody's desktop settings.

**`system_env` was `child_env` with less of it.** Measured: `system_env() ==
child_env()`, and `child_env` covers `DYLD_LIBRARY_PATH` too, so the new one
was a strict subset written seven hundred lines below the old one. Deleted; the
gsettings half of the story moved into `child_env`'s docstring, where it
belongs -- it is the second measured instance of one trap, not two traps.

**The geometry hold gave up silently.** After `WP_FIX_LIMIT` corrections it
returned and logged nothing; `_wp_box_moaned` only ever covered the exception
path. That is the one route that ends in a wallpaper in the wrong place and an
empty log, which is a thing this repo has been bitten by before. It now says
where the window keeps landing and where it was asked to go, once.

Two findings from the same read were left alone, deliberately:

- **`_hold_wallpaper_box`'s docstring says re-measuring "picks up the work area
  changing later -- a panel hidden, shown, or set to auto-hide".** It does not.
  `_wallpaper_box()` is built from `SDL_GetDisplayBounds`, which is monitor
  geometry; no panel can move it. What re-measuring catches is the *window*
  being wrong, whoever moved it -- including a window manager that clamped it
  to the work area once and never revisited, which is what the test of that
  name actually exercises. Worth correcting before somebody "fixes"
  `_wallpaper_box` to `SDL_GetDisplayUsableBounds` to match the prose and
  brings the strip along the bottom straight back.
- **The six `pactl` / `parec` calls do not use `child_env`.** Measured and not
  currently broken: both link only libpulse and libpulsecommon, the bundle
  carries neither, and `pactl list sources short` is byte-identical bare and
  under `_internal`. That is an accident of what PySide6 drags in rather than a
  decision, and this is the third appearance of the trap.

One more measured thing, about the code rather than the tests: **`org.nemo.desktop`
exists on this Ubuntu GNOME box**, so `nemo_allow_desktop_window` runs to
completion and writes here. The comment saying the schema is "absent on every
desktop that is not Nemo" is not true of a machine that merely has Nemo
installed. The write is inert -- nothing is reading that list on a GNOME
session -- but the check is "Nemo is installed", not "Nemo is the desktop". It
was written and reset during the review: `dconf reset
/org/nemo/desktop/ignored-desktop-handlers` puts it back to the stock
`['conky', 'csd-background']`.

`installer/dist/` still holds the 2.1.2 tarball built from `4dc8258`; this
commit changes `viz_common.py`, so it is one commit behind. Left as it is on
purpose -- more changes are coming before the Windows trip, and the package is
worth building once at the end of them.


### Linux came straight to 2.1.10 — 2026-09-16

Nothing to port. 2.1.8, 2.1.9 and 2.1.10 are all in `viz_common.py`, which is
one file shared by both machines, so the Linux tree fast-forwarded from 2.1.7
to `82409a1` and had the features. What Linux owed was the other half of three
seams that Windows could only prove on Windows, and one of them is a claim the
Windows entry makes *about* X11 rather than about itself.

**The overhang is absent, as 2.1.10 says it must be.** `_wallpaper_box` grew an
`overhang` argument gated on the caller, and the Windows way in asks for two
pixels past every edge while `_hold_wallpaper_box` does not — the note warns
that asking on X11 would be a window a window manager clamps back and the hold
corrects for the life of the process. Measured on Muffin: entering wallpaper
mode puts the window at **0,0 1920x1080 exactly**, held over six seconds, with
`nemo-desktop` above it in `_NET_CLIENT_LIST_STACKING`. Both overhang call
sites check out by reading: the unconditional one at the `SetWindowPos` is
inside the Windows branch, and `_wallpaper_on_display` gates on `sys.platform`.

**The monitor choice works here too**, which is the half 2.1.10 said Linux
wants. Driven on the live app with a `_refresh_displays` wrapper adding a
pretend 800x450 screen at x=1920 — the same trick the Windows pass used, for
the same reason — and with `request_wallpaper_on` called from a timer, so the
path under test is *moving while already up* and not just the box arithmetic:

    whole desktop   0,0      2720x1080     (the union, both screens)
    monitor 2       1920,0   800x450       (exactly the pretend rectangle)
    whole desktop   0,0      2720x1080     (and back)

Worth noting that X11 gets there by a different road than Windows does.
`_wallpaper_on_display` moves the HWND directly on Windows and leaves X11 to
`_fit_to_wallpaper`, which is SDL — and SDL drops a request matching its own
cache. A move to a different monitor is not such a request, so it goes; and
where it would not, `_hold_wallpaper_box` recomputes the box from the same
config key every two seconds and converges anyway. Both routes were inside
three seconds here, so this is an observation and not a defect.

**2.1.9's two portable halves hold.** The `SHQueryUserNotificationState` bug
cannot happen here — `fullscreen_app_running` returns False off Windows and
says so — but the silence-clock restart and the still-drawing first pass are
shared. Entering the mode produced **no `[idle] paused` at all** in its first
six seconds, and the preset list loaded and advanced. Later, after a genuinely
quiet stretch, it parked as designed, and the 2.1.2 geometry hold still ran
underneath it: shoved to `0,-40 1920x1040` **while parked**, back to
`0,0 1920x1080` within six seconds.

**The 2.1.8 visuals drive clean on X11.** `request_dual`, `request_visual`
either way round, `request_crossfade` to both ends and back, then dual off —
every transition logged, no traceback, and after `dual off` exactly one window
is left at 1920x1080, which is the "stop a switched-off visual showing" fix
doing its job. The pair-on-another-monitor gap 2.1.8 left open on purpose is
the same here and stays open: it is a question about what the feature is.

**26 of 26 suites pass — and the last Linux pass reporting 20 of 22 was the
harness, not the code.** The binding is PySide6; the venv that reported those
two failures had PyQt6 in it, which `viz_common` falls back to for Bomsai
Player's sake and which fails `test_theme` and `test_thumbnails` on API
differences that have nothing to do with this app. Four traps from building
that venv are in CLAUDE.md: the binding, the pins being for Python 3.14 rather
than whatever the host ships, a source run keeping its config in the repo
directory where `XDG_CONFIG_HOME` cannot reach it, and Mint's SDL2 being too
old for the pinned PySDL2 in a way that reports itself as a wallpaper failure.

#### The verified 2.1.10 Linux package

**`BomsAIVisualizer-2.1.10-linux-x86_64.tar.gz`**, 86 MB (89,945,128 bytes),
sha256 `a09ff2b9d79d137f757dc6b3a63d0973bca491b530a6a63e036d633d9e974330`.
`VERSION` inside reads 2.1.10 and the bundled EULA agrees. Highest glibc symbol
required is **GLIBC_2.34**, unchanged, which is the oldest system it starts on.

**And the shipped build was seen doing it**, which is the standard the Windows
entries set. Extracted from the tarball and run on an isolated profile — a
source run cannot be isolated, but a frozen one reads `XDG_CONFIG_HOME`, and
this one did: the window at **0,0 1920x1080** for six seconds with
`nemo-desktop` above it, 9,795 presets loaded, and **no `[idle] paused`**. The
screen capture has the desktop icons, the conky widgets, another application's
window and the taskbar all composited over the visuals — the same picture the
Windows 2.1.10 entry got from Desktop Duplication, and here an ordinary root
grab is enough, because X11 has no fullscreen optimisation to lie about.

The installer script is unchanged and nothing here touches it.

## Notes

- `SDL_GetWindowWMInfo` is used once, for the embedded-in-BomsAI-Studio case. The
  `XID=` key on that print stays literal on every platform; the host parses it.
- FPS `0` means uncapped in the UI, but projectM's internal timers need a real number,
  so a nominal value is passed to `projectm_set_fps` while the loop runs free.

### The last of the tweaks — 2026-09-14

Six things were scoped before building. Three are shipped; the rest are below.

**The lock and the rating moved onto the tab row** of the preset editor, as a
`setCornerWidget`, so they are on screen from every tab rather than only from
the Code tab's footer. Icons, not words: four tabs and "Lock Visuals" do not
both fit across the browser's width. The lock now follows `lock_changed`,
because the key and the right-click menu move the same lock and a button that
only ever heard about its own clicks was wrong for as long as the panel stayed
open. The rating buttons needed `Renderer.request_rate`, which **accumulates**
rather than latching — the render thread serves these once a frame at best,
and these buttons get clicked in a row.

**The browser list can turn its pictures off**, which fits about twice as many
presets on screen. Off has to undo both halves: the icons already on the rows,
*and* the stylesheet row height, which is what makes every row tall whether it
carries a picture or not.

**The picture can now be asked for.** The passive capture — five seconds on
screen, once per preset, only when there is no file — was already there and is
unchanged bar an `auto_thumbnails` switch. What is new is a snapshot that
jumps all three of those gates, on a button and on `c`. A row with no
thumbnail is cached as a *miss*, so `thumbnail_ready` carries the name back
and invalidates it; without that a snapshot did not appear until the list was
rebuilt.

**A slider on any number in the Code tab.** Put the cursor in a number, press
the button, and a slider appears bound to that exact offset — constants inside
a shader included, which the Tweak tab's ten named settings cannot reach at
all. Up to six at once. The text handling is four plain functions in
`viz_common.py` rather than methods on the panel, because all of the
difficulty is there and none of it needs a widget:

- `number_span_at` — `dx=-0.005` is a negative number, the minus in `q1-1` is
  an operator, `1.5e-3` is one number, and the `1` in `per_frame_1` is part of
  a key. A slider on that last one would rewrite the key and silently drop the
  equation on the line, so it returns None rather than guessing.
- `splice` applies edits right to left, so no edit has to know that one after
  it changed the length of the text beyond it.
- `format_like` — `0.960` comes back as `0.960`, not `0.96` and not
  `0.9599999999999999`. A preset is a text file somebody may read and diff.
- `slider_range` is proportional to the value and centred on it, so the middle
  of the slider is where the number started and a slider over `0.001` is as
  usable as one over `120`. Two ceilings, both deliberate: nothing drags past
  twice itself, and a negative number cannot be dragged positive.

Dragging one slider shifts the offsets of every pin after it, which is what
lets six sit over one preset without any of them tracking the others. Typing
under them lets them go rather than splicing on stale offsets — deferred
through `_rebuild_later`, because clearing the strip inside a slider's own
signal frees that slider under itself, which is the crash CLAUDE.md already
paid for once.

**Twenty mutations, all caught** — but three of them only after the tests were
fixed, and two defects were found by a screenshot that no test could see.

Nothing exercised `request_rate` at all. The lock-button check had set the
host's state *before* signalling it, which defeated the thing it was meant to
prove. And the run loop could have accumulated ratings into a field nothing
read: that one is pinned with `ast` over `Renderer.run`, the same trick
`test_lock.py` uses on the auto-advance guard.

The two the screenshot found:

- **The slider labels were useless on the line that matters most.** Named by
  the text before the number, two constants on one shader line came out as
  ``…_1=`shader_body { ret`` and `…_body { ret = 0.5*rad ` — and the second
  had picked up a value the first slider had already changed. They are named
  by the setting where there is one and by `line 6: 0.5` where there is not.
- **The browser's `resize(470, 620)` had stopped being true some buttons
  ago.** The toolbar's own minimum was 522, Qt was silently widening the
  window to fit, and the comment in the source claimed it had been measured.
  Two more buttons took it to 602. `WIDTH` now carries the measured number and
  `test_browser_title` fails if an eleventh button outgrows it. See CLAUDE.md.

Also: four test GIFs arrived in `assets/`, which the spec copies **wholesale**
into the frozen bundle — 34 MB of test material would have shipped in the
installer. They live in a gitignored `testdata/` now.

**Still to build, in this order:** GIF and video burnt into the preset through
`projectm_opengl_burn_texture`, then dual-preset mode blended over itself.
Both C entry points were checked against the shipped binaries rather than the
headers — `projectm_opengl_burn_texture`, `projectm_opengl_render_frame_fbo`
and `projectm_sprite_create` all export from `installer/vendor/linux/
libprojectM-4.so.4` and from `installer/vendor/windows/projectM-4.dll`, so
neither needs libprojectM rebuilt or the pin moved.

**What was written here as impossible was wrong, and is now shipped.** The
claim was: an animated texture *inside* a preset cannot be done, because
libprojectM's extension list is hard-coded in `TextureManager.hpp` with no
`.gif` in it, textures resolve once when a preset loads, and no API hands a
live texture id to a sampler the preset names. The first two are true. The
third is not — `projectm_set_texture_load_event_callback` does exactly that.
See *Live textures* at the end of this file. Burn-in is still a separate
feature and still worth having: it does not need the preset's cooperation,
because it does not care what the preset asks for.


### Burning a picture into the preset — 2026-09-14

GIF, animated WEBP and APNG through Qt's own reader, honouring each frame's
own delay; video through ffmpeg on a thread of its own, paced with `-re`.
Four controls in the Overlay tab: the file, its size, where it sits, and how
hard it burns.

Two things the rig found that reading the headers never would have.

**`projectm_opengl_burn_texture`'s `left` and `top` are broken upstream.**
CopyTexture's vertex shader does `vec4(position, 0.0, 1.0) * vertex_transformation`
-- a row-vector multiply -- so with glm's column-major storage those two
parameters land in `gl_Position.w` and come out as a projective shear rather
than a move. Asking for a 173x173 patch 560px left of centre drew a trapezoid
across half the screen, which reads as our arithmetic being wrong. `width`
and `height` are on the diagonal and survive it, so the one call that behaves
is `(0, 0, whole window)` and `burn_canvas` does the placing here. Measured
before relying on it: the canvas's transparent surround **is** honoured, which
is the only reason a full-window burn does not wipe the preset's feedback
buffer every frame.

**The texture is upside down** relative to a QImage -- a GL texture's first
row is the bottom of the picture. The first run put the picture at the bottom
of the window when it had been asked for at the top.

And one correction to what was said when this was scoped: **an opaque burn-in
is not smeared.** It paints over its own warped trail every frame and simply
sits there, which is what a sprite already does better. The smear needs it
partially transparent, so there is a Burn slider, defaulting to 0.25. That
slider's effect could **not** be isolated on screen: both presets it was
tested against hold their feedback buffer near-permanently, so 0.05 and 0.30
measured 174 against 175 mean brightness -- which is also why the welcome
preset's logo never faded out of any of those screenshots. It is tested
deterministically at the canvas level instead; whether the look is right is a
judgement to make live against real presets.

Ten mutations, nine caught first time. The one that survived was a test that
could not fail: `video_frame_size` was checked against a line with no
size-shaped token in it at all, so dropping the "only read the `Video:` line"
guard changed nothing. Now checked against a `title: 1920x1080 remux` in the
metadata, which is exactly what that guard exists for.

### Animated textures *inside* a preset — the earlier answer was wrong

Scoping said this was impossible: libprojectM's texture extension list is
hard-coded, textures resolve once from disk when a preset loads, and no API
hands a sampler a live texture id. The first two are true. The third is not.

`projectm_set_texture_load_event_callback` (4.2.0, and **exported by both
shipped binaries** -- checked, no rebuild and no pin move) fires whenever a
preset asks for a texture the manager does not have, and it takes either raw
pixels **or an existing OpenGL texture id**. projectM wraps that id without
taking ownership -- its own comment says so -- and caches the wrapper, so the
preset samples *our* texture every frame it draws. Anything written into it
with `glTexSubImage2D` between frames is live inside the preset.

That is a genuinely different feature from burn-in: the preset's own shader
samples the picture wherever it names it, at whatever scale and wrap mode the
preset chose, rather than it being smeared into the feedback buffer. It also
makes the extension list moot, because the filesystem is never consulted for
a name the callback answers -- so `.gif`, `.webp`, `.tif`, `.svg` and
anything else Qt reads come along for free, with no patch to libprojectM.


### Live textures — built 2026-09-14

**What it is.** Any picture Qt reads and libprojectM does not
(`LIVE_IMAGE_EXTENSIONS`), plus video through ffmpeg, answered for by name
when a preset or a sprite asks for it. The animated ones keep animating while
the preset samples them.

**Sprites came free.** `MilkdropSprite.cpp` resolves `img=` through the same
`TextureManager::GetTexture`, so a GIF or an MP4 dropped in as a sprite
animates with no sprite-side code at all. That was not the plan; it was found
by reading how a sprite loads its picture, and then shot.

**Seen on screen, not inferred.** Three rig runs, each grabbed twice a few
seconds apart so the two frames prove movement rather than one frame proving
a decode:

| what | file | evidence |
|---|---|---|
| texture a preset samples | `livetest.gif` 480x480 | two different GIF frames, matching `ffmpeg`'s own frame 0 |
| sprite | `spr_rigtest.gif` 500x500 | two different frames, upright, square |
| sprite | `spr_vidtest.mp4` 640x360 | counter reads a different second, rainbow bar scrolled |

**Two rig traps, both costing a run each.** `soft_cut_duration` was **10.0**
against a `preset_duration` of 3, so every preset switch started a ten-second
blend that never finished and the screen was a permanent cross-fade — the new
preset appeared to not load at all. And `soft_cut_duration = 0.0` is worse
than a small value: nothing switched. Use **1.0**. Second trap: the welcome
preset is loaded before the library and stays until the first auto-advance,
so a grab taken too early shows the logo and reads as "the preset failed".

**Not flipped, unlike burn-in.** `TextureManager.cpp` never calls
`stbi_set_flip_vertically_on_load`, so every stock texture is top-row-first
and ours must be too. Burn-in needs the flip because it goes through
`projectm_opengl_burn_texture`, which is a different path.

**The header lies about ownership.** `callbacks.h` has an `@warning` saying
projectM takes ownership of a supplied texture id and deletes it.
`TextureManager.cpp` passes `false` and says the opposite in a comment;
`Texture::~Texture` honours the flag. So our textures are never given back
while projectM is alive — hence `LIVE_MAX_TEXTURES = 8` as a ceiling rather
than an eviction policy, and hence `_LiveTexture.stop()` deliberately leaving
the GL texture alone. A preset on screen still holds a `shared_ptr` to the
wrapper; deleting the id under it is a sampler reading a texture that is not
there.

**The size is pinned at the first frame.** projectM recorded width and height
when it wrapped the id and never asks again, so later frames are scaled to
that size rather than uploaded as they arrive. A GIF is allowed to change
frame size mid-animation, and `glTexSubImage2D` with the wrong one is a
corrupt texture, not an error.

**Only what projectM cannot read is claimed.** A `.png` still goes to its own
scanner: claiming it would hand a working texture to a second decoder that
also caps the long side at 512, for no gain. The decision is
`live_texture_file`, which is a pure function and is tested as one.

**A texture name is reduced to a basename** before anything looks at the
disk. Preset files are downloaded by the thousand; a name like
`../../../.ssh/id_rsa` must not walk out of the directories the user pointed
at.

**A video costs one synchronous ffmpeg probe** on the render thread, when the
name is first asked for. That is a fifth of a second, once, for a file
somebody deliberately put in a texture directory — and the alternative, an
answer that arrives two frames later, is a preset that loaded with a
placeholder where its texture should be.

**Replacing a live texture needs a restart.** `_do_reset_textures` leaves
them alone on purpose, for the ownership reason above. Replacing a stock
texture *with* a GIF does not need one, because that name has never been
answered for yet.

**What it does not do: fit the screen.** A preset samples the texture with
whatever UVs its shader writes, so a 16:9 video used as a full-screen texture
stretches exactly like any stock MilkDrop texture. Sprites *are* fitted —
projectM corrects aspect itself and the Size slider drives `sx`/`sy` — and so
is burn-in, which recomputes its rectangle from the live drawable size every
frame. Asked and answered 14 Sep 2026.


### Two visuals at once — built 2026-09-14

**What it is.** A second projectM instance in the same GL context, blended
over the first. Off by default. The key is `2`, `TAB` swaps which of the two
every control is driving, and the right-click menu carries both.

**How it draws.** Each instance renders into a colour texture of its own with
`projectm_opengl_render_frame_fbo`, then one shader pass samples both and
screens them: `1 - (1 - a) * (1 - b * strength)`. Screen rather than add,
because two bright presets added together clip to white and both shapes
disappear. `glBlitFramebuffer` cannot do this at all — it ignores blending —
which is why there is a shader here and nowhere else in the application.

**The bug that cost a run.** `projectm_opengl_render_frame` is
`RenderFrame(0)`: it targets framebuffer 0 whatever is bound when it is
called. Binding our framebuffer and calling it drew both visuals to the
window and the blend then painted two empty textures over the top. A black
window, no GL error, nothing in the log. `render_frame_fbo` is the one that
takes a target. `test_dual.py` now fails if the plain one comes back.

**`self._pm` is the focus.** Not a property and not a lookup — a plain
attribute pointed at whichever instance is being adjusted. That is what let a
hundred-odd call sites, the browser, the menu, the keys, the tweak panel and
the ratings all keep working untouched. Switching focus is two lines.

**What reaches both anyway:** the audio (a visual that cannot hear the music
is a still picture), the window size, and the auto-advance clock. Everything a
person does — next, previous, lock, the browser, the editor — goes to the
focused one alone.

**What is deliberately refused:** a thumbnail. The window is the two of them
mixed, and filing that under one preset's name puts a picture in the browser
that the preset cannot produce on its own. Asking for one while two are up
gets a toast saying so.

**Cost.** Roughly twice a single visual, which is why the second one has its
own quality setting and defaults to low. Both settings are in the Settings
dialog under the first visual's quality.

**Seen on screen in the rig, 800x600, four solid-colour presets:**

| what | evidence |
|---|---|
| two visuals blended | logo preset over a green preset; centre pixel matches the screen formula |
| the strength control | at 0.0 the corners are pure black and the logo is untouched; at 0.5 the field is green |
| the key toggles it | one `xdotool key 2` = one `[dual] on`/`off` line, and the instance is created and destroyed repeatedly without a crash |
| focus routes the controls | `RIGHT` changed only the green channel (the first visual); after `TAB`, `RIGHT` changed only the blue (the second) |

Measuring the blend by pixel rather than by eye is what made the last row
provable: solid-colour presets turn "which one moved?" into arithmetic.


### Windows caught up with 2.1.2 — 2026-09-14

The clone was already at `5ec553d`; nothing to merge. Twenty-two of the
twenty-four suites were green on the first run, and **both failures were real
findings rather than noise** — one a shipped bug, one a test that could only
ever have been right on one machine.

**A bare `glBindFramebuffer` on the dual teardown path, and Windows is where
it bites.** `test_dual.stopping_puts_everything_back` died with
`NullFunctionError`. PyOpenGL resolves an entry point on first use and raises
when it cannot, which on Linux is a silent no-op — the same split CLAUDE.md
already records for `glViewport`, except this one is in shipped code. The
route that reaches the line is the one that matters: `_render_visuals` calls
`_do_set_dual(False)` when the blend program will not build, so **the machine
with no framebuffer entry points is exactly the machine that runs it**, and
the exception kills the render thread — black window, no frames, and the log
ending on "could not build". Guarded with `if bool(glBindFramebuffer)`, which
is the idiom PyOpenGL's own error text names. `_Blender.drop()` and
`_LiveTexture.stop()` were checked alongside it and are already safe.

**The browser's toolbar width is a font measurement, and the suite does not
run in the app's font.** `test_browser_title` said the toolbar needed 818px
against a `WIDTH` of 610 — but the browser opens at **612** on this machine in
real life. The 818 comes from `QT_QPA_PLATFORM=offscreen` falling back to
"Sans Serif"; with the plugin unset the same row measures **599** in Segoe UI,
against Linux's 602. So a hand-measured constant was never going to be right
on both, and the check had only passed on Linux because that box's offscreen
fallback sits near its real font. The window now opens at
`max(WIDTH, bar.minimumSize().width())` — correct in any font — and what the
suite pins is the **button count**, which is the thing that was really being
watched and is the same number everywhere. Mutation-checked: deleting the
`max()` fails the suite.

**Driven on the real app, one feature per run, with the evidence measured
rather than eyeballed.**

| what | evidence on Windows |
|---|---|
| live GIF texture (sprite) | a colour-cycling GIF: centre pixel `0,180,0` then `252,0,0` 1.2s later |
| live video texture | `[texture] spr_vidtest 512x288 from spr_vidtest.mp4`; counter digit and colour bars both moved between grabs |
| sprite scaling | 0.15 draws a small square, 0.5 fills the window — responds to size, not broken |
| burn-in | `[burn] 512x288 at 156,135 587x330 of 900x600`, and **visible** against the welcome preset, correctly placed and the right way up, so the vertical flip holds here too |
| dual visuals | `[dual] on`, a second instance created (a second texture-path line), the second preset plainly blended through the first, no GL error |
| dual teardown | `[dual] off`, process alive, frames still coming, fps back to single-visual levels — i.e. the guard above is transparent when the entry point does resolve |
| tab-row lock and rating | the corner widget is on the tab row on every tab |
| number sliders | pinned from the Code tab, labelled `fDecay` from the setting name, "Clear sliders" appears |
| browser at 612px | ten buttons in one readable row, thumbnails on the rows, scroll handle visible |

Burn-in is invisible on most presets and that is not a Windows problem: the
burn goes into the framebuffer **before** the preset renders, so anything that
paints its whole canvas covers it. It took the welcome preset — plain black,
nothing over it — to see the picture at all. Linux said the same thing about
not being able to isolate the Burn slider.

**Three hazard classes checked and cleared** while looking for more of the
first finding's shape, since a Linux box cannot see any of them:

- both new `ffmpeg` subprocesses (the video probe and the decode) set
  `CREATE_NO_WINDOW` and resolve the binary through `find_ffmpeg()` — no
  console window flashing over a windowed build;
- the texture-load callback keeps its trampoline alive on `self._tex_cb`, with
  `argtypes`/`restype` spelled out, so there is no garbage-collected callback
  for C to fall into;
- `live_texture_file`'s containment is structural, not just the basename
  reduction: the path it returns is always `os.path.join(d, f)` with `f` taken
  from `os.listdir(d)`, so no name a preset supplies can escape whatever the
  separator rules are on the platform.

**`BomsAIVisualizer-2.1.2-win64-setup.exe`** — built from the tree at this
commit, 78.4 MB, sha256
`c3932b53fcb370851ed937988336244151ef4f5961960d4ab5eb94f210e119c1`.

Verified against the artifact, not the tree: both `VERSION` files say 2.1.2 and
so does the licence; seven licence files; both projectM DLLs and SDL2, no
`glew32.dll`, the `.scr` present; the bundled ffmpeg reports no `--enable-gpl`;
and the `testdata/` GIF and video used for the runs above are **not** in it,
which is the trap Linux paid for when four test GIFs nearly shipped inside
`assets/`. All fifteen markers of the new work — `live_texture_file`,
`_LiveTexture`, `projectm_opengl_burn_texture`, `burn_canvas`,
`projectm_opengl_render_frame_fbo`, `_Blender`, `_stop_dual`, `number_span_at`,
`slider_range`, `_hold_wallpaper_box`, `nemo_allow_desktop_window` among them —
are inside the frozen executable, and **none of the fifteen** is in the 2.1.0
executable beside it, which is what makes the first result mean anything. The Linux tarball in `installer/dist/` is still the 2.1.2 one built
from `4dc8258`, now five commits behind; that is Linux's to rebuild.

Not done: the Inno installer itself. Running it trips UAC and there is no
non-interactive elevation here, so a fresh install, the shortcuts and the
screensaver registration stay unverified — the payload is not.


### The freeze on closing a dialog — 2026-09-14

Reported from the installed 2.1.2 build: *"went to change preset duration, app
froze, had to force-close"*. It is neither preset duration nor the Settings
dialog. **Closing any window the visualiser owns can deadlock the
application**, and it has been able to since `keep_dialogs_above_visuals` was
written — this is not a 2.1.2 regression.

**What was measured, in order.**

1. Reproduced from source. `BOMSAI_FPS=1` stopped printing the moment OK was
   clicked and never resumed: the render thread was gone, not slow.
2. Narrowed by one variable at a time — it hangs with **nothing changed** in
   the dialog, and opening the dialog is fine. It is the close.
3. `faulthandler.dump_traceback_later(10, repeat=True, exit=False)` gave two
   dumps ten seconds apart that are **identical**:
   - main thread in `SettingsDialog.accept` → `super().accept()`
   - render thread in `viz_common.py:2097`, `projectm_opengl_render_frame`
4. The ownership was then read off a live process rather than inferred:

   ```
   SDL window   hwnd 1901934  — thread 6924
   Settings     hwnd 3146818  — thread 6760
   Settings GWLP_HWNDPARENT = 1901934
   ```

   A window on one thread, owned by a window on another.

**The mechanism.** Windows keeps an owned window above its owner, and when an
owned window closes it transfers activation to the owner — synchronously, on
the *owner's* thread. That thread is the render thread, which pumps only
between frames; the rest of the time it is inside projectM. The driver call it
is inside can itself wait on window state the Qt thread is holding, and then
neither side moves. Nothing is logged because nothing failed.

**The fix.** `_OwnedByVisuals` now clears `GWLP_HWNDPARENT` on
`QEvent.Type.Close` as well as setting it on `Show`. Which event to use was
measured, not guessed — on `accept()` the order is ActivationChange, **Close**,
WindowDeactivate, Hide, and only the first three arrive while the native window
is still visible, so `Hide` (the obvious choice) is already too late. Setting
an owner is safe: it is one call on our own window and the owner's thread is
never consulted. It is only the close that is dangerous, so the relationship
now lasts exactly as long as it is useful.

**What is verified, and what is not.** Verified on the real app: the dialog is
still owned by the visualiser while it is up (`GWLP_HWNDPARENT` reads back as
the SDL window), a click on the visuals still leaves the dialog on top — which
is the whole reason the filter exists — and twenty-four suites pass.

**Not verified: that the hang is gone.** It reproduced 3/3 by hand and then
**0/20 scripted** — quick, with a fifteen-second dwell, with the value actually
changed, under load, from source and from the installed build. It needs the
render thread to be inside a slow frame at the instant of the close, which
depends on which preset happens to be up. So the fix removes the mechanism that
the stacks show, and nobody should read a run that did not hang as proof.
If it ever recurs, the first move is the faulthandler wrapper again: a build
with `MILKDROP_CONSOLE=1` plus `dump_traceback_later(10, repeat=True)` names
the two parked threads in one shot.

Two probes that lied on the way past, both mine rather than the app's: a
liveness check that hashed the window bitmap called a **fresh install
"static"** because the welcome preset is a still picture and 11,223 presets
were still loading; and a stand-in Qt harness reported the dialog unowned
because an application event filter's deferred `singleShot(0)` had not run yet
and because filters run in **reverse** install order. The real app answered
both questions correctly and immediately. Test the app, not a model of it.

**2.1.3, because the fix has to reach the machine that reported it.** The
2.1.2 installer was built at 18:34 and the fix landed at 19:42, so the package
on disk — and the copy installed from it — did not contain it. Proved against
the artifact rather than the timestamps: the 2.1.2 executable carries the old
`_own` and neither `_set_owner` nor `disown`, and the 2.1.3 one carries all
three.

The number moved rather than rebuilding 2.1.2 in place, because two different
executables under one name is exactly what this file already records going
wrong with the stale 2.0.0 installer — and the Inno `AppId` does not move, so
the new package upgrades the installed one. `VERSION` is shared, so Linux
inherits 2.1.3; nothing else about it is Windows-only.

**`BomsAIVisualizer-2.1.3-win64-setup.exe`**, 78.4 MB, sha256
`0e4c3dc6de28438b16d3f7ed95be42f68f0e324d0aa2de9d015f7f73ee0e96fe`. Both
`VERSION` files and the licence read 2.1.3. Building it also emptied
`installer/dist/`, which took the stale 2.0.0, 2.1.0 and 2.1.2 packages with
it — the one-package-per-directory rule now holds on Windows by accident
rather than by hand.

### Linux caught up with 2.1.3, and two bugs fell out of driving it — 2026-09-14

The app was driven in the Xephyr rig rather than read, for a sweep and for
fresh site screenshots. Two defects came out of it that no suite had, and
both were of the same shape: a feature quietly stops working and nothing says
why.

| Symptom on screen | What it actually was |
| --- | --- |
| Pressing the snapshot key toggled hard cuts | Two actions on "c". `load_keymaps_from` merges keymaps.json **over** DEFAULT_KEYMAPS, so an action added later can land on a key an older saved file already spent. The handler is one elif chain, so the earlier branch wins and the dialog still shows the later one as bound. |
| The second visual disappeared, log said only `[dual] off` | One frame with a zero drawable size. `_blend.ready` answers false for `w <= 0` as well as for a blender that failed, and the caller treated both as failure and called `_do_set_dual(False)`, which is permanent. |

The first was found by pressing a key and reading the log; the second by
noticing that `[dual] off` had arrived **without** the `[dual] <why>` line
that a real blender failure prints first. Both are fixed in `9b62fff`, with
`test_keymaps` new (the collision rules, the dialog, a corrupt file, and the
invariant that no two shipped defaults share a key) and `test_dual` extended.
Twenty-five suites now.

Two things the rig cannot tell you, recorded so they are not chased again:

- **Under Xephyr there is no GPU.** Mesa falls back to llvmpipe, and two
  projectM instances at *high* quality on a 1920x1080 window is 610% CPU and
  well under a frame a second — the window goes black and stays bit-identical
  for minutes while the log keeps advancing presets. That is not a hang and
  not a bug; `dual_quality` defaults to low for exactly this reason. Read the
  thread states (`/proc/<pid>/task/*/comm`) before believing a freeze.
- **The repo's own `keymaps.json` is not the installed one.** A source
  checkout reads the file beside the code; an installed build reads
  `~/.config/BomsAIVisualizer/keymaps.json`. They have drifted, and the repo's
  is the older. Which one is in front of you changes what the keys do.

**`BomsAIVisualizer-2.1.3-linux-x86_64.tar.gz`**, 86 MB, sha256
`e2bc3490bbf9672d6f45033cb117afaed12c4d1dd600c1d8d879809c51a239f2`.
Built in the almalinux:9 container from the clean tree at `9b62fff`; highest
glibc symbol required **GLIBC_2.34**. Verified against the artifact in a stock
`ubuntu:24.04` with only `libgl1 libglib2.0-0t64 libegl1` installed: exit 0
from `--help`, the `sounddevice unavailable` line printed, `VERSION` inside
reads 2.1.3. The frozen archive was inflated (688 zlib streams) and carries
`_release_elsewhere`, `has no key` and `is already` — the strings the keymap
fix added — with two never-written control strings absent. The 2.1.2 tarball
and its extracted tree were deleted; one package per directory.

### Windows caught up with 2.1.3, and driving it found one more — 2026-09-15

The two Linux commits (`9b62fff`, `b7033ed`) landed here clean: all
twenty-five suites pass on Windows, and the keymap fix is verified against
this machine's own state rather than only in a suite. This repo's
`keymaps.json` has the exact collision Linux described — `toggle_hard_cuts`
on "c", which `snapshot` defaults to — so a source run now prints
`[config] snapshot has no key: c is already toggle_hard_cuts in keymaps.json`
and `snapshot` comes up unbound, with no duplicate live binding left in the
table. The installed build's `keymaps.json` (`%APPDATA%\BomsAIVisualizer`)
has no collision and keeps all four later defaults — `toggle_on_top` "t",
`snapshot` "c", `toggle_dual` "2", `swap_focus` TAB.

**The two 2.1.3 packages were not the same application, and the version
number hid it.** The Windows `2.1.3-win64-setup.exe` was built from
`4d43d43`, which predates both of Linux's fixes; the Linux tarball of the
same version was built from `9b62fff`, which contains them. Nothing about
either artifact says so. It is rebuilt from the clean tree now, but the
general point is worth keeping: **a version bump has to happen before the
second platform builds, not after**, or the number stops identifying the
code. That is why the fix below took 2.1.4 rather than a second 2.1.3.

#### The preset browser can be lost for the session

Found by driving the shipped build rather than by reading it, and it is not
Windows-only — the semantics are Qt's — but it needs a real window manager,
so no offscreen suite could have produced it.

| What the user does | What happens |
| --- | --- |
| Minimise the preset browser with its own titlebar button | `vis=True min=True` — Qt still considers the widget shown |
| Pick "Preset browser" from the right-click menu | The toggle reads `isVisible()` as True, calls `hide()`. Window leaves the taskbar: `vis=False min=True` |
| Pick it again. And again. And again | `vis=False min=True` every time. It never comes back |

`isVisible()` is True for a minimised window because minimised is a window
*state*, not a visibility — and `show()` restores a window to the state it
still holds, which is the minimised one. So the entry appears to do nothing
for the rest of the session. `App._toggle_browser` now asks
`isVisible() and not isMinimized()`, and un-minimises with `showNormal()`
**only** on that branch: `showNormal()` clears maximised as well, and an
ordinary hide/show must not un-maximise a window somebody maximised.

**That fix on its own hung the application** — this half *is* Windows-only,
so Linux needs the `main.py` change and gets the rest for free
(`keep_dialogs_above_visuals` returns None off win32). The hang is the entry
already in CLAUDE.md wearing a different hat: the browser is owned by the SDL
window, whose pump is the render thread, so un-minimising it hands activation
across threads exactly the way closing it does. Measured against a source
run — `showNormal()` never returned. The instrumented line before it printed
and the one after it did not; the renderer stopped logging at the same
instant; the window sat at `0,1004 160x28`. A probe with the same sequence
but an owner on the *calling* thread restores correctly every time, which is
what places the blame.

So `_OwnedByVisuals` now watches `WindowStateChange` too and hands the owner
back the moment a window is minimised — immediately, not deferred, for the
same reason its `Close` branch is not deferred. **And that still was not
enough**: clearing the owner of a freshly minimised window makes Windows show
it again, Qt delivers a `Show` with `isMinimized()` still true, and the
`Show` branch owned it straight back one line later. Caught by tagging each
ownership change with the branch that asked for it:

```
_set_owner(show)      own=True  -> 1573716
WindowStateChange min=True
_set_owner(state-min) own=False -> 0
Show min=True                              <- Windows re-shows it
_set_owner(show)      own=True  -> 1573716 <- and the disown is undone
```

The refusal therefore went into `_set_owner`'s own guard
(`or w.isMinimized()`), where no future branch can get round it. Verified end
to end on a source run: opened at `647,175 626x659`, minimised to `owner=0`,
and the menu entry brought it back at `647,175 626x659` with the owner
restored, painting, following the current preset — and the render thread went
on advancing.

`test_browser_toggle.py` is new: it pulls `App._toggle_browser` out of
`main.py` and the `_set_owner` guard out of `viz_common.py` with `ast`, runs
them against fakes with Qt's semantics — including the already-broken
hidden-and-minimised state anyone who hit this under 2.1.3 is sitting in —
and asserts the filter disowns on the way down. All four mutations of the fix
are caught (the guard, the disown, the old condition, plain `show()`).
Twenty-six suites now.

#### The animated-media claims, checked on Windows

The site lists moving textures and video, so each was put through the real
app on this machine rather than taken from the suites:

| Claim | How it was checked | Result |
| --- | --- | --- |
| A GIF as an overlay, spinning | `gif-7.gif` added through Add a picture…, Movement → Spinning | On screen, rotating, over the preset |
| An **MP4** as an overlay | `testdata/vidtest.mp4`, Movement → Still, five grabs 1.2 s apart cropped to the countdown digit | Four distinct hashes and the fifth equal to the first — it plays *and* loops |
| An animated **WebP** as an overlay | `gif-3.gif` re-encoded to `webp_anim` 500x281 with the bundled ffmpeg, five grabs 1.1 s apart cropped well inside the opaque overlay rectangle | Five distinct hashes |
| A GIF **burnt into** the preset | `gif-1.gif` chosen as the burn-in, then turned Off on the same preset | A/B: magenta-and-yellow flower with it on, black-and-white radial with it off. The preset's warp is taking up the picture, which is the effect |

`.apng`, `.tif` and `.svg` are in `LIVE_IMAGE_EXTENSIONS` beside `.webp` and
go through the same `QImageReader` path, which is the one now exercised, but
none of the three was driven for want of a file. Live textures *inside* a
preset are covered by `test_live_textures` and were not driven by hand
either. The seven GIFs used here are the user's own, in `C:\Projects\gif
images` — the texture pack's only animated files are a dodecahedron on an
opaque white background, a 32px dark blob, and a static optical illusion, and
the first of those puts a white box on screen that reads as a defect.

#### The site's Windows screenshots

`bomsaisoftware.com/software/milkdrop-visualizer/` pairs each Linux shot
with a `win-` counterpart. Three had no Windows half at all — **dual**,
**code-editor** and **overlay-animated**, which are exactly the 2.1
features — and three more were taken on 8 Sep and no longer show the
product: the context menu had no "Add a second visual", the overlay tab had
no burn-in section, and Settings had no second-visual rows. All nine
(those six plus tweak-panel, search and key-bindings, which had grown a
toolbar button and four actions) were retaken from the frozen build and left
in `C:\Projects\pics for site` under the site's own `win-` names.

`win-collections` was left alone deliberately: its alt text says "before any
collection has been made", which is a real first-run state and pairs
usefully with the populated Linux one. `win-browser-by-rating` was left
alone too — the existing shot has ratings and thumbnails from real use, and
a scripted session can only produce a thinner one.

**`BomsAIVisualizer-2.1.4-win64-setup.exe`**, 78.4 MB, sha256
`afe16d4c0f4ca5c654a16af0c0f689763bae6aa4036539db3554c52dc44eb9af`. Built
from the clean tree at `f3d0d13`; `VERSION` in the portable tree reads 2.1.4
and the bundled EULA agrees. The frozen exe was inflated (691 zlib streams)
and carries `isMinimized`, `showNormal` and `WindowStateChange` — the fix
adds no new string literals, so what is searched for is the attribute names,
which marshal into the code objects' `co_names` — plus `has no key` and
`_release_elsewhere` from Linux's keymap work, with two never-written control
names absent. Smoke-tested from the portable tree: it renders (two grabs 1.5 s
apart differ), the minimise round restores at `647,175 626x659` with the
owner back, and the process is responding afterwards. The 2.1.3 exe and its
extracted tree were deleted; one package per directory.

**Still not verified: the Inno installer itself.** Running it trips UAC and
there is no non-interactive elevation here, so what is checked is the payload
through the portable tree, not a fresh install, its shortcuts, or the
screensaver registration. Unchanged from every previous Windows package.

### 2.1.5 — a polish pass found one a buyer could be stopped by — 2026-09-15

Three fixes and a clean-up, none of them on Windows-only ground, so there is
nothing to port: Windows needs a rebuild at this number and no code changes.

- **The Settings dialog could not be made shorter than about 860px.** A
  `QFormLayout` set straight on a `QDialog` makes the form's height the
  window's minimum, and a window manager then refuses to shrink it — so on a
  1366x768 laptop, the smallest screen the Microsoft Store accepts a
  screenshot at, OK and Cancel sat below the bottom edge with the preset
  folder a new install has to set inside the dialog they could not finish.
  Rows in a `QScrollArea`, buttons outside it, and the window sized off the
  form because a scroll area's own size hint ignores its widget. `84c2c7e`.
- **Preset names in the browser stepped 96px left and right.** Qt indents a
  row that carries an icon and draws one without it flush against the edge,
  and most presets in a fresh library have no thumbnail yet. Rows without one
  now hold a shared transparent pixmap. Two smaller things fell out of it: a
  thumbnail taken after you scrolled past its row never appeared, and rows
  revealed by dragging the window taller were never asked for pictures at all.
  `394725b`.
- **A recording that lost its sound said nothing.** `write_audio` swallowed
  every wave error, so a full disk produced a silent clip with nothing in the
  log. `79115fc`.
- Dead bindings removed and the stale `store/demo.mp4` deleted. `192c70a`.

All three were found by driving the shipped build under xfwm4 in Xephyr, not
by reading code. CLAUDE.md carries what each one taught.

**`BomsAIVisualizer-2.1.5-linux-x86_64.tar.gz`**, 86 MB, sha256
`afc4fb5d80e50edc0f8e62a8024a3b22fc24efbcec42022347ffdb078a6885ec`. Built in
`almalinux:9` from the clean tree at `79115fc`; highest glibc symbol required
is **GLIBC_2.34**, unchanged. `VERSION` inside reads 2.1.5 and the bundled
EULA agrees. The frozen archive was inflated (668 zlib streams out of the
executable — the app's own modules are in the PKG appended to it, *not* in
`_internal`, so a byte search of the unpacked directory finds nothing) and
carries `_blank_icon`, `_screen_height`, `_load_visible_icons`,
`setWidgetResizable` and the string `sound stopped`. Three never-written
control names are absent, one of them `_fit_to_screen` — a method that existed
in an intermediate draft of this same session's code and was renamed before
the commit, so its absence proves the build is the final tree and not a stale
one. The 2.1.4 tarball was deleted; one package per directory.

**Run twice in stock `ubuntu:24.04`, and the container needs a display.** The
2.1.4 entry above does not say so and it matters:

- `--help` **exits 0** with the usage text and the `sounddevice unavailable`
  line, on a container with only `libgl1 libglib2.0-0t64 libegl1`. Interpreter
  up, bundle's libraries resolving.
- A plain start is **still running when `timeout` kills it at 20 s — exit
  124**, with nothing worse than the `sounddevice` line and the fontconfig
  warning a bare container always gives. This one needs `libxcb-cursor0` and
  `libxkbcommon-x11-0` on top, and the host's X socket passed in
  (`--net=host -e DISPLAY=:0 -v /tmp/.X11-unix:/tmp/.X11-unix:ro` plus the
  Xauthority). Without a display it aborts with **134** on `qt.qpa.xcb: could
  not connect to display`, which reads exactly like a crash in the build and
  is not one — an hour went into that today.

Take the app's own status either way. A piped run reports the *last command in
the pipe*.

### Linux caught up with 2.1.4 — 2026-09-15

Nothing to port: `f3d0d13` fixed both halves of the minimised-browser bug in
one commit, and the Qt half of it (`isVisible()` is true for a minimised
window) is not Windows-specific. Twenty-six suites pass here, `check_sync.py`
is clean, and the Player has the same `viz_common.py` at `b9cb9f3`.

**`BomsAIVisualizer-2.1.4-linux-x86_64.tar.gz`**, 86 MB, sha256
`0560f2df3ef3c0e71cd72dda5ea61f63bf0351a464917513fdac07756500ca12`. Built in
`almalinux:9` from the clean tree at `3d6e8b7`; highest glibc symbol required
is **GLIBC_2.34**, which is the oldest system it will start on. `VERSION`
inside reads 2.1.4 and the bundled EULA agrees. The frozen archive was
inflated (688 zlib streams) and carries `isMinimized`, `showNormal` and
`WindowStateChange` — attribute names, not string literals, because the fix
writes no new strings — plus `has no key` and `_release_elsewhere` from the
keymap work, with two never-written control names absent, which is what makes
the probe worth anything. The 2.1.3 tarball and both extracted trees were
deleted; one package per directory.

Run in stock `ubuntu:24.04` with only `libgl1 libglib2.0-0t64 libegl1`
installed, twice, because the two runs answer different questions:

- `--help` **exits 0** with the usage text and the `sounddevice unavailable`
  line. That proves the bundle's libraries resolve and the interpreter comes
  up, and it is the check every previous entry here records.
- A plain start is **still running when `timeout` kills it at 20 s — exit
  124**, with nothing worse than the `sounddevice` line and the fontconfig
  warning a bare container always gives. `--help` short-circuits before the
  window, so on its own it would not notice a build that dies while opening
  one. 124 is the number being read, not merely "not a crash": it only
  happens if the process was alive at the end of the window.

Take the app's own status either way. A piped run reports the *last command
in the pipe*, so `./BomsAIVisualizer ... | head` says 0 whatever the app did
— the first attempt at this check was written that way and had to be redone.

The Inno installer's Linux counterpart, `install.sh`, is not exercised either
— what is checked is the payload, not the desktop entry or the uninstaller.

### Windows caught up with 2.1.5, and a polish pass took it to 2.1.6 — 2026-09-15

Pulled `d4a52d5`, ran the suites, then drove the source build for an afternoon
looking for what a buyer would hit. Twenty-six suites pass here and
`check_sync.py` skips — there is no Player clone on this machine, so the Linux
side still owns keeping `Bomsai Player/visualizer/viz_common.py` in step, and
this session changed `viz_common.py`, so **it has drifted again**.

**Why 2.1.6 and not 2.1.5.** Linux has already built, verified and recorded a
2.1.5 tarball. Two of the fixes below are in `viz_common.py`, which both
platforms ship, so a Windows package called 2.1.5 would be *different code*
under the same number — which is exactly what forced 2.1.4 a day ago, and the
number is the cheap half of that trade. Linux needs a pull and a rebuild at
2.1.6; there is nothing for it to port, only to rebuild.

#### What Linux sent over, checked on Windows

All four of Linux's 2.1.5 changes behave here:

- **The Settings dialog shrinks.** Dragged to 413px tall under the real window
  manager: the rows scroll and OK and Cancel stay put. The `QScrollArea`
  rewrite needed nothing Windows-specific.
- **The browser's names line up.** Verified against the real library, where
  most rows have no thumbnail yet: the blank pixmap holds the column open, and
  dragging the window taller fills in pictures for the rows that appear —
  which is the `resizeEvent` half of `394725b`.
- **A clip carries its sound.** Recorded nine seconds with a two-tone signal
  playing: 188 frames, an AAC stream at 48 kHz, and a Goertzel over one second
  of it reads 440 Hz strong, 660 Hz at half that, and nothing at two control
  frequencies. The `write_audio` change reported nothing, which is what it is
  supposed to do when there is nothing wrong.
- Dead-binding removal: pyflakes over the tree here is clean apart from the one
  deliberate `# noqa: F401` availability probe, same as Linux reports.

#### What driving it found

- **Settings opened four pixels short of its own rows**, so the dialog that
  fits came up with a vertical scroll bar down every row of it, with a scroll
  range of exactly 4. `form.minimumSizeHint()` is measured inside `__init__`,
  where the `QFormLayout` has not been activated and answers 782 for a form
  that settles at 786. `layout.activate()` first. `ensurePolished()` does not
  do it; only `activate()` does, and that was measured rather than assumed.
  The gap is font-sized — 8px under the offscreen fallback — so the suite pins
  the *relationship* (what the dialog read equals what the layout settles on)
  and not a pixel. Verified to fail with the fix taken back out.
- **Every note in the Key bindings table was cut off mid-word**, including the
  lock one, which is the only place the app says that next and previous still
  work while a preset is locked. Two causes, both measured: the window opened
  at a hard-coded 560px, and Action and Notes were both `Stretch`, which
  splits the leftover evenly and so gave each 179px against the 237 and 359
  they ask for. Action is `ResizeToContents` now and the width comes from
  `sizeHintForColumn`, which answers before the window exists. The width adds
  the frame **four** times rather than twice because `dialog_qss` styles
  `QAbstractItemView` — a `QTableWidget` is one — so the sheet's
  `padding: 4px 6px` makes `frameWidth()` read 7 where the style says 2, and
  Qt then loses that padding again out of the stretch column: viewport 726,
  sections 712, against a gap of 0 for a bare table under the same sheet.
  Three mutations of the fix are each caught by `test_keymaps`.
- **The shipped Windows README described a 1.x product.** It said in as many
  words that clips are silent — the feature shipped in 2.0 and the Linux
  README was updated for it, this one never was — and its controls list was
  missing six keys (`t`, `x`, `c`, `d`, `2`, tab). Two of the things the store
  listing sells, two visuals at once and the preset panel, were not mentioned
  at all. Corrected, completed, and kept pure ASCII: Inno reads
  `InfoAfterFile` as ANSI without a BOM, and the file had no non-ASCII
  character in it before this.

  **The Linux README's controls list is four keys short of the same thing**
  (`t`, `c`, `2`, tab), and those four are in `DEFAULT_KEYMAPS` on both
  platforms, so they were added there too. Normally that would not be touched
  from here — changing a shipped file behind a package that has already been
  built and recorded is how two builds end up with one number — but Linux has
  to rebuild at 2.1.6 regardless, so there is nothing to invalidate. Revert it
  if you would rather own that file yourself.
- **The rename left debris that an upgrade never cleans up.** The AppId is
  deliberately stable, so Inno replaces the install rather than sitting beside
  it — but it only removes what it is installing now, under the names it is
  installing now. Measured on this machine: a dead 5.6 MB
  `MilkDropVisualizer.exe` beside the new executable, and four shortcuts in a
  Start Menu group still called "MilkDrop Visualizer", two of them for a
  product that no longer exists and one of those pointing at that dead exe.
  `[InstallDelete]` removes them and `UsePreviousGroup=no` moves the group;
  the install *directory* is deliberately left where it is, because a new one
  would orphan the old with the uninstaller inside it. **Not verified** — the
  installer needs a UAC click, which is the same gap every entry here records.
- **PyAudioWPatch ships and was not named in the licence notice.** It is the
  Windows capture backend — `_portaudiowpatch.pyd` is in the installed tree —
  and it is **Apache-2.0**, whose section 4(a) asks for a copy of the licence
  to travel with the distribution. Added as `PyAudioWPatch-Apache-2.0.txt`,
  and the notice's PortAudio line corrected: on Windows PortAudio arrives
  inside PyAudioWPatch, not via sounddevice. Left open on purpose, because it
  is a decision about what to ship rather than a defect to fix quietly: the
  notice names SDL2, PyOpenGL, PortAudio and NumPy without carrying their
  texts, and Python's own PSF licence is not mentioned at all.

#### One freeze, observed once, not explained

The app froze while opening Settings from the right-click menu with the preset
browser already open: `Responding` false, CPU flat at 477s across two samples,
the render thread stopped advancing presets one change after the click, and no
dialog ever reached the screen. It fits the owned-window family CLAUDE.md
already carries two entries for — showing a window owned by the SDL window
moves activation, and Windows moves activation on the owner's thread, which
here is the render thread — and "another owned window already showing" would
explain why it had not happened before.

It did **not** reproduce in 20 scripted rounds of that exact sequence, and a
second candidate cannot be ruled out from one occurrence: the driving harness
calls `AttachThreadInput`, which Microsoft documents as able to hang the
caller. Nothing was changed for it. Loosening the ownership to chase an
unreproduced freeze at release time is how the dialogs end up behind the
visuals again, and the fix for the two known triggers is measured and in.
What is written down instead is how to catch the next one: run the app through
`scratchpad/fh.py` and the dump names the line in both threads.

A harness trap fell out of it and is in CLAUDE.md: `GetWindowTextW` is a
`SendMessage`, so a window-listing helper that reads titles blocks forever on
a hung app and reads as a broken script rather than a stuck program. Ask
`(Get-Process ...).Responding` first.

#### Also checked, no change needed

Wallpaper mode still enters and leaves cleanly on 11 Pro 26200: the SDL window
lands as a layered child of the Progman WorkerW at 0,0 1920x1080 with
`WS_CLIPCHILDREN` set on the host, the desktop icons composite above it, the
tray icon opens the same menu with "Leave wallpaper mode" in it, and the
window comes back to 59,29 902x632 exactly where it left. The screen still
cannot be photographed there — two grabs five seconds apart were
byte-identical at mean brightness 0.9, and the user's own wallpaper read 9.2
in the same rectangle the moment the mode was left — so *that it is covering
the desktop* is measured and *that it is painting live* still needs an eye.

Fullscreen sits at -2,-2 1924x1084 and the context menu draws over it.
Borderless, always-on-top, two visuals, tab between them, the four browser
tabs, Get Presets, Customize menu, About and every submenu were opened and
read. The light theme is correct across the menu, the browser, the panel and
all three dialogs; the greyed labels on preset-driven sliders are legible in
both themes.

Forty-two new thumbnails landed in the user's real
`%LOCALAPPDATA%\BomsAIVisualizer\thumbs` while driving. Left there on purpose
— they are pictures of their own presets and the app exists to collect them —
but it is worth knowing that a source run on Windows writes to the same data
directory a frozen one does, because `user_data_dir()` reads `LOCALAPPDATA`
whatever `APPDATA` is pointed at.

#### The verified 2.1.6 Windows package

**`BomsAIVisualizer-2.1.6-win64-setup.exe`**, 78.4 MB (82,217,716 bytes),
sha256 `ccb9bc9794ae211103bf97f62a299513a217ef6c82a8e84b07cdfa66ac3339db`.
Built from the clean tree with the 2.1.5 installer deleted first — one package
per directory, same rule as the Linux side.

Checked, in this order:

- **`VERSION` inside reads 2.1.6 and the bundled EULA agrees.** Both build
  scripts refuse to run otherwise, so this is belt and braces, but the EULA
  line is prose that nothing reads back and it has drifted before.
- **The frozen executable carries the fixes.** 691 zlib streams inflated out
  of the PYZ — the app's modules are one stream each inside the PKG appended
  to the exe, so nothing greps it — and it holds `sizeHintForColumn`,
  `ResizeToContents` and `activate` from this session, plus `_blank_icon`,
  `_screen_height`, `setWidgetResizable` and the string `sound stopped` from
  Linux's 2.1.5. These are attribute names rather than string literals,
  because neither fix writes a new string. Three names that are nowhere in
  this repo are the control, and none of them matched, which is what makes
  the rest worth anything.
- **`licenses/` carries the new `PyAudioWPatch-Apache-2.0.txt`**, and the
  staged `README.txt` is the corrected one — the sound line, the six missing
  keys and the two new sections are all in the package, not just the repo.
- **The bundled ffmpeg is LGPL**: `--disable-libx264`, no `--enable-gpl`, read
  off the binary in the package rather than trusted from the build log.
- **The portable tree runs.** Started with `APPDATA` and `LOCALAPPDATA`
  pointed at a scratch profile: alive and `Responding` after 100 seconds, a
  window at 199,119 802x532 with a preset drawing in it. Key bindings opens at
  800px with every note readable and shows `DEFAULT_KEYMAPS` (RIGHT / LEFT /
  SPACE / r / h), which is what a fresh install gets; Settings opens with no
  scroll bar. Both fixes are therefore verified in the artifact and not only
  in source. The scratch profile collected twelve thumbnails, a config and the
  AMD GL cache, and the user's own `%APPDATA%\BomsAIVisualizer` was still
  dated before the session started — which is the check that the isolation
  held, and the one that failed a session ago.

#### The installer is verified now — 2026-09-15, and it took one click

Every entry above this one says the Inno installer is unverified because it
needs a UAC prompt and there is no non-interactive elevation here. Adam ran it
by hand and accepted the prompt, so this is the first entry that can say what
an install actually does.

**How to check one, in the order worth doing it:**

1. **Diff the installed tree against the portable tree the same build
   produced.** This is the whole check, and everything else is a detail: walk
   both, compare relative path and size, and the only difference should be
   Inno's own `unins000.exe` and `unins000.dat`. A file in the install that is
   not in the package is **left over from an older version** — Inno removes
   only what it is installing now, under the names it is installing now.
2. The Start Menu group: its *name*, and the target of every `.lnk` in it.
3. The Add/Remove Programs entry — `DisplayName`, `DisplayVersion`,
   `Publisher`, `InstallLocation` — out of
   `HKLM\...\CurrentVersion\Uninstall\*`.
4. `HKCU\Control Panel\Desktop\SCRNSAVE.EXE`, if the screensaver task was
   ticked.
5. Start it from the Start Menu shortcut, not from the folder.

**What the 2.1.6 install came back with.** 270 package files, all 270
installed, no size difference on any of them. `MilkDropVisualizer.exe` gone.
The "MilkDrop Visualizer" Start Menu group gone entirely — `dirifempty` took
the folder once its four shortcuts were cleared — and a "BomsAI Visualizer"
group in its place holding exactly two, both pointing where they should.
Add/Remove reads BomsAI Visualizer 2.1.6, BomsAI Software. The screensaver
value still points at `BomsAIVisualizer.scr`. So `[InstallDelete]` and
`UsePreviousGroup=no` both did what they were written to do.

**And step 1 found two more.** The install carried `app/_internal/glew32.dll`
(506 KB) and `licenses/GLEW-LICENSE.txt`, neither of which is in the package.
GLEW was dropped when the projectM pin moved — the entry in CLAUDE.md about
`_init_glew()` becoming a startup failure is the same event — and every
upgrade since has carried both files forward. Nothing loads the DLL any more,
so it is dead weight rather than a hazard, but a licences folder naming a
component that does not ship is the kind of thing that folder exists to get
right.

Fixed by clearing `_internal` and `licenses` **wholesale** in
`[InstallDelete]` rather than by naming the two files. Both directories are
entirely ours — PyInstaller owns one, the build script fills the other — so
anything in them the package no longer carries is by definition left over,
and naming things only ever catches the ones somebody remembered. The
installer was rebuilt with that in; the exe recorded above was superseded
before it went anywhere, and its sha is the one in this entry.

The install directory is still `C:\Program Files\MilkDrop Visualizer`, which
is cosmetic and deliberate: `UsePreviousAppDir` stays on, because a new
directory would orphan the old one with the uninstaller inside it. A clean
uninstall and reinstall is what moves it, and that is Adam's call rather than
something an upgrade should do behind him.

Inno also prints a warning that predates this session and is worth knowing
about: `PrivilegesRequired=admin` with `HKCU` writes in the script. The
screensaver keys go to whichever user's hive the elevated installer runs as,
which on a one-account machine is the right one and on a shared machine is
not. Not changed here; it is a design decision about who the screensaver
setting belongs to.

### 2.1.7 — the second visual became a pair, and got its own screen — 2026-09-15

Adam's call, after playing with 2.1.6: the "Second visual" toggle was the
wrong shape. Visual 2 was a thing you added on top of visual 1, and with a
fader between them that is no longer what it is — either of the two can be the
one on screen. Four changes, all in `viz_common.py`, and the Windows build at
2.1.6 needs a rebuild to match.

**Two switches instead of one toggle.** The right-click menu carries
`⧉ Visual 1` and `⧉ Visual 2`, both checkable, and whichever is left on its
own is greyed out — both off is a black window with the menu that would undo
it drawn over the top. `Renderer.request_visual` refuses it a second time, for
the key binding that has no checkbox to grey. Visual 1 switching off does
**not** destroy the primary projectM: that instance owns the playlist, the
ratings and everything the browser talks to, so it stays and simply is not
drawn, which is where the whole cost was anyway.

**A crossfader, in the menu.** It is the one control here you want to move
while watching what it changes, so it is a slider on the menu rather than a
number behind Settings — same shape as the audio sensitivity row, save timer
included, because `sliderReleased` does not fire for a click on the groove.
`[` and `]` move it by 0.05 a press.

The shader had to change to do it. Until 2.1.6 it scaled only the second
visual — `1-(1-a)(1-b*strength)` — which reaches "visual 1" and "both at once"
and can never reach "visual 2 alone", so the right-hand half of a crossfader
would have done nothing. It weights **both** now,
`1-(1-a(1-x))(1-b·x)`, which is exact at either end and still screens in the
middle, so two bright presets cannot clip to white. A visual the fader has
faded out is not rendered at all, which makes parking it at one end cost one
visual rather than two; the price is that it stops advancing while it is off,
which is what "off" should look like.

`dual_opacity` became `dual_crossfade`. The old key is still read when the new
one is missing: the two agree at 0 and in the middle, and the middle is where
an untouched config sits, so carrying it over beats snapping every upgraded
config back to 0.5.

**Send visual to.** `visual2_display` names a monitor; visual 2 then gets a
borderless SDL window of its own on it, showing visual 2 alone, letterboxed
rather than stretched — the two targets are built at the main window's size,
and a second monitor of another shape would otherwise pull the picture out of
shape. One GL context serves both windows (`SDL_GL_MakeCurrent` moves it), so
there is no sharing to set up and every texture and framebuffer is already
visible to both. Visual 2's window is drawn and swapped **before** the main
one, and the context is handed back, because the recorder and the thumbnail
both read the main window's back buffer. Pointing both visuals at one screen
is the blended pair said the other way round, and is treated as such.

Two window-manager traps came out of this and are in CLAUDE.md: a reparenting
WM ignores `SDL_CreateWindow`'s position for a borderless window, and SDL
drops the obvious correction because it matches its own cache.

**Settings.** "Second visual quality" and "Second visual strength" are gone.
The strength is the fader; the quality is the ordinary Quality row, read
against whichever of the two `Visual 1` / `Visual 2` boxes is ticked. One pair
of boxes doing both jobs — a tick switches that visual on *and* points the
rows at it, which is the same thing you meant either way.

**Verified in a two-screen Xephyr rig**, driving the real menu:
- the fader end to end, measured by pixel rather than by eye — mean brightness
  38 at visual 1 alone, 104 in the middle, 143 at visual 2 alone, and the
  middle brighter than the average of the ends, which is the screen blend
  behaving;
- both windows up at once showing different presets, the second letterboxed
  into a 140×700 strip with correct bars;
- the second window placed exactly on its monitor's corner;
- teardown both ways — back to one window, and unticking Visual 2 while it
  owns a window;
- the fader written to disk 0.6 s after the last key and not during a run of
  them.

**Linux 2.1.7 built and verified — 2026-09-15.**
`BomsAIVisualizer-2.1.7-linux-x86_64.tar.gz`, 86 MB, sha256
`90a457b3710fda5e7f89f5e03acd16740114e3a8a43da3d013251b3593a20316`, built
from `4d927e8` with `PROJECTM_SRC=/home/fr0st/projectm` at the pin
`397f15eb`. **glibc floor 2.34**, unchanged.

It has to be the container build. This box is now on **glibc 2.43**, so a
bare `build_linux.sh` would stamp 2.43 as the floor and the package would
start on almost nothing but the machine that made it. `build_linux_docker.sh`
is what produces a shippable one.

Contents proved by inflating the **688 zlib streams out of the executable**
(13.8 MB) — the app's own modules live in the PKG appended to the exe, not in
`_internal`, so a byte search of the unpacked directory finds nothing.
Present: `dual_crossfade`, `_do_set_window2`, `_place_second_window`,
`_draw_second_window`, `toggle_visual1`, `crossfade_back`, `visual1_enabled`,
`FADE_SETTLE_S`. Absent, and checked for on purpose because this release
removed them: `_dual_opacity` and "Second visual strength". Names that should
be gone are worth asserting as well as names that should be there — a stale
build passes the first half of that test.

Container check, stock `ubuntu:24.04`, both halves of the recipe this file
recorded at 2.1.5:
- `--help` with `libgl1 libglib2.0-0t64 libegl1` → usage, the
  `sounddevice unavailable` line, **exit 0**;
- a plain start with `libxcb-cursor0 libxkbcommon-x11-0` on top and the host
  X socket (`--net=host -e DISPLAY=:0 -v /tmp/.X11-unix:/tmp/.X11-unix:ro`)
  → still alive when `timeout` killed it at 20 s, **exit 124**.

**No Windows package at 2.1.7 yet** — the newest is the 2.1.6 setup.exe. That
build is Adam's; everything it needs is on the drive and nothing under
`installer/` changed this release except the EULA's version line, so it is a
plain rerun of `build_windows.ps1`.

**552 MB deleted** with it, all gitignored and therefore final: the superseded
2.1.5 tarball (86 MB) and its extracted tree (224 MB), and `installer/build/`
(242 MB).

### Windows caught up with 2.1.7, and the pair of new switches took it to 2.1.8 — 2026-09-16

Pulled `6a30cb0`. Twenty-six suites pass here unchanged, and the whole of the
crossfader and the second window were driven on a box with **one monitor**,
through the same `_refresh_displays` wrapper the Linux entry describes — there
is no fake-head option at all on Windows, so that wrapper is not a
convenience here, it is the only way in.

**Why 2.1.8.** Three fixes below are in `viz_common.py`, which both platforms
ship, and Linux has already built and recorded a 2.1.7 tarball. Same trade as
2.1.6 a day ago, and the number is still the cheap half of it. Linux needs a
pull and a rebuild at 2.1.8; nothing to port.

Worth saying out loud, because this is twice running: **the bump is a product
of the order, not of the code.** Linux builds and records a package, then
Windows verifies and finds something in shared code, and the number has to
move. Holding the Linux build until after the Windows pass would put one
number on both packages and cost nothing but a day.

#### What 2.1.7 does on Windows

Everything Linux built works here, and the one place the code says "Windows
needs nothing" is right: `SDL_CreateWindow` put visual 2's borderless window
at exactly the rectangle it was given, so the X11 correction in
`_place_second_window` is correctly a no-op. One GL context serving two
windows, `SDL_GL_MakeCurrent` between them every frame, teardown both ways,
and the keyboard handed back to the main window — all as written.

Measured rather than assumed:

- The fader is exact at both ends. Driven with `]` and `[`, each end showed
  one preset alone and the other gone, and `dual_crossfade` reached disk.
- A clip recorded while the visuals were split came back **946x532** — the
  main window's size, not the second window's — so the back buffer the
  recorder reads is the one the code says it is.
- Both windows render different presets at once, and the second window came
  back on its remembered monitor after a restart.
- The Settings dialog is down two rows and still opens without the scroll bar
  2.1.6 fixed; the Key bindings dialog absorbed three new actions and still
  sizes its columns to fit every note. Both of those were measured fixes from
  yesterday and neither needed touching.

#### Three things the pass found, all in the same seam

Each of the three is the *pair* of two features that are each correct alone.

- **Visual 1 switched off still showed on a screen each.** A screen apart
  makes the main window draw `crossfade 0.0`, which is "visual 1 alone", and
  it did that whether or not visual 1's switch was on — so the visual sat
  there at full strength with the tick beside it saying off. `test_dual` has
  a check for each half and the combination was not one of them. The window
  is cleared instead, which is also the honest thing for the recorder and the
  thumbnail to read out of it. Seen on screen, not only in the fakes.
- **The remembered monitor was never read back.** `visual2_display` is a
  setting like the two switches beside it, `_stop_dual` drops the window
  without clearing it, and nothing restored it — so the menu, which ticks the
  monitor straight out of config, claimed visual 2 was on a screen it was not
  on. Reachable twice: switch it off and on again, and every launch after the
  feature has been used once. `_do_set_dual` now queues `_req_win2` from
  config when it brings the second visual up, which covers the in-session
  route and startup in one line, because startup is just another caller.
  Verified both ways on screen.
- **The shipped README described the 2.1.6 shape of all this** — "a second
  preset over the first", the strength in Settings — one day after I rewrote
  that section. Rewritten again around the two switches, the fader, the two
  keys and "Send visual to", and the controls list in **both** READMEs gained
  `1`, `[` and `]`.

All three are mutation-checked, and the rig scores on the **exit code**: the
trap the CLAUDE.md entry records is real here, because `test_dual`'s `check()`
lets the AssertionError out and a failing run prints no `FAIL` line at all.

#### Two findings left alone on purpose

- **Wallpaper mode and a visual on another monitor do not compose.** Turning
  wallpaper mode on with the pair split sends the main window to the desktop
  layer and leaves visual 2 an ordinary window floating over everything on its
  monitor, taskbar button and all. It is recoverable rather than a trap — the
  tray menu is complete in that state, and "Leave wallpaper mode" put both
  windows back exactly where they were — but it is not what the word wallpaper
  promises. The two ways out are giving the second window a desktop host of
  its own on that monitor, or folding it back into the wallpaper with a toast
  saying so, and which of those is right is a decision about what the feature
  *is*, not a defect to quietly pick a side on.
- **`send_to` is not in `MENU_ENTRIES`, so Customize menu cannot hide it**,
  while `display` — conditional on the same two monitors — can. Cosmetic, and
  on the other machine's newest code, so it is written down rather than
  changed.

#### Also worth knowing

- **The DWM fullscreen-optimisation trap needs the FOREGROUND window.** Visual
  2's window is borderless at exactly a monitor's rectangle, which is the
  shape CLAUDE.md says cannot be photographed — and it photographs perfectly,
  because `SDL_RaiseWindow` hands activation straight back to the main window.
  Two grabs three seconds apart differed in 23,213 of 24,069 sampled pixels.
  The entry has been corrected. It also means the `_overhang_display` trick
  must *not* be copied to that window: it would spill two pixels onto the
  neighbouring monitor for no gain.
- **The letterbox follows the main window's aspect, not the second monitor's.**
  A 900x600 main window puts 150px bars down each side of a 1920x1080 second
  screen; making the main window 16:9 removed them exactly. Deliberate, and
  invisible in the ordinary case where the main window is fullscreen on a
  monitor the same shape — but it is the first thing anyone will report, so
  the README now says it.
- A harness trap, in CLAUDE.md: **PowerShell unrolls an array of arrays into a
  pipeline**, so a `Where-Object` that matched exactly one window row hands
  `ForEach-Object` its six *fields*, and `$_[2]` then indexes a string. The
  output is single characters where coordinates should be, which reads as the
  window list being wrong rather than the shell taking it apart.

#### The verified 2.1.8 Windows package

**`BomsAIVisualizer-2.1.8-win64-setup.exe`**, 78.4 MB (82,216,946 bytes),
sha256 `9607ae056db178b6fd01720ca2c419710e1c462d8815cfb2379dfad83ee2dbdb`.
Built from the clean tree with 2.1.6 deleted first; `VERSION` inside reads
2.1.8 and the bundled EULA agrees.

691 zlib streams inflated out of the PYZ, carrying `_clear_window` and
`_req_win2` from this pass and `_draw_second_window`, `crossfade`,
`visual2_display` and the string `second window lost` from Linux's 2.1.7.
Three never-written control names are absent, one of them
`_letterbox_to_monitor` — a method that was considered and not written while
reading this code, so its absence says the probe is matching the tree rather
than matching anything. Eight licence files including the PyAudioWPatch one
2.1.6 added; ffmpeg still the LGPL build.

**The smoke run was pointed at a monitor that is not there**, which is the
one thing about this release a frozen build on a one-monitor box can test
that a source run cannot: the isolated profile was seeded with
`dual_enabled: true` and `visual2_display: 1`. The app came up, stayed alive
and responding for 110 seconds, opened exactly **one** SDL window, and drew
the two visuals blended in it — so the restore found no second monitor, said
so, folded visual 2 back into the one window and did not take anything down
with it. That is the "the TV was unplugged since last time" path, verified on
the artifact.

The split itself was driven from source rather than from the package: the
fake monitor is a wrapper around `Renderer._refresh_displays`, and there is
nothing to wrap in a frozen exe. Everything either side of it — the
crossfader, both switches, the recording, the dialogs — was seen on both.

One wrinkle worth knowing, and left as it is: when the restore finds the
monitor missing it sets `visual2_display` to -1 **in memory** and does not
write it, so the file keeps the index. That is the better half of the trade —
unplug the TV for one session and the setting survives — but it does mean the
value is forgotten the moment anything else writes the config, which the
switches now do on every toggle. Arbitrary rather than wrong.

**The installer is the verified one from 2.1.6** — `[InstallDelete]` clearing
`_internal` and `licenses` wholesale, `UsePreviousGroup=no` — and nothing in
this build changes it. The check that proved it is in the entry above: diff
the installed tree against the portable tree, and expect Inno's `unins000`
pair and nothing else.

### Desktop wallpaper mode paused itself — 2.1.9, 2026-09-16

Reported against the 2.1.8 install, an hour after it went on: *"Tried desktop
mode, app stops working once enabled... displays a static box, can't interact
any further, using Task Manager to close afterwards."*

Nothing had crashed. No `Application Error`, no bugcheck, no minidump, the
machine up six hours, and the app's own config untouched since 8 September —
worth writing down because the WER log *did* carry four `BlueScreen` entries
from that morning, with empty fault buckets and no reboot behind them. Stale
WER records are not a blue screen; check for a BugCheck event and an uptime
before reading anything into one.

#### What it was

`fullscreen_app_running()` asked `SHQueryUserNotificationState`, which answers
"is this machine busy" — and in wallpaper mode the thing making it busy is
**us**. A full-screen OpenGL window covering the display reads back as
`QUNS_RUNNING_D3D_FULL_SCREEN`. Measured both ways: state **3** with the
wallpaper up, state **5** (ACCEPTS_NOTIFICATIONS) with the app shut.
`should_idle` pauses for a full-screen app *whatever the silence is*, so
within one 2-second poll of the mode starting the render loop parked, and no
amount of music brought it back. The visuals had drawn one frame; the preset
never advanced past the first.

That it looked like a **box** rather than a still picture is the second half.
The visualiser is a layered child of WorkerW and DWM only recomposites it when
it swaps, so with the loop parked the desktop keeps whatever was on the buffer
— and entering wallpaper mode resizes the window, so what stayed there was the
frame from the old 1280x720 window, in the corner where that window had been.

And the constants were off by one, which is why the call read as plausible:
the real values are `NOT_PRESENT` 1, `BUSY` 2, `RUNNING_D3D_FULL_SCREEN` 3,
`PRESENTATION_MODE` 4, and the code had BUSY at 1. So it treated NOT_PRESENT
as busy and missed PRESENTATION_MODE, while its own "presentation mode" value
of 3 happened to land on the real D3D-fullscreen one.

#### Three changes

- **`fullscreen_app_running` asks what is actually in front.**
  `GetForegroundWindow`, its pid against ours, its class against the shell's,
  its rectangle against its monitor's. A wallpaper is a child of WorkerW and
  never takes focus, so the foreground window can never be the one that trips
  it. The decision is `foreign_fullscreen`, a plain function beside
  `should_idle` for the same reason that one is: policy, and the loop it
  serves needs a display.
- **The pass that goes idle still draws.** `_check_idle` returns False the
  first time round, so whatever gets parked was drawn since the resize.
- **Entering wallpaper mode restarts the silence clock**, so the mode gets its
  whole twenty seconds instead of inheriting one that ran out during startup
  on a machine nobody has played anything on. Without it the feature looks
  broken by the act of asking for it, even with the other two fixed.

#### Verified

Reproduced first, with the reporter's own config copied into the repo: within
5 seconds of pressing `d`, `[idle] paused`, the preset stuck on the first one,
and the desktop showing a 1280x720 rectangle of stale visuals. With the fixes
in, the same run holds no `[idle] paused` at all over 64 seconds of a quiet
room, and the preset advances.

The screen still cannot be photographed in this mode — that trap is already
written up — so the proof is **a clip recorded from the tray menu while the
wallpaper was up: 1920x1080, 255 frames, 8.5 seconds.** Full screen, live,
and at the size the old code could never reach. The tray menu opening at all
is the other half of the report answered: the app was never unresponsive, and
"can't interact any further" is what a desktop window is — it takes no input
by design, and the tray icon, which Windows 11 hides in the chevron flyout, is
the only way in. Worth considering whether the toast on the way in says that
loudly enough.

Five mutations of the three fixes are each caught by `test_idle_pause`, scored
on the exit code. Three new checks name the bug: our own wallpaper is not
another app, the desktop is not either, and the frame that goes idle is still
drawn. The one that cannot run without a window — the silence-clock restart —
is pinned structurally with `ast`, which is the same tool the auto-advance
guard is pinned with.

**2.1.9** because 2.1.8 is installed on the reporter's machine and these are
three more changes to `viz_common.py`. Linux is still to rebuild; it should
come straight to 2.1.9 rather than stopping at 2.1.8, and the fix matters
there too — the API is Windows-only, but the parked-frame and silence-clock
halves are not.

#### The verified 2.1.9 Windows package

**`BomsAIVisualizer-2.1.9-win64-setup.exe`**, 78.4 MB (82,233,667 bytes),
sha256 `af4a8d16ff0b967167d34add971537c7d4dc1b0256c2addba40edf5900df1081`.
Built from the settled tree with 2.1.8 deleted first; `VERSION` inside reads
2.1.9 and the bundled EULA agrees.

691 zlib streams inflated, carrying `foreign_fullscreen`, `_SHELL_CLASSES`,
`_MONITORINFO`, `GetForegroundWindow` from this pass and `_clear_window` and
`_req_win2` from 2.1.8.

**The first control was wrong and the probe caught nothing because of it.**
`SHQueryUserNotificationState` — the API this release removes — was set as a
must-be-absent control and was found in the package: the name survives in the
docstring that explains why it went, and docstrings compile into the
bytecode. Controls have to be names that appear nowhere in the repo at all,
prose included. Rerun with three of those and the package is clean; that the
*call* is gone was settled by grepping the source for `WinDLL("shell32")`,
which is the right tool for that question and reads zero.

**Run on the reporter's own config**, copied into an isolated profile
untouched, on the frozen build: alive and responding 105 seconds after start,
then `d`, then forty seconds of desktop mode in a quiet room — still alive,
still responding, window at 0,0 1920x1080 parented into a WorkerW. On 2.1.8
that same run parked within five seconds and never came back.

Be precise about where each half was proved. The **frozen** build gives
structure and liveness. The proof that it is *drawing*, at the right size, is
from the **source** run: a clip recorded from the tray menu while the
wallpaper was up came back 1920x1080, 255 frames, 8.5 seconds. The tray icon
would not take a click on the later frozen attempt — the Windows 11 chevron
flyout did not open under the pointer — so that clip was not repeated on the
artifact. The code is identical and the probe says so, but the sentence "the
shipped build was seen drawing" is not one this entry gets to make.

The installer is the verified one from 2.1.6 and nothing here touches it.

### The desktop wallpaper took the screen, and then learned which one — 2.1.10, 2026-09-16

Reported against the installed 2.1.9: *"Desktop wallpaper now works, with one
minor thing…it starts out fullscreen at first, but when I click or right-click
anywhere, it snaps behind icons and widgets, displaying correctly."* Which
sounds like a stacking bug, and is not one. Structurally the window was
perfect the whole time: parented into Explorer's WorkerW, which is Progman's
child below `SHELLDLL_DefView`, at 0,0 1920x1080 — measured on the live
process every round. What fails is **composition**, not stacking.

The rectangle is exactly the monitor's and the window is the foreground one at
the moment it is parented, because the user clicked the menu entry to get
there. That is DWM's fullscreen optimisation, the same trap `_FS_OVERHANG` was
written for in fullscreen mode a week ago, reached from a completely different
direction: the display is handed to the window and nothing else composites at
all — desktop icons, taskbar, Rainmeter widgets and every other application's
window, all gone.

**The instrument is the finding.** `Graphics.CopyFromScreen` is already
written up as a liar here, and it lied convincingly four times in a row: it
showed the visuals frozen in the *old* window rectangle with the icons drawn
over them, and each of four plausible fixes — clearing the `WS_POPUP` that
`SDL_SetWindowBordered` puts back, handing the foreground to Progman, hiding
and re-showing the window, forcing a real resize — appeared to change it.
`ffmpeg -f lavfi -i "ddagrab=0:framerate=1" -vf hwdownload,format=bgra` reads
the Desktop Duplication API, which is the composed output rather than the
desktop DC, and it reproduced the user's screen exactly and scored every fix
honestly: all four did nothing. Two pixels past every edge fixed it, and
putting the exact rectangle back broke it again — A/B/A/B.

So `_wallpaper_box` grew an `overhang` argument rather than a platform test,
and the pure `wallpaper_box(displays, want, overhang)` behind it is what the
suite can reach. **X11 must not have the overhang**: two pixels off the screen
there is a window manager clamping the window back and `_hold_wallpaper_box`
correcting it for the life of the process, so the Windows way in asks for it
and the hold does not — which is pinned structurally, because nothing
offscreen can see a compositor.

#### Then the monitor of your choice

`wallpaper_display`, and not `fullscreen_display` as the box read before:
which screen the wallpaper covers and which screen fullscreen goes to are two
questions, and answering the second used to move the first. Default -1, the
whole desktop, which is what it did before.

**Set as desktop wallpaper** is a submenu on more than one monitor — whole
desktop, then a row per screen, with the current one ticked — and a single
click on one. Picking a monitor is an instruction to *be* the wallpaper there:
it turns the mode on when it is off, and moves it when it is already up, which
is what makes the same list useful from the tray menu, where leaving the mode
stays the first entry. A monitor that has been unplugged since it was chosen
falls back to the whole desktop rather than to nothing.

Driven on the real app through the `_refresh_displays` wrapper, which is still
the only way to have a second monitor on this box: the visuals confined to the
pretend screen's rectangle and behind the icons, moved to the whole desktop
and back with the toast naming where they went, and the submenu itself
photographed — `✔ Whole desktop / 1. Panasonic-TV 1920×1080 / 2. Pretend
monitor 800×450`. Seven mutations of the new work are each caught, scored on
the exit code.

**2.1.10**: 2.1.9 is installed on the reporter's machine and this is a
visible fix plus a feature in `viz_common.py`. Linux wants this one too — the
overhang is Windows-only and gated on the caller, but the monitor choice is
not.

#### The verified 2.1.10 Windows package

**`BomsAIVisualizer-2.1.10-win64-setup.exe`**, 78.4 MB (82,239,343 bytes),
sha256 `2230c9da80ded10522c92d7388d1ac24e504921a3010a2d1cc8f63a44f928085`.
Built from the settled tree with 2.1.9 deleted first; `VERSION` inside reads
2.1.10 and the bundled EULA agrees.

691 zlib streams inflated, carrying `wallpaper_box`, `_add_wallpaper_monitors`,
`_wallpaper_on_display`, `wallpaper_display`, `_req_wp_display` and
`request_wallpaper_on`. Three control names that appear nowhere in this repo
at all — grepped first, which is the lesson from last time — are absent, so
the scan is matching the work and not noise.

**And this time the shipped build was seen doing it.** The frozen package, run
on an isolated profile seeded with the reporter's own config: `d`, and one
second later the Desktop Duplication capture has the visuals as the wallpaper
with the desktop icons, RocketDock, the Rainmeter widgets, the taskbar and
another application's window all composited over them — no click needed. Then
thirty seconds of silence in a quiet room: the idle pause parks it on a
full-size frame with everything still over the top, so 2.1.9's parked-frame
fix survives the overhang. Pressing `d` again brought the window back and left
Explorer's WorkerW exactly as it was found.

The installer is the verified one from 2.1.6 and nothing here touches it.

### The wallpaper switch crashed inside AMD's driver, and Quit lost a clip — 2.1.11, 2026-10-02

Two defects found while checking smaller items, both fixed and both in
`viz_common.py` / `main.py`, so **Linux wants this one too** -- and this time
Windows went first, so the tarball can carry the same number. Build it at
2.1.11 rather than bumping.

**Entering wallpaper mode killed the render thread about one switch in
eight** on this box (Radeon HD 7800, AMD legacy driver 27.20.1034.6): an
access violation out of `render_frame`, visuals frozen, tray and menu still
up, always **128-136 ms** after `[wallpaper] on`. A native vectored exception
handler put it inside `atio6axx.dll`, under `opengl32!glDrawElements`, under
projectM -- the driver drawing into a window that had just been reparented,
made layered and resized. The render loop now leaves GL alone for
`WP_GL_QUIET_S` (0.5 s) after a switch in either direction, events and
requests still running. Source launches: 8 crashes in 34 before, 0 in 80
after. **It is not Windows code**, so the Linux path gets the same pause: the
amdgpu GPU reset in `wallpaper_may_start`'s note was the same chip family at
the same moment, and may be the same race. It is also the first suspect for
the slower laptop's crash if that machine has an AMD card and was in
wallpaper mode -- if it still crashes on 2.1.11, run it from a terminal and
keep the output, and `journalctl -b -1` for a GPU reset.

**Quit from the right-click menu lost a clip being recorded.** The row is
plain `QApplication.quit` and the render thread is a daemon, so the process
ended under the render thread's cleanup: a recording was left as its
`.audio.wav` and `.video.mp4` halves with no `.mp4`, and a quit from the
desktop menu printed a bare `TypeError`. `App.run` now stops the loop and
joins the thread after `exec()`, pumping Qt events while it waits.

Smaller: the key-bindings dialog opens tall enough for all 24 rows (capped to
the screen), and a new Customize-menu category is called "New list", which
fits the placement box that used to show "New categor".

#### The verified 2.1.11 Windows package

**`BomsAIVisualizer-2.1.11-win64-setup.exe`**, 78.4 MB (82,258,077 bytes),
sha256 `44e7b3d14a0f5338b1ee3418192d35bb4bc0e131e18096f77b1bd84cf1d257e0`.
Built with the 2.1.10 outputs deleted first; `VERSION` inside reads 2.1.11
and the bundled EULA agrees.

691 zlib streams inflated, carrying `WP_GL_QUIET_S`, `_gl_quiet_until`,
"New list" and `totalHeightForWidth`; three control names grepped absent from
the repo first are absent from the package. The frozen build, on a scratch
profile (`APPDATA` and `LOCALAPPDATA` both redirected, nothing reached the
real one): record four seconds, Quit from the menu, and a finished 4.7 s
`.mp4` with no halves left over; 24 `--wallpaper` launches, every one
reparented into the desktop by 16 s and still drawing (3D engine at 18-29%
for the process in each of the twelve sampled that way).

Not done: installing it. That needs a UAC click, and the installed-vs-portable
diff with it.
