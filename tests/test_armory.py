"""Tests for the Armory (M10): several weapon specs composed into one SDK mod with a spawn
console, driven offline against the fakes.

* two weapons on two host gestalts (the AR own-balance spec and a Jakobs sniper spec) emit
  into one folder: ``__init__.py`` + ``weapons/<id>.py``, no ``build_mod`` in a component,
  every component hook wired into the one mod and enabled at import (F8)
* both components register at the menu tick: each gestalt table grows by its own fragment
  count, each pool gains exactly one entry
* ``armory spawn <id>`` (console), the menu button and the keybind hand out a weapon whose
  every part is that weapon's; the mission's reward table is restored through the shared
  registry (F26) whichever order the restores land in
* the census sees both; the save round-trip records land in the shared per-package folder
* L11 refuses two weapons that touch the same object (two weapons on one gestalt)
* install copies the folder once and every weapon's package

Run with ``python -m pytest tests/test_armory.py -q``.
"""

from __future__ import annotations

import ast
import importlib.util
import itertools
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests" / "fakes"))

import mods_base  # noqa: E402  (the fake)
import unrealsdk  # noqa: E402  (the fake)

from bl2_catalog import load_catalog  # noqa: E402
from bl2_partgen import (  # noqa: E402
    ArmoryConflict,
    SpecError,
    emit_armory,
    install_armory,
    load_armory,
)
from bl2_partgen.spec import ArmorySpec  # noqa: E402

from tests.fakes.graph import (  # noqa: E402
    LEGENDARY_POOL,
    SNIPER_BALANCE,
    SNIPER_POOL,
    SNIPER_TEMPLATE_PART,
    SNIPER_TITLE,
    build_graph,
)
from tests.test_partgen import SPEC_PATH, _own_balance_spec  # noqa: E402

KEEP_ALIVE_FLAG = 0x4000
AK_BALANCE = "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_PL_Multi"
SNIPER_NEW_BALANCE = "GD_Weap_SniperRifles.A_Weapons_Legendary.Sniper_Jakobs_5_PL_Test"
_counter = itertools.count()


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def catalog() -> dict[str, Any]:
    path = REPO / "catalog" / "parts.json"
    if not path.exists():
        pytest.skip("catalog/parts.json absent; run `python -m bl2_catalog`")
    return load_catalog(path)


@pytest.fixture()
def graph(catalog: dict[str, Any]) -> Any:
    return build_graph(catalog)


def _ar_spec() -> dict[str, Any]:
    if not SPEC_PATH.exists():
        pytest.skip("specs/bent_barrel.json absent")
    spec = _own_balance_spec(json.loads(SPEC_PATH.read_text(encoding="utf-8")))
    spec["mod"]["name"] = "PipelineTestAK"
    spec["options"].update(test_harness=False, harness_auto=False)
    return spec


def _sniper_spec(catalog: dict[str, Any]) -> dict[str, Any]:
    """A one-fragment weapon on the Jakobs sniper: its own balance, title, pool."""
    # past the end of the stock index buffer, like a real appended fragment (L4 warns, not errors)
    first_index = int(catalog["weapon_types"]["SniperRifle"].get("index_count", 64866))
    return {
        "mod": {"name": "PipelineTestSniper", "author": "tests", "version": "0.0.1",
                "description": "A test sniper."},
        "package": "PipelineMeshesAWP",
        "mesh_path": "PipelineMeshesAWP.PL_SR_Gestalt_Mesh",
        "package_file": "../scratch/PipelineMeshesAWP.upk",
        "weapon_type": "SniperRifle",
        "fragments": [{
            "name": "SR_Barrel_PL_Test", "template_fragment": "SR_Barrel_Jakobs",
            "first_index": first_index, "num_primitives": 50,
            "dz": 0.0, "sockets": [], "dz_sockets": [],
        }],
        "parts": [{
            "part_name": "SR_Barrel_PL_Test", "slot": "WP_Barrel",
            "outer": "GD_Weap_SniperRifles.Barrel", "fragment": "SR_Barrel_PL_Test",
            "template_part": SNIPER_TEMPLATE_PART, "register_in": [],
        }],
        "balances": [{
            "name": "Sniper_Jakobs_5_PL_Test",
            "outer": "GD_Weap_SniperRifles.A_Weapons_Legendary",
            "template_balance": SNIPER_BALANCE,
            "part_lists": {"BarrelPartData": ["SR_Barrel_PL_Test"]},
            "title": {
                "name": "Title_Legendary_PL_Test",
                "outer": "GD_Weap_SniperRifles.Name.Title_Jakobs",
                "template": SNIPER_TITLE, "part_name": "Test Sniper",
                "red_text": "One shot.", "on_parts": ["SR_Barrel_PL_Test"],
            },
            "pools": [SNIPER_POOL],
        }],
        "options": {"register_at": "menu", "keep_alive": True, "save_roundtrip": True,
                    "validate_override": True, "test_harness": False},
    }


