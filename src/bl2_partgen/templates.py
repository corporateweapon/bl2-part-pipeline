"""The code generator proper: a :class:`~bl2_partgen.resolve.ResolvedSpec` in, the
text of an SDK mod out.

Deliberately dumb: ``string.Template`` substitution into a handful of section
templates, no AST building. The generated file is meant to be *read and debugged
by a human*, so the templates carry the comments that explain why each step is
there, and every fact that varies is a module-level constant at the top of the
generated file rather than something buried in a function.

``$`` is the substitution marker, so the templates must never contain a literal
dollar sign (the generated code does not need one).
"""

from __future__ import annotations

import pprint
import textwrap
from pathlib import Path
from string import Template
from typing import Any

from .alt_fire_template import ALT_FIRE_SECTION, ALT_TEST_SECTION, TRACE_SECTION
from .rage_template import HUD_TEST_SECTION, RAGE_SECTION, RAGE_TEST_SECTION
from .resolve import ResolvedSpec

__all__ = ["render_mod", "render_pyproject", "render_readme", "render_settings"]

#: the fields of ``WeaponDefinitionData`` that hold a part (see docs/PARTGEN_SPEC.md)
SLOT_FIELDS = (
    "BodyPartDefinition",
    "GripPartDefinition",
    "BarrelPartDefinition",
    "SightPartDefinition",
    "StockPartDefinition",
    "ElementalPartDefinition",
    "Accessory1PartDefinition",
    "Accessory2PartDefinition",
    "MaterialPartDefinition",
    "PrefixPartDefinition",
    "TitlePartDefinition",
)

#: the mission whose reward the test harness hijacks to grant a weapon
HARNESS_MISSION = "GD_Episode01.M_Ep1_Champion"


# --------------------------------------------------------------------- literals
def _str_tuple(values: list[str], indent: str) -> str:
    """``("a", "b")`` on one line, or one per line when it gets long."""
    if not values:
        return "()"
    flat = ", ".join(f'"{v}"' for v in values)
    if len(flat) + len(indent) < 88:
        return f"({flat},)" if len(values) == 1 else f"({flat})"
    body = "".join(f'\n{indent}    "{v}",' for v in values)
    return f"({body}\n{indent})"


def _fragments_literal(resolved: ResolvedSpec) -> str:
    rows = []
    for frag in resolved.fragments:
        # the key is only emitted when the spec uses it, so specs that do not (every
        # spec written before M6) keep producing byte-identical mods
        overrides = ""
        if frag.socket_overrides:
            body = "".join(
                f'\n                           "{name}": ({loc[0]!r}, {loc[1]!r}, {loc[2]!r}),'
                for name, loc in sorted(frag.socket_overrides.items()))
            overrides = f',\n     "socket_overrides": {{{body}\n     }}'
        if frag.bounds_override is not None:
            b = frag.bounds_override
            overrides += (
                f',\n     "bounds_override": {{"origin": {tuple(b["origin"])!r}, '
                f'"extent": {tuple(b["extent"])!r}, "radius": {b["radius"]!r}}}'
            )
        rows.append(
            f'    {{"name": "{frag.name}", "template": "{frag.template_fragment}",\n'
            f'     "first_index": {frag.first_index}, "num_primitives": '
            f'{frag.num_primitives}, "dz": {float(frag.dz)!r},\n'
            f'     "sockets": {_str_tuple(frag.sockets, "     ")},\n'
            f'     "dz_sockets": {_str_tuple(frag.dz_sockets, "     ")}{overrides}}},'
        )
    return "\n".join(rows)


def _parts_literal(resolved: ResolvedSpec) -> str:
    rows = []
    for part in resolved.parts:
        registrations = "".join(
            f'\n      # appended to the runtime part list\n'
            f'      #   {reg.list_path}\n'
            f'      {{"balance": "{reg.balance}",\n'
            f'       "field": "{reg.field}"}},'
            for reg in part.registrations
        )
        slot = f'"{part.slot}"' if part.slot else "None"
        fragment = f'"{part.fragment}"' if part.fragment is not None else "None"
        # only emitted when the spec uses it (M7 stats), like socket_overrides
        overrides = ""
        if part.overrides is not None:
            body = pprint.pformat(part.overrides.to_dict(), width=84, sort_dicts=False)
            body = textwrap.indent(body, "         ").lstrip()
            overrides = f',\n     "overrides": {body}'
        rows.append(
            f'    {{"name": "{part.part_name}", "slot": {slot}, '
            f'"fragment": {fragment},\n'
            f'     "path": "{part.part_path}",\n'
            f'     "outer": "{part.outer}",\n'
            f'     "template_part": "{part.template_part}",\n'
            f'     "register_in": ({registrations}\n     ){overrides}}},'
        )
    return "\n".join(rows)


def _balances_literal(resolved: ResolvedSpec) -> str:
    """``BALANCES = (...)`` plus the path sets the hooks key on; empty for pre-M7 specs."""
    if not resolved.balances:
        return ""
    rows = []
    for bal in resolved.balances:
        lists = "".join(
            f'\n         "{fld}": {_str_tuple(paths, "         ")},'
            for fld, paths in bal.part_lists.items()
        )
        if bal.title is not None:
            t = bal.title
            title = (
                f'{{"name": {t.spec.name!r}, "path": {t.path!r},\n'
                f'               "outer": {t.outer!r},\n'
                f'               "template": {t.template!r},\n'
                f'               "part_name": {t.part_name!r},\n'
                f'               "red_text": {t.red_text!r},\n'
                f'               "name_is_unique": {t.name_is_unique!r},\n'
                f'               "on_parts": {_str_tuple(t.on_part_paths, "               ")}}}'
            )
        else:
            title = "None"
        rows.append(
            f'    {{"name": {bal.name!r}, "path": {bal.path!r},\n'
            f'     "outer": {bal.outer!r},\n'
            f'     "template": {bal.template_balance!r},\n'
            f'     "collection_name": {bal.collection_name!r},\n'
            f'     # field -> exactly these parts; fields not named keep the template\'s rows\n'
            f'     "part_lists": {{{lists}\n     }},\n'
            f'     "title": {title},\n'
            f'     "suppress_prefix": {bal.suppress_prefix!r},\n'
            f'     "pools": {_str_tuple(bal.pools, "     ")}}},'
        )
    body = "\n".join(rows)
    return (
        "\n\n#: runtime WeaponBalanceDefinitions: the weapon as its own gun, with its own part\n"
        "#: lists (M7). Constructed at the menu tick after the parts, GC-rooted like them.\n"
        "BALANCES: tuple[dict[str, Any], ...] = (\n"
        f"{body}\n"
        ")\n"
        "OUR_BALANCE_PATHS = frozenset(bal[\"path\"] for bal in BALANCES)\n"
        "#: titles are WeaponNamePartDefinitions, i.e. parts: the save/validate hooks key on\n"
        "#: them exactly like the mesh parts (TitlePartDefinition is a slot field)\n"
        "OUR_TITLE_PATHS = frozenset(bal[\"title\"][\"path\"] for bal in BALANCES if bal[\"title\"])\n"
        "#: balances whose weapons carry no prefix (the weapon type\'s fallback \"Assault\" included)\n"
        "SUPPRESS_PREFIX_BALANCES = frozenset(bal[\"path\"] for bal in BALANCES if bal[\"suppress_prefix\"])"
    )


def _materials_literal(resolved: ResolvedSpec) -> str:
    """``EXTRA_PACKAGES`` / ``MATERIALS``; empty when the spec has neither (pre-M7)."""
    out = ""
    if resolved.extra_packages:
        out += (
            "\n\n#: further packages loaded (and rooted, F17) at the menu tick, before anything\n"
            "#: that references their objects: a loose Texture2D package, say\n"
            f"EXTRA_PACKAGES = {_str_tuple(list(resolved.extra_packages), '')}"
        )
    if resolved.materials:
        rows = []
        for mat in resolved.materials:
            textures = "".join(f'\n         {k!r}: {v!r},' for k, v in mat.texture_parameters.items())
            vectors = "".join(f'\n         {k!r}: ({", ".join(repr(float(x)) for x in v)}),'
                              for k, v in mat.vector_parameters.items())
            scalars = "".join(f'\n         {k!r}: {float(v)!r},' for k, v in mat.scalar_parameters.items())
            rows.append(
                f'    {{"name": {mat.name!r}, "path": {mat.path!r},\n'
                f'     "outer": {mat.outer!r},\n'
                f'     "parent": {mat.parent!r},\n'
                f'     "texture_parameters": {{{textures}{chr(10) + "     " if textures else ""}}},\n'
                f'     "vector_parameters": {{{vectors}{chr(10) + "     " if vectors else ""}}},\n'
                f'     "scalar_parameters": {{{scalars}{chr(10) + "     " if scalars else ""}}}}},'
            )
        out += (
            "\n\n#: runtime MaterialInstanceConstants: an empty MIC parented to a stock one, with\n"
            "#: the parameters below set on it (the way skin mods do it). Constructed at the\n"
            "#: menu tick before the parts, so a part's Material can point at one.\n"
            "MATERIALS: tuple[dict[str, Any], ...] = (\n" + "\n".join(rows) + "\n)"
        )
    return out


_GESTALT_LOAD_SHARED = '''    gestalt = active_gestalt()
    gestalt.GestaltSkeletalMesh = mesh
    return mesh


def active_gestalt() -> UObject:
    """The stock gestalt definition; load_mesh_package re-points it at our mesh."""
    return unrealsdk.find_object("GestaltSkeletalMeshDefinition", GESTALT_DEF)


'''

_GESTALT_LOAD_OWN = '''    active_gestalt(package).GestaltSkeletalMesh = mesh
    return mesh


_active_gestalt: UObject | None = None


def active_gestalt(package: UObject | None = None) -> UObject:
    """The gestalt definition our fragments go into and our weapons draw from.

    own_gestalt: a clone of the stock definition inside our package. The stock one is never
    re-pointed; only weapons of our own WeaponTypeDefinition (register_balance) draw from it.
    """
    global _active_gestalt
    if _active_gestalt is not None:
        return _active_gestalt
    stock = unrealsdk.find_object("GestaltSkeletalMeshDefinition", GESTALT_DEF)
    outer = package or unrealsdk.find_object("Package", PACKAGE)
    own = _find("GestaltSkeletalMeshDefinition", f"{PACKAGE}.{OWN_GESTALT_NAME}")
    if own is None:
        own = unrealsdk.construct_object(
            "GestaltSkeletalMeshDefinition", outer, OWN_GESTALT_NAME, 0, stock)
    keep_alive(own)
    _active_gestalt = own
    return own


'''

_OWN_GESTALT_CONSTS = '''
#: own_gestalt: this weapon draws from its OWN clone of GESTALT_DEF (named below, inside
#: PACKAGE) through its own WeaponTypeDefinition, so the stock definition is never re-pointed
#: and any number of weapons can share one host type, each with its own mesh package
OWN_GESTALT_NAME = "GestaltDef_%s"'''

_OWN_TYPE_HOOK = '''


# --------------------------------------------------------------------- own_gestalt type (F34)
_type_forced: dict[str, int] = {}


@hook("WillowGame.WillowWeapon:InitializeInternal", Type.PRE)
def on_weapon_type_pre(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    """Put our WeaponTypeDefinition on every weapon of our balance before it is assembled.

    The type is where the weapon gets its gestalt; generated weapons arrive with the stock
    type even when our balance and its part lists name ours (laser runs 6-8), so the mesh
    assembled from the stock gestalt, where our fragment names do not exist: empty hands.
    """
    try:
        definition = obj.DefinitionData
        balance = definition.BalanceDefinition
        if balance is None:
            return
        own_type = OUR_TYPE_BY_BALANCE.get(balance._path_name())
        if own_type is None:
            return
        current = definition.WeaponTypeDefinition
        if current is not own_type:
            definition.WeaponTypeDefinition = own_type
            key = current._path_name() if current is not None else "None"
            _type_forced[key] = _type_forced.get(key, 0) + 1
            _write({"own_type_forced": dict(_type_forced), "weapon": obj._path_name()})
    except Exception as ex:  # noqa: BLE001
        _write({"own_type_forced": "error", "error": repr(ex)})'''

_FREEZE_SECTION = '''

# --------------------------------------------------------------------- freeze and shatter
#: BL2 has no freeze (TPS's is native), so it lives here: cryo applications are counted per
#: pawn; enough of them inside the window stop the pawn, its controller and its weapon with
#: CustomTimeDilation and apply the frozen status; a pawn that dies frozen is gibbed.
FREEZE: dict[str, Any] = $freeze_literal

_stacks: dict[int, list[float]] = {}
_frozen: dict[int, dict[str, Any]] = {}
_freeze_log: dict[str, int] = {"applied": 0, "frozen": 0, "thawed": 0, "shattered": 0}


def _is_player(pawn: UObject) -> bool:
    controller = getattr(pawn, "Controller", None)
    return controller is not None and "PlayerController" in controller.Class.Name


def _dilate(pawn: UObject, value: float | None, saved: dict[str, float] | None = None) -> dict[str, float]:
    """Set (value) or restore (saved) CustomTimeDilation on pawn, controller and weapon."""
    out: dict[str, float] = {}
    for key, actor in (("pawn", pawn), ("controller", getattr(pawn, "Controller", None)),
                       ("weapon", getattr(pawn, "Weapon", None))):
        if actor is None:
            continue
        out[key] = float(actor.CustomTimeDilation)
        actor.CustomTimeDilation = value if value is not None else (saved or {}).get(key, 1.0)
    return out


def _spawn_fx(which: str, pawn: UObject) -> Any:
    """Spawn one of FREEZE["fx"] at the pawn (not attached: an attached emitter would run at
    the frozen pawn's time dilation); its bone-socket modules follow the pawn's skeleton."""
    path = (FREEZE.get("fx") or {}).get(which)
    if not path:
        return None
    try:
        template = _find("ParticleSystem", path)
        if template is None:
            return None
        pool = pawn.WorldInfo.MyEmitterPool
        component = pool.SpawnEmitter(template, pawn.Location, pawn.Rotation)
        if component is not None:
            component.SetActorParameter(FREEZE["fx"].get("bone_param", "BoneSocketActor"), pawn)
        _freeze_log[f"fx_{which}"] = _freeze_log.get(f"fx_{which}", 0) + 1
        return component
    except Exception as ex:  # noqa: BLE001 - effects must never break the freeze
        _write({"freeze": f"fx_{which}", "error": repr(ex)})
        return None


def freeze_pawn(pawn: UObject, instigator: Any = None) -> None:
    key = id(pawn)
    if key in _frozen:
        return
    saved = _dilate(pawn, FREEZE["time_dilation"])
    materials = _ice_on(pawn)
    # the frozen status is applied on the next frame, from freeze_tick: applying it from
    # inside the ApplyStatusEffect hook that detected the freeze overwrote the cryo entry
    # still being applied (probe runs 15-17: frozen never listed, cryo ended at the thaw)
    _frozen[key] = {"pawn": pawn, "saved": saved, "materials": materials,
                    "fx": _spawn_fx("frozen", pawn),
                    "until": time.time() + FREEZE["seconds"],
                    "instigator": instigator, "status_pending": bool(FREEZE["frozen_status"])}
    _stacks.pop(key, None)
    _freeze_log["frozen"] += 1
    freeze_tick.enable()
    _write({"freeze": "frozen", "pawn": pawn._path_name(), **_freeze_log})


def _ice_on(pawn: UObject) -> list[Any] | None:
    """Every element of the pawn's mesh to the ice material; returns the originals."""
    if not FREEZE.get("ice_material"):
        return None
    ice = _find("MaterialInterface", FREEZE["ice_material"])
    mesh = getattr(pawn, "Mesh", None)
    if ice is None or mesh is None:
        return None
    saved = [mesh.GetMaterial(i) for i in range(int(mesh.GetNumElements()))]
    for i in range(len(saved)):
        mesh.SetMaterial(i, ice)
    return saved


def _ice_off(pawn: UObject, saved: list[Any] | None) -> None:
    mesh = getattr(pawn, "Mesh", None)
    if not saved or mesh is None:
        return
    for i, material in enumerate(saved):
        mesh.SetMaterial(i, material)


def thaw_pawn(key: int, reason: str) -> None:
    entry = _frozen.pop(key, None)
    if entry is None:
        return
    try:
        _dilate(entry["pawn"], None, entry["saved"])
    except Exception:  # noqa: BLE001 - the pawn may be gone
        pass
    try:
        _ice_off(entry["pawn"], entry.get("materials"))
    except Exception:  # noqa: BLE001
        pass
    try:
        if entry.get("fx") is not None:
            entry["fx"].DeactivateSystem()
        if reason == "timer":
            _spawn_fx("thaw", entry["pawn"])
    except Exception:  # noqa: BLE001
        pass
    _freeze_log["thawed"] += 1
    _write({"freeze": "thawed", "reason": reason, "frozen_status_via": entry.get("status_via"),
            **_freeze_log})


def _apply_status(component: UObject, status: UObject, instigator: Any) -> str:
    """Apply a status effect directly. The real entry point: CheatApplyStatusEffect exists
    in the shipping build but does nothing (probe runs 14 and 19)."""
    component.ApplyStatusEffect(StatusEffectDefinition=status, InstigatedBy=instigator,
                                DamageType=unrealsdk.find_class("WillowDamageType"))
    return "ApplyStatusEffect"


@hook("WillowGame.StatusEffectsComponent:ApplyStatusEffect", Type.POST)
def on_status_applied(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    try:
        status = args.StatusEffectDefinition
        if status is None or status._path_name() != FREEZE["status"]:
            return
        count_application(obj.Owner, args.InstigatedBy)
    except Exception as ex:  # noqa: BLE001
        _write({"freeze": "error", "error": repr(ex)})


def count_application(pawn: Any, instigator: Any) -> None:
    """One cryo application on ``pawn``; freezes it at FREEZE["stacks"] inside the window."""
    if pawn is None or not hasattr(pawn, "StatusEffectComp"):
        return
    if _is_player(pawn) and not FREEZE["affect_players"]:
        return
    _freeze_log["applied"] += 1
    key, now = id(pawn), time.time()
    if key in _frozen:
        return
    stamps = [t for t in _stacks.get(key, []) if now - t <= FREEZE["window_s"]] + [now]
    _stacks[key] = stamps
    if len(stamps) >= FREEZE["stacks"]:
        freeze_pawn(pawn, instigator)


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def freeze_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    now = time.time()
    for key, entry in list(_frozen.items()):
        if entry.get("status_pending") or (FREEZE["frozen_status"]
                                           and now >= entry.get("refresh_at", now + 1.0)
                                           and now < entry["until"] - 0.25):
            # refreshed every second, only inside the freeze: statuses on a dilated pawn are
            # dropped ~2.4 s in (probe runs 20-22, player as target) and a refresh after
            # the thaw left the vulnerability on for 3 s more
            entry["status_pending"] = False
            entry["refresh_at"] = now + 1.0
            try:
                status = _find("StatusEffectDefinition", FREEZE["frozen_status"])
                component = getattr(entry["pawn"], "StatusEffectComp", None)
                if status is not None and component is not None:
                    entry["status_via"] = _apply_status(component, status, entry["instigator"])
            except Exception as ex:  # noqa: BLE001
                _write({"freeze": "frozen_status", "error": repr(ex)})
        if now >= entry["until"]:
            thaw_pawn(key, "timer")
    if not _frozen:
        freeze_tick.disable()


def _on_died(obj: UObject, args: WrappedStruct) -> None:
    key = id(obj)
    if key not in _frozen:
        return
    thaw_pawn(key, "died")
    if not FREEZE["shatter"]:
        return
    _spawn_fx("shatter", obj)
    try:
        dtype = _find("WillowDamageTypeDefinition", FREEZE["shatter_damage_type"] or "")
        gibbed = obj.TryFullBodyGib(Damage=100000, InstigatedBy=args.Killer,
                                    DamageType=args.DamageType, DamageTypeDefinition=dtype,
                                    HitLocation=args.HitLocation,
                                    Momentum=unrealsdk.make_struct("Vector"))
        _freeze_log["shattered"] += 1
        _write({"freeze": "shatter", "gibbed": repr(gibbed), **_freeze_log})
    except Exception as ex:  # noqa: BLE001
        _write({"freeze": "shatter", "error": repr(ex)})


@hook("WillowGame.WillowAIPawn:Died", Type.PRE)
def on_ai_died(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    _on_died(obj, args)


@hook("WillowGame.WillowPawn:Died", Type.PRE)
def on_pawn_died(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    _on_died(obj, args)'''

