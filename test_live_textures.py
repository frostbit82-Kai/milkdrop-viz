#!/usr/bin/env python3
"""Checks textures answered for by us. Run: python test_live_textures.py

libprojectM's texture scanner reads seven extensions and none of them
animate, so a .gif in a texture pack is invisible to it. The texture-load
callback answers for those names instead, and may hand back an *existing* GL
texture id -- which projectM wraps without taking ownership and caches, so
the preset samples our texture every frame and a frame uploaded between
renders is live inside the preset.

The parts that can go quietly wrong are the ones tested here: which files are
claimed (claiming one projectM can read itself would take a working texture
away from it), what the callback answers a second time, and the pinned size
-- projectM recorded the width and height when it wrapped the id and never
asks again.

No display and no libprojectM needed: the GL upload is stubbed and the rest
is text and dictionaries.
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="viz-live-tex-")
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP, "data")
os.environ["LOCALAPPDATA"] = os.path.join(_TMP, "data")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "config")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import base64                                          # noqa: E402
import inspect                                         # noqa: E402
import viz_common as v                                 # noqa: E402
from viz_common import QImage                          # noqa: E402


def check(name, fn):
    fn()
    print(f"  ok  {name}")


#: Two frames, 4x4, red then blue -- the same fixture the burn-in suite uses,
#: and animated on purpose: a still would not prove the frame clock runs.
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


def _dir(name):
    d = os.path.join(_TMP, name)
    os.makedirs(d, exist_ok=True)
    return d


def _file(d, name, data=b"x"):
    p = os.path.join(d, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


class _Uploads(list):
    """Stands in for gl_upload_rgba and records what it was asked to put
    where. Returns the texture it was given, which is what the real one does
    once the texture exists."""

    def __call__(self, tex, size, w, h, buf):
        self.append((tex, tuple(size), w, h, len(buf)))
        return (tex or 1), (w, h)


# ── which files are ours to answer for ────────────────────────────────────

def a_picture_projectm_cannot_read_is_claimed():
    d = _dir("tex1")
    _file(d, "swirl.gif", TINY_GIF)
    assert v.live_texture_file("swirl", [d]) == os.path.join(d, "swirl.gif")


def a_picture_projectm_can_read_is_left_to_it():
    """Claiming a .png would take a working texture off the scanner and hand
    it to a second decoder for no gain -- and ours caps the size, so it would
    be a slightly worse picture as well."""
    d = _dir("tex2")
    _file(d, "cells.png")
    assert v.live_texture_file("cells", [d]) == ""


def video_counts_as_a_picture_here():
    d = _dir("tex3")
    _file(d, "clip.mp4")
    assert v.live_texture_file("clip", [d]).endswith("clip.mp4")


def the_first_directory_wins_like_projectms_own_scan():
    """The override directory leads the search path, and a texture swapped
    for a .gif has to beat the pack copy the same way a .png does."""
    first, second = _dir("tex4a"), _dir("tex4b")
    _file(second, "shared.gif", TINY_GIF)
    _file(first, "shared.webp")
    assert v.live_texture_file("shared", [first, second]).endswith("shared.webp")
    assert v.live_texture_file("shared", [second, first]).endswith("shared.gif")


def the_match_ignores_case_like_the_names_in_a_preset():
    d = _dir("tex5")
    _file(d, "Swirl.GIF", TINY_GIF)
    assert v.live_texture_file("swirl", [d]).endswith("Swirl.GIF")


def a_name_that_is_a_path_is_reduced_to_a_stem():
    """A preset asks for a texture by name and the name comes from a file
    somebody downloaded. "../../../.ssh/id_rsa" must not walk out of the
    directories the user pointed at."""
    d = _dir("tex6")
    _file(d, "id_rsa.gif", TINY_GIF)
    assert v.live_texture_file("../../../.ssh/id_rsa", [d]).endswith("id_rsa.gif")
    assert v.live_texture_file("", [d]) == ""
    assert v.live_texture_file("nothing", [d]) == ""


def a_directory_that_is_not_there_is_skipped_not_fatal():
    d = _dir("tex7")
    _file(d, "a.gif", TINY_GIF)
    assert v.live_texture_file("a", [os.path.join(_TMP, "gone"), d]) != ""


def the_decoder_is_chosen_from_our_list_not_the_burn_ins():
    """burn-in takes whatever a user picked in a dialog, including a .png. A
    live texture must not, for the reason above."""
    d = _dir("tex8")
    gif = _file(d, "two.gif", TINY_GIF)
    png = _file(d, "flat.png")
    src = v.frame_source(gif, v.LIVE_IMAGE_EXTENSIONS)
    assert type(src).__name__ == "_ImageFrames", type(src).__name__
    src.stop()
    assert v.frame_source(png, v.LIVE_IMAGE_EXTENSIONS) is None
    assert type(v.frame_source(png)).__name__ == "_ImageFrames", \
        "burn-in stopped taking a .png"


def every_format_named_in_the_dialogs_is_one_of_the_three_lists():
    assert ".gif" in v.PICTURE_EXTENSIONS and ".mp4" in v.PICTURE_EXTENSIONS
    assert ".png" in v.PICTURE_EXTENSIONS, "projectM's own list went missing"
    both = set(v.TEXTURE_EXTENSIONS) & set(v.LIVE_IMAGE_EXTENSIONS)
    assert not both, f"claimed and left to projectM at once: {both}"


# ── the texture, once it exists ───────────────────────────────────────────

def a_frame_of_the_wrong_size_is_scaled_to_the_one_projectm_recorded():
    """projectM took the width and height when it wrapped the id and never
    asks again. A GIF is allowed to change frame size mid-animation, and
    glTexSubImage2D with the wrong one is a corrupt texture, not an error."""
    got = _Uploads()
    real, v.gl_upload_rgba = v.gl_upload_rgba, got
    try:
        class Src:
            def next(self, now):
                return QImage(9, 3, QImage.Format.Format_RGBA8888)

            def stop(self):
                pass

        live = v._LiveTexture("n", "p", Src(), 7, 4, 4)
        assert live.refresh(0.0) is True
        assert got, "nothing was uploaded"
        tex, size, w, h, length = got[-1]
        assert (tex, size, w, h) == (7, (4, 4), 4, 4), got[-1]
        assert length == 4 * 4 * 4, length
    finally:
        v.gl_upload_rgba = real


def nothing_is_uploaded_when_the_decoder_has_nothing_new():
    """Every frame, for every live texture, sixty times a second. A still
    picture reports no delay and is held for ever; asking has to be free."""
    got = _Uploads()
    real, v.gl_upload_rgba = v.gl_upload_rgba, got
    try:
        class Src:
            def next(self, now):
                return None

            def stop(self):
                pass

        live = v._LiveTexture("n", "p", Src(), 7, 4, 4)
        assert live.refresh(0.0) is False
        assert not got, "uploaded a frame the decoder never handed over"
    finally:
        v.gl_upload_rgba = real


def stopping_a_live_texture_leaves_its_gl_texture_alone():
    """projectM is still holding the id: it wrapped ours without taking
    ownership, and a preset on screen keeps that wrapper alive. Deleting it
    here is a sampler reading a texture that is not there any more."""
    dead = []
    real, v.glDeleteTextures = v.glDeleteTextures, \
        lambda n, ids: dead.append(ids)
    try:
        class Src:
            stopped = False

            def next(self, now):
                return None

            def stop(self):
                Src.stopped = True

        live = v._LiveTexture("n", "p", Src(), 7, 4, 4)
        live.stop()
        assert Src.stopped, "the decoder was left running"
        assert live.tex == 7 and not dead, f"gave the texture back: {dead}"
        assert live.refresh(0.0) is False, "kept decoding after being stopped"
    finally:
        v.glDeleteTextures = real


def an_animated_picture_drives_the_texture_from_its_own_clock():
    d = _dir("tex9")
    src = v.frame_source(_file(d, "clock.gif", TINY_GIF), v.LIVE_IMAGE_EXTENSIONS)
    got = _Uploads()
    real, v.gl_upload_rgba = v.gl_upload_rgba, got
    try:
        live = v._LiveTexture("clock", "p", src, 5, 4, 4)
        assert live.refresh(0.0) is True, "never read the first frame"
        assert live.refresh(0.0) is False, "uploaded before the frame was due"
        assert live.refresh(1e6) is True, "never advanced to the next frame"
    finally:
        v.gl_upload_rgba = real
        src.stop()


# ── what the callback answers ─────────────────────────────────────────────

def an_incomplete_answer_is_no_answer():
    """projectM logs a warning and falls back to the filesystem for a texture
    id with a zero width, so half an answer is worse than none."""
    assert v.texture_load_answer((7, 4, 4)) == (7, 4, 4)
    assert v.texture_load_answer(None) is None
    assert v.texture_load_answer((0, 4, 4)) is None
    assert v.texture_load_answer((7, 0, 4)) is None
    assert v.texture_load_answer((7, 4, 0)) is None
    assert v.texture_load_answer(("x", 4, 4)) is None
    assert v.texture_load_answer((7, 4)) is None


def _renderer(opened=None):
    """A Renderer with only the fields the callback touches. Building a real
    one needs a window, a GL context and libprojectM."""
    r = object.__new__(v.Renderer)
    r._cfg = {}
    r._live_tex = {}
    r._live_missing = set()
    r.opened = []

    def open_one(key):
        r.opened.append(key)
        if opened is None:
            return None
        return v._LiveTexture(key, f"{key}.gif", None, len(r.opened), 4, 4)

    r._open_live_texture = open_one
    return r


def a_texture_already_answered_for_is_not_opened_twice():
    """Once per name is the whole reason this is not a per-frame cost --
    projectM caches the wrapper, but ResetTextures throws that away and asks
    again, and a second decoder for the same file would leak the first."""
    r = _renderer(opened=True)
    first = r._on_texture_wanted("swirl")
    assert first is not None
    assert r._on_texture_wanted("swirl") == first
    assert r._on_texture_wanted("SWIRL") == first, "case made a second one"
    assert r.opened == ["swirl"], r.opened


def a_name_we_have_no_file_for_is_only_looked_for_once():
    """Presets are full of stock texture names. Listing every texture
    directory for each of them, on every preset load, is the cost this
    avoids."""
    r = _renderer(opened=None)
    assert r._on_texture_wanted("cells") is None
    assert r._on_texture_wanted("cells") is None
    assert r.opened == ["cells"], r.opened


def the_ceiling_holds_and_is_not_a_per_frame_message():
    """They are never given back while projectM is running, so the ceiling is
    a ceiling rather than an eviction policy."""
    r = _renderer(opened=True)
    for i in range(v.LIVE_MAX_TEXTURES):
        assert r._on_texture_wanted(f"t{i}") is not None
    assert r._on_texture_wanted("one_too_many") is None
    assert len(r._live_tex) == v.LIVE_MAX_TEXTURES
    assert r._on_texture_wanted("one_too_many") is None
    assert "one_too_many" not in r.opened, "asked the disk after the ceiling"


def a_decoder_that_throws_declines_rather_than_taking_the_render_thread_down():
    r = _renderer(opened=True)

    def boom(_key):
        raise RuntimeError("no")

    r._open_live_texture = boom
    assert r._on_texture_wanted("bad") is None


def changing_a_texture_directory_forgets_what_was_not_found():
    """Otherwise a picture added in the new directory is never looked for:
    the name is on the not-found list from before it existed."""
    r = _renderer()
    r._pm = None
    r._live_missing.add("swirl")
    r._apply_texture_paths()
    assert not r._live_missing


def shutdown_stops_every_decoder():
    """A video's ffmpeg is a child process. Leaving one running leaves a
    decoder writing into a pipe nobody will read again."""
    r = _renderer(opened=True)
    stopped = []

    class Src:
        def __init__(self, n):
            self.n = n

        def next(self, now):
            return None

        def stop(self):
            stopped.append(self.n)

    for i in range(3):
        r._live_tex[f"t{i}"] = v._LiveTexture(f"t{i}", "p", Src(i), i + 1, 4, 4)
    r._stop_live_textures()
    assert sorted(stopped) == [0, 1, 2], stopped
    assert not r._live_tex and not r._live_missing


def a_gif_can_be_dropped_in_as_a_sprite():
    """The whole feature is unreachable otherwise: the file dialog and the
    copy-in both gate on the same list, and libprojectM's own is seven
    extensions with nothing animated in it."""
    src = _file(_dir("drop"), "wiggle.gif", TINY_GIF)
    stem, why = v.add_sprite_image(src)
    assert not why, why
    assert stem == "spr_wiggle", stem
    assert os.path.isfile(os.path.join(v.sprite_dir(), "spr_wiggle.gif"))
    assert ("spr_wiggle", os.path.join(v.sprite_dir(), "spr_wiggle.gif")) \
        in v.sprite_images(), "the sprite list cannot see it"
    assert v.remove_sprite_image("spr_wiggle"), "cannot be removed again"
    assert not os.path.isfile(os.path.join(v.sprite_dir(), "spr_wiggle.gif"))


def a_video_is_refused_by_name_not_by_a_wall_of_extensions():
    _, why = v.add_sprite_image(_file(_dir("drop"), "notes.txt"))
    assert why and "txt" in why, why
    assert len(why) < 160, f"a toast nobody can read: {why}"


def a_gif_can_stand_in_for_a_texture_and_be_taken_back_out():
    """Replace is how a stock texture becomes an animated one, and Restore
    has to find the same file again -- a .gif left behind would go on
    answering for that name for ever."""
    src = _file(_dir("drop"), "swap.gif", TINY_GIF)
    assert v.set_texture_override("cells", src) == ""
    assert v.texture_override_path("cells").endswith("cells.gif")
    assert "cells" in v.texture_overrides()
    assert v.clear_texture_override("cells")
    assert v.texture_override_path("cells") == ""


# ── wiring, read off the shipped source ───────────────────────────────────

def the_callback_is_registered_once_the_search_paths_are_in_place():
    # That one method, not the whole class: the second visual registers the
    # callback too, and checking the class source let a mutation that
    # unguarded this one go straight past -- the string was still there,
    # three thousand lines away, so the check could not fail.
    body = inspect.getsource(v.Renderer._init_projectm)
    assert "set_texture_load_callback(self._on_texture_wanted)" in body, \
        "nothing registers the texture callback"
    i = body.index("set_texture_load_callback(self._on_texture_wanted)")
    j = body.index("self._apply_texture_paths()")
    assert j < i, "registered before projectM knows where to look"
    assert 'self._cfg.get("live_textures", True)' in body, \
        "no way to turn it off"


def the_callback_and_projectm_are_given_the_same_directories():
    """Two lists in two orders is a texture that resolves one way for
    projectM and another way for us."""
    assert "self._texture_paths()" in inspect.getsource(
        v.Renderer._open_live_texture)
    assert "self._texture_paths()" in inspect.getsource(
        v.Renderer._apply_texture_paths)


def frames_go_in_before_the_preset_draws():
    """After render_frame the preset has already sampled the texture, so a
    frame uploaded there is one frame late, every frame."""
    body = inspect.getsource(v.Renderer.run)
    assert "self._live_this_frame()" in body, "nothing feeds the textures"
    assert body.index("self._live_this_frame()") < \
        body.index("self._render_visuals()"), "fed after the preset drew"
    assert "self._stop_live_textures()" in body, "decoders outlive the loop"


def the_c_callback_object_is_kept_alive():
    """ctypes does not hold a CFUNCTYPE for you. One that goes out of scope
    is freed, and C then calls into the hole it left -- which here is
    whatever projectM does when it asks for a texture."""
    body = inspect.getsource(v.ProjectM4.set_texture_load_callback)
    assert "self._tex_cb = self._tex_proto(trampoline)" in body


for name, fn in list(globals().items()):
    if callable(fn) and not isinstance(fn, type) \
            and not name.startswith(("_", "check")) \
            and getattr(fn, "__module__", None) == "__main__":
        check(name, fn)
print("all live texture checks passed")
sys.stdout.flush()
os._exit(0)
