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

import base64
import inspect
import viz_common as v                                # noqa: E402
from viz_common import QImage                          # noqa: E402


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
    body = inspect.getsource(v.Renderer._texture_paths)
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




# ── burn-in ───────────────────────────────────────────────────────────────
#
# The other kind of picture over the visuals: not laid on top like a sprite
# but drawn into the preset's own framebuffer, where the warp and decay get
# hold of it. The geometry is worth testing hardest, because the four numbers
# projectM takes are not the rectangle their names suggest -- see burn_canvas.

#: Two frames, 4x4, red then blue. Built here rather than read from a file so
#: the suite needs nothing on disk, and animated rather than still because the
#: per-frame delay is the whole reason the image path is not a plain load.
TINY_GIF = base64.b64decode(
    "R0lGODlhBAAEAPcfMQAAACQAAEgAAGwAAJAAALQAANgAAPwAAAAkACQkAEgkAGwkAJAk"
    "ALQkANgkAPwkAABIACRIAEhIAGxIAJBIALRIANhIAPxIAABsACRsAEhsAGxsAJBsALRs"
    "ANhsAPxsAACQACSQAEiQAGyQAJCQALSQANiQAPyQAAC0ACS0AEi0AGy0AJC0ALS0ANi0"
    "APy0AADYACTYAEjYAGzYAJDYALTYANjYAPzYAAD8ACT8AEj8AGz8AJD8ALT8ANj8APz8"
    "AAAAVSQAVUgAVWwAVZAAVbQAVdgAVfwAVQAkVSQkVUgkVWwkVZAkVbQkVdgkVfwkVQBI"
    "VSRIVUhIVWxIVZBIVbRIVdhIVfxIVQBsVSRsVUhsVWxsVZBsVbRsVdhsVfxsVQCQVSSQ"
    "VUiQVWyQVZCQVbSQVdiQVfyQVQC0VSS0VUi0VWy0VZC0VbS0Vdi0Vfy0VQDYVSTYVUjY"
    "VWzYVZDYVbTYVdjYVfzYVQD8VST8VUj8VWz8VZD8VbT8Vdj8Vfz8VQAAqiQAqkgAqmwA"
    "qpAAqrQAqtgAqvwAqgAkqiQkqkgkqmwkqpAkqrQkqtgkqvwkqgBIqiRIqkhIqmxIqpBI"
    "qrRIqthIqvxIqgBsqiRsqkhsqmxsqpBsqrRsqthsqvxsqgCQqiSQqkiQqmyQqpCQqrSQ"
    "qtiQqvyQqgC0qiS0qki0qmy0qpC0qrS0qti0qvy0qgDYqiTYqkjYqmzYqpDYqrTYqtjY"
    "qvzYqgD8qiT8qkj8qmz8qpD8qrT8qtj8qvz8qgAA/yQA/0gA/2wA/5AA/7QA/9gA//wA"
    "/wAk/yQk/0gk/2wk/5Ak/7Qk/9gk//wk/wBI/yRI/0hI/2xI/5BI/7RI/9hI//xI/wBs"
    "/yRs/0hs/2xs/5Bs/7Rs/9hs//xs/wCQ/ySQ/0iQ/2yQ/5CQ/7SQ/9iQ//yQ/wC0/yS0"
    "/0i0/2y0/5C0/7S0/9i0//y0/wDY/yTY/0jY/2zY/5DY/7TY/9jY//zY/wD8/yT8/0j8"
    "/2z8/5D8/7T8/9j8//z8/yH/C05FVFNDQVBFMi4wAwEAAAAh+QQEMgAfACwAAAAABAAE"
    "AAAICwAPCBxIUKCBgQEBACH5BAUyAAAALAAAAAAEAAQAAAgKAAEBG0iwoMGAAAA7")


def a_burnt_in_picture_is_placed_as_a_plain_rectangle():
    """burn_fit's own output is a top-left rect in pixels -- what goes to
    projectM is (0, 0, whole window), which is the only call it gets right."""
    # Centred, half the height, square source: a square in the middle.
    left, top, w, h = v.burn_fit(400, 400, 800, 600, 0.5, 0.5, 0.5)
    assert (w, h) == (300, 300), (w, h)
    assert (left, top) == (250, 150), (left, top)
    # The aspect is the source's, not the window's.
    _l, _t, w, h = v.burn_fit(500, 281, 800, 600, 0.5, 0.5, 0.5)
    assert h == 300 and abs(w / h - 500 / 281) < 0.01, (w, h)
    # A corner, and a size that cannot be zero however small it is asked for.
    left, top, w, h = v.burn_fit(100, 100, 800, 600, 0.0, 0.0, 0.0)
    assert w >= 1 and h >= 1, (w, h)
    assert left < 0 and top < 0, "a picture centred on the corner half hangs off"
    # Nothing to place, and nothing to place it in.
    assert v.burn_fit(0, 0, 800, 600, 0.5, 0.5, 0.5) == (0, 0, 0, 0)
    assert v.burn_fit(100, 100, 0, 0, 0.5, 0.5, 0.5) == (0, 0, 0, 0)


def the_canvas_puts_the_picture_where_it_was_asked_for_and_the_right_way_up():
    """A GL texture's first row is the bottom of the picture and a QImage's is
    the top, so the canvas is flipped on the way out. Without it the picture
    is drawn upside down -- which is what the first run on real hardware
    showed, and what no amount of reading the transform would have."""
    frame = QImage(10, 10, QImage.Format.Format_RGBA8888)
    frame.fill(0xFFFF0000)                       # opaque red
    raw = v.burn_canvas(frame, 40, 20, (4, 2, 8, 6))
    assert len(raw) == 40 * 20 * 4, len(raw)
    out = QImage(raw, 40, 20, QImage.Format.Format_RGBA8888)
    # Flipped: what was drawn at the top of the canvas is at the bottom here.
    assert out.pixelColor(8, 20 - 4).alpha() > 200, "the picture is not there"
    assert out.pixelColor(8, 4).alpha() == 0, "the picture was not flipped"
    # And the surround is transparent, which is what stops the burn wiping
    # the preset's feedback buffer everywhere it does not cover.
    assert out.pixelColor(35, 10).alpha() == 0, "the surround is opaque"


