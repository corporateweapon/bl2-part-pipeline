"""Tests for the labelled cut preview's Blender-free half: rules -> lines, and drawing a
plan onto an image with Pillow.

    python -m pytest tests/test_retarget_overlay.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bl2_retarget import overlay  # noqa: E402
from bl2_retarget.__main__ import main as cli_main  # noqa: E402
from bl2_retarget.recipe import Fragment  # noqa: E402

Image = pytest.importorskip("PIL.Image")


def test_plan_lines_covers_every_rule_kind():
    frags = [
        Fragment("B", "t", "remainder"),
        Fragment("Bar", "t", {"front_y_lt": -35.0}),
        Fragment("Scope", "t", {"cz_gt": 12.0, "cy_between": [-32.0, 10.0]}),
        Fragment("Grip", "t", {"cy_gt": 2.0, "cz_lt": -2.0, "majority_group": "x"}),
        Fragment("Mag", "t", "remainder", subset_of="B", majority_group="clip"),
    ]
    colours = {f.name: overlay.colour_for(i) for i, f in enumerate(frags)}
    lines = overlay.plan_lines(frags, colours, valleys=[{"y_from": -35.0, "y_to": -30.0, "vertices": 98}])
    kinds = [(l["axis"], l["value"], l["kind"]) for l in lines]
    assert ("y", -35.0, "cut") in kinds and ("z", 12.0, "cut") in kinds
    assert ("y", -32.0, "cut") in kinds and ("y", 10.0, "cut") in kinds
    assert ("y", 2.0, "cut") in kinds and ("z", -2.0, "cut") in kinds
    assert sum(1 for l in lines if l["kind"] == "valley") == 1
    assert not any("majority_group" in l["label"] for l in lines)
    assert all(l["colour"] == colours["Scope"] for l in lines if l["label"].startswith("Scope"))
    labels = [l["label"] for l in lines]
    assert "Bar: front y < -35" in labels and "Grip: cz < -2" in labels and "valley 98v" in labels


def test_apply_plan_draws_lines_legend_and_band(tmp_path: Path):
    side = tmp_path / "x_labelled_side.png"
    Image.new("RGB", (400, 200), (20, 20, 20)).save(side)
    plan = {
        "title": "toy: fragments and cut planes",
        "legend": [{"name": "Barrel", "colour": (0.85, 0.33, 0.31), "vertices": 10, "triangles": 8, "bone": "Barrel"}],
        "images": {"side": {"path": str(side), "lines": [
            {"x0": 100.0, "y0": 0.0, "x1": 100.0, "y1": 200.0, "label": "Barrel: front y < -35", "colour": (0.85, 0.33, 0.31), "kind": "cut"},
            {"x0": 0.0, "y0": 150.0, "x1": 400.0, "y1": 150.0, "label": "Grip: cz < -2", "colour": (0.36, 0.72, 0.36), "kind": "cut"},
            {"x0": 250.0, "y0": 0.0, "x1": 250.0, "y1": 200.0, "label": "valley 98v", "colour": (0.95, 0.85, 0.2), "kind": "valley", "band": [240.0, 260.0]},
        ]}},
    }
    written = overlay.apply_plan(plan)
    assert written == [str(side)]
    im = Image.open(side).convert("RGB")
    # the dashed vertical cut at x=100 leaves red-ish pixels somewhere in that column
    col = [im.getpixel((100, y)) for y in range(0, 200, 5)]
    assert any(p[0] > 150 and p[1] < 120 for p in col)
    # the valley band tints the columns 240..260
    band = im.getpixel((250, 100))
    assert band != (20, 20, 20)
    # the legend box at the bottom left is no longer background
    assert im.getpixel((14, 180)) != (20, 20, 20)


def test_apply_report_marks_applied_and_is_rerunnable(tmp_path: Path, capsys):
    top = tmp_path / "t.png"
    Image.new("RGB", (100, 50), (0, 0, 0)).save(top)
    rp = tmp_path / "report.json"
    rp.write_text(json.dumps({"labelled": {"images": {"top": {"path": str(top), "lines": []}}, "legend": [], "title": "t"}}),
                  encoding="utf-8")
    assert overlay.apply_report(rp) == [str(top)]
    assert json.loads(rp.read_text(encoding="utf-8"))["labelled"]["applied"] is True
    assert overlay.apply_report(rp) == [str(top)]          # re-runnable: draws from the source render
    assert cli_main(["label", str(rp)]) == 0
    assert str(top) in capsys.readouterr().out


def test_missing_image_is_skipped_not_fatal(tmp_path: Path):
    plan = {"images": {"side": {"source": str(tmp_path / "absent.png"), "path": str(tmp_path / "out.png"), "lines": []}}, "legend": [], "title": ""}
    assert overlay.apply_plan(plan) == []
