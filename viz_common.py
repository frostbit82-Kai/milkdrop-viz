"""
viz_common.py - Shared code for milkdrop-viz and milkdrop-viz2.
Used by both visualizer versions to reduce duplication.

Exports:
  AudioCapture       — from audio.py (identical in both)
  IPCReader          — from ipc_reader.py (identical in both)
  Bridge, ToastLabel, SettingsDialog, KeymapDialog, ContextMenu  — from ui.py (identical in both)
  Renderer, _build_key_lookup, _token_matches                    — from renderer.py (identical in both)
  ProjectM4, load_projectm                                        — projectm_wrapper (milkdrop-viz is superset)
  QUALITY_MESH, DEFAULT_KEYMAPS, common config helpers            — from config.py (shared logic)
"""

# ─── audio ────────────────────────────────────────────────────────────────────

import os
import json
import time
import logging
import threading
import subprocess
from collections import deque
import numpy as np

logger_audio = logging.getLogger("visualizer.audio")


def _log(msg: str):
    print(msg)             # standalone SDL viz (no logging config)
    logger_audio.info(msg) # embedded player (bomsai_player.log)


try:
    import sounddevice as sd
    _SD_AVAILABLE = True
except ImportError:
    _SD_AVAILABLE = False
    print("[audio] sounddevice not installed — visualization will run silent")


class AudioCapture:
    CHUNK = 512

    def __init__(self, device_name: str):
        self._device_name = device_name
        self._buffer: deque = deque(maxlen=8)  # Keep last 8 audio chunks
        self._lock = threading.Lock()
        self._stream = None          # sounddevice backend
        self._proc = None            # parec backend
        self._reader = None
        self._stop_flag = False

    # ── parec backend (preferred): direct, exact source capture ─────────
    def _start_parec(self) -> bool:
        """Capture via 'parec' (pulseaudio-utils). Connects straight to the
        named source — no ALSA plugins, no stream re-routing required."""
        import shutil
        if not self._device_name or shutil.which("parec") is None:
            return False
        cmd = ["parec", f"--device={self._device_name}",
               "--format=float32le", "--rate=44100", "--channels=2",
               "--latency-msec=30"]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as e:
            _log(f"[audio] parec failed to start: {e}")
            self._proc = None
            return False
        time.sleep(0.2)
        if self._proc.poll() is not None:
            err = (self._proc.stderr.read() or b"").decode(
                errors="ignore").strip()
            _log(f"[audio] parec rejected source "
                 f"'{self._device_name}': {err or 'exited'}")
            self._proc = None
            return False

        self._stop_flag = False
        nbytes = self.CHUNK * 2 * 4          # frames × channels × float32

        def reader():
            proc = self._proc
            while not self._stop_flag and proc and proc.poll() is None:
                try:
                    data = proc.stdout.read(nbytes)
                except Exception:
                    break
                if not data:
                    break
                arr = np.frombuffer(data, dtype=np.float32)
                if arr.size % 2:
                    arr = arr[:-1]
                chunk = arr.reshape(-1, 2)
                with self._lock:
                    self._buffer.append(chunk)
            if not self._stop_flag:
                _log("[audio] parec stream ended (source gone?)")

        self._reader = threading.Thread(target=reader, daemon=True)
        self._reader.start()
        _log(f"[audio] parec capture started ← {self._device_name}")
        return True

    def _pulse_device(self) -> "int | None":
        """Index of PortAudio's pulse/pipewire passthrough device, if any.
        Prefer 'pulse' (honours PULSE_SOURCE) over 'pipewire'/'default'."""
        found = {}
        for i, d in enumerate(sd.query_devices()):
            n = d["name"].lower()
            if n in ("pulse", "pipewire", "default") and d["max_input_channels"] > 0:
                found.setdefault(n, i)
        for n in ("pulse", "pipewire", "default"):
            if n in found:
                return found[n]
        return None

    def _move_to_source(self):
        """
        Re-route our recording stream to the requested source via
        'pactl move-source-output' — works regardless of which passthrough
        device PortAudio opened (the PULSE_SOURCE env var only affects the
        'pulse' plugin; 'pipewire'/'default' ignore it). Runs in a thread:
        the stream registers with the sound server asynchronously.
        """
        target = self._device_name
        if not target:
            return

        def worker():
            pid = str(os.getpid())
            for _ in range(6):                  # up to ~3s for registration
                try:
                    out = subprocess.run(
                        ["pactl", "-f", "json", "list", "source-outputs"],
                        capture_output=True, text=True, timeout=3)
                    for o in json.loads(out.stdout or "[]"):
                        props = o.get("properties", {}) or {}
                        if props.get("application.process.id") != pid:
                            continue
                        r = subprocess.run(
                            ["pactl", "move-source-output",
                             str(o["index"]), target],
                            capture_output=True, text=True, timeout=3)
                        if r.returncode == 0:
                            _log(f"[audio] Capture re-routed → {target}")
                        else:
                            _log(f"[audio] move-source-output failed: "
                                 f"{(r.stderr or '').strip()}")
                        return
                except FileNotFoundError:
                    _log("[audio] 'pactl' not found — source selection needs "
                         "it: sudo apt install pulseaudio-utils")
                    return
                except Exception as e:
                    _log(f"[audio] Source re-route error: {e}")
                    return
                time.sleep(0.5)
            _log("[audio] Could not find our capture stream to re-route "
                 "(pactl list source-outputs)")

        threading.Thread(target=worker, daemon=True).start()

    def _resolve_device(self) -> int:
        """Find device index by partial name match, fall back to any monitor."""
        if not _SD_AVAILABLE:
            return 0
        devices = sd.query_devices()
        name = self._device_name.lower()
        if name:
            for i, d in enumerate(devices):
                if name in d["name"].lower() and d["max_input_channels"] > 0:
                    return i
        for i, d in enumerate(devices):
            if "monitor" in d["name"].lower() and d["max_input_channels"] > 0:
                print(f"[audio] Falling back to monitor: {d['name']}")
                return i
        return sd.default.device[0]

    def start(self):
        # Preferred: parec talks to the sound server directly
        if self._start_parec():
            return
        if not _SD_AVAILABLE:
            _log("[audio] No capture backend available (no parec, "
                 "no sounddevice)")
            return

        # Fallback: sounddevice/PortAudio.
        # Route explicit Pulse source names through the pulse device
        device = None
        if self._device_name:
            os.environ["PULSE_SOURCE"] = self._device_name
            device = self._pulse_device()
        else:
            os.environ.pop("PULSE_SOURCE", None)
        if device is None:
            device = self._resolve_device()
        _log(f"[audio] Using device: {sd.query_devices(device)['name']} "
             f"(source: {self._device_name or 'default'})")

        def callback(indata, frames, time_info, status):
            data = indata
            if data.ndim == 2 and data.shape[1] == 1:
                data = np.repeat(data, 2, axis=1)   # mono → stereo
            with self._lock:
                self._buffer.append(data.copy())

        # Try stereo@44.1k first, then progressively relax: device-default
        # samplerate, then mono (mics/line-in are often 1-channel).
        last_err = None
        for channels, samplerate in ((2, 44100), (2, None),
                                     (1, 44100), (1, None)):
            try:
                self._stream = sd.InputStream(
                    device=device,
                    channels=channels,
                    samplerate=samplerate,
                    blocksize=self.CHUNK,
                    dtype="float32",
                    callback=callback,
                )
                self._stream.start()
                self._move_to_source()
                return
            except Exception as e:
                last_err = e
                self._stream = None
        _log(f"[audio] Could not open audio stream: {last_err}")
        _log("[audio] Visualization will run without audio reaction.")

    def stop(self):
        self._stop_flag = True
        if self._proc:
            try:
                self._proc.kill()
                self._proc.wait(timeout=2)
            except Exception:
                pass
            self._proc = None
        if self._reader:
            try:
                self._reader.join(timeout=1)
            except Exception:
                pass
            self._reader = None
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def is_alive(self) -> bool:
        """True if a capture backend is currently delivering (or able to)."""
        if self._proc is not None and self._proc.poll() is None:
            return True
        return self._stream is not None

    def get_chunk(self) -> "np.ndarray | None":
        with self._lock:
            if self._buffer:
                return self._buffer.popleft()
        return None

    def update_device(self, device_name: str):
        """Hot-swap audio device (Audio Source menu / settings dialog)."""
        was_running = self._stream is not None or self._proc is not None
        self.stop()
        self._device_name = device_name
        if was_running:
            self.start()


# ─── ipc_reader ───────────────────────────────────────────────────────────────

import sys

logger_ipc = logging.getLogger(__name__)


class IPCReader(threading.Thread):
    def __init__(self, renderer):
        super().__init__(daemon=True, name="ipc-reader")
        self._renderer = renderer

    def run(self):
        try:
            for raw_line in sys.stdin:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._dispatch(obj)
        except Exception:
            pass   # stdin closed / pipe broken — just exit thread

    def _dispatch(self, obj: dict):
        cmd = obj.get("cmd", "")
        r   = self._renderer

        if cmd == "quit":
            r._running = False

        elif cmd == "next_preset":
            r.request_next()

        elif cmd == "prev_preset":
            r.request_prev()

        elif cmd == "lock":
            locked = bool(obj.get("locked", False))
            with r._lock:
                r._req_lock   = True
                r._req_lock_v = locked

        elif cmd == "geometry":
            x = obj.get("x")
            y = obj.get("y")
            w = obj.get("w")
            h = obj.get("h")
            if all(v is not None for v in (x, y, w, h)):
                with r._lock:
                    r._req_geometry = (int(x), int(y), int(w), int(h))

        elif cmd == "show_window":
            with r._lock:
                r._req_show_window = True


# ─── config (shared logic only — paths stay local per instance) ───────────────

QUALITY_MESH = {
    "low":    (32, 24),
    "medium": (48, 36),
    "high":   (64, 48),
    "ultra":  (96, 72),
}

# Default keybindings — SDL2 keysym names
# Special values: "SCROLLLOCK" uses scancode; "DBLCLICK" is mouse
DEFAULT_KEYMAPS = {
    "next":             "RIGHT",
    "prev":             "LEFT",
    "hard_cut_next":    "SPACE",
    "random":           "r",
    "lock":             "SCROLLLOCK",
    "toggle_hard_cuts": "h",
    "fullscreen":       "f",
    "fullscreen_alt":   "F11",
    "rate_up":          "+",
    "rate_down":        "-",
    "quit":             "ESCAPE",
    "toggle_mode":      "m",
    "fullscreen_mouse": "DBLCLICK",
}


