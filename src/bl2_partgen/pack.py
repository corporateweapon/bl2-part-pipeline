"""Build Armory weapon packs and the Armory release itself (both as zips laid out like the game folder).

A pack is what a weapon modder publishes: ``armory_pack.json`` (per weapon, the spec and its
resolved form -- see :mod:`.packdata`), the baked sounds, a README and the licence, plus the
weapon's ``.upk`` files. The zip mirrors the game folder, so a player extracts it into
``Borderlands 2\\`` and every file lands where it belongs::

    sdk_mods/ArmoryPacks/<pack id>/armory_pack.json, README.md, LICENSE.txt, sounds/*.wav
    WillowGame/CookedPCConsole/<package>.upk

The pack is built from a *pack manifest* (``packs/<id>.json`` in this repo)::

    {"id": "boxgun", "name": "Boxgun", "version": "1.0.0", "author": "...",
     "license": "CC0-1.0", "license_file": null, "description": "...", "url": "",
     "weapons": [{"id": "boxgun", "label": "Boxgun", "spec": "../specs/boxgun.json",
                  "sidecar": "../scratch/PipelineMeshesBoxgun.fragment.json"}]}

A manifest may also (or only) list character skins built by ``bl2_charswap``::

     "characters": [{"spec": "../specs/characters/ct_krieg.json"}]

each becomes the skin manifest the Armory's character runtime reads (checked by
:func:`.packdata.load_character`, the Armory's own check) plus its ``.upk`` files.

Nothing is written while a weapon fails the linter, the pack checks the Armory itself will run
(:func:`.packdata.load_weapon` / ``load_character``), or a render/compile of its component.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bl2_catalog import load_catalog

from .emit import EmitRefused, _catalog_stamp, lint_spec, write_fire_sound
from .packdata import (
    ARMORY_VERSION,
    PACK_FILE,
    PACK_FORMAT,
    PACK_SCHEMA,
    WEAPON_ONLY_SCHEMA,
    PackError,
    character_claims,
    claimed_paths,
    features_of,
    load_character,
    load_weapon,
    render_weapon,
    resolved_to_dict,
)
from .resolve import resolve
from .spec import load_spec

__all__ = ["PackBuild", "PackManifestError", "build_armory_release", "build_pack",
           "install_tree", "load_pack_manifest"]

PACK_ID = re.compile(r"^[A-Za-z0-9_]{1,40}$")
WEAPON_ID = re.compile(r"^[a-z0-9_]{1,32}$")

#: the renderer modules the Armory carries as ``Armory/_render`` (stdlib-only)
RENDER_MODULES = ("spec.py", "resolve.py", "templates.py", "alt_fire_template.py",
                  "rage_template.py", "packdata.py", "wave_mixer.py")
#: the character runtime (src/bl2_charswap/runtime.py) ships as Armory/_render/<this name>
CHARACTER_RUNTIME = "charswap_runtime.py"
#: the double-click installer every zip carries at its top level (armory/installer/)
INSTALLER_SRC = Path(__file__).resolve().parents[2] / "armory" / "installer"
INSTALLER_FILES = ("Install.bat", "Uninstall.bat", "_installer", "HOW_TO_INSTALL.txt")
#: the only top-level folders of a zip that belong in the game folder
GAME_TOP = ("sdk_mods", "WillowGame")

#: short notices for common licences when the manifest names no licence file
LICENSE_NOTICES = {
    "CC0-1.0": "To the extent possible under law, {author} has waived all copyright and related "
               "or neighboring rights to {name}.\nhttps://creativecommons.org/publicdomain/zero/1.0/",
    "CC-BY-4.0": "{name} by {author} is licensed under Creative Commons Attribution 4.0.\n"
                 "https://creativecommons.org/licenses/by/4.0/",
    "MIT": "Copyright (c) {author}\nReleased under the MIT License: "
           "https://opensource.org/license/mit",
}


class PackManifestError(ValueError):
    """The pack manifest is incomplete or names something that is not there."""


@dataclass
class PackBuild:
    """What :func:`build_pack` produced."""

    pack_id: str
    stage: Path                      # the folder that mirrors the game root
    pack_dir: Path                   # stage / sdk_mods / ArmoryPacks / <id>
    manifest: dict[str, Any]         # the armory_pack.json written
    packages: list[Path] = field(default_factory=list)
    zip_path: Path | None = None
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------- manifest
def load_pack_manifest(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        raise PackManifestError(f"{path}: {ex}") from ex
    for key in ("id", "name", "version", "author", "license"):
        if not data.get(key):
            raise PackManifestError(f"{path}: '{key}' is required")
    characters = data.setdefault("characters", [])
    if not isinstance(characters, list) or any(not isinstance(c, dict) or not c.get("spec")
                                               for c in characters):
        raise PackManifestError(f"{path}: characters must be a list of {{\"spec\": ...}}")
    data.setdefault("weapons", [])
    if not data["weapons"] and not characters:
        raise PackManifestError(f"{path}: it lists no weapons and no characters")
    if not PACK_ID.match(str(data["id"])):
        raise PackManifestError(f"{path}: id {data['id']!r} must be 1-40 letters, digits or _")
    weapons = data["weapons"]
    if not isinstance(weapons, list):
        raise PackManifestError(f"{path}: weapons must be a list")
    ids = [str(w.get("id", "")) for w in weapons]
    for wid in ids:
        if not WEAPON_ID.match(wid):
            raise PackManifestError(f"{path}: weapon id {wid!r} must be 1-32 of a-z, 0-9, _ "
                                    "(it is what players type after 'armory spawn')")
    if len(set(ids)) != len(ids):
        raise PackManifestError(f"{path}: weapon ids repeat")
    for w in weapons:
        if not w.get("spec") or not w.get("label"):
            raise PackManifestError(f"{path}: weapon {w.get('id')!r} needs 'spec' and 'label'")
    data["_dir"] = path.resolve().parent
    return data


def _rel(base: Path, value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else (base / p).resolve()


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _shareable_spec(spec_dict: dict[str, Any], name: str) -> dict[str, Any]:
    """The spec as a pack carries it: no path on the modder's machine survives."""
    out = json.loads(json.dumps(spec_dict))
    out["package_file"] = None
    options = out.get("options") or {}
    options["status_path"] = None
    out["options"] = options
    if out.get("fire_sound"):
        out["fire_sound"]["wav"] = f"sounds/{name}_fire.wav"
    if out.get("alt_fire") and out["alt_fire"].get("sound"):
        out["alt_fire"]["sound"] = f"sounds/{name}_alt_fire.wav"
    for extra in out.get("extra_packages") or []:
        if isinstance(extra, dict):
            extra["file"] = None
    return out