_PROBE_SECTION = '''

# --------------------------------------------------------------------- status probe (harness)
#: apply a status effect to the PLAYER at map load and sample what it does (the loop's save
#: has no enemies); the phase machine starts after the last sample
STATUS_PROBE: dict[str, Any] = $probe_literal
_probe: dict[str, Any] = {}


def _probe_target() -> UObject:
    """The player, or the nearest living AI pawn (Sanctuary's NPCs, in the loop's save)."""
    player = get_pc().Pawn
    if STATUS_PROBE.get("target") != "nearest_ai":
        return player
    best, best_d = None, None
    here = player.Location
    seen: dict[str, int] = {}
    for pawn in unrealsdk.find_all("WillowPawn", exact=False):
        try:
            if pawn is player or pawn.Class.Name.startswith("Default__"):
                continue
            seen[pawn.Class.Name] = seen.get(pawn.Class.Name, 0) + 1
            if "Default__" in pawn._path_name() or pawn.StatusEffectComp is None                     or getattr(pawn, "Controller", None) is None or _is_player(pawn):
                continue
            d = ((pawn.Location.X - here.X) ** 2 + (pawn.Location.Y - here.Y) ** 2
                 + (pawn.Location.Z - here.Z) ** 2)
        except Exception:  # noqa: BLE001 - class default objects and the like
            continue
        if best_d is None or d < best_d:
            best, best_d = pawn, d
    _probe["candidates"] = seen
    if best is None:
        raise LookupError(f"no AI pawn with a controller yet (seen {seen})")
    return best


def _probe_sample(label: str) -> dict[str, Any]:
    pawn = _probe["pawn"]
    component = pawn.StatusEffectComp
    row: dict[str, Any] = {"at": label, "t": round(time.time() - _probe["t0"], 2)}
    try:
        row["GroundSpeedModifiers"] = len(pawn.GroundSpeedModifierStack)
    except Exception as ex:  # noqa: BLE001
        row["GroundSpeedModifiers"] = repr(ex)
    try:
        mesh = pawn.Mesh
        first = mesh.GetMaterial(0) if int(mesh.GetNumElements()) else None
        row["material0"] = first._path_name() if first is not None else None
    except Exception as ex:  # noqa: BLE001
        row["material0"] = repr(ex)
    for name in ("GroundSpeed", "GroundSpeedBaseValue", "CustomTimeDilation"):
        try:
            row[name] = float(getattr(pawn, name))
        except Exception as ex:  # noqa: BLE001
            row[name] = repr(ex)
    try:
        row["active"] = [
            {"definition": (e.StatusEffectDefinition._path_name()
                            if getattr(e, "StatusEffectDefinition", None) is not None else None),
             "detail": repr(e)[:600]}
            for e in component.ActiveStatusEffects]
    except Exception as ex:  # noqa: BLE001
        row["active"] = repr(ex)
    return row


def _probe_start() -> None:
    _probe.clear()
    _probe.update({"t0": time.time(), "applied": 0, "samples": [], "next_sample": 0,
                   "via": None})
    _probe["pawn"] = None
    _probe["armed"] = time.time()
    probe_tick.enable()


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def probe_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    try:
        if time.time() - _probe["armed"] < STATUS_PROBE.get("start_after_s", 0.0):
            return  # the loading movie is still up for a while after the map-load hook
        if _probe["pawn"] is None:
            # NPCs spawn after the map-load hook; look again every tick for up to 10 s
            try:
                _probe["pawn"] = _probe_target()
            except LookupError:
                if time.time() - _probe["armed"] < STATUS_PROBE.get("start_after_s", 0.0) + 10.0:
                    return
                raise
            _probe["target"] = _probe["pawn"]._path_name()
            if STATUS_PROBE.get("look_at"):
                _probe["look"] = _probe_look(_probe["pawn"])
            if STATUS_PROBE.get("behind_view"):
                get_pc().SetBehindView(True)  # third person: the frozen player is in shot
            _probe["t0"] = time.time()
            _probe["samples"].append(_probe_sample("before"))
        elapsed = time.time() - _probe["t0"]
        if _probe["applied"] < STATUS_PROBE["applications"] and \
                elapsed >= _probe["applied"] * STATUS_PROBE["interval_s"]:
            controller = get_pc()
            status = unrealsdk.find_object("StatusEffectDefinition", STATUS_PROBE["status"])
            counted = globals().get("_freeze_log", {}).get("applied")
            _probe["via"] = _apply_status_probe(_probe["pawn"].StatusEffectComp, status, controller)
            if counted is not None and _freeze_log["applied"] == counted \
                    and status._path_name() == FREEZE["status"]:
                # the entry point used did not pass through the hooked ApplyStatusEffect
                count_application(_probe["pawn"], controller)
                _probe["via"] += " + counted by the probe"
            _probe["applied"] += 1
        kill_at = STATUS_PROBE.get("kill_at_s")
        if kill_at is not None and not _probe.get("killed") and elapsed >= kill_at:
            _probe["killed"] = True
            _probe["kill"] = _probe_kill(_probe["pawn"], get_pc())
        extra = STATUS_PROBE.get("extra_status")
        if extra and not _probe.get("extra_done") and elapsed >= STATUS_PROBE.get("extra_at_s", 6.0):
            controller = get_pc()
            status = unrealsdk.find_object("StatusEffectDefinition", extra)
            _probe["extra_via"] = _apply_status_probe(_probe["pawn"].StatusEffectComp, status, controller)
            _probe["extra_done"] = True
        samples = STATUS_PROBE["samples_s"]
        if _probe["next_sample"] < len(samples) and elapsed >= samples[_probe["next_sample"]]:
            _probe["samples"].append(_probe_sample(f"+{samples[_probe['next_sample']]}s"))
            _probe["next_sample"] += 1
        if _probe["next_sample"] >= len(samples):
            probe_tick.disable()
            _probe_look_back()
            if STATUS_PROBE.get("behind_view"):
                get_pc().SetBehindView(False)
            _write({"status_probe": {"status": STATUS_PROBE["status"], "via": _probe["via"],
                                     "extra_via": _probe.get("extra_via"),
                                     "target": _probe.get("target"), "kill": _probe.get("kill"),
                                     "candidates": _probe.get("candidates"),
                                     "look": _probe.get("look"),
                                     "freeze_log": dict(globals().get("_freeze_log", {})),
                                     "applied": _probe["applied"], "samples": _probe["samples"]}})
            seq_tick.enable()
    except Exception as ex:  # noqa: BLE001
        probe_tick.disable()
        _write({"status_probe": {"error": f"{ex!r}\\n{traceback.format_exc()}",
                                 "candidates": _probe.get("candidates")}})
        seq_tick.enable()


def _probe_look(pawn: UObject) -> str:
    """Face the target (for a screenshot of the freeze); _probe_look_back undoes it."""
    import math
    controller = get_pc()
    here, there = controller.Pawn.Location, pawn.Location
    dx, dy, dz = there.X - here.X, there.Y - here.Y, there.Z - here.Z
    yaw = int(math.atan2(dy, dx) * 32768 / math.pi) & 0xFFFF
    pitch = int(math.atan2(dz, math.hypot(dx, dy)) * 32768 / math.pi) & 0xFFFF
    old = controller.Rotation
    _probe["rotation_before"] = (int(old.Pitch), int(old.Yaw), int(old.Roll))
    controller.SetRotation(unrealsdk.make_struct("Rotator", Pitch=pitch, Yaw=yaw, Roll=0))
    return f"yaw {yaw} pitch {pitch}, distance {math.hypot(dx, dy):.0f}"


def _probe_look_back() -> None:
    before = _probe.get("rotation_before")
    if before:
        try:
            get_pc().SetRotation(unrealsdk.make_struct(
                "Rotator", Pitch=before[0], Yaw=before[1], Roll=before[2]))
        except Exception:  # noqa: BLE001
            pass


def _probe_kill(pawn: UObject, controller: UObject) -> str:
    """Lethal damage to the probe's target (the shatter path runs in its Died hook)."""
    steps = []
    try:
        pawn.TakeDamage(Damage=10000000, InstigatedBy=controller, HitLocation=pawn.Location,
                        Momentum=unrealsdk.make_struct("Vector"),
                        DamageType=unrealsdk.find_class("WillowDamageType"),
                        DamageCauser=controller.Pawn)
        steps.append("TakeDamage")
    except Exception as ex:  # noqa: BLE001
        steps.append(f"TakeDamage failed: {ex!r}")
    try:
        alive = float(pawn.GetHealth()) > 0
    except Exception as ex:  # noqa: BLE001
        alive = True
        steps.append(f"GetHealth failed: {ex!r}")
    if alive:
        # a friendly NPC shrugs damage off; Died runs the same hook a real kill would
        try:
            pawn.Died(Killer=controller, DamageType=unrealsdk.find_class("WillowDamageType"),
                      HitLocation=pawn.Location)
            steps.append("Died called")
        except Exception as ex:  # noqa: BLE001
            steps.append(f"Died failed: {ex!r}")
    return "; ".join(steps)


def _apply_status_probe(component: UObject, status: UObject, instigator: Any) -> str:
    """The real entry point first (it is the one the freeze hook sees), the cheat second."""
    try:
        component.ApplyStatusEffect(StatusEffectDefinition=status, InstigatedBy=instigator,
                                  DamageType=unrealsdk.find_class("WillowDamageType"))
        return "ApplyStatusEffect"
    except Exception as ex:  # noqa: BLE001
        component.CheatApplyStatusEffect(StatusEffectDefinition=status, InstigatedBy=instigator)
        return f"CheatApplyStatusEffect (ApplyStatusEffect: {ex!r})"


_arm_harness_plain = arm_harness


def arm_harness() -> str:
    """The phase machine, held until the status probe has taken its last sample."""
    result = _arm_harness_plain()
    if result.startswith("no pawn"):
        return result
    seq_tick.disable()
    try:
        _probe_start()
    except Exception as ex:  # noqa: BLE001 - the probe must never hold the harness up
        _write({"status_probe": {"error": repr(ex)}})
        seq_tick.enable()
        return result + f"; status probe failed to start: {ex!r}"
    return result + "; status probe first"'''

_FIRE_TEST_SECTION = '''


# --------------------------------------------------------------------- fire test (harness)
#: after the phase machine is done (and the loop has had time to take its capture), hold the
#: trigger for a moment so the tracer / impact / muzzle effects can be screenshotted
FIRE_TEST: dict[str, Any] = $fire_literal
_fire: dict[str, Any] = {}


def _fire_test_arm() -> None:
    _fire.clear()
    _fire.update({"t0": time.time(), "state": "waiting"})
    fire_test_tick.enable()


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def fire_test_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    try:
        elapsed = time.time() - _fire["t0"]
        controller = get_pc()
        if _fire["state"] == "waiting" and elapsed >= FIRE_TEST.get("delay_s", 6.0):
            controller.StartFire(0)
            _fire["state"] = "firing"
            _write({"fire": "start", "weapon": controller.Pawn.Weapon._path_name()})
        elif _fire["state"] == "firing" and elapsed >= FIRE_TEST.get("delay_s", 6.0) + FIRE_TEST.get("seconds", 1.5):
            controller.StopFire(0)
            _fire["state"] = "done"
            fire_test_tick.disable()
            _write({"fire": "stop"})
    except Exception as ex:  # noqa: BLE001
        fire_test_tick.disable()
        _write({"fire": "error", "error": repr(ex)})'''

_CARD_ICON_SECTION = '''


# --------------------------------------------------------------------- element icon on the card
#: BL2's item card movie (SharedWillowComponents, in an exe-hashed package: not editable) has
#: no frame for this element, so the icon is drawn into the card at runtime the way TPS's
#: card shows it: the card's elementalIcon goes to its empty frame and an image clip carries
#: TPS's icon at TPS's placement; the damage line's inline icon is hidden and replaced the same
#: way. Everything is undone when the (reused) card shows anything else.
CARD_ICON: dict[str, Any] = $card_literal
_card_state: dict[int, bool] = {}


def _card_wants_icon(item: Any) -> bool:
    try:
        definition = item.DefinitionData
        part = getattr(definition, "ElementalPartDefinition", None)
        return part is not None and part._path_name() in CARD_ICON["element_parts"]
    except Exception:  # noqa: BLE001 - not a weapon (shields, class mods...)
        return False


def _as_string(text: str) -> Any:
    return unrealsdk.make_struct("ASValue", Type=unrealsdk.find_enum("ASType").AS_String, S=text)


def _image_clip(parent: Any, name: str, box: dict[str, float], depth: int) -> Any:
    clip = parent.GetObject(name)
    if clip is None:
        clip = parent.CreateEmptyMovieClip(name, depth)
        clip.Invoke("loadMovie", [_as_string(CARD_ICON["image"])])
    size = float(CARD_ICON.get("texture_size", 64))
    clip.SetFloat("_x", float(box["x"]))
    clip.SetFloat("_y", float(box["y"]))
    clip.SetFloat("_xscale", 100.0 * float(box["w"]) / size)
    clip.SetFloat("_yscale", 100.0 * float(box["h"]) / size)
    clip.SetBool("_visible", True)
    return clip


def _drop_clip(parent: Any, name: str) -> None:
    clip = parent.GetObject(name) if parent is not None else None
    if clip is not None:
        clip.SetBool("_visible", False)
        clip.Invoke("removeMovieClip", [])


def apply_card_icon(card: Any, wanted: bool) -> str:
    element = card.GetObject("elementalIcon")
    stat = card.GetObject(CARD_ICON.get("stat", "stat1"))
    inline = stat.GetObject("icon") if stat is not None else None
    if wanted:
        if element is not None and CARD_ICON.get("card"):
            element.GotoAndStop("none")
            _image_clip(element, "pipelineElementIcon", CARD_ICON["card"], 1000)
        if inline is not None and CARD_ICON.get("inline"):
            inline.SetBool("_visible", False)
            box = dict(CARD_ICON["inline"])
            box["x"] += inline.GetFloat("_x")
            box["y"] += inline.GetFloat("_y")
            _image_clip(stat, "pipelineInlineIcon", box, 1001)
        return "drawn"
    _drop_clip(element, "pipelineElementIcon")
    _drop_clip(stat, "pipelineInlineIcon")
    if inline is not None:
        inline.SetBool("_visible", True)
    return "cleared"


@hook("WillowGame.ItemCardGFxObject:SetItemCardEx", Type.POST)
def on_item_card(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    try:
        wanted = _card_wants_icon(args.InventoryItem)
        if wanted or _card_state.get(id(obj)):
            result = apply_card_icon(obj, wanted)
            if wanted and not _card_state.get(id(obj)):
                _write({"card_icon": result, "item": args.InventoryItem._path_name()})
        _card_state[id(obj)] = wanted
    except Exception as ex:  # noqa: BLE001 - never break the card
        _write({"card_icon": "error", "error": repr(ex)})'''

_OWN_TYPE_CALL = '''
    result["weapon_type"] = _own_weapon_type(balance, template, bal["name"])'''


def _objects_literal(resolved: ResolvedSpec) -> str:
    """``OBJECTS``: the spec's runtime objects in construction order; empty when none."""
    objects = resolved.spec.objects
    if not objects:
        return ""
    rows = [dict(o.to_dict(), path=o.path) for o in objects]
    return (
        "\n\n#: runtime objects cloned from stock ones and edited (element plumbing): built at the\n"
        "#: menu tick in this order, after the materials and before the parts, so a part can\n"
        "#: point at one. Values: plain scalars, None, {\"object\": path}, {\"enum\": \"E.Member\"}.\n"
        "OBJECTS: tuple[dict[str, Any], ...] = tuple(" + pprint.pformat(rows, width=96, sort_dicts=False) + ")"
    )


