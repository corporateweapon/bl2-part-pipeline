"""``install(emit_result, game_dir)``: put a generated mod (and its package) in place.

Three destinations, all of which have their own way of failing silently:

* ``<game>/sdk_mods/<name>/``             the mod itself, plus its ``pyproject.toml``
* ``<game>/sdk_mods/settings/<name>.json``  ``{"enabled": true}`` -- without it the mod is
  never enabled on the launch that installs it and no hook ever binds (F8)
* ``<game>/WillowGame/CookedPCConsole/<package>.upk``  the mesh package

The package copy refuses two things outright: a name in the exe's SHA1 table (writing one
of those twelve files kills the game three seconds after launch, F12), and overwriting an
existing package whose bytes differ, unless ``replace=True`` says that is intended.
"""

from __future__ import annotations

import filecmp
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bl2_catalog import load_catalog

from .emit import EmitResult

__all__ = ["InstallRefused", "InstallResult", "install"]

#: fallback if the catalog is unavailable; the real list lives in meta.hashed_packages (F12)
FALLBACK_HASHED_PACKAGES = (
    "akaudio.upk", "core.upk", "engine.upk", "gameframework.upk", "gearboxframework.upk",
    "gfxui.upk", "ipdrv.upk", "menumap.upk", "onlinesubsystemsteamworks.upk", "startup.upk",
    "startup_loc_int.upk", "willowgame.upk",
)

COOKED = Path("WillowGame") / "CookedPCConsole"
SDK_MODS = Path("sdk_mods")


class InstallRefused(RuntimeError):
    """Installing would do something the pipeline has already been burned by."""


@dataclass
class InstallResult:
    mod_dir: Path
    settings_file: Path
    files: list[Path] = field(default_factory=list)
    package_source: Path | None = None
    package_dest: Path | None = None
    package_replaced: bool = False
    notes: list[str] = field(default_factory=list)


def _hashed_packages(catalog: dict[str, Any] | None) -> set[str]:
    if catalog is None:
        try:
            catalog = load_catalog()
        except Exception:  # noqa: BLE001 - the check must work without a catalog
            catalog = None
    names = (catalog or {}).get("meta", {}).get("hashed_packages") or FALLBACK_HASHED_PACKAGES
    return {str(name).lower() for name in names}


