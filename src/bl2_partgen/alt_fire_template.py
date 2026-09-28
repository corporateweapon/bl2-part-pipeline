"""The ``alt_fire`` section of a generated mod (RMB as a second trigger, no zoom).

Kept out of ``templates.py`` (already 3,300 lines) and spliced in by ``render_mod`` only when a
spec has ``alt_fire``, so every existing build renders byte-identical (no F28 re-emit).
"""
from __future__ import annotations

ALT_FIRE_SECTION = '''


# --------------------------------------------------------------------- alt fire (RMB)
# Right mouse button is a second trigger for our weapons, never a zoom. StartAltFire /
# StopAltFire are blocked while one of our balances is held; the press pulls the ordinary
# trigger (StartFire(0)) with the next shot marked as an alt shot. FireAmmunition PRE then
# swaps the weapon's ShotCost / InstantHitDamage / PerShotAccuracyImpulse for the alt values
# and POST puts them back, so the engine's own shot code does the rest. If the engine spends
# fewer rounds than ammo_cost (ShotCost not honoured) the POST takes the rest off ReloadCnt.
ALT_FIRE: dict[str, Any] = $alt_literal
FIRE_SOUND_ALT_FILE = os.path.join(MOD_DIR, "sounds", MOD_NAME + "_alt_fire.wav")
if "wave_mixer" in globals() and os.path.isfile(FIRE_SOUND_ALT_FILE):
    wave_mixer.preload(FIRE_SOUND_ALT_FILE)  # the fire_sound section embeds the mixer
_ROT_PER_DEG = 65536.0 / 360.0

_alt: dict[str, Any] = {"armed_at": 0.0, "last_press": 0.0, "last_release": 0.0,
                        "active": False, "saved": None, "clip_before": None}
_alt_counts = {"pulls": 0, "dry": 0, "alt_shots": 0, "hip_shots": 0, "releases": 0,
               "ammo_fixups": 0, "kicks": 0, "knockbacks": 0, "errors": 0}
_kick: dict[str, float] = {"left": 0.0, "last": 0.0}


def _alt_note(what: str, **detail: Any) -> None:
    """A status record for the first few of each event (the harness and loop read them)."""
    count = _alt_counts.get(what, 0)
    if count <= 3 or count in (10, 100):
        _write({"alt_fire": what, "counts": dict(_alt_counts), **detail})


def _held_weapon_of_ours(controller: Any) -> Any:
    try:
        pawn = controller.Pawn
        weapon = pawn.Weapon if pawn is not None else None
        if weapon is None:
            return None
        balance = weapon.DefinitionData.BalanceDefinition
        if balance is None or balance._path_name() not in OUR_BALANCE_PATHS:
            return None
        return weapon
    except Exception:  # noqa: BLE001 - half-built pawns / weapons throw on attribute access
        return None


def _alt_armed() -> bool:
    return _alt["armed_at"] > 0.0 and time.time() - _alt["armed_at"] <= ALT_FIRE["arm_window_s"]


def _fire_sound_file(weapon: Any) -> str:
    """The fire_sound section asks which clip to play: the alt clip on an alt shot."""
    if (_alt["active"] or _alt_armed()) and os.path.isfile(FIRE_SOUND_ALT_FILE):
        return FIRE_SOUND_ALT_FILE
    return FIRE_SOUND_FILE


def _clip(weapon: Any) -> int | None:
    """Rounds in the clip (ReloadCnt counts down as the clip empties), None if unreadable."""
    try:
        return int(weapon.ReloadCnt)
    except Exception:  # noqa: BLE001
        return None


def _alt_press(controller: Any) -> Any:
    weapon = _held_weapon_of_ours(controller)
    if weapon is None:
        return None
    now = time.time()
    if now - _alt["last_press"] < 0.05:  # the Willow and Engine hooks both see one press
        return Block
    _alt["last_press"] = now
    clip = _clip(weapon)
    if clip is not None and clip < ALT_FIRE["ammo_cost"]:
        _alt_counts["dry"] += 1
        _alt_note("dry", clip=clip)
        return Block
    _alt["armed_at"] = now
    _alt_counts["pulls"] += 1
    _alt_note("pulls", clip=clip)
    try:
        controller.StartFire(0)
    except Exception as ex:  # noqa: BLE001
        _alt_counts["errors"] += 1
        _log(f"alt fire: StartFire failed: {ex!r}")
    return Block


def _alt_release(controller: Any) -> Any:
    if _held_weapon_of_ours(controller) is None:
        return None
    now = time.time()
    if now - _alt["last_release"] < 0.05:
        return Block
    _alt["last_release"] = now
    _alt_counts["releases"] += 1
    try:
        controller.StopFire(0)
    except Exception as ex:  # noqa: BLE001
        _alt_counts["errors"] += 1
        _log(f"alt fire: StopFire failed: {ex!r}")
    return Block


@hook("WillowGame.WillowPlayerController:StartAltFire", Type.PRE)
def on_alt_start(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> Any:
    return _alt_press(obj) if ALT_FIRE["block_zoom"] else None


@hook("Engine.PlayerController:StartAltFire", Type.PRE)
def on_alt_start_engine(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> Any:
    return _alt_press(obj) if ALT_FIRE["block_zoom"] else None


@hook("WillowGame.WillowPlayerController:StopAltFire", Type.PRE)
def on_alt_stop(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> Any:
    return _alt_release(obj) if ALT_FIRE["block_zoom"] else None


@hook("Engine.PlayerController:StopAltFire", Type.PRE)
def on_alt_stop_engine(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> Any:
    return _alt_release(obj) if ALT_FIRE["block_zoom"] else None


def _set_prop(weapon: Any, name: str, value: float) -> Any:
    old = getattr(weapon, name)
    setattr(weapon, name, type(old)(value))
    return old


def _view_dir(controller: Any) -> tuple[float, float, float]:
    import math

    rot = controller.Rotation
    pitch = int(rot.Pitch) & 0xFFFF
    if pitch > 32767:
        pitch -= 65536
    p = pitch * math.pi / 32768.0
    y = (int(rot.Yaw) & 0xFFFF) * math.pi / 32768.0
    return (math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), math.sin(p))


def _knockback(controller: Any, strength: float, lift: float) -> None:
    if strength <= 0 and lift <= 0:
        return
    pawn = controller.Pawn
    dx, dy, dz = _view_dir(controller)
    push = unrealsdk.make_struct("Vector", X=-dx * strength, Y=-dy * strength,
                                 Z=-dz * strength + lift)
    try:
        # Pawn.AddVelocity switches a walking pawn to falling, so the push is not eaten by
        # ground friction on the next tick
        pawn.AddVelocity(push, pawn.Location, None)
    except Exception:  # noqa: BLE001 - signature differs: push the velocity by hand
        vel = pawn.Velocity
        pawn.Velocity = unrealsdk.make_struct("Vector", X=vel.X + push.X, Y=vel.Y + push.Y,
                                              Z=vel.Z + push.Z)
        pawn.SetPhysics(2)  # PHYS_Falling
    _alt_counts["knockbacks"] += 1


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def alt_kick_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    """Spread the alt shot's view kick over recoil_seconds instead of snapping the camera."""
    try:
        now = time.time()
        step_s = min(now - _kick["last"], 0.05)
        _kick["last"] = now
        if _kick["left"] <= 0:
            alt_kick_tick.disable()
            return
        rate = ALT_FIRE["recoil_pitch_deg"] * _ROT_PER_DEG / ALT_FIRE["recoil_seconds"]
        amount = min(_kick["left"], rate * step_s)
        _kick["left"] -= amount
        from mods_base import get_pc

        controller = get_pc()
        rot = controller.Rotation
        controller.Rotation = unrealsdk.make_struct(
            "Rotator", Pitch=int(rot.Pitch) + int(round(amount)), Yaw=int(rot.Yaw),
            Roll=int(rot.Roll))
    except Exception as ex:  # noqa: BLE001
        _kick["left"] = 0.0
        alt_kick_tick.disable()
        _alt_counts["errors"] += 1
        _log(f"alt fire: view kick failed: {ex!r}")


def _is_our_player_weapon(weapon: Any) -> bool:
    try:
        from mods_base import get_pc

        controller = get_pc(possibly_loading=True)
        return (controller is not None and controller.Pawn is not None
                and weapon.Owner == controller.Pawn
                and _held_weapon_of_ours(controller) == weapon)
    except Exception:  # noqa: BLE001
        return False


def _alt_restore() -> None:
    held = _alt["saved"]
    _alt["saved"] = None
    if not held:
        return
    target, saved = held
    for name, value in saved.items():
        try:
            setattr(target, name, value)
        except Exception as ex:  # noqa: BLE001
            _alt_counts["errors"] += 1
            _log(f"alt fire: could not restore {name}: {ex!r}")


@hook("WillowGame.WillowWeapon:FireAmmunition", Type.PRE)
def on_alt_shot_pre(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    _alt_restore()  # a POST that never ran must not leave a weapon at alt values
    if not _alt_armed() or not _is_our_player_weapon(obj):
        return
    _alt["armed_at"] = 0.0
    _alt["active"] = True
    _alt["clip_before"] = _clip(obj)
    saved: dict[str, Any] = {}
    try:
        saved["ShotCost"] = _set_prop(obj, "ShotCost", ALT_FIRE["ammo_cost"])
        saved["InstantHitDamage"] = _set_prop(
            obj, "InstantHitDamage", float(obj.InstantHitDamage) * ALT_FIRE["damage_scale"])
        saved["PerShotAccuracyImpulse"] = _set_prop(
            obj, "PerShotAccuracyImpulse",
            float(obj.PerShotAccuracyImpulse) * ALT_FIRE["accuracy_impulse_scale"])
    except Exception as ex:  # noqa: BLE001 - a property this build lacks: shoot anyway
        _alt_counts["errors"] += 1
        _log(f"alt fire: could not set property {len(saved) + 1}: {ex!r}")
    _alt["saved"] = (obj, saved)
    _alt_note("alt_shot_values", clip_before=_alt["clip_before"],
              values={k: getattr(obj, k) for k in saved}, stock=dict(saved))


def _vel(controller: Any) -> list[float] | None:
    try:
        v = controller.Pawn.Velocity
        return [round(float(v.X), 1), round(float(v.Y), 1), round(float(v.Z), 1)]
    except Exception:  # noqa: BLE001
        return None


def _push(controller: Any, strength: float, lift: float) -> None:
    try:
        _knockback(controller, strength, lift)
    except Exception as ex:  # noqa: BLE001
        _alt_counts["errors"] += 1
        _log(f"alt fire: knockback failed: {ex!r}")


@hook("WillowGame.WillowWeapon:FireAmmunition", Type.POST)
def on_alt_shot_post(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    alt = _alt["active"]
    _alt["active"] = False
    _alt_restore()
    if not _is_our_player_weapon(obj):
        return
    from mods_base import get_pc

    controller = get_pc()
    if not alt:
        _alt_counts["hip_shots"] += 1
        _push(controller, ALT_FIRE["knockback_hip"], ALT_FIRE["lift_hip"])
        _alt_note("hip_shots", clip_after=_clip(obj), velocity=_vel(controller),
                  damage=float(obj.InstantHitDamage))
        return
    _alt_counts["alt_shots"] += 1
    before, after = _alt["clip_before"], _clip(obj)
    spent = None if before is None or after is None else before - after
    if spent is not None and 0 <= spent < ALT_FIRE["ammo_cost"]:
        try:
            obj.ReloadCnt = max(0, after - (ALT_FIRE["ammo_cost"] - spent))
            _alt_counts["ammo_fixups"] += 1
        except Exception as ex:  # noqa: BLE001
            _alt_counts["errors"] += 1
            _log(f"alt fire: ammo fix-up failed: {ex!r}")
    _kick["left"] = ALT_FIRE["recoil_pitch_deg"] * _ROT_PER_DEG
    _kick["last"] = time.time()
    alt_kick_tick.enable()
    _alt_counts["kicks"] += 1
    _push(controller, ALT_FIRE["knockback_alt"], ALT_FIRE["lift_alt"])
    _alt_note("alt_shots", clip_before=before, clip_after=after, spent_by_engine=spent,
              clip_final=_clip(obj), velocity=_vel(controller),
              damage_after_restore=float(obj.InstantHitDamage))'''

