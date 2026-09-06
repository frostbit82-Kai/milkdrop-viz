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
- [x] **libprojectM 4.1.0 built with `ENABLE_PLAYLIST=ON`** — `projectM-4.dll` and
      `projectM-4-playlist.dll`, Release, x64, dynamic CRT
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
      `MilkDropVisualizer-<version>-linux-x86_64.tar.gz`; the user extracts it and
      runs `./install.sh`, which installs to `~/.local` with no root, adds a menu
      entry and a `milkdrop-viz` command, and smoke-tests the app before saying it
      worked. Verified end to end: installed copy loads its own bundled
      libprojectM, 11,223 presets, parec bound to the sink monitor, presets
      rendering and switching. `uninstall.sh` reverses it and keeps settings
      and ratings
- [x] libprojectM vendored at `installer/vendor/linux/` rather than rebuilt, with
      projectM's LGPL-2.1 text alongside it in `installer/licenses/`
- [x] **Old-system build**: `installer/build_linux_docker.sh`, glibc floor 2.43 -> 2.36
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
      43.7 MB `MilkDropVisualizer-<version>-win64-setup.exe` plus the portable
      tree. Installed to `C:\Program Files\MilkDrop Visualizer`, Start Menu
      entry, optional desktop icon, uninstaller; settings and ratings live in
      `%APPDATA%\MilkDropVisualizer` and survive uninstall. Verified end to end:
      silent install, launch from the installed path, bundled projectM loaded,
      11,223 presets, WASAPI loopback, presets rendering and switching, and the
      About dialog showing its logo and the `VERSION` file out of `sys._MEIPASS`
- [x] The release build is **windowed** (no console box behind the visualiser)
      and therefore silent. `MILKDROP_CONSOLE=1` builds a console version, which
      is the only way to debug a frozen failure
- [x] libprojectM vendored at `installer/vendor/windows/` — the same three DLLs
      `setup_windows.ps1` gathers, with GLEW's licence added to `licenses/`
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

## Packaging

`installer/README.md` covers the build. The parts that cost time:

**The glibc of the build machine becomes the floor.** Measured, not guessed:
a package built on this box needs **glibc 2.43** — Ubuntu 26.04 and nothing
else. `installer/build_linux_docker.sh` builds the whole thing (libprojectM
included) inside Debian 12 instead and takes the floor to **2.36**: Debian 12+,
Ubuntu 24.04+, Fedora 37+, current Arch and SteamOS. Use it for anything
released. Ubuntu 22.04 (2.35) needs a lower base image and therefore building
Python too; for Steam the eventual target is the Steam Runtime 3 "sniper" SDK.

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

## Notes

- `SDL_GetWindowWMInfo` is used once, for the embedded-in-BomsAI-Studio case. The
  `XID=` key on that print stays literal on every platform; the host parses it.
- FPS `0` means uncapped in the UI, but projectM's internal timers need a real number,
  so a nominal value is passed to `projectm_set_fps` while the loop runs free.