def _write_armory(tmp_path: Path, catalog: dict[str, Any], **spawn: Any) -> Path:
    specs = tmp_path / "specs"
    specs.mkdir(exist_ok=True)
    (specs / "ak.json").write_text(json.dumps(_ar_spec()), encoding="utf-8")
    (specs / "sniper.json").write_text(json.dumps(_sniper_spec(catalog)), encoding="utf-8")
    armory = {
        "mod": {"name": "PipelineTestArmory", "author": "tests", "version": "0.0.1",
                "description": "Two test weapons."},
        "weapons": [
            {"id": "ak", "spec": "ak.json", "label": "Test AK"},
            {"id": "sniper", "spec": "sniper.json", "label": "Test Sniper"},
        ],
        "spawn": {"keybind": "F5", **spawn},
    }
    path = specs / "armory.json"
    path.write_text(json.dumps(armory), encoding="utf-8")
    return path


@pytest.fixture()
def armory_path(tmp_path: Path, catalog: dict[str, Any]) -> Path:
    return _write_armory(tmp_path, catalog)


@pytest.fixture()
def emitted(tmp_path: Path, catalog: dict[str, Any], armory_path: Path) -> Any:
    return emit_armory(armory_path, tmp_path / "armory", catalog=catalog)


def load_armory_mod(out_dir: Path, graph: Any) -> ModuleType:
    """Import a generated Armory as a package (so ``from .weapons import ...`` resolves)."""
    unrealsdk.set_world(graph.world)
    mods_base.set_pc(graph.controller)
    mods_base.built_mods.clear()
    sys.modules.pop("_bl2_pipeline_shared", None)
    name = f"emitted_armory_{next(_counter)}"
    spec = importlib.util.spec_from_file_location(
        name, out_dir / "__init__.py", submodule_search_locations=[str(out_dir)]
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _sniper_gestalt(graph: Any) -> Any:
    return graph.world.find("GestaltSkeletalMeshDefinition",
                            "Weap_SniperRifles.GestaltDef_SniperRifle")


# ------------------------------------------------------------------ emitting
def test_armory_emits_one_mod_with_a_component_per_weapon(emitted: Any) -> None:
    files = {p.relative_to(emitted.out_dir).as_posix() for p in emitted.files}
    assert {"__init__.py", "weapons/__init__.py", "weapons/ak.py", "weapons/sniper.py",
            "pyproject.toml", "settings/PipelineTestArmory.json", "README.md", "spec.json",
            "lint.json"} <= files
    assert emitted.lint_ok and not emitted.forced, emitted.lint_errors
    for rel in ("__init__.py", "weapons/ak.py", "weapons/sniper.py"):
        source = (emitted.out_dir / rel).read_text(encoding="utf-8")
        ast.parse(source)
        assert "$" not in source, f"unsubstituted template marker in {rel}"
    for rel in ("weapons/ak.py", "weapons/sniper.py"):
        source = (emitted.out_dir / rel).read_text(encoding="utf-8")
        assert "build_mod(" not in source and "COMPONENT: dict" in source
        assert ".enable()" not in source, "the Armory enables component hooks, not the component"
        assert 'MOD_NAME + ".status.json"' in source, "components must not share status.json"
    armory_source = emitted.mod_file.read_text(encoding="utf-8")
    assert "from .weapons import ak, sniper" in armory_source
    assert '@command("armory"' in armory_source and "DropdownOption(" in armory_source
    lint = json.loads((emitted.out_dir / "lint.json").read_text(encoding="utf-8"))
    assert set(lint["weapons"]) == {"ak", "sniper"} and lint["ok"]
    spec_json = json.loads((emitted.out_dir / "spec.json").read_text(encoding="utf-8"))
    assert [w["id"] for w in spec_json["armory"]["weapons"]] == ["ak", "sniper"]


def test_armory_spec_validation(tmp_path: Path, catalog: dict[str, Any]) -> None:
    path = _write_armory(tmp_path, catalog)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["weapons"][1]["id"] = "ak"
    with pytest.raises(SpecError, match="unique"):
        ArmorySpec.from_dict(data, source_dir=path.parent)
    data["weapons"][1]["id"] = "not-an-identifier"
    with pytest.raises(SpecError, match="identifier"):
        ArmorySpec.from_dict(data, source_dir=path.parent)
    data["weapons"][1]["id"] = "sniper"
    data["weapons"][1]["spec"] = "missing.json"
    with pytest.raises(SpecError, match="not found"):
        ArmorySpec.from_dict(data, source_dir=path.parent)
    data["weapons"][1]["spec"] = "sniper.json"
    data["spawn"]["mission"] = "nope"
    with pytest.raises(SpecError, match="mission"):
        ArmorySpec.from_dict(data, source_dir=path.parent)
    data["spawn"]["mission"] = "GD_Episode01.M_Ep1_Champion"
    armory = ArmorySpec.from_dict(data, source_dir=path.parent)
    assert armory.to_dict()["spawn"]["keybind"] == "F5"
    assert load_armory(path).name == "PipelineTestArmory"


def test_l11_refuses_two_weapons_on_one_gestalt(tmp_path: Path, catalog: dict[str, Any]) -> None:
    specs = tmp_path / "specs"
    specs.mkdir()
    first = _ar_spec()
    second = json.loads(json.dumps(first))
    second["mod"]["name"] = "PipelineTestAK2"
    second["package"] = "PipelineMeshesTwo"
    second["mesh_path"] = "PipelineMeshesTwo.PL_AR_Gestalt_Mesh"
    for frag in second["fragments"]:
        frag["name"] += "2"
    for part in second["parts"]:
        part["part_name"] += "2"
        part["fragment"] = part["fragment"] + "2" if part.get("fragment") else None
    bal = second["balances"][0]
    bal["name"] += "2"
    bal["part_lists"] = {k: [p + "2" for p in v] for k, v in bal["part_lists"].items()}
    bal["title"]["name"] += "2"
    bal["title"]["on_parts"] = [p + "2" for p in bal["title"]["on_parts"]]
    (specs / "a.json").write_text(json.dumps(first), encoding="utf-8")
    (specs / "b.json").write_text(json.dumps(second), encoding="utf-8")
    (specs / "armory.json").write_text(json.dumps({
        "mod": {"name": "PipelineTwoARs"},
        "weapons": [{"id": "a", "spec": "a.json"}, {"id": "b", "spec": "b.json"}],
    }), encoding="utf-8")
    with pytest.raises(ArmoryConflict, match="L11.*GestaltDef_AssaultRifle"):
        emit_armory(specs / "armory.json", tmp_path / "out", catalog=catalog)
    assert not (tmp_path / "out").exists(), "nothing may be written on a conflict"


# ------------------------------------------------------------------ the generated mod
def test_armory_wires_both_components_into_one_mod(emitted: Any, graph: Any) -> None:
    module = load_armory_mod(emitted.out_dir, graph)
    assert [w["id"] for w in module.WEAPONS] == ["ak", "sniper"]
    assert module.WEAPONS[0]["balance"] == AK_BALANCE
    assert module.WEAPONS[1]["balance"] == SNIPER_NEW_BALANCE
    mod = mods_base.built_mods[-1]
    assert len(mods_base.built_mods) == 1, "components must not build mods of their own"
    assert mod.name == "PipelineTestArmory"
    component_hooks = [h for c in module.COMPONENTS for h in c["hooks"]]
    assert component_hooks and all(h.enabled for h in component_hooks), "F8"
    assert set(mod.hooks) >= set(component_hooks) and module.restore_tick in mod.hooks
    assert [kb.key for kb in mod.keybinds] == ["F5"]
    assert [c.cmd for c in mod.commands] == ["armory"] and mod.commands[0].enabled
    assert mod.find_option("Weapon").choices == ["Test AK", "Test Sniper"]
    assert mod.find_option("Spawn selected").on_press is not None
    # the two components have distinct hook objects even where the function names match
    names = [(h.__name__, id(h)) for h in component_hooks]
    assert len({i for _, i in names}) == len(names)
    assert sum(1 for n, _ in names if n == "menu_tick") == 2


def test_both_components_register_at_the_menu(emitted: Any, graph: Any) -> None:
    module = load_armory_mod(emitted.out_dir, graph)
    ar_before = len(graph.fragment_names)
    sniper = _sniper_gestalt(graph)
    sniper_before = len(sniper.GestaltInfos[0].Parts)
    pool_before = len(graph.pool.BalancedItems)
    sniper_pool = graph.world.find("ItemPoolDefinition", SNIPER_POOL)
    sniper_pool_before = len(sniper_pool.BalancedItems)

    assert module.registration() == {"ak": None, "sniper": None}
    records = {w["id"]: w["module"].menu_setup() for w in module.WEAPONS}
    assert all(r["ok"] for r in records.values()), records
    assert module.registration()["ak"]["ok"] and module.registration()["sniper"]["ok"]

    ak_fragments = len(emitted.weapons[0].resolved.fragments)
    assert len(graph.fragment_names) == ar_before + ak_fragments
    assert len(sniper.GestaltInfos[0].Parts) == sniper_before + 1
    assert str(sniper.GestaltInfos[0].Parts[-1].SkeletalMeshFragmentName) == "SR_Barrel_PL_Test"
    assert len(graph.pool.BalancedItems) == pool_before + 1
    assert len(sniper_pool.BalancedItems) == sniper_pool_before + 1
    assert "PipelineMeshes" in graph.world.loaded_packages
    assert "PipelineMeshesAWP" in graph.world.loaded_packages
    assert sniper.GestaltSkeletalMesh._path_name() == "PipelineMeshesAWP.PL_SR_Gestalt_Mesh"
    for path in (AK_BALANCE, SNIPER_NEW_BALANCE):
        obj = unrealsdk.find_object("WeaponBalanceDefinition", path)
        assert obj.ObjectFlags & KEEP_ALIVE_FLAG, f"{path} not rooted (F17)"


def _spawned(module: Any, weapon_id: str) -> Any:
    rows = [r for r in module._status if r.get("spawn") == weapon_id]
    assert rows and "error" not in rows[-1], rows
    return rows[-1]


def test_console_spawns_each_weapon_and_restores_the_mission(emitted: Any, graph: Any) -> None:
    module = load_armory_mod(emitted.out_dir, graph)
    for w in module.WEAPONS:
        assert w["module"].menu_setup()["ok"]
    mission = graph.mission
    assert list(mission.Reward.RewardItems) == [] and mission.GameStage == 1

    module.armory_cmd.run("spawn ak --level 40")
    record = _spawned(module, "ak")
    assert record["level"] == 40 and len(record["granted"]) == 1
    granted = record["granted"][0]
    assert granted["id"] == "ak" and granted["balance"] == AK_BALANCE
    assert granted["BarrelPartDefinition"] == "GD_Weap_AssaultRifle.Barrel.AR_Barrel_PL_Bent"
    assert granted["BodyPartDefinition"].endswith("AR_Body_PL_Multi")
    assert granted["TitlePartDefinition"].endswith("Title_Legendary_PL_Multi")
    # the mission is hijacked right now, with the pristine table in the shared registry
    assert [b._path_name() for b in mission.Reward.RewardItems] == [AK_BALANCE]
    shared = sys.modules["_bl2_pipeline_shared"].reward_backups
    assert list(shared[module.SPAWN_MISSION]["reward_items"]) == []

    module.armory_cmd.run("spawn sniper")
    record = _spawned(module, "sniper")
    assert record["level"] == 30, "default level is the player's"
    granted = record["granted"][0]
    assert granted["balance"] == SNIPER_NEW_BALANCE
    assert granted["BarrelPartDefinition"] == "GD_Weap_SniperRifles.Barrel.SR_Barrel_PL_Test"
    assert granted["TitlePartDefinition"].endswith("Title_Legendary_PL_Test")
    assert list(shared[module.SPAWN_MISSION]["reward_items"]) == [], (
        "the second spawn must not snapshot the first spawn's hijack"
    )

    # the deferred equip lands on the next tick once its delay has passed
    module._equip_due = 0.0
    module.restore_tick(None, None, None, None)
    equips = [r for r in module._status if "equip" in r]
    assert equips and equips[-1]["equip"] == "sniper" and "error" not in equips[-1]
    assert graph.pawn.Weapon.DefinitionData.BalanceDefinition._path_name() == SNIPER_NEW_BALANCE

    # RESTORE_TICKS later the pristine table is back, and a second restore is a no-op
    for _ in range(module.RESTORE_TICKS + 1):
        module.restore_tick(None, None, None, None)
    assert not module.restore_tick.enabled
    assert list(mission.Reward.RewardItems) == [] and mission.GameStage == 1
    assert module.SPAWN_MISSION not in shared
    module._restore_reward()
    assert list(mission.Reward.RewardItems) == []

    rows = module.census()
    assert [r["id"] for r in rows] == ["ak", "sniper"]
    module.armory_cmd.run("census")
    assert module._status[-1]["census"] == rows
    module.armory_cmd.run("spawn bogus")
    assert "unknown weapon" in module._status[-1]["error"]
    module.armory_cmd.run("list")  # must not raise


def test_menu_button_and_keybind_spawn_the_selected_weapon(emitted: Any, graph: Any) -> None:
    module = load_armory_mod(emitted.out_dir, graph)
    for w in module.WEAPONS:
        assert w["module"].menu_setup()["ok"]
    mod = mods_base.built_mods[-1]
    option = mod.find_option("Weapon")
    button = mod.find_option("Spawn selected")
    assert option.value == "Test AK"
    button.press()
    assert _spawned(module, "ak")["granted"][0]["balance"] == AK_BALANCE
    option.value = "Test Sniper"
    module.kb_spawn()
    assert _spawned(module, "sniper")["granted"][0]["balance"] == SNIPER_NEW_BALANCE
    assert len(graph.granted) == 2


def test_save_records_land_in_the_shared_per_package_folder(emitted: Any, graph: Any) -> None:
    module = load_armory_mod(emitted.out_dir, graph)
    for w in module.WEAPONS:
        assert w["module"].menu_setup()["ok"]
    module.spawn("ak", equip=False)
    module.spawn("sniper", equip=False)
    for w in module.WEAPONS:
        component = w["module"]
        component.on_generate_save(graph.controller, None, None, None)
    weapons_dir = emitted.out_dir / "weapons"
    ak_record = weapons_dir / "_pipeline_saves" / "PipelineMeshes" / "Save0001.sav.json"
    sniper_record = weapons_dir / "_pipeline_saves" / "PipelineMeshesAWP" / "Save0001.sav.json"
    assert ak_record.exists() and sniper_record.exists()
    ak = json.loads(ak_record.read_text(encoding="utf-8"))
    sniper = json.loads(sniper_record.read_text(encoding="utf-8"))
    assert any("AR_Barrel_PL_Bent" in v for rec in ak.values() for v in rec.values())
    assert any("SR_Barrel_PL_Test" in v for rec in sniper.values() for v in rec.values())
    assert not any("SR_Barrel" in v for rec in ak.values() for v in rec.values()), (
        "each component records only its own parts"
    )
    # each component keeps its own status file beside the others
    assert (weapons_dir / "PipelineTestAK.status.json").exists()
    assert (weapons_dir / "PipelineTestSniper.status.json").exists()


# ------------------------------------------------------------------ install
def test_install_armory_copies_the_folder_and_every_package(
    tmp_path: Path, catalog: dict[str, Any]
) -> None:
    armory_path = _write_armory(tmp_path, catalog)
    for name in ("ak.json", "sniper.json"):
        path = armory_path.parent / name
        data = json.loads(path.read_text(encoding="utf-8"))
        upk = tmp_path / f"{data['package']}.upk"
        upk.write_bytes(b"\xc1\x83\x2a\x9e" + bytes(60))
        data["package_file"] = str(upk)
        path.write_text(json.dumps(data), encoding="utf-8")
    result = emit_armory(armory_path, tmp_path / "armory", catalog=catalog)
    game = tmp_path / "game"
    (game / "WillowGame" / "CookedPCConsole").mkdir(parents=True)
    (game / "sdk_mods").mkdir()
    installed = install_armory(result, game, catalog=catalog)
    assert installed.mod_dir == game / "sdk_mods" / "PipelineTestArmory"
    assert (installed.mod_dir / "__init__.py").exists()
    assert (installed.mod_dir / "weapons" / "ak.py").exists()
    assert (installed.mod_dir / "weapons" / "sniper.py").exists()
    assert not (installed.mod_dir / "settings").exists()
    assert json.loads(installed.settings_file.read_text(encoding="utf-8")) == {"enabled": True}
    cooked = game / "WillowGame" / "CookedPCConsole"
    assert (cooked / "PipelineMeshes.upk").exists() and (cooked / "PipelineMeshesAWP.upk").exists()
    # re-install replaces the folder wholesale (a dropped weapon must not linger)
    (installed.mod_dir / "weapons" / "stale.py").write_text("", encoding="utf-8")
    install_armory(result, game, catalog=catalog)
    assert not (installed.mod_dir / "weapons" / "stale.py").exists()


def test_auto_spawn_sequence_from_the_control_file(emitted: Any, graph: Any) -> None:
    """control.json {"auto_spawn": [...]}: census on load, each spawn on the wall clock,
    closing census + done -- the records src/bl2_verify/armory_check.py reads."""
    module = load_armory_mod(emitted.out_dir, graph)
    for w in module.WEAPONS:
        assert w["module"].menu_setup()["ok"]
    assert module.on_map_loaded.enabled and not module.seq_tick.enabled
    # no control file: a map load only records the census
    module.on_map_loaded(graph.controller, None, None, None)
    assert module._status[-1]["census_on_load"] == [] and not module.seq_tick.enabled
    assert module._status[-1]["registration"] == {"ak": True, "sniper": True}

    (emitted.out_dir / "control.json").write_text(
        json.dumps({"auto_spawn": ["ak", "sniper"]}), encoding="utf-8")
    module.on_map_loaded(graph.controller, None, None, None)
    assert module.seq_tick.enabled and module._status[-1]["auto_spawn"] == ["ak", "sniper"]
    module.seq_tick(None, None, None, None)  # before the first delay: nothing
    assert not any(r.get("spawn") for r in module._status)
    for _ in range(4):
        module._auto_deadline = 0.0
        module.seq_tick(None, None, None, None)
    spawns = [r["spawn"] for r in module._status if "spawn" in r]
    assert spawns == ["ak", "sniper"]
    done = [r for r in module._status if r.get("done")]
    assert len(done) == 1 and [c["id"] for c in done[0]["census"]] == ["ak", "sniper"]
    assert not module.seq_tick.enabled
    # a second map load (the reload) sees both weapons before spawning again
    module.on_map_loaded(graph.controller, None, None, None)
    loads = [r for r in module._status if "census_on_load" in r]
    assert [c["id"] for c in loads[-1]["census_on_load"]] == ["ak", "sniper"]