#: the alt-fire half of the harness fire test: after the hip test is done, pull RMB through
#: the controller (StartAltFire, the path the hooks block) twice, a second apart
ALT_TEST_SECTION = '''


# --------------------------------------------------------------------- alt fire test (harness)
_alt_test: dict[str, Any] = {"state": "idle", "t": 0.0, "n": 0}


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def alt_test_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    try:
        if _fire.get("state") != "done":
            return
        now = time.time()
        controller = get_pc()
        if _alt_test["state"] == "idle":
            _alt_test.update(state="wait", t=now)
        elif _alt_test["state"] == "wait" and now - _alt_test["t"] >= FIRE_TEST.get("alt_delay_s", 2.0):
            controller.StartAltFire()
            _alt_test.update(state="down", t=now)
            _write({"alt_test": "press", "n": _alt_test["n"],
                     "zoomed": bool(getattr(controller.Pawn.Weapon, "bZoomed", False))})
        elif _alt_test["state"] == "down" and now - _alt_test["t"] >= 0.3:
            controller.StopAltFire()
            _alt_test["n"] += 1
            weapon = controller.Pawn.Weapon
            _write({"alt_test": "release", "n": _alt_test["n"],
                    "zoomed": bool(getattr(weapon, "bZoomed", False)),
                    "zoom_state": int(getattr(weapon, "ZoomState", -1)),
                    "clip": _clip(weapon)})
            if _alt_test["n"] >= FIRE_TEST.get("alt_presses", 2):
                _alt_test["state"] = "finished"
                alt_test_tick.disable()
            else:
                _alt_test.update(state="wait", t=now)
    except Exception as ex:  # noqa: BLE001
        alt_test_tick.disable()
        _write({"alt_test": "error", "error": repr(ex)})


alt_test_tick.enable()  # idles until the hip fire test is done; not in the mod's hook list'''

#: harness-only function tracer: which engine functions fire, e.g. on a real RMB press
TRACE_SECTION = '''


# --------------------------------------------------------------------- function trace (harness)
TRACE_FUNCTIONS: list[str] = $trace_literal
_trace_counts: dict[str, int] = {}


def _trace_install() -> None:
    import unrealsdk.hooks as _hooks

    for name in TRACE_FUNCTIONS:
        def _cb(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction,
                _name: str = name) -> None:
            n = _trace_counts.get(_name, 0) + 1
            _trace_counts[_name] = n
            if n <= 3:
                _write({"trace": _name, "n": n, "obj": obj._path_name()})
        try:
            _hooks.add_hook(name, Type.PRE, f"{MOD_NAME}_trace_{name}", _cb)
        except Exception as ex:  # noqa: BLE001
            _write({"trace": name, "error": repr(ex)})


_trace_install()'''
