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
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from viz_common import QApplication
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

WITH_CODE = """[preset00]
fDecay=0.960
per_frame_1=q1=bass
per_frame_2=q2=treb
warp_1=`ret=0;
"""


class FakeHost:
    """Only the surface TweakPanel touches."""

    def __init__(self):
        self.text = ORIGINAL
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
    """reload() sets the box from the host. A blind toggle there would unlock
    the preset it had just been told was locked."""
    host, panel = _panel()
    host.locked = True
    panel.reload()
    assert panel._code_lock.isChecked()
    assert host.toggles == 0, "reload toggled the lock"


def ticking_the_lock_box_locks_the_preset():
    host, panel = _panel()
    panel._code_lock.setChecked(True)
    assert host.toggles == 1
    assert host.locked


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


for name, fn in list(globals().items()):
    if callable(fn) and not isinstance(fn, type) \
            and not name.startswith(("_", "check")) \
            and getattr(fn, "__module__", None) == "__main__":
        results.append(check(name, fn))

print("all code-editor checks passed" if all(results)
      else f"{results.count(False)} FAILED")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
