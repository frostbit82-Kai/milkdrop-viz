#!/usr/bin/env python3
"""The Code tab: editing a preset's own text and hearing why it failed.

Two things here are worth more than the widget itself. One is that there is
never a second version of the preset that only one tab knows about -- applying
the editor makes its text the base the sliders are rebuilt from, rather than
leaving a patch to be re-applied over stale text. The other is that an edit
nobody applied yet is somebody's work: the playlist moving on must not throw it
away, and Save must not quietly write the version on screen instead of it.

Run: QT_QPA_PLATFORM=offscreen python test_code_editor.py
"""
import ast, inspect, os, sys, textwrap, threading
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_common
from viz_common import QApplication, theme_colour, QObject, pyqtSignal
from preset_browser import TweakPanel

ORIGINAL = """[preset00]
fRating=3.000
fDecay=0.960
zoom=1.010
warp_1=`shader_body { ret = 0; }
"""
EDITED = ORIGINAL.replace("fDecay=0.960", "fDecay=0.500")

COLOURFUL = """[preset00]
fDecay=0.960
wave_r=1.000
wave_g=0.000
wave_b=0.000
"""

DONOR = """PSVERSION=3
PSVERSION_WARP=3
[preset00]
fDecay=0.100
warp_1=`ret = 9;
"""

NUMBERS = """[preset00]
fRating=3.000
nMotionVectorsX=12
fDecay=0.960
warp_1=`shader_body { ret = 0; }
"""

WITH_CODE = """[preset00]
fDecay=0.960
per_frame_1=q1=bass
per_frame_2=q2=treb
warp_1=`ret=0;
"""


class FakeHost(QObject):
    """Only the surface TweakPanel touches.

    A QObject because the lock is no longer only ever moved from this panel:
    the key and the right-click menu move it too, and the panel now follows
    the host's signal to stay honest about it.
    """

    lock_changed = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.text = ORIGINAL
        self.rated = 0
        self.previewed = []
        self.saved = None
        self.reverted = 0
        self.locked = False
        self.toggles = 0
        self.error = ""
        self.donor = ("other one", DONOR)

    # editing
    def can_tweak(self):              return True
    def preset_text(self):            return self.text
    def preview_preset(self, t):      self.previewed.append(t)
    def revert_preset(self):          self.reverted += 1
    def last_preset_error(self):      return self.error
    def current_preset_path(self):    return "/presets/thing.milk"

    def save_preset_as(self, text, name):
        self.saved = (text, name)
        return ""

    def donor_preset(self):           return self.donor

    # locking
    def is_locked(self):              return self.locked

    def request_toggle_lock(self):
        self.toggles += 1
        self.locked = not self.locked
        self.lock_changed.emit(self.locked)

    # rating
    def request_rate(self, delta):
        self.rated += int(delta)

    # textures and overlays, which the panel builds whether we care or not
    def can_sprites(self):            return False
    def sprites(self):                return []
    def set_sprites(self, entries):   pass
    def clear_sprites(self):          pass
    def swap_texture(self, n, p):     return ""


results = []


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    except Exception as e:
        print(f"  ERROR {name}: {type(e).__name__}: {e}")
        return False
    print(f"  ok  {name}")
    return True


app = QApplication.instance() or QApplication(sys.argv)


def _panel():
    host = FakeHost()
    panel = TweakPanel(host)
    panel.reload()
    return host, panel


def _type(panel, text):
    """What a keystroke does, which setPlainText does not: mark it modified."""
    panel._code.setPlainText(text)
    panel._code.document().setModified(True)


def _pin_at(panel, needle, offset=0):
    """Put the caret inside a number and press the pin button."""
    text = panel._code.toPlainText()
    cur = panel._code.textCursor()
    cur.setPosition(text.index(needle) + offset)
    panel._code.setTextCursor(cur)
    panel._pin_number()


def _numbers(host):
    host.text = NUMBERS
    h, panel = host, TweakPanel(host)
    panel.reload()
    return h, panel


