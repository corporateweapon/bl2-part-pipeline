"""Blender operators -- thin wrappers over :mod:`bl2_blender.api`."""

from __future__ import annotations

import traceback

import bpy
from bpy.props import BoolProperty, FloatProperty, StringProperty
from bpy.types import Operator

from . import api

_LAST: dict[str, object] = {"checks": [], "summary": ""}


def last_checks() -> list[api.Check]:
    return list(_LAST["checks"])  # type: ignore[arg-type]


def last_summary() -> str:
    return str(_LAST["summary"])


def _remember(checks, summary: str) -> None:
    _LAST["checks"] = list(checks)
    _LAST["summary"] = summary


def _report_checks(op: Operator, checks) -> None:
    for check in checks:
        level = {"error": "ERROR", "warn": "WARNING"}.get(check.level, "INFO")
        op.report({level}, str(check))


def _fail(op: Operator, exc: Exception) -> set:
    traceback.print_exc()
    op.report({"ERROR"}, f"{type(exc).__name__}: {exc}")
    return {"CANCELLED"}


class BL2PL_OT_import_fragment(Operator):
    """Import one gestalt fragment (mesh + armature + sockets) from a package"""

    bl_idname = "bl2pl.import_fragment"
    bl_label = "Import Fragment"
    bl_options = {"REGISTER", "UNDO"}

    package_path: StringProperty(
        name="Package", subtype="FILE_PATH", default="",
        description="Decompressed .upk holding the gestalt SkeletalMesh")
    mesh_path: StringProperty(
        name="Mesh", default=api.DEFAULT_MESH_PATH,
        description="Export path of the gestalt SkeletalMesh inside that package")
    fragment: StringProperty(
        name="Fragment", default=api.DEFAULT_FRAGMENT,
        description="Fragment (part) name from the gestalt definition's Parts table")
    fragments_table: StringProperty(
        name="Fragment table", subtype="FILE_PATH", default="")

    def invoke(self, context, event):
        # resolved here, not at class definition time, so the repo-root
        # preference / $BL2_PIPELINE_ROOT is honoured by an installed zip
        self.package_path = self.package_path or str(api.default_package())
        self.fragments_table = self.fragments_table or str(api.default_fragments_table())
        return context.window_manager.invoke_props_dialog(self, width=520)

    def execute(self, context):
        try:
            result = api.import_fragment(
                self.package_path or None, self.mesh_path, self.fragment,
                self.fragments_table or None)
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            return _fail(self, exc)
        summary = (f"{result.fragment}: {result.vertex_count} verts / "
                   f"{result.triangle_count} tris / {result.bone_count} bones / "
                   f"{len(result.socket_names)} sockets")
        _remember(result.checks, summary)
        _report_checks(self, result.checks)
        self.report({"ERROR"} if not result.ok else {"INFO"}, summary)
        return {"FINISHED"} if result.ok else {"CANCELLED"}


class BL2PL_OT_validate(Operator):
    """Run the export guardrails on the active fragment"""

    bl_idname = "bl2pl.validate"
    bl_label = "Validate"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == "MESH"

    def execute(self, context):
        try:
            checks = api.validate_for_export(context.active_object)
        except Exception as exc:  # noqa: BLE001
            return _fail(self, exc)
        bad = sum(1 for c in checks if c.level == "error")
        warn = sum(1 for c in checks if c.level == "warn")
        summary = f"{len(checks)} checks, {bad} failed, {warn} warnings"
        _remember(checks, summary)
        _report_checks(self, checks)
        self.report({"ERROR"} if bad else {"INFO"}, summary)
        return {"FINISHED"}


class BL2PL_OT_standard_deformation(Operator):
    """Apply the D7 standard deformation (bent, flared muzzle)"""

    bl_idname = "bl2pl.standard_deformation"
    bl_label = "Apply Standard Deformation"
    bl_options = {"REGISTER", "UNDO"}

    y_cut: FloatProperty(name="Y cut", default=-80.0,
                         description="Vertices with Y below this move (the muzzle half)")
    dz: FloatProperty(name="Z offset", default=8.0)
    x_scale: FloatProperty(name="X scale", default=1.5)

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == "MESH"

    def execute(self, context):
        try:
            moved = api.standard_deformation(
                context.active_object, self.y_cut, self.dz, self.x_scale)
        except Exception as exc:  # noqa: BLE001
            return _fail(self, exc)
        summary = f"moved {moved} vertices (y < {self.y_cut}, +{self.dz} Z, x x{self.x_scale})"
        _remember([api.Check("standard deformation", "ok", summary)], summary)
        self.report({"INFO"}, summary)
        return {"FINISHED"}


