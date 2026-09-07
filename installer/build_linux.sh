#!/usr/bin/env bash
# Build the Linux release tarball. Developer-side script — end users run the
# install.sh that comes inside the tarball it produces.
#
#   bash installer/build_linux.sh
#
# Output: installer/dist/BomsAIVisualizer-<version>-linux-x86_64.tar.gz
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERE="$REPO/installer"
VERSION="$(cat "$REPO/VERSION")"
NAME="BomsAIVisualizer-${VERSION}-linux-x86_64"
BUILD="$HERE/build"
OUT="$HERE/dist"

# libprojectM is the one dependency that cannot be rebuilt on demand: upstream's
# release links have gone dead before now, and building it from source needs a
# toolchain the packaging box may not have. So a known-good copy is vendored and
# used by default; PROJECTM_LIB_DIR overrides it when a newer one is built.
PROJECTM_LIB_DIR="${PROJECTM_LIB_DIR:-$HERE/vendor/linux}"
if ! compgen -G "$PROJECTM_LIB_DIR/libprojectM-4*.so*" >/dev/null; then
    echo "No libprojectM-4*.so* in $PROJECTM_LIB_DIR" >&2
    echo "Vendor one with:  cp -L /usr/local/lib/libprojectM-4{,-playlist}.so.4 $HERE/vendor/linux/" >&2
    exit 1
fi

VENV="${VENV:-$REPO/.venv}"
[ -x "$VENV/bin/python" ] || { echo "No venv at $VENV — run: bash setup.sh" >&2; exit 1; }
"$VENV/bin/python" -c "import PyInstaller" 2>/dev/null || "$VENV/bin/pip" install -q pyinstaller

echo "==> [1/3] Freezing the app"
rm -rf "$BUILD" "$OUT/$NAME"
MILKDROP_REPO="$REPO" PROJECTM_LIB_DIR="$PROJECTM_LIB_DIR" \
    "$VENV/bin/pyinstaller" --noconfirm --clean \
    --distpath "$BUILD/dist" --workpath "$BUILD/work" \
    "$HERE/bomsai-visualizer.spec"

echo "==> [2/3] Assembling the package"
STAGE="$OUT/$NAME"
mkdir -p "$STAGE"
cp -r "$BUILD/dist/BomsAIVisualizer" "$STAGE/app"
cp "$HERE/linux/install.sh" "$HERE/linux/uninstall.sh" "$STAGE/"
cp "$HERE/linux/README.txt" "$STAGE/"
cp "$HERE/linux/bomsai-visualizer.desktop" "$HERE/linux/bomsai-visualizer.svg" "$STAGE/"
mkdir -p "$STAGE/licenses"
cp "$HERE/licenses/"* "$STAGE/licenses/"
chmod +x "$STAGE/install.sh" "$STAGE/uninstall.sh"
printf '%s\n' "$VERSION" > "$STAGE/VERSION"

echo "==> [3/3] Compressing"
tar -C "$OUT" -czf "$OUT/$NAME.tar.gz" "$NAME"
rm -rf "$STAGE"

echo
echo "Built $OUT/$NAME.tar.gz  ($(du -h "$OUT/$NAME.tar.gz" | cut -f1))"