_VALUE_HELPERS = '''


# --------------------------------------------------------------------- dotted-path edits
_STEP = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\\[(\\d+)\\])?")


def _resolve_value(value: Any) -> Any:
    """Decode a spec value: {"object": path} -> the object, {"enum": "E.M"} -> the member."""
    if isinstance(value, dict):
        if "object" in value:
            target = _find("Object", value["object"])
            if target is None:
                raise ValueError(f"object {value['object']} not found")
            return target
        if "enum" in value:
            if "." not in value["enum"]:
                return _Member(value["enum"])  # resolved against the property's own type
            enum_name, member = value["enum"].split(".")
            return getattr(unrealsdk.find_enum(enum_name), member)
    return value


class _Member:
    """An enum member named without its enum: _assign takes the type from the property."""

    def __init__(self, name: str) -> None:
        self.name = name

    def against(self, current: Any) -> Any:
        return type(current)[self.name]


def _assign(root: Any, dotted: str, value: Any) -> None:
    """``root.A.B[2].C = value`` for a dotted path; struct views are edited in place."""
    target = root
    steps = dotted.split(".")
    for i, step in enumerate(steps):
        match = _STEP.fullmatch(step)
        if match is None:
            raise ValueError(f"bad path step {step!r} in {dotted!r}")
        name, index = match.group(1), match.group(2)
        last = i == len(steps) - 1
        if index is None:
            if last:
                if isinstance(value, _Member):
                    value = value.against(getattr(target, name))
                setattr(target, name, value)
                return
            target = getattr(target, name)
        else:
            array = getattr(target, name)
            if last:
                if isinstance(value, _Member):
                    value = value.against(array[int(index)])
                array[int(index)] = value
                return
            target = array[int(index)]'''

_OBJECTS_SECTION = '''


# --------------------------------------------------------------------- runtime objects
def register_object(spec: dict[str, Any]) -> dict[str, Any]:
    """Clone one stock object (a damage type, status effect, skill, behavior) and edit it.

    Order inside: struct arrays, then object arrays, then dotted values -- the values may
    reach into rows the struct arrays just wrote. Idempotent: looked up before constructed.
    """
    result: dict[str, Any] = {"object": spec["path"]}
    template = _find("Object", spec["template"])
    if template is None:
        raise ValueError(f"template {spec['template']} for {spec['path']} not found")
    obj = _find(spec["class"], spec["path"])
    if obj is None:
        outer = _find("Object", spec["outer"])
        if outer is None:
            if spec.get("subobject"):
                raise ValueError(f"outer {spec['outer']} for sub-object {spec['path']} not found")
            outer = template.Outer
        obj = unrealsdk.construct_object(spec["class"], outer, spec["name"], 0, template)
        result["constructed"] = True
    else:
        result["constructed"] = False
    keep_alive(obj)
    for field, block in (spec.get("struct_rows") or {}).items():
        source, source_field = template, field
        if block.get("prototype"):
            source_path, source_field = block["prototype"].rsplit(":", 1)
            source = _find("Object", source_path)
            if source is None:
                raise ValueError(f"prototype object {source_path} not found")
        prototypes = getattr(source, source_field)
        if not len(prototypes):
            raise ValueError(f"{block.get('prototype') or spec['template']}.{source_field} "
                             "has no row to clone")
        prototype = prototypes[0]
        array = getattr(obj, field)
        array.clear()
        for row in block["rows"]:
            array.append(prototype)
            for dotted, value in row.items():
                _assign(array[-1], dotted, _resolve_value(value))
        result[field] = f"{len(array)} row(s)"
    for field, paths in (spec.get("object_lists") or {}).items():
        array = getattr(obj, field)
        array.clear()
        for path in paths:
            array.append(_resolve_value({"object": path}) if path else None)
        result[field] = [p for p in paths]
    for dotted, value in (spec.get("values") or {}).items():
        _assign(obj, dotted, _resolve_value(value))
    result["values"] = len(spec.get("values") or {})
    return result


_late_done = False


def register_late_objects() -> Any:
    """Objects whose templates only exist once a map is loaded; built on the first one."""
    global _late_done
    if _late_done:
        return "already built"
    out = []
    for spec in OBJECTS:
        if spec.get("when") != "map_load":
            continue
        try:
            out.append(register_object(spec))
        except Exception as ex:  # noqa: BLE001 - one missing template must not stop the rest
            out.append({"object": spec["path"], "error": repr(ex)})
    _late_done = True
    return out'''


_FRAGMENT_ASSIGN = '''\
    definition.GestaltModeSkeletalMeshName = part["fragment"]
    result["fragment"] = str(definition.GestaltModeSkeletalMeshName)'''

_FRAGMENT_ASSIGN_OR_NONE = '''\
    if part["fragment"] is None:
        # The part draws nothing: a clone of a non-gestalt part (AR_Sight_None). Its
        # inherited fragment name (AR_Scope_Bandit) IS still assembled onto a gestalt
        # weapon whatever bIsGestaltMode says -- the M7 AK wore a Bandit scope block until
        # the name was cleared -- so both are set. This is how a slot is claimed and left
        # empty (a stock scope would otherwise roll onto a body with its own sights).
        definition.bIsGestaltMode = False
        definition.GestaltModeSkeletalMeshName = "None"
        result["fragment"] = None
    else:
        definition.GestaltModeSkeletalMeshName = part["fragment"]
        result["fragment"] = str(definition.GestaltModeSkeletalMeshName)'''


def _part_fragment_block(resolved: ResolvedSpec) -> dict[str, str]:
    """``register_part``'s fragment assignment: the M2 two-liner, or the branch that also
    handles a part with no fragment.  The branch is only emitted when a spec has such a
    part, so every earlier spec keeps emitting the file it always did."""
    if any(part.fragment is None for part in resolved.parts):
        return {"part_fragment_assign": _FRAGMENT_ASSIGN_OR_NONE}
    return {"part_fragment_assign": _FRAGMENT_ASSIGN}


_BOUNDS_OVERRIDE_BRANCH = '''    override_box = frag.get("bounds_override")
    if override_box is not None:
        # The template's box is the template geometry's; the item-card preview frames the
        # weapon from these, so a fragment of a different shape gets its own (M8 s7).
        box = bounds[-1].ReferencePoseBounds
        box.Origin.X, box.Origin.Y, box.Origin.Z = (float(v) for v in override_box["origin"])
        box.BoxExtent.X, box.BoxExtent.Y, box.BoxExtent.Z = (float(v) for v in override_box["extent"])
        box.SphereRadius = float(override_box["radius"])
        result["bounds_override"] = dict(override_box)
        dz = 0.0
'''


def _bounds_override_block(resolved: ResolvedSpec) -> dict[str, str]:
    """Emitted only when a fragment overrides its bounds (the pre-M8 text is untouched)."""
    if any(frag.bounds_override is not None for frag in resolved.fragments):
        return {"bounds_override_branch": _BOUNDS_OVERRIDE_BRANCH}
    return {"bounds_override_branch": ""}


def _socket_override_blocks(resolved: ResolvedSpec) -> dict[str, str]:
    """The three pieces ``register_fragment`` grows when a spec places sockets itself.

    A socket's ``RelativeLocation`` is relative to its **bone**, so putting one at an
    arbitrary mesh-space point means re-parenting it to a bone whose transform is the
    identity.  ``Root`` is exactly that on the gestalt skeleton: both of
    ``AR_Barrel_Vladof``'s Root sockets land at precisely their stored location
    (``docs/FINDINGS.md``), so mesh space and Root-bone space are the same frame.

    All three come out empty when no fragment uses ``socket_overrides``, which keeps
    every pre-M6 spec emitting the byte-for-byte file it always did.
    """
    if not any(frag.socket_overrides for frag in resolved.fragments):
        return {"socket_overrides_init": "",
                "socket_dz_branch": "        if",
                "socket_overrides_record": ""}
    return {
        "socket_overrides_init": (
            '\n    # {socket: (x, y, z)} in MESH space, for geometry that is not a variation'
            '\n    # of the template (M6: the AK\'s muzzle is 49 units behind the Shredifier\'s).'
            '\n    # RelativeLocation is relative to the socket\'s BONE, so an overridden socket'
            '\n    # is re-parented to Root -- the identity bone of this skeleton -- and then'
            '\n    # mesh space and bone space are the same thing. dz is NOT added on top.'
            '\n    overrides = frag.get("socket_overrides") or {}'),
        "socket_dz_branch": (
            '        override = overrides.get(original)\n'
            '        if override is not None:\n'
            '            socket.BoneName = "Root"\n'
            '            socket.RelativeLocation.X = float(override[0])\n'
            '            socket.RelativeLocation.Y = float(override[1])\n'
            '            socket.RelativeLocation.Z = float(override[2])\n'
            '        elif'),
        "socket_overrides_record": (
            '\n    if overrides:'
            '\n        result["socket_overrides"] = {k: list(v) for k, v in overrides.items()}'),
    }


def _wrap(text: str, indent: str = "  ", width: int = 96) -> str:
    """Docstring prose, hard-wrapped so the generated file stays inside 100 columns."""
    return "\n".join(
        textwrap.fill(
            line,
            width=width,
            initial_indent=indent,
            subsequent_indent=indent + "  ",
            break_long_words=False,
            break_on_hyphens=False,
        )
        for line in text.strip().splitlines()
    )


def _summary_lines(resolved: ResolvedSpec) -> str:
    lines = []
    for frag in resolved.fragments:
        lines.append(
            f"fragment {frag.name}: {frag.num_primitives} triangles at index "
            f"{frag.first_index}, cloned from {frag.template_fragment}, "
            f"{len(frag.sockets)} socket(s)"
        )
    for part in resolved.parts:
        targets = [f"{r.balance}.{r.field}" for r in part.registrations]
        targets += [f"{bal}.{fld} (runtime)" for bal, fld in part.runtime_registrations]
        drawn = "" if part.fragment is not None else ", draws nothing"
        lines.append(f"part {part.part_path} ({part.slot}{drawn}) -> {', '.join(targets)}")
    for name in resolved.extra_packages:
        lines.append(f"extra package {name} loaded at the menu tick")
    for mat in resolved.materials:
        params = ", ".join(f"{k}={v.rsplit('.', 1)[-1]}" for k, v in mat.texture_parameters.items())
        lines.append(f"material {mat.path} parented to {mat.parent}: {params}")
    for bal in resolved.balances:
        lists = ", ".join(f"{fld}=[{', '.join(p.rsplit('.', 1)[-1] for p in paths)}]"
                          for fld, paths in bal.part_lists.items())
        lines.append(f"balance {bal.path} cloned from {bal.template_balance}: {lists}")
        if bal.title is not None:
            lines.append(f"title {bal.title.path} = {bal.title.part_name!r} on "
                         f"{', '.join(p.rsplit('.', 1)[-1] for p in bal.title.on_part_paths)}")
        if bal.pools:
            lines.append(f"pools: {', '.join(bal.pools)}")
    return "\n".join(_wrap(line, "    ") for line in lines)


# --------------------------------------------------------------------- sections
_HEADER = Template('''"""$mod_name -- runtime gestalt fragment + weapon part registration.

$description
GENERATED by bl2_partgen from a spec; edit the spec and re-emit rather than this file.
  spec:    $spec_name
  catalog: $catalog_stamp

WHAT IT REGISTERS (see FRAGMENTS / PARTS below, and README.md)
$summary

HOOKS (README.md has the long form)
  import         every hook below is enable()d at import: auto_enable only re-enables mods
                 that were enabled last launch, so a new mod would never bind one (F8)
  menu tick $menu_ticks  menu_setup(): load the package, re-point the gestalt mesh, append the
                 fragment table entry / bounds / socket mappings, construct the sockets and
                 the part definition(s), append them to their runtime part lists -- at the
                 MAIN MENU, before any save is deserialised (F16), rooting all of it (F17)$menu_extra
$when_extra

PACKAGE $package.upk is a NEW file beside the cooked ones: not one of the twelve the exe
  SHA1-verifies (F12), exports written with ExportFlags=0 or none of them can be found
  (F13), and no stale ".uncompressed_size" sidecar beside it (F11).
"""
from __future__ import annotations

import json
import os
import re
import time
import traceback
from typing import Any

import unrealsdk
from mods_base import build_mod, hook$mods_base_extra
from unrealsdk.hooks import Block, Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

__version__ = "$version"
__author__ = "$author"

# --------------------------------------------------------------------- what we register
MOD_NAME = "$mod_name"
MOD_DIR = os.path.dirname(os.path.abspath(__file__))



def _sdk_mods_dir() -> str:
    """The game's sdk_mods folder, found by walking up from this file (else this folder)."""
    here = MOD_DIR
    while os.path.basename(here).lower() != "sdk_mods" and os.path.dirname(here) != here:
        here = os.path.dirname(here)
    return here if os.path.basename(here).lower() == "sdk_mods" else MOD_DIR


#: per-save record of which weapons carry our parts (F18): keyed by PACKAGE, not mod name,
#: so every build of this weapon (player, harness, spawn, armory component) shares one set
SAVE_DIR = os.path.join(_sdk_mods_dir(), "_pipeline_saves", "$save_package")
#: optional; {"status_path": "..."} redirects the status JSON for the verify loop
CONTROL_FILE = os.path.join(MOD_DIR, "control.json")
$status_file

PACKAGE = "$package"
MESH_PATH = "$mesh_path"
GESTALT_DEF = "$gestalt_def"$own_gestalt_consts
#: for reference when debugging, the mesh the gestalt definition shipped with:
#:   $stock_mesh

#: viewport ticks to wait before registering; the main menu is up long before this (F16)
MENU_TICKS = $menu_ticks
#: the SDK's KeepAlive bit; unrooted objects die in the level-transition GC (F17)
KEEP_ALIVE_FLAG = 0x4000
KEEP_ALIVE = $keep_alive

FRAGMENTS: tuple[dict[str, Any], ...] = (
$fragments
)

PARTS: tuple[dict[str, Any], ...] = (
$parts
)$balances_literal$materials_literal

#: exact paths of the parts we construct; the save/validate hooks key off this
OUR_PART_PATHS = frozenset(part["path"] for part in PARTS)$our_paths_extra

#: the fields of WeaponDefinitionData that can hold one of our parts
SLOT_FIELDS = (
$slot_fields
)


# --------------------------------------------------------------------- plumbing
def _log(message: str) -> None:
    unrealsdk.logging.info(f"[{MOD_NAME}] {message}")


def _control() -> dict[str, Any]:
    """Optional control file; absent is the normal case."""
    try:
        with open(CONTROL_FILE, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:  # noqa: BLE001 - a missing/broken control file must never break the mod
        return {}


_status: list[dict[str, Any]] = []


def _write(record: dict[str, Any]) -> dict[str, Any]:
    """Append a record to the status JSON (what bl2_verify reads back)."""
    record["t"] = time.time()
    _status.append(record)
    try:
        path = str(_control().get("status_path") or DEFAULT_STATUS_FILE)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(_status, handle, indent=1, default=str)
    except Exception as ex:  # noqa: BLE001
        _log(f"status write failed: {ex!r}")
    return record


def keep_alive(obj: UObject | None) -> None:
    """Root an object and its whole outer chain against the level-transition GC (F17).

    Startup-package objects are in the engine's disregard-for-GC set, so references FROM
    them to what we load later are never traversed: without this flag our mesh/part is
    collected at the next map load and the fatal error reads "Ran out of virtual memory".
    """
    if not KEEP_ALIVE:
        return
    while obj is not None:
        obj.ObjectFlags |= KEEP_ALIVE_FLAG
        obj = obj.Outer


def _find(cls: str, path: str) -> UObject | None:
    """find_object that answers None instead of raising when the object is absent."""
    try:
        return unrealsdk.find_object(cls, path)
    except Exception:  # noqa: BLE001 - the SDK raises ValueError; older builds raised others
        return None
''')


