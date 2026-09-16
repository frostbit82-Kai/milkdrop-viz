#!/usr/bin/env python3
"""Checks two visuals at once. Run: python test_dual.py

Two projectM instances in one GL context, each drawing into a framebuffer of
its own, mixed by one shader pass. The shader and the framebuffers need a GPU
and are shot in the rig instead; what is checked here is everything around
them, which is where the mistakes actually were:

  * which entry point each visual is rendered with -- the plain one targets
    framebuffer 0 whatever is bound, so using it here drew both visuals to
    the window and then painted two empty textures over the top. A black
    window, no GL error and nothing in the log.
  * which instance a control reaches. `self._pm` *is* the focus, and a
    hundred call sites read it without knowing there are two.
  * what has to reach both: the audio, the window size, and nothing else.

No display and no libprojectM: the instances and the blender are stand-ins.
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="viz-dual-")
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP, "data")
os.environ["LOCALAPPDATA"] = os.path.join(_TMP, "data")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "config")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ast                                             # noqa: E402
import inspect                                         # noqa: E402
import textwrap                                        # noqa: E402
import viz_common as v                                 # noqa: E402
import config as app_config                            # noqa: E402


def check(name, fn):
    fn()
    print(f"  ok  {name}")


class FakePM:
    """Only what the render loop and the dual code ask a projectM for."""

    def __init__(self, name="a"):
        self.name = name
        self.can_render_fbo = True
        self.drawn = []          # ("fbo", id) or ("window",)
        self.pcm = 0
        self.sizes = []
        self.position = 0
        self.destroyed = False
        self.nexts = 0

    def render_frame(self):
        self.drawn.append(("window",))

    def render_frame_fbo(self, fbo):
        self.drawn.append(("fbo", fbo))

    def add_pcm_float(self, samples, count, channels):
        self.pcm += 1

    def set_window_size(self, w, h):
        self.sizes.append((w, h))

    def next_preset(self, hard_cut=True):
        self.nexts += 1

    def playlist_position(self):
        return self.position

    def set_playlist_position(self, index, hard_cut=True):
        self.position = index

    def destroy(self):
        self.destroyed = True

    def current_preset_name(self):
        return f"preset-{self.name}"


class FakeBlend:
    def __init__(self, ok=True):
        self.ok = ok
        self.error = ""
        self.drawn = []
        self.dropped = False

    def ready(self, w, h):
        return self.ok

    def target(self, i):
        return 100 + i

    def draw(self, w, h, strength):
        self.drawn.append((w, h, strength))

    def drop(self):
        self.dropped = True


class FakeSignal:
    def __init__(self):
        self.sent = []

    def emit(self, *a):
        self.sent.append(a if len(a) != 1 else a[0])


class FakeBridge:
    def __init__(self):
        for name in ("show_toast", "dual_changed", "focus_changed",
                     "visual_changed", "crossfade_changed",
                     "preset_changed", "status_message"):
            setattr(self, name, FakeSignal())


class _Lock:
    """`with` and nothing else -- these tests are single-threaded."""
    def __enter__(self): return self
    def __exit__(self, *a): return False


def renderer(second=True, blend=True, cfg=None):
    """A Renderer with only the fields the dual code touches."""
    r = object.__new__(v.Renderer)
    r._cfg = dict(cfg or {})
    r._bridge = FakeBridge()
    r._pms = [FakePM("a"), FakePM("b") if second else None]
    r._pm = r._pms[0]
    r._focus = 0
    r._blend = FakeBlend(blend) if second else None
    r._v1_on = True
    r._window2 = None
    r._preset_start = 0.0
    r._burnt = []
    r._burn_this_frame = lambda pm=None: r._burnt.append(
        getattr(pm, "name", None))
    r._drawable_size = lambda: (800, 600)
    r._emit_current_preset = lambda: None
    return r


# ── which entry point draws which visual ──────────────────────────────────

def one_visual_draws_straight_to_the_window():
    """The ordinary path, and it has to stay free: no framebuffer, no shader
    pass, nothing new between the preset and the screen."""
    r = renderer(second=False)
    r._render_visuals()
    assert r._pms[0].drawn == [("window",)], r._pms[0].drawn


def two_visuals_each_draw_into_a_framebuffer_of_their_own():
    """render_frame targets framebuffer 0 whatever is bound when it is
    called. Using it here drew both visuals to the window and the blend then
    painted two empty textures over the top -- a black window, no GL error,
    and nothing in the log to say why."""
    r = renderer()
    r._render_visuals()
    assert r._pms[0].drawn == [("fbo", 100)], r._pms[0].drawn
    assert r._pms[1].drawn == [("fbo", 101)], r._pms[1].drawn
    assert r._blend.drawn == [(800, 600, v.DUAL_CROSSFADE_DEFAULT)], \
        r._blend.drawn


def the_crossfade_comes_from_the_config_every_frame():
    """So the menu's fader moves the picture while it is still being held."""
    r = renderer(cfg={"dual_crossfade": 0.2})
    r._render_visuals()
    assert r._blend.drawn[-1][2] == 0.2, r._blend.drawn


