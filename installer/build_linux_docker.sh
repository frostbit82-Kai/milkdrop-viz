#!/usr/bin/env bash
# Build the Linux package inside an older-glibc container, so it runs on more
# than the machine that built it. Same output as build_linux.sh, lower floor.
#
#   bash installer/build_linux_docker.sh
#
# PROJECTM_SRC   projectM source tree (must include vendor/, submodules and all)
# BASE           builder base image, default python:3.14-slim-bookworm
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERE="$REPO/installer"
BASE="${BASE:-python:3.14-slim-bookworm}"
IMAGE="milkdrop-viz-builder"

# libprojectM has to be rebuilt in the container: the copy vendored for the
# host build is linked against the host's glibc, which is the very thing this
# script exists to get away from. Upstream's release links have gone dead
# before, so a local source tree is preferred over cloning.
PIN="$(grep -v '^#' "$HERE/PROJECTM_COMMIT" | tr -d '[:space:]')"
[ -n "$PIN" ] || { echo "installer/PROJECTM_COMMIT has no commit in it." >&2; exit 1; }

# A tree is only usable if it is AT the pin. Checking that it merely exists is
# how the wrong library gets built: several projectM checkouts live on this
# machine at different versions, the first one found wins, and a 4.1.0 build
# comes out reporting success. Reported, never silently checked out -- the
# source tree belongs to whoever cloned it.
usable() {
    # Both files, not just the top one: a tree whose submodules were never
    # initialised has the outer CMakeLists.txt and nothing under vendor/, and
    # cmake only says so two minutes into the container build.
    [ -f "$1/CMakeLists.txt" ] && [ -f "$1/vendor/projectm-eval/CMakeLists.txt" ] \
        && [ "$(git -C "$1" rev-parse HEAD 2>/dev/null)" = "$PIN" ]
}

PROJECTM_SRC="${PROJECTM_SRC:-}"
if [ -n "$PROJECTM_SRC" ]; then
    usable "$PROJECTM_SRC" || {
        echo "PROJECTM_SRC=$PROJECTM_SRC is not at the pinned commit." >&2
        echo "  wanted: $PIN" >&2
        echo "  got:    $(git -C "$PROJECTM_SRC" rev-parse HEAD 2>/dev/null || echo 'not a git tree')" >&2
        echo "  fix:    git -C $PROJECTM_SRC fetch origin && git -C $PROJECTM_SRC checkout $PIN \\" >&2
        echo "          && git -C $PROJECTM_SRC submodule update --init --recursive" >&2
        exit 1
    }
else
    NEAR=""
    for c in "$REPO/../projectm" "$HOME/projectm" "$HOME/dev/projectm"; do
        [ -d "$c" ] || continue
        if usable "$c"; then PROJECTM_SRC="$(cd "$c" && pwd)"; break; fi
        # Remember one wrong-version tree so the failure can name it rather
        # than claiming nothing is there. An if, not an && chain: under set -e
        # the chain's own failure once NEAR is set would end the script.
        if [ -z "$NEAR" ] && [ -f "$c/CMakeLists.txt" ]; then NEAR="$c"; fi
    done
    if [ -z "$PROJECTM_SRC" ] && [ -n "$NEAR" ]; then
        echo "==> $NEAR is at $(git -C "$NEAR" rev-parse --short HEAD 2>/dev/null || echo '?'), not the pin" >&2
    fi
fi
REBUILD_PM=1
if [ ! -f "${PROJECTM_SRC:-/nonexistent}/vendor/projectm-eval/CMakeLists.txt" ]; then
    # No usable source tree — the drive holding it may not be mounted, or the
    # one that is there has no submodules. If a previous container build
    # already left its libprojectM in vendor/, reuse that rather than
    # refusing: it was built in this same image, so it has the same floor.
    # The floor check at the end is what proves it.
    if [ -f "$HERE/vendor/linux/libprojectM-4.so.4" ]; then
        REBUILD_PM=0
        echo "==> No projectM source tree at the pin; reusing installer/vendor/linux/"
        echo "    That library is whatever was committed, NOT necessarily $PIN."
    else
        echo "No projectM source tree at the pinned commit. Clone one:" >&2
        echo "  git clone --recurse-submodules https://github.com/projectM-visualizer/projectm.git ~/projectm" >&2
        echo "  git -C ~/projectm checkout $PIN && git -C ~/projectm submodule update --init --recursive" >&2
        echo "Then re-run, or set PROJECTM_SRC to it." >&2
        exit 1
    fi
else
    echo "==> projectM source: $PROJECTM_SRC @ ${PIN:0:12}"
fi

echo "==> [1/3] Builder image ($BASE)"
docker build -q --build-arg "BASE=$BASE" -t "$IMAGE" -f "$HERE/Dockerfile.linux" "$HERE"

echo "==> [2/3] Building inside the container"
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e "REBUILD_PM=$REBUILD_PM" \
    -v "$REPO:/src" ${REBUILD_PM:+${PROJECTM_SRC:+-v "$PROJECTM_SRC:/projectm:ro"}} \
    "$IMAGE" bash -euo pipefail -c '
        if [ "$REBUILD_PM" = 1 ]; then
            # Out-of-tree build: /projectm is mounted read-only on purpose, so
            # a release build can never write into the source it came from.
            cmake -S /projectm -B /tmp/pmbuild -DCMAKE_BUILD_TYPE=Release \
                  -DENABLE_PLAYLIST=ON -DBUILD_TESTING=OFF \
                  -DCMAKE_INSTALL_PREFIX=/tmp/pmroot >/dev/null
            cmake --build /tmp/pmbuild -j"$(nproc)" >/dev/null
            cmake --install /tmp/pmbuild >/dev/null
            mkdir -p /src/installer/vendor/linux
            cp -L /tmp/pmroot/lib/libprojectM-4.so.4 \
                  /tmp/pmroot/lib/libprojectM-4-playlist.so.4 \
                  /src/installer/vendor/linux/
        fi

        python -m venv /tmp/venv
        /tmp/venv/bin/pip install -q --upgrade pip
        /tmp/venv/bin/pip install -q -r /src/requirements.txt pyinstaller

        VENV=/tmp/venv bash /src/installer/build_linux.sh
    '

echo "==> [3/3] Floor check"
EXE="$HERE/build/dist/BomsAIVisualizer/BomsAIVisualizer"
{
    objdump -T "$EXE" 2>/dev/null
    cat "$HERE"/build/dist/BomsAIVisualizer/_internal/*.so* >/dev/null 2>&1 || true
    for so in "$HERE"/build/dist/BomsAIVisualizer/_internal/*.so*; do
        objdump -T "$so" 2>/dev/null
    done
} | grep -oE 'GLIBC_[0-9]+\.[0-9]+' | sort -Vu | tail -1 |
    sed 's/^/    highest glibc symbol required: /'
echo "    (that is the oldest system this package will start on)"
