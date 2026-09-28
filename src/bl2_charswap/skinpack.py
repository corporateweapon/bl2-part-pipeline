"""A built skin's manifest and packages, for the Armory pack builder (``bl2_partgen.pack``).

``python -m bl2_charswap build`` writes ``scratch/charswap/<id>/skin.json`` beside the skin's
packages. A character pack carries that manifest (the keys the runtime reads) and the packages;
when a build folder is gone the copies installed in the game are used instead.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Borderlands 2")
#: what the runtime reads from a skin (bl2_charswap/runtime.py)
SKIN_KEYS = ("id", "label", "character", "description", "packages", "swaps", "hide_mesh_prefixes")


def build_dir(spec: dict[str, Any]) -> Path:
    return REPO / "scratch" / "charswap" / str(spec["id"])


def skin_manifest(spec: dict[str, Any], game: Path = GAME, out: Path | None = None) -> tuple[dict[str, Any], str]:
    """The skin as the runtime reads it, and where it came from (the build, else the game's
    installed PipelineCharacters/characters.json)."""
    out = out or build_dir(spec)
    built = out / "skin.json"
    if built.exists():
        data = json.loads(built.read_text(encoding="utf-8"))
        return {k: data[k] for k in SKIN_KEYS if k in data}, str(built)
    installed = game / "sdk_mods" / "PipelineCharacters" / "characters.json"
    if installed.exists():
        for skin in json.loads(installed.read_text(encoding="utf-8")).get("skins", []):
            if skin.get("id") == spec["id"]:
                return {k: skin[k] for k in SKIN_KEYS if k in skin}, str(installed)
    raise FileNotFoundError(f"no manifest for skin {spec['id']}: {built} is missing and it is not "
                            "installed; run python -m bl2_charswap build first")


def skin_package_files(skin: dict[str, Any], game: Path = GAME, out: Path | None = None) -> dict[str, Path]:
    """``{package: .upk file}``: the build folder's copy, else the game's CookedPCConsole."""
    out = out or build_dir(skin)
    cooked = game / "WillowGame" / "CookedPCConsole"
    files: dict[str, Path] = {}
    for pkg in skin["packages"]:
        built, installed = out / f"{pkg}.upk", cooked / f"{pkg}.upk"
        src = built if built.exists() else installed
        if not src.exists():
            raise FileNotFoundError(f"package {pkg}.upk is neither in {out} nor in {cooked}")
        files[pkg] = src
    return files
