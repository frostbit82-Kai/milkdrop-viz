# Installers

Release packaging for BomsAI Visualizer. Two platforms, one shared spec.

    installer/
      build_linux.sh          build the Linux tarball  (run on Linux)
      build_linux_docker.sh   same, in an older-glibc container
      Dockerfile.linux        that container
      bomsai-visualizer.spec       PyInstaller spec, used by both platforms
      vendor/linux/           prebuilt libprojectM-4 .so files, committed
      licenses/               projectM's LGPL-2.1 text, shipped with the app
      linux/                  what goes inside the tarball for the end user
      build/  dist/           output, gitignored

## Linux

    bash installer/build_linux.sh

Produces `installer/dist/BomsAIVisualizer-<version>-linux-x86_64.tar.gz` (~88 MB).
The user extracts it and runs `./install.sh`: it installs to `~/.local` with no
root, adds a menu entry and a `bomsai-viz` command, and starts the app once to
check it runs before claiming success. `./uninstall.sh` reverses it and leaves
settings and preset ratings alone.

**libprojectM is vendored, not built.** Upstream's release links have gone dead
before now, and building from source needs a toolchain the packaging machine may
not have — so a known-good `libprojectM-4.so.4` and `libprojectM-4-playlist.so.4`
are committed under `vendor/linux/`. Set `PROJECTM_LIB_DIR` to override:

    PROJECTM_LIB_DIR=/usr/local/lib bash installer/build_linux.sh

projectM is LGPL-2.1. It stays a separate shared library loaded with `ctypes`,
and `licenses/` travels with the app, which is what keeps that licence satisfied
for a paid product. Do not statically link it.

### Older systems

**Whatever glibc the build machine has becomes the floor.** Built on Ubuntu
26.04 the package needs glibc 2.43 and starts on Ubuntu 26.04 and nothing else.
Measure it, do not guess:

    objdump -T <file> | grep -oE 'GLIBC_[0-9.]+' | sort -Vu | tail -1

So there is a second build path that runs the whole thing inside an older
container:

    bash installer/build_linux_docker.sh

Same output, floor dropped to **glibc 2.36** — Debian 12+, Ubuntu 24.04+,
Fedora 37+, current Arch and SteamOS. It rebuilds libprojectM in the container
(the vendored copy is linked against the host's glibc, which is the thing being
escaped) and refreshes `vendor/linux/` with the result, so the host build gets
the portable library too.

It needs a projectM source tree — `PROJECTM_SRC`, auto-detected from a few
usual places — rather than cloning, because upstream's links have gone dead
before. `vendor/projectm-eval` is a submodule, so the tree has to be a
`--recurse-submodules` clone.

Bookworm is as old as this goes for free: there is no `python:3.14` image below
it, so a lower floor (Ubuntu 22.04 is glibc 2.35) means building Python in the
image as well. `BASE=<image> bash installer/build_linux_docker.sh` is the knob
if that day comes. For Steam specifically the right target is eventually the
Steam Runtime 3 "sniper" SDK, which is glibc 2.31 and is shipped to players by
Steam itself.

## Windows

    powershell -ExecutionPolicy Bypass -File installer\build_windows.ps1

Produces `installer\dist\BomsAIVisualizer-<version>-win64-setup.exe` and the
portable tree beside it. It freezes with the same cross-platform spec the Linux
build uses — the spec picks `SDL2.dll` over `libSDL2-2.0.so` and reads
`PROJECTM_LIB_DIR` for `projectM-4.dll` and `projectM-4-playlist.dll` —
then hands the staged tree to Inno Setup 6. `-SkipInstaller` stops after
freezing, which is what you want while iterating on the app.

Both DLLs are vendored at `vendor/windows/` for the same reason as the
Linux `.so` files, and `PROJECTM_LIB_DIR` overrides them. There is no
`glew32.dll` any more: projectM loads OpenGL itself at the pinned commit. `setup_windows.ps1`
builds fresh ones and gathers them into `C:\dev\projectm-deploy`.

After re-vendoring them, run `windows\verify_dlls.ps1`. It checks that the DLLs
export every symbol `viz_common.py` binds — a projectM build missing
`projectm_playlist_add_presets` looks fine until the playlist silently comes up
empty — and lists what else must ship alongside. The symbol list is read out of
the source, so it cannot drift.

**The release build is windowed**, so there is no console box behind the
visuals — and therefore no log at all, since `print()` is a no-op when
`sys.stdout` is None. `MILKDROP_CONSOLE=1` builds a console version instead,
which is the only way to see why a frozen build failed.

Still missing: **code signing**. Unsigned, SmartScreen shows its "Windows
protected your PC" wall on first run. Steam delivers the app through its own
client, so this matters most for a direct download.

## One version, both platforms

`VERSION` drives both scripts, and both artifacts are named from it. Bump it
once and rebuild **both** packages from that commit, or two different builds
end up wearing the same version number.

## Where the working copies live

One repository, both platforms — the split is by folder, not by clone:

| Path | Machine | Holds |
|------|---------|-------|
| `installer/linux/` | — | `install.sh`, `.desktop`, icon, uninstaller |
| `installer/windows/` | — | Inno Setup script, `.ico`, `verify_dlls.ps1` |
| `installer/vendor/{linux,windows}/` | — | projectM (and GLEW) binaries, tracked in git |
| `installer/build/`, `installer/dist/` | — | build scratch and artifacts, both gitignored |

Each machine keeps exactly one clone, and they meet on GitHub:

| Path | Machine |
|------|---------|
| `~/Projects/milkdrop-viz` | Linux — Linux builds, and the shared code |
| `C:\Projects\milkdrop-viz` | Windows — Windows builds |
| `C:\Projects\milkdrop-assets` | Windows — presets and textures, never shipped, never in git |
| `C:\dev\` | Windows — the libprojectM build toolchain only (`vcpkg`, `projectm`) |

Commit on one, push, pull on the other. A second copy of the repo on the same
machine is how the two trees drift apart; there is no reason to make one.

## What is deliberately absent

Presets and textures. They are other people's work, the app asks where they live,
and `linux/README.txt` tells the user where to download them. See the repo's
`CLAUDE.md`.