# ---------------------------------------------------------------------- one pack
def build_pack(
    manifest_path: Path | str,
    out_root: Path | str,
    *,
    catalog: dict[str, Any] | None = None,
    with_packages: bool = True,
    make_zip: bool = True,
) -> PackBuild:
    """Stage ``<out_root>/<id>/`` (the game-folder layout) and zip it as ``<id>-<version>.zip``.

    ``with_packages=False`` skips the ``.upk`` files (CI, or a check before the package
    build exists); such a pack is not installable and the manifest says so.
    """
    manifest = load_pack_manifest(manifest_path)
    base: Path = manifest["_dir"]
    catalog = catalog if catalog is not None else load_catalog()
    stamp = _catalog_stamp(catalog)
    pack_id = str(manifest["id"])
    notes: list[str] = []

    entries: list[dict[str, Any]] = []
    package_files: dict[str, Path | None] = {}
    specs = []
    claimed: dict[str, str] = {}
    for weapon in manifest["weapons"]:
        wid = str(weapon["id"])
        spec_path = _rel(base, weapon["spec"])
        assert spec_path is not None
        if not spec_path.exists():
            raise PackManifestError(f"weapon {wid}: spec {spec_path} not found")
        spec = load_spec(spec_path)
        sidecar = _rel(base, weapon.get("sidecar"))
        if sidecar is not None and not sidecar.exists():
            if with_packages:
                raise PackManifestError(f"weapon {wid}: sidecar {sidecar} not found; build the "
                                        "package first (bl2 build)")
            notes.append(f"{wid}: sidecar {sidecar.name} absent; fragment ranges from the spec")
            sidecar = None
        elif sidecar is None:
            notes.append(f"{wid}: no sidecar named; fragment ranges come from the spec as written")
        resolved = resolve(spec, catalog, str(sidecar) if sidecar else None)
        errors = [f"[{r.part_name}] {e.check} {e.message}"
                  for r in lint_spec(spec, catalog, resolved) for e in r.report.errors]
        if errors:
            raise EmitRefused(f"weapon {wid}: the linter found {len(errors)} error(s):\n  "
                              + "\n  ".join(errors), [])
        entry = {
            "id": wid,
            "label": str(weapon["label"]),
            "weapon_type": spec.weapon_type,
            "balance": resolved.balances[0].path if resolved.balances else None,
            "package": spec.package_stem,
            "features": features_of(spec),
            "spec": _shareable_spec(spec.to_dict(), spec.name),
            "resolved": resolved_to_dict(resolved),
        }
        entry = json.loads(json.dumps(entry))  # exactly what a reader will see
        try:
            checked = load_weapon(entry)
            compile(render_weapon(checked, f"pack {pack_id}", stamp), f"<{wid}>", "exec")
        except PackError as ex:
            raise PackManifestError(f"weapon {wid}: {ex}") from ex
        for path, kind in claimed_paths(checked).items():
            if path in claimed and kind not in ("package", "extra package"):
                raise PackManifestError(f"weapon {wid}: {path} ({kind}) is also claimed by "
                                        f"weapon {claimed[path]} in this pack")
            claimed.setdefault(path, wid)
        package_files.setdefault(spec.package_stem, spec.resolved_package_file())
        for name, file in spec.resolved_extra_package_files():
            package_files.setdefault(name, file)
        entries.append(entry)
        specs.append(spec)

    skins: list[dict[str, Any]] = []
    if manifest["characters"]:
        from bl2_charswap.skinpack import skin_manifest, skin_package_files

        for character in manifest["characters"]:
            spec_path = _rel(base, character["spec"])
            assert spec_path is not None
            if not spec_path.exists():
                raise PackManifestError(f"character spec {spec_path} not found")
            cspec = json.loads(spec_path.read_text(encoding="utf-8"))
            try:
                skin, source = skin_manifest(cspec)
                files = skin_package_files(skin)
            except FileNotFoundError as ex:
                raise PackManifestError(str(ex)) from ex
            skin["default"] = bool(character.get("default", True))
            try:
                skin = load_character(json.loads(json.dumps(skin)), set(files))
            except PackError as ex:
                raise PackManifestError(f"character {cspec.get('id')}: {ex}") from ex
            for path, kind in character_claims(skin).items():
                if path in claimed:
                    raise PackManifestError(f"character {skin['id']}: {path} ({kind}) is claimed twice")
                claimed[path] = skin["id"]
            for name, file in files.items():
                package_files.setdefault(name, file)
            notes.append(f"skin {skin['id']}: manifest from {source}")
            skins.append(skin)

    stage = Path(out_root) / pack_id
    if stage.exists():
        shutil.rmtree(stage)
    pack_dir = stage / "sdk_mods" / "ArmoryPacks" / pack_id
    cooked = stage / "WillowGame" / "CookedPCConsole"
    pack_dir.mkdir(parents=True)

    packages_meta: list[dict[str, Any]] = []
    copied: list[Path] = []
    for name, source in package_files.items():
        meta: dict[str, Any] = {"file": f"{name}.upk"}
        if with_packages:
            if source is None or not source.is_file():
                raise PackManifestError(f"package {name}: file {source} not found; build it "
                                        "first (bl2 build) or set package_file / extra_packages")
            cooked.mkdir(parents=True, exist_ok=True)
            dest = cooked / f"{name}.upk"
            shutil.copy2(source, dest)
            copied.append(dest)
            meta.update(sha1=_sha1(dest), bytes=dest.stat().st_size)
        packages_meta.append(meta)

    for spec in specs:
        write_fire_sound(spec, pack_dir)

    pack = {
        "format": PACK_FORMAT,
        "schema": PACK_SCHEMA if skins else WEAPON_ONLY_SCHEMA,
        "id": pack_id,
        "name": str(manifest["name"]),
        "version": str(manifest["version"]),
        "author": str(manifest["author"]),
        "license": str(manifest["license"]),
        "description": str(manifest.get("description") or ""),
        "url": str(manifest.get("url") or ""),
        "armory_min": str(manifest.get("armory_min") or ARMORY_VERSION),
        "catalog": stamp,
        "installable": bool(with_packages),
        "packages": packages_meta,
        "weapons": entries,
    }
    if skins:
        pack["characters"] = skins
    (pack_dir / PACK_FILE).write_text(json.dumps(pack, indent=1) + "\n", encoding="utf-8",
                                      newline="\n")
    (pack_dir / "README.md").write_text(render_pack_readme(pack), encoding="utf-8", newline="\n")
    license_file = _rel(base, manifest.get("license_file"))
    if license_file is not None:
        shutil.copy2(license_file, pack_dir / "LICENSE.txt")
    else:
        notice = LICENSE_NOTICES.get(pack["license"])
        if notice is None:
            raise PackManifestError(f"licence {pack['license']!r} has no built-in notice; set "
                                    "license_file to the licence text")
        (pack_dir / "LICENSE.txt").write_text(
            notice.format(author=pack["author"], name=pack["name"]) + "\n", encoding="utf-8")

    add_installer(stage, render_how_to(pack["name"], pack["version"], _pack_kind(pack), stage,
                                       needs_armory=pack["armory_min"]))
    result = PackBuild(pack_id=pack_id, stage=stage, pack_dir=pack_dir, manifest=pack,
                       packages=copied, notes=notes)
    if make_zip:
        suffix = "" if with_packages else "-check"   # never mistaken for the upload
        result.zip_path = _zip(stage, Path(out_root) /
                               f"ArmoryPack-{pack_id}-{pack['version']}{suffix}.zip")
    return result


