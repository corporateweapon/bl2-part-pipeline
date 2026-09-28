"""The ``rage`` section of a generated mod: Shiv's Rage from Deadlock as a legendary effect.

Spliced in by ``render_mod`` only when a spec has ``rage`` (every other build renders
byte-identical). Canvas drawing follows what the user's ``stamina`` mod established for this
BL2 build: ``PostRender`` POST, ``DrawTile`` takes its colour as an argument (``DrawColor`` is
ignored), the white texture's whole extent is sampled, and ``PostRender`` fires over menus so
the meter gates itself on being in play.
"""
from __future__ import annotations

RAGE_SECTION = '''


# --------------------------------------------------------------------- rage (Shiv, Deadlock)
# Hits with our weapon build Rage (once per shot, not per pellet; more for an alt shot), kills
# made while holding it build more. After decay_delay_s without a gain Rage drains. At full
# Rage the player is Enraged: every point of damage they deal is scaled (TakeDamage is
# blocked and re-issued with the scaled amount) and their ground speed is scaled. A Canvas
# meter shows it while the weapon is held or Rage is above zero.
import math

RAGE: dict[str, Any] = $rage_literal
_rage: dict[str, Any] = {"value": 0.0, "last_gain": 0.0, "enraged": False, "last_tick": 0.0,
                         "shot_seq": 0, "shot_time": 0.0, "shot_alt": False, "credited": -1,
                         "in_recall": False, "speed_base": None, "speed_written": None,
                         "pawn": None, "last_kill": (None, 0.0), "enraged_since": 0.0}
_rage_counts = {"hits": 0, "alt_hits": 0, "kills": 0, "shots": 0, "scaled": 0,
                "enraged": 0, "calm": 0, "draw_errors": 0, "errors": 0}
_rage_draw: dict[str, Any] = {"white": None, "ul": 1.0, "vl": 1.0, "font": None, "blend": None,
                              "warned": False}


def _rage_note(what: str, **detail: Any) -> None:
    count = _rage_counts.get(what, 0)
    if count <= 3 or count in (10, 25, 100):
        _write({"rage": what, "value": round(_rage["value"], 2), "enraged": _rage["enraged"],
                "counts": dict(_rage_counts), **detail})


def _rage_pc() -> Any:
    from mods_base import get_pc

    return get_pc(possibly_loading=True)


def _rage_held(controller: Any) -> Any:
    """Our weapon if the player holds one of our balances, else None."""
    try:
        weapon = controller.Pawn.Weapon
        balance = weapon.DefinitionData.BalanceDefinition
        if balance is not None and balance._path_name() in OUR_BALANCE_PATHS:
            return weapon
    except Exception:  # noqa: BLE001 - no pawn / no weapon / half-built
        pass
    return None


def _rage_add(amount: float, why: str, **detail: Any) -> None:
    before = _rage["value"]
    _rage["value"] = min(float(RAGE["max"]), before + float(amount))
    _rage["last_gain"] = time.time()
    _rage_counts[why] = _rage_counts.get(why, 0) + 1
    _rage_note(why, gained=round(_rage["value"] - before, 2), **detail)


def _rage_shot_is_alt() -> bool:
    alt = globals().get("_alt")
    if not alt:
        return False
    armed = globals().get("_alt_armed")
    return bool(alt.get("active")) or bool(armed and armed())


@hook("WillowGame.WillowWeapon:FireAmmunition", Type.PRE)
def on_rage_shot(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    try:
        controller = _rage_pc()
        if controller is None or _rage_held(controller) != obj:
            return
        _rage["shot_seq"] += 1
        _rage["shot_time"] = time.time()
        _rage["shot_alt"] = _rage_shot_is_alt()
        _rage_counts["shots"] += 1
    except Exception as ex:  # noqa: BLE001
        _rage_counts["errors"] += 1
        _log(f"rage: shot hook failed: {ex!r}")


def _rage_recall(func: BoundFunction, args: WrappedStruct) -> None:
    """Call the hooked function again with (edited) args, our hooks passing it through."""
    _rage["in_recall"] = True
    try:
        try:
            # CPF_Parm 0x80, CPF_ReturnParm 0x400: a UFunction also lists its locals
            names = [prop.Name for prop in args._type._properties()
                     if int(prop.PropertyFlags) & 0x80 and not int(prop.PropertyFlags) & 0x400]
        except Exception:  # noqa: BLE001 - no property iterator on this SDK: hand the struct over
            func(args)
            return
        func(**{name: getattr(args, name) for name in names})
    finally:
        _rage["in_recall"] = False


def _rage_on_damage(target: Any, args: WrappedStruct, func: BoundFunction) -> Any:
    if _rage["in_recall"]:
        return None
    try:
        controller = _rage_pc()
        if controller is None or getattr(args, "InstigatedBy", None) != controller:
            return None
        pawn = controller.Pawn
        if pawn is None or target == pawn:
            return None
        now = time.time()
        weapon = _rage_held(controller)
        if weapon is not None:
            causer = getattr(args, "DamageCauser", None)
            recent = now - _rage["shot_time"] <= RAGE["hit_window_s"]
            from_us = causer == weapon or (causer == pawn and recent)
            if from_us and recent and _rage["credited"] != _rage["shot_seq"]:
                _rage["credited"] = _rage["shot_seq"]
                alt = _rage["shot_alt"]
                _rage_add(RAGE["gain_alt_hit"] if alt else RAGE["gain_hit"],
                          "alt_hits" if alt else "hits", target=target._path_name())
        if _rage["enraged"] and RAGE["damage_scale"] != 1.0:
            before = args.Damage
            args.Damage = type(before)(before * RAGE["damage_scale"])
            _rage_counts["scaled"] += 1
            _rage_note("scaled", damage_before=before, damage_after=args.Damage)
            _rage_recall(func, args)
            return Block
    except Exception as ex:  # noqa: BLE001 - a rage slip must never eat a hit
        _rage_counts["errors"] += 1
        _log(f"rage: damage hook failed: {ex!r}")
    return None


@hook("WillowGame.WillowAIPawn:TakeDamage", Type.PRE)
def on_rage_ai_damage(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> Any:
    return _rage_on_damage(obj, args, func)


@hook("WillowGame.WillowPawn:TakeDamage", Type.PRE)
def on_rage_pawn_damage(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> Any:
    return _rage_on_damage(obj, args, func)


def _rage_on_died(target: Any, args: WrappedStruct) -> None:
    try:
        controller = _rage_pc()
        if controller is None or getattr(args, "Killer", None) != controller:
            return
        if target == controller.Pawn or _rage_held(controller) is None:
            return
        last, when = _rage["last_kill"]
        now = time.time()
        if last == target and now - when < 1.0:  # the AI and base hooks both see one death
            return
        _rage["last_kill"] = (target, now)
        _rage_add(RAGE["gain_kill"], "kills", target=target._path_name())
    except Exception as ex:  # noqa: BLE001
        _rage_counts["errors"] += 1
        _log(f"rage: kill hook failed: {ex!r}")


@hook("WillowGame.WillowAIPawn:Died", Type.PRE)
def on_rage_ai_died(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    _rage_on_died(obj, args)


@hook("WillowGame.WillowPawn:Died", Type.PRE)
def on_rage_pawn_died(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    _rage_on_died(obj, args)


def _rage_speed(pawn: Any, on: bool) -> None:
    """Scale GroundSpeed while Enraged without fighting whoever else writes it.

    BL2's sprint (and the user's stamina dash) write GroundSpeed themselves. A value that is
    not the one we wrote last is someone else's: take it as the new base and scale that.
    On leaving Rage, put the base back only if nobody has written since.
    """
    if pawn is None:
        return
    current = float(pawn.GroundSpeed)
    if on:
        written = _rage["speed_written"]
        if written is None or abs(current - written) > 0.01:
            _rage["speed_base"] = current
            _rage["speed_written"] = current * RAGE["speed_scale"]
            pawn.GroundSpeed = _rage["speed_written"]
    else:
        written = _rage["speed_written"]
        if written is not None and abs(current - written) <= 0.01:
            pawn.GroundSpeed = _rage["speed_base"]
        _rage["speed_written"] = None
        _rage["speed_base"] = None


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def rage_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    try:
        now = time.time()
        dt = min(max(now - _rage["last_tick"], 0.0), 0.25)
        _rage["last_tick"] = now
        controller = _rage_pc()
        pawn = controller.Pawn if controller is not None else None
        if pawn is not _rage["pawn"] and pawn != _rage["pawn"]:
            # new pawn (map load, save loaded, respawn): start from nothing; our speed write
            # went with the old pawn
            if _rage["pawn"] is not None:  # not the very first adoption
                _rage.update(value=0.0, enraged=False, last_gain=0.0)
            _rage.update(pawn=pawn, speed_base=None, speed_written=None)
        if _rage["value"] > 0.0 and now - _rage["last_gain"] >= RAGE["decay_delay_s"]:
            _rage["value"] = max(0.0, _rage["value"] - RAGE["decay_per_s"] * dt)
        enraged = _rage["value"] >= float(RAGE["max"]) - 1e-6
        if enraged != _rage["enraged"]:
            _rage["enraged"] = enraged
            _rage["enraged_since"] = now
            _rage_counts["enraged" if enraged else "calm"] += 1
            if not enraged:
                _rage_speed(pawn, False)
            _rage_note("enraged" if enraged else "calm",
                       ground_speed=float(pawn.GroundSpeed) if pawn is not None else None)
        if enraged:
            _rage_speed(pawn, True)
    except Exception as ex:  # noqa: BLE001
        _rage_counts["errors"] += 1
        if _rage_counts["errors"] <= 3:
            _log(f"rage: tick failed: {ex!r}")


# ---- the meter ---------------------------------------------------------------
# Layout is a pure function of the canvas size, a text-measuring callback and the clock, so
# it can be rendered offline at any resolution (tests/test_rage.py). Everything is sized in
# 1080p design pixels times u = ClipY / 1080 and snapped to whole pixels; text is scaled to
# a target pixel height from its MEASURED height, never from the font's nominal size.
RAGE_FONTS = ("UI_Fonts.Font_Willowbody_18pt", "UI_Fonts.Font_Hud_Medium")
#: Willowbody 18pt glyph cells are 33 px tall (its Characters table); the fallback metric
FONT_CELL_PX = 33.0
#: measured in game (1280x720, run hud-2): capitals fill 54 % of the cell and start 23 % down,
#: so rows are laid out on CAP height and the cell is lifted by its top padding
FONT_CAP_RATIO = 0.54
FONT_CAP_TOP = 0.23


def _rage_in_play(controller: Any) -> bool:
    if controller is None or controller.Pawn is None:
        return False
    try:
        if controller.GetHUDMovie() is None:
            return False
    except Exception:  # noqa: BLE001
        return False
    return not any(getattr(controller, flag, False)
                   for flag in ("bStatusMenuOpen", "bInMenu", "bIsPaused"))


def _rage_white() -> Any:
    if _rage_draw["white"] is None:
        texture = unrealsdk.find_object("Texture2D", "EngineResources.WhiteSquareTexture")
        _rage_draw["white"] = texture
        _rage_draw["ul"] = float(getattr(texture, "SizeX", 0) or 1)
        _rage_draw["vl"] = float(getattr(texture, "SizeY", 0) or 1)
        try:
            _rage_draw["blend"] = int(unrealsdk.find_enum("EBlendMode").BLEND_Translucent)
        except Exception:  # noqa: BLE001
            _rage_draw["blend"] = 2
    return _rage_draw["white"]


def _rage_font() -> Any:
    """One fixed font by full path (searching loaded fonts picked different ones per session)."""
    if _rage_draw["font"] is None:
        _rage_draw["font"] = False
        for path in RAGE_FONTS:
            try:
                _rage_draw["font"] = unrealsdk.find_object("Font", path)
                _rage_draw["font_path"] = path
                break
            except Exception:  # noqa: BLE001
                continue
    return _rage_draw["font"] or None


def _rage_measure_on(canvas: Any):
    """A measure(text) -> (width, height) at scale 1 for this canvas's font, cached."""
    cache = _rage_draw.setdefault("measured", {})

    def measure(text: str) -> tuple[float, float]:
        if text in cache:
            return cache[text]
        size = None
        try:
            res = canvas.TextSize(text, 0.0, 0.0)  # out params come back in the tuple
            nums = [float(v) for v in (res if isinstance(res, tuple) else (res,))
                    if isinstance(v, (int, float))]
            if len(nums) >= 2 and nums[-1] > 0 and nums[-2] > 0:
                size = (nums[-2], nums[-1])
        except Exception:  # noqa: BLE001
            pass
        if size is None:
            size = (len(text) * 0.52 * FONT_CELL_PX, FONT_CELL_PX)
            _rage_draw["measure_fallback"] = True
        cache[text] = size
        return size

    return measure


def _rage_state(now: float) -> dict[str, Any]:
    idle = now - _rage["last_gain"]
    value = _rage["value"]
    return {"frac": max(0.0, min(1.0, value / float(RAGE["max"]))), "value": value,
            "enraged": _rage["enraged"],
            "decaying": value > 0 and idle >= RAGE["decay_delay_s"],
            "decay_in": max(0.0, RAGE["decay_delay_s"] - idle), "now": now}


def _pct(scale: float) -> str:
    return f"{int(round((scale - 1.0) * 100)):+d}%"


def _rage_bonus_text() -> str:
    return f"{_pct(RAGE['damage_scale'])} DAMAGE    {_pct(RAGE['speed_scale'])} SPEED"


def _mix(a: tuple[int, ...], b: tuple[int, ...], t: float) -> tuple[int, ...]:
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def _rage_layout(width: float, height: float, measure, state: dict[str, Any],
                 alpha: float = 1.0) -> list[tuple]:
    """The meter as draw ops: ("rect", x, y, w, h, rgba) and ("text", s, x, y, scale, rgba).

    All coordinates are whole pixels. Rows, top to bottom: header (label left, percentage
    right), the segmented bar, a status line. Nothing overlaps at any size: every row's
    height is its target pixel height, and text is scaled to exactly that height.
    """
    hud = RAGE["hud"]
    u = max(0.5, min(4.0, height / 1080.0))
    px = lambda v: int(round(v * u))  # noqa: E731
    one = max(1, px(1))
    pad, gap_row = px(hud["pad"]), px(hud["row_gap"])
    # cap heights, with a floor so small resolutions stay legible
    label_px = max(int(hud["min_label_px"]), px(hud["label_px"]))
    foot_px = max(int(hud["min_footer_px"]), px(hud["footer_px"]))
    bar_h = px(hud["bar_px"])
    panel_w = px(hud["w"])
    enraged, decaying = state["enraged"], state["decaying"]
    # the status row exists only when there is something to say (an empty meter says nothing)
    has_footer = enraged or decaying or state["value"] > 0 or bool(hud["idle_text"])
    panel_h = pad + label_px + gap_row + bar_h + (gap_row + foot_px if has_footer else 0) + pad
    x0 = int(round(width * hud["x"] - panel_w / 2.0))
    y0 = int(round(height * hud["y"]))
    inner_x, inner_w = x0 + pad, panel_w - 2 * pad
    y_label = y0 + pad
    y_bar = y_label + label_px + gap_row
    y_foot = y_bar + bar_h + gap_row
    frac = state["frac"]
    pulse = 0.5 + 0.5 * math.sin(state["now"] * 5.0)

    def a(colour: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return (colour[0], colour[1], colour[2], int(round(colour[3] * alpha)))

    ops: list[tuple] = []

    def text(s: str, x: int, y: int, cap_px: int, colour, right: bool = False) -> None:
        """Draw s so its CAPITALS are cap_px tall with their top at y."""
        w1, h1 = measure(s)
        scale = cap_px / (h1 * FONT_CAP_RATIO)
        tx = int(round(x - w1 * scale)) if right else x
        ty = int(round(y - h1 * scale * FONT_CAP_TOP))
        ops.append(("text", s, tx + one, ty + one, scale, a((0, 0, 0, 170))))  # drop shadow
        ops.append(("text", s, tx, ty, scale, a(colour)))

    accent = (232, 176, 72, 255) if enraged else (206, 32, 40, 255)
    if enraged:  # a slow glow around the whole panel
        g = one * 3
        ops.append(("rect", x0 - g, y0 - g, panel_w + 2 * g, panel_h + 2 * g,
                    a((255, 64, 32, int(38 + 42 * pulse)))))
    ops.append(("rect", x0, y0, panel_w, panel_h, a((10, 5, 6, 168))))          # backplate
    ops.append(("rect", x0, y0, panel_w, max(one, px(2)), a(accent)))           # top rule

    # header: label left, percentage right
    label = "ENRAGED" if enraged else "RAGE"
    ink = (255, 214, 140, 255) if enraged else (238, 232, 228, 255)
    text(label, inner_x, y_label, label_px, ink)
    text(f"{int(round(frac * 100))}%", inner_x + inner_w, y_label, label_px, ink, right=True)

    # bar: N equal integer segments with N-1 equal integer gaps, centred in the inner width
    # (rounding each edge separately gave 1 px and 2 px gaps side by side)
    segments = int(hud["segments"])
    gap = max(one, px(2))
    seg = max(1, (inner_w - gap * (segments - 1)) // segments)
    bar_w = seg * segments + gap * (segments - 1)
    bx = inner_x + (inner_w - bar_w) // 2
    ops.append(("rect", bx, y_bar, bar_w, bar_h, a((36, 5, 8, 255))))
    if enraged:
        fill = _mix((236, 44, 36, 255), (255, 120, 52, 255), pulse)
    elif decaying:
        fill = (150, 24, 30, 255)
    else:
        fill = (214, 30, 38, 255)
    for i in range(segments):
        left = bx + i * (seg + gap)
        ops.append(("rect", left, y_bar, seg, bar_h, a((74, 10, 16, 255))))
        part = max(0.0, min(1.0, frac * segments - i))
        if part > 0:
            fw = max(1, int(round(seg * part)))
            ops.append(("rect", left, y_bar, fw, bar_h, a(fill)))
            ops.append(("rect", left, y_bar, fw, one, a((255, 160, 150, 150))))  # top light

    # status line: what happens next
    if enraged:
        text(hud["enraged_text"] or _rage_bonus_text(), inner_x, y_foot, foot_px,
             (255, 196, 120, 255))
    elif decaying:
        text("DECAYING", inner_x, y_foot, foot_px, (214, 110, 104, 255))
    elif state["value"] > 0:
        text(f"DECAY IN {int(math.ceil(state['decay_in']))}s", inner_x, y_foot, foot_px,
             (176, 168, 164, 255))
    elif hud["idle_text"]:  # empty meter: nothing to say unless the spec asks
        text(hud["idle_text"], inner_x, y_foot, foot_px, (176, 168, 164, 255))
    return ops


def _rage_tile(canvas: Any, x: float, y: float, w: float, h: float,
               colour: tuple[int, int, int, int]) -> None:
    if w <= 0 or h <= 0 or colour[3] <= 0:
        return
    r, g, b, a = colour
    canvas.SetPos(x, y)
    canvas.DrawTile(_rage_white(), w, h, 0.0, 0.0, _rage_draw["ul"], _rage_draw["vl"],
                    # LinearColor is LINEAR: sRGB 0..255 must be un-gamma'd or reds wash to pink
                    unrealsdk.make_struct("LinearColor", R=(r / 255.0) ** 2.2, G=(g / 255.0) ** 2.2,
                                          B=(b / 255.0) ** 2.2, A=a / 255.0),
                    False, _rage_draw["blend"])


def _rage_meter(canvas: Any, alpha: float) -> None:
    width, height = float(canvas.ClipX), float(canvas.ClipY)
    if not width or not height:
        return
    font = _rage_font()
    if font is not None:
        canvas.Font = font
    measure = _rage_measure_on(canvas)
    ops = _rage_layout(width, height, measure, _rage_state(time.time()), alpha)
    if _rage_draw.get("size") != (width, height):  # one record per resolution
        _rage_draw["size"] = (width, height)
        _write({"rage": "hud_layout", "clip": [width, height], "font": _rage_draw.get("font_path"),
                "measured_RAGE": measure("RAGE"), "fallback": bool(_rage_draw.get("measure_fallback")),
                "ops": len(ops)})
    for op in ops:
        if op[0] == "rect":
            _rage_tile(canvas, *op[1:])
        else:
            try:  # text in its own guard: a font problem must not blank the bar
                _, s, x, y, scale, (r, g, b, a) = op
                if a <= 0:
                    continue
                canvas.SetDrawColor(r, g, b, a)
                canvas.SetPos(x, y)
                canvas.DrawText(s, False, scale, scale)
            except Exception as ex:  # noqa: BLE001
                _rage_counts["draw_errors"] += 1
                if not _rage_draw.get("text_warned"):
                    _rage_draw["text_warned"] = True
                    _write({"rage": "text_error", "error": repr(ex)})


@hook("WillowGame.WillowGameViewportClient:PostRender", Type.POST)
def rage_post_render(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    canvas = args.Canvas
    if canvas is None:
        return
    try:
        controller = _rage_pc()
        playing = _rage_in_play(controller)
        wanted = playing and (_rage["value"] > 0.0 or _rage_held(controller) is not None)
        # fade in/out over hud.fade_s rather than popping
        now = time.time()
        step = min(max(now - _rage_draw.get("fade_t", now), 0.0), 0.1)
        _rage_draw["fade_t"] = now
        rate = step / max(RAGE["hud"]["fade_s"], 0.01)
        alpha = _rage_draw.get("alpha", 0.0)
        alpha = min(1.0, alpha + rate) if wanted else max(0.0, alpha - rate)
        _rage_draw["alpha"] = alpha
        if not playing or alpha <= 0.0:
            return
        _rage_meter(canvas, alpha)
    except Exception as ex:  # noqa: BLE001 - a draw slip must never take the frame down
        _rage_counts["draw_errors"] += 1
        if not _rage_draw["warned"]:
            _rage_draw["warned"] = True
            _log(f"rage: meter draw failed: {ex!r}")
            _write({"rage": "draw_error", "error": repr(ex)})'''