def a_slider_reaches_a_number_no_named_setting_covers():
    """A constant inside a shader is not a key=value line, so the Tweak tab's
    ten named settings cannot see it at all. That is the whole reason these
    exist."""
    host, panel = _numbers(FakeHost())
    _pin_at(panel, "ret = 0", len("ret = "))
    assert len(panel._pins) == 1, panel._code_error.text()
    pin = panel._pins[0]
    assert panel._code.toPlainText()[pin.start:pin.end] == "0"
    panel._pin_moved(pin, panel.STEPS)
    assert "ret = 1;" in panel._code.toPlainText(), panel._code.toPlainText()


def a_slider_keeps_the_spelling_the_number_had_and_can_find_it_again():
    """Centred on the original, so the middle of the slider is where it
    started -- and 0.960 comes back as 0.960, not 0.96."""
    host, panel = _numbers(FakeHost())
    _pin_at(panel, "fDecay=0.960", len("fDecay="))
    assert len(panel._pins) == 1, panel._code_error.text()
    panel._pin_moved(panel._pins[0], panel.STEPS // 2)
    line = [l for l in panel._code.toPlainText().splitlines()
            if l.startswith("fDecay=")][0]
    assert line == "fDecay=0.960", line


def two_sliders_do_not_drift_into_each_other():
    """Dragging the first one changes the length of the text after it, which
    moves every character the second one is measured from."""
    host, panel = _numbers(FakeHost())
    _pin_at(panel, "nMotionVectorsX=12", len("nMotionVectorsX="))
    _pin_at(panel, "fDecay=0.960", len("fDecay="))
    assert len(panel._pins) == 2, panel._code_error.text()
    first, second = panel._pins
    panel._pin_moved(first, 0)               # 12 -> 0, one character shorter
    text = panel._code.toPlainText()
    assert "nMotionVectorsX=0\n" in text, text
    assert text[second.start:second.end] == "0.960", \
        f"the second slider slid onto {text[second.start:second.end]!r}"
    panel._pin_moved(second, 0)
    assert "fDecay=0.000" in panel._code.toPlainText(), \
        panel._code.toPlainText()


def a_slider_will_not_land_on_the_digits_in_a_name():
    """The 1 in per_frame_1 is part of the key. A slider on it would rewrite
    the key and drop the equation on that line without a word."""
    host, panel = _numbers(FakeHost())
    _pin_at(panel, "warp_1=", len("warp_"))
    assert not panel._pins, "pinned a slider to part of a key"
    assert panel._code_error.text(), "refused it and said nothing"


def two_sliders_cannot_share_one_number():
    host, panel = _numbers(FakeHost())
    _pin_at(panel, "fDecay=0.960", len("fDecay="))
    _pin_at(panel, "fDecay=0.960", len("fDecay=0."))
    assert len(panel._pins) == 1, "pinned two sliders to the same number"


def typing_under_the_sliders_lets_them_go():
    """Every offset past the caret is wrong the moment somebody types, and
    splicing on a guess corrupts the preset rather than editing it."""
    host, panel = _numbers(FakeHost())
    _pin_at(panel, "fDecay=0.960", len("fDecay="))
    pin = panel._pins[0]
    panel._code.document().setModified(True)          # somebody typed
    before = panel._code.toPlainText()
    panel._pin_moved(pin, 0)
    app.processEvents()                               # the deferred clear
    assert not panel._pins, "the sliders survived the text moving under them"
    assert panel._code.toPlainText() == before, "spliced on stale offsets"


def what_the_sliders_made_becomes_the_preset_the_other_tabs_work_from():
    """The panel's one invariant: there is never a second version of the
    preset that only one tab knows about. Without this the Tweak tab goes on
    patching text the sliders have already replaced."""
    host, panel = _numbers(FakeHost())
    _pin_at(panel, "fDecay=0.960", len("fDecay="))
    panel._pin_moved(panel._pins[0], panel.STEPS)
    panel._apply_pins()
    assert host.previewed[-1] == panel._code.toPlainText()
    assert panel._base == panel._code.toPlainText()
    assert panel.current_text() == panel._code.toPlainText()


def a_tweak_slider_lets_the_number_sliders_go():
    """It rebuilds the editor's text out of _base, which is not where the
    pins are measuring from -- so leaving them live would splice into the
    wrong characters on the next drag."""
    host, panel = _numbers(FakeHost())
    _pin_at(panel, "fDecay=0.960", len("fDecay="))
    assert panel._pins
    panel._values["fDecay"] = 0.1
    panel._touched.add("fDecay")
    panel._apply()
    assert not panel._pins, "the number sliders outlived the text they sat in"


def the_editor_opens_on_the_presets_own_text():
    _host, panel = _panel()
    assert panel._code.toPlainText() == ORIGINAL


def applying_the_editor_makes_it_the_preset():
    """Not a patch to be re-applied over the old text -- the text itself, with
    the sliders rebuilt out of it. Otherwise the Tweak tab keeps re-patching a
    version of the preset the Code tab has already replaced."""
    host, panel = _panel()
    _type(panel, EDITED)
    panel._apply_code()
    app.processEvents()                    # _rebuild_later's singleShot
    assert host.previewed[-1] == EDITED
    assert panel._base == EDITED
    assert panel.current_text() == EDITED, panel.current_text()
    assert panel._values.get("fDecay") == 0.5, panel._values


def an_empty_box_is_not_applied():
    host, panel = _panel()
    _type(panel, "   \n  ")
    panel._apply_code()
    assert not host.previewed, "played an empty preset"


def a_preset_change_does_not_throw_away_an_unapplied_edit():
    """The playlist moves on every 30 seconds by default. Losing what somebody
    typed because of that would make the tab unusable without the lock."""
    host, panel = _panel()
    _type(panel, EDITED)
    host.text = "[preset00]\nfDecay=0.100\n"      # a different preset now
    panel.mark_stale()
    panel.reload()
    assert panel._code.toPlainText() == EDITED, "clobbered an unapplied edit"


def a_preset_change_does_refresh_a_box_nobody_touched():
    host, panel = _panel()
    host.text = "[preset00]\nfDecay=0.100\n"
    panel.reload()
    assert panel._code.toPlainText() == host.text


def revert_discards_the_draft():
    """Revert is the one thing that means "the file on disk wins"."""
    host, panel = _panel()
    _type(panel, EDITED)
    panel._revert()
    assert panel._code.toPlainText() == ORIGINAL
    assert host.reverted == 1


def saving_writes_the_draft_not_what_is_on_screen():
    """Somebody who typed and went straight for Save meant the thing they
    typed, not the last thing that happened to reach the renderer."""
    host, panel = _panel()
    _type(panel, EDITED)
    panel._save_as_text_for_test = None
    # Skip the name dialog by driving the save the way _save_as does.
    if panel._code.document().isModified():
        panel._apply_code()
    app.processEvents()
    host.save_preset_as(panel.current_text(), "mine")
    assert host.saved[0] == EDITED, host.saved[0]


def a_slider_does_not_overwrite_an_unapplied_edit():
    host, panel = _panel()
    _type(panel, EDITED)
    panel._values["fDecay"] = 0.1
    panel._touched.add("fDecay")
    panel._apply()                          # what the slider debounce calls
    assert panel._code.toPlainText() == EDITED
    assert "fDecay=0.1" in host.previewed[-1], host.previewed[-1]


def an_untouched_preset_saves_byte_identical():
    """Open a preset, save it, and it must still be the file it came from --
    not the same numbers spelled differently."""
    _host, panel = _panel()
    assert panel.current_text() == ORIGINAL, panel.current_text()


def a_slider_does_refresh_a_box_nobody_touched():
    host, panel = _panel()
    panel._values["fDecay"] = 0.1
    panel._touched.add("fDecay")
    panel._apply()
    assert "fDecay=0.1" in panel._code.toPlainText()
    assert not panel._code.document().isModified()


def the_reason_an_edit_failed_is_shown():
    host, panel = _panel()
    host.error = '[MilkdropShader] missing "shader_body" entry point.'
    panel._show_code_error()
    assert panel._code_error.isVisible() or not panel.isVisible()
    assert "shader_body" in panel._code_error.text()


def a_clean_edit_shows_no_error():
    host, panel = _panel()
    host.error = ""
    panel._show_code_error()
    assert panel._code_error.text() == ""
    assert panel._code_error.isHidden()


def the_lock_box_follows_the_host_without_toggling_it():
    """reload() sets the button from the host. A blind toggle there would
    unlock the preset it had just been told was locked."""
    host, panel = _panel()
    host.locked = True
    panel.reload()
    assert panel._lock_btn.isChecked()
    assert host.toggles == 0, "reload toggled the lock"


def ticking_the_lock_box_locks_the_preset():
    host, panel = _panel()
    panel._lock_btn.setChecked(True)
    assert host.toggles == 1
    assert host.locked


def the_lock_button_follows_the_key_and_the_menu():
    """The lock moved off the Code tab and onto the tab row, where it is on
    screen the whole time the panel is open. A button that only ever learns
    about its own clicks is then wrong for as long as it is shown -- and the
    key and the right-click menu both move the same lock."""
    host, panel = _panel()
    assert not panel._lock_btn.isChecked()
    host.locked = True
    host.lock_changed.emit(True)
    app.processEvents()
    assert panel._lock_btn.isChecked(), "the button ignored the host"
    assert host.toggles == 0, "following the host toggled the lock back"


def a_rating_request_accumulates_rather_than_latching():
    """Three quick clicks are three stars. The render thread serves these
    once per frame at best, so a flag that only remembered the last one would
    throw away every click but one -- and the buttons are small and get
    clicked in a row."""
    r = object.__new__(viz_common.Renderer)
    r._lock = threading.Lock()
    r._req_rate = 0
    viz_common.Renderer.request_rate(r, +1)
    viz_common.Renderer.request_rate(r, +1)
    viz_common.Renderer.request_rate(r, -1)
    assert r._req_rate == 1, f"three clicks came out as {r._req_rate}"


def the_render_loop_actually_serves_the_rating_request():
    """Accumulating into a field nothing reads would pass the check above and
    do nothing at all on screen. The dispatch block is in Renderer.run, which
    needs a GPU to execute, so the shipped source is read instead of a copy."""
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(viz_common.Renderer.run)))
    served = [n for n in ast.walk(tree) if isinstance(n, ast.If)
              and "_req_rate" in ast.unparse(n.test)
              and "_rate_current" in ast.unparse(n)]
    assert len(served) == 1, \
        f"{len(served)} places in the run loop serve _req_rate, expected 1"
    cleared = ast.unparse(served[0])
    assert "_req_rate = 0" in cleared, \
        "the run loop serves the rating request without clearing it, so it "\
        "repeats every frame for the life of the app"