class BL2PL_OT_triangulate(Operator):
    """Triangulate the fragment as geometry (never as an export option -- F3)"""

    bl_idname = "bl2pl.triangulate"
    bl_label = "Triangulate Geometry"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == "MESH"

    def execute(self, context):
        try:
            split = api.ensure_triangulated(context.active_object)
        except Exception as exc:  # noqa: BLE001
            return _fail(self, exc)
        self.report({"INFO"}, f"triangulated {split} faces")
        return {"FINISHED"}


class BL2PL_OT_apply_transforms(Operator):
    """Bake the object transform into the mesh"""

    bl_idname = "bl2pl.apply_transforms"
    bl_label = "Apply Transform"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == "MESH"

    def execute(self, context):
        try:
            done = api.apply_transforms(context.active_object)
        except Exception as exc:  # noqa: BLE001
            return _fail(self, exc)
        self.report({"INFO"}, "transform applied" if done else "transform was already identity")
        return {"FINISHED"}


class BL2PL_OT_export_fragment(Operator):
    """Write a NEW package with this fragment appended under a NEW name"""

    bl_idname = "bl2pl.export_fragment"
    bl_label = "Export Fragment"
    bl_options = {"REGISTER"}

    out_path: StringProperty(name="Output .upk", subtype="FILE_PATH", default="")
    package_name: StringProperty(name="Package name", default=api.DEFAULT_PACKAGE_NAME)
    mesh_name: StringProperty(name="Mesh name", default=api.DEFAULT_MESH_NAME)
    fragment_name: StringProperty(
        name="New fragment name", default="",
        description="Must be unique: reusing an existing name is the silent "
                    "load-order failure (F1)")
    part_name: StringProperty(
        name="Part name", default="",
        description="WeaponPartDefinition name for the SDK mod (defaults to the fragment name)")
    auto_dz: BoolProperty(
        name="Measure socket Z offset", default=True,
        description="Derive the sidecar's dz from the edit instead of typing it")
    socket_dz: FloatProperty(name="Socket Z offset", default=0.0)
    force: BoolProperty(name="Ignore failed checks", default=False)

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == "MESH"

    def invoke(self, context, event):
        obj = context.active_object
        self.out_path = self.out_path or str(api.default_out_package())
        if not self.fragment_name:
            template = str(obj.get(api.P_FRAGMENT, api.DEFAULT_FRAGMENT))
            self.fragment_name = api.suggest_fragment_name(
                template, obj.get(api.P_TABLE) or None)
        return context.window_manager.invoke_props_dialog(self, width=520)

    def execute(self, context):
        try:
            result = api.export_fragment(
                context.active_object,
                self.out_path,
                self.package_name,
                self.mesh_name,
                self.fragment_name,
                None if self.auto_dz else self.socket_dz,
                part_name=self.part_name or None,
                force=self.force,
            )
        except api.ExportBlocked as exc:
            _remember(exc.checks, "export blocked")
            _report_checks(self, exc.checks)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:  # noqa: BLE001
            return _fail(self, exc)
        summary = (f"{result.fragment} -> {result.out_package} "
                   f"(first_index {result.first_index}, {result.num_primitives} tris, "
                   f"dz {result.dz})")
        _remember(result.checks, summary)
        _report_checks(self, result.checks)
        self.report({"INFO"}, summary)
        return {"FINISHED"}


CLASSES = (
    BL2PL_OT_import_fragment,
    BL2PL_OT_validate,
    BL2PL_OT_standard_deformation,
    BL2PL_OT_triangulate,
    BL2PL_OT_apply_transforms,
    BL2PL_OT_export_fragment,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
