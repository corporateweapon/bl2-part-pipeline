"""CLI: ``python -m bl2_lint proposal.json [--catalog catalog/parts.json]``.

Prints the report and exits 0 when it holds no errors, 1 otherwise. Run it as
the last step before every patch build and from the verify loop.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bl2_catalog import load_catalog

from . import lint
from .proposal import load_proposal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bl2_lint", description=__doc__)
    parser.add_argument("proposal", help="proposal JSON (docs/LINT_PROPOSAL_SCHEMA.md)")
    parser.add_argument("--catalog", default=None, help="catalog/parts.json (default: repo copy)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    proposal_path = Path(args.proposal)
    if not proposal_path.exists():
        print(f"proposal not found: {proposal_path}", file=sys.stderr)
        return 1
    catalog_path = Path(args.catalog) if args.catalog else None
    if catalog_path is not None and not catalog_path.exists():
        print(f"catalog not found: {catalog_path}", file=sys.stderr)
        return 1
    try:
        catalog = load_catalog(catalog_path)
    except FileNotFoundError:
        print("catalog not found; run `python -m bl2_catalog` first", file=sys.stderr)
        return 1

    report = lint(load_proposal(proposal_path), catalog)
    if args.json:
        print(json.dumps(report.to_dict(), indent=1, sort_keys=True))
    else:
        print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