#: harness-only: drive the real rage paths against the nearest AI after the scored capture
RAGE_TEST_SECTION = '''


# --------------------------------------------------------------------- rage test (harness)
RAGE_TEST: dict[str, Any] = $rage_test_literal
_rt: dict[str, Any] = {"step": 0, "t": 0.0, "armed": 0.0, "log": []}


def _rt_target() -> Any:
    player = get_pc().Pawn
    here = player.Location
    best, best_d = None, None
    for pawn in unrealsdk.find_all("WillowAIPawn", exact=False):
        try:
            if pawn is player or pawn.Class.Name.startswith("Default__") or pawn.Controller is None:
                continue
            d = (pawn.Location.X - here.X) ** 2 + (pawn.Location.Y - here.Y) ** 2
            if best_d is None or d < best_d:
                best, best_d = pawn, d
        except Exception:  # noqa: BLE001
            continue
    return best


def _rt_hit(target: Any, damage: int = 10) -> str:
    controller = get_pc()
    try:
        before = float(target.GetHealth())
    except Exception:  # noqa: BLE001
        before = None
    target.TakeDamage(Damage=damage, InstigatedBy=controller, HitLocation=target.Location,
                      Momentum=unrealsdk.make_struct("Vector"),
                      DamageType=unrealsdk.find_class("WillowDamageType"),
                      DamageCauser=controller.Pawn.Weapon)
    try:
        after = float(target.GetHealth())
    except Exception:  # noqa: BLE001
        after = None
    return f"health {before} -> {after}"


def _rt_attr(obj: Any, name: str) -> Any:
    try:
        value = getattr(obj, name)
        return round(float(value), 3)
    except Exception as ex:  # noqa: BLE001
        return repr(ex)


def _rt_stack(obj: Any, name: str) -> Any:
    """An attribute's modifier stack as text (where a stat's extra points come from)."""
    try:
        return [str(entry)[:200] for entry in getattr(obj, name)]
    except Exception as ex:  # noqa: BLE001
        return repr(ex)


def _rt_row(what: str, **extra: Any) -> None:
    pawn = get_pc().Pawn
    row = {"step": what, "rage": round(_rage["value"], 2), "enraged": _rage["enraged"],
           "ground_speed": round(float(pawn.GroundSpeed), 1), **extra}
    _rt["log"].append(row)
    _write({"rage_test": row})


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def rage_test_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    try:
        now = time.time()
        if _rt["armed"] == 0.0:
            return
        if now - _rt["armed"] < RAGE_TEST.get("start_s", 7.5) or now < _rt["t"]:
            return
        controller = get_pc()
        step = _rt["step"]
        target = _rt.get("target")
        if step == 0:
            _rt["target"] = target = _rt_target()
            weapon = controller.Pawn.Weapon
            _rt_row("start", target=target._path_name() if target else None,
                    clip_size=_rt_attr(weapon, "ClipSize"), clip_base=_rt_attr(weapon, "ClipSizeBaseValue"),
                    clip_stack=_rt_stack(weapon, "ClipSizeModifierStack"),
                    spread=_rt_attr(weapon, "Spread"), spread_base=_rt_attr(weapon, "SpreadBaseValue"))
            controller.StartFire(0)
            _rt.update(step=1, t=now + 0.15)
        elif step == 1:  # a hip shot that hits twice (two pellets): +gain_hit once
            controller.StopFire(0)
            hits = [_rt_hit(target), _rt_hit(target)] if target is not None else []
            _rt_row("hip_hit", hits=hits)
            _rt.update(step=2, t=now + 1.2)
        elif step == 2:
            controller.StartAltFire()
            _rt.update(step=3, t=now + 0.15)
        elif step == 3:  # an alt shot that hits: +gain_alt_hit
            controller.StopAltFire()
            hits = [_rt_hit(target), _rt_hit(target)] if target is not None else []
            _rt_row("alt_hit", hits=hits)
            _rt.update(step=4, t=now + 0.5)
        elif step == 4:  # top up to full: Enraged
            _rage_add(float(RAGE["max"]), "test_fill")
            _rt.update(step=5, t=now + 0.5)
        elif step == 5:  # Enraged: a 100-point hit on the LIVE target must take 125
            hit = _rt_hit(target, 100) if target is not None else "no target"
            _rt_row("enraged", damage_hit=hit, scaled=_rage_counts["scaled"])
            _rage["last_gain"] = now - RAGE["decay_delay_s"] - 0.01  # decay must start now
            _rt.update(step=6, t=now + 1.0)
        elif step == 6:
            _rt_row("decaying")  # below max: calm again, speed back
            _rage["value"] = 50.0
            _rage["last_gain"] = now
            if target is not None:  # a kill while holding the weapon: 50 -> 65
                target.Died(Killer=controller, DamageType=unrealsdk.find_class("WillowDamageType"),
                            HitLocation=target.Location)
            _rt_row("kill")
            _rt.update(step=7, t=now + 0.5)
        elif step == 7:
            _rage_add(float(RAGE["max"]), "test_fill")
            _rage["last_gain"] = now + RAGE_TEST.get("hold_s", 0.0)  # stay Enraged for the capture
            _rt_row("enraged_for_capture")
            rage_test_tick.disable()
    except Exception as ex:  # noqa: BLE001
        rage_test_tick.disable()
        _write({"rage_test": {"error": f"{ex!r}", "step": _rt.get("step")}})


def _rage_test_arm() -> None:
    _rt.update(step=0, t=0.0, armed=time.time())
    rage_test_tick.enable()'''

