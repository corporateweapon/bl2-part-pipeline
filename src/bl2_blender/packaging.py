"""Build an installable add-on zip (``bl2_upk`` vendored inside it).

``python -m bl2_blender.packaging [--out scratch/bl2_blender_addon.zip]``

The zip has a single top-level ``bl2_blender/`` folder, which is what both
Blender's legacy *Install from Disk* and the 4.2+ extension installer expect
(``blender_manifest.toml`` ships alongside ``bl_info``), and carries a copy of
``bl2_upk`` under ``bl2_blender/_vendor/`` so the add-on does not need the repo
on ``sys.path``.
Note that the *data* it reads (the decompressed ``Startup.upk`` and the fragment
table under ``scratch/``) still has to exist on disk -- the zip ships code only.
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent
REPO_ROOT = _SRC.parent
DEFAULT_ZIP = REPO_ROOT / "scratch" / "bl2_blender_addon.zip"

_SKIP_DIRS = {"__pycache__", "_vendor"}


def _iter_py(root: Path):
    for path in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def build_addon_zip(out_path: str | Path = DEFAULT_ZIP) -> Path:
    """Write the add-on zip and return its path."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        manifest = _HERE / "blender_manifest.toml"
        if manifest.exists():
            archive.write(manifest, "bl2_blender/blender_manifest.toml")
        for path in _iter_py(_HERE):
            archive.write(path, f"bl2_blender/{path.relative_to(_HERE).as_posix()}")
        for path in _iter_py(_SRC / "bl2_upk"):
            archive.write(
                path,
                f"bl2_blender/_vendor/bl2_upk/{path.relative_to(_SRC / 'bl2_upk').as_posix()}",
            )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_ZIP))
    args = parser.parse_args(argv)
    out = build_addon_zip(args.out)
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
    print(f"{out}  ({len(names)} files, {out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