_REGISTER = Template('''

# --------------------------------------------------------------------- registration
_registered = False


def load_mesh_package() -> UObject:
    """Load our package and point the gestalt definition at our mesh.

    Weapons created BEFORE this keep the mesh they were built with (F14).
    """
    package = unrealsdk.load_package(PACKAGE)
    mesh = unrealsdk.find_object("SkeletalMesh", MESH_PATH)
    keep_alive(package)
    keep_alive(mesh)
    for socket in mesh.Sockets:
        keep_alive(socket)
${gestalt_load}def register_fragment(gestalt: UObject, mesh: UObject, frag: dict[str, Any]) -> dict[str, Any]:
    """Append one fragment: table entry, reference-pose bounds and socket mappings.

    Every append clones the template row first (appending a struct to a WrappedArray copies
    it), then overwrites the fields that differ. Re-running is a no-op: the name is the key.
    """
    name, template = frag["name"], frag["template"]
    result: dict[str, Any] = {"fragment": name, "template": template}

    entries = gestalt.GestaltInfos[0].Parts
    if any(str(entry.SkeletalMeshFragmentName) == name for entry in entries):
        result["skipped"] = "fragment already in the table"
        return result

    source = next(e for e in entries if str(e.SkeletalMeshFragmentName) == template)
    entries.append(source)
    entries[-1].SkeletalMeshFragmentName = name
    entries[-1].FirstIndex = int(frag["first_index"])
    entries[-1].NumPrimitives = int(frag["num_primitives"])
    result["table_entry"] = {"first_index": int(entries[-1].FirstIndex),
                             "num_primitives": int(entries[-1].NumPrimitives),
                             "table_len": len(entries)}

    # Bounds drive culling and the "is the weapon on screen" test; a taller barrel needs a
    # taller box or it flickers out at the screen edge.
    dz = float(frag["dz"])
    bounds = gestalt.GestaltPartBounds
    source_bounds = next(b for b in bounds if str(b.SkeletalMeshFragmentName) == template)
    bounds.append(source_bounds)
    bounds[-1].SkeletalMeshFragmentName = name
$bounds_override_branch    if dz:
        box = bounds[-1].ReferencePoseBounds
        box.Origin.Z = box.Origin.Z + dz / 2
        box.BoxExtent.Z = box.BoxExtent.Z + dz / 2
        box.SphereRadius = box.SphereRadius + dz
    result["bounds_len"] = len(bounds)

    # Sockets are cooked onto the mesh with mangled names (<fragment>_<socket>); the mapping
    # table is what translates the part's "Muzzle" into "AR_Barrel_PL_Bent_Muzzle".
    mappings = gestalt.GestaltSocketMappings
    wanted = frag["sockets"]
    dz_sockets = frag["dz_sockets"]$socket_overrides_init
    made: list[str] = []
    for mapping in list(mappings):
        if str(mapping.SkeletalMeshFragmentName) != template:
            continue
        original = str(mapping.OriginalSocketName)
        if original not in wanted:
            continue
        template_mangled = str(mapping.MangledSocketName)
        mangled = f"{name}_{original}"
        template_socket = next(
            (s for s in mesh.Sockets if str(s.SocketName) == template_mangled), None
        )
        if template_socket is None:
            made.append(f"{original}: template socket {template_mangled} is missing from the mesh")
            continue
        socket = unrealsdk.construct_object(
            "SkeletalMeshSocket", mesh, mangled, 0, template_socket)
        keep_alive(socket)
        socket.SocketName = mangled
$socket_dz_branch dz and original in dz_sockets:
            socket.RelativeLocation.Z = socket.RelativeLocation.Z + dz
        mesh.Sockets.append(socket)
        mappings.append(mapping)
        mappings[-1].SkeletalMeshFragmentName = name
        mappings[-1].OriginalSocketName = original
        mappings[-1].MangledSocketName = mangled
        made.append(mangled)
    result["sockets"] = made$socket_overrides_record
    return result


def _clear_additional_fragments(definition: UObject) -> Any:
    """Set both AdditionalGestaltModeSkeletalMeshNames slots to None; report what was there."""
    try:
        before = [str(n) for n in definition.AdditionalGestaltModeSkeletalMeshNames]
    except Exception as ex:  # noqa: BLE001
        return f"unavailable: {ex!r}"
    try:
        definition.AdditionalGestaltModeSkeletalMeshNames = ("None", "None")
    except Exception:  # noqa: BLE001 - fixed arrays may only take element assignment
        try:
            for index in range(len(before)):
                definition.AdditionalGestaltModeSkeletalMeshNames[index] = "None"
        except Exception as ex:  # noqa: BLE001
            return f"FAILED {ex!r} (was {before})"
    return before


def register_part(part: dict[str, Any]) -> dict[str, Any]:
    """Construct one WeaponPartDefinition -- a clone of an existing part of the same slot, so
    its stats/material/attributes are populated -- and append it to its part list(s).
    """
    result: dict[str, Any] = {"part": part["path"]}
    template = unrealsdk.find_object("WeaponPartDefinition", part["template_part"])

    definition = _find("WeaponPartDefinition", part["path"])
    if definition is None:
        outer = _find("Package", part["outer"]) or template.Outer
        definition = unrealsdk.construct_object(
            "WeaponPartDefinition", outer, part["name"], 0, template
        )
        result["constructed"] = True
    else:
        result["constructed"] = False
    keep_alive(definition)
$part_fragment_assign
    # A template may draw EXTRA fragments beside its own (AR_Body_Vladof_4 carries the
    # Vladof body variants Var1/Var2 as AdditionalGestaltModeSkeletalMeshNames); the clone
    # inherits them and they land on top of the new geometry (M8 s6). Cleared, always.
    result["additional_cleared"] = _clear_additional_fragments(definition)$part_overrides_call

    # A part in no list never rolls (F5). We APPEND, so the stock entries keep rolling too.
    lists: list[dict[str, Any]] = []
    for target in part["register_in"]:
        balance = unrealsdk.find_object("WeaponBalanceDefinition", target["balance"])
        collection = balance.RuntimePartListCollection
        weighted = getattr(collection, target["field"]).WeightedParts
        already = any(
            entry.Part is not None and entry.Part._path_name() == part["path"]
            for entry in weighted
        )
        if not already:
            weighted.append(weighted[0])
            weighted[-1].Part = definition
        lists.append({"balance": target["balance"], "field": target["field"],
                      "already_present": already,
                      "parts": [e.Part._path_name() if e.Part else None for e in weighted]})
    result["lists"] = lists
    return result$overrides_section$materials_section$balances_section


def menu_setup() -> dict[str, Any]:
    """The whole registration, idempotent. Called from the menu tick; safe to call again."""
    global _registered
    record: dict[str, Any] = {"phase": "menu_setup", "mod": MOD_NAME}
    if _registered:
        record["skipped"] = "already registered this session"
        return _write(record)
    try:
        mesh = load_mesh_package()$extra_packages_step
        gestalt = active_gestalt()
        record["gestalt"] = gestalt._path_name()
        flags = int(mesh.ObjectFlags)
        record["mesh"] = f"{mesh._path_name()} sockets={len(mesh.Sockets)} flags=0x{flags:x}"
        record["fragments"] = [register_fragment(gestalt, mesh, f) for f in FRAGMENTS]$materials_step$objects_step
        record["parts"] = [register_part(p) for p in PARTS]$balances_step
        _registered = True
        record["ok"] = True
        _log("registration done")
    except Exception as ex:  # noqa: BLE001 - never take the game down with us
        record["ok"] = False
        record["error"] = f"{ex!r}\\n{traceback.format_exc()}"
        _log(f"registration FAILED: {ex!r}")
    return _write(record)


_menu_ticks = 0


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def menu_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    """Count viewport ticks at the main menu, then register once and unhook.

    A map-load hook is no good here: WillowClientDisableLoadingMovie never fires at the title
    screen (F9), and registering after a map load crashes a save that uses the part (F16).
    """
    global _menu_ticks
    _menu_ticks += 1
    if _menu_ticks < MENU_TICKS:
        return
    menu_tick.disable()
    menu_setup()
''')


_SAVE_HOOKS = Template('''

# --------------------------------------------------------------------- save round-trip (F18)
def our_parts_in(definition: Any) -> dict[str, str]:
    """{slot field: part path} for every slot of a WeaponDefinitionData holding our part."""
    found: dict[str, str] = {}
    for field in SLOT_FIELDS:
        part = getattr(definition, field, None)
        if part is not None and part._path_name() in OUR_PART_PATHS:
            found[field] = part._path_name()
    return found


def _save_file(save_name: Any) -> str:
    os.makedirs(SAVE_DIR, exist_ok=True)
    safe = "".join(c for c in str(save_name) if c.isalnum() or c in "._-") or "unknown"
    return os.path.join(SAVE_DIR, safe + ".json")


#: how many retired records to keep before the oldest is dropped for good
RETIRED_MAX = 500


def _retired_file(path: str) -> str:
    """Sibling of the active record file, holding ids pruned at load (F29).

    Pruning is keyed on the save's own weapon list, which is authoritative for what the
    pawn carries -- but the bank is not in it, so an id missing there is not proof the
    weapon is gone. Retiring rather than deleting means a weapon that comes back is
    restored from here and promoted, and the active file still stays small.
    """
    return (path[: -len(".json")] if path.endswith(".json") else path) + ".retired.json"


def _read_records(path: str) -> dict[str, Any]:
    """Records from one file; {} when absent, unreadable or not an object."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _owned_weapons(controller: UObject) -> list[UObject]:
    """Every weapon the pawn owns, carried or in the backpack."""
    pawn, weapons = controller.Pawn, []
    for weapon in unrealsdk.find_all("WillowWeapon", exact=False):
        try:  # half-destroyed actors can throw on attribute access
            if weapon.Owner == pawn:
                weapons.append(weapon)
        except Exception:  # noqa: BLE001
            continue
    return weapons


@hook("WillowGame.WillowPlayerController:GeneratePlayerSaveGame", Type.PRE)
def on_generate_save(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    """Record which weapons carry our parts, keyed by UniqueId, before the save is written.

    Runtime parts are not encodable, so the slot comes back null on load (F18); the UniqueId
    survives, so it is the join key. Also fires on every map load.

    The file is **merged, never replaced** (F29). A weapon is only seen here if it is carrying
    our parts at this instant, so anything that stops a load from restoring them -- the mod
    disabled, the SDK failing, a build looking in the wrong place -- makes the weapon invisible
    to this hook. Replacing the file would then drop its record on the very next map load and
    the weapon could never be repaired, from one bad load. Ids for weapons that are gone are
    harmless: nothing in the save will ever match them again.
    """
    try:
        path = _save_file(obj.SaveGameName)
        records: dict[str, dict[str, str]] = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    existing = json.load(handle)
                if isinstance(existing, dict):
                    records.update(existing)
            except (OSError, ValueError):
                pass  # unreadable or truncated: start fresh rather than lose this save's weapons
        seen: dict[str, dict[str, str]] = {}
        for weapon in _owned_weapons(obj):
            definition = weapon.DefinitionData
            slots = our_parts_in(definition)
            if not slots:
                continue
            for field in slots:
                keep_alive(getattr(definition, field))$save_balance_record
            seen[str(int(definition.UniqueId))] = slots
        records.update(seen)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(records, handle, indent=1)
        _write({"$save_key": "GeneratePlayerSaveGame", "file": path, "custom_weapons": seen,
                "kept_from_previous": sorted(set(records) - set(seen))})
    except Exception as ex:  # noqa: BLE001
        _write({"$save_key": "GeneratePlayerSaveGame", "error": f"{ex!r}\\n{traceback.format_exc()}"})


@hook("WillowGame.WillowPlayerController:ApplyPlayerSaveGameData", Type.PRE)
def on_apply_save(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    """Put our parts back into the saved weapon data before the game builds the weapons (F18).

    PRE is essential: by the time this returns, the weapons exist and the sanity check has
    already thrown ours away.
    """
    try:
        save_name = obj.GetSaveGameNameFromid(args.SaveGame.SaveGameId)
        path = _save_file(save_name)
        records, retired = _read_records(path), _read_records(_retired_file(path))
        if not records and not retired:
            _write({"$load_key": "ApplyPlayerSaveGameData", "save": str(save_name), "record": None})
            return
        restored: list[str] = []
        missing: list[str] = []
        promoted: list[str] = []
        live: set[str] = set()
        for weapon_data in args.SaveGame.WeaponData:
            definition = weapon_data.WeaponDefinitionData
            unique_id = str(int(definition.UniqueId))
            live.add(unique_id)
            slots = records.get(unique_id)
            if slots is None and unique_id in retired:
                # it came back (out of the bank, say): restore from the retired set and promote
                slots = records[unique_id] = retired.pop(unique_id)
                promoted.append(unique_id)
            if not slots:
                continue
            for field, part_path in slots.items():$load_balance_restore
                part = _find("WeaponPartDefinition", part_path)
                if part is None:
                    # registration failed or ran late; leaving the slot null is survivable
                    missing.append(part_path)
                    continue
                setattr(definition, field, part)
                restored.append(f"{int(definition.UniqueId)}:{field}={part_path}")
        # Retire records this save has no weapon for. Never on an empty scan: a save that
        # deserialised no weapons at all means the shape is not what we think, and pruning
        # on that would throw away every record at once.
        pruned = [uid for uid in list(records) if uid not in live] if live else []
        for unique_id in pruned:
            retired[unique_id] = records.pop(unique_id)
        while len(retired) > RETIRED_MAX:
            retired.pop(next(iter(retired)))
        if pruned or promoted:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(records, handle, indent=1)
            with open(_retired_file(path), "w", encoding="utf-8") as handle:
                json.dump(retired, handle, indent=1)
        _write({"$load_key": "ApplyPlayerSaveGameData", "save": str(save_name),
                "restored": restored, "missing": missing,
                "retired": pruned, "promoted": promoted})
    except Exception as ex:  # noqa: BLE001
        _write({"$load_key": "ApplyPlayerSaveGameData", "error": f"{ex!r}\\n{traceback.format_exc()}"})
''')


_VALIDATE_HOOK = Template('''

@hook("WillowGame.WillowPlayerController:ValidateWeaponDefinition", Type.PRE)
def on_validate_weapon(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> Any:
    """Force the sanity check to True for definitions that use one of our parts (F19).

    It resolves parts against the COOKED lists, so a runtime part fails it even though it is
    in the runtime list, and the weapon is deleted on load. Everything else is untouched.
    """
    try:
        slots = our_parts_in(args.DefinitionData)
        if slots:
            _write({"$validate_key": "ValidateWeaponDefinition", "forced": True, "slots": slots,
                    "uid": int(args.DefinitionData.UniqueId)})
            return (Block, True)
    except Exception as ex:  # noqa: BLE001
        _write({"$validate_key": "ValidateWeaponDefinition", "error": repr(ex)})
    return None
''')


_MAP_LOAD = Template('''

# --------------------------------------------------------------------- map-load fixups
def force_template_fragment() -> list[str]:
    """Put "$force_fragment" back on our template parts, on every map load.

    A text mod that retargets a stock part's GestaltModeSkeletalMeshName (the AK-47 mod does
    this to the Shredifier barrel) would otherwise be what our clone shows.
    """
    fixed = []
    for part in PARTS:
        template = _find("WeaponPartDefinition", part["template_part"])
        if template is None:
            continue
        template.GestaltModeSkeletalMeshName = "$force_fragment"
        fixed.append(f'{part["template_part"]}={template.GestaltModeSkeletalMeshName}')
    return fixed
''')


_MAP_LOAD_HOOK = '''

@hook("WillowGame.WillowPlayerController:WillowClientDisableLoadingMovie", Type.POST)
def on_map_loaded(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    """Fires once per gameplay map load (never at the title screen -- F9)."""
    record: dict[str, Any] = {"hook": "WillowClientDisableLoadingMovie"}
    try:
        if not _registered:
            # mod loaded mid-session: register now and hope no save references the part (F16)
            record["late_registration"] = menu_setup().get("ok")
$map_load_body
    except Exception as ex:  # noqa: BLE001
        record["error"] = f"{ex!r}\\n{traceback.format_exc()}"
    _write(record)
'''