def load_config_from(config_path, default_config: dict) -> dict:
    """Load config.json, merging over default_config. Path-agnostic."""
    from pathlib import Path
    cfg = dict(default_config)
    p = Path(config_path)
    if p.exists():
        try:
            with open(p) as f:
                cfg.update(json.load(f))
        except Exception as e:
            print(f"[config] Warning: could not load config.json: {e}")
    return cfg


def save_config_to(config_path, cfg: dict):
    """Save config.json. Path-agnostic."""
    try:
        with open(config_path, "w") as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        print(f"[config] Warning: could not save config.json: {e}")


def load_ratings_from(ratings_path) -> dict:
    """Load ratings.json. Path-agnostic."""
    from pathlib import Path
    p = Path(ratings_path)
    if p.exists():
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_ratings_to(ratings_path, ratings: dict):
    """Save ratings.json. Path-agnostic."""
    try:
        with open(ratings_path, "w") as f:
            json.dump(ratings, f, indent=4, sort_keys=True)
    except Exception as e:
        print(f"[config] Warning: could not save ratings.json: {e}")


def load_keymaps_from(keymaps_path) -> dict:
    """Load keymaps.json, merging over DEFAULT_KEYMAPS. Path-agnostic."""
    from pathlib import Path
    km = dict(DEFAULT_KEYMAPS)
    p = Path(keymaps_path)
    if p.exists():
        try:
            with open(p) as f:
                km.update(json.load(f))
        except Exception as e:
            print(f"[config] Warning: could not load keymaps.json: {e}")
    return km


def save_keymaps_to(keymaps_path, km: dict):
    """Save keymaps.json. Path-agnostic."""
    try:
        with open(keymaps_path, "w") as f:
            json.dump(km, f, indent=4)
    except Exception as e:
        print(f"[config] Warning: could not save keymaps.json: {e}")


def get_preset_rating(ratings: dict, name: str) -> int:
    return ratings.get(name, 5)


def set_preset_rating(ratings: dict, name: str, value: int) -> int:
    """Clamp to 0-10, return new value. Rating 0 = excluded from random."""
    # ponytail: skip synchronous save here; ratings persisted on app exit instead.
    # Avoids blocking GUI on every rating change (was causing stutter).
    value = max(0, min(10, value))
    ratings[name] = value
    return value


def is_excluded(ratings: dict, name: str) -> bool:
    """Rating of 0 means excluded from random rotation."""
    return ratings.get(name, 5) == 0


# ─── projectm_wrapper ─────────────────────────────────────────────────────────

import ctypes
import ctypes.util
import glob
import random
from pathlib import Path

logger_pm = logging.getLogger(__name__)

# Cross-platform library discovery
try:
    from pathlib import Path as PathlibPath
    _core_path = PathlibPath(__file__).parent.parent / "core"
    if _core_path.exists():
        sys.path.insert(0, str(_core_path.parent))
        from core.platform_config import (
            get_projectm_candidates,
            get_projectm_playlist_candidates,
        )
    else:
        raise ImportError("core not found, will use inline fallbacks")
except Exception:
    # Fallback if running standalone
    def get_projectm_candidates():
        if sys.platform == "win32":
            return ["libprojectM-4.dll", "projectM-4.dll"]
        elif sys.platform == "darwin":
            return ["/usr/local/lib/libprojectM-4.dylib", "/opt/homebrew/lib/libprojectM-4.dylib"]
        else:
            return [
                "/usr/local/lib/libprojectM-4.so",
                "/usr/local/lib/libprojectM-4.so.4",
                "/usr/local/lib/libprojectM-4.so.4.1.0",
                "/usr/lib/x86_64-linux-gnu/libprojectM-4.so",
            ]

    def get_projectm_playlist_candidates():
        if sys.platform == "win32":
            # MSVC emits no "lib" prefix (CMake OUTPUT_NAME is projectM-4-playlist),
            # so the unprefixed name is what a Visual Studio build actually produces.
            # The prefixed name is kept for MinGW/MSYS2 builds, matching the core list.
            return ["libprojectM-4-playlist.dll", "projectM-4-playlist.dll"]
        elif sys.platform == "darwin":
            return ["/usr/local/lib/libprojectM-4-playlist.dylib"]
        else:
            return [
                "/usr/local/lib/libprojectM-4-playlist.so",
                "/usr/local/lib/libprojectM-4-playlist.so.4",
                "/usr/local/lib/libprojectM-4-playlist.so.4.1.0",
                "/usr/lib/x86_64-linux-gnu/libprojectM-4-playlist.so",
            ]


def _find_lib(candidates: list, util_name: str):
    """Find and load a shared library from a list of candidate paths."""
    for p in candidates:
        if os.path.exists(p):
            try:
                return ctypes.CDLL(p)
            except OSError as e:
                logger_pm.warning(f"Failed to load {p}: {e}")
                continue
    # glob versioned
    for p in candidates:
        base = p.rstrip(".0123456789")
        for match in glob.glob(base + "*"):
            if os.path.isfile(match):
                try:
                    return ctypes.CDLL(match)
                except OSError as e:
                    logger_pm.warning(f"Failed to load {match}: {e}")
                    continue
    name = ctypes.util.find_library(util_name)
    if name:
        try:
            return ctypes.CDLL(name)
        except OSError as e:
            logger_pm.warning(f"Failed to load {name} via find_library: {e}")
    return None


def _load_libraries(config_dir=None):
    """Returns (core_lib, playlist_lib). playlist_lib may be None.

    config_dir: directory containing config.json (defaults to the caller's dir).
    """
    if config_dir is None:
        config_dir = PathlibPath(__file__).parent
    config_path = PathlibPath(config_dir) / "config.json"
    explicit_core = ""
    if config_path.exists():
        with open(config_path) as f:
            cfg = json.load(f)
        explicit_core = cfg.get("projectm_lib", "")

    # Core
    if explicit_core and os.path.exists(explicit_core):
        core = ctypes.CDLL(explicit_core)
    else:
        core = _find_lib(get_projectm_candidates(), "projectM-4")

    if core is None:
        platform_name = sys.platform
        raise RuntimeError(
            f"Cannot find libprojectM-4 ({platform_name}). "
            f"Tried: {get_projectm_candidates()}. "
            "Set 'projectm_lib' in config.json to the full path."
        )

    # Playlist (optional but strongly preferred)
    playlist = _find_lib(get_projectm_playlist_candidates(), "projectM-4-playlist")

    return core, playlist


