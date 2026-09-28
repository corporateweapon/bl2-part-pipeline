"""bl2-part-pipeline M2: a NEW gestalt fragment + NEW WeaponPartDefinition registered at runtime.

Registration now happens at the MAIN MENU (a few seconds after the SDK starts), so the new part
definition and fragment exist before any character save is deserialized (F16: registering after
map load crashes the game when a saved weapon references the part).

Control file scratch/m2_control.json:
  package, mesh_path, fragment, template_fragment, first_index, num_primitives, dz,
  part_name (default AR_Barrel_PL_Bent), enabled, grant (bool), behindview (bool), fov

Menu:      repoint gestalt def at the cloned mesh, append fragment/bounds/socket mappings,
           construct sockets + part definition, APPEND the part to the Shredifier barrel list.
Map load:  census of Shredifiers on the pawn (save/reload evidence), undo the AK-47 text mod's
           barrel retarget, then (if grant) one stock Shredifier and one with the new barrel forced.
F9 grant again, F10 equip newest Shredifier, F11 equip second newest.
"""
from __future__ import annotations

import json
import os
import time
import traceback
from typing import Any

import unrealsdk
from mods_base import build_mod, get_pc, hook, keybind
from unrealsdk.hooks import Block, Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

__version__ = "0.2"
__author__ = "44M0N"

REPO = os.environ.get("BL2_PIPELINE_REPO") or r"__REPO__"   # filled in when the template is installed
OUT = os.path.join(REPO, "scratch", "m2_status.json")
CONTROL = os.path.join(REPO, "scratch", "m2_control.json")

BALANCE = "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_Sherdifier"
SHRED_BARREL = "GD_Weap_AssaultRifle.Barrel.AR_Barrel_Vladof_Shredifier"
GESTALT_DEF = "Weap_AssaultRifles.GestaltDef_AssaultRifle"
MISSION = "GD_Episode01.M_Ep1_Champion"
MENU_TICKS = 600
TICKS_AFTER_LOAD = 120

_records: list[dict[str, Any]] = []
_registered = False
_backup: dict[str, Any] | None = None
_restore_left = 0
_barrel_backup: list[UObject] | None = None


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[m2] {msg}")


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
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(_records, f, indent=1, default=str)
    except Exception as ex:  # noqa: BLE001
        _log(f"write failed: {ex!r}")


def _step(rec: dict[str, Any], name: str, fn) -> Any:
    try:
        rec[name] = fn()
        return rec[name]
    except Exception as ex:  # noqa: BLE001
        rec[name] = f"FAILED {ex!r}\n{traceback.format_exc()}"
        return None


# ---------------------------------------------------------------- pieces
def shredifiers() -> list[tuple[int, UObject]]:
    pc = get_pc()
    out = []
    for w in unrealsdk.find_all("WillowWeapon", exact=False):
        try:
            if w.Owner == pc.Pawn and w.DefinitionData.BalanceDefinition is not None \
                    and w.DefinitionData.BalanceDefinition._path_name() == BALANCE:
                n = int(str(w.Name).rsplit("_", 1)[-1]) if "_" in str(w.Name) else 0
                out.append((n, w))
        except Exception:  # noqa: BLE001
            continue
    return sorted(out, key=lambda t: t[0])


def _desc(w: UObject) -> dict[str, Any]:
    dd = w.DefinitionData
    d: dict[str, Any] = {"weapon": w._path_name(), "slot": int(w.QuickSelectSlot)}
    d["barrel"] = dd.BarrelPartDefinition._path_name() if dd.BarrelPartDefinition else None
    d["frag"] = str(dd.BarrelPartDefinition.GestaltModeSkeletalMeshName) if dd.BarrelPartDefinition else None
    for attr in ("FirstPersonMesh", "ThirdPersonMesh"):
        c = getattr(w, attr, None)
        sm = getattr(c, "SkeletalMesh", None) if c is not None else None
        d[attr] = sm._path_name() if sm else None
    return d


def census() -> list[dict[str, Any]]:
    return [_desc(w) for _, w in shredifiers()]


KEEP_ALIVE_FLAG = 0x4000  # same bit the SDK's legacy KeepAlive() sets; survives level-transition GC


