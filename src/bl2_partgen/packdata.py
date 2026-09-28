"""Armory packs: what a pack carries -- weapons (turned back into code) and character skins (data).

A pack (``sdk_mods/ArmoryPacks/<id>/armory_pack.json``) holds, per weapon, the weapon's spec
and its *resolved* form -- the output of :func:`bl2_partgen.resolve.resolve`, which needs the
catalog and the package build's sidecar and so can only run on a modder's machine. The Armory
rebuilds a :class:`ResolvedSpec` from that data and renders it with :func:`render_mod`
(``component=True``): the same text ``emit_armory`` writes to ``weapons/<id>.py``, so a pack
behaves exactly like the verified component it was built from, and every pack runs the
Armory's copy of the runtime code rather than a copy frozen at the time it was built (F28).

A pack may also carry **character skins** (schema 2, Armory 1.1.0): each is the manifest
``bl2_charswap`` builds -- the packages to load and a table ``source mesh -> {mesh, material}``
-- which the Armory hands to its character runtime (``_render/charswap_runtime.py``). Skins are
pure data; :func:`load_character` checks every field before the runtime sees it.

Standard library + :mod:`.spec`, :mod:`.resolve` and :mod:`.templates` only: this module is
vendored into the Armory as ``Armory/_render`` together with those four, and runs in game.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

from .resolve import (
    ResolvedBalance,
    ResolvedFragment,
    ResolvedMaterial,
    ResolvedPart,
    ResolvedRegistration,
    ResolvedSpec,
    ResolvedTitle,
)
from .spec import Spec, SpecError
from .templates import render_mod

__all__ = [
    "ARMORY_VERSION",
    "PACK_FILE",
    "PACK_FORMAT",
    "PACK_SCHEMA",
    "SUPPORTED_FEATURES",
    "PackError",
    "VAULT_HUNTERS",
    "character_claims",
    "claimed_paths",
    "features_of",
    "load_character",
    "load_weapon",
    "render_weapon",
    "resolved_from_dict",
    "resolved_to_dict",
    "string_problems",
    "version_tuple",
]

#: the Armory runtime's version (the release's pyproject.toml is stamped with it); a pack's
#: ``armory_min`` defaults to it
ARMORY_VERSION = "1.1.0"
#: the file the Armory looks for in every folder under sdk_mods/ArmoryPacks/
PACK_FILE = "armory_pack.json"
PACK_FORMAT = "armory_pack"
#: bumped when the layout of armory_pack.json changes incompatibly. 2 = may carry "characters"
#: (a weapon-only pack is still written as 1, so an older Armory keeps reading it)
PACK_SCHEMA = 2
WEAPON_ONLY_SCHEMA = 1
#: the vault hunters a character skin may dress (one dropdown each in Mods > Armory > Characters)
VAULT_HUNTERS = ("Axton", "Maya", "Salvador", "Zer0", "Gaige", "Krieg")

#: everything a schema-1 pack may ask of the runtime. A pack naming a feature outside this set
#: was built by a newer pipeline than this Armory knows; it is refused with a clear message
#: instead of rendering code that silently skips the feature.
SUPPORTED_FEATURES = frozenset({
    "own_gestalt",        # required: every pack weapon draws from its own gestalt clone
    "balances",           # own WeaponBalanceDefinition, title, red text, pools
    "suppress_prefix",    # name hooks keep the prefix off the title
    "part_overrides",     # stats / effects / slot upgrades edited on the part clones
    "materials",          # runtime MaterialInstanceConstants
    "extra_packages",     # further .upk files (textures, FX) loaded at the menu
    "objects",            # arbitrary constructed objects (spec.objects)
    "freeze",             # the cryo freeze mechanic
    "card_icon",          # an element icon on the item card
    "fire_sound",         # a wav played on fire (and on alt fire)
    "alt_fire",           # right mouse fires a second shot type
    "rage",               # the rage meter legendary effect
    "force_template_fragment",
})

_IDENT = re.compile(r"^[A-Za-z0-9_]+$")
_SKIN_ID = re.compile(r"^[a-z0-9_]{1,32}$")
_PATH = re.compile(r"^[A-Za-z0-9_.:\-]+$")


class PackError(ValueError):
    """A pack (or one weapon in it) cannot be loaded; the message says why, for a player."""


# ---------------------------------------------------------------------- features
def features_of(spec: Spec) -> list[str]:
    """The runtime features a spec's rendered component uses (sorted)."""
    options = spec.options
    found = set()
    if options.own_gestalt:
        found.add("own_gestalt")
    if spec.balances:
        found.add("balances")
    if any(b.suppress_prefix for b in spec.balances):
        found.add("suppress_prefix")
    if any(p.overrides is not None for p in spec.parts):
        found.add("part_overrides")
    if spec.materials:
        found.add("materials")
    if spec.extra_packages:
        found.add("extra_packages")
    if spec.objects:
        found.add("objects")
    for name in ("freeze", "card_icon", "fire_sound", "alt_fire", "rage"):
        if getattr(spec, name, None) is not None:
            found.add(name)
    if options.force_template_fragment_on_map_load:
        found.add("force_template_fragment")
    return sorted(found)