def render_pack_readme(pack: dict[str, Any]) -> str:
    if not pack["weapons"]:
        return render_character_readme(pack)
    rows = "\n".join(
        f"| `{w['id']}` | {w['label']} | {w['weapon_type']} | `{w['balance']}` |"
        for w in pack["weapons"])
    files = "\n".join(f"    WillowGame\\CookedPCConsole\\{p['file']}" for p in pack["packages"])
    return f"""# {pack['name']} — an Armory weapon pack

{pack['description']}

* Pack id `{pack['id']}`, version {pack['version']}, by {pack['author']}
* Licence: {pack['license']} (see LICENSE.txt)
* Needs the **Armory {pack['armory_min']} or newer** (and the willow2 SDK mod manager it runs on)

| spawn id | weapon | type | balance |
|---|---|---|---|
{rows}

## Install

Extract the zip into your `Borderlands 2` folder (the one holding `Binaries` and
`WillowGame`) and let it merge folders. That places:

    sdk_mods\\ArmoryPacks\\{pack['id']}\\   (this folder)
{files}

Start the game. The Armory registers the weapon(s) at the main menu; load your character,
then press the Armory spawn key (F5 by default) or type `armory spawn {pack['weapons'][0]['id']}`
in the console. `armory packs` lists what loaded and why anything did not.

## Uninstall

Delete `sdk_mods\\ArmoryPacks\\{pack['id']}\\` and the `.upk` file(s) listed above. Weapons of
this pack in your saves lose their parts while it is removed; their records are kept under
`sdk_mods\\_pipeline_saves\\`, so putting the pack back restores them.
"""


