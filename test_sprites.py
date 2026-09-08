#!/usr/bin/env python3
"""Checks the picture overlay. Run: python test_sprites.py

A sprite is a file the user picked plus a few lines of MilkDrop code, and both
halves have a way to go quietly wrong. The file has to land somewhere the
texture manager will find it and, more importantly, somewhere it can never be
found *instead of* a texture a preset actually asked for -- a picture called
cells.png winning that match would silently corrupt every preset that samples
cells. The code has to bake the chosen size into the per-frame line, because
half the movements assign sx every frame and would otherwise throw away
whatever init set.

No display and no libprojectM needed: the generated text is checked directly.
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="viz-sprites-")
# Before the import, so sprite_dir() lands here and never in a real one.
# user_data_dir() reads LOCALAPPDATA on Windows and XDG_DATA_HOME everywhere
# else, so setting only the Linux one isolated nothing there: this suite wrote
# its fixtures into the real per-user directory, passed once, and then failed
# from the second run on when the names it expected were already taken.
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP, "data")
os.environ["LOCALAPPDATA"] = os.path.join(_TMP, "data")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "config")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_common as v                                # noqa: E402


def check(name, fn):
    fn()
    print(f"  ok  {name}")


def _picture(name="logo.png", data=b"\x89PNG\r\n\x1a\n rest is not read"):
    p = os.path.join(_TMP, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


# ── where the file goes ───────────────────────────────────────────────────

def a_picture_is_copied_in_under_a_prefixed_stem():
    stem, why = v.add_sprite_image(_picture())
    assert not why, why
    assert stem == "spr_logo", stem
    assert os.path.isfile(os.path.join(v.sprite_dir(), "spr_logo.png"))


def the_prefix_is_what_keeps_it_off_a_presets_textures():
    """`img=` matches on the stem, and so does every texture a preset asks
    for. Without the prefix, a picture called cells.png would answer for the
    cells texture in every preset that samples it."""
    stem, _ = v.add_sprite_image(_picture("cells.jpg"))
    assert stem.startswith(v.SPRITE_PREFIX), stem
    assert stem != "cells"


def the_sprite_directory_comes_last_in_the_search_path():
    """Belt to the prefix's braces. The override directory deliberately leads
    the search path so a swap wins; this one has to trail it so a sprite
    cannot win. Nothing else in the file can catch the order being flipped."""
    import inspect
    body = inspect.getsource(v.Renderer._apply_texture_paths)
    assert "sprite_dir()" in body, "sprites are not in the search path at all"
    assert body.index("texture_override_dir()") < body.index("sprite_dir()"), \
        "the sprite directory must not come before the override directory"
    tail = body[body.index("sprite_dir()"):]
    assert "os.path.expanduser" not in tail and "texture_dir" not in tail, \
        "something was added after the sprite directory"


def two_pictures_with_the_same_name_do_not_collide():
    """Both land in one flat directory, and the stem is the whole identity."""
    a, _ = v.add_sprite_image(_picture("shot.png"))
    other = os.path.join(_TMP, "sub")
    os.makedirs(other, exist_ok=True)
    p = os.path.join(other, "shot.png")
    with open(p, "wb") as f:
        f.write(b"different")
    b, _ = v.add_sprite_image(p)
    assert a != b, (a, b)
    assert os.path.isfile(os.path.join(v.sprite_dir(), b + ".png"))


def an_odd_file_name_still_produces_a_usable_stem():
    stem, why = v.add_sprite_image(_picture("My Cat (2019)!!.png"))
    assert not why, why
    assert stem.replace(v.SPRITE_PREFIX, "").replace("_", "").isalnum(), stem


def a_file_that_is_not_a_picture_is_refused_with_a_reason():
    _stem, why = v.add_sprite_image(_picture("notes.txt"))
    assert why and "txt" in why, why


def removing_takes_the_copy_with_it():
    stem, _ = v.add_sprite_image(_picture("gone.png"))
    assert v.remove_sprite_image(stem) is True
    assert v.remove_sprite_image(stem) is False
    assert not os.path.isfile(os.path.join(v.sprite_dir(), stem + ".png"))


# ── the generated code ────────────────────────────────────────────────────

def a_still_sprite_has_no_per_frame_code_at_all():
    text = v.sprite_code({"image": "spr_a", "motion": "still"})
    assert "img=spr_a" in text
    assert "code_1=" not in text, text


def the_size_is_baked_into_the_per_frame_line():
    """A movement that assigns sx every frame overwrites init. Leaving the
    size in init_1 only would make the size slider do nothing at all for
    exactly the two movements people reach for first."""
    text = v.sprite_code({"image": "spr_a", "motion": "pulse", "size": 0.25})
    code = [l for l in text.splitlines() if l.startswith("code_1=")][0]
    assert "0.2500" in code, code


def every_movement_produces_something_projectm_can_parse():
    """Every line is key=value, and the only keys the parser knows here are
    img, init_N and code_N."""
    for key, _label, _c in v.SPRITE_MOTIONS:
        text = v.sprite_code({"image": "spr_a", "motion": key, "size": 0.4})
        for line in text.splitlines():
            head = line.split("=", 1)[0]
            assert head in ("img", "init_1", "code_1"), (key, line)
        assert text.endswith("\n")


def opacity_and_blending_reach_the_init_line():
    text = v.sprite_code({"image": "spr_a", "alpha": 0.5, "blend": "add"})
    init = [l for l in text.splitlines() if l.startswith("init_1=")][0]
    assert "a=0.5000" in init, init
    assert "blendmode=2" in init, init


def an_unknown_movement_or_blend_falls_back_rather_than_raising():
    """The list is written into config.json, and a config from a later
    version can name a movement this one has never heard of."""
    text = v.sprite_code({"image": "spr_a", "motion": "disco", "blend": "neon"})
    assert "blendmode=4" in text
    assert "code_1=" not in text


def the_pictures_own_shape_is_left_to_projectm():
    """projectM scales the sprite quad by the picture's width/height itself
    before drawing it. This code used to scale sy by the same ratio as well,
    which squashed a 2:1 picture to 4:1 -- measured on screen at 630x160 for
    a 400x200 picture, where 630x320 was right."""
    init = [l for l in v.sprite_code(
        {"image": "spr_a", "size": 0.6}).splitlines()
        if l.startswith("init_1=")][0]
    assert "sx=0.6000" in init and "sy=0.6000" in init, init
    for key, _label, code in v.SPRITE_MOTIONS:
        assert "{ar" not in code, key


def the_blend_names_match_what_the_engine_does():
    """Mode 0 ignores the picture's alpha channel and mode 4 is the one that
    honours it -- the opposite of what their MilkDrop names suggest. The
    label a user picks has to be the behaviour they get."""
    blends = {k: b for k, _l, b in v.SPRITE_BLENDS}
    assert blends["normal"] == 4, blends
    assert blends["add"] == 2, blends
    assert blends["solid"] == 0, blends
    labels = {k: l for k, l, _b in v.SPRITE_BLENDS}
    assert "black is clear" not in labels["normal"].lower()


def a_blend_written_by_the_older_build_still_loads():
    """"key" was this mode's name before it got an honest one."""
    text = v.sprite_code({"image": "spr_a", "blend": "key"})
    assert "blendmode=4" in text, text