def version_tuple(text: str) -> tuple[int, ...]:
    """``"1.2.3"`` -> ``(1, 2, 3)``; anything after the numeric prefix of a part is ignored."""
    out = []
    for piece in str(text).split("."):
        digits = re.match(r"\d+", piece)
        out.append(int(digits.group(0)) if digits else 0)
    return tuple(out)


# ---------------------------------------------------------------------- (de)serialising
def _plain(obj: Any, skip: tuple[str, ...]) -> dict[str, Any]:
    """A dataclass's own fields as JSON-able values, leaving out the back-references."""
    out: dict[str, Any] = {}
    for f in dataclasses.fields(obj):
        if f.name in skip:
            continue
        value = getattr(obj, f.name)
        if isinstance(value, tuple):
            value = list(value)
        out[f.name] = value
    return out


def resolved_to_dict(resolved: ResolvedSpec) -> dict[str, Any]:
    """Everything :func:`resolve` added to the spec, as plain JSON.

    The ``spec`` back-references (and a part's ``overrides``, which *is* its spec's) are left
    out: they are rebound by position from the spec when the pack is read back.
    """
    return {
        "gestalt_def_path": resolved.gestalt_def_path,
        "stock_mesh_path": resolved.stock_mesh_path,
        "fragments": [_plain(f, ("spec",)) for f in resolved.fragments],
        "parts": [
            dict(_plain(p, ("spec", "overrides", "registrations", "runtime_registrations")),
                 registrations=[_plain(r, ()) for r in p.registrations],
                 runtime_registrations=[list(pair) for pair in p.runtime_registrations])
            for p in resolved.parts
        ],
        "balances": [
            dict(_plain(b, ("spec", "title")),
                 title=_plain(b.title, ("spec",)) if b.title is not None else None)
            for b in resolved.balances
        ],
        "materials": [_plain(m, ("spec",)) for m in resolved.materials],
        "extra_packages": list(resolved.extra_packages),
        "notes": list(resolved.notes),
        "total_vertices": resolved.total_vertices,
        "total_indices": resolved.total_indices,
    }


def _same_length(what: str, spec_side: list[Any], data_side: list[Any]) -> None:
    if len(spec_side) != len(data_side):
        raise PackError(f"{what}: the spec has {len(spec_side)}, the resolved data "
                        f"{len(data_side)}; the pack is damaged or hand-edited")


def resolved_from_dict(spec: Spec, data: dict[str, Any]) -> ResolvedSpec:
    """The inverse of :func:`resolved_to_dict`, rebinding every ``spec`` back-reference."""
    frags, parts = list(data.get("fragments") or []), list(data.get("parts") or [])
    bals, mats = list(data.get("balances") or []), list(data.get("materials") or [])
    _same_length("fragments", spec.fragments, frags)
    _same_length("parts", spec.parts, parts)
    _same_length("balances", spec.balances, bals)
    _same_length("materials", spec.materials, mats)
    try:
        fragments = [ResolvedFragment(spec=s, **d) for s, d in zip(spec.fragments, frags)]
        rparts = []
        for s, d in zip(spec.parts, parts):
            d = dict(d)
            regs = [ResolvedRegistration(**r) for r in d.pop("registrations", [])]
            runtime = [tuple(pair) for pair in d.pop("runtime_registrations", [])]
            rparts.append(ResolvedPart(spec=s, registrations=regs, runtime_registrations=runtime,
                                       overrides=s.overrides, **d))
        balances = []
        for s, d in zip(spec.balances, bals):
            d = dict(d)
            title_data = d.pop("title", None)
            title = None
            if title_data is not None:
                if s.title is None:
                    raise PackError(f"balance {s.name}: resolved data has a title, the spec none")
                title = ResolvedTitle(spec=s.title, **title_data)
            balances.append(ResolvedBalance(spec=s, title=title, **d))
        materials = [ResolvedMaterial(spec=s, **d) for s, d in zip(spec.materials, mats)]
        return ResolvedSpec(
            spec=spec,
            gestalt_def_path=str(data["gestalt_def_path"]),
            stock_mesh_path=str(data.get("stock_mesh_path", "")),
            fragments=fragments,
            parts=rparts,
            balances=balances,
            materials=materials,
            extra_packages=list(data.get("extra_packages") or []),
            notes=list(data.get("notes") or []),
            total_vertices=data.get("total_vertices"),
            total_indices=data.get("total_indices"),
        )
    except (TypeError, KeyError) as ex:
        raise PackError(f"resolved data does not match this Armory's layout ({ex}); the pack "
                        "was probably built for a different Armory version") from ex


