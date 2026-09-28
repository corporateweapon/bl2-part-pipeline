"""``fire_sound``: the emitted wav and the FireAmmunition hooks, offline.

Built on the Boxgun spec (CC0, committed) with a synthetic wav, so it needs neither the
game nor any CS2 file. What it pins: the wav lands beside the module at the spec's volume,
our shot plays it (through the embedded wave_mixer, its own waveOut stream) and blanks the
weapon type's stock FireSounds only until POST, and a stock weapon of the same type is left
alone.

    python -m pytest tests/test_fire_sound.py -q
"""

from __future__ import annotations

import array
import json
import math
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bl2_partgen import emit  # noqa: E402
from bl2_partgen.spec import Spec, SpecError  # noqa: E402

BOXGUN = REPO / "specs" / "boxgun.json"


def _wav(path: Path, peak: int = 32000) -> Path:
    samples = array.array("h", (int(peak * math.sin(i / 5)) for i in range(4410)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(samples.tobytes())
    return path


def _peak(path: Path) -> int:
    with wave.open(str(path), "rb") as r:
        a = array.array("h")
        a.frombytes(r.readframes(r.getnframes()))
    return max(abs(x) for x in a)


def _spec(tmp_path: Path, **fire: object) -> dict:
    data = json.loads(BOXGUN.read_text(encoding="utf-8"))
    data["package_file"] = None
    data["options"]["own_gestalt"] = False  # the balance-less case must reach the fire_sound check
    data["fire_sound"] = {"wav": str(_wav(tmp_path / "shot.wav")), **fire}
    return data


def test_fire_sound_needs_a_balance(tmp_path: Path):
    data = _spec(tmp_path)
    data.pop("balances")
    with pytest.raises(SpecError, match="fire_sound needs a balance"):
        Spec.from_dict(data)


def test_emit_writes_the_wav_at_its_volume(tmp_path: Path):
    out = emit(_spec(tmp_path, volume=50), tmp_path / "mod", force=True)
    wav = tmp_path / "mod" / "sounds" / f"{out.name}_fire.wav"
    assert wav in out.files
    assert abs(_peak(wav) - 16000) <= 1
    assert "on_fire_pre.enable()" in (tmp_path / "mod" / "__init__.py").read_text(encoding="utf-8")


def test_hooks_play_ours_and_mute_only_for_our_shot(tmp_path: Path):
    from bl2_catalog import load_catalog
    from bl2_preflight.augment import augment_world
    from bl2_preflight.dryrun import FAKES, _load

    if str(FAKES) not in sys.path:
        sys.path.insert(0, str(FAKES))
    from tests.fakes.graph import build_graph

    out = emit(_spec(tmp_path), tmp_path / "mod", force=True)
    catalog = load_catalog()
    graph = build_graph(catalog)
    augment_world(graph.world, catalog, json.loads((tmp_path / "mod" / "spec.json").read_text()))
    module = _load(tmp_path / "mod", graph, package=False)

    played: list[str] = []
    # the embedded mixer is a real module object inside the mod: swap its play() for a recorder
    module.wave_mixer.play = lambda path, volume=1.0: played.append(path)
    stock_event = object()
    weapon_type = SimpleNamespace(FireSounds=[SimpleNamespace(Event=stock_event)])

    def weapon(balance_path: str) -> SimpleNamespace:
        balance = SimpleNamespace(_path_name=lambda: balance_path)
        return SimpleNamespace(
            Owner=graph.controller.Pawn, _path_name=lambda: "Weapon_0",
            DefinitionData=SimpleNamespace(BalanceDefinition=balance,
                                           WeaponTypeDefinition=weapon_type))

    ours = weapon(module.BALANCES[0]["path"])
    module.on_fire_pre(ours, None, None, None)
    assert played == [module.FIRE_SOUND_FILE] and Path(played[0]).name == f"{out.name}_fire.wav"
    assert weapon_type.FireSounds[0].Event is None          # stock shot silenced for ours
    module.on_fire_post(ours, None, None, None)
    assert weapon_type.FireSounds[0].Event is stock_event   # and back straight after

    stock = weapon("GD_Weap_Pistol.A_Weapons_Legendary.Pistol_Jakobs_5_Maggie")
    module.on_fire_pre(stock, None, None, None)
    assert len(played) == 1 and weapon_type.FireSounds[0].Event is stock_event
