"""The N-panel: View3D > Sidebar > BL2 Part."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from . import api, ops

_ICON = {"ok": "CHECKMARK", "warn": "ERROR", "error": "CANCEL"}


class BL2PL_PT_panel(Panel):
    bl_label = "BL2 Part Pipeline"
    bl_idname = "BL2PL_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BL2 Part"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object

        column = layout.column(align=True)
        column.operator(ops.BL2PL_OT_import_fragment.bl_idname, icon="IMPORT")

        box = layout.box()
        if obj is not None and obj.get(api.P_FRAGMENT) is not None:
            box.label(text=f"Fragment: {obj[api.P_FRAGMENT]}", icon="MESH_DATA")
            box.label(text=f"Mesh: {obj.get(api.P_MESH_PATH, '?')}")
            box.label(text=f"Vertices: {len(obj.data.vertices)} "
                           f"(imported {obj.get(api.P_VERTEX_COUNT, '?')})")
            box.label(text=f"Triangles: {len(obj.data.polygons)}")
            box.label(text=api.TRANSFORM_NOTE, icon="ORIENTATION_GLOBAL")
        else:
            box.label(text="No imported fragment selected", icon="INFO")

        column = layout.column(align=True)
        column.operator(ops.BL2PL_OT_standard_deformation.bl_idname, icon="MOD_SIMPLEDEFORM")
        column.operator(ops.BL2PL_OT_triangulate.bl_idname, icon="MOD_TRIANGULATE")
        column.operator(ops.BL2PL_OT_apply_transforms.bl_idname, icon="CON_TRANSFORM")

        column = layout.column(align=True)
        column.operator(ops.BL2PL_OT_validate.bl_idname, icon="CHECKMARK")
        column.operator(ops.BL2PL_OT_export_fragment.bl_idname, icon="EXPORT")

        checks = ops.last_checks()
        if checks:
            box = layout.box()
            box.label(text=ops.last_summary())
            for check in checks:
                row = box.row()
                row.alert = check.level == "error"
                row.label(text=f"{check.name}: {check.message}"[:120],
                          icon=_ICON.get(check.level, "DOT"))


CLASSES = (BL2PL_PT_panel,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
