#!/usr/bin/env bash
# BomsAI Visualizer — launcher
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV" ]; then
    echo "Virtual environment not found. Run: bash setup.sh"
    exit 1
fi

source "$VENV/bin/activate"
export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH}"
export SDL_VIDEODRIVER=x11
export SDL_AUDIODRIVER=pulse

# Force Qt onto X11/xcb so right-click menus grab focus correctly.
# Without this, Qt tries Wayland popup rules which clash with the
# XWayland session SDL2 is already using.
export QT_QPA_PLATFORM=xcb

exec python "$SCRIPT_DIR/main.py" "$@"
