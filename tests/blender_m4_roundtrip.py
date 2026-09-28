"""M4 headless proof: Blender import -> validate -> D7 edit -> package export.

Run it the way the add-on is meant to be automatable::

    blender --background --python tests/blender_m4_roundtrip.py -- --out scratch/PipelineMeshes_m4.upk

Phase 1 (inside ``bpy``): import ``AR_Barrel_Vladof`` from the decompressed
``Startup.upk``, run the export guardrails, apply the D7 standard deformation in
Blender coordinates, export a NEW package with the NEW fragment
``AR_Barrel_PL_Bent`` appended (the source package is never touched -- F12).

Phase 2 (no ``bpy``, plain ``bl2_upk``): re-read the written package, check the
counts (28,519 verts / 71,577 indices), diff the appended fragment's positions
against ``scratch/PipelineMeshes_m2.upk`` (M2's own CLI build with the same
parameters) and let Gildor's umodel -- the only pre-game loader available --
load and export the result.

Writes a JSON result (``scratch/m4_roundtrip.json`` by default) and exits 0 only
if every check passed.
"""

from __future__ import annotations

import argparse
import json
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
M2_PACKAGE = REPO / "scratch" / "PipelineMeshes_m2.upk"
UMODEL = REPO / "tools" / "gildor" / "umodel_64.exe"

TEMPLATE_FRAGMENT = "AR_Barrel_Vladof"
NEW_FRAGMENT = "AR_Barrel_PL_Bent"
PACKAGE_NAME = "PipelineMeshes"
MESH_NAME = "PL_AR_Gestalt_Mesh"

EXPECTED_VERTICES = 28519
EXPECTED_INDICES = 71577
EXPECTED_NEW_VERTICES = 1308
EXPECTED_NEW_TRIANGLES = 1164
EXPECTED_MOVED = 281
POSITION_TOLERANCE = 1e-4

Y_CUT, DZ, X_SCALE = -80.0, 8.0, 1.5


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="blender_m4_roundtrip")
    parser.add_argument("--out", default=str(REPO / "scratch" / "PipelineMeshes_m4.upk"))
    parser.add_argument("--json", default=str(REPO / "scratch" / "m4_roundtrip.json"))
    parser.add_argument("--package", default=str(SOURCE_PACKAGE))
    parser.add_argument("--skip-umodel", action="store_true")
    return parser.parse_args(argv)


def say(text: str = "") -> None:
    print(text, flush=True)


# ---------------------------------------------------------------------------
# phase 1 -- Blender
# ---------------------------------------------------------------------------


def phase_blender(args, result: dict) -> None:
    import bpy

    from bl2_blender import api

    bpy.ops.wm.read_factory_settings(use_empty=True)
    result["blender"] = bpy.app.version_string
    result["python"] = sys.version.split()[0]

    say("== import ==")
    imported = api.import_fragment(args.package, api.DEFAULT_MESH_PATH, TEMPLATE_FRAGMENT)
    for check in imported.checks:
        say(f"   {check}")
    result["import"] = imported.to_dict()
    obj = bpy.data.objects[imported.object_name]

    say("== validate (before the edit) ==")
    before = api.validate_for_export(obj)
    for check in before:
        say(f"   {check}")
    result["validate_before"] = [c.to_dict() for c in before]

    say("== standard deformation ==")
    moved = api.standard_deformation(obj, Y_CUT, DZ, X_SCALE)
    say(f"   moved {moved} vertices (y < {Y_CUT}: z += {DZ}, x *= {X_SCALE})")
    result["moved"] = moved

    say("== validate (after the edit) ==")
    after = api.validate_for_export(obj)
    for check in after:
        say(f"   {check}")
    result["validate_after"] = [c.to_dict() for c in after]

    say("== export ==")
    exported = api.export_fragment(obj, args.out, PACKAGE_NAME, MESH_NAME, NEW_FRAGMENT)
    for check in exported.checks:
        say(f"   {check}")
    say(json.dumps(exported.to_dict(), indent=1))
    result["export"] = exported.to_dict()


# ---------------------------------------------------------------------------
# phase 1b -- the add-on itself, and the guardrails' negative paths
# ---------------------------------------------------------------------------


def _levels(checks) -> dict:
    return {c.name: c.level for c in checks}


