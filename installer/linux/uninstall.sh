#!/usr/bin/env bash
# MilkDrop Visualizer — uninstaller. Removes what install.sh added.
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
APPDIR="$PREFIX/share/MilkDropVisualizer"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/MilkDropVisualizer"

rm -rf "$APPDIR"
rm -f "$PREFIX/bin/milkdrop-viz" \
      "$PREFIX/share/applications/milkdrop-visualizer.desktop" \
      "$PREFIX/share/icons/hicolor/scalable/apps/milkdrop-visualizer.svg"
command -v update-desktop-database >/dev/null && \
    update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true

echo "Removed MilkDrop Visualizer from $PREFIX."
# Settings and preset ratings are left alone on purpose — reinstalling should
# not cost you the ratings you spent hours giving 11,000 presets.
[ -d "$CONFIG" ] && echo "Settings and ratings kept in $CONFIG (delete it yourself if you want them gone)."
exit 0