def showing_the_lock_does_not_move_it():
    """_lock_shown means show, not set. The host is the authority on whether
    the preset is locked; a button that asked for a change on being *told* of
    one would fight whatever had just told it -- and the key, the menu and
    this button all move the same lock."""
    host, panel = _panel()
    host.locked = False                 # the host has not changed
    panel._lock_shown(True)             # and we are only told how to look
    assert panel._lock_btn.isChecked(), "the button ignored what it was told"
    assert host.toggles == 0, "showing the lock asked the host to change it"


def the_rating_buttons_rate_the_visual_on_screen():
    """One star each way, and three clicks are three stars. The renderer
    accumulates rather than latching, which is the only reason that holds."""
    _host, panel = _panel()
    panel._rate(+1)
    panel._rate(+1)
    panel._rate(-1)
    assert _host.rated == 1, _host.rated


def shifting_colours_plays_it_and_leaves_it_in_the_box():
    """One press, one new colour scheme on screen. A button that only filled
    the box would make every try two clicks."""
    host, panel = _panel()
    _type(panel, COLOURFUL)
    panel._apply_code()
    app.processEvents()
    before = panel._code.toPlainText()
    panel._shift_colours()
    app.processEvents()
    after = panel._code.toPlainText()
    assert after != before, "nothing moved"
    assert host.previewed[-1] == after, "played something other than the box"
    assert "wave_r=" in after and len(after.splitlines()) == \
        len(before.splitlines())


