"""The geometry half of a retarget, with no ``bpy``: face classification by rule, the
frame checks, socket placement, bounds and histograms, all over plain coordinate lists.

Conventions: every coordinate is in the add-on Blender frame (muzzle is ``-Y``, up is
``+Z``, ``x`` is UE's ``-x``). ``coords[i]`` is vertex ``i``; ``polys`` is a list of vertex
index lists; ``groups`` maps a source vertex-group name to the vertex indices for which it
is the heaviest group; ``attachments`` maps a glTF attachment name to a point.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Iterable, Sequence

Point = tuple[float, float, float]


# --------------------------------------------------------------------------- basics
def bounds(points: Iterable[Sequence[float]]) -> dict[str, list[float]]:
    pts = list(points)
    if not pts:
        return {"min": [0.0, 0.0, 0.0], "max": [0.0, 0.0, 0.0]}
    return {"min": [round(min(p[i] for p in pts), 4) for i in range(3)],
            "max": [round(max(p[i] for p in pts), 4) for i in range(3)]}


def histogram(values: Iterable[float], step: float = 5.0) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        key = f"{math.floor(v / step) * step:.0f}"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: float(kv[0])))


def centroid(points: Sequence[Sequence[float]]) -> list[float]:
    n = len(points)
    return [sum(p[k] for p in points) / n for k in range(3)] if n else [0.0, 0.0, 0.0]


def to_ue(point: Sequence[float]) -> list[float]:
    """Blender ``(bx, by, bz)`` -> UE mesh space ``(-bx, by, bz)`` (docs/FINDINGS.md)."""
    return [round(-point[0], 3), round(point[1], 3), round(point[2], 3)]


# --------------------------------------------------------------------------- bones
def classify_vertices(n: int, groups: dict[str, list[int]], bone_map: dict[str, str],
                      static: str) -> tuple[list[str], dict[str, int]]:
    """One gestalt bone per vertex from the source groups; unmapped vertices are static."""
    bones = [static] * n
    for group, bone in bone_map.items():
        for i in groups.get(group, []):
            bones[i] = bone
    counts: dict[str, int] = {}
    for b in bones:
        counts[b] = counts.get(b, 0) + 1
    return bones, counts


# --------------------------------------------------------------------------- face rules
def _face_matches(rule: dict[str, Any], vids: Sequence[int], coords: Sequence[Sequence[float]],
                  groups: dict[str, set[int]]) -> bool:
    cx = sum(coords[v][0] for v in vids) / len(vids)
    cy = sum(coords[v][1] for v in vids) / len(vids)
    cz = sum(coords[v][2] for v in vids) / len(vids)
    for key, val in rule.items():
        if key == "front_y_lt" and not min(coords[v][1] for v in vids) < val:
            return False
        if key == "rear_y_gt" and not max(coords[v][1] for v in vids) > val:
            return False
        if key == "cy_gt" and not cy > val:
            return False
        if key == "cy_lt" and not cy < val:
            return False
        if key == "cz_gt" and not cz > val:
            return False
        if key == "cz_lt" and not cz < val:
            return False
        if key == "cx_gt" and not cx > val:
            return False
        if key == "cx_lt" and not cx < val:
            return False
        if key == "cy_between" and not (val[0] < cy < val[1]):
            return False
        if key == "cz_between" and not (val[0] < cz < val[1]):
            return False
        if key == "majority_group":
            members = groups.get(val, set())
            if not sum(1 for v in vids if v in members) * 2 > len(vids):
                return False
    return True


def classify_faces(polys: Sequence[Sequence[int]], coords: Sequence[Sequence[float]],
                   fragments: Sequence[Any], groups: dict[str, list[int]]) -> dict[str, list[int]]:
    """Polygon index -> fragment name, by the recipe's ordered rules.

    ``fragments`` are :class:`bl2_retarget.recipe.Fragment` (or anything with ``name``,
    ``rule``, ``subset_of``, ``majority_group``). Rules are tried in recipe order and the
    first match wins; the ``"remainder"`` fragment takes what is left. A ``subset_of``
    fragment is filled afterwards from its parent's faces by majority vertex group.
    """
    group_sets = {k: set(v) for k, v in groups.items()}
    faces: dict[str, list[int]] = {f.name: [] for f in fragments}
    ruled = [f for f in fragments if not f.subset_of and f.rule != "remainder"]
    remainder = next(f for f in fragments if not f.subset_of and f.rule == "remainder")
    for index, vids in enumerate(polys):
        vids = list(vids)
        target = remainder.name
        for f in ruled:
            if _face_matches(f.rule, vids, coords, group_sets):  # type: ignore[arg-type]
                target = f.name
                break
        faces[target].append(index)
    for f in fragments:
        if f.subset_of:
            members = group_sets.get(f.majority_group, set())
            faces[f.name] = [i for i in faces[f.subset_of]
                             if sum(1 for v in polys[i] if v in members) * 2 > len(polys[i])]
    return faces


# --------------------------------------------------------------------------- frame checks
def _check(name: str, ok: bool, detail: str, level: str = "error") -> dict[str, Any]:
    return {"name": name, "status": "ok" if ok else level, "detail": detail}


def frame_checks(coords: Sequence[Sequence[float]], groups: dict[str, list[int]],
                 heads: dict[str, Sequence[float]], attachments: dict[str, Sequence[float]],
                 checks: Sequence[dict[str, Any]], anchor_group: str = "trigger") -> list[dict[str, Any]]:
    """Prove the placed mesh sits where the gestalt frame says. Each entry of ``checks`` is
    ``{"kind": ..., ...}``; unknown kinds are reported as errors so a typo cannot pass.

    kinds:
      longest_axis_y
      muzzle_neg_y                 the frontmost vertex is forward of the anchor group
      mag_hangs_down               {group, below: "floor"|<z>}  the magazine's lowest vertex
      highest_point                {y_frac_lt | y_frac_gt}  where along Y the top vertex sits
      group_side                   {group, axis, lt|gt, level}
      stock_behind_root            the rearmost vertex is behind the Root bone head
      attachment_muzzle            {name, tol_y, tol_x}  on the bore line at the front
      attachment_above_centre      {name}
      attachment_side              {name, lt|gt, level}
    """
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    zs = [c[2] for c in coords]
    span = {"x": max(xs) - min(xs), "y": max(ys) - min(ys), "z": max(zs) - min(zs)}
    out: list[dict[str, Any]] = []
    for spec in checks:
        kind = spec.get("kind")
        level = spec.get("level", "error")
        if kind == "longest_axis_y":
            longest = max(span, key=span.get)  # type: ignore[arg-type]
            out.append(_check("longest axis is Y", longest == "y",
                              f"spans x={span['x']:.1f} y={span['y']:.1f} z={span['z']:.1f}; longest is {longest.upper()}", level))
        elif kind == "muzzle_neg_y":
            anchor = groups.get(anchor_group, [])
            t_y = sum(coords[i][1] for i in anchor) / len(anchor) if anchor else 0.0
            out.append(_check("muzzle is at -Y", min(ys) < t_y,
                              f"front of the mesh y={min(ys):.2f}, {anchor_group} y={t_y:.2f} (the muzzle must be the most negative Y)", level))
        elif kind == "mag_hangs_down":
            mag = groups.get(spec.get("group", "clip"), [])
            if mag:
                low = min(coords[i][2] for i in mag)
                below = spec.get("below", "floor")
                limit = min(zs) + 1.0 if below == "floor" else float(below)
                out.append(_check("magazine hangs down (-Z)", low < limit,
                                  f"magazine reaches z={low:.2f}, limit z<{limit:.2f}, mesh floor z={min(zs):.2f}", level))
        elif kind == "highest_point":
            top = max(range(len(coords)), key=lambda i: coords[i][2])
            top_y = coords[top][1]
            if "y_frac_lt" in spec:
                line = min(ys) + float(spec["y_frac_lt"]) * span["y"]
                out.append(_check("highest point is forward (sights)", top_y < line,
                                  f"highest vertex at {tuple(round(v, 2) for v in coords[top])}; must be forward of y={line:.1f}", level))
            else:
                line = min(ys) + float(spec.get("y_frac_gt", 0.45)) * span["y"]
                out.append(_check("highest point is over the receiver (scope)", top_y > line,
                                  f"highest vertex at {tuple(round(v, 2) for v in coords[top])}; must be behind y={line:.1f}", level))
        elif kind == "group_side":
            members = groups.get(spec["group"], [])
            if members:
                axis = "xyz".index(spec.get("axis", "x"))
                if "lt" in spec:
                    v = min(coords[i][axis] for i in members)
                    ok = v < float(spec["lt"])
                    want = f"< {spec['lt']}"
                else:
                    v = max(coords[i][axis] for i in members)
                    ok = v > float(spec["gt"])
                    want = f"> {spec['gt']}"
                out.append(_check(f"{spec['group']} is on the expected side", ok,
                                  f"{spec['group']} reaches {spec.get('axis', 'x')}={v:.2f}; expected {want}", spec.get("level", "warn")))
        elif kind == "stock_behind_root":
            root_y = heads.get("Root", (0.0, 0.0, 0.0))[1]
            out.append(_check("stock ends behind Root", max(ys) > root_y,
                              f"rearmost vertex y={max(ys):.2f}; Root bone y={root_y:.2f}", level))
        elif kind == "attachment_muzzle":
            name = spec.get("name", "muzzle")
            if name in attachments:
                m = attachments[name]
                ok = abs(m[1] - min(ys)) < float(spec.get("tol_y", 4.0)) and abs(m[0]) < float(spec.get("tol_x", 3.0))
                out.append(_check("muzzle attachment is at the front of the barrel", ok,
                                  f"{name} attachment at {tuple(round(v, 2) for v in m)}, frontmost vertex y={min(ys):.2f}; must be on the bore line", level))
        elif kind == "attachment_above_centre":
            name = spec.get("name", "sight")
            if name in attachments:
                s = attachments[name]
                out.append(_check(f"{name} attachment is above the receiver, on the centre line",
                                  s[2] > 0.0 and abs(s[0]) < float(spec.get("tol_x", 3.0)),
                                  f"{name} attachment at {tuple(round(v, 2) for v in s)}", level))
        elif kind == "attachment_side":
            name = spec.get("name", "eject")
            if name in attachments:
                e = attachments[name]
                ok = e[0] < float(spec["lt"]) if "lt" in spec else e[0] > float(spec.get("gt", 0.0))
                out.append(_check(f"{name} attachment is on the expected side", ok,
                                  f"{name} attachment x={e[0]:.2f}", spec.get("level", "warn")))
        else:
            out.append(_check(f"check {kind!r}", False, "unknown check kind (recipe typo?)"))
    return out


def checks_ok(checks: Sequence[dict[str, Any]]) -> bool:
    return all(c["status"] != "error" for c in checks)


# --------------------------------------------------------------------------- sockets
def _filter(points: Sequence[Sequence[float]], where: dict[str, Any] | None) -> list[Sequence[float]]:
    if not where:
        return list(points)
    out = []
    for p in points:
        ok = True
        for k, v in where.items():
            axis = "xyz".index(k[0])
            if k.endswith("_gt") and not p[axis] > v:
                ok = False
            elif k.endswith("_lt") and not p[axis] < v:
                ok = False
            elif k.endswith("_between") and not (v[0] <= p[axis] <= v[1]):
                ok = False
        if ok:
            out.append(p)
    return out


def resolve_point(spec: dict[str, Any], fragment_coords: dict[str, Sequence[Sequence[float]]],
                  attachments: dict[str, Sequence[float]], resolved: dict[str, list[float]],
                  bone_vertices: dict[str, dict[str, list[int]]] | None = None) -> list[float] | None:
    """One socket position (Blender frame) from a point spec, or None if it cannot be had.

    ``resolved`` holds sockets already placed (Blender frame) for ``resolved`` /
    ``sight_line``; ``bone_vertices[fragment][bone]`` lists vertex indices for ``bone_centroid``.
    """
    if "literal" in spec:
        p = list(spec["literal"])
        return [-p[0], p[1], p[2]] if spec.get("space", "blender") == "ue" else p
    if "resolved" in spec:
        return list(resolved[spec["resolved"]]) if spec["resolved"] in resolved else None
    if "attachment" in spec:
        if spec["attachment"] not in attachments:
            return None
        p = list(attachments[spec["attachment"]])
        push = spec.get("push_outside")
        if push:
            body = fragment_coords.get(push["of"], [])
            band = float(push.get("band", 6.0))
            near = [c[0] for c in body if abs(c[1] - p[1]) < band]
            if near:
                margin = float(push.get("margin", 0.5))
                # the ejection side is Blender -X; push beyond the widest vertex on that side
                p[0] = min(p[0], min(near) - margin)
        return p
    if "ring" in spec:
        pts = fragment_coords.get(spec["of"], [])
        if not pts:
            return None
        tol = float(spec.get("tol", 0.8))
        if spec["ring"] == "front":
            y = min(c[1] for c in pts)
            ring = [c for c in pts if c[1] <= y + tol]
        else:
            y = max(c[1] for c in pts)
            ring = [c for c in pts if c[1] >= y - tol]
        p = centroid(ring)
        if spec.get("snap_y", True):
            p[1] = y
        att = spec.get("xz_from_attachment")
        if att and att in attachments:
            a = attachments[att]
            p = [a[0], p[1], a[2]]
        return p
    if "extreme" in spec:
        pts = _filter(fragment_coords.get(spec["of"], []), spec.get("where"))
        if not pts:
            return None
        which = spec["extreme"]
        key = {"highest": (2, max), "lowest": (2, min), "rearmost": (1, max), "frontmost": (1, min),
               "leftmost": (0, min), "rightmost": (0, max)}[which]
        axis, fn = key
        p = list(fn(pts, key=lambda c: c[axis]))
        return p
    if "bone_centroid" in spec:
        bv = (bone_vertices or {}).get(spec["of"], {}).get(spec["bone_centroid"], [])
        pts = fragment_coords.get(spec["of"], [])
        if not bv or not pts:
            return None
        return centroid([pts[i] for i in bv])
    if "sight_line" in spec:
        sl = spec["sight_line"]
        rear, front = resolved.get(sl.get("rear", "RearSight")), resolved.get(sl.get("front", "FrontSight"))
        if rear is None or front is None:
            return None
        y = float(sl.get("y", 40.0))
        if abs(front[1] - rear[1]) > 1e-3:
            slope = (front[2] - rear[2]) / (front[1] - rear[1])
            z = rear[2] + slope * (y - rear[1])
        else:
            z = rear[2]
        return [float(sl.get("x", 0.0)), y, z]
    if "composite" in spec:
        comp = spec["composite"]
        out = []
        for axis in ("x", "y", "z"):
            v = comp.get(axis, 0.0)
            if isinstance(v, dict):
                pts = fragment_coords.get(v["of"], [])
                if not pts:
                    return None
                idx = "xyz".index(v["extreme"][-1])
                fn = min if v["extreme"].startswith("min") else max
                val = fn(c[idx] for c in pts) * float(v.get("scale", 1.0)) + float(v.get("offset", 0.0))
            else:
                val = float(v)
            out.append(val)
        return out
    return None


def socket_overrides(rules: Sequence[Any], fragment_coords: dict[str, Sequence[Sequence[float]]],
                     attachments: dict[str, Sequence[float]],
                     bone_vertices: dict[str, dict[str, list[int]]] | None = None) -> tuple[dict[str, dict[str, list[float]]], list[str]]:
    """Resolve every socket rule in order; returns ``({fragment: {socket: UE point}}, notes)``."""
    resolved: dict[str, list[float]] = {}
    out: dict[str, dict[str, list[float]]] = {}
    notes: list[str] = []
    for r in rules:
        p = resolve_point(r.point, fragment_coords, attachments, resolved, bone_vertices)
        used = "point"
        if p is None and r.fallback is not None:
            p = resolve_point(r.fallback, fragment_coords, attachments, resolved, bone_vertices)
            used = "fallback"
        if p is None:
            notes.append(f"{r.fragment}.{r.socket}: could not be placed (no data for its point spec)")
            continue
        resolved[r.socket] = list(p)
        out.setdefault(r.fragment, {})[r.socket] = to_ue(p)
        if used == "fallback":
            notes.append(f"{r.fragment}.{r.socket}: placed from the fallback")
    return out, notes


def bounds_overrides(fragment_coords: dict[str, Sequence[Sequence[float]]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, pts in fragment_coords.items():
        ue = [to_ue(c) for c in pts]
        if not ue:
            continue
        lo = [min(c[i] for c in ue) for i in range(3)]
        hi = [max(c[i] for c in ue) for i in range(3)]
        origin = [round((lo[i] + hi[i]) / 2, 3) for i in range(3)]
        extent = [round((hi[i] - lo[i]) / 2, 3) for i in range(3)]
        out[name] = {"origin": origin, "extent": extent, "radius": round(math.sqrt(sum(e * e for e in extent)), 3)}
    return out


# --------------------------------------------------------------------------- cut proposals
def propose_cuts(coords: Sequence[Sequence[float]], groups: dict[str, list[int]], step: float = 5.0) -> dict[str, Any]:
    """Cheap hints for choosing cut planes: the Y histogram's valleys and per-group bounds.

    A valley is a bucket with fewer vertices than both neighbours; the thinnest bands are
    where a cut costs least. This is a hint, not a decision.
    """
    ys = [c[1] for c in coords]
    hist = histogram(ys, step)
    keys = [float(k) for k in hist]
    counts = [hist[k] for k in hist]
    valleys = []
    for i in range(1, len(counts) - 1):
        if counts[i] < counts[i - 1] and counts[i] < counts[i + 1]:
            valleys.append({"y_from": keys[i], "y_to": keys[i] + step, "vertices": counts[i]})
    valleys.sort(key=lambda v: v["vertices"])
    return {"y_histogram": hist, "y_valleys": valleys[:8],
            "group_bounds": {g: bounds([coords[i] for i in idx]) for g, idx in sorted(groups.items()) if idx}}
