"""Build the pipeline's own example weapon, the **Boxgun**, and export it as a glb.

    "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background -noaudio \\
        --python examples/boxgun/make_boxgun.py -- --out examples/boxgun/model.glb

A rifle made of primitives: receiver, barrel with a front-sight post, rear-sight block,
magazine, pistol grip, stock, trigger, charging handle. It exists so the pipeline has a
source model that is **ours** -- CC0, committed, redistributable -- for the worked example,
the tests and a release, since the CS2 models the AK-47 and AWP were built from can never
ship (``docs/RELEASE_PLAN_NEXUS.md``).

Authored directly in the add-on's Blender frame at 1 unit = 1 UE unit: muzzle towards -Y,
up +Z, the gestalt Root at the origin, the trigger a couple of units behind it (the AR
``Trigger`` bone rests at (0, 1.87, 1.30)). The glTF round trip (Z-up -> Y-up -> Z-up) is the
identity, so the recipe uses the ``identity`` frame preset. A four-bone armature (root,
trigger, clip, bolt) carries the vertex groups through the glb as skin weights, which is how
the importer gives them back as vertex groups -- exactly what the CS2 models provide.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(Path(__file__).resolve().parent / "model.glb"))
    return p.parse_args(argv)


def box(name: str, lo, hi, collection):
    import bpy

    bpy.ops.mesh.primitive_cube_add(size=1.0)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = tuple((hi[i] - lo[i]) for i in range(3))
    obj.location = tuple((lo[i] + hi[i]) / 2 for i in range(3))
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    collection.objects.link(obj)
    return obj


def cylinder(name: str, radius: float, y0: float, y1: float, z: float, collection, x: float = 0.0):
    import bpy
    from math import radians

    bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=radius, depth=abs(y1 - y0))
    obj = bpy.context.active_object
    obj.name = name
    obj.rotation_euler = (radians(90.0), 0.0, 0.0)
    obj.location = (x, (y0 + y1) / 2, z)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    collection.objects.link(obj)
    return obj


def main() -> int:
    import bmesh
    import bpy

    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    col = bpy.data.collections.new("Boxgun")
    scene.collection.children.link(col)

    # parts, each tagged with the vertex group it will carry (None = static)
    parts = [
        ("receiver", box("receiver", (-2.0, -20.0, 0.0), (2.0, 10.0, 8.0), col), None),
        ("barrel", cylinder("barrel", 1.2, -60.0, -20.0, 5.0, col), None),
        ("front_sight", box("front_sight", (-0.5, -56.0, 6.0), (0.5, -54.0, 11.5), col), None),
        ("rear_sight", box("rear_sight", (-1.0, -9.0, 8.0), (1.0, -6.0, 10.0), col), None),
        ("magazine", box("magazine", (-1.5, -12.0, -12.0), (1.5, -4.0, 0.5), col), "clip"),
        ("grip", box("grip", (-1.2, 5.0, -9.0), (1.2, 9.5, -0.5), col), None),
        ("stock", box("stock", (-1.6, 10.0, 0.5), (1.6, 36.0, 7.0), col), None),
        ("trigger", box("trigger", (-0.3, 1.2, -1.8), (0.3, 2.6, -0.2), col), "trigger"),
        ("bolt", box("bolt", (-4.0, -8.0, 5.5), (-2.0, -3.0, 7.0), col), "bolt"),
    ]

    # join into one mesh, remembering which vertices belong to which group
    for _, obj, _ in parts:
        obj.select_set(True)
    body = parts[0][1]
    bpy.context.view_layer.objects.active = body
    group_of_vertex: list[str | None] = []
    for _, obj, group in parts:
        group_of_vertex.extend([group] * len(obj.data.vertices))
    bpy.ops.object.join()
    body.name = "boxgun_body"
    body.data.name = "boxgun_body"
    assert len(body.data.vertices) == len(group_of_vertex)

    # armature: root + one bone per group, so the glb carries the groups as skin weights
    arm_data = bpy.data.armatures.new("boxgun_rig")
    arm = bpy.data.objects.new("boxgun_rig", arm_data)
    col.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    heads = {"root": (0.0, 0.0, 0.0), "trigger": (0.0, 1.9, -1.0), "clip": (0.0, -8.0, -2.0), "bolt": (-3.0, -5.5, 6.2)}
    for name, head in heads.items():
        b = arm_data.edit_bones.new(name)
        b.head = head
        b.tail = (head[0], head[1] + 2.0, head[2])
        if name != "root":
            b.parent = arm_data.edit_bones["root"]
    bpy.ops.object.mode_set(mode="OBJECT")

    for name in heads:
        body.vertex_groups.new(name=name)
    for index, group in enumerate(group_of_vertex):
        body.vertex_groups[group or "root"].add([index], 1.0, "REPLACE")
    body.parent = arm
    mod = body.modifiers.new("Armature", "ARMATURE")
    mod.object = arm

    # a UV map so the export is a real mesh (smart project, one island per face group)
    bpy.context.view_layer.objects.active = body
    body.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")

    # an attachment empty at the muzzle, bone-parented like the CS2 ones
    muzzle = bpy.data.objects.new("muzzle", None)
    col.objects.link(muzzle)
    muzzle.parent = arm
    muzzle.parent_type = "BONE"
    muzzle.parent_bone = "root"
    muzzle.matrix_world = bpy.data.objects["boxgun_rig"].matrix_world.copy()
    muzzle.matrix_world.translation = (0.0, -60.0, 5.0)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(filepath=str(out), export_format="GLB", use_selection=True,
                              export_skins=True, export_animations=False, export_apply=True,
                              export_yup=True)
    print(f"boxgun: {len(body.data.vertices)} vertices, {len(body.data.polygons)} faces -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
