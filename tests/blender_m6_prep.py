"""M6 prep proof: three fragments -- two of them brand-new meshes -- in ONE package.

Run it the way the add-on is meant to be automatable::

    blender -noaudio --background --python tests/blender_m6_prep.py -- \
        --out scratch/PipelineMeshes_multi.upk

Phase 1 (inside ``bpy``): import ``AR_Barrel_Vladof`` with the add-on (that is
where the armature, the 38 bones and the sockets come from), build **two
synthetic objects that were never imported** -- a cylinder along -Y weighted
100% to ``Barrel`` and a box weighted to ``Mag``, each with its own ``UV0`` map,
triangulated and bound to that armature -- deform the imported barrel with the
D7 standard deformation, then export all three in one call as the fragments
``PL_Test_Cyl``, ``PL_Test_Box`` and ``AR_Barrel_PL_Bent``.

Phase 2 (no ``bpy``, plain ``bl2_upk``): re-read the written package and check
that the counts add up, that the three fragment ranges tile consecutively onto
the end of the stock index buffer, that every new vertex is weighted to the bone
it was painted with, and that Gildor's umodel -- the only pre-game loader we
have -- loads the result and sees the same vertex count.

Writes a JSON result (``scratch/m6_prep.json`` by default) and exits 0 only if
every check passed.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

SOURCE_PACKAGE = REPO / "scratch" / "decomp" / "Startup.upk"
UMODEL = REPO / "tools" / "gildor" / "umodel_64.exe"

TEMPLATE_FRAGMENT = "AR_Barrel_Vladof"
PACKAGE_NAME = "PipelineMeshes"
MESH_NAME = "PL_AR_Gestalt_Mesh"

#: stock AR gestalt mesh, before anything is appended
STOCK_VERTICES = 27211
STOCK_INDICES = 68085

CYL_FRAGMENT, CYL_BONE = "PL_Test_Cyl", "Barrel"
BOX_FRAGMENT, BOX_BONE = "PL_Test_Box", "Mag"
BENT_FRAGMENT = "AR_Barrel_PL_Bent"

Y_CUT, DZ, X_SCALE = -80.0, 8.0, 1.5


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="blender_m6_prep")
    parser.add_argument("--out", default=str(REPO / "scratch" / "PipelineMeshes_multi.upk"))
    parser.add_argument("--json", default=str(REPO / "scratch" / "m6_prep.json"))
    parser.add_argument("--package", default=str(SOURCE_PACKAGE))
    parser.add_argument("--skip-umodel", action="store_true")
    return parser.parse_args(argv)


def say(text: str = "") -> None:
    print(text, flush=True)


# ---------------------------------------------------------------------------
# phase 1 -- Blender
# ---------------------------------------------------------------------------


def _finish_new_object(obj, arm_obj, bone: str, api) -> None:
    """Give a freshly made primitive everything the exporter needs."""
    import bpy

    mesh = obj.data
    if mesh.uv_layers:
        mesh.uv_layers[0].name = api.UV0_NAME
    else:
        mesh.uv_layers.new(name=api.UV0_NAME)
    group = obj.vertex_groups.new(name=bone)
    group.add(list(range(len(mesh.vertices))), 1.0, "REPLACE")
    api.ensure_triangulated(obj)
    api.apply_transforms(obj)
    obj.parent = arm_obj
    modifier = obj.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = arm_obj
    bpy.context.view_layer.update()


def phase_blender(args, result: dict) -> None:
    import bpy

    from bl2_blender import api

    bpy.ops.wm.read_factory_settings(use_empty=True)
    result["blender"] = bpy.app.version_string
    result["python"] = sys.version.split()[0]

    say("== import the template fragment (armature + sockets) ==")
    imported = api.import_fragment(args.package, api.DEFAULT_MESH_PATH, TEMPLATE_FRAGMENT)
    for check in imported.checks:
        say(f"   {check}")
    result["import"] = imported.to_dict()
    barrel = bpy.data.objects[imported.object_name]
    arm_obj = bpy.data.objects[imported.armature_name]

    say("== build two NEW objects (never imported) ==")
    # a stubby cylinder lying along -Y, out where the muzzle is, weighted to Barrel
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=12, radius=2.0, depth=20.0,
        rotation=(math.radians(90.0), 0.0, 0.0), location=(0.0, -95.0, 6.0),
    )
    cylinder = bpy.context.view_layer.objects.active
    cylinder.name = CYL_FRAGMENT
    _finish_new_object(cylinder, arm_obj, CYL_BONE, api)

    # a box where the magazine is, weighted to Mag
    bpy.ops.mesh.primitive_cube_add(size=6.0, location=(0.0, -30.0, -6.0))
    box = bpy.context.view_layer.objects.active
    box.name = BOX_FRAGMENT
    _finish_new_object(box, arm_obj, BOX_BONE, api)

    for obj, bone in ((cylinder, CYL_BONE), (box, BOX_BONE)):
        say(f"   {obj.name}: {len(obj.data.vertices)} verts / {len(obj.data.polygons)} tris, "
            f"100% {bone}, UV0={api.UV0_NAME in obj.data.uv_layers}")

    say("== validate the new objects ==")
    result["validate_new"] = {}
    for obj in (cylinder, box):
        checks = api.validate_for_export(obj)
        for check in checks:
            say(f"   {obj.name}: {check}")
        result["validate_new"][obj.name] = [c.to_dict() for c in checks]

    say("== standard deformation on the imported barrel ==")
    moved = api.standard_deformation(barrel, Y_CUT, DZ, X_SCALE)
    say(f"   moved {moved} vertices")
    result["moved"] = moved

    say("== export three fragments into one package ==")
    exported = api.export_fragments(
        [cylinder, box, barrel],
        args.out,
        PACKAGE_NAME,
        MESH_NAME,
        [CYL_FRAGMENT, BOX_FRAGMENT, BENT_FRAGMENT],
        [TEMPLATE_FRAGMENT, TEMPLATE_FRAGMENT, TEMPLATE_FRAGMENT],
    )
    for check in exported.checks:
        say(f"   {check}")
    say(json.dumps(exported.to_dict(), indent=1))
    result["export"] = exported.to_dict()
    result["objects"] = {
        CYL_FRAGMENT: {"vertices": len(cylinder.data.vertices),
                       "triangles": len(cylinder.data.polygons), "bone": CYL_BONE},
        BOX_FRAGMENT: {"vertices": len(box.data.vertices),
                       "triangles": len(box.data.polygons), "bone": BOX_BONE},
        BENT_FRAGMENT: {"vertices": len(barrel.data.vertices),
                        "triangles": len(barrel.data.polygons), "bone": None},
    }


def phase_guardrails(args, result: dict) -> list[tuple[str, bool, str]]:
    """A new object that is missing what the exporter needs must be refused."""
    import bpy

    from bl2_blender import api

    checks: list[tuple[str, bool, str]] = []
    guard: dict[str, str] = {}
    say("== guardrails on new geometry ==")

    bpy.ops.mesh.primitive_cube_add(size=4.0, location=(0.0, -20.0, 0.0))
    orphan = bpy.context.view_layer.objects.active
    orphan.name = "PL_Orphan"
    orphan.data.uv_layers[0].name = api.UV0_NAME
    levels = {c.name: c.level for c in api.validate_for_export(orphan)}
    guard["no armature"] = levels.get("armature", "missing")

    arm_obj = bpy.data.objects[result["import"]["armature"]]
    orphan.parent = arm_obj
    guard["no vertex groups"] = {
        c.name: c.level for c in api.validate_for_export(orphan)}.get("bone weights", "missing")

    group = orphan.vertex_groups.new(name="NotABone")
    group.add(list(range(len(orphan.data.vertices))), 1.0, "REPLACE")
    guard["unknown bone name"] = {
        c.name: c.level for c in api.validate_for_export(orphan)}.get("bone weights", "missing")

    orphan.vertex_groups.remove(group)
    good = orphan.vertex_groups.new(name="Mag")
    good.add(list(range(len(orphan.data.vertices))), 1.0, "REPLACE")
    guard["ngons"] = {
        c.name: c.level for c in api.validate_for_export(orphan)}.get("triangulated", "missing")
    api.ensure_triangulated(orphan)
    orphan.data.uv_layers.remove(orphan.data.uv_layers[api.UV0_NAME])
    guard["no UV0"] = {
        c.name: c.level for c in api.validate_for_export(orphan)}.get("UV0", "missing")

    blocked = "not blocked"
    try:
        api.export_fragments([orphan], REPO / "scratch" / "should_not_exist_m6.upk",
                             PACKAGE_NAME, MESH_NAME, ["PL_Orphan"], [TEMPLATE_FRAGMENT])
    except api.ExportBlocked:
        blocked = "blocked"
    guard["export of a broken new object"] = blocked

    orphan.data.uv_layers.new(name=api.UV0_NAME)
    guard["repaired"] = "ok" if all(
        c.level != "error" for c in api.validate_for_export(orphan)) else "still failing"

    for name, level in guard.items():
        say(f"   {name}: {level}")
    result["guardrails"] = guard
    expected = {
        "no armature": "error",
        "no vertex groups": "error",
        "unknown bone name": "error",
        "ngons": "error",
        "no UV0": "error",
        "export of a broken new object": "blocked",
        "repaired": "ok",
    }
    for name, want in expected.items():
        got = guard.get(name)
        checks.append((f"guardrail: {name}", got == want, f"{got} (expected {want})"))
    checks.append(("blocked exports write nothing",
                   not (REPO / "scratch" / "should_not_exist_m6.upk").exists(), ""))
    return checks


# ---------------------------------------------------------------------------
# phase 2 -- bl2_upk / umodel, no bpy
# ---------------------------------------------------------------------------


def read_mesh(package_path: Path):
    from bl2_upk import Package, SkeletalMeshExport

    package = Package.from_file(package_path)
    entry = package.find_export(MESH_NAME, "SkeletalMesh")
    return SkeletalMeshExport.parse(package.read_export_bytes(entry), package)


def run_umodel(out_package: Path, expected_vertices: int) -> dict:
    info: dict = {"ran": False, "ok": False}
    if not UMODEL.exists():
        info["skipped"] = f"{UMODEL} not present"
        return info
    workspace = Path(tempfile.mkdtemp(prefix="bl2_m6_ws_"))
    out_dir = Path(tempfile.mkdtemp(prefix="bl2_m6_out_"))
    deployed = workspace / f"{PACKAGE_NAME}.upk"
    shutil.copyfile(out_package, deployed)
    command = [str(UMODEL), "-export", "-game=border", "-gltf",
               f"-path={workspace}", f"-out={out_dir}", deployed.name, MESH_NAME]
    info["command"] = " ".join(command)
    proc = subprocess.run(command, capture_output=True, text=True, cwd=str(REPO))
    output = (proc.stdout + proc.stderr).strip()
    info["ran"] = True
    info["returncode"] = proc.returncode
    info["output"] = output[-1500:]
    bad = [line for line in output.splitlines()
           if line.lower().startswith("error") or "fatal" in line.lower()]
    gltf = list(out_dir.rglob(f"{MESH_NAME}.gltf"))
    info["gltf"] = str(gltf[0]) if gltf else ""
    if gltf:
        data = json.loads(gltf[0].read_text())
        accessor = next(a for a in data["accessors"] if a.get("type") == "VEC3" and a.get("min"))
        info["gltf_vertices"] = accessor["count"]
        info["gltf_joints"] = len(data["skins"][0]["joints"])
        info["ok"] = (proc.returncode == 0 and not bad
                      and accessor["count"] == expected_vertices)
    info["errors"] = bad
    shutil.rmtree(workspace, ignore_errors=True)
    shutil.rmtree(out_dir, ignore_errors=True)
    return info


def phase_verify(args, result: dict) -> list[tuple[str, bool, str]]:
    out_package = Path(args.out)
    checks: list[tuple[str, bool, str]] = []
    objects = result["objects"]
    fragments = result["export"]["fragments"]

    say("== re-read the written package (bl2_upk, no bpy) ==")
    mesh = read_mesh(out_package)
    lod = mesh.lod0
    checks.append(("package parses", True, str(out_package)))

    expected_vertices = STOCK_VERTICES + sum(o["vertices"] for o in objects.values())
    expected_indices = STOCK_INDICES + 3 * sum(o["triangles"] for o in objects.values())
    say(f"   {len(lod.vertices)} vertices / {len(lod.indices)} indices "
        f"(expected {expected_vertices} / {expected_indices})")
    checks.append(("vertex count adds up", len(lod.vertices) == expected_vertices,
                   f"{len(lod.vertices)} (expected {expected_vertices})"))
    checks.append(("index count adds up", len(lod.indices) == expected_indices,
                   f"{len(lod.indices)} (expected {expected_indices})"))
    checks.append(("under the uint16 vertex ceiling", len(lod.vertices) < 65535,
                   f"{len(lod.vertices)} vertices"))
    result["counts"] = {"vertices": len(lod.vertices), "indices": len(lod.indices)}

    say("== the three fragments tile onto the end of the stock buffer ==")
    cursor = STOCK_INDICES
    tiled = True
    for entry in fragments:
        first, num = entry["first_index"], entry["num_primitives"]
        want = objects[entry["fragment"]]["triangles"]
        say(f"   {entry['fragment']}: [{first}, {first + 3 * num}) "
            f"{num} triangles, {entry['new_vertices']} vertices")
        tiled = tiled and first == cursor and num == want
        cursor = first + 3 * num
    checks.append(("fragments tile consecutively from the stock index count", tiled,
                   f"{STOCK_INDICES} -> {cursor}"))
    checks.append(("the last fragment ends at the index buffer's end",
                   cursor == len(lod.indices), f"{cursor} vs {len(lod.indices)}"))
    checks.append(("every fragment's vertex count matches the Blender object",
                   all(e["new_vertices"] == objects[e["fragment"]]["vertices"]
                       for e in fragments if e["new_geometry"]), ""))

    say("== bone indices of the new geometry ==")
    bone_names = mesh.bone_names
    bones_ok = True
    detail: dict[str, list[str]] = {}
    for entry in fragments:
        if not entry["new_geometry"]:
            continue
        first, num = entry["first_index"], entry["num_primitives"]
        vids = sorted(set(lod.indices[first: first + 3 * num]))
        used = sorted({
            bone_names[lod.chunk_for_vertex_index(vid).bone_map[lod.vertices[vid].bone_idx[slot]]]
            for vid in vids
            for slot in range(4)
            if lod.vertices[vid].bone_wt[slot]
        })
        detail[entry["fragment"]] = used
        want = objects[entry["fragment"]]["bone"]
        say(f"   {entry['fragment']}: {used} (expected [{want!r}])")
        bones_ok = bones_ok and used == [want]
        weights_ok = all(lod.vertices[vid].bone_wt[0] == 255 for vid in vids)
        checks.append((f"{entry['fragment']} weights normalise to 255", weights_ok, ""))
    result["bones"] = detail
    checks.append(("new geometry is weighted to the painted bone", bones_ok, str(detail)))

    if args.skip_umodel:
        say("== umodel (skipped) ==")
        result["umodel"] = {"skipped": "--skip-umodel"}
    else:
        say("== umodel ==")
        info = run_umodel(out_package, len(lod.vertices))
        result["umodel"] = info
        if info.get("skipped"):
            say(f"   skipped: {info['skipped']}")
            checks.append(("umodel loads the package", True, f"skipped ({info['skipped']})"))
        else:
            say(f"   rc={info['returncode']} gltf={info.get('gltf_vertices')} verts, "
                f"{info.get('gltf_joints')} joints")
            checks.append(("umodel loads the package", bool(info["ok"]),
                           f"rc={info['returncode']}, {info.get('gltf_vertices')} vertices, "
                           f"{info.get('gltf_joints')} joints"))

    say("== sidecar ==")
    sidecar = json.loads(Path(result["export"]["sidecar"]).read_text())
    result["sidecar"] = sidecar
    checks.append(("sidecar lists every fragment", len(sidecar.get("fragments", [])) == 3,
                   str(len(sidecar.get("fragments", [])))))
    checks.append(("sidecar keeps the single-fragment keys at the top level",
                   all(k in sidecar for k in ("package", "mesh_path", "fragment",
                                              "template_fragment", "first_index",
                                              "num_primitives", "dz", "part_name",
                                              "reparsed_ok", "out"))
                   and sidecar["fragment"] == fragments[0]["fragment"], ""))
    checks.append(("sidecar carries the vertex budget (lint L9)",
                   sidecar.get("total_vertices") == len(lod.vertices), ""))
    return checks


# ---------------------------------------------------------------------------


def main() -> int:
    args = parse_args()
    started = time.time()
    result: dict = {"out": args.out, "source": args.package}
    say("=" * 72)
    say("M6 prep: three fragments (two brand-new meshes) -> one package -> umodel")
    say("=" * 72)
    if not Path(args.package).exists():
        say(f"source package {args.package} is missing")
        Path(args.json).write_text(json.dumps(
            {"ok": False, "error": f"missing {args.package}"}, indent=1))
        return 2

    phase_blender(args, result)
    guardrail_checks = phase_guardrails(args, result)
    checks = phase_verify(args, result)
    checks.extend(guardrail_checks)

    checks.insert(0, ("import checks", bool(result["import"]["ok"]),
                      f"{len(result['import']['checks'])} checks"))
    checks.insert(1, ("export checks", bool(result["export"]["ok"]), ""))
    checks.insert(2, ("new objects validate clean",
                      all(c["level"] != "error"
                          for checks_ in result["validate_new"].values() for c in checks_), ""))
    checks.append(("source package untouched (F12)",
                   Path(args.package).resolve() != Path(args.out).resolve(), ""))

    say()
    say("== result ==")
    ok = True
    for name, passed, detail in checks:
        ok = ok and passed
        say(f"   [{'PASS' if passed else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    result["checks"] = [{"name": n, "ok": bool(p), "detail": d} for n, p, d in checks]
    result["ok"] = ok
    result["seconds"] = round(time.time() - started, 2)
    Path(args.json).write_text(json.dumps(result, indent=1))
    say(f"   -> {args.json}  ({result['seconds']} s)  {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    code = main()
    sys.exit(code)
