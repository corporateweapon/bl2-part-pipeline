"""Character model swaps at runtime: the logic shared by every mod that wears bl2_charswap skins.

Two hosts use it: the pipeline's own ``PipelineCharacters`` mod (skins from ``characters.json``
and ``skins/*.json``, test keys on) and the **Armory**, which carries a copy as
``Armory/_render/charswap_runtime.py`` and feeds it the skins of its character packs. Each host
imports this module once, calls :func:`configure` with its skins, and wires the returned hooks,
options and command into its own ``build_mod``.

A skin names the packages to load and a table ``source mesh path -> {mesh, material}``. One
dropdown per vault hunter ("Default" or one of the skins). While a skin is active every
SkeletalMeshComponent that wears one of its source meshes is re-pointed -- the pawn's body and
first-person arms, the character-menu preview, co-op copies -- and the character's separate head
mesh is hidden on those actors. Same skeleton, same animations, same sockets.

Console: ``characters list | set <character> <skin|default> | census | reapply``.
Stdlib + unrealsdk + mods_base only (it ships inside the Armory).
"""
from __future__ import annotations

import json
import os
import time
import traceback
from typing import Any

import unrealsdk
from mods_base import ButtonOption, DropdownOption, NestedOption, command, get_pc, hook, keybind
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

MENU_TICKS = 240   # ~4 s at the menu: packages load, then the menu pawn is dressed
SCAN_TICKS = 10
MENU_BURST_TICKS = 90   # after a status menu opens, scan every tick for this long
KEEP_ALIVE_FLAG = 0x4000
DEFAULT = "Default"
#: the vault hunters a skin may name (the dropdown per character)
VAULT_HUNTERS = ("Axton", "Maya", "Salvador", "Zer0", "Gaige", "Krieg")

#: set by configure(): the host's log prefix, its skins, and where status records go
MOD_NAME = "Characters"
SKINS: list[dict[str, Any]] = []
BY_ID: dict[str, dict[str, Any]] = {}
CHARACTERS: list[str] = []
STATUS: str | None = None
characters_menu: NestedOption | None = None


def configure(skins: list[dict[str, Any]], *, log_name: str, status_path: str | None,
              test_keys: bool = False) -> dict[str, list[Any]]:
    """Take the host's skins; build one dropdown per character; return what to wire.

    ``{"hooks", "keybinds", "options", "commands"}``: the host passes them to its build_mod
    and enables the hooks at import (a skin has to apply whatever the mod menu says). The
    test keybinds (behind view F7, census F9, inventory, grant) are for the pipeline's own
    checks and are only returned with ``test_keys``.
    """
    global MOD_NAME, STATUS, characters_menu
    MOD_NAME, STATUS = log_name, status_path
    SKINS[:] = list(skins)
    BY_ID.clear()
    BY_ID.update({s["id"]: s for s in SKINS})
    CHARACTERS[:] = sorted({s["character"] for s in SKINS})
    _options.clear()
    for character in CHARACTERS:
        labels = [DEFAULT] + [s["label"] for s in SKINS if s["character"] == character]
        # a skin marked "default" (a pack's) is worn from the first launch; a saved choice wins
        preset = next((s["label"] for s in SKINS if s["character"] == character and s.get("default")),
                      DEFAULT)
        _options[character] = DropdownOption(
            character, preset, labels,
            description=f"Which model {character} wears (body and first-person arms). "
                        "Default = the game's own.",
            on_change_anytime=_on_change,
        )
    characters_menu = NestedOption(
        "Characters", [*_options.values(), reapply_button, census_button],
        description="Character skins: pick a replacement body and first-person arms per vault hunter.",
    )
    return {
        "hooks": [menu_tick, on_map_loaded, scan_tick, pending_tick, on_status_menu],
        "keybinds": [kb_behind, kb_inventory, kb_census, kb_grant] if test_keys else [],
        "options": [characters_menu],
        "commands": [characters_cmd],
    }