_HARNESS = Template('''

# --------------------------------------------------------------------- test harness
# Only emitted when the spec says options.test_harness: this is the bl2_verify loop's
# instrumentation, not something a player wants. Letters never reach the game, so the
# bindings are F-keys (F15).
HARNESS_MISSION = "$harness_mission"
$harness_balances

#: the one part the harness drives, and the one part list it forces: PARTS[0] and its first
#: registration target. Everything else in PARTS is still registered, just not granted.
HARNESS_PART = PARTS[0]
$harness_target
HARNESS_TEMPLATE_PART = HARNESS_PART["template_part"]
#: BarrelPartData -> BarrelPartDefinition. The census reports THIS slot under the keys
#: "barrel"/"frag", because bl2_verify's loop was written against the M2 barrel harness and
#: reads those names; the per-slot paths are in the SLOT_FIELDS keys beside them.
HARNESS_SLOT_FIELD = HARNESS_TARGET["field"].replace("PartData", "PartDefinition")
#: the stock fragment HARNESS_TEMPLATE_PART is supposed to be showing
HARNESS_STOCK_FRAGMENT = next(
    frag["template"] for frag in FRAGMENTS if frag["name"] == HARNESS_PART["fragment"]
)

import sys
import types

#: One reward-table registry for EVERY pipeline mod in this interpreter, keyed by mission
#: path (F26). Two spawn/harness mods that hijack the same mission must both put back the
#: PRISTINE table: with a per-mod backup the second mod snapshots the first mod's hijack as
#: "original" and whichever restore lands last can leave a stock mission rewarding our gun.
_SHARED_REGISTRY = "_bl2_pipeline_shared"
_restore_left = 0


def _reward_backups() -> dict[str, dict[str, Any]]:
    module = sys.modules.get(_SHARED_REGISTRY)
    if module is None:
        module = types.ModuleType(_SHARED_REGISTRY)
        module.reward_backups = {}
        sys.modules[_SHARED_REGISTRY] = module
    return module.reward_backups


def _describe(weapon: UObject) -> dict[str, Any]:
    """One census row: which part and which mesh this weapon ended up with.

    The first six keys are M2's, verbatim ("weapon", "slot", "barrel", "frag",
    "FirstPersonMesh", "ThirdPersonMesh"), so bl2_verify can read this mod's status file
    wherever it read the M2 one.
    """
    definition = weapon.DefinitionData
    driven = getattr(definition, HARNESS_SLOT_FIELD, None)
    entry: dict[str, Any] = {
        "weapon": weapon._path_name(),
        "slot": int(weapon.QuickSelectSlot),
        "barrel": driven._path_name() if driven is not None else None,
        "frag": str(driven.GestaltModeSkeletalMeshName) if driven is not None else None,
        "unique_id": int(definition.UniqueId),
        "ours": our_parts_in(definition),
        "type": (definition.WeaponTypeDefinition._path_name()
                 if getattr(definition, "WeaponTypeDefinition", None) is not None else None),$describe_extra
    }
    for field in SLOT_FIELDS:
        part = getattr(definition, field, None)
        if part is not None:
            entry[field] = part._path_name()
    for attr in ("FirstPersonMesh", "ThirdPersonMesh"):
        component = getattr(weapon, attr, None)
        mesh = getattr(component, "SkeletalMesh", None) if component is not None else None
        entry[attr] = mesh._path_name() if mesh else None
    return entry


def _weapon_census() -> list[dict[str, Any]]:
    """What the pawn is carrying from our balances, oldest first.

    This is the evidence bl2_verify compares across a save/quit/reload: the custom slot has
    to still name our part afterwards (F18).
    """
    return [_describe(weapon) for _, weapon in _our_weapons()]


def _our_weapons() -> list[tuple[int, UObject]]:
    controller = get_pc()
    found = []
    for weapon in unrealsdk.find_all("WillowWeapon", exact=False):
        try:
            balance = weapon.DefinitionData.BalanceDefinition
            if weapon.Owner != controller.Pawn or balance is None:
                continue
            if balance._path_name() not in HARNESS_BALANCES:
                continue
        except Exception:  # noqa: BLE001
            continue
        name = str(weapon.Name)
        tail = name.rsplit("_", 1)[-1]
        found.append((int(tail) if tail.isdigit() else 0, weapon))
    return sorted(found, key=lambda pair: pair[0])


def grant() -> str:
    """Hijack one mission's reward to hand out a weapon from our balance, then restore it."""
    global _restore_left
    controller = get_pc()
    balance = unrealsdk.find_object("WeaponBalanceDefinition", HARNESS_BALANCES[0])
    mission = unrealsdk.find_object("MissionDefinition", HARNESS_MISSION)
    backups = _reward_backups()
    if HARNESS_MISSION not in backups:
        # the first hijack of this mission by ANY pipeline mod sees the pristine table
        backups[HARNESS_MISSION] = {
            "mission": mission,
            "game_stage": mission.GameStage,
            "reward_items": list(mission.Reward.RewardItems),
            "reward_pools": list(mission.Reward.RewardItemPools),
        }
    mission.GameStage = controller.PlayerReplicationInfo.ExpLevel
    mission.Reward.RewardItems = [balance]
    mission.Reward.RewardItemPools = []
    controller.ServerGrantMissionRewards(mission, False)
    _restore_left = 300
    restore_tick.enable()
    return f"granted {HARNESS_BALANCES[0]} at level {mission.GameStage}"


def _restore_reward() -> None:
    backup = _reward_backups().pop(HARNESS_MISSION, None)
    if backup is None:
        return  # nothing hijacked, or another pipeline mod already put the table back
    mission = backup["mission"]
    mission.GameStage = backup["game_stage"]
    mission.Reward.RewardItems = backup["reward_items"]
    mission.Reward.RewardItemPools = backup["reward_pools"]


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def restore_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _restore_left
    _restore_left -= 1
    if _restore_left <= 0:
        _restore_reward()
        restore_tick.disable()


def equip(which: int) -> str:
    controller = get_pc()
    manager = controller.GetPawnInventoryManager()
    weapons = _our_weapons()
    if len(weapons) < -which:
        return f"only {len(weapons)} of our weapons on the pawn"
    weapon = weapons[which][1]
    slot = int(weapon.QuickSelectSlot)
    if slot > 0:
        manager.EquipWeaponFromSlot(slot)
        return f"equipped slot {slot}"
    held = controller.Pawn.Weapon
    slot = int(held.QuickSelectSlot) if held is not None else 1
    manager.ReadyBackpackInventory(weapon, slot)
    manager.EquipWeaponFromSlot(slot)
    return f"readied and equipped slot {slot}"


@keybind("$mod_name: grant a weapon", "$key_grant")
def kb_grant() -> None:
    _write({"keybind": "$key_grant", "grant": grant(), "census": _weapon_census()})


@keybind("$mod_name: equip newest", "$key_equip_newest")
def kb_equip_newest() -> None:
    _write({"keybind": "$key_equip_newest", "equip": equip(-1), "census": _weapon_census()})


@keybind("$mod_name: equip second newest", "$key_equip_previous")
def kb_equip_previous() -> None:
    _write({"keybind": "$key_equip_previous", "equip": equip(-2), "census": _weapon_census()})


_behind_view = False


@keybind("$mod_name: toggle first/third person", "$key_view")
def kb_toggle_view() -> None:
    """Human review aid: the harness leaves the camera in third person after its sequence."""
    global _behind_view
    _behind_view = not _behind_view
    try:
        get_pc().SetBehindView(_behind_view)
        _write({"keybind": "$key_view", "behind_view": _behind_view})
    except Exception as ex:  # noqa: BLE001
        _write({"keybind": "$key_view", "error": repr(ex)})
''')


def _wall_clock_harness(text: str) -> str:
    """Rewrite the rendered phase machine from tick counts to wall-clock seconds.

    Every wait the M2 harness expressed in viewport ticks (arm delay, the three phase
    gaps, the ADS blend, the view hold) becomes ``time.time()`` arithmetic with the
    60 fps equivalents.  Done as a rewrite of the finished text so the tick version --
    the frozen M2 mod -- is still emitted byte for byte when the option is off.
    """
    rewrites = [
        ("HARNESS_TICKS_AFTER_LOAD = 120",
         "HARNESS_SECONDS_AFTER_LOAD = 2.0\n"
         "#: the three phase gaps, wall clock: tick counts scale with the frame rate (M8 s6)\n"
         "HARNESS_PHASE_SECONDS = (1.5, 1.5, 2.0)"),
        ("_phase = 0\n_wait = 0", "_phase = 0\n_deadline = 0.0"),
        ("    global _phase, _wait\n    controller = get_pc(possibly_loading=True)",
         "    global _phase, _deadline\n    controller = get_pc(possibly_loading=True)"),
        ("    _phase, _wait = 0, HARNESS_TICKS_AFTER_LOAD",
         "    _phase, _deadline = 0, time.time() + HARNESS_SECONDS_AFTER_LOAD"),
        ('    return f"armed, {HARNESS_TICKS_AFTER_LOAD} ticks"',
         '    return f"armed, {HARNESS_SECONDS_AFTER_LOAD}s"'),
        ("    global _phase, _wait", "    global _phase, _deadline"),
        ("    _wait -= 1\n    if _wait > 0:\n        return",
         "    if time.time() < _deadline:\n        return"),
        ("        _wait, _phase = 90, 1", "        _deadline, _phase = time.time() + HARNESS_PHASE_SECONDS[0], 1"),
        ("        _wait, _phase = 90, 2", "        _deadline, _phase = time.time() + HARNESS_PHASE_SECONDS[1], 2"),
        ("        _wait, _phase = 120, 3", "        _deadline, _phase = time.time() + HARNESS_PHASE_SECONDS[2], 3"),
        ("HARNESS_ADS_TICKS = 60", "HARNESS_ADS_SECONDS = 1.0"),
        ("        _phase, _wait = 5, HARNESS_ADS_TICKS",
         "        _phase, _deadline = 5, time.time() + HARNESS_ADS_SECONDS"),
        ("HARNESS_VIEW_HOLD_TICKS = 1800\n_hold_left = 0",
         "HARNESS_VIEW_HOLD_SECONDS = 30.0\n_hold_until = 0.0\n_hold_last = 0.0"),
        (", _hold_left", ", _hold_until"),
        ("        _hold_left = HARNESS_VIEW_HOLD_TICKS",
         "        _hold_until = time.time() + HARNESS_VIEW_HOLD_SECONDS"),
        ("        if _hold_left > 0:\n            hold_tick.enable()",
         "        if _hold_until > time.time():\n            hold_tick.enable()"),
        ("    global _hold_left\n    _hold_left -= 1\n    if _hold_left <= 0:\n"
         "        hold_tick.disable()\n        return\n    if _hold_left % 30 == 0:",
         "    global _hold_last\n    if time.time() >= _hold_until:\n"
         "        hold_tick.disable()\n        return\n    if time.time() - _hold_last >= 0.5:\n"
         "        _hold_last = time.time()"),
        ("#: viewport ticks after phase 3 during which the capture view is re-asserted every 30",
         "#: seconds after phase 3 during which the capture view is re-asserted twice a second"),
        ("#: viewport ticks between StartAltFire and the sight_check record (the zoom blend)",
         "#: seconds between StartAltFire and the sight_check record (the zoom blend)"),
    ]
    for old, new in rewrites:
        text = text.replace(old, new)
    if "_wait" in text or "_hold_left" in text:
        raise ValueError("wall-clock rewrite left a tick counter behind")
    return text


# ------------------------------------------------------------- M7 pieces (conditional)
# Every block below is substituted in only when a spec uses the feature, so a spec that
# does not keeps emitting the exact file it always did.

_PART_OVERRIDES_CALL = '''
    if part.get("overrides"):
        result["overrides"] = apply_overrides(definition, part["overrides"], template)'''

_OVERRIDES_SECTION = '''


# --------------------------------------------------------------------- part overrides (stats)
#: EModifierType by index, for an SDK build without find_enum
MODIFIER_TYPES = {"MT_Scale": 0, "MT_PreAdd": 1, "MT_PostAdd": 2}


def _modifier(name: str) -> Any:
    try:
        return getattr(unrealsdk.find_enum("EModifierType"), name)
    except Exception:  # noqa: BLE001 - older SDK, or a renamed enum: the index still works
        return MODIFIER_TYPES[name]


def _prototype(template: UObject, *fields: str) -> Any:
    """The first entry of the first non-empty array among ``fields`` on the TEMPLATE part.

    The SDK appends a struct by copying one, so a new row needs an existing row of the
    same struct type to clone. It comes from the template, never from the array being
    rewritten: clearing that array would leave the prototype dangling.
    """
    for field in fields:
        array = getattr(template, field, None)
        if array is not None and len(array):
            return array[0]
    return None


def _replace_effects(definition: UObject, field: str, rows: list[dict[str, Any]],
                     template: UObject) -> Any:
    """Make ``definition.<field>`` exactly ``rows`` of (attribute, modifier, constant)."""
    proto = _prototype(template, field, "WeaponAttributeEffects", "ExternalAttributeEffects",
                       "ZoomWeaponAttributeEffects", "ZoomExternalAttributeEffects")
    if proto is None:
        return f"SKIPPED: the template has no attribute-effect row to clone for {field}"
    array = getattr(definition, field)
    array.clear()
    written = []
    for row in rows:
        attribute = _find("Object", row["attribute"])
        if attribute is None:
            written.append(f"SKIPPED {row['attribute']}: not found")
            continue
        array.append(proto)
        entry = array[-1]
        entry.AttributeToModify = attribute
        entry.ModifierType = _modifier(row["modifier"])
        entry.BaseModifierValue.BaseValueConstant = float(row["value"])
        entry.BaseModifierValue.BaseValueAttribute = None
        entry.BaseModifierValue.InitializationDefinition = None
        entry.BaseModifierValue.BaseValueScaleConstant = 1.0
        written.append(f"{row['attribute']} {row['modifier']} {row['value']}")
    return written


def _replace_slot_upgrades(definition: UObject, rows: list[dict[str, Any]],
                           template: UObject) -> Any:
    """Make ``definition.AttributeSlotUpgrades`` exactly ``rows`` of (slot, grade, activate)."""
    proto = _prototype(template, "AttributeSlotUpgrades")
    if proto is None:
        return "SKIPPED: the template has no AttributeSlotUpgrades row to clone"
    array = definition.AttributeSlotUpgrades
    array.clear()
    written = []
    for row in rows:
        array.append(proto)
        entry = array[-1]
        entry.SlotName = row["slot"]
        entry.GradeIncrease = int(row["grade"])
        entry.bActivateSlot = bool(row.get("activate", True))
        written.append(f"{row['slot']} {int(row['grade']):+d}")
    return written


def apply_overrides(definition: UObject, overrides: dict[str, Any],
                    template: UObject) -> dict[str, Any]:
    """The spec's stat edits on the freshly cloned part (the AK's CS2-derived numbers)."""
    result: dict[str, Any] = {}
    for prop, value in (overrides.get("properties") or {}).items():
        setattr(definition, prop, value)
        result[prop] = getattr(definition, prop)
    for prop in overrides.get("clear_arrays") or ():
        array = getattr(definition, prop)
        had = len(array)
        array.clear()
        result[prop] = f"cleared {had} entr{'y' if had == 1 else 'ies'}"
    for prop, path in (overrides.get("object_properties") or {}).items():
        target = _find("Object", path)
        if target is None:
            result[prop] = f"SKIPPED: {path} not found"
            continue
        keep_alive(target)
        setattr(definition, prop, target)
        result[prop] = target._path_name()
    for field, key in (("WeaponAttributeEffects", "weapon_attribute_effects"),
                       ("ExternalAttributeEffects", "external_attribute_effects"),
                       ("ZoomWeaponAttributeEffects", "zoom_weapon_attribute_effects"),
                       ("ZoomExternalAttributeEffects", "zoom_external_attribute_effects")):
        rows = overrides.get(key)
        if rows is not None:
            result[field] = _replace_effects(definition, field, rows, template)
    rows = overrides.get("attribute_slot_upgrades")
    if rows is not None:
        result["AttributeSlotUpgrades"] = _replace_slot_upgrades(definition, rows, template)
    for dotted, value in (overrides.get("values") or {}).items():
        _assign(definition, dotted, _resolve_value(value))
        result[dotted] = repr(value)
    return result'''

_BALANCES_SECTION = '''


# --------------------------------------------------------------------- balances + titles (M7)
def register_title(title: dict[str, Any]) -> dict[str, Any]:
    """Construct the WeaponNamePartDefinition: the name on the card and the red text.

    Cloned from a stock legendary title (priority and level range come along). The red
    text is NoConstraintText on the title\'s first CustomPresentations entry, a sub-object
    of the title, so OUR title gets its own clone of it and the template\'s is untouched.
    A weapon\'s title comes from its parts, so the carrying parts\' TitleList is replaced.
    """
    result: dict[str, Any] = {"title": title["path"]}
    template = unrealsdk.find_object("WeaponNamePartDefinition", title["template"])
    definition = _find("WeaponNamePartDefinition", title["path"])
    if definition is None:
        outer = _find("Package", title["outer"]) or template.Outer
        definition = unrealsdk.construct_object(
            "WeaponNamePartDefinition", outer, title["name"], 0, template)
        result["constructed"] = True
    else:
        result["constructed"] = False
    keep_alive(definition)
    definition.PartName = title["part_name"]
    result["part_name"] = str(definition.PartName)
    if title.get("name_is_unique"):
        # the weapon type supplies a fallback prefix ("Assault") when no part does; a unique
        # name is shown alone, the way the game names its own uniques
        definition.bNameIsUnique = True
        result["name_is_unique"] = True
    if title["red_text"] is not None:
        presentations = list(template.CustomPresentations)
        if presentations:
            pres = _find("AttributePresentationDefinition", f"{title[\'path\']}:RedText")
            if pres is None:
                pres = unrealsdk.construct_object(
                    "AttributePresentationDefinition", definition, "RedText", 0, presentations[0])
            keep_alive(pres)
            pres.NoConstraintText = title["red_text"]
            definition.CustomPresentations = [pres]
            result["red_text"] = str(pres.NoConstraintText)
        else:
            result["red_text"] = "SKIPPED: the template title has no CustomPresentations to clone"
    carried = []
    for part_path in title["on_parts"]:
        part = unrealsdk.find_object("WeaponPartDefinition", part_path)
        part.TitleList = [definition]
        carried.append(f"{part_path}: {[t._path_name() for t in part.TitleList]}")
    result["on_parts"] = carried
    return result


def _weighted_prototype(source: UObject, field: str) -> Any:
    """A WeightedParts row to clone: the template collection\'s own row for that field, else
    any field\'s (the struct is the same; only the weight index differs)."""
    weighted = getattr(source, field).WeightedParts
    if len(weighted):
        return weighted[0]
    for other in ("BarrelPartData", "BodyPartData", "GripPartData", "StockPartData",
                  "SightPartData", "ElementalPartData", "Accessory1PartData",
                  "MaterialPartData", "Accessory2PartData"):
        weighted = getattr(source, other).WeightedParts
        if len(weighted):
            return weighted[0]
    return None


#: our own WeaponTypeDefinitions (own_gestalt); the save hooks record them like balances
OUR_TYPE_PATHS: set[str] = set()
#: balance path -> our type for it; on_weapon_type_pre forces it onto every weapon built
#: from that balance (weapon generation does not take it from the balance, F34)
OUR_TYPE_BY_BALANCE: dict[str, UObject] = {}


def _own_weapon_type(balance: UObject, template: UObject, name: str) -> str:
    """Give ``balance`` its own clone of the template's weapon type, drawing from our gestalt.

    The type is where a weapon gets its gestalt (``WeaponTypeDefinition.GestaltMesh``); a
    balance without its own ``InventoryDefinition`` inherits one through ``BaseDefinition``.
    """
    source, hops = template, 0
    while source is not None and source.InventoryDefinition is None and hops < 8:
        source, hops = source.BaseDefinition, hops + 1
    if source is None or source.InventoryDefinition is None:
        return "SKIPPED: no InventoryDefinition on the template balance or its bases"
    stock_type = source.InventoryDefinition
    path = f"{stock_type.Outer._path_name()}.WT_{name}"
    own_type = _find("WeaponTypeDefinition", path)
    if own_type is None:
        own_type = unrealsdk.construct_object(
            "WeaponTypeDefinition", stock_type.Outer, f"WT_{name}", 0, stock_type)
    keep_alive(own_type)
    own_type.GestaltMesh = active_gestalt()
    balance.InventoryDefinition = own_type
    OUR_TYPE_BY_BALANCE[balance._path_name()] = own_type
    # weapon generation takes the type from the part-list collection, not the balance: a
    # collection cloned from the template still names the stock type (2026-09-22: the laser
    # was built on WT_Dahl_AssaultRifle and drew nothing, its parts are not in the stock
    # gestalt). Every collection the balance uses is pointed at our type.
    for collection in {id(c): c for c in (balance.RuntimePartListCollection,
                                          balance.WeaponPartListCollection) if c is not None}.values():
        collection.AssociatedWeaponType = own_type
    OUR_TYPE_PATHS.add(own_type._path_name())
    return f"{own_type._path_name()} (from {stock_type._path_name()}) -> {own_type.GestaltMesh._path_name()}"


def register_balance(bal: dict[str, Any]) -> dict[str, Any]:
    """Construct the balance as a clone of its template with its OWN runtime part lists.

    The clone keeps the template\'s rarity, manufacturer and base definition. Its
    RuntimePartListCollection is a clone of the template\'s, with every field the spec names
    replaced by exactly the listed parts (fields not named keep the template\'s rows), and
    the cooked WeaponPartListCollection is pointed at the same object so nothing that
    consults either list sees the template\'s parts. Then the title, then the item pools.
    Idempotent: every object is looked up before it is constructed.
    """
    result: dict[str, Any] = {"balance": bal["path"]}
    template = unrealsdk.find_object("WeaponBalanceDefinition", bal["template"])
    balance = _find("WeaponBalanceDefinition", bal["path"])
    if balance is None:
        outer = _find("Package", bal["outer"]) or template.Outer
        balance = unrealsdk.construct_object(
            "WeaponBalanceDefinition", outer, bal["name"], 0, template)
        result["constructed"] = True
    else:
        result["constructed"] = False
    keep_alive(balance)

    source = template.RuntimePartListCollection
    collection_path = f"{bal[\'path\']}:{bal[\'collection_name\']}"
    collection = _find("WeaponPartListCollectionDefinition", collection_path)
    if collection is None:
        collection = unrealsdk.construct_object(
            "WeaponPartListCollectionDefinition", balance, bal["collection_name"], 0, source)
    keep_alive(collection)
    balance.RuntimePartListCollection = collection
    balance.WeaponPartListCollection = collection
    result["collection"] = collection._path_name()$own_type_call

    lists: dict[str, Any] = {}
    for field, part_paths in bal["part_lists"].items():
        proto = _weighted_prototype(source, field)
        if proto is None:
            lists[field] = "SKIPPED: the template collection has no WeightedParts row to clone"
            continue
        parts = [unrealsdk.find_object("WeaponPartDefinition", p) for p in part_paths]
        slot = getattr(collection, field)
        weighted = slot.WeightedParts
        weighted.clear()
        for part in parts:
            weighted.append(proto)
            weighted[-1].Part = part
        slot.bEnabled = True
        lists[field] = [e.Part._path_name() if e.Part else None for e in weighted]
    result["part_lists"] = lists

    if bal["title"]:
        result["title"] = register_title(bal["title"])

    pools = []
    for pool_path in bal["pools"]:
        pool = _find("ItemPoolDefinition", pool_path)
        if pool is None:
            pools.append(f"{pool_path}: not found")
            continue
        items = pool.BalancedItems
        if any(e.InvBalanceDefinition is not None
               and e.InvBalanceDefinition._path_name() == bal["path"] for e in items):
            pools.append(f"{pool_path}: already present")
            continue
        if not len(items):
            pools.append(f"{pool_path}: empty, no entry to clone")
            continue
        items.append(items[-1])
        items[-1].InvBalanceDefinition = balance
        items[-1].ItmPoolDefinition = None
        pools.append(f"{pool_path}: {len(items)} entries")
    result["pools"] = pools
    return result'''

