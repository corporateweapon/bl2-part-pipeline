"""The partgen *spec*: what mod to generate.

A spec is the emitter's input. It is a superset of one or more lint proposals
(``docs/LINT_PROPOSAL_SCHEMA.md``): the same mesh/fragment/part facts, plus the
mod's identity and the runtime options the generated code bakes in.

Full documentation of every field is in ``docs/PARTGEN_SPEC.md``; this module is
the machine-readable half of it.
"""

from __future__ import annotations

import re

import json
from dataclasses import dataclass, field
from pathlib import Path

#: ``${REPO}`` in a spec path stands for this checkout, so specs carry no machine-specific paths
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _expand_repo(value: str) -> str:
    return value.replace("${REPO}", str(_REPO_ROOT))
from typing import Any

__all__ = [
    "ArmorySpec",
    "ArmoryWeapon",
    "is_armory",
    "load_armory",
    "ALL_SOCKETS",
    "AttributeEffect",
    "BalanceSpec",
    "ExtraPackage",
    "FragmentSpec",
    "FireSoundSpec",
    "MaterialSpec",
    "MODIFIER_TYPES",
    "Options",
    "PART_LIST_FIELDS",
    "PartOverrides",
    "PartSpec",
    "RegisterTarget",
    "SPEC_SCHEMA_VERSION",
    "SlotUpgrade",
    "Spec",
    "SpecError",
    "TitleSpec",
    "load_spec",
]

SPEC_SCHEMA_VERSION = 1

#: ``sockets: "all"`` means "every socket the template fragment maps" (resolved
#: against the catalog at emit time).
ALL_SOCKETS = "all"

#: the ``WeightedParts`` fields of a ``WeaponPartListCollectionDefinition``
PART_LIST_FIELDS = (
    "BodyPartData",
    "GripPartData",
    "BarrelPartData",
    "SightPartData",
    "StockPartData",
    "ElementalPartData",
    "Accessory1PartData",
    "Accessory2PartData",
    "MaterialPartData",
)

#: ``EModifierType`` members, in enum order (the generated mod falls back to the index)
MODIFIER_TYPES = ("MT_Scale", "MT_PreAdd", "MT_PostAdd")


class SpecError(ValueError):
    """The spec is structurally unusable (missing field, dangling reference)."""


def _req(data: dict[str, Any], key: str, where: str) -> Any:
    if key not in data or data[key] in (None, ""):
        raise SpecError(f"{where}: required field {key!r} is missing")
    return data[key]


def _int(value: Any, key: str, where: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SpecError(f"{where}: {key!r} must be an integer, got {value!r}") from exc


@dataclass
class FragmentSpec:
    """One new gestalt fragment appended to a weapon type's fragment table."""

    name: str
    template_fragment: str
    first_index: int
    num_primitives: int
    #: vertical offset applied to the bounds and to ``dz_sockets`` (unreal units)
    dz: float = 0.0
    #: original (unmangled) socket names to clone, or ``"all"``
    sockets: list[str] | str = ALL_SOCKETS
    #: sockets that move by ``dz`` along Z; the M1/M2 deformation moves the muzzle
    dz_sockets: list[str] = field(default_factory=lambda: ["Muzzle"])
    #: ``{"Muzzle": [x, y, z]}`` -- put a socket at an explicit point in **mesh
    #: space** instead of wherever the template fragment had it.  Needed when the
    #: new geometry is not a variation of the template but a different weapon (M6:
    #: the AK's muzzle is nowhere near the Shredifier's).  The generated mod
    #: re-parents such a socket to the gestalt ``Root`` bone, which is the identity
    #: for this skeleton, so the value is used verbatim as ``RelativeLocation``.
    #: ``dz`` is *not* applied on top of an override.
    socket_overrides: dict[str, list[float]] = field(default_factory=dict)
    #: ``{"origin": [x, y, z], "extent": [ex, ey, ez], "radius": r}`` in mesh space: the
    #: fragment's ``ReferencePoseBounds`` instead of the template's.  The template's box is
    #: the *template geometry's* (the Shredifier barrel reaches y = -104); the item-card
    #: preview frames the weapon from these boxes, so a fragment of a different shape
    #: needs its own (M8 s7).  ``dz`` is not applied on top of an override.
    bounds_override: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FragmentSpec:
        where = f"fragment {data.get('name', '?')!r}"
        bounds = data.get("bounds_override")
        if bounds is not None:
            if not isinstance(bounds, dict):
                raise SpecError(f"{where}: bounds_override must be an object")
            for key in ("origin", "extent"):
                value = bounds.get(key)
                if not isinstance(value, (list, tuple)) or len(value) != 3:
                    raise SpecError(f"{where}: bounds_override.{key} must be [x, y, z]")
            if "radius" not in bounds:
                raise SpecError(f"{where}: bounds_override needs a radius")
            bounds = {"origin": [float(v) for v in bounds["origin"]],
                      "extent": [float(v) for v in bounds["extent"]],
                      "radius": float(bounds["radius"])}
        sockets = data.get("sockets", ALL_SOCKETS)
        if isinstance(sockets, str) and sockets != ALL_SOCKETS:
            raise SpecError(f"{where}: sockets must be a list or {ALL_SOCKETS!r}")
        overrides: dict[str, list[float]] = {}
        raw = data.get("socket_overrides") or {}
        if not isinstance(raw, dict):
            raise SpecError(f"{where}: socket_overrides must be an object")
        for socket, location in raw.items():
            if not isinstance(location, (list, tuple)) or len(location) != 3:
                raise SpecError(
                    f"{where}: socket_overrides[{socket!r}] must be [x, y, z] in mesh space"
                )
            overrides[str(socket)] = [float(v) for v in location]
        return cls(
            name=str(_req(data, "name", where)),
            template_fragment=str(_req(data, "template_fragment", where)),
            first_index=_int(_req(data, "first_index", where), "first_index", where),
            num_primitives=_int(_req(data, "num_primitives", where), "num_primitives", where),
            dz=float(data.get("dz", 0.0) or 0.0),
            sockets=sockets if isinstance(sockets, str) else [str(s) for s in sockets],
            dz_sockets=[str(s) for s in data.get("dz_sockets", ["Muzzle"])],
            socket_overrides=overrides,
            bounds_override=bounds,
        )

    def to_dict(self) -> dict[str, Any]:
        out = {
            "name": self.name,
            "template_fragment": self.template_fragment,
            "first_index": self.first_index,
            "num_primitives": self.num_primitives,
            "dz": self.dz,
            "sockets": self.sockets if isinstance(self.sockets, str) else list(self.sockets),
            "dz_sockets": list(self.dz_sockets),
        }
        # kept out of the dict when empty so a spec that does not use overrides
        # round-trips byte-for-byte through the emitter, as it always did
        if self.socket_overrides:
            out["socket_overrides"] = {k: list(v) for k, v in self.socket_overrides.items()}
        if self.bounds_override is not None:
            out["bounds_override"] = dict(self.bounds_override)
        return out


@dataclass
class RegisterTarget:
    """Where a part is registered so it can roll (F5): a balance + a slot field."""

    balance: str
    field: str

    @classmethod
    def from_dict(cls, data: dict[str, Any], where: str) -> RegisterTarget:
        if not isinstance(data, dict):
            raise SpecError(f"{where}: register_in entries must be objects")
        return cls(balance=str(_req(data, "balance", where)), field=str(_req(data, "field", where)))

    def to_dict(self) -> dict[str, Any]:
        return {"balance": self.balance, "field": self.field}


@dataclass
class AttributeEffect:
    """One ``WeaponAttributeEffects`` / ``ExternalAttributeEffects`` row: attribute,
    modifier type and a constant value (``BaseValueConstant``; scale 1, no attribute)."""

    attribute: str
    modifier: str = "MT_Scale"
    value: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any], where: str) -> AttributeEffect:
        if not isinstance(data, dict):
            raise SpecError(f"{where}: attribute effects must be objects")
        modifier = str(data.get("modifier", "MT_Scale") or "MT_Scale")
        if modifier not in MODIFIER_TYPES:
            raise SpecError(
                f"{where}: modifier {modifier!r} is not one of {', '.join(MODIFIER_TYPES)}"
            )
        return cls(
            attribute=str(_req(data, "attribute", where)),
            modifier=modifier,
            value=float(data.get("value", 0.0) or 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"attribute": self.attribute, "modifier": self.modifier, "value": self.value}


@dataclass
class SlotUpgrade:
    """One ``AttributeSlotUpgrades`` row: ``(SlotName, GradeIncrease, bActivateSlot)``."""

    slot: str
    grade: int = 0
    activate: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any], where: str) -> SlotUpgrade:
        if not isinstance(data, dict):
            raise SpecError(f"{where}: attribute_slot_upgrades entries must be objects")
        return cls(
            slot=str(_req(data, "slot", where)),
            grade=_int(data.get("grade", 0) or 0, "grade", where),
            activate=bool(data.get("activate", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"slot": self.slot, "grade": self.grade, "activate": self.activate}


@dataclass
class PartOverrides:
    """Property edits applied to a cloned part after construction.

    ``properties`` sets plain scalars (``bIsSpinningEnabled``, ``NumPhysicalBarrelsToFireFrom``);
    the three lists **replace** the clone's whole array when given (``None`` leaves the
    template's rows alone).  This is how the AK gets the CS2-derived numbers from
    ``bl2-mods/ak47/AK47.txt`` instead of the Shredifier's.
    """

    properties: dict[str, Any] = field(default_factory=dict)
    #: ``{property: object path}`` -- object references (``Material`` on a material
    #: part); resolved with ``find_object`` at registration time
    object_properties: dict[str, str] = field(default_factory=dict)
    #: array properties emptied on the clone (``PrefixList``: the Vladof grip carries seven
    #: manufacturer prefixes, so a clone rolls "Angry"/"Rabid"; empty means no prefix)
    clear_arrays: list[str] = field(default_factory=list)
    weapon_attribute_effects: list[AttributeEffect] | None = None
    external_attribute_effects: list[AttributeEffect] | None = None
    #: the same two arrays but applied only while the zoom button is held
    #: (``ZoomWeaponAttributeEffects`` / ``ZoomExternalAttributeEffects``)
    zoom_weapon_attribute_effects: list[AttributeEffect] | None = None
    zoom_external_attribute_effects: list[AttributeEffect] | None = None
    attribute_slot_upgrades: list[SlotUpgrade] | None = None
    #: ``{dotted path: value}`` set on the clone last -- any depth of struct/array
    #: (``MaterialVectorParameterValues[0].ParameterValue.B``); values are encoded as in
    #: :class:`ObjectSpec` (``{"object": path}``, ``{"enum": "E.Member"}``, ``null``)
    values: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any], where: str) -> PartOverrides:
        if not isinstance(data, dict):
            raise SpecError(f"{where}: overrides must be an object")
        props = data.get("properties") or {}
        if not isinstance(props, dict):
            raise SpecError(f"{where}: overrides.properties must be an object")
        for key, value in props.items():
            if not isinstance(value, (bool, int, float, str)):
                raise SpecError(
                    f"{where}: overrides.properties[{key!r}] must be a bool/int/float/str "
                    "(arrays and structs have their own keys)"
                )

        clears = data.get("clear_arrays") or []
        if not isinstance(clears, list) or not all(isinstance(v, str) and v for v in clears):
            raise SpecError(f"{where}: overrides.clear_arrays must be a list of property names")
        objects = data.get("object_properties") or {}
        if not isinstance(objects, dict) or not all(
                isinstance(v, str) and v for v in objects.values()):
            raise SpecError(f"{where}: overrides.object_properties must map property -> object path")

        def effects(key: str) -> list[AttributeEffect] | None:
            rows = data.get(key)
            if rows is None:
                return None
            if not isinstance(rows, list):
                raise SpecError(f"{where}: overrides.{key} must be a list")
            return [AttributeEffect.from_dict(r, f"{where} overrides.{key}") for r in rows]

        upgrades = data.get("attribute_slot_upgrades")
        if upgrades is not None and not isinstance(upgrades, list):
            raise SpecError(f"{where}: overrides.attribute_slot_upgrades must be a list")
        values = data.get("values") or {}
        _check_values(values, f"{where}: overrides.values")
        return cls(
            values=dict(values),
            properties=dict(props),
            object_properties={str(k): str(v) for k, v in objects.items()},
            clear_arrays=[str(v) for v in clears],
            weapon_attribute_effects=effects("weapon_attribute_effects"),
            external_attribute_effects=effects("external_attribute_effects"),
            zoom_weapon_attribute_effects=effects("zoom_weapon_attribute_effects"),
            zoom_external_attribute_effects=effects("zoom_external_attribute_effects"),
            attribute_slot_upgrades=(
                [SlotUpgrade.from_dict(r, f"{where} overrides.attribute_slot_upgrades")
                 for r in upgrades]
                if upgrades is not None else None
            ),
        )

    def is_empty(self) -> bool:
        return (
            not self.properties
            and not self.object_properties
            and not self.clear_arrays
            and self.weapon_attribute_effects is None
            and self.external_attribute_effects is None
            and self.zoom_weapon_attribute_effects is None
            and self.zoom_external_attribute_effects is None
            and self.attribute_slot_upgrades is None
            and not self.values
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.properties:
            out["properties"] = dict(self.properties)
        if self.object_properties:
            out["object_properties"] = dict(self.object_properties)
        if self.clear_arrays:
            out["clear_arrays"] = list(self.clear_arrays)
        if self.weapon_attribute_effects is not None:
            out["weapon_attribute_effects"] = [e.to_dict() for e in self.weapon_attribute_effects]
        if self.external_attribute_effects is not None:
            out["external_attribute_effects"] = [
                e.to_dict() for e in self.external_attribute_effects]
        if self.zoom_weapon_attribute_effects is not None:
            out["zoom_weapon_attribute_effects"] = [
                e.to_dict() for e in self.zoom_weapon_attribute_effects]
        if self.zoom_external_attribute_effects is not None:
            out["zoom_external_attribute_effects"] = [
                e.to_dict() for e in self.zoom_external_attribute_effects]
        if self.attribute_slot_upgrades is not None:
            out["attribute_slot_upgrades"] = [u.to_dict() for u in self.attribute_slot_upgrades]
        if self.values:
            out["values"] = dict(self.values)
        return out


_PATH_STEP = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[\d+\])?")


