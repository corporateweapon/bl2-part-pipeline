"""CLI: ``python -m bl2_catalog`` -- (re)build ``catalog/parts.json``.

    python -m bl2_catalog                      # write catalog/parts.json, print a summary
    python -m bl2_catalog --out /tmp/x.json    # write elsewhere
    python -m bl2_catalog --check              # build in memory, fail on a bad tiling
    python -m bl2_catalog --scratch <dir>      # read the inputs from another scratch dir

Exit code 0 on success, 1 if an input is missing or ``--check`` finds a problem.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .builder import build_catalog
from .sources import Sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bl2_catalog", description=__doc__)
    parser.add_argument("--out", default=None, help="output path (default catalog/parts.json)")
    parser.add_argument("--scratch", default=None, help="directory holding the generated inputs")
    parser.add_argument("--root", default=None, help="repository root")
    parser.add_argument("--check", action="store_true", help="do not write; verify tiling only")
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = parser.parse_args(argv)

    sources = Sources.locate(root=args.root, scratch=args.scratch)
    try:
        catalog = build_catalog(sources, out_path=args.out, write=not args.check)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    bad = sorted(
        key for key, wt in catalog["weapon_types"].items() if not wt["tiling"]["ok"]
    )
    summary = {
        **catalog["meta"]["counts"],
        "load_order_source": catalog["meta"]["load_order_source"],
        "bad_tiling": bad,
    }
    if not args.check:
        target = Path(args.out) if args.out else sources.root / "catalog" / "parts.json"
        summary["out"] = str(target)
        summary["bytes"] = target.stat().st_size

    if args.json:
        print(json.dumps(summary, indent=1, sort_keys=True))
    else:
        for key, value in sorted(summary.items()):
            print(f"{key:22s} {value}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
