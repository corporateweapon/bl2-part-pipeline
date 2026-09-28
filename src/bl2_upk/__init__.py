"""Binary codec for Borderlands 2 (UE3 ArVer 832 / LicenseeVer 46) packages.

Public surface:

* :class:`Package` -- name/import/export tables of a decompressed ``.upk``.
* :func:`parse_properties` / :func:`serialize_properties` -- byte-exact tagged
  property lists.
* :class:`SkeletalMeshExport` -- full decode/encode of a ``SkeletalMesh`` export.
* :func:`replace_export_payload` -- write a modified payload back into a package.
* :class:`PackageBuilder` / :func:`clone_skeletal_mesh` -- emit a brand-new
  cooked package containing a copy of a mesh under new names.
* :mod:`bl2_upk.fragment` -- gestalt fragment table, fragment append and the
  one sanctioned write path :func:`build_fragment_package` (and its
  several-fragments-at-once form :func:`build_multi_fragment_package`).
"""

from __future__ import annotations

from .fragment import (
    DEFAULT_FRAGMENT_TABLE,
    DEFAULT_SOURCE_PACKAGE,
    GESTALT_AR_MESH,
    MAX_GPU_VERTICES,
    FragmentRange,
    FragmentSpec,
    NewVertex,
    append_fragment,
    append_new_geometry,
    build_fragment_package,
    build_multi_fragment_package,
    fragment_vertices,
    load_fragment_rows,
    load_fragments,
    pack_normal,
    standard_deformation_edit,
    unpack_normal,
)
from .patch import PatchError, replace_export_payload
from .props import PropertyTag, find_property, parse_properties, serialize_properties
from .reader import (
    EXPORT_ENTRY_FIXED_SIZE,
    EXPORT_SERIAL_OFFSET_OFFSET,
    EXPORT_SERIAL_SIZE_OFFSET,
    ExportEntry,
    FName,
    ImportEntry,
    Package,
    PackageError,
    PackageSummary,
)
from .skelmesh import (
    BulkData,
    Chunk,
    GpuVertex,
    LodModel,
    MeshBone,
    Section,
    SkelMeshError,
    SkeletalMeshExport,
    float_to_half,
    half_to_float,
)
from .writer import (
    CloneResult,
    PackageBuilder,
    PayloadSource,
    WriterError,
    clone_skeletal_mesh,
    qualified_path,
)

__all__ = [
    "DEFAULT_FRAGMENT_TABLE",
    "DEFAULT_SOURCE_PACKAGE",
    "EXPORT_ENTRY_FIXED_SIZE",
    "EXPORT_SERIAL_OFFSET_OFFSET",
    "EXPORT_SERIAL_SIZE_OFFSET",
    "BulkData",
    "CloneResult",
    "Chunk",
    "ExportEntry",
    "FName",
    "FragmentRange",
    "FragmentSpec",
    "GESTALT_AR_MESH",
    "MAX_GPU_VERTICES",
    "NewVertex",
    "GpuVertex",
    "ImportEntry",
    "LodModel",
    "MeshBone",
    "Package",
    "PackageBuilder",
    "PackageError",
    "PackageSummary",
    "PatchError",
    "PayloadSource",
    "PropertyTag",
    "Section",
    "SkelMeshError",
    "SkeletalMeshExport",
    "WriterError",
    "append_fragment",
    "append_new_geometry",
    "build_fragment_package",
    "build_multi_fragment_package",
    "clone_skeletal_mesh",
    "find_property",
    "float_to_half",
    "fragment_vertices",
    "half_to_float",
    "load_fragment_rows",
    "load_fragments",
    "pack_normal",
    "parse_properties",
    "qualified_path",
    "replace_export_payload",
    "serialize_properties",
    "standard_deformation_edit",
    "unpack_normal",
]