def _check_value(value: Any, where: str) -> None:
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    if isinstance(value, dict) and len(value) == 1 and (
            isinstance(value.get("object"), str) or isinstance(value.get("enum"), str)):
        if "enum" in value and value["enum"].count(".") > 1:
            raise SpecError(f"{where}: enum value {value['enum']!r} must be 'Member' or 'EnumName.Member'")
        return
    raise SpecError(f"{where}: {value!r} is not a scalar, null, {{'object': path}} or {{'enum': 'E.M'}}")


def _check_values(values: Any, where: str) -> None:
    if not isinstance(values, dict):
        raise SpecError(f"{where} must map dotted path -> value")
    for path, value in values.items():
        if not all(_PATH_STEP.fullmatch(step) for step in str(path).split(".")):
            raise SpecError(f"{where}: bad path {path!r} (Name, Name[3], dotted)")
        _check_value(value, f"{where}[{path!r}]")


def _card_icon(data: Any) -> dict[str, Any] | None:
    if not data:
        return None
    if not isinstance(data, dict) or not data.get("element_parts") or not str(data.get("image", "")).startswith("img://"):
        raise SpecError("card_icon needs element_parts and an img:// image")
    for key in ("card", "inline"):
        box = data.get(key)
        if box is not None and not all(k in box for k in ("x", "y", "w", "h")):
            raise SpecError(f"card_icon.{key} needs x, y, w, h")
    return dict(data)


def _when(value: Any, where: str) -> str:
    if value not in ("menu", "map_load"):
        raise SpecError(f"{where}: when is 'menu' or 'map_load'")
    return str(value)


