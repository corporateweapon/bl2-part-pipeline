"""The lint proposal: what a pipeline run is about to build and register.

Full documentation of every field is in ``docs/LINT_PROPOSAL_SCHEMA.md``; this
module is the machine-readable half of it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "FragmentProposal",
    "MAX_GPU_VERTICES",
    "PART_ONLY_EXEMPT",
    "PROPOSAL_SCHEMA_VERSION",
    "Proposal",
    "REQUIRED_FIELDS",
    "load_proposal",
]

PROPOSAL_SCHEMA_VERSION = 1

#: The gestalt LOD index buffer is uint16 (``bl2_upk.fragment.MAX_GPU_VERTICES``);
#: duplicated here so ``bl2_lint`` stays importable without ``bl2_upk``.
MAX_GPU_VERTICES = 65535

#: fields without which the linter cannot do its job
REQUIRED_FIELDS = (
    "package",
    "mesh_path",
    "weapon_type",
    "fragment",
    "first_index",
    "num_primitives",
    "template_fragment",
    "part_path",
    "register_in_lists",
)

#: not required of a ``part_only`` proposal: it adds no fragment
PART_ONLY_EXEMPT = ("fragment", "first_index", "num_primitives", "template_fragment")


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_strlist(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


@dataclass
class FragmentProposal:
    """One fragment of a multi-fragment proposal (the ``fragments: [...]`` array).

    The same facts the single-fragment form carries at the top level, plus
    ``new_vertices`` (how many vertices the fragment adds to the mesh), which is
    what L9 budgets against the uint16 index buffer.
    """

    fragment: str = ""
    template_fragment: str = ""
    first_index: int | None = None
    num_primitives: int | None = None
    sockets: list[str] = field(default_factory=list)
    new_vertices: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FragmentProposal:
        return cls(
            fragment=str(data.get("fragment", data.get("name", "")) or ""),
            template_fragment=str(data.get("template_fragment", "") or ""),
            first_index=_as_int(data.get("first_index")),
            num_primitives=_as_int(data.get("num_primitives")),
            sockets=_as_strlist(data.get("sockets")),
            new_vertices=_as_int(data.get("new_vertices")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fragment": self.fragment,
            "template_fragment": self.template_fragment,
            "first_index": self.first_index,
            "num_primitives": self.num_primitives,
            "sockets": list(self.sockets),
            "new_vertices": self.new_vertices,
        }


@dataclass
class Proposal:
    """A proposed new gestalt fragment + weapon part.

    Extra keys in the source JSON (the M2 sidecar carries ``dz``, ``out``,
    ``reparsed_ok``) are preserved in :attr:`extra` and ignored by the checks.
    """

    package: str = ""
    mesh_path: str = ""
    weapon_type: str = ""
    fragment: str = ""
    first_index: int | None = None
    num_primitives: int | None = None
    template_fragment: str = ""
    part_path: str = ""
    part_slot: str | None = None
    sockets: list[str] = field(default_factory=list)
    register_in_lists: list[str] = field(default_factory=list)
    register_at: str | None = None
    keep_alive: bool | None = None
    objects: list[str] = field(default_factory=list)
    #: several fragments in one package (the M6 multi-fragment sidecar); empty
    #: for the single-fragment form, where the top-level fields are the fragment
    fragments: list[FragmentProposal] = field(default_factory=list)
    #: the part adds **no** fragment: it is a clone of a non-gestalt part
    #: (``AR_Sight_None``) that draws nothing.  The fragment fields are then not
    #: required and the fragment checks (L2, L4, L5) have nothing to look at.
    part_only: bool = False
    #: part lists the mod constructs at runtime (a ``WeaponBalanceDefinition`` of its
    #: own): ``<balance path>:<collection>.<SlotField>``.  L6 accepts them in place of
    #: catalog lists, since the catalog cannot know a list that does not exist yet.
    register_in_runtime_lists: list[str] = field(default_factory=list)
    #: runtime balances the mod constructs: ``{path, template, title?, title_template?,
    #: pools?}``.  L10 checks the path is new and the template exists.
    new_balances: list[dict[str, Any]] = field(default_factory=list)
    #: runtime MaterialInstanceConstants: ``{path, parent, textures?}`` (L10 notes them;
    #: the catalog has no materials or textures to check against)
    new_materials: list[dict[str, Any]] = field(default_factory=list)
    #: further packages the mod loads: L1 checks each name like the main package
    extra_packages: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Proposal:
        known = {
            "package", "mesh_path", "weapon_type", "fragment", "first_index",
            "num_primitives", "template_fragment", "part_path", "part_slot",
            "sockets", "register_in_lists", "register_at", "keep_alive", "objects",
            "fragments", "part_only", "register_in_runtime_lists", "new_balances",
            "new_materials", "extra_packages",
        }
        _int = _as_int
        _strlist = _as_strlist

        raw_fragments = data.get("fragments") or []
        if isinstance(raw_fragments, dict):
            raw_fragments = [raw_fragments]
        fragments = [
            FragmentProposal.from_dict(entry)
            for entry in raw_fragments
            if isinstance(entry, dict)
        ]
        # the multi-fragment sidecar repeats the first fragment at the top level;
        # tolerate one that does not, so every check still has something to say
        head = fragments[0] if fragments else None
        return cls(
            package=str(data.get("package", "") or ""),
            mesh_path=str(data.get("mesh_path", "") or ""),
            weapon_type=str(data.get("weapon_type", "") or ""),
            fragment=str(data.get("fragment", "") or (head.fragment if head else "")),
            first_index=(
                _int(data.get("first_index"))
                if data.get("first_index") is not None
                else (head.first_index if head else None)
            ),
            num_primitives=(
                _int(data.get("num_primitives"))
                if data.get("num_primitives") is not None
                else (head.num_primitives if head else None)
            ),
            template_fragment=str(
                data.get("template_fragment", "") or (head.template_fragment if head else "")
            ),
            part_path=str(data.get("part_path", "") or ""),
            part_slot=(str(data["part_slot"]) if data.get("part_slot") else None),
            sockets=_strlist(data.get("sockets")) or (list(head.sockets) if head else []),
            register_in_lists=_strlist(data.get("register_in_lists")),
            register_at=(str(data["register_at"]) if data.get("register_at") else None),
            keep_alive=data.get("keep_alive"),
            objects=_strlist(data.get("objects")),
            fragments=fragments,
            part_only=bool(data.get("part_only", False)),
            register_in_runtime_lists=_strlist(data.get("register_in_runtime_lists")),
            new_balances=[
                dict(b) for b in (data.get("new_balances") or []) if isinstance(b, dict)
            ],
            new_materials=[
                dict(b) for b in (data.get("new_materials") or []) if isinstance(b, dict)
            ],
            extra_packages=_strlist(data.get("extra_packages")),
            extra={k: v for k, v in data.items() if k not in known},
        )

    def fragment_entries(self) -> list[FragmentProposal]:
        """Every fragment the proposal adds, single- and multi-fragment alike.

        Empty for a part-only proposal: there is no fragment to check.
        """
        if self.part_only:
            return []
        if self.fragments:
            return list(self.fragments)
        return [
            FragmentProposal(
                fragment=self.fragment,
                template_fragment=self.template_fragment,
                first_index=self.first_index,
                num_primitives=self.num_primitives,
                sockets=list(self.sockets),
                new_vertices=_as_int(self.extra.get("new_vertices")),
            )
        ]

    @property
    def total_vertices(self) -> int | None:
        """Mesh vertex count *after* the build, when the sidecar recorded it."""
        return _as_int(self.extra.get("total_vertices"))

    @property
    def base_vertex_count(self) -> int | None:
        """Vertex count of the mesh the fragments are appended to, if stated."""
        for key in ("base_vertex_count", "source_vertex_count", "stock_vertex_count"):
            value = _as_int(self.extra.get(key))
            if value is not None:
                return value
        return None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "package": self.package,
            "mesh_path": self.mesh_path,
            "weapon_type": self.weapon_type,
            "fragment": self.fragment,
            "first_index": self.first_index,
            "num_primitives": self.num_primitives,
            "template_fragment": self.template_fragment,
            "part_path": self.part_path,
            "part_slot": self.part_slot,
            "sockets": list(self.sockets),
            "register_in_lists": list(self.register_in_lists),
            "register_at": self.register_at,
            "keep_alive": self.keep_alive,
            "objects": list(self.objects),
            "fragments": [f.to_dict() for f in self.fragments],
        }
        # only stated when set, so a report of any earlier proposal reads as it always did
        if self.part_only:
            out["part_only"] = True
        if self.register_in_runtime_lists:
            out["register_in_runtime_lists"] = list(self.register_in_runtime_lists)
        if self.new_balances:
            out["new_balances"] = [dict(b) for b in self.new_balances]
        if self.new_materials:
            out["new_materials"] = [dict(b) for b in self.new_materials]
        if self.extra_packages:
            out["extra_packages"] = list(self.extra_packages)
        return out

    def missing_fields(self) -> list[str]:
        """Required fields that are empty / absent.

        A part-only proposal (:attr:`part_only`) adds no fragment, so the fragment
        fields are not required of it.
        """
        values = self.to_dict()
        required = REQUIRED_FIELDS
        if self.part_only:
            required = tuple(n for n in required if n not in PART_ONLY_EXEMPT)
        if self.register_in_runtime_lists:
            # a runtime list stands in for a catalog list (L6 notes it)
            required = tuple(n for n in required if n != "register_in_lists")
        return [
            name
            for name in required
            if values.get(name) in (None, "", [], {})
        ]

    @property
    def package_stem(self) -> str:
        """``PipelineMeshes.upk`` and ``PipelineMeshes`` both -> ``PipelineMeshes``."""
        stem = self.package
        if stem.lower().endswith(".upk"):
            stem = stem[:-4]
        return stem


def load_proposal(path: Path | str) -> Proposal:
    """Read a proposal JSON file."""
    with Path(path).open("r", encoding="utf-8") as handle:
        return Proposal.from_dict(json.load(handle))