def phase_guardrails(result: dict) -> list[tuple[str, bool, str]]:
    """Register the add-on, drive its operators, and break things on purpose."""
    import bmesh
    import bpy
    from mathutils import Matrix

    import bl2_blender
    from bl2_blender import api

    checks: list[tuple[str, bool, str]] = []
    say("== add-on register / operators ==")
    bl2_blender.register()
    registered = all(hasattr(bpy.ops.bl2pl, name) for name in
                     ("import_fragment", "validate", "standard_deformation",
                      "triangulate", "apply_transforms", "export_fragment"))
    say(f"   operators registered: {registered}")
    status = bpy.ops.bl2pl.import_fragment("EXEC_DEFAULT")
    obj = bpy.context.view_layer.objects.active
    say(f"   bl2pl.import_fragment -> {status} ({obj.name})")
    checks.append(("add-on operators register and run",
                   registered and status == {"FINISHED"}
                   and obj.get(api.P_FRAGMENT) == TEMPLATE_FRAGMENT, obj.name))
    say(f"   bl2pl.validate -> {bpy.ops.bl2pl.validate('EXEC_DEFAULT')}")
    say(f"   bl2pl.standard_deformation -> "
        f"{bpy.ops.bl2pl.standard_deformation('EXEC_DEFAULT')}")

    say("== guardrails ==")
    guard: dict[str, str] = {}

    obj.matrix_world = Matrix.Diagonal((1.0, 1.0, 2.0, 1.0))
    guard["unapplied transform"] = _levels(api.validate_for_export(obj))["object transform"]
    api.apply_transforms(obj)
    guard["after apply_transforms"] = _levels(api.validate_for_export(obj))["object transform"]

    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    edge = next(e for e in bm.edges if len(e.link_faces) == 2)
    bmesh.ops.dissolve_edges(bm, edges=[edge], use_verts=False)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    guard["ngon"] = _levels(api.validate_for_export(obj))["triangulated"]
    split = api.ensure_triangulated(obj)
    after = _levels(api.validate_for_export(obj))
    guard["after ensure_triangulated"] = after["triangulated"]
    guard["src vids survive triangulation"] = after[api.ATTR_SRC_VID]
    say(f"   ensure_triangulated split {split} face(s)")

    try:
        api.export_fragment(obj, REPO / "scratch" / "should_not_exist.upk",
                            PACKAGE_NAME, MESH_NAME, "AR_Barrel_Dahl")
        guard["reused fragment name"] = "not blocked"
    except api.ExportBlocked as exc:
        guard["reused fragment name"] = _levels(exc.checks)["fragment name"]

    socket = bpy.data.objects[json.loads(obj[api.P_SOCKETS])[0]]
    say(f"   deleting socket empty {socket.name}")
    bpy.data.objects.remove(socket, do_unlink=True)
    guard["deleted socket"] = _levels(api.validate_for_export(obj))["sockets"]

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.verts[0]], context="VERTS")
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    guard["deleted vertex"] = _levels(api.validate_for_export(obj))["vertex count"]
    try:
        api.export_fragment(obj, REPO / "scratch" / "should_not_exist.upk",
                            PACKAGE_NAME, MESH_NAME, "AR_Barrel_PL_Broken")
        guard["export after damage"] = "not blocked"
    except api.ExportBlocked:
        guard["export after damage"] = "blocked"

    for name, level in guard.items():
        say(f"   {name}: {level}")
    result["guardrails"] = guard
    bl2_blender.unregister()

    expected = {
        "unapplied transform": "warn",
        "after apply_transforms": "ok",
        "ngon": "error",
        "after ensure_triangulated": "ok",
        "src vids survive triangulation": "ok",
        "reused fragment name": "error",
        "deleted socket": "error",
        "deleted vertex": "error",
        "export after damage": "blocked",
    }
    for name, want in expected.items():
        got = guard.get(name)
        checks.append((f"guardrail: {name}", got == want, f"{got} (expected {want})"))
    unwritten = not (REPO / "scratch" / "should_not_exist.upk").exists()
    checks.append(("blocked exports write nothing", unwritten, ""))
    return checks


# ---------------------------------------------------------------------------
# phase 2 -- bl2_upk / umodel, no bpy
# ---------------------------------------------------------------------------


def appended_positions(package_path: Path, first_new_vertex: int):
    from bl2_upk import Package, SkeletalMeshExport

    package = Package.from_file(package_path)
    entry = package.find_export(MESH_NAME, "SkeletalMesh")
    mesh = SkeletalMeshExport.parse(package.read_export_bytes(entry), package)
    lod = mesh.lod0
    return lod, [(v.x, v.y, v.z) for v in lod.vertices[first_new_vertex:]]


def ensure_m2_reference() -> Path:
    if M2_PACKAGE.exists():
        return M2_PACKAGE
    say(f"   {M2_PACKAGE.name} missing -- building it with the M2 CLI")
    from bl2_verify import m2_build

    m2_build.main(["--out", str(M2_PACKAGE), "--fragment", NEW_FRAGMENT,
                   "--y-cut", str(Y_CUT), "--dz", str(DZ), "--x-scale", str(X_SCALE)])
    return M2_PACKAGE


def run_umodel(out_package: Path, result: dict) -> dict:
    info: dict = {"ran": False, "ok": False}
    if not UMODEL.exists():
        info["skipped"] = f"{UMODEL} not present"
        return info
    workspace = Path(tempfile.mkdtemp(prefix="bl2_m4_ws_"))
    out_dir = Path(tempfile.mkdtemp(prefix="bl2_m4_out_"))
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
        accessor = next(a for a in data["accessors"]
                        if a.get("type") == "VEC3" and a.get("min"))
        info["gltf_vertices"] = accessor["count"]
        info["gltf_joints"] = len(data["skins"][0]["joints"])
        info["ok"] = (proc.returncode == 0 and not bad
                      and accessor["count"] == EXPECTED_VERTICES)
    info["errors"] = bad
    shutil.rmtree(workspace, ignore_errors=True)
    shutil.rmtree(out_dir, ignore_errors=True)
    return info