def render_character_readme(pack: dict[str, Any]) -> str:
    rows = "\n".join(f"| `{s['id']}` | {s['label']} | {s['character']} |" for s in pack["characters"])
    files = "\n".join(f"    WillowGame\\CookedPCConsole\\{p['file']}" for p in pack["packages"])
    first = pack["characters"][0]
    return f"""# {pack['name']} — an Armory character pack

{pack['description']}

* Pack id `{pack['id']}`, version {pack['version']}, by {pack['author']}
* Licence: {pack['license']} (see LICENSE.txt)
* Needs the **Armory {pack['armory_min']} or newer** (and the willow2 SDK mod manager it runs on)

| skin id | skin | vault hunter |
|---|---|---|
{rows}

## Install

Extract the zip into your `Borderlands 2` folder (the one holding `Binaries` and
`WillowGame`) and let it merge folders. That places:

    sdk_mods\\ArmoryPacks\\{pack['id']}\\   (this folder)
{files}

Start the game. The Armory loads the skin at the main menu and {first['character']} wears it
from then on. Switch in **Mods > Armory > Characters** (Default = the game's own model), or in
the console: `characters set {first['character']} {first['id']}` / `characters list`.
`armory packs` lists what loaded and why anything did not.

## Uninstall

Delete `sdk_mods\\ArmoryPacks\\{pack['id']}\\` and the `.upk` file(s) listed above.
"""


