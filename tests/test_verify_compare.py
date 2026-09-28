"""Tests for ``bl2_verify.compare`` -- the weapon-crop pixel diff.

Everything here runs on synthetic 1920x1080 PNGs; nothing launches the game. The point is to
pin the two decisions the verify loop makes from this module:

* two captures of the same build must read as ``matches`` (``diff_fraction`` < 0.5 %)
* a capture where the weapon region changed must read as ``differs`` (> 2 %)
* and changes *outside* the weapon crop must not move either number, because the HUD clock and
  the world behind the character are different in every capture

Runnable with pytest or directly::

    python -m pytest tests/test_verify_compare.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bl2_verify.compare import (  # noqa: E402
    DIFFER_MIN, FIRST_PERSON_BOX, MATCH_MAX, SWAY_TOLERANCE_PX, THRESHOLD, WEAPON_BOX, best_pair, compare, main as compare_main,
)

Image = pytest.importorskip("PIL.Image", reason="Pillow is required for the capture comparison")

W, H = 1920, 1080
X0, Y0, X1, Y1 = WEAPON_BOX
CROP_PIXELS = (X1 - X0) * (Y1 - Y0)


def _frame(path: Path, weapon_fill: tuple[int, int, int] = (90, 90, 95),
           weapon_patch: tuple[int, int, int, int] | None = None,
           patch_fill: tuple[int, int, int] = (250, 40, 40),
           hud: tuple[int, int, int] = (10, 10, 10), noise: int = 0) -> Path:
    """A fake 1920x1080 capture: dark world, a flat 'weapon' in the crop, a 'HUD' block."""
    im = Image.new("RGB", (W, H), (20, 24, 30))
    im.paste(Image.new("RGB", (X1 - X0, Y1 - Y0), weapon_fill), (X0, Y0))
    if weapon_patch is not None:
        px0, py0, px1, py1 = weapon_patch
        im.paste(Image.new("RGB", (px1 - px0, py1 - py0), patch_fill), (px0, py0))
    im.paste(Image.new("RGB", (300, 80), hud), (1500, 40))  # 'HUD', outside the crop
    if noise:
        px = im.load()
        for i in range(0, X1 - X0, 3):  # a thin, sub-threshold shimmer over the weapon
            for j in range(0, Y1 - Y0, 3):
                r, g, b = px[X0 + i, Y0 + j]
                px[X0 + i, Y0 + j] = (min(255, r + noise), g, b)
    im.save(path)
    return path


def test_identical_frames_match(tmp_path: Path) -> None:
    a = _frame(tmp_path / "a.png")
    b = _frame(tmp_path / "b.png")
    res = compare(a, b)
    assert res.pixels == CROP_PIXELS
    assert res.mean_abs_diff == 0.0
    assert res.diff_pixels == 0
    assert res.matches and not res.differs


def test_sub_threshold_shimmer_still_matches(tmp_path: Path) -> None:
    """Small everywhere-differences (lighting flicker) must not break reproducibility."""
    a = _frame(tmp_path / "a.png")
    b = _frame(tmp_path / "b.png", noise=THRESHOLD - 10)
    res = compare(a, b)
    assert res.mean_abs_diff > 0.0, "the shimmer should show up in the mean"
    assert res.diff_fraction < MATCH_MAX
    assert res.matches and not res.differs


def test_changed_weapon_differs(tmp_path: Path) -> None:
    """A part swap repaints a big chunk of the crop: well over the 2 % bar."""
    a = _frame(tmp_path / "stock.png")
    b = _frame(tmp_path / "modified.png", weapon_patch=(X0 + 40, Y0 + 40, X0 + 200, Y0 + 180))
    res = compare(a, b)
    changed = (200 - 40) * (180 - 40)
    assert res.diff_pixels == changed
    assert res.diff_fraction == pytest.approx(changed / CROP_PIXELS)
    assert res.diff_fraction > DIFFER_MIN
    assert res.differs and not res.matches


def test_changes_outside_the_crop_are_ignored(tmp_path: Path) -> None:
    """The HUD and the world behind the pawn move between captures; the crop must not care."""
    a = _frame(tmp_path / "a.png", hud=(0, 0, 0))
    b = _frame(tmp_path / "b.png", hud=(255, 255, 255))
    res = compare(a, b)
    assert res.diff_pixels == 0
    assert res.matches


def test_borderline_fraction_lands_between_the_thresholds(tmp_path: Path) -> None:
    """A change covering ~1 % of the crop is neither 'stable' nor 'different enough'."""
    side = int((0.01 * CROP_PIXELS) ** 0.5)
    a = _frame(tmp_path / "a.png")
    b = _frame(tmp_path / "b.png", weapon_patch=(X0, Y0, X0 + side, Y0 + side))
    res = compare(a, b)
    assert MATCH_MAX < res.diff_fraction < DIFFER_MIN
    assert not res.matches and not res.differs


def test_custom_box_and_threshold(tmp_path: Path) -> None:
    a = _frame(tmp_path / "a.png", hud=(0, 0, 0))
    b = _frame(tmp_path / "b.png", hud=(255, 255, 255))
    res = compare(a, b, box=(1500, 40, 1800, 120))
    assert res.pixels == 300 * 80
    assert res.diff_fraction == 1.0
    assert compare(a, b, box=(1500, 40, 1800, 120), threshold=254).diff_fraction == 1.0
    assert compare(a, b, box=(1500, 40, 1800, 120), threshold=255).diff_fraction == 0.0


def test_box_outside_the_image_is_an_error(tmp_path: Path) -> None:
    a = _frame(tmp_path / "a.png")
    b = _frame(tmp_path / "b.png")
    with pytest.raises(ValueError):
        compare(a, b, box=(0, 0, W + 10, H))


def test_best_pair_picks_the_closest_frames(tmp_path: Path) -> None:
    """A burst stands in for one frame: the animation phase must not decide the verdict."""
    side = int((0.05 * CROP_PIXELS) ** 0.5)
    run_a = [_frame(tmp_path / "a0.png", weapon_patch=(X0, Y0, X0 + side, Y0 + side)),
             _frame(tmp_path / "a1.png")]
    run_b = [_frame(tmp_path / "b0.png", weapon_patch=(X0, Y0, X0 + side, Y0 + side),
                    patch_fill=(10, 200, 10)),
             _frame(tmp_path / "b1.png")]
    # every cross pair is far apart except a1/b1, which are identical
    assert compare(run_a[0], run_b[0]).diff_fraction > MATCH_MAX
    res = best_pair(run_a, run_b)
    assert res.diff_fraction == 0.0
    assert res.matches
    assert Path(res.a).name == "a1.png" and Path(res.b).name == "b1.png"


def test_best_pair_needs_both_bursts(tmp_path: Path) -> None:
    a = _frame(tmp_path / "a.png")
    with pytest.raises(ValueError):
        best_pair([a], [])


def test_cli_expect_flags(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = _frame(tmp_path / "a.png")
    b = _frame(tmp_path / "b.png", weapon_patch=(X0 + 40, Y0 + 40, X0 + 200, Y0 + 180))
    assert compare_main([str(a), str(b), "--expect", "differ"]) == 0
    assert compare_main([str(a), str(b), "--expect", "match"]) == 1
    assert compare_main([str(a), str(a), "--expect", "match"]) == 0
    out = capsys.readouterr().out
    assert '"diff_fraction"' in out and '"differs"' in out


def _stripes(path: Path, box: tuple[int, int, int, int], offset: int = 0,
             patch: tuple[int, int, int, int] | None = None) -> Path:
    """A 'weapon' made of dense vertical stripes (all edge), shifted ``offset`` px: idle sway."""
    im = Image.new("RGB", (W, H), (20, 24, 30))
    px = im.load()
    x0, y0, x1, y1 = box
    for x in range(x0, x1):
        on = ((x + offset) // 3) % 2 == 0
        for y in range(y0, y1, 1):
            px[x, y] = (230, 230, 230) if on else (40, 40, 40)
    if patch is not None:
        im.paste(Image.new("RGB", (patch[2] - patch[0], patch[3] - patch[1]), (250, 40, 40)),
                 (patch[0], patch[1]))
    im.save(path)
    return path


def test_sway_tolerance_absorbs_a_small_shift(tmp_path: Path) -> None:
    """The 2026-09-22 laser: a crop full of edges swaying a pixel fails the plain count."""
    a = _stripes(tmp_path / "a.png", FIRST_PERSON_BOX)
    b = _stripes(tmp_path / "b.png", FIRST_PERSON_BOX, offset=1)
    assert compare(a, b, FIRST_PERSON_BOX).diff_fraction > MATCH_MAX
    tolerant = compare(a, b, FIRST_PERSON_BOX, tolerance=SWAY_TOLERANCE_PX)
    assert tolerant.matches and tolerant.tolerance == SWAY_TOLERANCE_PX


def test_sway_tolerance_still_sees_a_real_change(tmp_path: Path) -> None:
    x0, y0 = FIRST_PERSON_BOX[:2]
    a = _stripes(tmp_path / "a.png", FIRST_PERSON_BOX)
    b = _stripes(tmp_path / "b.png", FIRST_PERSON_BOX, offset=1, patch=(x0 + 100, y0 + 100, x0 + 260, y0 + 200))
    res = compare(a, b, FIRST_PERSON_BOX, tolerance=SWAY_TOLERANCE_PX)
    assert not res.matches and res.diff_fraction > DIFFER_MIN


def test_sway_tolerance_on_a_box_touching_the_frame_edge(tmp_path: Path) -> None:
    """The first-person box ends on the frame edge; the margin must not raise there."""
    a = _stripes(tmp_path / "a.png", FIRST_PERSON_BOX)
    assert FIRST_PERSON_BOX[2] == W and FIRST_PERSON_BOX[3] == H
    assert compare(a, a, FIRST_PERSON_BOX, tolerance=SWAY_TOLERANCE_PX).diff_fraction == 0.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_first_person_box_is_inside_the_frame_and_off_the_pawn() -> None:
    """F22: the first-person crop is the weapon region at the bottom right of a 1920x1080
    frame; it must be a proper box inside the frame and distinct from the third-person one."""
    from bl2_verify import compare
    x0, y0, x1, y1 = compare.FIRST_PERSON_BOX
    assert 0 <= x0 < x1 <= 1920 and 0 <= y0 < y1 <= 1080
    assert compare.FIRST_PERSON_BOX != compare.WEAPON_BOX
    assert (x1 - x0) * (y1 - y0) >= 100_000, "a crop this small would measure the HUD, not the gun"