@dataclass
class ObjectSpec:
    """A runtime object cloned from a stock one and edited: the element plumbing (a
    WillowDamageTypeDefinition, a StatusEffectDefinition, a SkillDefinition, the Behavior_*
    sub-objects that link them). Constructed at the menu tick in spec order, after the
    materials and before the parts, so a part's overrides can point at one.

    ``values`` are set last and may reach any depth (``BaseDuration.BaseValueConstant``);
    ``object_lists`` replace an array of object references; ``struct_rows`` replace an
    array of structs, each row appended as a copy of a prototype row (the template's own
    first row, or ``prototype`` = ``"<object path>:<Field>"``) and then edited by dotted path.
    ``subobject`` puts it under ``outer`` with the ``:`` separator (a behavior inside a
    status effect) instead of inside a package.
    """

    class_name: str
    name: str
    outer: str
    template: str
    subobject: bool = False
    #: "menu" (default) or "map_load": built on the first map load, for templates that are
    #: only loaded with a map (GD_Impacts.Bullets.* are imports of Startup, not exports)
    when: str = "menu"
    values: dict[str, Any] = field(default_factory=dict)
    object_lists: dict[str, list[str | None]] = field(default_factory=dict)
    struct_rows: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def path(self) -> str:
        return f"{self.outer}{':' if self.subobject else '.'}{self.name}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ObjectSpec":
        if not isinstance(data, dict):
            raise SpecError("objects entries must be objects")
        where = f"object {data.get('name', '?')!r}"
        values = data.get("values") or {}
        _check_values(values, f"{where}: values")
        lists = data.get("object_lists") or {}
        if not isinstance(lists, dict) or not all(
                isinstance(v, list) and all(x is None or isinstance(x, str) for x in v)
                for v in lists.values()):
            raise SpecError(f"{where}: object_lists must map field -> [object path | null]")
        rows = data.get("struct_rows") or {}
        if not isinstance(rows, dict):
            raise SpecError(f"{where}: struct_rows must map field -> {{rows, prototype?}}")
        for field_name, block in rows.items():
            if not isinstance(block, dict) or not isinstance(block.get("rows"), list):
                raise SpecError(f"{where}: struct_rows[{field_name!r}] needs a rows list")
            proto = block.get("prototype")
            if proto is not None and (not isinstance(proto, str) or ":" not in proto):
                raise SpecError(f"{where}: struct_rows[{field_name!r}].prototype is "
                                "'<object path>:<Field>'")
            for i, row in enumerate(block["rows"]):
                _check_values(row, f"{where}: struct_rows[{field_name!r}].rows[{i}]")
        return cls(
            class_name=str(_req(data, "class", where)),
            name=str(_req(data, "name", where)),
            outer=str(_req(data, "outer", where)),
            template=str(_req(data, "template", where)),
            subobject=bool(data.get("subobject", False)),
            when=_when(data.get("when", "menu"), where),
            values=dict(values),
            object_lists={str(k): list(v) for k, v in lists.items()},
            struct_rows={str(k): dict(v) for k, v in rows.items()},
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"class": self.class_name, "name": self.name,
                               "outer": self.outer, "template": self.template}
        if self.subobject:
            out["subobject"] = True
        if self.when != "menu":
            out["when"] = self.when
        if self.values:
            out["values"] = dict(self.values)
        if self.object_lists:
            out["object_lists"] = {k: list(v) for k, v in self.object_lists.items()}
        if self.struct_rows:
            out["struct_rows"] = {k: dict(v) for k, v in self.struct_rows.items()}
        return out


@dataclass
class FreezeSpec:
    """Freeze and shatter, in the mod (BL2 has no freeze: TPS's is native).

    Every application of ``status`` to a pawn is counted; ``stacks`` of them within
    ``window_s`` freeze it for ``seconds``: ``CustomTimeDilation`` of the pawn, its
    controller and its weapon drops to ``time_dilation`` and ``frozen_status`` is applied
    (its skill carries the frozen-state vulnerabilities). A pawn that dies frozen is
    gibbed (``WillowPawn.TryFullBodyGib``) when ``shatter`` is set. Players are left alone
    unless ``affect_players`` (the harness probe turns it on).
    """

    status: str
    frozen_status: str | None = None
    stacks: int = 3
    window_s: float = 5.0
    seconds: float = 3.0
    time_dilation: float = 0.02
    shatter: bool = True
    shatter_damage_type: str | None = None
    affect_players: bool = False
    #: material put on every element of the frozen pawn's mesh (a runtime MIC path), the
    #: originals restored on thaw/death; TPS drives a character-material scalar BL2 lacks
    ice_material: str | None = None
    #: particle systems spawned by the freeze: {"frozen": ..., "thaw": ..., "shatter": ...,
    #: "bone_param": "BoneSocketActor"} (actor parameter set to the pawn on each emitter)
    fx: dict[str, str] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FreezeSpec":
        if not isinstance(data, dict):
            raise SpecError("freeze must be an object")
        spec = cls(
            status=str(_req(data, "status", "freeze")),
            frozen_status=(str(data["frozen_status"]) if data.get("frozen_status") else None),
            stacks=int(data.get("stacks", 3)),
            window_s=float(data.get("window_s", 5.0)),
            seconds=float(data.get("seconds", 3.0)),
            time_dilation=float(data.get("time_dilation", 0.02)),
            shatter=bool(data.get("shatter", True)),
            shatter_damage_type=(str(data["shatter_damage_type"])
                                 if data.get("shatter_damage_type") else None),
            affect_players=bool(data.get("affect_players", False)),
            ice_material=(str(data["ice_material"]) if data.get("ice_material") else None),
            fx=({str(k): str(v) for k, v in data["fx"].items()} if data.get("fx") else None),
        )
        if spec.stacks < 1 or spec.seconds <= 0 or not 0 < spec.time_dilation < 1:
            raise SpecError("freeze: stacks >= 1, seconds > 0, 0 < time_dilation < 1")
        return spec

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in (
            "status", "frozen_status", "stacks", "window_s", "seconds", "time_dilation",
            "shatter", "shatter_damage_type", "affect_players", "ice_material", "fx")}


@dataclass
class PartSpec:
    """One new ``WeaponPartDefinition`` constructed at runtime.

    ``fragment`` may be ``None``: the part then **draws nothing**.  It is cloned from a
    non-gestalt template (``AR_Sight_None``, ``AR_Accessory_None``) and the generated
    mod keeps ``bIsGestaltMode`` False on the clone, so no fragment is looked up at
    all.  That is how a spec claims a slot it wants empty (the AK's rear sight is body
    geometry; a stock scope rolling into ``SightPartData`` would sit on top of it).
    Such a part must name its ``template_part``: there is no fragment to default it from.
    """

    part_name: str
    fragment: str | None
    slot: str | None = None
    #: object path of the group the part is constructed under; defaults to the
    #: catalog's usual outer for this weapon type + slot (GD_Weap_AssaultRifle.Barrel)
    outer: str | None = None
    #: the existing part the new one is cloned from; defaults to a catalog part
    #: that uses the fragment's template
    template_part: str | None = None
    #: may be empty when a ``balances[]`` entry lists the part in one of its part lists
    register_in: list[RegisterTarget] = field(default_factory=list)
    #: property edits on the clone (stats); ``None`` keeps the template's values
    overrides: PartOverrides | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PartSpec:
        where = f"part {data.get('part_name', '?')!r}"
        overrides = (
            PartOverrides.from_dict(data["overrides"], where) if data.get("overrides") else None
        )
        return cls(
            part_name=str(_req(data, "part_name", where)),
            fragment=(str(data["fragment"]) if data.get("fragment") else None),
            slot=(str(data["slot"]) if data.get("slot") else None),
            outer=(str(data["outer"]) if data.get("outer") else None),
            template_part=(str(data["template_part"]) if data.get("template_part") else None),
            register_in=[
                RegisterTarget.from_dict(entry, where) for entry in data.get("register_in", [])
            ],
            overrides=overrides if overrides is not None and not overrides.is_empty() else None,
        )

    def to_dict(self) -> dict[str, Any]:
        out = {
            "part_name": self.part_name,
            "fragment": self.fragment,
            "slot": self.slot,
            "outer": self.outer,
            "template_part": self.template_part,
            "register_in": [t.to_dict() for t in self.register_in],
        }
        # only when used, so earlier specs round-trip byte for byte
        if self.overrides is not None:
            out["overrides"] = self.overrides.to_dict()
        return out


@dataclass
class ExtraPackage:
    """A further ``.upk`` the mod loads (and roots) at the menu tick: textures, say."""

    name: str
    file: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtraPackage:
        if isinstance(data, str):
            return cls(name=data)
        if not isinstance(data, dict):
            raise SpecError("extra_packages entries must be objects or names")
        return cls(name=str(_req(data, "name", "extra_packages")),
                   file=(str(data["file"]) if data.get("file") else None))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "file": self.file}