def a_config_from_before_the_fader_keeps_the_number_it_had():
    """2.1.6 and earlier wrote `dual_opacity`. Ignoring it would snap every
    upgraded config back to the middle on first run, which is a visible
    change to something the user set on purpose."""
    r = renderer(cfg={"dual_opacity": 0.8})
    r._render_visuals()
    assert r._blend.drawn[-1][2] == 0.8, r._blend.drawn


def the_new_key_wins_over_the_old_one():
    """Both present means an upgraded config that has since been touched."""
    r = renderer(cfg={"dual_opacity": 0.8, "dual_crossfade": 0.3})
    r._render_visuals()
    assert r._blend.drawn[-1][2] == 0.3, r._blend.drawn


def a_faded_out_visual_is_not_drawn_at_all():
    """The shader multiplies that end by zero, so its target is never
    sampled -- rendering into it is work nobody can see. Parking the fader
    at one end has to cost one visual, not two."""
    r = renderer(cfg={"dual_crossfade": 0.0})
    r._render_visuals()
    assert r._pms[0].drawn == [("fbo", 100)], r._pms[0].drawn
    assert r._pms[1].drawn == [], \
        f"visual 2 drew at a fader that hides it: {r._pms[1].drawn}"

    r = renderer(cfg={"dual_crossfade": 1.0})
    r._render_visuals()
    assert r._pms[0].drawn == [], \
        f"visual 1 drew at a fader that hides it: {r._pms[0].drawn}"
    assert r._pms[1].drawn == [("fbo", 101)], r._pms[1].drawn


def switching_visual_one_off_pins_the_fader_to_visual_two():
    """The switch and the fader are the same mechanism underneath: "off" is
    the far end. What must not happen is the stored fader position showing
    visual 1 through a switch that says it is off."""
    r = renderer(cfg={"dual_crossfade": 0.5})
    r._v1_on = False
    r._render_visuals()
    assert r._blend.drawn[-1][2] == 1.0, r._blend.drawn
    assert r._pms[0].drawn == [], r._pms[0].drawn


def a_window_each_draws_both_visuals_whatever_the_fader_says():
    """A screen each is not a mix: both are wanted whole. Carrying the fader
    into it would blank one of the two monitors at either end -- and the
    fader sitting at an end is the ordinary case for anyone who used it
    before sending a visual away."""
    r = renderer(cfg={"dual_crossfade": 0.0})
    r._window2 = object()
    drew = []
    r._draw_second_window = lambda: drew.append(True)
    r._render_visuals()
    assert r._pms[0].drawn == [("fbo", 100)], r._pms[0].drawn
    assert r._pms[1].drawn == [("fbo", 101)], \
        f"visual 2 was skipped on a monitor of its own: {r._pms[1].drawn}"
    assert drew == [True], "the second window was not drawn"
    # And this window is left showing visual 1: the recorder and the
    # thumbnail both read its back buffer after this returns.
    assert r._blend.drawn[-1][2] == 0.0, r._blend.drawn


