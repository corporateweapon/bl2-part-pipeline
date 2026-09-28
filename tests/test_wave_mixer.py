"""``wave_mixer``: concurrent wav playback through waveOut, and its embedding in a mod.

Offline parts run anywhere (decode, volume, cache, the embedded copy inside an emitted mod).
The playback parts need winmm and a wave-out device, so they skip where ``available()`` is
False or the device refuses to open (a CI box without audio).

    python -m pytest tests/test_wave_mixer.py -q
"""

from __future__ import annotations

import array
import json
import math
import sys
import time
import wave
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bl2_partgen import wave_mixer as wm  # noqa: E402
from bl2_partgen.emit import emit  # noqa: E402

BOXGUN = REPO / "specs" / "boxgun.json"


def _wav(path: Path, seconds: float = 0.25, rate: int = 44100, channels: int = 1,
         peak: int = 16000) -> Path:
    n = int(seconds * rate)
    samples = array.array("h", (int(peak * math.sin(i / 7.0)) for i in range(n * channels)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return path


def _peak(frames: bytes) -> int:
    a = array.array("h")
    a.frombytes(frames)
    return max(abs(x) for x in a)


def test_clip_decodes_and_scales_volume(tmp_path: Path):
    src = _wav(tmp_path / "a.wav", channels=2, rate=48000)
    full = wm.Clip(src)
    half = wm.Clip(src, 0.5)
    assert (full.nchannels, full.sampwidth, full.framerate) == (2, 2, 48000)
    assert abs(full.seconds - 0.25) < 1e-3 and full.nbytes == half.nbytes
    assert 15900 <= _peak(full._frames) <= 16000
    assert abs(_peak(half._frames) - _peak(full._frames) / 2) <= 1


def test_load_caches_per_path_and_volume(tmp_path: Path):
    src = _wav(tmp_path / "b.wav")
    a = wm.load(src, 0.7)
    assert wm.load(src, 0.7) is a                 # same file, same level: cached
    assert wm.load(src, 0.2) is not a             # another level is another buffer
    assert wm.load(src, 1.0).volume == 1.0
    assert wm.preload(tmp_path / "missing.wav") is None  # never raises


def test_rejects_24_bit(tmp_path: Path):
    path = tmp_path / "c.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(44100)
        w.writeframes(b"\0" * 3000)
    with pytest.raises(ValueError, match="24-bit"):
        wm.Clip(path)


def test_emitted_mod_embeds_the_mixer_as_a_module(tmp_path: Path):
    data = json.loads(BOXGUN.read_text(encoding="utf-8"))
    data["package_file"] = None
    data["options"]["own_gestalt"] = False
    data["fire_sound"] = {"wav": str(_wav(tmp_path / "shot.wav"))}
    emit(data, tmp_path / "mod", force=True)
    text = (tmp_path / "mod" / "__init__.py").read_text(encoding="utf-8")
    source = (REPO / "src" / "bl2_partgen" / "wave_mixer.py").read_text(encoding="utf-8")
    assert source in text                                  # verbatim, one file per mod
    assert 'wave_mixer = _types.ModuleType("wave_mixer")' in text
    assert "winsound" not in text.replace("winsound.PlaySound(..., SND_ASYNC)", "")
    assert text.count("from __future__ import annotations") == 2  # mod's own + inside the r''' string


@pytest.mark.skipif(not wm.available(), reason="winmm not available")
def test_two_clips_play_at_once_and_both_finish(tmp_path: Path):
    a = wm.load(_wav(tmp_path / "a.wav", seconds=0.6, rate=44100, channels=2))
    b = wm.load(_wav(tmp_path / "b.wav", seconds=0.4, rate=48000, channels=1))
    va = wm.play(a)
    time.sleep(0.15)
    vb = wm.play(b)
    deadline = time.perf_counter() + 3.0
    while (va.started is None or vb.started is None) and time.perf_counter() < deadline:
        time.sleep(0.01)
    if va.error or vb.error:
        pytest.skip(f"no usable wave-out device: {va.error or vb.error}")
    time.sleep(0.1)
    assert not va.done and not vb.done and wm.live_count() == 2   # overlapping
    assert va.position_bytes() > 0 and vb.position_bytes() > 0
    # `finished` is stamped when the worker sweeps the stream, one tick after `done`
    while not (va._closed and vb._closed) and time.perf_counter() < deadline:
        time.sleep(0.01)
    assert va.done and vb.done and va._closed and vb._closed
    assert va.finished - va.started >= a.seconds - 0.05   # neither was cut off
    assert vb.finished - vb.started >= b.seconds - 0.05
    time.sleep(0.15)
    assert wm.live_count() == 0                            # swept


@pytest.mark.skipif(not wm.available(), reason="winmm not available")
def test_stop_cuts_one_voice_and_stop_all_the_rest(tmp_path: Path):
    clip = wm.load(_wav(tmp_path / "long.wav", seconds=2.0))
    voices = [wm.play(clip) for _ in range(3)]
    deadline = time.perf_counter() + 3.0
    while any(v.started is None for v in voices) and time.perf_counter() < deadline:
        time.sleep(0.01)
    if any(v.error for v in voices):
        pytest.skip("no usable wave-out device")
    voices[0].stop()
    time.sleep(0.15)
    assert voices[0].done and not voices[1].done
    wm.stop_all()
    time.sleep(0.15)
    assert all(v.done for v in voices) and wm.live_count() == 0
    assert all(v.finished - v.started < 1.0 for v in voices)


@pytest.mark.skipif(not wm.available(), reason="winmm not available")
def test_loop_repeats_until_stopped(tmp_path: Path):
    clip = wm.load(_wav(tmp_path / "short.wav", seconds=0.2))
    v = wm.play(clip, loop=True)
    deadline = time.perf_counter() + 3.0
    while v.started is None and time.perf_counter() < deadline:
        time.sleep(0.01)
    if v.error:
        pytest.skip("no usable wave-out device")
    time.sleep(0.6)                      # three clip lengths later
    assert not v.done and v.position_bytes() > clip.nbytes   # still going, past one pass
    v.stop()
    time.sleep(0.15)
    assert v.done and wm.live_count() == 0
