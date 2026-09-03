#!/usr/bin/env bash
# MilkDrop Visualizer — one-shot setup for a fresh Ubuntu install.
# Installs system deps, builds libprojectM-4 from source, creates the venv.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECTM_TAG="v4.1.0"   # matches the lib version projectm_wrapper.py expects

echo "==> [1/4] Installing system packages..."
sudo apt-get update
sudo apt-get install -y \
    build-essential cmake git pkg-config \
    libgl1-mesa-dev libglm-dev \
    python3 python3-venv python3-dev \
    libportaudio2 \
    libxcb-cursor0 libxcb-xinerama0 libxkbcommon-x11-0 \
    libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libxcb-xkb1 \
    libxcb-image0 libxcb-render-util0 libxcb-randr0 \
    libsdl2-2.0-0

echo "==> [2/4] Building libprojectM-4 (${PROJECTM_TAG})..."
if [ -e /usr/local/lib/libprojectM-4.so ]; then
    echo "    libprojectM-4 already installed, skipping build."
else
    BUILD_DIR="$(mktemp -d)"
    git clone --depth 1 --branch "$PROJECTM_TAG" --recurse-submodules \
        https://github.com/projectM-visualizer/projectm.git "$BUILD_DIR/projectm"
    cmake -S "$BUILD_DIR/projectm" -B "$BUILD_DIR/build" \
        -DCMAKE_BUILD_TYPE=Release \
        -DENABLE_PLAYLIST=ON
    cmake --build "$BUILD_DIR/build" -j"$(nproc)"
    sudo cmake --install "$BUILD_DIR/build"
    sudo ldconfig
    rm -rf "$BUILD_DIR"
fi

echo "==> [3/4] Creating Python virtual environment..."
# Prefer python3.14 (what the old venv used) if present, else system python3.
PY=python3
command -v python3.14 >/dev/null 2>&1 && PY=python3.14
rm -rf "$SCRIPT_DIR/.venv"
"$PY" -m venv "$SCRIPT_DIR/.venv"
"$SCRIPT_DIR/.venv/bin/pip" install --upgrade pip
"$SCRIPT_DIR/.venv/bin/pip" install \
    numpy \
    PyQt6 \
    PyOpenGL PyOpenGL_accelerate \
    PySDL2 pysdl2-dll \
    sounddevice

echo "==> [4/4] Checking preset directory..."
# config.json points at ~/projectm-presets; link it to the repo copy if missing.
if [ ! -e "$HOME/projectm-presets" ] && [ -d "$SCRIPT_DIR/projectm-presets" ]; then
    ln -s "$SCRIPT_DIR/projectm-presets" "$HOME/projectm-presets"
    echo "    Symlinked ~/projectm-presets -> repo presets."
fi

echo ""
echo "Done. Launch with: bash $SCRIPT_DIR/run.sh"