@dataclass
class MaterialSpec:
    """A ``MaterialInstanceConstant`` constructed at runtime.

    Built the way in-game skin mods build one: an empty MIC, ``SetParent(parent)`` to a
    stock MIC (so the compiled shader is the parent's), then ``Set*ParameterValue`` for
    every parameter named here.  Texture parameters name ``Texture2D`` paths, usually in
    one of the spec's ``extra_packages``; vector parameters are ``[r, g, b, a]``.
    """

    name: str
    outer: str
    parent: str
    texture_parameters: dict[str, str] = field(default_factory=dict)
    vector_parameters: dict[str, list[float]] = field(default_factory=dict)
    scalar_parameters: dict[str, float] = field(default_factory=dict)

    @property
    def path(self) -> str:
        return f"{self.outer}.{self.name}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MaterialSpec:
        where = f"material {data.get('name', '?')!r}"
        textures = data.get("texture_parameters") or {}
        vectors = data.get("vector_parameters") or {}
        scalars = data.get("scalar_parameters") or {}
        for label, value in (("texture_parameters", textures), ("vector_parameters", vectors),
                             ("scalar_parameters", scalars)):
            if not isinstance(value, dict):
                raise SpecError(f"{where}: {label} must be an object")
        for pname, rgba in vectors.items():
            if not isinstance(rgba, (list, tuple)) or len(rgba) != 4:
                raise SpecError(f"{where}: vector_parameters[{pname!r}] must be [r, g, b, a]")
        return cls(
            name=str(_req(data, "name", where)),
            outer=str(_req(data, "outer", where)),
            parent=str(_req(data, "parent", where)),
            texture_parameters={str(k): str(v) for k, v in textures.items()},
            vector_parameters={str(k): [float(x) for x in v] for k, v in vectors.items()},
            scalar_parameters={str(k): float(v) for k, v in scalars.items()},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "outer": self.outer,
            "parent": self.parent,
            "texture_parameters": dict(self.texture_parameters),
            "vector_parameters": {k: list(v) for k, v in self.vector_parameters.items()},
            "scalar_parameters": dict(self.scalar_parameters),
        }


