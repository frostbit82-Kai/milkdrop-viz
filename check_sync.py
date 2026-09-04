#!/usr/bin/env python3
"""Fail if this repo's viz_common.py has drifted from BomsAI Studio's copy.

The player embeds this visualiser and keeps its own copy of viz_common.py.
Nothing in either tree enforces that the two match, and they have silently
drifted twice already — once by 372 lines, once by a bug fix that reached only
one of them. This is the cheap check that notices.

    python check_sync.py           # compare, exit 1 if they differ
    python check_sync.py --fix     # copy THIS repo's copy over the player's
    python check_sync.py <path>    # compare against an explicit file

The player's copy is found via $BOMSAI_PLAYER_VIZ, an argument, or a short list
of usual places. Not finding it is not a failure — a fresh clone has no player
beside it and should still pass.
"""
import difflib
import hashlib
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MINE = HERE / "viz_common.py"
CANDIDATES = [
    "../Bomsai Player/visualizer/viz_common.py",
    "../../Bomsai Player/visualizer/viz_common.py",
    "../BomsAI Studio/visualizer/viz_common.py",
]


def find_theirs(argv) -> "Path | None":
    explicit = next((a for a in argv if not a.startswith("-")), None)
    for raw in (explicit, os.environ.get("BOMSAI_PLAYER_VIZ")):
        if raw:
            p = Path(raw).expanduser()
            return p if p.is_file() else None
    for rel in CANDIDATES:
        p = (HERE / rel).resolve()
        if p.is_file():
            return p
    return None


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv) -> int:
    if not MINE.is_file():
        print(f"check_sync: {MINE} is missing", file=sys.stderr)
        return 2

    theirs = find_theirs(argv)
    if theirs is None:
        print("check_sync: no BomsAI Studio copy found beside this repo — "
              "nothing to compare, skipping.")
        return 0

    if digest(MINE) == digest(theirs):
        print(f"check_sync: identical to {theirs}")
        return 0

    if "--fix" in argv:
        shutil.copyfile(MINE, theirs)
        print(f"check_sync: copied {MINE.name} over {theirs}")
        print("Commit it in that repo too — this only touched the file.")
        return 0

    # Deliberately a summary, not the whole diff: this runs to answer "have they
    # drifted", and a 400-line dump buries that answer.
    a = MINE.read_text().splitlines(keepends=True)
    b = theirs.read_text().splitlines(keepends=True)
    diff = list(difflib.unified_diff(a, b, "milkdrop-viz", "bomsai-player", n=0))
    changed = sum(1 for l in diff if l[:1] in "+-" and l[:3] not in ("+++", "---"))
    print(f"check_sync: DRIFTED from {theirs}")
    print(f"  {changed} changed lines, {sum(1 for l in diff if l.startswith('@@'))} hunks")
    for line in diff[:20]:
        print("  " + line.rstrip())
    if len(diff) > 20:
        print(f"  ... {len(diff) - 20} more diff lines")
    print("\nRun with --fix to copy this repo's copy over the player's, or "
          "reconcile by hand if the player has changes worth keeping.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