def visual_one_switched_off_is_not_drawn_on_the_monitor_it_left_behind():
    """The two checks above are each right on their own and the pair was not.

    A screen each means the main window shows visual 1 -- `draw(0.0)` -- and
    it did that whether or not visual 1's own switch was on, so switching it
    off with visual 2 away on another monitor left visual 1 on screen at full
    strength with the tick beside it saying "off". This window has nothing to
    show in that state, and black is also the honest thing for the recorder
    and the thumbnail to read out of it."""
    r = renderer(cfg={"dual_crossfade": 0.5})
    r._v1_on = False
    r._window2 = object()
    drew, cleared = [], []
    r._draw_second_window = lambda: drew.append(True)
    r._clear_window = lambda w, h: cleared.append((w, h))
    r._render_visuals()
    assert drew == [True], "the second window was not drawn"
    assert r._pms[1].drawn == [("fbo", 101)], r._pms[1].drawn
    assert r._pms[0].drawn == [], \
        f"visual 1 was rendered with its switch off: {r._pms[0].drawn}"
    assert r._blend.drawn == [], \
        f"visual 1 was put on the main window with its switch off: " \
        f"{r._blend.drawn}"
    assert cleared, "the main window was left showing the last frame"


def a_blender_that_cannot_be_built_falls_back_to_one_visual():
    """A black window is the worst possible answer to a driver that will not
    compile the shader."""
    r = renderer(blend=False)
    asked = []

    def stop(on):
        asked.append(on)
        r._pms[1] = None

    r._do_set_dual = stop
    r._render_visuals()
    # Both halves, because falling through to the one-visual path happens
    # either way -- what has to also happen is the second visual being put
    # down, or every later frame runs the failing blender again.
    assert asked == [False], f"the dead blender was left on: {asked}"
    assert r._pms[0].drawn == [("window",)], r._pms[0].drawn


def a_frame_with_no_size_is_skipped_not_taken_as_a_failure():
    """A window mid-way through a fullscreen change, or minimised, reports a
    drawable of nothing. Falling through on that put the second visual down
    for the rest of the session over one frame, and said only "[dual] off" --
    which reads as the user having turned it off. Seen for real in the rig."""
    r = renderer()
    r._drawable_size = lambda: (0, 0)
    asked = []
    r._do_set_dual = lambda on: asked.append(on)
    r._render_visuals()
    assert asked == [], f"one sizeless frame ended dual mode: {asked}"
    assert r._pms[0].drawn == [], r._pms[0].drawn
    assert r._blend.drawn == [], r._blend.drawn
    # And the next frame, once the window has a size again, draws both.
    r._drawable_size = lambda: (800, 600)
    r._render_visuals()
    assert r._pms[1].drawn == [("fbo", 101)], r._pms[1].drawn


def one_visual_still_draws_when_the_window_has_no_size():
    """The skip is only for the two-visual path: with one visual there is no
    second instance to take down, and the old behaviour is what it was."""
    r = renderer(second=False)
    r._drawable_size = lambda: (0, 0)
    r._render_visuals()
    assert r._pms[0].drawn == [("window",)], r._pms[0].drawn


def the_burn_in_goes_into_the_visual_being_adjusted():
    """Matching every other control. Burning it into both would double it,
    and into the wrong one would put it where nobody asked."""
    r = renderer()
    r._render_visuals()
    assert r._burnt == ["a"], r._burnt
    r._focus = 1
    r._burnt.clear()
    r._render_visuals()
    assert r._burnt == ["b"], r._burnt


# ── which instance a control reaches ──────────────────────────────────────

def the_focus_is_what_self_pm_points_at():
    """Not a lookup the call sites do -- they never learn there are two."""
    r = renderer()
    r._do_set_focus(1)
    assert r._pm is r._pms[1]
    assert r.focus() == 1
    assert r._cfg["dual_focus"] == 1
    assert r._bridge.focus_changed.sent == [1]
    r._do_set_focus(0)
    assert r._pm is r._pms[0]


