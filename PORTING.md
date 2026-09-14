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

**VERSION is 2.0.0.** Bumped on 2026-09-09, once section A was complete; both
build scripts read the `VERSION` file, so nothing else needs editing to make a
2.0.0 package. The Inno `AppId` GUID does **not** move with it — that GUID is
what makes the installer an upgrade rather than a second copy, and it never
changes.

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

**The Linux tarball is current**: rebuilt at 14:19 from `0bc88dc`, 86 MB,
md5 `97fdba8f19dc2f21fdf8550019537570`, floor GLIBC_2.34, and verified to
start on a stock `ubuntu:24.04` with no libportaudio2.


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


## Notes

- `SDL_GetWindowWMInfo` is used once, for the embedded-in-BomsAI-Studio case. The
  `XID=` key on that print stays literal on every platform; the host parses it.
- FPS `0` means uncapped in the UI, but projectM's internal timers need a real number,
  so a nominal value is passed to `projectm_set_fps` while the loop runs free.
