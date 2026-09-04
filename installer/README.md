# Installers

Release packaging for MilkDrop Visualizer. Two platforms, one shared spec.

    installer/
      build_linux.sh          build the Linux tarball  (run on Linux)
      milkdrop-viz.spec       PyInstaller spec, used by both platforms
      vendor/linux/           prebuilt libprojectM-4 .so files, committed
      licenses/               projectM's LGPL-2.1 text, shipped with the app
      linux/                  what goes inside the tarball for the end user
      build/  dist/           output, gitignored

## Linux

    bash installer/build_linux.sh

Produces `installer/dist/MilkDropVisualizer-<version>-linux-x86_64.tar.gz` (~88 MB).
The user extracts it and runs `./install.sh`: it installs to `~/.local` with no
root, adds a menu entry and a `milkdrop-viz` command, and starts the app once to
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

**Whatever glibc the build machine has becomes the floor.** PyInstaller bundles
this box's `libpython`, and the Qt and SDL2 wheels are manylinux, but the
bootloader and the vendored libprojectM are not — a package built on Ubuntu
26.04 will not start on an older distro. Build on the oldest release worth
supporting.

## Windows

Not built yet. The spec is already cross-platform: it picks `SDL2.dll` instead of
`libSDL2-2.0.so` and expects `PROJECTM_LIB_DIR` to hold `projectM-4.dll`,
`projectM-4-playlist.dll` and `glew32.dll` — the three that `setup_windows.ps1`
gathers into `C:\dev\projectm-deploy`. What is missing is the counterpart to
`build_linux.sh` and an installer format (Inno Setup is the obvious one), plus
code signing.

## What is deliberately absent

Presets and textures. They are other people's work, the app asks where they live,
and `linux/README.txt` tells the user where to download them. See the repo's
`CLAUDE.md`.
