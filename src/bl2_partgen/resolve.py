"""Resolve a :class:`~bl2_partgen.spec.Spec` against ``catalog/parts.json``.

Everything the generated mod needs that the spec is allowed to leave out lives
here: the gestalt definition path for a weapon type, the socket names a template
fragment maps, the usual outer group for a part slot, a sensible template part,
and the *runtime* part-list collection behind a balance (that is the list the
game actually rolls from, and the one the M2 mod appended to).

The resolved spec is also what :func:`to_proposals` turns into lint proposals
(``docs/LINT_PROPOSAL_SCHEMA.md``), one per part.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .spec import (
    ALL_SOCKETS, BalanceSpec, FragmentSpec, MaterialSpec, PartOverrides, PartSpec, Spec, TitleSpec,
)

__all__ = [
    "ResolveError",
    "ResolvedBalance",
    "ResolvedFragment",
    "ResolvedMaterial",
    "ResolvedPart",
    "ResolvedRegistration",
    "ResolvedSpec",
    "ResolvedTitle",
    "load_sidecar",
    "resolve",
    "sidecar_totals",
    "to_proposals",
]

#: sidecar keys the resolver reads; everything else in the file is ignored
SIDECAR_FIELDS = ("first_index", "num_primitives", "template_fragment", "dz",
                  "part_name", "new_vertices")


class ResolveError(ValueError):
    """The spec names something the catalog does not have."""


@dataclass
class ResolvedFragment:
    spec: FragmentSpec
    name: str
    template_fragment: str
    first_index: int
    num_primitives: int
    dz: float
    #: original socket names, in catalog order
    sockets: list[str]
    dz_sockets: list[str]
    #: ``{socket: [x, y, z]}`` in mesh space, restricted to :attr:`sockets`
    socket_overrides: dict[str, list[float]] = field(default_factory=dict)
    #: ``{origin, extent, radius}`` replacing the template's ReferencePoseBounds
    bounds_override: dict[str, Any] | None = None
    #: informational, straight out of the catalog
    template_bounds: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResolvedRegistration:
    balance: str
    field: str
    #: the runtime part-list collection the balance instantiates (what we append to)
    list_path: str
    #: True when the catalog only knew the cooked list, not a runtime one
    cooked_fallback: bool = False


@dataclass
class ResolvedPart:
    spec: PartSpec
    part_name: str
    part_path: str
    outer: str
    slot: str | None
    #: ``None`` for a part that draws nothing (see :class:`~bl2_partgen.spec.PartSpec`)
    fragment: str | None
    template_part: str
    registrations: list[ResolvedRegistration]
    #: ``(balance path, field)`` for every runtime balance of the spec that lists the part
    runtime_registrations: list[tuple[str, str]] = field(default_factory=list)
    overrides: PartOverrides | None = None


@dataclass
class ResolvedMaterial:
    spec: MaterialSpec
    name: str
    path: str
    outer: str
    parent: str
    texture_parameters: dict[str, str]
    vector_parameters: dict[str, list[float]]
    scalar_parameters: dict[str, float]


@dataclass
class ResolvedTitle:
    spec: TitleSpec
    path: str
    outer: str
    template: str
    part_name: str
    red_text: str | None
    #: full paths of the parts whose TitleList becomes this title
    on_part_paths: list[str]
    name_is_unique: bool = False


@dataclass
class ResolvedBalance:
    spec: BalanceSpec
    name: str
    path: str
    outer: str
    template_balance: str
    #: the template's runtime part-list collection (what ours is cloned from)
    template_collection: str
    collection_name: str
    collection_path: str
    #: field -> full part paths, in spec order
    part_lists: dict[str, list[str]]
    title: ResolvedTitle | None
    pools: list[str]
    suppress_prefix: bool = False


@dataclass
class ResolvedSpec:
    spec: Spec
    gestalt_def_path: str
    stock_mesh_path: str
    fragments: list[ResolvedFragment]
    parts: list[ResolvedPart]
    balances: list[ResolvedBalance] = field(default_factory=list)
    materials: list[ResolvedMaterial] = field(default_factory=list)
    extra_packages: list[str] = field(default_factory=list)
    #: non-fatal observations made while resolving defaults
    notes: list[str] = field(default_factory=list)
    #: totals from the build sidecar, when one was supplied: what lint L9 budgets
    #: against the uint16 index buffer.  ``None`` when the spec was resolved without
    #: a sidecar, which is how every pre-M6 proposal was rendered.
    total_vertices: int | None = None
    total_indices: int | None = None

    def fragment(self, name: str) -> ResolvedFragment:
        for frag in self.fragments:
            if frag.name == name:
                return frag
        raise ResolveError(f"no fragment {name!r} in this spec")


def _weapon_type(catalog: dict[str, Any], key: str) -> dict[str, Any]:
    types = catalog.get("weapon_types", {})
    entry = types.get(key)
    if entry is None:
        raise ResolveError(
            f"weapon_type {key!r} is not a catalog key; known: {', '.join(sorted(types))}"
        )
    return entry


def _template_fragment(weapon_type: dict[str, Any], name: str, key: str) -> dict[str, Any]:
    frag = weapon_type.get("fragments", {}).get(name)
    if frag is None:
        raise ResolveError(f"template fragment {name!r} does not exist in weapon type {key!r}")
    return frag


def _default_outer(catalog: dict[str, Any], weapon_type: str, slot: str | None) -> str | None:
    """The group most existing parts of this weapon type + slot are exported under."""
    outers: Counter[str] = Counter()
    for path, part in catalog.get("parts", {}).items():
        if part.get("weapon_type") != weapon_type:
            continue
        if slot is not None and part.get("slot") != slot:
            continue
        head, _, _ = path.rpartition(".")
        if head:
            outers[head] += 1
    if not outers:
        return None
    # deterministic: most common first, then alphabetical
    return sorted(outers.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _default_template_part(
    catalog: dict[str, Any], weapon_type: str, fragment: str, slot: str | None
) -> str | None:
    """An existing part that already uses the fragment we are cloning from."""
    candidates = [
        path
        for path, part in catalog.get("parts", {}).items()
        if part.get("fragment") == fragment
        and part.get("weapon_type") == weapon_type
        and (slot is None or part.get("slot") == slot)
    ]
    return sorted(candidates)[0] if candidates else None


def _part_slot(catalog: dict[str, Any], template_part: str | None, declared: str | None) -> str | None:
    if declared:
        return declared
    if template_part:
        entry = catalog.get("parts", {}).get(template_part)
        if entry:
            return entry.get("slot")
    return None


def _runtime_list(catalog: dict[str, Any], balance_path: str) -> tuple[str, bool]:
    balance = catalog.get("balances", {}).get(balance_path)
    if balance is None:
        raise ResolveError(
            f"balance {balance_path!r} is not in the catalog (expected a WeaponBalanceDefinition path)"
        )
    runtime = balance.get("runtime_part_list_collection")
    if runtime:
        return str(runtime), False
    cooked = balance.get("weapon_part_list_collection") or balance.get("part_list_collection")
    if cooked:
        return str(cooked), True
    raise ResolveError(f"balance {balance_path!r} has no part-list collection to register into (F5)")


def sidecar_totals(source: dict[str, Any] | str | Path) -> dict[str, int]:
    """``total_vertices`` / ``total_indices`` from a build sidecar, if it states them.

    They are package-wide, not per-fragment, so they live at the sidecar's top level
    and are what lint **L9** budgets against ``MAX_GPU_VERTICES`` (65,535 -- the LOD
    index buffer is uint16).  M6 is the first build where the answer is not obvious:
    the AK adds 27,018 vertices to the gestalt's 27,211.
    """
    data = source if isinstance(source, dict) else json.loads(
        Path(source).read_text(encoding="utf-8"))
    out: dict[str, int] = {}
    for key in ("total_vertices", "total_indices"):
        value = data.get(key)
        if value is not None:
            out[key] = int(value)
    return out


def load_sidecar(source: dict[str, Any] | str | Path) -> dict[str, dict[str, Any]]:
    """``fragment name -> {first_index, num_primitives, ...}`` from a build sidecar.

    Accepts both shapes the writer produces: the single-fragment
    ``.fragment.json`` (one fragment at the top level) and the multi-fragment
    one, which lists every fragment under ``fragments`` *and* repeats the first
    at the top level.  Only the fields in :data:`SIDECAR_FIELDS` are read.
    """
    data = source if isinstance(source, dict) else json.loads(
        Path(source).read_text(encoding="utf-8"))
    rows = data.get("fragments")
    if isinstance(rows, dict):
        rows = [rows]
    entries = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    if not entries and (data.get("fragment") or data.get("name")):
        entries = [data]
    out: dict[str, dict[str, Any]] = {}
    for entry in entries:
        name = str(entry.get("fragment") or entry.get("name") or "")
        if not name:
            continue
        out[name] = {key: entry[key] for key in SIDECAR_FIELDS if entry.get(key) is not None}
    return out


def resolve(
    spec: Spec,
    catalog: dict[str, Any],
    sidecar: dict[str, Any] | str | Path | None = None,
) -> ResolvedSpec:
    """Fill in every default the spec left out; raise :class:`ResolveError` if it cannot.

    With a ``sidecar`` (a ``.fragment.json`` written by the package build), each
    fragment's ``first_index`` / ``num_primitives`` are taken from the file the
    package was actually written with, so a stale spec cannot point a part at a
    range the mesh does not have.  Without one, nothing changes.
    """
    weapon_type = _weapon_type(catalog, spec.weapon_type)
    notes: list[str] = []
    built = load_sidecar(sidecar) if sidecar is not None else {}
    totals = sidecar_totals(sidecar) if sidecar is not None else {}
    if built:
        missing = [f.name for f in spec.fragments if f.name not in built]
        if missing:
            raise ResolveError(
                f"the sidecar lists fragments {', '.join(sorted(built))} but the spec needs "
                f"{', '.join(missing)}; rebuild the package or fix the spec"
            )

    fragments: list[ResolvedFragment] = []
    for frag in spec.fragments:
        row = built.get(frag.name, {})
        first_index, num_primitives = frag.first_index, frag.num_primitives
        for key, current in (("first_index", first_index), ("num_primitives", num_primitives)):
            if key not in row:
                continue
            value = int(row[key])
            if value != current:
                notes.append(
                    f"fragment {frag.name}: {key} {current} -> {value} (from the build sidecar)"
                )
            if key == "first_index":
                first_index = value
            else:
                num_primitives = value
        template = _template_fragment(weapon_type, frag.template_fragment, spec.weapon_type)
        available = [str(s["original"]) for s in template.get("sockets", [])]
        if frag.sockets == ALL_SOCKETS:
            sockets = list(available)
            notes.append(
                f"fragment {frag.name}: sockets=all resolved to {', '.join(sockets) or '(none)'}"
            )
        else:
            sockets = [str(s) for s in frag.sockets]
            missing = [s for s in sockets if s not in available]
            if missing:
                raise ResolveError(
                    f"fragment {frag.name!r}: template {frag.template_fragment!r} does not map "
                    f"socket(s) {', '.join(missing)}; it maps {', '.join(available) or '(none)'}"
                )
        overrides = dict(frag.socket_overrides)
        stray = [s for s in overrides if s not in sockets]
        if stray:
            raise ResolveError(
                f"fragment {frag.name!r}: socket_overrides names {', '.join(sorted(stray))}, "
                f"which this fragment does not clone ({', '.join(sockets) or '(none)'})"
            )
        if overrides:
            notes.append(
                f"fragment {frag.name}: socket(s) "
                f"{', '.join(f'{k}->{v}' for k, v in sorted(overrides.items()))} "
                "placed in mesh space (re-parented to the Root bone), not taken from the template"
            )
            both = sorted(set(overrides) & set(frag.dz_sockets))
            if both and frag.dz:
                notes.append(
                    f"fragment {frag.name}: dz={frag.dz} is NOT applied to overridden "
                    f"socket(s) {', '.join(both)}; the override is absolute"
                )
        fragments.append(
            ResolvedFragment(
                spec=frag,
                name=frag.name,
                template_fragment=frag.template_fragment,
                first_index=first_index,
                num_primitives=num_primitives,
                dz=frag.dz,
                sockets=sockets,
                dz_sockets=[s for s in frag.dz_sockets if s in sockets],
                socket_overrides=overrides,
                bounds_override=frag.bounds_override,
                template_bounds=template.get("bounds", {}),
            )
        )
        if frag.bounds_override is not None:
            notes.append(
                f"fragment {frag.name}: bounds_override origin {frag.bounds_override['origin']} "
                f"extent {frag.bounds_override['extent']} radius {frag.bounds_override['radius']} "
                "replaces the template's ReferencePoseBounds"
            )

    by_name = {f.name: f for f in fragments}
    parts: list[ResolvedPart] = []
    for part in spec.parts:
        frag = by_name[part.fragment] if part.fragment is not None else None
        template_part = part.template_part
        if template_part is None and frag is not None:
            template_part = _default_template_part(
                catalog, spec.weapon_type, frag.template_fragment, part.slot
            )
        if template_part is None:
            raise ResolveError(
                f"part {part.part_name!r}: no template_part given and no catalog part uses "
                f"fragment {frag.template_fragment!r} in weapon type {spec.weapon_type!r}"
            )
        if template_part not in catalog.get("parts", {}):
            raise ResolveError(
                f"part {part.part_name!r}: template_part {template_part!r} is not in the catalog"
            )
        if part.template_part is None:
            notes.append(f"part {part.part_name}: template_part defaulted to {template_part}")
        if frag is None:
            entry = catalog.get("parts", {}).get(template_part, {})
            if entry.get("gestalt"):
                notes.append(
                    f"part {part.part_name}: draws nothing, but its template {template_part} is a "
                    "gestalt part; the generated mod forces bIsGestaltMode False on the clone"
                )
            else:
                notes.append(
                    f"part {part.part_name}: draws nothing (no fragment; cloned from the "
                    f"non-gestalt {template_part})"
                )
        slot = _part_slot(catalog, template_part, part.slot)
        outer = part.outer or _default_outer(catalog, spec.weapon_type, slot)
        if outer is None:
            outer, _, _ = template_part.rpartition(".")
            notes.append(f"part {part.part_name}: outer defaulted to the template's outer {outer}")
        elif part.outer is None:
            notes.append(f"part {part.part_name}: outer defaulted to {outer}")

        registrations: list[ResolvedRegistration] = []
        for target in part.register_in:
            list_path, fallback = _runtime_list(catalog, target.balance)
            if fallback:
                notes.append(
                    f"part {part.part_name}: balance {target.balance} has no runtime part-list "
                    f"collection in the catalog; falling back to the cooked {list_path}"
                )
            registrations.append(
                ResolvedRegistration(
                    balance=target.balance,
                    field=target.field,
                    list_path=list_path,
                    cooked_fallback=fallback,
                )
            )

        parts.append(
            ResolvedPart(
                spec=part,
                part_name=part.part_name,
                part_path=f"{outer}.{part.part_name}",
                outer=outer,
                slot=slot,
                fragment=part.fragment,
                template_part=template_part,
                registrations=registrations,
                overrides=part.overrides,
            )
        )
        if part.overrides is not None:
            edits = []
            if part.overrides.properties:
                edits.append(f"{len(part.overrides.properties)} propert"
                             f"{'y' if len(part.overrides.properties) == 1 else 'ies'}")
            if part.overrides.clear_arrays:
                edits.append(f"{', '.join(part.overrides.clear_arrays)} emptied")
            for key, rows in (("WeaponAttributeEffects", part.overrides.weapon_attribute_effects),
                              ("ExternalAttributeEffects", part.overrides.external_attribute_effects),
                              ("ZoomWeaponAttributeEffects", part.overrides.zoom_weapon_attribute_effects),
                              ("ZoomExternalAttributeEffects", part.overrides.zoom_external_attribute_effects),
                              ("AttributeSlotUpgrades", part.overrides.attribute_slot_upgrades)):
                if rows is not None:
                    edits.append(f"{key} replaced by {len(rows)} row(s)")
            notes.append(f"part {part.part_name}: overrides on the clone: {', '.join(edits)}")

    materials: list[ResolvedMaterial] = []
    extra_names = [p.name for p in spec.extra_packages]
    for mat in spec.materials:
        for pname, tex_path in mat.texture_parameters.items():
            head = tex_path.split(".", 1)[0]
            if head in extra_names:
                notes.append(f"material {mat.name}: {pname} -> {tex_path} (from extra package {head})")
            elif head == spec.package_stem:
                notes.append(f"material {mat.name}: {pname} -> {tex_path} (from the spec's package)")
            else:
                notes.append(f"material {mat.name}: {pname} -> {tex_path} (a cooked texture; not "
                             "checked, the catalog has no textures)")
        notes.append(f"material {mat.name}: {mat.path} parented to {mat.parent}")
        materials.append(ResolvedMaterial(
            spec=mat, name=mat.name, path=mat.path, outer=mat.outer, parent=mat.parent,
            texture_parameters=dict(mat.texture_parameters),
            vector_parameters={k: list(v) for k, v in mat.vector_parameters.items()},
            scalar_parameters=dict(mat.scalar_parameters),
        ))
    if extra_names:
        notes.append(f"extra package(s) loaded at the menu tick: {', '.join(extra_names)}")

    by_part_name = {p.part_name: p for p in parts}
    balances: list[ResolvedBalance] = []
    for bal in spec.balances:
        catalog_balances = catalog.get("balances", {})
        if bal.path in catalog_balances:
            raise ResolveError(
                f"balance {bal.name!r}: {bal.path} already exists in the catalog; a runtime "
                "balance needs a new path"
            )
        template_collection, fallback = _runtime_list(catalog, bal.template_balance)
        if fallback:
            notes.append(
                f"balance {bal.name}: template {bal.template_balance} has no runtime part-list "
                f"collection in the catalog; the mod clones its RuntimePartListCollection at "
                f"runtime regardless (catalog knows only {template_collection})"
            )
        lists: dict[str, list[str]] = {}
        for fld, refs in bal.part_lists.items():
            paths = []
            for ref in refs:
                if ref in by_part_name:
                    paths.append(by_part_name[ref].part_path)
                    by_part_name[ref].runtime_registrations.append((bal.path, fld))
                elif ref in catalog.get("parts", {}):
                    paths.append(ref)
                else:
                    raise ResolveError(
                        f"balance {bal.name!r}: part_lists[{fld!r}] names {ref!r}, which is "
                        "neither a part of this spec nor a catalog part"
                    )
            lists[fld] = paths
        title: ResolvedTitle | None = None
        if bal.title is not None:
            t = bal.title
            title = ResolvedTitle(
                spec=t, path=t.path, outer=t.outer, template=t.template,
                part_name=t.part_name, red_text=t.red_text,
                on_part_paths=[by_part_name[p].part_path for p in t.on_parts],
                name_is_unique=t.name_is_unique,
            )
            notes.append(
                f"balance {bal.name}: title {t.path} ({t.part_name!r}) cloned from {t.template}, "
                f"carried by {', '.join(t.on_parts)}"
            )
        notes.append(
            f"balance {bal.name}: {bal.path} cloned from {bal.template_balance}; own runtime "
            f"part list {bal.collection_path} with "
            + ", ".join(f"{fld}=[{', '.join(p.rsplit('.', 1)[-1] for p in paths)}]"
                        for fld, paths in lists.items())
            + (f"; appended to pool(s) {', '.join(bal.pools)}" if bal.pools else "")
        )
        balances.append(
            ResolvedBalance(
                spec=bal, name=bal.name, path=bal.path, outer=bal.outer,
                template_balance=bal.template_balance,
                template_collection=template_collection,
                collection_name=bal.collection_name, collection_path=bal.collection_path,
                part_lists=lists, title=title, pools=list(bal.pools),
                suppress_prefix=bal.suppress_prefix,
            )
        )
        if bal.suppress_prefix:
            notes.append(f"balance {bal.name}: prefix suppressed on its weapons (name hooks)")

    return ResolvedSpec(
        spec=spec,
        gestalt_def_path=str(weapon_type["def_path"]),
        stock_mesh_path=str(weapon_type.get("mesh_path", "")),
        fragments=fragments,
        parts=parts,
        balances=balances,
        materials=materials,
        extra_packages=extra_names,
        notes=notes,
        total_vertices=totals.get("total_vertices"),
        total_indices=totals.get("total_indices"),
    )


def to_proposals(resolved: ResolvedSpec) -> list[dict[str, Any]]:
    """One lint proposal per part (``docs/LINT_PROPOSAL_SCHEMA.md``)."""
    spec = resolved.spec
    proposals: list[dict[str, Any]] = []
    by_path = {b.path: b for b in resolved.balances}
    for index, part in enumerate(resolved.parts):
        common = {
            "part_path": part.part_path,
            "part_slot": part.slot,
            "register_in_lists": [
                f"{reg.list_path}.{reg.field}" for reg in part.registrations
            ],
            "register_at": spec.options.register_at,
            "keep_alive": spec.options.keep_alive,
            "objects": [resolved.gestalt_def_path, part.template_part],
        }
        # only stated when the spec has runtime balances, so earlier proposals are unchanged
        if part.runtime_registrations:
            common["register_in_runtime_lists"] = [
                f"{by_path[bal].collection_path}.{fld}" for bal, fld in part.runtime_registrations
            ]
        if index == 0 and resolved.materials:
            common["new_materials"] = [
                {"path": m.path, "parent": m.parent, "textures": list(m.texture_parameters.values())}
                for m in resolved.materials
            ]
        if index == 0 and resolved.extra_packages:
            common["extra_packages"] = list(resolved.extra_packages)
        if index == 0 and resolved.balances:
            # the balance facts ride on the first proposal: one L10 report, not one per part
            common["new_balances"] = [
                {
                    "path": bal.path,
                    "template": bal.template_balance,
                    "collection": bal.collection_path,
                    "title": bal.title.path if bal.title else None,
                    "title_template": bal.title.template if bal.title else None,
                    "pools": list(bal.pools),
                }
                for bal in resolved.balances
            ]
        if part.fragment is None:
            # no fragment to lint: L3 / L6 / L7 / L8 are what apply to a part that draws nothing
            proposals.append({
                "package": spec.package,
                "mesh_path": spec.mesh_path,
                "weapon_type": spec.weapon_type,
                "part_only": True,
                **common,
            })
            continue
        frag = resolved.fragment(part.fragment)
        proposals.append(
            {
                "package": spec.package,
                "mesh_path": spec.mesh_path,
                "weapon_type": spec.weapon_type,
                "fragment": frag.name,
                "first_index": frag.first_index,
                "num_primitives": frag.num_primitives,
                "template_fragment": frag.template_fragment,
                "sockets": list(frag.sockets),
                **common,
            }
        )
        # only stated when the build sidecar did, so a proposal rendered without one
        # is exactly the proposal it always was (L9 then reports "not checked")
        if resolved.total_vertices is not None:
            proposals[-1]["total_vertices"] = resolved.total_vertices
        if resolved.total_indices is not None:
            proposals[-1]["total_indices"] = resolved.total_indices
    return proposals