@dataclass
class TitleSpec:
    """A ``WeaponNamePartDefinition`` constructed at runtime: the weapon's name and red text.

    Cloned from ``template`` (a stock legendary title keeps its priority and level range),
    given ``part_name`` (what the item card shows) and, optionally, ``red_text`` -- the
    ``NoConstraintText`` of the title's first ``CustomPresentations`` entry, which is where
    every legendary's flavour line lives.  ``on_parts`` names the spec parts whose
    ``TitleList`` is replaced by this title, because a weapon's title comes from its parts
    (the Shredifier's barrel carries ``Title_Legendary_Shredifier``).
    """

    name: str
    outer: str
    template: str
    part_name: str
    red_text: str | None = None
    on_parts: list[str] = field(default_factory=list)
    #: ``bNameIsUnique``: the card shows the title alone, no prefix (how the game's own uniques
    #: are named; the weapon type otherwise supplies a fallback prefix such as "Assault")
    name_is_unique: bool = False

    @property
    def path(self) -> str:
        return f"{self.outer}.{self.name}"

    @classmethod
    def from_dict(cls, data: dict[str, Any], where: str) -> TitleSpec:
        if not isinstance(data, dict):
            raise SpecError(f"{where}: title must be an object")
        where = f"{where} title {data.get('name', '?')!r}"
        return cls(
            name=str(_req(data, "name", where)),
            outer=str(_req(data, "outer", where)),
            template=str(_req(data, "template", where)),
            part_name=str(_req(data, "part_name", where)),
            red_text=(str(data["red_text"]) if data.get("red_text") else None),
            on_parts=[str(p) for p in data.get("on_parts", [])],
            name_is_unique=bool(data.get("name_is_unique", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "outer": self.outer,
            "template": self.template,
            "part_name": self.part_name,
            "red_text": self.red_text,
            "on_parts": list(self.on_parts),
            "name_is_unique": self.name_is_unique,
        }


@dataclass
class BalanceSpec:
    """A ``WeaponBalanceDefinition`` constructed at runtime: the weapon as its own gun.

    Cloned from ``template_balance`` (so rarity, manufacturer, base definition and the
    cooked part list come along), then given its **own** runtime part-list collection --
    a clone of the template's, with every field named in ``part_lists`` replaced by
    exactly the listed parts (spec part names, or full paths of stock parts).  Fields not
    named keep the template's rows (the Shredifier's elemental and material lists, say).
    ``pools`` are ``ItemPoolDefinition`` paths the balance is appended to so it can drop.
    """

    name: str
    outer: str
    template_balance: str
    part_lists: dict[str, list[str]] = field(default_factory=dict)
    title: TitleSpec | None = None
    pools: list[str] = field(default_factory=list)
    #: leaf name of the runtime ``WeaponPartListCollectionDefinition`` under the balance
    collection_name: str = "RuntimePartList"
    #: null ``PrefixPartDefinition`` on every weapon of this balance as it is named (hooks on
    #: ``WillowWeapon:ChooseRandomNameParts`` / ``InitializeInternal``).  The weapon type
    #: supplies a fallback prefix ("Assault") when no part carries one, and ``bNameIsUnique``
    #: on the title does not suppress it (M8 s8); this does.
    suppress_prefix: bool = False

    @property
    def path(self) -> str:
        return f"{self.outer}.{self.name}"

    @property
    def collection_path(self) -> str:
        return f"{self.path}:{self.collection_name}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BalanceSpec:
        where = f"balance {data.get('name', '?')!r}"
        raw_lists = data.get("part_lists") or {}
        if not isinstance(raw_lists, dict):
            raise SpecError(f"{where}: part_lists must be an object of field -> [parts]")
        part_lists: dict[str, list[str]] = {}
        for fld, parts in raw_lists.items():
            if fld not in PART_LIST_FIELDS:
                raise SpecError(
                    f"{where}: part_lists field {fld!r} is not one of {', '.join(PART_LIST_FIELDS)}"
                )
            if not isinstance(parts, list) or not parts:
                raise SpecError(f"{where}: part_lists[{fld!r}] must be a non-empty list")
            part_lists[fld] = [str(p) for p in parts]
        return cls(
            name=str(_req(data, "name", where)),
            outer=str(_req(data, "outer", where)),
            template_balance=str(_req(data, "template_balance", where)),
            part_lists=part_lists,
            title=TitleSpec.from_dict(data["title"], where) if data.get("title") else None,
            pools=[str(p) for p in data.get("pools", [])],
            collection_name=str(data.get("collection_name", "RuntimePartList") or "RuntimePartList"),
            suppress_prefix=bool(data.get("suppress_prefix", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "outer": self.outer,
            "template_balance": self.template_balance,
            "part_lists": {k: list(v) for k, v in self.part_lists.items()},
            "pools": list(self.pools),
            "collection_name": self.collection_name,
        }
        if self.suppress_prefix:
            out["suppress_prefix"] = True
        if self.title is not None:
            out["title"] = self.title.to_dict()
        return out


@dataclass
class Options:
    """Runtime behaviour baked into the generated mod."""

    #: must stay ``"menu"``: registering after a map load crashes (F16)
    register_at: str = "menu"
    #: must stay True: unrooted objects die in the level-transition GC (F17)
    keep_alive: bool = True
    #: emit the GeneratePlayerSaveGame / ApplyPlayerSaveGameData hooks (F18)
    save_roundtrip: bool = True
    #: emit the ValidateWeaponDefinition override (F19)
    validate_override: bool = True
    #: emit the F9/F10/F11 keybinds and the census bl2_verify reads
    test_harness: bool = False
    #: with ``test_harness``: also emit the per-map phase machine that reproduces
    #: ``src/bl2_verify/m2_mod_template.py`` record for record, so the unattended
    #: verify loop can read this mod's status file instead of the M2 one
    harness_auto: bool = True
    #: the harness's ``ConsoleCommand("FOV <n>")`` in phase 3
    harness_fov: int = 100
    #: the harness's ``SetBehindView(True)`` in phase 3
    harness_behindview: bool = True
    #: ``"first"`` / ``"third"``: make phase 3 set the camera explicitly to that view
    #: (``SetBehindView(view == "third")``) instead of only ever switching third person
    #: on.  First person is what the verify loop captures for a short weapon held close:
    #: the weapon fills a fixed screen region and the pawn is not in the crop (F22).
    #: ``None`` keeps the M2 behaviour (``harness_behindview`` alone decides).
    harness_capture_view: str | None = None
    #: which of our weapons phase 2 equips for the capture: ``"newest"`` (the one built
    #: from our parts) or ``"second_newest"`` (the A/B stock grant), the latter being how
    #: a stock baseline capture is taken in the same pose and view
    harness_equip: str = "newest"
    #: ``"idle"`` (M2 behaviour) or ``"ads"``: after the camera step, phase 3 aims down
    #: sights (``StartAltFire``), waits 60 ticks for the zoom blend, then records the
    #: camera POV and the world position of every socket of ours on the held weapon's
    #: first-person mesh in a ``sight_check`` record before ``done`` -- the numbers the
    #: iron-sight alignment (M7 task 5) is measured from.  ``"hip"``: the same record
    #: without the zoom -- where the camera and sockets are in the hip pose.
    #: ``"inventory"``: phase 3 opens the status menu on the inventory
    #: (``ShowStatusMenu_Inventory``) instead of zooming, so the capture is the item card
    harness_pose: str = "idle"
    #: ``"ticks"`` (M2: phase waits counted in viewport ticks) or ``"seconds"``: the same
    #: waits on the wall clock.  Tick counts scale with the frame rate -- at ~375 fps the
    #: whole phase machine ran in 0.8 s and the equip fired before the grant had landed
    #: (M8 §6) -- so any spec meant to run unattended should say "seconds".
    harness_timing: str = "ticks"
    #: **``test_harness`` only.** The mission whose reward table the harness hijacks to
    #: grant a weapon.  Every pipeline mod shares one pristine snapshot per mission
    #: (F26), so two mods may name the same mission; a spec can still pick its own.
    harness_mission: str = "GD_Episode01.M_Ep1_Champion"
    #: **``test_harness`` only.** The four keybinds, in order: grant, equip newest, equip
    #: second newest, toggle first/third person.  Two spawn/harness mods enabled at once
    #: must not share keys, so the second one moves (e.g. F5-F8).
    harness_keys: tuple[str, str, str, str] = ("F9", "F10", "F11", "F12")
    #: fragment name to force back onto its stock part on every map load, for text
    #: mods that retarget it (the AK-47 text mod retargets the Shredifier barrel)
    force_template_fragment_on_map_load: str | None = None
    #: viewport ticks to wait at the main menu before registering
    menu_ticks: int = 600
    #: absolute path baked into the mod as its status file, so the verify loop can
    #: point it at ``scratch/<name>_status.json`` without dropping a control file.
    #: ``control.json``'s ``status_path`` still wins over it at runtime.
    status_path: str | None = None
    #: draw from our OWN clone of the host gestalt definition through our own
    #: WeaponTypeDefinition (per balance) instead of re-pointing the stock definition, so
    #: several weapons on one host type each keep their own package and vertex budget
    own_gestalt: bool = False
    #: folder under sdk_mods/_pipeline_saves/ holding this weapon's per-save part records;
    #: default the package name. A weapon moved to its own package keeps reading its old
    #: records with it (the Boxgun left the shared PipelineMeshes package on 2026-09-23 and
    #: players' saves hold Boxguns recorded under PipelineMeshes)
    save_package: str | None = None
    #: **test_harness only.** Apply a status effect to the PLAYER pawn at map load and record
    #: ground speed / active effects / time dilation over time (the loop's save has no
    #: enemies): ``{"status": path, "applications": n, "interval_s": s, "samples_s": [...]}``.
    #: The phase machine waits until the last sample, so the capture is unaffected.
    harness_status_probe: dict[str, Any] | None = None
    #: **test_harness only.** ``{"delay_s": 6, "seconds": 1.5}``: after ``done``, hold the
    #: trigger for ``seconds`` (tracer/impact/muzzle effects for the FX screenshots)
    harness_fire_test: dict[str, Any] | None = None
    #: harness only: engine function paths to log (first 3 calls each) as status records
    harness_trace: list[str] = field(default_factory=list)
    #: harness only: after the capture, drive rage through hits/kill/fill/decay ({start_s})
    harness_rage_test: dict[str, Any] | None = None
    #: harness only: setres through resolutions x meter states, cue driver captures
    harness_hud_test: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Options:
        return cls(
            register_at=str(data.get("register_at", "menu")),
            keep_alive=bool(data.get("keep_alive", True)),
            save_roundtrip=bool(data.get("save_roundtrip", True)),
            validate_override=bool(data.get("validate_override", True)),
            test_harness=bool(data.get("test_harness", False)),
            harness_auto=bool(data.get("harness_auto", True)),
            harness_fov=int(data.get("harness_fov", 100)),
            harness_behindview=bool(data.get("harness_behindview", True)),
            harness_capture_view=(
                str(data["harness_capture_view"]) if data.get("harness_capture_view") else None
            ),
            harness_equip=str(data.get("harness_equip", "newest") or "newest"),
            harness_pose=str(data.get("harness_pose", "idle") or "idle"),
            harness_timing=str(data.get("harness_timing", "ticks") or "ticks"),
            harness_mission=str(
                data.get("harness_mission", "GD_Episode01.M_Ep1_Champion")
                or "GD_Episode01.M_Ep1_Champion"
            ),
            harness_keys=tuple(
                str(k) for k in (data.get("harness_keys") or ("F9", "F10", "F11", "F12"))
            ),
            force_template_fragment_on_map_load=(
                str(data["force_template_fragment_on_map_load"])
                if data.get("force_template_fragment_on_map_load")
                else None
            ),
            menu_ticks=int(data.get("menu_ticks", 600)),
            status_path=(_expand_repo(str(data["status_path"])) if data.get("status_path") else None),
            own_gestalt=bool(data.get("own_gestalt", False)),
            save_package=(str(data["save_package"]) if data.get("save_package") else None),
            harness_status_probe=_probe(data.get("harness_status_probe")),
            harness_fire_test=(dict(data["harness_fire_test"]) if data.get("harness_fire_test") else None),
            harness_trace=[str(n) for n in data.get("harness_trace") or []],
            harness_rage_test=(dict(data["harness_rage_test"])
                               if data.get("harness_rage_test") else None),
            harness_hud_test=(dict(data["harness_hud_test"])
                              if data.get("harness_hud_test") else None),
        )

    def to_dict(self) -> dict[str, Any]:
        out = {
            "register_at": self.register_at,
            "keep_alive": self.keep_alive,
            "save_roundtrip": self.save_roundtrip,
            "validate_override": self.validate_override,
            "test_harness": self.test_harness,
            "harness_auto": self.harness_auto,
            "harness_fov": self.harness_fov,
            "harness_behindview": self.harness_behindview,
            "force_template_fragment_on_map_load": self.force_template_fragment_on_map_load,
            "menu_ticks": self.menu_ticks,
            "status_path": self.status_path,
        }
        # M7 options only when set, so an earlier spec's emitted spec.json is unchanged
        if self.harness_capture_view is not None:
            out["harness_capture_view"] = self.harness_capture_view
        if self.harness_equip != "newest":
            out["harness_equip"] = self.harness_equip
        if self.harness_pose != "idle":
            out["harness_pose"] = self.harness_pose
        if self.harness_timing != "ticks":
            out["harness_timing"] = self.harness_timing
        if self.harness_mission != "GD_Episode01.M_Ep1_Champion":
            out["harness_mission"] = self.harness_mission
        if tuple(self.harness_keys) != ("F9", "F10", "F11", "F12"):
            out["harness_keys"] = list(self.harness_keys)
        if self.own_gestalt:
            out["own_gestalt"] = True
        if self.save_package:
            out["save_package"] = self.save_package
        if self.harness_status_probe:
            out["harness_status_probe"] = dict(self.harness_status_probe)
        if self.harness_fire_test:
            out["harness_fire_test"] = dict(self.harness_fire_test)
        if self.harness_trace:
            out["harness_trace"] = list(self.harness_trace)
        if self.harness_rage_test:
            out["harness_rage_test"] = dict(self.harness_rage_test)
        if self.harness_hud_test:
            out["harness_hud_test"] = dict(self.harness_hud_test)
        return out


def _probe(data: Any) -> dict[str, Any] | None:
    if not data:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("status"), str):
        raise SpecError("options.harness_status_probe needs a status path")
    samples = [float(x) for x in (data.get("samples_s") or [1.0, 4.0, 8.0])]
    if samples != sorted(samples) or not samples or samples[0] <= 0:
        raise SpecError("options.harness_status_probe.samples_s must be ascending seconds > 0")
    out = {"status": data["status"], "applications": int(data.get("applications", 1)),
           "interval_s": float(data.get("interval_s", 0.25)), "samples_s": samples}
    target = str(data.get("target", "player"))
    if target not in ("player", "nearest_ai"):
        raise SpecError("options.harness_status_probe.target is 'player' or 'nearest_ai'")
    out["target"] = target
    if data.get("look_at"):  # face the target while probing (screenshots), restored after
        out["look_at"] = True
    if data.get("start_after_s"):  # wait out the loading movie before probing
        out["start_after_s"] = float(data["start_after_s"])
    if data.get("behind_view"):  # third person while probing (screenshots of the player)
        out["behind_view"] = True
    if data.get("kill_at_s") is not None:  # lethal damage to the target (shatter path)
        out["kill_at_s"] = float(data["kill_at_s"])
    if data.get("extra_status"):  # a second status applied alone later, to see it land
        out["extra_status"] = str(data["extra_status"])
        out["extra_at_s"] = float(data.get("extra_at_s", 6.0))
    return out


@dataclass
class FireSoundSpec:
    """A wav played through Windows on every shot of this spec's balances.

    BL2's audio is Wwise banks with no loose-file path, so the sound goes round the engine
    through :mod:`bl2_partgen.wave_mixer` (winmm ``waveOut``, one stream per shot, embedded
    in the generated module), which overlaps freely with every other custom clip. The file
    must be 16-bit PCM; ``volume`` (percent) is baked into the emitted copy. ``mute_stock``
    blanks the weapon type's ``FireSounds`` events for the duration of our shot only, so
    other weapons of that type keep theirs.
    """

    wav: str
    volume: int = 15
    mute_stock: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FireSoundSpec:
        if not isinstance(data, dict):
            raise SpecError("fire_sound must be an object")
        volume = int(data.get("volume", 15))
        if not 1 <= volume <= 100:
            raise SpecError(f"fire_sound.volume {volume} must be 1..100 (percent)")
        return cls(wav=str(_req(data, "wav", "fire_sound")), volume=volume,
                   mute_stock=bool(data.get("mute_stock", True)))

    def to_dict(self) -> dict[str, Any]:
        return {"wav": self.wav, "volume": self.volume, "mute_stock": self.mute_stock}


@dataclass
class AltFireSpec:
    """Right mouse button as a second trigger instead of aim-down-sights (Shiv's shotgun).

    While the player holds a weapon of this spec's balances, ``StartAltFire`` / ``StopAltFire``
    are blocked (so the weapon never zooms) and RMB pulls the normal trigger with the next shot
    marked as an alt shot: it costs ``ammo_cost`` rounds, deals ``damage_scale`` times the
    damage, blooms ``accuracy_impulse_scale`` times as far, kicks the view up by
    ``recoil_pitch_deg`` over ``recoil_seconds`` and pushes the player back. Hip shots get the
    smaller ``knockback_hip`` push. Knockback is ``Pawn.AddVelocity`` against the aim, in
    unreal units per second, plus a small lift so a grounded player leaves the floor friction.
    An alt pull with fewer than ``ammo_cost`` rounds in the clip does nothing (dry click).
    ``sound`` (optional 16-bit PCM wav) replaces the fire_sound clip on alt shots.
    """

    ammo_cost: int = 3
    damage_scale: float = 3.0
    accuracy_impulse_scale: float = 3.0
    recoil_pitch_deg: float = 6.0
    recoil_seconds: float = 0.12
    knockback_hip: float = 180.0
    knockback_alt: float = 520.0
    lift_hip: float = 60.0
    lift_alt: float = 160.0
    block_zoom: bool = True
    arm_window_s: float = 1.0
    sound: str | None = None
    volume: int | None = None

    _FLOATS = ("damage_scale", "accuracy_impulse_scale", "recoil_pitch_deg", "recoil_seconds",
               "knockback_hip", "knockback_alt", "lift_hip", "lift_alt", "arm_window_s")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AltFireSpec:
        if not isinstance(data, dict):
            raise SpecError("alt_fire must be an object")
        unknown = set(data) - {"ammo_cost", "block_zoom", "sound", "volume", *cls._FLOATS}
        if unknown:
            raise SpecError(f"alt_fire: unknown key(s) {sorted(unknown)}")
        out = cls()
        for key in cls._FLOATS:
            if key in data:
                setattr(out, key, float(data[key]))
        out.ammo_cost = int(data.get("ammo_cost", out.ammo_cost))
        out.block_zoom = bool(data.get("block_zoom", True))
        out.sound = str(data["sound"]) if data.get("sound") else None
        out.volume = int(data["volume"]) if data.get("volume") is not None else None
        if out.ammo_cost < 1:
            raise SpecError(f"alt_fire.ammo_cost {out.ammo_cost} must be >= 1")
        if out.damage_scale <= 0:
            raise SpecError(f"alt_fire.damage_scale {out.damage_scale} must be > 0")
        if out.recoil_seconds <= 0 or out.arm_window_s <= 0:
            raise SpecError("alt_fire.recoil_seconds and arm_window_s must be > 0")
        if out.volume is not None and not 1 <= out.volume <= 100:
            raise SpecError(f"alt_fire.volume {out.volume} must be 1..100 (percent)")
        return out

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ammo_cost": self.ammo_cost, "block_zoom": self.block_zoom}
        for key in self._FLOATS:
            out[key] = getattr(self, key)
        if self.sound:
            out["sound"] = self.sound
        if self.volume is not None:
            out["volume"] = self.volume
        return out

    def runtime(self) -> dict[str, Any]:
        """The literal the generated mod carries (no file paths)."""
        out = self.to_dict()
        out.pop("sound", None)
        out.pop("volume", None)
        return out


RAGE_HUD_DEFAULTS: dict[str, Any] = {
    # panel centre x and top y as fractions of the screen; sizes in 1080p pixels (scaled by
    # screen height, snapped to whole pixels)
    # label_px / footer_px are CAPITAL heights (the font cell is ~1.85x taller); min_* floors
    "x": 0.5, "y": 0.785, "w": 330, "pad": 11, "row_gap": 7, "label_px": 13, "bar_px": 12,
    "footer_px": 10, "min_label_px": 9, "min_footer_px": 7, "segments": 10, "fade_s": 0.2,
    # enraged_text None = derived from damage/speed_scale (cannot disagree with the gameplay);
    # idle_text None = nothing under an empty meter (the player learns the gains by playing)
    "enraged_text": None, "idle_text": None,
}


@dataclass
class RageSpec:
    """Shiv's Rage (Deadlock) as a legendary effect: a 0..max meter built by hits and kills.

    A shot of our weapon that hits gains ``gain_hit`` once (``gain_alt_hit`` for an alt shot),
    however many pellets land; a kill made while holding the weapon gains ``gain_kill``. After
    ``decay_delay_s`` without a gain the meter drains at ``decay_per_s``. At ``max`` the player
    is Enraged: all damage they deal x ``damage_scale``, ground speed x ``speed_scale``. ``hud``
    places the Canvas meter (fractions of the screen; pixel sizes at 1080p, scaled).
    """

    max: float = 100.0
    gain_hit: float = 5.0
    gain_alt_hit: float = 10.0
    gain_kill: float = 15.0
    decay_delay_s: float = 10.0
    decay_per_s: float = 2.5
    hit_window_s: float = 1.0
    damage_scale: float = 1.25
    speed_scale: float = 1.2
    hud: dict[str, Any] = field(default_factory=lambda: dict(RAGE_HUD_DEFAULTS))

    _FLOATS = ("max", "gain_hit", "gain_alt_hit", "gain_kill", "decay_delay_s", "decay_per_s",
               "hit_window_s", "damage_scale", "speed_scale")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RageSpec:
        if not isinstance(data, dict):
            raise SpecError("rage must be an object")
        unknown = set(data) - {"hud", *cls._FLOATS}
        if unknown:
            raise SpecError(f"rage: unknown key(s) {sorted(unknown)}")
        out = cls()
        for key in cls._FLOATS:
            if key in data:
                setattr(out, key, float(data[key]))
        hud = dict(RAGE_HUD_DEFAULTS)
        extra = set(data.get("hud") or {}) - set(hud)
        if extra:
            raise SpecError(f"rage.hud: unknown key(s) {sorted(extra)}")
        hud.update(data.get("hud") or {})
        out.hud = hud
        if out.max <= 0 or out.decay_per_s < 0 or out.decay_delay_s < 0:
            raise SpecError("rage: max must be > 0, decay values >= 0")
        if out.damage_scale <= 0 or out.speed_scale <= 0:
            raise SpecError("rage: damage_scale and speed_scale must be > 0")
        if int(hud["segments"]) < 1:
            raise SpecError("rage.hud.segments must be >= 1")
        return out

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {key: getattr(self, key) for key in self._FLOATS}
        out["hud"] = dict(self.hud)
        return out


@dataclass
class Spec:
    """Everything the emitter needs to write one SDK mod folder."""

    name: str
    package: str
    mesh_path: str
    weapon_type: str
    fragments: list[FragmentSpec] = field(default_factory=list)
    parts: list[PartSpec] = field(default_factory=list)
    #: runtime balances (the weapon as its own gun); empty for every pre-M7 spec
    balances: list[BalanceSpec] = field(default_factory=list)
    #: further packages loaded at the menu tick (a loose Texture2D package, M7 task 4)
    extra_packages: list[ExtraPackage] = field(default_factory=list)
    #: runtime MaterialInstanceConstants (own texture on a stock master material)
    materials: list[MaterialSpec] = field(default_factory=list)
    #: runtime objects cloned from stock ones (element plumbing), built before the parts
    objects: list[ObjectSpec] = field(default_factory=list)
    #: freeze and shatter driven by a status effect (cryo), in the mod
    freeze: FreezeSpec | None = None
    #: an element icon BL2's item card has no frame for (cryo), drawn into the card at runtime:
    #: {"element_parts": [part paths], "image": "img://Pkg.Texture", "texture_size": 64,
    #:  "card": {"x", "y", "w", "h"}, "inline": {"x", "y", "w", "h"}, "stat": "stat1"}
    card_icon: dict[str, Any] | None = None
    #: a wav played on every shot of our balances (winsound, round the engine)
    fire_sound: FireSoundSpec | None = None
    #: RMB as a second trigger (no zoom): ammo cost, damage scale, recoil, knockback
    alt_fire: AltFireSpec | None = None
    #: Shiv's Rage: hits/kills fill a meter, full meter = damage and speed (legendary effect)
    rage: RageSpec | None = None
    options: Options = field(default_factory=Options)
    author: str = ""
    version: str = "0.1.0"
    description: str = ""
    #: path of the built ``.upk`` (used by :func:`bl2_partgen.install`)
    package_file: str | None = None
    #: directory the spec was loaded from; a relative ``package_file`` resolves against it
    source_dir: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any], source_dir: Path | str | None = None) -> Spec:
        mod = data.get("mod", {})
        if not isinstance(mod, dict):
            raise SpecError("spec: 'mod' must be an object")
        known = {
            "mod", "package", "mesh_path", "weapon_type", "fragments", "parts", "options",
            "package_file", "schema_version", "balances", "extra_packages", "materials",
            "fire_sound", "objects", "freeze", "card_icon", "alt_fire", "rage",
        }
        spec = cls(
            name=str(_req(mod, "name", "spec.mod")),
            package=str(_req(data, "package", "spec")),
            mesh_path=str(_req(data, "mesh_path", "spec")),
            weapon_type=str(_req(data, "weapon_type", "spec")),
            fragments=[FragmentSpec.from_dict(f) for f in data.get("fragments", [])],
            parts=[PartSpec.from_dict(p) for p in data.get("parts", [])],
            balances=[BalanceSpec.from_dict(b) for b in data.get("balances", [])],
            extra_packages=[ExtraPackage.from_dict(p) for p in data.get("extra_packages", [])],
            materials=[MaterialSpec.from_dict(m) for m in data.get("materials", [])],
            objects=[ObjectSpec.from_dict(o) for o in data.get("objects", [])],
            freeze=FreezeSpec.from_dict(data["freeze"]) if data.get("freeze") else None,
            card_icon=_card_icon(data.get("card_icon")),
            fire_sound=(FireSoundSpec.from_dict(data["fire_sound"])
                        if data.get("fire_sound") else None),
            alt_fire=(AltFireSpec.from_dict(data["alt_fire"]) if data.get("alt_fire") else None),
            rage=RageSpec.from_dict(data["rage"]) if data.get("rage") is not None else None,
            options=Options.from_dict(data.get("options", {})),
            author=str(mod.get("author", "") or ""),
            version=str(mod.get("version", "0.1.0") or "0.1.0"),
            description=str(mod.get("description", "") or ""),
            package_file=(str(data["package_file"]) if data.get("package_file") else None),
            source_dir=Path(source_dir) if source_dir is not None else None,
            extra={k: v for k, v in data.items() if k not in known},
        )
        spec.validate()
        return spec

    def validate(self) -> None:
        """Structural checks only; catalog collisions are ``bl2_lint``'s job."""
        if self.options.own_gestalt and not self.balances:
            raise SpecError("options.own_gestalt needs balances[]: a weapon reaches its own "
                            "gestalt through its balance's own WeaponTypeDefinition")
        if not self.fragments:
            raise SpecError("spec: at least one fragment is required")
        if not self.parts:
            raise SpecError("spec: at least one part is required")
        if not self.name.replace("_", "").replace("-", "").isalnum():
            raise SpecError(
                f"spec.mod.name {self.name!r} must be alphanumeric (it is a folder name)"
            )
        names = [f.name for f in self.fragments]
        if len(set(names)) != len(names):
            raise SpecError(f"spec: duplicate fragment names in {names}")
        part_names = [p.part_name for p in self.parts]
        if len(set(part_names)) != len(part_names):
            raise SpecError(f"spec: duplicate part names in {part_names}")
        material_paths = [m.path for m in self.materials]
        if len(set(material_paths)) != len(material_paths):
            raise SpecError(f"spec: duplicate material paths in {material_paths}")
        for material in self.materials:
            if material.path == material.parent:
                raise SpecError(f"material {material.name!r}: its path is its own parent")
        package_names = [p.name for p in self.extra_packages]
        if len(set(package_names)) != len(package_names):
            raise SpecError(f"spec: duplicate extra_packages {package_names}")
        if self.package_stem in package_names:
            raise SpecError(f"extra_packages names the spec's own package {self.package_stem!r}")
        if self.fire_sound is not None and not self.balances:
            raise SpecError("fire_sound needs a balance: the shot hook picks our weapons "
                            "out by their BalanceDefinition")
        if self.alt_fire is not None and not self.balances:
            raise SpecError("alt_fire needs a balance: the trigger hooks pick our weapons "
                            "out by their BalanceDefinition")
        if self.rage is not None and not self.balances:
            raise SpecError("rage needs a balance: hits and kills count only with our weapon")
        if self.alt_fire is not None and self.alt_fire.sound and self.fire_sound is None:
            raise SpecError("alt_fire.sound rides on fire_sound (its winsound player and "
                            "stock-shot mute); add fire_sound too")
        listed_in_balances: set[str] = set()
        balance_paths = [b.path for b in self.balances]
        if len(set(balance_paths)) != len(balance_paths):
            raise SpecError(f"spec: duplicate balance paths in {balance_paths}")
        for balance in self.balances:
            where = f"balance {balance.name!r}"
            if balance.path == balance.template_balance:
                raise SpecError(f"{where}: its path is its own template_balance")
            if not balance.part_lists:
                raise SpecError(f"{where}: part_lists is empty; it would be a plain copy of "
                                f"{balance.template_balance}")
            for fld, refs in balance.part_lists.items():
                for ref in refs:
                    if "." in ref:
                        continue  # full path of a stock part; the resolver checks the catalog
                    if ref not in part_names:
                        raise SpecError(
                            f"{where}: part_lists[{fld!r}] names {ref!r}, which is neither a "
                            f"part of this spec ({', '.join(part_names)}) nor a full object path"
                        )
                    listed_in_balances.add(ref)
            if balance.title is not None:
                if not balance.title.on_parts:
                    raise SpecError(f"{where}: the title names no on_parts, so no weapon would "
                                    "ever carry it")
                for ref in balance.title.on_parts:
                    if ref not in part_names:
                        raise SpecError(f"{where}: title.on_parts names {ref!r}, which is not a "
                                        f"part of this spec ({', '.join(part_names)})")
        for part in self.parts:
            if part.fragment is None:
                if not part.template_part:
                    raise SpecError(
                        f"part {part.part_name!r} has no fragment (it draws nothing), so it "
                        "must name the non-gestalt template_part it is cloned from"
                    )
            elif part.fragment not in names:
                raise SpecError(
                    f"part {part.part_name!r} references fragment {part.fragment!r}, "
                    f"which this spec does not define ({', '.join(names)})"
                )
            if not part.register_in and part.part_name not in listed_in_balances:
                raise SpecError(
                    f"part {part.part_name!r} has an empty register_in and no balance of this "
                    "spec lists it: a part that is in no part list never rolls on any weapon (F5)"
                )
        ranges: list[tuple[int, int, str]] = []
        for frag in self.fragments:
            if frag.num_primitives <= 0:
                raise SpecError(f"fragment {frag.name!r}: num_primitives must be > 0")
            start = frag.first_index
            ranges.append((start, start + 3 * frag.num_primitives, frag.name))
        ranges.sort()
        for (a_start, a_end, a_name), (b_start, b_end, b_name) in zip(ranges, ranges[1:]):
            if b_start < a_end:
                raise SpecError(
                    f"fragments {a_name!r} [{a_start}, {a_end}) and {b_name!r} "
                    f"[{b_start}, {b_end}) overlap in the index buffer"
                )
        if self.options.test_harness and self.parts[0].fragment is None:
            raise SpecError(
                f"parts[0] ({self.parts[0].part_name!r}) has no fragment, but the test harness "
                "drives parts[0] and reports its fragment; put a fragment-bearing part first"
            )
        force = self.options.force_template_fragment_on_map_load
        if force is not None and force in names:
            raise SpecError(
                "options.force_template_fragment_on_map_load names one of this spec's own "
                f"fragments ({force!r}); it must name the stock fragment to restore"
            )
        view = self.options.harness_capture_view
        if view is not None and view not in ("first", "third"):
            raise SpecError(
                f"options.harness_capture_view must be \"first\" or \"third\", got {view!r}"
            )
        if self.options.harness_pose not in ("idle", "ads", "hip", "inventory"):
            raise SpecError(
                "options.harness_pose must be \"idle\", \"ads\", \"hip\" or \"inventory\", "
                f"got {self.options.harness_pose!r}"
            )
        if self.options.harness_timing not in ("ticks", "seconds"):
            raise SpecError(
                f"options.harness_timing must be \"ticks\" or \"seconds\", got {self.options.harness_timing!r}"
            )
        keys = tuple(self.options.harness_keys)
        if len(keys) != 4 or len(set(keys)) != 4 or not all(keys):
            raise SpecError(
                "options.harness_keys must be four distinct keys (grant, equip newest, "
                f"equip second newest, toggle view), got {list(keys)!r}"
            )
        mission = self.options.harness_mission
        if "." not in mission or mission.startswith(".") or mission.endswith("."):
            raise SpecError(
                f"options.harness_mission must be a full object path, got {mission!r}"
            )
        if self.options.harness_equip not in ("newest", "second_newest"):
            raise SpecError(
                "options.harness_equip must be \"newest\" or \"second_newest\", got "
                f"{self.options.harness_equip!r}"
            )
        status_path = self.options.status_path
        if status_path is not None and not Path(status_path).is_absolute():
            raise SpecError(
                f"options.status_path {status_path!r} must be absolute: it is baked into the "
                "generated mod, whose working directory is the game's, not the spec's"
            )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema_version": SPEC_SCHEMA_VERSION,
            "mod": {
                "name": self.name,
                "author": self.author,
                "version": self.version,
                "description": self.description,
            },
            "package": self.package,
            "mesh_path": self.mesh_path,
            "weapon_type": self.weapon_type,
            "package_file": self.package_file,
            "fragments": [f.to_dict() for f in self.fragments],
            "parts": [p.to_dict() for p in self.parts],
            "options": self.options.to_dict(),
        }
        if self.balances:
            out["balances"] = [b.to_dict() for b in self.balances]
        if self.extra_packages:
            out["extra_packages"] = [p.to_dict() for p in self.extra_packages]
        if self.materials:
            out["materials"] = [m.to_dict() for m in self.materials]
        if self.objects:
            out["objects"] = [o.to_dict() for o in self.objects]
        if self.freeze is not None:
            out["freeze"] = self.freeze.to_dict()
        if self.card_icon is not None:
            out["card_icon"] = dict(self.card_icon)
        if self.fire_sound is not None:
            out["fire_sound"] = self.fire_sound.to_dict()
        if self.alt_fire is not None:
            out["alt_fire"] = self.alt_fire.to_dict()
        if self.rage is not None:
            out["rage"] = self.rage.to_dict()
        return out

    @property
    def package_stem(self) -> str:
        """``PipelineMeshes.upk`` and ``PipelineMeshes`` both -> ``PipelineMeshes``."""
        stem = self.package
        if stem.lower().endswith(".upk"):
            stem = stem[:-4]
        return stem

    def resolved_package_file(self) -> Path | None:
        """``package_file`` made absolute against the spec's own directory."""
        return self._resolve(self.package_file)

    def _resolve(self, file: str | None) -> Path | None:
        if not file:
            return None
        path = Path(file)
        if not path.is_absolute() and self.source_dir is not None:
            path = self.source_dir / path
        return path

    def resolved_fire_sound_file(self) -> Path | None:
        """``fire_sound.wav`` made absolute against the spec's own directory."""
        return self._resolve(self.fire_sound.wav) if self.fire_sound else None

    def resolved_alt_fire_sound_file(self) -> Path | None:
        """``alt_fire.sound`` made absolute against the spec's own directory."""
        return self._resolve(self.alt_fire.sound) if self.alt_fire and self.alt_fire.sound else None

    def resolved_extra_package_files(self) -> list[tuple[str, Path | None]]:
        """``(name, absolute file or None)`` for every extra package."""
        return [(p.name, self._resolve(p.file)) for p in self.extra_packages]


