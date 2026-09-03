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

Not started:

- [~] Audio capture: WASAPI loopback — **written, never executed.** `_start_wasapi`
      in `viz_common.py` uses `soundcard` to open the render endpoint in loopback
      mode, and `start()` tries it after parec (each no-ops on the other platform).
      `capture_devices()` now feeds the settings picker per platform: input devices
      on Linux, loopback-capable speakers on Windows. Verify this first on Windows
      — it is the one piece of the port nobody has run.
- [ ] Run it end to end on Windows
- [ ] PyInstaller packaging, then code signing
- [ ] Converge the two `viz_common.py` copies
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

**Audio.** The current capture is PulseAudio three ways over: a `parec` subprocess,
`pactl move-source-output` for routing, and PortAudio's `pulse` device. All of it is
in `AudioCapture` in `viz_common.py`.

Windows needs WASAPI loopback, and `sounddevice` 0.5.5 cannot do it (checked — no
`loopback` parameter on `WasapiSettings`, no occurrence of the word in the module).
Use `PyAudioWPatch` or `soundcard`.

This is now written but unrun. `_start_wasapi()` sits beside `_start_parec()` and
`start()` tries both; each returns False on the other platform, so no branching was
needed in the existing methods. If the loopback backend fails, the sounddevice path
still runs — on Windows that opens a microphone rather than the system output, which
is worth knowing when the visualiser reacts to the room instead of the music.

What to check when you run it, in order: that `soundcard` installed at all; that the
picker lists speakers rather than microphones; and that chunks arrive as float32 of
shape (frames, 2), which is the contract the parec reader established and the rest of
the app assumes.

**Run it.** Presets and textures are not in the repo — download a pack, point the
two settings at it, confirm texture-sampling presets are not rendering white. The
render loop and live audio have never been exercised on Windows; building the DLL
proves the binding, not the GL path.

**Where the repo lives.** It currently sits on an ext4 drive that only WSL mounts, so
Windows reaches it over `\\wsl.localhost\...`. That is fine for editing and was fine
for the build (which happens in `C:\dev`), but before running the GL loop or packaging,
move or clone the repo to a native Windows path.

## Notes

- `SDL_GetWindowWMInfo` is used once, for the embedded-in-BomsAI-Studio case. The
  `XID=` key on that print stays literal on every platform; the host parses it.
- FPS `0` means uncapped in the UI, but projectM's internal timers need a real number,
  so a nominal value is passed to `projectm_set_fps` while the loop runs free.
