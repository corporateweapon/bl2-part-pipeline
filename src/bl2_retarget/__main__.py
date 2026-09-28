"""CLI for the recipe-driven retarget.

    python -m bl2_retarget run recipes/awp.json [--measure-only] [--scale S] [--out P] [--skip-render]
    python -m bl2_retarget run recipes/ak47.json recipes/boxgun.json --out scratch/PipelineMeshes_ak47.upk   # package group
    python -m bl2_retarget apply-spec scratch/awp_retarget_report.json specs/awp.json [specs/awp_harness.json ...]
    python -m bl2_retarget check recipes/awp.json          # validate the recipe, no Blender
    python -m bl2_retarget summarize scratch/awp_retarget_report.json
    python -m bl2_retarget label scratch/awp_retarget_report.json    # redraw the labelled previews

``run`` spawns Blender (``--blender`` or the default install) on ``engine.py`` unless it is
already inside Blender. ``apply-spec`` fills each spec's ``fragments[]`` (by name) with what
it lacks from the report's ``spec_fragments`` -- a missing ``bounds_override``, a socket the
spec has no override for -- and *reports* where a value the spec already holds differs (those
are hand-tuned design decisions: M8's raised sight sockets, M9's EyeSocket2). ``--overwrite``
replaces them.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bl2_retarget.recipe import REPO, RecipeError, load_recipe  # noqa: E402

DEFAULT_BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")
ENGINE = Path(__file__).with_name("engine.py")


def cmd_check(a: argparse.Namespace) -> int:
    try:
        r = load_recipe(a.recipe)
    except (RecipeError, OSError, json.JSONDecodeError) as ex:
        print(f"recipe invalid: {ex}")
        return 2
    glb = r.resolve(r.source["glb"])
    print(f"recipe {r.name}: {len(r.fragments)} fragment(s) ({len(r.exported)} exported), {len(r.sockets)} socket rule(s), "
          f"{len(r.checks)} check(s); host {r.host['mesh_path']}; frame {r.frame().get('note')}")
    print(f"  glb {'present' if glb and glb.exists() else 'MISSING'}: {glb}")
    for f in r.fragments:
        print(f"  {f.name:<12} <- {f.template:<18} rule={f.rule if isinstance(f.rule, str) else json.dumps(f.rule)} bone={f.bone} export={f.export}")
    return 0 if glb and glb.exists() else 1


def cmd_run(a: argparse.Namespace) -> int:
    try:
        import bpy  # noqa: F401
        inside = True
    except ImportError:
        inside = False
    args: list[str] = []
    for rp in [a.recipe, *a.more]:
        args += ["--recipe", str(Path(rp).resolve())]
    if a.out:
        args += ["--out", str(a.out)]
    for rp in (a.report or []):
        args += ["--report", str(rp)]
    if a.package_name:
        args += ["--package-name", a.package_name]
    if a.mesh_name:
        args += ["--mesh-name", a.mesh_name]
    if a.scale is not None:
        args += ["--scale", str(a.scale)]
    for flag in ("measure_only", "skip_render", "with_extras"):
        if getattr(a, flag):
            args.append("--" + flag.replace("_", "-"))
    if inside:
        from bl2_retarget import engine
        sys.argv = [sys.argv[0], "--", *args]
        return engine.main()
    blender = Path(a.blender)
    if not blender.exists():
        print(f"Blender not found at {blender}; pass --blender")
        return 2
    cmd = [str(blender), "-noaudio", "--background", "--python", str(ENGINE), "--", *args]
    print("$ " + " ".join(cmd), flush=True)
    res = subprocess.run(cmd, cwd=str(REPO), check=False)
    if res.returncode == 0:
        recipes = [load_recipe(rp) for rp in [a.recipe, *a.more]]
        paths = [Path(rp) for rp in a.report] if a.report else [
            r.out_path("report", f"scratch/{r.name.lower()}_retarget_report.json") for r in recipes]
        for report_path in paths:
            try:
                from bl2_retarget.overlay import apply_report
                for path in apply_report(report_path):
                    print(f"   labelled -> {path}", flush=True)
            except Exception as ex:  # noqa: BLE001 -- the build stands; only the picture failed
                print(f"   labelled overlay failed: {ex!r}", flush=True)
    return res.returncode


def _close(a: Any, b: Any, tol: float = 0.011) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tol
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return all(_close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict) and set(a) == set(b):
        return all(_close(a[k], b[k], tol) for k in a)
    return a == b


def apply_spec(report: dict, spec_path: Path, overwrite: bool = False) -> list[str]:
    """Merge the report's ``spec_fragments`` into a spec's ``fragments[]`` by name.

    Only what the spec **lacks** is written (a fragment's missing ``bounds_override``, a socket
    missing from its ``socket_overrides``). Values the spec already holds are design decisions
    -- M8 raised the AK's sight sockets by hand and put its sight line through the hip camera,
    M9 hand-placed the AWP's EyeSocket2 -- so a differing value is *reported*, not replaced,
    unless ``overwrite`` is set. Returns one line per change or difference.
    """
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    by_name = {f["name"]: f for f in report.get("spec_fragments") or []}
    lines: list[str] = []
    changed = False
    for frag in spec.get("fragments") or []:
        src = by_name.get(frag.get("name"))
        if src is None:
            continue
        name = frag["name"]
        if src.get("template_fragment") and frag.get("template_fragment") != src["template_fragment"]:
            if overwrite or not frag.get("template_fragment"):
                frag["template_fragment"] = src["template_fragment"]
                lines.append(f"{name}.template_fragment <- {src['template_fragment']}")
                changed = True
            else:
                lines.append(f"{name}.template_fragment differs (kept {frag['template_fragment']!r}; retarget says {src['template_fragment']!r})")
        for sock, val in (src.get("socket_overrides") or {}).items():
            have = (frag.get("socket_overrides") or {}).get(sock)
            if have is None:
                frag.setdefault("socket_overrides", {})[sock] = val
                lines.append(f"{name}.socket_overrides.{sock} <- {val}")
                changed = True
            elif not _close(have, val):
                if overwrite:
                    frag["socket_overrides"][sock] = val
                    lines.append(f"{name}.socket_overrides.{sock} <- {val} (was {have})")
                    changed = True
                else:
                    lines.append(f"{name}.socket_overrides.{sock} differs (kept {have}; retarget says {val})")
        b = src.get("bounds_override")
        if b is not None:
            have = frag.get("bounds_override")
            if have is None:
                frag["bounds_override"] = b
                lines.append(f"{name}.bounds_override <- origin {b['origin']} extent {b['extent']}")
                changed = True
            elif not _close(have, b):
                if overwrite:
                    frag["bounds_override"] = b
                    lines.append(f"{name}.bounds_override <- origin {b['origin']} extent {b['extent']} (replaced)")
                    changed = True
                else:
                    lines.append(f"{name}.bounds_override differs (kept origin {have.get('origin')}; retarget says {b['origin']})")
    missing = [n for n in by_name if n not in {f.get("name") for f in spec.get("fragments") or []}]
    if changed:
        spec_path.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")
    if missing:
        lines.append(f"(not in spec, add by hand: {', '.join(missing)})")
    return lines


def cmd_apply(a: argparse.Namespace) -> int:
    report = json.loads(Path(a.report).read_text(encoding="utf-8"))
    if not report.get("spec_fragments"):
        print("report has no spec_fragments (measure-only run, or an old script's report)")
        return 2
    for sp in a.specs:
        lines = apply_spec(report, Path(sp), overwrite=a.overwrite)
        print(f"{sp}: " + ("already up to date" if not lines else ""))
        for line in lines:
            print(f"  {line}")
    return 0


def cmd_label(a: argparse.Namespace) -> int:
    from bl2_retarget.overlay import apply_report
    written = apply_report(Path(a.report))
    print("\n".join(written) if written else "nothing to label (no plan, or already applied)")
    return 0


def cmd_summarize(a: argparse.Namespace) -> int:
    r = json.loads(Path(a.report).read_text(encoding="utf-8"))
    print(f"retarget {r.get('name')} ok={r.get('ok')} measure_only={r.get('measure_only', False)} "
          f"elapsed={r.get('elapsed_s')}s -> {r.get('out')}")
    pl = r.get("placement") or {}
    print(f"  placement: scale={pl.get('scale')} anchor={pl.get('anchor')} muzzle tip {pl.get('muzzle_tip')} "
          f"vs socket {pl.get('muzzle_socket')} (dy={pl.get('muzzle_distance_y')}, scale to reach={pl.get('scale_that_would_reach_socket')})")
    for c in r.get("frame_checks") or []:
        print(f"  [{c['status']}] {c['name']}: {c['detail']}")
    fr = (r.get("fragments") or {}).get("fragments") or {}
    for name, st in fr.items():
        print(f"  {name:<12} {st['vertices']:>6} v {st['triangles']:>6} t bones={st['bones']}")
    for name, socks in (r.get("socket_overrides") or {}).items():
        print(f"  sockets {name}: " + ", ".join(f"{k}={v}" for k, v in socks.items()))
    for n in r.get("socket_notes") or []:
        print(f"  note: {n}")
    ex = r.get("export") or {}
    if ex:
        print(f"  export ok={ex.get('ok')} total_vertices={ex.get('total_vertices')} indices={ex.get('total_indices')}")
    lab = r.get("labelled") or {}
    for view, img in (lab.get("images") or {}).items():
        print(f"  labelled {view}: {img['path']}" + ("" if lab.get("applied") else "  (overlay not applied: python -m bl2_retarget label <report>)"))
    m = r.get("measure") or {}
    if m.get("y_valleys"):
        print("  y valleys (cut candidates): " + ", ".join(f"[{v['y_from']:.0f},{v['y_to']:.0f}) {v['vertices']}v" for v in m["y_valleys"][:6]))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bl2_retarget", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("recipe")
    c.set_defaults(fn=cmd_check)
    r = sub.add_parser("run")
    r.add_argument("recipe")
    r.add_argument("more", nargs="*", help="further recipes on the same host: one package group, first keeps its ranges")
    r.add_argument("--blender", default=str(DEFAULT_BLENDER))
    r.add_argument("--out", default=None)
    r.add_argument("--report", action="append", default=None, help="one per recipe, in order")
    r.add_argument("--package-name", default=None)
    r.add_argument("--mesh-name", default=None)
    r.add_argument("--scale", type=float, default=None)
    r.add_argument("--measure-only", action="store_true")
    r.add_argument("--skip-render", action="store_true")
    r.add_argument("--with-extras", action="store_true")
    r.set_defaults(fn=cmd_run)
    ap = sub.add_parser("apply-spec")
    ap.add_argument("report")
    ap.add_argument("specs", nargs="+")
    ap.add_argument("--overwrite", action="store_true", help="replace values the spec already holds (default: fill missing, report differences)")
    ap.set_defaults(fn=cmd_apply)
    s = sub.add_parser("summarize")
    s.add_argument("report")
    s.set_defaults(fn=cmd_summarize)
    lb = sub.add_parser("label", help="draw the cut lines/legend onto a report's labelled renders (done by run automatically)")
    lb.add_argument("report")
    lb.set_defaults(fn=cmd_label)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
