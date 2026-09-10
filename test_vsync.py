#!/usr/bin/env python3
"""vsync is trusted until it is measured, and on one real machine it fails.

The app asks SDL for a swap interval of 1 and normally gets frames paced to the
monitor. On this project's Windows box (AMD Pitcairn, Windows 11) the request
was accepted, reported back, and every frame rendered correctly -- while each
swap blocked for about 250 ms, which is 4 fps on a card that does 4,500
uncapped. It cleared on its own an hour later with no driver change, so it is
an intermittent stall rather than a driver trait, and that is precisely why the
app measures instead of assuming: nothing else in it can see the difference.
`run` times a few swaps at startup and `vsync_paces` decides what the number
means.

The measurement needs a GPU; the decision does not, which is why it is a plain
function and why this file can check it.
Run: QT_QPA_PLATFORM=offscreen python test_vsync.py
"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from viz_common import vsync_paces, VSYNC_MIN_FRACTION

results = []


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    print(f"  ok  {name}")
    return True


def a_healthy_sixty_hertz_keeps_its_vsync():
    """Six swaps in 100 ms is 60 fps on a 60 Hz panel: exactly right."""
    assert vsync_paces(6, 0.100, 60)


def the_measured_stall_turns_vsync_off():
    """The real numbers off this project's Windows box on the day it stalled:
    one swap in the 250 ms the probe is allowed, against a 59 Hz display."""
    assert not vsync_paces(1, 0.250, 59)


def a_driver_dropping_the_odd_frame_is_left_alone():
    """45 fps on a 60 Hz panel is a busy preset, not a broken driver. Turning
    vsync off there would trade tearing for nothing."""
    assert vsync_paces(45, 1.0, 60)


def exactly_half_the_refresh_rate_is_still_good_enough():
    """The boundary is inclusive, so a panel measured at precisely half does
    not flip on a rounding error."""
    assert vsync_paces(30, 1.0, 60)
    assert not vsync_paces(29, 1.0, 60)


def a_high_refresh_panel_raises_the_bar():
    """60 fps is healthy on a 60 Hz panel and a stall on a 240 Hz one; the
    test has to be against the panel, not against a fixed number."""
    assert vsync_paces(60, 1.0, 60)
    assert not vsync_paces(60, 1.0, 240)


def nothing_measured_leaves_vsync_alone():
    """Zero frames, or no elapsed time, must not divide by zero and must not
    be read as a failure -- vsync is the default and staying on is the safe
    way to be wrong."""
    assert vsync_paces(0, 0.25, 60)
    assert vsync_paces(6, 0.0, 60)
    assert vsync_paces(6, -1.0, 60)


def an_unknown_refresh_rate_falls_back_to_sixty():
    """SDL reports 0 for a display whose mode it cannot read; treating that as
    0 Hz would make every measurement pass and the check pointless."""
    assert not vsync_paces(1, 0.25, 0)
    assert vsync_paces(60, 1.0, 0)


def the_threshold_is_a_fraction_not_a_frame_count():
    """Guards the constant itself: it is compared against the refresh rate."""
    assert 0 < VSYNC_MIN_FRACTION < 1, VSYNC_MIN_FRACTION


for name, fn in list(globals().items()):
    if callable(fn) and not name.startswith(("check", "_")) \
            and getattr(fn, "__module__", None) == "__main__":
        results.append(check(name, fn))

print("all vsync checks passed" if all(results) else "FAILURES")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