def load_spec(path: Path | str) -> Spec:
    """Read a spec JSON file (``docs/PARTGEN_SPEC.md``)."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        return Spec.from_dict(json.load(handle), source_dir=path.resolve().parent)


# ---------------------------------------------------------------------- the Armory (M10)
@dataclass
class ArmoryWeapon:
    """One weapon inside an Armory: an existing weapon spec plus its id and label."""

    id: str
    spec: Path
    label: str
    sidecar: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "spec": str(self.spec), "label": self.label}
        if self.sidecar is not None:
            out["sidecar"] = str(self.sidecar)
        return out


@dataclass
class ArmorySpec:
    """``armory.json``: several weapon specs composed into one SDK mod with a spawn console.

    A weapon's own spec stays the single source of truth for its parts, balance, title,
    material and packages; the Armory adds nothing to a weapon, it composes them.
    """

    name: str
    weapons: list[ArmoryWeapon]
    author: str = ""
    version: str = "0.1.0"
    description: str = ""
    spawn_key: str = "F5"
    spawn_mission: str = "GD_Episode01.M_Ep1_Champion"
    source_dir: Path | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], source_dir: Path | None = None) -> ArmorySpec:
        mod = data.get("mod") or {}
        if not isinstance(mod, dict) or not mod.get("name"):
            raise SpecError("armory: mod.name is required")
        base = Path(source_dir) if source_dir is not None else Path(".")
        weapons: list[ArmoryWeapon] = []
        for entry in data.get("weapons") or []:
            if not isinstance(entry, dict) or not entry.get("id") or not entry.get("spec"):
                raise SpecError(f"armory: every weapon needs an id and a spec path, got {entry!r}")
            sidecar = entry.get("sidecar")
            weapons.append(ArmoryWeapon(
                id=str(entry["id"]),
                spec=(base / str(entry["spec"])).resolve(),
                label=str(entry.get("label") or entry["id"]),
                sidecar=(base / str(sidecar)).resolve() if sidecar else None,
            ))
        spawn = data.get("spawn") or {}
        armory = cls(
            name=str(mod["name"]),
            weapons=weapons,
            author=str(mod.get("author", "")),
            version=str(mod.get("version", "0.1.0")),
            description=str(mod.get("description", "")),
            spawn_key=str(spawn.get("keybind", "F5") or "F5"),
            spawn_mission=str(spawn.get("mission", "GD_Episode01.M_Ep1_Champion")
                              or "GD_Episode01.M_Ep1_Champion"),
            source_dir=source_dir,
        )
        armory.validate()
        return armory

    def validate(self) -> None:
        if not self.weapons:
            raise SpecError("armory: no weapons")
        ids = [w.id for w in self.weapons]
        if len(set(ids)) != len(ids):
            raise SpecError(f"armory: weapon ids must be unique, got {ids}")
        for weapon in self.weapons:
            if not weapon.id.isidentifier() or weapon.id.startswith("_"):
                raise SpecError(
                    f"armory: weapon id {weapon.id!r} must be a Python identifier (it becomes "
                    "weapons/<id>.py)"
                )
            if not weapon.spec.is_file():
                raise SpecError(f"armory: weapon {weapon.id}: spec not found: {weapon.spec}")
            if weapon.sidecar is not None and not weapon.sidecar.is_file():
                raise SpecError(f"armory: weapon {weapon.id}: sidecar not found: {weapon.sidecar}")
        labels = [w.label for w in self.weapons]
        if len(set(labels)) != len(labels):
            raise SpecError(f"armory: weapon labels must be unique, got {labels}")
        if not self.spawn_key:
            raise SpecError("armory: spawn.keybind must not be empty")
        mission = self.spawn_mission
        if "." not in mission or mission.startswith(".") or mission.endswith("."):
            raise SpecError(f"armory: spawn.mission must be a full object path, got {mission!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mod": {"name": self.name, "author": self.author, "version": self.version,
                    "description": self.description},
            "weapons": [w.to_dict() for w in self.weapons],
            "spawn": {"keybind": self.spawn_key, "mission": self.spawn_mission},
        }


def is_armory(data: dict[str, Any]) -> bool:
    """An armory JSON has a ``weapons`` list; a weapon spec has ``fragments``/``parts``."""
    return isinstance(data, dict) and "weapons" in data and "parts" not in data


def load_armory(path: Path | str) -> ArmorySpec:
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        return ArmorySpec.from_dict(json.load(handle), source_dir=path.resolve().parent)