class ProjectM4:

    def __init__(self, core: ctypes.CDLL, playlist):
        self._core     = core
        self._playlist = playlist
        self._pm       = None   # core handle  (c_void_p)
        self._pl       = None   # playlist handle (c_void_p)
        self._locked   = False

        # Fallback preset list when no playlist .so
        self._preset_files: list = []
        self._preset_index: int = 0

        self._setup_core_signatures()
        if playlist:
            self._setup_playlist_signatures()

    # ── Signature setup ───────────────────────────────────────────────────

    def _setup_core_signatures(self):
        C = self._core

        C.projectm_create.restype  = ctypes.c_void_p
        C.projectm_create.argtypes = []

        C.projectm_destroy.restype  = None
        C.projectm_destroy.argtypes = [ctypes.c_void_p]

        # Texture search paths — without these, presets that sample image
        # textures render WHITE/blank (built-in noise textures still work)
        try:
            C.projectm_set_texture_search_paths.restype  = None
            C.projectm_set_texture_search_paths.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_char_p),
                ctypes.c_size_t,
            ]
        except AttributeError:
            print("[projectm_wrapper] projectm_set_texture_search_paths "
                  "missing from libprojectM")

        # THE KEY FIX: render is projectm_opengl_render_frame, not projectm_render_frame
        C.projectm_opengl_render_frame.restype  = None
        C.projectm_opengl_render_frame.argtypes = [ctypes.c_void_p]

        C.projectm_set_window_size.restype  = None
        C.projectm_set_window_size.argtypes = [ctypes.c_void_p,
                                                ctypes.c_size_t, ctypes.c_size_t]

        C.projectm_get_window_size.restype  = None
        C.projectm_get_window_size.argtypes = [ctypes.c_void_p,
                                                ctypes.POINTER(ctypes.c_size_t),
                                                ctypes.POINTER(ctypes.c_size_t)]

        C.projectm_pcm_add_float.restype  = None
        C.projectm_pcm_add_float.argtypes = [ctypes.c_void_p,
                                              ctypes.POINTER(ctypes.c_float),
                                              ctypes.c_uint32,
                                              ctypes.c_uint32]

        C.projectm_set_fps.restype  = None
        C.projectm_set_fps.argtypes = [ctypes.c_void_p, ctypes.c_int32]

        C.projectm_get_fps.restype  = ctypes.c_int32
        C.projectm_get_fps.argtypes = [ctypes.c_void_p]

        C.projectm_set_mesh_size.restype  = None
        C.projectm_set_mesh_size.argtypes = [ctypes.c_void_p,
                                              ctypes.c_size_t, ctypes.c_size_t]

        C.projectm_set_beat_sensitivity.restype  = None
        C.projectm_set_beat_sensitivity.argtypes = [ctypes.c_void_p, ctypes.c_float]

        C.projectm_get_beat_sensitivity.restype  = ctypes.c_float
        C.projectm_get_beat_sensitivity.argtypes = [ctypes.c_void_p]

        C.projectm_set_hard_cut_enabled.restype  = None
        C.projectm_set_hard_cut_enabled.argtypes = [ctypes.c_void_p, ctypes.c_bool]

        C.projectm_get_hard_cut_enabled.restype  = ctypes.c_bool
        C.projectm_get_hard_cut_enabled.argtypes = [ctypes.c_void_p]

        C.projectm_set_hard_cut_sensitivity.restype  = None
        C.projectm_set_hard_cut_sensitivity.argtypes = [ctypes.c_void_p, ctypes.c_float]

        C.projectm_get_hard_cut_sensitivity.restype  = ctypes.c_float
        C.projectm_get_hard_cut_sensitivity.argtypes = [ctypes.c_void_p]

        C.projectm_set_hard_cut_duration.restype  = None
        C.projectm_set_hard_cut_duration.argtypes = [ctypes.c_void_p, ctypes.c_double]

        C.projectm_set_soft_cut_duration.restype  = None
        C.projectm_set_soft_cut_duration.argtypes = [ctypes.c_void_p, ctypes.c_double]

        C.projectm_set_preset_duration.restype  = None
        C.projectm_set_preset_duration.argtypes = [ctypes.c_void_p, ctypes.c_double]

        C.projectm_get_preset_duration.restype  = ctypes.c_double
        C.projectm_get_preset_duration.argtypes = [ctypes.c_void_p]

        # Lock lives on core handle
        C.projectm_set_preset_locked.restype  = None
        C.projectm_set_preset_locked.argtypes = [ctypes.c_void_p, ctypes.c_bool]

        C.projectm_get_preset_locked.restype  = ctypes.c_bool
        C.projectm_get_preset_locked.argtypes = [ctypes.c_void_p]

        # Direct preset load (used as fallback when no playlist .so)
        C.projectm_load_preset_file.restype  = None
        C.projectm_load_preset_file.argtypes = [ctypes.c_void_p,
                                                 ctypes.c_char_p,
                                                 ctypes.c_bool]

        # Preset-switched callback (used to track current name)
        C.projectm_set_preset_switch_requested_event_callback.restype  = None
        C.projectm_set_preset_switch_requested_event_callback.argtypes = [
            ctypes.c_void_p,   # handle
            ctypes.c_void_p,   # callback fn ptr (CFUNCTYPE)
            ctypes.c_void_p,   # user_data
        ]

    def _setup_playlist_signatures(self):
        P = self._playlist

        # projectm_playlist_create takes NO arguments (unlike what I assumed before)
        P.projectm_playlist_create.restype  = ctypes.c_void_p
        P.projectm_playlist_create.argtypes = []

        P.projectm_playlist_destroy.restype  = None
        P.projectm_playlist_destroy.argtypes = [ctypes.c_void_p]

        # connect(playlist_handle, projectm_handle)
        P.projectm_playlist_connect.restype  = None
        P.projectm_playlist_connect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        P.projectm_playlist_add_path.restype  = ctypes.c_uint32
        P.projectm_playlist_add_path.argtypes = [ctypes.c_void_p,  # playlist
                                                   ctypes.c_char_p,  # path
                                                   ctypes.c_bool,    # recurse
                                                   ctypes.c_bool]    # allow_duplicates

        # Batch add of individual preset files (for incremental background load)
        try:
            P.projectm_playlist_add_presets.restype  = ctypes.c_uint32
            P.projectm_playlist_add_presets.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_char_p),
                ctypes.c_uint32,
                ctypes.c_bool,
            ]
            self._has_add_presets = True
        except AttributeError:
            self._has_add_presets = False

        P.projectm_playlist_size.restype  = ctypes.c_uint32
        P.projectm_playlist_size.argtypes = [ctypes.c_void_p]

        P.projectm_playlist_item.restype  = ctypes.c_char_p
        P.projectm_playlist_item.argtypes = [ctypes.c_void_p, ctypes.c_uint32]

        P.projectm_playlist_get_position.restype  = ctypes.c_uint32
        P.projectm_playlist_get_position.argtypes = [ctypes.c_void_p]

        P.projectm_playlist_set_position.restype  = None
        P.projectm_playlist_set_position.argtypes = [ctypes.c_void_p,  # playlist
                                                       ctypes.c_uint32,  # index
                                                       ctypes.c_bool]    # hard_cut

        P.projectm_playlist_play_next.restype  = None
        P.projectm_playlist_play_next.argtypes = [ctypes.c_void_p, ctypes.c_bool]

        P.projectm_playlist_play_previous.restype  = None
        P.projectm_playlist_play_previous.argtypes = [ctypes.c_void_p, ctypes.c_bool]

        P.projectm_playlist_get_shuffle.restype  = ctypes.c_bool
        P.projectm_playlist_get_shuffle.argtypes = [ctypes.c_void_p]

        P.projectm_playlist_set_shuffle.restype  = None
        P.projectm_playlist_set_shuffle.argtypes = [ctypes.c_void_p, ctypes.c_bool]

        P.projectm_playlist_clear.restype  = None
        P.projectm_playlist_clear.argtypes = [ctypes.c_void_p]

    # ── Public API ────────────────────────────────────────────────────────

    def create(self, width: int, height: int, fps: int = 60):
        self._pm = self._core.projectm_create()
        if not self._pm:
            raise RuntimeError("projectm_create() returned NULL")

        self._core.projectm_set_window_size(self._pm, width, height)
        self._core.projectm_set_fps(self._pm, fps)

        if self._playlist:
            self._pl = self._playlist.projectm_playlist_create()
            if not self._pl:
                raise RuntimeError("projectm_playlist_create() returned NULL")
            self._playlist.projectm_playlist_connect(self._pl, self._pm)
        else:
            print("[projectm_wrapper] No playlist .so — using direct file loading fallback")

    def clear_playlist(self):
        """Remove all items from the playlist (used when switching folders)."""
        if self._pl and self._playlist:
            try:
                self._playlist.projectm_playlist_clear(self._pl)
            except Exception as e:
                print(f"[projectm_wrapper] clear_playlist failed: {e}")

    def add_presets(self, paths: list) -> int:
        """Append individual preset files to the playlist (one batch call).
        Returns the number added; 0 if unsupported (caller can fall back)."""
        if (not paths or not self._pl or not self._playlist
                or not getattr(self, "_has_add_presets", False)):
            return 0
        try:
            arr = (ctypes.c_char_p * len(paths))(
                *[str(p).encode() for p in paths])
            return int(self._playlist.projectm_playlist_add_presets(
                self._pl, arr, len(paths), False))
        except Exception as e:
            print(f"[projectm_wrapper] add_presets failed: {e}")
            return 0

    def set_texture_search_paths(self, paths: list):
        """Tell projectM where preset textures live. Presets that sample
        image textures render white/blank without this."""
        if not self._pm or not paths:
            return
        try:
            arr = (ctypes.c_char_p * len(paths))(
                *[str(p).encode() for p in paths])
            self._core.projectm_set_texture_search_paths(
                self._pm, arr, len(paths))
            print(f"[projectm_wrapper] Texture search paths: {paths}")
        except Exception as e:
            print(f"[projectm_wrapper] set_texture_search_paths failed: {e}")

    def destroy(self):
        if self._pl and self._playlist:
            self._playlist.projectm_playlist_destroy(self._pl)
            self._pl = None
        if self._pm:
            self._core.projectm_destroy(self._pm)
            self._pm = None

    def load_preset_dir(self, path: str, shuffle: bool = False) -> int:
        """Load presets from a directory.

        shuffle: if True, enable projectM's internal shuffle after loading.
        milkdrop-viz keeps this False (Python manages order); milkdrop-viz2
        historically used True — callers pass what they need.
        """
        if self._pl and self._playlist:
            count = self._playlist.projectm_playlist_add_path(
                self._pl, path.encode(), False, False
            )
            # Order is managed in Python (ProjectMVisualizer) so that "prev"
            # returns to the actual previous preset and shuffle reorders the
            # list deterministically — keep projectM's own shuffle OFF by
            # default; pass shuffle=True to enable projectM's own shuffler.
            self._playlist.projectm_playlist_set_shuffle(self._pl, shuffle)
            return count
        else:
            # Fallback: collect files ourselves, load first one directly
            self._preset_files = sorted(
                str(p) for p in Path(path).glob("*.milk")
            )
            random.shuffle(self._preset_files)
            if self._preset_files:
                self._preset_index = 0
                self._load_preset_direct(self._preset_index)
            return len(self._preset_files)

    def _load_preset_direct(self, index: int):
        if 0 <= index < len(self._preset_files):
            path = self._preset_files[index].encode()
            self._core.projectm_load_preset_file(self._pm, path, True)

    def render_frame(self):
        self._core.projectm_opengl_render_frame(self._pm)

    def add_pcm_float(self, samples, count: int, channels: int = 2):
        arr = (ctypes.c_float * len(samples))(*samples)
        self._core.projectm_pcm_add_float(self._pm, arr, count, channels)

    def set_window_size(self, w: int, h: int):
        self._core.projectm_set_window_size(self._pm, w, h)

    def next_preset(self, hard_cut: bool = True):
        if self._locked:
            return
        if self._pl and self._playlist:
            self._playlist.projectm_playlist_play_next(self._pl, hard_cut)
        else:
            self._preset_index = (self._preset_index + 1) % max(1, len(self._preset_files))
            self._load_preset_direct(self._preset_index)

    def prev_preset(self, hard_cut: bool = True):
        if self._locked:
            return
        if self._pl and self._playlist:
            self._playlist.projectm_playlist_play_previous(self._pl, hard_cut)
        else:
            self._preset_index = (self._preset_index - 1) % max(1, len(self._preset_files))
            self._load_preset_direct(self._preset_index)

    def set_locked(self, locked: bool):
        self._locked = locked
        self._core.projectm_set_preset_locked(self._pm, locked)

    def get_locked(self) -> bool:
        return bool(self._core.projectm_get_preset_locked(self._pm))

    def current_preset_name(self) -> str:
        try:
            if self._pl and self._playlist:
                pos = self._playlist.projectm_playlist_get_position(self._pl)
                raw = self._playlist.projectm_playlist_item(self._pl, pos)
                if raw:
                    return Path(raw.decode(errors="replace")).stem
            elif self._preset_files:
                return Path(self._preset_files[self._preset_index]).stem
        except Exception:
            pass
        return "Unknown"

    def playlist_size(self) -> int:
        if self._pl and self._playlist:
            return self._playlist.projectm_playlist_size(self._pl)
        return len(self._preset_files)

    def playlist_item(self, index: int) -> str:
        """Preset name (stem) at the given native playlist index."""
        try:
            if self._pl and self._playlist:
                raw = self._playlist.projectm_playlist_item(self._pl, int(index))
                if raw:
                    return Path(raw.decode(errors="replace")).stem
            elif 0 <= index < len(self._preset_files):
                return Path(self._preset_files[index]).stem
        except Exception:
            pass
        return ""

    def playlist_position(self) -> int:
        """Current native playlist index."""
        try:
            if self._pl and self._playlist:
                return int(self._playlist.projectm_playlist_get_position(self._pl))
        except Exception:
            pass
        return self._preset_index

    def set_playlist_position(self, index: int, hard_cut: bool = True):
        """Jump to a specific native playlist index (loads that preset).
        Explicit jumps work even when locked — lock only suppresses projectM's
        own auto-switching; manual next/prev is driven through here."""
        if self._pl and self._playlist:
            # Bounds-guard: an out-of-range index makes libprojectM index a
            # bad preset pointer and segfault inside projectm_load_preset_file.
            size = int(self._playlist.projectm_playlist_size(self._pl))
            if size <= 0:
                return
            idx = int(index) % size
            self._playlist.projectm_playlist_set_position(
                self._pl, idx, bool(hard_cut))
        else:
            self._preset_index = int(index) % max(1, len(self._preset_files))
            self._load_preset_direct(self._preset_index)

    def set_shuffle(self, on: bool):
        """Toggle projectM's *internal* shuffle (normally kept OFF — order is
        managed in Python)."""
        if self._pl and self._playlist:
            self._playlist.projectm_playlist_set_shuffle(self._pl, bool(on))

    def set_beat_sensitivity(self, v: float):
        self._core.projectm_set_beat_sensitivity(self._pm, v)

    def set_fps(self, fps: int):
        self._core.projectm_set_fps(self._pm, fps)

    def set_mesh_size(self, x: int, y: int):
        self._core.projectm_set_mesh_size(self._pm, x, y)

    def set_hard_cut_enabled(self, enabled: bool):
        self._core.projectm_set_hard_cut_enabled(self._pm, enabled)

    def set_hard_cut_sensitivity(self, v: float):
        self._core.projectm_set_hard_cut_sensitivity(self._pm, v)

    def set_preset_duration(self, seconds: float):
        # projectM manages its own timer; we also manage ours in visualizer.py
        # but setting this keeps projectM's internal state consistent
        self._core.projectm_set_preset_duration(self._pm, seconds)

    def set_soft_cut_duration(self, seconds: float):
        self._core.projectm_set_soft_cut_duration(self._pm, seconds)


