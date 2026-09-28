"""Bone-motion probe: what the Jakobs sniper animations actually do to the skeleton.

A disposable measurement mod, in the shape of `probe_mod_template.py`. It registers
nothing and constructs nothing, so it is **not** a pipeline mod and may sit beside the
Armory: the rule is one mod per host gestalt, and this one touches no gestalt at all.

Why it exists (M9 §4, handoff task 2). The AWP's bolt and magazine are weighted to
`Root`, i.e. rigid, because `Jakobs_BoltHandle` (rest head y=12.34, z=9.59) and `Clip`
(y=-6.59, z=6.71) sit 12-28 units away from the AWP geometry they would drive, and a
*rotation* on a bone that far away swings that geometry across the gun. The verify loop
never fires and never reloads, so a wrong choice there is invisible to it. Rather than
guess, measure: sample every bone of the sniper skeleton through a fire and a reload and
see whether those two bones translate (safe to use) or rotate (not).

What it writes, to `scratch/anim_probe.json`:

    bones_ref     {bone: {location, rotation}} in the idle pose, world space
    samples       [{t, phase, bones: {bone: {location, rotation}}}, ...]
    weapon        the held weapon's name and balance, so the sample is attributable

Analysis is offline (`src/bl2_verify/anim_probe.py --analyse`): every bone's motion is
expressed *relative to Root*, so the player's own sway and movement drop out, and the
rotation magnitude is what decides the bone map.
"""
from __future__ import annotations

import json
import os
import time
import traceback
from typing import Any

import unrealsdk
from mods_base import build_mod, get_pc, hook
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

__version__ = "0.1"
__author__ = "44M0N"

REPO = os.environ.get("BL2_PIPELINE_REPO") or r"__REPO__"   # filled in when the template is installed
OUT = os.path.join(REPO, "scratch", "anim_probe.json")
CONTROL = os.path.join(REPO, "scratch", "anim_probe_control.json")

#: every bone of Weap_SniperRifles.GestaltDef_SniperRifle_GestaltSkeletalMesh
BONES = [
    "Root", "WeaponOffset", "Trigger", "Jakobs_ChamberArm", "Jakobs_Chamber",
    "Jakobs_Rounds", "Maliwan_BarrelHinge", "Barrel", "Vladoff_Gatling",
    "Alien_Top_R_Flap", "Alien_Bottom_L_Flap", "Alien_Bottom_R_Flap", "Alien_Top_L_Flap",
    "Clip", "Maliwan_Clip", "Scope", "Dahl_Clip", "Vladof_Clip", "Vladof_AmmoRelease",
    "Slider", "Jakobs_BoltHandle", "Jakobs_VarA_BackSlider",
]

#: wall-clock plan, seconds from the moment a matching weapon is in hand.
#: ``fire`` is long and *pulsed*: a Jakobs weapon is semi-auto, so holding the trigger
#: fires once. Pulsing empties the magazine, and an empty magazine is what makes the
#: game play the reload clip on its own -- the first probe run found no function that
#: triggers one (``reload_via: null``), so this is the way in.
PLAN = [("settle", 1.5), ("rest", 1.0), ("fire", 12.0), ("reload", 7.0)]

#: seconds between trigger pulls during the fire phase
PULSE = 0.45

R: dict[str, Any] = {"started": time.time(), "samples": [], "events": []}
_phase_index = -1
_phase_until = 0.0
_armed = False
_done = False
_weapon: Any = None
_next_pulse = 0.0
_trigger_down = False
_waits = 0


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[anim_probe] {msg}")


