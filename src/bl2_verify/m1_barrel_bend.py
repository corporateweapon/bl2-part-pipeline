"""M1: same-size vertex edit of one gestalt fragment (brief §D7 standard deformation).

Usage:
    python -m bl2_verify.m1_barrel_bend <in.upk> <out.upk> [--fragment AR_Barrel_Vladof]
        [--y-cut -80] [--dz 8] [--x-scale 1.5] [--revert]

Reads the decompressed package, finds the assault-rifle gestalt mesh, moves every vertex of the
fragment that lies forward of `--y-cut` (the gestalt frame has -Y = muzzle direction) up by `--dz`
and scales its X by `--x-scale`, re-serializes the export IN PLACE (same byte length) and writes
the whole package to <out.upk>. Vertex/triangle counts are unchanged, so nothing else in the
package moves. Prints a JSON summary; exit code 1 on any self-check failure.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bl2_upk import Package, SkeletalMeshExport, replace_export_payload  # noqa: E402

# The fragment table reader and the fragment->vertex-id lookup now live in
# ``bl2_upk.fragment`` (shared with M2's builder and the M4 Blender addon); they
# stay importable from here because M1/M2 callers already use these names.
from bl2_upk.fragment import (  # noqa: E402,F401
    DEFAULT_FRAGMENT_TABLE as FRAG_TABLE,
    GESTALT_AR_MESH as GESTALT_MESH,
    fragment_vertices,
    load_fragments,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("inp")
    ap.add_argument("out")
    ap.add_argument("--fragment", default="AR_Barrel_Vladof")
    ap.add_argument("--y-cut", type=float, default=-80.0)
    ap.add_argument("--dz", type=float, default=8.0)
    ap.add_argument("--x-scale", type=float, default=1.5)
    args = ap.parse_args(argv)

    pkg = Package.from_file(args.inp)
    exp = pkg.find_export(GESTALT_MESH, "SkeletalMesh")
    original = pkg.read_export_bytes(exp)
    mesh = SkeletalMeshExport.parse(original, pkg)
    lod = mesh.lod0
    first, num = load_fragments()[args.fragment]
    verts = fragment_vertices(lod, first, num)

    moved = 0
    for i in verts:
        v = lod.vertices[i]
        if v.y < args.y_cut:
            v.z += args.dz
            v.x *= args.x_scale
            moved += 1

    new = mesh.serialize(exp.off)
    summary = {
        "fragment": args.fragment,
        "fragment_vertices": len(verts),
        "moved": moved,
        "orig_len": len(original),
        "new_len": len(new),
        "same_size": len(new) == len(original),
        "changed_bytes": sum(1 for a, b in zip(original, new) if a != b),
    }
    if not summary["same_size"] or moved == 0:
        print(json.dumps(summary, indent=1))
        return 1
    replace_export_payload(args.inp, GESTALT_MESH, new, args.out, strategy="inplace")

    # re-verify from the written file
    pkg2 = Package.from_file(args.out)
    exp2 = pkg2.find_export(GESTALT_MESH, "SkeletalMesh")
    mesh2 = SkeletalMeshExport.parse(pkg2.read_export_bytes(exp2), pkg2)
    zs = [mesh2.lod0.vertices[i].z for i in verts if mesh.lod0.vertices[i].y < args.y_cut]
    summary["reparsed_ok"] = len(mesh2.lod0.vertices) == len(lod.vertices)
    summary["reparsed_moved_z_min"] = min(zs) if zs else None
    summary["out"] = args.out
    print(json.dumps(summary, indent=1))
    return 0 if summary["reparsed_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
