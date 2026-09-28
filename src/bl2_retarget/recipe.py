"""The retarget recipe: everything that was per-weapon in ``ak47_retarget.py`` /
``awp_retarget.py``, as data.

A recipe is a JSON file (``recipes/<weapon>.json``). ``load_recipe`` validates it into a
:class:`Recipe`; nothing here imports ``bpy``. The engine (:mod:`bl2_retarget.engine`)
interprets it inside Blender; the geometry rules it references are evaluated by
:mod:`bl2_retarget.geometry`, which is plain Python and unit-tested.

Shape (every key with its meaning; ``recipes/awp.json`` is the worked example)::

    {
      "name": "AWP",                          # label; names the collection and the source object
      "source": {
        "glb": "C:/.../awp/model.glb",        # never copied into the repo
        "body_match": "awp",                  # the body mesh: name contains this ...
        "body_exclude": "arms",               # ... and not this; else the biggest mesh
        "attachments": ["muzzle", "eject"],   # glTF empties to measure (bone-tail offset removed)
        "frame": {"preset": "cs2_gltf"}       # or {"matrix": [[...4x4...]]}: import frame -> add-on frame
      },
      "host": {
        "mesh_path": "Weap_SniperRifles.GestaltDef_SniperRifle_GestaltSkeletalMesh",
        "fragment_table": "scratch/gestalt_SR_fragments.txt",   # null = the add-on's AR default
        "reference_fragment": "SR_Body_Dahl",                   # imported for its armature + sockets
        "muzzle_socket": "SR_Body_Dahl_Muzzle",                 # for the placement report
        "muzzle_socket_default": [0.0, -131.912, 9.75]          # if that socket is absent
      },
      "placement": {"anchor_group": "trigger", "anchor_bone": "Trigger", "scale": 1.0},
      "bones": {"map": {"trigger": "Trigger"}, "static": "Root"},
      "checks": [ ... see geometry.frame_checks ... ],
      "rule_order": ["AWP_Barrel", "AWP_Scope", "AWP_Stock", "AWP_Grip"],   # optional: order the
                                              # face rules are tried in; default = fragments order
      "fragments": [                          # EXPORT order (= index ranges in the package)
        {"name": "AWP_Barrel", "template": "SR_Barrel_Jakobs", "rule": {"front_y_lt": -35.0}, "bone": "Barrel"},
        {"name": "AWP_Body",   "template": "SR_Body_Dahl",     "rule": "remainder", "bone": null},
        {"name": "AWP_Mag",    "template": "SR_Body_Dahl",     "subset_of": "AWP_Body",
                               "majority_group": "clip", "bone": "Clip", "export": false}
      ],
      "sockets": [                            # resolved in order; later ones may use earlier ones
        {"socket": "Muzzle", "fragment": "AWP_Barrel", "point": {"ring": "front", "of": "AWP_Barrel", "tol": 0.8,
                                                                  "xz_from_attachment": "muzzle"}}
      ],
      "output": {"package_name": "PipelineMeshesAWP", "mesh_name": "PL_SR_Gestalt_Mesh",
                 "package": "scratch/PipelineMeshesAWP.upk", "report": "scratch/awp_retarget_report.json",
                 "captures": "scratch/captures", "preview_prefix": "awp_retarget"}
    }

Paths are relative to the repo root unless absolute.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]

FRAME_PRESETS: dict[str, dict[str, Any]] = {
    # glTF-import frame (Blender, after the importer's Y-up -> Z-up) -> add-on Blender frame.
    # CS2 via the surf-central normalizer: metres, muzzle -Z_CS2, up +Y_CS2. The derivation in
    # the CS2 export convention lands on a 180 degree turn about Z times 100 (m -> cm).
    "cs2_gltf": {"diagonal": [-1.0, -1.0, 1.0], "units_to_unreal": 100.0,
                 "note": "180 deg about Z, x100 (CS2 (x,y,z) -> UE (x,z,y) -> Blender (-x,y,z))"},
    # a glb already in the add-on frame at 1 unit = 1 UE unit
    "identity": {"diagonal": [1.0, 1.0, 1.0], "units_to_unreal": 1.0, "note": "identity"},
}

RULE_KEYS = {"front_y_lt", "rear_y_gt", "cy_gt", "cy_lt", "cz_gt", "cz_lt", "cx_gt", "cx_lt",
             "cy_between", "cz_between", "majority_group"}
POINT_KEYS = {"ring", "extreme", "attachment", "bone_centroid", "sight_line", "literal", "resolved", "composite"}


class RecipeError(ValueError):
    pass


@dataclass
class Fragment:
    name: str
    template: str
    rule: dict[str, Any] | str = "remainder"
    bone: str | None = None            # force every vertex onto this bone; None = per-vertex from the map
    subset_of: str | None = None       # a duplicate carved from another fragment's faces
    majority_group: str | None = None  # ... where most vertices are in this source group
    export: bool = True


@dataclass
class SocketRule:
    socket: str
    fragment: str
    point: dict[str, Any]
    fallback: dict[str, Any] | None = None


@dataclass
class Recipe:
    name: str
    source: dict[str, Any]
    host: dict[str, Any]
    placement: dict[str, Any]
    bones: dict[str, Any]
    checks: list[dict[str, Any]]
    fragments: list[Fragment]
    sockets: list[SocketRule]
    output: dict[str, Any]
    rule_order: list[str] = field(default_factory=list)
    path: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ derived
    @property
    def fragment_names(self) -> list[str]:
        return [f.name for f in self.fragments]

    @property
    def rule_fragments(self) -> list[Fragment]:
        """Fragments in the order their face rules are tried (``rule_order`` first)."""
        by = {f.name: f for f in self.fragments}
        ordered = [by[n] for n in self.rule_order]
        return ordered + [f for f in self.fragments if f.name not in self.rule_order]

    @property
    def exported(self) -> list[Fragment]:
        return [f for f in self.fragments if f.export]

    @property
    def bone_map(self) -> dict[str, str]:
        return dict(self.bones.get("map") or {})

    @property
    def static_bone(self) -> str:
        return str(self.bones.get("static") or "Root")

    def frame(self) -> dict[str, Any]:
        f = self.source.get("frame") or {"preset": "cs2_gltf"}
        if "preset" in f:
            if f["preset"] not in FRAME_PRESETS:
                raise RecipeError(f"unknown frame preset {f['preset']!r}; known: {sorted(FRAME_PRESETS)}")
            return dict(FRAME_PRESETS[f["preset"]])
        if "matrix" in f:
            return {"matrix": f["matrix"], "note": f.get("note", "explicit matrix")}
        raise RecipeError("source.frame needs a preset or a matrix")

    def resolve(self, value: str | None) -> Path | None:
        if value is None:
            return None
        p = Path(str(value))
        return p if p.is_absolute() else (REPO / p)

    def out_path(self, key: str, default: str) -> Path:
        return self.resolve(self.output.get(key) or default)  # type: ignore[return-value]

    def to_dict(self) -> dict[str, Any]:
        return self.raw


def _fragment(d: dict[str, Any]) -> Fragment:
    if "name" not in d or "template" not in d:
        raise RecipeError(f"fragment needs name and template: {d}")
    rule = d.get("rule", "remainder")
    if isinstance(rule, dict):
        bad = set(rule) - RULE_KEYS
        if bad:
            raise RecipeError(f"fragment {d['name']}: unknown rule keys {sorted(bad)}; known {sorted(RULE_KEYS)}")
    elif rule != "remainder":
        raise RecipeError(f"fragment {d['name']}: rule must be a dict or \"remainder\"")
    return Fragment(name=str(d["name"]), template=str(d["template"]), rule=rule, bone=d.get("bone"),
                    subset_of=d.get("subset_of"), majority_group=d.get("majority_group"),
                    export=bool(d.get("export", True)))


def _socket(d: dict[str, Any]) -> SocketRule:
    for k in ("socket", "fragment", "point"):
        if k not in d:
            raise RecipeError(f"socket rule needs {k}: {d}")
    for p in (d["point"], d.get("fallback")):
        if p is not None and not (set(p) & POINT_KEYS):
            raise RecipeError(f"socket {d['socket']}: point needs one of {sorted(POINT_KEYS)}: {p}")
    return SocketRule(socket=str(d["socket"]), fragment=str(d["fragment"]), point=dict(d["point"]),
                      fallback=dict(d["fallback"]) if d.get("fallback") else None)


def load_recipe(path: str | Path) -> Recipe:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return recipe_from_dict(data, path)


def recipe_from_dict(data: dict[str, Any], path: Path | None = None) -> Recipe:
    for k in ("name", "source", "host", "fragments", "output"):
        if k not in data:
            raise RecipeError(f"recipe is missing {k!r}")
    if not data["source"].get("glb"):
        raise RecipeError("source.glb is required")
    if not data["host"].get("mesh_path") or not data["host"].get("reference_fragment"):
        raise RecipeError("host.mesh_path and host.reference_fragment are required")
    fragments = [_fragment(f) for f in data["fragments"]]
    names = [f.name for f in fragments]
    if len(set(names)) != len(names):
        raise RecipeError(f"duplicate fragment names: {names}")
    remainders = [f for f in fragments if f.rule == "remainder" and not f.subset_of]
    if len(remainders) != 1:
        raise RecipeError("exactly one fragment must have rule \"remainder\" (the body)")
    for f in fragments:
        if f.subset_of and f.subset_of not in names:
            raise RecipeError(f"fragment {f.name}: subset_of {f.subset_of!r} is not a fragment")
        if f.subset_of and not f.majority_group:
            raise RecipeError(f"fragment {f.name}: subset_of needs majority_group")
    rule_order = [str(n) for n in data.get("rule_order") or []]
    for n in rule_order:
        if n not in names:
            raise RecipeError(f"rule_order names {n!r}, which is not a fragment")
    sockets = [_socket(s) for s in data.get("sockets") or []]
    for s in sockets:
        if s.fragment not in names:
            raise RecipeError(f"socket {s.socket}: fragment {s.fragment!r} is not a fragment")
    r = Recipe(name=str(data["name"]), source=dict(data["source"]), host=dict(data["host"]),
               placement=dict(data.get("placement") or {"anchor_group": "trigger", "anchor_bone": "Trigger", "scale": 1.0}),
               bones=dict(data.get("bones") or {"map": {}, "static": "Root"}),
               checks=list(data.get("checks") or []), fragments=fragments, rule_order=rule_order, sockets=sockets,
               output=dict(data["output"]), path=path, raw=data)
    r.frame()  # validates
    return r
