#!/usr/bin/env python3
"""Checks preset tweaking and texture swaps. Run: python test_tweak.py

A .milk file is somebody else's work, so the rules here are narrow: read the
scalars, change only the lines asked for, and never touch anything else. The
part worth testing hardest is `preset_driven_keys` — about half the library
animates `zoom` from its own per-frame code, and a slider for it in those
presets would move and do nothing.

Texture swaps are the same shape: the override sits in front of the stock file
rather than over it, so a test that got that backwards would be a test that
eats a 2.5 GB texture pack. XDG is redirected before the import.

No display needed. Runs against the real preset library when one is
configured, and against fixtures otherwise.
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="viz-tweak-")
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP, "data")
os.environ["LOCALAPPDATA"] = os.path.join(_TMP, "data")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_common as v                                # noqa: E402

assert _TMP in v.texture_override_dir(), v.texture_override_dir()

PRESET = """MILKDROP_PRESET_VERSION=201
PSVERSION=2
[preset00]
fRating=5.000000
fGammaAdj=1.900
fDecay=0.980
zoom=1.011000
rot=0.020000
warp=1.234000
dx=0.000000
dy=0.000000
fWaveScale=1.500000
fWaveAlpha=1.000000
fVideoEchoAlpha=0.000000
per_frame_1=zoom = zoom + 0.1*sin(time);
per_frame_2=q1 = bass_att;
per_pixel_1=rot = rot*1.01;
warp_1=`   float2 uv2 = uv;
comp_1=`   ret = tex2D(sampler_worms, uv).xyz;
comp_2=`   ret = ret + tex2D(sampler_pw_noise_lq, uv).xyz;
comp_3=`   ret = ret * tex2D(sampler_fc_main, uv).xyz;
"""


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    print(f"  ok  {name}")
    return True


results = []


# ── reading ───────────────────────────────────────────────────────────────

def scalars_come_back_as_numbers():
    assert v.preset_scalar(PRESET, "zoom") == 1.011
    assert v.preset_scalar(PRESET, "fDecay") == 0.98
    assert v.preset_scalar(PRESET, "nope") is None


def a_key_is_matched_case_insensitively():
    """Presets in the wild spell fDecay as fdecay and FDECAY."""
    assert v.preset_scalar(PRESET, "fdecay") == 0.98
    assert v.preset_scalar(PRESET, "FGAMMAADJ") == 1.9


def a_key_is_matched_only_at_the_start_of_a_line():
    """`warp=` is a scalar; `warp_1=` is a shader, and `wave_a` appears inside
    per-frame code. Neither may be read as the other."""
    assert v.preset_scalar(PRESET, "warp") == 1.234
    text = "zoom=2.0\nper_frame_1=my_zoom = 9.0;\n"
    assert v.preset_scalar(text, "zoom") == 2.0


# ── which sliders will actually do something ──────────────────────────────

def a_variable_animated_by_the_preset_is_reported_as_driven():
    """The whole reason this function exists: the per-frame code overwrites
    the file's value before the first pixel is drawn."""
    driven = v.preset_driven_keys(PRESET)
    assert "zoom" in driven, driven


def per_pixel_code_counts_as_driving_it_too():
    assert "rot" in v.preset_driven_keys(PRESET)


def a_scalar_the_code_never_assigns_is_not_driven():
    driven = v.preset_driven_keys(PRESET)
    for key in ("fDecay", "fGammaAdj", "fWaveScale", "warp"):
        assert key not in driven, (key, driven)


def the_ini_name_and_the_shader_name_are_not_the_same():
    """fDecay in the file is `decay` in the equations. Looking for the ini
    spelling in the code would find nothing, ever."""
    text = PRESET + "per_frame_9=decay = 0.9;\n"
    assert "fDecay" in v.preset_driven_keys(text)


def a_comparison_is_not_an_assignment():
    text = "zoom=1.0\nper_frame_1=if (zoom == 2, 1, 0);\n"
    assert "zoom" not in v.preset_driven_keys(text)


