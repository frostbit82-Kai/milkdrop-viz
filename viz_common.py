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


try:
    import sounddevice as sd
    _SD_AVAILABLE = True
except ImportError:
    _SD_AVAILABLE = False
    print("[audio] sounddevice not installed — visualization will run silent")


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
        evt, thread = self._follow_stop, self._follow_thread
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
    "toggle_borderless": "b",
    "fullscreen_mouse": "DBLCLICK",
    "record":           "F9",
    "toggle_wallpaper": "d",
    "exclude":          "x",
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
    """Load keymaps.json, merging over DEFAULT_KEYMAPS. Path-agnostic."""
    from pathlib import Path
    km = dict(DEFAULT_KEYMAPS)
    p = Path(keymaps_path)
    if p.exists():
        try:
            with open(p, encoding="utf-8-sig") as f:
                km.update(json.load(f))
        except Exception as e:
            print(f"[config] Warning: could not load keymaps.json: {e}")
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
    (QtCore, ("Qt", "QObject", "QPoint", "QSize", "QTimer", "QEvent", "QThread",
              "QUrl")),
    (QtGui,  ("QKeySequence", "QFont", "QCursor", "QAction", "QActionGroup",
              "QIcon", "QImage", "QPixmap", "QColor", "QDesktopServices")),
    (QtWidgets, ("QApplication", "QMenu", "QDialog", "QFormLayout",
                 "QVBoxLayout", "QHBoxLayout", "QPushButton", "QLabel",
                 "QSpinBox", "QDoubleSpinBox", "QComboBox", "QDialogButtonBox",
                 "QCheckBox", "QFrame", "QTableWidget", "QTableWidgetItem",
                 "QHeaderView", "QLineEdit", "QSizePolicy", "QMessageBox",
                 "QFileDialog", "QSlider", "QWidget", "QWidgetAction",
                 "QListWidget", "QListWidgetItem", "QAbstractItemView",
                 "QProgressBar", "QSystemTrayIcon", "QInputDialog")),
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
        "link":          "#e0b44a",
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
        "scroll_handle": "rgba(255, 255, 255, 55)",
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
        "link":          "#8a5d00",
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
        "scroll_handle": "rgba(0, 0, 0, 70)",
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


def theme_colour(key: str, theme: str = None) -> str:
    """One palette entry, for the few labels that set their own colour."""
    return _THEME_PALETTES[resolve_theme(theme)].get(key, "")


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
        if sys.platform == "win32":
            layout.addRow("", self._autostart_wp)

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
            "theme":                self._theme.currentData() or "dark",
            "mesh_x":               mx,
            "mesh_y":               my,
            "soft_cut_duration":    round(self._soft_cut.value(), 1),
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
    "fullscreen_mouse": "Toggle fullscreen (mouse)",
    "record":           "Start / stop recording a clip",
    "toggle_wallpaper": "Desktop wallpaper mode on / off",
    "exclude":          "Exclude this visual (rate it 0★) and move on",
}