def install(
    result: EmitResult,
    game_dir: Path | str,
    *,
    package_file: Path | str | None = None,
    replace: bool = False,
    catalog: dict[str, Any] | None = None,
) -> InstallResult:
    """Copy the emitted mod into ``game_dir``; also copy the package when there is one."""
    game_dir = Path(game_dir)
    if not game_dir.is_dir():
        raise InstallRefused(f"game directory does not exist: {game_dir}")
    if not result.out_dir.is_dir():
        raise InstallRefused(f"emitted mod folder does not exist: {result.out_dir}")

    mod_dir = game_dir / SDK_MODS / result.name
    settings_file = game_dir / SDK_MODS / "settings" / f"{result.name}.json"
    installed = InstallResult(mod_dir=mod_dir, settings_file=settings_file)

    # --- the mod folder ------------------------------------------------------------
    mod_dir.mkdir(parents=True, exist_ok=True)
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    for source in sorted(result.out_dir.rglob("*")):
        if source.is_dir() or source.parent.name == "settings":
            continue
        dest = mod_dir / source.relative_to(result.out_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        installed.files.append(dest)

    emitted_settings = result.settings_file
    if emitted_settings.exists():
        shutil.copy2(emitted_settings, settings_file)
    else:  # emit always writes it; belt and braces so F8 cannot come back
        settings_file.write_text('{\n "enabled": true\n}\n', encoding="utf-8", newline="\n")
    installed.files.append(settings_file)

    _install_packages(result.spec, game_dir, installed, package_file=package_file,
                      replace=replace, catalog=catalog)
    return installed


def _install_packages(
    spec: Any,
    game_dir: Path,
    installed: InstallResult,
    *,
    package_file: Path | str | None = None,
    replace: bool = False,
    catalog: dict[str, Any] | None = None,
) -> None:
    """Copy a spec's package and extra packages into the game (F11/F12 rules)."""
    source_package = Path(package_file) if package_file is not None else None
    if source_package is None and spec is not None:
        source_package = spec.resolved_package_file()
    if spec is None:
        installed.notes.append("no spec on the emit result; package not installed")
        return

    package_name = f"{spec.package_stem}.upk"
    hashed = _hashed_packages(catalog)
    for name in [package_name, *(f"{n}.upk" for n, _ in spec.resolved_extra_package_files())]:
        if name.lower() in hashed:
            raise InstallRefused(
                f"{name} is one of the twelve packages the exe SHA1-verifies: any byte "
                "change in it makes the game die ~3 s after launch (F12). Ship a new package "
                "file instead and re-point at runtime."
            )

    if source_package is None:
        installed.notes.append(
            "spec has no package_file; copy the .upk into WillowGame/CookedPCConsole yourself"
        )
        return
    if not source_package.is_file():
        raise InstallRefused(f"package file not found: {source_package}")

    dest_dir = game_dir / COOKED
    if not dest_dir.is_dir():
        raise InstallRefused(f"not a game directory (no {COOKED}): {game_dir}")
    dest = dest_dir / package_name
    if dest.exists():
        if filecmp.cmp(source_package, dest, shallow=False):
            installed.notes.append(f"{package_name} already installed with identical bytes")
        elif not replace:
            raise InstallRefused(
                f"{dest} exists with different bytes; pass replace=True (--replace) to "
                "overwrite it"
            )
        else:
            installed.package_replaced = True
    shutil.copy2(source_package, dest)
    installed.package_source = source_package
    installed.package_dest = dest
    installed.files.append(dest)

    # A leftover sidecar makes the loader treat an uncompressed package as compressed (F11).
    # --- extra packages (textures, ...), same rules ------------------------------------
    for name, extra_file in spec.resolved_extra_package_files():
        if extra_file is None:
            installed.notes.append(
                f"extra package {name} has no file in the spec; copy {name}.upk into "
                "WillowGame/CookedPCConsole yourself"
            )
            continue
        if not extra_file.is_file():
            raise InstallRefused(f"extra package file not found: {extra_file}")
        extra_dest = dest_dir / f"{name}.upk"
        if extra_dest.exists() and not filecmp.cmp(extra_file, extra_dest, shallow=False):
            if not replace:
                raise InstallRefused(
                    f"{extra_dest} exists with different bytes; pass replace=True (--replace) "
                    "to overwrite it"
                )
            installed.package_replaced = True
        shutil.copy2(extra_file, extra_dest)
        installed.files.append(extra_dest)
        stale = extra_dest.with_suffix(extra_dest.suffix + ".uncompressed_size")
        if stale.exists():
            stale.rename(stale.with_suffix(".stale"))
            installed.notes.append(f"renamed stale sidecar {stale.name} (F11)")

    sidecar = dest.with_suffix(dest.suffix + ".uncompressed_size")
    if sidecar.exists():
        sidecar.unlink()
        installed.notes.append(f"removed stale {sidecar.name} (F11)")


def install_armory(
    result: Any,
    game_dir: Path | str,
    *,
    replace: bool = False,
    catalog: dict[str, Any] | None = None,
) -> InstallResult:
    """Copy an emitted Armory into ``game_dir`` plus every weapon's packages (M10)."""
    game_dir = Path(game_dir)
    if not game_dir.is_dir():
        raise InstallRefused(f"game directory does not exist: {game_dir}")
    mod_dir = game_dir / SDK_MODS / result.name
    settings_file = game_dir / SDK_MODS / "settings" / f"{result.name}.json"
    installed = InstallResult(mod_dir=mod_dir, settings_file=settings_file)
    if mod_dir.exists():
        shutil.rmtree(mod_dir)  # a weapon dropped from the armory must not linger
    mod_dir.mkdir(parents=True, exist_ok=True)
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    for source in sorted(result.out_dir.rglob("*")):
        if source.is_dir() or source.parent.name == "settings":
            continue
        dest = mod_dir / source.relative_to(result.out_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        installed.files.append(dest)
    if result.settings_file.exists():
        shutil.copy2(result.settings_file, settings_file)
    else:
        settings_file.write_text('{\n "enabled": true\n}\n', encoding="utf-8", newline="\n")
    installed.files.append(settings_file)
    for weapon in result.weapons:
        _install_packages(weapon.spec, game_dir, installed, replace=replace, catalog=catalog)
    return installed