def _pack_kind(pack: dict[str, Any]) -> str:
    kinds = []
    if pack["weapons"]:
        kinds.append("weapon pack: " + ", ".join(w["label"] for w in pack["weapons"]))
    if pack.get("characters"):
        kinds.append("character pack: " + ", ".join(f"{c['label']} for {c['character']}"
                                                    for c in pack["characters"]))
    return "; ".join(kinds)


def add_installer(stage: Path, how_to: str) -> None:
    """Put Install.bat / Uninstall.bat / _installer/ and HOW_TO_INSTALL.txt at the zip's top level."""
    for name in ("Install.bat", "Uninstall.bat"):
        shutil.copy2(INSTALLER_SRC / name, stage / name)
    (stage / "_installer").mkdir(exist_ok=True)
    shutil.copy2(INSTALLER_SRC / "armory_install.ps1", stage / "_installer" / "armory_install.ps1")
    (stage / "HOW_TO_INSTALL.txt").write_bytes(how_to.replace("\n", "\r\n").encode("ascii", "replace"))


def render_how_to(name: str, version: str, kind: str, stage: Path, *, needs_armory: str | None) -> str:
    """Plain-text install steps for a player: the installer, or drag and drop (with the exact files)."""
    files = sorted(str(p.relative_to(stage)).replace("/", "\\") for p in stage.rglob("*")
                   if p.is_file() and p.relative_to(stage).parts[0] in GAME_TOP)
    shown = [f for f in files if not f.startswith("sdk_mods\\Armory\\_render")]
    listing = "\n".join(f"      {f}" for f in shown)
    if len(shown) < len(files):
        listing += "\n      sdk_mods\\Armory\\_render\\... (the Armory's code)"
    requires = ("the Armory " + needs_armory + " or newer (Armory-<version>.zip, install it first) and "
                if needs_armory else "")
    return f"""{name} {version}
{kind}
{"=" * 72}

Requires: {requires}Borderlands 2 on Steam (Windows) with the willow2-mod-manager
(https://bl-sdk.github.io/willow2-mod-db/). Close the game before installing.

OPTION 1 (easiest): double-click Install.bat
  1. Extract this WHOLE zip to a normal folder first (right-click the zip > Extract All...).
     Don't run Install.bat from inside the zip window.
  2. Open the extracted folder and double-click Install.bat.
     It finds your Borderlands 2 folder through Steam (or asks you to pick it), copies the
     files below into it, checks every copy, and tells you what it did.
  Windows warns about any script that came out of a downloaded zip. "Windows protected your
  PC": click "More info" then "Run anyway". "Open File - Security Warning": click "Run".
  To skip the warning: before extracting, right-click the zip > Properties > tick "Unblock" > OK.

OPTION 2: drag and drop (no scripts)
  1. Open your Borderlands 2 folder: in Steam, right-click Borderlands 2 > Manage >
     Browse local files. It is the folder holding Binaries, WillowGame and sdk_mods.
  2. From the extracted zip, drag the "sdk_mods" and "WillowGame" folders onto that window.
     When Windows asks, choose "Replace the files in the destination". Folders merge; nothing
     of the game's own is replaced. These are the files that land in the game folder:
{listing}
  Install.bat, Uninstall.bat, the _installer folder and this file do NOT go into the game.

UNINSTALL: double-click Uninstall.bat, or delete the files listed above.

Start Borderlands 2 and wait for the main menu before loading a character. Mods > Armory
lists the weapons (F5 spawns the selected one) and the character skins. In the console:
'armory packs' shows what loaded and why anything did not.
"""