#: harness-only: resolutions x meter states, captured by run_loop --hud-watch
HUD_TEST_SECTION = '''


# --------------------------------------------------------------------- HUD test (harness)
# Step the game through windowed resolutions and the meter through its states; for each
# combination write {"hud_capture": name} and hold it, so the driver (run_loop --hud-watch)
# screenshots exactly that frame. The game's own resolution setting is the user's: the loop
# restores WillowEngine.ini after the run.
HUD_TEST: dict[str, Any] = $hud_test_literal
_ht: dict[str, Any] = {"armed": 0.0, "queue": [], "t": 0.0}
_HT_STATES = {
    "idle": {"value": 0.0, "idle_s": 0.0},
    "building": {"value": 43.0, "idle_s": 3.4},
    "decaying": {"value": 71.0, "idle_s": 12.0},
    "enraged": {"value": 100.0, "idle_s": 0.0},
}


def _hud_test_arm() -> None:
    queue = []
    for res in HUD_TEST["resolutions"] or ["native"]:
        if res != "native":  # a live setres stalls the render loop; prefer one launch per size
            queue.append(("setres", res))
        queue += [("state", res, s) for s in HUD_TEST["states"]]
    _ht.update(armed=time.time(), queue=queue, t=0.0)
    hud_test_tick.enable()
    hud_test_cue.enable()


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def hud_test_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    try:
        now = time.time()
        if _ht["armed"] == 0.0 or now - _ht["armed"] < HUD_TEST.get("start_s", 8.0) or now < _ht["t"]:
            return
        if not _ht["queue"]:
            hud_test_tick.disable()
            _write({"hud_test": "done"})
            return
        if _ht["queue"][0][0] == "capture":  # the POST cue owns this step
            return
        step = _ht["queue"].pop(0)
        if step[0] == "setres":
            get_pc().ConsoleCommand(f"setres {step[1]}w")
            _ht["t"] = now + HUD_TEST.get("settle_s", 2.5)
            return
        _, res, state = step
        spec = _HT_STATES[state]
        _rage["value"] = spec["value"]
        _rage["last_gain"] = now - spec["idle_s"]
        _ht["t"] = now + 0.6  # let the tick settle Enraged and the fade finish
        _ht["queue"].insert(0, ("capture", res, state))
    except Exception as ex:  # noqa: BLE001
        hud_test_tick.disable()
        _write({"hud_test": "error", "error": repr(ex)})


@hook("WillowGame.WillowGameViewportClient:Tick", Type.POST)
def hud_test_cue(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    """Cue the driver once the state has settled; hold it for hold_s."""
    try:
        if not _ht["queue"] or _ht["queue"][0][0] != "capture" or time.time() < _ht["t"]:
            return
        _, res, state = _ht["queue"].pop(0)
        _rage["last_gain"] = time.time() - _HT_STATES[state]["idle_s"]  # freeze the countdown
        size = _rage_draw.get("size") or ()
        tag = "x".join(str(int(v)) for v in size) if res == "native" and size else res
        _write({"hud_capture": f"{tag}_{state}", "clip": list(size),
                "alpha": _rage_draw.get("alpha")})
        _ht["t"] = time.time() + HUD_TEST.get("hold_s", 1.6)
    except Exception as ex:  # noqa: BLE001
        _write({"hud_test": "error", "error": repr(ex)})'''
