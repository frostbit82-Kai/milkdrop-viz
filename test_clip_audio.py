#!/usr/bin/env python3
"""Recorded clips carry sound. Run: python test_clip_audio.py  (needs ffmpeg)

Clips used to be silent, which is half a feature: nobody posts a visualiser
clip with no music under it. The sound is captured straight off the audio
backend into a WAV and muxed on at the end, because the render loop pops one
chunk per frame while the backend pushes about 86 a second -- a clip built from
what the loop sees would be a third short, and silently so.

The mux stream-copies the video and retimes it with -itsscale, so the two
things worth checking are that the output really has an audio stream and that
the retiming maths matches dropped frames rather than inventing a stretch.
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np                                    # noqa: E402
from viz_common import _Recorder, find_ffmpeg         # noqa: E402

W, H, FPS, N = 160, 120, 30, 45
RATE = 44100
CHUNK = 512


def frame(i):
    band = (i * 5) % W
    rows = []
    for _y in range(H):
        row = bytearray(bytes((20, 20, 200)) * W)
        row[band * 3:(band + 1) * 3] = b"\xff\xff\xff"
        rows.append(bytes(row))
    return b"".join(rows)


def tone(i):
    """One capture chunk of a sine, as the backend hands them over: (n, 2)
    float32 in -1..1."""
    t = (np.arange(CHUNK) + i * CHUNK) / float(RATE)
    mono = np.sin(2 * np.pi * 440 * t).astype("float32") * 0.5
    return np.stack([mono, mono], axis=1)


def probe(path, entries, stream):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream,
         "-show_entries", entries, "-of", "csv=p=0", path],
        capture_output=True, text=True)
    return r.stdout.strip()


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    print(f"  ok  {name}")
    return True


tmp = tempfile.mkdtemp(prefix="viz-clip-audio-")
results = []


def record_with_sound(name="clip.mp4", frames=N, chunks=None):
    """Run a whole recording: frames in, audio in, file out."""
    path = os.path.join(tmp, name)
    rec = _Recorder(path, (W, H), FPS, "mp4", audio_rate=RATE)
    why = rec.start_encoder()
    assert not why, why
    # Enough audio to cover the clip's real duration, which is what the
    # backend would have produced in the same wall-clock time.
    for i in range(chunks if chunks is not None
                   else int(frames / FPS * RATE / CHUNK) + 2):
        rec.write_audio(tone(i))
    for i in range(frames):
        rec.submit(frame(i))
    err = rec.finish()
    assert not err, err
    return rec, path


# ── the output ────────────────────────────────────────────────────────────

def a_clip_has_an_audio_stream():
    _rec, path = record_with_sound()
    codec = probe(path, "stream=codec_name", "a:0")
    assert codec == "aac", f"no audio track (got {codec!r})"


def the_video_is_still_there_and_the_right_size():
    """The mux stream-copies the video. A copy that silently re-encoded, or
    dropped the stream, would still leave a playable file."""
    _rec, path = record_with_sound("size.mp4")
    assert probe(path, "stream=width,height", "v:0") == f"{W},{H}"
    assert probe(path, "stream=codec_name", "v:0") != "", "video stream gone"


def the_audio_is_about_as_long_as_the_video():
    """-shortest, so the sound cannot run past the picture."""
    _rec, path = record_with_sound("dur.mp4")
    v_dur = float(probe(path, "stream=duration", "v:0") or 0)
    a_dur = float(probe(path, "stream=duration", "a:0") or 0)
    assert v_dur > 0.5, v_dur
    assert abs(a_dur - v_dur) < 0.5, (a_dur, v_dur)


def the_temporary_files_are_cleaned_up():
    """The video and WAV are written beside the clip under its own name."""
    _rec, path = record_with_sound("tidy.mp4")
    leftovers = [f for f in os.listdir(tmp)
                 if f.startswith("tidy.mp4") and f != "tidy.mp4"]
    assert leftovers == [], leftovers


def no_audio_at_all_still_produces_the_clip():
    """No capture backend, or a device that never delivered a chunk. Losing
    the recording over missing sound would be much worse than silence."""
    path = os.path.join(tmp, "silent.mp4")
    rec = _Recorder(path, (W, H), FPS, "mp4", audio_rate=RATE)
    assert not rec.start_encoder()
    for i in range(N):
        rec.submit(frame(i))
    assert not rec.finish()
    assert os.path.getsize(path) > 0
    assert probe(path, "stream=codec_name", "v:0") != ""


def a_gif_never_asks_for_sound():
    """No audio track in the format, and the mux would have nothing to do."""
    rec = _Recorder(os.path.join(tmp, "x.gif"), (W, H), FPS, "gif",
                    audio_rate=RATE)
    assert rec._audio_rate == 0
    assert rec._video_path == rec.path


# ── the retiming ──────────────────────────────────────────────────────────

def _scaler(frames, fps, seconds):
    rec = _Recorder("/dev/null", (W, H), fps, "mp4", audio_rate=RATE)
    rec.frames = frames
    rec._t0 = 100.0
    rec._t_last = 100.0 + seconds
    return rec._timestamp_scale()


def a_clip_that_dropped_nothing_is_not_retimed():
    # 31 frames at 30fps spans 30 intervals = exactly one second.
    assert abs(_scaler(31, 30, 1.0) - 1.0) < 0.01


def dropped_frames_stretch_the_video_to_real_time():
    """ffmpeg is told a constant frame rate, so a clip that lost a third of
    its frames is a third short -- and the sound, captured in real time,
    would run ahead of the picture by that much."""
    assert abs(_scaler(21, 30, 1.0) - 1.5) < 0.01


def a_wild_ratio_is_ignored_rather_than_applied():
    """A stalled encoder or a clock jump. Stretching to match that turns a
    slightly short clip into an unwatchable one."""
    assert _scaler(31, 30, 60.0) == 1.0
    assert _scaler(31, 30, 0.1) == 1.0


def too_few_frames_to_measure_is_not_a_division_by_zero():
    assert _scaler(1, 30, 1.0) == 1.0
    assert _scaler(0, 30, 1.0) == 1.0


if not find_ffmpeg() or not __import__("shutil").which("ffprobe"):
    print("SKIP: ffmpeg/ffprobe not found — clip audio needs both")
    raise SystemExit(0)

for name, fn in list(globals().items()):
    if callable(fn) and not name.startswith(("_", "check", "record", "frame",
                                             "tone", "probe")) \
            and getattr(fn, "__module__", None) == "__main__":
        results.append(check(name, fn))

print("all clip-audio checks passed" if all(results)
      else f"{results.count(False)} FAILED")
raise SystemExit(0 if all(results) else 1)