def keep_alive(obj: UObject) -> None:
    """Root an object (and its outer chain) so the level-transition GC does not destroy it.

    Objects from startup packages live in the engine's disregard-for-GC set, so references FROM
    them (e.g. GestaltDef.GestaltSkeletalMesh) to objects we load/construct later are never seen
    by the collector. Without this flag our mesh is destroyed at the next map load and the
    definition dangles -> "Ran out of virtual memory" fatal error (F17).
    """
    o: UObject | None = obj
    while o is not None:
        o.ObjectFlags |= KEEP_ALIVE_FLAG
        o = o.Outer


def repoint(ctl: dict[str, Any]) -> str:
    pkg = unrealsdk.load_package(ctl["package"])
    mesh = unrealsdk.find_object("SkeletalMesh", ctl["mesh_path"])
    keep_alive(pkg)
    keep_alive(mesh)
    for s in mesh.Sockets:
        keep_alive(s)
    gd = unrealsdk.find_object("GestaltSkeletalMeshDefinition", GESTALT_DEF)
    gd.GestaltSkeletalMesh = mesh
    return (f"{gd.GestaltSkeletalMesh._path_name()} sockets={len(mesh.Sockets)} "
            f"flags=0x{int(mesh.ObjectFlags):x} kept_alive")


def _part_path(ctl: dict[str, Any]) -> str:
    return f"GD_Weap_AssaultRifle.Barrel.{ctl.get('part_name', 'AR_Barrel_PL_Bent')}"


def register(ctl: dict[str, Any], steps: dict[str, Any] | None = None) -> dict[str, Any]:
    """Each sub-step can be switched off via control["steps"] for bisecting crashes (F17)."""
    global _registered
    steps = steps or {}
    frag, tmpl = ctl["fragment"], ctl["template_fragment"]
    dz = float(ctl.get("dz", 0.0))
    gd = unrealsdk.find_object("GestaltSkeletalMeshDefinition", GESTALT_DEF)
    mesh = gd.GestaltSkeletalMesh
    res: dict[str, Any] = {}
    if _registered:
        res["note"] = "already registered this session"
        return res
    if steps.get("parts", True):
        parts = gd.GestaltInfos[0].Parts
        src = next(p for p in parts if str(p.SkeletalMeshFragmentName) == tmpl)
        parts.append(src)
        parts[-1].SkeletalMeshFragmentName = frag
        parts[-1].FirstIndex = int(ctl["first_index"])
        parts[-1].NumPrimitives = int(ctl["num_primitives"])
        res["part_entry"] = {"name": str(parts[-1].SkeletalMeshFragmentName), "first": parts[-1].FirstIndex,
                             "num": parts[-1].NumPrimitives, "parts_len": len(gd.GestaltInfos[0].Parts)}
    if steps.get("bounds", True):
        b = gd.GestaltPartBounds
        bsrc = next(x for x in b if str(x.SkeletalMeshFragmentName) == tmpl)
        b.append(bsrc)
        b[-1].SkeletalMeshFragmentName = frag
        b[-1].ReferencePoseBounds.Origin.Z = b[-1].ReferencePoseBounds.Origin.Z + dz / 2
        b[-1].ReferencePoseBounds.BoxExtent.Z = b[-1].ReferencePoseBounds.BoxExtent.Z + dz / 2
        b[-1].ReferencePoseBounds.SphereRadius = b[-1].ReferencePoseBounds.SphereRadius + dz
        res["bounds_len"] = len(gd.GestaltPartBounds)
    sm = gd.GestaltSocketMappings
    made = []
    for m in (list(sm) if steps.get("sockets", True) else []):
        if str(m.SkeletalMeshFragmentName) != tmpl:
            continue
        orig = str(m.OriginalSocketName)
        old_mangled = str(m.MangledSocketName)
        new_mangled = f"{frag}_{orig}"
        tsock = next((s for s in mesh.Sockets if str(s.SocketName) == old_mangled), None)
        if tsock is None:
            made.append(f"{orig}: template socket {old_mangled} missing")
            continue
        s = unrealsdk.construct_object("SkeletalMeshSocket", mesh, new_mangled, 0, tsock)
        keep_alive(s)
        s.SocketName = new_mangled
        if orig.lower() == "muzzle":
            s.RelativeLocation.Z = s.RelativeLocation.Z + dz
        mesh.Sockets.append(s)
        sm.append(m)
        sm[-1].SkeletalMeshFragmentName = frag
        sm[-1].OriginalSocketName = orig
        sm[-1].MangledSocketName = new_mangled
        made.append(new_mangled)
    res["sockets"] = made
    if steps.get("partdef", True):
        tmpl_part = unrealsdk.find_object("WeaponPartDefinition", SHRED_BARREL)
        part_name = ctl.get("part_name", "AR_Barrel_PL_Bent")
        try:
            part = unrealsdk.find_object("WeaponPartDefinition", _part_path(ctl))
        except ValueError:
            part = unrealsdk.construct_object("WeaponPartDefinition", tmpl_part.Outer, part_name, 0, tmpl_part)
        keep_alive(part)
        part.GestaltModeSkeletalMeshName = frag if steps.get("parts", True) else tmpl
        res["part"] = f"{part._path_name()} frag={part.GestaltModeSkeletalMeshName}"
        if steps.get("partlist", True):
            # APPEND to the Shredifier barrel list (keeps the stock entry; both roll)
            bal = unrealsdk.find_object("WeaponBalanceDefinition", BALANCE)
            wp = bal.RuntimePartListCollection.BarrelPartData.WeightedParts
            if not any(e.Part is not None and e.Part._path_name() == part._path_name() for e in wp):
                wp.append(wp[0])
                wp[-1].Part = part
            res["barrel_list"] = [e.Part._path_name() if e.Part else None for e in wp]
    _registered = True
    return res