_EXTRA_PACKAGES_SECTION = '''


# --------------------------------------------------------------------- extra packages
def load_extra_packages() -> list[str]:
    """Load the spec\'s further packages and root them (F17); their objects are found
    by path afterwards (register_material roots each texture it uses)."""
    loaded = []
    for name in EXTRA_PACKAGES:
        package = unrealsdk.load_package(name)
        keep_alive(package)
        loaded.append(package._path_name())
    return loaded'''

_MATERIALS_SECTION = '''


# --------------------------------------------------------------------- materials (M7)
def register_material(mat: dict[str, Any]) -> dict[str, Any]:
    """Construct one MaterialInstanceConstant: empty, parented to a stock MIC, parameters set.

    Parenting (rather than cloning with a template) keeps the compiled shader the parent\'s;
    a runtime clone would carry bHasStaticPermutationResource without the resource behind
    it. Textures come from EXTRA_PACKAGES or cooked packages and are rooted here.
    """
    result: dict[str, Any] = {"material": mat["path"]}
    parent = unrealsdk.find_object("MaterialInterface", mat["parent"])
    mic = _find("MaterialInstanceConstant", mat["path"])
    if mic is None:
        outer = _find("Package", mat["outer"]) or parent.Outer
        mic = unrealsdk.construct_object("MaterialInstanceConstant", outer, mat["name"], 0, None)
        result["constructed"] = True
    else:
        result["constructed"] = False
    keep_alive(mic)
    mic.SetParent(parent)
    result["parent"] = parent._path_name()
    textures = []
    for pname, tex_path in mat["texture_parameters"].items():
        texture = _find("Texture", tex_path)
        if texture is None:
            textures.append(f"SKIPPED {pname}: {tex_path} not found")
            continue
        keep_alive(texture)
        mic.SetTextureParameterValue(pname, texture)
        textures.append(f"{pname}={tex_path}")
    result["textures"] = textures
    vectors = []
    for pname, rgba in mat["vector_parameters"].items():
        colour = unrealsdk.make_struct("LinearColor", R=rgba[0], G=rgba[1], B=rgba[2], A=rgba[3])
        mic.SetVectorParameterValue(pname, colour)
        vectors.append(f"{pname}={tuple(rgba)}")
    result["vectors"] = vectors
    scalars = []
    for pname, value in mat["scalar_parameters"].items():
        mic.SetScalarParameterValue(pname, float(value))
        scalars.append(f"{pname}={value}")
    result["scalars"] = scalars
    return result'''

_INVENTORY_EXTRA = '''


def _open_inventory() -> str:
    """harness_pose "inventory": open the status menu on the inventory for the capture."""
    get_pc().ShowStatusMenu_Inventory()
    return "ShowStatusMenu_Inventory"'''

_HIP_EXTRA = '''


def _no_zoom() -> str:
    """harness_pose "hip": the sight_check without aiming down sights."""
    return "skipped (hip pose)"'''

_PREFIX_HOOKS = '''


# --------------------------------------------------------------------- no prefix (M8)
def _suppress_prefix(weapon: UObject) -> bool:
    """Null PrefixPartDefinition on a weapon of a suppress_prefix balance.

    The weapon type supplies a fallback prefix ("Assault") when no part carries one, and
    bNameIsUnique on the title does not hide it; the slot has to be empty. Fired after the
    game names the weapon (ChooseRandomNameParts) and after it initialises (InitializeInternal,
    which also covers weapons rebuilt from a save).
    """
    try:
        definition = weapon.DefinitionData
        balance = definition.BalanceDefinition
        if balance is None or balance._path_name() not in SUPPRESS_PREFIX_BALANCES:
            return False
        if definition.PrefixPartDefinition is None:
            return False
        definition.PrefixPartDefinition = None
        return True
    except Exception:  # noqa: BLE001 - half-built weapons throw on attribute access
        return False


_prefix_suppressed = 0


@hook("WillowGame.WillowWeapon:ChooseRandomNameParts", Type.POST)
def on_weapon_name_parts(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _prefix_suppressed
    if _suppress_prefix(obj):
        _prefix_suppressed += 1
        _write({"prefix_hook": "ChooseRandomNameParts", "weapon": obj._path_name(),
                "count": _prefix_suppressed})


@hook("WillowGame.WillowWeapon:InitializeInternal", Type.POST)
def on_weapon_init(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _prefix_suppressed
    if _suppress_prefix(obj):
        _prefix_suppressed += 1
        _write({"prefix_hook": "InitializeInternal", "weapon": obj._path_name(),
                "count": _prefix_suppressed})'''

_WAVE_MIXER_SOURCE = Path(__file__).with_name("wave_mixer.py").read_text(encoding="utf-8")
assert "'''" not in _WAVE_MIXER_SOURCE, "wave_mixer.py is embedded in a ''' string"

_WAVE_MIXER_SECTION = '''


# --------------------------------------------------------------------- wave mixer
# Concurrent wav playback through winmm waveOut: one stream per play(), so a shot, a dash and a
# slide overlap instead of cutting each other off (Win32 PlaySound, the old path, owns ONE slot
# per process). This is src/bl2_partgen/wave_mixer.py embedded verbatim so the mod stays one
# file; the Armory's pack runtime renders it the same way.
import types as _types

_WAVE_MIXER_SOURCE = r\'\'\'$wave_mixer_source\'\'\'
wave_mixer = _types.ModuleType("wave_mixer")
wave_mixer.__file__ = os.path.join(MOD_DIR, "wave_mixer.py")
exec(compile(_WAVE_MIXER_SOURCE, wave_mixer.__file__, "exec"), wave_mixer.__dict__)  # noqa: S102
del _WAVE_MIXER_SOURCE'''.replace("$wave_mixer_source", _WAVE_MIXER_SOURCE)

_FIRE_SOUND_SECTION = '''


# --------------------------------------------------------------------- fire sound
# BL2's audio is Wwise banks with no way to hand the engine a loose file, so the shot goes
# round the engine: a 16-bit PCM wav on its own waveOut stream (wave_mixer above). It ignores
# the in-game volume slider (the level is baked into the emitted copy) and is not positional,
# but it overlaps freely with every other clip, ours or another mod's.
FIRE_SOUND_FILE = os.path.join(MOD_DIR, "sounds", MOD_NAME + "_fire.wav")
FIRE_SOUND_MUTE_STOCK = $mute_stock
if os.path.isfile(FIRE_SOUND_FILE):
    wave_mixer.preload(FIRE_SOUND_FILE)  # decode once, pay the first device open now

#: (weapon type, [its FireSounds events]) blanked by the PRE hook, put back by the POST
_muted: list[tuple[Any, list[Any]]] = []
_fire_counts = {"shots": 0, "played": 0, "muted": 0, "errors": 0}


def _unmute() -> None:
    while _muted:
        weapon_type, events = _muted.pop()
        try:
            for entry, event in zip(weapon_type.FireSounds, events):
                entry.Event = event
        except Exception as ex:  # noqa: BLE001
            _fire_counts["errors"] += 1
            _log(f"fire sound: could not restore {weapon_type}: {ex!r}")


def _is_our_shot(weapon: UObject) -> bool:
    try:
        balance = weapon.DefinitionData.BalanceDefinition
        if balance is None or balance._path_name() not in OUR_BALANCE_PATHS:
            return False
        from mods_base import get_pc

        pc = get_pc(possibly_loading=True)
        return pc is not None and pc.Pawn is not None and weapon.Owner == pc.Pawn
    except Exception:  # noqa: BLE001 - half-built weapons throw on attribute access
        return False


@hook("WillowGame.WillowWeapon:FireAmmunition", Type.PRE)
def on_fire_pre(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    _unmute()  # a POST that never ran (the call threw) must not leave a type silent
    if not _is_our_shot(obj):
        return
    _fire_counts["shots"] += 1
    if FIRE_SOUND_MUTE_STOCK:
        try:
            weapon_type = obj.DefinitionData.WeaponTypeDefinition
            events = [entry.Event for entry in weapon_type.FireSounds]
            for entry in weapon_type.FireSounds:
                entry.Event = None
            _muted.append((weapon_type, events))
            _fire_counts["muted"] += 1
        except Exception as ex:  # noqa: BLE001
            _fire_counts["errors"] += 1
            _log(f"fire sound: could not mute the stock shot: {ex!r}")
    if os.path.isfile(FIRE_SOUND_FILE):
        try:
            # its own stream, started on the mixer's worker thread: returns at once
            wave_mixer.play(FIRE_SOUND_FILE)
            _fire_counts["played"] += 1
        except Exception as ex:  # noqa: BLE001 - a sound must never break a shot
            _fire_counts["errors"] += 1
            _log(f"fire sound: play failed: {ex!r}")
    if _fire_counts["shots"] in (1, 10, 100):
        _write({"fire_sound": dict(_fire_counts), "weapon": obj._path_name()})


@hook("WillowGame.WillowWeapon:FireAmmunition", Type.POST)
def on_fire_post(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    _unmute()'''

_SAVE_BALANCE_RECORD = '''
            # a runtime balance is not cooked either: record it beside the parts (M7)
            balance = definition.BalanceDefinition
            if balance is not None and balance._path_name() in OUR_BALANCE_PATHS:
                keep_alive(balance)
                slots = dict(slots, BalanceDefinition=balance._path_name())
            # own_gestalt: the runtime weapon type is not cooked either
            weapon_type = getattr(definition, "WeaponTypeDefinition", None)
            if weapon_type is not None and weapon_type._path_name() in OUR_TYPE_PATHS:
                slots = dict(slots, WeaponTypeDefinition=weapon_type._path_name())'''

_LOAD_BALANCE_RESTORE = '''
                if field == "BalanceDefinition":
                    balance = _find("WeaponBalanceDefinition", part_path)
                    if balance is None:
                        missing.append(part_path)
                    else:
                        definition.BalanceDefinition = balance
                        restored.append(f"{int(definition.UniqueId)}:BalanceDefinition={part_path}")
                    continue
                if field == "WeaponTypeDefinition":
                    weapon_type = _find("WeaponTypeDefinition", part_path)
                    if weapon_type is None:
                        missing.append(part_path)
                    else:
                        definition.WeaponTypeDefinition = weapon_type
                        restored.append(f"{int(definition.UniqueId)}:WeaponTypeDefinition={part_path}")
                    continue'''

_HARNESS_BALANCES = '''HARNESS_BALANCES = tuple(
    dict.fromkeys(target["balance"] for part in PARTS for target in part["register_in"])
)'''

_HARNESS_BALANCES_M7 = '''#: our own balance(s) first: that is what the grant hands out
HARNESS_BALANCES = tuple(dict.fromkeys(
    [*(bal["path"] for bal in BALANCES),
     *(target["balance"] for part in PARTS for target in part["register_in"])]
))'''

_HARNESS_TARGET = '''HARNESS_TARGET = HARNESS_PART["register_in"][0]'''

_HARNESS_TARGET_M7 = '''HARNESS_TARGET = (HARNESS_PART["register_in"] or [
    {"balance": bal["path"], "field": field}
    for bal in BALANCES for field, paths in bal["part_lists"].items()
    if HARNESS_PART["path"] in paths
])[0]'''

_DESCRIBE_EXTRA = '''
        "balance": (definition.BalanceDefinition._path_name()
                    if definition.BalanceDefinition is not None else None),'''

_HARNESS_TARGETS = '''
    {"balance": target["balance"], "field": target["field"],
     "new": part["path"], "template": part["template_part"], "part_name": part["name"]}
    for part in PARTS for target in part["register_in"]'''

_HARNESS_TARGETS_M7 = '''
    [*({"balance": target["balance"], "field": target["field"],
        "new": part["path"], "template": part["template_part"], "part_name": part["name"]}
       for part in PARTS for target in part["register_in"]),
     # and every field of our own balance(s) that holds one of our parts
     *({"balance": bal["path"], "field": field,
        "new": part["path"], "template": part["template_part"], "part_name": part["name"]}
       for bal in BALANCES for field, paths in bal["part_lists"].items()
       for part in PARTS if part["path"] in paths)]'''


# With an explicit capture view the harness HOLDS it after phase 3: an F12 (Steam binds it
# to screenshots, and it is our own view toggle) landing between the camera step and the
# capture burst flipped a first-person run back to third person once (M7 §3).
_VIEW_HOLD_ARM = """
        _hold_left = HARNESS_VIEW_HOLD_TICKS"""
_VIEW_HOLD_START = """
        if _hold_left > 0:
            hold_tick.enable()"""
_VIEW_HOLD_TICK = """

#: viewport ticks after phase 3 during which the capture view is re-asserted every 30
HARNESS_VIEW_HOLD_TICKS = 1800
_hold_left = 0


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def hold_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    \"\"\"Keep the camera in HARNESS_CAPTURE_VIEW through the capture window, whatever F12 does.\"\"\"
    global _hold_left
    _hold_left -= 1
    if _hold_left <= 0:
        hold_tick.disable()
        return
    if _hold_left % 30 == 0:
        try:
            get_pc().SetBehindView(HARNESS_CAPTURE_VIEW == "third")
        except Exception:  # noqa: BLE001 - between maps there may be no controller
            pass
"""


# harness_pose "ads": phase 3 zooms and hands over to phase 5, which records the sight
# geometry after the zoom blend and only then lets the done record out.
_ADS_PHASE3 = '''
        _step(record, "zoom", _zoom)
        _phase, _wait = 5, HARNESS_ADS_TICKS
    elif _phase == 5:
        _step(record, "sight_check", _sight_check)
        _phase = 4'''

_ADS_SECTION = '''

# --------------------------------------------------------------------- aim down sights
#: viewport ticks between StartAltFire and the sight_check record (the zoom blend)
HARNESS_ADS_TICKS = 60


def _zoom() -> str:
    """Aim down sights: alt fire is the zoom in Borderlands 2."""
    get_pc().StartAltFire()
    return "StartAltFire"


def _vec(value: Any) -> list[float]:
    return [float(value.X), float(value.Y), float(value.Z)]


def _rot(value: Any) -> list[int]:
    return [int(value.Pitch), int(value.Yaw), int(value.Roll)]


def _out_vector() -> Any:
    """Out params must be passed on this SDK; a fresh struct is the placeholder."""
    return unrealsdk.make_struct("Vector")


def _out_rotator() -> Any:
    return unrealsdk.make_struct("Rotator")


def _sight_check() -> dict[str, Any]:
    """Where the camera is and where our sockets are, in world space, while zoomed.

    The iron-sight alignment is a geometric question -- are EyeSocket2, RearSight and
    FrontSight on the camera's forward ray? -- so it is answered with numbers, not by
    eyeballing a capture. Every read is guarded: a missing function on some SDK build
    must not stop the done record. Functions with out params get placeholder structs
    and hand the values back in the returned tuple.
    """
    controller = get_pc()
    weapon = controller.Pawn.Weapon
    out: dict[str, Any] = {}

    def zoom_socket() -> dict[str, Any]:
        result = weapon.GetZoomSocket(_out_vector(), _out_rotator())
        return {"return": str(result[0]), "location": _vec(result[1]), "rotation": _rot(result[2])}

    def ironsights_socket() -> str:
        result = weapon.GetIronsightsSocket("None")
        return str(result[1] if isinstance(result, tuple) else result)

    def view_point() -> dict[str, Any]:
        result = controller.GetPlayerViewPoint(_out_vector(), _out_rotator())
        location, rotation = result[-2], result[-1]
        return {"location": _vec(location), "rotation": _rot(rotation),
                "fov": float(controller.FOVAngle)}

    for key, read in (
        ("zoomed", lambda: bool(weapon.bZoomed)),
        ("zoom_socket", zoom_socket),
        ("ironsights_socket", ironsights_socket),
        ("fire_start", lambda: _vec(weapon.GetPhysicalFireStartLoc())),
        ("name", lambda: str(weapon.GetHumanReadableName())),
        ("camera", view_point),
        ("pawn_location", lambda: _vec(controller.Pawn.Location)),
    ):
        try:
            out[key] = read()
        except Exception as ex:  # noqa: BLE001
            out[key] = f"unavailable: {ex!r}"
    sockets: dict[str, Any] = {}
    try:
        component = weapon.FirstPersonMesh
        prefixes = tuple(f"{frag['name']}_" for frag in FRAGMENTS)
        for socket in component.SkeletalMesh.Sockets:
            name = str(socket.SocketName)
            if not name.startswith(prefixes):
                continue
            try:
                result = component.GetSocketWorldLocationAndRotation(
                    name, _out_vector(), _out_rotator())
                sockets[name] = {"location": _vec(result[1]), "rotation": _rot(result[2])}
            except Exception as ex:  # noqa: BLE001
                sockets[name] = f"unavailable: {ex!r}"
    except Exception as ex:  # noqa: BLE001
        sockets["error"] = repr(ex)
    out["sockets"] = sockets
    return out'''


