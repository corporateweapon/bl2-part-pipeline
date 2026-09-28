"""Tests for ``bl2_retarget`` -- recipe loading, the geometry rules, socket placement, and
``apply-spec``. None of this needs Blender: the geometry module is plain Python over
coordinate lists, and the two committed recipes are checked for shape (their glbs are not
in the repo, so nothing here opens them).

    python -m pytest tests/test_retarget.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bl2_retarget import geometry as geo  # noqa: E402
from bl2_retarget.__main__ import apply_spec, main as cli_main  # noqa: E402
from bl2_retarget.recipe import Fragment, RecipeError, SocketRule, load_recipe, recipe_from_dict  # noqa: E402


# --------------------------------------------------------------------------- a toy gun
def toy_gun():
    """A box gun along -Y: barrel tube in front, receiver, grip below/behind, stock behind.

    Vertices are laid out so every rule in the recipes has something to bite on.
    """
    coords = []
    polys = []

    def quad(pts):
        base = len(coords)
        coords.extend(pts)
        polys.append([base, base + 1, base + 2])
        polys.append([base, base + 2, base + 3])

    # barrel: y in [-60, -20], thin, z ~ 5 (front sight post on top at y=-55, z=12)
    quad([(-1, -60, 4), (1, -60, 4), (1, -20, 4), (-1, -20, 4)])
    quad([(-0.5, -56, 12), (0.5, -56, 12), (0.5, -54, 12), (-0.5, -54, 12)])
    # receiver: y in [-20, 12], z in [0, 8]; rear sight bump at y=-10, z=10
    quad([(-2, -20, 0), (2, -20, 0), (2, 12, 8), (-2, 12, 8)])
    quad([(-0.5, -11, 10), (0.5, -11, 10), (0.5, -9, 10), (-0.5, -9, 10)])
    # magazine: hangs below, y in [-12, -4], z in [-10, 0]
    mag_start = len(coords)
    quad([(-1, -12, -10), (1, -12, -10), (1, -4, 0), (-1, -4, 0)])
    mag_ids = list(range(mag_start, len(coords)))
    # grip: y in [6, 10], z in [-8, -2]
    quad([(-1, 6, -8), (1, 6, -8), (1, 10, -2), (-1, 10, -2)])
    # stock: y in [12, 40], z in [0, 6]
    quad([(-1.5, 12, 0), (1.5, 12, 0), (1.5, 40, 6), (-1.5, 40, 6)])
    # trigger: a tiny quad at y in [2, 3], z in [-1, 0]
    trig_start = len(coords)
    quad([(-0.2, 2, -1), (0.2, 2, -1), (0.2, 3, 0), (-0.2, 3, 0)])
    trig_ids = list(range(trig_start, len(coords)))
    # bolt on the -X side, y in [-8, -2], z ~ 6
    bolt_start = len(coords)
    quad([(-4, -8, 6), (-3, -8, 6), (-3, -2, 6), (-4, -2, 6)])
    bolt_ids = list(range(bolt_start, len(coords)))
    groups = {"clip": mag_ids, "trigger": trig_ids, "bolt": bolt_ids}
    return coords, polys, groups


FRAGS = [
    Fragment("G_Body", "AR_Body_Vladof", "remainder", None),
    Fragment("G_Barrel", "AR_Barrel_Vladof", {"front_y_lt": -20.5}, "Barrel"),
    Fragment("G_Grip", "AR_Grip_Vladof", {"cy_gt": 5.0, "cz_lt": -1.0}, "Root"),
    Fragment("G_Stock", "AR_Stock_Vladof", {"cy_gt": 11.5}, "Root"),
    Fragment("G_Mag", "AR_Body_Vladof", "remainder", "Mag", subset_of="G_Body", majority_group="clip"),
]


def frag_coords(coords, polys, faces):
    out = {}
    for name, idx in faces.items():
        vids = sorted({v for i in idx for v in polys[i]})
        out[name] = [coords[v] for v in vids]
    return out


# --------------------------------------------------------------------------- geometry
def test_classify_faces_by_rule_order_and_subset():
    coords, polys, groups = toy_gun()
    order = [FRAGS[1], FRAGS[3], FRAGS[2], FRAGS[0], FRAGS[4]]   # barrel, stock, grip, body, mag
    faces = geo.classify_faces(polys, coords, order, groups)
    assert sum(len(v) for k, v in faces.items() if k != "G_Mag") == len(polys)
    assert len(faces["G_Barrel"]) == 4      # tube + sight post
    assert len(faces["G_Stock"]) == 2
    assert len(faces["G_Grip"]) == 2
    assert len(faces["G_Mag"]) == 2 and set(faces["G_Mag"]) <= set(faces["G_Body"])
    # the grip rule would also take the magazine (cz<-1) if it were tried first; it is not,
    # because the mag centroid y (-8) fails cy_gt 5. The trigger (y 2..3) stays in the body.
    trig = [i for i, p in enumerate(polys) if all(v in set(groups["trigger"]) for v in p)]
    assert set(trig) <= set(faces["G_Body"])


def test_rule_order_changes_the_outcome():
    coords, polys, groups = toy_gun()
    wide_grip = Fragment("G_Grip", "AR_Grip_Vladof", {"cz_lt": -1.0}, "Root")   # no y test: takes the mag too
    faces = geo.classify_faces(polys, coords, [wide_grip, FRAGS[0]], groups)
    assert len(faces["G_Grip"]) == 4
    faces = geo.classify_faces(polys, coords, [FRAGS[1], wide_grip, FRAGS[0]], groups)
    assert len(faces["G_Grip"]) == 4 and len(faces["G_Barrel"]) == 4


def test_classify_vertices_bone_map():
    coords, polys, groups = toy_gun()
    bones, counts = geo.classify_vertices(len(coords), groups, {"clip": "Mag", "trigger": "Trigger"}, "Root")
    assert counts["Mag"] == 4 and counts["Trigger"] == 4
    assert counts["Root"] == len(coords) - 8
    assert bones[groups["bolt"][0]] == "Root"


def test_frame_checks_pass_on_the_toy_gun_and_fail_when_mirrored():
    coords, polys, groups = toy_gun()
    heads = {"Root": (0.0, 5.0, 0.0), "Trigger": (0.0, 2.5, -0.5)}
    checks = [{"kind": "longest_axis_y"}, {"kind": "muzzle_neg_y"},
              {"kind": "mag_hangs_down", "group": "clip", "below": "floor"},
              {"kind": "highest_point", "y_frac_lt": 0.35},
              {"kind": "group_side", "group": "bolt", "axis": "x", "lt": -2.0, "level": "warn"},
              {"kind": "stock_behind_root"}]
    res = geo.frame_checks(coords, groups, heads, {}, checks)
    assert geo.checks_ok(res), res
    assert [c["status"] for c in res] == ["ok"] * 6
    mirrored = [(x, -y, z) for x, y, z in coords]
    res = geo.frame_checks(mirrored, groups, heads, {}, checks)
    assert not geo.checks_ok(res)
    # a Y mirror puts the front sight at the back: the "highest point forward" check is the one
    # that sees it (the muzzle test alone is weak: the stock now reads as the front)
    assert any(c["name"] == "highest point is forward (sights)" and c["status"] == "error" for c in res)


def test_frame_checks_with_attachments_and_unknown_kind():
    coords, polys, groups = toy_gun()
    att = {"muzzle": (0.0, -60.0, 4.0), "sight": (0.0, -10.0, 10.0), "eject": (-3.0, -5.0, 6.0)}
    checks = [{"kind": "attachment_muzzle"}, {"kind": "attachment_above_centre"},
              {"kind": "attachment_side", "name": "eject", "lt": 0.0}, {"kind": "highest_point", "y_frac_gt": 0.45},
              {"kind": "not_a_check"}]
    res = geo.frame_checks(coords, groups, {"Root": (0, 5, 0)}, att, checks)
    by = {c["name"]: c["status"] for c in res}
    assert by["muzzle attachment is at the front of the barrel"] == "ok"
    assert by["sight attachment is above the receiver, on the centre line"] == "ok"
    assert by["eject attachment is on the expected side"] == "ok"
    assert by["highest point is over the receiver (scope)"] == "error"   # the toy has a front sight, not a scope
    assert by["check 'not_a_check'"] == "error"


def test_socket_rules_resolve_in_order_with_fallbacks():
    coords, polys, groups = toy_gun()
    order = [FRAGS[1], FRAGS[3], FRAGS[2], FRAGS[0], FRAGS[4]]
    fc = frag_coords(coords, polys, geo.classify_faces(polys, coords, order, groups))
    rules = [
        SocketRule("Muzzle", "G_Barrel", {"ring": "front", "of": "G_Barrel", "tol": 0.6}),
        SocketRule("FrontSight", "G_Barrel", {"extreme": "highest", "of": "G_Barrel"}),
        SocketRule("RearSight", "G_Body", {"extreme": "highest", "of": "G_Body", "where": {"y_between": [-20.0, -6.0]}},
                   fallback={"resolved": "FrontSight"}),
        SocketRule("EjectPort", "G_Body", {"bone_centroid": "ChargeHandle", "of": "G_Body"},
                   fallback={"resolved": "RearSight"}),
        SocketRule("EyeSocket2", "G_Barrel", {"sight_line": {"y": 40.0, "x": 0.0}}),
        SocketRule("Nowhere", "G_Body", {"attachment": "eject"}),
    ]
    out, notes = geo.socket_overrides(rules, fc, {}, {"G_Body": {}})
    assert out["G_Barrel"]["Muzzle"] == [0.0, -60.0, 4.0]              # ring centroid, y snapped, x negated (0)
    assert out["G_Barrel"]["FrontSight"][2] == 12.0
    assert out["G_Body"]["RearSight"][2] == 10.0 and -20 <= out["G_Body"]["RearSight"][1] <= -6
    assert out["G_Body"]["EjectPort"] == out["G_Body"]["RearSight"]     # fallback: no ChargeHandle vertices
    eye = out["G_Barrel"]["EyeSocket2"]
    assert eye[1] == 40.0 and eye[2] < 10.0                              # extrapolated down the sight line behind the gun
    assert any("EjectPort: placed from the fallback" in n for n in notes)
    assert any("Nowhere: could not be placed" in n for n in notes)
    assert "G_Body" in out and "Nowhere" not in out["G_Body"]


def test_socket_attachment_push_outside_and_ue_conversion():
    body = [(-3.0, -5.0, 6.0), (3.0, -5.0, 6.0), (-2.0, 20.0, 6.0)]
    out, _ = geo.socket_overrides(
        [SocketRule("EjectPort", "B", {"attachment": "eject", "push_outside": {"of": "B", "band": 6.0, "margin": 0.5}})],
        {"B": body}, {"eject": (-1.0, -5.0, 6.0)})
    # pushed to the widest -X vertex in the band (-3) minus 0.5 = -3.5 Blender -> +3.5 UE
    assert out["B"]["EjectPort"] == [3.5, -5.0, 6.0]
    assert geo.to_ue((1.5, 2.0, 3.0)) == [-1.5, 2.0, 3.0]
    out, _ = geo.socket_overrides([SocketRule("S", "B", {"literal": [1.0, 2.0, 3.0], "space": "ue"})], {"B": body}, {})
    assert out["B"]["S"] == [1.0, 2.0, 3.0]


def test_ring_with_xz_from_attachment_and_composite():
    barrel = [(-1.0, -60.0, 4.0), (1.0, -60.0, 4.0), (0.0, -20.0, 4.0)]
    out, _ = geo.socket_overrides(
        [SocketRule("Muzzle", "B", {"ring": "front", "of": "B", "tol": 0.8, "xz_from_attachment": "muzzle"})],
        {"B": barrel}, {"muzzle": (0.3, -58.0, 4.4)})
    assert out["B"]["Muzzle"] == [-0.3, -60.0, 4.4]
    out, _ = geo.socket_overrides(
        [SocketRule("E", "B", {"composite": {"x": {"extreme": "min_x", "of": "B", "offset": -0.5}, "y": 0.0,
                                             "z": {"extreme": "max_z", "of": "B", "scale": 0.5}}})], {"B": barrel}, {})
    assert out["B"]["E"] == [1.5, 0.0, 2.0]


def test_bounds_overrides_and_cut_proposals():
    coords, polys, groups = toy_gun()
    b = geo.bounds_overrides({"all": coords})["all"]
    assert b["origin"][1] == pytest.approx((-60 + 40) / 2) and b["extent"][1] == 50.0
    assert b["radius"] == pytest.approx((b["extent"][0] ** 2 + b["extent"][1] ** 2 + b["extent"][2] ** 2) ** 0.5, abs=1e-3)
    m = geo.propose_cuts(coords, groups)
    assert "y_histogram" in m and "group_bounds" in m and "clip" in m["group_bounds"]
    assert all(v["vertices"] >= 0 for v in m["y_valleys"])


# --------------------------------------------------------------------------- recipes
def test_committed_recipes_load_and_match_the_old_scripts_export_order():
    ak = load_recipe(REPO / "recipes" / "ak47.json")
    awp = load_recipe(REPO / "recipes" / "awp.json")
    assert [f.name for f in ak.exported] == ["AK_Body", "AK_Barrel", "AK_Grip", "AK_Stock", "AK_Mag"]
    assert [f.name for f in awp.exported] == ["AWP_Body", "AWP_Barrel", "AWP_Scope", "AWP_Grip", "AWP_Stock"]
    assert [f.name for f in awp.rule_fragments][:4] == ["AWP_Barrel", "AWP_Scope", "AWP_Stock", "AWP_Grip"]
    assert ak.bone_map == {"clip": "Mag", "trigger": "Trigger", "bolt": "ChargeHandle"}
    assert awp.frame()["units_to_unreal"] == 100.0
    assert awp.host["fragment_table"].endswith("gestalt_SR_fragments.txt")


def minimal_recipe(**over) -> dict:
    d = {"name": "T", "source": {"glb": "x.glb"}, "host": {"mesh_path": "M", "reference_fragment": "R"},
         "fragments": [{"name": "T_Body", "template": "B", "rule": "remainder"},
                       {"name": "T_Barrel", "template": "Bar", "rule": {"front_y_lt": -10}}],
         "output": {}}
    d.update(over)
    return d


def test_recipe_validation_errors():
    recipe_from_dict(minimal_recipe())
    with pytest.raises(RecipeError, match="remainder"):
        recipe_from_dict(minimal_recipe(fragments=[{"name": "A", "template": "B", "rule": {"cy_gt": 1}}]))
    with pytest.raises(RecipeError, match="unknown rule keys"):
        recipe_from_dict(minimal_recipe(fragments=[{"name": "A", "template": "B"}, {"name": "C", "template": "D", "rule": {"nope": 1}}]))
    with pytest.raises(RecipeError, match="subset_of"):
        recipe_from_dict(minimal_recipe(fragments=[{"name": "A", "template": "B"}, {"name": "C", "template": "D", "subset_of": "Z", "majority_group": "g"}]))
    with pytest.raises(RecipeError, match="rule_order"):
        recipe_from_dict(minimal_recipe(rule_order=["Nope"]))
    with pytest.raises(RecipeError, match="frame preset"):
        recipe_from_dict(minimal_recipe(source={"glb": "x", "frame": {"preset": "martian"}}))
    with pytest.raises(RecipeError, match="point needs"):
        recipe_from_dict(minimal_recipe(sockets=[{"socket": "S", "fragment": "T_Body", "point": {"bogus": 1}}]))


# --------------------------------------------------------------------------- apply-spec
def test_apply_spec_fills_missing_and_keeps_hand_tuned_values(tmp_path: Path, capsys):
    spec = {"fragments": [{"name": "AWP_Barrel", "template_fragment": "SR_Barrel_Jakobs", "sockets": "all",
                           "socket_overrides": {"Muzzle": [0, 0, 0]}},
                          {"name": "AWP_Body", "template_fragment": "SR_Body_Dahl",
                           "socket_overrides": {"EyeSocket2": [0.0, 34.0, 15.255]}}]}
    sp = tmp_path / "awp.json"
    sp.write_text(json.dumps(spec), encoding="utf-8")
    report = {"spec_fragments": [
        {"name": "AWP_Barrel", "template_fragment": "SR_Barrel_Jakobs", "sockets": "all",
         "socket_overrides": {"Muzzle": [0.836, -96.609, 9.171], "FrontSight": [0.448, -90.761, 11.035]},
         "bounds_override": {"origin": [0, 0, 0], "extent": [1, 1, 1], "radius": 1.7}},
        {"name": "AWP_Body", "template_fragment": "SR_Body_Dahl", "sockets": "all",
         "socket_overrides": {"RearSight": [-1.9, 18.3, 6.9]}, "bounds_override": None},
        {"name": "AWP_Scope", "template_fragment": "SR_Scope_Jakobs", "sockets": "all", "bounds_override": None},
    ]}
    lines = apply_spec(report, sp)
    new = json.loads(sp.read_text(encoding="utf-8"))
    barrel, body = new["fragments"]
    assert barrel["socket_overrides"]["Muzzle"] == [0, 0, 0]                 # hand value kept ...
    assert any("Muzzle differs (kept" in l for l in lines)                    # ... and reported
    assert barrel["socket_overrides"]["FrontSight"] == [0.448, -90.761, 11.035]   # missing one filled
    assert barrel["bounds_override"]["radius"] == 1.7
    assert body["socket_overrides"] == {"EyeSocket2": [0.0, 34.0, 15.255], "RearSight": [-1.9, 18.3, 6.9]}
    assert "bounds_override" not in body
    assert any("AWP_Scope" in l and "not in spec" in l for l in lines)
    # a second pass changes nothing but still reports the kept difference
    again = apply_spec(report, sp)
    assert [l for l in again if "<-" in l] == []
    assert any("Muzzle differs" in l for l in again)
    # --overwrite replaces it
    lines = apply_spec(report, sp, overwrite=True)
    assert json.loads(sp.read_text(encoding="utf-8"))["fragments"][0]["socket_overrides"]["Muzzle"] == [0.836, -96.609, 9.171]
    assert any("Muzzle <- " in l and "(was [0, 0, 0])" in l for l in lines)
    rp = tmp_path / "r.json"
    rp.write_text(json.dumps(report), encoding="utf-8")
    assert cli_main(["apply-spec", str(rp), str(sp)]) == 0
    assert "not in spec" in capsys.readouterr().out


def test_cli_check_reports_missing_glb(tmp_path: Path, capsys):
    rp = tmp_path / "r.json"
    rp.write_text(json.dumps(minimal_recipe()), encoding="utf-8")
    assert cli_main(["check", str(rp)]) == 1
    assert "MISSING" in capsys.readouterr().out
