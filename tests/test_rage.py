"""``rage``: Shiv's Rage legendary effect, offline.

Built on the Boxgun spec with synthetic wavs. What it pins: the spec validates; a build
without ``rage`` renders none of it; a shot that lands several pellets gains once, an alt
shot gains more, a kill gains most; full Rage scales damage by re-issuing TakeDamage (and
the re-issued call passes through) and scales ground speed without clobbering someone
else's write; decay starts only after the delay; the meter draws without errors.

    python -m pytest tests/test_rage.py -q
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
from test_alt_fire import _spec as _alt_spec  # noqa: E402


def _spec(tmp_path: Path, **rage: object) -> dict:
    data = _alt_spec(tmp_path, ammo_cost=2)
    data["rage"] = dict(rage)
    return data


def test_rage_validates(tmp_path: Path):
    spec = Spec.from_dict(_spec(tmp_path, gain_hit=5, hud={"y": 0.7}))
    assert spec.rage is not None and spec.rage.hud["y"] == 0.7 and spec.rage.max == 100.0
    with pytest.raises(SpecError, match="unknown key"):
        Spec.from_dict(_spec(tmp_path, rage_per_hit=5))
    with pytest.raises(SpecError, match="rage.hud"):
        Spec.from_dict(_spec(tmp_path, hud={"colour": "red"}))


def test_a_build_without_rage_renders_none_of_it(tmp_path: Path):
    data = _spec(tmp_path)
    data.pop("rage")
    emit(data, tmp_path / "mod", force=True)
    assert "RAGE" not in (tmp_path / "mod" / "__init__.py").read_text(encoding="utf-8")


class _Args(SimpleNamespace):
    """A TakeDamage args struct: attribute access plus the property iterator the recall uses."""

    def __init__(self, **fields):
        super().__init__(**fields)
        names = list(fields)
        self._type = SimpleNamespace(_properties=lambda: [SimpleNamespace(Name=n, PropertyFlags=0x80) for n in names])


def _world(tmp_path: Path):
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
    controller, pawn = graph.controller, graph.controller.Pawn
    balance = SimpleNamespace(_path_name=lambda: module.BALANCES[0]["path"])
    weapon = SimpleNamespace(Owner=pawn, _path_name=lambda: "Weapon_0",
                             DefinitionData=SimpleNamespace(BalanceDefinition=balance))
    pawn.Weapon = weapon
    pawn.GroundSpeed = 440.0
    return module, controller, pawn, weapon


def _hit(module, controller, weapon, target, damage=10, calls=None):
    args = _Args(Damage=damage, InstigatedBy=controller, HitLocation=None, Momentum=None,
                 DamageType=None, DamageCauser=weapon)
    func = lambda **kw: calls.append(kw) if calls is not None else None  # noqa: E731
    return module.on_rage_ai_damage(target, args, None, func)


def test_hits_kills_enraged_and_decay(tmp_path: Path):
    module, controller, pawn, weapon = _world(tmp_path)
    enemy = SimpleNamespace(_path_name=lambda: "Enemy_0")

    # one hip shot, three pellets land: +gain_hit once
    module.on_rage_shot(weapon, None, None, None)
    for _ in range(3):
        _hit(module, controller, weapon, enemy)
    assert module._rage["value"] == pytest.approx(5.0)

    # an alt shot: +gain_alt_hit
    module._alt["active"] = True
    module.on_rage_shot(weapon, None, None, None)
    module._alt["active"] = False
    _hit(module, controller, weapon, enemy)
    assert module._rage["value"] == pytest.approx(15.0)

    # damage from someone else, or to ourselves, gains nothing
    module.on_rage_shot(weapon, None, None, None)
    module.on_rage_ai_damage(enemy, _Args(Damage=5, InstigatedBy=object(), DamageCauser=weapon), None,
                             lambda **kw: None)
    module.on_rage_pawn_damage(pawn, _Args(Damage=5, InstigatedBy=controller, DamageCauser=weapon), None,
                               lambda **kw: None)
    assert module._rage["value"] == pytest.approx(15.0)

    # a kill while holding the weapon: +gain_kill, once even though two Died hooks fire
    died = SimpleNamespace(Killer=controller)
    module.on_rage_ai_died(enemy, died, None, None)
    module.on_rage_pawn_died(enemy, died, None, None)
    assert module._rage["value"] == pytest.approx(30.0)

    # full: Enraged -> speed x1.2, damage re-issued x1.25 and the re-issue passes through
    module._rage_add(100.0, "test")
    module.rage_tick(None, None, None, None)
    assert module._rage["enraged"] and pawn.GroundSpeed == pytest.approx(528.0)
    calls: list[dict] = []
    module.on_rage_shot(weapon, None, None, None)
    ret = _hit(module, controller, weapon, enemy, damage=100, calls=calls)
    assert ret is module.Block and calls and calls[0]["Damage"] == 125
    # the game writes its own speed (sprint): that becomes the new base
    pawn.GroundSpeed = 594.0
    module.rage_tick(None, None, None, None)
    assert pawn.GroundSpeed == pytest.approx(712.8)

    # no gain for decay_delay_s: drains, Enraged ends, our speed write is undone
    module._rage["last_gain"] -= module.RAGE["decay_delay_s"] + 0.1
    module._rage["last_tick"] -= 0.2
    module.rage_tick(None, None, None, None)
    assert module._rage["value"] < 100.0 and not module._rage["enraged"]
    assert pawn.GroundSpeed == pytest.approx(594.0)


def test_decay_waits_for_the_delay(tmp_path: Path):
    module, controller, pawn, weapon = _world(tmp_path)
    module._rage_add(40.0, "test")
    module._rage["last_tick"] -= 0.2
    module.rage_tick(None, None, None, None)
    assert module._rage["value"] == pytest.approx(40.0)


def test_meter_draws(tmp_path: Path):
    module, controller, pawn, weapon = _world(tmp_path)
    drawn: list[tuple] = []
    canvas = SimpleNamespace(
        ClipX=1920.0, ClipY=1080.0, Font=None,
        SetPos=lambda x, y: None, SetDrawColor=lambda *c: None,
        DrawTile=lambda *a: drawn.append(a), DrawText=lambda *a: drawn.append(("text",) + a),
        TextSize=lambda text, xl, yl: (len(text) * 17.0, 33.0))
    module._rage_draw.update(white=SimpleNamespace(SizeX=1, SizeY=1), ul=1.0, vl=1.0, blend=2, font=False)
    module._rage_add(55.0, "test")
    module._rage_meter(canvas, 1.0)
    texts = [d[1] for d in drawn if d[0] == "text"]
    assert "RAGE" in texts and "55%" in texts and any(t.startswith("DECAY IN") for t in texts)
    assert module._rage_counts["draw_errors"] == 0


RESOLUTIONS = [(1280, 720), (1920, 1080), (2560, 1440), (3440, 1440), (3840, 2160)]


def _states(module):
    base = {"now": 100.0, "decay_in": 4.2}
    return {
        "empty": dict(base, frac=0.0, value=0.0, enraged=False, decaying=False),
        "building": dict(base, frac=0.43, value=43.0, enraged=False, decaying=False),
        "decaying": dict(base, frac=0.71, value=71.0, enraged=False, decaying=True),
        "enraged": dict(base, frac=1.0, value=100.0, enraged=True, decaying=False),
    }


@pytest.mark.parametrize("cell", [33.0, 25.0])  # Willowbody 18pt, and the Hud_Medium fallback
def test_layout_never_overlaps_at_any_resolution(tmp_path: Path, cell: float):
    module, *_ = _world(tmp_path)
    measure = lambda s: (len(s) * 0.55 * cell, cell)  # noqa: E731
    for w, h in RESOLUTIONS:
        for name, state in _states(module).items():
            ops = module._rage_layout(float(w), float(h), measure, state)
            rects = [o for o in ops if o[0] == "rect"]
            texts = [o for o in ops if o[0] == "text"]
            for o in rects:  # whole pixels, on screen
                assert all(isinstance(v, int) for v in o[1:5]), (w, h, name, o)
                assert 0 <= o[1] and o[1] + o[3] <= w and 0 <= o[2] and o[2] + o[4] <= h
            panel = rects[1] if state["enraged"] else rects[0]
            bar = next(o for o in rects if o[5][:3] == (36, 5, 8))
            u = h / 1080.0
            hud = module.RAGE["hud"]
            caps_ok = {max(hud["min_label_px"], round(hud["label_px"] * u)),
                       max(hud["min_footer_px"], round(hud["footer_px"] * u))}
            for o in texts:
                _, s, x, y, scale, _c = o
                tw, cell_px = measure(s)[0] * scale, measure(s)[1] * scale
                cap_top = y + cell_px * module.FONT_CAP_TOP
                cap_h = cell_px * module.FONT_CAP_RATIO
                # inside the panel, visible letters never touching the bar
                assert panel[1] <= x and x + tw <= panel[1] + panel[3] + 2, (w, h, name, s)
                assert cap_top + cap_h <= bar[2] + 1 or cap_top >= bar[2] + bar[4] - 1,                     (w, h, name, s, cap_top, cap_h, bar)
                assert panel[2] <= cap_top and cap_top + cap_h <= panel[2] + panel[4] + 1
                # capitals are the design cap height times u (or its floor), whatever the font
                assert min(abs(cap_h - c) for c in caps_ok) <= 1.01, (w, h, s, cap_h, caps_ok)
            # every gap between segments is the same number of pixels
            segs = sorted(o for o in rects if o[5][:3] == (74, 10, 16))
            gaps = {b[1] - (a[1] + a[3]) for a, b in zip(segs, segs[1:])}
            assert len(segs) == 10 and len(gaps) == 1, (w, h, gaps)


def test_status_text_follows_the_numbers(tmp_path: Path):
    module, *_ = _world(tmp_path)
    assert module._rage_bonus_text() == "+25% DAMAGE    +20% SPEED"
    measure = lambda s: (len(s) * 18.0, 33.0)  # noqa: E731
    empty = _states(module)["empty"]
    texts = [o[1] for o in module._rage_layout(1920.0, 1080.0, measure, empty) if o[0] == "text"]
    assert texts == ["RAGE", "RAGE", "0%", "0%"]  # label + percent (each with its shadow), no hint


def test_hud_test_steps_through_every_state(tmp_path: Path):
    """The harness HUD test must cue one capture per state (a PRE/POST mix-up looped forever)."""
    data = _spec(tmp_path)
    data["options"].update(test_harness=True, harness_auto=True, harness_timing="seconds",
                           status_path=str(tmp_path / "status.json"),
                           harness_hud_test={"start_s": 0.0, "resolutions": [],
                                             "states": ["idle", "enraged"], "hold_s": 0.0})
    from bl2_catalog import load_catalog
    from bl2_preflight.augment import augment_world
    from bl2_preflight.dryrun import FAKES, _load

    if str(FAKES) not in sys.path:
        sys.path.insert(0, str(FAKES))
    from tests.fakes.graph import build_graph

    emit(data, tmp_path / "mod", force=True)
    catalog = load_catalog()
    graph = build_graph(catalog)
    augment_world(graph.world, catalog, json.loads((tmp_path / "mod" / "spec.json").read_text()))
    m = _load(tmp_path / "mod", graph, package=False)
    written: list[dict] = []
    m._write = lambda rec: written.append(rec) or rec
    m._hud_test_arm()
    import time as _time
    for _ in range(40):
        m._ht["t"] = min(m._ht["t"], _time.time())  # skip the waits
        m.hud_test_tick(None, None, None, None)
        m.hud_test_cue(None, None, None, None)
    cues = [r["hud_capture"] for r in written if "hud_capture" in r]
    assert [c.split("_")[-1] for c in cues] == ["idle", "enraged"]
    assert any(r.get("hud_test") == "done" for r in written)


def test_a_new_pawn_starts_with_no_rage(tmp_path: Path):
    module, controller, pawn, weapon = _world(tmp_path)
    module.rage_tick(None, None, None, None)          # adopt the pawn
    module._rage_add(60.0, "test")
    controller.Pawn = SimpleNamespace(GroundSpeed=440.0, Weapon=weapon)  # map load / respawn
    module.rage_tick(None, None, None, None)
    assert module._rage["value"] == 0.0 and not module._rage["enraged"]
