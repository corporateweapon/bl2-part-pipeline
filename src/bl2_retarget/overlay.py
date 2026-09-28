"""The labelled cut preview, in two halves.

Inside Blender (:func:`plan_lines` is pure; the engine projects the lines it returns into
pixels with the render camera) the engine writes a *plan*: which images, which lines at
which pixels with which label and colour, and a legend. Outside Blender
(:func:`apply_plan`, Pillow) the lines, labels and legend are drawn onto the renders. The
split exists because Blender's Python has no Pillow, and drawing in image space is simpler
and sharper than modelling planes in the scene.

The picture answers two questions a retarget keeps asking: *where do the cut planes fall on
this gun* (every rule in the recipe is a line, coloured like the fragment it makes) and, in
measure-only mode, *where would a cut cost least* (the Y-histogram valleys as shaded bands).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

#: fragment colours, in recipe order; also applied to the Blender objects (workbench OBJECT colour)
PALETTE = [
    (0.85, 0.33, 0.31), (0.32, 0.60, 0.86), (0.36, 0.72, 0.36), (0.93, 0.66, 0.25),
    (0.62, 0.45, 0.80), (0.20, 0.72, 0.72), (0.80, 0.55, 0.35), (0.55, 0.55, 0.55),
]
VALLEY_COLOUR = (0.95, 0.85, 0.20)


def colour_for(index: int) -> tuple[float, float, float]:
    return PALETTE[index % len(PALETTE)]


# --------------------------------------------------------------------------- pure: rules -> lines
def plan_lines(fragments: Sequence[Any], colours: dict[str, tuple[float, float, float]],
               valleys: Sequence[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Turn the recipe's face rules into world-space lines: ``{axis, value, label, colour, kind}``.

    ``axis`` is the world axis the plane is perpendicular to (``y`` for a Y cut, ``z`` for a
    Z cut, ``x`` for an X cut). The engine decides which views can show each axis.
    """
    out: list[dict[str, Any]] = []
    for f in fragments:
        rule = getattr(f, "rule", None)
        if not isinstance(rule, dict):
            continue
        col = colours.get(f.name, (1.0, 1.0, 1.0))
        for key, val in rule.items():
            if key == "majority_group":
                continue
            if key.endswith("_between"):
                axis = key[1]
                for v, tag in ((val[0], ">"), (val[1], "<")):
                    out.append({"axis": axis, "value": float(v), "colour": col, "kind": "cut",
                                "label": f"{f.name}: c{axis} {tag} {v:g}"})
                continue
            if key.startswith("front_y"):
                axis, op = "y", "front y <"
            elif key.startswith("rear_y"):
                axis, op = "y", "rear y >"
            else:
                axis = key[1]
                op = f"c{axis} " + (">" if key.endswith("_gt") else "<")
            out.append({"axis": axis, "value": float(val), "colour": col, "kind": "cut",
                        "label": f"{f.name}: {op} {val:g}"})
    for v in valleys or []:
        out.append({"axis": "y", "value": float(v["y_from"]), "value_to": float(v["y_to"]), "colour": VALLEY_COLOUR,
                    "kind": "valley", "label": f"valley {v['vertices']}v"})
    return out


# --------------------------------------------------------------------------- Pillow: draw the plan
def _rgb(c: Sequence[float]) -> tuple[int, int, int]:
    return tuple(int(round(v * 255)) for v in c[:3])  # type: ignore[return-value]


def _font(size: int):
    from PIL import ImageFont
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _place(rows: list[list[tuple[float, float]]], x0: float, x1: float) -> int:
    """First row where [x0, x1] overlaps nothing already placed; rows grow as needed."""
    for i, row in enumerate(rows):
        if all(x1 < a - 4 or x0 > b + 4 for a, b in row):
            row.append((x0, x1))
            return i
    rows.append([(x0, x1)])
    return len(rows) - 1