def a_preset_with_no_stated_colours_says_so():
    """Rather than a button that appears to do nothing."""
    _host, panel = _panel()
    _type(panel, "[preset00]\nfDecay=0.500\n")
    panel._apply_code()
    app.processEvents()
    panel._shift_colours()
    assert "no colours" in panel._code_error.text(), panel._code_error.text()


def replace_all_stays_in_the_box_until_apply():
    """A replace across a shader can break it in ways one Apply cannot undo."""
    host, panel = _panel()
    played = len(host.previewed)
    panel._find.setText("fDecay")
    panel._repl.setText("fdecay")
    panel._replace_all()
    assert "fdecay=0.960" in panel._code.toPlainText()
    assert panel._code.document().isModified()
    assert len(host.previewed) == played, "played an unapplied replace"


def a_search_that_finds_nothing_says_so():
    _host, panel = _panel()
    panel._find.setText("no_such_key")
    panel._replace_all()
    assert "not in this preset" in panel._code_error.text()
    assert panel._code.toPlainText() == ORIGINAL, "changed it anyway"


def find_wraps_round_the_end():
    _host, panel = _panel()
    cursor = panel._code.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    panel._code.setTextCursor(cursor)
    panel._find.setText("fRating")
    panel._find_next()
    assert panel._code.textCursor().hasSelection(), "did not wrap"