ACTION_NOTES = {
    "fullscreen_mouse": "Value must be DBLCLICK",
    "toggle_borderless": "Drops the title bar; the window stays its size",
    "lock":             "SCROLLLOCK uses the Scroll Lock key",
    "rate_down":        "Rating 0 excludes preset from random rotation",
    "record":           "Needs ffmpeg; clips land in the clips folder",
    "toggle_wallpaper": "Windows only; leave it again from the tray icon",
    "exclude":          "Hides it from rotation. Nothing is deleted",
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
                    return True
        return super().eventFilter(obj, event)

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

    def _build(self):
        locked = self._renderer.is_locked()

        self.addAction("▶   Next visual",      lambda: self._renderer.request_next())
        self.addAction("◀   Previous visual",  lambda: self._renderer.request_prev())
        self.addAction("⟳   Random visual",    lambda: self._renderer.request_random())
        self.addSeparator()

        self.addAction("🗂   Preset browser",   lambda: self._renderer.request_toggle_browser())
        self.addAction("↕   Sort by name",     lambda: self._renderer.request_sort("name"))
        self.addAction("★   Sort by rating",   lambda: self._renderer.request_sort("rating"))
        self.addAction("🚫   Exclude this visual (0★)",
                       lambda: self._renderer.request_exclude())
        self.addAction("🗑   Delete 0★ presets…", self._confirm_delete_zero)
        self.addAction("↺   Reset all ratings…", self._confirm_reset_ratings)
        self._build_collections_menu()
        self.addSeparator()

        lock_text = "\U0001f512  Unlock visual" if locked else "\U0001f513  Lock visual"
        self.addAction(lock_text, lambda: self._renderer.request_toggle_lock())
        self.addSeparator()

        borderless = self._renderer.is_borderless()
        border_text = "☐   Show window border" if borderless else "▣   Borderless window"
        self.addAction(border_text, lambda: self._renderer.request_toggle_borderless())

        full = self._renderer.is_fullscreen()
        self.addAction("🗗   Leave fullscreen" if full else "⛶   Fullscreen",
                       lambda: self._renderer.request_fullscreen())
        self._build_display_menu()
        self.addSeparator()

        self._build_record_menu()
        self._build_wallpaper_entry()
        self.addSeparator()

        self._build_audio_menu()
        self.addSeparator()

        self.addAction("⬇   Get presets…",    self._open_packs)
        self._build_backup_menu()
        self.addAction("⌨   Key bindings…",   self._open_keymaps)
        self.addAction("⚙   Settings…",        self._open_settings)
        self.addAction("ℹ   About…",           self._open_about)
        self.addSeparator()

        self.addAction("✕   Quit",             QApplication.quit)

    def _build_wallpaper_entry(self):
        """Windows only. Nothing on Linux draws behind the desktop icons in a
        way an application can ask for, so an entry there would be a promise
        the platform cannot keep."""
        if sys.platform != "win32":
            return
        on = self._renderer.is_wallpaper()
        if on:
            self.addAction("🖥   Leave wallpaper mode",
                           lambda: self._renderer.request_wallpaper())
            return
        host, _layered = find_desktop_host(wait=0.3)
        if not host:
            # No Explorer desktop to draw into at all -- a third-party shell,
            # or the desktop not up yet. Offering the entry anyway spends a
            # click to reach a toast, so say so where the click would have been.
            dead = QAction("🖥   Desktop wallpaper — not available on this "
                           "version of Windows", self)
            dead.setEnabled(False)
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
        sub.addAction("⬆   Back up ratings & collections…", self._do_backup)
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
            except Exception:
                pass

    def _close_wav(self) -> int:
        """Close the WAV and return how many sample frames it holds."""
        with self._wav_lock:
            w, self._wav = self._wav, None
        if w is None:
            return 0
        try:
            n = w.getnframes()
            w.close()
            return n
        except Exception:
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

    def submit(self, path: str, w: int, h: int, buf: bytes):
        try:
            self._q.put_nowait((path, w, h, buf))
        except Exception:
            pass          # a thumbnail is never worth waiting for

    def run(self):
        while True:
            path, w, h, buf = self._q.get()
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
            except Exception as e:
                print(f"[thumb] {path}: {e}", flush=True)


_THUMB_WRITER = None


def thumb_writer() -> "_ThumbWriter":
    global _THUMB_WRITER
    if _THUMB_WRITER is None:
        _THUMB_WRITER = _ThumbWriter()
        _THUMB_WRITER.start()
    return _THUMB_WRITER


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
from OpenGL.GL import (glViewport, glClear, GL_COLOR_BUFFER_BIT,
                       glReadPixels, glPixelStorei, GL_PACK_ALIGNMENT,
                       GL_RGB, GL_UNSIGNED_BYTE)


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
        self._req_fullscreen             = False  # toggle fullscreen
        self._req_record                 = None   # 'mp4' | 'gif' | 'stop'
        self._req_wallpaper              = None   # True/False: be the desktop
        self._req_collection             = None   # name to play, "" = all
        self._rated                      = False  # favorite (rating-weighted) shuffle
        self._rated_history              = deque(maxlen=200)  # native indices for rated prev

        self._locked              = False
        self._borderless          = False
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
        #: HWND on Windows, X11 window id elsewhere. Only the frame theme
        #: uses it, and only on Windows.
        self._native_handle = 0
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
        self._thumb_done = True    # one attempt per preset; see _maybe_thumbnail
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
                # Kept so the frame can be re-themed when the setting changes.
                self._native_handle = handle
                apply_window_theme(handle, self._cfg.get("theme"))
        except Exception as e:
            print(f"[renderer] Could not get XID: {e}", flush=True)

        return True

    # ── projectM init ─────────────────────────────────────────────────────

    def _init_glew(self) -> bool:
        """Resolve OpenGL entry points on Windows, before projectM makes any.

        libprojectM links GLEW on Windows but never calls glewInit() itself —
        upstream only does it in its SDL developer UI, which this build does not
        include. Until something calls it every GLEW function pointer is NULL,
        and the first GL call inside projectm_create() writes through a null
        pointer: "access violation writing 0x0000000000000000".

        Linux never hits this. projectM links libGL directly there, so the
        dynamic loader resolves the symbols and there is no GLEW indirection.

        This works only because projectM was built against the *shared*
        glew32.dll: it and this call share one global pointer table. A
        statically linked GLEW would hand projectM its own private copy and
        initialising ours would silently fix nothing.

        Requires a current GL context, so call it after SDL_GL_CreateContext
        and on the same thread.
        """
        if sys.platform != "win32":
            return True
        try:
            glew = _ctypes.CDLL("glew32.dll")
        except OSError as e:
            print(f"[renderer] glew32.dll not found. It must sit beside "
                  f"projectM-4.dll or on PATH: {e}")
            return False
        try:
            # GLboolean is an unsigned char; a wider write would clobber
            # whatever GLEW keeps next to it. Core profiles need this set or
            # glewInit() refuses on some drivers.
            _ctypes.c_ubyte.in_dll(glew, "glewExperimental").value = 1
        except ValueError:
            pass                      # not exported by every GLEW build
        glew.glewInit.restype = _ctypes.c_uint
        err = glew.glewInit()
        if err != 0:                  # GLEW_OK
            print(f"[renderer] glewInit() failed with code {err}")
            return False
        return True

    def _init_projectm(self) -> bool:
        try:
            self._pm = self._create_projectm()
        except RuntimeError as e:
            print(f"[renderer] Fatal: {e}")
            return False

        # After the libraries are loaded (so we bind the same glew32.dll
        # projectM does) and before any projectM call touches OpenGL.
        if not self._init_glew():
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

        self._apply_texture_paths()

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

        Texture-sampling presets render solid white without these. The
        configured directory leads and the rest are guesses kept for installs
        that never set one -- nothing is bundled. Called again when Settings
        changes a directory, so neither field needs a restart.
        """
        if not self._pm or not hasattr(self._pm, "set_texture_search_paths"):
            return
        preset_dir = self._cfg.get("preset_dir", "")
        tex_paths = [p for p in (
            self._cfg.get("texture_dir", "").strip(),
            os.path.join(os.path.dirname(__file__), "textures"),
            os.path.join(preset_dir, "textures"),
            os.path.expanduser("~/projectm-textures"),
            preset_dir,
        ) if p and os.path.isdir(p)]
        if tex_paths:
            self._pm.set_texture_search_paths(tex_paths)

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
                        self._pm.set_window_size(nw, nh)
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
                if self._req_settings_updates is not None:
                    updates = self._req_settings_updates
                    self._req_settings_updates = None
                    self._cfg.update(updates)
                    # The merged config, not the diff: _apply_pm_settings reads
                    # every projectM setting with a default, so handing it a
                    # partial dict would quietly reset whatever is missing.
                    self._apply_pm_settings(self._cfg)
                    if "fps" in updates:
                        _f = updates.get("fps", 60)
                        sdl2.SDL_GL_SetSwapInterval(
                            0 if (not _f or _f <= 0) else 1)
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
                if self._req_collection is not None:
                    name = self._req_collection
                    self._req_collection = None
                    self._do_play_collection(name)

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
            # Before the swap, always: glReadPixels reads the back buffer, and
            # after SDL_GL_SwapWindow that is last frame's content, not this
            # one's.
            if self._rec is not None:
                self._capture_for_recording()
            self._maybe_thumbnail()
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
        # Before the GL context goes: an unfinished recording leaves a
        # zero-byte file that no player will open.
        if self._rec is not None:
            self._stop_recording()
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
        if self._locked or not self._pm:
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
            if cur and cur != "Unknown":
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
        """Parent the visualiser window into Explorer's wallpaper window.

        Windows only. There is no equivalent anywhere else that is worth
        shipping: X11 desktops each place the wallpaper differently and
        Wayland gives an application no way to reach behind the desktop at all.

        A window parented into WorkerW receives no keyboard or mouse input --
        that is Explorer's, not ours -- so the tray icon the host puts up is
        the only way back out. wallpaper_changed is what tells it to.
        """
        if on == self._wallpaper:
            return
        if sys.platform != "win32":
            self._bridge.show_toast.emit(
                "Wallpaper mode is Windows only — Linux desktops give an "
                "application no way to draw behind the icons")
            return
        try:
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
        except Exception as e:
            print(f"[wallpaper] {e}", flush=True)
            self._bridge.show_toast.emit(f"Wallpaper mode failed: {e}")
            return
        self._bridge.wallpaper_changed.emit(self._wallpaper)
        self._bridge.status_message.emit(
            "Wallpaper mode ON — use the tray icon to leave it"
            if self._wallpaper else "Wallpaper mode OFF")

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

    # ── Thumbnails ────────────────────────────────────────────────────────

    #: How long a preset must stay on screen before it is worth a thumbnail.
    #: Long enough that the picture has settled and that a preset skipped past
    #: never gets one -- the cache should fill with what was actually watched.
    THUMB_AFTER = 5.0

    def _maybe_thumbnail(self):
        """Cache a picture of the current preset, once, after it has settled.

        Deliberately passive: there is no batch pass over the library. Ten
        thousand presets rendered for a second each is two hours of nobody's
        time, and the thumbnails that matter are the ones for presets the
        owner has actually sat through.
        """
        if self._thumb_done:
            return
        if _time.monotonic() - self._preset_start < self.THUMB_AFTER:
            return
        self._thumb_done = True
        try:
            name = self._pm.current_preset_name()
            if not name or os.path.isfile(thumb_path(name)):
                return
            grab = self._grab_frame()
            if grab:
                w, h, buf = grab
                thumb_writer().submit(thumb_path(name), w, h, buf)
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