_status: list[dict[str, Any]] = []
_registered = False
_in_map = False
_scan = 0
_behind = False
#: skin id -> {source mesh path: {"mesh": UObject, "mic": UObject}}
_loaded: dict[str, dict[str, dict[str, UObject]]] = {}
#: source mesh path -> {"mesh", "mic", "skin"} for the skins selected right now
_active: dict[str, dict[str, Any]] = {}
_hide_prefixes: list[str] = []
#: component path -> original state, for restore()
_swapped: dict[str, dict[str, Any]] = {}
_hidden: dict[str, dict[str, Any]] = {}


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[{MOD_NAME}] {msg}")


def _write(record: dict[str, Any]) -> dict[str, Any]:
    record["t"] = time.time()
    _status.append(record)
    if not STATUS:
        return record
    try:
        os.makedirs(os.path.dirname(STATUS), exist_ok=True)
        with open(STATUS, "w", encoding="utf-8") as handle:
            json.dump(_status[-300:], handle, indent=1, default=str)
    except Exception as ex:  # noqa: BLE001
        _log(f"status write failed: {ex!r}")
    return record


def keep_alive(obj: UObject | None) -> None:
    while obj is not None:
        obj.ObjectFlags |= KEEP_ALIVE_FLAG
        obj = obj.Outer