def force_barrel(part_path: str | None) -> str:
    """Temporarily make every barrel-list entry the given part (None = restore)."""
    global _barrel_backup
    bal = unrealsdk.find_object("WeaponBalanceDefinition", BALANCE)
    wp = bal.RuntimePartListCollection.BarrelPartData.WeightedParts
    if part_path is None:
        if _barrel_backup is not None:
            for e, p in zip(wp, _barrel_backup):
                e.Part = p
            _barrel_backup = None
        return "restored"
    _barrel_backup = [e.Part for e in wp]
    part = unrealsdk.find_object("WeaponPartDefinition", part_path)
    for e in wp:
        e.Part = part
    return part_path


def grant() -> str:
    global _backup, _restore_left
    pc = get_pc()
    if _backup is not None:
        _restore()
    bal = unrealsdk.find_object("WeaponBalanceDefinition", BALANCE)
    mission = unrealsdk.find_object("MissionDefinition", MISSION)
    level = pc.PlayerReplicationInfo.ExpLevel
    _backup = {"mission": mission, "game_stage": mission.GameStage,
               "reward_items": list(mission.Reward.RewardItems),
               "reward_pools": list(mission.Reward.RewardItemPools)}
    mission.GameStage = level
    mission.Reward.RewardItems = [bal]
    mission.Reward.RewardItemPools = []
    pc.ServerGrantMissionRewards(mission, False)
    _restore_left = 300
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


def equip(which: int) -> str:
    pc = get_pc()
    inv = pc.GetPawnInventoryManager()
    ws = shredifiers()
    if len(ws) < -which:
        return f"only {len(ws)} shredifiers"
    w = ws[which][1]
    cur = int(w.QuickSelectSlot)
    if cur > 0:
        inv.EquipWeaponFromSlot(cur)
        return f"equipped slot {cur}: {_desc(w)}"
    held = pc.Pawn.Weapon
    slot = int(held.QuickSelectSlot) if held is not None else 1
    inv.ReadyBackpackInventory(w, slot)
    inv.EquipWeaponFromSlot(slot)
    return f"readied+equipped slot {slot}: {_desc(w)}"


# ---------------------------------------------------------------- save round-trip (F18)
SAVE_DIR = os.path.join(REPO, "scratch", "m2_saves")
SLOT_FIELDS = ("BodyPartDefinition", "GripPartDefinition", "BarrelPartDefinition", "SightPartDefinition",
               "StockPartDefinition", "ElementalPartDefinition", "Accessory1PartDefinition",
               "Accessory2PartDefinition", "MaterialPartDefinition", "PrefixPartDefinition",
               "TitlePartDefinition")