def shader_code_does_not_count_as_driving_a_scalar():
    """A comp shader assigning its own local `ret` or `uv` says nothing about
    the preset's scalars, and reading it would disable sliders at random."""
    text = "fDecay=0.9\ncomp_1=`  float decay = 0.5;\n"
    assert "fDecay" not in v.preset_driven_keys(text)


# ── writing ───────────────────────────────────────────────────────────────

def patching_changes_the_value_and_nothing_else():
    out = v.patch_preset(PRESET, {"fDecay": 0.5})
    assert v.preset_scalar(out, "fDecay") == 0.5
    before = [l for l in PRESET.splitlines() if not l.startswith("fDecay")]
    after = [l for l in out.splitlines() if not l.startswith("fDecay")]
    assert before == after, "a line other than fDecay changed"


def patching_leaves_the_equations_and_shaders_alone():
    out = v.patch_preset(PRESET, {"zoom": 1.5, "rot": -0.5})
    for line in ("per_frame_1=zoom = zoom + 0.1*sin(time);",
                 "comp_1=`   ret = tex2D(sampler_worms, uv).xyz;"):
        assert line in out, line


def a_missing_key_is_inserted_after_the_section_header():
    """Anything after the first per_frame line is code, so appending would put
    a scalar inside the program."""
    text = "MILKDROP_PRESET_VERSION=201\n[preset00]\nzoom=1.0\nper_frame_1=x=1;\n"
    out = v.patch_preset(text, {"fDecay": 0.75})
    lines = out.splitlines()
    assert lines[1] == "[preset00]" and lines[2] == "fDecay=0.75", lines
    assert v.preset_scalar(out, "fDecay") == 0.75


def only_the_first_occurrence_is_patched():
    text = "[preset00]\nzoom=1.0\nzoom=2.0\n"
    out = v.patch_preset(text, {"zoom": 3.0})
    assert out.splitlines() == ["[preset00]", "zoom=3.0", "zoom=2.0"], out


def a_whole_number_keeps_its_decimal_point():
    """The library writes zoom=1.011000; a bare "3" in that column reads as a
    different kind of value."""
    assert "zoom=3.0" in v.patch_preset("[preset00]\nzoom=1.0\n", {"zoom": 3})
    assert "dx=0.0" in v.patch_preset("[preset00]\ndx=0.5\n", {"dx": 0.0})


def a_round_trip_through_the_sliders_is_stable():
    """Patching with the values just read must not drift the file."""
    keys = [k for k, *_ in v.PRESET_TWEAKS
            if v.preset_scalar(PRESET, k) is not None]
    values = {k: v.preset_scalar(PRESET, k) for k in keys}
    once = v.patch_preset(PRESET, values)
    twice = v.patch_preset(once, values)
    assert once == twice, "patching is not idempotent"


# ── which textures a preset can have swapped ──────────────────────────────

def file_textures_are_found_and_builtins_are_not():
    tex = v.preset_textures(PRESET)
    assert tex == ["worms"], tex


def a_sampler_prefix_is_stripped_before_matching():
    """sampler_pw_noise_lq is noise_lq with point/wrap filtering — the same
    texture. Not stripping the prefix would offer `pw_noise_lq` for swapping,
    which is not a file and would never be found."""
    assert v.preset_textures("comp_1=tex2D(sampler_fw_clouds, uv)") == ["clouds"]
    assert v.preset_textures("comp_1=tex2D(sampler_pw_noise_lq, uv)") == []


def the_random_texture_slots_are_left_out():
    """rand00 picks a different file from the pack on every load, so an
    override for it would take effect at random."""
    assert v.preset_textures("comp_1=tex2D(sampler_rand00, uv)") == []


def each_texture_is_offered_once():
    text = "a=tex2D(sampler_worms,uv) b=tex2D(sampler_pw_worms,uv)"
    assert v.preset_textures(text) == ["worms"]


# ── swapping one ──────────────────────────────────────────────────────────