def _find(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except Exception:  # noqa: BLE001
        return None


def _path(obj: UObject | None) -> str | None:
    try:
        return obj._path_name() if obj is not None else None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------- registration
def _build_material(mat: dict[str, Any]) -> UObject:
    parent = unrealsdk.find_object("MaterialInterface", mat["parent"])
    mic = _find("MaterialInstanceConstant", f"{mat['outer']}.{mat['name']}")
    if mic is None:
        outer = _find("Package", mat["outer"]) or parent.Outer
        mic = unrealsdk.construct_object("MaterialInstanceConstant", outer, mat["name"], 0, None)
    keep_alive(mic)
    mic.SetParent(parent)
    for pname, tpath in mat.get("textures", {}).items():
        tex = _find("Texture", tpath)
        if tex is None:
            _log(f"material {mat['name']}: texture {tpath} not found")
            continue
        keep_alive(tex)
        mic.SetTextureParameterValue(pname, tex)
    return mic


def setup() -> dict[str, Any]:
    """Load every skin's packages and materials once (at the main menu, before any save)."""
    global _registered
    record: dict[str, Any] = {"phase": "setup", "skins": {}}
    if _registered:
        record["skipped"] = True
        return _write(record)
    ok = True
    for skin in SKINS:
        try:
            for pkg in skin["packages"]:
                keep_alive(unrealsdk.load_package(pkg))
            table: dict[str, dict[str, UObject]] = {}
            for source, swap in skin["swaps"].items():
                mesh = unrealsdk.find_object("SkeletalMesh", swap["mesh"])
                keep_alive(mesh)
                for socket in mesh.Sockets:
                    keep_alive(socket)
                table[source] = {"mesh": mesh, "mic": _build_material(swap["material"])}
            _loaded[skin["id"]] = table
            record["skins"][skin["id"]] = {src: f"{_path(t['mesh'])} bones={len(t['mesh'].RefSkeleton)} mic={_path(t['mic'])}"
                                           for src, t in table.items()}
        except Exception as ex:  # noqa: BLE001
            ok = False
            record["skins"][skin["id"]] = f"FAILED {ex!r}\n{traceback.format_exc()}"
            _log(f"skin {skin['id']} failed: {ex!r}")
    _registered = ok
    record["ok"] = ok
    _refresh_active()
    record["active"] = {src: a["skin"] for src, a in _active.items()}
    _log(f"setup ok={ok}: {record['active']}")
    _write(record)
    # the main menu already shows the character (its own pawn in the menu map): dress it now
    scan("setup")
    return record


def _selected_skin(character: str) -> dict[str, Any] | None:
    label = str(_options[character].value)
    for skin in SKINS:
        if skin["character"] == character and skin["label"] == label:
            return skin
    return None


def _refresh_active() -> None:
    _active.clear()
    _hide_prefixes.clear()
    for character in CHARACTERS:
        skin = _selected_skin(character)
        if skin is None or skin["id"] not in _loaded:
            continue
        for source, objs in _loaded[skin["id"]].items():
            _active[source] = {"mesh": objs["mesh"], "mic": objs["mic"], "skin": skin["id"]}
        _hide_prefixes.extend(skin.get("hide_mesh_prefixes", []))


# --------------------------------------------------------------------- the swap
def _derives_from(material: UObject | None, mic: UObject) -> bool:
    """True when ``material`` is ``mic`` or a MaterialInstance chained to it (UObject wrappers do
    not compare by identity, so paths are compared)."""
    target = _path(mic)
    current = material
    for _ in range(8):
        if current is None:
            return False
        if _path(current) == target:
            return True
        current = getattr(current, "Parent", None)
    return False


def _components() -> list[UObject]:
    out = []
    for comp in unrealsdk.find_all("SkeletalMeshComponent", exact=False):
        try:
            if comp.Owner is None or comp.SkeletalMesh is None:
                continue
            if _path(comp).startswith("Default__") or comp.Class.Name.startswith("Default__"):
                continue
            out.append(comp)
        except Exception:  # noqa: BLE001 - half-destroyed components
            continue
    return out


def scan(reason: str) -> dict[str, Any]:
    """Re-point every live component wearing a source mesh; hide heads on swapped actors."""
    acted: list[str] = []
    owners: set[str] = {rec["owner"] for rec in _swapped.values()}
    try:
        comps = _components()
        for comp in comps:
            cpath = _path(comp)
            current = _path(comp.SkeletalMesh)
            entry = _active.get(current)
            if entry is not None:
                _swapped[cpath] = {"comp": comp, "mesh": comp.SkeletalMesh, "materials": list(comp.Materials),
                                   "owner": _path(comp.Owner), "source": current}
                comp.SetSkeletalMesh(entry["mesh"], False)
                comp.SetMaterial(0, entry["mic"])
                owners.add(_path(comp.Owner))
                acted.append(f"{cpath}: {current} -> {_path(entry['mesh'])}")
                continue
            rec = _swapped.get(cpath)
            if rec is not None and current == _path(_active.get(rec["source"], {}).get("mesh")):
                mic = _active[rec["source"]]["mic"]
                # the game wraps whatever we set in its own per-component MaterialInstanceConstant
                # (shield/status effects), so accept any material that derives from ours
                first = comp.Materials[0] if len(comp.Materials) else None
                if not _derives_from(first, mic):
                    comp.SetMaterial(0, mic)
                    acted.append(f"{cpath}: material re-applied (was {_path(first)} <- {_path(getattr(first, 'Parent', None))})")
        for comp in comps:
            cpath = _path(comp)
            current = _path(comp.SkeletalMesh) or ""
            if any(current.startswith(p) for p in _hide_prefixes) and _path(comp.Owner) in owners:
                if not comp.HiddenGame:
                    if cpath not in _hidden:
                        _hidden[cpath] = {"comp": comp, "owner": _path(comp.Owner)}
                    comp.SetHidden(True)
                    acted.append(f"{cpath}: hidden ({current})")
    except Exception as ex:  # noqa: BLE001
        _log(f"scan FAILED: {ex!r}")
        return _write({"phase": "scan", "reason": reason, "error": f"{ex!r}\n{traceback.format_exc()}"})
    if acted:
        _log(f"scan ({reason}): {len(acted)} change(s)")
        return _write({"phase": "scan", "reason": reason, "acted": acted, "swapped": len(_swapped), "hidden": len(_hidden)})
    return {"phase": "scan", "reason": reason, "acted": []}


def restore(reason: str) -> dict[str, Any]:
    restored: list[str] = []
    for cpath, rec in list(_swapped.items()):
        try:
            comp = rec["comp"]
            comp.SetSkeletalMesh(rec["mesh"], False)
            for i, mat in enumerate(rec["materials"]):
                comp.SetMaterial(i, mat)
            restored.append(cpath)
        except Exception:  # noqa: BLE001 - component gone
            pass
    for cpath, rec in list(_hidden.items()):
        try:
            rec["comp"].SetHidden(False)
            restored.append(cpath)
        except Exception:  # noqa: BLE001
            pass
    _swapped.clear()
    _hidden.clear()
    return _write({"phase": "restore", "reason": reason, "restored": restored})


def reapply(reason: str) -> None:
    restore(reason)
    _refresh_active()
    scan(reason)


def census() -> dict[str, Any]:
    """Every live skeletal component: what it wears and who owns it (evidence for the driver)."""
    rows = []
    for comp in _components():
        try:
            rows.append(f"{_path(comp)} owner={_path(comp.Owner)} mesh={_path(comp.SkeletalMesh)} hidden={bool(comp.HiddenGame)}")
        except Exception:  # noqa: BLE001
            continue
    return _write({"phase": "census", "components": rows, "swapped": sorted(_swapped), "hidden": sorted(_hidden)})


# --------------------------------------------------------------------- hooks
_menu_ticks = 0


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def menu_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _menu_ticks
    _menu_ticks += 1
    if _menu_ticks < MENU_TICKS:
        return
    menu_tick.disable()
    setup()


@hook("WillowGame.WillowPlayerController:WillowClientDisableLoadingMovie", Type.POST)
def on_map_loaded(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _in_map, _scan
    if not _registered:
        setup()
    _in_map = True
    _swapped.clear()   # the previous map's components are gone
    _hidden.clear()
    _write({"phase": "map_loaded"})
    scan("map_loaded")
    _scan = SCAN_TICKS


_burst = 0


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def scan_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _scan, _burst
    # runs at the main menu too: the title screen never fires the map-load hook (F9), and the
    # menu character (SELECT CHARACTER can change it) should wear the skin before CONTINUE
    if not (_registered and _active):
        return
    if _burst > 0:
        _burst -= 1
        scan("menu-burst")
        return
    _scan -= 1
    if _scan > 0:
        return
    _scan = SCAN_TICKS
    scan("tick")


@hook("WillowGame.WillowPlayerController:ShowStatusMenu", Type.POST)
def on_status_menu(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    """The status menu spawns a fresh body+head preview rig on the pawn: catch it on its first
    frames rather than at the next periodic scan."""
    global _burst
    _burst = MENU_BURST_TICKS
    scan("status_menu")


# --------------------------------------------------------------------- menu + keys
def _on_change(option: DropdownOption, value: str) -> None:
    """mods_base updates the option's value AFTER this returns, so the re-apply is deferred to
    the next tick.  Never assign option.value here: that fires this callback again."""
    _pending.append(f"menu {option.identifier} -> {value}")


_pending: list[str] = []


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def pending_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    if not _pending:
        return
    reasons = list(_pending)
    _pending.clear()
    try:
        reapply("; ".join(reasons))
    except Exception as ex:  # noqa: BLE001
        _log(f"re-apply failed: {ex!r}")


_options: dict[str, DropdownOption] = {}

reapply_button = ButtonOption(
    "Re-apply now", description="Restore and re-apply the selected skins to every character in the level.",
    on_press=lambda _o: reapply("button"),
)
census_button = ButtonOption(
    "Census (status file)", description="Write every skeletal mesh component in the level to the status file.",
    on_press=lambda _o: census(),
)
@keybind("Characters: toggle behind view", "F7")
def kb_behind() -> None:
    global _behind
    _behind = not _behind
    try:
        get_pc().SetBehindView(_behind)
        _write({"phase": "behindview", "on": _behind})
    except Exception as ex:  # noqa: BLE001
        _write({"phase": "behindview", "error": repr(ex)})


@keybind("Characters: open inventory (test)", None)
def kb_inventory() -> None:
    try:
        get_pc().ShowStatusMenu_Inventory()
        _write({"phase": "inventory", "opened": True})
    except Exception as ex:  # noqa: BLE001
        _write({"phase": "inventory", "error": repr(ex)})


@keybind("Characters: census", "F9")
def kb_census() -> None:
    census()


# Test aid: hand the player a customization (skin) item so the inventory shows its 3D character
# preview.  Same mission-reward hijack the Armory uses; the pristine reward table goes back a
# few seconds later.
GRANT_MISSION = "GD_Episode01.M_Ep1_Champion"
GRANT_BALANCE = "GD_Psycho_Items_Lilac.BalanceDefs.Psycho_Skin_BlueA"
_grant_backup: dict[str, Any] | None = None
_grant_restore_left = 0


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def grant_restore_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _grant_restore_left, _grant_backup
    _grant_restore_left -= 1
    if _grant_restore_left > 0:
        return
    grant_restore_tick.disable()
    if _grant_backup:
        m = _grant_backup["mission"]
        m.GameStage = _grant_backup["game_stage"]
        m.Reward.RewardItems = _grant_backup["reward_items"]
        m.Reward.RewardItemPools = _grant_backup["reward_pools"]
        _grant_backup = None
        _write({"phase": "grant_restored"})


@keybind("Characters: grant a skin item (test)", None)
def kb_grant() -> None:
    global _grant_backup, _grant_restore_left
    record: dict[str, Any] = {"phase": "grant", "balance": GRANT_BALANCE}
    try:
        controller = get_pc()
        balance = unrealsdk.find_object("InventoryBalanceDefinition", GRANT_BALANCE)
        mission = unrealsdk.find_object("MissionDefinition", GRANT_MISSION)
        if _grant_backup is None:
            _grant_backup = {"mission": mission, "game_stage": mission.GameStage,
                             "reward_items": list(mission.Reward.RewardItems),
                             "reward_pools": list(mission.Reward.RewardItemPools)}
        mission.GameStage = int(controller.PlayerReplicationInfo.ExpLevel)
        mission.Reward.RewardItems = [balance]
        mission.Reward.RewardItemPools = []
        controller.ServerGrantMissionRewards(mission, False)
        _grant_restore_left = 300
        grant_restore_tick.enable()
        record["ok"] = True
    except Exception as ex:  # noqa: BLE001
        record["error"] = f"{ex!r}\n{traceback.format_exc()}"
    _write(record)


# --------------------------------------------------------------------- console
@command("characters", description="Character skins: list, set <character> <skin|default>, census, reapply")
def characters_cmd(args: Any) -> None:
    if args.action == "list":
        for character in CHARACTERS:
            current = str(_options[character].value)
            _log(f"{character}: {current}   (choices: {', '.join(_options[character].choices)})")
        for skin in SKINS:
            _log(f"  {skin['id']:<10} {skin['label']:<18} {skin['character']:<8} {', '.join(skin['swaps'])}")
    elif args.action == "set":
        character = next((c for c in CHARACTERS if c.lower() == (args.character or "").lower()), None)
        if character is None:
            _log("usage: characters set <character> <skin id|label|default>; characters: " + ", ".join(CHARACTERS))
            return
        want = (args.skin or DEFAULT)
        label = DEFAULT
        for skin in SKINS:
            if skin["character"] == character and want.lower() in (skin["id"].lower(), skin["label"].lower()):
                label = skin["label"]
        if want.lower() != DEFAULT.lower() and label == DEFAULT:
            _log(f"unknown skin {want!r} for {character}; choices: " + ", ".join(_options[character].choices))
            return
        _options[character].value = label          # persists through mods_base settings
        reapply(f"console {character} -> {label}")
        _log(f"{character} -> {label}")
    elif args.action == "census":
        rec = census()
        for row in rec["components"]:
            _log(row)
    elif args.action == "reapply":
        reapply("console")


characters_cmd.add_argument("action", choices=("list", "set", "census", "reapply"))
characters_cmd.add_argument("character", nargs="?", default=None)
characters_cmd.add_argument("skin", nargs="?", default=None)
