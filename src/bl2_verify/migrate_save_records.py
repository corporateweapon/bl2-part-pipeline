"""Merge every pipeline mod's per-save part records into the shared per-package folder.

Before M10 each build (player, harness, spawn) kept ``saves/<save>.json`` in its own mod
folder, so a weapon granted under one build lost its parts when the save was loaded under
another (F18 needs the record). Builds emitted from M10 on read and write
``sdk_mods/_pipeline_saves/<package>/<save>.json`` instead. This copies the old records
across, keyed by the package each mod registers (``PACKAGE = "..."`` in its ``__init__.py``),
newest file winning per unique id. Stale ids are harmless: they never match a weapon.

    PYTHONPATH=src python src/bl2_verify/migrate_save_records.py [--game <dir>] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Borderlands 2")


def package_of(mod_init: Path) -> str | None:
    try:
        text = mod_init.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r'^PACKAGE = "([^"]+)"', text, re.M)
    return match.group(1) if match else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--game", default=str(GAME))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip", nargs="*", default=["BentBarrel", "pipeline_"],
                   help="mod folders whose name contains any of these are ignored (M2-era mods)")
    args = p.parse_args(argv)
    sdk_mods = Path(args.game) / "sdk_mods"
    shared = sdk_mods / "_pipeline_saves"
    merged: dict[str, dict[str, dict]] = {}          # package -> save name -> {uid: record}
    sources: list[tuple[float, Path, str]] = []
    for record in sdk_mods.rglob("saves/*.json"):
        if shared in record.parents:
            continue
        mod_dir = record.parent.parent
        if any(token in mod_dir.name for token in args.skip):
            print(f"skip {record.relative_to(sdk_mods)}: {mod_dir.name} is excluded")
            continue
        package = package_of(mod_dir / "__init__.py")
        if package is None:
            print(f"skip {record}: no PACKAGE in {mod_dir.name}")
            continue
        sources.append((record.stat().st_mtime, record, package))
    for _, record, package in sorted(sources):      # oldest first, so newest wins
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError) as ex:
            print(f"skip {record}: {ex}")
            continue
        if not isinstance(data, dict):
            continue
        merged.setdefault(package, {}).setdefault(record.name, {}).update(data)
        print(f"{record.relative_to(sdk_mods)} -> {package}/{record.name}: {len(data)} id(s)")
    for package, saves in merged.items():
        for save_name, records in saves.items():
            dest = shared / package / save_name
            existing = {}
            if dest.exists():
                try:
                    existing = json.loads(dest.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    existing = {}
            combined = {**records, **existing}      # what the new builds wrote already wins
            print(f"write {dest.relative_to(sdk_mods)}: {len(combined)} id(s)"
                  + (" (dry run)" if args.dry_run else ""))
            if not args.dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps(combined, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