# ---------------------------------------------------------------------- safety
def string_problems(resolved: ResolvedSpec) -> list[str]:
    """Values the renderer pastes into code unquoted must be plain names and paths.

    A pack is data: nothing in it may be able to close a string or a docstring in the
    rendered module. Everything else reaches the code through ``repr``/``pprint``.
    """
    spec = resolved.spec
    problems: list[str] = []

    def ident(value: Any, what: str) -> None:
        if value is not None and not _IDENT.match(str(value)):
            problems.append(f"{what} {value!r} must be letters, digits and _ only")

    def path(value: Any, what: str) -> None:
        if value is not None and not _PATH.match(str(value)):
            problems.append(f"{what} {value!r} must be an object path (letters, digits, _ . : -)")

    def text(value: Any, what: str, quotes: bool = True) -> None:
        if value is None:
            return
        value = str(value)
        if '"""' in value or "\\" in value or (not quotes and ('"' in value or "\n" in value)):
            problems.append(f"{what} may not contain backslashes"
                            + ("" if quotes else ", quotes or line breaks")
                            + (" or triple quotes" if quotes else ""))

    ident(spec.name, "mod.name")
    ident(spec.package_stem, "package")
    ident(spec.options.save_package, "options.save_package")
    path(spec.mesh_path, "mesh_path")
    path(resolved.gestalt_def_path, "gestalt definition")
    path(resolved.stock_mesh_path or None, "stock mesh")
    text(spec.description, "mod.description")
    text(spec.author, "mod.author", quotes=False)
    text(spec.version, "mod.version", quotes=False)
    for frag in resolved.fragments:
        ident(frag.name, "fragment name")
        ident(frag.template_fragment, "template fragment")
    for part in resolved.parts:
        ident(part.part_name, "part name")
        path(part.part_path, "part path")
        path(part.template_part, "template part")
    for bal in resolved.balances:
        path(bal.path, "balance path")
        path(bal.template_balance, "template balance")
        for pool in bal.pools:
            path(pool, "item pool")
        if bal.title is not None:
            path(bal.title.path, "title path")
            text(bal.title.part_name, "title")
            text(bal.title.red_text, "red text")
    for mat in resolved.materials:
        path(mat.path, "material path")
        path(mat.parent, "material parent")
    for name in resolved.extra_packages:
        ident(name, "extra package")
    return problems


# ---------------------------------------------------------------------- claims
def claimed_paths(resolved: ResolvedSpec) -> dict[str, str]:
    """Every object a weapon constructs or owns -> what it is.

    Two weapons claiming one path would register it twice (fragments appended twice, a
    balance built twice): the Armory refuses the second pack. Mirrors lint L11.
    """
    spec = resolved.spec
    paths: dict[str, str] = {spec.package_stem: "package"}
    if spec.options.own_gestalt:
        paths[f"{spec.package_stem}.GestaltDef_{spec.name}"] = "own gestalt definition"
    for name in resolved.extra_packages:
        paths[name] = "extra package"
    for part in resolved.parts:
        paths[part.part_path] = "part"
    for bal in resolved.balances:
        paths[bal.path] = "balance"
        if bal.title is not None:
            paths[bal.title.path] = "title"
    for mat in resolved.materials:
        paths[mat.path] = "material"
    return paths


# ---------------------------------------------------------------------- one weapon
def load_weapon(entry: dict[str, Any]) -> ResolvedSpec:
    """A pack's weapon entry -> the :class:`ResolvedSpec` it was built from, checked."""
    if not isinstance(entry, dict) or "spec" not in entry or "resolved" not in entry:
        raise PackError("a weapon entry needs 'spec' and 'resolved'")
    try:
        spec = Spec.from_dict(entry["spec"])
    except SpecError as ex:
        raise PackError(f"its spec does not load in this Armory ({ex}); the pack was probably "
                        "built for a newer Armory") from ex
    if spec.options.test_harness:
        raise PackError("it is a test-harness build (options.test_harness); packs ship the "
                        "player build only")
    if not spec.options.own_gestalt:
        raise PackError("it does not set options.own_gestalt; every pack weapon must draw from "
                        "its own gestalt so several weapons can share a host type")
    if not spec.balances:
        raise PackError("it has no balances[]; the Armory spawns weapons by balance")
    resolved = resolved_from_dict(spec, entry["resolved"])
    problems = string_problems(resolved)
    if problems:
        raise PackError("; ".join(problems))
    declared = entry.get("features") or []
    unknown = sorted((set(features_of(spec)) | {str(f) for f in declared}) - SUPPORTED_FEATURES)
    if unknown:
        raise PackError(f"it needs feature(s) this Armory does not have: {', '.join(unknown)}")
    return resolved


