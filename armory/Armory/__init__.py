"""Armory -- the framework for custom Borderlands 2 content: weapons and character skins from packs.

A pack is a folder ``sdk_mods/ArmoryPacks/<id>/`` holding ``armory_pack.json`` (no Python) plus
its ``.upk`` files in ``WillowGame/CookedPCConsole/``. It carries weapons, character skins, or
both. Packs are built by the bl2-part-pipeline (``bl2 pack``); CREATING_PACKS.md says how.

Character skins (Armory 1.1.0, pack schema 2) are data: each names the packages to load and
which of a vault hunter's meshes to replace. The Armory checks every field
(``_render.packdata.load_character``) and hands the skins to its character runtime
(``_render/charswap_runtime.py``, the same module the pipeline's dev ``PipelineCharacters`` mod
runs): one dropdown per vault hunter under Mods > Armory > Characters, console ``characters``.
While ``sdk_mods/PipelineCharacters`` (the dev mod) is installed the Armory leaves characters to
it, so no mesh is swapped twice.

At import, for every pack folder, the Armory:

  1. reads armory_pack.json and refuses it (with the reason, in ``armory packs`` and the mod
     menu) if its schema, required Armory version or features are newer than this Armory;
  2. rebuilds each weapon's resolved spec and checks every name it would paste into code;
  3. refuses a pack that would construct an object another pack (or another installed
     pipeline mod, e.g. an old PipelineArmory) already owns -- a weapon registered twice
     corrupts its gestalt (rule 3 of the pipeline);
  4. checks the pack's ``.upk`` files are in CookedPCConsole and are the build it was made
     with (SHA1), copying them there from ``<pack>/packages/`` if the pack carries them;
  5. renders each weapon's component module with the pipeline's own renderer (``_render``,
     the same code ``bl2_partgen`` emits Armory components with) and runs it as
     ``Armory.packs.<pack>.<weapon>``.

Each component then registers its package, fragments, parts, balance, title and material at
the main menu (F16), keeps its save round-trip (F18/F29) and its other hooks. Those hooks are
on for as long as the Armory is installed -- they are not tied to the mod-menu toggle, because
a save written while they are off loses the parts of every custom weapon in it. The toggle
turns the spawn keybind on and off.

Spawn surface:
  console   armory list | armory packs | armory census
            armory spawn <id> [--level N] [--no-equip]
            characters list | set <character> <skin|default> | census | reapply
  menu      Mods > Armory > Weapon (dropdown), Spawn selected (button), Characters, Packs
  keybind   F5 (rebindable): spawn the selected weapon

Spawning borrows one mission's reward table (GD_Episode01.M_Ep1_Champion) and puts it back
RESTORE_TICKS later through the registry every pipeline mod shares (F26).

Unattended evidence (bl2_verify/armory_check.py): control.json {"auto_spawn": [ids],
"status_path": ...} makes every gameplay map load write a census_on_load record, spawn each
id in turn, and end with a census + {"done": true}. Without it nothing runs on its own.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
import types
from typing import Any

import unrealsdk
from mods_base import (
    ButtonOption,
    DropdownOption,
    NestedOption,
    build_mod,
    command,
    get_pc,
    hook,
    keybind,
)
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

from ._render.packdata import (
    ARMORY_VERSION,
    PACK_FILE,
    PACK_FORMAT,
    PACK_SCHEMA,
    PackError,
    character_claims,
    claimed_paths,
    load_character,
    load_weapon,
    render_weapon,
    version_tuple,
)
from ._render.templates import SLOT_FIELDS

try:  # the character runtime (Armory 1.1.0+); an older _render folder simply has no characters
    from ._render import charswap_runtime as _chars
except Exception as _ex:  # noqa: BLE001
    _chars = None
    _CHARS_IMPORT_ERROR = repr(_ex)
else:
    _CHARS_IMPORT_ERROR = None

try:  # optional: a HUD toast when a weapon is spawned (ui_utils.sdkmod)
    from ui_utils import show_hud_message
except Exception:  # noqa: BLE001
    show_hud_message = None

__version__ = ARMORY_VERSION
__author__ = "44M0N"

# --------------------------------------------------------------------- where things are
MOD_NAME = "Armory"
MOD_DIR = os.path.dirname(os.path.abspath(__file__))
SDK_MODS = os.path.dirname(MOD_DIR)
GAME_DIR = os.path.dirname(SDK_MODS)
COOKED_DIR = os.path.join(GAME_DIR, "WillowGame", "CookedPCConsole")
PACKS_DIR = os.path.join(SDK_MODS, "ArmoryPacks")
LOG_DIR = os.path.join(MOD_DIR, "logs")
#: optional; {"status_path": "...", "auto_spawn": [...]} for the verify loop
CONTROL_FILE = os.path.join(MOD_DIR, "control.json")
DEFAULT_STATUS_FILE = os.path.join(LOG_DIR, "armory.status.json")
CHARACTER_STATUS_FILE = os.path.join(LOG_DIR, "characters.status.json")
#: the pipeline's dev character mod; while it is installed the Armory leaves skins to it
DEV_CHARACTERS_MOD = "PipelineCharacters"

SPAWN_MISSION = "GD_Episode01.M_Ep1_Champion"
SPAWN_KEY = "F5"
#: viewport ticks the reward table stays hijacked before it is put back (F26)
RESTORE_TICKS = 300
#: seconds after a grant before the new weapon is equipped (tick counts scale with FPS, F25)
EQUIP_DELAY = 0.75

_PACK_ID = re.compile(r"^[A-Za-z0-9_]{1,40}$")
_WEAPON_ID = re.compile(r"^[a-z0-9_]{1,32}$")
_VERSION = re.compile(r"^[0-9A-Za-z.+\-]{1,24}$")
_UPK = re.compile(r"^[A-Za-z0-9_]+\.upk$")
_PACKAGE_LINE = re.compile(r'(?m)^PACKAGE = "([A-Za-z0-9_]+)"')


# --------------------------------------------------------------------- plumbing
def _log(message: str) -> None:
    unrealsdk.logging.info(f"[{MOD_NAME}] {message}")


def _warn(message: str) -> None:
    unrealsdk.logging.warning(f"[{MOD_NAME}] {message}")


def _control() -> dict[str, Any]:
    try:
        with open(CONTROL_FILE, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:  # noqa: BLE001 - a missing/broken control file must never break the mod
        return {}


_status: list[dict[str, Any]] = []


def _write(record: dict[str, Any]) -> dict[str, Any]:
    """Append a record to the Armory's status JSON (spawns, censuses, pack loading)."""
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


