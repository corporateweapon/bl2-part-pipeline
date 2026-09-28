"""Pixel comparison of in-game captures, restricted to the weapon region.

The verify loop asks two questions of a pair of 1920x1080 screenshots taken in the M2 mod's fixed
third-person pose (``SetBehindView(True)`` + ``FOV 100``):

* **did the change land?** -- the modified capture must differ from the stock baseline
* **is it reproducible?** -- two runs of the same build must produce the same picture

Comparing whole frames answers neither: the HUD clock, the character's idle animation, ambient
NPCs and the skybox all move between captures. So we crop to the weapon the pawn is holding --
box ``(560, 620, 880, 860)``, 320x240 px, which covers the gun in that pose -- and measure there.

Two numbers per pair:

``mean_abs_diff``
    mean |a-b| over all pixels and channels of the crop, in 0-255 levels. Good for eyeballing.

``diff_fraction``
    the fraction of crop pixels whose *largest channel difference* exceeds ``THRESHOLD`` (40
    levels). This is the number the thresholds below are written against, because it ignores the
    small, everywhere-present differences (lighting flicker, film grain, compression of the
    render target) and counts only pixels that genuinely changed colour.

Thresholds:

======================  ==========================  ====================================
comparison              expectation                 constant
======================  ==========================  ====================================
baseline vs modified    ``diff_fraction > 0.02``    ``DIFFER_MIN`` -- >2 % of crop pixels
run N vs run N+1        ``diff_fraction < 0.005``   ``MATCH_MAX`` -- <0.5 % of crop pixels
======================  ==========================  ====================================

**Sway tolerance (the reproducibility check only).** A weapon that fills most of the crop (the
TPS Dahl laser, 2026-09-22) has so much edge that sub-pixel idle sway alone exceeds 0.5 %,
even for the best-matching burst frames. ``best_pair(..., tolerance=SWAY_TOLERANCE_PX)`` counts a
pixel as changed only if **no** pixel of the other capture within that many pixels (Chebyshev)
matches it within ``THRESHOLD``, in both directions. Sway moves an edge by a pixel or two and is
absorbed; a real part or texture change moves whole regions and is not. The baseline check keeps
tolerance 0 -- there the question is "did anything change", and tolerance only makes it stricter.

The gap between the two (2 % vs 0.5 %) is deliberate: a real part swap moves thousands of pixels
(the M1/M2 barrel edit moves ~10-25 % of the crop), while two captures of the same build in the
same pose differ only at the edges of the animated arm.

CLI::

    python -m bl2_verify.compare a.png b.png [--box 560,620,880,860] [--expect differ|match]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

WEAPON_BOX: tuple[int, int, int, int] = (560, 620, 880, 860)
#: the same measurement in FIRST person (``options.harness_capture_view: "first"``, F22):
#: the held weapon fills a fixed region at the bottom right of a 1920x1080 frame at FOV 100
#: and nothing that breathes is inside it. Calibrated on an assault rifle held at the hip.
FIRST_PERSON_BOX: tuple[int, int, int, int] = (1120, 620, 1920, 1080)
THRESHOLD = 40
DIFFER_MIN = 0.02
MATCH_MAX = 0.005
#: neighbourhood radius (px, at the calibrated size) that absorbs idle sway in the
#: reproducibility check; see the module docstring
SWAY_TOLERANCE_PX = 2

#: every box above was measured on a 1920x1080 capture. The game's resolution is not ours
#: to fix -- it changed under us mid-session (F31) -- so boxes are scaled to whatever the
#: capture actually is instead of silently cropping the wrong part of the frame.
CALIBRATION_SIZE: tuple[int, int] = (1920, 1080)


def scale_box(box: tuple[int, int, int, int],
              size: tuple[int, int]) -> tuple[int, int, int, int]:
    """``box`` (calibrated at :data:`CALIBRATION_SIZE`) in the coordinates of ``size``."""
    sx = size[0] / CALIBRATION_SIZE[0]
    sy = size[1] / CALIBRATION_SIZE[1]
    return (int(round(box[0] * sx)), int(round(box[1] * sy)),
            int(round(box[2] * sx)), int(round(box[3] * sy)))


@dataclass(frozen=True)
class CompareResult:
    a: str
    b: str
    box: tuple[int, int, int, int]
    pixels: int
    mean_abs_diff: float
    diff_pixels: int
    diff_fraction: float
    threshold: int = THRESHOLD
    tolerance: int = 0

    @property
    def differs(self) -> bool:
        """True when the two captures are far enough apart to count as "the change landed"."""
        return self.diff_fraction > DIFFER_MIN

    @property
    def matches(self) -> bool:
        """True when the two captures are close enough to count as "reproducible"."""
        return self.diff_fraction < MATCH_MAX

    def to_dict(self) -> dict:
        d = asdict(self)
        d["box"] = list(self.box)
        d["differs"] = self.differs
        d["matches"] = self.matches
        d["differ_min"] = DIFFER_MIN
        d["match_max"] = MATCH_MAX
        return d


def _grow(box: tuple[int, int, int, int], margin: int) -> tuple[int, int, int, int]:
    return (box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin)


def _crop(path: Path, box: tuple[int, int, int, int], margin: int = 0):
    """Crop ``box`` from a capture, rescaling the box to the capture's own resolution.

    The boxes are calibrated at 1920x1080. A capture taken at another resolution is not
    an error -- the game's video settings changed under the loop once already (F31) --
    and cropping the literal box there would silently measure a different part of the
    frame, which reads as a huge, inexplicable diff rather than as a miscalibration.
    """
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        scaled = scale_box(box, (w, h))
        x0, y0, x1, y1 = scaled
        if x1 > w or y1 > h:
            raise ValueError(f"{path.name} is {w}x{h}, too small for box {box} -> {scaled}")
        if margin:
            # the sway margin may run past the frame edge (the first-person box ends on it);
            # PIL pads with black there, which can only add matches, never differences
            box = _grow(box, margin)
            scaled = scale_box(box, (w, h))
        crop = im.crop(scaled)
        if (w, h) != CALIBRATION_SIZE:
            # normalise back to the calibrated crop size so captures at two different
            # resolutions stay comparable pixel for pixel
            target = (box[2] - box[0], box[3] - box[1])
            crop = crop.resize(target, Image.BILINEAR)
        return crop.copy()


def _over_mask(diff):
    """L image: 255 where the largest channel of an RGB difference exceeds THRESHOLD."""
    from PIL import ImageChops
    r, g, b = diff.split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b)


def _tolerant_over(ca, cb_wide, margin: int, threshold: int):
    """Mask of ``ca`` pixels with no match within ``margin`` px in ``cb_wide``.

    ``cb_wide`` is the other capture cropped ``margin`` px wider on every side, so every
    shifted window is real image, not wrapped or padded.
    """
    from PIL import ImageChops
    w, h = ca.size
    best = None
    for dy in range(2 * margin + 1):
        for dx in range(2 * margin + 1):
            window = cb_wide.crop((dx, dy, dx + w, dy + h))
            d = _over_mask(ImageChops.difference(ca, window))
            best = d if best is None else ImageChops.darker(best, d)
    return best.point(lambda v: 255 if v > threshold else 0)


def compare(a: str | Path, b: str | Path, box: tuple[int, int, int, int] = WEAPON_BOX,
            threshold: int = THRESHOLD, tolerance: int = 0) -> CompareResult:
    """Compare the weapon crop of two captures.

    ``tolerance`` > 0 is the sway-tolerant count (module docstring): a pixel differs only if
    nothing within ``tolerance`` px of it in the other capture matches, checked both ways; the
    larger of the two counts is reported.
    """
    from PIL import ImageChops
    a, b = Path(a), Path(b)
    ca, cb = _crop(a, box), _crop(b, box)
    # both crops must describe the same region of the weapon even if one capture was
    # taken at a different resolution than the other (F31)
    if ca.size != cb.size:
        raise ValueError(f"crop size mismatch: {ca.size} vs {cb.size}")
    diff = ImageChops.difference(ca, cb)
    raw = diff.tobytes()  # RGB triples; tobytes avoids the deprecated getdata()
    n = diff.width * diff.height
    total = sum(raw)
    if tolerance > 0:
        ab = _tolerant_over(ca, _crop(b, box, tolerance), tolerance, threshold).histogram()[255]
        ba = _tolerant_over(cb, _crop(a, box, tolerance), tolerance, threshold).histogram()[255]
        over = max(ab, ba)
    else:
        over = 0
        for i in range(0, len(raw), 3):
            if raw[i] > threshold or raw[i + 1] > threshold or raw[i + 2] > threshold:
                over += 1
    return CompareResult(
        a=str(a), b=str(b), box=tuple(box), pixels=n,
        mean_abs_diff=total / (n * 3) if n else 0.0,
        diff_pixels=over, diff_fraction=over / n if n else 0.0, threshold=threshold,
        tolerance=tolerance)


def best_pair(a_paths: Sequence[str | Path], b_paths: Sequence[str | Path],
              box: tuple[int, int, int, int] = WEAPON_BOX,
              threshold: int = THRESHOLD, tolerance: int = 0) -> CompareResult:
    """Compare two *bursts* of captures and return the closest-matching pair.

    The pose the mod sets is fixed, but the pawn keeps breathing: the idle animation moves the
    weapon by well under a pixel between frames, which lights up every edge in the crop. Measured
    across three runs, two single frames of the *same* build landed anywhere between 0.12 % and
    1.40 % -- and no integer pixel shift aligns them, because the motion is sub-pixel and
    rotational. Sampling a handful of frames per run and taking the best-matching pair removes
    the animation phase from the answer while still comparing real captures.
    """
    if not a_paths or not b_paths:
        raise ValueError("both bursts need at least one capture")
    best: CompareResult | None = None
    for a in a_paths:
        for b in b_paths:
            res = compare(a, b, box, threshold, tolerance)
            if best is None or res.diff_fraction < best.diff_fraction:
                best = res
    assert best is not None
    return best


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bl2_verify.compare", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--box", default=",".join(str(v) for v in WEAPON_BOX),
                   help="x0,y0,x1,y1 crop (default: the weapon region)")
    p.add_argument("--threshold", type=int, default=THRESHOLD)
    p.add_argument("--tolerance", type=int, default=0,
                   help=f"sway tolerance in px (the reproducibility check uses {SWAY_TOLERANCE_PX})")
    p.add_argument("--expect", choices=("differ", "match"),
                   help="exit 1 unless the pair meets this expectation")
    args = p.parse_args(argv)
    box = tuple(int(v) for v in args.box.split(","))
    if len(box) != 4:
        print("--box needs four numbers", file=sys.stderr)
        return 2
    res = compare(args.a, args.b, box, args.threshold, args.tolerance)  # type: ignore[arg-type]
    print(json.dumps(res.to_dict(), indent=1))
    if args.expect == "differ":
        return 0 if res.differs else 1
    if args.expect == "match":
        return 0 if res.matches else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