#: The force machinery of the phase machine, in two shapes.  ``_FORCE_SINGLE`` is the
#: M2 original, frozen byte for byte: one part, one part list.  ``_FORCE_MULTI`` is
#: emitted instead as soon as the spec registers into more than one (balance, field)
#: -- M6's AK is five fragments and four parts, and a grant that forced only the
#: barrel list would hand out an AK barrel bolted to a stock Vladof body.  Which one
#: is used is decided in :func:`render_mod`; everything around them is identical, so
#: a one-part spec still emits exactly the file it emitted before.
_FORCE_SINGLE = '''#: what the part list held before force_barrel() started overwriting it
_barrel_backup: list[Any] | None = None
_phase = 0
_wait = 0


def _harness_list() -> Any:
    """The WeightedParts array the harness forces: HARNESS_TARGET's runtime part list."""
    balance = unrealsdk.find_object("WeaponBalanceDefinition", HARNESS_TARGET["balance"])
    return getattr(balance.RuntimePartListCollection, HARNESS_TARGET["field"]).WeightedParts


def force_barrel(part_path: str | None) -> str:
    """Temporarily make EVERY entry of the target part list one part; None restores them.

    HARNESS ONLY, and this is what makes the grant deterministic: the engine picks one of
    the weighted entries, so with all of them the same part the granted weapon is certain to
    carry it. The backup is taken once, at the first force, and phase 2 puts the list back
    exactly as it was -- a part list left overwritten would stop every other part rolling.
    """
    global _barrel_backup
    weighted = _harness_list()
    if part_path is None:
        if _barrel_backup is not None:
            for entry, original in zip(weighted, _barrel_backup):
                entry.Part = original
            _barrel_backup = None
        return "restored"
    if _barrel_backup is None:
        _barrel_backup = [entry.Part for entry in weighted]
    part = unrealsdk.find_object("WeaponPartDefinition", part_path)
    for entry in weighted:
        entry.Part = part
    return part_path'''

_FORCE_MULTI = '''#: every (balance, field) any of our parts registers into, with the part that goes in
#: it and the stock part it was cloned from. The harness forces ALL of them at once, so
#: the granted weapon is our whole set rather than one part on an otherwise stock gun.
HARNESS_TARGETS: tuple[dict[str, Any], ...] = tuple($harness_targets_expr
)

#: what each of those lists held before force_barrel() started overwriting it,
#: keyed by the HARNESS_TARGETS index
_barrel_backup: dict[int, list[Any]] | None = None
_phase = 0
_wait = 0


def _harness_list(target: dict[str, Any]) -> Any:
    """The WeightedParts array of one target's runtime part list."""
    balance = unrealsdk.find_object("WeaponBalanceDefinition", target["balance"])
    return getattr(balance.RuntimePartListCollection, target["field"]).WeightedParts


def force_barrel(which: str | None) -> str:
    """Force every one of our part lists to one part each; None restores them all.

    ``which`` is "new" (our parts) or "template" (the stock parts they were cloned
    from, for the A/B half of the phase machine).

    HARNESS ONLY, and this is what makes the grant deterministic: the engine picks one of
    the weighted entries, so with all of them the same part the granted weapon is certain to
    carry it. The backup is taken once, at the first force, and phase 2 puts every list back
    exactly as it was -- a part list left overwritten would stop every other part rolling.
    """
    global _barrel_backup
    if which is None:
        if _barrel_backup is not None:
            for index, target in enumerate(HARNESS_TARGETS):
                for entry, original in zip(_harness_list(target), _barrel_backup[index]):
                    entry.Part = original
            _barrel_backup = None
        return "restored"
    if _barrel_backup is None:
        _barrel_backup = {index: [entry.Part for entry in _harness_list(target)]
                          for index, target in enumerate(HARNESS_TARGETS)}
    forced = []
    for target in HARNESS_TARGETS:
        path = target[which]
        part = unrealsdk.find_object("WeaponPartDefinition", path)
        for entry in _harness_list(target):
            entry.Part = part
        forced.append(f"{target['field']}={path}")
    return ", ".join(forced)'''


_HARNESS_AUTO = Template('''

# --------------------------------------------------------------------- harness phase machine
# The per-map sequence src/bl2_verify/m2_mod_template.py ran by hand, kept record for record
# so bl2_verify's unattended loop can read THIS mod's status file in place of the M2 one:
#
#   phase 0  census_before, put the stock fragment back, grant a STOCK weapon
#   phase 1  describe it, force our part onto every list entry, grant again
#   phase 2  describe that one, restore the part list, equip the newest
#   phase 3  FOV + behind view, describe what is held, "done": true
#
# It is armed by the map-load hook and counts viewport ticks, because the pawn and its
# inventory are not ready the instant the loading movie goes away.
HARNESS_TICKS_AFTER_LOAD = 120
HARNESS_FOV = $harness_fov
HARNESS_BEHINDVIEW = $harness_behindview$harness_view_line

$force_section


def force_stock_fragment() -> str:
    """Put the stock fragment back on our template part before the A/B grant.

    A text mod that retargeted it (the AK-47 mod does this to the Shredifier barrel) would
    otherwise make the "stock" half of the comparison show something that is not stock.
    """
    template = unrealsdk.find_object("WeaponPartDefinition", HARNESS_TEMPLATE_PART)
    template.GestaltModeSkeletalMeshName = HARNESS_STOCK_FRAGMENT
    return str(template.GestaltModeSkeletalMeshName)


def _newest() -> dict[str, Any] | None:
    weapons = _our_weapons()
    return _describe(weapons[-1][1]) if weapons else None


def _held() -> dict[str, Any] | None:
    weapon = get_pc().Pawn.Weapon
    return _describe(weapon) if weapon is not None else None


def _camera() -> str:
    """Harness-only console/controller calls: they belong to the capture step, not to a mod."""
    controller = get_pc()
    controller.ConsoleCommand(f"FOV {HARNESS_FOV}")
$camera_body


def _step(record: dict[str, Any], name: str, action: Any) -> Any:
    """Run one step of a phase; a failure is recorded in place and the rest carries on."""
    try:
        record[name] = action()
    except Exception as ex:  # noqa: BLE001
        record[name] = f"FAILED {ex!r}\\n{traceback.format_exc()}"
    return record[name]


def arm_harness() -> str:
    """Re-arm the phase machine, HARNESS_TICKS_AFTER_LOAD ticks from now."""
    global _phase, _wait
    controller = get_pc(possibly_loading=True)
    if controller is None or controller.Pawn is None:
        return "no pawn: not a gameplay map"
    _phase, _wait = 0, HARNESS_TICKS_AFTER_LOAD
    seq_tick.enable()
    return f"armed, {HARNESS_TICKS_AFTER_LOAD} ticks"


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def seq_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    """One phase per wait, then unhook until the next map load."""
    global _phase, _wait$view_hold_global
    _wait -= 1
    if _wait > 0:
        return
    record: dict[str, Any] = {"phase": _phase}
    if _phase == 0:
        _step(record, "census_before", _weapon_census)
        _step(record, "force_stock_fragment", force_stock_fragment)
        if not _registered:
            # mod loaded mid-session: register now and hope no save references the part (F16)
            record["late_registration"] = menu_setup().get("ok")
        _step(record, "force_barrel_stock", $force_stock_expr)
        _step(record, "grant_stock", grant)
        _wait, _phase = 90, 1
    elif _phase == 1:
        _step(record, "stock_weapon", _newest)
        _step(record, "force_barrel_new", $force_new_expr)
        _step(record, "grant_new", grant)
        _wait, _phase = 90, 2
    elif _phase == 2:
        _step(record, "new_weapon", _newest)
        _step(record, "restore_barrel_list", $force_restore_expr)
        _step(record, "equip_newest", $harness_equip_expr)
        _wait, _phase = 120, 3
    elif _phase == 3:
        _step(record, "camera", _camera)
        _step(record, "held", _held)
        _phase = 4$view_hold_arm$ads_phase3
    if _phase == 4:
        seq_tick.disable()
        record["done"] = True$view_hold_start$fire_test_start
    _write(record)
    _log(f"harness phase {record['phase']} done")
$ads_section$view_hold_tick''')


_COMPONENT_FOOTER = Template('''

# --------------------------------------------------------------------- component export
# No build_mod here: this module is one weapon inside an Armory (sdk_mods/<armory>/weapons/)
# and the Armory's __init__.py wires every component's hooks into ONE mod and enables them.
COMPONENT: dict[str, Any] = {
    "name": MOD_NAME,
    "package": PACKAGE,
    "hooks": [$hook_list],
    "balances": [$balance_paths],
    "our_part_paths": OUR_PART_PATHS,
    "slot_fields": SLOT_FIELDS,
    "menu_setup": menu_setup,
    "status": _status,
}
''')


_FOOTER = Template('''

# --------------------------------------------------------------------- wiring
build_mod(
    name="$mod_name",
    description="$short_description",
    hooks=[$hook_list],$keybind_arg
)

# Hooks are enabled here rather than left to the mod menu: a mod that has never been
# enabled is not auto-enabled on the launch that installs it, so its hooks would never
# bind and nothing would happen, with nothing in the log to say why (F8).
$enable_lines
_log("armed; registration runs at the main menu")
''')


# ---------------------------------------------------------------------- rendering
def _status_file_block(status_path: str | None) -> str:
    """``DEFAULT_STATUS_FILE``: beside the mod, or wherever ``options.status_path`` says."""
    if not status_path:
        return 'DEFAULT_STATUS_FILE = os.path.join(MOD_DIR, "status.json")'
    return (
        "#: options.status_path from the spec, so the verify loop needs no control file;\n"
        "#: control.json's status_path still wins over it\n"
        f"DEFAULT_STATUS_FILE = {str(status_path)!r}"
    )