# ---------------------------------------------------------------------- the Armory release
def build_armory_release(
    out_root: Path | str,
    *,
    runtime_dir: Path | str,
    docs: list[Path] | None = None,
    packs: list[PackBuild] | None = None,
    make_zip: bool = True,
) -> tuple[Path, Path | None]:
    """Stage ``<out_root>/Armory-<version>/`` (game-folder layout) and zip it.

    ``runtime_dir`` is the committed ``armory/Armory`` folder; the renderer modules are copied
    from this package into ``sdk_mods/Armory/_render`` so the Armory and the pipeline cannot
    drift apart. ``packs`` (already built) are merged in as the default packs.
    """
    runtime_dir = Path(runtime_dir)
    stage = Path(out_root) / f"Armory-{ARMORY_VERSION}"
    if stage.exists():
        shutil.rmtree(stage)
    mod_dir = stage / "sdk_mods" / "Armory"
    shutil.copytree(runtime_dir, mod_dir,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "logs", "control.json"))
    render_dir = mod_dir / "_render"
    render_dir.mkdir(exist_ok=True)
    here = Path(__file__).resolve().parent
    for name in RENDER_MODULES:
        shutil.copy2(here / name, render_dir / name)
    shutil.copy2(here.parent / "bl2_charswap" / "runtime.py", render_dir / CHARACTER_RUNTIME)
    (render_dir / "__init__.py").write_text(
        '"""The pipeline\'s renderer (bl2_partgen), carried by the Armory to turn packs into '
        'code.\n\nCopied from src/bl2_partgen at release time; do not edit here."""\n',
        encoding="utf-8", newline="\n")
    pyproject = mod_dir / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8")
        text = re.sub(r'(?m)^version = ".*"$', f'version = "{ARMORY_VERSION}"', text)
        pyproject.write_text(text, encoding="utf-8", newline="\n")
    for doc in docs or []:
        shutil.copy2(doc, mod_dir / Path(doc).name)
    for pack in packs or []:
        shutil.copytree(pack.stage, stage, dirs_exist_ok=True)
    add_installer(stage, render_how_to(
        "Armory", ARMORY_VERSION,
        "the framework for custom weapons and character skins (includes the Boxgun)"
        if packs else "the framework for custom weapons and character skins", stage,
        needs_armory=None))
    check = any(not p.manifest.get("installable", True) for p in packs or [])
    zip_path = (_zip(stage, Path(out_root) / f"Armory-{ARMORY_VERSION}{'-check' if check else ''}.zip")
                if make_zip else None)
    return stage, zip_path


def _zip(stage: Path, dest: Path) -> Path:
    if dest.exists():
        dest.unlink()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(stage).as_posix())
    return dest


def install_tree(stage: Path | str, game: Path | str) -> list[Path]:
    """Copy a staged tree (pack or release) onto the game folder, merging directories."""
    stage, game = Path(stage), Path(game)
    written = []
    for path in sorted(stage.rglob("*")):
        # the zip's own installer and instructions stay out of the game folder
        if path.is_file() and path.relative_to(stage).parts[0] in GAME_TOP:
            dest = game / path.relative_to(stage)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            written.append(dest)
    return written


def pipeline_mods_in(game: Path | str) -> list[str]:
    """Generated pipeline mods sitting in the game's sdk_mods: the Armory refuses any pack
    whose package one of them also registers (rule 3), so installing says so up front."""
    sdk_mods = Path(game) / "sdk_mods"
    if not sdk_mods.is_dir():
        return []
    return sorted(p.name for p in sdk_mods.iterdir()
                  if p.is_dir() and p.name.startswith("Pipeline") and (p / "__init__.py").exists())


