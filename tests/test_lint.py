"""Tests for ``bl2_lint``.

The happy path is the real M2 artefact: ``scratch/PipelineMeshes_m2.fragment.json``
augmented with the proposal fields (``docs/LINT_PROPOSAL_SCHEMA.md``) must pass
clean. Every deliberate shadowing case then has to fail with a specific check id:

===== =========================================================================
 L1    package renamed to ``Startup`` (an exe-SHA1-verified package, F12)
 L2    fragment renamed to ``AR_Barrel_Vladof`` (already in the AR table)
 L3    part path set to the real Shredifier barrel
 L4    index range moved on top of an existing fragment
 L6    ``register_in_lists`` emptied (F5)
 L7    ``Char_Bandit_Marauder.Mesh.Skel_BanditMarauder`` -- 25 owners (F1)
 L8    ``register_at`` / ``keep_alive`` missing (F16, F17)
 L9    a total vertex count at or over the uint16 ceiling (65,535)
===== =========================================================================

A proposal may also carry a ``fragments`` array (several fragments in one
package, the M6 shape); L2, L4 and L5 then run over every entry and L4 checks
they tile consecutively from the mesh's current index count.

Runnable with pytest or directly::

    python -m pytest tests/test_lint.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bl2_catalog import load_catalog  # noqa: E402
from bl2_lint import ERROR, NOTE, Proposal, WARNING, lint  # noqa: E402
from bl2_lint.__main__ import main as lint_main  # noqa: E402

SHREDIFIER_BARREL = "GD_Weap_AssaultRifle.Barrel.AR_Barrel_Vladof_Shredifier"
SHREDIFIER_LIST = (
    "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_Sherdifier"
    ":WeaponPartListCollectionDefinition_39"
)
MARAUDER = "Char_Bandit_Marauder.Mesh.Skel_BanditMarauder"


@pytest.fixture(scope="module")
def catalog() -> dict[str, Any]:
    path = REPO / "catalog" / "parts.json"
    if not path.exists():
        pytest.skip("catalog/parts.json absent; run `python -m bl2_catalog`")
    return load_catalog(path)


@pytest.fixture(scope="module")
def sidecar() -> dict[str, Any]:
    """The real M2 sidecar, augmented with the fields the schema adds."""
    path = REPO / "scratch" / "PipelineMeshes_m2.fragment.json"
    if not path.exists():
        pytest.skip("scratch/PipelineMeshes_m2.fragment.json absent")
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(
        {
            "weapon_type": "AssaultRifle",
            "part_path": "GD_Weap_AssaultRifle.Barrel.AR_Barrel_PL_Bent",
            "part_slot": "WP_Barrel",
            "sockets": ["Muzzle", "EyeSocket2", "FrontSight"],
            "register_in_lists": [f"{SHREDIFIER_LIST}.BarrelPartData"],
            "register_at": "menu",
            "keep_alive": True,
        }
    )
    return data


def _ids(report, severity: str | None = None) -> set[str]:
    return {e.check for e in report.entries if severity is None or e.severity == severity}


# ------------------------------------------------------------------ happy path
def test_m2_sidecar_passes(catalog, sidecar):
    report = lint(sidecar, catalog)
    assert report.ok, report.render()
    assert _ids(report, ERROR) == set()
    assert _ids(report, WARNING) == set()
    assert report.has("L4", NOTE)  # appends at the end of the index buffer


def test_sidecar_extra_fields_are_preserved(sidecar):
    proposal = Proposal.from_dict(sidecar)
    assert proposal.extra["dz"] == 8.0
    assert proposal.missing_fields() == []
    assert proposal.package_stem == "PipelineMeshes"


def test_render_and_cli_exit_codes(catalog, sidecar, tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(sidecar), encoding="utf-8")
    catalog_path = str(REPO / "catalog" / "parts.json")
    assert lint_main([str(good), "--catalog", catalog_path]) == 0

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**sidecar, "register_in_lists": []}), encoding="utf-8")
    assert lint_main([str(bad), "--catalog", catalog_path]) == 1

    text = lint(sidecar, catalog).render()
    assert text.startswith("bl2_lint: GD_Weap_AssaultRifle.Barrel.AR_Barrel_PL_Bent")
    assert "PASS" in text
    assert "FAIL" in lint({**sidecar, "register_in_lists": []}, catalog).render()


# ------------------------------------------------------- deliberate collisions
def test_l1_package_name_collides_with_hashed_package(catalog, sidecar):
    report = lint({**sidecar, "package": "Startup"}, catalog)
    assert not report.ok
    assert "L1" in _ids(report, ERROR)
    entry = report.by_check("L1")[0]
    assert entry.detail["hashed"] is True
    assert entry.detail["collides_with"] == "Startup.upk"


def test_l1_package_name_collides_with_plain_package(catalog, sidecar):
    report = lint({**sidecar, "package": "ash_combat.upk"}, catalog)  # case-insensitive
    entry = report.by_check("L1")[0]
    assert entry.severity == ERROR
    assert entry.detail["hashed"] is False
    assert entry.detail["collides_with"] == "Ash_Combat.upk"


def test_l1_new_package_name_is_clean(catalog, sidecar):
    assert lint({**sidecar, "package": "PipelineMeshes"}, catalog).by_check("L1") == []


def test_l2_fragment_name_already_in_target_weapon_type(catalog, sidecar):
    report = lint({**sidecar, "fragment": "AR_Barrel_Vladof"}, catalog)
    assert not report.ok
    assert "L2" in _ids(report, ERROR)
    entry = next(e for e in report.by_check("L2") if e.severity == ERROR)
    assert entry.detail["existing_first_index"] == 23484


def test_l2_fragment_name_used_by_another_weapon_type_is_a_warning(catalog, sidecar):
    # Acc_Barrel_Bayonet1 exists on SMG/SniperRifle but not (under that exact
    # name) as a fresh AR fragment -- so it is a warning, not an error.
    report = lint({**sidecar, "fragment": "SR_Body_Jakobs"}, catalog)
    entry = next(e for e in report.by_check("L2") if e.severity == WARNING)
    assert "SniperRifle" in entry.detail["weapon_types"]
    assert report.ok, report.render()


def test_l3_part_path_already_exists(catalog, sidecar):
    report = lint({**sidecar, "part_path": SHREDIFIER_BARREL}, catalog)
    assert not report.ok
    assert "L3" in _ids(report, ERROR)
    error = next(e for e in report.by_check("L3") if e.severity == ERROR)
    assert error.detail["packages"] == ["Startup.upk"]


def test_l3_notes_that_the_earlier_loaded_package_wins(catalog, sidecar):
    report = lint({**sidecar, "part_path": SHREDIFIER_BARREL}, catalog)
    note = next(e for e in report.by_check("L3") if e.severity == NOTE)
    assert "earlier-loaded package wins" in note.message
    assert note.detail["earlier_packages"] == ["Startup.upk"]


def test_l4_overlapping_index_range(catalog, sidecar):
    report = lint({**sidecar, "first_index": 23484, "num_primitives": 1164}, catalog)
    assert not report.ok
    error = next(e for e in report.by_check("L4") if e.severity == ERROR)
    assert error.detail["overlaps"] == ["AR_Barrel_Vladof"]


def test_l4_zero_primitives(catalog, sidecar):
    report = lint({**sidecar, "num_primitives": 0}, catalog)
    assert not report.ok
    assert any("num_primitives" in e.message for e in report.by_check("L4"))


def test_l4_append_is_a_note_not_an_error(catalog, sidecar):
    report = lint(sidecar, catalog)
    note = next(e for e in report.by_check("L4") if e.severity == NOTE)
    assert note.detail["first_index"] == note.detail["index_count"] == 68085


def test_l4_range_past_the_end_of_the_buffer_warns(catalog, sidecar):
    # the AR buffer is fully tiled, so any non-appending range is past its end
    report = lint({**sidecar, "first_index": 200001}, catalog)
    entry = next(e for e in report.by_check("L4"))
    assert entry.severity == WARNING
    assert "past the end" in entry.message


def test_l4_unaligned_first_index_warns(catalog, sidecar):
    report = lint({**sidecar, "first_index": 200000}, catalog)
    assert any("multiple of 3" in e.message for e in report.by_check("L4"))


def test_l5_unknown_template_fragment(catalog, sidecar):
    report = lint({**sidecar, "template_fragment": "AR_Barrel_DoesNotExist"}, catalog)
    assert not report.ok
    assert "L5" in _ids(report, ERROR)


def test_l5_unknown_socket_is_a_warning(catalog, sidecar):
    report = lint({**sidecar, "sockets": ["Muzzle", "NotASocket"]}, catalog)
    entry = next(e for e in report.by_check("L5") if e.severity == WARNING)
    assert entry.detail["missing"] == ["NotASocket"]
    assert report.ok  # a socket to author from scratch is not fatal


def test_l6_empty_register_in_lists(catalog, sidecar):
    report = lint({**sidecar, "register_in_lists": []}, catalog)
    assert not report.ok
    assert "L6" in _ids(report, ERROR)
    assert "F5" in report.by_check("L6")[0].message


def test_l6_unknown_list_path(catalog, sidecar):
    report = lint({**sidecar, "register_in_lists": ["Nope.NotAList.BarrelPartData"]}, catalog)
    assert not report.ok
    assert report.by_check("L6")[0].detail["list_path"] == "Nope.NotAList"


def test_l6_accepts_a_bare_list_path_with_a_warning(catalog, sidecar):
    report = lint({**sidecar, "register_in_lists": [SHREDIFIER_LIST]}, catalog)
    entry = report.by_check("L6")[0]
    assert entry.severity == WARNING
    assert "BarrelPartData" in entry.detail["slots"]


def test_l7_multi_owner_object_names_all_25_owners(catalog, sidecar):
    report = lint({**sidecar, "objects": [MARAUDER]}, catalog)
    entry = next(e for e in report.by_check("L7"))
    assert entry.severity == WARNING
    assert entry.detail["owner_count"] == 25
    assert len(entry.detail["packages"]) == 25
    assert "Ash_Combat.upk" in entry.detail["packages"]
    assert report.ok  # a warning does not block


def test_l7_silent_for_single_owner_paths(catalog, sidecar):
    report = lint(
        {**sidecar, "objects": ["Weap_AssaultRifles.GestaltDef_AssaultRifle_GestaltSkeletalMesh"]},
        catalog,
    )
    assert report.by_check("L7") == []


def test_l8_requires_menu_registration_and_keep_alive(catalog, sidecar):
    stripped = {k: v for k, v in sidecar.items() if k not in ("register_at", "keep_alive")}
    report = lint(stripped, catalog)
    assert not report.ok
    messages = [e.message for e in report.by_check("L8")]
    assert len(messages) == 2
    assert any("F16" in m for m in messages)
    assert any("F17" in m for m in messages)

    report = lint({**sidecar, "register_at": "map", "keep_alive": False}, catalog)
    assert len(report.by_check("L8")) == 2


def test_l0_missing_fields_and_unknown_weapon_type(catalog):
    report = lint({}, catalog)
    assert not report.ok
    assert report.by_check("L0")[0].detail["missing"]
    report = lint({"weapon_type": "Crossbow"}, catalog)
    assert any("Crossbow" in e.message for e in report.by_check("L0"))


# ------------------------------------------------- L9 + multi-fragment proposals
def _multi(sidecar, fragments, **extra):
    """A proposal carrying a ``fragments`` array, first fragment repeated on top."""
    head = fragments[0]
    return {
        **sidecar,
        "fragment": head["fragment"],
        "first_index": head["first_index"],
        "num_primitives": head["num_primitives"],
        "template_fragment": head.get("template_fragment", sidecar["template_fragment"]),
        "fragments": fragments,
        **extra,
    }


def _three_fragments():
    return [
        {"fragment": "PL_Test_Cyl", "template_fragment": "AR_Barrel_Vladof",
         "first_index": 68085, "num_primitives": 44, "new_vertices": 24},
        {"fragment": "PL_Test_Box", "template_fragment": "AR_Barrel_Vladof",
         "first_index": 68217, "num_primitives": 12, "new_vertices": 8},
        {"fragment": "AR_Barrel_PL_Bent", "template_fragment": "AR_Barrel_Vladof",
         "first_index": 68253, "num_primitives": 1164, "new_vertices": 1308,
         "sockets": ["Muzzle"]},
    ]


def test_multi_fragment_proposal_passes(catalog, sidecar):
    report = lint(_multi(sidecar, _three_fragments(), total_vertices=28551), catalog)
    assert report.ok, report.render()
    assert _ids(report, ERROR) == set()
    appends = [e for e in report.by_check("L4") if e.severity == NOTE]
    assert [e.detail["fragment"] for e in appends] == [
        "PL_Test_Cyl", "PL_Test_Box", "AR_Barrel_PL_Bent"]
    # each one appends where the previous ended
    assert [e.detail["index_count"] for e in appends] == [68085, 68217, 68253]
    assert {e.detail["fragment"] for e in report.by_check("L2")} <= {
        "PL_Test_Cyl", "PL_Test_Box", "AR_Barrel_PL_Bent"}


def test_multi_fragment_gap_is_flagged(catalog, sidecar):
    fragments = _three_fragments()
    fragments[1]["first_index"] += 3  # leaves a hole the mesh does not have
    report = lint(_multi(sidecar, fragments), catalog)
    warning = next(e for e in report.by_check("L4") if e.severity == WARNING)
    assert "past the end" in warning.message
    assert warning.detail["fragment"] == "PL_Test_Box"


def test_multi_fragment_overlap_within_the_proposal_is_an_error(catalog, sidecar):
    fragments = _three_fragments()
    fragments[1]["first_index"] = fragments[0]["first_index"]
    report = lint(_multi(sidecar, fragments), catalog)
    assert not report.ok
    error = next(e for e in report.by_check("L4") if e.severity == ERROR)
    assert error.detail["overlaps"] == ["PL_Test_Cyl"]


def test_multi_fragment_checks_every_name_and_template(catalog, sidecar):
    fragments = _three_fragments()
    fragments[1]["fragment"] = "AR_Barrel_Vladof"            # L2: already in the AR table
    fragments[2]["template_fragment"] = "AR_Barrel_Nope"     # L5: no such template
    report = lint(_multi(sidecar, fragments), catalog)
    assert not report.ok
    assert {"L2", "L5"} <= _ids(report, ERROR)
    l5 = next(e for e in report.by_check("L5") if e.severity == ERROR)
    assert l5.detail["fragment"] == "AR_Barrel_PL_Bent"


def test_multi_fragment_duplicate_names(catalog, sidecar):
    fragments = _three_fragments()
    fragments[1]["fragment"] = fragments[0]["fragment"]
    report = lint(_multi(sidecar, fragments), catalog)
    assert any("listed twice" in e.message for e in report.by_check("L2"))


def test_l9_totals_the_vertices_the_fragments_add(catalog, sidecar):
    report = lint(_multi(sidecar, _three_fragments(), total_vertices=28551), catalog)
    note = report.by_check("L9")[0]
    assert note.severity == NOTE
    assert note.detail["total_vertices"] == 28551
    assert note.detail["added_vertices"] == 24 + 8 + 1308
    assert note.detail["limit"] == 65535


def test_l9_uses_a_base_count_when_there_is_no_total(catalog, sidecar):
    proposal = _multi(sidecar, _three_fragments(), base_vertex_count=27211)
    note = lint(proposal, catalog).by_check("L9")[0]
    assert note.detail["total_vertices"] == 27211 + 24 + 8 + 1308
    assert note.detail["source"] == "base_vertex_count + new_vertices"


def test_l9_warns_near_the_limit_and_errors_over_it(catalog, sidecar):
    warn = lint({**sidecar, "total_vertices": 61000}, catalog).by_check("L9")[0]
    assert warn.severity == WARNING
    assert "uint16" in warn.message or "overflows" in warn.message

    report = lint({**sidecar, "total_vertices": 70000}, catalog)
    assert not report.ok
    error = next(e for e in report.by_check("L9") if e.severity == ERROR)
    assert error.detail["limit"] == 65535
    assert "uint16" in error.message


def test_l9_says_so_when_it_cannot_count(catalog, sidecar):
    note = lint(sidecar, catalog).by_check("L9")[0]  # the M2 sidecar has no vertex counts
    assert note.severity == NOTE
    assert "not checked" in note.message


def test_all_deliberate_cases_at_once(catalog, sidecar):
    """One proposal that trips L1, L2, L3, L4, L6, L7 and L8 together."""
    report = lint(
        {
            **sidecar,
            "package": "Startup",
            "fragment": "AR_Barrel_Vladof",
            "part_path": SHREDIFIER_BARREL,
            "first_index": 23484,
            "register_in_lists": [],
            "register_at": "map",
            "keep_alive": False,
            "objects": [MARAUDER],
        },
        catalog,
    )
    assert not report.ok
    assert {"L1", "L2", "L3", "L4", "L6", "L8"} <= _ids(report, ERROR)
    assert "L7" in _ids(report, WARNING)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))


# --------------------------------------------------------------------- part-only proposals
def test_part_only_proposal_needs_no_fragment_fields(catalog, sidecar):
    """A part that draws nothing (a clone of ``AR_Sight_None``) adds no fragment: the
    fragment fields are not required and L2/L4/L5 have nothing to say."""
    proposal = {
        "package": sidecar["package"],
        "mesh_path": sidecar["mesh_path"],
        "weapon_type": "AssaultRifle",
        "part_only": True,
        "part_path": "GD_Weap_AssaultRifle.Sight.AR_Sight_PL_None",
        "part_slot": "WP_Sight",
        "register_in_lists": [SHREDIFIER_LIST + ".SightPartData"],
        "register_at": "menu",
        "keep_alive": True,
        "objects": ["GD_Weap_AssaultRifle.Sight.AR_Sight_None"],
    }
    report = lint(proposal, catalog)
    assert report.ok, report.render()
    ids = _ids(report)
    assert "L0" in _ids(report, NOTE) and not ids & {"L2", "L4", "L5"}
    assert Proposal.from_dict(proposal).fragment_entries() == []

    # and it is still a proposal: a colliding part path is still an error
    proposal["part_path"] = "GD_Weap_AssaultRifle.Sight.AR_Sight_None"
    assert "L3" in _ids(lint(proposal, catalog), ERROR)
    # without the flag the fragment fields are required as before
    del proposal["part_only"]
    assert "L0" in _ids(lint(proposal, catalog), ERROR)


# --------------------------------------------------------------------- L10 runtime balances
SHREDIFIER_BALANCE = "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_Sherdifier"


def test_l10_runtime_balance_must_be_new_and_cloned_from_a_catalog_balance(catalog, sidecar):
    proposal = dict(sidecar)
    proposal["register_in_lists"] = []
    proposal["register_in_runtime_lists"] = [
        "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_PL:RuntimePartList.BarrelPartData"]
    proposal["new_balances"] = [{
        "path": "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_PL",
        "template": SHREDIFIER_BALANCE,
        "title": "GD_Weap_AssaultRifle.Name.Title_Vladof.Title_Legendary_PL",
        "pools": ["GD_Itempools.WeaponPools.Pool_Weapons_AssaultRifles_06_Legendary"],
    }]
    report = lint(proposal, catalog)
    assert report.ok, report.render()
    assert "L10" in _ids(report, NOTE) and "L6" in _ids(report, NOTE)
    assert "L6" not in _ids(report, ERROR), "a runtime list stands in for a catalog list"

    shadow = json.loads(json.dumps(proposal))
    shadow["new_balances"][0]["path"] = SHREDIFIER_BALANCE
    assert "L10" in _ids(lint(shadow, catalog), ERROR)
    unknown = json.loads(json.dumps(proposal))
    unknown["new_balances"][0]["template"] = "GD_Weap_AssaultRifle.A_Weapons_Legendary.Nope"
    assert "L10" in _ids(lint(unknown, catalog), ERROR)
    bad_title = json.loads(json.dumps(proposal))
    bad_title["new_balances"][0]["title"] = SHREDIFIER_BARREL
    assert "L10" in _ids(lint(bad_title, catalog), ERROR)
    bare = json.loads(json.dumps(proposal))
    bare["register_in_runtime_lists"] = ["GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_PL"]
    assert "L6" in _ids(lint(bare, catalog), ERROR), "a runtime list still needs its slot field"


def test_l1_checks_extra_package_names_too(catalog, sidecar):
    proposal = dict(sidecar)
    proposal["extra_packages"] = ["PipelineTextures"]
    assert lint(proposal, catalog).ok
    proposal["extra_packages"] = ["Startup"]
    report = lint(proposal, catalog)
    assert "L1" in _ids(report, ERROR)
    assert any("extra package name" in e.message for e in report.errors)
    proposal["extra_packages"] = []
    proposal["new_materials"] = [{"path": "Common_GunMaterials.Materials.AssaultRifle.Mati_PL",
                                  "parent": "Common_GunMaterials.Materials.AssaultRifle.Mati_VladofLegendary"}]
    assert "L10" in _ids(lint(proposal, catalog), NOTE)
