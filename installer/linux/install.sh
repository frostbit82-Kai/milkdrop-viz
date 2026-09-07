#!/usr/bin/env bash
# BomsAI Visualizer — installer.
#
#   ./install.sh              install for this user (no root needed)
#   PREFIX=/opt ./install.sh  install somewhere else (may need sudo)
#   ./install.sh --uninstall  same as ./uninstall.sh
set -euo pipefail

[ "${1:-}" = "--uninstall" ] && exec "$(dirname "$0")/uninstall.sh"

SRC="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
APPDIR="$PREFIX/share/BomsAIVisualizer"
BINDIR="$PREFIX/bin"
DESKTOP="$PREFIX/share/applications"
ICONDIR="$PREFIX/share/icons/hicolor/scalable/apps"
EXE="$APPDIR/BomsAIVisualizer"

echo "BomsAI Visualizer $(cat "$SRC/VERSION")"
echo "Installing to $APPDIR"
echo

# Everything under PREFIX is created up front so a permission problem surfaces
# here rather than half way through a copy. APPDIR itself is not, because an
# upgrade has to replace it wholesale rather than merge into an older build.
mkdir -p "$(dirname "$APPDIR")" "$BINDIR" "$DESKTOP" "$ICONDIR"

echo "==> Copying files"
rm -rf "$APPDIR"
cp -r "$SRC/app" "$APPDIR"
cp -r "$SRC/licenses" "$APPDIR/licenses"
cp "$SRC/README.txt" "$APPDIR/"
chmod +x "$EXE"

echo "==> Adding the launcher and menu entry"
ln -sf "$EXE" "$BINDIR/bomsai-viz"
cp "$SRC/bomsai-visualizer.svg" "$ICONDIR/bomsai-visualizer.svg"
sed -e "s|@EXEC@|$EXE|" -e "s|@ICON@|bomsai-visualizer|" \
    "$SRC/bomsai-visualizer.desktop" > "$DESKTOP/bomsai-visualizer.desktop"
command -v update-desktop-database >/dev/null && \
    update-desktop-database "$DESKTOP" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && \
    gtk-update-icon-cache -qtf "$PREFIX/share/icons/hicolor" 2>/dev/null || true

# A smoke test, not a formality: this loads SDL2, Qt and every bundled shared
# library, so a host missing something says so now with the real linker error
# instead of on a double-click that appears to do nothing.
echo "==> Checking it runs"
if ! "$EXE" --help >/dev/null; then
    echo
    echo "The app did not start. The error above is the real reason." >&2
    exit 1
fi
echo "    ok"

# Warnings, not failures — the app degrades rather than refusing to start.
echo
command -v parec >/dev/null || cat <<'MSG'
Note: `parec` was not found. It is the audio capture this app prefers on Linux;
      without it the PortAudio fallback is used, which is fussier about picking
      the right device. Install it with:  sudo apt install pulseaudio-utils
MSG
case ":$PATH:" in
    *":$BINDIR:"*) ;;
    *) echo "Note: $BINDIR is not on your PATH, so the \`bomsai-viz\` command"
       echo "      will not be found. The menu entry works regardless." ;;
esac

cat <<MSG

Installed. Launch it from your applications menu, or run:  bomsai-viz

Presets and textures are NOT included — they are other people's work and are
downloaded separately. See README.txt (also copied to $APPDIR).
Once you have them: right-click the visualizer window, choose Settings, and set
the preset and texture directories.

To remove: $SRC/uninstall.sh
MSG