def focusing_a_visual_that_is_not_there_lands_on_the_first():
    """The alternative is self._pm being None and every control raising."""
    r = renderer(second=False)
    r._do_set_focus(1)
    assert r._pm is r._pms[0] and r.focus() == 0


def the_preset_clock_restarts_when_the_focus_moves():
    """The timer belongs to whichever visual is being adjusted. Without this
    a swap can be followed immediately by an auto-advance of a preset that
    has only just come up."""
    r = renderer()
    r._preset_start = 0.0
    r._do_set_focus(1)
    assert r._preset_start > 0.0


# ── starting and stopping ─────────────────────────────────────────────────

def stopping_puts_everything_back():
    r = renderer()
    second = r._pms[1]
    r._focus = 1
    r._pm = second
    r._stop_dual()
    assert second.destroyed, "the second instance was left running"
    assert r._pms[1] is None
    assert r._pm is r._pms[0] and r._focus == 0
    assert r._blend is None, "the framebuffers were left behind"


def turning_it_on_when_it_is_already_on_does_nothing():
    r = renderer()
    r._start_dual = lambda: (_ for _ in ()).throw(AssertionError("restarted"))
    r._do_set_dual(True)


def turning_it_off_says_so_and_remembers():
    r = renderer()
    r._do_set_dual(False)
    assert r._cfg["dual_enabled"] is False
    assert r._bridge.dual_changed.sent == [False]


def the_second_visuals_monitor_comes_back_with_it():
    """`visual2_display` is a setting like the two switches beside it, and
    `_stop_dual` drops the window without clearing it -- so the menu, which
    ticks the monitor straight out of config, claimed visual 2 was on a
    screen it was not on. Reachable twice over: switch it off and on again,
    and every launch after the feature has been used once."""
    r = renderer(second=False, cfg={"visual2_display": 1})

    def start():
        r._pms[1] = FakePM("b")
        r._blend = FakeBlend(True)

    r._start_dual = start
    r._req_win2 = None
    r._do_set_dual(True)
    assert r._req_win2 == 1, \
        f"the monitor was not asked for again: {r._req_win2!r}"


def a_second_visual_with_no_monitor_behind_it_asks_for_nothing():
    """-1 is the early return in `_do_set_window2`, so queueing it costs
    nothing -- but queue the *stored* value, not a blank, or the case above
    never fires."""
    r = renderer(second=False)

    def start():
        r._pms[1] = FakePM("b")
        r._blend = FakeBlend(True)

    r._start_dual = start
    r._req_win2 = None
    r._do_set_dual(True)
    assert r._req_win2 == -1, r._req_win2


def a_build_that_cannot_render_to_a_framebuffer_declines():
    r = renderer(second=False)
    r._pms[0].can_render_fbo = False
    r._pm = r._pms[0]
    assert not r.can_dual()
    r._start_dual()
    assert r._pms[1] is None
    said = " ".join(str(s) for s in r._bridge.show_toast.sent)
    assert "two visuals" in said, f"declined without saying why: {said!r}"


def the_second_visual_opens_on_a_different_preset():
    """Both on position 0 is two identical visuals blended into one, which
    reads as the feature not having worked at all."""
    src = inspect.getsource(v.Renderer._start_dual)
    assert "set_playlist_position" in src, "never moved off position 0"
    assert "count // 2" in src, "a random jump can land on the same preset"


# ── what has to reach both ────────────────────────────────────────────────

def _loop_block(marker: str, end: str) -> str:
    """One stretch of the real run loop, pulled out to run on a stub."""
    body = inspect.getsource(v.Renderer.run)
    at = body.index(marker)
    # Back to the start of that line, or dedent has nothing to measure the
    # first line against and the second line reads as an unexpected indent.
    start = body.rfind("\n", 0, at) + 1
    return textwrap.dedent(body[start:body.index(end, start)])


