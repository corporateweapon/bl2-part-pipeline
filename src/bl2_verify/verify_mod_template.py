"""bl2-part-pipeline M1/M2 in-game verification helper.

On every gameplay map load (and on F9):
  1. force the Shredifier barrel back to fragment `AR_Barrel_Vladof` (the AK-47 text mod
     retargets it to the Bandit barrel, which would hide our mesh edit),
  2. grant one Shredifier at the player's level via the mission-reward trick,
  3. ready it from the backpack so it is the equipped, first-person weapon,
  4. append a status record to scratch/verify_status.json in the pipeline repo.
Control file scratch/verify_control.json: {"enabled": true, "fragment": "AR_Barrel_Vladof"}.
"""
from __future__ import annotations

import json
import os
import time
import traceback
from typing import Any

import unrealsdk
from mods_base import build_mod, get_pc, hook, keybind
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

__version__ = "0.1"
__author__ = "44M0N"

REPO = os.environ.get("BL2_PIPELINE_REPO") or r"__REPO__"   # filled in when the template is installed
OUT = os.path.join(REPO, "scratch", "verify_status.json")
CONTROL = os.path.join(REPO, "scratch", "verify_control.json")

BALANCE = "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_Sherdifier"
SHRED_BARREL = "GD_Weap_AssaultRifle.Barrel.AR_Barrel_Vladof_Shredifier"
MISSION = "GD_Episode01.M_Ep1_Champion"
TICKS_AFTER_LOAD = 120
RESTORE_TICKS = 300

_pending = 0
_backup: dict[str, Any] | None = None
_restore_left = 0
_records: list[dict[str, Any]] = []


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[verify] {msg}")


