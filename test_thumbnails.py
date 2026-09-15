#!/usr/bin/env python3
"""Preset thumbnails: cached passively, saved the right way up, loaded lazily.

There is deliberately no batch pass over the library — ten thousand presets at
a second each is two hours — so the cache fills from what was actually watched.
These check the gate that decides when, and the writer that decides what.
Run: QT_QPA_PLATFORM=offscreen python test_thumbnails.py
"""
import os, sys, time, tempfile, shutil
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from viz_common import (QApplication, QImage, Renderer, _ThumbWriter,
                        thumb_path, thumbs_dir)
from preset_browser import PresetPlaylistWindow

W, H = 160, 120
RED, BLUE = (200, 20, 20), (20, 20, 200)
NAME = "Geiss - Reaction Diffusion"


def gl_frame():
    """Bottom half red — in GL order, so the buffer's FIRST rows are the
    bottom of the picture, which is what the writer has to mirror."""
    rows = [bytes(RED if y < H // 2 else BLUE) * W for y in range(H)]
    return b"".join(rows)


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    print(f"  ok  {name}")
    return True


app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp(prefix="viz-thumb-")
# user_data_dir() reads a different variable per platform, and setting only
# the Linux one meant this suite wrote into the real %LOCALAPPDATA% cache on
# Windows -- so results depended on what previous runs had left there.
os.environ["XDG_DATA_HOME"] = tmp
os.environ["LOCALAPPDATA"] = tmp
results = []


def a_thumbnail_path_is_stable_and_filesystem_safe():
    nasty = "Rovastar / Fvese - A:B*C? <hybrid>"
    a, b = thumb_path(nasty), thumb_path(nasty)
    assert a == b, "the same preset produced two different paths"
    assert a != thumb_path(nasty + "x"), "two presets collided"
    leaf = os.path.basename(a)
    assert leaf.endswith(".jpg") and all(
        c not in leaf for c in '/\\:*?"<>|'), leaf
    assert os.path.dirname(a).startswith(thumbs_dir()), a
    # two-hex fan-out, so one directory never holds ten thousand files
    assert len(os.path.basename(os.path.dirname(a))) == 2, a

def the_writer_saves_a_scaled_jpeg():
    w = _ThumbWriter(); w.start()
    path = thumb_path("scaled")
    w.submit(path, W, H, gl_frame())
    for _ in range(100):
        if os.path.isfile(path):
            break
        time.sleep(0.02)
    assert os.path.isfile(path), "nothing was written"
    img = QImage(path)
    assert not img.isNull(), "the file is not a readable image"
    assert img.width() == _ThumbWriter.WIDTH, img.width()
    assert img.height() == round(H * _ThumbWriter.WIDTH / W), img.height()

def the_thumbnail_is_the_right_way_up():
    """glReadPixels hands back rows bottom-up; the writer mirrors them."""
    w = _ThumbWriter(); w.start()
    path = thumb_path("upright")
    w.submit(path, W, H, gl_frame())
    for _ in range(100):
        if os.path.isfile(path):
            break
        time.sleep(0.02)
    img = QImage(path)
    top = img.pixelColor(img.width() // 2, 3)
    bottom = img.pixelColor(img.width() // 2, img.height() - 4)
    assert top.blue() > top.red() + 60, f"top is not blue: {top.getRgb()}"
    assert bottom.red() > bottom.blue() + 60, \
        f"bottom is not red: {bottom.getRgb()}"


class FakePM:
    def __init__(self, name=NAME): self.name = name
    def current_preset_name(self):  return self.name


class FakeSignal:
    def __init__(self):        self.sent = []
    def emit(self, *args):     self.sent.append(args)


class FakeBridge:
    """The two signals _maybe_thumbnail reaches for. thumbnail_ready is fired
    from the writer thread, not this one."""
    def __init__(self):
        self.show_toast      = FakeSignal()
        self.thumbnail_ready = FakeSignal()


def bare_renderer():
    """A Renderer without __init__ — it starts audio capture and an IPC
    thread, and none of that is what these checks are about."""
    r = object.__new__(Renderer)
    r._pm = FakePM()
    r._pms = [r._pm, None]
    r._cfg = {}
    r._bridge = FakeBridge()
    r._thumb_done = False
    r._want_snapshot = False
    r._preset_start = time.monotonic()
    r.grabs = 0
    def grab():
        r.grabs += 1
        return (W, H, gl_frame())
    r._grab_frame = grab
    return r


def a_build_without_the_jpeg_plugin_still_writes_something():
    """Qt carries JPEG as a plugin and PNG built in; a frozen build that lost
    the plugin would otherwise write nothing and never say so."""
    real = QImage.save
    def only_png(self, path, fmt=None, quality=-1):
        return False if fmt == "JPG" else real(self, path, fmt, quality)
    QImage.save = only_png
    try:
        w = _ThumbWriter(); w.start()
        path = thumb_path("no-jpeg-plugin")
        w.submit(path, W, H, gl_frame())
        for _ in range(100):
            if os.path.isfile(path):
                break
            time.sleep(0.02)
    finally:
        QImage.save = real
    assert os.path.isfile(path), "nothing was written without the JPEG plugin"
    img = QImage(path)
    assert not img.isNull(), "the fallback file is not a readable image"
    assert img.width() == _ThumbWriter.WIDTH, img.width()

def a_preset_skipped_past_gets_no_thumbnail():
    r = bare_renderer()
    r._maybe_thumbnail()
    assert r.grabs == 0, "grabbed a frame before the preset had settled"

def one_that_stays_gets_exactly_one():
    r = bare_renderer()
    r._preset_start = time.monotonic() - Renderer.THUMB_AFTER - 1
    r._maybe_thumbnail()
    r._maybe_thumbnail()
    r._maybe_thumbnail()
    assert r.grabs == 1, f"grabbed {r.grabs} frames for one preset"

def an_already_cached_preset_is_not_re_grabbed():
    r = bare_renderer()
    r._pm = FakePM("cached-one")
    path = thumb_path("cached-one")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(b"x")
    r._preset_start = time.monotonic() - Renderer.THUMB_AFTER - 1
    r._maybe_thumbnail()
    assert r.grabs == 0, "re-grabbed a preset that already has a thumbnail"

def the_automatic_picture_can_be_turned_off():
    """Somebody who does not want the app deciding when to take a picture
    still wants the button. Off stops the passive capture and nothing else."""
    r = bare_renderer()
    r._cfg = {"auto_thumbnails": False}
    r._preset_start = time.monotonic() - Renderer.THUMB_AFTER - 1
    r._maybe_thumbnail()
    assert r.grabs == 0, "took a picture with the automatic capture off"
    # ... and the button still works, which is the whole point of the setting
    r._want_snapshot = True
    r._maybe_thumbnail()
    assert r.grabs == 1, "the button was refused along with the automatic one"


def a_snapshot_jumps_every_gate_the_automatic_one_waits_for():
    """The wait, the once-per-preset latch and the file already being there.
    Somebody pressing the button is looking at the frame they want, and the
    reason to press it a second time is that the first one was not it."""
    r = bare_renderer()
    r._pm = FakePM("snapped-one")
    path = thumb_path("snapped-one")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(b"x")
    r._thumb_done = True                       # already had its one attempt
    r._preset_start = time.monotonic()         # and has not settled either
    r._want_snapshot = True
    r._maybe_thumbnail()
    assert r.grabs == 1, "the button was refused"
    assert r._bridge.show_toast.sent, "snapped without saying so"
    # Taken, not latched: a second press is a second picture.
    r._want_snapshot = True
    r._maybe_thumbnail()
    assert r.grabs == 2, f"a second press gave {r.grabs} pictures"
    # And nothing between them keeps taking pictures on its own.
    r._maybe_thumbnail()
    assert r.grabs == 2, "kept grabbing after the press was served"


def a_new_preset_re_arms_the_gate():
    """_emit_current_preset is the hook; without it only the first preset in
    a session would ever be captured."""
    import inspect
    body = inspect.getsource(Renderer._emit_current_preset)
    assert "_thumb_done = False" in body, \
        "preset changes no longer re-arm the thumbnail gate"


class BrowserHost:
    entries = [(NAME, 7), ("Never watched", 4)]
    def is_rated(self):            return False
    def is_shuffle(self):          return False
    def get_preset_playlist(self): return list(self.entries)
    def current_order_pos(self):   return 0
    def jump_to_order(self, i):    pass


def _write_thumb(path):
    """A 96x54 JPEG at `path`, or an assertion that says what went wrong.

    Retried once because this is Windows: a file created a moment ago can
    still be held by a virus scanner, and QImage.save then returns a bare
    False. That flaked this suite twice, in two different sessions, always on
    the same line and always with an empty assertion message -- which is the
    other half of what is fixed here.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = QImage(96, 54, QImage.Format.Format_RGB888)
    img.fill(0x3366cc)
    for attempt in (1, 2):
        if img.save(path, "JPG", 80):
            return
        time.sleep(0.25)
    raise AssertionError(
        f"could not write the test thumbnail to {path} in {attempt} attempts "
        f"(directory exists: {os.path.isdir(os.path.dirname(path))})")


def the_browser_shows_a_cached_thumbnail_and_holds_the_column_for_the_rest():
    """Once one row has a picture, every row has to reserve the same width.

    Qt indents a row that has an icon and draws one without flush against the
    edge, so a list where most presets are unwatched -- which is every fresh
    library -- had its names zig-zagging in and out by 96px. The unwatched
    rows get a transparent pixmap of the same size instead of nothing.
    """
    path = thumb_path(NAME)
    _write_thumb(path)
    win = PresetPlaylistWindow(host=BrowserHost())
    win.resize(420, 400)
    win.show()
    app.processEvents()
    win._load_visible_icons()
    assert not win._list.item(0).icon().isNull(), \
        "the watched preset got no thumbnail"
    assert win._list.iconSize() == win.ICON_SIZE, \
        "the icon column never opened up for the thumbnail"

    blank = win._list.item(1).icon()
    assert not blank.isNull(), \
        "an unwatched row reserved nothing, so its name sits 96px left of the rest"
    assert blank.cacheKey() == win._blank_icon().cacheKey(), \
        "the unwatched row got a picture from somewhere instead of the blank"
    # What the eye actually notices: both rows offer the view the same width.
    assert (win._list.item(0).icon().actualSize(win.ICON_SIZE)
            == blank.actualSize(win.ICON_SIZE)), \
        "the blank is not the size of a real thumbnail, so the names still step"
    win.close()

def growing_the_window_gives_the_new_rows_their_pictures():
    """Only a scroll and a rebuild used to ask for them.

    Drag the browser's bottom edge down and the rows that appear were never
    handed to _load_visible_icons, so they sat blank -- and once the blank
    placeholder went in, their names sat 96px left of every row above them.
    Seen in the rig, not in a test, which is why there is one now.
    """
    path = thumb_path(NAME)
    _write_thumb(path)

    class Many(BrowserHost):
        entries = [(NAME, 9)] + [(f"unwatched {i}", 5) for i in range(40)]
    win = PresetPlaylistWindow(host=Many())
    win.resize(420, 140)          # a couple of rows
    win.show()
    app.processEvents()
    win._load_visible_icons()
    short = sum(1 for i in range(win._list.count())
                if not win._list.item(i).icon().isNull())

    win.resize(420, 900)          # the drag
    app.processEvents()
    tall = sum(1 for i in range(win._list.count())
               if not win._list.item(i).icon().isNull())
    assert tall > short, \
        f"growing the window revealed rows with no icon at all ({short} then {tall})"
    win.close()

def a_thumbnail_below_an_unwatched_row_is_not_squashed_into_it():
    """uniformItemSizes measures the first row and reuses that height for
    every other one, so when the row at the top has no thumbnail -- normal,
    most presets have none -- a 54px thumbnail three rows down was drawn into
    a 26px row and spilled over its neighbours. Seen in a screenshot, not in
    a test, which is why there is one now."""
    path = thumb_path(NAME)
    _write_thumb(path)

    class TopUnwatched(BrowserHost):
        entries = [("never watched", 5), (NAME, 9), ("nor this", 5)]
    win = PresetPlaylistWindow(host=TopUnwatched())
    win.resize(420, 400)
    win.show()
    app.processEvents()
    win._load_visible_icons()
    assert win._list.iconSize() == win.ICON_SIZE
    # The drawn rectangle, not the size hint: with uniformItemSizes on, the
    # view ignores a hint set after the first layout, which is exactly how
    # this shipped looking broken while the hints all read correctly.
    h = win._list.visualItemRect(win._list.item(1)).height()
    assert h >= win.ICON_SIZE.height(), \
        f"rows are {h}px and the thumbnail is {win.ICON_SIZE.height()}px"
    # And a sort, which rebuilds every row, must not undo it.
    win.refresh()
    win._load_visible_icons()
    app.processEvents()
    h = win._list.visualItemRect(win._list.item(1)).height()
    assert h >= win.ICON_SIZE.height(), f"a rebuild put it back to {h}px"
    win.close()


def a_picture_that_arrives_after_the_row_was_read_still_appears():
    """A row with no thumbnail is remembered as a miss -- that is what stops
    ten thousand rows stat-ing the cache on every scroll -- so a snapshot
    taken while the browser is open would otherwise not show until the list
    was rebuilt."""
    name = "Snapped while the browser was open"
    class Late(BrowserHost):
        entries = [(name, 5)]
    win = PresetPlaylistWindow(host=Late())
    win.resize(420, 400)
    win.show()
    app.processEvents()
    win._load_visible_icons()
    assert win._list.item(0).icon().isNull(), "started with a picture"
    _write_thumb(thumb_path(name))
    win._on_thumbnail_ready(name)
    assert not win._list.item(0).icon().isNull(), \
        "the new picture never reached the row"
    win.close()


def turning_the_pictures_off_gives_the_rows_their_height_back():
    """Off has to undo both halves: the icons already on the rows, and the
    stylesheet row height _make_room_for_icons set, which is what makes every
    row tall whether it carries a picture or not."""
    _write_thumb(thumb_path(NAME))
    win = PresetPlaylistWindow(host=BrowserHost())
    win.resize(420, 400)
    win.show()
    app.processEvents()
    win._load_visible_icons()
    assert win._list.iconSize() == win.ICON_SIZE
    tall = win._list.visualItemRect(win._list.item(0)).height()

    win._btn_thumbs.setChecked(False)
    app.processEvents()
    assert win._list.iconSize().width() == 0, win._list.iconSize()
    assert win._list.item(0).icon().isNull(), "the row kept its picture"
    assert "min-height" not in win._list.styleSheet(), \
        "the rows are still reserving room for a picture"
    short = win._list.visualItemRect(win._list.item(0)).height()
    assert short < tall, f"rows are still {short}px, were {tall}px"

    # And back on again, which is the half that needs the cache forgotten.
    win._btn_thumbs.setChecked(True)
    app.processEvents()
    assert not win._list.item(0).icon().isNull(), \
        "turning them back on left the row blank"
    win.close()


def an_empty_cache_leaves_no_gutter():
    """A fresh install has no thumbnails, and a 96px empty column down ten
    thousand rows looks like the list is broken."""
    class Unwatched(BrowserHost):
        entries = [("nothing watched yet", 5)]
    win = PresetPlaylistWindow(host=Unwatched())
    win.resize(420, 400)
    win.show()
    app.processEvents()
    win._load_visible_icons()
    assert win._list.iconSize().width() == 0, win._list.iconSize()
    win.close()


for fn in (a_thumbnail_path_is_stable_and_filesystem_safe,
           the_writer_saves_a_scaled_jpeg,
           the_thumbnail_is_the_right_way_up,
           a_build_without_the_jpeg_plugin_still_writes_something,
           a_preset_skipped_past_gets_no_thumbnail,
           one_that_stays_gets_exactly_one,
           an_already_cached_preset_is_not_re_grabbed,
           a_new_preset_re_arms_the_gate,
           the_automatic_picture_can_be_turned_off,
           a_snapshot_jumps_every_gate_the_automatic_one_waits_for,
           the_browser_shows_a_cached_thumbnail_and_holds_the_column_for_the_rest,
           a_picture_that_arrives_after_the_row_was_read_still_appears,
           growing_the_window_gives_the_new_rows_their_pictures,
           turning_the_pictures_off_gives_the_rows_their_height_back,
           an_empty_cache_leaves_no_gutter,
           a_thumbnail_below_an_unwatched_row_is_not_squashed_into_it):
    results.append(check(fn.__name__, fn))

shutil.rmtree(tmp, ignore_errors=True)
print("all thumbnail checks passed" if all(results) else "FAILURES")
sys.exit(0 if all(results) else 1)