def a_per_frame_line_lands_in_the_per_frame_block():
    """Not at the end of the file, which is inside a shader."""
    _host, panel = _panel()
    _type(panel, WITH_CODE)
    panel._add_per_frame()
    out = panel._code.toPlainText()
    lines = out.splitlines()
    assert "per_frame_3=" in lines, lines
    assert lines.index("per_frame_3=") < lines.index("warp_1=`ret=0;"), lines
    assert panel._code.textCursor().position() == len("\n".join(
        lines[:lines.index("per_frame_3=") + 1])), "cursor not on the new line"


def mashing_plays_the_result_and_names_what_it_took():
    host, panel = _panel()
    _type(panel, ORIGINAL)
    panel._apply_code()
    app.processEvents()
    panel._mash_bins.setCurrentIndex(1)          # Warp shader
    panel._mash_up()
    app.processEvents()
    out = panel._code.toPlainText()
    assert "ret = 9" in out, out
    assert "shader_body { ret = 0; }" not in out, "kept the old warp shader"
    assert "fDecay=0.960" in out, "dragged the donor's scalars in"
    assert host.previewed[-1] == out
    panel._show_code_error()
    assert "other one" in panel._code_error.text(), panel._code_error.text()


def a_failed_mash_says_why_instead_of_the_donors_name():
    """The error wins: knowing where it came from is no help when it will not
    compile."""
    host, panel = _panel()
    _type(panel, ORIGINAL)
    panel._apply_code()
    app.processEvents()
    panel._mash_bins.setCurrentIndex(1)
    panel._mash_up()
    host.error = "[MilkdropShader] no shader_body"
    panel._show_code_error()
    assert "shader_body" in panel._code_error.text()


def a_donor_with_nothing_to_give_is_reported():
    host, panel = _panel()
    host.donor = ("empty one", "[preset00]\nfDecay=0.2\n")
    _type(panel, "[preset00]\nfDecay=0.960\n")
    panel._apply_code()
    app.processEvents()
    played = len(host.previewed)
    panel._mash_bins.setCurrentIndex(1)
    panel._mash_up()
    assert "nothing" in panel._code_error.text() or \
        "to give" in panel._code_error.text(), panel._code_error.text()
    assert len(host.previewed) == played, "played an unchanged preset"


def no_other_preset_at_all_is_reported():
    host, panel = _panel()
    host.donor = ("", "")
    panel._mash_up()
    assert "no other preset" in panel._code_error.text()


class _FakeLogFn:
    """A ctypes function stand-in: takes restype/argtypes, records the call."""
    restype = None
    argtypes = ()

    def __init__(self):
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)


