"""CLI: ``python -m bl2_preflight [--mod NAME] [--part-path P] [--control C] [--baseline B]
[--capture-view V] [--deep] [--dry-run] [--json]`` or ``python -m bl2_preflight dry-run <mod dir>``.

Exit status: 0 GO, 1 GO with warnings, 2 NO GO (or a failed dry run).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bl2_preflight import checks as ck  # noqa: E402
from bl2_preflight.dryrun import dry_run  # noqa: E402

EXIT = {"GO": 0, "GO (with warnings)": 1, "NO GO": 2}


def render(results: list[ck.Check]) -> list[str]:
    lines = []
    width = max((len(c.name) for c in results), default=10)
    for c in results:
        tag = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL", "skip": "skip"}[c.status]
        code = f" [{c.code}]" if c.code else ""
        lines.append(f"  {tag} {c.name:<{width}}  {c.detail}{code}")
        if c.fix and c.status in ("warn", "fail"):
            lines.append(f"       {'':<{width}}  fix: {c.fix}")
    return lines


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "dry-run":
        p = argparse.ArgumentParser(prog="bl2_preflight dry-run")
        p.add_argument("mod_dir", type=Path)
        p.add_argument("--catalog", type=Path, default=None)
        a = p.parse_args(argv[1:])
        r = dry_run(a.mod_dir, a.catalog)
        print("\n".join(r.lines()))
        return 0 if r.ok else 2
    p = argparse.ArgumentParser(prog="bl2_preflight", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mod", default=None, help="sdk_mods folder name of the build under test")
    p.add_argument("--part-path", default=None, help="the --part-path the loop will be given")
    p.add_argument("--control", type=Path, default=None)
    p.add_argument("--baseline", type=Path, default=None)
    p.add_argument("--capture-view", default=None, choices=("first", "third"))
    p.add_argument("--game", type=Path, default=ck.GAME)
    p.add_argument("--deep", action="store_true", help="sha256 Startup.upk against the backup")
    p.add_argument("--dry-run", action="store_true", help="also import the mod against the fake SDK (sdk_mods copy, else sdk_mod/<mod>)")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    ctx = ck.build_context(game=a.game, mod=a.mod, part_path=a.part_path, control=a.control,
                           baseline=a.baseline, capture_view=a.capture_view, deep=a.deep)
    results = ck.run_checks(ctx)
    dry = None
    if a.dry_run and a.mod:
        installed = a.game / "sdk_mods" / a.mod
        target = installed if (installed / "__init__.py").exists() else ck.REPO / "sdk_mod" / a.mod
        dry = dry_run(target)
        results.append(ck.Check("dry run", ck.OK if dry.ok else ck.FAIL,
                                f"{target.name}: " + ("registered clean offline" if dry.ok else "; ".join(dry.problems)[:300]),
                                "" if dry.ok else "fix the spec and re-emit; python -m bl2_preflight dry-run <dir> for the full list"))
    v = ck.verdict(results)
    if a.json:
        print(json.dumps({"verdict": v, "checks": [c.__dict__ for c in results],
                          "dry_run": (dry.__dict__ | {"mod_dir": str(dry.mod_dir)}) if dry else None,
                          "notes": ctx.notes}, indent=1, default=str))
        return EXIT[v]
    print(f"preflight{(' for ' + a.mod) if a.mod else ''}: {v}")
    print("\n".join(render(results)))
    if dry is not None and not dry.ok:
        print("\n".join(dry.lines()))
    for n in ctx.notes:
        print(f"note: {n}")
    return EXIT[v]


if __name__ == "__main__":
    sys.exit(main())