def _control() -> dict[str, Any]:
    try:
        with open(CONTROL, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:  # noqa: BLE001
        return {}


def _vec(value: Any) -> list[float]:
    return [round(float(value.X), 4), round(float(value.Y), 4), round(float(value.Z), 4)]


def _sample_bones(component: Any) -> dict[str, Any]:
    """Every bone's world location and rotation on this component, guarded per bone.

    ``GetBoneLocation``/``GetBoneQuaternion`` return values directly (no out params), so
    the M7 out-param dance does not apply here. Space 0 is world space.
    """
    out: dict[str, Any] = {}
    for bone in BONES:
        entry: dict[str, Any] = {}
        try:
            entry["location"] = _vec(component.GetBoneLocation(bone, 0))
        except Exception as ex:  # noqa: BLE001
            entry["location"] = f"unavailable: {ex!r}"
        for name, call in (
            ("quat", lambda b=bone: component.GetBoneQuaternion(b, 0)),
            ("axis", lambda b=bone: component.GetBoneAxis(b, 0)),
        ):
            try:
                value = call()
                entry[name] = [round(float(getattr(value, k)), 5)
                               for k in ("X", "Y", "Z", "W") if hasattr(value, k)]
                break
            except Exception as ex:  # noqa: BLE001
                entry[name] = f"unavailable: {ex!r}"
        out[bone] = entry
    return out


def _held_weapon() -> Any:
    controller = get_pc(possibly_loading=True)
    if controller is None or controller.Pawn is None:
        return None
    return controller.Pawn.Weapon


def _describe(weapon: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, read in (
        ("name", lambda: str(weapon.GetHumanReadableName())),
        ("balance", lambda: weapon.DefinitionData.BalanceDefinition._path_name()),
        ("mesh", lambda: weapon.FirstPersonMesh.SkeletalMesh._path_name()),
        ("clip", lambda: int(weapon.ReloadCnt)),
    ):
        try:
            out[key] = read()
        except Exception as ex:  # noqa: BLE001
            out[key] = f"unavailable: {ex!r}"
    return out


def _clip(weapon: Any) -> Any:
    """Rounds left in the magazine; the reload window is where this jumps back up."""
    for attr in ("ReloadCnt", "AmmoInClip", "ClipSize"):
        try:
            return int(getattr(weapon, attr))
        except Exception:  # noqa: BLE001
            continue
    return None


def _pulse(weapon: Any, now: float) -> None:
    """One trigger pull per :data:`PULSE` seconds, so a semi-auto actually empties."""
    global _next_pulse, _trigger_down
    if now < _next_pulse:
        return
    try:
        if _trigger_down:
            weapon.StopFire(0)
            _trigger_down = False
            _next_pulse = now + PULSE * 0.4
        else:
            weapon.StartFire(0)
            _trigger_down = True
            _next_pulse = now + PULSE * 0.6
    except Exception as ex:  # noqa: BLE001
        R["events"].append({"pulse_error": repr(ex), "t": now})


def _begin_phase(name: str, weapon: Any) -> None:
    """Whatever this phase has to make the weapon do, done once on entry."""
    global _trigger_down, _next_pulse
    try:
        if name == "fire":
            _next_pulse, _trigger_down = 0.0, False
        elif name == "reload":
            if _trigger_down:
                weapon.StopFire(0)
                _trigger_down = False
            # nothing to call: an empty magazine reloads itself. Recorded either way so
            # the analysis can say whether a reload actually happened.
            R["events"].append({"clip_at_reload_phase": _clip(weapon), "t": time.time()})
    except Exception as ex:  # noqa: BLE001
        R["events"].append({"phase_error": name, "error": repr(ex), "t": time.time()})


def _finish() -> None:
    R["finished"] = time.time()
    R["bones"] = BONES
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(R, handle, indent=1, default=str)
    _log(f"wrote {OUT} ({len(R['samples'])} samples)")
    if _control().get("autoexit", True):
        try:
            controller = get_pc(possibly_loading=True)
            if controller is not None:
                controller.ConsoleCommand("exit")
        except Exception as ex:  # noqa: BLE001
            _log(f"exit failed: {ex!r}")


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def on_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _armed, _done, _phase_index, _phase_until, _weapon
    if _done:
        return
    try:
        now = time.time()
        if not _armed:
            global _waits
            _waits += 1
            weapon = _held_weapon()
            wanted = str(_control().get("weapon_name", "AWP")).lower()
            described = _describe(weapon) if weapon is not None else None
            if _waits % 120 == 0:  # ~ once a second: say what we can actually see
                _log(f"waiting: weapon={described.get('name') if described else None!r} "
                     f"balance={described.get('balance') if described else None!r} "
                     f"wanted={wanted!r}")
                R["events"].append({"waiting": described, "wanted": wanted, "t": now})
            if weapon is None or described is None:
                return
            # match on the card name OR the balance path: a weapon whose name read is
            # unavailable for a frame must not cost the whole run
            haystack = f"{described.get('name', '')} {described.get('balance', '')}".lower()
            if wanted and wanted not in haystack:
                return  # not the weapon we came to measure; keep waiting
            _weapon = weapon
            _armed = True
            R["weapon"] = described
            R["bones_ref"] = _sample_bones(weapon.FirstPersonMesh)
            _phase_index, _phase_until = 0, now + PLAN[0][1]
            _log(f"armed on {described.get('name')}; {len(PLAN)} phase(s)")
            R["events"].append({"armed": described, "t": now})
            return

        phase = PLAN[_phase_index][0]
        if phase == "fire":
            _pulse(_weapon, now)
        R["samples"].append({"t": round(now - R["started"], 4), "phase": phase,
                             "clip": _clip(_weapon),
                             "bones": _sample_bones(_weapon.FirstPersonMesh)})
        if now < _phase_until:
            return
        _phase_index += 1
        if _phase_index >= len(PLAN):
            _done = True
            on_tick.disable()
            _finish()
            return
        name, seconds = PLAN[_phase_index]
        _phase_until = now + seconds
        R["events"].append({"phase": name, "t": now})
        _log(f"phase {name} ({seconds}s)")
        _begin_phase(name, _weapon)
    except Exception as ex:  # noqa: BLE001
        _done = True
        R["error"] = f"{ex!r}\n{traceback.format_exc()}"
        try:
            on_tick.disable()
        except Exception:  # noqa: BLE001
            pass
        _finish()


build_mod(name="Pipeline Anim Probe", description="sniper bone-motion probe", hooks=[on_tick])
# disposable probe: arm regardless of the mod-menu enabled state
on_tick.enable()
_log("armed; waiting for the weapon")