def _hud(text: str) -> None:
    _log(text)
    if show_hud_message is not None:
        try:
            show_hud_message(MOD_NAME, text)
        except Exception:  # noqa: BLE001 - the HUD helper may fail silently by design
            pass


def _sha1(path: str) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------- pack loading
def _packages_of_other_mods() -> dict[str, str]:
    """``{package: mod folder}`` for every other pipeline mod in sdk_mods.

    A generated pipeline mod enables its hooks at import whatever the mod menu says (F8), so
    being in sdk_mods at all means it registers its weapon -- and a pack registering the same
    package again would append its fragments twice.
    """
    owned: dict[str, str] = {}
    try:
        names = sorted(os.listdir(SDK_MODS))
    except OSError:
        return owned
    ours = os.path.basename(MOD_DIR)
    for name in names:
        if name.startswith((".", "_")) or name in (ours, "ArmoryPacks"):
            continue
        folder = os.path.join(SDK_MODS, name)
        init = os.path.join(folder, "__init__.py")
        if not os.path.isfile(init):
            continue
        files = [init]
        weapons = os.path.join(folder, "weapons")
        if os.path.isdir(weapons):
            files += [os.path.join(weapons, f) for f in sorted(os.listdir(weapons))
                      if f.endswith(".py")]
        for path in files:
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    head = handle.read(65536)
            except OSError:
                continue
            for package in _PACKAGE_LINE.findall(head):
                owned.setdefault(package, name)
    return owned