def apply_plan(plan: dict[str, Any]) -> list[str]:
    """Draw every image's lines and the legend. Returns the paths written.

    ``plan`` is what the engine wrote::

        {"images": {"side": {"source": <plain render>, "path": <labelled output>,
                             "lines": [{"x0","y0","x1","y1","label","colour","kind","band": [x0, x1] | null}]}},
         "legend": [{"name", "colour", "vertices", "triangles", "bone"}], "title": "..."}

    Everything is drawn on a transparent layer composited over the render, so bands really
    are translucent and lines stay visible through them. Labels for vertical lines stack in
    rows along the top, for bands along the bottom, each row free of overlaps.
    """
    from PIL import Image, ImageDraw

    written: list[str] = []
    font, small = _font(16), _font(13)
    for view, img in (plan.get("images") or {}).items():
        source = Path(img.get("source") or img["path"])
        if not source.exists():
            continue
        base = Image.open(source).convert("RGBA")
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        w, h = base.size
        top_rows: list[list[tuple[float, float]]] = []
        bottom_rows: list[list[tuple[float, float]]] = []
        left_rows: list[list[tuple[float, float]]] = []
        # bands first, so lines and labels sit on top of them
        for line in img.get("lines") or []:
            if line.get("kind") == "valley" and line.get("band"):
                col = _rgb(line["colour"])
                x0, x1 = sorted(line["band"])
                draw.rectangle([x0, 0, x1, h], fill=(*col, 60))
                draw.line([(x0, 0), (x0, h)], fill=(*col, 140), width=1)
                draw.line([(x1, 0), (x1, h)], fill=(*col, 140), width=1)
                tw = draw.textlength(line["label"], font=small)
                cx = (x0 + x1) / 2
                row = _place(bottom_rows, cx - tw / 2, cx + tw / 2)
                pos = (cx - tw / 2, h - 30 - 17 * row)
                draw.rectangle([pos[0] - 2, pos[1] - 1, pos[0] + tw + 2, pos[1] + 15], fill=(0, 0, 0, 170))
                draw.text(pos, line["label"], fill=(*col, 255), font=small)
        for line in img.get("lines") or []:
            if line.get("kind") == "valley":
                continue
            col = _rgb(line["colour"])
            x0, y0, x1, y1 = line["x0"], line["y0"], line["x1"], line["y1"]
            dx, dy = x1 - x0, y1 - y0
            length = max((dx * dx + dy * dy) ** 0.5, 1.0)
            steps = max(int(length / 12), 1)
            for i in range(0, steps, 2):
                t0, t1 = i / steps, min((i + 1) / steps, 1.0)
                draw.line([(x0 + dx * t0, y0 + dy * t0), (x0 + dx * t1, y0 + dy * t1)], fill=(*col, 235), width=3)
            tw = draw.textlength(line["label"], font=small)
            if abs(dx) < abs(dy):                       # vertical line: label along the top
                lx = min(x0, x1) + 4
                row = _place(top_rows, lx, lx + tw)
                pos = (lx, 8 + 18 * row)
            else:                                       # horizontal line: label along the left
                ly = min(y0, y1) - 20
                row = _place(left_rows, ly, ly + 16)
                pos = (8 + (tw + 12) * row, ly)
            draw.rectangle([pos[0] - 2, pos[1] - 1, pos[0] + tw + 2, pos[1] + 15], fill=(0, 0, 0, 170))
            draw.text(pos, line["label"], fill=(*col, 255), font=small)
        legend = plan.get("legend") or []
        if legend or plan.get("title"):
            rows_n = len(legend) + (1 if plan.get("title") else 0)
            x, y = 12, h - 12 - 20 * rows_n
            widths = [draw.textlength(plan.get("title") or "", font=font)]
            widths += [draw.textlength(f"{e['name']}  {e.get('vertices', '?')} v / {e.get('triangles', '?')} t  [{e.get('bone')}]", font=small) + 24
                       for e in legend]
            box_w = 12 + max(widths)
            draw.rectangle([x - 6, y - 6, x + box_w, h - 6], fill=(0, 0, 0, 170))
            if plan.get("title"):
                draw.text((x, y), plan["title"], fill=(255, 255, 255, 255), font=font)
                y += 22
            for entry in legend:
                draw.rectangle([x, y + 3, x + 14, y + 15], fill=(*_rgb(entry["colour"]), 255))
                txt = f"{entry['name']}  {entry.get('vertices', '?')} v / {entry.get('triangles', '?')} t"
                if entry.get("bone"):
                    txt += f"  [{entry['bone']}]"
                draw.text((x + 20, y), txt, fill=(255, 255, 255, 255), font=small)
                y += 20
        draw.text((w - 60, 8), view, fill=(255, 255, 255, 220), font=font)
        out = Image.alpha_composite(base, layer).convert("RGB")
        out.save(img["path"])
        written.append(str(img["path"]))
    return written


def apply_report(report_path: Path) -> list[str]:
    """Apply the plan stored under ``labelled`` in a retarget report (re-runnable: it draws
    from the plain renders the engine kept), and mark it applied."""
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    plan = report.get("labelled")
    if not plan:
        return []
    written = apply_plan(plan)
    plan["applied"] = True
    Path(report_path).write_text(json.dumps(report, indent=1), encoding="utf-8")
    return written
