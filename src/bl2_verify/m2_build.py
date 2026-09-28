"""M2: emit a package whose gestalt-mesh clone carries one NEW fragment.

The new fragment is a copy of `--template-fragment` (default AR_Barrel_Vladof) with the D7
deformation applied, appended as extra vertices + triangles at the end of LOD0. The original
fragments are untouched, so stock and new barrels coexist in one mesh. Writes the package and a
JSON sidecar describing the new fragment's index range (read by the verify mod's control file).

The mechanics live in `bl2_upk.fragment` (shared with the M4 Blender exporter); this module is
the CLI around them.

Usage: python -m bl2_verify.m2_build [--out scratch/PipelineMeshes_m2.upk] [--fragment AR_Barrel_PL_Bent]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from bl2_upk import SkeletalMeshExport  # noqa: E402
from bl2_upk.fragment import (  # noqa: E402
    GESTALT_AR_MESH,
    build_fragment_package,
    standard_deformation_edit,
)
from bl2_upk.fragment import append_fragment as _append_fragment  # noqa: E402

SRC_PKG = ROOT / "scratch" / "decomp" / "Startup.upk"
GESTALT_MESH = GESTALT_AR_MESH


def append_fragment(mesh: SkeletalMeshExport, first: int, num: int,
                    y_cut: float, dz: float, x_scale: float) -> dict:
    """Append `first`/`num`'s vertices+triangles with the D7 deformation applied.

    Kept as the historical M1/M2 call shape; `bl2_upk.fragment.append_fragment`
    is the general version (any per-vertex edit, used by the Blender exporter).
    """
    return _append_fragment(mesh, first, num, standard_deformation_edit(y_cut, dz, x_scale))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "scratch" / "PipelineMeshes_m2.upk"))
    ap.add_argument("--package-name", default="PipelineMeshes")
    ap.add_argument("--mesh-name", default="PL_AR_Gestalt_Mesh")
    ap.add_argument("--fragment", default="AR_Barrel_PL_Bent")
    ap.add_argument("--template-fragment", default="AR_Barrel_Vladof")
    ap.add_argument("--y-cut", type=float, default=-80.0)
    ap.add_argument("--dz", type=float, default=8.0)
    ap.add_argument("--x-scale", type=float, default=1.5)
    args = ap.parse_args(argv)

    result = build_fragment_package(
        args.out,
        args.fragment,
        source_package=SRC_PKG,
        source_mesh=GESTALT_MESH,
        package_name=args.package_name,
        mesh_name=args.mesh_name,
        template_fragment=args.template_fragment,
        edit=standard_deformation_edit(args.y_cut, args.dz, args.x_scale),
        dz=args.dz,
    )
    print(json.dumps(result, indent=1))
    return 0 if result["reparsed_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
