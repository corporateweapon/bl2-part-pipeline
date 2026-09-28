"""bl2-part-pipeline M0 discovery probe.

Runs once when the first map (the main menu) finishes loading, writes
scratch/probe_results.json in the pipeline repo, then quits the game if the
control file asks for autoexit. Every experiment is wrapped so a failure is
recorded, not fatal.
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
OUT = os.path.join(REPO, "scratch", "probe_results.json")
CONTROL = os.path.join(REPO, "scratch", "probe_control.json")

GESTALT_DEF = "Weap_AssaultRifles.GestaltDef_AssaultRifle"
GESTALT_MESH = "Weap_AssaultRifles.GestaltDef_AssaultRifle_GestaltSkeletalMesh"
SHRED_BARREL = "GD_Weap_AssaultRifle.Barrel.AR_Barrel_Vladof_Shredifier"
PROBE_PART = "AR_Barrel_PipelineProbe"
PROBE_FRAG = "AR_Barrel_PipelineProbeFrag"

_done = False
R: dict[str, Any] = {"started": time.time(), "steps": {}}


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[probe] {msg}")


def _step(name: str, fn) -> None:
    try:
        R["steps"][name] = {"ok": True, "result": fn()}
        _log(f"{name}: ok")
    except Exception as ex:  # noqa: BLE001
        R["steps"][name] = {"ok": False, "error": repr(ex), "trace": traceback.format_exc()}
        _log(f"{name}: FAILED {ex!r}")


def _control() -> dict[str, Any]:
    try:
        with open(CONTROL, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------- experiments
def p1_packages() -> dict[str, Any]:
    pkgs = []
    for i, p in enumerate(unrealsdk.find_all("Package", exact=True)):
        try:
            pkgs.append({"i": i, "path": p._path_name(), "outer": p.Outer._path_name() if p.Outer else None})
        except Exception as ex:  # noqa: BLE001
            pkgs.append({"i": i, "error": repr(ex)})
    top = [p["path"] for p in pkgs if p.get("outer") is None]
    return {"count": len(pkgs), "top_level_in_order": top, "all": pkgs}


def p2_gestalt_read() -> dict[str, Any]:
    gd = unrealsdk.find_object("GestaltSkeletalMeshDefinition", GESTALT_DEF)
    mesh = gd.GestaltSkeletalMesh
    infos = gd.GestaltInfos
    parts = infos[0].Parts
    frags = []
    for p in parts:
        frags.append({"name": str(p.SkeletalMeshFragmentName), "mat": p.MaterialIndex,
                      "first": p.FirstIndex, "num": p.NumPrimitives})
    return {
        "mesh_path": mesh._path_name() if mesh else None,
        "mesh_class": mesh.Class._path_name() if mesh else None,
        "gestalt_infos_len": len(infos),
        "parts_len": len(parts),
        "bounds_len": len(gd.GestaltPartBounds),
        "socket_mappings_len": len(gd.GestaltSocketMappings),
        "mesh_sockets_len": len(mesh.Sockets) if mesh else None,
        "mesh_materials": [m._path_name() if m else None for m in mesh.Materials] if mesh else None,
        "frags": frags,
        "part_struct_type": parts[0]._type._path_name() if len(parts) else None,
        "bounds_struct_type": gd.GestaltPartBounds[0]._type._path_name(),
        "sockmap_struct_type": gd.GestaltSocketMappings[0]._type._path_name(),
    }


def p3_construct_part() -> dict[str, Any]:
    src = unrealsdk.find_object("WeaponPartDefinition", SHRED_BARREL)
    outer = src.Outer
    new = unrealsdk.construct_object("WeaponPartDefinition", outer, PROBE_PART, 0, src)
    new.GestaltModeSkeletalMeshName = "AR_Barrel_Vladof"
    found = unrealsdk.find_object("WeaponPartDefinition", f"{outer._path_name()}.{PROBE_PART}")
    return {
        "new_path": new._path_name(),
        "resolves_via_find_object": found is not None and found._path_name() == new._path_name(),
        "gestalt_name_readback": str(new.GestaltModeSkeletalMeshName),
        "part_type": str(new.PartType),
        "title_list_len": len(new.TitleList),
    }


def p4_append_fragment() -> dict[str, Any]:
    gd = unrealsdk.find_object("GestaltSkeletalMeshDefinition", GESTALT_DEF)
    parts = gd.GestaltInfos[0].Parts
    before = len(parts)
    src = next(p for p in parts if str(p.SkeletalMeshFragmentName) == "AR_Barrel_Vladof")
    parts.append(src)
    parts[-1].SkeletalMeshFragmentName = PROBE_FRAG
    after = len(gd.GestaltInfos[0].Parts)
    tail = gd.GestaltInfos[0].Parts[-1]
    b = gd.GestaltPartBounds
    bsrc = next(x for x in b if str(x.SkeletalMeshFragmentName) == "AR_Barrel_Vladof")
    b.append(bsrc)
    b[-1].SkeletalMeshFragmentName = PROBE_FRAG
    return {"parts_before": before, "parts_after": after,
            "tail": {"name": str(tail.SkeletalMeshFragmentName), "first": tail.FirstIndex, "num": tail.NumPrimitives},
            "bounds_after": len(gd.GestaltPartBounds),
            "bounds_tail": str(gd.GestaltPartBounds[-1].SkeletalMeshFragmentName)}


def p5_construct_socket() -> dict[str, Any]:
    mesh = unrealsdk.find_object("SkeletalMesh", GESTALT_MESH)
    before = len(mesh.Sockets)
    tmpl = mesh.Sockets[0]
    s = unrealsdk.construct_object("SkeletalMeshSocket", mesh, f"{PROBE_FRAG}_Muzzle", 0, tmpl)
    s.SocketName = f"{PROBE_FRAG}_Muzzle"
    s.BoneName = "Barrel"
    mesh.Sockets.append(s)
    gd = unrealsdk.find_object("GestaltSkeletalMeshDefinition", GESTALT_DEF)
    sm = gd.GestaltSocketMappings
    sm.append(sm[0])
    sm[-1].SkeletalMeshFragmentName = PROBE_FRAG
    sm[-1].OriginalSocketName = "Muzzle"
    sm[-1].MangledSocketName = f"{PROBE_FRAG}_Muzzle"
    return {"sockets_before": before, "sockets_after": len(mesh.Sockets),
            "tail_socket": str(mesh.Sockets[-1].SocketName), "tail_bone": str(mesh.Sockets[-1].BoneName),
            "mappings_after": len(sm), "tail_mapping": str(sm[-1].MangledSocketName)}


def p6_mesh_native_fields() -> dict[str, Any]:
    mesh = unrealsdk.find_object("SkeletalMesh", GESTALT_MESH)
    out: dict[str, Any] = {}
    for f in ("Bounds", "SkeletalDepth", "bUseFullPrecisionUVs", "LODInfo", "RefSkeleton", "LODModels"):
        try:
            v = getattr(mesh, f)
            out[f] = repr(v)[:300]
        except Exception as ex:  # noqa: BLE001
            out[f] = f"ERR {ex!r}"
    try:
        out["dir"] = [d for d in dir(mesh) if not d.startswith("_")][:150]
    except Exception as ex:  # noqa: BLE001
        out["dir"] = repr(ex)
    return out


def p7_part_lists() -> dict[str, Any]:
    bal = unrealsdk.find_object(
        "WeaponBalanceDefinition", "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_Sherdifier")
    plc = bal.RuntimePartListCollection
    bp = plc.BarrelPartData
    return {"plc": plc._path_name(), "barrel_enabled": bool(bp.bEnabled),
            "barrel_weighted": [str(w.Part._path_name()) if w.Part else None for w in bp.WeightedParts],
            "weighted_struct": bp.WeightedParts[0]._type._path_name() if len(bp.WeightedParts) else None}


def p8_load_new_package() -> dict[str, Any]:
    """Does load_package find a NEW file dropped into CookedPCConsole (unhashed)?"""
    name = _control().get("load_package")
    if not name:
        return {"skipped": True}
    pkg = unrealsdk.load_package(name)
    objs = []
    for o in unrealsdk.find_all("Object", exact=False):
        try:
            p = o._path_name()
        except Exception:  # noqa: BLE001
            continue
        if p.startswith(name + "."):
            objs.append(p)
            if len(objs) >= 40:
                break
    return {"package": pkg._path_name() if pkg else None, "objects": objs}


def run_all() -> None:
    _step("p8_load_new_package", p8_load_new_package)
    _step("p1_packages", p1_packages)
    _step("p2_gestalt_read", p2_gestalt_read)
    _step("p3_construct_part", p3_construct_part)
    _step("p4_append_fragment", p4_append_fragment)
    _step("p5_construct_socket", p5_construct_socket)
    _step("p6_mesh_native_fields", p6_mesh_native_fields)
    _step("p7_part_lists", p7_part_lists)
    R["finished"] = time.time()
    R["sdk"] = str(unrealsdk.__version__)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1, default=str)
    _log(f"wrote {OUT}")


TICKS_BEFORE_RUN = 900  # ~15 s after the viewport starts ticking; main menu is up by then
_ticks = 0


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def on_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _done, _ticks
    if _done:
        return
    _ticks += 1
    if _ticks < TICKS_BEFORE_RUN:
        return
    _done = True
    on_tick.disable()
    ctl = _control()
    if not ctl.get("enabled", True):
        _log("disabled by control file")
        return
    _log("tick threshold reached; running probe")
    try:
        run_all()
    finally:
        if ctl.get("autoexit", False):
            _log("autoexit requested; quitting")
            try:
                pc = get_pc(possibly_loading=True)
                if pc is not None:
                    pc.ConsoleCommand("exit")
                else:
                    _log("no player controller for exit")
            except Exception as ex:  # noqa: BLE001
                _log(f"exit failed: {ex!r}")


build_mod(name="Pipeline Probe (M0)", description="bl2-part-pipeline discovery probe", hooks=[on_tick])
# This mod is a disposable probe: arm the hook regardless of the mod-menu enabled state.
on_tick.enable()
_log("probe armed")