def load_projectm(config_dir=None) -> ProjectM4:
    """
    Load libprojectM-4.so and (optionally) libprojectM-4-playlist.so.
    Returns a ProjectM4 instance. Call .create(w, h, fps) after this.

    config_dir: directory containing config.json (defaults to viz_common's parent).
    """
    core, playlist = _load_libraries(config_dir)
    print(f"[projectm_wrapper] Core:     {core._name}")
    if playlist:
        print(f"[projectm_wrapper] Playlist: {playlist._name}")
    else:
        print("[projectm_wrapper] Playlist .so not found — using direct load fallback")
    return ProjectM4(core, playlist)


# ─── ui ───────────────────────────────────────────────────────────────────────

from PyQt6.QtWidgets import (
    QApplication, QMenu, QDialog, QFormLayout, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSpinBox, QDoubleSpinBox, QComboBox,
    QDialogButtonBox, QCheckBox, QFrame, QTableWidget, QTableWidgetItem,
    QHeaderView, QLineEdit, QSizePolicy, QMessageBox, QFileDialog
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QPoint, QTimer
from PyQt6.QtGui import QKeySequence, QFont, QCursor


# ─────────────────────────────────────────────────────────────────────────────
#  Bridge  (render thread → Qt UI thread signals)
# ─────────────────────────────────────────────────────────────────────────────

class Bridge(QObject):
    preset_changed     = pyqtSignal(str, int)   # (preset_name, rating)
    lock_changed       = pyqtSignal(bool)
    borderless_changed = pyqtSignal(bool)
    status_message     = pyqtSignal(str)        # transient status (e.g. "Hard cuts: ON")
    show_toast         = pyqtSignal(str)        # brief overlay message
    request_quit       = pyqtSignal()
    toggle_preset_browser = pyqtSignal()        # show/hide the preset browser
    playlist_changed   = pyqtSignal()           # order/contents changed (sort, delete, mode)


# ─────────────────────────────────────────────────────────────────────────────
#  Toast notification  (top-right corner, auto-hides)
# ─────────────────────────────────────────────────────────────────────────────

class ToastLabel(QLabel):
    """
    Frameless always-on-top label that appears in the top-right corner
    of the screen for a short duration then fades out.
    """
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.X11BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setStyleSheet("""
            QLabel {
                color: #ffffff;
                background: rgba(0, 0, 0, 175);
                border-radius: 6px;
                padding: 8px 14px;
                font-size: 14px;
                font-weight: 500;
            }
        """)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_message(self, text: str, duration_ms: int = 2000):
        self.setText(text)
        self.adjustSize()
        # Position: top-right of the primary screen, 20px inset
        screen = QApplication.primaryScreen().geometry()
        x = screen.right()  - self.width()  - 20
        y = screen.top()    + 20
        self.move(x, y)
        self.show()
        self.raise_()
        self._hide_timer.start(duration_ms)


# ─────────────────────────────────────────────────────────────────────────────
#  Settings dialog
# ─────────────────────────────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Visualizer Settings")
        self.setMinimumWidth(400)

        layout = QFormLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        # Preset and texture directories. Neither ships with the app — presets
        # and textures are other people's work, downloaded separately — so
        # these two rows are the first thing a new install has to answer.
        self._preset_dir = self._dir_row(
            layout, "Preset directory:", cfg.get("preset_dir", ""),
            "Choose the folder holding your .milk presets")
        self._texture_dir = self._dir_row(
            layout, "Texture directory:", cfg.get("texture_dir", ""),
            "Choose the folder holding your preset textures")

        # Duration
        self._dur = QSpinBox()
        self._dur.setRange(5, 600)
        self._dur.setValue(cfg.get("preset_duration", 30))
        self._dur.setSuffix("  sec")
        layout.addRow("Preset duration:", self._dur)

        # Random extra
        self._rnd = QSpinBox()
        self._rnd.setRange(0, 300)
        self._rnd.setValue(cfg.get("preset_random_extra", 15))
        self._rnd.setSuffix("  sec extra")
        layout.addRow("Random extra:", self._rnd)

        # FPS
        self._fps = QSpinBox()
        self._fps.setRange(0, 240)
        self._fps.setSpecialValueText("Unlimited")   # 0 = uncapped
        self._fps.setValue(cfg.get("fps", 60))
        self._fps.setSuffix("  fps")
        layout.addRow("FPS:", self._fps)

        # Quality
        self._quality = QComboBox()
        for q in ["low", "medium", "high", "ultra"]:
            self._quality.addItem(q.capitalize(), q)
        idx = self._quality.findData(cfg.get("quality", "high"))
        if idx >= 0:
            self._quality.setCurrentIndex(idx)
        layout.addRow("Quality:", self._quality)

        # Soft cut duration
        self._soft_cut = QDoubleSpinBox()
        self._soft_cut.setRange(0.5, 10.0)
        self._soft_cut.setSingleStep(0.5)
        self._soft_cut.setDecimals(1)
        self._soft_cut.setValue(cfg.get("soft_cut_duration", 3.0))
        self._soft_cut.setSuffix("  sec")
        layout.addRow("Blend duration:", self._soft_cut)

        # Beat sensitivity
        self._beat = QDoubleSpinBox()
        self._beat.setRange(0.1, 4.0)
        self._beat.setSingleStep(0.1)
        self._beat.setDecimals(1)
        self._beat.setValue(cfg.get("beat_sensitivity", 1.0))
        layout.addRow("Beat sensitivity:", self._beat)

        # Hard cuts
        self._hard_cuts = QCheckBox("Enable hard cuts on beats")
        self._hard_cuts.setChecked(cfg.get("hard_cuts_enabled", True))
        layout.addRow("Hard cuts:", self._hard_cuts)

        # Hard cut sensitivity
        self._hcs = QDoubleSpinBox()
        self._hcs.setRange(0.1, 4.0)
        self._hcs.setSingleStep(0.1)
        self._hcs.setDecimals(1)
        self._hcs.setValue(cfg.get("hard_cut_sensitivity", 1.0))
        layout.addRow("Hard cut sensitivity:", self._hcs)

        # Audio device
        self._audio_dev = QComboBox()
        self._audio_dev.setEditable(True)
        self._audio_dev.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if _SD_AVAILABLE:
            try:
                for d in sd.query_devices():
                    if d["max_input_channels"] > 0:
                        self._audio_dev.addItem(d["name"])
            except Exception:
                pass
        self._audio_dev.setCurrentText(cfg.get("audio_device", ""))
        layout.addRow("Audio device:", self._audio_dev)

        # Buttons
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addRow(sep)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def _dir_row(self, layout, label: str, value: str, caption: str) -> QLineEdit:
        """A path field with a Browse button. Returns the field itself, so the
        caller keeps reading .text() exactly as it did before."""
        field = QLineEdit(value)
        browse = QPushButton("Browse\u2026")
        browse.setAutoDefault(False)          # or Enter in the field opens the picker

        def pick():
            start = field.text().strip() or os.path.expanduser("~")
            chosen = QFileDialog.getExistingDirectory(self, caption, start)
            if chosen:
                field.setText(chosen)

        browse.clicked.connect(pick)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(field, 1)
        row.addWidget(browse)
        layout.addRow(label, row)
        return field

    def get_config(self) -> dict:
        quality = self._quality.currentData()
        mx, my = QUALITY_MESH.get(quality, (64, 48))
        return {
            "preset_dir":           self._preset_dir.text().strip(),
            "texture_dir":          self._texture_dir.text().strip(),
            "preset_duration":      self._dur.value(),
            "preset_random_extra":  self._rnd.value(),
            "fps":                  self._fps.value(),
            "quality":              quality,
            "mesh_x":               mx,
            "mesh_y":               my,
            "soft_cut_duration":    round(self._soft_cut.value(), 1),
            "beat_sensitivity":     round(self._beat.value(), 1),
            "hard_cuts_enabled":    self._hard_cuts.isChecked(),
            "hard_cut_sensitivity": round(self._hcs.value(), 1),
            "audio_device":         self._audio_dev.currentText(),
        }


# ─────────────────────────────────────────────────────────────────────────────
#  Keymap editor dialog
# ─────────────────────────────────────────────────────────────────────────────

# Human-readable labels for each action
ACTION_LABELS = {
    "next":             "Next visual",
    "prev":             "Previous visual",
    "hard_cut_next":    "Hard cut to next visual",
    "random":           "Jump to random visual",
    "lock":             "Lock / unlock current visual",
    "toggle_hard_cuts": "Toggle hard cuts on/off",
    "fullscreen":       "Toggle fullscreen",
    "fullscreen_alt":   "Toggle fullscreen (alt key)",
    "rate_up":          "Rate up  (+1)",
    "rate_down":        "Rate down (−1)",
    "quit":             "Quit",
    "toggle_mode":      "Toggle random / sequential mode",
    "fullscreen_mouse": "Toggle fullscreen (mouse)",
}

ACTION_NOTES = {
    "fullscreen_mouse": "Value must be DBLCLICK",
    "lock":             "SCROLLLOCK uses the Scroll Lock key",
    "rate_down":        "Rating 0 excludes preset from random rotation",
}


class KeymapDialog(QDialog):
    def __init__(self, keymaps: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Mapping")
        self.setMinimumWidth(560)
        self.setMinimumHeight(480)
        self._keymaps = dict(keymaps)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # Info label
        info = QLabel(
            "Click a key cell and press a key to rebind.\n"
            "Special values: SCROLLLOCK · F1–F12 · SPACE · ESCAPE · DBLCLICK (mouse)\n"
            "Rating 0 = preset excluded from random rotation."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: palette(mid); font-size: 12px;")
        layout.addWidget(info)

        # Table
        self._table = QTableWidget(len(ACTION_LABELS), 3)
        self._table.setHorizontalHeaderLabels(["Action", "Key", "Notes"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(1, 130)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        for row, (action, label) in enumerate(ACTION_LABELS.items()):
            # Action label (read-only)
            lbl_item = QTableWidgetItem(label)
            lbl_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(row, 0, lbl_item)

            # Key value (editable via keypress)
            key_val = keymaps.get(action, DEFAULT_KEYMAPS.get(action, ""))
            key_item = QTableWidgetItem(key_val)
            key_item.setData(Qt.ItemDataRole.UserRole, action)
            key_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 1, key_item)

            # Notes
            note = ACTION_NOTES.get(action, "")
            note_item = QTableWidgetItem(note)
            note_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            note_item.setForeground(self.palette().mid())
            self._table.setItem(row, 2, note_item)

        self._table.installEventFilter(self)
        layout.addWidget(self._table)

        # Reset to defaults button
        btn_row = QHBoxLayout()
        reset_btn = QPushButton("Reset to defaults")
        reset_btn.clicked.connect(self._reset_defaults)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Dialog buttons
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if obj is self._table and event.type() == QEvent.Type.KeyPress:
            current = self._table.currentItem()
            if current and current.column() == 1:
                key = event.key()
                # Translate Qt key to a string token our renderer understands
                key_str = self._qt_key_to_token(key, event.text())
                if key_str:
                    current.setText(key_str)
                    action = current.data(Qt.ItemDataRole.UserRole)
                    self._keymaps[action] = key_str
                    return True
        return super().eventFilter(obj, event)

    def _qt_key_to_token(self, qt_key: int, text: str) -> str:
        """Convert a Qt key code to the string token used in keymaps.json."""
        from PyQt6.QtCore import Qt as _Qt
        special = {
            _Qt.Key.Key_Return:    "RETURN",
            _Qt.Key.Key_Enter:     "RETURN",
            _Qt.Key.Key_Escape:    "ESCAPE",
            _Qt.Key.Key_Space:     "SPACE",
            _Qt.Key.Key_Tab:       "TAB",
            _Qt.Key.Key_Backspace: "BACKSPACE",
            _Qt.Key.Key_Delete:    "DELETE",
            _Qt.Key.Key_Left:      "LEFT",
            _Qt.Key.Key_Right:     "RIGHT",
            _Qt.Key.Key_Up:        "UP",
            _Qt.Key.Key_Down:      "DOWN",
            _Qt.Key.Key_Home:      "HOME",
            _Qt.Key.Key_End:       "END",
            _Qt.Key.Key_PageUp:    "PAGEUP",
            _Qt.Key.Key_PageDown:  "PAGEDOWN",
            _Qt.Key.Key_ScrollLock: "SCROLLLOCK",
            _Qt.Key.Key_F1:  "F1",  _Qt.Key.Key_F2:  "F2",
            _Qt.Key.Key_F3:  "F3",  _Qt.Key.Key_F4:  "F4",
            _Qt.Key.Key_F5:  "F5",  _Qt.Key.Key_F6:  "F6",
            _Qt.Key.Key_F7:  "F7",  _Qt.Key.Key_F8:  "F8",
            _Qt.Key.Key_F9:  "F9",  _Qt.Key.Key_F10: "F10",
            _Qt.Key.Key_F11: "F11", _Qt.Key.Key_F12: "F12",
        }
        if qt_key in special:
            return special[qt_key]
        # Printable single character
        if text and len(text) == 1 and text.isprintable():
            return text
        return ""

    def _reset_defaults(self):
        self._keymaps = dict(DEFAULT_KEYMAPS)
        for row, action in enumerate(ACTION_LABELS):
            item = self._table.item(row, 1)
            if item:
                item.setText(DEFAULT_KEYMAPS.get(action, ""))

    def get_keymaps(self) -> dict:
        return dict(self._keymaps)


# ─────────────────────────────────────────────────────────────────────────────
#  Context menu  (spawned at cursor position, no overlay window)
# ─────────────────────────────────────────────────────────────────────────────

class ContextMenu(QMenu):
    """
    Right-click menu. Spawned by the renderer when it detects a right-click
    on the SDL2 window. Because there's no persistent overlay window, we
    temporarily create a hidden QWidget as parent so the menu has a valid
    native parent.
    """

    def __init__(self, renderer, cfg: dict, ratings: dict, keymaps: dict):
        # Need a real widget parent for proper theming
        from PyQt6.QtWidgets import QWidget
        self._tmp_parent = QWidget()
        self._tmp_parent.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
        )
        super().__init__(self._tmp_parent)
        self._renderer = renderer
        self._cfg      = cfg
        self._ratings  = ratings
        self._keymaps  = keymaps

        self.setStyleSheet("""
            QMenu {
                background: rgba(18, 18, 18, 235);
                color: #e8e8e8;
                border: 1px solid rgba(255,255,255,55);
                border-radius: 6px;
                padding: 5px 3px;
                font-size: 13px;
            }
            QMenu::item { padding: 7px 24px; border-radius: 4px; min-width: 180px; }
            QMenu::item:selected { background: rgba(255,255,255,22); }
            QMenu::separator { background: rgba(255,255,255,25); height: 1px; margin: 4px 10px; }
        """)

        self._build()

    def _build(self):
        locked = self._renderer.is_locked()

        self.addAction("▶   Next visual",      lambda: self._renderer.request_next())
        self.addAction("◀   Previous visual",  lambda: self._renderer.request_prev())
        self.addAction("⟳   Random visual",    lambda: self._renderer.request_random())
        self.addSeparator()

        self.addAction("🗂   Preset browser",   lambda: self._renderer.request_toggle_browser())
        self.addAction("↕   Sort by name",     lambda: self._renderer.request_sort("name"))
        self.addAction("★   Sort by rating",   lambda: self._renderer.request_sort("rating"))
        self.addAction("🗑   Delete 0★ presets…", self._confirm_delete_zero)
        self.addAction("↺   Reset all ratings…", self._confirm_reset_ratings)
        self.addSeparator()

        lock_text = "\U0001f512  Unlock visual" if locked else "\U0001f513  Lock visual"
        self.addAction(lock_text, lambda: self._renderer.request_toggle_lock())
        self.addSeparator()

        borderless = self._renderer.is_borderless()
        border_text = "☐   Show window border" if borderless else "▣   Borderless window"
        self.addAction(border_text, lambda: self._renderer.request_toggle_borderless())
        self.addSeparator()

        self.addAction("⌨   Key bindings…",   self._open_keymaps)
        self.addAction("⚙   Settings…",        self._open_settings)
        self.addSeparator()

        self.addAction("✕   Quit",             QApplication.quit)

    def _confirm_delete_zero(self):
        n = sum(1 for r in self._ratings.values() if r == 0)
        if n == 0:
            QMessageBox.information(
                self._tmp_parent, "Delete 0★ presets",
                "No presets are rated 0★.")
            return
        reply = QMessageBox.question(
            self._tmp_parent, "Delete 0★ presets",
            f"Permanently delete {n} preset file(s) rated 0★ from disk?\n"
            f"This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            n = self._renderer.delete_zero_rated()
            QMessageBox.information(
                self._tmp_parent, "Delete 0★ presets",
                f"Deleted {n} preset file(s) from disk.")

    def _confirm_reset_ratings(self):
        reply = QMessageBox.question(
            self._tmp_parent, "Reset all ratings",
            "Reset every preset's rating back to the default?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._renderer.reset_all_ratings()

    def _open_settings(self):
        dlg = SettingsDialog(self._cfg)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            updates = dlg.get_config()
            self._cfg.update(updates)
            self._renderer._save_config(self._cfg)
            self._renderer.apply_settings(updates)

    def _open_keymaps(self):
        dlg = KeymapDialog(self._keymaps)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_km = dlg.get_keymaps()
            self._keymaps.update(new_km)
            self._renderer._save_keymaps(new_km)
            self._renderer.update_keymaps(new_km)

    def popup_at_cursor(self):
        self.exec(QCursor.pos())
        self._tmp_parent.deleteLater()

    def __del__(self):
        try:
            self._tmp_parent.deleteLater()
        except Exception:
            pass


# ─── renderer ─────────────────────────────────────────────────────────────────

import ctypes as _ctypes
import random as _random
import time as _time
import subprocess as _subprocess

# PySDL2 reads PYSDL2_DLL_PATH once, at its own import, so this has to run
# first. Both variables are Linux-only: SDL_VIDEODRIVER=x11 is meaningless on
# Windows, and pointing PYSDL2_DLL_PATH at an ELF directory stops PySDL2 finding
# any SDL2 at all. renderer.py sets the same pair correctly before importing
# this module, so use setdefault and do not clobber what it chose.
if sys.platform.startswith("linux"):
    os.environ.setdefault("SDL_VIDEODRIVER", "x11")
    os.environ.setdefault("PYSDL2_DLL_PATH", "/usr/lib/x86_64-linux-gnu")
elif getattr(sys, "frozen", False):
    # Frozen build only: SDL2.dll sits beside the executable. NOT for a normal
    # install — the pysdl2-dll wheel points PySDL2 at its own bundled binaries,
    # but only while PYSDL2_DLL_PATH is still unset.
    os.environ.setdefault("PYSDL2_DLL_PATH", str(Path(sys.executable).parent))

import sdl2
import sdl2.ext
from sdl2 import (
    SDL_GL_CONTEXT_MAJOR_VERSION,
    SDL_GL_CONTEXT_MINOR_VERSION,
)
from OpenGL.GL import glViewport, glClear, GL_COLOR_BUFFER_BIT


# ─────────────────────────────────────────────────────────────────────────────
#  SDL2 key token → (sym, scancode) lookup
# ─────────────────────────────────────────────────────────────────────────────

def _build_key_lookup() -> dict:
    sym_map = {
        "ESCAPE":    sdl2.SDLK_ESCAPE,
        "SPACE":     sdl2.SDLK_SPACE,
        "RETURN":    sdl2.SDLK_RETURN,
        "TAB":       sdl2.SDLK_TAB,
        "BACKSPACE": sdl2.SDLK_BACKSPACE,
        "DELETE":    sdl2.SDLK_DELETE,
        "LEFT":      sdl2.SDLK_LEFT,
        "RIGHT":     sdl2.SDLK_RIGHT,
        "UP":        sdl2.SDLK_UP,
        "DOWN":      sdl2.SDLK_DOWN,
        "HOME":      sdl2.SDLK_HOME,
        "END":       sdl2.SDLK_END,
        "PAGEUP":    sdl2.SDLK_PAGEUP,
        "PAGEDOWN":  sdl2.SDLK_PAGEDOWN,
        "+":         sdl2.SDLK_PLUS,
        "=":         sdl2.SDLK_EQUALS,
        "-":         sdl2.SDLK_MINUS,
        "F1":  sdl2.SDLK_F1,  "F2":  sdl2.SDLK_F2,
        "F3":  sdl2.SDLK_F3,  "F4":  sdl2.SDLK_F4,
        "F5":  sdl2.SDLK_F5,  "F6":  sdl2.SDLK_F6,
        "F7":  sdl2.SDLK_F7,  "F8":  sdl2.SDLK_F8,
        "F9":  sdl2.SDLK_F9,  "F10": sdl2.SDLK_F10,
        "F11": sdl2.SDLK_F11, "F12": sdl2.SDLK_F12,
    }
    scancode_map = {
        "SCROLLLOCK": sdl2.SDL_SCANCODE_SCROLLLOCK,
    }
    result = {}
    for token, sym in sym_map.items():
        result[token.upper()] = (sym, None)
    for token, sc in scancode_map.items():
        result[token.upper()] = (None, sc)
    for c in "abcdefghijklmnopqrstuvwxyz0123456789`[]\\;',./":
        result[c] = (ord(c), None)
        result[c.upper()] = (ord(c), None)
    return result


_KEY_LOOKUP = _build_key_lookup()


def _token_matches(token: str, sym: int, scancode: int) -> bool:
    if not token or token.upper() == "DBLCLICK":
        return False
    entry = _KEY_LOOKUP.get(token.upper())
    if not entry:
        return False
    expected_sym, expected_sc = entry
    if expected_sc is not None:
        return scancode == expected_sc
    if token == "+":
        return sym in (sdl2.SDLK_PLUS, sdl2.SDLK_EQUALS, sdl2.SDLK_KP_PLUS)
    if token == "-":
        return sym in (sdl2.SDLK_MINUS, sdl2.SDLK_KP_MINUS)
    return sym == expected_sym



# ─────────────────────────────────────────────────────────────────────────────
#  Renderer
# ─────────────────────────────────────────────────────────────────────────────

class Renderer:
    _instance = None

    @classmethod
    def instance(cls):
        return cls._instance

    def __init__(self, cfg: dict, bridge, ratings: dict, keymaps: dict):
        Renderer._instance = self

        self._cfg      = cfg
        self._bridge   = bridge
        self._ratings  = ratings
        self._keymaps  = keymaps

        # Thread-safe request flags
        self._lock                       = threading.Lock()
        self._req_next                   = False
        self._req_prev                   = False
        self._req_random                 = False
        self._req_lock                   = False
        self._req_lock_v                 = False
        self._req_context_menu           = False
        self._req_borderless             = False
        self._req_settings_updates       = None
        self._req_geometry               = None   # (x, y, w, h) from IPC
        self._req_show_window            = False  # show after Qt adoption
        self._req_sort                   = None   # 'name' | 'rating'
        self._req_goto                   = None   # jump to playlist index
        self._req_toggle_mode            = False  # shuffle/sequential toggle
        self._req_reload                 = False  # rebuild playlist from disk
        self._rated                      = False  # favorite (rating-weighted) shuffle
        self._rated_history              = deque(maxlen=200)  # native indices for rated prev

        self._locked              = False
        self._borderless          = False
        self._borderless_on_start = False   # set by main.py --borderless flag
        self._shuffle             = True
        self._running             = False
        self._target_y            = None

        self._window  = None
        self._context = None
        self._pm      = None

        self._audio = AudioCapture(cfg.get("audio_device", ""))

        self._preset_start    = 0.0
        self._preset_duration = 0.0
        self._last_click      = 0.0
        self._last_mouse_move = 0.0
        self._cursor_hidden   = False
        self._next_duration()

        # Start IPC reader (reads JSON commands from stdin)
        try:
            self._ipc = IPCReader(self)
            self._ipc.start()
        except Exception:
            pass   # standalone mode — no ipc_reader, that's fine

    # ── Subclass hook: save config/keymaps (local paths differ per viz) ───

    def _save_config(self, cfg: dict):
        """Override in subclass to persist config to the right path."""
        pass

    def _save_keymaps(self, km: dict):
        """Override in subclass to persist keymaps to the right path."""
        pass

    def _save_ratings(self, ratings: dict):
        """Override in subclass to persist ratings to the right path."""
        pass

    # ── Duration ──────────────────────────────────────────────────────────

    def _next_duration(self):
        base  = self._cfg.get("preset_duration", 30)
        extra = self._cfg.get("preset_random_extra", 15)
        self._preset_duration = base + _random.uniform(0, extra)

    # ── SDL2 / GL init ────────────────────────────────────────────────────

    def _init_sdl(self) -> bool:
        if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO) < 0:
            print(f"[renderer] SDL_Init failed: {sdl2.SDL_GetError()}")
            return False

        sdl2.SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, 3)
        sdl2.SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 3)
        sdl2.SDL_GL_SetAttribute(sdl2.SDL_GL_DOUBLEBUFFER, 1)
        sdl2.SDL_GL_SetAttribute(sdl2.SDL_GL_DEPTH_SIZE, 24)

        w = self._cfg.get("width", 1280)
        h = self._cfg.get("height", 720)
        # window_x / window_y come from --geometry CLI arg via config overrides
        x = self._cfg.get("window_x", sdl2.SDL_WINDOWPOS_CENTERED)
        y = self._cfg.get("window_y", sdl2.SDL_WINDOWPOS_CENTERED)

        # Hint: ask the compositor not to manage our window position
        sdl2.SDL_SetHint(b"SDL_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR", b"1")
        sdl2.SDL_SetHint(b"SDL_VIDEODRIVER", b"x11")

        flags = (sdl2.SDL_WINDOW_OPENGL |
                 sdl2.SDL_WINDOW_SHOWN  |
                 sdl2.SDL_WINDOW_RESIZABLE)

        self._window = sdl2.SDL_CreateWindow(
            b"MilkDrop Visualizer",
            x, y, w, h, flags
        )
        if not self._window:
            print(f"[renderer] SDL_CreateWindow failed: {sdl2.SDL_GetError()}")
            return False

        self._context = sdl2.SDL_GL_CreateContext(self._window)
        if not self._context:
            print(f"[renderer] SDL_GL_CreateContext failed: {sdl2.SDL_GetError()}")
            return False

        # fps == 0 → uncapped: turn vsync off so we're not pinned to the
        # monitor refresh rate.
        _fps0 = self._cfg.get("fps", 60)
        sdl2.SDL_GL_SetSwapInterval(0 if (not _fps0 or _fps0 <= 0) else 1)

        # Embedded mode only: park off-screen until the Qt host adopts and
        # repositions the window. Standalone must stay on-screen and clickable
        # (nothing consumes _req_show_window here to bring it back).
        if self._cfg.get("embedded"):
            sdl2.SDL_SetWindowPosition(self._window, -10000, -10000)
        else:
            sdl2.SDL_RaiseWindow(self._window)

        # Print the X11 window ID to stdout so the host can read it
        # without relying on wmctrl (which can't see hidden/off-screen windows)
        try:
            wm_info = sdl2.SDL_SysWMinfo()
            sdl2.SDL_VERSION(wm_info.version)
            if sdl2.SDL_GetWindowWMInfo(self._window, _ctypes.byref(wm_info)):
                # The key stays XID= on every platform because the embedding host
                # parses that literal; only the handle behind it changes.
                if sys.platform == "win32":
                    handle = wm_info.info.win.window          # HWND
                else:
                    handle = wm_info.info.x11.window
                print(f"XID={handle}", flush=True)
        except Exception as e:
            print(f"[renderer] Could not get XID: {e}", flush=True)

        return True

    # ── projectM init ─────────────────────────────────────────────────────

    def _init_projectm(self) -> bool:
        try:
            self._pm = self._create_projectm()
        except RuntimeError as e:
            print(f"[renderer] Fatal: {e}")
            return False

        w   = self._cfg.get("width", 1280)
        h   = self._cfg.get("height", 720)
        fps = self._cfg.get("fps", 60)
        # projectM needs a real fps for its internal timers; 0 (uncapped) would
        # break them. Animation itself is wall-clock based, so a nominal value
        # is fine — the loop still runs uncapped.
        pm_fps = fps if fps and fps > 0 else 120

        try:
            self._pm.create(w, h, pm_fps)
        except RuntimeError as e:
            print(f"[renderer] projectm.create() failed: {e}")
            return False

        self._apply_pm_settings(self._cfg)

        preset_dir = self._cfg.get("preset_dir", "")

        # Texture search paths — texture-sampling presets render white
        # without these
        # The configured directory comes first and the rest are guesses kept for
        # installs that never set one. Nothing is bundled, so an empty setting
        # and no lucky guess means texture-sampling presets render white — which
        # is why the settings dialog asks for this up front.
        tex_paths = [p for p in (
            self._cfg.get("texture_dir", "").strip(),
            os.path.join(os.path.dirname(__file__), "textures"),
            os.path.join(preset_dir, "textures"),
            os.path.expanduser("~/projectm-textures"),
            preset_dir,
        ) if p and os.path.isdir(p)]
        if tex_paths and hasattr(self._pm, "set_texture_search_paths"):
            self._pm.set_texture_search_paths(tex_paths)

        if not os.path.isdir(preset_dir):
            print(f"[renderer] WARNING: preset dir not found: {preset_dir}")
        else:
            count = self._pm.load_preset_dir(preset_dir)
            print(f"[renderer] Loaded {count} presets from {preset_dir}")

        return True

    def _create_projectm(self) -> ProjectM4:
        """Factory hook — override to pass a different config_dir."""
        return load_projectm()

    def _apply_pm_settings(self, cfg: dict):
        quality = cfg.get("quality", "high")
        mx, my  = QUALITY_MESH.get(quality, (64, 48))
        self._pm.set_mesh_size(
            cfg.get("mesh_x", mx),
            cfg.get("mesh_y", my)
        )
        self._pm.set_beat_sensitivity(cfg.get("beat_sensitivity", 1.0))
        self._pm.set_hard_cut_enabled(cfg.get("hard_cuts_enabled", True))
        self._pm.set_hard_cut_sensitivity(cfg.get("hard_cut_sensitivity", 1.0))
        self._pm.set_soft_cut_duration(cfg.get("soft_cut_duration", 3.0))
        _fps = cfg.get("fps", 60)
        self._pm.set_fps(_fps if _fps and _fps > 0 else 120)

    # ── Public thread-safe requests ───────────────────────────────────────

    def request_next(self):
        with self._lock: self._req_next = True

    def request_prev(self):
        with self._lock: self._req_prev = True

    def request_random(self):
        with self._lock: self._req_random = True

    def request_toggle_lock(self):
        with self._lock:
            self._req_lock   = True
            self._req_lock_v = not self._locked

    def request_toggle_borderless(self):
        with self._lock:
            self._req_borderless = True

    def request_context_menu(self):
        with self._lock: self._req_context_menu = True

    def request_sort(self, by: str = "name"):
        """Reorder the playlist by preset name or rating (runs on render thread)."""
        with self._lock: self._req_sort = "rating" if by == "rating" else "name"

    def request_toggle_browser(self):
        """Show/hide the preset browser window (handled in the Qt thread)."""
        try:
            self._bridge.toggle_preset_browser.emit()
        except Exception:
            pass

    # ── Preset-browser host API (called from the Qt thread) ────────────────

    @property
    def preset_changed(self):
        """Bridge signal the browser connects to, to follow the active preset."""
        return self._bridge.preset_changed

    @property
    def playlist_changed(self):
        """Bridge signal fired when the playlist order/contents change."""
        return self._bridge.playlist_changed

    def sort_order(self, by: str = "name"):
        self.request_sort(by)

    def is_shuffle(self) -> bool:
        return self._shuffle

    def is_rated(self) -> bool:
        return self._rated

    def set_rated(self, on: bool):
        """Favorite mode: advance picks a rating-weighted random preset
        (rating 0 = never)."""
        with self._lock:
            self._rated = bool(on)

    def request_toggle_mode(self):
        """Toggle shuffle/sequential (runs on render thread)."""
        with self._lock: self._req_toggle_mode = True

    def toggle_shuffle(self):
        self.request_toggle_mode()

    def request_goto(self, index: int):
        """Jump to a playlist index (runs on render thread)."""
        with self._lock: self._req_goto = int(index)

    def jump_to_order(self, index: int):
        self.request_goto(index)

    def current_order_pos(self) -> int:
        try:
            return int(self._pm.playlist_position()) if self._pm else 0
        except Exception:
            return 0

    def get_preset_playlist(self):
        """[(name, rating), …] in current playlist order. Read-only; may race
        with a concurrent rebuild, so it is fully guarded."""
        out = []
        try:
            size = int(self._pm.playlist_size()) if self._pm else 0
            for i in range(size):
                name = self._pm.playlist_item(i)
                if not name:
                    continue
                out.append((name, get_preset_rating(self._ratings, name)))
        except Exception:
            pass
        return out

    def reset_all_ratings(self):
        """Clear every preset rating back to default. Safe from the Qt thread
        (touches only the ratings dict + file)."""
        self._ratings.clear()
        self._save_ratings(self._ratings)
        try:
            self._bridge.show_toast.emit("All ratings reset to default")
            self._emit_current_preset()
        except Exception:
            pass

    def delete_zero_rated(self) -> int:
        """Delete 0★-rated preset files from disk and request a playlist
        rebuild. Returns the number deleted. Safe from the Qt thread — file
        removal + ratings update happen here; only the projectM playlist
        rebuild is deferred to the render thread."""
        zero = {n for n, r in self._ratings.items() if r == 0}
        if not zero:
            return 0
        deleted = 0
        for path, stem in self._all_preset_files():
            if stem in zero:
                try:
                    os.remove(path)
                    deleted += 1
                except Exception as e:
                    print(f"[renderer] delete {path}: {e}", flush=True)
        for n in zero:
            self._ratings.pop(n, None)
        self._save_ratings(self._ratings)
        with self._lock:
            self._req_reload = True
        return deleted

    def is_locked(self) -> bool:
        return self._locked

    def apply_settings(self, updates: dict):
        with self._lock:
            self._req_settings_updates = updates

    def update_keymaps(self, new_km: dict):
        with self._lock:
            self._keymaps.update(new_km)

    # ── Key matching ──────────────────────────────────────────────────────

    def _km(self, action: str, sym: int, scancode: int) -> bool:
        token = self._keymaps.get(action, "")
        return _token_matches(token, sym, scancode)

    # ── Main render loop ──────────────────────────────────────────────────

    def run(self):
        if not self._init_sdl():
            return
        if not self._init_projectm():
            return

        self._audio.start()
        self._running      = True
        self._preset_start = _time.monotonic()

        # Apply borderless immediately if launched with --borderless
        if self._borderless_on_start:
            self._borderless = True
            sdl2.SDL_SetWindowBordered(self._window, sdl2.SDL_FALSE)

        event      = sdl2.SDL_Event()

        self._emit_current_preset()

        while self._running:

            # ── SDL events ────────────────────────────────────────────────
            while sdl2.SDL_PollEvent(_ctypes.byref(event)) != 0:
                if event.type == sdl2.SDL_QUIT:
                    self._running = False
                    break

                elif event.type == sdl2.SDL_KEYDOWN:
                    sym      = event.key.keysym.sym
                    scancode = event.key.keysym.scancode
                    self._handle_key(sym, scancode)

                elif event.type == sdl2.SDL_MOUSEMOTION:
                    self._last_mouse_move = _time.monotonic()
                    if self._cursor_hidden:
                        sdl2.SDL_ShowCursor(sdl2.SDL_ENABLE)
                        self._cursor_hidden = False

                elif event.type == sdl2.SDL_MOUSEBUTTONDOWN:
                    btn = event.button.button
                    if btn == sdl2.SDL_BUTTON_RIGHT:
                        # Forward to Qt host via stdout — host shows context menu
                        print("RCLICK", flush=True)
                        # Standalone: also open our own context menu locally.
                        self.request_context_menu()
                    elif btn == sdl2.SDL_BUTTON_LEFT:
                        token = self._keymaps.get("fullscreen_mouse", "DBLCLICK")
                        if token.upper() == "DBLCLICK":
                            now = _time.monotonic()
                            if now - self._last_click < 0.35:
                                self._toggle_fullscreen()
                                self._last_click = 0.0
                            else:
                                self._last_click = now

                elif event.type == sdl2.SDL_WINDOWEVENT:
                    if event.window.event == sdl2.SDL_WINDOWEVENT_RESIZED:
                        nw = event.window.data1
                        nh = event.window.data2
                        glViewport(0, 0, nw, nh)
                        self._pm.set_window_size(nw, nh)

            if not self._running:
                break

            # ── Dispatch thread-safe requests ─────────────────────────────
            with self._lock:
                if self._req_next:
                    self._req_next = False
                    self._do_next(hard=True)
                if self._req_prev:
                    self._req_prev = False
                    self._do_prev(hard=True)
                if self._req_random:
                    self._req_random = False
                    self._do_random()
                if self._req_lock:
                    self._req_lock = False
                    self._do_set_lock(self._req_lock_v)
                if self._req_context_menu:
                    self._req_context_menu = False
                    self._bridge.show_context_menu.emit()
                if self._req_borderless:
                    self._req_borderless = False
                    self._toggle_borderless()
                if self._req_settings_updates is not None:
                    updates = self._req_settings_updates
                    self._req_settings_updates = None
                    self._cfg.update(updates)
                    self._apply_pm_settings(updates)
                    if "fps" in updates:
                        _f = updates.get("fps", 60)
                        sdl2.SDL_GL_SetSwapInterval(
                            0 if (not _f or _f <= 0) else 1)
                    if "audio_device" in updates:
                        self._audio.update_device(updates["audio_device"])
                if self._req_geometry is not None:
                    gx, gy, gw, gh = self._req_geometry
                    self._req_geometry = None
                    sdl2.SDL_SetWindowSize(self._window, gw, gh)
                    glViewport(0, 0, gw, gh)
                    if self._pm:
                        self._pm.set_window_size(gw, gh)
                if self._req_sort is not None:
                    by = self._req_sort
                    self._req_sort = None
                    self._do_sort(by)
                if self._req_goto is not None:
                    idx = self._req_goto
                    self._req_goto = None
                    self._do_goto(idx)
                if self._req_toggle_mode:
                    self._req_toggle_mode = False
                    self._do_toggle_mode()
                if self._req_reload:
                    self._req_reload = False
                    self._do_reload_playlist()

            # ── Auto-advance ──────────────────────────────────────────────
            if not self._locked:
                elapsed = _time.monotonic() - self._preset_start
                if elapsed >= self._preset_duration:
                    self._do_next(hard=False)

            # ── Cursor auto-hide (fullscreen only, 3s idle) ───────────────
            flags = sdl2.SDL_GetWindowFlags(self._window) if self._window else 0
            if flags & sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP:
                idle = _time.monotonic() - self._last_mouse_move
                if idle > 3.0 and not self._cursor_hidden:
                    sdl2.SDL_ShowCursor(sdl2.SDL_DISABLE)
                    self._cursor_hidden = True
            elif self._cursor_hidden:
                sdl2.SDL_ShowCursor(sdl2.SDL_ENABLE)
                self._cursor_hidden = False

            # ── Audio ─────────────────────────────────────────────────────
            chunk = self._audio.get_chunk()
            if chunk is not None:
                flat = chunk.flatten().tolist()
                self._pm.add_pcm_float(flat, len(flat) // 2, 2)

            # ── Render ────────────────────────────────────────────────────
            t0 = _time.monotonic()
            self._pm.render_frame()
            sdl2.SDL_GL_SwapWindow(self._window)

            elapsed_frame = _time.monotonic() - t0
            # Read fps live so Settings changes take effect immediately.
            # fps <= 0 → uncapped (no sleep).
            _fps = self._cfg.get("fps", 60)
            frame_time = 1.0 / _fps if _fps and _fps > 0 else 0.0
            sleep_t = frame_time - elapsed_frame
            if sleep_t > 0.002:
                _time.sleep(sleep_t)

        # ── Cleanup ───────────────────────────────────────────────────────
        self._audio.stop()
        if self._pm:
            self._pm.destroy()
        if self._context:
            sdl2.SDL_GL_DeleteContext(self._context)
        if self._window:
            sdl2.SDL_DestroyWindow(self._window)
        sdl2.SDL_Quit()
        self._bridge.request_quit.emit()

    # ── Key handler ───────────────────────────────────────────────────────

    def _handle_key(self, sym: int, scancode: int):
        km = self._km
        if km("quit", sym, scancode):
            self._running = False
        elif km("next", sym, scancode):
            self._do_next(hard=True)
        elif km("prev", sym, scancode):
            self._do_prev(hard=True)
        elif km("hard_cut_next", sym, scancode):
            self._do_next(hard=True)
        elif km("random", sym, scancode):
            self._do_random()
        elif km("lock", sym, scancode):
            self._toggle_lock()
        elif km("toggle_hard_cuts", sym, scancode):
            self._do_toggle_hard_cuts()
        elif km("toggle_mode", sym, scancode):
            self._do_toggle_mode()
        elif km("fullscreen", sym, scancode) or km("fullscreen_alt", sym, scancode):
            self._toggle_fullscreen()
        elif km("rate_up", sym, scancode):
            self._rate_current(+1)
        elif km("rate_down", sym, scancode):
            self._rate_current(-1)

    # ── Preset navigation ─────────────────────────────────────────────────

    def _do_next(self, hard: bool):
        if self._locked or not self._pm:
            return
        try:
            if self._rated:
                idx = self._pick_rated_index()
                if idx is not None:
                    try:
                        self._rated_history.append(int(self._pm.playlist_position()))
                    except Exception:
                        pass
                    self._pm.set_playlist_position(idx, hard_cut=hard)
                else:
                    self._pm.next_preset(hard_cut=hard)
            else:
                self._pm.next_preset(hard_cut=hard)
            self._next_duration()
            self._preset_start = _time.monotonic()
            self._emit_current_preset()
        except Exception as e:
            print(f"[renderer] next_preset failed: {e}", flush=True)

    def _do_prev(self, hard: bool):
        if self._locked or not self._pm:
            return
        try:
            if self._rated:
                if not self._rated_history:
                    return   # nothing played yet in this rated session
                self._pm.set_playlist_position(self._rated_history.pop(), hard_cut=hard)
            else:
                self._pm.prev_preset(hard_cut=hard)
            self._next_duration()
            self._preset_start = _time.monotonic()
            self._emit_current_preset()
        except Exception as e:
            print(f"[renderer] prev_preset failed: {e}", flush=True)

    def _do_random(self):
        if self._locked or not self._pm:
            return
        try:
            size = self._pm.playlist_size()
            if size == 0:
                return
            if self._pm._pl and self._pm._playlist:
                for _ in range(20):
                    idx = _random.randint(0, size - 1)
                    name = ""
                    try:
                        raw = self._pm._playlist.projectm_playlist_item(
                            self._pm._pl, idx
                        )
                        if raw:
                            from pathlib import Path
                            name = Path(raw.decode(errors="replace")).stem
                    except Exception:
                        pass
                    if not is_excluded(self._ratings, name):
                        break
                self._pm._playlist.projectm_playlist_set_position(
                    self._pm._pl, idx, True
                )
            else:
                candidates = [
                    i for i, p in enumerate(self._pm._preset_files)
                    if not is_excluded(self._ratings,
                                       __import__("pathlib").Path(p).stem)
                ]
                if not candidates:
                    candidates = list(range(len(self._pm._preset_files)))
                idx = _random.choice(candidates)
                self._pm._preset_index = idx
                self._pm._load_preset_direct(idx)
            self._next_duration()
            self._preset_start = _time.monotonic()
            self._emit_current_preset()
        except Exception as e:
            print(f"[renderer] random_preset failed: {e}", flush=True)

    # ── Preset management (sort / delete-zero / reset ratings) ─────────────

    def _all_preset_files(self):
        """[(full_path, stem), …] for every .milk under the preset dir."""
        import glob
        from pathlib import Path
        preset_dir = self._cfg.get("preset_dir", "")
        if not preset_dir:
            return []
        files = glob.glob(os.path.join(preset_dir, "**", "*.milk"), recursive=True)
        return [(f, Path(f).stem) for f in files]

    def _rebuild_playlist(self, paths):
        """Clear the playlist and re-add `paths` in the given order (shuffle off,
        so native next/prev walk exactly this order)."""
        pm = self._pm
        pm.clear_playlist()
        added = pm.add_presets(paths)
        if added == 0 and paths:
            # No batch add_presets in this projectM build — fall back to the
            # direct-load path so navigation still works.
            pm._preset_files = [str(p) for p in paths]
            pm._preset_index = 0
            pm._load_preset_direct(0)
        pm.set_shuffle(False)
        return added

    def _seek_to_stem(self, stem: str):
        """Jump the playlist to the item whose name == stem (best effort)."""
        if not stem:
            return
        try:
            for i in range(int(self._pm.playlist_size())):
                if self._pm.playlist_item(i) == stem:
                    self._pm.set_playlist_position(i, hard_cut=True)
                    return
        except Exception:
            pass

    def _do_sort(self, by: str):
        if not self._pm:
            return
        try:
            files = self._all_preset_files()
            if not files:
                self._bridge.status_message.emit("Sort: no presets found")
                return
            if by == "rating":
                files.sort(key=lambda ps: (
                    -get_preset_rating(self._ratings, ps[1]), ps[1].lower()))
            else:
                files.sort(key=lambda ps: ps[1].lower())
            cur = self._pm.current_preset_name()
            self._rebuild_playlist([p for p, _ in files])
            self._shuffle = False
            self._rated_history.clear()   # native indices changed
            self._seek_to_stem(cur)
            label = "rating" if by == "rating" else "name"
            print(f"[renderer] Sorted {len(files)} presets by {label}")
            self._bridge.status_message.emit(f"Sorted by {label}")
            self._bridge.playlist_changed.emit()
            self._emit_current_preset()
        except Exception as e:
            print(f"[renderer] sort failed: {e}", flush=True)

    def _do_goto(self, index: int):
        """Jump to a playlist index (from the browser double-click)."""
        if self._locked or not self._pm:
            return
        try:
            self._pm.set_playlist_position(index, hard_cut=True)
            self._next_duration()
            self._preset_start = _time.monotonic()
            self._emit_current_preset()
        except Exception as e:
            print(f"[renderer] goto failed: {e}", flush=True)

    def _do_reload_playlist(self):
        """Rebuild the playlist from whatever .milk files remain on disk (used
        after delete-zero removes files)."""
        if not self._pm:
            return
        try:
            cur = self._pm.current_preset_name()
            paths = [p for p, _ in self._all_preset_files()]
            self._rebuild_playlist(paths)
            self._rated_history.clear()   # native indices changed
            self._seek_to_stem(cur)
            self._bridge.status_message.emit(f"Playlist: {len(paths)} presets")
            self._bridge.playlist_changed.emit()
            self._emit_current_preset()
        except Exception as e:
            print(f"[renderer] reload_playlist failed: {e}", flush=True)

    def _pick_rated_index(self):
        """Rating-weighted random native index (rating 0 excluded). None if
        nothing is rated above 0."""
        if not self._pm:
            return None
        idxs, weights = [], []
        try:
            for i in range(int(self._pm.playlist_size())):
                r = get_preset_rating(self._ratings, self._pm.playlist_item(i))
                if r > 0:
                    idxs.append(i)
                    weights.append(r)
        except Exception:
            return None
        if not idxs:
            return None
        return _random.choices(idxs, weights=weights, k=1)[0]

    # ── Lock ──────────────────────────────────────────────────────────────

    def _toggle_lock(self):
        self._do_set_lock(not self._locked)

    def _do_set_lock(self, locked: bool):
        self._locked = locked
        self._pm.set_locked(locked)
        self._bridge.lock_changed.emit(locked)
        print(f"[renderer] Preset {'locked' if locked else 'unlocked'}")

    # ── Hard cuts toggle ──────────────────────────────────────────────────

    def _do_toggle_hard_cuts(self):
        new_val = not self._cfg.get("hard_cuts_enabled", True)
        self._cfg["hard_cuts_enabled"] = new_val
        self._pm.set_hard_cut_enabled(new_val)
        self._save_config(self._cfg)
        label = "ON" if new_val else "OFF"
        print(f"[renderer] Hard cuts: {label}")
        self._bridge.status_message.emit(f"Hard cuts: {label}")

    def _do_toggle_mode(self):
        self._shuffle = not self._shuffle
        if self._pm._pl and self._pm._playlist:
            self._pm._playlist.projectm_playlist_set_shuffle(
                self._pm._pl, self._shuffle
            )
        label = "Random" if self._shuffle else "Sequential"
        print(f"[renderer] Mode: {label}")
        self._bridge.status_message.emit(f"Mode: {label}")
        self._bridge.playlist_changed.emit()

    # ── Fullscreen ────────────────────────────────────────────────────────

    def _toggle_fullscreen(self):
        flags = sdl2.SDL_GetWindowFlags(self._window)
        if flags & sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP:
            sdl2.SDL_SetWindowFullscreen(self._window, 0)
        else:
            sdl2.SDL_SetWindowFullscreen(
                self._window, sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP
            )

    def _toggle_borderless(self):
        self._borderless = not self._borderless
        sdl2.SDL_SetWindowBordered(
            self._window,
            sdl2.SDL_FALSE if self._borderless else sdl2.SDL_TRUE
        )
        label = "ON" if self._borderless else "OFF"
        print(f"[renderer] Borderless: {label}")
        self._bridge.status_message.emit(f"Borderless: {label}")
        self._bridge.borderless_changed.emit(self._borderless)

    def is_borderless(self) -> bool:
        return self._borderless

    # ── Rating ────────────────────────────────────────────────────────────

    def _rate_current(self, delta: int):
        name    = self._pm.current_preset_name()
        current = get_preset_rating(self._ratings, name)
        new_r   = set_preset_rating(self._ratings, name, current + delta)
        self._save_ratings(self._ratings)
        stars   = "★" * new_r + "☆" * (10 - new_r)
        suffix  = "  [excluded from random]" if new_r == 0 else ""
        toast   = f"{name}\n{stars}  {new_r}/10{suffix}"
        print(f"[renderer] Rated '{name}': {new_r}/10{suffix}")
        self._bridge.preset_changed.emit(name, new_r)
        self._bridge.show_toast.emit(toast)

    # ── Emit helpers ──────────────────────────────────────────────────────

    def _emit_current_preset(self):
        name     = self._pm.current_preset_name()
        rating   = get_preset_rating(self._ratings, name)
        excluded = is_excluded(self._ratings, name)
        tag      = " [excluded]" if excluded else ""
        print(f"[renderer] Now playing: {name}  ★{rating}/10{tag}")
        self._bridge.preset_changed.emit(name, rating)
