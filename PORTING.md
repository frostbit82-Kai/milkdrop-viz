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

Not started:

- [ ] **`libprojectM-4.dll` built on Windows — this gates everything below**
- [ ] Audio capture: WASAPI loopback
- [ ] Run it end to end on Windows
- [ ] PyInstaller packaging, then code signing
- [ ] Converge the two `viz_common.py` copies
- [ ] Steam page

## First session on Windows

Read `CLAUDE.md` first. Then, in order — **do not skip step 2, and stop if it fails.**

**1. Toolchain.** Git for Windows, Python 3.14 (fall back to 3.13 if any wheel in
`requirements.txt` has no 3.14 build), CMake, and Visual Studio 2022 Build Tools with
the "Desktop development with C++" workload.

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

**2. Build projectM 4.1.0.** The Linux recipe is in `setup.sh`; the Windows equivalent
is the same CMake invocation with vcpkg supplying the dependencies. `-DENABLE_PLAYLIST=ON`
is required — the app binds `libprojectM-4-playlist` as well as the core library.

Success is `libprojectM-4.dll` and `libprojectM-4-playlist.dll` somewhere on PATH or
beside the app. `viz_common.py` already looks for exactly those names on win32.

**This step is the whole risk.** If projectM will not build or run on Windows, the
product does not exist, and no amount of Python work changes that. Prove it before
touching anything else.

**3. Audio.** The current capture is PulseAudio three ways over: a `parec` subprocess,
`pactl move-source-output` for routing, and PortAudio's `pulse` device. All of it is
in `AudioCapture` in `viz_common.py`.

Windows needs WASAPI loopback, and `sounddevice` 0.5.5 cannot do it (checked — no
`loopback` parameter on `WasapiSettings`, no occurrence of the word in the module).
Use `PyAudioWPatch` or `soundcard`.

Shape it as a backend interface with a Linux and a Windows implementation rather than
branching inside the existing methods. Note the device picker changes meaning:
Windows loopback enumerates **render** endpoints, not monitor sources, so the settings
UI wording and the enumeration both need revisiting.

**4. Run it.** Presets and textures are not in the repo — download a pack, point the
two settings at it, confirm texture-sampling presets are not rendering white.

## Notes

- `SDL_GetWindowWMInfo` is used once, for the embedded-in-BomsAI-Studio case. The
  `XID=` key on that print stays literal on every platform; the host parses it.
- FPS `0` means uncapped in the UI, but projectM's internal timers need a real number,
  so a nominal value is passed to `projectm_set_fps` while the loop runs free.