def _dirty_render_modules(repo: Path) -> list[str]:
    """Renderer modules (and the runtime) with uncommitted changes; [] outside a git checkout.

    The release copies them into Armory/_render: a module mid-edit (another session working on
    a template, say) would ship to every player, and a syntax error there stops the Armory
    importing at all.
    """
    import subprocess

    paths = ([f"src/bl2_partgen/{m}" for m in RENDER_MODULES]
             + ["src/bl2_charswap/runtime.py", "armory/Armory"])
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", *paths], cwd=repo,
                             capture_output=True, text=True, check=False)
    except OSError:
        return []
    if out.returncode != 0:
        return []
    return [line[3:] for line in out.stdout.splitlines() if line.strip()]


# ---------------------------------------------------------------------- command line
def main(argv: list[str] | None = None) -> int:
    import argparse

    repo = Path(__file__).resolve().parents[2]
    p = argparse.ArgumentParser(prog="python -m bl2_partgen.pack", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pk = sub.add_parser("pack", help="build one weapon pack from a pack manifest")
    pk.add_argument("manifest", help="packs/<id>.json")
    pk.add_argument("--out", default=str(repo / "dist" / "packs"))
    pk.add_argument("--no-packages", action="store_true",
                    help="skip the .upk files (a check build; not installable)")
    pk.add_argument("--install", default=None, metavar="GAME", help="also extract it into GAME")
    rl = sub.add_parser("release", help="build the Armory release zip (runtime + default packs)")
    rl.add_argument("--out", default=str(repo / "dist"))
    rl.add_argument("--pack", action="append", default=[], metavar="MANIFEST",
                    help="a default pack to bundle (repeatable); default: packs/boxgun.json")
    rl.add_argument("--no-packages", action="store_true")
    rl.add_argument("--install", default=None, metavar="GAME", help="also extract it into GAME")
    rl.add_argument("--allow-dirty", action="store_true",
                    help="build even if a renderer module has uncommitted changes")
    a = p.parse_args(argv)

    if a.cmd == "release" and not a.allow_dirty and not a.no_packages:
        dirty = _dirty_render_modules(repo)
        if dirty:
            print("refused: the Armory would ship uncommitted renderer code ("
                  + ", ".join(dirty) + "); commit it and rerun the tests first, or pass "
                  "--allow-dirty for a local build")
            return 2

    try:
        if a.cmd == "pack":
            build = build_pack(a.manifest, a.out, with_packages=not a.no_packages)
            for note in build.notes:
                print(f"note: {note}")
            print(f"pack {build.pack_id}: {len(build.manifest['weapons'])} weapon(s), "
                  f"{len(build.manifest.get('characters') or [])} character skin(s), "
                  f"{len(build.packages)} package(s) -> {build.zip_path}")
            stage = build.stage
        else:
            manifests = a.pack or [str(repo / "packs" / "boxgun.json")]
            builds = [build_pack(m, Path(a.out) / "packs", with_packages=not a.no_packages)
                      for m in manifests]
            docs = [d for d in (repo / "armory" / "CREATING_PACKS.md",) if d.exists()]
            stage, zip_path = build_armory_release(
                a.out, runtime_dir=repo / "armory" / "Armory", docs=docs, packs=builds)
            print(f"Armory {ARMORY_VERSION} with pack(s) {', '.join(b.pack_id for b in builds)} "
                  f"-> {zip_path}")
    except (PackManifestError, PackError, EmitRefused) as ex:
        print(f"refused: {ex}")
        return 2
    if a.install:
        if a.no_packages:
            print("refused: a --no-packages build cannot be installed")
            return 2
        others = pipeline_mods_in(a.install)
        written = install_tree(stage, a.install)
        print(f"installed {len(written)} file(s) into {a.install}")
        if others:
            print("WARNING: these pipeline mods are also in sdk_mods and the Armory will refuse any "
                  f"pack whose package they register: {', '.join(others)} -- park them in "
                  "sdk_mods/_disabled/ (rule 3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
