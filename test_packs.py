#!/usr/bin/env python3
"""Checks for the preset-pack installer. Run: python test_packs.py

The download itself is not exercised here -- that needs the network and is
verified by hand. What is worth pinning down is the archive handling: where a
pack ends up pointing, and that a hostile zip cannot write outside the folder
the user chose.
"""
import os
import sys
import tempfile
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_common as v                              # noqa: E402


def check(name, fn):
    fn()
    print(f"  ok  {name}")


def _tree(root, *rel_files):
    for rel in rel_files:
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
    return root


def a_wrapped_pack_points_at_the_wrapper():
    """GitHub zips are always <repo>-<branch>/..., and loading is recursive,
    so the wrapper is the right answer however deeply it nests."""
    with tempfile.TemporaryDirectory() as d:
        _tree(d, "cream-master/Reaction/Ripples/a.milk",
                 "cream-master/Fractal/b.milk")
        assert v._preset_root(d) == os.path.join(d, "cream-master")


def a_flat_pack_points_at_the_folder_itself():
    with tempfile.TemporaryDirectory() as d:
        _tree(d, "a.milk", "b.milk")
        assert v._preset_root(d) == d


def two_packs_side_by_side_point_at_the_parent():
    with tempfile.TemporaryDirectory() as d:
        _tree(d, "packA/a.milk", "packB/b.milk")
        assert v._preset_root(d) == d


def an_archive_with_no_presets_reports_nothing():
    with tempfile.TemporaryDirectory() as d:
        _tree(d, "docs/readme.txt")
        assert v._preset_root(d) == ""


def a_textures_folder_is_found_at_any_depth():
    with tempfile.TemporaryDirectory() as d:
        _tree(d, "pack/Cat/a.milk", "pack/textures/t.jpg")
        root = v._preset_root(d)
        found = ""
        for dirpath, dirnames, _f in os.walk(d):
            for name in dirnames:
                if name.lower() == "textures":
                    found = os.path.join(dirpath, name)
                    break
            if found:
                break
        assert root == os.path.join(d, "pack")
        assert found == os.path.join(d, "pack", "textures")


def a_zip_cannot_escape_the_chosen_folder():
    """zipfile will follow ../ out of the destination. These archives are
    chosen by the user from wherever they downloaded them."""
    with tempfile.TemporaryDirectory() as d:
        archive = os.path.join(d, "evil.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../escaped.milk", "x")
        dest = os.path.join(d, "dest")
        os.makedirs(dest)
        try:
            with zipfile.ZipFile(archive) as zf:
                v._safe_extract(zf, dest)
        except ValueError:
            assert not os.path.exists(os.path.join(d, "escaped.milk"))
            return
        raise AssertionError("the escaping member was extracted")


def a_normal_zip_still_extracts():
    with tempfile.TemporaryDirectory() as d:
        archive = os.path.join(d, "ok.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("pack/a.milk", "x")
        dest = os.path.join(d, "dest")
        with zipfile.ZipFile(archive) as zf:
            assert v._safe_extract(zf, dest)
        assert os.path.exists(os.path.join(dest, "pack", "a.milk"))


def every_pack_has_a_page_and_a_sane_shape():
    for pack in v.PRESET_PACKS:
        assert pack["page"].startswith("https://"), pack["name"]
        assert pack["detail"] and pack["size"], pack["name"]
        if pack["url"]:
            assert pack["url"].startswith("https://"), pack["name"]


for _name, _fn in list(globals().items()):
    if callable(_fn) and not _name.startswith(("_", "check")) \
            and getattr(_fn, "__module__", None) == "__main__":
        check(_name, _fn)
print("all pack checks passed")
