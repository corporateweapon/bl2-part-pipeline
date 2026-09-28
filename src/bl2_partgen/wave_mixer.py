"""Concurrent one-shot wav playback through winmm ``waveOut``, ctypes only (no third party).

Why this exists: BL2's audio is Wwise banks with no loose-file path, so custom clips go
round the engine through Windows. The first cut used ``winsound.PlaySound(..., SND_ASYNC)``,
and Win32 ``PlaySound`` owns exactly ONE playback slot per process: any call, from any module,
stops whatever is playing. A shot cut off a dash, a dash cut off a slide.

``waveOutOpen`` hands out an independent stream per call and the Windows audio engine mixes
any number of them, so here every play() is its own stream and nothing cuts anything off.
Measured on the dev machine: four clips of mixed formats (44.1 kHz stereo + 48 kHz mono)
overlapped, each played its full length; a legacy PlaySound call mid-stream did not touch them.

Design:
* ``Clip`` decodes a 16-bit (or 8-bit) PCM wav once into a ctypes buffer. ``volume`` (0..1) is
  applied to the samples at load, NOT via ``waveOutSetVolume``: on a Vista+ audio session the
  latter's scope is not something to gamble the game's own mix on. Clips are cached per
  (path, volume, mtime), so a clip is decoded once per game session.
* ``play(clip_or_path, volume)`` queues the start on a single worker thread (the open+write
  costs ~8 ms after warm-up, ~30 ms the first time; neither belongs on the game thread) and
  returns a ``Voice`` immediately. The worker also reaps finished voices (unprepare + close).
* ``Voice.stop()`` resets that one stream; ``stop_all()`` resets every live one.
* Import is safe anywhere: without winmm (not Windows, or a broken install) ``available()`` is
  False and ``play`` raises ``OSError``. Callers that must never fail (a shot hook) wrap it.

Stdlib only, so it runs on the SDK's embedded Python (3.14, Win32) unchanged. The pipeline
embeds this file's source into every generated mod (``templates.py``), and the hand-written
``slide_sound`` / ``stamina`` mods carry a copy beside their ``__init__.py``.
"""

from __future__ import annotations

import array
import ctypes
import os
import queue
import threading
import time
import wave
from typing import Any

try:
    from ctypes import wintypes

    _winmm: Any = ctypes.WinDLL("winmm")  # type: ignore[attr-defined]
    _kernel32: Any = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
except (AttributeError, OSError):  # not Windows, or no winmm
    wintypes = None  # type: ignore[assignment]
    _winmm = None
    _kernel32 = None

WAVE_MAPPER = 0xFFFFFFFF
CALLBACK_NULL = 0x00000000
WHDR_DONE = 0x00000001
WHDR_PREPARED = 0x00000002
WHDR_BEGINLOOP = 0x00000004
WHDR_ENDLOOP = 0x00000008
WAVE_FORMAT_PCM = 1
MMSYSERR_NOERROR = 0
TIME_BYTES = 0x0004

#: how often the worker sweeps finished voices when idle (seconds)
_REAP_INTERVAL = 0.05


def available() -> bool:
    """True when winmm loaded, i.e. play() can work at all."""
    return _winmm is not None