def the_audio_reaches_both_visuals():
    """A second visual that cannot hear the music is a still picture."""
    code = _loop_block("chunk = self._audio.get_chunk()", "# ── Render")
    r = renderer()

    class Chunk:
        def flatten(self):
            return self

        def tolist(self):
            return [0.0] * 8

    r._audio = type("A", (), {"get_chunk": staticmethod(lambda: Chunk())})()
    exec(compile(ast.parse(code), "<audio>", "exec"), {}, {"self": r})
    assert r._pms[0].pcm == 1 and r._pms[1].pcm == 1, "one visual went deaf"


def a_resize_reaches_both_visuals():
    src = inspect.getsource(v.Renderer.run)
    assert src.count("pm.set_window_size(nw, nh)") == 1
    assert src.count("pm.set_window_size(gw, gh)") == 1
    assert "self._pm.set_window_size" not in src, \
        "only the focused visual is told the window changed size"


def auto_advance_moves_the_other_visual_too():
    """Everything a person does goes to the focused visual alone. The clock
    is the exception: a second visual stuck on one preset all night is not
    two visualisers, it is one and a wallpaper."""
    code = _loop_block("if not self._locked:", "# ── Cursor")
    r = renderer()
    r._locked = False
    r._preset_duration = 0.0
    moved = []
    r._do_next = lambda hard: moved.append(hard)
    exec(compile(ast.parse(code), "<advance>", "exec"),
         {"_time": type("T", (), {"monotonic": staticmethod(lambda: 999.0)}),
          "print": lambda *a, **k: None},
         {"self": r})
    assert moved == [False], moved
    assert r._pms[1].nexts == 1, "the second visual never moved"
    assert r._pms[0].nexts == 0, "the focused one was moved twice"


def no_thumbnail_is_taken_off_a_blend():
    """The window is the two of them mixed. Filing that under one preset's
    name puts a picture in the browser the preset cannot produce."""
    r = renderer()
    r._want_snapshot = True
    r._thumb_done = False
    r.grabbed = 0
    r._grab_frame = lambda: (r.__dict__.__setitem__("grabbed", 1), None)[1]
    r._maybe_thumbnail()
    assert r.grabbed == 0, "grabbed a frame of both visuals at once"
    assert r._bridge.show_toast.sent, "asked for a snapshot and got silence"


# ── the settings ──────────────────────────────────────────────────────────

def every_dual_setting_has_a_default():
    for key in ("visual1_enabled", "dual_enabled", "dual_crossfade",
                "dual_quality", "dual_focus", "visual2_display"):
        assert key in app_config.DEFAULT_CONFIG, key
    assert app_config.DEFAULT_CONFIG["dual_enabled"] is False, \
        "two visuals by default doubles what every machine has to draw"
    assert app_config.DEFAULT_CONFIG["visual1_enabled"] is True, \
        "a fresh install with no visual on is a black window"
    assert app_config.DEFAULT_CONFIG["dual_quality"] == "low"
    assert app_config.DEFAULT_CONFIG["visual2_display"] == -1, \
        "a fresh install must not claim a monitor it was never given"


def the_second_visual_keeps_its_own_quality_when_settings_change():
    """_apply_pm_settings reads quality out of the dict it is handed, and
    self._pm is whichever visual is being adjusted -- so applying the one
    config to self._pm would give the second visual the first one's mesh."""
    src = inspect.getsource(v.Renderer.run)
    assert "self._apply_pm_settings(self._cfg, self._pms[0])" in src
    assert 'second["quality"] = self._cfg.get("dual_quality"' in src
    # And the same at the moment it is switched on, or it comes up at the
    # first visual's quality and only drops to its own if Settings is opened.
    start = inspect.getsource(v.Renderer._start_dual)
    assert 'cfg["quality"] = self._cfg.get("dual_quality"' in start
    assert "self._apply_pm_settings(cfg, second)" in start