def render_mod(
    resolved: ResolvedSpec, spec_name: str = "<spec>", catalog_stamp: str = "",
    component: bool = False,
) -> str:
    """Render the mod's ``__init__.py``.

    ``component=True`` renders the same module for use inside an Armory: no ``build_mod``,
    no ``enable()`` lines, a ``COMPONENT`` export at the end, and the status/control files
    named after the mod so several components can share one folder (M10).
    """
    spec = resolved.spec
    options = spec.options
    # the spec path lands inside a docstring: a Windows backslash there is an escape sequence
    spec_name = str(spec_name).replace("\\", "/")
    harness = options.test_harness
    harness_auto = harness and options.harness_auto
    force = options.force_template_fragment_on_map_load
    late_objects = any(o.when == "map_load" for o in spec.objects)
    map_load = bool(force) or harness or late_objects
    balances = bool(resolved.balances)
    has_overrides = any(part.overrides is not None for part in resolved.parts)
    # the M2 harness's record key names, so bl2_verify reads one status format either way
    record_keys = (
        {"save_key": "save_hook", "load_key": "load_hook", "validate_key": "validate_hook"}
        if harness
        else {"save_key": "hook", "load_key": "hook", "validate_key": "hook"}
    )

    when_extra_lines = []
    if options.save_roundtrip:
        when_extra_lines.append(
            "  save / load    GeneratePlayerSaveGame PRE records {UniqueId: {slot: part}} under\n"
            "                 saves/<SaveGameName>.json; ApplyPlayerSaveGameData PRE writes them\n"
            "                 back before the game builds the weapons (F18)"
        )
    if options.validate_override:
        when_extra_lines.append(
            "  validate       ValidateWeaponDefinition PRE returns (Block, True) for definitions\n"
            "                 that use one of our parts; the cooked-data check rejects them (F19)"
        )
    if map_load:
        when_extra_lines.append(
            "  map load       WillowClientDisableLoadingMovie POST: late registration if the mod\n"
            "                 was loaded mid-session, plus the fixups below"
        )
    if harness_auto:
        when_extra_lines.append(
            "  harness        map load arms seq_tick: census, grant stock, grant ours, equip,\n"
            "                 camera -- one phase per wait, ending in a done record. Only\n"
            "                 under options.test_harness: bl2_verify instrumentation"
        )

    sections = [
        _HEADER.substitute(
            mod_name=spec.name,
            description=(_wrap(spec.description, "") + "\n") if spec.description else "",
            spec_name=spec_name,
            catalog_stamp=catalog_stamp or "(unstamped)",
            summary=_summary_lines(resolved),
            when_extra="\n".join(when_extra_lines),
            package=spec.package_stem,
            save_package=options.save_package or spec.package_stem,
            mesh_path=spec.mesh_path,
            gestalt_def=resolved.gestalt_def_path,
            own_gestalt_consts=(_OWN_GESTALT_CONSTS % spec.name if options.own_gestalt else ""),
            stock_mesh=resolved.stock_mesh_path,
            menu_ticks=options.menu_ticks,
            keep_alive=str(bool(options.keep_alive)),
            status_file=_status_file_block(options.status_path),
            version=spec.version,
            author=spec.author,
            mods_base_extra=", get_pc, keybind" if harness else "",
            fragments=_fragments_literal(resolved),
            parts=_parts_literal(resolved),
            balances_literal=_balances_literal(resolved),
            materials_literal=_materials_literal(resolved) + _objects_literal(resolved),
            our_paths_extra=" | OUR_TITLE_PATHS" if balances else "",
            menu_extra=(
                "; then construct the\n                 balance(s), their own part lists, "
                "title(s), and append them to their item pools" if balances else ""
            ),
            slot_fields=textwrap.fill(
                ", ".join(f'"{f}"' for f in SLOT_FIELDS) + ",",
                width=96, initial_indent="    ", subsequent_indent="    ",
            ),
        ),
        _REGISTER.substitute(
            gestalt_load=_GESTALT_LOAD_OWN if options.own_gestalt else _GESTALT_LOAD_SHARED,
            **_socket_override_blocks(resolved),
            **_part_fragment_block(resolved),
            **_bounds_override_block(resolved),
            part_overrides_call=_PART_OVERRIDES_CALL if has_overrides else "",
            overrides_section=_OVERRIDES_SECTION if has_overrides else "",
            balances_section=(_BALANCES_SECTION.replace(
                "$own_type_call", _OWN_TYPE_CALL if options.own_gestalt else "")
                if balances else ""),
            materials_section=(
                (_EXTRA_PACKAGES_SECTION if resolved.extra_packages else "")
                + (_MATERIALS_SECTION if resolved.materials else "")
                + (_VALUE_HELPERS if spec.objects or any(
                    part.overrides is not None and part.overrides.values
                    for part in resolved.parts) else "")
                + (_OBJECTS_SECTION if spec.objects else "")
            ),
            extra_packages_step=(
                '\n        record["extra_packages"] = load_extra_packages()'
                if resolved.extra_packages else ""
            ),
            objects_step=(
                '\n        record["objects"] = [register_object(o) for o in OBJECTS'
                '\n                             if o.get("when", "menu") == "menu"]'
                if spec.objects else ""
            ),
            materials_step=(
                '\n        record["materials"] = [register_material(m) for m in MATERIALS]'
                if resolved.materials else ""
            ),
            balances_step=(
                '\n        record["balances"] = [register_balance(b) for b in BALANCES]'
                if balances else ""
            ),
        ),
    ]
    record_keys.update(
        save_balance_record=_SAVE_BALANCE_RECORD if balances else "",
        load_balance_restore=_LOAD_BALANCE_RESTORE if balances else "",
    )

    hooks = ["menu_tick"]
    enables = ["menu_tick.enable()"]
    if options.save_roundtrip:
        sections.append(_SAVE_HOOKS.substitute(record_keys))
        hooks += ["on_generate_save", "on_apply_save"]
        enables += ["on_generate_save.enable()", "on_apply_save.enable()"]
    elif options.validate_override or harness:
        # our_parts_in() is shared plumbing; emit it even without the save hooks
        sections.append(
            _SAVE_HOOKS.substitute(record_keys).split("\n\n\ndef _save_file")[0]
        )
    if options.validate_override:
        sections.append(_VALIDATE_HOOK.substitute(record_keys))
        hooks.append("on_validate_weapon")
        enables.append("on_validate_weapon.enable()")

    suppress = any(bal.suppress_prefix for bal in resolved.balances)
    if suppress:
        sections.append(_PREFIX_HOOKS)
        hooks += ["on_weapon_name_parts", "on_weapon_init"]
        enables += ["on_weapon_name_parts.enable()", "on_weapon_init.enable()"]

    if spec.freeze is not None:
        sections.append(_FREEZE_SECTION.replace(
            "$freeze_literal", pprint.pformat(spec.freeze.to_dict(), width=96, sort_dicts=False)))
        # freeze_tick switches itself on and off, like seq_tick: not in the mod's list
        hooks += ["on_status_applied", "on_ai_died", "on_pawn_died"]
        enables += ["on_status_applied.enable()", "on_ai_died.enable()", "on_pawn_died.enable()"]

    if spec.card_icon is not None:
        sections.append(_CARD_ICON_SECTION.replace(
            "$card_literal", pprint.pformat(dict(spec.card_icon), width=96, sort_dicts=False)))
        hooks.append("on_item_card")
        enables.append("on_item_card.enable()")

    if options.own_gestalt and balances:
        sections.append(_OWN_TYPE_HOOK)
        hooks.append("on_weapon_type_pre")
        enables.append("on_weapon_type_pre.enable()")

    if spec.fire_sound is not None:
        fire_section = _FIRE_SOUND_SECTION.replace(
            "$mute_stock", str(bool(spec.fire_sound.mute_stock)))
        if spec.alt_fire is not None:
            # the alt section decides which clip plays (the alt clip on an alt shot)
            fire_section = fire_section.replace(
                "    if os.path.isfile(FIRE_SOUND_FILE):\n        try:",
                "    sound_file = _fire_sound_file(obj)\n"
                "    if os.path.isfile(sound_file):\n        try:").replace(
                "wave_mixer.play(FIRE_SOUND_FILE)", "wave_mixer.play(sound_file)")
        sections.append(_WAVE_MIXER_SECTION)
        sections.append(fire_section)
        hooks += ["on_fire_pre", "on_fire_post"]
        enables += ["on_fire_pre.enable()", "on_fire_post.enable()"]

    if spec.rage is not None:
        sections.append(RAGE_SECTION.replace(
            "$rage_literal", pprint.pformat(spec.rage.to_dict(), width=96, sort_dicts=False)))
        # rage_tick and rage_post_render run for the mod's lifetime, so they are listed too
        hooks += ['on_rage_shot', 'on_rage_ai_damage', 'on_rage_pawn_damage', 'on_rage_ai_died', 'on_rage_pawn_died', 'rage_tick', 'rage_post_render']
        enables += ['on_rage_shot.enable()', 'on_rage_ai_damage.enable()', 'on_rage_pawn_damage.enable()', 'on_rage_ai_died.enable()', 'on_rage_pawn_died.enable()', 'rage_tick.enable()', 'rage_post_render.enable()']

    if spec.alt_fire is not None:
        sections.append(ALT_FIRE_SECTION.replace(
            "$alt_literal", pprint.pformat(spec.alt_fire.runtime(), width=96, sort_dicts=False)))
        # alt_kick_tick switches itself on and off, like freeze_tick: not in the mod's list
        hooks += ["on_alt_start", "on_alt_start_engine", "on_alt_stop", "on_alt_stop_engine",
                  "on_alt_shot_pre", "on_alt_shot_post"]
        enables += ["on_alt_start.enable()", "on_alt_start_engine.enable()",
                    "on_alt_stop.enable()", "on_alt_stop_engine.enable()",
                    "on_alt_shot_pre.enable()", "on_alt_shot_post.enable()"]

    map_load_body = []
    if late_objects:
        map_load_body.append('        record["late_objects"] = register_late_objects()')
    if force:
        sections.append(_MAP_LOAD.substitute(force_fragment=force))
        map_load_body.append('        record["force_template_fragment"] = force_template_fragment()')
    if harness_auto:
        # first, so a hiccup in anything below cannot leave the phase machine unarmed
        map_load_body.append('        record["harness"] = arm_harness()')
    if harness:
        map_load_body.append('        record["census"] = _weapon_census()')
    if map_load:
        sections.append(_MAP_LOAD_HOOK.replace("$map_load_body", "\n".join(map_load_body)))
        hooks.append("on_map_loaded")
        enables.append("on_map_loaded.enable()")

    keybinds: list[str] = []
    if harness:
        keys = tuple(options.harness_keys)
        sections.append(_HARNESS.substitute(
            mod_name=spec.name, harness_mission=options.harness_mission,
            key_grant=keys[0], key_equip_newest=keys[1], key_equip_previous=keys[2],
            key_view=keys[3],
            harness_balances=_HARNESS_BALANCES_M7 if balances else _HARNESS_BALANCES,
            harness_target=_HARNESS_TARGET_M7 if balances else _HARNESS_TARGET,
            describe_extra=_DESCRIBE_EXTRA if balances else "",
        ))
        keybinds = ["kb_grant", "kb_equip_newest", "kb_equip_previous", "kb_toggle_view"]
    if harness_auto:
        targets = [reg for part in resolved.parts for reg in part.registrations]
        targets += [reg for part in resolved.parts for reg in part.runtime_registrations]
        if len(targets) > 1:
            force = {"force_section": _FORCE_MULTI.replace(
                         "$harness_targets_expr",
                         _HARNESS_TARGETS_M7 if balances else _HARNESS_TARGETS),
                     "force_stock_expr": 'lambda: force_barrel("template")',
                     "force_new_expr": 'lambda: force_barrel("new")',
                     "force_restore_expr": "lambda: force_barrel(None)"}
        else:
            force = {"force_section": _FORCE_SINGLE,
                     "force_stock_expr": "lambda: force_barrel(HARNESS_TEMPLATE_PART)",
                     "force_new_expr": 'lambda: force_barrel(HARNESS_PART["path"])',
                     "force_restore_expr": "lambda: force_barrel(None)"}
        view = options.harness_capture_view
        if view is None:
            camera_body = ('    if HARNESS_BEHINDVIEW:\n'
                           '        controller.SetBehindView(True)\n'
                           '    return "set"')
            view_line = ""
        else:
            camera_body = (
                '    # the view is explicit: F12 (or a previous run) may have left the camera\n'
                '    # either way, and the capture crop is calibrated for exactly one of them\n'
                '    controller.SetBehindView(HARNESS_CAPTURE_VIEW == "third")\n'
                '    return f"set ({HARNESS_CAPTURE_VIEW} person)"')
            view_line = f'\nHARNESS_CAPTURE_VIEW = "{view}"'
        sections.append(
            _HARNESS_AUTO.substitute(
                harness_fov=int(options.harness_fov),
                harness_behindview=str(bool(options.harness_behindview)),
                harness_view_line=view_line,
                camera_body=camera_body,
                view_hold_global="" if view is None else ", _hold_left",
                ads_phase3=(_ADS_PHASE3 if options.harness_pose == "ads"
                            else _ADS_PHASE3.replace("_zoom)", "_no_zoom)")
                            if options.harness_pose == "hip"
                            else _ADS_PHASE3.replace("_zoom)", "_open_inventory)")
                            if options.harness_pose == "inventory" else ""),
                ads_section=(_ADS_SECTION + _HIP_EXTRA if options.harness_pose == "hip"
                             else _ADS_SECTION + _INVENTORY_EXTRA
                             if options.harness_pose == "inventory"
                             else _ADS_SECTION if options.harness_pose == "ads" else ""),
                view_hold_arm="" if view is None else _VIEW_HOLD_ARM,
                fire_test_start=(("\n        _fire_test_arm()" if options.harness_fire_test else "")
                                 + ("\n        _hud_test_arm()"
                                    if options.harness_hud_test and spec.rage is not None else "")
                                 + ("\n        _rage_test_arm()"
                                    if options.harness_rage_test and spec.rage is not None
                                    else "")),
                view_hold_start="" if view is None else _VIEW_HOLD_START,
                view_hold_tick="" if view is None else _VIEW_HOLD_TICK,
                harness_equip_expr=(
                    "lambda: equip(-2)" if options.harness_equip == "second_newest"
                    else "lambda: equip(-1)"
                ),
                **force,
            )
        )

    if harness_auto and options.harness_timing == "seconds":
        sections[-1] = _wall_clock_harness(sections[-1])
    if harness_auto and options.harness_fire_test:
        sections.append(_FIRE_TEST_SECTION.replace(
            "$fire_literal", pprint.pformat(dict(options.harness_fire_test), sort_dicts=False)))
    if harness_auto and options.harness_fire_test and options.harness_fire_test.get("alt")             and spec.alt_fire is not None:
        sections.append(ALT_TEST_SECTION)
    if harness_auto and options.harness_hud_test and spec.rage is not None:
        sections.append(HUD_TEST_SECTION.replace(
            "$hud_test_literal", pprint.pformat(dict(options.harness_hud_test),
                                                sort_dicts=False)))
    if harness_auto and options.harness_rage_test and spec.rage is not None:
        sections.append(RAGE_TEST_SECTION.replace(
            "$rage_test_literal", pprint.pformat(dict(options.harness_rage_test),
                                                 sort_dicts=False)))
    if harness and options.harness_trace:
        sections.append(TRACE_SECTION.replace(
            "$trace_literal", pprint.pformat(list(options.harness_trace), width=96)))
    if harness_auto and options.harness_status_probe:
        sections.append(_PROBE_SECTION.replace(
            "$probe_literal", pprint.pformat(dict(options.harness_status_probe), width=96,
                                             sort_dicts=False)))

    short = (spec.description or "Runtime gestalt fragment + weapon part registration.")
    short = short.strip().splitlines()[0].replace('"', "'")
    if len(short) > 70:
        short = short[:67].rstrip() + "..."
    if component:
        sections.append(
            _COMPONENT_FOOTER.substitute(
                hook_list=", ".join(hooks),
                balance_paths='bal["path"] for bal in BALANCES' if balances else "",
            )
        )
        text = "".join(sections)
        text = text.replace(
            'CONTROL_FILE = os.path.join(MOD_DIR, "control.json")',
            'CONTROL_FILE = os.path.join(MOD_DIR, MOD_NAME + ".control.json")',
        )
        text = text.replace(
            'DEFAULT_STATUS_FILE = os.path.join(MOD_DIR, "status.json")',
            'DEFAULT_STATUS_FILE = os.path.join(MOD_DIR, MOD_NAME + ".status.json")',
        )
    else:
        sections.append(
            _FOOTER.substitute(
                mod_name=spec.name,
                short_description=short,
                hook_list=", ".join(hooks),
                keybind_arg=("\n    keybinds=[" + ", ".join(keybinds) + "],") if keybinds else "",
                enable_lines="\n".join(enables),
            )
        )
        text = "".join(sections)
    return text if text.endswith("\n") else text + "\n"


def render_pyproject(resolved: ResolvedSpec) -> str:
    spec = resolved.spec
    description = (spec.description or "Runtime gestalt fragment + weapon part registration.")
    description = description.strip().splitlines()[0].replace('"', "'")
    if len(description) > 90:
        description = description[:87].rstrip() + "..."
    author = f'\nauthors = [{{ name = "{spec.author}" }}]' if spec.author else ""
    return (
        "# Generated by bl2_partgen. pyunrealsdk reads this to name the mod folder;\n"
        "# the mod itself is sdk_mods/%s/__init__.py.\n"
        "[project]\n"
        'name = "%s"\n'
        'version = "%s"\n'
        'description = "%s"%s\n'
        'requires-python = ">=3.11"\n'
    ) % (spec.name, spec.name, spec.version, description, author)


def render_settings() -> str:
    """``sdk_mods/settings/<name>.json``: without it the mod is not enabled (F8)."""
    return '{\n "enabled": true\n}\n'


def _harness_target(resolved: ResolvedSpec) -> str:
    """``<balance>.<field>``: the one part list the test harness forces (PARTS[0], first target)."""
    part = resolved.parts[0]
    if part.registrations:
        registration = part.registrations[0]
        return f"{registration.balance}.{registration.field}"
    balance, fld = part.runtime_registrations[0]
    return f"{balance}.{fld}"


def render_readme(resolved: ResolvedSpec, spec_name: str) -> str:
    spec = resolved.spec
    options = spec.options
    spec_name = str(spec_name).replace("\\", "/")
    lines = [
        f"# {spec.name}",
        "",
        f"Generated by `bl2_partgen` from `{spec_name}`. Do not edit by hand: change the spec",
        "and re-emit (`python -m bl2_partgen <spec> --out <dir>`).",
        "",
        "## Install",
        "",
        "```",
        f"<game>/sdk_mods/{spec.name}/__init__.py        this mod",
        f"<game>/sdk_mods/settings/{spec.name}.json      {{\"enabled\": true}}  (F8)",
        f"<game>/WillowGame/CookedPCConsole/{spec.package_stem}.upk",
        "```",
        "",
        "`python -m bl2_partgen <spec> --out <dir> --install <game dir>` does all three.",
        "",
        "## What it registers",
        "",
    ]
    for frag in resolved.fragments:
        lines.append(
            f"* fragment `{frag.name}` = {frag.num_primitives} triangles at index "
            f"{frag.first_index}, cloned from `{frag.template_fragment}` "
            f"(sockets: {', '.join(frag.sockets) or 'none'})"
        )
    for part in resolved.parts:
        targets = [f"`{r.balance}`.{r.field}" for r in part.registrations]
        targets += [f"`{bal}`.{fld} (runtime balance)" for bal, fld in part.runtime_registrations]
        drawn = "" if part.fragment is not None else " -- draws nothing (no fragment)"
        stats = " -- with stat overrides" if part.overrides is not None else ""
        lines.append(f"* part `{part.part_path}` ({part.slot}) into {', '.join(targets)}{drawn}{stats}")
    for name in resolved.extra_packages:
        lines.append(f"* extra package `{name}.upk` loaded and rooted at the menu tick")
    for mat in resolved.materials:
        params = ", ".join(f"{k} = `{v}`" for k, v in mat.texture_parameters.items())
        lines.append(f"* material `{mat.path}` (runtime MIC parented to `{mat.parent}`): {params}")
    for bal in resolved.balances:
        lists = "; ".join(f"{fld} = {', '.join(f'`{p}`' for p in paths)}"
                          for fld, paths in bal.part_lists.items())
        lines.append(f"* balance `{bal.path}` cloned from `{bal.template_balance}` with its own "
                     f"runtime part list `{bal.collection_path}`: {lists}")
        if bal.title is not None:
            lines.append(f"* title `{bal.title.path}` = \"{bal.title.part_name}\" (cloned from "
                         f"`{bal.title.template}`) on {', '.join(f'`{p}`' for p in bal.title.on_part_paths)}"
                         + (f"; red text: {bal.title.red_text!r}" if bal.title.red_text else ""))
        if bal.pools:
            lines.append(f"* appended to pool(s) {', '.join(f'`{p}`' for p in bal.pools)}")
    lines += ["", "## What it does at each hook", ""]
    lines += [
        "| when | hook | what |",
        "|---|---|---|",
        "| import | - | every hook is `enable()`d at import; `auto_enable` would not bind them on "
        "the launch that installs the mod (F8) |",
        f"| menu tick {options.menu_ticks} | `WillowGameViewportClient:Tick` PRE | `menu_setup()`: "
        "load the package, re-point `GestaltDef.GestaltSkeletalMesh`, append the fragment table "
        "row / bounds / socket mappings, construct the sockets and the part(s), append each part "
        "to its runtime part list. At the main menu, before any save is read (F16); everything it "
        "touches is rooted with ObjectFlags 0x4000 (F17) |",
    ]
    if options.save_roundtrip:
        lines += [
            "| save | `GeneratePlayerSaveGame` PRE | record `{UniqueId: {slot: part path}}` in "
            "`saves/<SaveGameName>.json` for every weapon of ours (F18) |",
            "| load | `ApplyPlayerSaveGameData` PRE | write those parts back into the saved weapon "
            "data before the game builds the weapons (F18) |",
        ]
    if options.validate_override:
        lines += [
            "| validate | `ValidateWeaponDefinition` PRE | `(Block, True)` for definitions that use "
            "one of our parts; the cooked-data sanity check rejects them and the weapon would be "
            "deleted on load (F19) |",
        ]
    if options.force_template_fragment_on_map_load or options.test_harness:
        extra = []
        if options.force_template_fragment_on_map_load:
            extra.append(
                f"re-assert `{options.force_template_fragment_on_map_load}` on the template "
                "part(s), in case a text mod retargeted them"
            )
        if options.test_harness:
            extra.append("write the weapon census")
        lines += [
            "| map load | `WillowClientDisableLoadingMovie` POST | register late if the menu tick "
            "never fired, then " + "; ".join(extra) + " |",
        ]
    if options.test_harness and options.harness_auto:
        lines += [
            "| map load + 120 ticks | `WillowGameViewportClient:Tick` PRE (`seq_tick`) | the M2 "
            "phase machine, record for record, so `bl2_verify`'s loop can read this mod's status "
            "file: phase 0 census + re-assert the stock fragment + grant a stock weapon, phase 1 "
            "force our part onto every entry of "
            f"`{_harness_target(resolved)}` and grant again, phase 2 restore that list and equip "
            "the newest, phase 3 "
            f"`ConsoleCommand(\"FOV {int(options.harness_fov)}\")`"
            + (f" + `SetBehindView({options.harness_capture_view == 'third'})` "
               f"({options.harness_capture_view} person)"
               if options.harness_capture_view
               else (" + `SetBehindView(True)`" if options.harness_behindview else ""))
            + (" (equipping the A/B stock weapon, for a baseline capture)"
               if options.harness_equip == "second_newest" else "")
            + " and `\"done\": true` (test harness only) |",
        ]
    if options.test_harness:
        lines += [
            f"| {' / '.join(options.harness_keys[:3])} | keybinds | grant a weapon from the "
            "first registered balance; equip the newest; equip the second newest (test "
            "harness only) |",
        ]
    if options.status_path:
        status_line = (
            f"* `{options.status_path}` - what each hook did, appended in order; the spec's\n"
            "  `options.status_path` put it there, so the verify loop needs no control file\n"
            '  (`control.json` = `{"status_path": "..."}` still redirects it again)'
        )
    else:
        status_line = (
            "* `status.json` - what each hook did, appended in order (redirect it with\n"
            '  `control.json` = `{"status_path": "..."}`)'
        )
    lines += [
        "",
        "## Files it writes at runtime",
        "",
        status_line,
        "* `saves/<SaveGameName>.json` - `{UniqueId: {slot field: part path}}`, the record that",
        "  survives the save round-trip (F18)",
    ]
    return "\n".join(lines) + "\n"