def _custom_prefix() -> str:
    return f"GD_Weap_AssaultRifle.Barrel.{_control().get('part_name', 'AR_Barrel_PL_Bent')}"


def _save_file(save_name: str) -> str:
    os.makedirs(SAVE_DIR, exist_ok=True)
    safe = "".join(c for c in str(save_name) if c.isalnum() or c in "._-") or "unknown"
    return os.path.join(SAVE_DIR, safe + ".json")


def _all_weapons(pc: UObject) -> list[UObject]:
    out = []
    for w in unrealsdk.find_all("WillowWeapon", exact=False):
        try:
            if w.Owner == pc.Pawn:
                out.append(w)
        except Exception:  # noqa: BLE001
            continue
    return out


@hook("WillowGame.WillowPlayerController:GeneratePlayerSaveGame", Type.PRE)
def on_generate_save(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    """Record which weapons carry our custom parts (by UniqueId) so we can restore them on load."""
    try:
        prefix = _custom_prefix()
        recs: dict[str, dict[str, str]] = {}
        for w in _all_weapons(obj):
            dd = w.DefinitionData
            slots = {}
            for f in SLOT_FIELDS:
                p = getattr(dd, f, None)
                if p is not None and p._path_name().startswith(prefix):
                    slots[f] = p._path_name()
                    keep_alive(p)
            if slots:
                recs[str(int(dd.UniqueId))] = slots
        path = _save_file(obj.SaveGameName)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(recs, f, indent=1)
        _write({"save_hook": "GeneratePlayerSaveGame", "file": path, "custom_weapons": recs})
    except Exception as ex:  # noqa: BLE001
        _write({"save_hook_error": f"{ex!r}\n{traceback.format_exc()}"})


@hook("WillowGame.WillowPlayerController:ApplyPlayerSaveGameData", Type.PRE)
def on_apply_save(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    """Before the game validates/creates the saved weapons, put our parts back into their slots."""
    try:
        save_name = obj.GetSaveGameNameFromid(args.SaveGame.SaveGameId)
        path = _save_file(save_name)
        if not os.path.exists(path):
            _write({"load_hook": "no record", "save": str(save_name)})
            return
        with open(path, encoding="utf-8") as f:
            recs = json.load(f)
        fixed = []
        for wd in args.SaveGame.WeaponData:
            dd = wd.WeaponDefinitionData
            slots = recs.get(str(int(dd.UniqueId)))
            if not slots:
                continue
            for field, part_path in slots.items():
                part = unrealsdk.find_object("WeaponPartDefinition", part_path)
                setattr(dd, field, part)
                fixed.append(f"{int(dd.UniqueId)}:{field}={part_path}")
        _write({"load_hook": "ApplyPlayerSaveGameData", "save": str(save_name), "fixed": fixed})
    except Exception as ex:  # noqa: BLE001
        _write({"load_hook_error": f"{ex!r}\n{traceback.format_exc()}"})


@hook("WillowGame.WillowPlayerController:ValidateWeaponDefinition", Type.PRE)
def on_validate_weapon(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> Any:
    """The sanity check rejects weapons whose parts are not in the cooked part lists (F19).

    If the definition references one of our runtime parts, short-circuit the check to True.
    Everything else goes through the normal validation untouched.
    """
    try:
        prefix = _custom_prefix()
        dd = args.DefinitionData
        for f in SLOT_FIELDS:
            p = getattr(dd, f, None)
            if p is not None and p._path_name().startswith(prefix):
                _write({"validate_hook": "forced True", "part": p._path_name(), "uid": int(dd.UniqueId)})
                return (Block, True)
    except Exception as ex:  # noqa: BLE001
        _write({"validate_hook_error": repr(ex)})
    return None


# ---------------------------------------------------------------- main-menu setup
_menu_ticks = 0
_menu_done = False


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def menu_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _menu_ticks, _menu_done
    _menu_ticks += 1
    if _menu_ticks < MENU_TICKS:
        return
    menu_tick.disable()
    ctl = _control()
    if not ctl.get("enabled", False):
        return
    rec: dict[str, Any] = {"phase": "menu_setup", "steps_enabled": ctl.get("steps", {})}
    steps = ctl.get("steps", {})
    if steps.get("repoint", True):
        _step(rec, "repoint", lambda: repoint(ctl))
    if steps.get("register", True):
        _step(rec, "register", lambda: register(ctl, steps))
    _menu_done = True
    _write(rec)
    _log("menu setup done")


# ---------------------------------------------------------------- per-map sequencing
_phase = 0
_wait = 0


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def seq_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _phase, _wait
    _wait -= 1
    if _wait > 0:
        return
    ctl = _control()
    rec: dict[str, Any] = {"phase": _phase}
    if _phase == 0:
        _step(rec, "census_before", census)

        def _force() -> str:
            p = unrealsdk.find_object("WeaponPartDefinition", SHRED_BARREL)
            p.GestaltModeSkeletalMeshName = ctl.get("template_fragment", "AR_Barrel_Vladof")
            return str(p.GestaltModeSkeletalMeshName)
        _step(rec, "force_stock_fragment", _force)
        if not _menu_done:  # e.g. mod loaded mid-game
            _step(rec, "repoint", lambda: repoint(ctl))
            _step(rec, "register", lambda: register(ctl))
        if ctl.get("grant", True):
            _step(rec, "force_barrel_stock", lambda: force_barrel(SHRED_BARREL))
            _step(rec, "grant_stock", grant)
            _wait = 90
            _phase = 1
        else:
            _phase = 3
    elif _phase == 1:
        _step(rec, "stock_weapon", lambda: _desc(shredifiers()[-1][1]))
        _step(rec, "force_barrel_new", lambda: force_barrel(_part_path(ctl)))
        _step(rec, "grant_new", grant)
        _wait = 90
        _phase = 2
    elif _phase == 2:
        _step(rec, "new_weapon", lambda: _desc(shredifiers()[-1][1]))
        _step(rec, "restore_barrel_list", lambda: force_barrel(None))
        _step(rec, "equip_newest", lambda: equip(-1))
        _wait = 120
        _phase = 3
    elif _phase == 3:
        try:
            get_pc().ConsoleCommand(f"FOV {ctl.get('fov', 100)}")
            if ctl.get("behindview", True):
                get_pc().SetBehindView(True)
            rec["camera"] = "set"
        except Exception as ex:  # noqa: BLE001
            rec["camera"] = repr(ex)
        _step(rec, "held", lambda: _desc(get_pc().Pawn.Weapon) if get_pc().Pawn.Weapon else None)
        _phase = 4
    if _phase == 4:
        seq_tick.disable()
        rec["done"] = True
    _write(rec)
    _log(f"phase {rec['phase']} done")


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def restore_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _restore_left
    _restore_left -= 1
    if _restore_left <= 0:
        _restore()
        restore_tick.disable()


@hook("WillowGame.WillowPlayerController:WillowClientDisableLoadingMovie", Type.POST)
def on_map_loaded(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _phase, _wait
    pc = get_pc(possibly_loading=True)
    if pc is None or pc.Pawn is None or not _control().get("enabled", False):
        return
    _phase, _wait = 0, TICKS_AFTER_LOAD
    seq_tick.enable()


@keybind("M2: grant again", "F9")
def kb_grant() -> None:
    global _phase, _wait
    _phase, _wait = 0, 1
    seq_tick.enable()


@keybind("M2: equip newest Shredifier", "F10")
def kb_new() -> None:
    _write({"keybind": "F10", "equip": equip(-1)})


@keybind("M2: equip second-newest Shredifier", "F11")
def kb_prev() -> None:
    _write({"keybind": "F11", "equip": equip(-2)})


build_mod(name="Pipeline M2", description="New gestalt fragment + part definition registered at runtime.",
          hooks=[on_map_loaded, on_generate_save, on_apply_save, on_validate_weapon],
          keybinds=[kb_grant, kb_new, kb_prev])
on_map_loaded.enable()
menu_tick.enable()
on_generate_save.enable()
on_apply_save.enable()
on_validate_weapon.enable()
_log("m2 armed (menu setup pending)")