def the_shader_reaches_both_ends_of_the_fader():
    """The one piece of GLSL in the application, and nothing else here can
    run it -- there is no GL context in an offscreen suite, so a broken blend
    would ship looking fine. What is checkable without one is the arithmetic:
    pull the weight each visual is multiplied by straight out of the shipped
    source and evaluate it at both ends.

    The bug this exists for is the shape the shader had until 2.1.7, which
    scaled only the second visual. That reaches "visual 1" and "both at once"
    and can never reach "visual 2 alone" -- so the right-hand half of a
    crossfader did nothing, which is exactly the sort of thing that is
    obvious on screen and invisible in a diff."""
    import re
    weights = {}
    for name, sampler in (("a", "visual_a"), ("b", "visual_b")):
        m = re.search(
            r"vec3 %s = texture\(%s, uv\)\.rgb\s*(.*?);" % (name, sampler),
            v._BLEND_FRAG)
        assert m, f"the shader no longer samples {sampler} the expected way"
        # "1.0" plus whatever the shader multiplies the sample by, which is
        # the scalar weight -- in Python, because GLSL's `*` on a float is.
        weights[name] = "1.0 " + m.group(1).strip()

    def weigh(name, x):
        return eval(weights[name], {"__builtins__": {}}, {"crossfade": x})

    assert weigh("a", 0.0) == 1.0, f"visual 1 is not whole at 0: {weights['a']}"
    assert weigh("a", 1.0) == 0.0, \
        f"visual 1 still shows at the far end: {weights['a']}"
    assert weigh("b", 0.0) == 0.0, \
        f"visual 2 shows at the near end: {weights['b']}"
    assert weigh("b", 1.0) == 1.0, f"visual 2 is not whole at 1: {weights['b']}"
    assert weigh("a", 0.5) == weigh("b", 0.5), \
        "the middle of the fader does not weigh the two evenly"
    # And the screen itself, which is what keeps either end exact: with one
    # term at zero, 1-(1-0)(1-t) is t.
    assert "1.0 - (1.0 - a) * (1.0 - b)" in v._BLEND_FRAG, \
        "the screen blend went away; either end is only exact because of it"


def the_fader_keys_exist_and_move_it_both_ways():
    for action in ("crossfade_back", "crossfade_fwd", "toggle_visual1"):
        assert action in v.DEFAULT_KEYMAPS, action
        assert action in v.ACTION_LABELS, action
    assert v.DEFAULT_KEYMAPS["crossfade_back"] != \
        v.DEFAULT_KEYMAPS["crossfade_fwd"], "one key cannot fade both ways"
    assert 0.0 < v.CROSSFADE_STEP < 0.5, v.CROSSFADE_STEP


def the_last_visual_standing_cannot_be_switched_off():
    """Both off is a black window, and the menu that would undo it is drawn
    over that window. The greyed checkbox is the first line of defence; this
    is the one behind it, for the key binding that has no checkbox to grey."""
    r = renderer(second=False)
    r._lock = _Lock()
    r._req_dual = r._req_v1 = None
    r.request_visual(0, False)
    assert r._req_v1 is None, "the only visual on screen was switched off"
    # With both up, either may go.
    r = renderer()
    r._lock = _Lock()
    r._req_dual = r._req_v1 = None
    r.request_visual(0, False)
    assert r._req_v1 is False, "a visual that is not the last one would not go"


def both_visuals_answer_for_their_own_live_textures():
    src = inspect.getsource(v.Renderer._start_dual)
    assert "set_texture_load_callback(self._on_texture_wanted)" in src, \
        "the second visual cannot see a .gif the first one can"
    assert "set_texture_search_paths" in src


def there_is_a_key_and_a_menu_entry_for_both_halves():
    for action in ("toggle_dual", "swap_focus"):
        assert action in v.DEFAULT_KEYMAPS, action
        assert action in v.ACTION_LABELS, action
    assert any(k == "dual" for k, _l, _a in v.MENU_ENTRIES)


for name, fn in list(globals().items()):
    if callable(fn) and not isinstance(fn, type) \
            and not name.startswith(("_", "check", "renderer")) \
            and getattr(fn, "__module__", None) == "__main__":
        check(name, fn)
print("all dual checks passed")
sys.stdout.flush()
os._exit(0)