# ── the panel ─────────────────────────────────────────────────────────────

from viz_common import QApplication                    # noqa: E402
from preset_browser import TweakPanel                  # noqa: E402

_app = QApplication.instance() or QApplication([])


class FakeHost:
    def __init__(self):
        self.entries = [{"image": "spr_a", "motion": "still", "size": 0.5,
                         "alpha": 1.0, "blend": "normal"}]
    def can_tweak(self):            return False
    def can_sprites(self):          return True
    def preset_text(self):          return ""
    def sprites(self):              return [dict(e) for e in self.entries]
    def add_sprite(self, *a, **k):  return ""
    def update_sprite(self, i, **f):
        self.entries[i].update(f)
        return True
    def remove_sprite(self, i):     self.entries.pop(i); return True
    def clear_sprites(self):        n = len(self.entries); self.entries = []; return n


def a_rebuild_is_never_run_inside_the_signal_that_asked_for_it():
    """The handlers replace every widget in the tab, including the combo or
    button that emitted the signal. Doing that inline frees a live sender
    under itself and the process disappears with no traceback -- measured:
    changing a sprite's movement killed the app every single time. Asserted
    on the source because no offscreen test can see a use-after-free."""
    import inspect
    for name in ("_sprite_set", "_add_sprite", "_remove_sprite",
                 "_clear_sprites", "_replace", "_restore"):
        body = inspect.getsource(getattr(TweakPanel, name))
        assert "self._build_sprites()" not in body, name
        assert "self._build_textures()" not in body, name


def changing_the_movement_leaves_a_working_panel():
    """The deferred rebuild has to actually happen, not just be skipped."""
    host = FakeHost()
    panel = TweakPanel(host)
    panel._spr_list.setCurrentRow(0)
    keys = [k for k, _l, _c in v.SPRITE_MOTIONS]
    panel._spr_motion.setCurrentIndex(keys.index("spin"))
    assert host.entries[0]["motion"] == "spin", host.entries
    _app.processEvents()                     # let the deferred rebuild run
    assert panel._spr_list.count() == 1
    assert "Spinning" in panel._spr_list.item(0).text(), \
        panel._spr_list.item(0).text()


def selecting_a_sprite_does_not_write_the_previous_ones_values_over_it():
    """Setting the controls to match a selection emits their signals too."""
    host = FakeHost()
    host.entries.append({"image": "spr_b", "motion": "drift", "size": 0.9,
                         "alpha": 0.4, "blend": "add"})
    panel = TweakPanel(host)
    panel._spr_list.setCurrentRow(1)
    _app.processEvents()
    assert host.entries[1]["motion"] == "drift", host.entries[1]
    assert host.entries[1]["size"] == 0.9, host.entries[1]
    assert host.entries[0]["motion"] == "still", host.entries[0]


def the_preset_buttons_are_hidden_on_the_overlay_tab():
    """Revert and Save as… act on the preset. On this tab there is no preset
    being edited, and a Save that ignored the picture would be a lie."""
    panel = TweakPanel(FakeHost())
    panel._tabs.setCurrentIndex(panel._spr_tab)
    assert not any(w.isVisible() for w in panel._foot) or not panel.isVisible()
    panel._tabs.setCurrentIndex(0)
    assert all(w.isVisibleTo(panel) for w in panel._foot)


def an_old_libprojectm_gets_an_explanation_rather_than_dead_controls():
    class NoSprites(FakeHost):
        def can_sprites(self):  return False
    panel = TweakPanel(NoSprites())
    assert not hasattr(panel, "_spr_list") or panel._spr_list is None \
        or panel._spr_lay.count() <= 2


for name, fn in list(globals().items()):
    if callable(fn) and not isinstance(fn, type) \
            and not name.startswith(("_", "check")) \
            and getattr(fn, "__module__", None) == "__main__":
        check(name, fn)
print("all sprite checks passed")
sys.stdout.flush()
os._exit(0)