def render_weapon(resolved: ResolvedSpec, origin: str, catalog_stamp: str = "") -> str:
    """The component module source for one pack weapon (what ``weapons/<id>.py`` would be)."""
    return render_mod(resolved, origin, catalog_stamp, component=True)


# ---------------------------------------------------------------------- one character skin
def load_character(entry: dict[str, Any], pack_packages: set[str] | None = None) -> dict[str, Any]:
    """A pack's character entry -> the skin the runtime reads, checked field by field.

    ``pack_packages``: the ``.upk`` names (without extension) the pack lists; every package the
    skin loads must be one of them, so a skin can never load something the pack did not ship.
    """
    if not isinstance(entry, dict):
        raise PackError("a character entry must be an object")
    sid = str(entry.get("id", ""))
    if not _SKIN_ID.match(sid):
        raise PackError(f"character skin id {sid!r} must be 1-32 of a-z, 0-9, _")
    where = f"skin {sid}"
    label = entry.get("label")
    if not isinstance(label, str) or not label.strip() or len(label) > 40 or "\n" in label:
        raise PackError(f"{where}: label must be a one-line name of up to 40 characters")
    if entry.get("character") not in VAULT_HUNTERS:
        raise PackError(f"{where}: character {entry.get('character')!r} is not one of "
                        f"{', '.join(VAULT_HUNTERS)}")
    packages = entry.get("packages")
    if not isinstance(packages, list) or not packages:
        raise PackError(f"{where}: it lists no packages")
    for pkg in packages:
        if not isinstance(pkg, str) or not _IDENT.match(pkg):
            raise PackError(f"{where}: package {pkg!r} must be letters, digits and _ only")
        if pack_packages is not None and pkg not in pack_packages:
            raise PackError(f"{where}: package {pkg} is not among the pack's packages")
    swaps = entry.get("swaps")
    if not isinstance(swaps, dict) or not swaps:
        raise PackError(f"{where}: it swaps no meshes")

    def path(value: Any, what: str) -> str:
        if not isinstance(value, str) or not _PATH.match(value):
            raise PackError(f"{where}: {what} {value!r} must be an object path")
        return value

    clean_swaps: dict[str, Any] = {}
    for source, swap in swaps.items():
        path(source, "source mesh")
        if not isinstance(swap, dict) or not isinstance(swap.get("material"), dict):
            raise PackError(f"{where}: swap for {source} needs 'mesh' and 'material'")
        mat = swap["material"]
        textures = mat.get("textures") or {}
        if not isinstance(textures, dict):
            raise PackError(f"{where}: material textures must be an object")
        clean_swaps[source] = {
            "mesh": path(swap.get("mesh"), "mesh"),
            "material": {
                "name": path(mat.get("name"), "material name"),
                "outer": path(mat.get("outer"), "material outer"),
                "parent": path(mat.get("parent"), "material parent"),
                "textures": {path(k, "texture parameter"): path(v, "texture")
                             for k, v in textures.items()},
            },
        }
    hide = entry.get("hide_mesh_prefixes") or []
    if not isinstance(hide, list):
        raise PackError(f"{where}: hide_mesh_prefixes must be a list")
    description = entry.get("description") or ""
    if not isinstance(description, str) or len(description) > 600:
        raise PackError(f"{where}: description must be text of up to 600 characters")
    return {"id": sid, "label": label.strip(), "character": entry["character"],
            "description": description, "packages": list(packages), "swaps": clean_swaps,
            "hide_mesh_prefixes": [path(h, "hidden mesh prefix") for h in hide],
            "default": bool(entry.get("default", True))}


def character_claims(skin: dict[str, Any]) -> dict[str, str]:
    """What a skin owns: its id and the material instances it constructs (two skins building
    one MIC would fight over it)."""
    claims = {f"skin:{skin['id']}": "character skin"}
    for swap in skin["swaps"].values():
        mat = swap["material"]
        claims[f"{mat['outer']}.{mat['name']}"] = "skin material"
    return claims