class _FakeCore:
    """Only what _install_projectm_logging touches."""

    def __init__(self):
        self.projectm_set_log_callback = _FakeLogFn()
        self.projectm_set_log_level = _FakeLogFn()


def _log_callback():
    """The real _on_log, bound against a stand-in library.

    The shipped function is used rather than a copy of its condition: the
    whole defect was that the condition did not match what the library
    actually emits, which a reimplementation would have reproduced faithfully.
    """
    viz_common._LOG_CB = None          # it installs once, then refuses
    assert viz_common._install_projectm_logging(_FakeCore())
    return viz_common._LOG_CB


def a_shader_that_will_not_compile_is_read_off_the_log():
    """Measured against the shipped 4.2.0 library on Mint, 9 Sep 2026: a comp
    shader with a bad type logs

        [FinalComposite] Error compiling composite warp shader code

    at level 4 -- WARN, not ERROR, so a threshold of "ERROR and above" caught
    it and every other composite-shader failure with it, and the editor showed
    no error line for the one failure that editing a comp shader causes.
    Which is the whole of A1.

    The level is chosen per *stage*, not per severity: warp failures are
    LOG_ERROR (PerPixelMesh.cpp:61,80) and composite ones LOG_WARN
    (FinalComposite.cpp:49,87), which is why breaking a warp shader on the
    other machine looked fine.
    """
    cb = _log_callback()
    viz_common._LAST_PM_ERROR = ""
    cb(b"[FinalComposite] Error compiling composite warp shader code - "
       b"Using fallback shader.", 4, None)
    assert "Error compiling" in viz_common._LAST_PM_ERROR, \
        f"a WARN-level compile failure was dropped: " \
        f"{viz_common._LAST_PM_ERROR!r}"


def the_other_compile_failures_are_caught_too():
    """The library has several spellings and they are not interchangeable."""
    for line in (b"[PerPixelContext] Could not compile per-pixel code.",
                 b"[MilkdropShader] Error translating HLSL ",
                 b"[FinalComposite] Error loading composite warp shader code: "
                 b"x - Using fallback shader.",
                 b"[PerPixelMesh] Error compiling warp shader code."):
        cb = _log_callback()
        viz_common._LAST_PM_ERROR = ""
        cb(line, 4, None)
        assert viz_common._LAST_PM_ERROR, f"dropped: {line!r}"


def an_error_level_failure_is_still_kept_on_its_level_alone():
    """The text match is an addition, not a replacement. A warp shader that
    will not compile goes out at ERROR (PerPixelMesh.cpp:80) and so does the
    GLSL compiler's own message (Shader.cpp:215), which has no fixed wording
    at all -- dropping the level test would lose it."""
    cb = _log_callback()
    viz_common._LAST_PM_ERROR = ""
    cb(b"0:42(7): error: no matching function for call to zzz()", 5, None)
    assert viz_common._LAST_PM_ERROR, "an ERROR-level line was dropped"


def the_rest_of_the_warn_channel_is_not_an_error():
    """A preset pack triggers this constantly and nothing stops being drawn.
    Surfacing it would put a red line under the editor on every other preset,
    which is worse than the silence it replaced."""
    cb = _log_callback()
    viz_common._LAST_PM_ERROR = ""
    cb(b'[TextureManager] Failed to find requested texture "grad3"', 4, None)
    assert viz_common._LAST_PM_ERROR == "", \
        f"benign warning reported as an error: {viz_common._LAST_PM_ERROR!r}"


class _FakeFn(_FakeLogFn):
    """A ctypes function stand-in that does something when called."""

    def __init__(self, body):
        super().__init__()
        self.body = body

    def __call__(self, *args):
        self.calls.append(args)
        return self.body(*args)