if _winmm is not None:

    class WAVEFORMATEX(ctypes.Structure):
        _pack_ = 1  # <pshpack1.h> in mmreg.h
        _fields_ = [
            ("wFormatTag", wintypes.WORD),
            ("nChannels", wintypes.WORD),
            ("nSamplesPerSec", wintypes.DWORD),
            ("nAvgBytesPerSec", wintypes.DWORD),
            ("nBlockAlign", wintypes.WORD),
            ("wBitsPerSample", wintypes.WORD),
            ("cbSize", wintypes.WORD),
        ]

    class WAVEHDR(ctypes.Structure):
        pass

    WAVEHDR._fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_size_t),      # DWORD_PTR
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.POINTER(WAVEHDR)),
        ("reserved", ctypes.c_size_t),    # DWORD_PTR
    ]

    class MMTIME(ctypes.Structure):
        # wType + a union whose largest member (smpte) is 8 bytes
        _fields_ = [("wType", wintypes.UINT), ("u", wintypes.DWORD), ("_pad", wintypes.DWORD)]

    HWAVEOUT = wintypes.HANDLE
    _HDR_SIZE = ctypes.sizeof(WAVEHDR)

    _winmm.waveOutOpen.argtypes = [ctypes.POINTER(HWAVEOUT), wintypes.UINT,
                                   ctypes.POINTER(WAVEFORMATEX), ctypes.c_size_t,
                                   ctypes.c_size_t, wintypes.DWORD]
    _winmm.waveOutPrepareHeader.argtypes = [HWAVEOUT, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    _winmm.waveOutUnprepareHeader.argtypes = [HWAVEOUT, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    _winmm.waveOutWrite.argtypes = [HWAVEOUT, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    _winmm.waveOutReset.argtypes = [HWAVEOUT]
    _winmm.waveOutClose.argtypes = [HWAVEOUT]
    _winmm.waveOutGetPosition.argtypes = [HWAVEOUT, ctypes.POINTER(MMTIME), wintypes.UINT]
    for _fn in (_winmm.waveOutOpen, _winmm.waveOutPrepareHeader, _winmm.waveOutUnprepareHeader,
                _winmm.waveOutWrite, _winmm.waveOutReset, _winmm.waveOutClose,
                _winmm.waveOutGetPosition):
        _fn.restype = wintypes.UINT


class Clip:
    """A wav decoded once (volume applied) into a buffer any number of voices can share."""

    def __init__(self, path: str | os.PathLike[str], volume: float = 1.0) -> None:
        self.path = os.fspath(path)
        self.volume = max(0.0, min(1.0, float(volume)))
        with wave.open(self.path, "rb") as reader:
            params = reader.getparams()
            frames = reader.readframes(params.nframes)
        if params.sampwidth not in (1, 2):
            raise ValueError(f"{os.path.basename(self.path)}: {params.sampwidth * 8}-bit; "
                             "waveOut PCM takes 8- or 16-bit")
        if self.volume < 1.0 and params.sampwidth == 2:
            samples = array.array("h")
            samples.frombytes(frames)
            scale = self.volume
            for i, s in enumerate(samples):
                v = int(s * scale)
                samples[i] = 32767 if v > 32767 else (-32768 if v < -32768 else v)
            frames = samples.tobytes()
        self.nchannels = params.nchannels
        self.sampwidth = params.sampwidth
        self.framerate = params.framerate
        self.nbytes = len(frames)
        self.seconds = params.nframes / float(params.framerate) if params.framerate else 0.0
        # keep the raw bytes too: ctypes buffers are not picklable / comparable, bytes are
        self._frames = frames
        self._buffer = ctypes.create_string_buffer(frames, len(frames)) if frames else None
        if _winmm is not None:
            self._fmt = WAVEFORMATEX(
                WAVE_FORMAT_PCM, params.nchannels, params.framerate,
                params.framerate * params.nchannels * params.sampwidth,
                params.nchannels * params.sampwidth, params.sampwidth * 8, 0,
            )

    def __repr__(self) -> str:
        return (f"Clip({os.path.basename(self.path)!r}, {self.nchannels}ch "
                f"{self.sampwidth * 8}-bit {self.framerate} Hz, {self.seconds:.2f}s, "
                f"volume={self.volume:g})")


_clips: dict[tuple[str, int, float], Clip] = {}
_clips_lock = threading.Lock()


def load(path: str | os.PathLike[str], volume: float = 1.0) -> Clip:
    """The cached Clip for (path, volume); re-decoded when the file changes on disk."""
    path = os.path.abspath(os.fspath(path))
    level = int(round(max(0.0, min(1.0, float(volume))) * 100))
    mtime = os.stat(path).st_mtime
    key = (path, level, mtime)
    with _clips_lock:
        clip = _clips.get(key)
        if clip is None:
            # drop stale versions of the same file at this level
            for old in [k for k in _clips if k[0] == path and k[1] == level]:
                del _clips[old]
            clip = Clip(path, level / 100.0)
            _clips[key] = clip
    return clip


def preload(path: str | os.PathLike[str], volume: float = 1.0) -> Clip | None:
    """load() that swallows a missing / bad file (returns None) and warms the device."""
    try:
        clip = load(path, volume)
    except (OSError, ValueError, wave.Error, EOFError):
        return None
    _worker().warm()
    return clip


class Voice:
    """One playing instance: its own waveOut handle and header.

    Created by the worker; ``stop()`` may be called from any thread. ``done`` is True once the
    stream finished (or was stopped); ``error`` holds the MMRESULT text if the open failed.
    """

    def __init__(self, clip: Clip, loop: bool = False) -> None:
        self.clip = clip
        self.loop = loop
        self.started: float | None = None
        self.finished: float | None = None
        self.error: str | None = None
        self._handle = HWAVEOUT() if _winmm is not None else None
        self._hdr = WAVEHDR() if _winmm is not None else None
        self._opened = False
        self._closed = False
        self._lock = threading.Lock()

    # -- worker side -------------------------------------------------------------------
    def _start(self) -> None:
        if _winmm is None or self.clip._buffer is None:
            self.error = "waveOut unavailable" if _winmm is None else "empty clip"
            self.finished = time.perf_counter()
            self._closed = True
            return
        rc = _winmm.waveOutOpen(ctypes.byref(self._handle), WAVE_MAPPER, ctypes.byref(self.clip._fmt),
                                0, 0, CALLBACK_NULL)
        if rc != MMSYSERR_NOERROR:
            self.error = f"waveOutOpen MMRESULT {rc}"
            self.finished = time.perf_counter()
            self._closed = True
            return
        self._opened = True
        self._hdr.lpData = ctypes.cast(self.clip._buffer, ctypes.c_void_p)
        self._hdr.dwBufferLength = self.clip.nbytes
        self._hdr.dwFlags = 0
        rc = _winmm.waveOutPrepareHeader(self._handle, ctypes.byref(self._hdr), _HDR_SIZE)
        if rc == MMSYSERR_NOERROR:
            if self.loop:
                # after prepare (dwFlags must be 0 for it), before write: repeat until reset
                self._hdr.dwFlags |= WHDR_BEGINLOOP | WHDR_ENDLOOP
                self._hdr.dwLoops = 0xFFFFFFFF
            rc = _winmm.waveOutWrite(self._handle, ctypes.byref(self._hdr), _HDR_SIZE)
        if rc != MMSYSERR_NOERROR:
            self.error = f"waveOutWrite MMRESULT {rc}"
            self._release()
            return
        self.started = time.perf_counter()

    def _release(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self.finished is None:
                self.finished = time.perf_counter()
            if self._opened:
                if self._hdr.dwFlags & WHDR_PREPARED:
                    _winmm.waveOutUnprepareHeader(self._handle, ctypes.byref(self._hdr), _HDR_SIZE)
                _winmm.waveOutClose(self._handle)
                self._opened = False

    # -- any thread ----------------------------------------------------------------------
    @property
    def done(self) -> bool:
        if self._closed:
            return True
        if self.started is None:
            return False  # still queued
        return bool(self._hdr.dwFlags & WHDR_DONE)

    def position_bytes(self) -> int:
        """Bytes played so far (0 when queued or closed)."""
        if _winmm is None or not self._opened or self._closed:
            return 0
        mmt = MMTIME(TIME_BYTES, 0, 0)
        _winmm.waveOutGetPosition(self._handle, ctypes.byref(mmt), ctypes.sizeof(MMTIME))
        return int(mmt.u)

    def stop(self) -> None:
        """Stop this stream now; the worker closes it on its next sweep."""
        with self._lock:
            if self._opened and not self._closed:
                _winmm.waveOutReset(self._handle)  # marks the header DONE
                self.finished = time.perf_counter()


class _Worker:
    """The one thread that opens, writes and closes streams."""

    def __init__(self) -> None:
        self.queue: queue.Queue[Voice | None] = queue.Queue()
        self.live: list[Voice] = []
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self._warmed = False

    def ensure(self) -> None:
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, daemon=True, name="wave_mixer")
                self.thread.start()

    def warm(self) -> None:
        """Pay the first waveOutOpen (~30 ms) now rather than on the first shot."""
        if _winmm is None or self._warmed:
            return
        self._warmed = True
        self.ensure()
        self.queue.put(None)

    def _warm_now(self) -> None:
        fmt = WAVEFORMATEX(WAVE_FORMAT_PCM, 2, 44100, 44100 * 4, 4, 16, 0)
        handle = HWAVEOUT()
        if _winmm.waveOutOpen(ctypes.byref(handle), WAVE_MAPPER, ctypes.byref(fmt), 0, 0,
                              CALLBACK_NULL) == MMSYSERR_NOERROR:
            _winmm.waveOutClose(handle)

    def submit(self, voice: Voice) -> None:
        self.ensure()
        with self.lock:
            self.live.append(voice)
        self.queue.put(voice)

    def _run(self) -> None:
        while True:
            try:
                item = self.queue.get(timeout=_REAP_INTERVAL)
            except queue.Empty:
                item = False
            if item is None:
                try:
                    self._warm_now()
                except Exception:  # noqa: BLE001 - warming is best effort
                    pass
            elif item is not False:
                try:
                    item._start()
                except Exception as ex:  # noqa: BLE001 - a bad clip must not kill the worker
                    item.error = repr(ex)
                    item._release()
            self._reap()

    def _reap(self) -> None:
        with self.lock:
            live = list(self.live)
        for voice in live:
            if voice.started is not None and voice.done and not voice._closed:
                voice._release()
            if voice._closed:
                with self.lock:
                    if voice in self.live:
                        self.live.remove(voice)


_worker_instance: _Worker | None = None
_worker_lock = threading.Lock()


def _worker() -> _Worker:
    global _worker_instance
    with _worker_lock:
        if _worker_instance is None:
            _worker_instance = _Worker()
        return _worker_instance


def play(clip: Clip | str | os.PathLike[str], volume: float = 1.0, loop: bool = False) -> Voice:
    """Start ``clip`` (a Clip, or a wav path loaded through the cache) on its own stream.

    Returns at once; the stream starts on the worker within a few ms. ``loop`` repeats the
    clip until ``Voice.stop()`` / ``stop_all()``. Raises ``OSError`` when winmm is missing and
    ``ValueError`` / ``wave.Error`` / ``OSError`` for a bad or missing file. A stream that
    fails to open reports it in ``Voice.error`` instead.
    """
    if _winmm is None:
        raise OSError("waveOut unavailable (winmm did not load)")
    if not isinstance(clip, Clip):
        clip = load(clip, volume)
    voice = Voice(clip, loop=loop)
    _worker().submit(voice)
    return voice


def stop_all() -> None:
    """Reset every live stream (a mod's on_disable, or 'cut off when the slide ends')."""
    if _worker_instance is None:
        return
    with _worker_instance.lock:
        live = list(_worker_instance.live)
    for voice in live:
        voice.stop()


def live_count() -> int:
    """Streams currently open (queued, playing, or finished but not yet swept)."""
    if _worker_instance is None:
        return 0
    with _worker_instance.lock:
        return len(_worker_instance.live)