def _ensure_package(pack_dir: str, meta: dict[str, Any]) -> str | None:
    """Check one of the pack's .upk files is in CookedPCConsole (copying it there if the pack
    carries it); return a note when something was copied, raise PackError when it cannot be."""
    file = str(meta.get("file", ""))
    if not _UPK.match(file):
        raise PackError(f"package file name {file!r} is not a plain .upk name")
    target = os.path.join(COOKED_DIR, file)
    local = os.path.join(pack_dir, "packages", file)
    want = meta.get("sha1")
    if os.path.isfile(target):
        if not want or _sha1(target) == want:
            return None
        if os.path.isfile(local) and _sha1(local) == want:
            shutil.copy2(local, target)
            return f"updated {file} in CookedPCConsole from the pack"
        raise PackError(f"WillowGame\\CookedPCConsole\\{file} is not the build this pack was made "
                        "with; extract the pack's zip into the Borderlands 2 folder again")
    if os.path.isfile(local):
        os.makedirs(COOKED_DIR, exist_ok=True)
        shutil.copy2(local, target)
        return (f"copied {file} into CookedPCConsole; if the weapon does not appear this "
                "session, restart the game once")
    raise PackError(f"{file} is missing from WillowGame\\CookedPCConsole; extract the pack's "
                    "zip into the Borderlands 2 folder (the one holding Binaries)")


def _read_pack(folder: str) -> dict[str, Any]:
    path = os.path.join(folder, PACK_FILE)
    try:
        with open(path, encoding="utf-8") as handle:
            pack = json.load(handle)
    except (OSError, ValueError) as ex:
        raise PackError(f"{PACK_FILE} is unreadable ({ex})") from ex
    if not isinstance(pack, dict) or pack.get("format") != PACK_FORMAT:
        raise PackError(f"{PACK_FILE} is not an Armory pack (format is not {PACK_FORMAT!r})")
    schema = int(pack.get("schema") or 0)
    if schema > PACK_SCHEMA:
        raise PackError(f"pack schema {schema} is newer than this Armory reads ({PACK_SCHEMA}); "
                        "update the Armory")
    if not _PACK_ID.match(str(pack.get("id", ""))):
        raise PackError(f"pack id {pack.get('id')!r} must be 1-40 letters, digits or _")
    if not _VERSION.match(str(pack.get("version", ""))):
        raise PackError(f"pack version {pack.get('version')!r} is not a version")
    need = str(pack.get("armory_min") or "0")
    if version_tuple(need) > version_tuple(ARMORY_VERSION):
        raise PackError(f"needs Armory {need} or newer; this is {ARMORY_VERSION}")
    if pack.get("installable") is False:
        raise PackError("this is a check build without its .upk files; it cannot be installed")
    weapons = pack.get("weapons") or []
    characters = pack.get("characters") or []
    if not isinstance(weapons, list) or not isinstance(characters, list):
        raise PackError("'weapons' and 'characters' must be lists")
    if not weapons and not characters:
        raise PackError("it lists no weapons and no characters")
    return pack


def _dev_characters_installed() -> bool:
    return os.path.isfile(os.path.join(SDK_MODS, DEV_CHARACTERS_MOD, "__init__.py"))


