"""``alt_fire``: RMB as a second trigger (Shiv's shotgun), offline.

Built on the Boxgun spec (CC0, committed) with synthetic wavs, so it needs neither the game
nor any Deadlock file. What it pins: the spec validates, the alt wav is baked beside the fire
wav, a build without ``alt_fire`` renders none of it, RMB on our weapon is blocked and pulls
the trigger instead, the alt shot swaps ShotCost / damage / bloom only for that shot and
spends the full ammo cost, and both shots push the player back.

    python -m pytest tests/test_alt_fire.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

from bl2_partgen import emit  # noqa: E402
from bl2_partgen.spec import Spec, SpecError  # noqa: E402
from test_fire_sound import _peak, _wav  # noqa: E402

BOXGUN = REPO / "specs" / "boxgun.json"


def _spec(tmp_path: Path, **alt: object) -> dict:
    data = json.loads(BOXGUN.read_text(encoding="utf-8"))
    data["package_file"] = None
    data["fire_sound"] = {"wav": str(_wav(tmp_path / "shot.wav")), "volume": 50}
    data["alt_fire"] = {"sound": str(_wav(tmp_path / "alt.wav")), "volume": 25, **alt}
    return data


def test_alt_fire_validates(tmp_path: Path):
    spec = Spec.from_dict(_spec(tmp_path, ammo_cost=3, damage_scale=3))
    assert spec.alt_fire is not None and spec.alt_fire.ammo_cost == 3
    assert "sound" not in spec.alt_fire.runtime()
    with pytest.raises(SpecError, match="unknown key"):
        Spec.from_dict(_spec(tmp_path, recoil=1))
    with pytest.raises(SpecError, match="ammo_cost"):
        Spec.from_dict(_spec(tmp_path, ammo_cost=0))
    data = _spec(tmp_path)
    data.pop("fire_sound")
    with pytest.raises(SpecError, match="rides on fire_sound"):
        Spec.from_dict(data)


def test_emit_bakes_the_alt_wav_and_renders_the_hooks(tmp_path: Path):
    out = emit(_spec(tmp_path), tmp_path / "mod", force=True)
    alt = tmp_path / "mod" / "sounds" / f"{out.name}_alt_fire.wav"
    assert alt.is_file() and abs(_peak(alt) - 8000) <= 1
    text = (tmp_path / "mod" / "__init__.py").read_text(encoding="utf-8")
    assert "on_alt_start.enable()" in text and "on_alt_shot_post.enable()" in text
    assert "sound_file = _fire_sound_file(obj)" in text


def test_a_build_without_alt_fire_renders_none_of_it(tmp_path: Path):
    data = _spec(tmp_path)
    data.pop("alt_fire")
    emit(data, tmp_path / "mod", force=True)
    text = (tmp_path / "mod" / "__init__.py").read_text(encoding="utf-8")
    assert "ALT_FIRE" not in text and "_fire_sound_file" not in text
    assert "wave_mixer.play(FIRE_SOUND_FILE)" in text


def _module(tmp_path: Path):
    from bl2_catalog import load_catalog
    from bl2_preflight.augment import augment_world
    from bl2_preflight.dryrun import FAKES, _load

    if str(FAKES) not in sys.path:
        sys.path.insert(0, str(FAKES))
    from tests.fakes.graph import build_graph

    emit(_spec(tmp_path), tmp_path / "mod", force=True)
    catalog = load_catalog()
    graph = build_graph(catalog)
    augment_world(graph.world, catalog, json.loads((tmp_path / "mod" / "spec.json").read_text()))
    module = _load(tmp_path / "mod", graph, package=False)
    return module, graph


def test_rmb_pulls_the_trigger_and_the_alt_shot_costs_three(tmp_path: Path):
    module, graph = _module(tmp_path)
    module.wave_mixer.play = lambda p, volume=1.0: None
    controller = graph.controller
    pawn = controller.Pawn
    calls: list[str] = []
    pushes: list[tuple[float, float, float]] = []
    controller.StartFire = lambda mode=0: calls.append(f"StartFire({mode})")
    controller.StopFire = lambda mode=0: calls.append(f"StopFire({mode})")
    controller.Rotation = SimpleNamespace(Pitch=0, Yaw=0, Roll=0)
    pawn.AddVelocity = lambda v, loc, dmg: pushes.append((v.X, v.Y, v.Z))
    pawn.Location = SimpleNamespace(X=0.0, Y=0.0, Z=0.0)
    pawn.Velocity = SimpleNamespace(X=0.0, Y=0.0, Z=0.0)
    balance = SimpleNamespace(_path_name=lambda: module.BALANCES[0]["path"])
    weapon = SimpleNamespace(
        Owner=pawn, _path_name=lambda: "Weapon_0", ReloadCnt=6, ShotCost=1,
        InstantHitDamage=100.0, PerShotAccuracyImpulse=2.0,
        DefinitionData=SimpleNamespace(BalanceDefinition=balance,
                                       WeaponTypeDefinition=SimpleNamespace(FireSounds=[])))
    pawn.Weapon = weapon

    # RMB: blocked (no zoom), the trigger is pulled instead
    assert module.on_alt_start(controller, None, None, None) is module.Block
    assert calls == ["StartFire(0)"]
    assert module._fire_sound_file(weapon) == module.FIRE_SOUND_ALT_FILE

    # the engine's shot: alt values only inside FireAmmunition, and it only spends one round
    module.on_alt_shot_pre(weapon, None, None, None)
    assert (weapon.ShotCost, weapon.InstantHitDamage, weapon.PerShotAccuracyImpulse) == (3, 300.0, 6.0)
    weapon.ReloadCnt -= 1
    module.on_alt_shot_post(weapon, None, None, None)
    assert (weapon.ShotCost, weapon.InstantHitDamage, weapon.PerShotAccuracyImpulse) == (1, 100.0, 2.0)
    assert weapon.ReloadCnt == 3                      # 6 - 3: the fix-up took the other two
    assert pushes and pushes[-1][0] == pytest.approx(-module.ALT_FIRE["knockback_alt"])
    assert module._kick["left"] > 0                   # the view kick is under way

    assert module.on_alt_stop(controller, None, None, None) is module.Block
    assert calls[-1] == "StopFire(0)"

    # a hip shot: stock values, the small push
    module.on_alt_shot_pre(weapon, None, None, None)
    assert weapon.ShotCost == 1 and weapon.InstantHitDamage == 100.0
    module.on_alt_shot_post(weapon, None, None, None)
    assert pushes[-1][0] == pytest.approx(-module.ALT_FIRE["knockback_hip"])
    assert module._fire_sound_file(weapon) == module.FIRE_SOUND_FILE

    # fewer rounds than the cost: RMB is a dry click
    weapon.ReloadCnt = 2
    module._alt["last_press"] = 0.0
    assert module.on_alt_start(controller, None, None, None) is module.Block
    assert calls.count("StartFire(0)") == 1 and module._alt_counts["dry"] == 1

    # somebody else's weapon: RMB is left alone (it zooms as usual)
    weapon.DefinitionData.BalanceDefinition = SimpleNamespace(_path_name=lambda: "Other.Balance")
    module._alt["last_press"] = 0.0
    assert module.on_alt_start(controller, None, None, None) is None
