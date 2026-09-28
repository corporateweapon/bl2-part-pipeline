"""Build ``catalog/parts.json`` -- the pipeline's source of truth.

The catalog answers the questions the linter (:mod:`bl2_lint`), the Blender
addon and the SDK emitter all need and none of which can be answered from a
single file:

``weapon_types``
    the nine base-game gestalt definitions: which ``SkeletalMesh`` they drive,
    how its index buffer is tiled into named fragments, each fragment's
    reference-pose bounds and its mangled sockets (D1/D2).
``parts``
    every ``WeaponPartDefinition`` in the OpenBLCMM dumps: slot, fragment,
    weapon type, owning package(s) and every part list that references it (F5).
``part_lists`` / ``balances``
    the registration surface: the lists a new part has to be appended to and
    the balances that point at them.
``packages``
    per package file: what it exports, whether the exe SHA1-verifies it (F12)
    and its runtime load-order rank (D3, F1).
``multi_owner``
    every object path exported by more than one package -- the load-order
    shadowing case (F1), 9,391 paths of which 160 are ``SkeletalMesh``.

Everything is derived from read-only inputs under ``scratch/`` (see
:mod:`bl2_catalog.sources`); nothing here touches the game install.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .dumps import DumpObject, iter_dump_objects, obj_ref, parse_value, unquote
from .sources import ExportIndex, Sources, file_digest

__all__ = ["CATALOG_SCHEMA_VERSION", "build_catalog", "load_catalog", "weapon_type_key"]

#: bumped whenever the emitted structure changes incompatibly
CATALOG_SCHEMA_VERSION = 1

#: slot fields on a WeaponPartListCollectionDefinition, in weapon-assembly order
COLLECTION_SLOTS = (
    "BodyPartData",
    "GripPartData",
    "BarrelPartData",
    "SightPartData",
    "StockPartData",
    "ElementalPartData",
    "Accessory1PartData",
    "Accessory2PartData",
    "MaterialPartData",
)

#: WeaponTypeDefinition fields that name a WeaponPartListDefinition
TYPE_PART_LIST_FIELDS = (
    "BodyParts",
    "GripParts",
    "BarrelParts",
    "SightParts",
    "StockParts",
    "ElementalParts",
    "Accessory1Parts",
    "Accessory2Parts",
    "MaterialParts",
)

#: the single slot field on a WeaponPartListDefinition
PART_LIST_SLOT = "WeightedParts"

_NONE = "None"


def weapon_type_key(def_path: str) -> str:
    """``Weap_AssaultRifles.GestaltDef_AssaultRifle`` -> ``AssaultRifle``."""
    leaf = def_path.replace(":", ".").rsplit(".", 1)[-1]
    return leaf[len("GestaltDef_"):] if leaf.startswith("GestaltDef_") else leaf


def _f(raw: Any, digits: int = 6) -> float:
    """Dump float -> rounded Python float (the dumps print 6 decimals)."""
    try:
        return round(float(str(raw)), digits)
    except (TypeError, ValueError):
        return 0.0


def _i(raw: Any) -> int:
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return 0


def _ref_path(raw: str | None) -> str | None:
    """``Class'Path'`` -> ``Path``; ``None``/missing -> ``None``."""
    if raw is None:
        return None
    ref = obj_ref(raw)
    if ref is not None:
        return ref.path
    return None


def _vec(struct: Any) -> list[float]:
    if not isinstance(struct, dict):
        return [0.0, 0.0, 0.0]
    return [_f(struct.get("X")), _f(struct.get("Y")), _f(struct.get("Z"))]


def _rot(struct: Any) -> list[int]:
    if not isinstance(struct, dict):
        return [0, 0, 0]
    return [_i(struct.get("Pitch")), _i(struct.get("Yaw")), _i(struct.get("Roll"))]


def _is_default(obj: DumpObject) -> bool:
    return obj.name.startswith("Default__")


#: e.g. ``...AR_Vladof_5_Sherdifier:WeaponPartListCollectionDefinition_39`` -- an
#: object the game instantiates at load time from the cooked ``:PartList``. It is
#: in the OpenBLCMM dumps (taken from a running game) but in no package file, so
#: it must not be mistaken for DLC-only content.
_RUNTIME_LEAF = re.compile(r"[A-Za-z]+Definition_\d+$")


def _is_runtime_object(obj_path: str) -> bool:
    leaf = obj_path.replace(":", ".").rsplit(".", 1)[-1]
    return _RUNTIME_LEAF.fullmatch(leaf) is not None


# --------------------------------------------------------------- gestalt defs
def _tiling(fragments: dict[str, dict[str, Any]], index_count: int) -> dict[str, Any]:
    """Check the fragment ranges tile ``[0, index_count)`` exactly.

    A fragment name may own more than one range (``SR_Body_Jakobs`` has three,
    ``Acc_Barrel_Elemental2`` on the launcher has five), so this walks
    ``fragment["ranges"]``, not the one summary range per name.
    """
    ranges = sorted(
        (r["first_index"], r["first_index"] + 3 * r["num_primitives"], name)
        for name, f in fragments.items()
        for r in f["ranges"]
    )
    overlaps: list[dict[str, Any]] = []
    gaps: list[list[int]] = []
    cursor = 0
    for start, end, name in ranges:
        if start < cursor:
            overlaps.append({"fragment": name, "first_index": start, "overlaps_until": cursor})
        elif start > cursor:
            gaps.append([cursor, start])
        cursor = max(cursor, end)
    covered = sum(3 * f["num_primitives"] for f in fragments.values())
    return {
        "ok": not overlaps and not gaps and covered == index_count and cursor == index_count,
        "contiguous": not gaps and not overlaps,
        "overlaps": overlaps,
        "gaps": gaps,
        "covered_indices": covered,
        "index_count": index_count,
        "triangle_count": index_count // 3,
    }


def _sockets_by_mesh(sources: Sources, mesh_paths: set[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """``{mesh path: {socket name: socket record}}`` for the gestalt meshes only."""
    prefixes = tuple(f"{p}:" for p in mesh_paths)
    out: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for obj in iter_dump_objects(
        sources.dump_paths("SkeletalMeshSocket"),
        classes=("SkeletalMeshSocket",),
        path_prefixes=prefixes,
    ):
        mesh_path = obj.path.split(":", 1)[0]
        socket_name = unquote(obj.get("SocketName", ""))
        if not socket_name:
            continue
        out[mesh_path][socket_name] = {
            "object": obj.path,
            "bone": unquote(obj.get("BoneName", _NONE)),
            "location": _vec(parse_value(obj.get("RelativeLocation", "()"))),
            "rotation": _rot(parse_value(obj.get("RelativeRotation", "()"))),
            "scale": _vec(parse_value(obj.get("RelativeScale", "()"))),
        }
    return out


def _build_weapon_types(
    sources: Sources, exports: ExportIndex
) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]], dict[str, str]]:
    """Return ``(weapon_types, fragment name -> keys, def path -> key)``."""
    defs = [
        obj
        for obj in iter_dump_objects(
            sources.dump_paths("GestaltSkeletalMeshDefinition"),
            classes=("GestaltSkeletalMeshDefinition",),
        )
        if not _is_default(obj)
    ]
    mesh_paths = {
        path for path in (_ref_path(o.get("GestaltSkeletalMesh")) for o in defs) if path
    }
    sockets = _sockets_by_mesh(sources, mesh_paths)

    # WeaponTypeDefinition -> gestalt def, so each weapon type lists its users
    type_defs_by_gestalt: dict[str, list[str]] = defaultdict(list)
    for obj in iter_dump_objects(
        sources.dump_paths("WeaponTypeDefinition"), classes=("WeaponTypeDefinition",)
    ):
        if _is_default(obj):
            continue
        gestalt = _ref_path(obj.get("GestaltMesh")) or _ref_path(
            obj.get("GestaltSkeletalMeshDefinition")
        )
        if gestalt:
            type_defs_by_gestalt[gestalt].append(obj.path)

    weapon_types: dict[str, dict[str, Any]] = {}
    fragment_index: dict[str, set[str]] = defaultdict(set)
    key_by_def: dict[str, str] = {}

    for obj in defs:
        key = weapon_type_key(obj.path)
        key_by_def[obj.path] = key
        mesh_path = _ref_path(obj.get("GestaltSkeletalMesh"))
        infos = obj.array("GestaltInfos")
        parsed = parse_value(infos[0]) if infos else {}
        raw_parts = parsed.get("Parts", []) if isinstance(parsed, dict) else []
        if isinstance(raw_parts, dict):  # a one-entry array collapses to a struct
            raw_parts = [raw_parts]

        bounds_by_fragment: dict[str, dict[str, Any]] = {}
        for raw in obj.array("GestaltPartBounds"):
            entry = parse_value(raw)
            if not isinstance(entry, dict):
                continue
            rpb = entry.get("ReferencePoseBounds")
            rpb = rpb if isinstance(rpb, dict) else {}
            bounds_by_fragment[unquote(str(entry.get("SkeletalMeshFragmentName", "")))] = {
                "origin": _vec(rpb.get("Origin")),
                "extent": _vec(rpb.get("BoxExtent")),
                "radius": _f(rpb.get("SphereRadius")),
            }

        mappings_by_fragment: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for raw in obj.array("GestaltSocketMappings"):
            entry = parse_value(raw)
            if not isinstance(entry, dict):
                continue
            mappings_by_fragment[unquote(str(entry.get("SkeletalMeshFragmentName", "")))].append(
                (
                    unquote(str(entry.get("OriginalSocketName", ""))),
                    unquote(str(entry.get("MangledSocketName", ""))),
                )
            )

        mesh_sockets = sockets.get(mesh_path or "", {})
        fragments: dict[str, dict[str, Any]] = {}
        for part in raw_parts:
            if not isinstance(part, dict):
                continue
            name = unquote(str(part.get("SkeletalMeshFragmentName", "")))
            if not name:
                continue
            socket_records = []
            for original, mangled in sorted(mappings_by_fragment.get(name, [])):
                found = mesh_sockets.get(mangled)
                socket_records.append(
                    {
                        "original": original,
                        "mangled": mangled,
                        "bone": found["bone"] if found else None,
                        "location": found["location"] if found else None,
                        "rotation": found["rotation"] if found else None,
                        "resolved": found is not None,
                    }
                )
            entry = {
                "first_index": _i(part.get("FirstIndex")),
                "num_primitives": _i(part.get("NumPrimitives")),
                "material_index": _i(part.get("MaterialIndex")),
            }
            existing = fragments.get(name)
            if existing is None:
                fragments[name] = {
                    **entry,
                    "ranges": [entry],
                    "range_count": 1,
                    "bounds": bounds_by_fragment.get(name),
                    "sockets": socket_records,
                }
            else:
                # A fragment name can own several disjoint index ranges (the
                # launcher's Acc_Barrel_Elemental2 has five). Keep them all; the
                # summary first_index/num_primitives describe the whole fragment.
                existing["ranges"].append(entry)
                existing["range_count"] = len(existing["ranges"])
                existing["first_index"] = min(r["first_index"] for r in existing["ranges"])
                existing["num_primitives"] = sum(r["num_primitives"] for r in existing["ranges"])
            fragment_index[name].add(key)

        index_count = max(
            (r["first_index"] + 3 * r["num_primitives"]
             for f in fragments.values() for r in f["ranges"]),
            default=0,
        )
        def_owners = exports.owners_of(obj.path)
        mesh_owners = exports.owners_of(mesh_path or "")
        weapon_types[key] = {
            "def_path": obj.path,
            "mesh_path": mesh_path,
            "packages": def_owners,
            "mesh_packages": mesh_owners,
            "dlc": not def_owners,
            "index_count": index_count,
            "fragment_count": len(fragments),
            "socket_count": sum(len(f["sockets"]) for f in fragments.values()),
            "weapon_type_defs": sorted(type_defs_by_gestalt.get(obj.path, [])),
            "tiling": _tiling(fragments, index_count),
            "fragments": dict(sorted(fragments.items())),
        }
    return dict(sorted(weapon_types.items())), dict(fragment_index), key_by_def


# ------------------------------------------------------------------ part lists
def _weighted_parts(raw: Any) -> list[str | None]:
    """Pull the ``Part=`` object paths out of a ``WeightedParts`` literal."""
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str | None] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        out.append(_ref_path(str(entry.get("Part", _NONE))))
    return out


def _build_part_lists(
    sources: Sources, exports: ExportIndex, key_by_def: dict[str, str]
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, str]]]]:
    """Return ``(part_lists, part path -> [{list, slot}])``."""
    # WeaponTypeDefinition gives both the gestalt key for a collection's
    # AssociatedWeaponType and the owner of each standalone WeaponPartListDefinition.
    key_by_type_def: dict[str, str] = {}
    key_by_list_path: dict[str, str] = {}
    for obj in iter_dump_objects(
        sources.dump_paths("WeaponTypeDefinition"), classes=("WeaponTypeDefinition",)
    ):
        if _is_default(obj):
            continue
        gestalt = _ref_path(obj.get("GestaltMesh")) or _ref_path(
            obj.get("GestaltSkeletalMeshDefinition")
        )
        key = key_by_def.get(gestalt or "")
        if key is None:
            continue
        key_by_type_def[obj.path] = key
        for field_name in TYPE_PART_LIST_FIELDS:
            list_path = _ref_path(obj.get(field_name))
            if list_path:
                key_by_list_path.setdefault(list_path, key)

    part_lists: dict[str, dict[str, Any]] = {}
    in_lists: dict[str, list[dict[str, str]]] = defaultdict(list)

    def record(list_path: str, slot: str, parts: list[str | None]) -> list[str | None]:
        for part_path in parts:
            if part_path:
                in_lists[part_path].append({"list": list_path, "slot": slot})
        return parts

    for obj in iter_dump_objects(
        sources.dump_paths("WeaponPartListCollectionDefinition"),
        classes=("WeaponPartListCollectionDefinition",),
    ):
        if _is_default(obj):
            continue
        associated = _ref_path(obj.get("AssociatedWeaponType"))
        slots: dict[str, Any] = {}
        for slot in COLLECTION_SLOTS:
            raw = obj.get(slot)
            if raw is None:
                continue
            value = parse_value(raw)
            enabled = str(value.get("bEnabled", "False")) == "True" if isinstance(value, dict) else False
            parts = _weighted_parts(value.get("WeightedParts")) if isinstance(value, dict) else []
            slots[slot] = {"enabled": enabled, "parts": record(obj.path, slot, parts)}
        owners = exports.owners_of(obj.path)
        runtime = _is_runtime_object(obj.path)
        part_lists[obj.path] = {
            "class": "WeaponPartListCollectionDefinition",
            "packages": owners,
            "runtime": runtime,
            "dlc": not owners and not runtime,
            "associated_weapon_type": associated,
            "weapon_type": key_by_type_def.get(associated or ""),
            "slots": slots,
        }

    for obj in iter_dump_objects(
        sources.dump_paths("WeaponPartListDefinition"), classes=("WeaponPartListDefinition",)
    ):
        if _is_default(obj):
            continue
        parts: list[str | None] = []
        for raw in obj.array(PART_LIST_SLOT):
            entry = parse_value(raw)
            parts.append(
                _ref_path(str(entry.get("Part", _NONE))) if isinstance(entry, dict) else None
            )
        owners = exports.owners_of(obj.path)
        runtime = _is_runtime_object(obj.path)
        part_lists[obj.path] = {
            "class": "WeaponPartListDefinition",
            "packages": owners,
            "runtime": runtime,
            "dlc": not owners and not runtime,
            "associated_weapon_type": None,
            "weapon_type": key_by_list_path.get(obj.path),
            "slots": {
                PART_LIST_SLOT: {"enabled": True, "parts": record(obj.path, PART_LIST_SLOT, parts)}
            },
        }

    return dict(sorted(part_lists.items())), {k: v for k, v in sorted(in_lists.items())}


def _build_balances(sources: Sources, exports: ExportIndex, part_lists: dict[str, dict[str, Any]]
                    ) -> dict[str, dict[str, Any]]:
    balances: dict[str, dict[str, Any]] = {}
    for obj in iter_dump_objects(
        sources.dump_paths("WeaponBalanceDefinition"), classes=("WeaponBalanceDefinition",)
    ):
        if _is_default(obj):
            continue
        runtime = _ref_path(obj.get("RuntimePartListCollection"))
        cooked = _ref_path(obj.get("WeaponPartListCollection"))
        owners = exports.owners_of(obj.path)
        weapon_type = None
        for candidate in (runtime, cooked):
            entry = part_lists.get(candidate or "")
            if entry is not None and entry.get("weapon_type"):
                weapon_type = entry["weapon_type"]
                break
        balances[obj.path] = {
            "packages": owners,
            "runtime": _is_runtime_object(obj.path),
            "dlc": not owners and not _is_runtime_object(obj.path),
            "runtime_part_list_collection": runtime,
            "weapon_part_list_collection": cooked,
            "part_list_collection": _ref_path(obj.get("PartListCollection")),
            "inventory_definition": _ref_path(obj.get("InventoryDefinition")),
            "weapon_type": weapon_type,
        }
    return dict(sorted(balances.items()))


# ----------------------------------------------------------------------- parts
def _build_parts(
    sources: Sources,
    exports: ExportIndex,
    weapon_types: dict[str, dict[str, Any]],
    fragment_index: dict[str, set[str]],
    in_lists: dict[str, list[dict[str, str]]],
    part_lists: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    parts: dict[str, dict[str, Any]] = {}
    for obj in iter_dump_objects(
        sources.dump_paths("WeaponPartDefinition"), classes=("WeaponPartDefinition",)
    ):
        if _is_default(obj):
            continue
        fragment_raw = unquote(obj.get("GestaltModeSkeletalMeshName", _NONE))
        fragment = None if fragment_raw in ("", _NONE) else fragment_raw
        nongestalt = _ref_path(obj.get("NongestaltSkeletalMesh"))
        lists = in_lists.get(obj.path, [])

        # weapon type: prefer the lists this part is registered in (unambiguous),
        # fall back to the fragment name when it belongs to exactly one type.
        candidates = {
            part_lists[entry["list"]]["weapon_type"]
            for entry in lists
            if part_lists.get(entry["list"], {}).get("weapon_type")
        }
        weapon_type: str | None = None
        if len(candidates) == 1:
            weapon_type = next(iter(candidates))
        elif fragment is not None:
            by_fragment = fragment_index.get(fragment, set())
            if len(by_fragment) == 1:
                weapon_type = next(iter(by_fragment))
        mesh_path = None
        if weapon_type is not None:
            mesh_path = weapon_types[weapon_type]["mesh_path"]
        if nongestalt is not None:
            mesh_path = nongestalt

        owners = exports.owners_of(obj.path)
        titles = [
            path
            for path in (_ref_path(raw) for raw in obj.array("TitleList"))
            if path is not None
        ]
        parts[obj.path] = {
            "path": obj.path,
            "slot": unquote(obj.get("PartType", _NONE)),
            "fragment": fragment,
            "weapon_type": weapon_type,
            "weapon_type_candidates": sorted(c for c in candidates if c),
            "mesh_path": mesh_path,
            "gestalt": str(obj.get("bIsGestaltMode", "False")) == "True",
            "nongestalt_mesh": nongestalt,
            "material": _ref_path(obj.get("Material")),
            "packages": owners,
            "dlc": not owners,
            "in_lists": lists,
            "titles": titles,
        }
    return dict(sorted(parts.items()))


# -------------------------------------------------------------------- packages
def _build_packages(
    exports: ExportIndex,
    summary: dict[str, dict[str, int]],
    hashed: set[str],
    load_order: list[str],
) -> dict[str, dict[str, Any]]:
    rank_by_object = {name: i for i, name in enumerate(load_order)}
    packages: dict[str, dict[str, Any]] = {}
    for file_name, class_counts in sorted(exports.package_class_counts.items()):
        stem = file_name[:-4] if file_name.lower().endswith(".upk") else file_name
        stats = summary.get(file_name, {})
        packages[file_name] = {
            "file": file_name,
            "package_object": stem,
            "size": stats.get("size"),
            "names": stats.get("names"),
            "exports": stats.get("exports"),
            "imports": stats.get("imports"),
            "catalogued_exports": sum(class_counts.values()),
            "export_counts": class_counts,
            "hashed": file_name.lower() in hashed,
            "startup": stem.lower() == "startup",
            "load_rank": rank_by_object.get(stem),
        }
    return packages


# ------------------------------------------------------------------------ main
def build_catalog(
    sources: Sources | None = None,
    out_path: Path | str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Assemble the catalog and (by default) write it to ``catalog/parts.json``.

    Raises ``FileNotFoundError`` listing whatever input is missing.
    """
    sources = sources or Sources.locate()
    absent = sources.missing()
    if absent:
        raise FileNotFoundError("missing catalog inputs: " + ", ".join(absent))

    exports = ExportIndex.load(sources.all_exports)
    summary = sources.load_summary()
    hashed = set(sources.load_hashed_packages())
    load_order = sources.load_load_order()

    weapon_types, fragment_index, key_by_def = _build_weapon_types(sources, exports)
    part_lists, in_lists = _build_part_lists(sources, exports, key_by_def)
    balances = _build_balances(sources, exports, part_lists)
    parts = _build_parts(sources, exports, weapon_types, fragment_index, in_lists, part_lists)
    packages = _build_packages(exports, summary, hashed, load_order)

    multi_owner = {
        path: {"cls": exports.classes.get(path), "packages": owners}
        for path, owners in sorted(exports.multi_owner.items())
    }

    source_records = []
    for label, path in (
        ("all_exports", sources.all_exports),
        ("summary", sources.summary),
        ("exe_sha_table", sources.exe_sha_table),
        ("probe", sources.probe),
    ):
        if path is None:
            continue
        source_records.append(
            {
                "name": label,
                "file": path.name,
                "size": path.stat().st_size,
                "sha256": file_digest(path),
            }
        )
    for cls_name in sorted({"GestaltSkeletalMeshDefinition", "WeaponPartDefinition",
                            "WeaponPartListCollectionDefinition", "WeaponPartListDefinition",
                            "WeaponBalanceDefinition", "WeaponTypeDefinition",
                            "SkeletalMeshSocket"}):
        for path in sources.dump_paths(cls_name):
            source_records.append(
                {
                    "name": f"dump:{cls_name}",
                    "file": path.name,
                    "size": path.stat().st_size,
                    "sha256": file_digest(path),
                }
            )

    catalog: dict[str, Any] = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "meta": {
            "generated_utc": _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat(),
            "generator": "bl2_catalog.build_catalog",
            "load_order_source": sources.probe.name if sources.probe else None,
            "load_order_entries": len(load_order),
            # runtime creation order of top-level Package objects at the main menu.
            # Package FILES only get a rank when the file stem is itself such an
            # object (Startup, Engine, WillowGame, ...); Startup.upk's cooked-in
            # groups (Weap_AssaultRifles, GD_Weap_AssaultRifle, ...) appear here
            # under their own names, so object paths can be ranked even when their
            # file cannot.
            "load_order": load_order,
            "package_files": sorted(summary),
            "hashed_packages": sorted(hashed),
            "counts": {
                "weapon_types": len(weapon_types),
                "fragments": sum(w["fragment_count"] for w in weapon_types.values()),
                "parts": len(parts),
                "part_lists": len(part_lists),
                "runtime_part_lists": sum(1 for v in part_lists.values() if v["runtime"]),
                "dlc_part_lists": sum(1 for v in part_lists.values() if v["dlc"]),
                "balances": len(balances),
                "packages": len(packages),
                "package_files": len(summary),
                "multi_owner_paths": len(multi_owner),
                "dlc_parts": sum(1 for p in parts.values() if p["dlc"]),
            },
            "sources": source_records,
        },
        "weapon_types": weapon_types,
        "parts": parts,
        "part_lists": part_lists,
        "balances": balances,
        "packages": packages,
        "multi_owner": multi_owner,
    }

    if write:
        target = Path(out_path) if out_path is not None else sources.root / "catalog" / "parts.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(catalog, indent=1, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
    return catalog


def load_catalog(path: Path | str | None = None) -> dict[str, Any]:
    """Read a previously built ``parts.json``."""
    if path is None:
        path = Path(__file__).resolve().parents[2] / "catalog" / "parts.json"
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)