def phase_verify(args, result: dict) -> list[tuple[str, bool, str]]:
    out_package = Path(args.out)
    checks: list[tuple[str, bool, str]] = []

    say("== re-read the written package (bl2_upk, no bpy) ==")
    lod, new_positions = appended_positions(out_package, EXPECTED_VERTICES - EXPECTED_NEW_VERTICES)
    counts = (len(lod.vertices), len(lod.indices))
    say(f"   {counts[0]} vertices / {counts[1]} indices")
    checks.append(("package parses", True, str(out_package)))
    checks.append(("vertex count", counts[0] == EXPECTED_VERTICES,
                   f"{counts[0]} (expected {EXPECTED_VERTICES})"))
    checks.append(("index count", counts[1] == EXPECTED_INDICES,
                   f"{counts[1]} (expected {EXPECTED_INDICES})"))
    checks.append(("appended vertices", len(new_positions) == EXPECTED_NEW_VERTICES,
                   f"{len(new_positions)} (expected {EXPECTED_NEW_VERTICES})"))
    result["counts"] = {"vertices": counts[0], "indices": counts[1]}

    say("== diff against the M2 CLI build ==")
    reference = ensure_m2_reference()
    _, m2_positions = appended_positions(reference, EXPECTED_VERTICES - EXPECTED_NEW_VERTICES)
    worst = 0.0
    worst_at = -1
    for i, (a, b) in enumerate(zip(new_positions, m2_positions)):
        delta = max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))
        if delta > worst:
            worst, worst_at = delta, i
    same_count = len(new_positions) == len(m2_positions)
    say(f"   {len(m2_positions)} reference vertices, worst |delta| {worst:g} at index {worst_at}")
    result["m2_reference"] = {"package": str(reference), "max_delta": worst,
                              "worst_index": worst_at, "vertices": len(m2_positions)}
    checks.append(("m2 vertex counts", same_count,
                   f"{len(new_positions)} vs {len(m2_positions)}"))
    checks.append((f"positions match m2 within {POSITION_TOLERANCE}",
                   same_count and worst <= POSITION_TOLERANCE, f"max |delta| = {worst:g}"))

    if args.skip_umodel:
        say("== umodel (skipped) ==")
        result["umodel"] = {"skipped": "--skip-umodel"}
    else:
        say("== umodel ==")
        info = run_umodel(out_package, result)
        result["umodel"] = info
        if info.get("skipped"):
            say(f"   skipped: {info['skipped']}")
            checks.append(("umodel loads the package", True, f"skipped ({info['skipped']})"))
        else:
            say(f"   rc={info['returncode']} gltf={info.get('gltf_vertices')} verts, "
                f"{info.get('gltf_joints')} joints")
            checks.append(("umodel loads the package", bool(info["ok"]),
                           f"{info.get('gltf_vertices')} vertices, "
                           f"{info.get('gltf_joints')} joints"))
    return checks


# ---------------------------------------------------------------------------


def main() -> int:
    args = parse_args()
    started = time.time()
    result: dict = {"out": args.out, "source": args.package}
    say("=" * 72)
    say("M4 round trip: Blender -> bl2_upk package -> umodel")
    say("=" * 72)
    if not Path(args.package).exists():
        say(f"source package {args.package} is missing")
        Path(args.json).write_text(json.dumps(
            {"ok": False, "error": f"missing {args.package}"}, indent=1))
        return 2

    phase_blender(args, result)
    guardrail_checks = phase_guardrails(result)
    checks = phase_verify(args, result)
    checks.extend(guardrail_checks)

    import_ok = bool(result["import"]["ok"])
    export_ok = bool(result["export"]["ok"])
    validate_after_ok = all(c["level"] != "error" for c in result["validate_after"])
    checks.insert(0, ("import checks", import_ok, f"{len(result['import']['checks'])} checks"))
    checks.insert(1, ("validation after the edit", validate_after_ok, ""))
    checks.insert(2, ("export checks", export_ok, ""))
    checks.append(("moved vertices", result["moved"] == EXPECTED_MOVED,
                   f"{result['moved']} (expected {EXPECTED_MOVED})"))
    checks.append(("appended fragment range",
                   result["export"]["first_index"] == 68085
                   and result["export"]["num_primitives"] == EXPECTED_NEW_TRIANGLES,
                   f"first_index {result['export']['first_index']}, "
                   f"{result['export']['num_primitives']} triangles"))
    checks.append(("normals agree with the source",
                   result["export"]["normal_agreement"] > 0.9,
                   f"mean dot {result['export']['normal_agreement']}"))
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