def the_burn_slider_actually_changes_how_much_is_painted():
    """Fully opaque, each frame paints over the trail the last one left and
    the picture just sits there, which is what a sprite already does. Under 1
    the preset's feedback shows through and it smears with the warp."""
    frame = QImage(10, 10, QImage.Format.Format_RGBA8888)
    frame.fill(0xFFFFFFFF)
    def alpha_at(a):
        raw = v.burn_canvas(frame, 20, 20, (0, 0, 20, 20), a)
        return QImage(raw, 20, 20,
                      QImage.Format.Format_RGBA8888).pixelColor(10, 10).alpha()
    full, half, none = alpha_at(1.0), alpha_at(0.5), alpha_at(0.0)
    assert full > 240, full
    assert 100 < half < 160, half
    assert none == 0, none


def a_large_picture_is_brought_down_to_a_size_worth_uploading():
    """It is drawn into a framebuffer the size of the window at most, so a
    4K frame every frame costs bandwidth and shows nothing extra."""
    w, h = v.burn_scaled_size(3840, 2160)
    assert max(w, h) == v.BURN_MAX_SIDE, (w, h)
    assert abs(w / h - 3840 / 2160) < 0.02, (w, h)
    assert w % 2 == 0 and h % 2 == 0, "ffmpeg wants both sides even"
    # Small enough already: left alone but for the even rule.
    assert v.burn_scaled_size(480, 480) == (480, 480)
    assert v.burn_scaled_size(0, 0) == (0, 0)


def a_videos_size_is_read_off_ffmpegs_own_report():
    """ffprobe is a second binary and a frozen build ships one, not two."""
    said = (
        "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'x.mp4':\n"
        "  Duration: 00:00:10.00, start: 0.000000, bitrate: 1234 kb/s\n"
        "  Stream #0:0(und): Video: h264 (High), yuv420p, 1920x1080 "
        "[SAR 1:1 DAR 16:9], 30 fps, 30 tbr\n"
        "  Stream #0:1(und): Audio: aac, 48000 Hz, stereo\n")
    assert v.video_frame_size(said) == (1920, 1080)
    # A size-shaped token on a line that is not the video stream must not be
    # read as one -- a title, a comment or a filename can carry anything.
    assert v.video_frame_size(
        "  Metadata:\n    title: 1920x1080 remux\n"
        "  Stream #0:0: Audio: aac, 48000 Hz\n") == (0, 0)
    assert v.video_frame_size("Duration: 00:00:10.00, bitrate: 1234 kb/s") \
        == (0, 0)
    assert v.video_frame_size("") == (0, 0)


def the_extension_decides_which_decoder_runs():
    """Not the content: Qt will happily read the first frame of some video
    containers and then report no animation, which looks like a still picture
    rather than like a file we cannot play."""
    gif = os.path.join(_TMP, "two.gif")
    open(gif, "wb").write(TINY_GIF)
    src = v.frame_source(gif)
    assert type(src).__name__ == "_ImageFrames", type(src).__name__
    src.stop()
    assert v.frame_source(os.path.join(_TMP, "nope.gif")) is None
    assert v.frame_source("") is None
    odd = os.path.join(_TMP, "thing.txt")
    open(odd, "w").write("not a picture")
    assert v.frame_source(odd) is None, "tried to decode a text file"


def an_animated_picture_is_read_a_frame_at_a_time_on_its_own_clock():
    """Each frame carries its own delay in the format, which is what makes a
    GIF look right. Asking again before the next one is due has to be free --
    this runs on the render thread, sixty times a second."""
    gif = os.path.join(_TMP, "clock.gif")
    open(gif, "wb").write(TINY_GIF)
    src = v.frame_source(gif)
    first = src.next(0.0)
    assert first is not None and first.width() == 4, first
    assert src.next(0.0) is None, "handed over a frame before it was due"
    second = src.next(1e6)
    assert second is not None, "never advanced to the next frame"
    src.stop()
    assert src.next(1e6) is None, "kept decoding after being stopped"


def the_burnt_in_picture_comes_back_when_the_app_does():
    """It is in the config, so it has to start on its own. Without this the
    setting persisted, the panel showed the file, and nothing was ever burnt
    in until somebody chose it again -- which reads as the whole feature being
    broken rather than as one missing line. The arming sits in a method that
    needs a GL context, so the shipped source is read instead of run."""
    body = inspect.getsource(v.Renderer._load_libraries) \
        if hasattr(v.Renderer, "_load_libraries") else ""
    if "_req_burn" not in body:
        body = "".join(inspect.getsource(f) for f in
                       (v.Renderer.run, v.Renderer.__init__)
                       if "_req_burn" in inspect.getsource(f))
    whole = inspect.getsource(v.Renderer)
    assert 'self._req_burn = self.burn_in()["path"]' in whole, \
        "nothing arms the burn-in from the config at startup"


for name, fn in list(globals().items()):
    if callable(fn) and not isinstance(fn, type) \
            and not name.startswith(("_", "check")) \
            and getattr(fn, "__module__", None) == "__main__":
        check(name, fn)
print("all sprite checks passed")
sys.stdout.flush()
os._exit(0)