def _instantiate(pack: dict[str, Any], entry: dict[str, Any], resolved: Any,
                 pack_dir: str) -> types.ModuleType:
    """Render one weapon's component and run it as its own module.

    ``__file__`` points into the pack folder, so the component finds its sounds there and its
    per-save records under sdk_mods/_pipeline_saves/ (it walks up to sdk_mods). The rendered
    source is kept under logs/rendered/ so a traceback shows the real line.
    """
    weapon_id = entry["id"]
    name = f"{__name__}.packs.{pack['id']}.{weapon_id}"
    source = render_weapon(resolved, f"Armory pack {pack['id']} {pack['version']}",
                           str(pack.get("catalog") or ""))
    filename = f"<armory pack {pack['id']}: {weapon_id}>"
    try:
        rendered_dir = os.path.join(LOG_DIR, "rendered")
        os.makedirs(rendered_dir, exist_ok=True)
        filename = os.path.join(rendered_dir, f"{pack['id']}.{weapon_id}.py")
        with open(filename, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(source)
    except OSError:
        pass
    module = types.ModuleType(name)
    module.__file__ = os.path.join(pack_dir, f"{weapon_id}.py")
    sys.modules[name] = module
    try:
        exec(compile(source, filename, "exec", dont_inherit=True), module.__dict__)  # noqa: S102
    except BaseException:
        sys.modules.pop(name, None)
        raise
    module.DEFAULT_STATUS_FILE = os.path.join(LOG_DIR, f"{module.MOD_NAME}.status.json")
    return module


#: every loaded pack, and every folder that was refused (with why)
PACKS: list[dict[str, Any]] = []
PROBLEMS: list[dict[str, Any]] = []
#: one row per weapon: id, label, pack, module, balance(s), package, weapon type
WEAPONS: list[dict[str, Any]] = []
#: every character skin loaded from a pack (what the character runtime wears), with its pack
SKINS: list[dict[str, Any]] = []


def _load_packs() -> None:
    """Find, check and instantiate every pack. A bad pack is refused whole and reported;
    it never stops the others."""
    if not os.path.isdir(PACKS_DIR):
        _log(f"no packs folder ({PACKS_DIR}); install a pack to get weapons")
        return
    other_mods = _packages_of_other_mods()
    dev_characters = _dev_characters_installed()
    claimed: dict[str, str] = {}          # object path -> pack id
    skin_ids: dict[str, str] = {}         # skin id -> pack id
    weapon_ids: dict[str, str] = {}       # weapon id -> pack id
    pack_ids: dict[str, str] = {}         # pack id -> folder
    for folder_name in sorted(os.listdir(PACKS_DIR)):
        folder = os.path.join(PACKS_DIR, folder_name)
        if folder_name.startswith((".", "_")) or not os.path.isfile(os.path.join(folder, PACK_FILE)):
            continue
        pack: dict[str, Any] = {}
        try:
            pack = _read_pack(folder)
            pid = str(pack["id"])
            if pid in pack_ids:
                raise PackError(f"pack id {pid!r} is already loaded from ArmoryPacks\\"
                                f"{pack_ids[pid]}; keep one copy")
            loaded = []
            pack_claims: dict[str, str] = {}
            for entry in pack.get("weapons") or []:
                wid = str(entry.get("id", ""))
                if not _WEAPON_ID.match(wid):
                    raise PackError(f"weapon id {wid!r} must be 1-32 of a-z, 0-9, _")
                if wid in weapon_ids:
                    raise PackError(f"weapon id {wid!r} is already used by pack "
                                    f"{weapon_ids[wid]!r}")
                try:
                    resolved = load_weapon(entry)
                except PackError as ex:
                    raise PackError(f"weapon {wid}: {ex}") from ex
                for path, kind in claimed_paths(resolved).items():
                    if path in claimed:
                        raise PackError(f"weapon {wid}: {path} ({kind}) is already owned by pack "
                                        f"{claimed[path]!r}")
                    if kind in ("package", "extra package") and path in other_mods:
                        raise PackError(
                            f"weapon {wid}: package {path} is also registered by the mod "
                            f"sdk_mods\\{other_mods[path]}; move that mod out of sdk_mods "
                            "(a weapon registered twice breaks)")
                    pack_claims[path] = pid
                loaded.append((entry, resolved))
            skins = []
            pack_packages = {str(m.get("file", ""))[:-4] for m in pack.get("packages") or []
                             if str(m.get("file", "")).endswith(".upk")}
            for centry in pack.get("characters") or []:
                try:
                    skin = load_character(centry, pack_packages)
                except PackError as ex:
                    raise PackError(f"character: {ex}") from ex
                if skin["id"] in skin_ids:
                    raise PackError(f"character skin {skin['id']!r} is already used by pack "
                                    f"{skin_ids[skin['id']]!r}")
                for path, kind in character_claims(skin).items():
                    if path in claimed or path in pack_claims:
                        raise PackError(f"skin {skin['id']}: {path} ({kind}) is already owned by "
                                        f"pack {claimed.get(path, pid)!r}")
                    pack_claims[path] = pid
                skins.append(skin)
            skip_note = None
            if skins and (dev_characters or _chars is None):
                skip_note = (f"character skins left to sdk_mods\\{DEV_CHARACTERS_MOD} (the pipeline's "
                             "dev mod); move it out of sdk_mods to let the Armory wear them"
                             if dev_characters else
                             f"this Armory's character runtime did not load ({_CHARS_IMPORT_ERROR})")
                if not loaded:
                    raise PackError(skip_note)
                skins = []
            notes = [n for n in (_ensure_package(folder, meta)
                                 for meta in pack.get("packages") or []) if n]
            if skip_note:
                notes.append(skip_note)
            modules = []
            try:
                for entry, resolved in loaded:
                    modules.append((entry, resolved, _instantiate(pack, entry, resolved, folder)))
            except Exception as ex:
                for entry, _resolved, _module in modules:
                    sys.modules.pop(f"{__name__}.packs.{pid}.{entry['id']}", None)
                raise PackError(f"its code did not load ({ex!r}); see logs\\rendered") from ex
        except PackError as ex:
            PROBLEMS.append({"folder": folder_name, "pack": pack.get("id"),
                             "name": pack.get("name") or folder_name, "error": str(ex)})
            _warn(f"pack {folder_name} not loaded: {ex}")
            continue
        claimed.update(pack_claims)
        pack_ids[pid] = folder_name
        labels = {w["label"] for w in WEAPONS}
        for entry, resolved, module in modules:
            weapon_ids[entry["id"]] = pid
            label = str(entry.get("label") or entry["id"])
            if label in labels:
                label = f"{label} ({pack.get('name') or pid})"
            WEAPONS.append({
                "id": entry["id"], "label": label, "pack": pid, "module": module,
                "balance": resolved.balances[0].path,
                "balances": [b.path for b in resolved.balances],
                "package": resolved.spec.package_stem,
                "weapon_type": resolved.spec.weapon_type,
            })
        for skin in skins:
            skin_ids[skin["id"]] = pid
            SKINS.append(dict(skin, pack=pid))
        PACKS.append({"id": pid, "folder": folder_name, "name": pack.get("name") or pid,
                      "version": pack.get("version"), "author": pack.get("author"),
                      "license": pack.get("license"), "url": pack.get("url"),
                      "weapons": [e["id"] for e, _r, _m in modules],
                      "characters": [s["id"] for s in skins], "notes": notes})
        for note in notes:
            _log(f"pack {pid}: {note}")


_load_packs()
BY_ID = {w["id"]: w for w in WEAPONS}
BY_LABEL = {w["label"]: w for w in WEAPONS}
OUR_BALANCES = frozenset(b for w in WEAPONS for b in w["balances"])
COMPONENTS = tuple(w["module"].COMPONENT for w in WEAPONS)
#: the character runtime's hooks, options and command (None without skins)
CHARACTERS = (_chars.configure(SKINS, log_name=MOD_NAME, test_keys=False,
                               status_path=str(_control().get("characters_status_path")
                                               or CHARACTER_STATUS_FILE))
              if SKINS and _chars is not None else None)


def registration() -> dict[str, Any]:
    """Each weapon's last menu_setup record (None until the main menu has registered it)."""
    out: dict[str, Any] = {}
    for weapon in WEAPONS:
        records = [r for r in weapon["module"]._status if r.get("phase") == "menu_setup"]
        out[weapon["id"]] = records[-1] if records else None
    return out


def pack_report() -> dict[str, Any]:
    return {"armory": ARMORY_VERSION, "packs_dir": PACKS_DIR, "packs": PACKS,
            "problems": PROBLEMS, "weapons": [w["id"] for w in WEAPONS],
            "characters": [s["id"] for s in SKINS]}


# --------------------------------------------------------------------- census
def _weapon_id(balance_path: str | None) -> str | None:
    if not balance_path:
        return None
    return next((w["id"] for w in WEAPONS if balance_path in w["balances"]), None)


def _describe(weapon: UObject) -> dict[str, Any]:
    definition = weapon.DefinitionData
    balance = definition.BalanceDefinition
    balance_path = balance._path_name() if balance is not None else None
    entry: dict[str, Any] = {
        "weapon": weapon._path_name(),
        "id": _weapon_id(balance_path),
        "balance": balance_path,
        "slot": int(weapon.QuickSelectSlot),
        "unique_id": int(definition.UniqueId),
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


def _our_weapons() -> list[tuple[int, UObject]]:
    """Every Armory weapon the pawn owns, oldest first."""
    controller = get_pc()
    found = []
    for weapon in unrealsdk.find_all("WillowWeapon", exact=False):
        try:
            balance = weapon.DefinitionData.BalanceDefinition
            if weapon.Owner != controller.Pawn or balance is None:
                continue
            if balance._path_name() not in OUR_BALANCES:
                continue
        except Exception:  # noqa: BLE001 - half-destroyed actors throw on attribute access
            continue
        tail = str(weapon.Name).rsplit("_", 1)[-1]
        found.append((int(tail) if tail.isdigit() else 0, weapon))
    return sorted(found, key=lambda pair: pair[0])


def census() -> list[dict[str, Any]]:
    return [_describe(weapon) for _, weapon in _our_weapons()]


# --------------------------------------------------------------------- spawn (F26)
_SHARED_REGISTRY = "_bl2_pipeline_shared"
_restore_left = 0
_equip_due = 0.0
_equip_id: str | None = None


def _reward_backups() -> dict[str, dict[str, Any]]:
    """One reward-table registry for EVERY pipeline mod in this interpreter (F26)."""
    module = sys.modules.get(_SHARED_REGISTRY)
    if module is None:
        module = types.ModuleType(_SHARED_REGISTRY)
        module.reward_backups = {}
        sys.modules[_SHARED_REGISTRY] = module
    return module.reward_backups


def spawn(weapon_id: str, level: int | None = None, equip: bool = True) -> dict[str, Any]:
    """Hand the player one weapon of ``weapon_id``'s balance, at ``level`` (default: theirs)."""
    global _restore_left, _equip_due, _equip_id
    weapon = BY_ID.get(weapon_id)
    if weapon is None:
        return _write({"spawn": weapon_id, "error": f"unknown weapon; known: {sorted(BY_ID)}"})
    record: dict[str, Any] = {"spawn": weapon_id, "balance": weapon["balance"]}
    try:
        controller = get_pc()
        balance = unrealsdk.find_object("WeaponBalanceDefinition", weapon["balance"])
        mission = unrealsdk.find_object("MissionDefinition", SPAWN_MISSION)
        backups = _reward_backups()
        if SPAWN_MISSION not in backups:
            # the first hijack of this mission by ANY pipeline mod sees the pristine table
            backups[SPAWN_MISSION] = {
                "mission": mission,
                "game_stage": mission.GameStage,
                "reward_items": list(mission.Reward.RewardItems),
                "reward_pools": list(mission.Reward.RewardItemPools),
            }
        stage = int(level) if level else int(controller.PlayerReplicationInfo.ExpLevel)
        mission.GameStage = stage
        mission.Reward.RewardItems = [balance]
        mission.Reward.RewardItemPools = []
        before = {w._path_name() for _, w in _our_weapons()}
        controller.ServerGrantMissionRewards(mission, False)
        record["level"] = stage
        record["granted"] = [
            _describe(w) for _, w in _our_weapons() if w._path_name() not in before
        ]
        if equip:
            _equip_id, _equip_due = weapon_id, time.time() + EQUIP_DELAY
        _restore_left = RESTORE_TICKS
        restore_tick.enable()
        _hud(f"{weapon['label']} spawned at level {stage}")
    except Exception as ex:  # noqa: BLE001
        record["error"] = repr(ex)
        _log(f"spawn {weapon_id} failed: {ex!r}")
    return _write(record)


def _restore_reward() -> None:
    backup = _reward_backups().pop(SPAWN_MISSION, None)
    if backup is None:
        return  # nothing hijacked, or another pipeline mod already put the table back
    mission = backup["mission"]
    mission.GameStage = backup["game_stage"]
    mission.Reward.RewardItems = backup["reward_items"]
    mission.Reward.RewardItemPools = backup["reward_pools"]


def _equip_newest(weapon_id: str) -> str:
    controller = get_pc()
    manager = controller.GetPawnInventoryManager()
    ours = [w for _, w in _our_weapons()
            if _weapon_id(w.DefinitionData.BalanceDefinition._path_name()) == weapon_id]
    if not ours:
        return f"no {weapon_id} on the pawn yet"
    weapon = ours[-1]
    slot = int(weapon.QuickSelectSlot)
    if slot > 0:
        manager.EquipWeaponFromSlot(slot)
        return f"equipped slot {slot}"
    held = controller.Pawn.Weapon
    slot = int(held.QuickSelectSlot) if held is not None else 1
    manager.ReadyBackpackInventory(weapon, slot)
    manager.EquipWeaponFromSlot(slot)
    return f"readied and equipped slot {slot}"


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def restore_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    """Equip the spawned weapon once it exists, and put the mission's reward table back."""
    global _restore_left, _equip_id
    if _equip_id is not None and time.time() >= _equip_due:
        weapon_id, _equip_id = _equip_id, None
        try:
            _write({"equip": weapon_id, "result": _equip_newest(weapon_id)})
        except Exception as ex:  # noqa: BLE001
            _write({"equip": weapon_id, "error": repr(ex)})
    _restore_left -= 1
    if _restore_left <= 0:
        _restore_reward()
        if _equip_id is None:
            restore_tick.disable()


# --------------------------------------------------------------------- auto sequence
#: seconds after a map load before the first auto spawn, between spawns, and before the
#: closing census (wall clock: tick counts scale with the frame rate, F25)
AUTO_DELAYS = (2.0, 2.5, 2.0)
_auto_queue: list[str] = []
_auto_deadline = 0.0
_auto_phase = ""


@hook("WillowGame.WillowPlayerController:WillowClientDisableLoadingMovie", Type.POST)
def on_map_loaded(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    """Fires once per gameplay map load: census, then the control file's auto_spawn list."""
    global _auto_queue, _auto_deadline, _auto_phase
    try:
        _write({"census_on_load": census(), "registration": {
            k: (v.get("ok") if isinstance(v, dict) else None) for k, v in registration().items()}})
    except Exception as ex:  # noqa: BLE001
        _write({"census_on_load": None, "error": repr(ex)})
    wanted = _control().get("auto_spawn")
    if not isinstance(wanted, list) or not wanted:
        return
    _auto_queue = [str(w) for w in wanted]
    _auto_deadline = time.time() + AUTO_DELAYS[0]
    _auto_phase = "spawn"
    seq_tick.enable()
    _write({"auto_spawn": list(_auto_queue), "armed": True})


@hook("WillowGame.WillowGameViewportClient:Tick", Type.PRE)
def seq_tick(obj: UObject, args: WrappedStruct, ret: Any, func: BoundFunction) -> None:
    global _auto_queue, _auto_deadline, _auto_phase
    if not _auto_phase or time.time() < _auto_deadline:
        return
    if _auto_phase == "spawn" and _auto_queue:
        spawn(_auto_queue.pop(0))
        _auto_deadline = time.time() + AUTO_DELAYS[1]
        if not _auto_queue:
            _auto_phase = "census"
            _auto_deadline = time.time() + AUTO_DELAYS[2]
        return
    _auto_phase = ""
    seq_tick.disable()
    _write({"census": census(), "done": True})


# --------------------------------------------------------------------- console
def _print_packs() -> None:
    _log(f"Armory {ARMORY_VERSION}: {len(PACKS)} pack(s), {len(WEAPONS)} weapon(s), "
         f"{len(SKINS)} character skin(s) from {PACKS_DIR}")
    for pack in PACKS:
        content = ", ".join([*pack["weapons"], *(f"skin {c}" for c in pack["characters"])])
        _log(f"  {pack['id']:<16} {pack['name']} {pack['version']} by {pack['author']} "
             f"({pack['license']}): {content}")
        for note in pack["notes"]:
            _log(f"      note: {note}")
    for problem in PROBLEMS:
        _log(f"  NOT LOADED  ArmoryPacks\\{problem['folder']}: {problem['error']}")


@command("armory", description="Armory: list, spawn or census the custom weapons; list packs")
def armory_cmd(args: Any) -> None:
    if args.action == "list":
        if not WEAPONS:
            _log("no weapons loaded; 'armory packs' says why")
        for weapon in WEAPONS:
            _log(f"{weapon['id']:<14} {weapon['label']:<24} pack {weapon['pack']}")
    elif args.action == "packs":
        _print_packs()
    elif args.action == "census":
        rows = census()
        _write({"census": rows})
        for row in rows:
            _log(json.dumps(row, default=str))
        _log(f"{len(rows)} Armory weapon(s) on the pawn")
    elif args.action == "spawn":
        if not args.weapon:
            _log("usage: armory spawn <id> [--level N] [--no-equip]; ids: " + ", ".join(BY_ID))
            return
        spawn(args.weapon, args.level, not args.no_equip)


armory_cmd.add_argument("action", choices=("list", "spawn", "census", "packs"))
armory_cmd.add_argument("weapon", nargs="?", default=None, help="a weapon id ('armory list')")
armory_cmd.add_argument("--level", type=int, default=None, help="item level (default: yours)")
armory_cmd.add_argument("--no-equip", action="store_true", help="leave it in the backpack")


# --------------------------------------------------------------------- menu + keybind
_NO_WEAPONS = "(no weapons installed)"
weapon_option = DropdownOption(
    "Weapon", WEAPONS[0]["label"] if WEAPONS else _NO_WEAPONS,
    [w["label"] for w in WEAPONS] or [_NO_WEAPONS],
    description="Which weapon 'Spawn selected' and the spawn key hand out.",
)


def _selected_id() -> str | None:
    weapon = BY_LABEL.get(str(weapon_option.value)) or (WEAPONS[0] if WEAPONS else None)
    return weapon["id"] if weapon else None


def _spawn_selected() -> None:
    weapon_id = _selected_id()
    if weapon_id is None:
        _hud("no weapons installed; see Mods > Armory > Packs")
        return
    spawn(weapon_id)


spawn_button = ButtonOption(
    "Spawn selected",
    description="Spawn the selected weapon at your level and equip it.",
    on_press=lambda _option: _spawn_selected(),
)


def _pack_button(pack: dict[str, Any]) -> ButtonOption:
    weapons = ", ".join(BY_ID[w]["label"] for w in pack["weapons"])
    skins = ", ".join(f"{s['label']} ({s['character']})" for s in SKINS if s["pack"] == pack["id"])
    text = (f"{pack['name']} {pack['version']} by {pack['author']}\nLicence: {pack['license']}"
            + (f"\nWeapons: {weapons}" if weapons else "")
            + (f"\nCharacters: {skins}" if skins else "")
            + (f"\n{pack['url']}" if pack.get("url") else ""))
    return ButtonOption(f"{pack['name']} {pack['version']}", description=text,
                        on_press=lambda _option: _print_packs())


def _problem_button(problem: dict[str, Any]) -> ButtonOption:
    return ButtonOption(f"NOT LOADED: {problem['name']}",
                        description=f"ArmoryPacks\\{problem['folder']}: {problem['error']}",
                        on_press=lambda _option: _print_packs())


packs_menu = NestedOption(
    "Packs",
    [*(_pack_button(p) for p in PACKS), *(_problem_button(p) for p in PROBLEMS)]
    or [ButtonOption("No packs found", description=f"Put packs in {PACKS_DIR}",
                     on_press=lambda _option: _print_packs())],
    description="The packs in sdk_mods\\ArmoryPacks, and why any did not load.",
)
armory_menu = NestedOption(
    "Armory", [weapon_option, spawn_button, *(CHARACTERS["options"] if CHARACTERS else []),
               packs_menu],
    description="Spawn custom weapons and pick character skins "
                "(also: the 'armory' and 'characters' console commands).",
)


@keybind("Armory: spawn selected weapon", SPAWN_KEY)
def kb_spawn() -> None:
    _spawn_selected()


# --------------------------------------------------------------------- wiring
mod = build_mod(
    name=MOD_NAME,
    description=(f"Loads custom weapons and character skins from packs in sdk_mods\\ArmoryPacks. "
                 f"{len(WEAPONS)} weapon(s) and {len(SKINS)} skin(s) from {len(PACKS)} pack(s) loaded"
                 + (f"; {len(PROBLEMS)} pack(s) refused, see Packs." if PROBLEMS else ".")),
    hooks=[],
    keybinds=[kb_spawn],
    options=[armory_menu],
    commands=[armory_cmd, *(CHARACTERS["commands"] if CHARACTERS else [])],
)

# The weapons' own hooks (registration, save round-trip, validation, names, effects) are
# enabled here and NOT given to build_mod: they must stay on while the Armory is installed,
# whatever the mod menu says, or a save written in between loses every custom part (F18).
# restore_tick / seq_tick switch themselves on and off.
for _component in COMPONENTS:
    for _hook in _component["hooks"]:
        _hook.enable()
on_map_loaded.enable()
armory_cmd.enable()
# character skins apply whatever the mod menu says, like the dev PipelineCharacters mod
if CHARACTERS:
    for _hook in CHARACTERS["hooks"]:
        _hook.enable()
    for _cmd in CHARACTERS["commands"]:
        _cmd.enable()
_write({"packs": pack_report()})
_log(f"{ARMORY_VERSION} armed: {len(WEAPONS)} weapon(s), {len(SKINS)} character skin(s) "
     f"from {len(PACKS)} pack(s)"
     + (f", {len(PROBLEMS)} refused ('armory packs' says why)" if PROBLEMS else "")
     + "; registration runs at the main menu")