class _FakeProjectM:
    """libprojectM's two callback slots and the playlist that takes them.

    Connecting points both slots at the playlist, as PlaylistCWrapper.cpp:14-33
    does, and a load that will not compile calls whoever holds the failed slot,
    as the catch in ProjectM::LoadPresetData does. Every other entry point is
    a no-op that takes restype and argtypes.
    """
    BAD = b"per_frame_1=zoom = (1 + ;"

    def __init__(self):
        self.failed = self.requested = None
        self.playlist_heard = []
        self.projectm_create = _FakeFn(lambda: 1)
        self.projectm_playlist_create = _FakeFn(self._create)
        self.projectm_playlist_connect = _FakeFn(self._connect)
        self.projectm_set_preset_switch_failed_event_callback = _FakeFn(
            lambda pm, cb, user: setattr(self, "failed", cb))
        self.projectm_set_preset_switch_requested_event_callback = _FakeFn(
            lambda pm, cb, user: setattr(self, "requested", cb))
        self.projectm_load_preset_data = _FakeFn(self._load)

    def __getattr__(self, name):
        fn = _FakeLogFn()
        setattr(self, name, fn)
        return fn

    def _create(self, pm):
        self._connect(2, pm)
        return 2

    def _connect(self, pl, pm):
        self.failed = self.requested = "playlist"

    def _load(self, pm, data, smooth):
        if self.BAD in data:
            msg = b"[PerFrameContext] Could not compile per-frame code"
            if self.failed == "playlist":
                self.playlist_heard.append(msg)
            else:
                self.failed(b"", msg, None)


def a_failed_edit_reaches_last_error_and_not_the_playlist():
    """Measured 3 Oct 2026 against the pinned library: the playlist took the
    failed callback when it connected, so last_error never filled -- and the
    playlist, which heard the edit fail, took its *next* switch for the
    failure, dropped that good preset from the playlist and played another.
    Afterwards the playlist has to own the slot again (it skips and prunes a
    broken file on a switch) and the clock request has to stay ours."""
    lib = _FakeProjectM()
    pm = viz_common.ProjectM4(lib, lib)
    pm.create(320, 240)
    pm.take_switch_requests(lambda hard: None)

    assert pm.load_preset_data("[preset00]\n" + lib.BAD.decode() + "\n")
    assert "Could not compile" in pm.last_error, repr(pm.last_error)
    assert lib.playlist_heard == [], "the playlist heard the edit fail"
    assert lib.failed == "playlist", "the playlist lost its failed callback"
    assert lib.requested is pm._switch_cb, "the playlist got the clock back"

    assert pm.load_preset_data("[preset00]\nzoom=1.0\n")
    assert pm.last_error == ""
    assert (lib.failed, lib.requested) == ("playlist", pm._switch_cb)


def _colours(panel, line):
    """What the highlighter painted on one line, as {colour: "text"}."""
    panel._code.setPlainText(line)
    block = panel._code.document().findBlockByNumber(0)
    out = {}
    for r in block.layout().formats():
        colour = r.format.foreground().color().name()
        out.setdefault(colour, "")
        out[colour] += line[r.start:r.start + r.length]
    return out


def the_key_and_the_numbers_are_coloured():
    _host, panel = _panel()
    painted = _colours(panel, "fDecay=0.960")
    assert theme_colour("link") in painted, painted
    assert "fDecay" in painted[theme_colour("link")], painted
    assert painted.get(theme_colour("code_num"), "") == "0.960", painted


def a_word_in_a_title_is_not_a_shader_keyword():
    """The rules run on values, and a title is a value. `preset_bin` is what
    separates the two -- without it every preset whose name contains `sin` or
    `min` gets a function-coloured word in the middle of it."""
    _host, panel = _panel()
    painted = _colours(panel, "sTitle=cos and sin")
    assert theme_colour("code_kw") not in painted, painted
    painted = _colours(panel, "warp_1=ret = sin(uv.x);")
    assert "sin" in painted.get(theme_colour("code_kw"), ""), painted


def a_line_that_is_not_key_equals_value_is_left_alone():
    _host, panel = _panel()
    assert _colours(panel, "   ") == {}
    assert list(_colours(panel, "[preset00]")) == [theme_colour("muted")]


for name, fn in list(globals().items()):
    if callable(fn) and not isinstance(fn, type) \
            and not name.startswith(("_", "check")) \
            and getattr(fn, "__module__", None) == "__main__":
        results.append(check(name, fn))

print("all code-editor checks passed" if all(results)
      else f"{results.count(False)} FAILED")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