def _control() -> dict[str, Any]:
    try:
        with open(CONTROL, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _write(rec: dict[str, Any]) -> None:
    rec["t"] = time.time()
    _records.append(rec)
    try:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(_records, f, indent=1, default=str)
    except Exception as ex:  # noqa: BLE001
        _log(f"write failed: {ex!r}")


def force_fragment(name: str) -> str:
    part = unrealsdk.find_object("WeaponPartDefinition", SHRED_BARREL)
    part.GestaltModeSkeletalMeshName = name
    return str(part.GestaltModeSkeletalMeshName)


GESTALT_DEF = "Weap_AssaultRifles.GestaltDef_AssaultRifle"


def repoint_mesh(ctl: dict[str, Any]) -> str:
    """Load the pipeline package and make the AR gestalt def use its mesh copy."""
    package = ctl.get("package")
    mesh_path = ctl.get("mesh_path")
    if not package or not mesh_path:
        return "skipped (no package/mesh_path in control file)"
    pkg = unrealsdk.load_package(package)
    found = []
    for o in unrealsdk.find_all("Object", exact=False):
        try:
            p = o._path_name()
        except Exception:  # noqa: BLE001
            continue
        if p.startswith(package + ".") or p == package or "PL_AR_Gestalt" in p or "PipelineMeshes" in p:
            found.append(f"{o.Class.Name}:{p}")
            if len(found) >= 60:
                break
    _write({"reason": "repoint_enum", "steps": {"package_obj": pkg._path_name() if pkg else None, "objects": found}})
    mesh = unrealsdk.find_object("SkeletalMesh", mesh_path)
    gd = unrealsdk.find_object("GestaltSkeletalMeshDefinition", GESTALT_DEF)
    before = gd.GestaltSkeletalMesh._path_name() if gd.GestaltSkeletalMesh else None
    gd.GestaltSkeletalMesh = mesh
    after = gd.GestaltSkeletalMesh._path_name() if gd.GestaltSkeletalMesh else None
    return (f"package={pkg._path_name() if pkg else None} mesh={mesh._path_name()} "
            f"sockets={len(mesh.Sockets)} bounds={mesh.Bounds} def: {before} -> {after}")


def grant_shredifier() -> str:
    """Give one Shredifier at player level. Returns a description."""
    global _backup, _restore_left
    pc = get_pc()
    if pc.Pawn is None:
        return "no pawn"
    if _backup is not None:
        return "grant already pending"
    bal = unrealsdk.find_object("WeaponBalanceDefinition", BALANCE)
    mission = unrealsdk.find_object("MissionDefinition", MISSION)
    level = pc.PlayerReplicationInfo.ExpLevel
    _backup = {
        "mission": mission,
        "game_stage": mission.GameStage,
        "reward_items": list(mission.Reward.RewardItems),
        "reward_pools": list(mission.Reward.RewardItemPools),
    }
    mission.GameStage = level
    mission.Reward.RewardItems = [bal]
    mission.Reward.RewardItemPools = []
    pc.ServerGrantMissionRewards(mission, False)
    _restore_left = RESTORE_TICKS
    restore_tick.enable()
    return f"granted level {level}"


def _restore() -> None:
    global _backup
    b = _backup
    if b is None:
        return
    m = b["mission"]
    m.GameStage = b["game_stage"]
    m.Reward.RewardItems = b["reward_items"]
    m.Reward.RewardItemPools = b["reward_pools"]
    _backup = None
    _log("mission reward restored")


def equip_latest_shredifier() -> str:
    pc = get_pc()
    inv = pc.GetPawnInventoryManager()
    found = None
    best = -1
    for w in unrealsdk.find_all("WillowWeapon", exact=False):
        try:
            if w.Owner == pc.Pawn and w.DefinitionData.BalanceDefinition is not None \
                    and w.DefinitionData.BalanceDefinition._path_name() == BALANCE:
                n = int(str(w.Name).rsplit("_", 1)[-1]) if "_" in str(w.Name) else 0
                if n > best:  # newest instance = the one granted after the re-point
                    best, found = n, w
        except Exception:  # noqa: BLE001
            continue
    if found is None:
        return "no shredifier on pawn"
    try:
        held = pc.Pawn.Weapon
        cur = int(found.QuickSelectSlot)
        if cur > 0:  # already readied: just switch to it
            inv.EquipWeaponFromSlot(cur)
            state = f"already in slot {cur}; equipped"
        else:
            slot = int(held.QuickSelectSlot) if held is not None else 1
            inv.ReadyBackpackInventory(found, slot)  # swap into the held weapon's quick slot
            inv.EquipWeaponFromSlot(slot)
            state = f"readied into slot {slot} and equipped"
    except Exception as ex:  # noqa: BLE001
        state = f"ready/equip failed: {ex!r}"
    try:
        dd = found.DefinitionData
        parts = {
            "barrel": dd.BarrelPartDefinition._path_name() if dd.BarrelPartDefinition else None,
            "body": dd.BodyPartDefinition._path_name() if dd.BodyPartDefinition else None,
            "frag": str(dd.BarrelPartDefinition.GestaltModeSkeletalMeshName) if dd.BarrelPartDefinition else None,
        }
    except Exception as ex:  # noqa: BLE001
        parts = {"error": repr(ex)}
    return f"{state}; weapon={found._path_name()} parts={parts}"


def run(reason: str) -> None:
    ctl = _control()
    if not ctl.get("enabled", True):
        return
    rec: dict[str, Any] = {"reason": reason, "steps": {}}
    for name, fn in (
        ("force_fragment", lambda: force_fragment(ctl.get("fragment", "AR_Barrel_Vladof"))),
        ("repoint_mesh", lambda: repoint_mesh(ctl)),
        ("grant", grant_shredifier),
    ):
        try:
            rec["steps"][name] = fn()
        except Exception as ex:  # noqa: BLE001
            rec["steps"][name] = f"FAILED {ex!r}\n{traceback.format_exc()}"
    _write(rec)
    _log(f"{reason}: {rec['steps']}")
    # equip a few ticks later, once the grant has landed in the backpack
    global _pending
    _pending = 90
    equip_tick.enable()


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def equip_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _pending
    _pending -= 1
    if _pending > 0:
        return
    equip_tick.disable()
    try:
        res = equip_latest_shredifier()
    except Exception as ex:  # noqa: BLE001
        res = f"FAILED {ex!r}\n{traceback.format_exc()}"
    _write({"reason": "equip", "steps": {"equip": res}})
    _log(f"equip: {res}")
    global _shot_left
    _shot_left = 240
    shot_tick.enable()


_shot_left = 0


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def shot_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    """A few seconds after equip: widen FOV so the muzzle is on screen, report the held weapon, screenshot."""
    global _shot_left
    _shot_left -= 1
    if _shot_left == 120:
        try:
            get_pc().ConsoleCommand(f"FOV {_control().get('fov', 100)}")
        except Exception as ex:  # noqa: BLE001
            _log(f"fov failed: {ex!r}")
        return
    if _shot_left > 0:
        return
    shot_tick.disable()
    info: dict[str, Any] = {}
    try:
        pc = get_pc()
        w = pc.Pawn.Weapon
        info["held"] = w._path_name() if w else None
        info["held_balance"] = w.DefinitionData.BalanceDefinition._path_name() if w and w.DefinitionData.BalanceDefinition else None
        info["held_barrel"] = w.DefinitionData.BarrelPartDefinition._path_name() if w and w.DefinitionData.BarrelPartDefinition else None
        info["held_frag"] = str(w.DefinitionData.BarrelPartDefinition.GestaltModeSkeletalMeshName) if w and w.DefinitionData.BarrelPartDefinition else None
        try:
            comps = []
            for attr in ("Mesh", "SkeletalMeshComponent", "FirstPersonMesh", "ThirdPersonMesh"):
                c = getattr(w, attr, None)
                if c is not None:
                    sm = getattr(c, "SkeletalMesh", None)
                    comps.append(f"{attr}={c._path_name()} mesh={sm._path_name() if sm else None}")
            info["held_components"] = comps
        except Exception as ex:  # noqa: BLE001
            info["held_components_error"] = repr(ex)
        pc.ConsoleCommand("shot")
        info["shot"] = "requested"
    except Exception as ex:  # noqa: BLE001
        info["error"] = f"{ex!r}\n{traceback.format_exc()}"
    _write({"reason": "shot", "steps": info})
    _log(f"shot: {info}")
    global _cam_left
    _cam_left = 300
    cam_tick.enable()


_cam_left = 0


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def cam_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    """~5 s after the first-person record: switch to the behind view for a full-weapon capture."""
    global _cam_left
    _cam_left -= 1
    if _cam_left > 0:
        return
    cam_tick.disable()
    if not _control().get("behindview", True):
        return
    info: dict[str, Any] = {}
    try:
        pc = get_pc()
        pc.SetBehindView(True)
        info["behindview"] = "SetBehindView(True) called"
    except Exception as ex:  # noqa: BLE001
        info["behindview_error"] = repr(ex)
    _write({"reason": "behindview", "steps": info})
    _log(f"behindview: {info}")


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def restore_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _restore_left
    _restore_left -= 1
    if _restore_left <= 0:
        _restore()
        restore_tick.disable()


_load_ticks = 0


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def after_load_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _load_ticks
    _load_ticks -= 1
    if _load_ticks > 0:
        return
    after_load_tick.disable()
    run("map_loaded")


@hook("WillowGame.WillowPlayerController:WillowClientDisableLoadingMovie", Type.POST)
def on_map_loaded(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _load_ticks
    pc = get_pc(possibly_loading=True)
    if pc is None or pc.Pawn is None:
        return
    _load_ticks = TICKS_AFTER_LOAD
    after_load_tick.enable()


@keybind("Verify: give + equip Shredifier", "F9")
def kb_run() -> None:
    run("keybind")


@keybind("Verify: equip latest Shredifier", "F10")
def kb_equip() -> None:
    try:
        res = equip_latest_shredifier()
    except Exception as ex:  # noqa: BLE001
        res = f"FAILED {ex!r}"
    _write({"reason": "equip_keybind", "steps": {"equip": res}})
    _log(f"equip: {res}")


build_mod(
    name="Pipeline Verify (M1)",
    description="Forces the Shredifier barrel fragment, grants and equips a Shredifier for mesh verification.",
    hooks=[on_map_loaded],
    keybinds=[kb_run, kb_equip],
)
on_map_loaded.enable()
_log("verify armed")
