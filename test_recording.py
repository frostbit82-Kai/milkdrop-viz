#!/usr/bin/env python3
"""Recording produces a real, right-way-up clip and never blocks the renderer.

glReadPixels hands back rows bottom-up, so every filter chain starts with
vflip; getting that wrong yields an upside-down clip that still passes any
"the file exists" check. These decode the result and look.
Run: python test_recording.py            (needs ffmpeg on PATH)
"""
import os, sys, shutil, subprocess, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_common
from viz_common import _Recorder, find_ffmpeg

W, H, N = 160, 120, 24
RED, BLUE = (200, 20, 20), (20, 20, 200)


def frame(i):
    """Bottom half red, top half blue — in GL order, so the buffer's FIRST
    rows are the bottom of the picture. A moving band keeps the frames
    distinct so a stuck encoder cannot pass by repeating one."""
    rows = []
    for y in range(H):
        colour = RED if y < H // 2 else BLUE
        row = bytearray(bytes(colour) * W)
        band = (i * 5) % W
        row[band * 3:(band + 1) * 3] = b"\xff\xff\xff"
        rows.append(bytes(row))
    return b"".join(rows)


def decode(path):
    """(width, height, first frame as RGB bytes, frame count)."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,nb_read_packets",
         "-count_packets", "-of", "csv=p=0", path],
        capture_output=True, text=True)
    w, h, count = [int(x) for x in probe.stdout.strip().split(",")[:3]]
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True).stdout
    return w, h, raw, count


def row_mean(raw, w, y):
    row = raw[y * w * 3:(y + 1) * w * 3]
    return tuple(sum(row[c::3]) // w for c in range(3))


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    print(f"  ok  {name}")
    return True


tmp = tempfile.mkdtemp(prefix="viz-rec-")
results = []


def record(kind, frames=N):
    path = os.path.join(tmp, f"clip.{kind}")
    rec = _Recorder(path, (W, H), 30, kind)
    why = rec.start_encoder()
    assert not why, why
    for i in range(frames):
        rec.submit(frame(i))
        time.sleep(0.002)          # let the writer thread keep up
    err = rec.finish()
    assert not err, err
    return path, rec


def an_mp4_comes_out_playable():
    path, rec = record("mp4")
    assert os.path.getsize(path) > 1000, f"{os.path.getsize(path)} bytes"
    w, h, _raw, count = decode(path)
    assert (w, h) == (W, H), f"{w}x{h} != {W}x{H}"
    assert count >= N - 2, f"only {count} of {N} frames survived"
    assert rec.dropped == 0, f"{rec.dropped} frames dropped with a slow feed"

def the_mp4_is_the_right_way_up():
    """The whole reason vflip is in the chain."""
    path, _ = record("mp4")
    w, _h, raw, _c = decode(path)
    top, bottom = row_mean(raw, w, 4), row_mean(raw, w, H - 5)
    assert bottom[0] > bottom[2] + 60, f"bottom row is not red: {bottom}"
    assert top[2] > top[0] + 60, f"top row is not blue: {top}"

def odd_dimensions_still_encode():
    """H.264 refuses an odd width, and a window is whatever size it is."""
    path = os.path.join(tmp, "odd.mp4")
    rec = _Recorder(path, (W + 1, H + 1), 30, "mp4")
    assert not rec.start_encoder()
    row = bytes(RED) * (W + 1)
    for _ in range(8):
        rec.submit(row * (H + 1))
        time.sleep(0.002)
    err = rec.finish()
    assert not err, err
    w, h, _r, _c = decode(path)
    assert (w, h) == (W, H), f"odd size was not evened out: {w}x{h}"

def a_gif_comes_out_a_gif():
    path, _ = record("gif")
    with open(path, "rb") as f:
        magic = f.read(6)
    assert magic in (b"GIF87a", b"GIF89a"), magic
    w, _h, _raw, count = decode(path)
    assert w == 480, f"gif was not scaled to 480 wide: {w}"
    assert count >= 2, f"only {count} gif frames"

def a_full_queue_drops_instead_of_blocking():
    """Once running, the render thread must never wait on the encoder.

    The recorder is pushed past its startup window first — inside that window
    it is meant to wait, and testing there would measure the wrong rule.
    """
    path = os.path.join(tmp, "drop.mp4")
    rec = _Recorder(path, (W, H), 30, "mp4")
    assert not rec.start_encoder()
    rec._t0 = time.monotonic() - rec.STARTUP_S - 1     # running, not starting
    rec._q.maxsize = 2                                 # a stalled encoder
    while not rec._q.full():
        rec._q.put_nowait(frame(0))
    t0 = time.monotonic()
    for i in range(50):
        rec._q.maxsize = 2                             # the writer keeps draining
        rec.submit(frame(i))
    took = time.monotonic() - t0
    assert took < 0.5, f"submit blocked for {took:.2f}s on a full queue"
    assert rec.dropped > 0, "a full queue accepted every frame"
    rec.finish()

def the_startup_window_waits_rather_than_dropping():
    """ffmpeg needs a moment to start reading and one frame nearly fills the
    pipe buffer, so a strictly non-blocking submit lost the opening second."""
    rec = _Recorder(os.path.join(tmp, "grace.mp4"), (W, H), 30, "mp4")
    rec._t0 = time.monotonic()
    assert rec._grace() == rec.STARTUP_WAIT, rec._grace()
    rec._t0 = time.monotonic() - rec.STARTUP_S - 1
    assert rec._grace() == rec.RUNNING_WAIT, rec._grace()

def the_encoder_is_chosen_from_what_ffmpeg_actually_has():
    """libx264 is GPL, so an LGPL build — the one worth bundling in a paid
    application — does not have it and asking for it by name would fail."""
    class Run:
        def __init__(self, out): self.stdout = out
    real_run, real_cache = viz_common._subprocess.run, viz_common._VIDEO_CODEC_CACHE
    try:
        for listing, want in ((" V....D libx264  H.264\n V..... h264_mf x\n", "libx264"),
                              (" V..... h264_mf  H.264 via MF\n", "h264_mf"),
                              (" V..... mpeg4  MPEG-4 part 2\n", "mpeg4"),
                              ("", "mpeg4")):
            viz_common._VIDEO_CODEC_CACHE = {}
            viz_common._subprocess.run = lambda *a, **k: Run(listing)
            got = viz_common.video_codec_args("ffmpeg")
            assert got[1] == want, f"{listing!r} chose {got[1]}, wanted {want}"
    finally:
        viz_common._subprocess.run = real_run
        viz_common._VIDEO_CODEC_CACHE = real_cache

def the_fallback_encoder_also_produces_a_playable_file():
    """Proves the mpeg4 branch is not just syntactically plausible."""
    real = viz_common._VIDEO_CODEC_CACHE
    viz_common._VIDEO_CODEC_CACHE = {find_ffmpeg(): ["-c:v", "mpeg4", "-q:v", "3"]}
    try:
        path, _rec = record("mp4")
    finally:
        viz_common._VIDEO_CODEC_CACHE = real
    w, h, _raw, count = decode(path)
    assert (w, h) == (W, H), f"{w}x{h}"
    assert count >= N - 2, f"only {count} of {N} frames survived"

def a_missing_ffmpeg_is_a_message_not_a_crash():
    real = viz_common.find_ffmpeg
    viz_common.find_ffmpeg = lambda: ""
    try:
        rec = _Recorder(os.path.join(tmp, "none.mp4"), (W, H), 30, "mp4")
        why = rec.start_encoder()
    finally:
        viz_common.find_ffmpeg = real
    assert why and "ffmpeg" in why.lower(), f"unhelpful reason: {why!r}"

def a_dead_encoder_is_reported():
    """ffmpeg killed mid-clip must surface, not leave a silent 0-byte file."""
    path = os.path.join(tmp, "killed.mp4")
    rec = _Recorder(path, (W, H), 30, "mp4")
    assert not rec.start_encoder()
    rec.submit(frame(0))
    time.sleep(0.15)
    rec._proc.kill()
    time.sleep(0.15)
    for i in range(40):                     # writes into a dead pipe
        rec.submit(frame(i))
    err = rec.finish()
    assert err, "a killed ffmpeg reported success"


if not find_ffmpeg() or not shutil.which("ffprobe"):
    print("ffmpeg/ffprobe not on PATH — skipping recording checks")
    sys.exit(0)

for fn in (an_mp4_comes_out_playable,
           the_mp4_is_the_right_way_up,
           odd_dimensions_still_encode,
           a_gif_comes_out_a_gif,
           a_full_queue_drops_instead_of_blocking,
           the_startup_window_waits_rather_than_dropping,
           the_encoder_is_chosen_from_what_ffmpeg_actually_has,
           the_fallback_encoder_also_produces_a_playable_file,
           a_missing_ffmpeg_is_a_message_not_a_crash,
           a_dead_encoder_is_reported):
    results.append(check(fn.__name__, fn))

shutil.rmtree(tmp, ignore_errors=True)
print("all recording checks passed" if all(results) else "FAILURES")
sys.exit(0 if all(results) else 1)
