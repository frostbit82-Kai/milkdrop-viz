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
import sys
import json
import time
import logging
import threading
import subprocess
import re
import ast
from collections import deque
import numpy as np

# The Windows console encodes as cp1252, and this module prints stars, arrows
# and em dashes throughout. An encoding error there is never worth a crash: one
# already killed the audio backend and another killed the render thread mid
# preset. Change only the error handler, not the encoding, so the console keeps
# its native code page and the literal keys hosts parse (XID=) are untouched.
# Line buffering is separate and matters everywhere: a frozen build's stdout is
# block-buffered whenever it is redirected, PYTHONUNBUFFERED does not reliably
# reach it, and a crash then takes the whole buffer with it -- so the log stops
# at the last flushed line and points at the wrong place entirely.
for _stream in (sys.stdout, sys.stderr):
    try:
        if sys.platform == "win32":
            _stream.reconfigure(errors="replace", line_buffering=True)
        else:
            _stream.reconfigure(line_buffering=True)
    except Exception:
        pass           # not a TextIOWrapper (redirected, or replaced by a host)

logger_audio = logging.getLogger("visualizer.audio")


def _log(msg: str):
    """Print to stdout and to the embedded player's log.

    The Windows console is cp1252 by default, so the arrows and em dashes used
    throughout these messages raise UnicodeEncodeError there. A log line must
    never be able to take a capture backend down with it, so unprintable
    characters degrade instead of raising.
    """
    try:
        print(msg)         # standalone SDL viz (no logging config)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(msg.encode(enc, "replace").decode(enc, "replace"))
    try:
        logger_audio.info(msg)  # embedded player (bomsai_player.log)
    except Exception:
        pass


# OSError as well as ImportError: sounddevice is a ctypes binding, so a host
# with the package and without the PortAudio shared library imports fine and
# then raises `OSError: PortAudio library not found` at module scope. That is
# the normal state of a clean Linux box -- the frozen bundle carries
# libportaudio.so.2, but sounddevice looks for it with
# `ctypes.util.find_library`, which searches the *system*, never the bundle.
# Catching only ImportError takes the whole app down over a capture backend
# that Linux does not even lead with; parec does.
try:
    import sounddevice as sd
    _SD_AVAILABLE = True
except (ImportError, OSError) as _sd_why:
    _SD_AVAILABLE = False
    print(f"[audio] sounddevice unavailable ({_sd_why}) — "
          f"the PortAudio capture backend is off")


class _AudioBackend:
    """One way of pulling stereo float32 audio off the system.

    Which capture mechanisms exist is a platform question, so the capture layer
    is split into backends instead of branching inside AudioCapture. A backend
    turns whatever the OS offers into (frames, 2) float32 arrays and hands them
    to ``emit``; AudioCapture owns the ring buffer and the public API.
    """

    #: Short identifier used in log lines.
    name = "base"

    #: Sample rate the open stream is actually running at. Only the clip
    #: recorder reads it, and it has to be the real one: a WAV written at the
    #: wrong rate is silently the wrong length, which is a sync bug rather
    #: than an audible one. 44100 is the value every backend falls back to.
    rate = 44100

    def __init__(self, device_name: str, emit):
        self._device_name = device_name or ""
        self._emit = emit
        #: What start() actually opened. With an empty device name the backend
        #: picks the system default, so the caller cannot know the endpoint up
        #: front -- and following the default means noticing when it changes.
        self.active_name = ""

    @classmethod
    def available(cls) -> bool:
        """Cheap check: could this backend work on this machine at all?"""
        return False

    @staticmethod
    def list_devices() -> list:
        """Device names to offer in the settings dialog."""
        return []

    @staticmethod
    def default_device_name() -> str:
        """Identifier of the system's current default capture target.

        Returns '' when the backend cannot tell, which disables following
        rather than causing a restart loop on an unknown value. May be slow
        (subprocess or device enumeration), so callers must not run it on the
        render thread.
        """
        return ""

    def start(self) -> bool:
        return False

    def stop(self):
        pass

    def is_alive(self) -> bool:
        return False


def _to_stereo(arr: "np.ndarray", channels: int) -> "np.ndarray":
    """Normalise an interleaved capture buffer to (frames, 2) float32.

    Mono is duplicated; surround layouts are truncated to the front pair,
    which is what the visualiser reacts to and avoids guessing at a downmix.
    """
    if channels <= 1:
        return np.repeat(arr.reshape(-1, 1), 2, axis=1)
    frames = arr.size // channels
    arr = arr[:frames * channels].reshape(frames, channels)
    return arr[:, :2]


def pulse_sources() -> list:
    """Capturable PulseAudio/PipeWire sources: [{'name', 'label', 'kind'}].

    Ported from Bomsai Player. `pactl` is the only thing that knows the source
    *names* parec accepts -- sounddevice reports ALSA device names, which parec
    silently fails to match and then falls back to the default monitor, so a
    picker fed from sounddevice looks like it works and ignores the choice.
    """
    try:
        out = subprocess.run(
            ["pactl", "-f", "json", "list", "sources"],
            capture_output=True, text=True, timeout=3,
        )
        res = []
        for src in json.loads(out.stdout):
            name = src.get("name", "")
            if not name:
                continue
            low = name.lower()
            kind = ("monitor" if low.endswith(".monitor")
                    else "bluetooth" if "bluez" in low or "bluetooth" in low
                    else "input")
            res.append({"name": name,
                        "label": src.get("description") or name,
                        "kind": kind})
        # Monitors first: this app listens to what is playing far more often
        # than to a microphone.
        return ([s for s in res if s["kind"] == "monitor"]
                + [s for s in res if s["kind"] != "monitor"])
    except Exception:
        return []


class _ParecBackend(_AudioBackend):
    """Linux/PulseAudio, preferred: connects straight to the named source,
    so there are no ALSA plugins in the way and no stream re-routing needed."""

    name = "parec"

    def __init__(self, device_name: str, emit):
        super().__init__(device_name, emit)
        self._proc = None
        self._reader = None
        self._stop_flag = False

    @classmethod
    def available(cls) -> bool:
        import shutil
        return (sys.platform.startswith("linux")
                and shutil.which("parec") is not None)

    @staticmethod
    def list_devices() -> list:
        return [s["name"] for s in pulse_sources()]

    @staticmethod
    def default_device_name() -> str:
        """Monitor source of the current default sink.

        PulseAudio has no 'default source' worth following -- that is usually a
        microphone. What the visualiser wants is whatever the machine is
        *playing*, which is the default sink's monitor.
        """
        try:
            import subprocess
            sink = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
            return f"{sink}.monitor" if sink else ""
        except Exception:
            return ""

    def _resolve_source(self) -> str:
        """The source to actually record from.

        parec does not validate --device: handed a name no source answers to, it
        connects to PulseAudio's DEFAULT SOURCE instead, which is the microphone.
        Audio then flows, is_alive() is true and nothing looks wrong, while the
        visualiser reacts to the room. So the name is matched against the real
        source list here and anything that does not match falls back to the
        default sink's monitor — the same shape as the WASAPI backend's
        _pick_device, which is why Windows never hit this.
        """
        want = (self._device_name or "").strip()
        if want and want != AudioCapture.AUTO_LABEL:
            try:
                import subprocess
                out = subprocess.run(
                    ["pactl", "list", "sources", "short"],
                    capture_output=True, text=True, timeout=2).stdout
                names = [ln.split("\t")[1] for ln in out.splitlines()
                         if len(ln.split("\t")) > 1]
            except Exception:
                names = []
            if not names or want in names:
                return want
            _log(f"[audio] Source '{want}' not found — falling back to the "
                 f"default output's monitor")
        return self.default_device_name()

    def start(self) -> bool:
        if not self.available():
            return False
        device = self._resolve_source()
        if not device:
            return False
        self._device_name = device
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
        nbytes = AudioCapture.CHUNK * 2 * 4   # frames × channels × float32

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
                self._emit(arr.reshape(-1, 2))
            if not self._stop_flag:
                _log("[audio] parec stream ended (source gone?)")

        self._reader = threading.Thread(target=reader, daemon=True)
        self._reader.start()
        self.active_name = self._device_name
        _log(f"[audio] parec capture started ← {self._device_name}")
        return True

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

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None


class _SoundDeviceBackend(_AudioBackend):
    """PortAudio via sounddevice. On Linux this is the fallback when parec is
    missing, and it needs pactl to re-route onto the requested source. On
    Windows it captures a real input endpoint (a microphone or line-in) — it
    cannot do loopback, which is what _WasapiLoopbackBackend is for."""

    name = "sounddevice"

    def __init__(self, device_name: str, emit):
        super().__init__(device_name, emit)
        self._stream = None

    @classmethod
    def available(cls) -> bool:
        return _SD_AVAILABLE

    @staticmethod
    def list_devices() -> list:
        if not _SD_AVAILABLE:
            return []
        try:
            return [d["name"] for d in sd.query_devices()
                    if d["max_input_channels"] > 0]
        except Exception:
            return []

    @staticmethod
    def default_device_name() -> str:
        """On Linux this backend re-routes onto a PulseAudio source, so the
        thing worth following is the same default-sink monitor parec uses. On
        Windows it captures a real input (microphone, line-in), where "follow
        the output" has no meaning -- that is _WasapiLoopbackBackend's job.
        """
        if sys.platform.startswith("linux"):
            return _ParecBackend.default_device_name()
        return ""

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

    def start(self) -> bool:
        if not _SD_AVAILABLE:
            return False

        # PULSE_SOURCE is a PulseAudio variable; setting it anywhere else is
        # at best inert and at worst confusing, so it stays gated on Linux.
        is_linux = sys.platform.startswith("linux")
        device = None
        if is_linux:
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
            self._emit(data.copy())

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
                    blocksize=AudioCapture.CHUNK,
                    dtype="float32",
                    callback=callback,
                )
                self._stream.start()
                # Not `samplerate`: that is None for the device-default rows.
                self.rate = int(getattr(self._stream, "samplerate", 0) or 44100)
                if is_linux:
                    self._move_to_source()
                try:
                    self.active_name = sd.query_devices(device)["name"]
                except Exception:
                    self.active_name = self._device_name
                return True
            except Exception as e:
                last_err = e
                self._stream = None
        _log(f"[audio] Could not open audio stream: {last_err}")
        return False

    def stop(self):
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def is_alive(self) -> bool:
        return self._stream is not None


class _WasapiLoopbackBackend(_AudioBackend):
    """Windows: WASAPI loopback, which captures what a device is *playing*.

    The device list means the opposite of the Linux one. PulseAudio exposes a
    monitor *source* per sink and you record from it; WASAPI has no such
    object, so loopback attaches to a **render** endpoint (speakers,
    headphones) and returns its output stream. The settings dialog therefore
    lists outputs on Windows and inputs on Linux — see AudioCapture.device_label.
    """

    name = "wasapi-loopback"

    def __init__(self, device_name: str, emit):
        super().__init__(device_name, emit)
        self._pa = None
        self._stream = None
        self._channels = 2

    #: The one PortAudio instance this process gets. PyAudio.terminate() calls
    #: Pa_Terminate, which tears the WASAPI host API down under any stream
    #: still open on it -- and enumerating devices used to build a second
    #: instance and terminate it. Opening Settings while capture ran therefore
    #: killed the app outright: an access violation inside _portaudiowpatch,
    #: no Python traceback, on the one dialog a new install has to use. One
    #: shared instance, never terminated, removes the race; process exit
    #: reclaims it.
    _shared_pa = None
    _shared_pa_lock = threading.Lock()

    @classmethod
    def _pyaudio(cls):
        with cls._shared_pa_lock:
            if cls._shared_pa is None:
                import pyaudiowpatch as pyaudio
                cls._shared_pa = pyaudio.PyAudio()
            return cls._shared_pa

    @classmethod
    def available(cls) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import pyaudiowpatch          # noqa: F401
            return True
        except Exception:
            return False

    @staticmethod
    def _loopback_devices(pa) -> list:
        try:
            return list(pa.get_loopback_device_info_generator())
        except Exception:
            return []

    @staticmethod
    def list_devices() -> list:
        if not _WasapiLoopbackBackend.available():
            return []
        try:
            pa = _WasapiLoopbackBackend._pyaudio()
            return [d["name"]
                    for d in _WasapiLoopbackBackend._loopback_devices(pa)]
        except Exception as e:
            _log(f"[audio] Could not enumerate WASAPI loopback devices: {e}")
            return []

    @staticmethod
    def _default_loopback(pa, devices):
        """The loopback twin of the default render endpoint.

        WASAPI reports the default *output*; its loopback counterpart is a
        separate device whose name embeds the output's, so the two are matched
        by substring rather than by index.
        """
        try:
            import pyaudiowpatch as pyaudio
            api = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_out = pa.get_device_info_by_index(api["defaultOutputDevice"])
            for d in devices:
                if default_out["name"] in d["name"]:
                    return d
        except Exception:
            pass
        return devices[0] if devices else None

    @staticmethod
    def default_device_name() -> str:
        """Name of the loopback endpoint that auto mode would pick right now.

        Opens its own PyAudio instance: the watchdog has to answer this while a
        stream is already running on another one, and it must reflect the live
        system default rather than what was cached at start().
        """
        if not _WasapiLoopbackBackend.available():
            return ""
        try:
            pa = _WasapiLoopbackBackend._pyaudio()
            devices = _WasapiLoopbackBackend._loopback_devices(pa)
            dev = _WasapiLoopbackBackend._default_loopback(pa, devices)
            return dev["name"] if dev else ""
        except Exception:
            return ""

    def _pick_device(self, pa):
        """The configured loopback endpoint, else the default output's."""
        devices = self._loopback_devices(pa)
        if not devices:
            return None

        want = self._device_name.lower()
        if want:
            for d in devices:
                if want in d["name"].lower():
                    return d
            _log(f"[audio] Loopback device '{self._device_name}' not found — "
                 f"falling back to the default output")

        return self._default_loopback(pa, devices)

    def start(self) -> bool:
        if not self.available():
            return False
        import pyaudiowpatch as pyaudio

        try:
            self._pa = self._pyaudio()
        except Exception as e:
            _log(f"[audio] Could not initialise WASAPI: {e}")
            self._pa = None
            return False

        dev = self._pick_device(self._pa)
        if dev is None:
            _log("[audio] No WASAPI loopback endpoint found. Windows exposes "
                 "one per output device; is anything playing back at all?")
            self.stop()
            return False

        self._channels = max(1, int(dev.get("maxInputChannels") or 2))
        rate = int(dev.get("defaultSampleRate") or 44100)

        def callback(in_data, frame_count, time_info, status):
            if in_data:
                arr = np.frombuffer(in_data, dtype=np.float32)
                self._emit(_to_stereo(arr, self._channels).copy())
            return (None, pyaudio.paContinue)

        try:
            self._stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=self._channels,
                rate=rate,
                frames_per_buffer=AudioCapture.CHUNK,
                input=True,
                input_device_index=dev["index"],
                stream_callback=callback,
            )
            self._stream.start_stream()
            self.rate = rate
        except Exception as e:
            _log(f"[audio] Could not open WASAPI loopback stream: {e}")
            self.stop()
            return False

        self.active_name = dev["name"]
        _log(f"[audio] WASAPI loopback started ← {dev['name']} "
             f"({self._channels}ch @ {rate}Hz)")
        return True

    def stop(self):
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        # Deliberately not terminated -- see _shared_pa. Closing the stream is
        # what releases the endpoint; the instance is reused by the next
        # start() and by every device enumeration.
        self._pa = None

    def is_alive(self) -> bool:
        if self._stream is None:
            return False
        try:
            return bool(self._stream.is_active())
        except Exception:
            return False


def _backend_chain() -> list:
    """Backends to try, best first, for this platform."""
    if sys.platform == "win32":
        return [_WasapiLoopbackBackend, _SoundDeviceBackend]
    return [_ParecBackend, _SoundDeviceBackend]


class AudioCapture:
    CHUNK = 512

    #: main.py's --no-audio sets the device name to this. Nothing used to check
    #: for it, so the flag documented as "skip audio capture" opened a stream
    #: anyway -- on Windows it failed to match a loopback endpoint and fell back
    #: to the default output, capturing exactly what it was told not to.
    DISABLED = "__disabled__"

    #: Seconds between follow/reconnect checks. Matches the player's audio
    #: source watchdog; short enough that switching outputs feels immediate,
    #: long enough that device enumeration is not a constant cost.
    FOLLOW_POLL_S = 4.0

    def __init__(self, device_name: str):
        self._device_name = device_name
        #: Input sensitivity, applied in get_chunk(). 1.0 is untouched PCM.
        self._gain = 1.0
        self._buffer: deque = deque(maxlen=8)  # Keep last 8 audio chunks
        self._lock = threading.Lock()          # guards _buffer only
        self._backend = None
        #: Guards backend lifecycle. Separate from _lock because the watchdog
        #: tears a backend down while the render thread is still draining the
        #: ring buffer, and one lock covering both would make them wait on
        #: each other for no reason.
        self._life = threading.RLock()
        self._follow_thread = None
        self._follow_stop = None
        #: Set once a reopen attempt has failed, so a genuinely absent device
        #: logs one line rather than one every FOLLOW_POLL_S forever.
        self._reopen_failed = False
        #: Last default we reopened towards. Detecting the default and picking
        #: the endpoint are two separate queries, so they can disagree -- an
        #: endpoint that reports as default but refuses to open would otherwise
        #: be retried every tick forever. One attempt per distinct target.
        self._follow_target = ""
        #: Called with every captured chunk, or None. The clip recorder sets
        #: it. It has to be here rather than in the render loop because the
        #: render loop pops ONE chunk per frame while the backend pushes about
        #: 86 a second -- at 60fps a third of the audio never reaches it, and
        #: a recording made from what is left is silently short.
        self._tap = None
        #: Peak of the last chunk, after gain. Read by the level meter.
        self._level = 0.0

    # ── device discovery, for the settings dialog ───────────────────────
    @staticmethod
    def list_devices() -> list:
        """Capture device names for this platform, best backend first."""
        for cls in _backend_chain():
            if not cls.available():
                continue
            names = [n for n in cls.list_devices()]
            # First backend that knows any devices wins outright. Merging the
            # lists would mix PulseAudio source names with sounddevice's ALSA
            # ones, and the ALSA spellings are exactly what parec cannot match.
            if names:
                return names
        return []

    @staticmethod
    def device_labels() -> dict:
        """{device name: human description} for the pickers. Missing entries
        just show the raw name, which is what Windows endpoints already are."""
        return {s["name"]: s["label"] for s in pulse_sources()}

    #: Shown in the settings dialog for the empty device name. Stored as "",
    #: so existing config.json files keep working untouched.
    AUTO_LABEL = "Auto (follow system default)"

    @staticmethod
    def device_label() -> str:
        """Settings-dialog caption. Windows loopback listens to an *output*,
        which is the opposite of the Linux monitor-source model, so the
        wording has to differ or the list reads as nonsense."""
        if sys.platform == "win32":
            return "Output to capture:"
        return "Audio device:"

    # ── lifecycle ──────────────────────────────────────────────────────
    def _emit(self, chunk: "np.ndarray"):
        with self._lock:
            self._buffer.append(chunk)
        # Outside the buffer lock on purpose: the tap writes to a file and the
        # render thread takes this lock every frame to pop a chunk.
        try:
            peak = float(np.abs(chunk).max()) if chunk.size else 0.0
        except Exception:
            peak = 0.0
        # Post-gain, because that is the signal projectM sees -- a meter
        # showing the raw input would read empty on exactly the quiet source
        # the sensitivity slider exists for. tanh, matching get_chunk().
        self._level = float(np.tanh(peak * self._gain)) if self._gain != 1.0 \
            else peak
        tap = self._tap
        if tap is not None:
            try:
                tap(chunk)
            except Exception:
                pass          # a failing recorder must not stop capture

    def set_tap(self, fn):
        """Receive every captured chunk, or None to stop. Backend thread."""
        self._tap = fn

    def level(self) -> float:
        """Peak of the most recent chunk, 0.0-1.0ish, after gain."""
        return self._level

    def rate(self) -> int:
        """Sample rate of the open stream, or the usual default."""
        b = self._backend
        return int(getattr(b, "rate", 44100) or 44100) if b else 44100

    def is_following(self) -> bool:
        """True when no device is pinned, so capture tracks the system default.

        An empty device name has always meant "let the backend choose"; that
        choice is now re-made whenever the default moves.
        """
        return self._device_name not in (self.DISABLED,) and not self._device_name

    def _open_backend(self, quiet: bool = False) -> bool:
        """Try each backend in order. Caller must hold _life."""
        for cls in _backend_chain():
            if not cls.available():
                continue
            backend = cls(self._device_name, self._emit)
            try:
                if backend.start():
                    self._backend = backend
                    self._reopen_failed = False
                    return True
            except Exception as e:
                _log(f"[audio] {cls.name} backend failed: {e}")
            backend.stop()
        self._backend = None
        if not quiet:
            _log("[audio] No capture backend available — "
                 "visualization will run without audio reaction.")
        return False

    def start(self):
        if self._device_name == self.DISABLED:
            _log("[audio] Capture disabled (--no-audio)")
            self._backend = None
            return
        with self._life:
            self._open_backend()
        self._start_follow()

    def stop(self):
        self._stop_follow()
        with self._life:
            if self._backend:
                try:
                    self._backend.stop()
                except Exception:
                    pass
                self._backend = None

    def is_alive(self) -> bool:
        """True if a capture backend is currently delivering (or able to)."""
        with self._life:
            return self._backend is not None and self._backend.is_alive()

    def active_device(self) -> str:
        """The endpoint actually being captured, which in follow mode is not
        the configured one. Empty when capture is not running."""
        with self._life:
            return self._backend.active_name if self._backend else ""

    # ── follow / reconnect watchdog ─────────────────────────────────────
    def _start_follow(self):
        if self._device_name == self.DISABLED or self._follow_thread:
            return
        self._follow_stop = threading.Event()
        self._follow_thread = threading.Thread(
            target=self._follow_loop, args=(self._follow_stop,),
            daemon=True, name="audio-follow")
        self._follow_thread.start()

    def _stop_follow(self):
        evt = self._follow_stop
        self._follow_stop = self._follow_thread = None
        if evt:
            evt.set()
        # Not joined: the tick can be mid-enumeration, which on Windows means
        # a PyAudio init that takes long enough to stall a settings-dialog
        # apply. The thread is a daemon and re-checks the event before acting.

    def _follow_loop(self, stop_evt):
        """Reopen capture when it dies, and follow the default output.

        Runs off the render thread on purpose: device enumeration means a
        pactl subprocess on Linux and a PyAudio instance on Windows, either of
        which is tens of milliseconds. At 120 fps that is a visible stutter.
        """
        while not stop_evt.wait(self.FOLLOW_POLL_S):
            if stop_evt.is_set():
                return
            try:
                self._follow_tick(stop_evt)
            except Exception as e:
                _log(f"[audio] Follow watchdog error: {e}")

    def _follow_tick(self, stop_evt):
        with self._life:
            backend = self._backend
            following = self.is_following()
        if backend is None and not self._reopen_failed:
            return                      # never started, or deliberately stopped

        if backend is not None and not backend.is_alive():
            self._reopen("capture stopped", stop_evt)
            return

        if not following:
            return

        # Ask the class, not the instance: this must reflect the live system
        # default rather than anything cached when the stream was opened.
        want = type(backend).default_device_name() if backend else ""
        if not want:
            return                      # backend cannot tell; leave it alone
        if not backend.active_name or want == backend.active_name:
            self._follow_target = ""      # settled on the current default
            return
        if want == self._follow_target:
            return                        # already moved for this one; landed elsewhere
        self._follow_target = want
        self._reopen(f"default output changed to '{want}'", stop_evt)

    def _reopen(self, why: str, stop_evt=None):
        with self._life:
            if stop_evt is not None and stop_evt.is_set():
                return
            if self._backend is not None:
                _log(f"[audio] {why} — reopening capture")
                try:
                    self._backend.stop()
                except Exception:
                    pass
                self._backend = None
            elif self._reopen_failed:
                pass                    # retrying a device that was missing
            else:
                return
            if not self._open_backend(quiet=self._reopen_failed):
                self._reopen_failed = True

    def set_gain(self, gain: float):
        """Input sensitivity: a multiplier on the captured PCM.

        Not a volume control -- nothing is played back. It exists because a
        quiet source (a Bluetooth monitor especially) reaches projectM as a
        near-flat waveform however loud it sounds on the speakers, and beat
        detection then has nothing to work with.
        """
        self._gain = max(0.1, min(8.0, float(gain)))

    def gain(self) -> float:
        return self._gain

    def get_chunk(self) -> "np.ndarray | None":
        with self._lock:
            chunk = self._buffer.popleft() if self._buffer else None
        if chunk is None or self._gain == 1.0:
            return chunk
        # A soft limiter rather than a plain multiply. Hard clipping at high
        # gain turns the PCM into a square wave -- constant maximum energy,
        # which drives feedback presets to solid white. tanh is close to
        # linear while the signal is quiet and rounds the peaks instead of
        # flattening them. (Learned in Bomsai Player, which does this in
        # SharedAudio; its gain stays there, this one defaults to 1.0, so a
        # chunk is never scaled twice.)
        return np.tanh(chunk * self._gain)

    def update_device(self, device_name: str):
        """Hot-swap audio device (Audio Source menu / settings dialog)."""
        was_running = self._backend is not None
        self.stop()
        self._device_name = device_name
        self._reopen_failed = False
        self._follow_target = ""
        if was_running:
            self.start()

# ─── ipc_reader ───────────────────────────────────────────────────────────────

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
    "toggle_borderless": "b",
    "toggle_on_top":    "t",
    "fullscreen_mouse": "DBLCLICK",
    "record":           "F9",
    "toggle_wallpaper": "d",
    "exclude":          "x",
    "snapshot":         "c",
    "toggle_dual":      "2",
    "toggle_visual1":   "1",
    "crossfade_back":   "[",
    "crossfade_fwd":    "]",
    "swap_focus":       "TAB",
}


def load_config_from(config_path, default_config: dict) -> dict:
    """Load config.json, merging over default_config. Path-agnostic."""
    from pathlib import Path
    cfg = dict(default_config)
    p = Path(config_path)
    if p.exists():
        try:
            with open(p, encoding="utf-8-sig") as f:
                cfg.update(json.load(f))
        except Exception as e:
            print(f"[config] Warning: could not load config.json: {e}")
    return cfg


def save_config_to(config_path, cfg: dict):
    """Save config.json. Path-agnostic."""
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        print(f"[config] Warning: could not save config.json: {e}")


def load_ratings_from(ratings_path) -> dict:
    """Load ratings.json. Path-agnostic."""
    from pathlib import Path
    p = Path(ratings_path)
    if p.exists():
        try:
            with open(p, encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_ratings_to(ratings_path, ratings: dict):
    """Save ratings.json. Path-agnostic."""
    try:
        with open(ratings_path, "w", encoding="utf-8") as f:
            json.dump(ratings, f, indent=4, sort_keys=True)
    except Exception as e:
        print(f"[config] Warning: could not save ratings.json: {e}")


def load_keymaps_from(keymaps_path) -> dict:
    """Load keymaps.json, merging over DEFAULT_KEYMAPS. Path-agnostic.

    A default the saved file has already spent is dropped rather than kept.
    The handler is one elif chain in a fixed order, so two actions on one key
    do not tie -- whichever is tested first wins and the other is dead while
    still reading as bound in the Key bindings dialog. That is exactly what a
    new action does to an old customisation: `snapshot` arrived on "c" in
    2.1, and a keymaps.json written before it exists may already have given
    "c" to hard cuts -- as this repo's own does, which is how it was found.
    Pressing "c" then toggles hard cuts and the snapshot key looks broken.

    The saved file wins, because it is the one somebody chose. The new action
    comes up unbound, which the dialog shows as unbound and offers a key for,
    rather than bound to something it will never receive.
    """
    from pathlib import Path
    km = dict(DEFAULT_KEYMAPS)
    p = Path(keymaps_path)
    if not p.exists():
        return km
    try:
        with open(p, encoding="utf-8-sig") as f:
            saved = json.load(f)
    except Exception as e:
        print(f"[config] Warning: could not load keymaps.json: {e}")
        return km
    if not isinstance(saved, dict):
        print("[config] Warning: keymaps.json is not a table of bindings")
        return km
    km.update(saved)
    # Case-insensitively, because the tokens are matched that way. DBLCLICK
    # is not a key and two actions can share it without either being lost.
    taken = {str(v).upper(): a for a, v in saved.items()
             if v and str(v).upper() != "DBLCLICK"}
    for action, key in list(km.items()):
        if action in saved or not key:
            continue
        owner = taken.get(str(key).upper())
        if owner:
            km[action] = ""
            print(f"[config] {action} has no key: {key} is already "
                  f"{owner} in keymaps.json")
    return km


def save_keymaps_to(keymaps_path, km: dict):
    """Save keymaps.json. Path-agnostic."""
    try:
        with open(keymaps_path, "w", encoding="utf-8") as f:
            json.dump(km, f, indent=4)
    except Exception as e:
        print(f"[config] Warning: could not save keymaps.json: {e}")


def load_collections_from(path) -> dict:
    """{collection name: [preset stem, ...]}. Missing file = no collections.

    Ratings say how good a preset is. A collection says what it is *for* --
    the axis a 0-10 number cannot carry -- so the two are stored apart and
    neither rebuilds from the other.
    """
    from pathlib import Path
    p = Path(path)
    if p.exists():
        try:
            with open(p, encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # Shape-checked on the way in rather than at every use: this
                # file is small, hand-editable, and read by a menu builder.
                return {str(k): [str(v) for v in vals]
                        for k, vals in data.items()
                        if isinstance(vals, list)}
        except Exception as e:
            print(f"[config] Warning: could not load collections.json: {e}")
    return {}


def save_collections_to(path, data: dict):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"[config] Warning: could not save collections.json: {e}")


def backup_state(zip_path, paths: dict) -> str:
    """Zip up whichever of `paths` exist. Returns "" or the reason it failed.

    Ratings and collections are the part of this app that cannot be
    reinstalled -- they are hours of somebody deciding what they like. The
    presets themselves are a download; these are not.
    """
    import zipfile as _zf
    try:
        with _zf.ZipFile(zip_path, "w", _zf.ZIP_DEFLATED) as z:
            for name, src in paths.items():
                if os.path.isfile(src):
                    z.write(src, name)
        return ""
    except Exception as e:
        return str(e)


def restore_state(zip_path, paths: dict) -> list:
    """Unpack a backup over `paths`. Returns the names actually restored.

    Only the names the caller asked for, and each written whole: a backup made
    by a later version may hold files this one knows nothing about, and
    guessing where those belong is how a restore corrupts a config.
    """
    import zipfile as _zf
    done = []
    with _zf.ZipFile(zip_path) as z:
        have = set(z.namelist())
        for name, dest in paths.items():
            if name not in have:
                continue
            data = z.read(name)
            os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            done.append(name)
    return done


def get_preset_rating(ratings: dict, name: str) -> int:
    return ratings.get(name, 5)


def set_preset_rating(ratings: dict, name: str, value: int) -> int:
    """Clamp to 0-10, return new value. Rating 0 = excluded from random."""
    # ponytail: this touches the dict and nothing else -- the file write is the
    # caller's, so a batch (delete-0*, reset-all) costs one write rather than
    # one per preset. Every caller does write, straight away: _rate_current,
    # reset_all_ratings and the browser's rating buttons all call
    # _save_ratings on the next line, so a rating survives a force-quit.
    # This used to say ratings were "persisted on app exit instead", which was
    # never true of any caller and would have read as a licence to drop one of
    # those saves.
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
import zipfile
import urllib.request
from pathlib import Path

logger_pm = logging.getLogger(__name__)


def _bundled_libs(*names) -> list:
    """Absolute paths to libraries shipped inside a frozen build.

    PyInstaller puts bundled binaries in sys._MEIPASS (the _internal directory
    for a onedir build) and the launcher beside it, so both are offered. A
    source checkout is not frozen and gets an empty list, which is why this can
    lead every candidate list without changing how a developer run resolves.
    """
    if not getattr(sys, "frozen", False):
        return []
    roots = [getattr(sys, "_MEIPASS", ""), os.path.dirname(sys.executable)]
    return [os.path.join(r, n) for r in roots if r for n in names]


def _vendored_libs(*names) -> list:
    """Absolute paths to the binaries the installers ship, in the checkout.

    These go last, after every system location, so a developer's own build
    still wins where there is one. On Windows there is no system location at
    all, which used to mean a source run needed either a DLL beside the app or
    a projectm_lib path in config.json pointing at wherever it was built.
    Paths that do not exist are ignored by the caller, so this is inert for an
    install that has no installer/ directory beside it.
    """
    plat = "windows" if sys.platform == "win32" else "linux"
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "installer", "vendor", plat)
    return [os.path.join(root, n) for n in names]


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
            return _bundled_libs("projectM-4.dll", "libprojectM-4.dll") + [
                "libprojectM-4.dll", "projectM-4.dll"] + _vendored_libs(
                "projectM-4.dll")
        elif sys.platform == "darwin":
            return ["/usr/local/lib/libprojectM-4.dylib", "/opt/homebrew/lib/libprojectM-4.dylib"]
        else:
            return _bundled_libs(
                "libprojectM-4.so", "libprojectM-4.so.4") + [
                "/usr/local/lib/libprojectM-4.so",
                "/usr/local/lib/libprojectM-4.so.4",
                "/usr/local/lib/libprojectM-4.so.4.1.0",
                "/usr/lib/x86_64-linux-gnu/libprojectM-4.so",
            ] + _vendored_libs("libprojectM-4.so.4")

    def get_projectm_playlist_candidates():
        if sys.platform == "win32":
            # MSVC emits no "lib" prefix (CMake OUTPUT_NAME is projectM-4-playlist),
            # so the unprefixed name is what a Visual Studio build actually produces.
            # The prefixed name is kept for MinGW/MSYS2 builds, matching the core list.
            return _bundled_libs(
                "projectM-4-playlist.dll", "libprojectM-4-playlist.dll") + [
                "libprojectM-4-playlist.dll", "projectM-4-playlist.dll"
            ] + _vendored_libs("projectM-4-playlist.dll")
        elif sys.platform == "darwin":
            return ["/usr/local/lib/libprojectM-4-playlist.dylib"]
        else:
            return _bundled_libs(
                "libprojectM-4-playlist.so", "libprojectM-4-playlist.so.4") + [
                "/usr/local/lib/libprojectM-4-playlist.so",
                "/usr/local/lib/libprojectM-4-playlist.so.4",
                "/usr/local/lib/libprojectM-4-playlist.so.4.1.0",
                "/usr/lib/x86_64-linux-gnu/libprojectM-4-playlist.so",
            ] + _vendored_libs("libprojectM-4-playlist.so.4")


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
        try:
            with open(config_path, encoding="utf-8-sig") as f:
                explicit_core = json.load(f).get("projectm_lib", "")
        except (OSError, ValueError) as e:
            # Every other reader warns and falls back; this one used to raise
            # on the render thread, which killed it before the first frame.
            logger_pm.warning(f"Ignoring unreadable {config_path}: {e}")

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


#: Kept alive at module scope on purpose. ctypes does not own a CFUNCTYPE
#: instance, so a callback handed to a C library and then dropped is a use
#: after free the next time the library logs -- which here would be inside
#: projectM, on the render thread, at whatever moment a preset failed.
_LOG_CB = None

#: projectm_log_level. WARN and above are the ones worth a line: a preset
#: whose shader would not compile is a thing users report as "some presets are
#: black", and until this callback existed the reason went nowhere at all.
_PM_LOG_WARN = 4
_PM_LOG_NAMES = {4: "warn", 5: "error", 6: "fatal"}

#: What libprojectM says when an edit will not compile. The level is not a
#: usable filter, because it is chosen per *stage* rather than per severity --
#: read off the 4.2.0 source at the pinned commit:
#:
#:   warp shader     PerPixelMesh.cpp:61,80    LOG_ERROR  (5)
#:   composite       FinalComposite.cpp:49,87  LOG_WARN   (4)
#:   the GLSL text   Shader.cpp:215            LOG_ERROR  (5)
#:   per-pixel, per-frame, shape, wave         thrown, never logged above DEBUG
#:
#: So "ERROR and above" silently drops every composite-shader failure, which is
#: what editing a comp shader produces. Matched on the text as well as the
#: level. Not the whole of WARN: most of that level is `[TextureManager] Failed
#: to find requested texture`, which a preset pack triggers constantly and
#: which stops nothing being drawn, and a red line under the editor on every
#: other preset is worse than the silence it replaced.
#:
#: "could not compile" is the per-pixel and per-frame spelling. Those are
#: thrown rather than logged, so they arrive through the failed-preset callback
#: instead and this never matches them -- kept because it costs nothing and the
#: two channels have swapped before.
_PM_COMPILE_FAIL = ("error compiling", "error translating",
                    "error loading", "could not compile")

#: The newest failure the library logged. A preset that cannot be *parsed*
#: raises through projectm_set_preset_switch_failed_event_callback, but a
#: preset whose *shader* will not compile does not: projectM logs it and
#: renders on with the broken stage dropped. That second kind is the one
#: somebody editing shader code hits constantly, so the log is the only place
#: it can be read from.
_LAST_PM_ERROR = ""


def _install_projectm_logging(core) -> bool:
    """Route libprojectM's own log at WARN and above to stdout. Once."""
    global _LOG_CB
    if _LOG_CB is not None:
        return True
    try:
        proto = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int,
                                 ctypes.c_void_p)
        core.projectm_set_log_callback.restype = None
        core.projectm_set_log_callback.argtypes = [proto, ctypes.c_bool,
                                                   ctypes.c_void_p]
        core.projectm_set_log_level.restype = None
        core.projectm_set_log_level.argtypes = [ctypes.c_int, ctypes.c_bool]
    except AttributeError:
        return False

    def _on_log(message, level, _user):
        global _LAST_PM_ERROR
        try:
            text = (message or b"").decode("utf-8", "replace").strip()
            if text:
                if level >= 5 or any(s in text.lower()
                                     for s in _PM_COMPILE_FAIL):
                    _LAST_PM_ERROR = text
                print(f"[projectm:{_PM_LOG_NAMES.get(level, level)}] {text}",
                      flush=True)
        except Exception:
            pass                      # a logger must never take the app down

    _LOG_CB = proto(_on_log)
    try:
        # False: every thread. projectM renders on one thread and is created
        # on another, and a message pinned to the calling thread would miss
        # the half that matters.
        core.projectm_set_log_callback(_LOG_CB, False, None)
        core.projectm_set_log_level(_PM_LOG_WARN, False)
        return True
    except Exception as e:
        _LOG_CB = None
        print(f"[projectm_wrapper] log callback failed: {e}", flush=True)
        return False


def texture_load_answer(got):
    """What the texture callback hands projectM: (id, w, h), or None.

    Its own guard, not projectM's: a texture id with a zero width is logged
    as a warning and thrown away at the other end, so the answer is either
    complete or not given at all. Separate from the trampoline because
    everything above this line can be tested without a GL context.
    """
    if not got:
        return None
    try:
        tex, w, h = int(got[0]), int(got[1]), int(got[2])
    except (TypeError, ValueError, IndexError):
        return None
    if tex and w > 0 and h > 0:
        return (tex, w, h)
    return None


class _TextureLoadData(ctypes.Structure):
    """projectm_texture_load_data, filled in by the texture-load callback.

    Either raw pixels or an existing GL texture id; the id wins if both are
    set. projectM wraps an id *without* taking ownership -- TextureManager.cpp
    passes false for that and says so in a comment -- and caches the wrapper,
    so the preset samples our texture every frame and a glTexSubImage2D
    between frames is live inside the preset. The public header's @warning
    claims the opposite. The code is what runs; the header is stale.
    """
    _fields_ = [("data",       ctypes.POINTER(ctypes.c_ubyte)),
                ("width",      ctypes.c_uint),
                ("height",     ctypes.c_uint),
                ("channels",   ctypes.c_uint),
                ("texture_id", ctypes.c_uint)]


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
        _install_projectm_logging(core)

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

        # Render into a framebuffer of ours rather than the window. What
        # makes two visuals at once possible: each instance draws into its
        # own colour texture and one shader pass mixes them.
        try:
            C.projectm_opengl_render_frame_fbo.restype  = None
            C.projectm_opengl_render_frame_fbo.argtypes = [ctypes.c_void_p,
                                                            ctypes.c_uint32]
            self.can_render_fbo = True
        except AttributeError:
            self.can_render_fbo = False

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

        # Load a preset from a string rather than a file. This is what makes
        # editing one possible at all: the text can be patched and played back
        # without ever being written to disk, so nothing is risked until Save.
        # Guarded because it is optional -- an older libprojectM without it
        # simply has no tweak feature rather than no application.
        try:
            C.projectm_load_preset_data.restype  = None
            C.projectm_load_preset_data.argtypes = [ctypes.c_void_p,
                                                     ctypes.c_char_p,
                                                     ctypes.c_bool]
            self.can_load_data = True
        except AttributeError:
            self.can_load_data = False
            print("[projectm_wrapper] projectm_load_preset_data missing — "
                  "live preset tweaking is unavailable")

        # projectm_load_preset_data returns void, so a call that "worked" and
        # a preset whose shader would not compile look identical from Python.
        # This callback is the only thing that knows the difference, and it is
        # what makes an editor possible: without it a bad edit leaves the last
        # good frame on screen and nothing says why.
        self.last_error = ""
        self._fail_cb = None
        self._fail_proto = ctypes.CFUNCTYPE(None, ctypes.c_char_p,
                                            ctypes.c_char_p, ctypes.c_void_p)
        try:
            C.projectm_set_preset_switch_failed_event_callback.restype = None
            C.projectm_set_preset_switch_failed_event_callback.argtypes = [
                ctypes.c_void_p, self._fail_proto, ctypes.c_void_p]
            self.can_report_failures = True
        except AttributeError:
            self.can_report_failures = False

        # Drops the scanned texture list, so a texture swapped on disk is
        # picked up without recreating the whole projectM instance.
        try:
            C.projectm_reset_textures.restype  = None
            C.projectm_reset_textures.argtypes = [ctypes.c_void_p]
            self.can_reset_textures = True
        except AttributeError:
            self.can_reset_textures = False

        # User sprites: a picture laid over the preset, moved by MilkDrop
        # sprite code. Only on libprojectM master -- the 4.1.x releases have
        # none of this -- so it is guarded like everything else optional, and
        # the whole tab disappears rather than the app failing to start.
        try:
            C.projectm_sprite_create.restype  = ctypes.c_uint32
            C.projectm_sprite_create.argtypes = [ctypes.c_void_p,
                                                  ctypes.c_char_p,
                                                  ctypes.c_char_p]
            C.projectm_sprite_destroy_all.restype  = None
            C.projectm_sprite_destroy_all.argtypes = [ctypes.c_void_p]
            C.projectm_sprite_get_sprite_count.restype  = ctypes.c_uint32
            C.projectm_sprite_get_sprite_count.argtypes = [ctypes.c_void_p]
            C.projectm_sprite_set_max_sprites.restype  = None
            C.projectm_sprite_set_max_sprites.argtypes = [ctypes.c_void_p,
                                                           ctypes.c_uint32]
            self.can_sprites = True
        except AttributeError:
            self.can_sprites = False

        # Answers for a texture a preset asks for that libprojectM cannot
        # load itself. The struct is filled in by the callback: an existing
        # GL texture id wins over raw pixels, and leaving it alone is how we
        # say "not ours, look on disk as usual".
        self._tex_cb = None
        self._tex_proto = ctypes.CFUNCTYPE(
            None, ctypes.c_char_p, ctypes.POINTER(_TextureLoadData),
            ctypes.c_void_p)
        try:
            C.projectm_set_texture_load_event_callback.restype = None
            C.projectm_set_texture_load_event_callback.argtypes = [
                ctypes.c_void_p, self._tex_proto, ctypes.c_void_p]
            self.can_live_textures = True
        except AttributeError:
            self.can_live_textures = False

        # Burn-in: a texture of ours drawn into the *preset's* framebuffer,
        # so the preset's own warp and decay take hold of it. Where a live
        # texture is sampled by name wherever the preset happens to use it,
        # this is laid over the whole frame and smeared by the warp -- two
        # different effects, and both are wanted.
        try:
            C.projectm_opengl_burn_texture.restype  = None
            C.projectm_opengl_burn_texture.argtypes = [
                ctypes.c_void_p, ctypes.c_uint32,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
            self.can_burn = True
        except AttributeError:
            self.can_burn = False

        # Whether a new preset inherits the last frame of the old one. The
        # inheritance is projectM's default and is usually what people want;
        # it also means a preset that starts dark takes a moment to shed the
        # bright one before it, which some people read as a glitch.
        try:
            C.projectm_set_preset_start_clean.restype  = None
            C.projectm_set_preset_start_clean.argtypes = [ctypes.c_void_p,
                                                           ctypes.c_bool]
            self.can_start_clean = True
        except AttributeError:
            self.can_start_clean = False

        # Preset-switched callback (used to track current name)
        C.projectm_set_preset_switch_requested_event_callback.restype  = None
        C.projectm_set_preset_switch_requested_event_callback.argtypes = [
            ctypes.c_void_p,   # handle
            ctypes.c_void_p,   # callback fn ptr (CFUNCTYPE)
            ctypes.c_void_p,   # user_data
        ]

    def _setup_playlist_signatures(self):
        P = self._playlist

        # Takes the projectM instance:
        #   projectm_playlist_handle projectm_playlist_create(projectm_handle)
        # Declaring it as no-args left the first argument register holding
        # whatever happened to be there. On the SysV ABI (RDI) that was benign
        # often enough to look correct on Linux, helped by the explicit
        # projectm_playlist_connect() below doing the real wiring. On Windows
        # x64 the argument is RCX, and projectM wrote through the garbage:
        # "access violation writing 0x00000000000036C8".
        P.projectm_playlist_create.restype  = ctypes.c_void_p
        P.projectm_playlist_create.argtypes = [ctypes.c_void_p]

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

        # Held on the instance for the reason the log callback is held at
        # module scope: ctypes does not own a CFUNCTYPE, and projectM calls
        # this one from the render thread the moment a preset fails.
        if self.can_report_failures:
            self._fail_cb = self._fail_proto(self._on_preset_failed)
            self._core.projectm_set_preset_switch_failed_event_callback(
                self._pm, self._fail_cb, None)

        if self._playlist:
            self._pl = self._playlist.projectm_playlist_create(self._pm)
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
            # Recursive, because _all_preset_files() has always globbed with
            # ** and the two have to agree: a non-recursive load left the
            # playlist holding only the top level while sort, delete-zero and
            # the browser saw every nested preset. Packs are nested -- Cream of
            # the Crop ships 9,795 presets two folders deep, and pointing at
            # its root used to load four.
            count = self._playlist.projectm_playlist_add_path(
                self._pl, path.encode(), True, False
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

    def current_preset_path(self) -> str:
        """Full path of the preset playing, or "" when it cannot be known.

        current_preset_name() takes the stem of this same string, because that
        is what ratings and thumbnails are keyed by. Editing needs the file.
        """
        try:
            if self._pl and self._playlist:
                pos = self._playlist.projectm_playlist_get_position(self._pl)
                raw = self._playlist.projectm_playlist_item(self._pl, pos)
                if raw:
                    return raw.decode(errors="replace")
            elif self._preset_files:
                return self._preset_files[self._preset_index]
        except Exception:
            pass
        return ""

    def _on_preset_failed(self, filename, message, _user):
        """projectM could not compile a preset. Render thread.

        Fires for playlist switches as well as edits, which is why the name is
        logged: on a 130,000-preset pack "some presets are black" is a report
        we used to have no answer to.
        """
        try:
            self.last_error = (message or b"").decode("utf-8",
                                                      "replace").strip()
            name = os.path.basename(
                (filename or b"").decode("utf-8", "replace"))
            print(f"[projectm] preset failed: {name or '<edited>'}: "
                  f"{self.last_error}", flush=True)
        except Exception:
            pass                  # a reporter must never take the app down

    def load_preset_data(self, text: str, smooth: bool = False) -> bool:
        """Play a preset held in a string. Render thread only -- it compiles
        shaders, which needs the GL context.

        True means the call went through, not that the preset compiled. Read
        `last_error` after it for that -- it is cleared here and filled in by
        the callback before this returns.
        """
        global _LAST_PM_ERROR
        if not self._pm or not getattr(self, "can_load_data", False):
            return False
        self.last_error = _LAST_PM_ERROR = ""
        try:
            self._core.projectm_load_preset_data(
                self._pm, text.encode("utf-8", "replace"), bool(smooth))
            return True
        except Exception as e:
            print(f"[projectm_wrapper] load_preset_data failed: {e}",
                  flush=True)
            return False

    def reset_textures(self) -> bool:
        """Forget the scanned texture list. Render thread only."""
        if not self._pm or not getattr(self, "can_reset_textures", False):
            return False
        try:
            self._core.projectm_reset_textures(self._pm)
            return True
        except Exception as e:
            print(f"[projectm_wrapper] reset_textures failed: {e}", flush=True)
            return False

    def burn_texture(self, texture: int, left: int, top: int,
                     width: int, height: int):
        """Draw a GL texture into the preset's framebuffer. Render thread.

        Call it *before* render_frame: the warp reads the framebuffer it is
        about to draw over, so burning first is what puts the picture through
        this frame's warp rather than the next one's.
        """
        if not self._pm or not getattr(self, "can_burn", False):
            return
        try:
            self._core.projectm_opengl_burn_texture(
                self._pm, int(texture), int(left), int(top),
                int(width), int(height))
        except Exception as e:
            print(f"[projectm_wrapper] burn_texture failed: {e}", flush=True)

    def set_texture_load_callback(self, answer) -> bool:
        """Answer for textures libprojectM cannot load itself. Render thread.

        `answer(name)` returns (texture_id, width, height) to hand projectM a
        GL texture of ours, or None to leave the name to projectM's own disk
        scan. Called once per texture name, when a preset or a sprite first
        asks for it -- projectM caches what comes back, so this is not a
        per-frame cost, and the frames that follow go into the texture.

        Only one callback per instance, and passing NULL removes it.
        """
        if not self._pm or not getattr(self, "can_live_textures", False):
            return False

        def trampoline(name, out, _user):
            # Nothing may escape into C: a Python exception crossing a ctypes
            # callback prints a traceback and then returns whatever happens
            # to be in the register, which here would be a texture id.
            try:
                got = texture_load_answer(
                    answer((name or b"").decode("utf-8", "replace")))
                if got:
                    out[0].texture_id, out[0].width, out[0].height = got
                    out[0].channels = 4
            except Exception as e:
                print(f"[projectm_wrapper] texture callback failed: {e}",
                      flush=True)

        # Held on self because ctypes does not: a CFUNCTYPE object that goes
        # out of scope is freed, and C then calls into the hole it left.
        self._tex_cb = self._tex_proto(trampoline)
        self._core.projectm_set_texture_load_event_callback(
            self._pm, self._tex_cb, None)
        return True

    def set_sprites(self, definitions) -> int:
        """Replace every sprite with this list of definitions. Render thread.

        Replace rather than add: the panel hands over the whole configured
        list after any change, so there is one code path and no way for the
        screen and the configuration to drift apart. Returns how many the
        library accepted -- a definition naming a picture the texture manager
        cannot find fails inside projectM, which is exactly the case worth
        reporting back rather than leaving as an invisible no-op.
        """
        if not self._pm or not getattr(self, "can_sprites", False):
            return 0
        try:
            self._core.projectm_sprite_destroy_all(self._pm)
            self._core.projectm_sprite_set_max_sprites(self._pm, MAX_SPRITES)
            for text in definitions:
                self._core.projectm_sprite_create(
                    self._pm, b"milkdrop", text.encode("utf-8"))
            return int(self._core.projectm_sprite_get_sprite_count(self._pm))
        except Exception as e:
            print(f"[projectm_wrapper] set_sprites failed: {e}", flush=True)
            return 0

    def set_preset_start_clean(self, on: bool) -> bool:
        if not self._pm or not getattr(self, "can_start_clean", False):
            return False
        try:
            self._core.projectm_set_preset_start_clean(self._pm, bool(on))
            return True
        except Exception as e:
            print(f"[projectm_wrapper] set_preset_start_clean failed: {e}",
                  flush=True)
            return False

    def render_frame(self):
        self._core.projectm_opengl_render_frame(self._pm)

    def render_frame_fbo(self, fbo: int):
        """Draw this frame into `fbo` instead of the window. Render thread.

        projectM binds it itself and leaves it bound, so whatever draws next
        has to bind what it wants.
        """
        self._core.projectm_opengl_render_frame_fbo(self._pm, int(fbo))

    def add_pcm_float(self, samples, count: int, channels: int = 2):
        arr = (ctypes.c_float * len(samples))(*samples)
        self._core.projectm_pcm_add_float(self._pm, arr, count, channels)

    def set_window_size(self, w: int, h: int):
        self._core.projectm_set_window_size(self._pm, w, h)

    def next_preset(self, hard_cut: bool = True):
        """Step forward. Works while the preset is locked -- see set_locked."""
        if self._pl and self._playlist:
            self._playlist.projectm_playlist_play_next(self._pl, hard_cut)
        else:
            self._preset_index = (self._preset_index + 1) % max(1, len(self._preset_files))
            self._load_preset_direct(self._preset_index)

    def prev_preset(self, hard_cut: bool = True):
        """Step back. Works while the preset is locked -- see set_locked."""
        if self._pl and self._playlist:
            self._playlist.projectm_playlist_play_previous(self._pl, hard_cut)
        else:
            self._preset_index = (self._preset_index - 1) % max(1, len(self._preset_files))
            self._load_preset_direct(self._preset_index)

    def set_locked(self, locked: bool):
        """Stop the preset changing *on its own*.

        Lock is a hold on the clock, not on the controls: next, previous,
        random and a jump from the browser all still move, here and in the
        renderer above. Measured against libprojectM 4.1.0 -- with the core
        lock on, projectm_playlist_play_next, play_previous and set_position
        each moved the playlist, and ten rendered frames later the new preset
        was still the one on screen, so nothing has to be unlocked around an
        explicit jump.
        """
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
        # An empty playlist is the welcome preset showing, not a failure.
        try:
            if self.playlist_size() == 0:
                return WELCOME_NAME
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

        Works while locked, like every other explicit move: lock only stops
        the preset changing by itself.
        """
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


# ─── preset editing ───────────────────────────────────────────────────────────
#
# A .milk file is an INI: a few header lines, a [preset00] section of scalars,
# then the per-frame/per-pixel equations and the warp/comp shaders. Everything
# here works on that text and nothing else, so it is all testable without a GPU
# -- the only part that needs one is handing the result to
# ProjectM4.load_preset_data(), which plays a preset straight out of a string.
# Nothing is written to disk until Save.

#: (ini key, shader variable, label, low, high, decimals). The shader variable
#: is not always the key: the file says fDecay and the equations say decay, and
#: telling whether a slider will do anything depends on knowing both. Measured
#: across 9,795 presets -- every one of these keys is present in >99% of them.
PRESET_TWEAKS = (
    ("zoom",            "zoom",       "Zoom",             0.5,  2.0,  3),
    ("rot",             "rot",        "Rotation",        -1.0,  1.0,  3),
    ("warp",            "warp",       "Warp",             0.0,  4.0,  3),
    ("dx",              "dx",         "Drift across",    -0.05, 0.05, 4),
    ("dy",              "dy",         "Drift down",      -0.05, 0.05, 4),
    ("fDecay",          "decay",      "Trail length",     0.5,  1.0,  3),
    ("fGammaAdj",       "gamma",      "Brightness",       0.5,  3.0,  3),
    ("fVideoEchoAlpha", "echo_alpha", "Echo",             0.0,  1.0,  3),
    ("fWaveScale",      "wave_scale", "Wave size",        0.01, 5.0,  3),
    ("fWaveAlpha",      "wave_a",     "Wave opacity",     0.0,  4.0,  3),
)

#: Sampler names projectM provides itself -- the framebuffers and the built-in
#: noise volumes. They are not files and cannot be swapped for a picture.
BUILTIN_TEXTURES = frozenset((
    "main", "blur1", "blur2", "blur3",
    "noise_lq", "noise_lq_lite", "noise_mq", "noise_hq",
    "noisevol_lq", "noisevol_hq",
))

#: MilkDrop's sampler names carry an optional filter/wrap prefix, so
#: sampler_pw_noise_lq and sampler_noise_lq are the same texture.
_SAMPLER_PREFIXES = ("fw_", "fc_", "pw_", "pc_")

#: Extensions libprojectM's texture scanner accepts, lower-case. The lookup
#: itself is by filename *stem*, so a .png override replaces a stock .jpg.
#: This is libprojectM's own list, copied from TextureManager.hpp, and it is
#: short: no .gif, no .webp, nothing animated. See LIVE_IMAGE_EXTENSIONS.
TEXTURE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".dds", ".tga", ".bmp", ".dib")

#: Pictures Qt reads that libprojectM does not. A preset or sprite naming one
#: of these is answered by our own texture callback instead of by its scanner
#: -- and the animated ones keep animating while the preset samples them.
LIVE_IMAGE_EXTENSIONS = (".gif", ".webp", ".apng", ".tif", ".tiff", ".svg",
                         ".svgz", ".xpm", ".xbm", ".pbm", ".pgm", ".ppm",
                         ".ico", ".icns", ".jfif", ".wbmp")

#: Video containers, read through ffmpeg. Same route into a preset as an
#: animated picture: one frame at a time into a texture projectM samples.
VIDEO_EXTENSIONS = (".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v",
                    ".mpg", ".mpeg", ".wmv")

#: Everything accepted as a texture override or a sprite picture. Wider than
#: what libprojectM reads, because the callback covers the difference.
PICTURE_EXTENSIONS = (TEXTURE_EXTENSIONS + LIVE_IMAGE_EXTENSIONS
                      + VIDEO_EXTENSIONS)


def _picture_formats_line() -> str:
    """What to say when a file is not a picture. Named rather than listed:
    thirty-two extensions in a toast is a wall, not an answer."""
    return ("Use a picture (png, jpg, gif, webp, tif\u2026) or a video "
            "(mp4, webm, mkv\u2026).")

_CODE_KEYS = ("per_frame_", "per_pixel_", "per_frame_init_")


def _preset_code(text: str) -> str:
    """Just the equation lines, joined. Shaders are deliberately left out:
    they assign to their own locals, not to the preset's scalars."""
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if any(stripped.startswith(k) for k in _CODE_KEYS):
            out.append(stripped.split("=", 1)[-1])
    return "\n".join(out)


def preset_scalar(text: str, key: str):
    """The value of one top-level `key=value` line, or None.

    Anchored to the start of a line and matched case-insensitively, because
    presets in the wild spell fDecay as fdecay and FDECAY.
    """
    import re
    m = re.search(rf"^[ \t]*{re.escape(key)}[ \t]*=[ \t]*([^\r\n]*)",
                  text, re.IGNORECASE | re.MULTILINE)
    if not m:
        return None
    try:
        return float(m.group(1).strip())
    except ValueError:
        return None


def preset_driven_keys(text: str) -> set:
    """Which tweakable variables the preset's own code assigns every frame.

    This matters more than it sounds: `zoom` is set from per-frame code in
    **49% of the 9,795 presets measured**, and in those the base value in the
    file is overwritten before the first pixel is drawn. A slider for it would
    move and do nothing, which reads as a broken feature rather than as a
    preset that has its own opinion. The UI disables those and says why.

    `==` is excluded so a comparison is not mistaken for an assignment.
    """
    import re
    code = _preset_code(text)
    driven = set()
    for key, var, *_ in PRESET_TWEAKS:
        if re.search(rf"\b{re.escape(var)}[ \t]*=[^=]", code):
            driven.add(key)
    return driven


def patch_preset(text: str, updates: dict) -> str:
    """Return `text` with these top-level scalars replaced.

    The rest of the file is untouched, line endings included -- a preset is
    somebody else's work and a rewrite that reformats it is a rewrite that
    cannot be diffed against the original. A key the file does not have is
    inserted after the [preset00] header rather than appended, because
    anything after the first `per_frame_` line is code.
    """
    import re
    for key, value in updates.items():
        # Six decimals then trimmed, but never past the point: the library
        # writes zoom=1.011000 and rot=0.020000, and a bare "3" sitting in
        # that column reads as a different kind of value.
        val = f"{float(value):.6f}".rstrip("0")
        if val.endswith("."):
            val += "0"
        pattern = re.compile(
            rf"^([ \t]*{re.escape(key)}[ \t]*=)[ \t]*[^\r\n]*",
            re.IGNORECASE | re.MULTILINE)
        text, n = pattern.subn(lambda m: m.group(1) + val, text, count=1)
        if n:
            continue
        header = re.search(r"^\[[^\]\r\n]+\][ \t]*\r?\n", text, re.MULTILINE)
        if header:
            text = text[:header.end()] + f"{key}={val}\n" + text[header.end():]
        else:
            text = f"{key}={val}\n" + text
    return text


#: Preset keys that spell one colour as three numbers. A shape carries a
#: second colour in r2/g2/b2, so the trailing digit belongs to the group and
#: not to the channel.
_COLOUR_PREFIXES = ("wave", "ob", "ib", "mv")


def shift_preset_colours(text: str, turns: float) -> str:
    """Return `text` with every colour rotated the same way round the hue wheel.

    One offset for the whole preset rather than a fresh random colour per slot.
    The relationships between a preset's colours are most of its design, and
    rolling each one on its own gives a different preset every time instead of
    the same preset in another key. A grey has no hue and comes back unchanged,
    which is what keeps a black background black.

    Two things carry colour. The named triples -- wave_, ob_, ib_, mv_ and each
    shape's own pair -- and float3/float4 literals in the shaders. A literal is
    only touched when its three colour components all read as plain numbers in
    0..1: a float3 in a shader is just as likely to be a direction or a texture
    coordinate, and rotating one of those bends the geometry rather than
    recolouring it.

    Nothing else in the file moves, decimal places included, so a preset that
    has been recoloured still diffs cleanly against the one it came from.
    """
    import re
    import colorsys

    def rotate(r, g, b):
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        return colorsys.hsv_to_rgb((h + turns) % 1.0, s, v)

    def like(sample: str, value: float) -> str:
        """The number, written with the decimals the file already used.

        Only ever called for a value that actually moved. Comparing the
        rotated number against the old one rather than comparing the two
        spellings is what keeps a full turn a genuine no-op: presets in the
        wild write plain `1` where this would write `1.000`, and rewriting
        those would make noise of every line in the diff.
        """
        t = sample.strip()
        dec = len(t) - t.find(".") - 1 if "." in t else 3
        return f"{value:.{min(max(dec, 1), 6)}f}"

    edits = []                    # (start, end, replacement)

    named = re.compile(
        r"^[ \t]*(?P<name>[A-Za-z_0-9]+?)_(?P<ch>[rgb])(?P<slot>2?)"
        r"[ \t]*=[ \t]*(?P<val>[-+0-9.]+)[ \t]*$",
        re.IGNORECASE | re.MULTILINE)
    groups = {}
    for m in named.finditer(text):
        name = m.group("name").lower()
        if name not in _COLOUR_PREFIXES \
                and not re.fullmatch(r"shapecode_\d+", name):
            continue
        groups.setdefault((name, m.group("slot")), {})[m.group("ch").lower()] = m
    for chans in groups.values():
        if set(chans) != {"r", "g", "b"}:
            continue              # half a colour is not one
        try:
            rgb = [float(chans[c].group("val")) for c in "rgb"]
        except ValueError:
            continue
        if not all(0.0 <= v <= 1.0 for v in rgb):
            continue
        for c, v, old in zip("rgb", rotate(*rgb), rgb):
            if abs(v - old) < 5e-7:
                continue          # this channel did not move
            m = chans[c]
            edits.append((m.start("val"), m.end("val"),
                          like(m.group("val"), v)))

    literal = re.compile(r"\bfloat([34])[ \t]*\(([^()]*)\)", re.IGNORECASE)
    for m in literal.finditer(text):
        parts = m.group(2).split(",")
        if len(parts) != int(m.group(1)):
            continue
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            continue              # an expression, not a colour
        if not all(0.0 <= v <= 1.0 for v in nums[:3]):
            continue
        out = list(rotate(*nums[:3])) + nums[3:]
        if all(abs(a - b) < 5e-7 for a, b in zip(nums, out)):
            continue
        edits.append((m.start(2), m.end(2),
                      ",".join(like(p, v) for p, v in zip(parts, out))))

    for start, end, rep in sorted(edits, key=lambda e: e[0], reverse=True):
        text = text[:start] + rep + text[end:]
    return text


#: The bins a preset comes apart into, in the order MilkDrop's own mash-up
#: offers them. The label is what the UI says; the pattern is what decides
#: which bin a line belongs to.
PRESET_BINS = (
    ("warp",   "Warp shader",  r"(?:PSVERSION_WARP|warp_\d+)"),
    ("comp",   "Composite shader", r"(?:PSVERSION_COMP|comp_\d+)"),
    ("motion", "Motion",
     r"(?:per_frame_init_\d+|per_frame_\d+|per_pixel_\d+)"),
    ("waves",  "Waves",  r"(?:wavecode_\d+_\w+|wave_\d+_per_\w+)"),
    ("shapes", "Shapes", r"(?:shapecode_\d+_\w+|shape_\d+_per_\w+)"),
)


def preset_bin(line: str) -> str:
    """Which bin this line belongs to, or "" for everything else.

    Deliberately narrow. `wave_r=` is a colour and stays with the scalars;
    only `wave_0_per_point1=` is wave *code*. A bin that swept up the scalars
    too would move a preset's colours every time somebody took its waves.
    """
    import re
    for name, _label, pattern in PRESET_BINS:
        if re.match(rf"[ \t]*{pattern}[ \t]*=", line, re.IGNORECASE):
            return name
    return ""


def split_preset(text: str) -> list:
    """[(bin, line), …] for every line, terminators kept.

    `"".join(line for _, line in split_preset(t)) == t` for any t at all. That
    property is the whole feature: a splitter that drops a byte drops somebody
    else's work, and everything below is built on top of it.
    """
    return [(preset_bin(line), line)
            for line in text.splitlines(keepends=True)]


def mashup_preset(base: str, donor: str, bins) -> str:
    """`base` with each named bin replaced by `donor`'s.

    Lines are taken verbatim and in the donor's own order, because order
    inside a bin is meaningful -- warp_1..warp_20 are concatenated in that
    order to make one shader, and per_frame_N runs in N order. Order *between*
    bins is not: each line is its own key=value, so a bin the base does not
    carry at all can go at the end without disturbing anything.

    PSVERSION travels with the shader that needs it. Without that a version 3
    warp shader lands in a preset that declares version 2 and will not
    compile, which is a mash-up that looks broken rather than one that is.
    """
    import re
    wanted = {b for b in bins if b}
    if not wanted:
        return base

    taken = {}
    for name, line in split_preset(donor):
        if name in wanted:
            taken.setdefault(name, []).append(line)

    out, done = [], set()
    for name, line in split_preset(base):
        if name not in wanted:
            out.append(line)
            continue
        if name not in done:
            done.add(name)
            out.extend(taken.get(name, []))
    for name in PRESET_BINS:
        if name[0] in wanted and name[0] not in done:
            out.extend(taken.get(name[0], []))
    text = "".join(out)

    if wanted & {"warp", "comp"}:
        versions = [v for v in (preset_scalar(text, "PSVERSION"),
                                preset_scalar(text, "PSVERSION_WARP"),
                                preset_scalar(text, "PSVERSION_COMP"))
                    if v is not None]
        # Written as an integer, not through patch_preset: that spells every
        # value as a float, and PSVERSION=3.0 is not what any preset says.
        if versions:
            text = re.sub(r"(?im)^([ \t]*PSVERSION[ \t]*=)[ \t]*[^\r\n]*",
                          lambda m: m.group(1) + str(int(max(versions))),
                          text, count=1)
    return text


def add_per_frame_line(text: str):
    """Insert the next free `per_frame_N=` line. Returns (text, cursor offset).

    This is the generic form of MilkDrop 3's inject-a-Q-variable button, and a
    smaller one: the editor can already type `per_frame_12=q1=bass`, and the
    only thing that is genuinely awkward by hand is knowing that 12 is free.

    Placement is the other half. A per-frame line has to sit in the per-frame
    block: after the last one if there is one, and otherwise ahead of the first
    line of whatever block comes next, because everything after that point is
    code of another kind. Appending to the end of the file would drop it inside
    a shader.
    """
    import re
    used = [int(m.group(1)) for m in
            re.finditer(r"^per_frame_(\d+)[ \t]*=", text,
                        re.IGNORECASE | re.MULTILINE)]
    line = f"per_frame_{max(used) + 1 if used else 1}="

    last = None
    for m in re.finditer(r"^per_frame_\d+[ \t]*=[^\r\n]*\r?\n?", text,
                         re.IGNORECASE | re.MULTILINE):
        last = m
    if last is not None:
        at = last.end()
    else:
        nxt = re.search(r"^(?:per_pixel_|warp_|comp_|shapecode_|wavecode_)",
                        text, re.IGNORECASE | re.MULTILINE)
        at = nxt.start() if nxt else len(text)
    if at and not text[:at].endswith("\n"):
        line = "\n" + line
    return text[:at] + line + "\n" + text[at:], at + len(line)


def preset_textures(text: str) -> list:
    """File textures this preset samples, in first-seen order.

    The built-in framebuffers and noise volumes are dropped, and so are
    MilkDrop's rand00-rand07 slots: those pick a random file from the pack on
    every load, so overriding one would take effect at random.
    """
    import re
    seen, out = set(), []
    for raw in re.findall(r"sampler_([A-Za-z0-9_]+)", text):
        name = raw.lower()
        for prefix in _SAMPLER_PREFIXES:
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        if (name in BUILTIN_TEXTURES or name in seen
                or re.fullmatch(r"rand\d\d", name)):
            continue
        seen.add(name)
        out.append(name)
    return out


def bundled_dir(*parts) -> str:
    """A file that ships with the app, source checkout or frozen build alike.

    PyInstaller unpacks the bundle to a temporary directory and points
    sys._MEIPASS at it; the source tree has everything beside this file.
    """
    root = getattr(sys, "_MEIPASS", None) or os.path.dirname(
        os.path.abspath(__file__))
    return os.path.join(root, *parts)


#: What the welcome preset calls itself in the title bar and the browser.
#: Not "Unknown", which is what an empty playlist reads as everywhere else and
#: is the flag several code paths use for "nothing is playing yet".
WELCOME_NAME = "Welcome — choose a preset folder to begin"


def welcome_preset() -> str:
    """The preset shown before the library is playing, or in place of it.

    Without this projectM draws its own idle preset -- the projectM "M" logo
    -- which is the first thing a new install shows, for as long as it takes
    to choose a preset folder. It is somebody else's branding on the first
    frame of a paid app. This one is ours: it samples
    assets/textures/bomsai.png through the texture search path, which is why
    that directory is on it.
    """
    try:
        with open(bundled_dir("assets", "welcome.milk"), encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        print(f"[renderer] no welcome preset: {e}", flush=True)
        return ""


def texture_override_dir() -> str:
    """Where a picture put in place of a stock texture lives.

    A separate directory that leads the search path, rather than overwriting
    the file in the texture pack. libprojectM scans its search paths in order
    and the first match by filename stem wins, so this has exactly the effect
    of replacing the stock texture -- and the pack, which is 8,000 files of
    other people's work downloaded separately, is never written to. Undoing a
    swap is deleting one file, and a reinstall of the pack cannot wipe it.
    """
    return os.path.join(user_data_dir(), "texture-overrides")


def texture_override_path(name: str) -> str:
    """The override in place for this texture name, or "" if there is none."""
    d = texture_override_dir()
    for ext in PICTURE_EXTENSIONS:
        p = os.path.join(d, name + ext)
        if os.path.isfile(p):
            return p
    return ""


def set_texture_override(name: str, image_path: str) -> str:
    """Copy `image_path` in as the texture called `name`. "" or the failure.

    The stem is what libprojectM matches on, so the copy keeps the source
    file's extension and any other override for the same name is removed
    first -- two files with the same stem would leave the winner to whichever
    the directory scan reached first.
    """
    import shutil
    ext = os.path.splitext(image_path)[1].lower()
    if ext not in PICTURE_EXTENSIONS:
        return (f"{ext or 'That file'} is not a picture this can use. "
                + _picture_formats_line())
    try:
        os.makedirs(texture_override_dir(), exist_ok=True)
        clear_texture_override(name)
        shutil.copyfile(image_path,
                        os.path.join(texture_override_dir(), name + ext))
        return ""
    except Exception as e:
        return str(e)


def clear_texture_override(name: str) -> bool:
    """Remove the override for one texture. True if there was one."""
    gone = False
    for ext in PICTURE_EXTENSIONS:
        p = os.path.join(texture_override_dir(), name + ext)
        try:
            os.remove(p)
            gone = True
        except OSError:
            pass
    return gone


def texture_overrides() -> list:
    """Names that currently have a picture standing in for them."""
    try:
        return sorted({os.path.splitext(f)[0]
                       for f in os.listdir(texture_override_dir())
                       if os.path.splitext(f)[1].lower()
                       in PICTURE_EXTENSIONS})
    except OSError:
        return []


# ─── numbers in preset code ──────────────────────────────────────────────────
#
# The Tweak tab moves ten named settings over ranges somebody chose. This is
# the other half: a slider on any number anywhere in the preset, the constants
# inside a shader included -- which a named list cannot reach at all.
# Plain text functions, out here rather than in the panel, because all of the
# difficulty is in the text handling and none of it needs a widget.

#: Characters that continue a number. An exponent is handled separately: `e`
#: also starts half the variable names in a preset.
_NUM_BODY = "0123456789."

#: A `-` belongs to the number only after one of these. `dx=-0.005` is a
#: negative number; the one in `q1-1` is a subtraction, and swallowing it
#: would turn dragging a slider into editing the expression.
_NUM_SIGN_AFTER = "=,(:[<>+-*/ \t\n"


def number_span_at(text: str, pos: int):
    """(start, end) of the number at `pos`, or None if there is not one.

    `pos` is a cursor position, not a token: somebody who double-clicked a
    number gets a selection and somebody who merely clicked gets a caret, and
    either end of a number is a place people click. Both ends are scanned.

    None rather than a guess when the digits are part of a name --
    `per_frame_1`, `q1`, `warp_2`. A slider on the 1 in `per_frame_1` would
    rewrite the line's key and silently drop the equation on it.
    """
    n = len(text)
    if not 0 <= pos <= n:
        return None
    start = pos
    while start > 0 and text[start - 1] in _NUM_BODY:
        start -= 1
    end = pos
    while end < n and text[end] in _NUM_BODY:
        end += 1
    if start == end:
        return None
    if start > 0 and (text[start - 1].isalpha() or text[start - 1] == "_"):
        return None
    if end < n and text[end] in "eE":
        e = end + 1
        if e < n and text[e] in "+-":
            e += 1
        if e < n and text[e].isdigit():
            while e < n and text[e].isdigit():
                e += 1
            end = e
    if end < n and (text[end].isalpha() or text[end] == "_"):
        return None
    if start > 0 and text[start - 1] == "-":
        before = text[start - 2] if start >= 2 else "\n"
        if before in _NUM_SIGN_AFTER:
            start -= 1
    try:
        float(text[start:end])
    except ValueError:
        return None
    return start, end


def splice(text: str, edits) -> str:
    """Apply (start, end, replacement) edits, every offset into `text` itself.

    Right to left, so no edit has to know that one after it changed the length
    of the text beyond it.
    """
    out = text
    for start, end, repl in sorted(edits, key=lambda e: e[0], reverse=True):
        out = out[:start] + repl + out[end:]
    return out


def format_like(original: str, value: float) -> str:
    """`value` spelled the way `original` was.

    A preset is a text file somebody may read, diff and pass on. Turning 0.960
    into 0.96 -- or into 0.9599999999999999 -- makes every edit noisier than
    it is; the Tweak tab refuses to rewrite lines nobody moved for the same
    reason.
    """
    s = original.strip()
    if "e" in s.lower():
        return f"{value:g}"
    if "." in s:
        return f"{value:.{len(s.split('.', 1)[1])}f}"
    return str(int(round(value)))


def slider_range(value: float):
    """A range to put a slider on, around a number that declares no bounds.

    Proportional to the value, so a slider over 0.001 is as usable as one over
    120, and centred on it, so whatever it was is always reachable again.

    ponytail: nothing can be dragged past twice itself, and a negative number
    cannot be dragged positive. Both are one keystroke in the editor and a
    re-pin -- a range box per slider is a lot of widget for that.
    """
    span = abs(value) or 1.0
    return value - span, value + span


def number_label(text: str, start: int, orig: str) -> str:
    """A short name for the slider on the number at `start`.

    The setting's name where there is one: `fDecay=0.960` is the easy case and
    the common one. A shader line has no name to take, and the text before a
    constant there is neither short nor unique -- two constants on one line
    share nearly all of it -- so those are named by where they are and what
    they were. Measured the other way round first: labelled by that shared
    lead, two sliders on one shader line were `\u2026_1=`shader_body { ret` and
    `\u2026_body { ret = 0.5*rad ` and there was no telling them apart.
    """
    line_start = text.rfind("\n", 0, start) + 1
    lead = text[line_start:start].strip()
    if lead.endswith("=") and len(lead) <= 22 and "`" not in lead:
        return lead[:-1]
    return f"line {text.count(chr(10), 0, start) + 1}: {orig}"


# ─── sprites ─────────────────────────────────────────────────────────────────
# A picture laid over whatever preset is playing, moved by a few lines of
# MilkDrop sprite code. libprojectM parses that code itself; all this end has
# to do is put the image somewhere the texture manager will find it and hand
# over the text.

#: Prefixed so a sprite image can never be mistaken for a preset's own
#: texture, and kept in a directory that goes LAST in the search path so even
#: a collision could not shadow one. The override directory deliberately leads
#: the path; this one deliberately trails it.
SPRITE_PREFIX = "spr_"

#: (key, label, per-frame code). The code is what `code_1=` gets, with `{sx}`
#: filled in per sprite -- a motion that drives sx every frame overwrites
#: whatever init set, so the chosen size has to be baked into the expression
#: rather than left in init_1 to be trampled.
#:
#: sy is always set equal to sx. projectM scales the sprite quad by the
#: picture's own width/height before it draws (MilkdropSprite.cpp, "First
#: aspect ratio"), so correcting for it here as well squashed a 2:1 picture
#: to 4:1 -- measured on screen, not read off the source.
SPRITE_MOTIONS = (
    ("still", "Still", ""),
    ("spin", "Spinning", "rot = time * 0.6;"),
    ("pulse", "Pulsing to the beat",
     "sx = {sx:.4f} * (0.80 + 0.35 * bass_att); sy = sx;"),
    ("drift", "Drifting about",
     "x = 0.5 + 0.22 * sin(time * 0.7); y = 0.5 + 0.13 * cos(time * 0.9);"),
    ("spinpulse", "Spinning and pulsing",
     "rot = time * 0.6; sx = {sx:.4f} * (0.80 + 0.35 * bass_att); "
     "sy = sx;"),
)

#: (key, label, MilkDrop blendmode). The labels are what these modes do in
#: projectM's own sprite shader, which is not what their MilkDrop names
#: suggest:
#:   0 ignores the picture's alpha channel entirely, so a cut-out PNG draws
#:     as a solid rectangle;
#:   2 adds the picture to what is behind it, which makes black vanish and
#:     bright parts glow;
#:   4 multiplies vertex alpha by texture alpha -- the only mode that keeps a
#:     PNG's own transparency. MilkDrop called it colour keying and projectM
#:     says so in a comment: "Milkdrop actually changed color keying to using
#:     texture alpha. The color key is ignored." Labelling it "black is clear"
#:     promised something no mode here does.
SPRITE_BLENDS = (
    ("normal", "Normal — keeps the picture's own transparency", 4),
    ("add", "Additive — black turns clear, bright parts glow", 2),
    ("solid", "Solid — the whole rectangle, transparency ignored", 0),
)

#: Blend keys written by an older build. "key" was mode 4 there and is mode 4
#: here; it just has an honest name now.
SPRITE_BLEND_ALIASES = {"key": "normal"}

#: More than this and the picture is the visualiser. projectM's own default
#: is 16; there is no reason to raise it.
MAX_SPRITES = 16


def sprite_dir() -> str:
    """Where pictures used as sprites are kept."""
    return os.path.join(user_data_dir(), "sprites")


def sprite_images() -> list:
    """(stem, full path) for every picture available as a sprite."""
    out = []
    try:
        for f in sorted(os.listdir(sprite_dir())):
            if os.path.splitext(f)[1].lower() in PICTURE_EXTENSIONS:
                out.append((os.path.splitext(f)[0],
                            os.path.join(sprite_dir(), f)))
    except OSError:
        pass
    return out


def add_sprite_image(image_path: str) -> tuple:
    """Copy a picture in for use as a sprite. (stem, "") or ("", why).

    The stem is what `img=` matches on, so it is derived from the file name,
    prefixed, and made unique -- dropping two different pictures both called
    "logo.png" in here would otherwise leave which one renders to whichever
    the directory scan reached first.
    """
    import re
    import shutil
    ext = os.path.splitext(image_path)[1].lower()
    if ext not in PICTURE_EXTENSIONS:
        return ("", f"{ext or 'That file'} is not a picture this can use. "
                    + _picture_formats_line())
    base = re.sub(r"[^a-z0-9]+", "_",
                  os.path.splitext(os.path.basename(image_path))[0].lower())
    base = base.strip("_") or "picture"
    taken = {s for s, _ in sprite_images()}
    stem = SPRITE_PREFIX + base
    n = 2
    while stem in taken:
        stem = f"{SPRITE_PREFIX}{base}_{n}"
        n += 1
    try:
        os.makedirs(sprite_dir(), exist_ok=True)
        shutil.copyfile(image_path, os.path.join(sprite_dir(), stem + ext))
        return (stem, "")
    except Exception as e:
        return ("", str(e))


def remove_sprite_image(stem: str) -> bool:
    """Delete the picture behind a sprite. True if there was one."""
    gone = False
    for ext in PICTURE_EXTENSIONS:
        try:
            os.remove(os.path.join(sprite_dir(), stem + ext))
            gone = True
        except OSError:
            pass
    return gone


def sprite_code(entry: dict) -> str:
    """The MilkDrop sprite definition for one configured sprite.

    Regenerated from scratch whenever anything about the sprite changes and
    the sprite recreated, rather than poking `projectm_sprite_set_var`: half
    the motions drive sx or x from per-frame code, so a variable set from
    outside would be overwritten before the next frame drew. Recreating is
    cheap here -- unlike a preset, there is no shader to compile.
    """
    motions = {k: c for k, _l, c in SPRITE_MOTIONS}
    blends = {k: b for k, _l, b in SPRITE_BLENDS}
    default_blend = SPRITE_BLENDS[0][2]
    stem = entry.get("image", "")
    size = float(entry.get("size", 0.5))
    alpha = float(entry.get("alpha", 1.0))
    want = entry.get("blend", "normal")
    want = SPRITE_BLEND_ALIASES.get(want, want)
    blend = blends.get(want, default_blend)
    code = motions.get(entry.get("motion", "still"), "")
    lines = [
        f"img={stem}",
        f"init_1=x=0.5; y=0.5; sx={size:.4f}; sy={size:.4f}; "
        f"a={alpha:.4f}; blendmode={blend}; done=0;",
    ]
    if code:
        lines.append("code_1=" + code.format(sx=size))
    return "\n".join(lines) + "\n"


# ─── ui ───────────────────────────────────────────────────────────────────────

# ── Qt binding ───────────────────────────────────────────────────────────────
# PySide6 first. It is LGPL, which is what allows shipping it inside a paid
# application; PyQt6 would need a commercial licence from Riverbank or the
# whole source released under the GPL. PyQt6 stays as the fallback because
# this same file is Bomsai Player's visualiser and that app is PyQt6
# throughout.
#
# Whichever binding is ALREADY imported wins. Two bindings in one process
# means two copies of the Qt libraries, which does not survive first contact.
if "PyQt6.QtCore" in sys.modules:
    from PyQt6 import QtCore, QtGui, QtWidgets
elif "PySide6.QtCore" in sys.modules:
    from PySide6 import QtCore, QtGui, QtWidgets
else:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PyQt6 import QtCore, QtGui, QtWidgets

QT_BINDING = QtCore.__name__.split(".")[0]

#: The one name that genuinely differs. Everything else this app touches is
#: spelled the same in both bindings, including the scoped enums.
Signal = getattr(QtCore, "Signal", None) or QtCore.pyqtSignal
pyqtSignal = Signal          # the shim modules beside this one still say this

# Bound here rather than imported by name, so the code below reads exactly as
# it did when this was `from PyQt6.QtWidgets import ...`. The tuples are the
# authoritative list of what this module and its shims use.
for _mod, _names in (
    (QtCore, ("Qt", "QObject", "QPoint", "QRect", "QSize", "QTimer", "QEvent",
              "QThread", "QUrl")),
    (QtGui,  ("QKeySequence", "QFont", "QFontDatabase", "QShortcut",
              "QTextCursor", "QSyntaxHighlighter", "QTextCharFormat",
              "QCursor", "QAction", "QActionGroup",
              "QIcon", "QImage", "QImageReader", "QPainter", "QPixmap",
              "QColor", "QDesktopServices")),
    (QtWidgets, ("QApplication", "QMenu", "QDialog", "QFormLayout",
                 "QVBoxLayout", "QHBoxLayout", "QPushButton", "QLabel",
                 "QSpinBox", "QDoubleSpinBox", "QComboBox", "QDialogButtonBox",
                 "QCheckBox", "QFrame", "QTableWidget", "QTableWidgetItem",
                 "QHeaderView", "QLineEdit", "QSizePolicy", "QMessageBox",
                 "QFileDialog", "QSlider", "QWidget", "QWidgetAction",
                 "QListWidget", "QListWidgetItem", "QAbstractItemView",
                 "QProgressBar", "QSystemTrayIcon", "QInputDialog",
                 "QTabWidget", "QScrollArea", "QPlainTextEdit")),
):
    for _n in _names:
        globals()[_n] = getattr(_mod, _n)


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
    close_context_menu = pyqtSignal()           # host should dismiss its menu
    playlist_changed   = pyqtSignal()           # order/contents changed (sort, delete, mode)
    no_presets         = pyqtSignal(str)        # nothing loaded; arg is the directory tried
    wallpaper_changed  = pyqtSignal(bool)       # window is/is not the desktop
    thumbnail_ready    = pyqtSignal(str)        # a preset's picture was written
    dual_changed       = pyqtSignal(bool)       # a second visual is/is not on
    focus_changed      = pyqtSignal(int)        # which visual the controls drive
    visual_changed     = pyqtSignal(int, bool)  # visual 0 or 1 is/is not drawn
    crossfade_changed  = pyqtSignal(float)      # the fader moved


# ─────────────────────────────────────────────────────────────────────────────
#  Toast notification  (top-right corner, auto-hides)
# ─────────────────────────────────────────────────────────────────────────────

def _asset_path(*parts) -> str:
    """A file that ships with the app (the logo, VERSION).

    Beside this module in a checkout; inside the bundle when frozen -- which is
    `sys._MEIPASS`, the `_internal` directory, NOT the executable's directory.
    """
    root = getattr(sys, "_MEIPASS", "") if getattr(sys, "frozen", False) else ""
    return os.path.join(root or os.path.dirname(os.path.abspath(__file__)), *parts)


# The product name, in the two forms everything else needs. Kept here because
# this file is shared with Bomsai Player: a literal spelled out at each use site
# is how the two copies drift. APP_SLUG is the filename-safe form -- it names the
# per-user data directory and the download User-Agent, so changing it moves
# where installed preset packs live.
APP_NAME = "BomsAI Visualizer"
APP_SLUG = "BomsAIVisualizer"


def user_data_dir() -> str:
    """Per-user data directory.

    Never the install directory: install.sh replaces it wholesale on upgrade
    and removes it on uninstall, and under Program Files it is not writable
    without elevation. Bulk data belongs in the OS's data location, which is
    a different one from the config location this app already uses.
    """
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser(
            "~\\AppData\\Local")
    elif sys.platform == "darwin":
        root = os.path.expanduser("~/Library/Application Support")
    else:
        root = os.environ.get("XDG_DATA_HOME") or os.path.expanduser(
            "~/.local/share")
    return os.path.join(root, APP_SLUG)


def default_pack_dir(cfg: dict) -> str:
    """Where the Get Presets dialog offers to install."""
    return os.path.join(user_data_dir(), "presets")


def app_icon():
    """The application's icon, for the window and the tray."""
    return QIcon(_asset_path("assets", "bomsai-logo.png"))


def app_version() -> str:
    """Version string from the VERSION file, or 'dev' when it is not there --
    which is the case in Bomsai Player, where this module also lives."""
    try:
        with open(_asset_path("VERSION")) as f:
            return f.read().strip() or "dev"
    except OSError:
        return "dev"


#: The three values `theme` takes in config.json. "system" is resolved through
#: Qt's own colour-scheme hint, which is fed by the Windows app-theme setting
#: and by the freedesktop colour-scheme portal on Linux -- so following the
#: desktop costs no platform code of our own.
THEMES = ("dark", "light", "system")

#: Every colour the Qt surface uses, per resolved theme. The visualiser itself
#: is untouched: it is an SDL window full of somebody else's preset, and the
#: toast is a translucent black overlay on top of it, so neither takes a theme.
_THEME_PALETTES = {
    "dark": {
        "bg":            "#141414",
        "fg":            "#e8e8e8",
        "muted":         "#9a9a9a",
        "bad":           "#e0706a",
        "link":          "#e0b44a",
        "code_kw":       "#7fb7d9",
        "code_num":      "#b5c46a",
        "field":         "#1e1e1e",
        "border":        "rgba(255, 255, 255, 40)",
        "sel_bg":        "#3a6ea5",
        "sel_fg":        "#ffffff",
        "button":        "#262626",
        "button_hover":  "#333333",
        "button_press":  "#1c1c1c",
        "button_border": "rgba(255, 255, 255, 45)",
        "focus_border":  "rgba(255, 255, 255, 90)",
        "spin":          "#2a2a2a",
        "spin_hover":    "#3a3a3a",
        "table":         "#1a1a1a",
        "grid":          "rgba(255, 255, 255, 25)",
        "header":        "#232323",
        "header_fg":     "#dddddd",
        "separator":     "rgba(255, 255, 255, 30)",
        "groove":        "rgba(255, 255, 255, 45)",
        "handle":        "#e8e8e8",
        "scroll_bg":     "#1a1a1a",
        #: 85, not 55. Composited over the trough that is 3.0:1 against
        #: it; 55 gave 2.0:1, under WCAG 1.4.11's floor for a control
        #: you have to see to use, and it was reported as looking like
        #: no handle at all.
        "scroll_handle": "rgba(255, 255, 255, 85)",
        "menu_bg":       "rgba(18, 18, 18, 235)",
        "menu_border":   "rgba(255, 255, 255, 55)",
        "menu_hover":    "rgba(255, 255, 255, 22)",
        "menu_sep":      "rgba(255, 255, 255, 25)",
        "list_bg":       "#14141c",
        "list_border":   "#2a2a38",
        "list_line":     "#1d1d27",
        "panel":         "#0c0c12",
        "arrow_down":    "arrow-down.png",
        "arrow_up":      "arrow-up.png",
    },
    "light": {
        "bg":            "#f5f5f5",
        "fg":            "#1c1c1c",
        "muted":         "#5f5f5f",
        "bad":           "#b3261e",
        "link":          "#8a5d00",
        "code_kw":       "#1c5f86",
        "code_num":      "#4a6112",
        "field":         "#ffffff",
        "border":        "rgba(0, 0, 0, 55)",
        "sel_bg":        "#3a6ea5",
        "sel_fg":        "#ffffff",
        "button":        "#e6e6e6",
        "button_hover":  "#dadada",
        "button_press":  "#c8c8c8",
        "button_border": "rgba(0, 0, 0, 60)",
        "focus_border":  "rgba(0, 0, 0, 120)",
        "spin":          "#e0e0e0",
        "spin_hover":    "#d0d0d0",
        "table":         "#ffffff",
        "grid":          "rgba(0, 0, 0, 30)",
        "header":        "#e9e9e9",
        "header_fg":     "#333333",
        "separator":     "rgba(0, 0, 0, 40)",
        "groove":        "rgba(0, 0, 0, 55)",
        "handle":        "#444444",
        "scroll_bg":     "#ededed",
        #: 109 for the same 3.0:1 the dark sheet gets; 70 gave 1.9:1.
        "scroll_handle": "rgba(0, 0, 0, 109)",
        "menu_bg":       "rgba(250, 250, 250, 242)",
        "menu_border":   "rgba(0, 0, 0, 60)",
        "menu_hover":    "rgba(0, 0, 0, 22)",
        "menu_sep":      "rgba(0, 0, 0, 30)",
        "list_bg":       "#ffffff",
        "list_border":   "#c8c8d0",
        "list_line":     "#e4e4ea",
        "panel":         "#ececed",
        "arrow_down":    "arrow-down-dark.png",
        "arrow_up":      "arrow-up-dark.png",
    },
}

#: What `theme` is set to right now. Dialogs are built fresh every time they
#: are opened, so they simply read this -- there is no live widget to restyle
#: and no signal to keep connected.
_app_theme = "dark"

#: Same sheet for every dialog. The visualiser is a black window with a
#: near-black menu over it, so dark is the default; "light" and "system" exist
#: because a desktop in a light theme should be allowed to say so.
_DIALOG_QSS_TEMPLATE = """
    QDialog, QWidget, QMessageBox { background: @bg@; color: @fg@; }
    QLabel, QCheckBox, QRadioButton { color: @fg@; background: transparent; }
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QAbstractItemView {
        background: @field@;
        color: @fg@;
        border: 1px solid @border@;
        border-radius: 4px;
        padding: 4px 6px;
        selection-background-color: @sel_bg@;
        selection-color: @sel_fg@;
    }
    /* A stylesheet replaces the native widget drawing wholesale, and that
       takes the spin and combo arrows with it -- an unstyled spin box comes
       out as two empty boxes. Qt draws no CSS border-triangle either (it
       fills the block), so these are real images, generated once and shipped
       in assets/. The light theme needs its own pair: the shipped arrows are
       #cbcbcb, which disappears on a light spin button. */
    QComboBox::drop-down { border: 0; width: 18px; }
    QComboBox::down-arrow, QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
        image: url(@ARROW_DOWN@); width: 9px; height: 6px;
    }
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
        image: url(@ARROW_UP@); width: 9px; height: 6px;
    }
    QSpinBox::up-button, QDoubleSpinBox::up-button,
    QSpinBox::down-button, QDoubleSpinBox::down-button {
        background: @spin@; border: 0; width: 16px;
    }
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
        background: @spin_hover@;
    }
    QComboBox QAbstractItemView { border: 1px solid @border@; }
    QPushButton {
        background: @button@;
        color: @fg@;
        border: 1px solid @button_border@;
        border-radius: 4px;
        padding: 5px 14px;
    }
    QPushButton:hover   { background: @button_hover@; }
    QPushButton:pressed { background: @button_press@; }
    QPushButton:default { border-color: @focus_border@; }
    QTableWidget {
        background: @table@;
        color: @fg@;
        gridline-color: @grid@;
        selection-background-color: @sel_bg@;
        selection-color: @sel_fg@;
    }
    QHeaderView::section {
        background: @header@; color: @header_fg@; border: 0; padding: 4px;
    }
    QTableCornerButton::section { background: @header@; border: 0; }
    QFrame[frameShape="4"], QFrame[frameShape="5"] { color: @separator@; }
    QSlider::groove:horizontal {
        height: 4px; background: @groove@; border-radius: 2px;
    }
    QSlider::handle:horizontal {
        background: @handle@; width: 12px; margin: -5px 0; border-radius: 6px;
    }
    QScrollBar:vertical, QScrollBar:horizontal { background: @scroll_bg@; border: 0; }
    QScrollBar::handle { background: @scroll_handle@; border-radius: 4px; }
    /* min-height/min-width are not decoration. A stylesheet-drawn handle has
       no floor of its own -- Qt's PM_ScrollBarSliderMin stops applying once
       the sub-control is drawn from a sheet -- so it is sized purely by the
       page/range ratio. On the ten thousand presets this browser is built for
       that comes out at ONE pixel, which the 4px radius then rounds away to
       nothing, and the bar reads as having no handle at all. */
    QScrollBar::handle:vertical { min-height: 28px; }
    QScrollBar::handle:horizontal { min-width: 28px; }
    QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
    QToolTip { background: @field@; color: @fg@; border: 1px solid @border@; }
"""

#: The right-click menu floats over the visualiser rather than sitting in a
#: dialog, so it keeps its own translucent sheet.
_MENU_QSS_TEMPLATE = """
    QMenu {
        background: @menu_bg@;
        color: @fg@;
        border: 1px solid @menu_border@;
        border-radius: 6px;
        padding: 5px 3px;
        font-size: 13px;
    }
    QMenu::item { padding: 7px 24px; border-radius: 4px; min-width: 180px; }
    QMenu::item:selected { background: @menu_hover@; }
    QMenu::separator { background: @menu_sep@; height: 1px; margin: 4px 10px; }
"""

#: The preset browser is its own window with its own list, so it gets its own
#: sheet -- built here so every themed surface lives in one file.
_BROWSER_QSS_TEMPLATE = """
    QWidget { background: @panel@; color: @fg@; }
    QLabel#title {
        color: @fg@; font-size: 14px; font-weight: bold;
        padding: 8px 10px; background: @header@;
    }
    QListWidget {
        background: @list_bg@; border: 1px solid @list_border@;
        font-size: 12px; outline: none;
    }
    QListWidget::item { padding: 4px 8px; border-bottom: 1px solid @list_line@; }
    QListWidget::item:selected { background: @sel_bg@; color: @sel_fg@; }
    QPushButton {
        background: @button@; color: @fg@;
        border: 1px solid @button_border@; border-radius: 5px;
        padding: 4px 10px;
    }
    QPushButton:hover { background: @button_hover@; }
    QPushButton:checked {
        background: @sel_bg@; color: @sel_fg@; border-color: @focus_border@;
    }
    QLineEdit#search {
        background: @list_bg@; color: @fg@;
        border: 1px solid @list_border@; border-radius: 5px;
        margin: 0 8px 6px 8px; padding: 5px 8px; font-size: 12px;
    }
    QLineEdit#search:focus { border-color: @focus_border@; }
    /* The tweak drop-down. Without these the tabs, sliders and scroll bars
       come out in the platform style over a dark panel -- the same trap the
       dialog sheet documents: a stylesheet replaces native drawing, so a
       widget the sheet does not mention is a widget half-drawn. */
    QTabWidget::pane {
        border: 1px solid @list_border@; border-radius: 5px; top: -1px;
    }
    QTabBar::tab {
        background: @button@; color: @fg@;
        border: 1px solid @button_border@; border-bottom: none;
        border-top-left-radius: 5px; border-top-right-radius: 5px;
        padding: 4px 12px; margin-right: 2px; font-size: 12px;
    }
    QTabBar::tab:selected { background: @sel_bg@; color: @sel_fg@; }
    QTabBar::tab:hover:!selected { background: @button_hover@; }
    QScrollArea { border: none; }
    QSlider::groove:horizontal {
        height: 4px; background: @groove@; border-radius: 2px;
    }
    QSlider::handle:horizontal {
        background: @handle@; width: 12px; margin: -5px 0; border-radius: 6px;
    }
    QSlider::handle:horizontal:disabled { background: @button_border@; }
    QScrollBar:vertical, QScrollBar:horizontal { background: @scroll_bg@; border: 0; }
    QScrollBar::handle { background: @scroll_handle@; border-radius: 4px; }
    /* min-height/min-width are not decoration. A stylesheet-drawn handle has
       no floor of its own -- Qt's PM_ScrollBarSliderMin stops applying once
       the sub-control is drawn from a sheet -- so it is sized purely by the
       page/range ratio. On the ten thousand presets this browser is built for
       that comes out at ONE pixel, which the 4px radius then rounds away to
       nothing, and the bar reads as having no handle at all. */
    QScrollBar::handle:vertical { min-height: 28px; }
    QScrollBar::handle:horizontal { min-width: 28px; }
    QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
"""


def resolve_theme(theme: str = None) -> str:
    """Turn a configured theme into "dark" or "light" -- never "system".

    "system" is answered by Qt's colour-scheme hint rather than by reading the
    registry or a gsettings key here: Qt 6.5+ already tracks the Windows
    app-theme setting and the freedesktop colour-scheme portal, so following
    the desktop costs no platform code. Anything Qt cannot answer falls back
    to dark, which is what this app looked like before the setting existed.
    """
    theme = (theme or _app_theme or "dark").lower()
    if theme in ("dark", "light"):
        return theme
    try:
        scheme = QApplication.instance().styleHints().colorScheme()
        return "light" if scheme == Qt.ColorScheme.Light else "dark"
    except Exception:
        return "dark"          # Qt < 6.5, or no QApplication yet


def set_app_theme(theme: str):
    """Remember the configured theme, and let Qt's own chrome follow it.

    A stylesheet cannot reach everything: on Windows 11 the dialog title bar
    is drawn by the OS, and a dark dialog under a white title bar looks
    broken. styleHints().setColorScheme() is what fixes that, and it is also
    what makes the native file dialog behind "Browse..." match. Qt < 6.8 has
    no setter, hence the guard.
    """
    global _app_theme
    _app_theme = theme if theme in THEMES else "dark"
    try:
        hints = QApplication.instance().styleHints()
        hints.setColorScheme({
            "dark":  Qt.ColorScheme.Dark,
            "light": Qt.ColorScheme.Light,
        }.get(_app_theme, Qt.ColorScheme.Unknown))
    except Exception:
        pass


def apply_window_theme(handle, theme: str = None) -> bool:
    """Match a window's OS-drawn title bar and border to the app theme.

    The frame around the visualiser is not ours to paint -- Windows draws it,
    from its own light/dark setting -- so a dark visualiser could sit under a
    white title bar with nothing the stylesheet could do about it. DWM's
    immersive-dark-mode attribute is the override. It is applied to the SDL
    window, which Qt's setColorScheme cannot reach: that one only covers
    windows Qt itself created (the dialogs).

    Attribute 20 is the documented DWMWA_USE_IMMERSIVE_DARK_MODE (Windows 10
    20H1 and later); 19 was the undocumented spelling on 1809-1909. Passing
    the wrong one fails rather than misbehaving, so both are tried in turn.

    Linux has no equivalent and needs none: the frame belongs to the window
    manager, which paints it from the desktop's own theme. Returns False
    there, and on any Windows too old for either attribute.
    """
    if sys.platform != "win32" or not handle:
        return False
    dark = ctypes.c_int(1 if resolve_theme(theme) == "dark" else 0)
    try:
        dwm = ctypes.windll.dwmapi
        user32 = ctypes.windll.user32
    except (AttributeError, OSError):
        return False
    hwnd = ctypes.c_void_p(int(handle))
    applied = False
    for attribute in (20, 19):
        if dwm.DwmSetWindowAttribute(hwnd, ctypes.c_int(attribute),
                                     ctypes.byref(dark),
                                     ctypes.sizeof(dark)) == 0:
            applied = True
            break
    if applied:
        # The attribute alone repaints the frame on the next activation, which
        # from the user's side looks like the setting not having worked.
        # SWP_FRAMECHANGED forces it now. NOMOVE|NOSIZE|NOZORDER|NOACTIVATE
        # so nothing else about the window moves.
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001
                            | 0x0004 | 0x0010)
    return applied


class _OwnedByVisuals(QObject):
    """Give every Qt window the visualiser as its native owner, on Windows.

    Windows keeps an owned window above its owner and never behind it. Nothing
    here had an owner: the dialogs are parented to a QWidget that is never
    shown, so it has no native handle and Qt sets none -- every dialog came up
    with owner=0, and one click on the visuals dropped it behind them with the
    taskbar entry the only way back. Worst on a fresh install, where Get
    Presets opens *behind* the window it exists to explain. Measured on 11 Pro
    26200: with the owner set, the same click leaves the dialog on top.

    An event filter rather than a line at each of the nine places a dialog is
    made -- they are spread over three files, a static QMessageBox call among
    them, and the tenth would be written without it. Linux needs none of this:
    the window manager keeps a dialog above the window it came from.

    The owner is set from a zero-delay timer, not straight from the show
    event. Setting it inside the event does take -- reading it back in the
    same call returns the handle just written -- and then Qt finishes raising
    the dialog modally and the owner is 0 again. Deferring by one turn of the
    event loop is the same trick, and for the same reason, as
    TweakPanel._rebuild_later.
    """

    _GWLP_HWNDPARENT = -8

    def __init__(self, host):
        super().__init__()
        self._host = host          # the Renderer, for its SDL window handle

    #: Real windows and dialogs only. isWindow() is also true for a QMenu,
    #: and giving the context menu an owner fights the pointer grab that keeps
    #: it up: it flickered open and shut under the cursor, and then stopped
    #: appearing at all until the app was restarted. The toast is a Tool and
    #: already sits on top through its own WindowStaysOnTopHint.
    _OWNABLE = (int(Qt.WindowType.Window), int(Qt.WindowType.Dialog))

    def eventFilter(self, obj, event):
        if not (isinstance(obj, QWidget) and obj.isWindow()
                and (int(obj.windowFlags()) & int(Qt.WindowType.WindowType_Mask))
                    in self._OWNABLE):
            return False
        if event.type() == QEvent.Type.Show:
            QTimer.singleShot(0, lambda w=obj: self._set_owner(w, True))
        elif event.type() == QEvent.Type.WindowStateChange:
            # Minimising is the other way an owned window has to talk to its
            # owner's thread, and un-minimising one is as dangerous as closing
            # it: Windows hands activation back to the owner synchronously,
            # and the owner's pump is the render thread. Measured 15 Sep 2026
            # with the owner left on -- `showNormal()` on the preset browser
            # never returned. The Qt thread stopped inside it, the render
            # thread stopped advancing presets, and the window was left at the
            # iconic placeholder rectangle (0,1004 160x28: a title bar and no
            # content). The same sequence against a probe whose owner is on
            # the calling thread restores correctly, which is what places the
            # blame on the ownership rather than on Qt.
            #
            # So the window gives the owner back on the way down, and takes it
            # again on the way up -- deferred, for the same reason `Show` is.
            # Nothing is lost while it is minimised: there are no visuals to
            # be dropped behind.
            if obj.isMinimized():
                self._set_owner(obj, False)
            else:
                QTimer.singleShot(0, lambda w=obj: self._set_owner(w, True))
        elif event.type() == QEvent.Type.Close:
            # Straight away, NOT deferred: this has to happen while the window
            # is still up. Measured -- on accept() the order is
            # ActivationChange, Close, WindowDeactivate, Hide, and only the
            # first three arrive while the native window is still visible.
            # See _set_owner for why closing an owned window is dangerous here.
            self._set_owner(obj, False)
        return False

    def _set_owner(self, w, own: bool):
        """Own `w` to the visualiser, or hand it back.

        **The owner is cleared before the window closes, and that is not
        tidying up.** The visualiser's window belongs to the render thread --
        SDL created it there -- so an owned dialog on the Qt thread has an
        owner whose message pump is somebody else's. Closing such a window
        makes Windows transfer activation to the owner *synchronously, on the
        owner's thread*, and the render thread only pumps between frames: it
        is inside `projectm_opengl_render_frame` the rest of the time, which
        on an unlucky preset is a long time, and the driver call it is inside
        can in turn be waiting on the window state the Qt thread is now
        holding. That is a circular wait, and it hangs the whole application
        with no log and no traceback -- reported from a real install on
        2026-09-14, and caught with faulthandler: main thread parked in
        QDialog.accept, render thread parked in render_frame, unchanged
        across two dumps ten seconds apart.

        Setting the owner is safe (it is one call on our own window, and the
        owner's thread is not consulted); it is the calls that make Windows
        *move activation* that are not, and there are two of them -- closing
        an owned window and un-minimising one. So the relationship exists for
        exactly as long as it is useful -- while the dialog is up and could be
        clicked behind the visuals -- and is gone before either.
        """
        hwnd = getattr(self._host, "_native_handle", None)
        # A minimised window must not be owned, and the check belongs here
        # rather than at each caller: clearing the owner of a window that has
        # just been minimised makes Windows show it again, Qt reports a Show,
        # and the Show branch owned it straight back. Measured 15 Sep 2026 --
        # `Show min=True` one line after the disown, every time.
        if own and (not hwnd or not w.isVisible() or w.isMinimized()):
            return                 # no SDL window yet, minimised, or dismissed
        try:
            u = _ctypes.windll.user32
            u.SetWindowLongPtrW.restype  = _ctypes.c_void_p
            u.SetWindowLongPtrW.argtypes = [_ctypes.c_void_p, _ctypes.c_int,
                                            _ctypes.c_void_p]
            u.SetWindowLongPtrW(_ctypes.c_void_p(int(w.winId())),
                                self._GWLP_HWNDPARENT,
                                _ctypes.c_void_p(int(hwnd) if own else 0))
        except Exception as e:
            verb = "own" if own else "disown"
            print(f"[ui] could not {verb} {type(w).__name__}"
                  f" to the visualiser: {e}", flush=True)


def keep_dialogs_above_visuals(app, host):
    """Install _OwnedByVisuals on the application. Windows only; no-op elsewhere."""
    if sys.platform != "win32":
        return None
    f = _OwnedByVisuals(host)
    app.installEventFilter(f)
    return f          # returned so the caller keeps it alive


def theme_colour(key: str, theme: str = None) -> str:
    """One palette entry, for the few labels that set their own colour."""
    return _THEME_PALETTES[resolve_theme(theme)].get(key, "")


class MilkHighlighter(QSyntaxHighlighter):
    """Colour a .milk file in the Code tab.

    A .milk is `key=value` per line, and the shader bodies are values like
    every other line -- `warp_1=` through `warp_20=` concatenate into one HLSL
    program. So the key is coloured separately from what follows it, and the
    keyword rule is applied only where the value is *code*, which `preset_bin`
    already knows: `sin` in a warp shader is a function, `sin` in
    `sTitle=cos and sin` is a word in a title.
    """

    #: HLSL types and the built-ins Milkdrop's own shaders lean on, plus the
    #: per-frame equation keywords. One list on purpose: the two languages are
    #: never in the same *value*, so there is nothing to tell apart.
    _WORDS = (
        "float float2 float3 float4 float2x2 float3x3 float4x4 half int bool "
        "void return if else while for sampler sampler2D sampler3D "
        "tex2D tex3D tex2Dbias lerp saturate frac fmod pow exp exp2 log log2 "
        "sqrt rsqrt abs sign min max clamp step smoothstep length normalize "
        "dot cross reflect ddx ddy sin cos tan asin acos atan atan2 "
        "floor ceil round trunc mul div rand above below equal bnot bor band "
        "sigmoid sqr rad deg megabuf gmegabuf loop while exec2 exec3"
    ).split()

    @staticmethod
    def _build_rules():
        """Not cached on the class: the formats hold resolved colours, and a
        cached set would still be in the old palette after a theme change."""
        import re

        def fmt(colour, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(colour))
            if italic:
                f.setFontItalic(True)
            return f

        # Order matters: comments are applied last so a // inside a shader
        # body wins over anything coloured underneath it. `code_only` says
        # whether the rule may run on a plain `key=value` line.
        return [
            (re.compile(r"\b\d+\.?\d*(?:[eE][-+]?\d+)?\b"),
             fmt(theme_colour("code_num")), False),
            (re.compile(r"\b(?:%s)\b" % "|".join(MilkHighlighter._WORDS)),
             fmt(theme_colour("code_kw")), True),
            (re.compile(r"//[^\n]*"),
             fmt(theme_colour("muted"), italic=True), False),
        ]

    def __init__(self, document):
        super().__init__(document)
        import re
        self._value_rules = self._build_rules()
        self._key = re.compile(r"^[ \t]*([A-Za-z_][A-Za-z_0-9]*)[ \t]*=")
        self._section = re.compile(r"^\[[^\]]*\][ \t]*$")
        self._key_fmt = QTextCharFormat()
        self._key_fmt.setForeground(QColor(theme_colour("link")))
        self._sec_fmt = QTextCharFormat()
        self._sec_fmt.setForeground(QColor(theme_colour("muted")))

    def highlightBlock(self, text: str):
        if self._section.match(text):
            self.setFormat(0, len(text), self._sec_fmt)
            return
        m = self._key.match(text)
        if not m:
            return                      # not a key=value line; leave it plain
        self.setFormat(m.start(1), m.end(1) - m.start(1), self._key_fmt)
        start = m.end()
        is_code = bool(preset_bin(text))
        for pattern, fmt, code_only in self._value_rules:
            if code_only and not is_code:
                continue
            for hit in pattern.finditer(text, start):
                self.setFormat(hit.start(), hit.end() - hit.start(), fmt)


def _build_qss(template: str, theme: str = None) -> str:
    pal = _THEME_PALETTES[resolve_theme(theme)]
    out = template
    for key, value in pal.items():
        out = out.replace("@%s@" % key, value)
    for token, name in (("@ARROW_DOWN@", pal["arrow_down"]),
                        ("@ARROW_UP@",   pal["arrow_up"])):
        # Absolute paths, forward slashes: Qt's stylesheet parser reads a
        # backslash as an escape, so a Windows path has to be normalised.
        out = out.replace(token,
                          _asset_path("assets", name).replace("\\", "/"))
    return out


def dialog_qss(theme: str = None) -> str:
    """The dialog sheet for the current theme, or for the one named."""
    return _build_qss(_DIALOG_QSS_TEMPLATE, theme)


def menu_qss(theme: str = None) -> str:
    return _build_qss(_MENU_QSS_TEMPLATE, theme)


def browser_qss(theme: str = None) -> str:
    return _build_qss(_BROWSER_QSS_TEMPLATE, theme)


#: Kept as a name because Bomsai Player's shims import it. It is the dark
#: sheet, which is what it has always been; anything theme-aware calls
#: dialog_qss() instead.
DIALOG_QSS = dialog_qss("dark")


class ToastLabel(QLabel):
    """
    Frameless always-on-top label that appears in the top-right corner of the
    visualiser window for a short duration, then hides.

    It is a separate top-level window (the visuals are an SDL surface, with no
    Qt widget over them to draw into), so where it goes has to be worked out
    from the SDL window's geometry -- see set_anchor.
    """
    def __init__(self):
        super().__init__()
        #: Callable returning the visualiser's (x, y, w, h), or None. Unset
        #: means the primary screen, which is where this used to always go.
        self._anchor = None
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

    def set_anchor(self, rect_fn):
        """Give the toast something to sit inside: a callable returning the
        visualiser window's (x, y, w, h). Called on the Qt thread every time a
        message is shown, so it must not touch SDL directly."""
        self._anchor = rect_fn

    def show_message(self, text: str, duration_ms: int = 2000):
        self.setText(text)
        self.adjustSize()
        screen = QApplication.primaryScreen().availableGeometry()
        rect = None
        if self._anchor is not None:
            try:
                rect = self._anchor()
            except Exception:
                rect = None
        if rect:
            wx, wy, ww, wh = rect
            # Top-right of the window, 20px in. max() keeps it from hanging off
            # the left when the window is narrower than the message.
            x = max(wx + 8, wx + ww - self.width() - 20)
            y = wy + 20
        else:
            x = screen.right() - self.width() - 20
            y = screen.top()   + 20
        # Clamped to the screen regardless: an embedded build parks its SDL
        # window at -10000 until the host adopts it, and a toast sent in that
        # window would otherwise be invisible.
        x = min(max(x, screen.left()), screen.right() - self.width())
        y = min(max(y, screen.top()), screen.bottom() - self.height())
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
        # A copy, not the caller's dict: every caller does cfg.update(...) after
        # exec() returns, and changed_config() has to compare against the values
        # the dialog opened with, not the ones it has just written back.
        self._opened_with = dict(cfg)
        # The registry (or ~/.config/autostart) is the truth about whether
        # this app starts at login, not config.json -- somebody may have
        # removed the entry from outside. Seeding the baseline with the real
        # answer is also what keeps changed_config() from rewriting it on
        # every OK.
        self._opened_with["autostart"] = autostart_enabled()
        self.setWindowTitle("Visualizer Settings")
        self.setMinimumWidth(400)
        self.setStyleSheet(dialog_qss())

        # Not parented to the dialog: the rows go inside a scroll area at the
        # end of __init__, and the buttons stay outside it. See there.
        layout = QFormLayout()
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

        # Which visuals are on, and which of them the rows below are for.
        # One pair of boxes doing both jobs: ticking one switches it on and
        # points the settings at it, which is the same thing you meant either
        # way -- nobody ticks "visual 2" to leave it alone.
        #
        # There is no "second visual quality" row any more, and no strength.
        # Quality is THIS row, read against whichever visual is ticked, and
        # the strength was replaced by the crossfader on the right-click menu,
        # where you can see what it is doing while you move it.
        self._editing = 1 if cfg.get("dual_focus") else 0
        self._quality_by_visual = [cfg.get("quality", "high"),
                                   cfg.get("dual_quality", "low")]
        self._visual_box = []
        picker = QHBoxLayout()
        picker.setContentsMargins(0, 0, 0, 0)
        picker.setSpacing(12)
        for which in (0, 1):
            box = QCheckBox(f"Visual {which + 1}")
            box.setChecked(bool(cfg.get("visual1_enabled", True)) if not which
                           else bool(cfg.get("dual_enabled", False)))
            box.setToolTip("Tick to switch it on and set it up below.")
            box.clicked.connect(
                lambda checked, i=which: self._pick_visual(i, checked))
            self._visual_box.append(box)
            picker.addWidget(box)
        picker.addStretch()
        layout.addRow("Visuals:", picker)

        # Quality, of whichever visual is being edited.
        self._quality = QComboBox()
        for q in ["low", "medium", "high", "ultra"]:
            self._quality.addItem(q.capitalize(), q)
        self._quality_label = QLabel()
        layout.addRow(self._quality_label, self._quality)
        self._show_visual_settings()

        # Theme. Dark is the default and was the only option: the
        # visualiser is a black window, and a light dialog beside it is a
        # flashbang. "Follow system" is for desktops that are not.
        self._theme = QComboBox()
        for value, label in (("dark",   "Dark"),
                             ("light",  "Light"),
                             ("system", "Follow system")):
            self._theme.addItem(label, value)
        idx = self._theme.findData(cfg.get("theme", "dark"))
        if idx >= 0:
            self._theme.setCurrentIndex(idx)
        layout.addRow("Theme:", self._theme)

        # Soft cut duration
        self._soft_cut = QDoubleSpinBox()
        self._soft_cut.setRange(0.5, 10.0)
        self._soft_cut.setSingleStep(0.5)
        self._soft_cut.setDecimals(1)
        self._soft_cut.setValue(cfg.get("soft_cut_duration", 3.0))
        self._soft_cut.setSuffix("  sec")
        layout.addRow("Blend duration:", self._soft_cut)

        self._start_clean = QCheckBox("Start each visual on a black frame")
        self._start_clean.setChecked(bool(cfg.get("preset_start_clean")))
        self._start_clean.setToolTip(
            "Off, a new visual inherits the last frame of the one before and "
            "grows out of it. On, it starts from nothing — which suits a dark "
            "visual following a bright one.")
        layout.addRow("Hand-over:", self._start_clean)

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
        # Ask the capture layer, not sounddevice: on Windows the list is
        # WASAPI loopback endpoints, which sounddevice cannot enumerate.
        self._audio_dev.addItem(AudioCapture.AUTO_LABEL, "")
        try:
            labels = AudioCapture.device_labels()
            for name in AudioCapture.list_devices():
                self._audio_dev.addItem(labels.get(name, name), name)
        except Exception:
            pass
        _dev = cfg.get("audio_device", "")
        # The item text is a description now, so the stored name is matched
        # against the item *data*. A name from a device that is not plugged in
        # right now still has to survive, hence the free-text fallback.
        _idx = self._audio_dev.findData(_dev)
        if _idx >= 0:
            self._audio_dev.setCurrentIndex(_idx)
        else:
            self._audio_dev.setCurrentText(_dev or AudioCapture.AUTO_LABEL)
        layout.addRow(AudioCapture.device_label(), self._audio_dev)

        # Input sensitivity. Same 0.1x-8x range as the right-click menu's
        # slider, and the same config key, so the two always agree.
        self._gain = QSlider(Qt.Orientation.Horizontal)
        self._gain.setRange(10, 800)
        self._gain.setValue(int(float(cfg.get("audio_gain", 1.0)) * 100))
        _gain_lbl = QLabel(f"{self._gain.value()}%")
        _gain_lbl.setMinimumWidth(46)
        self._gain.valueChanged.connect(lambda v: _gain_lbl.setText(f"{v}%"))
        _gain_row = QHBoxLayout()
        _gain_row.setContentsMargins(0, 0, 0, 0)
        _gain_row.addWidget(self._gain, 1)
        _gain_row.addWidget(_gain_lbl)
        layout.addRow("Input sensitivity:", _gain_row)

        # Idle pause. Scoped to wallpaper mode by default: a visualiser
        # somebody is watching must not freeze because the track ended --
        # projectM animates without audio, and stopping it looks like a crash.
        self._idle_mode = QComboBox()
        for value, label in IDLE_MODES:
            self._idle_mode.addItem(label, value)
        idx = self._idle_mode.findData(cfg.get("pause_when_idle", "wallpaper"))
        if idx >= 0:
            self._idle_mode.setCurrentIndex(idx)
        self._idle_mode.setToolTip(
            "Stop drawing while nothing is playing, or while a full-screen "
            "application is running")
        layout.addRow("Pause when idle:", self._idle_mode)

        self._idle_secs = QSpinBox()
        self._idle_secs.setRange(0, 3600)
        self._idle_secs.setSpecialValueText("Never on silence")
        self._idle_secs.setValue(int(cfg.get("idle_silence_seconds", 20)))
        self._idle_secs.setSuffix("  sec of silence")
        layout.addRow("Pause after:", self._idle_secs)

        # Start at login. Applied to the OS in accept(), because the
        # checkbox is a view of the registry rather than of config.json.
        self._autostart = QCheckBox("Start automatically when I log in")
        self._autostart.setChecked(self._opened_with["autostart"])
        layout.addRow("At login:", self._autostart)

        self._autostart_wp = QCheckBox("…as the desktop wallpaper")
        self._autostart_wp.setChecked(bool(cfg.get("autostart_wallpaper", False)))
        self._autostart_wp.setEnabled(self._autostart.isChecked())
        self._autostart.toggled.connect(self._autostart_wp.setEnabled)
        if wallpaper_support()[0]:
            layout.addRow("", self._autostart_wp)

        # The rows scroll; the buttons do not.
        #
        # Twenty-one rows come to about 860px, and a QFormLayout set straight
        # on the dialog makes that a MINIMUM -- the window manager then refuses
        # to shrink it. On a 1366x768 laptop, which is most of the machines
        # this is sold to, OK and Cancel were below the bottom of the screen
        # and there was no way to reach them; the preset folder is the first
        # thing a new install has to set, so that is not a corner case.
        form = QWidget()
        form.setLayout(layout)
        scroll = QScrollArea()
        scroll.setWidget(form)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 16, 16)
        outer.setSpacing(10)
        outer.addWidget(scroll)
        outer.addWidget(sep)
        outer.addWidget(btns)

        # Size it off the FORM, not off self.sizeHint(). A QScrollArea's own
        # hint ignores how big its widget wants to be -- ask the dialog and it
        # says 628x476 for a form that needs 810x838, so it opened narrower
        # than the rows (a horizontal scrollbar it never needs) and shorter
        # than the screen had room for.
        #
        # And activate the layout before asking, or the answer is stale: an
        # unactivated QFormLayout reports 782 here where the settled figure is
        # 786, so the dialog opened four pixels short of its own rows and came
        # up with a vertical scroll bar down every row of a dialog that fits.
        # Measured on Windows 2026-09-15 -- scroll range 0..4, and re-asking
        # after show() gives the larger number. ensurePolished() does not do
        # it; only activate() does.
        layout.activate()
        need = form.minimumSizeHint()
        m = outer.contentsMargins()
        wide = need.width() + m.left() + m.right() + \
            scroll.verticalScrollBar().sizeHint().width() + 2
        tall = need.height() + m.top() + m.bottom() + outer.spacing() * 2 + \
            sep.sizeHint().height() + btns.sizeHint().height()
        # Not a resize: the rows genuinely cannot be drawn narrower, and
        # letting the window manager squeeze them is what brings the
        # horizontal scrollbar back.
        self.setMinimumWidth(wide)
        self.resize(wide, min(tall, max(320, self._screen_height() - 80)))

    def _screen_height(self) -> int:
        """Usable height of whatever screen this is opening on.

        Falls back to the form's own height, so a headless run -- the test
        suite -- caps at nothing rather than at zero.
        """
        for get in (lambda: self.screen().availableGeometry().height(),
                    lambda: QApplication.primaryScreen()
                    .availableGeometry().height()):
            try:
                h = get()
                if h:
                    return h
            except Exception:
                continue
        return 1 << 20

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

    def _pick_visual(self, which: int, checked: bool):
        """A tick switches that visual on AND points the rows at it.

        Both off is a black window, so the last one standing goes back on --
        silently, because the box springing back under the cursor says it
        more plainly than a message box would.
        """
        other = self._visual_box[1 - which]
        if not checked and not other.isChecked():
            self._visual_box[which].setChecked(True)
            return
        if not checked:
            # Switched off. Nothing to set up on a visual that is not there,
            # so the rows follow the one that is.
            self._set_editing(1 - which)
            return
        self._set_editing(which)

    def _set_editing(self, which: int):
        """Point the per-visual rows at visual 1 (0) or visual 2 (1)."""
        which = 1 if which else 0
        # Keep what is on screen before swapping it out, or moving between
        # the two would quietly hand both of them the same value.
        self._quality_by_visual[self._editing] = \
            self._quality.currentData() or "high"
        self._editing = which
        self._show_visual_settings()

    def _show_visual_settings(self):
        """Load the editing visual's values into the rows that are per-visual."""
        self._quality_label.setText(f"Visual {self._editing + 1} quality:")
        idx = self._quality.findData(self._quality_by_visual[self._editing])
        if idx >= 0:
            self._quality.setCurrentIndex(idx)
        self._quality.setToolTip(
            "Applies at once." if not self._editing else
            "Applies next time the second visual is switched on.")

    def get_config(self) -> dict:
        self._quality_by_visual[self._editing] = \
            self._quality.currentData() or "high"
        quality = self._quality_by_visual[0]
        mx, my = QUALITY_MESH.get(quality, (64, 48))
        return {
            "preset_dir":           self._preset_dir.text().strip(),
            "texture_dir":          self._texture_dir.text().strip(),
            "preset_duration":      self._dur.value(),
            "preset_random_extra":  self._rnd.value(),
            "fps":                  self._fps.value(),
            "quality":              quality,
            "dual_quality":         self._quality_by_visual[1],
            "visual1_enabled":      self._visual_box[0].isChecked(),
            "dual_enabled":         self._visual_box[1].isChecked(),
            "theme":                self._theme.currentData() or "dark",
            "mesh_x":               mx,
            "mesh_y":               my,
            "soft_cut_duration":    round(self._soft_cut.value(), 1),
            "preset_start_clean":   self._start_clean.isChecked(),
            "beat_sensitivity":     round(self._beat.value(), 1),
            "hard_cuts_enabled":    self._hard_cuts.isChecked(),
            "hard_cut_sensitivity": round(self._hcs.value(), 1),
            "audio_device":         self._audio_device_value(),
            "audio_gain":           round(self._gain.value() / 100.0, 2),
            "pause_when_idle":      self._idle_mode.currentData() or "wallpaper",
            "idle_silence_seconds": self._idle_secs.value(),
            "autostart":            self._autostart.isChecked(),
            "autostart_wallpaper":  self._autostart_wp.isChecked(),
        }

    def accept(self):
        """Write the login entry before closing.

        A side effect in a dialog, on purpose: this checkbox shows the state
        of the registry, not of config.json, and both callers of this dialog
        (the app and the screensaver's /c branch) would otherwise each need
        their own copy of the same three lines.
        """
        if "autostart" in self.changed_config() or (
                self._autostart.isChecked()
                and self._autostart_wp.isChecked()
                != bool(self._opened_with.get("autostart_wallpaper", False))):
            why = set_autostart(self._autostart.isChecked(),
                                self._autostart_wp.isChecked())
            if why:
                QMessageBox.warning(self, "Start at login",
                                    f"Could not change the login entry:\n{why}")
        super().accept()

    def changed_config(self) -> dict:
        """Only the settings the user actually altered.

        get_config() always returns every field, and handing that whole dict to
        apply_settings made each Save look like a preset-directory change: the
        renderer rescanned the entire tree, jumped back to the first preset and
        dropped both the shuffle order and the rated history, even when the only
        edit was the FPS. Nine thousand presets make that very obvious.
        """
        return {k: v for k, v in self.get_config().items()
                if k not in self._opened_with or self._opened_with[k] != v}

    def _audio_device_value(self) -> str:
        """Combo text back to a stored device name.

        The auto entry is a label, not a device: it has to round-trip to the
        empty string or it would be handed to the backend as a name to match
        and never found.
        """
        idx = self._audio_dev.currentIndex()
        if idx >= 0 and self._audio_dev.itemText(idx) == self._audio_dev.currentText():
            return self._audio_dev.itemData(idx) or ""
        text = self._audio_dev.currentText().strip()
        return "" if text == AudioCapture.AUTO_LABEL else text


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
    "toggle_mode":      "Shuffle the playlist order on / off",
    "toggle_borderless": "Toggle borderless window",
    "toggle_on_top":    "Keep the window above other windows",
    "fullscreen_mouse": "Toggle fullscreen (mouse)",
    "record":           "Start / stop recording a clip",
    "toggle_wallpaper": "Desktop wallpaper mode on / off",
    "exclude":          "Exclude this visual (rate it 0★) and move on",
    "snapshot":         "Snapshot this visual for the browser",
    "toggle_dual":      "Visual 2 on / off",
    "toggle_visual1":   "Visual 1 on / off",
    "crossfade_back":   "Crossfade towards visual 1",
    "crossfade_fwd":    "Crossfade towards visual 2",
    "swap_focus":       "Adjust the other visual",
}

ACTION_NOTES = {
    "fullscreen_mouse": "Value must be DBLCLICK",
    "toggle_borderless": "Drops the title bar; the window stays its size",
    "toggle_on_top":    "Pairs with borderless to park it in a corner",
    "lock":             "Stops the timer; next / previous still work",
    "rate_down":        "Rating 0 excludes preset from random rotation",
    "record":           "Needs ffmpeg; clips land in the clips folder",
    "toggle_wallpaper": "Leave it again from the tray icon — a desktop "
                        "window gets no keys",
    "exclude":          "Hides it from rotation. Nothing is deleted",
    "snapshot":         "Replaces the picture the browser already has",
    "toggle_visual1":   "One of the two always stays on",
    "crossfade_back":   "Only does anything while both visuals are on",
    "crossfade_fwd":    "Only does anything while both visuals are on",
}


class KeymapDialog(QDialog):
    def __init__(self, keymaps: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Mapping")
        self.setMinimumWidth(560)
        self.setMinimumHeight(480)
        self.setStyleSheet(dialog_qss())
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
        info.setStyleSheet(f"color: {theme_colour('muted')}; font-size: 12px;")
        layout.addWidget(info)

        # Table
        self._table = QTableWidget(len(ACTION_LABELS), 3)
        self._table.setHorizontalHeaderLabels(["Action", "Key", "Notes"])
        # Action takes what it needs and no more; Notes takes everything left.
        # Both were Stretch, which splits the leftover EVENLY -- so widening
        # the dialog for the notes fed half of every pixel to a column of
        # short labels that was already big enough, and the notes stayed cut.
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
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
            note_item.setForeground(QColor(theme_colour("muted")))
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

        # Open wide enough for the notes column. 560 was a guess, and columns
        # 0 and 2 are Stretch, so it split what was left evenly and gave each
        # of them 179px against the 237 and 359 they ask for -- every note in
        # the table clipped mid-word, including the lock one, which is the
        # only place the app says that next and previous still work while a
        # preset is locked. Measured rather than guessed, in whatever font
        # this is running in; the column hints answer before the window
        # exists, so this can size the dialog rather than correct it after.
        #
        # `frameWidth()` appears FOUR times on purpose -- twice for the frame
        # itself, and twice more for the same width spent a second time.
        # `dialog_qss` styles `QAbstractItemView`, which a QTableWidget is, so
        # the sheet's `padding: 4px 6px` lands on the table and `frameWidth()`
        # reads 7 rather than the style's 2; Qt then subtracts that padding
        # again when it hands the stretch column what is left, so the sections
        # come to 14px less than the viewport they sit in. Measured:
        # viewport 726, sections 712, on a table whose frame the sheet is the
        # only thing setting. Without this the widest note is short by the
        # width of its last word, which is the whole reason for the line.
        #
        # ponytail: no cap against the screen. Every string in this table is
        # the app's own, the widest total is ~800px, and the Store's smallest
        # accepted screenshot is 1366 wide. If these notes ever become user
        # text or get translated, cap it the way SettingsDialog does.
        m = layout.contentsMargins()
        self.resize(
            self._table.sizeHintForColumn(0) + self._table.columnWidth(1)
            + self._table.sizeHintForColumn(2)
            + self._table.verticalScrollBar().sizeHint().width()
            + 4 * self._table.frameWidth() + m.left() + m.right(),
            self.height(),
        )

    def eventFilter(self, obj, event):
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
                    self._release_elsewhere(action, key_str)
                    return True
        return super().eventFilter(obj, event)

    def _release_elsewhere(self, action: str, key_str: str):
        """Take `key_str` off every other action, in the table and the dict.

        The key handler is one elif chain, so a key given to two actions only
        ever reaches the earlier one -- and the row further down goes on
        showing a key it will never receive, which is a worse answer than an
        empty cell. The row just typed into keeps it; the other is cleared.
        DBLCLICK is the exception: it is not a key and nothing dispatches on
        it, so two actions may hold it.
        """
        if key_str.upper() == "DBLCLICK":
            return
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 1)
            if item is None:
                continue
            other = item.data(Qt.ItemDataRole.UserRole)
            if other == action or item.text().upper() != key_str.upper():
                continue
            item.setText("")
            self._keymaps[other] = ""

    def _qt_key_to_token(self, qt_key: int, text: str) -> str:
        """Convert a Qt key code to the string token used in keymaps.json."""
        special = {
            Qt.Key.Key_Return:    "RETURN",
            Qt.Key.Key_Enter:     "RETURN",
            Qt.Key.Key_Escape:    "ESCAPE",
            Qt.Key.Key_Space:     "SPACE",
            Qt.Key.Key_Tab:       "TAB",
            Qt.Key.Key_Backspace: "BACKSPACE",
            Qt.Key.Key_Delete:    "DELETE",
            Qt.Key.Key_Left:      "LEFT",
            Qt.Key.Key_Right:     "RIGHT",
            Qt.Key.Key_Up:        "UP",
            Qt.Key.Key_Down:      "DOWN",
            Qt.Key.Key_Home:      "HOME",
            Qt.Key.Key_End:       "END",
            Qt.Key.Key_PageUp:    "PAGEUP",
            Qt.Key.Key_PageDown:  "PAGEDOWN",
            Qt.Key.Key_ScrollLock: "SCROLLLOCK",
            Qt.Key.Key_F1:  "F1",  Qt.Key.Key_F2:  "F2",
            Qt.Key.Key_F3:  "F3",  Qt.Key.Key_F4:  "F4",
            Qt.Key.Key_F5:  "F5",  Qt.Key.Key_F6:  "F6",
            Qt.Key.Key_F7:  "F7",  Qt.Key.Key_F8:  "F8",
            Qt.Key.Key_F9:  "F9",  Qt.Key.Key_F10: "F10",
            Qt.Key.Key_F11: "F11", Qt.Key.Key_F12: "F12",
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
        """What the table shows, not what was handed in.

        Rows fall back to DEFAULT_KEYMAPS for an action the loaded file did
        not have -- a binding added in a later version -- so reading the table
        is what writes a complete keymaps.json back out.
        """
        km = dict(self._keymaps)
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 1)
            action = item.data(Qt.ItemDataRole.UserRole) if item else None
            if action:
                km[action] = item.text().strip()
        return km


# ─────────────────────────────────────────────────────────────────────────────
#  Context menu  (spawned at cursor position, no overlay window)
# ─────────────────────────────────────────────────────────────────────────────

class AboutDialog(QDialog):
    """Who made this, what it is built on, and where the presets are not."""

    SITE = "https://bomsaisoftware.com"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setStyleSheet(dialog_qss())
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(10)

        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pix = QPixmap(_asset_path("assets", "bomsai-logo.png"))
        if not pix.isNull():
            logo.setPixmap(pix.scaledToWidth(
                120, Qt.TransformationMode.SmoothTransformation))
            # The artwork has a black background of its own, so it is given a
            # rounded black tile to sit in rather than a hard square edge
            # against the dialog.
            logo.setStyleSheet("background:#000000;border-radius:10px;"
                               "padding:6px;margin-bottom:6px;")
            layout.addWidget(logo, 0, Qt.AlignmentFlag.AlignCenter)

        muted = theme_colour("muted")

        def line(html: str, size: int = 13, colour: str = None,
                 weight: int = 400, space_after: int = 0):
            lab = QLabel(html)
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setWordWrap(True)
            lab.setOpenExternalLinks(True)     # the site link opens a browser
            lab.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextBrowserInteraction)
            lab.setStyleSheet(
                f"color:{colour or theme_colour('fg')};"
                f"font-size:{size}px;font-weight:{weight};"
                f"margin-bottom:{space_after}px;")
            layout.addWidget(lab)
            return lab

        line(APP_NAME, size=18, weight=600)
        line(f"Version {app_version()}", size=12, colour=muted, space_after=6)
        line("Created by <b>Adam Bom</b>", size=13)
        line(f'BomsAI Software &nbsp;·&nbsp; '
             f'<a href="{self.SITE}" style="color:{theme_colour("link")};">'
             f'bomsaisoftware.com</a>',
             size=13, space_after=8)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        # projectM is LGPL-2.1 and loaded as a separate shared library. Saying
        # so here, and shipping the licence text beside the app, is part of
        # what makes including it in a paid app legitimate.
        line("Visuals rendered by <b>projectM 4</b> (LGPL-2.1), with SDL2, "
             "PySide6, PyOpenGL and PortAudio. Licence texts ship in the "
             "<i>licenses</i> folder beside the app.",
             size=11, colour=muted)
        line("Presets and textures are the work of their own authors and are "
             "not included — point the app at your own copies in Settings.",
             size=11, colour=muted, space_after=4)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)


# ─────────────────────────────────────────────────────────────────────────────
#  Preset packs  (download / install)
# ─────────────────────────────────────────────────────────────────────────────

# Nothing here ships with the app. These are the two packs worth pointing a new
# install at, and the app fetches them on request rather than redistributing
# them. A pack with no "url" cannot be fetched directly -- Google Drive answers
# a large-file request with a virus-scan interstitial rather than the file, and
# throttles once enough people have pulled it, which would surface as this app
# being broken -- so those offer the download page and a local-zip import.
PRESET_PACKS = (
    {
        "name":    "Cream of the Crop",
        "detail":  "9,795 presets, curated by ISOSCELES. The default set for "
                   "projectM since 2022 — the one to start with.",
        "size":    "about 50 MB",
        "url":     "https://github.com/projectM-visualizer/"
                   "presets-cream-of-the-crop/archive/refs/heads/master.zip",
        "page":    "https://github.com/projectM-visualizer/presets-cream-of-the-crop",
        "textures": False,
    },
    {
        "name":    "MilkDrop 135k+ MegaPack 2026",
        "detail":  "Everything, plus the texture pack many presets sample. "
                   "Several GB — Google Drive will not hand this to the app, "
                   "so download it in a browser and install the .zip below.",
        "size":    "several GB",
        "url":     None,
        "page":    "https://drive.google.com/file/d/"
                   "1DlszoqMG-pc5v1Bo9x4NhemGPiwT-0pv/view",
        "textures": True,
    },
)


#: Windows refuses to create a path of 260 characters or more unless long
#: paths are switched on, which they are not by default and which needs the
#: registry. Extraction is where it bites: zipfile raises ENOENT thousands of
#: files in and leaves a half-installed pack behind.
_MAX_PATH = 259


def _zip_root(names) -> str:
    """The single top-level directory every member sits under, or "".

    GitHub wraps its archives in one (<repo>-<branch>), and on Windows that
    wrapper is not free: Cream of the Crop's own longest path is 207 characters
    and "presets-cream-of-the-crop-master/" adds 33 more, which pushes it past
    MAX_PATH under the default install folder. Dropping the wrapper buys back
    every one of those characters and loses nothing -- loading is recursive and
    _preset_root only existed to point past it.
    """
    roots = set()
    for name in names:
        head = name.split("/", 1)
        if len(head) == 1:
            return ""            # a file at the top level: no single wrapper
        # head[1] empty means the wrapper's own directory entry ("pack/"),
        # which still names the wrapper -- it is not a reason to give up.
        if head[0] in ("", ".", "..") or ":" in head[0]:
            # Only ever strip a plain directory name. Stripping ".." would
            # turn ../escape.milk into escape.milk and quietly satisfy the
            # containment check that exists to reject exactly that.
            return ""
        roots.add(head[0])
        if len(roots) > 1:
            return ""
    return roots.pop() if roots else ""


def _preset_root(dest: str) -> str:
    """The folder to point the app at once a pack is extracted.

    Normally dest itself, because _safe_extract drops the archive's wrapper
    directory. A zip that wrapped its presets some other way still gets the
    single-subdirectory answer. Loading is recursive, so the root is the right
    answer and there is no need to guess which subfolder holds the most
    presets; Cream of the Crop nests its 9,795 two deep under category names.
    """
    if not any(f.lower().endswith(".milk")
               for _d, _s, files in os.walk(dest) for f in files):
        return ""
    entries = [e for e in os.listdir(dest) if not e.startswith(".")]
    subdirs = [e for e in entries if os.path.isdir(os.path.join(dest, e))]
    flat = any(e.lower().endswith(".milk") for e in entries)
    if len(subdirs) == 1 and not flat:
        return os.path.join(dest, subdirs[0])
    return dest


def _safe_extract(zf, dest: str, progress=None, cancelled=None):
    """Extract, refusing members that would land outside dest.

    A zip may name ../ or an absolute path; zipfile will happily follow it.
    Nothing in the packs does, but this reads archives the user chose.

    The archive's single wrapper directory is dropped on the way through, both
    because it is what _preset_root would have skipped anyway and because on
    Windows its characters count against MAX_PATH. What still will not fit is
    reported before a single file is written -- a pack that dies two thousand
    files in has left a mess that looks like a working install.
    """
    dest_abs = os.path.abspath(dest)
    members = zf.infolist()
    root = _zip_root([m.filename for m in members])
    strip = len(root) + 1 if root else 0

    plan = []
    for m in members:
        name = m.filename[strip:]
        if not name:
            continue                     # the wrapper directory itself
        target = os.path.abspath(os.path.join(dest_abs, name))
        if target != dest_abs and not target.startswith(dest_abs + os.sep):
            raise ValueError(f"refusing to extract outside the folder: {m.filename}")
        plan.append((m, name, target))

    if sys.platform == "win32":
        over = [t for _m, _n, t in plan if len(t) > _MAX_PATH]
        if over:
            room = max(len(t) for t in over) - len(dest_abs)
            raise ValueError(
                f"{len(over)} of {len(plan)} files need a path longer than "
                f"Windows allows ({_MAX_PATH} characters). Install into a "
                f"folder of at most {_MAX_PATH - room} characters — this one "
                f"is {len(dest_abs)}.")

    for i, (m, name, _t) in enumerate(plan):
        if cancelled and cancelled():
            return False
        # zipfile builds the destination from filename, and it is also what
        # strips characters Windows forbids in a name, so rename rather than
        # writing the bytes here and losing that.
        original, m.filename = m.filename, name
        try:
            zf.extract(m, dest_abs)
        finally:
            m.filename = original
        if progress and (i % 200 == 0 or i == len(plan) - 1):
            progress(i + 1, len(plan))
    return True


class _PackWorker(QThread):
    """Downloads and/or extracts a pack off the UI thread."""

    progress = Signal(int, int, str)   # done, total, phase ("" total = unknown)
    finished_ok = Signal(str, str)     # preset_dir, texture_dir ("" if none)
    failed = Signal(str)

    def __init__(self, dest: str, url: str = "", zip_path: str = "", parent=None):
        super().__init__(parent)
        self._dest = dest
        self._url = url
        self._zip = zip_path
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        tmp = ""
        try:
            os.makedirs(self._dest, exist_ok=True)
            src = self._zip
            if self._url:
                # Beside the destination so the read-back is a local file and
                # a full temp filesystem cannot fail the download half way.
                tmp = os.path.join(self._dest, ".download.part")
                src = self._download(self._url, tmp)
                if src is None:
                    return
            with zipfile.ZipFile(src) as zf:
                ok = _safe_extract(
                    zf, self._dest,
                    lambda d, t: self.progress.emit(d, t, "Extracting"),
                    lambda: self._cancel)
            if not ok:
                return
            presets = _preset_root(self._dest)
            if not presets:
                self.failed.emit("No .milk presets in that archive.")
                return
            textures = ""
            for dirpath, dirnames, _f in os.walk(self._dest):
                for d in dirnames:
                    if d.lower() == "textures":
                        textures = os.path.join(dirpath, d)
                        break
                if textures:
                    break
            self.finished_ok.emit(presets, textures)
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def _download(self, url: str, tmp: str):
        req = urllib.request.Request(
            url, headers={"User-Agent": APP_SLUG})
        with urllib.request.urlopen(req, timeout=30) as r:
            # Absent on a chunked response; the bar goes indeterminate rather
            # than pretending to a percentage it cannot know.
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as f:
                while True:
                    if self._cancel:
                        return None
                    chunk = r.read(262144)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    self.progress.emit(done, total, "Downloading")
        return tmp


class PresetPackDialog(QDialog):
    """Get presets: download a pack, or install one already downloaded."""

    def __init__(self, cfg: dict, on_applied=None, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._on_applied = on_applied
        self._worker = None
        self.setWindowTitle("Get Presets")
        self.setMinimumWidth(560)
        self.setStyleSheet(dialog_qss())

        outer = QVBoxLayout(self)
        outer.setSpacing(12)
        outer.setContentsMargins(16, 16, 16, 16)

        intro = QLabel(
            "Presets are other people's work and are not included, so the app "
            "fetches them from the people who published them.")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        row = QHBoxLayout()
        row.addWidget(QLabel("Install into:"))
        self._dest = QLineEdit(default_pack_dir(cfg))
        row.addWidget(self._dest, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._pick_dest)
        row.addWidget(browse)
        outer.addLayout(row)

        self._buttons = []
        for pack in PRESET_PACKS:
            outer.addWidget(self._pack_row(pack))

        self._bar = QProgressBar()
        self._bar.setVisible(False)
        outer.addWidget(self._bar)
        self._status = QLabel("")
        self._status.setWordWrap(True)
        outer.addWidget(self._status)

        foot = QHBoxLayout()
        foot.addStretch(1)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setVisible(False)
        self._cancel_btn.clicked.connect(self._cancel)
        foot.addWidget(self._cancel_btn)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.reject)
        foot.addWidget(self._close_btn)
        outer.addLayout(foot)

    # ── layout helpers ──────────────────────────────────────────────────
    def _pack_row(self, pack: dict) -> QWidget:
        box = QFrame()
        box.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(box)
        title = QLabel(f"<b>{pack['name']}</b>  —  {pack['size']}")
        lay.addWidget(title)
        detail = QLabel(pack["detail"])
        detail.setWordWrap(True)
        lay.addWidget(detail)

        row = QHBoxLayout()
        if pack["url"]:
            b = QPushButton("Download and install")
            b.clicked.connect(lambda _=False, p=pack: self._start(url=p["url"]))
            row.addWidget(b)
            self._buttons.append(b)
        open_page = QPushButton("Open download page")
        open_page.clicked.connect(
            lambda _=False, p=pack: QDesktopServices.openUrl(QUrl(p["page"])))
        row.addWidget(open_page)
        self._buttons.append(open_page)
        if not pack["url"]:
            b = QPushButton("Install a .zip I downloaded…")
            b.clicked.connect(self._pick_zip)
            row.addWidget(b)
            self._buttons.append(b)
        row.addStretch(1)
        lay.addLayout(row)
        return box

    def _pick_dest(self):
        d = QFileDialog.getExistingDirectory(
            self, "Where should presets go?", self._dest.text())
        if d:
            self._dest.setText(d)

    def _pick_zip(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Choose a preset pack", os.path.expanduser("~"),
            "Zip archives (*.zip)")
        if f:
            self._start(zip_path=f)

    # ── work ────────────────────────────────────────────────────────────
    def _start(self, url: str = "", zip_path: str = ""):
        dest = self._dest.text().strip()
        if not dest:
            self._status.setText("Choose a folder to install into first.")
            return
        for b in self._buttons:
            b.setEnabled(False)
        self._bar.setVisible(True)
        self._bar.setRange(0, 0)
        self._cancel_btn.setVisible(True)
        self._close_btn.setEnabled(False)
        self._status.setText("Starting…")
        # Parented to the dialog and kept in an attribute: a QThread garbage
        # collected while run() is still unwinding aborts the process, and
        # run() is still unwinding when finished_ok reaches this thread.
        self._worker = _PackWorker(dest, url, zip_path, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        # No deleteLater on finished: the dialog asks the worker whether it is
        # still running when it closes, and asking a QThread that Qt has
        # already deleted raises out of reject() -- which is what both Close
        # and the window's X call, so the dialog stayed on screen and a
        # windowed build printed the traceback nowhere. It is parented here
        # and goes when the dialog does.
        self._worker.start()

    def _on_progress(self, done: int, total: int, phase: str):
        if total:
            self._bar.setRange(0, total)
            self._bar.setValue(done)
            if phase == "Downloading":
                self._status.setText(
                    f"{phase}… {done / 1048576:.0f} of {total / 1048576:.0f} MB")
            else:
                self._status.setText(f"{phase}… {done} of {total} files")
        else:
            self._bar.setRange(0, 0)
            self._status.setText(f"{phase}… {done / 1048576:.0f} MB")

    def _on_done(self, preset_dir: str, texture_dir: str):
        self._reset()
        updates = {"preset_dir": preset_dir}
        if texture_dir:
            updates["texture_dir"] = texture_dir
        self._cfg.update(updates)
        n = len(glob.glob(os.path.join(preset_dir, "**", "*.milk"),
                          recursive=True))
        self._status.setText(
            f"Installed {n} presets." +
            (" Textures found too." if texture_dir else ""))
        if self._on_applied:
            self._on_applied(updates)

    def _on_failed(self, message: str):
        self._reset()
        self._status.setText(f"Failed: {message}")

    def _cancel(self):
        if self._worker:
            self._worker.cancel()
        self._status.setText("Cancelling…")

    def _reset(self):
        self._bar.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._close_btn.setEnabled(True)
        for b in self._buttons:
            b.setEnabled(True)

    def reject(self):
        # A half-written archive left behind would look like a corrupt pack the
        # next time round, and the worker owns the file until it returns.
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(4000)
        super().reject()


#: Right-click entries the user can switch off, in the order they appear.
#: The key is what goes in config["menu_hidden"]; the third field is the
#: keymap action that does the same job, where there is one -- which is the
#: whole reason for hiding an entry, so the dialog puts the key beside it.
#:
#: Customize and Quit are deliberately absent. One is the way back for anyone
#: who switches everything else off, and the other is the way out.
MENU_ENTRIES = (
    ("next",          "Next visual",                  "next"),
    ("prev",          "Previous visual",              "prev"),
    ("random",        "Random visual",                "random"),
    ("browser",       "Preset browser",               ""),
    ("sort_name",     "Sort by name",                 ""),
    ("sort_rating",   "Sort by rating",               ""),
    ("exclude",       "Exclude this visual (0\u2605)",  "exclude"),
    ("delete_zero",   "Delete 0\u2605 presets\u2026",    ""),
    ("reset_ratings", "Reset all ratings\u2026",        ""),
    ("collections",   "Collections",                  ""),
    ("lock",          "Lock / unlock visual",         "lock"),
    ("dual",          "Visuals and crossfade",        "toggle_dual"),
    ("borderless",    "Borderless window",            "toggle_borderless"),
    ("on_top",        "Always on top",                "toggle_on_top"),
    ("fullscreen",    "Fullscreen",                   "fullscreen"),
    ("display",       "Fullscreen display",           ""),
    ("record",        "Record clip",                  "record"),
    ("wallpaper",     "Desktop wallpaper",            "toggle_wallpaper"),
    ("audio",         "Audio source",                 ""),
    ("packs",         "Get presets\u2026",              ""),
    ("backup",        "Backup",                       ""),
    ("keymaps",       "Key bindings\u2026",             ""),
    ("settings",      "Settings\u2026",                 ""),
    ("about",         "About\u2026",                    ""),
)


class MenuCustomiseDialog(QDialog):
    """Which right-click entries to show.

    The menu has grown past a comfortable screenful, and nearly every line on
    it is also a key -- so the useful thing to offer is not reordering but
    switching off what you already have a key for. Each row carries its key
    for exactly that reason: it is the answer to "can I lose this one?".

    Hidden entries are stored, rather than shown ones, so that an entry added
    in a later version arrives visible instead of vanishing on an old config.
    """

    def __init__(self, cfg: dict, keymaps: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Customize menu")
        self.setStyleSheet(dialog_qss())
        hidden = set(cfg.get("menu_hidden") or ())
        keys = dict(DEFAULT_KEYMAPS)
        keys.update(keymaps or {})

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        info = QLabel(
            "Tick what the right-click menu shows. Nothing is lost by clearing "
            "one — an entry with a key still answers that key, and this dialog "
            "stays on the menu whatever else is off."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {theme_colour('muted')}; font-size: 12px;")
        layout.addWidget(info)

        # Two columns rather than one long list: twenty-three rows in a column
        # is a dialog taller than some of the screens this runs on.
        columns = QHBoxLayout()
        columns.setSpacing(24)
        half = (len(MENU_ENTRIES) + 1) // 2
        self._boxes = {}
        for start, end in ((0, half), (half, len(MENU_ENTRIES))):
            col = QVBoxLayout()
            col.setSpacing(6)
            for key, label, action in MENU_ENTRIES[start:end]:
                stroke = keys.get(action, "") if action else ""
                box = QCheckBox(f"{label}    ({stroke})" if stroke else label)
                box.setChecked(key not in hidden)
                self._boxes[key] = box
                col.addWidget(box)
            col.addStretch()
            columns.addLayout(col)
        layout.addLayout(columns)

        btn_row = QHBoxLayout()
        show_all = QPushButton("Show everything")
        show_all.clicked.connect(self._show_everything)
        btn_row.addWidget(show_all)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _show_everything(self):
        for box in self._boxes.values():
            box.setChecked(True)

    def hidden(self) -> list:
        """The keys to leave out, in menu order."""
        return [k for k, _label, _action in MENU_ENTRIES
                if not self._boxes[k].isChecked()]


class ContextMenu(QMenu):
    """
    Right-click menu. Spawned by the renderer when it detects a right-click
    on the SDL2 window. Because there's no persistent overlay window, we
    temporarily create a hidden QWidget as parent so the menu has a valid
    native parent.
    """

    def __init__(self, renderer, cfg: dict, ratings: dict, keymaps: dict):
        # Need a real widget parent for proper theming
        self._tmp_parent = QWidget()
        self._tmp_parent.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
        )
        # Stylesheets inherit down the widget-parent chain, and the confirm
        # dialogs below are parented to this, so setting it once here makes
        # every QMessageBox dark too.
        self._tmp_parent.setStyleSheet(dialog_qss())
        super().__init__(self._tmp_parent)
        self._renderer = renderer
        self._cfg      = cfg
        self._ratings  = ratings
        self._keymaps  = keymaps

        self.setStyleSheet(menu_qss())

        self._build()

    def _shown(self, key: str) -> bool:
        """Is this entry switched on? See MENU_ENTRIES.

        Hiding is opt-in per entry, so an entry added in a later version turns
        up visible rather than silently missing on a config written before it
        existed.
        """
        return key not in self._hidden

    def _build(self):
        self._hidden = set(self._cfg.get("menu_hidden") or ())
        locked = self._renderer.is_locked()
        on = self._shown

        if on("next"):
            self.addAction("▶   Next visual",      lambda: self._renderer.request_next())
        if on("prev"):
            self.addAction("◀   Previous visual",  lambda: self._renderer.request_prev())
        if on("random"):
            self.addAction("⟳   Random visual",    lambda: self._renderer.request_random())
        self.addSeparator()

        if on("browser"):
            self.addAction("🗂   Preset browser",   lambda: self._renderer.request_toggle_browser())
        if on("sort_name"):
            self.addAction("↕   Sort by name",     lambda: self._renderer.request_sort("name"))
        if on("sort_rating"):
            self.addAction("★   Sort by rating",   lambda: self._renderer.request_sort("rating"))
        if on("exclude"):
            self.addAction("🚫   Exclude this visual (0★)",
                           lambda: self._renderer.request_exclude())
        if on("delete_zero"):
            self.addAction("🗑   Delete 0★ presets…", self._confirm_delete_zero)
        if on("reset_ratings"):
            self.addAction("↺   Reset all ratings…", self._confirm_reset_ratings)
        if on("collections"):
            self._build_collections_menu()
        self.addSeparator()

        if on("lock"):
            lock_text = "\U0001f512  Unlock visual" if locked else "\U0001f513  Lock visual"
            self.addAction(lock_text, lambda: self._renderer.request_toggle_lock())
        if on("dual"):
            self._build_dual_menu()
        self.addSeparator()

        if on("borderless"):
            borderless = self._renderer.is_borderless()
            border_text = "☐   Show window border" if borderless else "▣   Borderless window"
            self.addAction(border_text, lambda: self._renderer.request_toggle_borderless())

        if on("on_top"):
            on_top = self._renderer.is_on_top()
            top_text = "\u2934   Stop keeping it on top" if on_top else "\u2934   Always on top"
            self.addAction(top_text, lambda: self._renderer.request_toggle_on_top())

        if on("fullscreen"):
            full = self._renderer.is_fullscreen()
            self.addAction("🗗   Leave fullscreen" if full else "⛶   Fullscreen",
                           lambda: self._renderer.request_fullscreen())
        if on("display"):
            self._build_display_menu()
        self.addSeparator()

        if on("record"):
            self._build_record_menu()
        if on("wallpaper"):
            self._build_wallpaper_entry()
        self.addSeparator()

        if on("audio"):
            self._build_audio_menu()
        self.addSeparator()

        if on("packs"):
            self.addAction("⬇   Get presets…",    self._open_packs)
        if on("backup"):
            self._build_backup_menu()
        if on("keymaps"):
            self.addAction("⌨   Key bindings…",   self._open_keymaps)
        if on("settings"):
            self.addAction("⚙   Settings…",        self._open_settings)
        # Not hideable, and deliberately the entry above About: it is the only
        # way back for anyone who switches everything else off.
        self.addAction("🎛   Customize menu…",   self._open_customize)
        if on("about"):
            self.addAction("ℹ   About…",           self._open_about)
        self.addSeparator()

        self.addAction("✕   Quit",             QApplication.quit)
        self._tidy_separators()

    def _tidy_separators(self):
        """Drop the rules that have nothing left on one side of them.

        The separators above are written for the full menu. Switch a whole
        group off and its rule stays behind as a double line, or a line at the
        very top -- which reads as a rendering fault rather than a setting.
        """
        previous_was_a_rule = True        # a leading rule is as empty as a double
        for act in list(self.actions()):
            if not act.isSeparator():
                previous_was_a_rule = False
                continue
            if previous_was_a_rule:
                self.removeAction(act)
            else:
                previous_was_a_rule = True
        while self.actions() and self.actions()[-1].isSeparator():
            self.removeAction(self.actions()[-1])

    def _build_dual_menu(self):
        """The two visuals, the fader between them, and where each one goes.

        Two switches rather than one "second visual" toggle: with a fader in
        the menu, visual 2 is no longer a thing you add on top of visual 1 --
        either of the two can be the one on screen, and a single toggle
        cannot say that. Whichever is left on its own is greyed out, because
        both off is a black window and the menu that would undo it is no
        longer being drawn.

        The focus line stays and still needs saying out loud: everything else
        on this menu, every key and the whole preset browser act on one of the
        two visuals, and without a line naming which, the other one looks
        broken rather than unselected.
        """
        if not self._renderer.can_dual():
            return
        on = (self._renderer.visual_on(0), self._renderer.visual_on(1))
        for which in (0, 1):
            act = QAction(f"\u29c9   Visual {which + 1}", self)
            act.setCheckable(True)
            act.setChecked(on[which])
            if on[which] and not on[1 - which]:
                act.setEnabled(False)
                act.setToolTip("The only visual on screen. Switch the other "
                               "one on first.")
            act.triggered.connect(
                lambda checked, i=which: self._renderer.request_visual(
                    i, checked))
            self.addAction(act)

        self._build_crossfade_row(on[0] and on[1])
        self._build_send_visual_menu()

        if not self._renderer.dual_on():
            return
        which = self._renderer.focus()
        self.addAction("\u21c4   Adjust the first visual" if which
                       else "\u21c4   Adjust the second visual",
                       lambda: self._renderer.request_focus(1 - which))

    def _build_crossfade_row(self, live: bool):
        """The fader, in the menu rather than behind Settings.

        It is the one control here you want to move while watching the thing
        it changes, which is exactly what a settings dialog is worst at. Same
        shape as the audio sensitivity row further down, including the save
        timer -- sliderReleased does not fire for a click on the groove or an
        arrow key, and a fader that moves the picture without ever being
        written down is the bug that row already paid for.
        """
        row = QWidget()
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(14, 4, 14, 4)
        row_l.setSpacing(8)
        value = int(round(self._renderer.crossfade() * 100))

        def caption(v: int) -> str:
            if v <= 0:
                return "\u21c4 Visual 1 only"
            if v >= 100:
                return "\u21c4 Visual 2 only"
            return f"\u21c4 {100 - v}%  /  {v}%"

        lbl = QLabel(caption(value))
        lbl.setStyleSheet(f"color:{theme_colour('fg' if live else 'muted')};"
                          "font-size:12px;min-width:130px;")
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(value)
        slider.setFixedWidth(160)
        slider.setEnabled(live)
        if not live:
            slider.setToolTip("Switch both visuals on to fade between them.")

        # What is on disk, not self._cfg: request_crossfade writes into the
        # very dict the render loop is reading, so comparing against it would
        # find the new value already there and skip every save.
        on_disk = [self._renderer.crossfade()]

        def store():
            x = round(slider.value() / 100.0, 2)
            if x == on_disk[0]:
                return
            on_disk[0] = x
            self._renderer._save_config(self._cfg)
            _log(f"[dual] crossfade {x:g}")

        save_timer = QTimer(row)
        save_timer.setSingleShot(True)
        save_timer.setInterval(250)
        save_timer.timeout.connect(store)

        def on_change(v):
            lbl.setText(caption(v))
            self._renderer.request_crossfade(v / 100.0)
            save_timer.start()

        slider.valueChanged.connect(on_change)
        row_l.addWidget(lbl)
        row_l.addWidget(slider)
        wa = QWidgetAction(self)
        wa.setDefaultWidget(row)
        self.addAction(wa)

    def _build_send_visual_menu(self):
        """Put each visual on a monitor of its own.

        Nothing to offer on one monitor -- the two are already sharing the
        only screen there is, which is what the fader above is for. Point
        them at the same screen and that is what you get back.
        """
        try:
            displays = self._renderer.list_displays()
        except Exception:
            displays = []
        if len(displays) < 2:
            return
        sub = self.addMenu("\U0001f5b5   Send visual to")
        sub.setStyleSheet(self.styleSheet())
        here = int(self._cfg.get("fullscreen_display", -1))
        there = int(self._cfg.get("visual2_display", -1))

        def monitors(parent, current, chosen, first_label):
            group = QActionGroup(parent)
            group.setExclusive(True)
            rows = [(-1, first_label)]
            rows += [(i, f"{i + 1}.  {name}   {w}\u00d7{h}")
                     for i, name, _x, _y, w, h in displays]
            for idx, label in rows:
                act = QAction(label, parent)
                act.setCheckable(True)
                act.setChecked(idx == current)
                act.triggered.connect(lambda _, i=idx: chosen(i))
                group.addAction(act)
                parent.addAction(act)
                if idx == -1:
                    parent.addSeparator()

        def send_first(idx: int):
            self._cfg["fullscreen_display"] = idx
            self._renderer._save_config(self._cfg)
            if idx >= 0:
                self._renderer.request_fullscreen_on(idx)

        def send_second(idx: int):
            self._cfg["visual2_display"] = idx
            self._renderer._save_config(self._cfg)
            self._renderer.request_second_window(idx)
            if idx >= 0:
                # A window of its own shows visual 2 alone, so a second
                # visual has to exist for there to be anything in it.
                self._renderer.request_visual(1, True)

        one = sub.addMenu("Visual 1")
        one.setStyleSheet(self.styleSheet())
        monitors(one, here, send_first, "Follow the window")
        two = sub.addMenu("Visual 2")
        two.setStyleSheet(self.styleSheet())
        monitors(two, there, send_second, "Same window as visual 1")

    def _build_wallpaper_entry(self):
        """Live where the session can host it, disabled with a reason where it
        cannot. Offering it anyway spends a click to reach a toast that is gone
        three seconds later, so the reason goes where the click would have
        been -- in the tooltip, which is longer than a menu line allows."""
        if self._renderer.is_wallpaper():
            self.addAction("🖥   Leave wallpaper mode",
                           lambda: self._renderer.request_wallpaper())
            return
        ok, why = wallpaper_support()
        if ok and sys.platform == "win32":
            host, _layered = find_desktop_host(wait=0.3)
            if not host:
                # No Explorer desktop to draw into at all -- a third-party
                # shell, or the desktop not up yet.
                ok, why = False, ("Wallpaper mode is not available on this "
                                  "version of Windows.")
        if ok and not QSystemTrayIcon.isSystemTrayAvailable():
            # A desktop window gets no keyboard and no mouse -- the icons above
            # it take both -- so the tray icon is the only way back out. On a
            # desktop with no tray, switching it on is a one-way trip ending in
            # a kill, which is not something to offer behind a single click.
            ok, why = False, ("Wallpaper mode gets no keyboard and no mouse, so "
                              "the tray icon is the only way back out of it — "
                              "and this desktop has no system tray.")
        if not ok:
            dead = QAction("🖥   Desktop wallpaper — not available here", self)
            dead.setEnabled(False)
            dead.setToolTip(why)
            self.setToolTipsVisible(True)
            self.addAction(dead)
            return
        self.addAction("🖥   Set as desktop wallpaper",
                       lambda: self._renderer.request_wallpaper())

    def _build_record_menu(self):
        """Record what is on screen to a clip.

        A GIF is here beside the MP4 because a GIF is what gets posted, and a
        clip someone posts is the cheapest advertising this app has.
        """
        if self._renderer.is_recording():
            self.addAction("⏹   Stop recording",
                           lambda: self._renderer.request_record())
            return
        sub = self.addMenu("⏺   Record clip")
        sub.setStyleSheet(self.styleSheet())
        if find_ffmpeg():
            sub.addAction("🎬   Video (MP4)",
                          lambda: self._renderer.request_record("mp4"))
            sub.addAction("🖼   Animation (GIF)",
                          lambda: self._renderer.request_record("gif"))
        else:
            act = QAction("ffmpeg not found — install it to record", sub)
            act.setEnabled(False)
            sub.addAction(act)
        sub.addSeparator()
        sub.addAction("📂   Open clips folder", self._open_clips)

    def _build_collections_menu(self):
        """Named sets of presets: pick one to play, or file the current one.

        Ratings answer "is this any good". A collection answers "what is it
        for", which is the question a library of ten thousand still leaves
        open once everything has been rated.
        """
        sub = self.addMenu("🎵   Collections")
        sub.setStyleSheet(self.styleSheet())
        try:
            names = sorted(self._renderer.collections(), key=str.lower)
            active = self._renderer.collection()
        except Exception:
            names, active = [], ""

        group = QActionGroup(sub)
        group.setExclusive(True)
        for name, label in [("", "All presets")] + [(n, n) for n in names]:
            act = QAction(label, sub)
            act.setCheckable(True)
            act.setChecked(name == active)
            act.triggered.connect(
                lambda _, n=name: self._renderer.play_collection(n))
            group.addAction(act)
            sub.addAction(act)
            if not name:
                sub.addSeparator()

        sub.addSeparator()
        stem = ""
        try:
            stem = self._renderer.current_preset_stem()
        except Exception:
            pass
        members = {}
        try:
            members = self._renderer.collections()
        except Exception:
            pass
        add = sub.addMenu("➕   Add this visual to")
        add.setStyleSheet(self.styleSheet())
        for name in names:
            act = QAction(name, add)
            # Disabled rather than hidden: seeing that it is already in there
            # is the answer to the question that opened this menu.
            act.setEnabled(stem not in members.get(name, ()))
            act.triggered.connect(lambda _, n=name: self._add_current_to(n))
            add.addAction(act)
        if names:
            add.addSeparator()
        add.addAction("✎   New collection…", self._new_collection)

        if active:
            sub.addAction(f"➖   Remove this visual from “{active}”",
                          lambda: self._remove_current_from(active))
            sub.addAction(f"🗑   Delete the “{active}” collection…",
                          lambda: self._delete_collection(active))
        elif not names:
            hint = QAction("(none yet — add a visual to make one)", sub)
            hint.setEnabled(False)
            sub.addAction(hint)

    def _add_current_to(self, name: str):
        stem = self._renderer.current_preset_stem()
        if self._renderer.add_to_collection(name, stem):
            self._renderer._bridge.show_toast.emit(f"Added to “{name}”")

    def _remove_current_from(self, name: str):
        stem = self._renderer.current_preset_stem()
        if self._renderer.remove_from_collection(name, stem):
            self._renderer._bridge.show_toast.emit(f"Removed from “{name}”")

    def _new_collection(self):
        name, ok = QInputDialog.getText(
            self._tmp_parent, "New collection",
            "Name it — “chill”, “party”, whatever you will recognise:")
        if not ok:
            return
        if not self._renderer.create_collection(name):
            QMessageBox.information(
                self._tmp_parent, "New collection",
                "That name is empty or already in use.")
            return
        self._add_current_to(name.strip())

    def _delete_collection(self, name: str):
        reply = QMessageBox.question(
            self._tmp_parent, "Delete collection",
            f"Delete the collection “{name}”?\n"
            "The presets themselves are not touched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._renderer.delete_collection(name)

    def _build_backup_menu(self):
        """Ratings and collections out to a zip, and back in again.

        These are the only files here that cannot be downloaded again: they
        are hours of somebody deciding what they like. The presets are a
        download; this is not.
        """
        sub = self.addMenu("💾   Backup")
        sub.setStyleSheet(self.styleSheet())
        # "&&" not "&": Qt reads a single ampersand in an action label as the
        # keyboard mnemonic marker, so this row rendered as "ratings
        # _collections…" with the c underlined.
        sub.addAction("⬆   Back up ratings && collections…", self._do_backup)
        sub.addAction("⬇   Restore from a backup…", self._do_restore)

    def _default_backup_name(self) -> str:
        stamp = _time.strftime("%Y-%m-%d")
        return os.path.join(os.path.expanduser("~"),
                            f"{APP_SLUG}-backup-{stamp}.zip")

    def _do_backup(self):
        path, _ = QFileDialog.getSaveFileName(
            self._tmp_parent, "Save a backup", self._default_backup_name(),
            "Zip archive (*.zip)")
        if not path:
            return
        why = backup_state(path, self._renderer.state_paths())
        if why:
            QMessageBox.warning(self._tmp_parent, "Backup", f"Failed: {why}")
        else:
            QMessageBox.information(
                self._tmp_parent, "Backup",
                f"Saved to\n{path}\n\nRatings, collections, settings and key "
                "bindings are all in it.")

    def _do_restore(self):
        path, _ = QFileDialog.getOpenFileName(
            self._tmp_parent, "Restore a backup",
            os.path.expanduser("~"), "Zip archive (*.zip)")
        if not path:
            return
        reply = QMessageBox.question(
            self._tmp_parent, "Restore",
            "Replace the current ratings, collections, settings and key "
            "bindings with the ones in this backup?\n\n"
            f"{APP_NAME} has to be restarted afterwards.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            done = restore_state(path, self._renderer.state_paths())
        except Exception as e:
            QMessageBox.warning(self._tmp_parent, "Restore", f"Failed: {e}")
            return
        if not done:
            QMessageBox.warning(
                self._tmp_parent, "Restore",
                "That zip holds none of this application's files.")
            return
        QMessageBox.information(
            self._tmp_parent, "Restore",
            f"Restored {len(done)} file(s).\n\nClose and reopen "
            f"{APP_NAME} for them to take effect.")

    def _open_clips(self):
        d = clips_dir()
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def _build_display_menu(self):
        """Which monitor fullscreen means.

        Not shown on a single-monitor machine: the only answer there is the
        one it already does, so the menu would be a question with no choices.
        """
        try:
            displays = self._renderer.list_displays()
        except Exception:
            displays = []
        if len(displays) < 2:
            return
        sub = self.addMenu("🖵   Fullscreen display")
        sub.setStyleSheet(self.styleSheet())
        group = QActionGroup(sub)
        group.setExclusive(True)
        current = int(self._cfg.get("fullscreen_display", -1))

        def choose(idx: int):
            self._cfg["fullscreen_display"] = idx
            self._renderer._save_config(self._cfg)

        rows = [(-1, "Follow the window")]
        rows += [(i, f"{i + 1}.  {name}   {w}×{h}")
                 for i, name, _x, _y, w, h in displays]
        for idx, label in rows:
            act = QAction(label, sub)
            act.setCheckable(True)
            act.setChecked(idx == current)
            act.triggered.connect(lambda _, i=idx: choose(i))
            group.addAction(act)
            sub.addAction(act)
            if idx == -1:
                sub.addSeparator()

    def _build_audio_menu(self):
        """Source picker and sensitivity, without going through Settings.

        The device list is rebuilt on every right-click, so something plugged
        in a moment ago is in it. Both entries write the same config keys the
        settings dialog does.
        """
        sub = self.addMenu("🎧   Audio source")
        sub.setStyleSheet(self.styleSheet())
        group = QActionGroup(sub)
        group.setExclusive(True)
        current = self._cfg.get("audio_device", "")

        def choose(name: str):
            self._cfg["audio_device"] = name
            self._renderer._save_config(self._cfg)
            self._renderer.apply_settings({"audio_device": name})

        try:
            devices = AudioCapture.list_devices()
            labels = AudioCapture.device_labels()
        except Exception:
            devices, labels = [], {}
        for name in [""] + devices:
            text = labels.get(name, name)
            act = QAction(text[:52] + ("…" if len(text) > 52 else "")
                          if name else AudioCapture.AUTO_LABEL, sub)
            act.setToolTip(name)
            act.setCheckable(True)
            act.setChecked(name == current)
            act.triggered.connect(lambda _, n=name: choose(n))
            group.addAction(act)
            sub.addAction(act)
            if not name:
                sub.addSeparator()
        if not devices:
            none = QAction("(no capture devices found)", sub)
            none.setEnabled(False)
            sub.addAction(none)

        # Level meter. The commonest first-run failure is a source that
        # captures nothing -- a microphone instead of the speakers, an output
        # nothing is playing through -- and the symptom is visuals that look
        # alive but never react. There is no way to tell that apart from a
        # quiet track without a number, so here is the number.
        sub.addSeparator()
        meter_row = QWidget()
        meter_l = QHBoxLayout(meter_row)
        meter_l.setContentsMargins(14, 4, 14, 4)
        meter_l.setSpacing(8)
        meter_lbl = QLabel("🎚 Level")
        meter_lbl.setStyleSheet(f"color:{theme_colour('fg')};"
                                "font-size:12px;min-width:130px;")
        meter = QProgressBar()
        meter.setRange(0, 100)
        meter.setTextVisible(False)
        meter.setFixedWidth(160)
        meter.setFixedHeight(10)
        meter_l.addWidget(meter_lbl)
        meter_l.addWidget(meter)
        mwa = QWidgetAction(sub)
        mwa.setDefaultWidget(meter_row)
        sub.addAction(mwa)

        def tick():
            try:
                v = int(min(1.0, self._renderer.audio_level()) * 100)
            except Exception:
                v = 0
            meter.setValue(v)
            meter_lbl.setText("🎚 Level" if v else "🎚 Level — silent")

        # Parented to the row so it dies with the menu, same as the save timer
        # below. 80ms is fast enough to look live and slow enough that a menu
        # left open costs nothing.
        meter_timer = QTimer(meter_row)
        meter_timer.timeout.connect(tick)
        meter_timer.start(80)
        tick()

        # Sensitivity. Live while dragging so the effect is visible against
        # the running visuals; written to config only on release, because
        # every drag step would otherwise rewrite config.json.
        sub.addSeparator()
        row = QWidget()
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(14, 4, 14, 4)
        row_l.setSpacing(8)
        gain = float(self._cfg.get("audio_gain", 1.0))
        lbl = QLabel(f"🔉 Sensitivity {int(gain * 100)}%")
        lbl.setStyleSheet(f"color:{theme_colour('fg')};"
                          "font-size:12px;min-width:130px;")
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(10, 800)             # 0.1x - 8x
        slider.setValue(int(gain * 100))
        slider.setFixedWidth(160)

        def preview(v):
            lbl.setText(f"🔉 Sensitivity {v}%")
            self._renderer.apply_settings({"audio_gain": v / 100.0})

        # What is on disk. NOT self._cfg["audio_gain"]: apply_settings hands
        # the render loop the very same dict object, which updates it as soon
        # as the preview lands -- so comparing against it would find the value
        # already there and skip every save. (It did, silently.)
        on_disk = [gain]

        def store():
            g = round(slider.value() / 100.0, 2)
            if g == on_disk[0]:
                return
            on_disk[0] = g
            self._cfg["audio_gain"] = g
            self._renderer._save_config(self._cfg)
            _log(f"[audio] input sensitivity {g:g}x")

        # Saving is on a short timer rather than on sliderReleased: that
        # signal only fires for a handle drag, so a click on the groove or an
        # arrow key would change the sound and never write it down. Measured
        # -- a menu closed by ESCAPE does not emit aboutToHide for a submenu
        # either, so that is not a fallback. The timer is parented to the row
        # so it dies with the menu.
        save_timer = QTimer(row)
        save_timer.setSingleShot(True)
        save_timer.setInterval(250)
        save_timer.timeout.connect(store)

        def on_change(v):
            preview(v)
            save_timer.start()

        slider.valueChanged.connect(on_change)
        row_l.addWidget(lbl)
        row_l.addWidget(slider)
        wa = QWidgetAction(sub)
        wa.setDefaultWidget(row)
        sub.addAction(wa)

        def reset():
            self._cfg["audio_gain"] = 1.0
            self._renderer._save_config(self._cfg)
            self._renderer.apply_settings({"audio_gain": 1.0})

        sub.addAction("↺   Reset sensitivity (100%)", reset)

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
            updates = dlg.changed_config()
            # Only where the theme is ours to set. This menu is also Bomsai
            # Player's, whose config has no "theme" and whose QApplication is
            # the host's -- restyling that from here would repaint somebody
            # else's app.
            themed = "theme" in self._cfg
            self._cfg.update(updates)
            if themed:
                # Dialogs are built fresh each time, so the next one picks the
                # theme up on its own; this is only so Qt's own chrome follows.
                # From the merged config, because updates carries "theme" only
                # when it changed and the default would undo the real setting.
                set_app_theme(self._cfg.get("theme", "dark"))
            self._renderer._save_config(self._cfg)
            self._renderer.apply_settings(updates)

    def _open_packs(self):
        def applied(updates):
            self._renderer._save_config(self._cfg)
            self._renderer.apply_settings(updates)
        PresetPackDialog(self._cfg, applied, self._tmp_parent).exec()

    def _open_about(self):
        AboutDialog(self._tmp_parent).exec()

    def _open_customize(self):
        dlg = MenuCustomiseDialog(self._cfg, self._keymaps)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._cfg["menu_hidden"] = dlg.hidden()
            self._renderer._save_config(self._cfg)

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


# ─── frame capture ────────────────────────────────────────────────────────────

def find_ffmpeg() -> str:
    """Path to an ffmpeg binary, or "" when there is none.

    Beside the app first, so a bundled copy wins over whatever the host has --
    an installed build must behave the same on every machine. PATH is the
    fallback, which is what a source checkout uses.
    """
    import shutil
    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    here = [_asset_path(exe), _asset_path("bin", exe)]
    if getattr(sys, "frozen", False):
        here.insert(0, os.path.join(os.path.dirname(sys.executable), exe))
    for cand in here:
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return shutil.which(exe) or ""


def child_env() -> dict:
    """Environment for a helper process spawned out of a frozen build.

    PyInstaller puts the bundle's own library directory on the loader path so
    the app finds its copies of Qt and SDL, and every child process inherits
    it -- so a system ffmpeg starts against the bundle's older libstdc++ and
    dies with "GLIBCXX_3.4.32 not found" before reading a byte. Measured on
    the 1.5.0 Linux build, where the bundle is deliberately built old to keep
    the glibc floor down. PyInstaller saves the original in <VAR>_ORIG for
    exactly this; an empty one means the variable was not set to begin with.

    The second time this bit, it did not crash, which was worse. `gsettings`
    run from inside the bundle loaded the bundled libgio, which cannot load
    the system's dconf GIO module, and silently fell back to the **keyfile**
    backend: `get` returned the schema defaults, `set` returned 0, the value
    persisted across runs -- in ~/.config/glib-2.0/settings/keyfile, where
    nothing else on the desktop looks. Measured 14 Sep 2026, when the app
    reported writing Nemo's ignore list while Nemo went on reading a dconf
    entry that had never changed. So: every system binary this app shells out
    to gets this environment, whether or not it is known to link anything the
    bundle carries.
    """
    env = dict(os.environ)
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        orig = env.pop(var + "_ORIG", None)
        if orig:
            env[var] = orig
        elif orig is not None or getattr(sys, "frozen", False):
            env.pop(var, None)
    return env


_VIDEO_CODEC_CACHE = {}


def video_codec_args(ffmpeg: str) -> list:
    """The best H.264 settings this particular ffmpeg can actually do.

    libx264 is GPL. An LGPL ffmpeg -- the kind that can be shipped inside a
    paid application without a licence argument -- does not have it, so asking
    for it would fail on exactly the build worth bundling. Media Foundation
    (h264_mf) is present in LGPL Windows builds and is hardware accelerated;
    mpeg4 is in every build and is the last resort. The quality flags differ
    per encoder, which is why this returns the whole group and not a name.
    """
    if ffmpeg in _VIDEO_CODEC_CACHE:
        return list(_VIDEO_CODEC_CACHE[ffmpeg])
    have = ""
    try:
        have = _subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-encoders"],
            capture_output=True, text=True, timeout=15, env=child_env(),
            creationflags=(_subprocess.CREATE_NO_WINDOW
                           if sys.platform == "win32" else 0)).stdout
    except Exception as e:
        print(f"[record] could not list encoders: {e}", flush=True)
    if " libx264" in have:
        args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    elif " h264_mf" in have:
        args = ["-c:v", "h264_mf", "-b:v", "8M"]
    else:
        args = ["-c:v", "mpeg4", "-q:v", "3"]
    _VIDEO_CODEC_CACHE[ffmpeg] = args
    return list(args)


def clips_dir() -> str:
    """Where recordings land. Created on demand, not at import."""
    return os.path.join(user_data_dir(), "clips")


class _Recorder(threading.Thread):
    """Raw RGB frames in, an MP4 or GIF out, ffmpeg doing the work.

    A thread with a bounded queue rather than writing to ffmpeg's stdin from
    the render loop: at 720p a frame is 2.7MB, and a pipe that blocks for one
    encode blocks the visualiser. When the queue is full the frame is dropped
    and counted -- a clip that skips is better than a visualiser that stutters
    while it records.
    """

    #: Deliberately small. A deep queue would just buy latency and RAM before
    #: dropping anyway -- at 720p a slot costs 2.7MB -- and the drop is what
    #: protects the frame rate.
    MAX_PENDING = 8
    #: How long after starting ffmpeg a submit may wait for room, and the cap
    #: once it is running. See _grace().
    STARTUP_S, STARTUP_WAIT, RUNNING_WAIT = 2.0, 0.5, 0.005

    def __init__(self, path: str, size: tuple, fps: int, kind: str = "mp4",
                 audio_rate: int = 0):
        super().__init__(daemon=True)
        import queue
        self.path    = path
        self.kind    = kind
        self._size   = size
        self._fps    = max(5, min(60, int(fps)))
        self._q      = queue.Queue(maxsize=self.MAX_PENDING)
        self._proc   = None
        self._t0     = 0.0
        self.frames  = 0
        self.dropped = 0
        self.error   = ""
        #: Something went wrong that did NOT cost the clip -- the sound, most
        #: likely. Kept apart from error so a saved file is never reported as
        #: a failed recording.
        self.warning = ""
        #: Sound is a second file, muxed in at the end -- see _mux_audio.
        #: A GIF has no audio track, so it never asks for one.
        self._audio_rate = int(audio_rate) if kind == "mp4" else 0
        self._wav = None
        self._wav_lock = threading.Lock()
        #: Where the video goes while sound is being recorded, before the two
        #: are joined into self.path.
        self._video_path = (path + ".video.mp4") if self._audio_rate else path

    # ── ffmpeg ────────────────────────────────────────────────────────────

    def _ffmpeg_args(self, ffmpeg: str) -> list:
        # Not _args: threading.Thread.__init__ sets self._args, and an instance
        # attribute shadows the method of the same name.
        w, h = self._size
        base = [ffmpeg, "-y", "-loglevel", "error",
                "-f", "rawvideo", "-pixel_format", "rgb24",
                "-video_size", f"{w}x{h}", "-framerate", str(self._fps),
                "-i", "-"]
        if self.kind == "gif":
            # One pass, not the usual two: the palette is generated from the
            # same stream it is applied to via split, so there is no temporary
            # file and nothing to clean up. Needs -filter_complex -- split has
            # two outputs and -vf only takes a straight chain.
            return base + [
                "-filter_complex",
                "[0:v]vflip,fps=15,scale=480:-2:flags=lanczos,split[a][b];"
                "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer",
                "-loop", "0", self._video_path]
        # trunc(/2)*2 because H.264 will not take an odd width or height, and
        # the window is whatever the user dragged it to.
        return base + [
            "-vf", "vflip,scale=trunc(iw/2)*2:trunc(ih/2)*2",
        ] + video_codec_args(ffmpeg) + [
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", self._video_path]

    def start_encoder(self) -> str:
        """Spawn ffmpeg. Returns "" on success, else the reason it failed."""
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            return ("ffmpeg was not found. Install it, or put ffmpeg beside "
                    "the application, to record clips.")
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self._proc = _subprocess.Popen(
                self._ffmpeg_args(ffmpeg),
                stdin=_subprocess.PIPE,
                stdout=_subprocess.DEVNULL,
                stderr=_subprocess.PIPE,
                env=child_env(),
                creationflags=(_subprocess.CREATE_NO_WINDOW
                               if sys.platform == "win32" else 0),
            )
        except Exception as e:
            return f"could not start ffmpeg: {e}"
        self._t0 = self._t_last = _time.monotonic()
        self._open_wav()
        self.start()
        return ""

    # ── Sound ─────────────────────────────────────────────────────────────

    def _wav_path(self) -> str:
        return self.path + ".audio.wav"

    def _open_wav(self):
        """A plain 16-bit WAV beside the video, filled by the capture thread.

        Not a second pipe into the same ffmpeg: passing an extra file
        descriptor is POSIX-only, and a named pipe on Windows is a lot of
        machinery for a file that is a few megabytes. Muxing at the end costs
        one remux -- the video is stream-copied, never re-encoded.
        """
        if not self._audio_rate:
            return
        import wave
        try:
            w = wave.open(self._wav_path(), "wb")
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(self._audio_rate)
            self._wav = w
        except Exception as e:
            print(f"[record] no sound: {e}", flush=True)
            self._wav = None

    def write_audio(self, chunk):
        """One captured chunk. Called on the audio backend's thread."""
        if self._wav is None:
            return
        try:
            pcm = (np.clip(chunk, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        except Exception:
            return
        with self._wav_lock:
            w = self._wav
            if w is None:
                return
            try:
                w.writeframes(pcm)
            except Exception as e:
                # Give up on the sound rather than retry every chunk: the
                # realistic failure is a full disk, which fails the same way
                # next time, and the clip is silent either way. What this cost
                # before was a silent clip with nothing in the log to say why
                # -- and sound is half of what the clip is for.
                self._wav = None
                print(f"[record] sound stopped: {e}", flush=True)

    def _close_wav(self) -> int:
        """Close the WAV and return how many sample frames it holds.

        Zero means the mux leaves the sound out, so a failure here is a silent
        clip. Say so.
        """
        with self._wav_lock:
            w, self._wav = self._wav, None
        if w is None:
            return 0
        try:
            n = w.getnframes()
            w.close()
            return n
        except Exception as e:
            print(f"[record] no sound in the clip: {e}", flush=True)
            return 0

    def _timestamp_scale(self) -> float:
        """How much the video's timestamps have to stretch to match real time.

        ffmpeg is told a constant frame rate, so a clip that dropped frames is
        shorter than the wall clock it covered -- and the sound, which is
        captured in real time, would run ahead of it. Measured against the
        frames that actually reached the encoder, and applied with -itsscale,
        which retimes without re-encoding anything.
        """
        if self.frames < 2:
            return 1.0
        nominal = (self.frames - 1) / float(self._fps)
        wall = self._t_last - self._t0
        if nominal <= 0 or wall <= 0:
            return 1.0
        scale = wall / nominal
        # Outside this range something other than dropped frames is wrong --
        # a stalled encoder, a clock jump -- and stretching to match it would
        # turn a slightly short clip into an unwatchable one.
        return scale if 0.5 <= scale <= 4.0 else 1.0

    def _mux_audio(self) -> str:
        """Join the temp video and WAV into self.path. "" or the failure."""
        wav = self._wav_path()
        video = self._video_path
        try:
            if not os.path.isfile(video):
                return "no video was written"
            ffmpeg = find_ffmpeg()
            if not ffmpeg or self._close_wav() == 0:
                # No sound to add. The video already is the clip.
                os.replace(video, self.path)
                return ""
            cmd = [ffmpeg, "-y", "-loglevel", "error",
                   "-itsscale", f"{self._timestamp_scale():.6f}", "-i", video,
                   "-i", wav,
                   "-map", "0:v:0", "-map", "1:a:0",
                   "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                   "-shortest", "-movflags", "+faststart", self.path]
            r = _subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
                env=child_env(),
                creationflags=(_subprocess.CREATE_NO_WINDOW
                               if sys.platform == "win32" else 0))
            if r.returncode != 0 or not os.path.isfile(self.path):
                # Keep the silent video rather than losing the recording. A
                # warning, not an error: the clip is on disk either way.
                os.replace(video, self.path)
                tail = (r.stderr or "").strip().splitlines()
                self.warning = ("saved without sound: "
                                f"{tail[-1] if tail else 'mux failed'}")
            return ""
        finally:
            self._close_wav()
            for junk in (wav, video):
                if junk != self.path:
                    try:
                        os.remove(junk)
                    except OSError:
                        pass

    # ── Producer side (render thread) ─────────────────────────────────────

    def _grace(self) -> float:
        """How long submit() may wait for a queue slot.

        ffmpeg takes a couple of hundred milliseconds to start reading, and at
        720p a single frame nearly fills the OS pipe buffer -- so with a
        strictly non-blocking submit the opening second of every clip went
        into the drop counter (measured: 13 of 24 frames). A wide window at
        the start buys those back and costs one brief hitch at the moment you
        press record; the encoder keeps up easily after that, so the running
        cap is short enough to lose a frame rather than stutter the visuals.
        """
        return (self.STARTUP_WAIT
                if _time.monotonic() - self._t0 < self.STARTUP_S
                else self.RUNNING_WAIT)

    def submit(self, frame: bytes):
        self._t_last = _time.monotonic()
        try:
            self._q.put(frame, timeout=self._grace())
        except Exception:
            self.dropped += 1

    def finish(self, timeout: float = 20.0) -> str:
        """Stop, let ffmpeg drain, return "" or the reason it failed."""
        self._q.put(None)
        self.join(timeout)
        if self._proc is not None:
            try:
                self._proc.wait(timeout=timeout)
            except Exception:
                self._proc.kill()
        if self._audio_rate and not self.error:
            # Only when the video came out clean: muxing onto a broken file
            # would replace a diagnosable failure with a confusing one.
            self.error = self._mux_audio()
        else:
            self._close_wav()
        return self.error

    # ── Consumer side ─────────────────────────────────────────────────────

    def run(self):
        stdin = self._proc.stdin
        while True:
            frame = self._q.get()
            if frame is None:
                break
            try:
                stdin.write(frame)
                self.frames += 1
            except Exception as e:
                # A broken pipe means ffmpeg died -- keep draining the queue so
                # the render thread's put_nowait never starts blocking on it.
                if not self.error:
                    self.error = f"ffmpeg stopped: {e}"
        try:
            stdin.close()
        except Exception:
            pass
        code = self._proc.wait()
        if self.error or code not in (0, None):
            # ffmpeg's own last line, whenever there is one: "broken pipe" only
            # says the encoder died, and the reason it died is here. Reading it
            # after the wait is safe because -loglevel error keeps the output
            # far short of the pipe buffer.
            tail = (self._proc.stderr.read() or b"").decode(
                "utf-8", "replace").strip()
            if tail:
                self.error = tail.splitlines()[-1]
            elif not self.error:
                self.error = f"ffmpeg exited {code}"


# ─── desktop wallpaper (Windows) ──────────────────────────────────────────────

#: Undocumented Progman message that makes Explorer split the desktop into a
#: SHELLDLL_DefView holding the icons and a WorkerW behind it holding the
#: wallpaper. Documented nowhere by Microsoft, relied on by every wallpaper
#: application on Windows.
_WM_SPAWN_WORKERW = 0x052C

#: Window styles and SetWindowPos flags, for the Windows 11 route below.
_GWL_STYLE, _GWL_EXSTYLE = -16, -20
_WS_CHILD, _WS_POPUP, _WS_CLIPCHILDREN = 0x40000000, 0x80000000, 0x02000000
#: Caption, sysmenu, thick frame and the two title-bar buttons. A desktop
#: child keeps none of them.
_WS_FRAME = 0x00C00000 | 0x00080000 | 0x00040000 | 0x00020000 | 0x00010000
_WS_EX_LAYERED, _LWA_ALPHA = 0x00080000, 0x02
_HWND_TOP = ctypes.c_void_p(0)
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOZORDER = 0x0001, 0x0002, 0x0004
_SWP_FRAMECHANGED, _SWP_SHOWWINDOW = 0x0020, 0x0040
_SWP_NOACTIVATE = 0x0010


def _user32():
    """user32 with handle-sized argument types set.

    Not optional: ctypes defaults every unspecified return type to C int, so
    on 64-bit Windows an HWND comes back truncated to its low 32 bits and the
    next call is handed a window that does not exist.
    """
    u = ctypes.WinDLL("user32", use_last_error=True)
    P, W = ctypes.c_void_p, ctypes.c_wchar_p
    u.FindWindowW.restype, u.FindWindowW.argtypes = P, (W, W)
    u.FindWindowExW.restype, u.FindWindowExW.argtypes = P, (P, P, W, W)
    u.SetParent.restype, u.SetParent.argtypes = P, (P, P)
    u.EnumWindows.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    u.SendMessageTimeoutW.argtypes = (P, ctypes.c_uint, P, P,
                                      ctypes.c_uint, ctypes.c_uint, P)
    u.GetWindowLongPtrW.restype, u.GetWindowLongPtrW.argtypes = P, (P, ctypes.c_int)
    u.SetWindowLongPtrW.restype = P
    u.SetWindowLongPtrW.argtypes = (P, ctypes.c_int, P)
    u.SetWindowPos.argtypes = (P, P, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_uint)
    u.SetLayeredWindowAttributes.argtypes = (P, ctypes.c_uint, ctypes.c_ubyte,
                                             ctypes.c_uint)
    u.InvalidateRect.argtypes = (P, P, ctypes.c_bool)
    return u


def pick_workerw(u, top_level):
    """Given the top-level windows, the WorkerW a wallpaper belongs in, or 0.

    Explorer paints the wallpaper on a window of class WorkerW. Sending
    Progman 0x052C makes it create a second one, and the two are siblings: the
    FIRST WorkerW owns SHELLDLL_DefView, which is the desktop icons, and the
    one to draw into is the NEXT sibling after it -- behind the icons rather
    than over them. Parenting into the first instead is the classic way to get
    this wrong and covers the icons up.

    Split out from find_workerw so the choice can be tested off Windows;
    everything around it is ctypes calls into user32.
    """
    for hwnd in top_level:
        if u.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None):
            sibling = u.FindWindowExW(None, hwnd, "WorkerW", None)
            if sibling:
                return sibling
    return 0


def pick_desktop_host(u, top_level, progman):
    """Where the visualiser goes, as (hwnd, layered). Windows.

    Two desktop layouts, and the same feature reaches both:

    Windows 7 to 10 answer 0x052C by creating a second *top-level* WorkerW
    behind the icons. pick_workerw finds it and a plain child window parented
    there is drawn, which is the technique every wallpaper app has used.

    Windows 11 (measured on build 26200) creates no such window. Progman has
    a WorkerW *child* instead, and it is the one painting the wallpaper --
    verified by asking each desktop window to PrintWindow itself. Two things
    stand between that and a visible window, and both are why this looked
    impossible: the WorkerW has no WS_CLIPCHILDREN, so it paints the wallpaper
    straight over anything parented into it, and a plain child of it is never
    composited at all -- it can be drawn into continuously and nothing reaches
    the screen. SHELLDLL_DefView, the one window in that tree that does show,
    is WS_EX_LAYERED. Give our window the same and it composites too, behind
    the icons where it belongs. That is what `layered` asks the caller for.
    """
    worker = pick_workerw(u, top_level)
    if worker:
        return worker, False
    if progman:
        child = u.FindWindowExW(progman, None, "WorkerW", None)
        if child:
            return child, True
    return 0, False


def find_desktop_host(u=None, wait=2.0):
    """The desktop window to draw into, and whether it needs a layered child.

    Explorer creates the WorkerW on its own message loop, so it is not there
    when SendMessageTimeout returns -- measured at a couple of seconds after
    an Explorer restart. Looking once reported the mode unavailable on a
    machine that supports it; `wait` bounds how long to keep asking.
    """
    u = u or _user32()
    progman = u.FindWindowW("Progman", None)
    if progman:
        u.SendMessageTimeoutW(progman, _WM_SPAWN_WORKERW, None, None,
                              0x0000, 1000, None)
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    deadline = _time.monotonic() + wait
    while True:
        tops = []

        def _cb(hwnd, _lparam):
            tops.append(hwnd)
            return True

        u.EnumWindows(proto(_cb), None)
        host = pick_desktop_host(u, tops, progman)
        if host[0] or _time.monotonic() >= deadline:
            return host
        _time.sleep(0.05)


# ─── desktop wallpaper (X11) ──────────────────────────────────────────────────

#: EWMH is the whole of what X11 offers here. A window whose _NET_WM_WINDOW_TYPE
#: is _NET_WM_WINDOW_TYPE_DESKTOP is the desktop as far as the window manager is
#: concerned: undecorated, off the taskbar, and stacked at the very bottom --
#: which is where the wallpaper goes and, on every desktop that draws icons,
#: underneath the window those icons live on.
_X11_TYPE = "_NET_WM_WINDOW_TYPE"
_X11_DESKTOP = "_NET_WM_WINDOW_TYPE_DESKTOP"
_X11_NORMAL = "_NET_WM_WINDOW_TYPE_NORMAL"
_X11_STATE = "_NET_WM_STATE"
#: BELOW is redundant with the desktop type on a well-behaved window manager and
#: is what saves the feature on one that only half-implements it. STICKY keeps
#: the wallpaper on every virtual desktop, which is what a wallpaper is.
_X11_STATES = ("_NET_WM_STATE_BELOW", "_NET_WM_STATE_SKIP_TASKBAR",
               "_NET_WM_STATE_SKIP_PAGER", "_NET_WM_STATE_STICKY")

_XA_ATOM, _PROP_REPLACE = 4, 0

#: ClientMessage, and the two masks that send it to the window manager rather
#: than to the root window itself.
_X11_CLIENT_MESSAGE = 33
_X11_WM_EVENT_MASK = (1 << 20) | (1 << 19)   # SubstructureRedirect|Notify
_XA_WINDOW = 33


class _XClientMessageData(ctypes.Union):
    _fields_ = [("b", ctypes.c_char * 20), ("s", ctypes.c_short * 10),
                ("l", ctypes.c_long * 5)]


class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                ("format", ctypes.c_int), ("data", _XClientMessageData)]


class _XEvent(ctypes.Union):
    # The pad is what makes this the size Xlib expects; without it XSendEvent
    # reads past the end of the buffer.
    _fields_ = [("type", ctypes.c_int), ("xclient", _XClientMessageEvent),
                ("pad", ctypes.c_long * 24)]


def _libx11():
    """Xlib, already loaded into this process by SDL's x11 driver.

    ctypes rather than a binding: there is no python3-xlib in the runtime
    dependencies and four calls do not earn one.
    """
    lib = ctypes.CDLL("libX11.so.6")
    # Every signature is spelled out because the ctypes defaults are wrong
    # here in both directions: a returned Atom would come back through c_int
    # and lose its top half on 64-bit, and a Window passed as a bare Python
    # int would go out as c_int and address some other window entirely.
    ulong, ptr, i = ctypes.c_ulong, ctypes.c_void_p, ctypes.c_int
    lib.XInternAtom.restype = ulong
    lib.XInternAtom.argtypes = [ptr, ctypes.c_char_p, i]
    lib.XChangeProperty.argtypes = [ptr, ulong, ulong, ulong, i, i, ptr, i]
    lib.XDeleteProperty.argtypes = [ptr, ulong, ulong]
    lib.XLowerWindow.argtypes = [ptr, ulong]
    lib.XMoveResizeWindow.argtypes = [ptr, ulong, i, i,
                                      ctypes.c_uint, ctypes.c_uint]
    lib.XSync.argtypes = [ptr, i]
    lib.XDefaultRootWindow.restype = ulong
    lib.XDefaultRootWindow.argtypes = [ptr]
    lib.XQueryTree.argtypes = [ptr, ulong, ctypes.POINTER(ulong),
                               ctypes.POINTER(ulong),
                               ctypes.POINTER(ctypes.POINTER(ulong)),
                               ctypes.POINTER(ctypes.c_uint)]
    lib.XFree.argtypes = [ptr]
    lib.XGetGeometry.argtypes = [ptr, ulong, ctypes.POINTER(ulong),
                                 ctypes.POINTER(i), ctypes.POINTER(i),
                                 ctypes.POINTER(ctypes.c_uint),
                                 ctypes.POINTER(ctypes.c_uint),
                                 ctypes.POINTER(ctypes.c_uint),
                                 ctypes.POINTER(ctypes.c_uint)]
    lib.XSendEvent.restype = i
    lib.XSendEvent.argtypes = [ptr, ulong, i, ctypes.c_long,
                               ctypes.POINTER(_XEvent)]
    lib.XGetWindowProperty.argtypes = [
        ptr, ulong, ulong, ctypes.c_long, ctypes.c_long, i, ulong,
        ctypes.POINTER(ulong), ctypes.POINTER(i), ctypes.POINTER(ulong),
        ctypes.POINTER(ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]
    return lib


def _x11_query_tree(lib, dpy, win):
    """(parent, [children, bottom first]) for one window. (0, []) on failure."""
    root_out, parent = ctypes.c_ulong(), ctypes.c_ulong()
    kids = ctypes.POINTER(ctypes.c_ulong)()
    count = ctypes.c_uint()
    if not lib.XQueryTree(dpy, win, ctypes.byref(root_out), ctypes.byref(parent),
                          ctypes.byref(kids), ctypes.byref(count)):
        return 0, []
    try:
        return parent.value, [kids[i] for i in range(count.value)]
    finally:
        if kids:
            lib.XFree(kids)


def x11_bottom_child(lib, dpy):
    """The window at the very bottom of the root's stack, or 0."""
    _parent, kids = _x11_query_tree(lib, dpy, lib.XDefaultRootWindow(dpy))
    return kids[0] if kids else 0


def x11_toplevel_of(lib, dpy, win):
    """The ancestor of `win` that is a direct child of the root -- or `win`.

    Some window managers reparent a client into a frame of their own. xfwm4
    does it even to a desktop window; muffin does not. Restacking happens in
    the root's stack, and what sits there is the frame, so lowering the client
    handle on a reparenting window manager moves nothing and the visuals stay
    in front of the icons.
    """
    root = lib.XDefaultRootWindow(dpy)
    cur = win
    for _ in range(16):        # a frame chain is one deep; the bound is for cycles
        parent, _kids = _x11_query_tree(lib, dpy, cur)
        if not parent or parent == root:
            return cur
        cur = parent
    return cur


def x11_window_rect(lib, dpy, win):
    """(x, y, w, h) of a window in its parent's coordinates, or None."""
    root = ctypes.c_ulong()
    x, y = ctypes.c_int(), ctypes.c_int()
    w, h = ctypes.c_uint(), ctypes.c_uint()
    bw, depth = ctypes.c_uint(), ctypes.c_uint()
    if not lib.XGetGeometry(dpy, win, ctypes.byref(root), ctypes.byref(x),
                            ctypes.byref(y), ctypes.byref(w), ctypes.byref(h),
                            ctypes.byref(bw), ctypes.byref(depth)):
        return None
    return x.value, y.value, w.value, h.value


def x11_window_origin(lib, dpy, win):
    """(x, y) of a window in its parent's coordinates, or None."""
    rect = x11_window_rect(lib, dpy, win)
    return None if rect is None else rect[:2]


def x11_align_frame(lib, dpy, win, want, move):
    """Nudge the window until its *frame* sits on `want`, not its client area.

    A reparenting window manager positions the frame and the client sits inside
    it, so asking for (0, 0) does not put the top-left pixel at (0, 0). xfwm4
    put the frame at (-5, -29), which left a black strip down the right of the
    screen and along the bottom. The border is not guessable -- it is a theme
    setting -- so measure what actually happened and correct by the difference.

    `move` places the client; returns True once the frame is where it should be.
    """
    want_x, want_y = want
    for _ in range(3):        # one correction is enough; the bound is for a fight
        top = x11_toplevel_of(lib, dpy, win)
        origin = x11_window_origin(lib, dpy, top)
        if origin is None:
            return False
        if origin == (want_x, want_y):
            return True
        here = x11_window_origin(lib, dpy, win) or (want_x, want_y)
        if top == win:
            # Not reparented, so the client *is* the frame and the window
            # manager simply put it elsewhere. Ask again, plainly.
            move(want_x, want_y)
        else:
            move(here[0] + (want_x - origin[0]),
                 here[1] + (want_y - origin[1]))
        lib.XSync(dpy, False)
    return x11_window_origin(lib, dpy, x11_toplevel_of(lib, dpy, win)) == \
        (want_x, want_y)


def x11_client_stack(lib, dpy):
    """The windows the window manager manages, bottom first.

    `_NET_CLIENT_LIST_STACKING` holds only real, managed windows, in the order
    the window manager stacks them -- which on a compositing one is also the
    order they are drawn. The root's own children are a different list and the
    wrong one to reason about: see x11_lower_to_bottom.

    Empty if the property is missing, which is the signal to fall back.
    """
    actual_type, actual_format = ctypes.c_ulong(), ctypes.c_int()
    nitems, bytes_after = ctypes.c_ulong(), ctypes.c_ulong()
    data = ctypes.POINTER(ctypes.c_ubyte)()
    prop = lib.XInternAtom(dpy, b"_NET_CLIENT_LIST_STACKING", False)
    if lib.XGetWindowProperty(
            dpy, lib.XDefaultRootWindow(dpy), prop, 0, 8192, False, _XA_WINDOW,
            ctypes.byref(actual_type), ctypes.byref(actual_format),
            ctypes.byref(nitems), ctypes.byref(bytes_after),
            ctypes.byref(data)) != 0 or not data:
        return []
    try:
        # Format 32 is an array of *long* in Xlib, not of uint32.
        if actual_format.value != 32:
            return []
        window = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))
        return [window[i] for i in range(nitems.value)]
    finally:
        lib.XFree(data)


#: The schema Nemo publishes its escape hatch under, and the key on it.
#: Absent on every desktop that is not Cinnamon/Nemo, which is the check.
_NEMO_SCHEMA, _NEMO_KEY = "org.nemo.desktop", "ignored-desktop-handlers"


def x11_wm_class(lib, dpy, win) -> str:
    """The window's WM_CLASS *instance* name, or "".

    WM_CLASS is two NUL-separated strings, instance then class. The instance
    is the half wanted here: Nemo's ignore list ships `conky` and
    `csd-background`, both instance names -- the classes are `Conky` and
    `Csd-background`.
    """
    actual_type, actual_format = ctypes.c_ulong(), ctypes.c_int()
    nitems, bytes_after = ctypes.c_ulong(), ctypes.c_ulong()
    data = ctypes.POINTER(ctypes.c_ubyte)()
    prop = lib.XInternAtom(dpy, b"WM_CLASS", False)
    if lib.XGetWindowProperty(
            dpy, win, prop, 0, 256, False, 0,
            ctypes.byref(actual_type), ctypes.byref(actual_format),
            ctypes.byref(nitems), ctypes.byref(bytes_after),
            ctypes.byref(data)) != 0 or not data:
        return ""
    try:
        raw = bytes(bytearray(data[i] for i in range(nitems.value)))
        return raw.split(b"\0")[0].decode("utf-8", "replace")
    finally:
        lib.XFree(data)


def nemo_allow_desktop_window(wm_class: str, run=None) -> str:
    """Stop Nemo abandoning the desktop icons because of our window.

    Nemo looks for other `_NET_WM_WINDOW_TYPE_DESKTOP` windows **when it
    starts**, and if it finds one it skips managing the desktop altogether --
    "Desktop already managed by another application, skipping desktop setup."
    Measured on Cinnamon 6.0.5, 14 Sep 2026: with the visualiser already in
    wallpaper mode, `nemo-desktop` exits without drawing a single icon.

    That is why this reads as the visuals *covering* the icons while the
    desklets still show over the top: the desklets are drawn by the Cinnamon
    shell and never went away, and the icons were never drawn at all. Nothing
    is stacked wrongly, so nothing measuring the stack can see it -- which is
    why `_hold_wallpaper_bottom`, which is right about everything it checks,
    does not help here.

    The order decides whether it bites. Turning wallpaper mode on while Nemo is
    already up is fine; Nemo only checks at startup. It is the sessions where
    Nemo starts second that lose the icons -- a login with
    autostart-as-wallpaper, a Cinnamon restart, Nemo restarting for any reason.

    `ignored-desktop-handlers` is the escape hatch Nemo names in that warning.
    conky and csd-background ship on it and a MilkDrop wallpaper has the same
    claim, so this puts the app on it once and leaves it there -- removing it
    again on the way out would just lose the icons on the next restart.

    Returns a sentence for the log when it changed something and "" otherwise,
    including on every desktop that is not Nemo, where the schema is absent.
    `run` is a seam for the tests; it defaults to subprocess.run.
    """
    # No stray quoting worries and no way to inject into the gsettings
    # argument: anything that is not a plain WM_CLASS is simply not handled.
    if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,64}", wm_class or ""):
        return ""
    run = subprocess.run if run is None else run

    def gs(*args):
        return run(["gsettings", *args], capture_output=True, text=True,
                   timeout=10, env=child_env())

    try:
        got = gs("get", _NEMO_SCHEMA, _NEMO_KEY)
    except (OSError, subprocess.SubprocessError):
        return ""                      # no gsettings at all
    if got.returncode != 0:
        return ""                      # not a Nemo desktop
    try:
        # GVariant prints a string array as a Python list literal does --
        # except when it is empty, which prints as `@as []`: the type has to
        # be spelled out because `[]` alone would be ambiguous. Somebody who
        # has cleared this key is exactly the person who needs it written.
        text = got.stdout.strip()
        if text.startswith("@"):
            text = text.partition(" ")[2]
        current = ast.literal_eval(text)
        if not isinstance(current, (list, tuple)):
            return ""
        current = [str(x) for x in current]
    except (ValueError, SyntaxError):
        return ""
    if wm_class in current:
        return ""
    try:
        set_ = gs("set", _NEMO_SCHEMA, _NEMO_KEY, repr(current + [wm_class]))
    except (OSError, subprocess.SubprocessError):
        return ""
    if set_.returncode != 0:
        return ""
    return (f"told Nemo to keep drawing desktop icons over {wm_class} "
            f"({_NEMO_SCHEMA} {_NEMO_KEY})")


def x11_restack_below(lib, dpy, win, sibling):
    """Ask the window manager to put `win` directly below `sibling`.

    XLowerWindow talks to the X server. A compositing window manager keeps its
    own stack and paints from that, so a raw lower is either undone on its
    next restack or simply never reaches the screen. `_NET_RESTACK_WINDOW` is
    the EWMH way to ask the window manager itself.
    """
    ev = _XEvent()
    ev.xclient.type = _X11_CLIENT_MESSAGE
    ev.xclient.serial = 0
    ev.xclient.send_event = 1
    ev.xclient.display = dpy
    ev.xclient.window = win
    ev.xclient.message_type = lib.XInternAtom(
        dpy, b"_NET_RESTACK_WINDOW", False)
    ev.xclient.format = 32
    ev.xclient.data.l[0] = 2          # source indication: a pager, near enough
    ev.xclient.data.l[1] = sibling
    ev.xclient.data.l[2] = 1          # Below
    return bool(lib.XSendEvent(dpy, lib.XDefaultRootWindow(dpy), False,
                               _X11_WM_EVENT_MASK, ctypes.byref(ev)))


def x11_lower_to_bottom(lib, dpy, win, timeout=1.0):
    """Lower the window until it is below everything the desktop draws.

    One XLowerWindow straight after the map gets thrown away. A window manager
    restacks a newly mapped window on its own event loop, which runs after
    ours, so the window comes back up above the one drawing the desktop icons
    -- measured under muffin, where a single lower left the visualiser on top
    and a late one stuck. So lower, then check, rather than lower and hope.

    Both halves of that had to change when this met a real Cinnamon desktop
    rather than the Xephyr rig it was written against.

    **Telling X is not enough on a compositing window manager.** XLowerWindow
    moves the window in the X server's stack; muffin keeps its own and paints
    from that, so a raw lower is either undone on its next restack or never
    reaches the screen. Measured on Cinnamon 6.0.5: the raw X stack had the
    visualiser below nemo-desktop while _NET_CLIENT_LIST_STACKING -- and the
    screen -- had it above, icons covered. _NET_RESTACK_WINDOW asks the window
    manager itself, and that is what puts the icons back on top.

    **The root's children are the wrong list to test against.** A real session
    has dozens (81 on the Mint box) and the lowest are unmapped utility
    windows nothing can get under, so `kids[0]` is never the visualiser however
    far it is lowered: the old test could only time out and toast, on desktops
    where the feature was in fact working. The managed-client stack holds only
    real windows, in the order they are drawn.

    Falls back to the old behaviour where no client stack is published.

    Returns True if it got there.
    """
    deadline = time.monotonic() + timeout
    stale, was = 0, -1
    while True:
        # Re-read each time round: a window manager can reparent late, and the
        # frame that exists now is the one that has to move.
        top = x11_toplevel_of(lib, dpy, win)
        stack = x11_client_stack(lib, dpy)
        if stack:
            if stack[0] == win:
                return True
            # A window manager that will not honour _NET_RESTACK_WINDOW (xfwm4
            # does not) leaves us exactly where we were, and waiting out the
            # whole timeout stalls the render thread for a second on every
            # toggle. Three rounds with no movement is a refusal, not slowness.
            here = stack.index(win) if win in stack else -1
            stale = stale + 1 if here >= 0 and here == was else 0
            was = here
            if stale >= 3:
                return False
            # EWMH takes the client window, not the frame.
            x11_restack_below(lib, dpy, win, stack[0])
        lib.XLowerWindow(dpy, top)     # for a window manager with no EWMH restack
        lib.XSync(dpy, False)
        if not stack and x11_bottom_child(lib, dpy) == top:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def x11_set_atoms(lib, dpy, win, prop, values):
    """Replace an atom-list property on a window. An empty list deletes it."""
    target = lib.XInternAtom(dpy, prop.encode(), False)
    if not values:
        lib.XDeleteProperty(dpy, win, target)
        return
    atoms = [lib.XInternAtom(dpy, v.encode(), False) for v in values]
    # Format 32 means "an array of long", not of int32 -- an Xlib wart that
    # costs a garbled property on any 64-bit machine if it is taken literally.
    buf = (ctypes.c_ulong * len(atoms))(*atoms)
    lib.XChangeProperty(dpy, win, target, _XA_ATOM, 32, _PROP_REPLACE,
                        buf, len(atoms))


def wallpaper_support(env=None):
    """Can this session host wallpaper mode? (ok, reason).

    `reason` is what to tell the user when it cannot, and it is deliberately
    specific: "not supported" invites a bug report, and the fix here is a
    thing the user can actually do.
    """
    env = os.environ if env is None else env
    if sys.platform == "win32":
        return True, ""
    if sys.platform != "linux":
        return False, "Wallpaper mode is available on Windows and on Linux."
    # A Wayland session counts, as long as XWayland is up. The visualiser's
    # window is an X11 surface either way, and a compositor that manages X11
    # clients at all reads _NET_WM_WINDOW_TYPE while it does it -- measured
    # under GNOME/mutter on Wayland, where a desktop-type XWayland window went
    # to the bottom of _NET_CLIENT_LIST_STACKING and stayed there while other
    # windows were mapped and unmapped over it. Refusing every Wayland session,
    # which is what this did until 2026-09-13, turned the feature off on the
    # most common Linux desktop there is.
    #
    # A compositor with no desktop layer to offer is not refused here either:
    # guessing per compositor is a list that goes stale, and x11_lower_to_bottom
    # already *measures* where the window landed and says so. So the only thing
    # left to refuse is having no X server to ask.
    if not env.get("DISPLAY"):
        return False, ("Wallpaper mode needs an X server, and this session has "
                       "neither Xorg nor XWayland running.")
    return True, ""


def thumbs_dir() -> str:
    return os.path.join(user_data_dir(), "thumbs")


def collections_path() -> str:
    """Where named collections live.

    The data directory, not the config one, and deliberately not a per-app
    path handed down by a subclass: a collection is a list of preset names,
    which means the same thing to anything that plays the same library.
    """
    return os.path.join(user_data_dir(), "collections.json")


def thumb_path(preset_name: str) -> str:
    """Cache path for one preset's thumbnail.

    Named by hash rather than by preset name: names come from packs assembled
    by hundreds of different people and carry slashes, colons and characters
    that are illegal in a filename on one platform or another. The two-hex
    subdirectory keeps ten thousand files out of a single directory, which
    some filesystems handle badly and every file manager handles slowly.
    """
    import hashlib
    h = hashlib.sha1(preset_name.encode("utf-8", "replace")).hexdigest()
    return os.path.join(thumbs_dir(), h[:2], h + ".jpg")


class _ThumbWriter(threading.Thread):
    """Scales captured frames down and writes them, off the render thread.

    QImage is not a widget and its scaling and encoding are safe away from the
    GUI thread; what is not safe is doing them in the render loop, where a
    JPEG encode would land as a dropped frame.
    """

    WIDTH = 240

    def __init__(self):
        super().__init__(daemon=True)
        import queue
        self._q = queue.Queue(maxsize=4)

    def submit(self, path: str, w: int, h: int, buf: bytes, done=None):
        """`done` is called on this thread once the file is on disk.

        Not when the frame is handed over: the browser draws whatever is at
        the path the moment it is told, and told too early it caches the miss
        and the row stays blank until something else invalidates it.
        """
        try:
            self._q.put_nowait((path, w, h, buf, done))
        except Exception:
            pass          # a thumbnail is never worth waiting for

    def run(self):
        while True:
            path, w, h, buf, done = self._q.get()
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                img = QImage(buf, w, h, w * 3, QImage.Format.Format_RGB888)
                # copy() because QImage does not own that buffer, and mirrored
                # because glReadPixels hands back rows bottom-up.
                img = img.copy().mirrored(False, True).scaledToWidth(
                    self.WIDTH, Qt.TransformationMode.SmoothTransformation)
                # Written under a temporary name and moved into place, because
                # the browser reads this directory while the writer is filling
                # it: QImage.save writes in place, so a reader that opened the
                # path mid-encode got a truncated JPEG and drew nothing.
                # os.replace is atomic on both platforms.
                part = path + ".part"
                # Qt carries JPEG as a plugin; PNG is built into QtGui. A frozen
                # build that lost the imageformats plugin would otherwise write
                # nothing at all and never say so. The name still ends .jpg --
                # Qt loads images by content, not by extension -- so the browser
                # finds it either way.
                if img.save(part, "JPG", 80) or img.save(part, "PNG"):
                    os.replace(part, path)
                    if done is not None:
                        done()
            except Exception as e:
                print(f"[thumb] {path}: {e}", flush=True)


_THUMB_WRITER = None


def thumb_writer() -> "_ThumbWriter":
    global _THUMB_WRITER
    if _THUMB_WRITER is None:
        _THUMB_WRITER = _ThumbWriter()
        _THUMB_WRITER.start()
    return _THUMB_WRITER


# ─── burn-in ─────────────────────────────────────────────────────────────────
#
# A picture of ours drawn into the *preset's* framebuffer every frame, so the
# preset's own warp, decay and echo take hold of it. Not a sprite -- a sprite
# sits on top, finished -- and not a live texture either: that one is sampled
# by name, wherever the preset happens to use it, and comes out crisp. This
# one is laid over the whole frame and smeared by the warp. Two different
# effects; this is the one that does not need the preset's cooperation,
# because it does not care what the preset asks for.

#: Longest side of the burn-in texture. It is drawn into a framebuffer the
#: size of the window at most, so anything bigger costs upload bandwidth every
#: frame and buys nothing on screen.
BURN_MAX_SIDE = 512

#: What Qt reads itself, a frame at a time. Everything else goes to ffmpeg.
BURN_IMAGE_EXTENSIONS = (".gif", ".webp", ".apng", ".png", ".jpg", ".jpeg",
                         ".bmp")
BURN_EXTENSIONS = BURN_IMAGE_EXTENSIONS + VIDEO_EXTENSIONS


def burn_fit(src_w, src_h, win_w, win_h, size, x, y):
    """Where a src_w x src_h picture goes inside a win_w x win_h canvas.

    A plain top-left (left, top, width, height) in pixels. `size` is the
    fraction of the canvas height the picture covers, and `x`, `y` place its
    centre as fractions of the canvas -- (0.5, 0.5) being the middle, y
    counting down. Fractions rather than pixels so a setting keeps meaning the
    same thing when the window is resized or goes fullscreen.

    Note this is *not* what projectm_opengl_burn_texture takes. See
    burn_canvas for why nothing but the whole window is ever passed to it.
    """
    if min(src_w, src_h, win_w, win_h) <= 0:
        return (0, 0, 0, 0)
    h = max(1, int(round(win_h * min(1.0, max(0.02, size)))))
    w = max(1, int(round(h * src_w / src_h)))
    return (int(round(win_w * x - w / 2.0)),
            int(round(win_h * y - h / 2.0)), w, h)


def burn_canvas(frame, win_w: int, win_h: int, rect, alpha=1.0) -> bytes:
    """A window-sized RGBA buffer with `frame` drawn into `rect`, GL side up.

    Why a whole canvas rather than telling projectM where to put the picture:
    the `left` and `top` of projectm_opengl_burn_texture do not work at the
    pinned commit. CopyTexture's vertex shader multiplies the other way round
    -- `vec4(position, 0.0, 1.0) * vertex_transformation` -- so with glm's
    column-major storage those two land in `gl_Position.w` and come out as a
    projective shear instead of a move. Measured: asking for a 173x173 patch
    560px left of centre drew a trapezoid across half the screen. The scale
    survives it, being on the diagonal, so the one call that behaves is the
    whole window at (0, 0) and the placing is done here.

    The surround is transparent and projectM honours it -- measured too, and
    it is what makes this work at all: an opaque canvas would wipe the
    preset's feedback buffer every frame instead of adding to it.

    Flipped bottom to top because a GL texture's first row is the bottom of
    the picture and a QImage's is the top. Without it the picture is drawn
    upside down in the window, which is precisely what the first run showed.

    `alpha` is what makes this a burn-in rather than an overlay. Fully opaque,
    each frame paints over the warped trail the last one left and the picture
    just sits there crisply, which is what a sprite already does better.
    Under 1 the preset's own feedback shows through and accumulates, so the
    picture smears away with the warp between frames -- measured, and the
    difference between the two is the whole feature.
    """
    canvas = QImage(int(win_w), int(win_h), QImage.Format.Format_RGBA8888)
    canvas.fill(0)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setOpacity(max(0.0, min(1.0, float(alpha))))
    painter.drawImage(QRect(*rect), frame)
    painter.end()
    return _image_bytes(canvas.mirrored(False, True))


def _image_bytes(img) -> bytes:
    """A QImage's pixels, through whichever Qt binding is loaded.

    PySide6 hands back a memoryview that knows its own length; PyQt6 hands
    back a sip.voidptr that does not, and bytes() on that raises rather than
    returning something short. One of the few places the two bindings differ
    in a way the re-export table cannot paper over.
    """
    ptr = img.constBits()
    try:
        return bytes(ptr)
    except TypeError:
        ptr.setsize(img.sizeInBytes())
        return bytes(ptr)


def _rgba_frame(img):
    """A QImage ready to be drawn into the canvas, scaled down if it is large.

    RGBA8888 rather than RGB888 because a QImage pads every row to four bytes,
    and an odd width in RGB then arrives sheared -- the same trap glReadPixels
    has with GL_PACK_ALIGNMENT, seen from the other end.
    """
    side = max(img.width(), img.height())
    if side > BURN_MAX_SIDE:
        img = img.scaled(
            max(1, img.width() * BURN_MAX_SIDE // side),
            max(1, img.height() * BURN_MAX_SIDE // side),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
    return img.convertToFormat(QImage.Format.Format_RGBA8888)


class _ImageFrames:
    """Frames out of an animated image, through Qt's own reader.

    QImageReader walks a GIF or an animated WEBP one frame at a time and
    reports each frame's own delay, which is what makes a GIF look right: the
    delay is per frame in the format, not one rate for the file. Decoding is
    cheap enough to do on the render thread -- a 480x480 GIF frame is LZW over
    a palette -- which is why this is not a thread and the video one is.
    """

    def __init__(self, path: str):
        self.path = path
        self._reader = None
        self._due = 0.0
        self._open()

    def _open(self):
        try:
            r = QImageReader(self.path)
            r.setAutoTransform(True)
            self._reader = r if r.canRead() else None
        except Exception:
            self._reader = None

    def next(self, now: float):
        """(w, h, RGBA bytes) when a new frame is due, else None."""
        if self._reader is None or now < self._due:
            return None
        img = self._reader.read()
        if img.isNull():
            self._open()                  # the end of the animation; loop
            if self._reader is None:
                return None
            img = self._reader.read()
            if img.isNull():
                self._reader = None       # not a picture at all
                return None
        delay = self._reader.nextImageDelay() / 1000.0
        # A still reports no delay, so it is read once and then held for ever.
        # Without that, a JPEG would be decoded sixty times a second to
        # produce the same pixels.
        self._due = (now + delay) if delay > 0 else float("inf")
        return _rgba_frame(img)

    def stop(self):
        self._reader = None


#: ffmpeg prints what it found before it complains about having no output
#: file. Restricted to the Video: line because the banner is full of numbers.
_VIDEO_SIZE = re.compile(r"\b(\d{2,5})x(\d{2,5})\b")


def video_frame_size(text: str):
    """(w, h) out of ffmpeg's own report of a file, or (0, 0).

    From ffmpeg rather than ffprobe: ffprobe is a second binary and a frozen
    build ships one, not two. Split from the call that produces the text so
    the parsing can be tested against real ffmpeg output without a video file.
    """
    for line in (text or "").splitlines():
        if "Video:" not in line:
            continue
        m = _VIDEO_SIZE.search(line)
        if m:
            return (int(m.group(1)), int(m.group(2)))
    return (0, 0)


def burn_scaled_size(w: int, h: int):
    """`w` x `h` brought under BURN_MAX_SIDE, both sides even.

    Even because every encoder ffmpeg might be asked to scale for wants it,
    and an odd size is the kind of thing that works on one machine.
    """
    if w <= 0 or h <= 0:
        return (0, 0)
    side = max(w, h)
    if side > BURN_MAX_SIDE:
        w = w * BURN_MAX_SIDE // side
        h = h * BURN_MAX_SIDE // side
    return (max(2, w - (w % 2)), max(2, h - (h % 2)))


class _VideoFrames(threading.Thread):
    """Frames out of a video file, through ffmpeg, on a thread of its own.

    Not on the render thread: a pipe read blocks, and a decoder that stalls
    for a frame time is a frame the visualiser did not draw. One frame of
    slack is kept and the newest wins; when the decoder is late the last frame
    is simply used again, which for something being smeared through a preset's
    warp nobody can see. `-re` paces ffmpeg at the file's own rate, without
    which it decodes as fast as the pipe drains and burns a core to produce
    frames that are thrown away.
    """

    def __init__(self, path: str):
        super().__init__(daemon=True)
        self.path = path
        self.width = self.height = 0
        self.error = ""
        self._ffmpeg = find_ffmpeg()
        self._proc = None
        self._burn_on = True
        self._burn_lock = threading.Lock()
        self._burn_frame = None
        if not self._ffmpeg:
            self.error = ("ffmpeg was not found, and a video needs it. A GIF "
                          "does not \u2014 that goes through Qt.")
            return
        try:
            said = _subprocess.run(
                [self._ffmpeg, "-hide_banner", "-i", path],
                capture_output=True, text=True, timeout=30, env=child_env(),
                creationflags=(_subprocess.CREATE_NO_WINDOW
                               if sys.platform == "win32" else 0)).stderr
        except Exception as e:
            self.error = f"ffmpeg could not read that file: {e}"
            return
        w, h = burn_scaled_size(*video_frame_size(said))
        if w <= 0:
            self.error = "ffmpeg found no video in that file."
            return
        self.width, self.height = w, h
        self.start()

    def run(self):
        need = self.width * self.height * 4
        cmd = [self._ffmpeg, "-v", "error", "-re", "-stream_loop", "-1",
               "-i", self.path, "-vf", f"scale={self.width}:{self.height}",
               "-pix_fmt", "rgba", "-f", "rawvideo", "-"]
        try:
            self._proc = _subprocess.Popen(
                cmd, stdout=_subprocess.PIPE, stderr=_subprocess.PIPE,
                env=child_env(),
                creationflags=(_subprocess.CREATE_NO_WINDOW
                               if sys.platform == "win32" else 0))
        except Exception as e:
            self.error = f"could not start ffmpeg: {e}"
            return
        while self._burn_on:
            buf = self._proc.stdout.read(need)
            if len(buf) < need:
                break
            with self._burn_lock:
                self._burn_frame = buf
        # Always read a dead helper's stderr: the one line that says why is
        # thrown away otherwise, which this project has already paid for once.
        if self._burn_on:
            try:
                why = (self._proc.stderr.read() or b"").decode(
                    "utf-8", "replace").strip()
            except Exception:
                why = ""
            if why:
                self.error = why.splitlines()[-1]
                print(f"[burn] ffmpeg: {self.error}", flush=True)

    def next(self, _now=0.0):
        """The newest decoded frame, once. None when there is nothing new."""
        with self._burn_lock:
            buf, self._burn_frame = self._burn_frame, None
        if buf is None:
            return None
        # copy() because QImage does not own that buffer and the next frame
        # will overwrite the name holding it.
        return QImage(buf, self.width, self.height,
                      QImage.Format.Format_RGBA8888).copy()

    def stop(self):
        self._burn_on = False
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass


def frame_source(path: str, image_extensions=BURN_IMAGE_EXTENSIONS):
    """Something with .next(now) and .stop() for `path`, or None.

    The extension decides, not the content: Qt would happily read the first
    frame of some video containers and then report no animation at all, which
    looks like a still picture rather than like a file we cannot play.

    `image_extensions` differs between the two callers: burn-in takes what a
    user picks in a dialog, a live texture takes only what libprojectM cannot
    read for itself, so a .png stays projectM's own business.
    """
    if not path or not os.path.isfile(path):
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext in image_extensions:
        return _ImageFrames(path)
    if ext in VIDEO_EXTENSIONS:
        return _VideoFrames(path)
    return None


# ─── live textures ─────────────────────────────────────────────────────────
#
# A texture a preset or a sprite names, answered by us instead of by
# libprojectM's own disk scan. Two things fall out of that.
#
# Formats: its scanner reads seven extensions and none of them animate. A
# .gif in a texture pack is invisible to it -- the Cream of the Crop pack has
# three -- and so is every .webp, .tif and .svg. Qt reads all of those, so
# the callback hands them over and nothing else has to change.
#
# Movement: the callback may return an *existing* GL texture id, and projectM
# wraps it without taking ownership (TextureManager.cpp passes false and says
# so). It caches the wrapper, so the preset samples our texture every frame
# and a glTexSubImage2D between frames is live inside the preset -- a GIF
# playing where the preset expected a still picture, warped and lit by
# whatever that preset does to it. Sprites come free: libprojectM resolves a
# sprite's img= through the same TextureManager.
#
# Scoping said none of this was possible. That was wrong, and the route was
# there all along: projectm_set_texture_load_event_callback, 4.2.0, exported
# by both shipped binaries.

#: Most live textures at once. They are never given back while projectM is
#: running -- it is holding the ids -- so this is the ceiling on what one
#: session can accumulate: eight 512x512 RGBA textures is 8 MB.
LIVE_MAX_TEXTURES = 8


def live_texture_file(name: str, paths) -> str:
    """The file that answers for texture `name`, or "".

    Only files libprojectM cannot read itself: anything on its own list is
    left to it, same picture, one less moving part. First match wins in the
    order the paths are given, which is the order its scanner uses too, so a
    texture override still stands in front of the pack.

    The name is reduced to a basename first. A preset asking for
    "../../../.ssh/id_rsa" gets a stem, not a path out of the directories the
    user pointed at.
    """
    want = os.path.splitext(os.path.basename(name or ""))[0].lower()
    if not want:
        return ""
    for d in paths:
        try:
            entries = sorted(os.listdir(d))
        except OSError:
            continue
        for f in entries:
            stem, ext = os.path.splitext(f)
            if (stem.lower() == want
                    and ext.lower() in LIVE_IMAGE_EXTENSIONS + VIDEO_EXTENSIONS):
                full = os.path.join(d, f)
                if os.path.isfile(full):
                    return full
    return ""


def gl_upload_rgba(tex: int, size, w: int, h: int, buf: bytes):
    """Put an RGBA frame in `tex`, making it if there is none. (tex, (w, h)).

    glTexSubImage2D while the size holds, because reallocating the whole
    texture every frame is what makes a moving picture cost more than the
    preset it is being drawn into. Render thread: it is the only one that may
    touch the context.
    """
    if tex and tuple(size) == (w, h):
        glBindTexture(GL_TEXTURE_2D, tex)
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, w, h,
                        GL_RGBA, GL_UNSIGNED_BYTE, buf)
    else:
        if tex:
            try:
                glDeleteTextures(1, [tex])
            except Exception:
                pass
        tex = int(glGenTextures(1))
        glBindTexture(GL_TEXTURE_2D, tex)
        for pname, value in ((GL_TEXTURE_MIN_FILTER, GL_LINEAR),
                             (GL_TEXTURE_MAG_FILTER, GL_LINEAR),
                             (GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE),
                             (GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)):
            glTexParameteri(GL_TEXTURE_2D, pname, value)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0,
                     GL_RGBA, GL_UNSIGNED_BYTE, buf)
    glBindTexture(GL_TEXTURE_2D, 0)
    return tex, (w, h)


class _LiveTexture:
    """One texture of ours that projectM is sampling.

    The size is pinned at the first frame and every later frame is scaled to
    it. projectM recorded the width and height when it wrapped the id and
    never asks again, so a source whose frames are not all one size -- which
    a GIF is allowed to be -- would otherwise be uploaded into a texture of
    the wrong shape.

    Not flipped, unlike the burn-in: libprojectM loads its own textures with
    stb_image and never sets stbi_set_flip_vertically_on_load, so top row
    first is what every stock texture already is and what presets expect.
    """

    def __init__(self, name: str, path: str, src, tex: int, w: int, h: int):
        self.name, self.path, self.src = name, path, src
        self.tex, self.w, self.h = tex, w, h

    def refresh(self, now: float) -> bool:
        """Upload the next frame if the decoder has one. Render thread.

        False when there is nothing to do, which for a still picture is every
        frame after the first: _ImageFrames holds a file with no animation
        delay for ever rather than decoding the same pixels sixty times a
        second.
        """
        if self.src is None:
            return False
        img = self.src.next(now)
        if img is None:
            return False
        if img.width() != self.w or img.height() != self.h:
            img = img.scaled(self.w, self.h,
                             Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        img = img.convertToFormat(QImage.Format.Format_RGBA8888)
        gl_upload_rgba(self.tex, (self.w, self.h), self.w, self.h,
                       _image_bytes(img))
        return True

    def stop(self):
        """Stop decoding. The GL texture is deliberately left alone: projectM
        is still holding the id, and deleting it under a live preset is a
        sampler reading a texture that is not there any more."""
        if self.src is not None:
            self.src.stop()
            self.src = None


# ─── dual preset ───────────────────────────────────────────────────────────
#
# Two projectM instances in one GL context, each drawing into a colour
# texture of its own, mixed by one shader pass. Blended over each other
# rather than side by side, which is what was asked for and is also the only
# one of the two that needs a shader at all.
#
# glBlitFramebuffer would be the obvious way to get a framebuffer onto the
# screen and it cannot do this: it ignores blending entirely. Hence a program
# of our own -- the one piece of hand-written OpenGL in the application.

#: Where the crossfader sits: 0 is visual 1 alone, 1 is visual 2 alone,
#: 0.5 is both at even weight.
DUAL_CROSSFADE_DEFAULT = 0.5

def display_no(index: int) -> str:
    """A monitor's number as a person counts them, from one."""
    return str(int(index) + 1)


#: How far one press of a crossfade key moves it. Twenty steps end to end:
#: few enough to cross in a second of held key, fine enough to place.
CROSSFADE_STEP = 0.05


def dual_crossfade(cfg: dict) -> float:
    """Read the crossfader, falling back to the strength it replaced.

    Up to 2.1.6 this number was a strength: how hard visual 2 screened over
    visual 1, running from "visual 1 alone" to "both, hard". The crossfader
    ends at *visual 2 alone* instead, so the two are not the same control --
    but they agree at 0 and they agree in the middle, and the middle is where
    a config that was never touched is sitting. Carrying the old key over is
    therefore right for the default and close for everything else, which beats
    snapping every upgraded config back to 0.5.
    """
    x = cfg.get("dual_crossfade")
    if x is None:
        x = cfg.get("dual_opacity", DUAL_CROSSFADE_DEFAULT)
    try:
        return min(1.0, max(0.0, float(x)))
    except (TypeError, ValueError):
        return DUAL_CROSSFADE_DEFAULT

_BLEND_VERT = """#version 330 core
out vec2 uv;
void main()
{
    // One oversized triangle, not a quad: three vertices, no buffer and no
    // vertex attributes to set up. IDs 0,1,2 give (0,0) (2,0) (0,2).
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    uv = p;
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

_BLEND_FRAG = """#version 330 core
in vec2 uv;
out vec4 colour;
uniform sampler2D visual_a;
uniform sampler2D visual_b;
uniform float crossfade;
void main()
{
    // Weight BOTH, not just the second one. Scaling b alone can reach
    // "visual 1" and "both at once" but never "visual 2 alone", which is
    // the far end of a crossfader and half of what it is for.
    vec3 a = texture(visual_a, uv).rgb * (1.0 - crossfade);
    vec3 b = texture(visual_b, uv).rgb * crossfade;
    // Screen rather than add. Two bright presets added together clip to
    // white and both shapes disappear; screen keeps each one readable and
    // cannot blow past 1 however bright they get. At either end one term is
    // zero and screen leaves the other exactly as it was.
    colour = vec4(1.0 - (1.0 - a) * (1.0 - b), 1.0);
}
"""


def _compile_shader(kind, source: str) -> int:
    """One shader, compiled, or 0 with the reason printed.

    A driver that refuses this is a driver that cannot run projectM either,
    so the failure is reported and dual mode declines rather than the
    application ending.
    """
    shader = glCreateShader(kind)
    glShaderSource(shader, source)
    glCompileShader(shader)
    if not glGetShaderiv(shader, GL_COMPILE_STATUS):
        why = glGetShaderInfoLog(shader)
        if isinstance(why, bytes):
            why = why.decode("utf-8", "replace")
        print(f"[dual] shader: {why.strip()}", flush=True)
        glDeleteShader(shader)
        return 0
    return shader


class _Blender:
    """Two offscreen targets and the one draw call that mixes them.

    Everything here belongs to the render thread: it is the only one with the
    GL context. Built the first time it is used and rebuilt whenever the
    window changes size, because a framebuffer's colour texture is a fixed
    size and a stale one would show the old window letterboxed into the new.
    """

    def __init__(self):
        self.size = (0, 0)
        self.error = ""
        self._fbo = [0, 0]
        self._tex = [0, 0]
        self._depth = [0, 0]
        self._program = 0
        self._vao = 0
        self._uniform = {}

    def ready(self, w: int, h: int) -> bool:
        """True when both targets exist at this size, building them if not."""
        if self.error or w <= 0 or h <= 0:
            return False
        if self._program and self.size == (w, h):
            return True
        try:
            if not self._program:
                self._build_program()
            self._build_targets(w, h)
            self.size = (w, h)
            return True
        except Exception as why:
            # Said once, and dual mode goes back off rather than the loop
            # trying again sixty times a second.
            self.error = str(why)
            print(f"[dual] {why}", flush=True)
            self.drop()
            return False

    def _build_program(self):
        vert = _compile_shader(GL_VERTEX_SHADER, _BLEND_VERT)
        frag = _compile_shader(GL_FRAGMENT_SHADER, _BLEND_FRAG)
        if not vert or not frag:
            raise RuntimeError("the blend shader would not compile")
        program = glCreateProgram()
        glAttachShader(program, vert)
        glAttachShader(program, frag)
        glLinkProgram(program)
        glDeleteShader(vert)
        glDeleteShader(frag)
        if not glGetProgramiv(program, GL_LINK_STATUS):
            why = glGetProgramInfoLog(program)
            if isinstance(why, bytes):
                why = why.decode("utf-8", "replace")
            glDeleteProgram(program)
            raise RuntimeError(f"the blend shader would not link: {why.strip()}")
        self._program = int(program)
        self._uniform = {n: glGetUniformLocation(self._program, n)
                         for n in ("visual_a", "visual_b", "crossfade")}
        # A core profile refuses to draw without one bound, even for a
        # triangle that reads no vertex attributes at all.
        self._vao = int(glGenVertexArrays(1))

    def _build_targets(self, w: int, h: int):
        self._drop_targets()
        for i in (0, 1):
            tex = int(glGenTextures(1))
            glBindTexture(GL_TEXTURE_2D, tex)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0,
                         GL_RGBA, GL_UNSIGNED_BYTE, None)
            for pname, value in ((GL_TEXTURE_MIN_FILTER, GL_LINEAR),
                                 (GL_TEXTURE_MAG_FILTER, GL_LINEAR),
                                 (GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE),
                                 (GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)):
                glTexParameteri(GL_TEXTURE_2D, pname, value)
            glBindTexture(GL_TEXTURE_2D, 0)
            fbo = int(glGenFramebuffers(1))
            glBindFramebuffer(GL_FRAMEBUFFER, fbo)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                   GL_TEXTURE_2D, tex, 0)
            # projectM asks SDL for a depth buffer, so give each target one
            # rather than find out which preset needs it.
            depth = int(glGenRenderbuffers(1))
            glBindRenderbuffer(GL_RENDERBUFFER, depth)
            glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT24, w, h)
            glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
                                      GL_RENDERBUFFER, depth)
            done = glCheckFramebufferStatus(GL_FRAMEBUFFER)
            glBindFramebuffer(GL_FRAMEBUFFER, 0)
            self._fbo[i], self._tex[i], self._depth[i] = fbo, tex, depth
            if done != GL_FRAMEBUFFER_COMPLETE:
                raise RuntimeError(f"framebuffer {i} is not complete ({done})")

    def target(self, i: int) -> int:
        """The framebuffer the i-th visual draws into."""
        return self._fbo[i]

    def draw(self, w: int, h: int, crossfade: float, origin=(0, 0)):
        """Mix the two targets onto the window. Render thread.

        `origin` places the w by h drawing inside a larger window, for the
        letterbox a second monitor of another shape needs.

        `crossfade` runs 0 (visual 1 alone) to 1 (visual 2 alone). Either end
        is exact, so this one call also serves a lone visual -- there is no
        second program for the single-visual case.

        projectM leaves its own program, its own framebuffer and whatever
        blend state it used behind it, so all of that is set here rather than
        assumed -- the symptom of assuming is a black window on one machine
        and a correct one on the next.
        """
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        glViewport(origin[0], origin[1], w, h)
        glDisable(GL_BLEND)
        glDisable(GL_DEPTH_TEST)
        glDisable(GL_SCISSOR_TEST)
        glUseProgram(self._program)
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, self._tex[0])
        glActiveTexture(GL_TEXTURE1)
        glBindTexture(GL_TEXTURE_2D, self._tex[1])
        glUniform1i(self._uniform["visual_a"], 0)
        glUniform1i(self._uniform["visual_b"], 1)
        glUniform1f(self._uniform["crossfade"],
                    max(0.0, min(1.0, float(crossfade))))
        glBindVertexArray(self._vao)
        glDrawArrays(GL_TRIANGLES, 0, 3)
        glBindVertexArray(0)
        glUseProgram(0)
        glActiveTexture(GL_TEXTURE0)

    def _drop_targets(self):
        for i in (0, 1):
            if self._fbo[i]:
                glDeleteFramebuffers(1, [self._fbo[i]])
            if self._tex[i]:
                glDeleteTextures(1, [self._tex[i]])
            if self._depth[i]:
                glDeleteRenderbuffers(1, [self._depth[i]])
            self._fbo[i] = self._tex[i] = self._depth[i] = 0
        self.size = (0, 0)

    def drop(self):
        """Give everything back. Render thread, before the context goes."""
        try:
            self._drop_targets()
            if self._vao:
                glDeleteVertexArrays(1, [self._vao])
            if self._program:
                glDeleteProgram(self._program)
        except Exception:
            pass
        self._vao = self._program = 0


# ─── idle pause ───────────────────────────────────────────────────────────────

#: SHQueryUserNotificationState answers. Only two of them mean "somebody is
#: using this screen for something else", and those are the two worth pausing
#: for: a full-screen Direct3D application (a game) and a full-screen window
#: with presentation mode on.
_QUNS_BUSY, _QUNS_RUNNING_D3D_FULL_SCREEN, _QUNS_PRESENTATION_MODE = 1, 2, 3


def fullscreen_app_running() -> bool:
    """Is something else running full-screen right now? Windows only.

    Nothing equivalent exists on Linux: a full-screen window there is an
    ordinary window with a hint set, and asking every window about it from
    another process is neither cheap nor reliable. So the answer off Windows
    is False, and the silence timer is what pauses instead.
    """
    if sys.platform != "win32":
        return False
    try:
        state = ctypes.c_int(0)
        # S_OK is 0. Any failure leaves state at 0, which is not one of the
        # busy values, so a broken call reads as "not busy" rather than
        # freezing the visuals.
        if ctypes.WinDLL("shell32").SHQueryUserNotificationState(
                ctypes.byref(state)) != 0:
            return False
        return state.value in (_QUNS_BUSY, _QUNS_RUNNING_D3D_FULL_SCREEN,
                               _QUNS_PRESENTATION_MODE)
    except Exception:
        return False


#: What "pause_when_idle" may be set to, and what the settings dialog offers.
IDLE_MODES = (("wallpaper", "Only as desktop wallpaper"),
              ("always",    "Always"),
              ("never",     "Never"))


def should_idle(mode: str, wallpaper: bool, silent_for: float,
                silence_after: float, fullscreen_app: bool) -> bool:
    """Whether the render loop should stop drawing.

    Split out as a plain function because the reasons to pause are a policy
    question and the render loop is not testable without a GPU.

    The default scope is wallpaper mode alone. A visualiser someone is
    watching must not freeze because the track ended -- projectM animates
    without audio, and stopping that looks like a crash. A wallpaper that
    keeps a GPU busy behind a full-screen game is the actual complaint.
    """
    if mode == "never" or (mode != "always" and not wallpaper):
        return False
    if fullscreen_app:
        return True
    return silence_after > 0 and silent_for >= silence_after


#: Peak sample below which a chunk counts as silence. Not zero: a live
#: loopback capture of a stopped player still carries dither and the odd
#: denormal, and projectM makes nothing of either.
SILENCE_PEAK = 0.002


# ─── vsync ────────────────────────────────────────────────────────────────────

#: A swap rate below this fraction of the display's refresh rate means vsync is
#: blocking rather than pacing. Loose on purpose: a driver dropping the odd
#: frame keeps its vsync, and the collapse this exists to catch is an order of
#: magnitude, not a few per cent.
VSYNC_MIN_FRACTION = 0.5


def vsync_paces(frames: int, seconds: float, refresh_hz: int) -> bool:
    """Did `frames` swaps in `seconds` keep up with the monitor?

    A plain function for the same reason `should_idle` is one: the answer is a
    policy question and the thing it is measured from needs a GPU. Anything it
    cannot judge is True -- vsync is the default, and refusing to turn it off
    is the safe way to be wrong.
    """
    if frames <= 0 or seconds <= 0:
        return True                    # nothing measured
    hz = refresh_hz if refresh_hz and refresh_hz > 0 else 60
    return frames / seconds >= hz * VSYNC_MIN_FRACTION


# ─── start at login ───────────────────────────────────────────────────────────

#: Registry value name on Windows, and the .desktop basename on Linux. Fixed,
#: so turning the setting off finds the entry turning it on wrote.
_AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command(wallpaper: bool = False) -> str:
    """The command line an autostart entry should run.

    A frozen build is its own executable. A source checkout is the interpreter
    plus main.py, which is worth supporting: it is how this gets tested at all.
    """
    if getattr(sys, "frozen", False):
        parts = [sys.executable]
    else:
        main = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "main.py")
        parts = [sys.executable, main]
    if wallpaper:
        parts.append("--wallpaper")
    return " ".join(f'"{p}"' if " " in p else p for p in parts)


def autostart_enabled() -> bool:
    """Is there an autostart entry for this app? The OS is the truth here."""
    try:
        if sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY) as k:
                winreg.QueryValueEx(k, APP_NAME)
            return True
        return os.path.isfile(_autostart_desktop_path())
    except Exception:
        return False


def _autostart_desktop_path() -> str:
    root = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(root, "autostart", f"{APP_SLUG}.desktop")


def set_autostart(on: bool, wallpaper: bool = False) -> str:
    """Add or remove the login entry. Returns "" or the reason it failed.

    Per-user in both cases -- HKCU and ~/.config/autostart -- so it never needs
    elevation and never touches anyone else's login.
    """
    try:
        if sys.platform == "win32":
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                  _AUTOSTART_KEY) as k:
                if on:
                    winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ,
                                      autostart_command(wallpaper))
                else:
                    try:
                        winreg.DeleteValue(k, APP_NAME)
                    except FileNotFoundError:
                        pass
            return ""
        path = _autostart_desktop_path()
        if not on:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            return ""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("[Desktop Entry]\n"
                    "Type=Application\n"
                    f"Name={APP_NAME}\n"
                    f"Exec={autostart_command(wallpaper)}\n"
                    "Terminal=false\n"
                    "X-GNOME-Autostart-enabled=true\n")
        return ""
    except Exception as e:
        return str(e)


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
if getattr(sys, "frozen", False):
    # Frozen builds first, on every platform: SDL2 travels inside the bundle and
    # an installed copy must not fall through to the host's. This has to sit
    # above the Linux branch rather than after it, because whichever module
    # imports viz_common first decides — and config.py gets here before
    # renderer.py does. NOT for a normal install: the pysdl2-dll wheel points
    # PySDL2 at its own binaries, but only while PYSDL2_DLL_PATH is unset.
    os.environ.setdefault(
        "PYSDL2_DLL_PATH",
        getattr(sys, "_MEIPASS", str(Path(sys.executable).parent)),
    )

if sys.platform.startswith("linux"):
    os.environ.setdefault("SDL_VIDEODRIVER", "x11")
    os.environ.setdefault("PYSDL2_DLL_PATH", "/usr/lib/x86_64-linux-gnu")

import sdl2
import sdl2.ext
from sdl2 import (
    SDL_GL_CONTEXT_MAJOR_VERSION,
    SDL_GL_CONTEXT_MINOR_VERSION,
)
from OpenGL.GL import (glViewport,
                       glReadPixels, glPixelStorei, GL_PACK_ALIGNMENT,
                       GL_RGB, GL_UNSIGNED_BYTE,
                       glGenTextures, glDeleteTextures, glBindTexture,
                       glTexImage2D, glTexSubImage2D, glTexParameteri,
                       GL_TEXTURE_2D, GL_RGBA, GL_LINEAR, GL_CLAMP_TO_EDGE,
                       GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER,
                       GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T)
# Dual-preset mode only. Kept in a block of its own because it is the one
# place this application does its own OpenGL beyond uploading a texture:
# two framebuffers, a shader, and a triangle.
from OpenGL.GL import (glGenFramebuffers, glBindFramebuffer,
                       glFramebufferTexture2D, glDeleteFramebuffers,
                       glCheckFramebufferStatus, GL_FRAMEBUFFER,
                       GL_COLOR_ATTACHMENT0, GL_FRAMEBUFFER_COMPLETE,
                       glGenRenderbuffers, glBindRenderbuffer,
                       glRenderbufferStorage, glFramebufferRenderbuffer,
                       glDeleteRenderbuffers, GL_RENDERBUFFER,
                       GL_DEPTH_COMPONENT24, GL_DEPTH_ATTACHMENT,
                       glCreateShader, glShaderSource, glCompileShader,
                       glGetShaderiv, GL_COMPILE_STATUS, glGetShaderInfoLog,
                       glCreateProgram, glAttachShader, glLinkProgram,
                       glGetProgramiv, GL_LINK_STATUS, glGetProgramInfoLog,
                       glDeleteShader, glDeleteProgram, glUseProgram,
                       glGetUniformLocation, glUniform1i, glUniform1f,
                       GL_VERTEX_SHADER, GL_FRAGMENT_SHADER,
                       glGenVertexArrays, glBindVertexArray,
                       glDeleteVertexArrays, glDrawArrays, GL_TRIANGLES,
                       glActiveTexture, GL_TEXTURE0, GL_TEXTURE1,
                       glDisable, GL_BLEND, GL_DEPTH_TEST, GL_SCISSOR_TEST,
                       glClear, glClearColor, GL_COLOR_BUFFER_BIT)


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
        self._req_on_top                 = False
        self._req_settings_updates       = None
        self._req_geometry               = None   # (x, y, w, h) from IPC
        self._req_show_window            = False  # show after Qt adoption
        self._req_sort                   = None   # 'name' | 'rating'
        self._req_goto                   = None   # jump to playlist index
        self._req_toggle_mode            = False  # shuffle/sequential toggle
        self._req_reload                 = False  # rebuild playlist from disk
        self._req_fullscreen             = False  # toggle fullscreen
        self._req_record                 = None   # 'mp4' | 'gif' | 'stop'
        self._req_wallpaper              = None   # True/False: be the desktop
        self._req_collection             = None   # name to play, "" = all
        self._req_preset_data            = None   # preset text to play now
        self._req_reset_textures         = False  # rescan after a swap
        self._req_sprites                = False  # rebuild the overlay list
        self._req_rate                   = 0      # stars to add, from the GUI
        self._req_snapshot               = False  # re-take the browser picture
        self._req_burn                   = None   # a picture to burn in, "" off
        #: Frames left before an edit's error is read back. 0 = not editing,
        #: which is what keeps a 130,000-preset pack from toasting all night.
        self._edit_error_in              = 0
        self._rated                      = False  # favorite (rating-weighted) shuffle
        self._rated_history              = deque(maxlen=200)  # native indices for rated prev

        self._locked              = False
        self._borderless          = False
        self._on_top              = False
        self._borderless_on_start = False   # set by main.py --borderless flag
        #: Screensaver mode: fullscreen at once, and any real input quits.
        #: Set by main.py before run(), from /s or --screensaver.
        self._screensaver         = False
        self._ss_origin           = None    # first pointer position seen
        self._menu_open           = False   # host's context menu is on screen
        self._shuffle             = False  # list order is as loaded
        self._running             = False
        self._target_y            = None

        self._window  = None
        self._context = None
        self._pm      = None
        #: Last known (x, y, w, h) of the SDL window, refreshed on window
        #: events. Cached rather than queried on demand because the Qt thread
        #: reads it (to place the toast) and SDL calls belong to the thread
        #: that created the window.
        self._win_rect = None
        #: HWND on Windows, X11 window id elsewhere. The frame theme uses it
        #: on Windows, wallpaper mode on both.
        self._native_handle = 0
        #: SDL's own Display*, on X11 only. Wallpaper mode changes window
        #: properties through it rather than opening a second connection, so
        #: there is no second connection to keep in step.
        self._x11_display = None
        #: [(index, name, x, y, w, h), ...] for every monitor. Same reason as
        #: _win_rect: SDL's video calls belong to the render thread, and the
        #: Qt thread builds the display menu out of this cache.
        self._displays = []
        self._fullscreen = False
        self._rec        = None    # a _Recorder while a clip is being taken
        self._rec_size   = None    # the size ffmpeg was told; frames must match
        self._rec_last   = 0.0     # monotonic time of the last frame submitted
        self._rec_fps    = 30
        self._wallpaper  = False
        self._wp_host    = None    # (hwnd, style) of a WorkerW we clipped
        self._wp_restore = None    # (x, y, w, h, borderless) to come back to
        #: When the wallpaper window last checked it was still the bottom one.
        #: See _hold_wallpaper_bottom -- getting there once is not enough.
        self._wp_hold_at = 0.0
        #: Consecutive geometry corrections; see _hold_wallpaper_box.
        self._wp_fixes = 0
        #: Both said once, not every two seconds for the life of the app.
        self._wp_box_moaned = False
        self._wp_gave_up = False
        self._thumb_done = True    # one attempt per preset; see _maybe_thumbnail
        #: The picture burnt into the preset, and the GL texture holding its
        #: current frame. Both belong to the render thread: the texture is
        #: made in the GL context, and the decoder is stopped from the same
        #: place it was started so a dead ffmpeg is never left behind.
        self._burn_src   = None
        self._burn_tex   = 0
        self._burn_size  = (0, 0)   # what the texture currently holds
        self._burn_image = None     # the last frame the decoder handed over
        self._burn_want  = None     # canvas size and placement it was drawn at

        #: Textures answered for by us, by the lower-case name the preset
        #: asked for, and the names already looked for and not found. The
        #: second one is what keeps a preset full of stock texture names from
        #: listing every texture directory on every preset load.
        self._live_tex     = {}
        self._live_missing = set()

        #: Both visuals, primary first, and which one the controls drive.
        #: `self._pm` is whichever has focus -- a plain attribute rather than
        #: a property, so all hundred-odd call sites, the browser, the menu,
        #: the keys and the tweak panel keep working untouched and drive
        #: whichever visual is being adjusted.
        self._pms   = [None, None]
        self._focus = 0
        self._blend = None          # a _Blender while two are on screen
        self._req_dual  = None      # True or False, from the Qt thread
        self._req_focus = None      # 0 or 1, ditto
        # Visual 1 is a switch, not an instance: the primary projectM owns the
        # playlist, the ratings and everything the browser talks to, so it
        # stays alive when it is switched off and simply is not drawn. What
        # that saves is the render, which is the whole cost.
        self._v1_on = True
        self._req_v1 = None         # True or False, from the Qt thread
        # A second SDL window, borderless over one monitor, showing visual 2
        # on its own. One GL context serves both -- SDL will make a context
        # current on any window built to the same attributes, so there is no
        # sharing to set up and every texture and framebuffer is already
        # visible to both.
        self._window2 = None
        self._window2_display = -1
        #: When the fader last moved under a key, or 0.0 for "nothing owed".
        self._fade_dirty = 0.0
        self._req_win2 = None       # display index, or -1 for "no second window"
        self._req_fullscreen_on = None   # display index, from the Qt thread

        #: Set by the key or the browser's button. Taken on the render thread
        #: at the one point in the loop where the back buffer holds this
        #: frame, which is where _maybe_thumbnail already runs.
        self._want_snapshot = False
        #: Idle pause. _last_sound is when audio was last above SILENCE_PEAK,
        #: _idle whether the loop is currently not drawing, and _idle_since
        #: when it stopped -- the preset clock is pushed forward by that much
        #: on the way out, or a paused wallpaper would come back having
        #: "played" through a hundred presets it never showed.
        self._last_sound = 0.0
        self._idle = False
        self._idle_since = 0.0
        self._fs_app = False
        self._fs_app_checked = 0.0
        #: Whether this machine's vsync actually paces frames. None until it
        #: has been measured, once, just after the GL context is made.
        self._vsync_ok = None
        #: Name of the collection being played, or "" for the whole library.
        self._collection = ""
        #: Loaded on first use, not here: this runs before the data directory
        #: is known to exist and a menu is the only thing that ever asks.
        self._collections = None

        self._audio = AudioCapture(cfg.get("audio_device", ""))
        self._audio.set_gain(cfg.get("audio_gain", 1.0))

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

        # Both hints name X11 and mean nothing anywhere else, so they are
        # gated like the environment variables at the top of this module.
        if sys.platform.startswith("linux"):
            # Ask the compositor not to manage our window position.
            sdl2.SDL_SetHint(b"SDL_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR", b"1")
            sdl2.SDL_SetHint(b"SDL_VIDEODRIVER", b"x11")

        flags = (sdl2.SDL_WINDOW_OPENGL |
                 sdl2.SDL_WINDOW_SHOWN  |
                 sdl2.SDL_WINDOW_RESIZABLE)

        self._window = sdl2.SDL_CreateWindow(
            APP_NAME.encode(),
            x, y, w, h, flags
        )
        if not self._window:
            print(f"[renderer] SDL_CreateWindow failed: {sdl2.SDL_GetError()}")
            return False

        self._track_window()

        self._context = sdl2.SDL_GL_CreateContext(self._window)
        if not self._context:
            print(f"[renderer] SDL_GL_CreateContext failed: {sdl2.SDL_GetError()}")
            return False

        self._set_swap_interval(self._cfg.get("fps", 60))

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
                    self._x11_display = wm_info.info.x11.display
                print(f"XID={handle}", flush=True)
                # Kept so the frame can be re-themed when the setting changes.
                self._native_handle = handle
                apply_window_theme(handle, self._cfg.get("theme"))
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

        self._pms[0] = self._pm
        self._apply_pm_settings(self._cfg)

        preset_dir = self._cfg.get("preset_dir", "")

        # Created before the paths are handed over, not when the first picture
        # is added: the search path is fixed when projectM builds its texture
        # manager, a directory that did not exist is dropped from the list,
        # and no later rescan can put it back. A cache miss does rescan the
        # directories it knows about, which is why nothing else is needed to
        # pick up a picture added while running.
        try:
            os.makedirs(sprite_dir(), exist_ok=True)
        except OSError:
            pass

        self._apply_texture_paths()
        # After the paths, before any preset: the callback is what answers
        # for a .gif or a .webp, which projectM's own scanner cannot see at
        # all, and a preset asks for its textures the moment it loads.
        if self._cfg.get("live_textures", True):
            self._pm.set_texture_load_callback(self._on_texture_wanted)
        # Overlays outlive a restart. Requested rather than applied straight
        # away: this runs before the first frame, and the sprite's picture is
        # resolved through the texture manager, which wants the search paths
        # in place -- they are, one line up, but the queue is the one path
        # every other sprite change already takes.
        if self.sprites():
            self._req_sprites = True
        # And the same for the burnt-in picture, for the same reason: it is
        # in the config, so it has to come back on its own. Without this the
        # setting persisted, the panel showed the file, and nothing was ever
        # burnt in until somebody chose it again -- which reads as the whole
        # feature being broken rather than as one missing line.
        if self.burn_in()["path"]:
            self._req_burn = self.burn_in()["path"]
        # Same again for the second visual: it is a setting, so it has to
        # come back by itself. Queued rather than built here, because
        # building it wants the preset directory loaded first.
        if self._cfg.get("dual_enabled"):
            self._req_dual = True
            # Only alongside a second visual. On its own it is a black window,
            # and a config that somehow says so is not worth honouring.
            self._v1_on = bool(self._cfg.get("visual1_enabled", True))

        # Before the library loads, so the first frame drawn is ours. projectM
        # draws its own "M" idle preset for as long as nothing else is
        # loaded, which on a fresh install is until a preset folder is
        # chosen -- somebody else's logo on the first screen of a paid app.
        text = welcome_preset()
        if text:
            self._pm.load_preset_data(text, smooth=False)

        count = 0
        if not os.path.isdir(preset_dir):
            print(f"[renderer] WARNING: preset dir not found: {preset_dir!r}")
        else:
            count = self._pm.load_preset_dir(preset_dir)
            print(f"[renderer] Loaded {count} presets from {preset_dir}")

        if count == 0:
            # Nothing bundles presets, so this is the normal first run -- and
            # it is also what a moved or emptied folder looks like. Either way
            # the window is black and stdout is not where someone who launched
            # this from a menu entry is looking, so the host is told to ask.
            self._bridge.no_presets.emit(preset_dir)

        return True

    def _apply_texture_paths(self):
        """Point projectM at the texture directories.

        Texture-sampling presets render solid white without these. Called
        again when Settings changes a directory, so neither field needs a
        restart -- and the not-found list is dropped with it, or a picture
        added in the new directory would never be looked for.
        """
        self._live_missing.clear()
        if not self._pm or not hasattr(self._pm, "set_texture_search_paths"):
            return
        tex_paths = self._texture_paths()
        if tex_paths:
            self._pm.set_texture_search_paths(tex_paths)

    def _texture_paths(self) -> list:
        """Where a texture is looked for, in order.

        The configured directory leads and the rest are guesses kept for
        installs that never set one -- nothing is bundled. Shared with the
        texture callback so both look in the same places in the same order.
        """
        preset_dir = self._cfg.get("preset_dir", "")
        return [p for p in (
            # First, and that is the whole mechanism behind swapping a
            # texture for your own picture: libprojectM scans its search
            # paths in order and the first file whose stem matches wins, so
            # an override here stands in front of the stock one without the
            # texture pack ever being written to.
            texture_override_dir(),
            self._cfg.get("texture_dir", "").strip(),
            os.path.join(os.path.dirname(__file__), "textures"),
            os.path.join(preset_dir, "textures"),
            os.path.expanduser("~/projectm-textures"),
            preset_dir,
            # Last, and that matters: a sprite is a picture the user dropped
            # in, and it must never win a stem match against a texture a
            # preset actually asks for. The spr_ prefix makes a collision
            # unlikely; being last makes it harmless.
            sprite_dir(),
            # Ours, and only one file: the logo the welcome preset samples.
            # Same reasoning as the line above -- last, so it can never
            # answer for a texture a downloaded preset asked for.
            bundled_dir("assets", "textures"),
        ) if p and os.path.isdir(p)]

    def _drawable_size(self):
        """The window in pixels, which on a scaled display is not its size.

        SDL reports both and they differ by the scale factor; everything
        drawn with GL wants this one.
        """
        if not self._window:
            return (0, 0)
        w, h = ctypes.c_int(), ctypes.c_int()
        sdl2.SDL_GL_GetDrawableSize(self._window,
                                    ctypes.byref(w), ctypes.byref(h))
        return (int(w.value), int(h.value))

    def _create_projectm(self) -> ProjectM4:
        """Factory hook — override to pass a different config_dir."""
        return load_projectm()

    def _apply_pm_settings(self, cfg: dict, pm=None):
        pm = pm or self._pm
        quality = cfg.get("quality", "high")
        mx, my  = QUALITY_MESH.get(quality, (64, 48))
        pm.set_mesh_size(
            cfg.get("mesh_x", mx),
            cfg.get("mesh_y", my)
        )
        pm.set_beat_sensitivity(cfg.get("beat_sensitivity", 1.0))
        pm.set_hard_cut_enabled(cfg.get("hard_cuts_enabled", True))
        pm.set_hard_cut_sensitivity(cfg.get("hard_cut_sensitivity", 1.0))
        pm.set_soft_cut_duration(cfg.get("soft_cut_duration", 3.0))
        _fps = cfg.get("fps", 60)
        pm.set_fps(_fps if _fps and _fps > 0 else 120)
        pm.set_preset_start_clean(bool(cfg.get("preset_start_clean")))

    # ── Public thread-safe requests ───────────────────────────────────────

    def request_next(self):
        with self._lock: self._req_next = True

    def request_prev(self):
        with self._lock: self._req_prev = True

    def request_random(self):
        with self._lock: self._req_random = True

    def request_rate(self, delta: int):
        """Nudge the current preset's rating. Safe from the Qt thread.

        Added rather than calling _rate_current directly: it asks projectM for
        the preset name, and every other question put to projectM is asked on
        the render thread. Accumulated rather than latched, so three quick
        clicks are three stars and not one.
        """
        with self._lock:
            self._req_rate += int(delta)

    def burn_in(self) -> dict:
        """What is being burnt into the preset. Qt thread.

        Always four keys with sane values, because this is read straight into
        widgets and a config somebody hand-edited is not worth a traceback.
        """
        e = self._cfg.get("burn_in") or {}
        def num(key, default, lo, hi):
            try:
                return max(lo, min(hi, float(e.get(key, default))))
            except (TypeError, ValueError):
                return default
        return {"path":  str(e.get("path", "") or ""),
                "size":  num("size", 0.5, 0.02, 1.0),
                "x":     num("x", 0.5, 0.0, 1.0),
                "y":     num("y", 0.5, 0.0, 1.0),
                "alpha": num("alpha", 0.25, 0.02, 1.0)}

    def set_burn_in(self, **fields):
        """Change the burnt-in picture, or stop it with path="". Qt thread.

        Only a new `path` restarts the decoder; size and position are read
        fresh every frame, so dragging them is free.
        """
        entry = self.burn_in()
        entry.update({k: v for k, v in fields.items() if k in entry})
        self._cfg["burn_in"] = entry
        self._save_config(self._cfg)
        if "path" in fields:
            with self._lock:
                self._req_burn = entry["path"]

    def can_burn(self) -> bool:
        """False on a libprojectM without the burn-in entry point."""
        return bool(self._pm and getattr(self._pm, "can_burn", False))

    def request_snapshot(self):
        """Take the browser's picture of this preset again, now."""
        with self._lock:
            self._req_snapshot = True

    def get_setting(self, key: str, default=None):
        """One config value. Qt thread; the browser keeps its own switches
        here rather than inventing a second settings file for two booleans."""
        return self._cfg.get(key, default)

    def put_setting(self, key: str, value):
        """Write one config value straight through to disk."""
        self._cfg[key] = value
        self._save_config(self._cfg)

    def request_toggle_lock(self):
        with self._lock:
            self._req_lock   = True
            self._req_lock_v = not self._locked

    def request_toggle_on_top(self):
        with self._lock:
            self._req_on_top = True

    def request_toggle_borderless(self):
        with self._lock:
            self._req_borderless = True

    def request_context_menu(self):
        with self._lock: self._req_context_menu = True

    def set_menu_open(self, is_open: bool):
        """Host tells the render loop whether its context menu is on screen.

        Only the host sets this. Arming it here, when the menu is merely
        requested, would leave a host that ignores the request — Bomsai Studio
        consumes the RCLICK line itself — with the flag stuck on and every key
        and click swallowed for the rest of the session.
        """
        self._menu_open = bool(is_open)

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

    @property
    def lock_changed(self):
        """Bridge signal fired when the lock goes on or off, whatever moved
        it -- the key, the menu or the browser's own button."""
        return self._bridge.lock_changed

    @property
    def thumbnail_ready(self):
        """Bridge signal carrying the name of a preset whose picture has just
        been written. The browser caches a missing thumbnail as a miss, so
        without this a fresh snapshot does not appear until the list is
        rebuilt."""
        return self._bridge.thumbnail_ready

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

    # ── Collections ───────────────────────────────────────────────────────
    #
    # A rating says how good a preset is. A collection says what it is for --
    # "chill", "party", "the one with the tunnels" -- which is the axis a 0-10
    # number cannot carry, and the reason a library of ten thousand still
    # needs sorting after everything has been rated.
    #
    # Playing one is a playlist rebuild and nothing more, so the browser, the
    # search box, sort, shuffle and delete-0★ all narrow to it for free.

    def collections(self) -> dict:
        """{name: [preset stem, ...]}, read from disk once per process."""
        if getattr(self, "_collections", None) is None:
            self._collections = load_collections_from(collections_path())
        return self._collections

    def _write_collections(self):
        save_collections_to(collections_path(), self.collections())

    def collection(self) -> str:
        """Name of the collection being played, or "" for the whole library."""
        return self._collection

    def create_collection(self, name: str) -> bool:
        name = (name or "").strip()
        if not name or name in self.collections():
            return False
        self.collections()[name] = []
        self._write_collections()
        return True

    def delete_collection(self, name: str):
        if self.collections().pop(name, None) is None:
            return
        self._write_collections()
        if self._collection == name:
            self.play_collection("")

    def add_to_collection(self, name: str, stem: str) -> bool:
        """Returns False when it was already there, so the caller can say so."""
        members = self.collections().setdefault(name, [])
        if not stem or stem in members:
            return False
        members.append(stem)
        self._write_collections()
        return True

    def remove_from_collection(self, name: str, stem: str) -> bool:
        members = self.collections().get(name)
        if not members or stem not in members:
            return False
        members.remove(stem)
        self._write_collections()
        # The playlist is only rebuilt when the collection being played
        # changed; anything else would interrupt a preset to remove a
        # different one.
        if self._collection == name:
            with self._lock:
                self._req_collection = name
        return True

    def play_collection(self, name: str = ""):
        """Play only this collection, or "" for the whole library."""
        with self._lock:
            self._req_collection = name or ""

    def current_preset_stem(self) -> str:
        try:
            return self._pm.current_preset_name() or ""
        except Exception:
            return ""

    def _do_play_collection(self, name: str):
        """Render thread. Rebuild the playlist from a collection, or all."""
        if name and name not in self.collections():
            name = ""
        self._collection = name
        files = self._all_preset_files()
        if name and not files:
            # An empty collection would leave a black window with no
            # explanation, and the way back out is a menu two levels deep.
            self._collection = ""
            self._bridge.status_message.emit(
                f"“{name}” has nothing in it yet")
            return
        if not files:
            self._bridge.status_message.emit("No presets found")
            return
        files.sort(key=lambda ps: ps[1].lower())
        cur = self.current_preset_stem()
        self._rebuild_playlist([f for f, _ in files])
        self._shuffle = False
        self._rated_history.clear()      # native indices changed
        self._seek_to_stem(cur)
        self._bridge.status_message.emit(
            f"Collection: {name} — {len(files)} presets" if name
            else f"All presets — {len(files)}")
        self._bridge.playlist_changed.emit()
        self._emit_current_preset()

    # ── Tweaking the preset on screen ─────────────────────────────────────
    #
    # Everything the browser's tweak panel needs. The text work is all in
    # module functions above; what lives here is the two things that must
    # happen on the render thread, because both compile shaders: playing a
    # patched preset, and rescanning textures after a swap.

    def can_tweak(self) -> bool:
        """False on a libprojectM too old to play a preset from a string."""
        return bool(self._pm and getattr(self._pm, "can_load_data", False))

    def current_preset_path(self) -> str:
        try:
            return self._pm.current_preset_path() if self._pm else ""
        except Exception:
            return ""

    def last_preset_error(self) -> str:
        """Why the last edit did not take, or "". Qt thread.

        Two sources because projectM has two: a preset it could not parse
        raises through the failed-event callback, and a shader it could not
        compile only reaches the log. Ask no sooner than a few frames after
        the edit -- the shader one is not written until the preset is drawn.
        """
        return (getattr(self._pm, "last_error", "") if self._pm
                else "") or _LAST_PM_ERROR

    def donor_preset(self) -> tuple:
        """Another preset from the ones in play, as (name, text). Qt thread.

        Picked at random rather than chosen from a list: MilkDrop's own
        mash-up keys pick for you, and choosing from 9,795 entries is a
        different and much worse feature.
        """
        here = self.current_preset_path()
        files = [p for p, _stem in self._all_preset_files() if p != here]
        if not files:
            return ("", "")
        path = random.choice(files)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return (os.path.splitext(os.path.basename(path))[0], f.read())
        except Exception as e:
            print(f"[mashup] could not read {path}: {e}", flush=True)
            return ("", "")

    def preset_text(self) -> str:
        """The current preset's file, or "". Qt thread -- it is a file read."""
        path = self.current_preset_path()
        if not path or not os.path.isfile(path):
            return ""
        try:
            # Presets come from packs assembled by hundreds of people over
            # twenty years; a stray byte is not a reason to refuse to open one.
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as e:
            print(f"[tweak] could not read {path}: {e}", flush=True)
            return ""

    def preview_preset(self, text: str):
        """Play this preset text now. Not saved anywhere."""
        with self._lock:
            self._req_preset_data = text

    def _do_load_preset_data(self, text: str):
        """Render thread. A hard cut, deliberately: blending on every slider
        step turns a drag into a smear of half-applied presets."""
        if not self._pm:
            return
        if not self._pm.load_preset_data(text, smooth=False):
            self._bridge.show_toast.emit(
                "This build of projectM cannot play an edited preset")
            return
        print(f"[tweak] applied {len(text)} bytes", flush=True)
        # A parse error is already in last_error by now; a shader error is not
        # logged until the preset is drawn. Ask again once frames have gone by,
        # rather than reporting success on an edit that is about to fail.
        self._edit_error_in = 3

    def revert_preset(self):
        """Back to the file on disk, discarding an unsaved tweak.

        By reloading the text rather than re-seeking the playlist: seeking is
        refused while the preset is locked, and locking the thing you are
        tweaking is the obvious way to keep it on screen while you work.
        """
        text = self.preset_text()
        if text:
            self.preview_preset(text)

    def save_preset_as(self, text: str, name: str) -> str:
        """Write a tweaked preset beside the original. "" or the failure.

        Into the same directory the preset came from, so it is picked up by
        the next scan and lands in the same collection and sort as its
        neighbours. Never over the original: the point of a tweak is that the
        thing you started from is still there.
        """
        src = self.current_preset_path()
        folder = (os.path.dirname(src) if src
                  else self._cfg.get("preset_dir", ""))
        if not folder or not os.path.isdir(folder):
            return "There is no preset folder to save into."
        stem = "".join(c for c in (name or "").strip()
                       if c not in '\\/:*?"<>|').strip()
        if not stem:
            return "Give it a name."
        dest = os.path.join(folder, stem + ".milk")
        if os.path.exists(dest):
            return f"“{stem}” already exists in that folder."
        try:
            with open(dest, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            return str(e)
        self._ratings[stem] = get_preset_rating(self._ratings, stem)
        self._save_ratings(self._ratings)
        with self._lock:
            self._req_reload = True
        self._bridge.show_toast.emit(f"Saved “{stem}”")
        return ""

    # ── Swapping a texture for a picture ──────────────────────────────────

    def swap_texture(self, name: str, image_path: str) -> str:
        """Stand `image_path` in for the stock texture `name`. "" or why not."""
        why = set_texture_override(name, image_path)
        if not why:
            self._refresh_textures()
        return why

    def restore_texture(self, name: str) -> bool:
        gone = clear_texture_override(name)
        if gone:
            self._refresh_textures()
        return gone

    def _refresh_textures(self):
        with self._lock:
            self._req_reset_textures = True

    # ── Sprites ───────────────────────────────────────────────────────────
    # Pictures laid over whatever is playing. Configuration is the list in
    # config.json; the screen is rebuilt from it wholesale on any change, so
    # the two cannot disagree.

    def can_sprites(self) -> bool:
        return bool(self._pm and getattr(self._pm, "can_sprites", False))

    def sprites(self) -> list:
        """The configured overlays, in draw order."""
        out = []
        for e in self._cfg.get("sprites", []) or []:
            if isinstance(e, dict) and e.get("image"):
                out.append(dict(e))
        return out

    def _save_sprites(self, entries: list):
        self._cfg["sprites"] = entries
        self._save_config(self._cfg)
        with self._lock:
            self._req_sprites = True

    def add_sprite(self, image_path: str, motion: str = "still",
                   size: float = 0.5, alpha: float = 1.0,
                   blend: str = "normal") -> str:
        """Copy a picture in and lay it over the render. "" or why not."""
        entries = self.sprites()
        if len(entries) >= MAX_SPRITES:
            return f"That is already {MAX_SPRITES} pictures on screen."
        stem, why = add_sprite_image(image_path)
        if why:
            return why
        entries.append({"image": stem, "motion": motion, "size": size,
                        "alpha": alpha, "blend": blend})
        self._save_sprites(entries)
        return ""

    def update_sprite(self, index: int, **fields) -> bool:
        entries = self.sprites()
        if not 0 <= index < len(entries):
            return False
        entries[index].update(fields)
        self._save_sprites(entries)
        return True

    def remove_sprite(self, index: int) -> bool:
        entries = self.sprites()
        if not 0 <= index < len(entries):
            return False
        gone = entries.pop(index)
        # Only when nothing else still points at it: the same picture can be
        # laid on twice, spinning one and still the other.
        if not any(e.get("image") == gone.get("image") for e in entries):
            remove_sprite_image(gone.get("image", ""))
        self._save_sprites(entries)
        return True

    def clear_sprites(self) -> int:
        entries = self.sprites()
        for e in entries:
            remove_sprite_image(e.get("image", ""))
        self._save_sprites([])
        return len(entries)

    def _do_apply_sprites(self):
        """Render thread. Rebuild every sprite from the configuration."""
        if not self._pm:
            return
        entries = self.sprites()
        wanted = len(entries)
        got = self._pm.set_sprites([sprite_code(e) for e in entries])
        if got != wanted:
            # Almost always a picture the texture manager could not find,
            # which projectM reports by refusing the sprite and nothing else.
            print(f"[sprite] {got}/{wanted} accepted — the rest name a "
                  f"picture projectM could not load", flush=True)
        elif wanted:
            print(f"[sprite] {wanted} on screen", flush=True)

    def _do_reset_textures(self):
        """Render thread. Re-point projectM at the search paths and make it
        forget what it scanned, then reload the preset -- textures are
        resolved when a preset loads, so nothing changes on screen until it
        does. That is also why a swap used to look like it needed a restart.
        """
        if not self._pm:
            return
        self._apply_texture_paths()
        # Live textures deliberately survive this. A preset that is on screen
        # still holds a shared_ptr to the wrapper around our texture id, so
        # giving the id back here is a sampler reading a texture that is not
        # there any more. The cost is that replacing a .gif that is already
        # playing needs a restart; replacing a stock texture with one does
        # not, because that name has never been answered for yet.
        self._pm.reset_textures()
        text = self.preset_text()
        if text:
            self._pm.load_preset_data(text, smooth=False)

    # ── Backup ────────────────────────────────────────────────────────────

    def state_paths(self) -> dict:
        """{name inside the backup: file on disk}. Subclasses add their own.

        Only collections here, because that is the one file whose location
        this class decides. Config, ratings and keymaps live wherever the
        concrete app put them.
        """
        return {"collections.json": collections_path()}

    def is_locked(self) -> bool:
        return self._locked

    def apply_settings(self, updates: dict):
        with self._lock:
            # Merged, not replaced. The sensitivity slider calls this on every
            # drag step, and a plain assignment would drop a device change the
            # render loop had not picked up yet.
            if self._req_settings_updates is None:
                self._req_settings_updates = dict(updates)
            else:
                self._req_settings_updates.update(updates)

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

        self._refresh_displays()
        self._audio.start()
        self._running      = True
        self._preset_start = _time.monotonic()

        # Apply borderless immediately if launched with --borderless
        if self._borderless_on_start:
            self._borderless = True
            sdl2.SDL_SetWindowBordered(self._window, sdl2.SDL_FALSE)

        if self._screensaver:
            self._toggle_fullscreen()
            sdl2.SDL_ShowCursor(sdl2.SDL_DISABLE)
            self._cursor_hidden = True

        event      = sdl2.SDL_Event()

        # BOMSAI_FPS=1 reports the frame rate every 5s on stderr. It exists so
        # "is it slow on your machine?" can be answered from a shipped build
        # rather than a profiler, and it is what caught a build container whose
        # compiler cost a quarter of the frame rate.
        _fps_t0 = _time.monotonic() if os.environ.get("BOMSAI_FPS") else None
        _fps_n = 0

        self._emit_current_preset()

        while self._running:

            # ── SDL events ────────────────────────────────────────────────
            while sdl2.SDL_PollEvent(_ctypes.byref(event)) != 0:
                if event.type == sdl2.SDL_QUIT:
                    self._running = False
                    break

                # Belt and braces for a report that would not reproduce here:
                # a context menu that did not go away when the visualiser
                # behind it was clicked. Qt grabs the pointer for its popup and
                # on X11 that grab does cover SDL's window — measured, with
                # xdotool driving a real window — so normally the renderer sees
                # none of these events and this branch never runs. Where the
                # grab does not hold it does see them, and forwarding one
                # closes the menu rather than leaving it stranded. It also
                # keeps an ESCAPE meant for the menu from quitting the app.
                if self._menu_open and event.type in (
                        sdl2.SDL_MOUSEBUTTONDOWN, sdl2.SDL_KEYDOWN):
                    self._bridge.close_context_menu.emit()
                    continue

                elif self._screensaver and self._ss_should_quit(event):
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
                        for pm in self._pms:
                            if pm is not None:
                                pm.set_window_size(nw, nh)
                    # Any window event may have moved or resized us: this one
                    # branch covers moves, fullscreen, restore and maximise
                    # without a case for each.
                    self._track_window()

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
                    # The menu lists monitors and is about to be built on the
                    # Qt thread, which cannot ask SDL for them itself.
                    self._refresh_displays()
                    self._bridge.show_context_menu.emit()
                if self._req_fullscreen:
                    self._req_fullscreen = False
                    self._toggle_fullscreen()
                if self._req_wallpaper is not None:
                    want = self._req_wallpaper
                    self._req_wallpaper = None
                    self._set_wallpaper_mode(want)
                if self._req_record is not None:
                    kind = self._req_record
                    self._req_record = None
                    if kind == "stop":
                        self._stop_recording()
                    else:
                        self._start_recording(kind)
                if self._req_borderless:
                    self._req_borderless = False
                    self._toggle_borderless()
                if self._req_on_top:
                    self._req_on_top = False
                    self._toggle_on_top()
                if self._req_settings_updates is not None:
                    updates = self._req_settings_updates
                    self._req_settings_updates = None
                    self._cfg.update(updates)
                    # The merged config, not the diff: _apply_pm_settings reads
                    # every projectM setting with a default, so handing it a
                    # partial dict would quietly reset whatever is missing.
                    # Named instances rather than self._pm: with two visuals
                    # up, self._pm is whichever is being adjusted, and the
                    # second one has a quality of its own.
                    self._apply_pm_settings(self._cfg, self._pms[0])
                    if self._pms[1] is not None:
                        second = dict(self._cfg)
                        second["quality"] = self._cfg.get("dual_quality",
                                                          "low")
                        second.pop("mesh_x", None)
                        second.pop("mesh_y", None)
                        self._apply_pm_settings(second, self._pms[1])
                    if "visual1_enabled" in updates or "dual_enabled" in updates:
                        want1 = bool(self._cfg.get("visual1_enabled", True))
                        want2 = bool(self._cfg.get("dual_enabled", False))
                        # Neither is not a setting, it is a black window.
                        if not want1 and not want2:
                            want1 = True
                        # Up before down: taking the last one off screen trips
                        # the guard that puts one back, and the guard would
                        # undo half of what was just asked for.
                        if want2:
                            self._do_set_dual(True)
                        self._do_set_v1(want1)
                        if not want2:
                            self._do_set_dual(False)
                    if "fps" in updates:
                        self._set_swap_interval(updates.get("fps", 60))
                    if "audio_device" in updates:
                        self._audio.update_device(updates["audio_device"])
                    if "audio_gain" in updates:
                        self._audio.set_gain(updates["audio_gain"])
                    if "theme" in updates:
                        # Render thread only: the frame belongs to the thread
                        # that made the window, same rule as every other SDL
                        # call here.
                        apply_window_theme(self._native_handle,
                                           updates["theme"])
                    if "preset_dir" in updates or "texture_dir" in updates:
                        self._apply_texture_paths()
                    if "preset_dir" in updates:
                        self._reload_preset_dir()
                    elif "texture_dir" in updates:
                        self._reload_current_preset()
                if self._req_geometry is not None:
                    gx, gy, gw, gh = self._req_geometry
                    self._req_geometry = None
                    sdl2.SDL_SetWindowSize(self._window, gw, gh)
                    self._track_window()
                    glViewport(0, 0, gw, gh)
                    for pm in self._pms:
                        if pm is not None:
                            pm.set_window_size(gw, gh)
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
                if self._req_collection is not None:
                    name = self._req_collection
                    self._req_collection = None
                    self._do_play_collection(name)
                if self._req_reset_textures:
                    self._req_reset_textures = False
                    self._do_reset_textures()
                if self._req_sprites:
                    self._req_sprites = False
                    self._do_apply_sprites()
                if self._req_rate:
                    delta = self._req_rate
                    self._req_rate = 0
                    self._rate_current(delta)
                if self._req_snapshot:
                    self._req_snapshot = False
                    self._want_snapshot = True
                if self._req_burn is not None:
                    path = self._req_burn
                    self._req_burn = None
                    self._do_set_burn(path)
                if self._req_dual is not None:
                    on = self._req_dual
                    self._req_dual = None
                    self._do_set_dual(on)
                if self._req_v1 is not None:
                    on = self._req_v1
                    self._req_v1 = None
                    self._do_set_v1(on)
                if self._req_win2 is not None:
                    where = self._req_win2
                    self._req_win2 = None
                    self._do_set_window2(where)
                if self._req_fullscreen_on is not None:
                    where = self._req_fullscreen_on
                    self._req_fullscreen_on = None
                    self._do_fullscreen_on(where)
            if self._fade_dirty and \
                    _time.monotonic() - self._fade_dirty > self.FADE_SETTLE_S:
                self._fade_dirty = 0.0
                self._save_config(self._cfg)
                if self._req_focus is not None:
                    which = self._req_focus
                    self._req_focus = None
                    self._do_set_focus(which)
                if self._req_preset_data is not None:
                    text = self._req_preset_data
                    self._req_preset_data = None
                    self._do_load_preset_data(text)

            # ── Wallpaper: stay at the bottom, and stay the right size ────
            # Above the idle pause, not below it. A wallpaper spends most of
            # its life parked -- the pause is on silence, and a desktop is
            # usually quiet -- and below the `continue` it stops defending
            # itself for exactly as long as nobody is playing anything. The
            # window manager does not pause with it: measured, a window shoved
            # off the bottom edge while parked stayed there until sound came
            # back. One property read every two seconds is cheap enough to do
            # in a loop that is otherwise asleep.
            if self._wallpaper and sys.platform != "win32":
                self._hold_wallpaper_bottom()

            # ── Idle pause ────────────────────────────────────────────────
            # Above auto-advance, not below it: a paused wallpaper that still
            # rotates is worse than one that does not pause at all -- it comes
            # back having silently played a hundred presets nobody saw, and
            # measuring it live is the only way this showed up. The level is
            # updated on the capture thread, so it is still current in here.
            if self._audio.level() > SILENCE_PEAK:
                self._last_sound = _time.monotonic()
            if self._check_idle():
                # Keep popping, or the ring buffer hands projectM a stale
                # chunk the moment the music comes back. No render and no
                # swap: the window keeps the last frame it drew, which as a
                # wallpaper is simply a still picture.
                self._audio.get_chunk()
                _time.sleep(0.15)
                continue

            # ── Auto-advance ──────────────────────────────────────────────
            # The only thing the lock holds. Next, previous, random and a jump
            # from the browser all still move while it is on -- a lock is for
            # "stay on this one", not "take the controls away", and a user who
            # has locked a preset they like still wants to look at the next.
            if not self._locked:
                elapsed = _time.monotonic() - self._preset_start
                if elapsed >= self._preset_duration:
                    self._do_next(hard=False)
                    # The one that is not being adjusted moves as well, on
                    # its own playlist position, so the two are never showing
                    # the same preset for long. Everything a person does --
                    # next, previous, the browser, the editor -- still goes
                    # to the focused one alone.
                    other = self._pms[1 - self._focus]
                    if other is not None:
                        try:
                            other.next_preset(hard_cut=False)
                        except Exception as why:
                            print(f"[dual] next failed: {why}", flush=True)

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
                # Both visuals, not the focused one: a second visual that
                # does not hear the music is a still picture.
                for pm in self._pms:
                    if pm is not None:
                        pm.add_pcm_float(flat, len(flat) // 2, 2)

            # ── Render ────────────────────────────────────────────────────
            t0 = _time.monotonic()
            # Before the preset draws, not after: the warp reads the buffer it
            # is about to draw over, so burning first is what puts the picture
            # through this frame's warp rather than the next one's.
            self._live_this_frame()
            self._render_visuals()
            # Before the swap, always: glReadPixels reads the back buffer, and
            # after SDL_GL_SwapWindow that is last frame's content, not this
            # one's.
            if self._rec is not None:
                self._capture_for_recording()
            self._maybe_thumbnail()
            sdl2.SDL_GL_SwapWindow(self._window)

            if self._edit_error_in:
                self._edit_error_in -= 1
                if not self._edit_error_in:
                    err = self._pm.last_error or _LAST_PM_ERROR
                    if err:
                        self._bridge.show_toast.emit(err)

            if _fps_t0 is not None:
                _fps_n += 1
                _dt = _time.monotonic() - _fps_t0
                if _dt >= 5.0:
                    print(f"[fps] {_fps_n / _dt:.1f}", file=sys.stderr, flush=True)
                    _fps_t0, _fps_n = _time.monotonic(), 0

            elapsed_frame = _time.monotonic() - t0
            # Read fps live so Settings changes take effect immediately.
            # fps <= 0 → uncapped (no sleep).
            _fps = self._cfg.get("fps", 60)
            frame_time = 1.0 / _fps if _fps and _fps > 0 else 0.0
            sleep_t = frame_time - elapsed_frame
            if sleep_t > 0.002:
                _time.sleep(sleep_t)

        # ── Cleanup ───────────────────────────────────────────────────────
        # Before the GL context goes: an unfinished recording leaves a
        # zero-byte file that no player will open.
        if self._rec is not None:
            self._stop_recording()
        # And before the context goes for the other reason: the burn-in's
        # ffmpeg is a child process, and leaving it running leaves a decoder
        # writing into a pipe nobody will ever read again.
        self._do_set_burn("")
        self._stop_live_textures()
        self._audio.stop()
        if self._blend is not None:
            self._blend.drop()
            self._blend = None
        for i, pm in enumerate(self._pms):
            if pm is not None:
                pm.destroy()
            self._pms[i] = None
        self._pm = None
        self._drop_window2()
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
        elif km("record", sym, scancode):
            self.request_record("mp4")
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
        elif km("toggle_borderless", sym, scancode):
            self._toggle_borderless()
        elif km("toggle_on_top", sym, scancode):
            self._toggle_on_top()
        elif km("exclude", sym, scancode):
            self.request_exclude()
        elif km("toggle_wallpaper", sym, scancode):
            # Only ever turns it on from here: once the window is a desktop
            # child it gets no keyboard, so the key cannot toggle it back --
            # the tray icon does that, same as the menu entry.
            self.request_wallpaper()
        elif km("fullscreen", sym, scancode) or km("fullscreen_alt", sym, scancode):
            self._toggle_fullscreen()
        elif km("rate_up", sym, scancode):
            self._rate_current(+1)
        elif km("rate_down", sym, scancode):
            self._rate_current(-1)
        elif km("toggle_dual", sym, scancode):
            self._do_set_dual(not self.dual_on())
        elif km("toggle_visual1", sym, scancode):
            # Straight through request_visual, not _do_set_v1: it is the one
            # that refuses to leave a black window.
            self.request_visual(0, not self._v1_on)
        elif km("crossfade_back", sym, scancode):
            self.nudge_crossfade(-CROSSFADE_STEP)
        elif km("crossfade_fwd", sym, scancode):
            self.nudge_crossfade(CROSSFADE_STEP)
        elif km("swap_focus", sym, scancode):
            if self.dual_on():
                self._do_set_focus(1 - self._focus)
                self._bridge.show_toast.emit(
                    "Adjusting the second visual" if self._focus
                    else "Adjusting the first visual")
        elif km("snapshot", sym, scancode):
            self._want_snapshot = True

    # ── Preset navigation ─────────────────────────────────────────────────

    def _do_next(self, hard: bool):
        if not self._pm:
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
        if not self._pm:
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
        if not self._pm:
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
        """[(full_path, stem), …] for the presets in play.

        Narrowed to the active collection when there is one, which is what
        makes sort, shuffle and delete-0★ act on the collection rather than
        silently dragging the whole library back in.
        """
        import glob
        from pathlib import Path
        preset_dir = self._cfg.get("preset_dir", "")
        if not preset_dir:
            return []
        files = glob.glob(os.path.join(preset_dir, "**", "*.milk"), recursive=True)
        pairs = [(f, Path(f).stem) for f in files]
        members = self.collections().get(self._collection) if self._collection \
            else None
        if members is None:
            return pairs
        want = set(members)
        return [ps for ps in pairs if ps[1] in want]

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

    def _do_shuffle_playlist(self) -> bool:
        """Reorder the playlist itself. Render thread only.

        projectM's own shuffle picks a random item on every advance, so the
        list is never walked: left and right both land somewhere new and there
        is no way back to what just played. Permuting the playlist and leaving
        the native shuffle off (_rebuild_playlist does that) keeps next/prev
        walking a sequence -- one that happens to be in a random order.

        The current preset is sought out again afterwards, so hitting shuffle
        reorders what comes next without interrupting what is on screen.
        """
        files = self._all_preset_files()
        if not files:
            self._bridge.status_message.emit("Shuffle: no presets found")
            return False
        _random.shuffle(files)
        cur = self._pm.current_preset_name()
        self._rebuild_playlist([p for p, _ in files])
        self._rated_history.clear()      # native indices changed
        self._seek_to_stem(cur)
        print(f"[renderer] Shuffled {len(files)} presets")
        return True

    def _do_goto(self, index: int):
        """Jump to a playlist index (from the browser double-click)."""
        if not self._pm:
            return
        try:
            self._pm.set_playlist_position(index, hard_cut=True)
            self._next_duration()
            self._preset_start = _time.monotonic()
            self._emit_current_preset()
        except Exception as e:
            print(f"[renderer] goto failed: {e}", flush=True)

    def _reload_preset_dir(self):
        """Load the preset directory Settings just changed to.

        Deliberately not _do_reload_playlist: that one rebuilds the playlist
        from the paths it already knows via projectm_playlist_add_presets,
        which some projectM builds do not export -- there it silently leaves
        an empty playlist behind and every preset name reads "Unknown". This
        takes the same route startup does, which works on every build.
        """
        if not self._pm:
            return
        d = self._cfg.get("preset_dir", "")
        if not os.path.isdir(d):
            print(f"[renderer] preset dir not usable: {d!r}", flush=True)
            self._bridge.status_message.emit("Preset folder not found")
            return
        try:
            self._pm.clear_playlist()
            count = self._pm.load_preset_dir(d)
            if count:
                self._pm.set_playlist_position(0, hard_cut=True)
            self._rated_history.clear()          # native indices changed
            self._shuffle = False                # a fresh list, in load order
            self._preset_start = _time.monotonic()
            print(f"[renderer] Loaded {count} presets from {d}", flush=True)
            self._bridge.status_message.emit(f"Loaded {count} presets")
            self._bridge.playlist_changed.emit()
            self._emit_current_preset()
        except Exception as e:
            print(f"[renderer] loading preset dir failed: {e}", flush=True)

    def _reload_current_preset(self):
        """Re-load the preset on screen, in place.

        projectM resolves a preset's textures when it loads it, so a texture
        directory changed while something is already playing does nothing
        visible until the next preset -- which, at a ten-minute duration on a
        locked preset, reads as "it needs a restart". Seeking to the position
        it is already at makes it load again against the new search paths.
        """
        if not self._pm:
            return
        try:
            pos = self._pm.playlist_position()
            self._pm.set_playlist_position(pos, hard_cut=True)
            self._preset_start = _time.monotonic()
            print(f"[renderer] Reloaded preset {pos} for the new textures",
                  flush=True)
            self._bridge.status_message.emit("Textures reloaded")
            self._emit_current_preset()
        except Exception as e:
            print(f"[renderer] reloading the current preset failed: {e}",
                  flush=True)

    def _do_reload_playlist(self):
        """Rebuild the playlist from whatever .milk files remain on disk (used
        after delete-zero removes files)."""
        if not self._pm:
            return
        try:
            cur = self._pm.current_preset_name()
            paths = [p for p, _ in self._all_preset_files()]
            if self._shuffle:
                _random.shuffle(paths)   # a rebuild must not silently unshuffle
            self._rebuild_playlist(paths)
            self._rated_history.clear()   # native indices changed
            if cur and cur not in ("Unknown", WELCOME_NAME):
                self._seek_to_stem(cur)
            elif paths:
                # Nothing was playing: first run, or the preset directory was
                # wrong until a moment ago. Start on the first preset rather
                # than leaving projectM's idle screen up until the next
                # auto-advance, which is half a minute by default.
                self._pm.set_playlist_position(0, hard_cut=True)
                self._preset_start = _time.monotonic()
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
        print(f"[renderer] Preset {'locked' if locked else 'unlocked'} "
              f"(the timer, not the controls)")

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
        """Shuffle on reshuffles the playlist; shuffle off leaves the order.

        Turning it off deliberately does *not* restore the previous order --
        you carry on walking whatever sequence you are in the middle of. That
        makes turning it on again the only way to get a new order, which is
        also how you reshuffle.
        """
        if not self._pm:
            return
        try:
            want = not self._shuffle
            if want and not self._do_shuffle_playlist():
                return                  # nothing to shuffle -- stay as we were
            self._shuffle = want
            label = "ON" if want else "OFF (order kept)"
            print(f"[renderer] Shuffle: {label}")
            self._bridge.status_message.emit(f"Shuffle: {label}")
            self._bridge.playlist_changed.emit()
            self._emit_current_preset()
        except Exception as e:
            print(f"[renderer] shuffle toggle failed: {e}", flush=True)

    # ── Fullscreen ────────────────────────────────────────────────────────

    def _track_window(self):
        """Refresh the cached window rect. Render thread only."""
        if not self._window:
            return
        x, y = ctypes.c_int(), ctypes.c_int()
        w, h = ctypes.c_int(), ctypes.c_int()
        sdl2.SDL_GetWindowPosition(self._window, ctypes.byref(x), ctypes.byref(y))
        sdl2.SDL_GetWindowSize(self._window, ctypes.byref(w), ctypes.byref(h))
        self._win_rect = (x.value, y.value, w.value, h.value)
        self._fullscreen = bool(sdl2.SDL_GetWindowFlags(self._window)
                                & sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP)

    def window_rect(self):
        """(x, y, w, h) of the visualiser window, or None before it exists.
        Safe to call from any thread -- it reads the cache, not SDL."""
        return self._win_rect

    # ── Desktop wallpaper ─────────────────────────────────────────────────

    def is_wallpaper(self) -> bool:
        return self._wallpaper

    def request_wallpaper(self, on: bool = None):
        """Become the desktop wallpaper, or stop (render thread)."""
        with self._lock:
            self._req_wallpaper = (not self._wallpaper) if on is None else bool(on)

    def _set_wallpaper_mode(self, on: bool):
        """Become the desktop: Explorer's wallpaper window on Windows, an
        EWMH desktop window on X11.

        Either way the visualiser ends up below everything and receives no
        keyboard or mouse input -- the desktop keeps it -- so the tray icon
        the host puts up is the only way back out. wallpaper_changed is what
        tells it to.
        """
        if on == self._wallpaper:
            return
        ok, why = wallpaper_support()
        if not ok:
            self._bridge.show_toast.emit(why)
            return
        # ponytail: dropped on the way in, never restored on the way out. Both
        # platforms wipe what carries it -- X11 rewrites _NET_WM_STATE whole,
        # Windows puts back a saved exstyle -- so a window that came back still
        # *claiming* to be on top would be lying. Press the key again.
        if on and self._on_top:
            self._toggle_on_top()
        try:
            if sys.platform == "win32":
                u = _user32()
                hwnd = ctypes.c_void_p(self._native_handle)
                if on:
                    host, layered = find_desktop_host(u)
                    if not host:
                        # Printed as well as toasted, because a toast is gone in
                        # three seconds and never reaches a log.
                        print("[wallpaper] no desktop window to draw into",
                              flush=True)
                        self._bridge.show_toast.emit(
                            "Wallpaper mode is not available on this version of "
                            "Windows.")
                        return
                    self._track_window()
                    self._wp_restore = (
                        self._win_rect, self._borderless,
                        u.GetWindowLongPtrW(hwnd, _GWL_STYLE) or 0,
                        u.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE) or 0)
                    self._wp_host = None
                    host_style = u.GetWindowLongPtrW(host, _GWL_STYLE) or 0
                    if layered and not host_style & _WS_CLIPCHILDREN:
                        # Left alone this WorkerW paints the wallpaper over its own
                        # children. Nothing is lost by clipping it: with no child
                        # of ours there is nothing to clip, so a crash in here
                        # leaves the desktop exactly as it was found.
                        u.SetWindowLongPtrW(host, _GWL_STYLE, ctypes.c_void_p(
                            host_style | _WS_CLIPCHILDREN))
                        self._wp_host = (host, host_style)
                    # The order below is the one that was measured to work, and
                    # the two obvious rearrangements were measured not to: SDL
                    # calls before the styles gave a window that never composited,
                    # and sizing through SDL alone left the HWND where it was.
                    if layered:
                        # Fully opaque: the layer is what gets the window
                        # composited at all, not an effect anyone should see.
                        ex = u.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE) or 0
                        u.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE,
                                            ctypes.c_void_p(ex | _WS_EX_LAYERED))
                        u.SetLayeredWindowAttributes(hwnd, 0, 255, _LWA_ALPHA)
                    u.SetParent(hwnd, host)
                    # A parented window that is still WS_POPUP is not drawn, and a
                    # child keeps no caption or frame.
                    st = u.GetWindowLongPtrW(hwnd, _GWL_STYLE) or 0
                    u.SetWindowLongPtrW(hwnd, _GWL_STYLE, ctypes.c_void_p(
                        (st | _WS_CHILD) & ~(_WS_POPUP | _WS_FRAME)))
                    self._wallpaper = True
                    box = self._wallpaper_box()
                    if box:
                        u.SetWindowPos(hwnd, _HWND_TOP, *box,
                                       _SWP_FRAMECHANGED | _SWP_SHOWWINDOW)
                    else:
                        u.SetWindowPos(hwnd, _HWND_TOP, 0, 0, 0, 0,
                                       _SWP_NOMOVE | _SWP_NOSIZE |
                                       _SWP_FRAMECHANGED | _SWP_SHOWWINDOW)
                    self._fit_to_wallpaper(box)
                else:
                    u.SetParent(hwnd, None)
                    self._wallpaper = False
                    rect, was_borderless, style, exstyle = (
                        self._wp_restore or (None, False, 0, 0))
                    if style:
                        # Back to a top-level window: keeping WS_CHILD leaves SDL
                        # with a window that has no parent to be a child of.
                        u.SetWindowLongPtrW(hwnd, _GWL_STYLE, ctypes.c_void_p(style))
                        u.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE,
                                            ctypes.c_void_p(exstyle))
                        u.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                                       _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER |
                                       _SWP_FRAMECHANGED | _SWP_SHOWWINDOW)
                    if self._wp_host:
                        # Hand Explorer its window back as it was found, then ask
                        # for a repaint: the clipped region is blank until
                        # something draws the wallpaper into it again.
                        host_hwnd, host_style = self._wp_host
                        u.SetWindowLongPtrW(host_hwnd, _GWL_STYLE,
                                            ctypes.c_void_p(host_style))
                        u.InvalidateRect(host_hwnd, None, True)
                        self._wp_host = None
                    if rect:
                        x, y, w, h = rect
                        sdl2.SDL_SetWindowPosition(self._window, x, y)
                        sdl2.SDL_SetWindowSize(self._window, w, h)
                    sdl2.SDL_SetWindowBordered(
                        self._window,
                        sdl2.SDL_FALSE if was_borderless else sdl2.SDL_TRUE)
                    self._borderless = was_borderless
                    self._track_window()
            else:
                self._set_x11_wallpaper(on)
        except Exception as e:
            print(f"[wallpaper] {e}", flush=True)
            self._bridge.show_toast.emit(f"Wallpaper mode failed: {e}")
            return
        self._bridge.wallpaper_changed.emit(self._wallpaper)
        self._bridge.status_message.emit(
            "Wallpaper mode ON — use the tray icon to leave it"
            if self._wallpaper else "Wallpaper mode OFF")

    def _set_x11_wallpaper(self, on: bool):
        """Ask the window manager to treat the visualiser as the desktop.

        _NET_WM_WINDOW_TYPE is read when a window is mapped, so changing it on
        a window that is already up does nothing until the window goes away and
        comes back. SDL_HideWindow/SDL_ShowWindow are used for that round trip
        rather than XUnmapWindow, so SDL's own idea of the window stays true.

        XLowerWindow at the end is the part that is not optional. The desktop
        type puts us in the bottom layer; it does not say where among the other
        windows already in that layer we land, and the window holding the
        desktop icons -- xfdesktop, Nemo, plasmashell -- is one of them. Lower
        the window and the icons are above it, which is the whole feature.
        """
        if not (self._native_handle and self._x11_display):
            raise RuntimeError("no X11 window to make the desktop")
        lib = _libx11()
        dpy, win = self._x11_display, self._native_handle
        sdl2.SDL_HideWindow(self._window)
        if on:
            self._track_window()
            self._wp_restore = (self._win_rect, self._borderless, 0, 0)
            x11_set_atoms(lib, dpy, win, _X11_TYPE, [_X11_DESKTOP])
            x11_set_atoms(lib, dpy, win, _X11_STATE, list(_X11_STATES))
            self._wallpaper = True
            # Being the desktop is what makes Nemo refuse to draw the icons
            # next time it starts, so this goes here rather than at launch:
            # nowhere else knows the window is about to claim the desktop.
            # Its own try: this is a convenience for the *next* session, and
            # a desktop setting that will not be written is not a reason to
            # refuse to be the wallpaper in this one.
            try:
                did = nemo_allow_desktop_window(x11_wm_class(lib, dpy, win))
            except Exception as e:                       # noqa: BLE001
                did = ""
                print(f"[wallpaper] could not check Nemo's ignore list: {e}",
                      flush=True)
            if did:
                # Said once in the life of the install, because the setting
                # persists. Deliberately not "your icons were switched off":
                # the common case is turning wallpaper mode on while Nemo is
                # already up and the icons are fine, and this is insurance
                # against the *next* restart. It only has to be true in both.
                print(f"[wallpaper] {did}", flush=True)
                self._bridge.show_toast.emit(
                    "Told Cinnamon to keep drawing your desktop icons over "
                    "the visualiser. If they are missing now, they are back "
                    "at the next login.")
            self._fit_to_wallpaper()
        else:
            x11_set_atoms(lib, dpy, win, _X11_TYPE, [_X11_NORMAL])
            x11_set_atoms(lib, dpy, win, _X11_STATE, [])
            self._wallpaper = False
            rect, was_borderless, _s, _e = (
                self._wp_restore or (None, False, 0, 0))
            sdl2.SDL_SetWindowBordered(
                self._window,
                sdl2.SDL_FALSE if was_borderless else sdl2.SDL_TRUE)
            self._borderless = was_borderless
            if rect:
                x, y, w, h = rect
                sdl2.SDL_SetWindowPosition(self._window, x, y)
                sdl2.SDL_SetWindowSize(self._window, w, h)
        # The properties have to be on the server before the map request or
        # the window manager reads the old ones.
        lib.XSync(dpy, False)
        sdl2.SDL_ShowWindow(self._window)
        if on:
            if not x11_lower_to_bottom(lib, dpy, win):
                # Not fatal: the visuals are up, they are just in front of the
                # icons. Say so rather than let it look like the feature works.
                print("[wallpaper] could not get below the desktop icons",
                      flush=True)
                self._bridge.show_toast.emit(
                    "Wallpaper mode is on, but this desktop will not put the "
                    "visuals behind it — use the tray icon to leave")
            # Again, after the map: a window manager places a desktop window
            # itself, and what it decides overwrites whatever was asked for
            # while the window was down.
            box = self._fit_to_wallpaper()
            if box:
                x11_align_frame(
                    lib, dpy, win, (box[0], box[1]),
                    lambda x, y: sdl2.SDL_SetWindowPosition(self._window, x, y))
                self._track_window()
        else:
            sdl2.SDL_RaiseWindow(self._window)
        lib.XSync(dpy, False)
        self._track_window()

    #: How often the wallpaper window re-checks that it is still the bottom
    #: one. Long enough that the cost is nothing (one property read off the
    #: root), short enough that nobody sits looking at covered icons.
    WP_HOLD_EVERY = 2.0

    def _hold_wallpaper_bottom(self):
        """Keep the window under the desktop icons for as long as it is the
        desktop.

        Getting there once is not enough. A window manager restacks on its own
        schedule -- a workspace switch, the icon manager restarting, another
        desktop-layer window being mapped -- and a compositing one paints from
        a stack of its own, so a single lower at map time is an invariant that
        quietly stops holding. Reported on Cinnamon: wallpaper mode switched on
        successfully, no toast, and the visuals were over the Nemo icons anyway.
        The toggle could not have known -- it had already measured the stack and
        been right at the time.

        So this is the same measurement, on a timer. One XGetWindowProperty per
        pass and nothing on the wire at all unless the answer is wrong.
        """
        now = _time.monotonic()
        if now - self._wp_hold_at < self.WP_HOLD_EVERY:
            return
        self._wp_hold_at = now
        if not (self._native_handle and self._x11_display):
            return
        try:
            lib = _libx11()
            dpy, win = self._x11_display, self._native_handle
            # Before the stacking check, because that one returns early in
            # the common case -- the window is usually already at the bottom.
            # Its own try: the stacking hold backs off for a minute when it
            # throws, and a window that cannot be measured is not a reason to
            # stop keeping it under the icons for the next sixty seconds.
            try:
                self._hold_wallpaper_box(lib, dpy, win)
            except Exception as e:                       # noqa: BLE001
                if not self._wp_box_moaned:
                    self._wp_box_moaned = True
                    print(f"[wallpaper] could not hold the geometry: {e}",
                          flush=True)
            stack = x11_client_stack(lib, dpy)
            if not stack or stack[0] == win or win not in stack:
                # Bottom already, or a window manager that publishes no client
                # stack -- there is nothing to measure against, and lowering on
                # a hunch every two seconds is how a window ends up fighting.
                return
            x11_restack_below(lib, dpy, win, stack[0])
            lib.XLowerWindow(dpy, x11_toplevel_of(lib, dpy, win))
            lib.XSync(dpy, False)
        except Exception as e:
            # Back off rather than repeat the same failure every two seconds
            # for as long as the app is up.
            self._wp_hold_at = now + 60.0
            print(f"[wallpaper] could not hold the bottom: {e}", flush=True)

    #: How many times running the geometry may be put back before the window
    #: manager is taken to have meant it. Without a stop, a window manager that
    #: insists on its own placement gets fought every two seconds for the life
    #: of the app, and the window flickers between the two answers.
    WP_FIX_LIMIT = 5

    def _hold_wallpaper_box(self, lib, dpy, win):
        """Keep the window *filling* the desktop, the same way the stacking is
        kept: by measuring rather than by remembering.

        x11_align_frame corrects for the frame once, while the window manager
        is still deciding whether a desktop window gets one at all. Measured on
        Muffin: it read a 32px frame that was gone a moment later, corrected by
        it, and left the window at y=-32 for the rest of the session. The strip
        of real wallpaper that leaves along the bottom is exactly the height of
        the panel covering it, so nobody sees it until the panel is hidden.

        Re-measuring also picks up the work area changing later -- a panel
        hidden, shown, or set to auto-hide -- which the one-shot fit could not.
        """
        box = self._wallpaper_box()
        if box is None:
            return
        rect = x11_window_rect(lib, dpy, win)
        origin = x11_window_origin(lib, dpy, x11_toplevel_of(lib, dpy, win))
        if rect is None or origin is None:
            return
        if origin == (box[0], box[1]) and rect[2:] == (box[2], box[3]):
            self._wp_fixes = 0          # settled; spend the budget again later
            return
        if self._wp_fixes >= self.WP_FIX_LIMIT:
            if not self._wp_gave_up:
                # The one path that would otherwise end in a wallpaper in the
                # wrong place and an empty log. Giving up is the right answer
                # -- a window manager entitled to place a desktop window will
                # win -- but it is not something to do quietly.
                self._wp_gave_up = True
                print(f"[wallpaper] this desktop keeps putting the window at "
                      f"{rect[0]},{rect[1]} {rect[2]}x{rect[3]} rather than "
                      f"{box[0]},{box[1]} {box[2]}x{box[3]} — left as it is",
                      flush=True)
            return
        self._wp_fixes += 1
        # Through X rather than SDL, which is the whole reason this is needed:
        # SDL drops a request that matches its own cached position and size,
        # and they match -- SDL asked for this box once and believes it got it.
        # The window manager moved the window afterwards and SDL never heard.
        # Measured: SDL_SetWindowPosition(0, 0) on a window sitting at y=-32
        # put nothing on the wire at all.
        lib.XMoveResizeWindow(dpy, x11_toplevel_of(lib, dpy, win),
                              box[0], box[1], box[2], box[3])
        lib.XSync(dpy, False)
        if rect[2:] != (box[2], box[3]):
            # Only a size change needs the rest of it: the GL viewport and
            # projectM's idea of the window. A move does not.
            self._fit_to_wallpaper(box)
        self._track_window()

    def _wallpaper_box(self):
        """Where the visualiser goes on the desktop: (x, y, w, h), or None.

        The chosen monitor, or the whole desktop when none is chosen. Not SDL
        fullscreen: a fullscreen window is taken out of its parent and put
        back on top, which is exactly what wallpaper mode is not.
        """
        self._refresh_displays()
        want = int(self._cfg.get("fullscreen_display", -1))
        box = next((d for d in self._displays if d[0] == want), None)
        if box is None and self._displays:
            xs = [d[2] for d in self._displays]
            ys = [d[3] for d in self._displays]
            x2 = max(d[2] + d[4] for d in self._displays)
            y2 = max(d[3] + d[5] for d in self._displays)
            box = (-1, "", min(xs), min(ys), x2 - min(xs), y2 - min(ys))
        if box is None:
            return None
        _i, _n, x, y, w, h = box
        # Positions are relative to WorkerW, whose origin is the top-left of
        # the virtual desktop -- which is not (0, 0) when a monitor sits above
        # or left of the primary one.
        vx = min((d[2] for d in self._displays), default=0)
        vy = min((d[3] for d in self._displays), default=0)
        return (x - vx, y - vy, w, h)

    def _fit_to_wallpaper(self, box=None):
        """SDL's side of the desktop geometry: its own bookkeeping, the GL
        viewport and projectM's idea of the window.

        The caller moves the HWND itself. SDL takes a new size into its
        bookkeeping and leaves a window it did not parent where it is --
        measured, SDL_GetWindowSize answered 1920x1080 while the HWND was
        still the 900x600 it started at, and the visualiser drew in the
        top-left corner of the desktop.
        """
        box = box or self._wallpaper_box()
        if box is None:
            return None
        x, y, w, h = box
        sdl2.SDL_SetWindowBordered(self._window, sdl2.SDL_FALSE)
        self._borderless = True
        sdl2.SDL_SetWindowPosition(self._window, x, y)
        sdl2.SDL_SetWindowSize(self._window, w, h)
        glViewport(0, 0, w, h)
        if self._pm:
            self._pm.set_window_size(w, h)
        self._track_window()
        return box

    # ── Screensaver ───────────────────────────────────────────────────────

    #: How far the pointer must travel before it counts as the user coming
    #: back. Windows uses a few pixels for the same reason: a mouse sitting on
    #: a soft surface reports motion on its own, and a screensaver that quits
    #: on the first stray event never stays up.
    SS_SLACK_PX = 12

    def _ss_should_quit(self, event) -> bool:
        """Does this event mean the user is back? Screensaver mode only."""
        if event.type in (sdl2.SDL_KEYDOWN, sdl2.SDL_MOUSEBUTTONDOWN):
            return True
        if event.type != sdl2.SDL_MOUSEMOTION:
            return False
        x, y = event.motion.x, event.motion.y
        if self._ss_origin is None:
            # The first motion event is where the pointer already was, not a
            # move -- taking it as one closes the screensaver instantly.
            self._ss_origin = (x, y)
            return False
        ox, oy = self._ss_origin
        return abs(x - ox) > self.SS_SLACK_PX or abs(y - oy) > self.SS_SLACK_PX

    # ── Displays ──────────────────────────────────────────────────────────

    def _refresh_displays(self):
        """Re-enumerate the monitors. Render thread only.

        Called again on every right-click rather than once at startup, so a
        monitor plugged in since launch is in the menu.
        """
        out = []
        try:
            for i in range(int(sdl2.SDL_GetNumVideoDisplays())):
                rect = sdl2.SDL_Rect()
                if sdl2.SDL_GetDisplayBounds(i, ctypes.byref(rect)) != 0:
                    continue
                raw = sdl2.SDL_GetDisplayName(i)
                name = raw.decode("utf-8", "replace") if raw else ""
                out.append((i, name or f"Display {i + 1}",
                            rect.x, rect.y, rect.w, rect.h))
        except Exception as e:
            print(f"[renderer] display enumeration failed: {e}", flush=True)
        if out:
            self._displays = out

    def list_displays(self):
        """[(index, name, x, y, w, h), ...]. Reads the cache, so any thread."""
        return list(self._displays)

    def is_fullscreen(self) -> bool:
        return self._fullscreen

    def request_fullscreen(self):
        """Toggle fullscreen (runs on the render thread)."""
        with self._lock:
            self._req_fullscreen = True

    # ── Recording ─────────────────────────────────────────────────────────

    #: How often the "is a game running" question is asked. It is a shell
    #: call, and the answer cannot change usefully faster than this.
    FS_APP_POLL_S = 2.0

    def _check_idle(self) -> bool:
        """Should this frame be skipped? Updates the idle state as it goes."""
        if self._rec is not None:
            # Never while recording. The sound tap keeps running whatever this
            # loop does, so a pause would leave a clip whose audio is minutes
            # longer than its video.
            return False
        now = _time.monotonic()
        if now - self._fs_app_checked >= self.FS_APP_POLL_S:
            self._fs_app_checked = now
            self._fs_app = fullscreen_app_running()
        if not self._last_sound:
            self._last_sound = now      # first pass: nothing has been quiet yet
        idle = should_idle(
            str(self._cfg.get("pause_when_idle", "wallpaper")),
            self._wallpaper,
            now - self._last_sound,
            float(self._cfg.get("idle_silence_seconds", 20)),
            self._fs_app)
        if idle and not self._idle:
            self._idle, self._idle_since = True, now
            print("[idle] paused", flush=True)
        elif not idle and self._idle:
            self._idle = False
            # Give the current preset back the time it did not get to play.
            self._preset_start += now - self._idle_since
            print("[idle] resumed", flush=True)
        return self._idle

    def audio_level(self) -> float:
        """Peak of the last captured chunk, for the menu's level meter."""
        try:
            return self._audio.level()
        except Exception:
            return 0.0

    def request_record(self, kind: str = "mp4"):
        """Start recording, or stop the one running (render thread)."""
        with self._lock:
            self._req_record = "stop" if self._rec else (
                kind if kind in ("mp4", "gif") else "mp4")

    def is_recording(self) -> bool:
        return self._rec is not None

    def _grab_frame(self):
        """(width, height, RGB bytes) of the frame just rendered.

        Render thread only, and it has to run BEFORE SDL_GL_SwapWindow -- after
        the swap the back buffer holds whatever was there two frames ago. The
        drawable size, not the window size: they differ on a HiDPI display and
        glReadPixels works in pixels. Rows come out bottom-up, which is why
        every ffmpeg filter chain here starts with vflip.
        """
        w, h = ctypes.c_int(), ctypes.c_int()
        sdl2.SDL_GL_GetDrawableSize(self._window,
                                    ctypes.byref(w), ctypes.byref(h))
        if w.value <= 0 or h.value <= 0:
            return None
        # Default pack alignment is 4, which pads every row whose length is not
        # a multiple of it -- a window 1 pixel off would shear the whole image.
        glPixelStorei(GL_PACK_ALIGNMENT, 1)
        data = glReadPixels(0, 0, w.value, h.value, GL_RGB, GL_UNSIGNED_BYTE)
        buf = data.tobytes() if hasattr(data, "tobytes") else bytes(data)
        return w.value, h.value, buf

    def _start_recording(self, kind: str):
        grab = self._grab_frame()
        if grab is None:
            return
        w, h, _ = grab
        stamp = _time.strftime("%Y-%m-%d_%H-%M-%S")
        path  = os.path.join(clips_dir(), f"{APP_SLUG}_{stamp}.{kind}")
        rec   = _Recorder(path, (w, h), self._rec_fps, kind,
                          audio_rate=self._audio.rate())
        why   = rec.start_encoder()
        if why:
            print(f"[record] {why}", flush=True)
            self._bridge.show_toast.emit(why)
            return
        self._rec      = rec
        self._rec_size = (w, h)
        self._rec_last = 0.0
        # Straight off the capture backend, not off the render loop: the loop
        # pops one chunk per frame and the backend pushes about 86 a second,
        # so a clip built from what the loop sees would be a third short.
        self._audio.set_tap(rec.write_audio)
        print(f"[record] recording to {path}", flush=True)
        self._bridge.show_toast.emit(
            f"⏺  Recording {kind.upper()} — press the record key again to stop")

    def _stop_recording(self):
        rec, self._rec, self._rec_size = self._rec, None, None
        if rec is None:
            return
        self._audio.set_tap(None)
        err = rec.finish()
        if err:
            print(f"[record] {err}", flush=True)
            self._bridge.show_toast.emit(f"Recording failed: {err}")
            return
        note = f"  ({rec.dropped} frames dropped)" if rec.dropped else ""
        if rec.warning:
            print(f"[record] {rec.warning}", flush=True)
            note += f"  — {rec.warning}"
        print(f"[record] wrote {rec.path}  {rec.frames} frames{note}",
              flush=True)
        self._bridge.show_toast.emit(
            f"⏹  Saved {os.path.basename(rec.path)}{note}")

    # ── Burn-in ───────────────────────────────────────────────────────────

    def _do_set_burn(self, path: str):
        """Start, change or stop the burnt-in picture. Render thread."""
        if self._burn_src is not None:
            self._burn_src.stop()
            self._burn_src = None
        self._burn_image, self._burn_want = None, None
        if not path:
            self._drop_burn_texture()
            return
        if not self.can_burn():
            self._bridge.show_toast.emit(
                "This build of projectM cannot burn a picture into a preset.")
            return
        src = frame_source(path)
        if src is None:
            self._bridge.show_toast.emit(
                f"Nothing here can play {os.path.basename(path)}")
            return
        why = getattr(src, "error", "")
        if why:
            src.stop()
            self._bridge.show_toast.emit(why)
            return
        self._burn_src = src
        print(f"[burn] {os.path.basename(path)}", flush=True)

    def _drop_burn_texture(self):
        """Give the GL texture back. Render thread -- it is the only one that
        may touch the context."""
        if self._burn_tex:
            try:
                glDeleteTextures(1, [self._burn_tex])
            except Exception:
                pass
        self._burn_tex, self._burn_size = 0, (0, 0)

    def _upload_burn(self, w: int, h: int, buf: bytes):
        """Put one RGBA frame in the burn-in texture. Render thread."""
        self._burn_tex, self._burn_size = gl_upload_rgba(
            self._burn_tex, self._burn_size, w, h, buf)

    def _burn_this_frame(self, pm=None):
        """Draw the burnt-in picture into the preset. Render thread.

        The canvas is rebuilt only when the decoder has a new frame or the
        placement moved -- a GIF at 14fps against a 60fps render would
        otherwise be composited four times for every frame it has. The burn
        itself happens every frame regardless: the preset's warp takes the
        picture away again immediately, so drawing it once in four would show
        it flickering rather than sitting there.
        """
        pm = pm or self._pm
        if self._burn_src is None or not pm:
            return
        try:
            dw, dh = self._drawable_size()
            if dw <= 0 or dh <= 0:
                return
            fresh = self._burn_src.next(_time.monotonic())
            if fresh is not None:
                self._burn_image = fresh
            if self._burn_image is None:
                return
            e = self.burn_in()
            want = (dw, dh, e["size"], e["x"], e["y"], e["alpha"])
            if fresh is not None or want != self._burn_want:
                rect = burn_fit(self._burn_image.width(),
                                self._burn_image.height(),
                                dw, dh, e["size"], e["x"], e["y"])
                if want != self._burn_want:
                    print(f"[burn] {self._burn_image.width()}x"
                          f"{self._burn_image.height()} at {rect[0]},{rect[1]} "
                          f"{rect[2]}x{rect[3]} of {dw}x{dh}", flush=True)
                self._burn_want = want
                self._upload_burn(
                    dw, dh, burn_canvas(self._burn_image, dw, dh, rect,
                                        e["alpha"]))
            if self._burn_tex:
                # (0, 0, whole window) is the only call this API gets right --
                # see burn_canvas.
                pm.burn_texture(self._burn_tex, 0, 0, dw, dh)
        except Exception as why:
            # One bad frame must not take the render thread down, and a
            # message every frame is worse than none: stop, and say so once.
            print(f"[burn] stopped: {why}", flush=True)
            self._do_set_burn("")
            self._bridge.show_toast.emit(f"Burn-in stopped: {why}")

    # ── Two visuals at once ───────────────────────────────────────────────

    #: How long the crossfade key has to be still before the new position is
    #: written down. Long enough to swallow a held key's repeats, short
    #: enough that nudging it and quitting keeps the nudge.
    FADE_SETTLE_S = 0.6

    def dual_on(self) -> bool:
        """Is a second visual on screen? Either thread."""
        return self._pms[1] is not None

    def focus(self) -> int:
        """Which visual the controls are driving. 0 is the primary."""
        return self._focus

    def can_dual(self) -> bool:
        """False on a libprojectM that cannot render into a framebuffer."""
        return bool(self._pm and getattr(self._pm, "can_render_fbo", False))

    def request_dual(self, on: bool):
        """Turn the second visual on or off. Qt thread."""
        with self._lock:
            self._req_dual = bool(on)

    def request_focus(self, which: int):
        """Point the controls at the primary (0) or the secondary (1)."""
        with self._lock:
            self._req_focus = 1 if which else 0

    def visual_on(self, which: int) -> bool:
        """Is visual 1 (0) or visual 2 (1) on screen? Either thread."""
        return self.dual_on() if which else bool(self._v1_on)

    def request_visual(self, which: int, on: bool):
        """Switch one of the two visuals on or off. Qt thread.

        The last one standing stays: switching both off is a black window and
        no way back from a key, so the request is simply dropped. Callers that
        can grey the control out should -- this is the backstop for the ones
        that cannot, like a key binding.
        """
        which = 1 if which else 0
        on = bool(on)
        if not on and not self.visual_on(1 - which):
            return
        with self._lock:
            if which:
                self._req_dual = on
            else:
                self._req_v1 = on

    def crossfade(self) -> float:
        """Where the fader sits. 0 is visual 1 alone, 1 is visual 2 alone."""
        return dual_crossfade(self._cfg)

    def request_crossfade(self, x: float):
        """Move the fader. Read by the render thread on the next frame.

        A bare float write, like the strength it replaces: the render thread
        reads it once a frame and a half-written one is not a thing CPython
        can hand it.
        """
        try:
            x = min(1.0, max(0.0, float(x)))
        except (TypeError, ValueError):
            return
        self._cfg["dual_crossfade"] = round(x, 3)
        self._bridge.crossfade_changed.emit(self._cfg["dual_crossfade"])

    def nudge_crossfade(self, delta: float):
        """Move the fader by a step, for the two keys bound to it.

        Marked for saving rather than saved here: a held key repeats about
        thirty times a second and crossing the range takes twenty presses,
        so writing on each one is a file write per frame for no gain. The
        render loop writes it once the key stops.
        """
        self.request_crossfade(self.crossfade() + delta)
        self._fade_dirty = _time.monotonic()

    def second_window_display(self) -> int:
        """Which monitor visual 2 has a window on. -1 is "none". Any thread."""
        return self._window2_display

    def request_second_window(self, display: int):
        """Give visual 2 a monitor of its own, or take it back. Qt thread.

        -1 puts it back in the one window, blended against visual 1 by the
        fader -- and so does naming the monitor visual 1 is already on, which
        is the same request said a different way.
        """
        try:
            display = int(display)
        except (TypeError, ValueError):
            return
        with self._lock:
            self._req_win2 = display

    def request_fullscreen_on(self, display: int):
        """Put the main window fullscreen on a named monitor. Qt thread.

        Not the fullscreen toggle: picking a monitor is an instruction to go
        there, and toggling would send every second pick back to a window.
        """
        with self._lock:
            self._req_fullscreen_on = int(display)

    def _render_visuals(self):
        """Draw this frame. Render thread.

        One visual is the ordinary path and costs nothing new. Two means each
        draws into a colour texture of its own and a shader pass mixes them,
        which is the only way to blend two framebuffers -- glBlitFramebuffer
        ignores blending.
        """
        second = self._pms[1]
        dw, dh = self._drawable_size()
        if second is not None and (dw <= 0 or dh <= 0):
            # Nothing to draw into this frame -- the window is mid-way
            # through a fullscreen change, or minimised. Skip the frame.
            #
            # It used to fall through to the line below, and that line is
            # permanent: one frame that arrived with no size took the second
            # visual down for the rest of the session, saying only
            # "[dual] off" with no reason, which reads exactly like the user
            # having turned it off. Seen for real in the rig on 14 Sep 2026,
            # after a fullscreen toggle while a clip was recording -- and it
            # is racy, so it did not happen on the next try.
            return
        if (second is not None and self._blend is not None
                and self._blend.ready(dw, dh)):
            # A screen each means the fader is not mixing anything: both
            # visuals are wanted at full strength, so neither may be skipped
            # and neither is weighted. `x` is None for exactly that case.
            split = self._window2 is not None
            x = None if split else (
                1.0 if not self._v1_on else dual_crossfade(self._cfg))
            for i, pm in enumerate(self._pms):
                # A visual whose switch is off is not drawn, whether or not
                # the fader is what is carrying the "off". Without this line
                # the split case below draws visual 1 into this window at
                # full strength while its own switch says it is off -- each
                # half is covered on its own and the pair was not.
                if i == 0 and not self._v1_on:
                    continue
                # Don't draw what the crossfader has faded out. The shader
                # multiplies that end by zero, so its target is not sampled
                # and a stale frame in it cannot show -- which makes parking
                # the fader at one end cost one visual rather than two.
                # The price is that a fully faded visual stops advancing;
                # it picks up where it left off, which is what "off" should
                # look like.
                if x is not None and ((x >= 1.0 and i == 0) or
                                      (x <= 0.0 and i == 1)):
                    continue
                # The burn-in goes into whichever visual is being adjusted,
                # matching every other control.
                if i == self._focus:
                    self._burn_this_frame(pm)
                # render_frame_fbo, not render_frame: the plain one targets
                # framebuffer 0 whatever is bound when it is called, so both
                # visuals went to the window and the blend then painted two
                # empty textures over the top. A black window, no GL error,
                # and nothing in the log.
                pm.render_frame_fbo(self._blend.target(i))
            if split:
                # Visual 2's window first, then visual 1 into this one. The
                # caller reads THIS window's back buffer for the recording
                # and for thumbnails, so it has to be the one left drawn.
                self._draw_second_window()
                if self._v1_on:
                    self._blend.draw(dw, dh, 0.0)
                else:
                    # Visual 1 switched off with visual 2 away on its own
                    # monitor leaves this window nothing to show, and
                    # `draw(0.0)` is "visual 1 alone" -- the one thing the
                    # switch says must not be on screen. Black instead, and
                    # black is also what the recorder and the thumbnail then
                    # read, which is the honest answer to "record what this
                    # window is showing".
                    self._clear_window(dw, dh)
            else:
                self._blend.draw(dw, dh, x)
            return
        if second is not None:
            # The blender gave up -- it says why once. Fall back to one
            # visual rather than a black window.
            self._do_set_dual(False)
        self._burn_this_frame()
        self._pm.render_frame()

    def _do_set_v1(self, on: bool):
        """Draw the primary, or stop drawing it. Render thread.

        Nothing is created or destroyed -- the primary projectM is the one
        the playlist, the ratings and the browser all speak to, and taking it
        down would take them with it. The crossfader is parked at the far end
        instead, which is what "off" already looks like to the shader.
        """
        on = bool(on)
        if on == self._v1_on:
            return
        self._v1_on = on
        self._cfg["visual1_enabled"] = on
        # Written through, the same as the hard-cuts key: it is a switch in a
        # menu now, not a transient toggle, and a tick that is gone at the
        # next launch reads as the setting not having taken.
        self._save_config(self._cfg)
        self._bridge.visual_changed.emit(0, on)

    def _do_set_dual(self, on: bool):
        """Bring the second visual up or take it down. Render thread."""
        if bool(on) == self.dual_on():
            return
        if on:
            self._start_dual()
            if self.dual_on():
                # Back on the monitor it was sent to. `visual2_display` is a
                # setting like the switches beside it, and `_stop_dual` drops
                # the window without clearing it, so without this the *menu*
                # is what lies: it ticks the monitor out of config, and both
                # a switch off and on again and every launch after using the
                # feature showed visual 2 as being on a screen it was not on.
                #
                # Queued rather than done here: the loop drains `_req_win2`
                # straight after the request that got us here, and by then
                # `run` has enumerated the monitors that `_do_set_window2`
                # reads. -1 costs nothing -- it is the early return there --
                # and a monitor that has since been unplugged says so and
                # falls back to the one window.
                self._req_win2 = int(self._cfg.get("visual2_display", -1))
        else:
            self._stop_dual()
        self._cfg["dual_enabled"] = self.dual_on()
        self._save_config(self._cfg)
        self._bridge.dual_changed.emit(self.dual_on())
        self._bridge.visual_changed.emit(1, self.dual_on())
        if not self.dual_on() and not self._v1_on:
            # It went down on its own -- the blender gave up, or it never
            # started. Something has to be on screen.
            self._do_set_v1(True)

    def _start_dual(self):
        """A second projectM in the same GL context. Render thread.

        Its own instance, its own playlist position and its own quality, so
        the two are independent in everything except the window they share.
        """
        if not self.can_dual():
            self._bridge.show_toast.emit(
                "This build of projectM cannot show two visuals at once.")
            return
        dw, dh = self._drawable_size()
        fps = self._cfg.get("fps", 60)
        second = None
        try:
            second = self._create_projectm()
            second.create(max(1, dw), max(1, dh),
                          fps if fps and fps > 0 else 120)
            # Low by default and separately settable: the second visual is
            # the one you are not looking at, and the pair costs about twice
            # a single one to draw.
            cfg = dict(self._cfg)
            cfg["quality"] = self._cfg.get("dual_quality", "low")
            cfg.pop("mesh_x", None)
            cfg.pop("mesh_y", None)
            self._apply_pm_settings(cfg, second)
            paths = self._texture_paths()
            if paths:
                second.set_texture_search_paths(paths)
            if self._cfg.get("live_textures", True):
                second.set_texture_load_callback(self._on_texture_wanted)
            preset_dir = self._cfg.get("preset_dir", "")
            if os.path.isdir(preset_dir):
                count = second.load_preset_dir(preset_dir)
                # Somewhere else in the library, or both visuals open on the
                # same preset and the whole thing reads as not having worked.
                # Half the library away rather than a random jump, so it is
                # never the same preset and never a coin toss.
                if count > 1:
                    here = 0
                    try:
                        here = int(self._pm.playlist_position())
                    except Exception:
                        pass
                    second.set_playlist_position((here + count // 2) % count,
                                                 hard_cut=True)
        except Exception as why:
            print(f"[dual] could not start: {why}", flush=True)
            self._bridge.show_toast.emit(f"Two visuals failed: {why}")
            # A half-built instance still holds a projectM and everything it
            # loaded. Failing to start is not a reason to keep it.
            if second is not None:
                try:
                    second.destroy()
                except Exception:
                    pass
            return
        self._pms[1] = second
        if self._blend is None:
            self._blend = _Blender()
        self._blend.error = ""
        print("[dual] on", flush=True)

    def _stop_dual(self):
        """Take the second visual down. Render thread."""
        # Its own window shows visual 2 and nothing else, so an empty one is
        # a black rectangle over a monitor with no way to shift it.
        self._drop_window2()
        second, self._pms[1] = self._pms[1], None
        # Back to the primary first: leaving the controls pointed at a
        # visual that is being destroyed is a use-after-free with extra
        # steps.
        self._do_set_focus(0)
        if second is not None:
            try:
                second.destroy()
            except Exception as why:
                print(f"[dual] destroy failed: {why}", flush=True)
        if self._blend is not None:
            self._blend.drop()
            self._blend = None
        # Guarded, and the guard is not for the tests. PyOpenGL resolves an
        # entry point on first use and raises NullFunctionError when it
        # cannot -- silently no-opping on Linux and raising on Windows, the
        # same split CLAUDE.md records for glViewport. The route that gets
        # here is the one that matters: a blender that would not build calls
        # _do_set_dual(False) from inside the render loop, so the machine
        # with no framebuffer entry points is exactly the machine that runs
        # this line, and an exception there kills the render thread with a
        # black window and no frames.
        if bool(glBindFramebuffer):
            glBindFramebuffer(GL_FRAMEBUFFER, 0)
        print("[dual] off", flush=True)

    def _do_set_focus(self, which: int):
        """Point every control at one visual or the other. Render thread.

        `self._pm` *is* the focus: the browser, the menu, the keys, the tweak
        panel and a hundred other call sites read it without knowing there
        are two.
        """
        which = 1 if which else 0
        if self._pms[which] is None:
            which = 0
        self._focus = which
        self._pm = self._pms[which]
        self._cfg["dual_focus"] = which
        self._preset_start = _time.monotonic()
        self._bridge.focus_changed.emit(which)
        self._emit_current_preset()

    # ── Live textures ─────────────────────────────────────────────────────

    def _on_texture_wanted(self, name: str):
        """Answer for one texture, or None to leave it to projectM.

        Called on the render thread from inside projectM, once per name --
        it caches what comes back -- so the cost here is a directory listing
        and a decode at preset-load time, not per frame.

        Returning None is not a failure: projectM falls back to its own disk
        scan, which is the right answer for every name it can read itself.
        It does not cache that fallback either, so a name declined now is
        retried by the next preset that asks for it.
        """
        key = os.path.splitext(os.path.basename(name or ""))[0].lower()
        live = self._live_tex.get(key)
        if live is not None:
            return (live.tex, live.w, live.h)
        if not key or key in self._live_missing:
            return None
        if len(self._live_tex) >= LIVE_MAX_TEXTURES:
            # Not given back while projectM holds the ids, so the ceiling is
            # a ceiling. Said once per name rather than once per frame.
            self._live_missing.add(key)
            print(f"[texture] {key}: already holding {LIVE_MAX_TEXTURES}",
                  flush=True)
            return None
        try:
            live = self._open_live_texture(key)
        except Exception as why:
            print(f"[texture] {key}: {why}", flush=True)
            live = None
        if live is None:
            self._live_missing.add(key)
            return None
        self._live_tex[key] = live
        print(f"[texture] {key} {live.w}x{live.h} from "
              f"{os.path.basename(live.path)}", flush=True)
        return (live.tex, live.w, live.h)

    def _open_live_texture(self, key: str):
        """Decode `key`'s file and put its first frame in a GL texture.

        Synchronous, including the ffmpeg probe a video needs. That probe is
        a fifth of a second on the render thread, once, for a file somebody
        deliberately put in a texture directory -- and the alternative, an
        answer that arrives two frames later, is a preset that loaded with a
        placeholder where its texture should be.
        """
        path = live_texture_file(key, self._texture_paths())
        if not path:
            return None
        src = frame_source(path, LIVE_IMAGE_EXTENSIONS)
        if src is None:
            return None
        why = getattr(src, "error", "")
        if why:
            src.stop()
            print(f"[texture] {key}: {why}", flush=True)
            return None
        img = src.next(_time.monotonic())
        if img is not None:
            w, h = img.width(), img.height()
            buf = _image_bytes(img.convertToFormat(
                QImage.Format.Format_RGBA8888))
        else:
            # A video: the decoder thread knows the size before it has a
            # frame, so the texture is made empty now and filled in a frame
            # or two. Black rather than nothing -- projectM is about to
            # sample it either way.
            w, h = int(getattr(src, "width", 0)), int(getattr(src, "height", 0))
            buf = bytes(max(0, w * h * 4))
        if w <= 0 or h <= 0:
            src.stop()
            return None
        tex, _ = gl_upload_rgba(0, (0, 0), w, h, buf)
        return _LiveTexture(key, path, src, tex, w, h)

    def _live_this_frame(self):
        """Hand every live texture its next frame. Render thread.

        Before the preset draws, like the burn-in, so what the preset samples
        this frame is this frame's picture. A still costs a dictionary lookup
        and a comparison; nothing is uploaded until a decoder has something.
        """
        if not self._live_tex:
            return
        now = _time.monotonic()
        for key, live in list(self._live_tex.items()):
            try:
                live.refresh(now)
            except Exception as why:
                # Stop that one, keep the others, and say so once: a message
                # every frame is worse than none.
                print(f"[texture] {key} stopped: {why}", flush=True)
                live.stop()

    def _stop_live_textures(self):
        """Stop every decoder. Render thread, at shutdown.

        The GL textures are not deleted here: projectM is holding those ids
        and is destroyed a moment later, which takes the context with it.
        What must not be left behind is a video's ffmpeg, writing into a pipe
        nobody will read again.
        """
        for live in self._live_tex.values():
            live.stop()
        self._live_tex.clear()
        self._live_missing.clear()

    # ── Thumbnails ────────────────────────────────────────────────────────

    #: How long a preset must stay on screen before it is worth a thumbnail.
    #: Long enough that the picture has settled and that a preset skipped past
    #: never gets one -- the cache should fill with what was actually watched.
    THUMB_AFTER = 5.0

    def _maybe_thumbnail(self):
        """Cache a picture of the current preset, once, after it has settled.

        Never while two visuals are on screen: the window is the two of them
        mixed, and filing that under one preset's name puts a picture in the
        browser that the preset cannot produce on its own.

        Deliberately passive: there is no batch pass over the library. Ten
        thousand presets rendered for a second each is two hours of nobody's
        time, and the thumbnails that matter are the ones for presets the
        owner has actually sat through.

        An asked-for snapshot jumps every one of those guards -- the wait, the
        once-per-preset latch and the file already being there. Somebody
        pressing the button is looking at the frame they want.
        """
        forced = self._want_snapshot
        self._want_snapshot = False
        if self.dual_on():
            if forced:
                self._bridge.show_toast.emit(
                    "Not with two visuals on screen \u2014 the picture would "
                    "be both of them.")
            return
        if not forced:
            if self._thumb_done or not self._cfg.get("auto_thumbnails", True):
                return
            if _time.monotonic() - self._preset_start < self.THUMB_AFTER:
                return
            self._thumb_done = True
        try:
            name = self._pm.current_preset_name()
            if not name:
                return
            if not forced and os.path.isfile(thumb_path(name)):
                return
            grab = self._grab_frame()
            if grab:
                w, h, buf = grab
                # Bound late, and by value: the preset will have moved on by
                # the time the writer gets here, and the row that needs
                # redrawing is the one this picture belongs to.
                thumb_writer().submit(
                    thumb_path(name), w, h, buf,
                    lambda n=name: self._bridge.thumbnail_ready.emit(n))
                if forced:
                    self._bridge.show_toast.emit(f"\U0001f4f7  Snapped {name}")
        except Exception as e:
            print(f"[thumb] capture failed: {e}", flush=True)

    def _capture_for_recording(self):
        """Feed one frame to the encoder, at the recording frame rate."""
        now = _time.monotonic()
        if now - self._rec_last < 1.0 / self._rec_fps:
            return
        self._rec_last = now
        grab = self._grab_frame()
        if grab is None:
            return
        w, h, buf = grab
        if (w, h) != self._rec_size:
            # ffmpeg was told one frame size and cannot be told another. Every
            # later frame would be dropped, so the clip would freeze here
            # anyway -- ending it says so instead of looking like a hang.
            self._stop_recording()
            self._bridge.show_toast.emit(
                "⏹  Recording stopped — the window was resized")
            return
        self._rec.submit(buf)

    def _move_to_chosen_display(self):
        """Put the window on the configured monitor before it goes fullscreen.

        SDL has no "fullscreen on display N" call -- it takes a window
        fullscreen on whichever display the window currently sits on -- so the
        only way to choose is to move the window there first.
        """
        want = int(self._cfg.get("fullscreen_display", -1))
        if want < 0:                       # follow the window, the old behaviour
            return
        self._refresh_displays()
        for idx, _name, x, y, w, h in self._displays:
            if idx != want:
                continue
            ww, wh = ctypes.c_int(), ctypes.c_int()
            sdl2.SDL_GetWindowSize(self._window,
                                   ctypes.byref(ww), ctypes.byref(wh))
            sdl2.SDL_SetWindowPosition(self._window,
                                       x + max(0, w - ww.value) // 2,
                                       y + max(0, h - wh.value) // 2)
            return

    #: How far past each edge of the display the fullscreen window sits, in
    #: pixels. Windows hands a window whose rectangle is exactly the monitor's
    #: the display itself and stops compositing anything over it, so the
    #: context menu -- genuinely in front, holding the pointer grab, taking
    #: clicks -- is never drawn: it appears for one frame and is gone, and the
    #: whole screen flashes as the compositor changes mode around it. Sitting
    #: a little past every edge covers the same pixels and leaves the
    #: rectangle different from the monitor's, which is all it takes.
    #: Measured on 11 Pro 26200 by grabbing the screen twice in a row: at
    #: exactly 1920x1080 four pairs out of four are byte-identical, so nothing
    #: is reaching the compositor; two pixels over each edge and none are.
    #: Two, not one: the same test at 1920x1081 and 1920x1082 still covered
    #: the monitor exactly and stayed uncomposited.
    _FS_OVERHANG = 2

    #: How long to spend measuring vsync, and how many frames at most.
    #: Bounded both ways on purpose: six frames on a healthy 60 Hz display
    #: cost 100 ms, and on a driver where a vsynced swap blocks for a quarter
    #: of a second the time limit stops the measurement after one.
    _VSYNC_PROBE_S, _VSYNC_PROBE_FRAMES = 0.25, 6

    def _set_swap_interval(self, fps):
        """vsync on, unless the frame rate is uncapped or vsync does not work.

        `fps <= 0` means uncapped, and being pinned to the refresh rate is
        precisely what that asks not to happen. The measurement below is done
        once per run and remembered, so changing the frame rate in Settings
        does not pay for it again.
        """
        if not fps or fps <= 0:
            sdl2.SDL_GL_SetSwapInterval(0)
            return
        sdl2.SDL_GL_SetSwapInterval(1)
        if self._vsync_ok is None:
            self._vsync_ok = self._vsync_paces()
            if not self._vsync_ok:
                print("[renderer] vsync is not pacing frames here - "
                      "capping in software instead", flush=True)
        if not self._vsync_ok:
            sdl2.SDL_GL_SetSwapInterval(0)

    def _vsync_paces(self) -> bool:
        """Is vsync pacing frames to the refresh rate, or stalling on it?

        SDL hands back the interval it was asked for and every frame the
        driver produces is correct -- just very late -- so nothing else in
        the app can tell the difference.

        Measured on this project's own Windows box, 10 Sep 2026 (AMD
        Pitcairn, legacy driver 27.20.1034.6, Windows 11 build 26200): a
        windowed vsynced swap blocked for about 250 ms instead of 17 ms --
        4 fps on a card that does 4,500 uncapped. Reproduced three times by a
        probe containing none of this app's code, foreground and background,
        at two window sizes. An hour later, with no driver change and no
        reboot, the same probe read a healthy 59.9 fps.

        So it is an intermittent stall somewhere in the compositing stack,
        **not** a property of the driver, and the cause is not established --
        the suspicion is DWM left degraded, because the machine had spent the
        afternoon with windows parented into Explorer's WorkerW for wallpaper
        mode. Do not write it up as a driver bug without measuring again.

        That is the argument for measuring rather than assuming in either
        direction. Where vsync is healthy this costs six swaps at startup and
        changes nothing; where it has collapsed it is the difference between
        4 fps and the frame cap. The same driver also ignores the interval
        entirely once the window is fullscreen, so measuring once, windowed,
        at startup is safe: where vsync does nothing anyway, turning it off
        costs nothing.

        The software frame cap at the bottom of `run` is what holds the rate
        afterwards, and it is already there for the uncapped case.
        """
        # The first swap after a context is created is not representative on
        # any driver; it is the one that allocates the back buffers.
        sdl2.SDL_GL_SwapWindow(self._window)
        t0, n = _time.monotonic(), 0
        while (n < self._VSYNC_PROBE_FRAMES
               and _time.monotonic() - t0 < self._VSYNC_PROBE_S):
            sdl2.SDL_GL_SwapWindow(self._window)
            n += 1
        dt = _time.monotonic() - t0
        mode = sdl2.SDL_DisplayMode()
        idx = max(sdl2.SDL_GetWindowDisplayIndex(self._window), 0)
        hz = (mode.refresh_rate
              if sdl2.SDL_GetCurrentDisplayMode(idx, _ctypes.byref(mode)) == 0
              else 0)
        return vsync_paces(n, dt, hz)

    def _overhang_display(self):
        """Sit the fullscreen window just past the display edges. Windows only.

        SDL is not told: it keeps its own idea of a fullscreen window, and the
        resize this causes comes back through SDL_WINDOWEVENT_RESIZED like any
        other, so the GL viewport and projectM follow on their own.
        """
        if sys.platform != "win32" or not self._native_handle:
            return
        idx = sdl2.SDL_GetWindowDisplayIndex(self._window)
        if idx < 0:
            return
        r = sdl2.SDL_Rect()
        if sdl2.SDL_GetDisplayBounds(idx, ctypes.byref(r)) != 0:
            return
        o = self._FS_OVERHANG
        try:
            _user32().SetWindowPos(
                ctypes.c_void_p(self._native_handle), _HWND_TOP,
                r.x - o, r.y - o, r.w + 2 * o, r.h + 2 * o,
                _SWP_NOZORDER | _SWP_NOACTIVATE)
        except Exception as e:
            print(f"[renderer] could not overhang the display: {e}", flush=True)

    def _clear_window(self, w: int, h: int):
        """Black, on the main window. Render thread.

        For the one state that has nothing to draw: visual 1 switched off
        while visual 2 is away on a monitor of its own. Guarded the way
        `_stop_dual` is, and for two reasons: PyOpenGL raises
        `NullFunctionError` rather than no-opping when it cannot resolve an
        entry point, which is the Windows-only half; and this is the only
        raw GL call `_render_visuals` makes -- everything else goes through
        the blender, which the suite replaces with a stand-in -- so without
        it no offscreen test could reach this branch at all.
        """
        if not bool(glBindFramebuffer):
            return
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        glViewport(0, 0, w, h)
        glClearColor(0.0, 0.0, 0.0, 1.0)
        glClear(GL_COLOR_BUFFER_BIT)

    def _draw_second_window(self):
        """Visual 2 alone, in its own window. Render thread.

        Letterboxed rather than stretched. The two targets are built at the
        MAIN window's size, so a second monitor of another shape would pull
        the picture out of shape -- and a stretched visual reads as a broken
        one. Two monitors of the same shape, which is the ordinary case, get
        the whole screen and no bars.
        """
        if self._window2 is None or self._blend is None:
            return
        if sdl2.SDL_GL_MakeCurrent(self._window2, self._context) != 0:
            # The window went away underneath us -- a monitor unplugged, or
            # the session taking it. Say so once and fall back to one window
            # rather than repeating this sixty times a second.
            print(f"[dual] second window lost: {sdl2.SDL_GetError()}",
                  flush=True)
            self._drop_window2()
            return
        try:
            w, h = ctypes.c_int(), ctypes.c_int()
            sdl2.SDL_GL_GetDrawableSize(self._window2,
                                        ctypes.byref(w), ctypes.byref(h))
            ww, wh = int(w.value), int(h.value)
            if ww <= 0 or wh <= 0:
                return
            tw, th = self._blend.size
            if tw <= 0 or th <= 0:
                return
            scale = min(ww / tw, wh / th)
            dw, dh = max(1, int(tw * scale)), max(1, int(th * scale))
            if (dw, dh) != (ww, wh):
                # Only when there are bars to paint. glClear on every frame
                # of a full-bleed window is a wasted pass over the screen.
                glBindFramebuffer(GL_FRAMEBUFFER, 0)
                glViewport(0, 0, ww, wh)
                glClearColor(0.0, 0.0, 0.0, 1.0)
                glClear(GL_COLOR_BUFFER_BIT)
            # 1.0 is visual 2 alone: the same shader, with visual 1's term
            # multiplied out. There is no second program for this.
            self._blend.draw(dw, dh, 1.0,
                             ((ww - dw) // 2, (wh - dh) // 2))
            sdl2.SDL_GL_SwapWindow(self._window2)
        finally:
            sdl2.SDL_GL_MakeCurrent(self._window, self._context)

    def _do_set_window2(self, display: int):
        """Open, move or close visual 2's own window. Render thread."""
        if display >= 0 and display == int(
                self._cfg.get("fullscreen_display", -1)):
            # Both visuals pointed at one screen is the blended pair, asked
            # for the other way round.
            display = -1
        if display == self._window2_display:
            return
        self._drop_window2()
        if display < 0:
            self._cfg["visual2_display"] = -1
            return
        rect = None
        for i, _name, mx, my, mw, mh in self._displays:
            if i == display:
                rect = (mx, my, mw, mh)
                break
        if rect is None:
            print(f"[dual] there is no monitor {display + 1}", flush=True)
            self._cfg["visual2_display"] = -1
            return
        mx, my, mw, mh = rect
        # Borderless at the monitor's own rectangle rather than
        # FULLSCREEN_DESKTOP: a second fullscreen window takes the focus from
        # the first, and the first is the one with the keys on it. The pixels
        # are the same either way.
        win = sdl2.SDL_CreateWindow(
            f"{APP_NAME} \u2014 visual 2".encode(), mx, my, mw, mh,
            sdl2.SDL_WINDOW_OPENGL | sdl2.SDL_WINDOW_SHOWN |
            sdl2.SDL_WINDOW_BORDERLESS)
        if not win:
            print(f"[dual] second window failed: {sdl2.SDL_GetError()}",
                  flush=True)
            self._bridge.show_toast.emit("Could not open a second window.")
            self._cfg["visual2_display"] = -1
            return
        self._window2 = win
        self._window2_display = display
        self._cfg["visual2_display"] = display
        # Ask for the position again, because a reparenting window manager
        # places a borderless window by its own policy and simply ignores the
        # one SDL_CreateWindow was given. Measured under xfwm4 on 15 Sep 2026:
        # a window created at (1100, 0) was mapped at (0, 0), on top of the
        # main window -- which reads as the second visual having replaced the
        # first rather than joined it. SDL drops a request matching its own
        # cache, and its cache already says (1100, 0), so the cache has to be
        # moved off the target before the real ask goes out.
        def place(x, y):
            sdl2.SDL_SetWindowPosition(win, x + 1, y)
            sdl2.SDL_SetWindowPosition(win, x, y)

        place(mx, my)
        # And then put it there for real, through X. That ask still lands
        # short -- measured under xfwm4, 29px, because it reparents even a
        # borderless window and positions the FRAME while the client sits
        # inside it, leaving a black strip down the monitor's left edge.
        # SDL is the right way to ask for a change and the wrong way to undo
        # somebody else's, which is the same conclusion the wallpaper hold
        # reached: move the toplevel itself.
        self._place_second_window(win, mx, my, mw, mh)
        # And hand the keyboard back. Every key in the application belongs to
        # the main window, so a second window that keeps the focus it took on
        # the way up leaves the app looking dead to every key press.
        sdl2.SDL_RaiseWindow(self._window)
        # The context followed the new window on some drivers merely by it
        # being created. Put it back on the main one before the frame loop
        # draws anything.
        sdl2.SDL_GL_MakeCurrent(self._window, self._context)
        self._bridge.show_toast.emit(f"Visual 2 on monitor {display + 1}")

    def _place_second_window(self, win, mx, my, mw, mh):
        """Move the second window's toplevel onto the monitor. X11 only.

        Windows does not reparent a window into a frame of someone else's, so
        SDL's own placement is final there and there is nothing to do.

        Whatever the window manager wrapped around the window is what sits in
        the root's stack, so that is what has to be moved -- moving the client
        inside its own frame is what left it 29px short.
        """
        if not sys.platform.startswith("linux"):
            return
        try:
            info = sdl2.SDL_SysWMinfo()
            sdl2.SDL_VERSION(info.version)
            if not sdl2.SDL_GetWindowWMInfo(win, _ctypes.byref(info)):
                return
            lib, dpy = _libx11(), info.info.x11.display
            handle = info.info.x11.window
            if not lib or not dpy or not handle:
                return
            lib.XMoveResizeWindow(dpy, x11_toplevel_of(lib, dpy, handle),
                                  int(mx), int(my), int(mw), int(mh))
            lib.XSync(dpy, False)
            where = x11_window_origin(lib, dpy,
                                      x11_toplevel_of(lib, dpy, handle))
            if where is not None and where != (int(mx), int(my)):
                # Not fatal: the visual is up and on the right screen, just
                # not flush to its corner. Worth a line, because the symptom
                # is a black strip nobody would connect to a window manager.
                print(f"[dual] visual 2 sits at {where}, not "
                      f"({mx}, {my}) -- the window manager moved it back",
                      flush=True)
        except Exception as why:
            print(f"[dual] could not place the second window: {why}",
                  flush=True)

    def _drop_window2(self):
        """Close visual 2's window if it has one. Render thread."""
        if self._window2 is not None:
            sdl2.SDL_DestroyWindow(self._window2)
            self._window2 = None
            sdl2.SDL_GL_MakeCurrent(self._window, self._context)
        self._window2_display = -1

    def _do_fullscreen_on(self, display: int):
        """Fullscreen on a named monitor. Render thread.

        Not the fullscreen toggle: naming a monitor is an instruction to go
        there, and a toggle would send every second pick back to a window.
        """
        display = int(display)
        self._cfg["fullscreen_display"] = display
        if display < 0:
            return
        if self._window2_display == display:
            # Visual 2 was sent here first. One of them has to move, and the
            # one just asked for wins.
            self._drop_window2()
            self._cfg["visual2_display"] = -1
        if not self._fullscreen:
            self._toggle_fullscreen()       # reads fullscreen_display itself
            return
        # Already fullscreen somewhere else. Leaving and re-entering is the
        # only way SDL moves a fullscreen-desktop window between monitors.
        sdl2.SDL_SetWindowFullscreen(self._window, 0)
        self._move_to_chosen_display()
        sdl2.SDL_SetWindowFullscreen(self._window,
                                     sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP)
        self._overhang_display()

    def _toggle_fullscreen(self):
        flags = sdl2.SDL_GetWindowFlags(self._window)
        if flags & sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP:
            sdl2.SDL_SetWindowFullscreen(self._window, 0)
            self._fullscreen = False
        else:
            self._move_to_chosen_display()
            sdl2.SDL_SetWindowFullscreen(
                self._window, sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP
            )
            self._fullscreen = True
            self._overhang_display()

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

    def _toggle_on_top(self):
        self._on_top = not self._on_top
        sdl2.SDL_SetWindowAlwaysOnTop(
            self._window,
            sdl2.SDL_TRUE if self._on_top else sdl2.SDL_FALSE
        )
        label = "ON" if self._on_top else "OFF"
        print(f"[renderer] Always on top: {label}")
        self._bridge.status_message.emit(f"Always on top: {label}")

    def is_on_top(self) -> bool:
        return self._on_top

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

    def request_exclude(self):
        """Rate the current preset 0★ and move on. Safe from the Qt thread.

        Rating 0 is already what keeps a preset out of the random and rated
        rotations; this is the one keystroke that gets there, because holding
        the rate-down key five times to bury something you never want to see
        again is what makes people delete files instead.
        """
        try:
            name = self._pm.current_preset_name()
            if not name:
                return
            set_preset_rating(self._ratings, name, 0)
            self._save_ratings(self._ratings)
            self._bridge.preset_changed.emit(name, 0)
            self._bridge.show_toast.emit(
                f"Excluded  {name}\nRate it up again to bring it back")
            print(f"[renderer] Excluded '{name}'", flush=True)
        except Exception as e:
            print(f"[renderer] exclude failed: {e}", flush=True)
            return
        with self._lock:
            self._req_next = True

    # ── Emit helpers ──────────────────────────────────────────────────────

    def _emit_current_preset(self):
        self._thumb_done = False        # a new preset is worth a new picture
        name     = self._pm.current_preset_name()
        rating   = get_preset_rating(self._ratings, name)
        excluded = is_excluded(self._ratings, name)
        tag      = " [excluded]" if excluded else ""
        print(f"[renderer] Now playing: {name}  ★{rating}/10{tag}")
        self._bridge.preset_changed.emit(name, rating)
