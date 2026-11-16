#!/usr/bin/env bash
# BomsAI Visualizer — uninstaller. Removes what install.sh added.
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
APPDIR="$PREFIX/lib/BomsAIVisualizer"
# Releases up to 2.1.14 installed here, inside the folder the app keeps its
# user's files in. See install.sh.
OLDDIR="$PREFIX/share/BomsAIVisualizer"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/BomsAIVisualizer"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/BomsAIVisualizer"

rm -rf "$APPDIR"
# An old install: its four entries by name, and never the folder itself.
if [ -f "$OLDDIR/BomsAIVisualizer" ] && [ -d "$OLDDIR/_internal" ]; then
    rm -rf "$OLDDIR/BomsAIVisualizer" "$OLDDIR/_internal" \
           "$OLDDIR/licenses" "$OLDDIR/README.txt"
fi
rm -f "$PREFIX/bin/bomsai-viz" \
      "$PREFIX/share/applications/bomsai-visualizer.desktop" \
      "$PREFIX/share/icons/hicolor/scalable/apps/bomsai-visualizer.svg"
command -v update-desktop-database >/dev/null && \
    update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true

echo "Removed BomsAI Visualizer from $PREFIX."
# Settings and preset ratings are left alone on purpose — reinstalling should
# not cost you the ratings you spent hours giving 11,000 presets. The same
# goes for what the app saved for you: clips, thumbnails, fetched preset
# packs, collections.
[ -d "$CONFIG" ] && echo "Settings and ratings kept in $CONFIG (delete it yourself if you want them gone)."
[ -d "$DATA" ] && echo "Clips, thumbnails, downloaded presets and collections kept in $DATA (the same goes for those)."
exit 0
