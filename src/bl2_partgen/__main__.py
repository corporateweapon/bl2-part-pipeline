"""CLI: ``python -m bl2_partgen spec.json --out sdk_mod/<name>``.

Exit codes: 0 emitted (and installed, if asked), 1 refused or failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bl2_catalog import load_catalog

from .emit import ArmoryConflict, EmitRefused, emit, emit_armory, lint_spec
from .install import InstallRefused, install, install_armory
from .resolve import ResolveError, resolve
from .spec import SpecError, is_armory, load_armory, load_spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bl2_partgen", description=__doc__)
    parser.add_argument("spec", help="spec JSON (docs/PARTGEN_SPEC.md)")
    parser.add_argument("--out", default=None, help="output folder (default: sdk_mod/<mod name>)")
    parser.add_argument("--catalog", default=None, help="catalog/parts.json (default: repo copy)")
    parser.add_argument(
        "--sidecar",
        default=None,
        metavar="FRAGMENT_JSON",
        help="a .fragment.json from the package build; its fragment ranges win over the spec's",
    )
    parser.add_argument("--force", action="store_true", help="emit even if the linter errors")
    parser.add_argument("--lint-only", action="store_true", help="lint the spec, write nothing")
    parser.add_argument("--install", default=None, metavar="GAME_DIR", help="install afterwards")
    parser.add_argument("--replace", action="store_true", help="overwrite a differing package")
    parser.add_argument(
        "--no-clean", action="store_true", help="keep files already in the output folder"
    )
    args = parser.parse_args(argv)

    spec_path = Path(args.spec)
    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        return 1
    try:
        catalog = load_catalog(Path(args.catalog) if args.catalog else None)
    except FileNotFoundError:
        print("catalog not found; run `python -m bl2_catalog` first", file=sys.stderr)
        return 1

    try:
        with spec_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if is_armory(raw):
            return _main_armory(args, spec_path, catalog)
        spec = load_spec(spec_path)
    except SpecError as ex:
        print(f"bad spec: {ex}", file=sys.stderr)
        return 1

    try:
        if args.lint_only:
            results = lint_spec(spec, catalog, resolve(spec, catalog, args.sidecar))
            for result in results:
                print(f"=== {result.part_name} ===")
                print(result.report.render())
            return 0 if all(r.ok for r in results) else 1

        out_dir = Path(args.out) if args.out else Path("sdk_mod") / spec.name
        result = emit(
            spec,
            out_dir,
            catalog=catalog,
            force=args.force,
            clean=not args.no_clean,
            spec_name=str(spec_path),
            sidecar=args.sidecar,
        )
    except (ResolveError, SpecError) as ex:
        print(f"cannot resolve the spec against the catalog: {ex}", file=sys.stderr)
        return 1
    except EmitRefused as ex:
        print(str(ex), file=sys.stderr)
        print("(pass --force to emit anyway)", file=sys.stderr)
        return 1

    for note in result.notes:
        print(f"note: {note}")
    for warning in result.lint_warnings:
        print(f"lint warning: {warning}")
    if result.forced:
        print("FORCED: emitted with lint errors outstanding:", file=sys.stderr)
        for error in result.lint_errors:
            print(f"  {error}", file=sys.stderr)
    print(f"emitted {result.name} -> {result.out_dir}")
    for path in result.files:
        print(f"  {path}")

    if args.install:
        try:
            installed = install(result, args.install, replace=args.replace, catalog=catalog)
        except InstallRefused as ex:
            print(f"install refused: {ex}", file=sys.stderr)
            return 1
        for note in installed.notes:
            print(f"install note: {note}")
        print(f"installed -> {installed.mod_dir}")
        if installed.package_dest:
            print(f"package    -> {installed.package_dest}")
    return 0


def _main_armory(args: argparse.Namespace, spec_path: Path, catalog: dict) -> int:
    """``python -m bl2_partgen specs/armory.json``: one mod, many weapons (M10)."""
    if args.sidecar:
        print("armory: sidecars go in each weapon entry (\"sidecar\": ...), not --sidecar",
              file=sys.stderr)
        return 1
    if args.lint_only:
        print("armory: lint each weapon spec with --lint-only on its own file", file=sys.stderr)
        return 1
    try:
        armory = load_armory(spec_path)
        out_dir = Path(args.out) if args.out else Path("sdk_mod") / armory.name
        result = emit_armory(
            armory, out_dir, catalog=catalog, force=args.force, clean=not args.no_clean,
            spec_name=str(spec_path),
        )
    except (ResolveError, SpecError) as ex:
        print(f"cannot resolve the armory against the catalog: {ex}", file=sys.stderr)
        return 1
    except ArmoryConflict as ex:
        print(f"armory conflict: {ex}", file=sys.stderr)
        return 1
    except EmitRefused as ex:
        print(str(ex), file=sys.stderr)
        print("(pass --force to emit anyway)", file=sys.stderr)
        return 1
    for note in result.notes:
        print(f"note: {note}")
    for warning in result.lint_warnings:
        print(f"lint warning: {warning}")
    if result.forced:
        print("FORCED: emitted with lint errors outstanding:", file=sys.stderr)
        for error in result.lint_errors:
            print(f"  {error}", file=sys.stderr)
    print(f"emitted armory {result.name} ({len(result.weapons)} weapon(s)) -> {result.out_dir}")
    for path in result.files:
        print(f"  {path}")
    if args.install:
        try:
            installed = install_armory(result, args.install, replace=args.replace, catalog=catalog)
        except InstallRefused as ex:
            print(f"install refused: {ex}", file=sys.stderr)
            return 1
        for note in installed.notes:
            print(f"install note: {note}")
        print(f"installed -> {installed.mod_dir}")
        for path in installed.files:
            if path.suffix == ".upk":
                print(f"package    -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