def _image(name="pic.png", data=b"\x89PNG\r\n\x1a\n rest"):
    p = os.path.join(_TMP, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


def a_swap_writes_into_the_override_directory_only():
    """Never over the stock file. The texture pack is 8,000 files of other
    people's work, downloaded separately, and a swap has to be undoable."""
    stock = os.path.join(_TMP, "stock")
    os.makedirs(stock, exist_ok=True)
    original = os.path.join(stock, "worms.jpg")
    with open(original, "wb") as f:
        f.write(b"the original")
    assert v.set_texture_override("worms", _image()) == ""
    with open(original, "rb") as f:
        assert f.read() == b"the original", "the stock texture was written to"
    assert v.texture_override_path("worms").endswith("worms.png")


def the_override_directory_leads_the_search_path():
    """libprojectM scans its paths in order and the first stem match wins, so
    this order is the entire swap mechanism."""
    import inspect
    body = inspect.getsource(v.Renderer._apply_texture_paths)
    assert body.index("texture_override_dir()") < body.index('"texture_dir"')


def restoring_removes_the_override():
    v.set_texture_override("worms", _image())
    assert v.clear_texture_override("worms") is True
    assert v.texture_override_path("worms") == ""
    assert v.clear_texture_override("worms") is False


def replacing_an_override_does_not_leave_the_old_extension_behind():
    """Two files with the same stem leave the winner to the scan order."""
    v.set_texture_override("worms", _image("a.png"))
    v.set_texture_override("worms", _image("b.jpg"))
    assert sorted(os.listdir(v.texture_override_dir())) == ["worms.jpg"]
    v.clear_texture_override("worms")


def a_file_projectm_cannot_read_is_refused_with_a_reason():
    why = v.set_texture_override("worms", _image("notes.txt", b"hello"))
    assert why and "txt" in why, why
    assert v.texture_override_path("worms") == ""


def the_override_list_reports_what_is_in_place():
    v.set_texture_override("worms", _image())
    v.set_texture_override("clouds", _image())
    assert v.texture_overrides() == ["clouds", "worms"]
    v.clear_texture_override("worms")
    v.clear_texture_override("clouds")


# ── against the real library ──────────────────────────────────────────────

def _library():
    try:
        from config import load_config
        d = load_config().get("preset_dir", "")
    except Exception:
        return []
    if not d or not os.path.isdir(d):
        return []
    import glob
    return sorted(glob.glob(os.path.join(d, "**", "*.milk"), recursive=True))


def every_slider_finds_its_value_in_real_presets():
    """The slider set was chosen by measuring the whole library. If a key
    stopped being near-universal the panel would come up mostly empty."""
    files = _library()
    if not files:
        print("      (no preset library configured — skipped)")
        return
    sample = files[::max(1, len(files) // 400)][:400]
    found = {k: 0 for k, *_ in v.PRESET_TWEAKS}
    for path in sample:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        for key in found:
            if v.preset_scalar(text, key) is not None:
                found[key] += 1
    thin = {k: n for k, n in found.items() if n < len(sample) * 0.9}
    assert not thin, f"present in under 90% of {len(sample)} presets: {thin}"


def patching_real_presets_never_reorders_or_loses_a_line():
    files = _library()
    if not files:
        print("      (no preset library configured — skipped)")
        return
    for path in files[::max(1, len(files) // 200)][:200]:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        out = v.patch_preset(text, {"fDecay": 0.777, "zoom": 1.25})
        assert len(out.splitlines()) == len(text.splitlines()), path
        assert v.preset_scalar(out, "fDecay") == 0.777, path
        changed = [i for i, (a, b)
                   in enumerate(zip(text.splitlines(), out.splitlines()))
                   if a != b]
        assert len(changed) <= 2, (path, changed)


# ── the failure callback ─────────────────────────────────────────────────────
#
# projectm_load_preset_data returns void. The only thing that knows whether an
# edit compiled is the callback, so these run ProjectM4 against a stand-in
# library rather than testing the text functions above.

class _FakeFn:
    def __init__(self, lib, name):
        self._lib, self._name = lib, name
        self.restype = None
        self.argtypes = None

    def __call__(self, *a):
        return self._lib.call(self._name, *a)


class _FakeCore:
    """Enough of libprojectM to drive the error reporting, and no more."""

    def __init__(self, missing=()):
        self._missing = set(missing)
        self._fns = {}
        self.fail_with = b""      # what the next load reports, if anything
        self.log_error = b""      # what it logs while drawing, if anything
        self.failed_cb = None
        self.log_cb = None

    def __getattr__(self, name):
        if not name.startswith("projectm_") or name in self._missing:
            raise AttributeError(name)
        return self._fns.setdefault(name, _FakeFn(self, name))

    def call(self, name, *a):
        if name == "projectm_create":
            return 0xBEEF
        if name == "projectm_set_log_callback":
            self.log_cb = a[0]
        if name == "projectm_set_preset_switch_failed_event_callback":
            self.failed_cb = a[1]
        if name == "projectm_load_preset_data":
            if self.fail_with:
                self.failed_cb(b"/presets/x.milk", self.fail_with, None)
            if self.log_error:
                # projectM logs a shader error while the frame is drawn, not
                # while the preset loads. The order does not matter to the
                # slot; that it survives the load does.
                self.log_cb(self.log_error, 5, None)
        return 0


def _pm(missing=()):
    # The real one installs its log callback once per process and then skips.
    # Each stand-in library needs its own, so forget the last one.
    v._LOG_CB = None
    core = _FakeCore(missing)
    pm = v.ProjectM4(core, None)
    pm.create(64, 64)
    return core, pm


def a_preset_that_will_not_compile_reports_why():
    core, pm = _pm()
    core.fail_with = b"blur1: syntax error at line 4"
    assert pm.load_preset_data("[preset00]"), "the call itself should go through"
    assert pm.last_error == "blur1: syntax error at line 4", pm.last_error


def a_preset_that_compiles_leaves_no_error_behind():
    """The clear has to happen before the load, not after: a stale message from
    the previous edit would toast on the edit that fixed it."""
    core, pm = _pm()
    core.fail_with = b"boom"
    pm.load_preset_data("[preset00]")
    assert pm.last_error
    core.fail_with = b""
    pm.load_preset_data("[preset00]")
    assert pm.last_error == "", f"stale error survived: {pm.last_error!r}"


def a_shader_that_will_not_compile_is_read_from_the_log():
    """The failed event fires only when the preset cannot be parsed. A preset
    that parses and whose shader will not compile is logged instead, and
    projectM renders on with the broken stage dropped -- which is the error
    somebody editing shader code hits most, so it has to be readable too."""
    core, pm = _pm()
    core.log_error = (b'[PerPixelMesh] Error loading warp shader code:'
                      b'[MilkdropShader] missing "shader_body" entry point.')
    pm.load_preset_data("[preset00]")
    assert pm.last_error == "", "a shader error is not a parse failure"
    assert "shader_body" in v._LAST_PM_ERROR, v._LAST_PM_ERROR


def the_previous_edits_error_does_not_survive_the_next_one():
    """Otherwise the edit that fixes a preset toasts the error it just fixed."""
    core, pm = _pm()
    core.log_error = b"boom"
    pm.load_preset_data("[preset00]")
    assert v._LAST_PM_ERROR == "boom"
    core.log_error = b""
    pm.load_preset_data("[preset00]")
    assert v._LAST_PM_ERROR == "", v._LAST_PM_ERROR


def a_library_without_the_callback_still_edits():
    """An older libprojectM loses the error text, not the editor."""
    core, pm = _pm({"projectm_set_preset_switch_failed_event_callback"})
    assert not pm.can_report_failures
    assert core.failed_cb is None, "registered a callback that does not exist"
    assert pm.load_preset_data("[preset00]")
    assert pm.last_error == ""


for name, fn in list(globals().items()):
    if callable(fn) and not name.startswith(("_", "check")) \
            and getattr(fn, "__module__", None) == "__main__":
        results.append(check(name, fn))

print("all tweak checks passed" if all(results)
      else f"{results.count(False)} FAILED")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
