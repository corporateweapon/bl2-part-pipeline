"""The Blender half of a recipe-driven retarget.

Runs under ``blender --background --python src/bl2_retarget/engine.py -- --recipe <json>``
(``python -m bl2_retarget run <recipe>`` spawns exactly that). Everything weapon-specific
comes from the recipe; everything geometric is delegated to :mod:`bl2_retarget.geometry`.
The steps are the ones ``ak47_retarget.py`` / ``awp_retarget.py`` performed, in the same
order, so a recipe that transcribes one of them reproduces its package byte for byte:

1. import the host's reference fragment (armature, bone rest heads, sockets)
2. import the glb, keep the body mesh, measure the attachment empties (bone-tail
   offset removed, armatures at rest), unparent **keeping the world matrix**
3. apply the frame matrix, anchor a vertex-group centroid on a gestalt bone, apply
4. frame checks (stop unless ``--measure-only``)
5. classify faces by the recipe's rules, carve with bmesh, one bone per vertex, UV0,
   triangulate, bind to the armature
6. socket + bounds overrides, validate, export the fragments marked for export,
   previews, report; ``<report>`` also carries a ``spec_fragments`` block that
   ``python -m bl2_retarget apply-spec`` writes into a partgen spec
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    _here = Path(__file__).resolve()
    if str(_here.parents[1]) not in sys.path:
        sys.path.insert(0, str(_here.parents[1]))

from bl2_blender import api  # noqa: E402
from bl2_retarget import geometry as geo  # noqa: E402
from bl2_retarget.recipe import REPO, Recipe, load_recipe  # noqa: E402


# --------------------------------------------------------------------------- transform
def frame_matrix(recipe: Recipe, scale: float):
    """glTF-import frame -> add-on Blender frame, as a ``mathutils.Matrix``.

    For a preset with a ``diagonal`` the matrix is also derived the long way (source ->
    UE -> Blender) and both are asserted equal, as the two scripts did; any frame must be
    a proper rotation (positive determinant) or every triangle winding inverts.
    """
    from mathutils import Matrix

    f = recipe.frame()
    if "matrix" in f:
        m = Matrix([[float(v) for v in row] for row in f["matrix"]])
        m = Matrix.Scale(float(scale), 4) @ m
    else:
        factor = float(f["units_to_unreal"]) * float(scale)
        d = f["diagonal"]
        m = Matrix.Diagonal((d[0] * factor, d[1] * factor, d[2] * factor, 1.0))
        if d == [-1.0, -1.0, 1.0]:
            # long form, CS2: G -> CS2 -> UE -> B
            g_to_cs2 = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))
            cs2_to_ue = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
            ue_to_b = Matrix.Diagonal((-1.0, 1.0, 1.0, 1.0))
            long = Matrix.Scale(factor, 4) @ ue_to_b @ cs2_to_ue @ g_to_cs2
            worst = max(abs(m[r][c] - long[r][c]) for r in range(4) for c in range(4))
            if worst > 1e-9:
                raise AssertionError(f"the two derivations of the frame matrix disagree by {worst}")
    if m.determinant() <= 0.0:
        raise AssertionError(f"frame matrix determinant {m.determinant()} is not positive; a mirror would invert every triangle")
    return m


# --------------------------------------------------------------------------- bpy helpers
def _world_coords(obj) -> list[tuple[float, float, float]]:
    mw = obj.matrix_world
    return [tuple(mw @ v.co) for v in obj.data.vertices]


def _group_indices(obj) -> dict[str, list[int]]:
    names = {g.index: g.name for g in obj.vertex_groups}
    out: dict[str, list[int]] = {name: [] for name in names.values()}
    for v in obj.data.vertices:
        best, weight = None, 0.0
        for g in v.groups:
            if g.weight > weight:
                best, weight = names.get(g.group), g.weight
        if best is not None:
            out[best].append(v.index)
    return out


def bone_heads(arm) -> dict[str, tuple[float, float, float]]:
    return {b.name: tuple(arm.matrix_world @ b.head_local) for b in arm.data.bones}


def socket_worlds(arm) -> dict[str, tuple[float, float, float]]:
    return {child.name: tuple(child.matrix_world.translation) for child in arm.children if child.type == "EMPTY"}


def import_reference(recipe: Recipe):
    import bpy

    host = recipe.host
    result = api.import_fragment(recipe.resolve(host.get("package")), host["mesh_path"],
                                 host["reference_fragment"], recipe.resolve(host.get("fragment_table")))
    obj = bpy.data.objects[result.object_name]
    arm = bpy.data.objects[result.armature_name]
    bpy.context.view_layer.update()
    return result, obj, arm


def _attachment_point(obj, arm):
    from mathutils import Vector

    point = obj.matrix_world.translation.copy()
    bone = arm.data.bones.get(obj.parent_bone) if (arm is not None and obj.parent_type == "BONE") else None
    if bone is None:
        return point
    axis = (arm.matrix_world @ bone.matrix_local).to_3x3() @ Vector((0.0, 1.0, 0.0))
    return point - axis * bone.length


def import_source(recipe: Recipe):
    """Import the glb; keep the body mesh, measure attachments, drop the rest."""
    import bpy

    src = recipe.source
    glb = recipe.resolve(src["glb"])
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(glb))
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    if not meshes:
        raise RuntimeError(f"{glb} contains no mesh")
    match = str(src.get("body_match") or "").lower()
    exclude = str(src.get("body_exclude") or "arms").lower()

    def is_body(o) -> bool:
        name = (o.name + " " + o.data.name).lower()
        return bool(match) and match in name and exclude not in name

    body = next((o for o in meshes if is_body(o)), None) or max(meshes, key=lambda o: len(o.data.vertices))

    armatures = [o for o in new if o.type == "ARMATURE"]
    for a in armatures:
        a.data.pose_position = "REST"
    bpy.context.view_layer.update()

    wanted = tuple(src.get("attachments") or [])
    attachments: dict[str, Any] = {}
    for obj in new:
        base = obj.name.split(".")[0]
        if base in wanted and base not in attachments and obj.type == "EMPTY":
            arm = obj.parent if obj.parent in armatures else (armatures[0] if armatures else None)
            attachments[base] = _attachment_point(obj, arm)

    dropped = [f"{o.name} ({o.type})" for o in new if o is not body]
    matrix = body.matrix_world.copy()
    body.parent = None
    body.matrix_world = matrix     # unparenting resets matrix_basis (M9 section 2)
    bpy.context.view_layer.update()
    for o in new:
        if o is not body:
            bpy.data.objects.remove(o, do_unlink=True)
    body.modifiers.clear()
    body.name = f"{recipe.name}_Source"
    bpy.context.view_layer.update()
    return body, attachments, dropped


def place(recipe: Recipe, body, attachments: dict[str, Any], arm, scale: float) -> dict[str, Any]:
    import bpy
    from mathutils import Matrix, Vector

    matrix = frame_matrix(recipe, scale)
    body.matrix_world = matrix @ body.matrix_world
    for k in list(attachments):
        attachments[k] = matrix @ attachments[k]
    bpy.context.view_layer.update()

    pl = recipe.placement
    group, bone = pl.get("anchor_group", "trigger"), pl.get("anchor_bone", "Trigger")
    idx = _group_indices(body).get(group) or []
    if not idx:
        raise RuntimeError(f"the source mesh has no {group!r} vertex group to anchor on")
    coords = _world_coords(body)
    c = geo.centroid([coords[i] for i in idx])
    heads = bone_heads(arm)
    if bone not in heads:
        raise RuntimeError(f"the host armature has no bone {bone!r}")
    target = Vector(heads[bone])
    delta = target - Vector(c)
    body.matrix_world = Matrix.Translation(delta) @ body.matrix_world
    for k in list(attachments):
        attachments[k] = attachments[k] + delta
    bpy.context.view_layer.update()
    api.apply_transforms(body)
    bpy.context.view_layer.update()

    coords = _world_coords(body)
    muzzle_y = min(p[1] for p in coords)
    tip = geo.centroid([p for p in coords if p[1] <= muzzle_y + 0.5])
    tip[1] = muzzle_y
    sockets = socket_worlds(arm)
    host = recipe.host
    socket = list(sockets.get(host.get("muzzle_socket", ""), host.get("muzzle_socket_default", (0.0, 0.0, 0.0))))
    import math
    distance = math.dist(tip, socket)
    reach = ((socket[1] - target.y) / (muzzle_y - target.y)) if muzzle_y != target.y else 0.0
    return {
        "scale": float(scale),
        "matrix": [[round(matrix[r][c], 6) for c in range(4)] for r in range(4)],
        "translation": [round(v, 4) for v in delta],
        "anchor": f"{group} vertex-group centroid -> {bone} bone head",
        "anchor_centroid_before": [round(v, 4) for v in c],
        "anchor_bone": [round(v, 4) for v in target],
        "muzzle_socket": [round(v, 4) for v in socket],
        "muzzle_tip": [round(v, 4) for v in tip],
        "muzzle_distance": round(distance, 3),
        "muzzle_distance_y": round(abs(socket[1] - muzzle_y), 3),
        "scale_that_would_reach_socket": round(reach * float(scale), 4),
    }


# --------------------------------------------------------------------------- the split
def _set_bone_groups(obj, bones: list[str]) -> dict[str, int]:
    for g in list(obj.vertex_groups):
        obj.vertex_groups.remove(g)
    buckets: dict[str, list[int]] = {}
    for i, b in enumerate(bones):
        buckets.setdefault(b, []).append(i)
    for b, idx in buckets.items():
        obj.vertex_groups.new(name=b).add(idx, 1.0, "REPLACE")
    return {b: len(idx) for b, idx in sorted(buckets.items())}


def _carve(body, name: str, face_indices: list[int], collection):
    import bmesh
    import bpy

    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bm.from_mesh(body.data)
    bm.faces.ensure_lookup_table()
    wanted = set(face_indices)
    doomed = [f for f in bm.faces if f.index not in wanted]
    if doomed:
        bmesh.ops.delete(bm, geom=doomed, context="FACES")
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    for g in body.vertex_groups:
        obj.vertex_groups.new(name=g.name)
    return obj


def _finish(obj, arm, bone: str | None, static: str) -> dict[str, Any]:
    import bpy

    mesh = obj.data
    if bone is not None:
        counts = _set_bone_groups(obj, [bone] * len(mesh.vertices))
    else:
        names = {g.index: g.name for g in obj.vertex_groups}
        bones = []
        for v in mesh.vertices:
            best, weight = static, 0.0
            for g in v.groups:
                if g.weight > weight:
                    best, weight = names.get(g.group, static), g.weight
            bones.append(best)
        counts = _set_bone_groups(obj, bones)
    if api.UV0_NAME not in mesh.uv_layers:
        if mesh.uv_layers:
            mesh.uv_layers[0].name = api.UV0_NAME
        else:
            mesh.uv_layers.new(name=api.UV0_NAME)
    for layer in [u for u in mesh.uv_layers if u.name != api.UV0_NAME]:
        mesh.uv_layers.remove(layer)
    api.ensure_triangulated(obj)
    api.apply_transforms(obj)
    obj.parent = arm
    mod = obj.modifiers.new(name="Armature", type="ARMATURE")
    mod.object = arm
    bpy.context.view_layer.update()
    return {"vertices": len(mesh.vertices), "triangles": len(mesh.polygons), "bones": counts,
            "bounds": geo.bounds(_world_coords(obj))}


def build_fragments(recipe: Recipe, body, arm) -> tuple[dict[str, Any], dict[str, Any]]:
    import bpy

    groups = _group_indices(body)
    bones, bone_counts = geo.classify_vertices(len(body.data.vertices), groups, recipe.bone_map, recipe.static_bone)
    coords = _world_coords(body)
    polys = [list(p.vertices) for p in body.data.polygons]
    faces = geo.classify_faces(polys, coords, recipe.rule_fragments, groups)
    _set_bone_groups(body, bones)

    collection = bpy.data.collections.new(f"{recipe.name}_Fragments")
    bpy.context.scene.collection.children.link(collection)
    objects: dict[str, Any] = {}
    stats: dict[str, Any] = {"source_bone_counts": bone_counts, "source_vertices": len(body.data.vertices),
                             "source_triangles": len(body.data.polygons), "fragments": {}}
    for f in recipe.fragments:
        idx = faces[f.name]
        if not idx:
            raise RuntimeError(f"fragment {f.name} came out empty with rule {f.rule!r}; re-tune the recipe")
        obj = _carve(body, f.name, idx, collection)
        stats["fragments"][f.name] = _finish(obj, arm, f.bone, recipe.static_bone)
        stats["fragments"][f.name]["source_faces"] = len(idx)
        objects[f.name] = obj
    return objects, stats


def _bone_vertices(objects: dict[str, Any]) -> dict[str, dict[str, list[int]]]:
    out: dict[str, dict[str, list[int]]] = {}
    for name, obj in objects.items():
        names = {g.index: g.name for g in obj.vertex_groups}
        per: dict[str, list[int]] = {}
        for v in obj.data.vertices:
            for g in v.groups:
                if g.weight > 0.0:
                    per.setdefault(names.get(g.group, "?"), []).append(v.index)
        out[name] = per
    return out


# --------------------------------------------------------------------------- previews
def render_previews(out_dir: Path, targets, prefix: str) -> list[str]:
    import bpy
    from mathutils import Vector

    out_dir.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_WORKBENCH"
    except TypeError:
        pass
    scene.render.resolution_x, scene.render.resolution_y = 1600, 900
    scene.render.film_transparent = False
    shading = scene.display.shading
    shading.light, shading.color_type, shading.show_object_outline = "STUDIO", "OBJECT", True
    scene.display.render_aa = "8"
    points = [p for obj in targets for p in _world_coords(obj)]
    lo = Vector((min(p[i] for p in points) for i in range(3)))
    hi = Vector((max(p[i] for p in points) for i in range(3)))
    centre, size = (lo + hi) * 0.5, max(hi - lo) * 1.15
    cam_data = bpy.data.cameras.new("Preview")
    cam_data.type, cam_data.ortho_scale = "ORTHO", size
    cam = bpy.data.objects.new("Preview", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    written = []
    for name, direction in {"side": Vector((1.0, 0.0, 0.0)), "top": Vector((0.0, 0.0, 1.0)),
                            "threequarter": Vector((0.8, 0.45, 0.45))}.items():
        direction = direction.normalized()
        cam.location = centre + direction * (size * 2.5)
        cam.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()
        path = out_dir / f"{prefix}_{name}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        written.append(str(path))
    return written


def render_labelled(recipe: Recipe, out_dir: Path, prefix: str, targets: dict[str, Any],
                    lines: list[dict[str, Any]], legend: list[dict[str, Any]], title: str,
                    extra_targets: list[Any] | None = None) -> dict[str, Any]:
    """Side + top renders with each target coloured, plus a pixel-space plan of ``lines``
    (world-axis planes from :func:`overlay.plan_lines`) for :func:`overlay.apply_plan`.

    The camera maths never leaves Blender: every line's endpoints are projected with
    ``world_to_camera_view``, so no view has a sign to get wrong.
    """
    import bpy
    from bpy_extras.object_utils import world_to_camera_view
    from mathutils import Vector

    from bl2_retarget import overlay

    out_dir.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_WORKBENCH"
    except TypeError:
        pass
    rx, ry = 1600, 900
    scene.render.resolution_x, scene.render.resolution_y = rx, ry
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    shading = scene.display.shading
    shading.light, shading.color_type, shading.show_object_outline = "STUDIO", "OBJECT", True
    scene.display.render_aa = "8"
    for i, (name, obj) in enumerate(targets.items()):
        c = overlay.colour_for(i)
        obj.color = (c[0], c[1], c[2], 1.0)
    for obj in extra_targets or []:
        obj.color = (0.75, 0.75, 0.75, 1.0)
    all_targets = [*targets.values(), *(extra_targets or [])]
    points = [p for obj in all_targets for p in _world_coords(obj)]
    lo = Vector((min(p[i] for p in points) for i in range(3)))
    hi = Vector((max(p[i] for p in points) for i in range(3)))
    centre, size = (lo + hi) * 0.5, max(hi - lo) * 1.15
    cam_data = bpy.data.cameras.new("Labelled")
    cam_data.type, cam_data.ortho_scale = "ORTHO", size
    cam = bpy.data.objects.new("Labelled", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    pad = size * 0.6
    axis_span = {"x": (lo.x - pad, hi.x + pad), "y": (lo.y - pad, hi.y + pad), "z": (lo.z - pad, hi.z + pad)}
    plan: dict[str, Any] = {"images": {}, "legend": legend, "title": title, "applied": False}
    views = {"side": (Vector((1.0, 0.0, 0.0)), ("y", "z")), "top": (Vector((0.0, 0.0, 1.0)), ("y", "x"))}
    for view, (direction, visible_axes) in views.items():
        from math import radians

        from mathutils import Quaternion

        direction = direction.normalized()
        cam.location = centre + direction * (size * 2.5)
        quat = direction.to_track_quat("Z", "Y")
        if view == "top":
            # a quarter turn about the view axis lays the gun's long (Y) axis along the
            # image width instead of its height, so a 16:9 frame does not crop it
            quat = quat @ Quaternion((0.0, 0.0, 1.0), radians(90.0))
        cam.rotation_euler = quat.to_euler()
        bpy.context.view_layer.update()
        source = out_dir / f"{prefix}_plain_{view}.png"        # kept, so the overlay can be redrawn
        path = out_dir / f"{prefix}_labelled_{view}.png"
        scene.render.filepath = str(source)
        bpy.ops.render.render(write_still=True)

        def px(p: Vector) -> tuple[float, float]:
            v = world_to_camera_view(scene, cam, p)
            return (v.x * rx, (1.0 - v.y) * ry)

        out_lines = []
        for line in lines:
            axis = line["axis"]
            if axis not in visible_axes:
                continue
            other = [a for a in visible_axes if a != axis][0]
            o0, o1 = axis_span[other]

            def pt(val: float, o: float) -> Vector:
                d = {"x": centre.x, "y": centre.y, "z": centre.z}
                d[axis] = val
                d[other] = o
                return Vector((d["x"], d["y"], d["z"]))

            (x0, y0), (x1, y1) = px(pt(line["value"], o0)), px(pt(line["value"], o1))
            entry = {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "label": line["label"],
                     "colour": line["colour"], "kind": line["kind"]}
            if line.get("kind") == "valley" and "value_to" in line:
                (bx0, _), (bx1, _) = px(pt(line["value"], o0)), px(pt(line["value_to"], o0))
                entry["band"] = [bx0, bx1]
            out_lines.append(entry)
        plan["images"][view] = {"source": str(source), "path": str(path), "lines": out_lines}
    bpy.data.objects.remove(cam, do_unlink=True)
    return plan


# --------------------------------------------------------------------------- the run
def _process(recipe: Recipe, ref_obj, arm, heads, *, scale: float | None, measure_only: bool,
             skip_render: bool, with_extras: bool, report: dict[str, Any]) -> tuple[dict[str, Any], list[Any]]:
    """Import, place, check, split and measure ONE recipe against an already imported host.

    Fills ``report`` and returns ``(objects, to_export)``; the caller exports (one recipe or a
    whole package group) and writes the report.
    """
    import bpy

    scale = float(recipe.placement.get("scale", 1.0)) if scale is None else float(scale)
    body, attachments, dropped = import_source(recipe)
    report["glb_dropped"] = dropped
    report["source"] = {"vertices": len(body.data.vertices), "triangles": len(body.data.polygons),
                        "uv_layers": [u.name for u in body.data.uv_layers],
                        "vertex_groups": [g.name for g in body.vertex_groups],
                        "attachments": sorted(attachments), "bounds_glb_frame": geo.bounds(_world_coords(body))}
    report["placement"] = place(recipe, body, attachments, arm, scale)
    coords = _world_coords(body)
    groups = _group_indices(body)
    att = {k: tuple(v) for k, v in attachments.items()}
    report["placed_bounds"] = geo.bounds(coords)
    report["attachments_placed"] = {k: [round(v, 3) for v in p] for k, p in sorted(att.items())}
    report["measure"] = geo.propose_cuts(coords, groups)
    report["measure"]["z_histogram"] = geo.histogram([c[2] for c in coords], 5.0)

    checks = geo.frame_checks(coords, groups, heads, att, recipe.checks, recipe.placement.get("anchor_group", "trigger"))
    report["frame_checks"] = checks
    for c in checks:
        print(f"   [{c['status']}] {c['name']}: {c['detail']}", flush=True)
    if not geo.checks_ok(checks) and not measure_only:
        raise RuntimeError(f"{recipe.name}: the frame checks failed; fix the recipe's frame/anchor before going near the game")

    if measure_only:
        if not skip_render:
            from bl2_retarget import overlay
            colours = {f.name: overlay.colour_for(i) for i, f in enumerate(recipe.fragments)}
            lines = overlay.plan_lines(recipe.fragments, colours, report["measure"].get("y_valleys"))
            legend = [{"name": recipe.name, "colour": (0.75, 0.75, 0.75), "vertices": len(coords),
                       "triangles": len(body.data.polygons)}]
            report["labelled"] = render_labelled(
                recipe, recipe.out_path("captures", "scratch/captures"),
                recipe.output.get("preview_prefix", f"{recipe.name.lower()}_retarget"),
                {}, lines, legend, f"{recipe.name}: placed; rules as they stand, valleys = cut candidates",
                extra_targets=[body, ref_obj])
        report.update(ok=True, measure_only=True)
        bpy.data.objects.remove(body, do_unlink=True)
        return {}, []

    objects, stats = build_fragments(recipe, body, arm)
    report["fragments"] = stats
    bpy.data.objects.remove(body, do_unlink=True)

    frag_coords = {name: _world_coords(obj) for name, obj in objects.items()}
    overrides, notes = geo.socket_overrides(recipe.sockets, frag_coords, att, _bone_vertices(objects))
    report["socket_overrides"] = overrides
    report["socket_notes"] = notes
    report["bounds_overrides"] = geo.bounds_overrides(frag_coords)

    to_export = [f for f in recipe.fragments if f.export or with_extras]
    report["validate"] = {}
    blocked = []
    for f in to_export:
        cs = api.validate_for_export(objects[f.name])
        report["validate"][f.name] = [c.to_dict() for c in cs]
        for c in cs:
            print(f"   {f.name}: {c}", flush=True)
        if not api.checks_ok(cs):
            blocked.append(f.name)
    if blocked:
        raise RuntimeError(f"{recipe.name}: validate_for_export refused {blocked}")
    report["exported_fragments"] = [f.name for f in to_export]

    if not skip_render:
        from bl2_retarget import overlay
        ordered = [objects[f.name] for f in to_export]
        report["captures"] = render_previews(recipe.out_path("captures", "scratch/captures"), [ref_obj, *ordered],
                                             recipe.output.get("preview_prefix", f"{recipe.name.lower()}_retarget"))
        colours = {f.name: overlay.colour_for(i) for i, f in enumerate(recipe.fragments)}
        lines = overlay.plan_lines(recipe.fragments, colours)
        legend = [{"name": f.name, "colour": colours[f.name], "bone": f.bone,
                   **{k: stats["fragments"][f.name][k] for k in ("vertices", "triangles")}}
                  for f in recipe.fragments]
        ref_obj.hide_render = True      # the host's reference fragment would sit over the body
        try:
            report["labelled"] = render_labelled(
                recipe, recipe.out_path("captures", "scratch/captures"),
                recipe.output.get("preview_prefix", f"{recipe.name.lower()}_retarget"),
                {f.name: objects[f.name] for f in recipe.fragments if f.export},
                lines, legend, f"{recipe.name}: fragments and cut planes")
        finally:
            ref_obj.hide_render = False
    else:
        report["captures"] = []
    report["spec_fragments"] = [
        {"name": f.name, "template_fragment": f.template, "sockets": "all",
         **({"socket_overrides": overrides[f.name]} if f.name in overrides else {}),
         "bounds_override": report["bounds_overrides"].get(f.name)}
        for f in to_export
    ]
    return objects, to_export


def run_many(recipes: list[Recipe], *, measure_only: bool = False, scale: float | None = None,
             skip_render: bool = False, out: Path | None = None, report_paths: list[Path] | None = None,
             with_extras: bool = False, package_name: str | None = None, mesh_name: str | None = None) -> list[dict[str, Any]]:
    """Retarget one or more recipes on the SAME host into ONE package (a package group).

    Fragments are appended in recipe order, so the first recipe's index ranges are what they
    would be alone (the AK keeps its ranges when the Boxgun is appended after it). Each recipe
    gets its own report; the package and its sidecar are shared and named by the first
    recipe's ``output`` unless overridden.
    """
    import bpy

    started = time.time()
    if not recipes:
        raise ValueError("no recipes")
    first = recipes[0]
    hosts = {r.host["mesh_path"] for r in recipes}
    if len(hosts) != 1:
        raise RuntimeError(f"a package group needs one host gestalt; got {sorted(hosts)}")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    out = out or first.out_path("package", f"scratch/PipelineMeshes_{first.name.lower()}.upk")
    package_name = package_name or first.output.get("package_name", api.DEFAULT_PACKAGE_NAME)
    mesh_name = mesh_name or first.output.get("mesh_name", api.DEFAULT_MESH_NAME)
    report_paths = report_paths or [r.out_path("report", f"scratch/{r.name.lower()}_retarget_report.json") for r in recipes]

    imported, ref_obj, arm = import_reference(first)
    if not imported.ok:
        raise RuntimeError("the reference fragment import failed its own checks")
    heads = bone_heads(arm)
    host_info = {"reference_import": imported.to_dict(),
                 "host_bones": {k: [round(c, 4) for c in v] for k, v in heads.items()},
                 "host_sockets": {k: [round(c, 4) for c in v] for k, v in socket_worlds(arm).items()},
                 "reference_bounds": geo.bounds(_world_coords(ref_obj))}

    reports: list[dict[str, Any]] = []
    all_objects: list[Any] = []
    names: list[str] = []
    templates: list[str] = []
    for recipe in recipes:
        report: dict[str, Any] = {
            "recipe": str(recipe.path), "name": recipe.name, "blender": bpy.app.version_string,
            "python": sys.version.split()[0], "addon_version": list(api.ADDON_VERSION),
            "glb": str(recipe.resolve(recipe.source["glb"])), "out": str(out),
            "package_name": package_name, "mesh_path": recipe.host["mesh_path"], "frame": recipe.frame().get("note"),
            "group": [r.name for r in recipes] if len(recipes) > 1 else None, **host_info,
        }
        print(f"--- {recipe.name}", flush=True)
        objects, to_export = _process(recipe, ref_obj, arm, heads, scale=scale, measure_only=measure_only,
                                      skip_render=skip_render, with_extras=with_extras, report=report)
        for f in to_export:
            all_objects.append(objects[f.name])
            names.append(f.name)
            templates.append(f.template)
        reports.append(report)

    if not measure_only:
        exported = api.export_fragments(all_objects, out, package_name, mesh_name, names, templates)
        for c in exported.checks:
            print(f"   {c}", flush=True)
        for report in reports:
            report["export"] = exported.to_dict()
            report["ok"] = bool(exported.ok)
    for report, path in zip(reports, report_paths):
        report["elapsed_s"] = round(time.time() - started, 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"   -> {path}" + (" (measure only)" if measure_only else ""), flush=True)
    return reports


def run(recipe: Recipe, *, measure_only: bool = False, scale: float | None = None,
        skip_render: bool = False, out: Path | None = None, report_path: Path | None = None,
        with_extras: bool = False) -> dict[str, Any]:
    """One recipe, one package: :func:`run_many` with a single recipe."""
    return run_many([recipe], measure_only=measure_only, scale=scale, skip_render=skip_render, out=out,
                    report_paths=[report_path] if report_path else None, with_extras=with_extras)[0]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    p = argparse.ArgumentParser(prog="bl2_retarget.engine")
    p.add_argument("--recipe", action="append", required=True, help="repeat for a package group (first recipe keeps its ranges)")
    p.add_argument("--out", default=None)
    p.add_argument("--report", action="append", default=None, help="one per --recipe, in order")
    p.add_argument("--package-name", default=None)
    p.add_argument("--mesh-name", default=None)
    p.add_argument("--scale", type=float, default=None)
    p.add_argument("--measure-only", action="store_true")
    p.add_argument("--skip-render", action="store_true")
    p.add_argument("--with-extras", action="store_true", help="also export fragments marked export: false")
    return p.parse_args(argv)


def main() -> int:
    a = parse_args()
    recipes = [load_recipe(r) for r in a.recipe]
    print("=" * 72, flush=True)
    print(f"bl2_retarget: {', '.join(r.name for r in recipes)} -> {recipes[0].host['mesh_path']}", flush=True)
    print("=" * 72, flush=True)
    reports = run_many(recipes, measure_only=a.measure_only, scale=a.scale, skip_render=a.skip_render,
                       out=Path(a.out) if a.out else None,
                       report_paths=[Path(r) for r in a.report] if a.report else None,
                       with_extras=a.with_extras, package_name=a.package_name, mesh_name=a.mesh_name)
    ok = all(r.get("ok") for r in reports)
    print(f"   ok={ok} in {reports[-1].get('elapsed_s')}s", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
