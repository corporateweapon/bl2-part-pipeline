"""Tests for ``bl2_catalog``.

Three groups:

a. unit tests for the OpenBLCMM dump parser (no inputs needed);
b. a freshly built catalog agrees with ``scratch/gestalt_AR_fragments.txt``
   (47 fragments, ranges tile the 68,085-index buffer exactly) and with the
   facts recorded in ``docs/FINDINGS.md`` D1/D3;
c. the committed ``catalog/parts.json`` is in sync with what the builder emits.

Group (b)/(c) skip when the gitignored ``scratch/`` inputs are absent.

Runnable with pytest or directly::

    python -m pytest tests/test_catalog.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bl2_catalog import (  # noqa: E402
    CATALOG_SCHEMA_VERSION,
    Sources,
    build_catalog,
    iter_dump_objects,
    obj_ref,
    parse_value,
    unquote,
    weapon_type_key,
)
from bl2_catalog.dumps import split_top_level  # noqa: E402

AR = "AssaultRifle"
AR_INDEX_COUNT = 68085  # D1: 22,695 triangles
AR_FRAGMENTS = 47
AR_SOCKETS = 48
MARAUDER = "Char_Bandit_Marauder.Mesh.Skel_BanditMarauder"
SHREDIFIER_BARREL = "GD_Weap_AssaultRifle.Barrel.AR_Barrel_Vladof_Shredifier"


# ------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def sources() -> Sources:
    found = Sources.locate(REPO)
    missing = found.missing()
    if missing:
        pytest.skip(f"generated inputs absent: {missing[0]}")
    return found


@pytest.fixture(scope="module")
def catalog(sources: Sources) -> dict:
    return build_catalog(sources, write=False)


def _scratch_ar_fragments() -> dict[str, tuple[int, int, int]]:
    """Parse ``scratch/gestalt_AR_fragments.txt`` (the M1-era reference table)."""
    path = REPO / "scratch" / "gestalt_AR_fragments.txt"
    if not path.exists():
        pytest.skip("scratch/gestalt_AR_fragments.txt absent")
    table: dict[str, tuple[int, int, int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        name, *rest = line.split(",")
        fields = dict(part.split("=", 1) for part in rest)
        table[name] = (
            int(fields["MaterialIndex"]),
            int(fields["FirstIndex"]),
            int(fields["NumPrimitives"]),
        )
    return table


# ------------------------------------------------------------- a. dump parser
def test_split_top_level_respects_parens_and_quotes():
    assert split_top_level("a=1,b=(x=1,y=2),c=\"p,q\"") == ["a=1", "b=(x=1,y=2)", 'c="p,q"']


def test_parse_value_struct_and_array():
    struct = parse_value("(X=1.000000,Y=-2.500000,Z=0.000000)")
    assert struct == {"X": "1.000000", "Y": "-2.500000", "Z": "0.000000"}
    array = parse_value("((Part=A'x.y'),(Part=None))")
    assert isinstance(array, list) and len(array) == 2
    assert array[0]["Part"] == "A'x.y'"  # type: ignore[index]
    assert parse_value("None") == "None"
    assert parse_value("()") == {}


def test_obj_ref_and_unquote():
    ref = obj_ref("WeaponPartDefinition'GD_Weap_AssaultRifle.Barrel.AR_Barrel_Vladof'")
    assert ref is not None
    assert ref.cls == "WeaponPartDefinition"
    assert ref.path == "GD_Weap_AssaultRifle.Barrel.AR_Barrel_Vladof"
    assert obj_ref("None") is None
    assert unquote('"AR_Barrel_Vladof"') == "AR_Barrel_Vladof"


def test_weapon_type_key():
    assert weapon_type_key("Weap_AssaultRifles.GestaltDef_AssaultRifle") == AR
    assert weapon_type_key("Item_Shields.Gestalt.GestaltDef_Shields") == "Shields"


def test_iter_dump_objects_filters(sources: Sources):
    objects = list(
        iter_dump_objects(
            sources.dump_paths("GestaltSkeletalMeshDefinition"),
            classes=("GestaltSkeletalMeshDefinition",),
            path_prefixes=("Weap_AssaultRifles.",),
        )
    )
    assert [o.path for o in objects] == ["Weap_AssaultRifles.GestaltDef_AssaultRifle"]
    assert objects[0].trailer["Outer"] == "Package'Weap_AssaultRifles'"


# ------------------------------------------------------------- b. built catalog
def test_ar_fragment_table_matches_scratch(catalog: dict):
    reference = _scratch_ar_fragments()
    assert len(reference) == AR_FRAGMENTS
    fragments = catalog["weapon_types"][AR]["fragments"]
    assert set(fragments) == set(reference)
    for name, (material, first, num) in reference.items():
        entry = fragments[name]
        assert entry["material_index"] == material, name
        assert entry["first_index"] == first, name
        assert entry["num_primitives"] == num, name
        assert entry["range_count"] == 1, name


def test_ar_tiling_and_index_count(catalog: dict):
    weapon_type = catalog["weapon_types"][AR]
    assert weapon_type["index_count"] == AR_INDEX_COUNT
    assert weapon_type["fragment_count"] == AR_FRAGMENTS
    assert weapon_type["socket_count"] == AR_SOCKETS
    tiling = weapon_type["tiling"]
    assert tiling["ok"] is True
    assert tiling["gaps"] == [] and tiling["overlaps"] == []
    assert tiling["covered_indices"] == AR_INDEX_COUNT
    assert tiling["triangle_count"] == AR_INDEX_COUNT // 3


def test_every_weapon_type_tiles(catalog: dict):
    assert len(catalog["weapon_types"]) >= 9
    bad = [k for k, v in catalog["weapon_types"].items() if not v["tiling"]["ok"]]
    assert bad == []


def test_nine_base_game_weapon_types_plus_dlc(catalog: dict):
    base = sorted(k for k, v in catalog["weapon_types"].items() if not v["dlc"])
    assert base == [
        "Artifact",
        "AssaultRifle",
        "Grenades",
        "Launcher",
        "Pistol",
        "SMG",
        "Shields",
        "Shotgun",
        "SniperRifle",
    ]
    # the Krieg buzz axe gestalt is dumped but no installed package exports it
    assert catalog["weapon_types"]["LilacBuzzAxe"]["dlc"] is True


def test_ar_barrel_vladof_sockets(catalog: dict):
    fragment = catalog["weapon_types"][AR]["fragments"]["AR_Barrel_Vladof"]
    sockets = {s["original"]: s for s in fragment["sockets"]}
    assert set(sockets) == {"Muzzle", "EyeSocket2", "FrontSight"}  # D2
    assert sockets["Muzzle"]["mangled"] == "AR_Barrel_Vladof_Muzzle"
    assert sockets["Muzzle"]["bone"] == "Barrel"
    assert all(s["resolved"] for s in fragment["sockets"])
    assert fragment["bounds"]["origin"][1] < 0  # barrels sit at negative Y


def test_shredifier_part_entry(catalog: dict):
    part = catalog["parts"][SHREDIFIER_BARREL]
    assert part["slot"] == "WP_Barrel"
    assert part["fragment"] == "AR_Barrel_Vladof"
    assert part["weapon_type"] == AR
    assert part["packages"] == ["Startup.upk"]  # D3: Startup owns every AR part
    assert part["dlc"] is False
    lists = {entry["list"] for entry in part["in_lists"]}
    assert any("AR_Vladof_5_Sherdifier" in path for path in lists)
    assert {entry["slot"] for entry in part["in_lists"]} == {"BarrelPartData"}


def test_every_part_is_in_a_list(catalog: dict):
    orphans = [p for p, v in catalog["parts"].items() if not v["in_lists"]]
    assert orphans == []


def test_dlc_parts_flagged(catalog: dict):
    dlc = [p for p, v in catalog["parts"].items() if v["dlc"]]
    assert dlc, "the dumps cover DLC objects; some parts must be flagged"
    assert all(not catalog["parts"][p]["packages"] for p in dlc)
    assert "GD_Anemone_Weap_SniperRifles.Accessory.Sniper_Accessory_Bayonet1" in dlc


def test_startup_package_record(catalog: dict):
    startup = catalog["packages"]["Startup.upk"]
    assert startup["startup"] is True
    assert startup["hashed"] is True  # F12
    assert startup["load_rank"] is not None
    assert startup["export_counts"]["GestaltSkeletalMeshDefinition"] == 9
    other = catalog["packages"]["Ash_Combat.upk"]
    assert other["hashed"] is False and other["startup"] is False


def test_multi_owner_holds_the_shadowing_case(catalog: dict):
    entry = catalog["multi_owner"][MARAUDER]
    assert entry["cls"] == "SkeletalMesh"
    assert len(entry["packages"]) == 25  # D3
    skeletal = [
        path
        for path, value in catalog["multi_owner"].items()
        if value["cls"] == "SkeletalMesh"
    ]
    assert len(skeletal) == 160  # D3: 160 duplicated SkeletalMesh paths
    assert "Weap_AssaultRifles.GestaltDef_AssaultRifle_GestaltSkeletalMesh" not in catalog["multi_owner"]


def test_part_lists_and_balances(catalog: dict):
    collection = catalog["part_lists"][
        "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_Sherdifier"
        ":WeaponPartListCollectionDefinition_39"
    ]
    assert collection["weapon_type"] == AR
    barrel = collection["slots"]["BarrelPartData"]
    assert barrel["enabled"] is True
    assert SHREDIFIER_BARREL in barrel["parts"]
    balance = catalog["balances"][
        "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_Sherdifier"
    ]
    assert balance["runtime_part_list_collection"] == (
        "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_Sherdifier"
        ":WeaponPartListCollectionDefinition_39"
    )
    assert balance["weapon_type"] == AR


def test_cooked_part_list_resolves_despite_the_colon_separator(catalog: dict):
    """The dumps write sub-objects as ``outer:leaf``, the export scanner as ``outer.leaf``."""
    cooked = catalog["part_lists"][
        "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_Sherdifier:PartList"
    ]
    assert cooked["packages"] == ["Startup.upk"]
    assert cooked["runtime"] is False and cooked["dlc"] is False


def test_runtime_part_lists_are_not_mistaken_for_dlc(catalog: dict):
    runtime = catalog["part_lists"][
        "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_Sherdifier"
        ":WeaponPartListCollectionDefinition_39"
    ]
    assert runtime["runtime"] is True  # instantiated at load from the cooked :PartList
    assert runtime["packages"] == [] and runtime["dlc"] is False
    counts = catalog["meta"]["counts"]
    assert counts["runtime_part_lists"] > 0
    assert counts["dlc_part_lists"] < counts["part_lists"] - counts["runtime_part_lists"]


def test_meta_is_self_describing(catalog: dict):
    meta = catalog["meta"]
    assert catalog["schema_version"] == CATALOG_SCHEMA_VERSION
    assert len(meta["package_files"]) == 914  # D0
    assert len(meta["hashed_packages"]) == 12  # F12
    assert meta["counts"]["weapon_types"] == len(catalog["weapon_types"])
    assert meta["counts"]["parts"] == len(catalog["parts"])
    assert {s["name"] for s in meta["sources"]} >= {"all_exports", "summary", "exe_sha_table"}
    assert all(len(s["sha256"]) == 64 for s in meta["sources"])


def test_output_is_deterministic_and_small(tmp_path: Path, sources: Sources):
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    build_catalog(sources, out_path=first)
    build_catalog(sources, out_path=second)
    a = json.loads(first.read_text(encoding="utf-8"))
    b = json.loads(second.read_text(encoding="utf-8"))
    for blob in (a, b):
        blob["meta"].pop("generated_utc")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert first.stat().st_size < 15 * 1024 * 1024


# ------------------------------------------------------- c. committed artifact
def test_committed_catalog_is_current(catalog: dict):
    path = REPO / "catalog" / "parts.json"
    assert path.exists(), "catalog/parts.json is the pipeline's source of truth; commit it"
    committed = json.loads(path.read_text(encoding="utf-8"))
    assert committed["schema_version"] == CATALOG_SCHEMA_VERSION
    assert committed["meta"]["counts"] == catalog["meta"]["counts"]
    assert committed["weapon_types"][AR]["fragments"] == catalog["weapon_types"][AR]["fragments"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
