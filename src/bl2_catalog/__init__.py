"""``bl2_catalog`` -- build ``catalog/parts.json``, the pipeline's source of truth.

See ``docs/LINT_PROPOSAL_SCHEMA.md`` for what the catalog contains and how the linter reads it.

Typical use::

    from bl2_catalog import build_catalog, load_catalog
    build_catalog()                 # writes catalog/parts.json
    catalog = load_catalog()

CLI: ``python -m bl2_catalog [--out catalog/parts.json] [--check]``.
"""

from __future__ import annotations

from .builder import (
    CATALOG_SCHEMA_VERSION,
    build_catalog,
    load_catalog,
    weapon_type_key,
)
from .dumps import DumpObject, ObjRef, iter_dump_objects, obj_ref, parse_value, unquote
from .sources import ExportIndex, Sources, file_digest

__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "DumpObject",
    "ExportIndex",
    "ObjRef",
    "Sources",
    "build_catalog",
    "file_digest",
    "iter_dump_objects",
    "load_catalog",
    "obj_ref",
    "parse_value",
    "unquote",
    "weapon_type_key",
]
