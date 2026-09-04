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

Not started:
- [ ] PyInstaller packaging, then code signing
- [ ] Steam page

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

Still unproven: audio has never been fed into a running projectM on Windows. Capture is
verified in isolation, not through the render loop.

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

**Presets and textures still live on the WSL drive.** The repo moved to
`C:\Projects\milkdrop-viz`, but the asset directories did not: 2.5 GB of textures
(8,414 files) and 143 MB of presets (11,223 files) are still under
`\\wsl.localhost\Ubuntu\mnt\wsl\PHYSICALDRIVE1p2\home\fr0st\Projects\milkdrop-viz\`.
They are gitignored and never ship, so where they sit is purely a performance question,
but textures are read while presets load and that path is a network redirector. Copy
them to a native Windows drive before judging load times.

## Notes

- `SDL_GetWindowWMInfo` is used once, for the embedded-in-BomsAI-Studio case. The
  `XID=` key on that print stays literal on every platform; the host parses it.
- FPS `0` means uncapped in the UI, but projectM's internal timers need a real number,
  so a nominal value is passed to `projectm_set_fps` while the loop runs free.
