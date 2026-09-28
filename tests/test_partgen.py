"""Tests for ``bl2_partgen``: the emitter, the generated mod, and the installer.

The generated mod is exercised for real against the fakes in ``tests/fakes`` (a fake
``unrealsdk`` + ``mods_base`` over an object graph built from ``catalog/parts.json``), so
these tests check behaviour and not just that a string was written:

* the M2 spec emits a module that parses, imports, and enables every hook at import (F8)
* ``menu_setup()`` registers exactly one fragment, one bounds row, three socket mappings
  and one part, appends the part to the Shredifier barrel list (F5) and leaves the stock
  rows alone
* everything it touches, and its outer chain, comes back with ObjectFlags 0x4000 (F17)
* the save hooks record a custom part by UniqueId and put it back on load (F18)
* ``ValidateWeaponDefinition`` is forced True for our parts and left alone for others (F19)
* under ``test_harness`` + ``harness_auto`` the per-map phase machine runs all four phases
  against the fakes, writing the record keys ``bl2_verify``'s loop reads, granting our part
  deterministically and leaving the part list exactly as it found it
* a spec that collides with the catalog is refused before anything is written
* install writes the three destinations and refuses an exe-hashed package name (F12)

Run with ``python -m pytest tests/test_partgen.py -q``.
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
from unrealsdk.hooks import Block  # noqa: E402
from unrealsdk.objects import FakeObject, FakeStruct  # noqa: E402

from bl2_catalog import load_catalog  # noqa: E402
from bl2_partgen import EmitRefused, InstallRefused, emit, install, load_spec  # noqa: E402
from bl2_partgen.spec import Spec, SpecError  # noqa: E402

from tests.fakes.graph import build_graph  # noqa: E402

KEEP_ALIVE_FLAG = 0x4000
SPEC_PATH = REPO / "specs" / "bent_barrel.json"
NEW_FRAGMENT = "AR_Barrel_PL_Bent"
NEW_PART = "GD_Weap_AssaultRifle.Barrel.AR_Barrel_PL_Bent"
TEMPLATE_PART = "GD_Weap_AssaultRifle.Barrel.AR_Barrel_Vladof_Shredifier"
_counter = itertools.count()


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def catalog() -> dict[str, Any]:
    path = REPO / "catalog" / "parts.json"
    if not path.exists():
        pytest.skip("catalog/parts.json absent; run `python -m bl2_catalog`")
    return load_catalog(path)


@pytest.fixture()
def spec_dict() -> dict[str, Any]:
    if not SPEC_PATH.exists():
        pytest.skip("specs/bent_barrel.json absent")
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def emitted(tmp_path: Path, catalog: dict[str, Any]) -> Any:
    return emit(load_spec(SPEC_PATH), tmp_path / "mod", catalog=catalog, spec_name=str(SPEC_PATH))


def load_mod(out_dir: Path, graph: Any) -> ModuleType:
    """Import a generated mod against the fake SDK, as the game would at startup."""
    unrealsdk.set_world(graph.world)
    mods_base.set_pc(graph.controller)
    mods_base.built_mods.clear()
    name = f"emitted_mod_{next(_counter)}"
    spec = importlib.util.spec_from_file_location(name, out_dir / "__init__.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def graph(catalog: dict[str, Any]) -> Any:
    return build_graph(catalog)


@pytest.fixture()
def mod(emitted: Any, graph: Any) -> ModuleType:
    return load_mod(emitted.out_dir, graph)


# ------------------------------------------------------------------ emitting
def test_emits_a_module_that_parses(emitted: Any) -> None:
    source = emitted.mod_file.read_text(encoding="utf-8")
    ast.parse(source)  # raises SyntaxError if the generator produced garbage
    compile(source, str(emitted.mod_file), "exec")
    assert emitted.lint_ok and not emitted.forced
    names = {path.name for path in emitted.files}
    assert names == {
        "__init__.py", "pyproject.toml", "PipelineBentBarrel.json", "README.md", "spec.json",
        "lint.json",
    }
    assert json.loads(emitted.settings_file.read_text(encoding="utf-8")) == {"enabled": True}
    # the M2 mod carries every optional hook; it still has to stay readable in one sitting
    # (the budget grew once, by the AdditionalGestaltModeSkeletalMeshNames clearing -- M8 s6,
    # and twice for F29: merge-never-replace on save, then retire-not-delete on load)
    assert len(source.splitlines()) < 600


def test_registration_only_mod_fits_the_line_budget(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any]
) -> None:
    """One fragment + one part, registration only: under ~400 lines of generated code."""
    spec_dict["options"].update(
        save_roundtrip=False, validate_override=False, force_template_fragment_on_map_load=None
    )
    result = emit(Spec.from_dict(spec_dict), tmp_path / "core", catalog=catalog)
    source = result.mod_file.read_text(encoding="utf-8")
    ast.parse(source)
    assert len(source.splitlines()) < 400
    assert "GeneratePlayerSaveGame" not in source and "ValidateWeaponDefinition" not in source


def test_generated_mod_is_generic_not_the_m2_harness(emitted: Any) -> None:
    source = emitted.mod_file.read_text(encoding="utf-8")
    for dropped in ("keybind", "SetBehindView", "ServerGrantMissionRewards", "ConsoleCommand"):
        assert dropped not in source, f"{dropped} is test-harness only (test_harness: false)"
    for required in ("menu_setup", "keep_alive", "0x4000", "GeneratePlayerSaveGame",
                     "ApplyPlayerSaveGameData", "ValidateWeaponDefinition"):
        assert required in source


def test_test_harness_option_adds_the_keybinds(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    spec_dict["options"]["test_harness"] = True
    result = emit(Spec.from_dict(spec_dict), tmp_path / "harness", catalog=catalog)
    source = result.mod_file.read_text(encoding="utf-8")
    assert "@keybind(" in source and "_weapon_census" in source
    module = load_mod(result.out_dir, graph)
    assert [kb.key for kb in mods_base.built_mods[-1].keybinds] == ["F9", "F10", "F11", "F12"]
    module.menu_setup()
    census = module._weapon_census()
    assert census and census[0]["BarrelPartDefinition"] == TEMPLATE_PART


def test_harness_auto_reproduces_the_m2_phase_machine(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """`options.harness_auto`: the per-map sequence bl2_verify's loop reads, phase by phase.

    The loop (``src/bl2_verify/run_loop.py``) was written against ``pipeline_m2``: it waits
    for ``{"phase": "menu_setup"}``, then for ``{"done": true}``, and reads the custom part
    out of the second ``phase 0`` record's ``census_before[i]["barrel"]``. This drives the
    tick counter through all four phases and checks exactly that shape.
    """
    spec_dict["options"].update(test_harness=True, harness_auto=True)
    result = emit(Spec.from_dict(spec_dict), tmp_path / "auto", catalog=catalog)
    module = load_mod(result.out_dir, graph)

    assert module.menu_setup()["phase"] == "menu_setup", "the loop waits for this record"
    barrel_list_before = [entry.Part for entry in graph.barrel_parts]

    # the map-load hook arms the phase machine; nothing happens for 120 viewport ticks
    module.on_map_loaded(graph.controller, None, None, None)
    assert module.seq_tick.enabled and module._wait == module.HARNESS_TICKS_AFTER_LOAD
    for _ in range(module.HARNESS_TICKS_AFTER_LOAD - 1):
        module.seq_tick(None, None, None, None)
    assert not any(r.get("phase") == 0 for r in module._status), "fired before its wait was up"

    for _ in range(600):
        if not module.seq_tick.enabled:
            break
        module.seq_tick(None, None, None, None)
    assert not module.seq_tick.enabled, "the phase machine never reached phase 4"

    phases = {r["phase"]: r for r in module._status if isinstance(r.get("phase"), int)}
    assert set(phases) == {0, 1, 2, 3}
    # the record keys are m2_mod_template.py's, verbatim: the loop keys off them
    assert set(phases[0]) >= {
        "census_before", "force_stock_fragment", "force_barrel_stock", "grant_stock"
    }
    assert set(phases[1]) >= {"stock_weapon", "force_barrel_new", "grant_new"}
    assert set(phases[2]) >= {"new_weapon", "restore_barrel_list", "equip_newest"}
    assert set(phases[3]) >= {"camera", "held", "done"}
    assert phases[3]["done"] is True
    assert all(
        not str(value).startswith("FAILED") for record in phases.values()
        for value in record.values()
    ), phases

    # census rows carry the M2 keys (the loop reads "barrel")
    row = phases[0]["census_before"][0]
    assert {"weapon", "slot", "barrel", "frag", "FirstPersonMesh", "ThirdPersonMesh"} <= set(row)
    assert row["barrel"] == TEMPLATE_PART and row["frag"] == "AR_Barrel_Vladof"

    # the forced grant is deterministic: stock first, then ours, and the held weapon is ours
    assert phases[0]["force_stock_fragment"] == "AR_Barrel_Vladof"
    assert phases[1]["stock_weapon"]["barrel"] == TEMPLATE_PART
    assert phases[2]["new_weapon"]["barrel"] == NEW_PART
    assert phases[2]["new_weapon"]["frag"] == NEW_FRAGMENT
    assert phases[3]["held"]["barrel"] == NEW_PART
    assert len(graph.granted) == 2

    # phase 3 is the only place the harness touches the camera
    assert phases[3]["camera"] == "set"
    assert graph.console == ["FOV 100", "SetBehindView(True)"]

    # and the part list is back exactly as it was: forcing it is a harness-only loan
    assert phases[2]["restore_barrel_list"] == "restored"
    assert [entry.Part for entry in graph.barrel_parts] == barrel_list_before
    assert [entry.Part._path_name() for entry in graph.barrel_parts] == [TEMPLATE_PART, NEW_PART]
    assert module._barrel_backup is None


def test_harness_auto_off_drops_the_phase_machine(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any]
) -> None:
    spec_dict["options"].update(test_harness=True, harness_auto=False)
    source = emit(
        Spec.from_dict(spec_dict), tmp_path / "manual", catalog=catalog
    ).mod_file.read_text(encoding="utf-8")
    assert "@keybind(" in source, "the keybinds are the rest of test_harness"
    for dropped in ("seq_tick", "force_barrel", "_camera", "ConsoleCommand"):
        assert dropped not in source  # SetBehindView stays: the F12 review toggle is a keybind, not the auto sequence


def test_harness_status_records_use_the_m2_key_names(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """The loop's reload assertion reads save_hook / load_hook / validate_hook records."""
    spec_dict["options"]["test_harness"] = True
    result = emit(Spec.from_dict(spec_dict), tmp_path / "keys", catalog=catalog)
    module = load_mod(result.out_dir, graph)
    part = _equip_our_part(module, graph)

    module.on_generate_save(graph.controller, None, None, None)
    assert module._status[-1]["save_hook"] == "GeneratePlayerSaveGame"

    args = FakeStruct(SaveGame=FakeStruct(SaveGameId=1, WeaponData=[]))
    module.on_apply_save(graph.controller, args, None, None)
    assert module._status[-1]["load_hook"] == "ApplyPlayerSaveGameData"

    ours = FakeStruct(UniqueId=1234567, BarrelPartDefinition=part)
    module.on_validate_weapon(graph.controller, FakeStruct(DefinitionData=ours), None, None)
    assert module._status[-1]["validate_hook"] == "ValidateWeaponDefinition"


def test_status_path_option_is_baked_into_the_mod(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """The loop points the status file at scratch/ without needing a control file."""
    baked = tmp_path / "scratch" / "PipelineBentBarrelHarness_status.json"
    spec_dict["options"].update(test_harness=True, status_path=str(baked))
    result = emit(Spec.from_dict(spec_dict), tmp_path / "baked", catalog=catalog)
    module = load_mod(result.out_dir, graph)

    module._write({"phase": "probe"})
    assert json.loads(baked.read_text(encoding="utf-8"))[-1]["phase"] == "probe"
    assert not (result.out_dir / "status.json").exists()

    # a control file still wins, so a run can be redirected without re-emitting
    redirected = tmp_path / "elsewhere" / "status.json"
    Path(module.CONTROL_FILE).write_text(
        json.dumps({"status_path": str(redirected)}), encoding="utf-8"
    )
    module._write({"phase": "redirected"})
    assert json.loads(redirected.read_text(encoding="utf-8"))[-1]["phase"] == "redirected"

    spec_dict["options"]["status_path"] = "relative/status.json"
    with pytest.raises(SpecError, match="absolute"):
        Spec.from_dict(spec_dict)


def test_lint_refuses_a_colliding_spec(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any]
) -> None:
    """A fragment name that already exists in the AR table shadows it: L2, error."""
    spec_dict["fragments"][0]["name"] = "AR_Barrel_Vladof"
    spec_dict["parts"][0]["fragment"] = "AR_Barrel_Vladof"
    spec_dict["options"]["force_template_fragment_on_map_load"] = None
    out = tmp_path / "colliding"
    with pytest.raises(EmitRefused) as excinfo:
        emit(Spec.from_dict(spec_dict), out, catalog=catalog)
    assert "L2" in str(excinfo.value)
    assert not out.exists(), "nothing may be written while the linter is unhappy"

    forced = emit(Spec.from_dict(spec_dict), out, catalog=catalog, force=True)
    assert forced.forced and not forced.lint_ok
    assert json.loads((out / "lint.json").read_text(encoding="utf-8"))["forced"] is True


# ------------------------------------------------------------------ the generated mod
def test_hooks_are_enabled_at_import(mod: ModuleType) -> None:
    """F8: nothing auto-enables a mod on the launch that installs it."""
    built = mods_base.built_mods[-1]
    assert built.name == "PipelineBentBarrel"
    assert {hook.__name__ for hook in built.hooks} == {
        "menu_tick", "on_generate_save", "on_apply_save", "on_validate_weapon", "on_map_loaded",
    }
    assert all(hook.enabled for hook in built.hooks), "every hook must be enabled at import"
    assert mod.menu_tick.target == "WillowGame.WillowGameViewportClient:Tick"


def test_menu_setup_registers_exactly_one_of_everything(mod: ModuleType, graph: Any) -> None:
    before_fragments = len(graph.gestalt.GestaltInfos[0].Parts)
    before_bounds = len(graph.gestalt.GestaltPartBounds)
    before_mappings = len(graph.gestalt.GestaltSocketMappings)
    assert before_fragments == 47 and len(graph.new_mesh.Sockets) == 48

    record = mod.menu_setup()
    assert record["ok"] is True, record.get("error")

    # the package was loaded and the gestalt definition now points at our mesh
    assert "PipelineMeshes" in graph.world.loaded_packages
    assert graph.gestalt.GestaltSkeletalMesh is graph.new_mesh

    parts = graph.gestalt.GestaltInfos[0].Parts
    assert len(parts) == before_fragments + 1
    new = parts[-1]
    assert str(new.SkeletalMeshFragmentName) == NEW_FRAGMENT
    assert (int(new.FirstIndex), int(new.NumPrimitives)) == (68085, 1164)
    assert graph.fragment_names.count(NEW_FRAGMENT) == 1

    # the template row must be untouched: appending a struct copies it
    template_row = next(
        p for p in parts if str(p.SkeletalMeshFragmentName) == "AR_Barrel_Vladof"
    )
    assert int(template_row.FirstIndex) == 23484

    assert len(graph.gestalt.GestaltPartBounds) == before_bounds + 1
    new_bounds = graph.gestalt.GestaltPartBounds[-1]
    assert str(new_bounds.SkeletalMeshFragmentName) == NEW_FRAGMENT
    # dz 8.0 raises the box by half of it and the sphere by all of it
    assert new_bounds.ReferencePoseBounds.SphereRadius == pytest.approx(45.075493 + 8.0)

    mappings = graph.gestalt.GestaltSocketMappings
    ours = [m for m in mappings if str(m.SkeletalMeshFragmentName) == NEW_FRAGMENT]
    assert len(mappings) == before_mappings + 3
    assert {str(m.OriginalSocketName) for m in ours} == {"Muzzle", "EyeSocket2", "FrontSight"}
    assert {str(m.MangledSocketName) for m in ours} == {
        f"{NEW_FRAGMENT}_Muzzle", f"{NEW_FRAGMENT}_EyeSocket2", f"{NEW_FRAGMENT}_FrontSight"
    }

    sockets = graph.socket_names
    assert len(sockets) == 51
    assert f"{NEW_FRAGMENT}_Muzzle" in sockets
    muzzle = next(
        s for s in graph.gestalt.GestaltSkeletalMesh.Sockets
        if str(s.SocketName) == f"{NEW_FRAGMENT}_Muzzle"
    )
    template_muzzle = next(
        s for s in graph.gestalt.GestaltSkeletalMesh.Sockets
        if str(s.SocketName) == "AR_Barrel_Vladof_Muzzle"
    )
    assert muzzle.RelativeLocation.Z == pytest.approx(template_muzzle.RelativeLocation.Z + 8.0)

    part = unrealsdk.find_object("WeaponPartDefinition", NEW_PART)
    assert str(part.GestaltModeSkeletalMeshName) == NEW_FRAGMENT
    assert part.Outer._path_name() == "GD_Weap_AssaultRifle.Barrel"
    assert graph.template_part.GestaltModeSkeletalMeshName == "AR_Barrel_Vladof"

    weighted = graph.barrel_parts
    assert len(weighted) == 2
    assert [entry.Part._path_name() for entry in weighted] == [TEMPLATE_PART, NEW_PART]
    # the appended entry keeps the stock entry's weights
    assert weighted[-1].DefaultWeightIndex == weighted[0].DefaultWeightIndex


def test_menu_setup_roots_everything_it_touches(mod: ModuleType, graph: Any) -> None:
    """F17: unrooted objects are collected at the next map load and the game dies."""
    mod.menu_setup()
    rooted = lambda obj: bool(int(obj.ObjectFlags) & KEEP_ALIVE_FLAG)  # noqa: E731
    mesh = graph.gestalt.GestaltSkeletalMesh
    assert rooted(mesh) and rooted(mesh.Outer), "mesh and its package"
    assert all(rooted(socket) for socket in mesh.Sockets), "every socket, old and new"
    part = unrealsdk.find_object("WeaponPartDefinition", NEW_PART)
    assert rooted(part) and rooted(part.Outer) and rooted(part.Outer.Outer), "part outer chain"
    assert rooted(unrealsdk.find_object("Package", "PipelineMeshes"))


def test_menu_setup_is_idempotent(mod: ModuleType, graph: Any) -> None:
    first = mod.menu_setup()
    counts = (
        len(graph.gestalt.GestaltInfos[0].Parts),
        len(graph.gestalt.GestaltPartBounds),
        len(graph.gestalt.GestaltSocketMappings),
        len(graph.socket_names),
        len(graph.barrel_parts),
    )
    assert first["ok"] is True
    second = mod.menu_setup()
    assert "skipped" in second
    # and even with the guard defeated, re-registering must not double anything
    mod._registered = False
    mod.menu_setup()
    assert counts == (
        len(graph.gestalt.GestaltInfos[0].Parts),
        len(graph.gestalt.GestaltPartBounds),
        len(graph.gestalt.GestaltSocketMappings),
        len(graph.socket_names),
        len(graph.barrel_parts),
    )


def test_menu_tick_registers_only_at_the_menu_tick_count(mod: ModuleType, graph: Any) -> None:
    """F16: registration must happen at the main menu, not at once and not after a map load."""
    for _ in range(mod.MENU_TICKS - 1):
        mod.menu_tick(None, None, None, None)
    assert len(graph.gestalt.GestaltInfos[0].Parts) == 47, "nothing registered yet"
    assert mod.menu_tick.enabled
    mod.menu_tick(None, None, None, None)
    assert len(graph.gestalt.GestaltInfos[0].Parts) == 48
    assert not mod.menu_tick.enabled, "the tick hook unhooks itself once it has fired"


def test_status_json_goes_where_the_control_file_says(
    mod: ModuleType, emitted: Any, tmp_path: Path
) -> None:
    mod.menu_setup()
    default = emitted.out_dir / "status.json"
    assert default.exists() and json.loads(default.read_text(encoding="utf-8"))[-1]["ok"] is True

    redirected = tmp_path / "verify" / "status.json"
    Path(mod.CONTROL_FILE).write_text(
        json.dumps({"status_path": str(redirected)}), encoding="utf-8"
    )
    mod._write({"phase": "probe"})
    assert json.loads(redirected.read_text(encoding="utf-8"))[-1]["phase"] == "probe"


# ------------------------------------------------------------------ save round-trip
def _equip_our_part(mod: ModuleType, graph: Any) -> Any:
    mod.menu_setup()
    part = unrealsdk.find_object("WeaponPartDefinition", NEW_PART)
    graph.weapon.DefinitionData.BarrelPartDefinition = part
    return part


def test_save_records_our_part_by_unique_id(mod: ModuleType, graph: Any, emitted: Any) -> None:
    """F18: the part cannot be serialised, so we record it beside the save."""
    part = _equip_our_part(mod, graph)
    mod.on_generate_save(graph.controller, None, None, None)

    record_file = emitted.out_dir / "_pipeline_saves" / "PipelineMeshes" / "Save0001.sav.json"
    records = json.loads(record_file.read_text(encoding="utf-8"))
    assert records == {"1234567": {"BarrelPartDefinition": NEW_PART}}
    assert int(part.ObjectFlags) & KEEP_ALIVE_FLAG


def test_save_keeps_records_for_weapons_it_cannot_see(
    mod: ModuleType, graph: Any, emitted: Any
) -> None:
    """F29: one bad load must not destroy the record, or the weapon can never be repaired.

    A weapon only reaches ``on_generate_save`` if it is carrying our parts *at that instant*.
    Anything that stops a load restoring them -- the mod disabled, a build reading the wrong
    folder -- makes it invisible here, and this hook also runs on every map load. Replacing the
    file would drop the record seconds after a bad load; merging keeps it repairable.
    """
    _equip_our_part(mod, graph)
    mod.on_generate_save(graph.controller, None, None, None)
    record_file = emitted.out_dir / "_pipeline_saves" / "PipelineMeshes" / "Save0001.sav.json"
    assert set(json.loads(record_file.read_text(encoding="utf-8"))) == {"1234567"}

    # the load did not restore it: the weapon is in the inventory without our part
    graph.weapon.DefinitionData.BarrelPartDefinition = graph.template_part
    mod.on_generate_save(graph.controller, None, None, None)

    records = json.loads(record_file.read_text(encoding="utf-8"))
    assert records == {"1234567": {"BarrelPartDefinition": NEW_PART}}, (
        "the record for a weapon this save could not see was dropped; a single bad load "
        "would make it unrepairable"
    )
    assert mod._status[-1]["custom_weapons"] == {}
    assert mod._status[-1]["kept_from_previous"] == ["1234567"]


def _apply(mod: ModuleType, graph: Any, unique_ids: list[int]) -> None:
    """Drive on_apply_save with a save holding exactly these weapon ids, parts stripped."""
    args = FakeStruct(
        SaveGame=FakeStruct(
            SaveGameId=1,
            WeaponData=[
                FakeStruct(
                    WeaponDefinitionData=FakeStruct(
                        UniqueId=uid, BalanceDefinition=graph.balance, BarrelPartDefinition=None
                    )
                )
                for uid in unique_ids
            ],
        )
    )
    mod.on_apply_save(graph.controller, args, None, None)


def test_load_retires_records_the_save_has_no_weapon_for(
    mod: ModuleType, graph: Any, emitted: Any
) -> None:
    """F29: the active file stays small, but a pruned record is retired, never deleted."""
    _equip_our_part(mod, graph)
    mod.on_generate_save(graph.controller, None, None, None)
    active = emitted.out_dir / "_pipeline_saves" / "PipelineMeshes" / "Save0001.sav.json"
    retired = active.with_name("Save0001.sav.retired.json")

    _apply(mod, graph, [999])  # the save holds some other weapon; ours is not in it

    assert json.loads(active.read_text(encoding="utf-8")) == {}
    assert json.loads(retired.read_text(encoding="utf-8")) == {
        "1234567": {"BarrelPartDefinition": NEW_PART}
    }
    assert mod._status[-1]["retired"] == ["1234567"]


def test_load_promotes_a_retired_record_when_the_weapon_comes_back(
    mod: ModuleType, graph: Any, emitted: Any
) -> None:
    """Banking a weapon takes it out of the save's weapon list; withdrawing it must work."""
    _equip_our_part(mod, graph)
    mod.on_generate_save(graph.controller, None, None, None)
    active = emitted.out_dir / "_pipeline_saves" / "PipelineMeshes" / "Save0001.sav.json"
    _apply(mod, graph, [999])
    assert json.loads(active.read_text(encoding="utf-8")) == {}

    _apply(mod, graph, [1234567])  # back out of the bank

    assert mod._status[-1]["promoted"] == ["1234567"]
    assert any("1234567:BarrelPartDefinition" in r for r in mod._status[-1]["restored"])
    assert json.loads(active.read_text(encoding="utf-8")) == {
        "1234567": {"BarrelPartDefinition": NEW_PART}
    }


def test_load_never_prunes_when_the_save_deserialised_no_weapons(
    mod: ModuleType, graph: Any, emitted: Any
) -> None:
    """An empty scan means the shape is not what we think -- pruning then loses everything."""
    _equip_our_part(mod, graph)
    mod.on_generate_save(graph.controller, None, None, None)
    active = emitted.out_dir / "_pipeline_saves" / "PipelineMeshes" / "Save0001.sav.json"

    _apply(mod, graph, [])

    assert json.loads(active.read_text(encoding="utf-8")) == {
        "1234567": {"BarrelPartDefinition": NEW_PART}
    }
    assert mod._status[-1]["retired"] == []


def test_load_restores_our_part_before_the_game_builds_the_weapon(
    mod: ModuleType, graph: Any
) -> None:
    part = _equip_our_part(mod, graph)
    mod.on_generate_save(graph.controller, None, None, None)

    # what the save deserialises to: the custom slot comes back null (F18)
    saved = FakeStruct(
        UniqueId=1234567, BalanceDefinition=graph.balance, BarrelPartDefinition=None
    )
    other = FakeStruct(
        UniqueId=999, BalanceDefinition=graph.balance, BarrelPartDefinition=graph.template_part
    )
    args = FakeStruct(
        SaveGame=FakeStruct(
            SaveGameId=1,
            WeaponData=[
                FakeStruct(WeaponDefinitionData=saved),
                FakeStruct(WeaponDefinitionData=other),
            ],
        )
    )
    mod.on_apply_save(graph.controller, args, None, None)

    assert saved.BarrelPartDefinition is part
    assert other.BarrelPartDefinition is graph.template_part, "other weapons are left alone"


def test_load_survives_a_save_with_no_record(mod: ModuleType, graph: Any) -> None:
    mod.menu_setup()
    args = FakeStruct(SaveGame=FakeStruct(SaveGameId=7, WeaponData=[]))
    mod.on_apply_save(graph.controller, args, None, None)  # must not raise
    assert mod._status[-1]["record"] is None


def test_validation_is_forced_only_for_our_parts(mod: ModuleType, graph: Any) -> None:
    """F19: the cooked-data sanity check deletes weapons that use a runtime part."""
    part = _equip_our_part(mod, graph)
    ours = FakeStruct(UniqueId=1234567, BarrelPartDefinition=part)
    theirs = FakeStruct(UniqueId=999, BarrelPartDefinition=graph.template_part)

    assert mod.on_validate_weapon(
        graph.controller, FakeStruct(DefinitionData=ours), None, None
    ) == (Block, True)
    assert mod.on_validate_weapon(
        graph.controller, FakeStruct(DefinitionData=theirs), None, None
    ) is None


def test_map_load_reasserts_the_stock_fragment(mod: ModuleType, graph: Any) -> None:
    """A text mod may have retargeted the template part; put it back."""
    mod.menu_setup()
    graph.template_part.GestaltModeSkeletalMeshName = "AK47_Barrel"
    mod.on_map_loaded(graph.controller, None, None, None)
    assert graph.template_part.GestaltModeSkeletalMeshName == "AR_Barrel_Vladof"


# ------------------------------------------------------------------ install
def make_game_dir(tmp_path: Path) -> Path:
    game = tmp_path / "game"
    (game / "WillowGame" / "CookedPCConsole").mkdir(parents=True)
    (game / "Binaries" / "Win32").mkdir(parents=True)
    (game / "sdk_mods").mkdir()
    return game


def test_install_writes_the_three_destinations(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any]
) -> None:
    package = tmp_path / "PipelineMeshes.upk"
    package.write_bytes(b"fake package bytes")
    spec_dict["package_file"] = str(package)
    result = emit(Spec.from_dict(spec_dict), tmp_path / "mod", catalog=catalog)

    game = make_game_dir(tmp_path)
    installed = install(result, game, catalog=catalog)

    mod_dir = game / "sdk_mods" / "PipelineBentBarrel"
    assert (mod_dir / "__init__.py").exists()
    assert (mod_dir / "pyproject.toml").exists()
    assert not (mod_dir / "settings").exists(), "settings go to sdk_mods/settings/"
    settings = game / "sdk_mods" / "settings" / "PipelineBentBarrel.json"
    assert json.loads(settings.read_text(encoding="utf-8")) == {"enabled": True}
    dest = game / "WillowGame" / "CookedPCConsole" / "PipelineMeshes.upk"
    assert dest.read_bytes() == b"fake package bytes"
    assert installed.package_dest == dest

    # same bytes again is fine; different bytes need --replace
    install(result, game, catalog=catalog)
    package.write_bytes(b"different bytes")
    with pytest.raises(InstallRefused, match="different bytes"):
        install(result, game, catalog=catalog)
    installed = install(result, game, replace=True, catalog=catalog)
    assert installed.package_replaced and dest.read_bytes() == b"different bytes"


def test_install_refuses_a_hashed_package_name(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any]
) -> None:
    """F12: any byte change in one of the twelve hashed packages kills the game."""
    package = tmp_path / "Startup.upk"
    package.write_bytes(b"nope")
    spec_dict["package"] = "Startup"
    spec_dict["mesh_path"] = "Startup.PL_AR_Gestalt_Mesh"
    spec_dict["package_file"] = str(package)
    # the linter already says no (L1), so emitting at all takes --force
    result = emit(Spec.from_dict(spec_dict), tmp_path / "mod", catalog=catalog, force=True)
    assert any("L1" in error for error in result.lint_errors)

    game = make_game_dir(tmp_path)
    with pytest.raises(InstallRefused, match="SHA1"):
        install(result, game, catalog=catalog)
    assert not (game / "WillowGame" / "CookedPCConsole" / "Startup.upk").exists()


def test_install_refuses_a_directory_that_is_not_a_game(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any]
) -> None:
    package = tmp_path / "PipelineMeshes.upk"
    package.write_bytes(b"bytes")
    spec_dict["package_file"] = str(package)
    result = emit(Spec.from_dict(spec_dict), tmp_path / "mod", catalog=catalog)
    empty = tmp_path / "not_a_game"
    empty.mkdir()
    with pytest.raises(InstallRefused, match="CookedPCConsole"):
        install(result, empty, catalog=catalog)


# ------------------------------------------------------------------ CLI
def test_cli_emits_and_lints(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from bl2_partgen.__main__ import main

    out = tmp_path / "cli_mod"
    assert main([str(SPEC_PATH), "--out", str(out)]) == 0
    assert (out / "__init__.py").exists()
    assert main([str(SPEC_PATH), "--lint-only"]) == 0
    printed = capsys.readouterr().out
    assert "AR_Barrel_PL_Bent" in printed and "PASS" in printed


# ------------------------------------------------------------------ build sidecars
def test_resolver_reads_a_single_fragment_sidecar(
    catalog: dict[str, Any], spec_dict: dict[str, Any]
) -> None:
    """The M2/M4 sidecar shape: one fragment at the top level."""
    from bl2_partgen.resolve import load_sidecar, resolve

    sidecar = {
        "package": "PipelineMeshes",
        "mesh_path": "PipelineMeshes.PL_AR_Gestalt_Mesh",
        "fragment": NEW_FRAGMENT,
        "template_fragment": "AR_Barrel_Vladof",
        "first_index": 68085,
        "num_primitives": 1164,
        "dz": 8.0,
        "part_name": NEW_FRAGMENT,
        "reparsed_ok": True,
        "out": "scratch/PipelineMeshes_m2.upk",
    }
    assert load_sidecar(sidecar) == {
        NEW_FRAGMENT: {"first_index": 68085, "num_primitives": 1164,
                       "template_fragment": "AR_Barrel_Vladof", "dz": 8.0,
                       "part_name": NEW_FRAGMENT}
    }
    spec = Spec.from_dict(spec_dict)
    plain = resolve(spec, catalog)
    withsidecar = resolve(spec, catalog, sidecar)
    assert (withsidecar.fragments[0].first_index, withsidecar.fragments[0].num_primitives) == (
        plain.fragments[0].first_index, plain.fragments[0].num_primitives)
    assert withsidecar.notes == plain.notes  # nothing moved, so nothing to say


def test_resolver_reads_a_multi_fragment_sidecar(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any]
) -> None:
    """The M6 shape: a ``fragments`` array, only the needed fields read from it."""
    from bl2_partgen.resolve import ResolveError, load_sidecar, resolve

    sidecar = {
        "package": "PipelineMeshes",
        "mesh_path": "PipelineMeshes.PL_AR_Gestalt_Mesh",
        "fragment": "PL_Test_Cyl",
        "first_index": 68085,
        "num_primitives": 44,
        "total_vertices": 28551,
        "fragments": [
            {"fragment": "PL_Test_Cyl", "first_index": 68085, "num_primitives": 44,
             "new_vertices": 24, "new_geometry": True},
            {"fragment": NEW_FRAGMENT, "first_index": 68217, "num_primitives": 1164,
             "new_vertices": 1308, "dz": 8.0},
        ],
    }
    assert sorted(load_sidecar(sidecar)) == sorted(["PL_Test_Cyl", NEW_FRAGMENT])
    assert load_sidecar(sidecar)[NEW_FRAGMENT]["first_index"] == 68217

    path = tmp_path / "PipelineMeshes_multi.fragment.json"
    path.write_text(json.dumps(sidecar), encoding="utf-8")

    resolved = resolve(Spec.from_dict(spec_dict), catalog, path)
    assert resolved.fragments[0].first_index == 68217  # moved by the first fragment
    assert resolved.fragments[0].num_primitives == 1164
    assert any("from the build sidecar" in note for note in resolved.notes)

    sidecar["fragments"] = [sidecar["fragments"][0]]
    with pytest.raises(ResolveError, match="rebuild the package"):
        resolve(Spec.from_dict(spec_dict), catalog, sidecar)


# ------------------------------------------------------------------ M6: several parts at once
def _multipart_spec(spec_dict: dict[str, Any]) -> dict[str, Any]:
    """The M2 spec grown into an M6-shaped one: four fragments, four parts, four slots."""
    spec_dict = json.loads(json.dumps(spec_dict))
    spec_dict["mod"]["name"] = "PipelineMultiPartHarness"
    base = spec_dict["fragments"][0]
    first = base["first_index"] + 3 * base["num_primitives"]
    extra = [
        ("AR_Body_PL_Multi", "AR_Body_Vladof", "WP_Body", "BodyPartData",
         "GD_Weap_AssaultRifle.Body", "GD_Weap_AssaultRifle.Body.AR_Body_Vladof_4"),
        ("AR_Grip_PL_Multi", "AR_Grip_Vladof", "WP_Grip", "GripPartData",
         "GD_Weap_AssaultRifle.Grip", "GD_Weap_AssaultRifle.Grip.AR_Grip_Vladof"),
        ("AR_Stock_PL_Multi", "AR_Stock_Vladof", "WP_Stock", "StockPartData",
         "GD_Weap_AssaultRifle.Stock", "GD_Weap_AssaultRifle.Stock.AR_Stock_Vladof"),
    ]
    balance = spec_dict["parts"][0]["register_in"][0]["balance"]
    for name, template, slot, field, outer, template_part in extra:
        spec_dict["fragments"].append({
            "name": name, "template_fragment": template,
            "first_index": first, "num_primitives": 100, "dz": 0.0,
            "sockets": [], "dz_sockets": [],
        })
        first += 300
        spec_dict["parts"].append({
            "part_name": name, "slot": slot, "outer": outer, "fragment": name,
            "template_part": template_part,
            "register_in": [{"balance": balance, "field": field}],
        })
    spec_dict["options"].update(test_harness=True, harness_auto=True)
    return spec_dict


def test_multi_part_harness_forces_every_registered_list(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """M6: a spec with four parts must grant a weapon carrying *all four*.

    The M2 harness forced only ``PARTS[0]``'s first list, which would have granted an
    AK barrel bolted onto a stock Vladof body.  With more than one registration target
    the emitter switches to the generalised ``force_barrel("new"|"template"|None)``,
    which forces every one of them and restores every one of them.
    """
    result = emit(Spec.from_dict(_multipart_spec(spec_dict)), tmp_path / "multi", catalog=catalog)
    source = result.mod_file.read_text(encoding="utf-8")
    assert "HARNESS_TARGETS" in source, "the multi-target force section was not emitted"

    module = load_mod(result.out_dir, graph)
    module.menu_setup()
    collection = graph.balance.RuntimePartListCollection
    fields = ["BodyPartData", "GripPartData", "BarrelPartData", "StockPartData"]
    before = {f: [e.Part for e in getattr(collection, f).WeightedParts] for f in fields}
    assert len(module.HARNESS_TARGETS) == 4

    module.on_map_loaded(graph.controller, None, None, None)
    for _ in range(3000):
        if not module.seq_tick.enabled:
            break
        module.seq_tick(None, None, None, None)
    assert not module.seq_tick.enabled

    phases = {r["phase"]: r for r in module._status if isinstance(r.get("phase"), int)}
    assert all(
        not str(value).startswith("FAILED") for record in phases.values()
        for value in record.values()
    ), phases

    # phase 1's weapon is all-stock, phase 2's is all-ours, in every one of the four slots
    stock, ours = phases[1]["stock_weapon"], phases[2]["new_weapon"]
    for field in fields:
        slot = field.replace("PartData", "PartDefinition")
        assert stock[slot] is not None and not stock[slot].endswith("_PL_Multi") \
            and stock[slot] != NEW_PART, (field, stock[slot])
        assert ours[slot].endswith(("_PL_Bent", "_PL_Multi")), (field, ours[slot])
    assert ours["BarrelPartDefinition"] == NEW_PART
    assert phases[3]["held"]["barrel"] == NEW_PART, "the loop reads 'barrel' from PARTS[0]"

    # and every list it borrowed is handed back exactly as it was
    assert phases[2]["restore_barrel_list"] == "restored"
    assert module._barrel_backup is None
    after = {f: [e.Part for e in getattr(collection, f).WeightedParts] for f in fields}
    assert after == before


def test_socket_overrides_place_a_socket_in_mesh_space(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """M6: ``socket_overrides`` puts a socket at an explicit mesh-space point.

    The gestalt ``Root`` bone is the identity, so an overridden socket is re-parented to
    it and the spec's numbers become ``RelativeLocation`` verbatim -- and ``dz`` (which
    would otherwise move the muzzle) is deliberately *not* added on top.
    """
    spec_dict["fragments"][0]["socket_overrides"] = {"Muzzle": [-0.162, -57.887, 5.86]}
    result = emit(Spec.from_dict(spec_dict), tmp_path / "sockets", catalog=catalog)
    module = load_mod(result.out_dir, graph)
    record = module.menu_setup()

    frag = record["fragments"][0]
    assert frag["socket_overrides"] == {"Muzzle": [-0.162, -57.887, 5.86]}
    sockets = graph.gestalt.GestaltSkeletalMesh.Sockets
    muzzle = next(s for s in sockets if str(s.SocketName) == f"{NEW_FRAGMENT}_Muzzle")
    assert str(muzzle.BoneName) == "Root", "an overridden socket must sit on the identity bone"
    assert (round(muzzle.RelativeLocation.X, 3), round(muzzle.RelativeLocation.Y, 3),
            round(muzzle.RelativeLocation.Z, 3)) == (-0.162, -57.887, 5.86)

    # the sockets that were NOT overridden still come from the template + dz
    eye = next(s for s in sockets if str(s.SocketName) == f"{NEW_FRAGMENT}_EyeSocket2")
    assert str(eye.BoneName) != "Root" or round(eye.RelativeLocation.Y, 3) != -57.887


def test_socket_override_for_an_unmapped_socket_is_refused(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any]
) -> None:
    spec_dict["fragments"][0]["socket_overrides"] = {"NotASocket": [0.0, 0.0, 0.0]}
    with pytest.raises(Exception) as excinfo:
        emit(Spec.from_dict(spec_dict), tmp_path / "bad", catalog=catalog)
    assert "NotASocket" in str(excinfo.value)


def test_socket_overrides_must_be_triples(spec_dict: dict[str, Any]) -> None:
    spec_dict["fragments"][0]["socket_overrides"] = {"Muzzle": [0.0, 1.0]}
    with pytest.raises(SpecError):
        Spec.from_dict(spec_dict)


# ------------------------------------------------------------------ parts that draw nothing
SIGHT_NONE = "GD_Weap_AssaultRifle.Sight.AR_Sight_None"
INVISIBLE_PART = "GD_Weap_AssaultRifle.Sight.AR_Sight_PL_None"


def _with_invisible_sight(spec_dict: dict[str, Any]) -> dict[str, Any]:
    """The M2 spec plus a part with **no fragment** that claims the sight slot."""
    spec_dict = json.loads(json.dumps(spec_dict))
    balance = spec_dict["parts"][0]["register_in"][0]["balance"]
    spec_dict["parts"].append({
        "part_name": "AR_Sight_PL_None", "slot": "WP_Sight", "fragment": None,
        "outer": "GD_Weap_AssaultRifle.Sight", "template_part": SIGHT_NONE,
        "register_in": [{"balance": balance, "field": "SightPartData"}],
    })
    return spec_dict


def test_a_part_with_no_fragment_claims_a_slot_and_draws_nothing(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """``"fragment": null``: a clone of the game's own non-gestalt ``AR_Sight_None``.

    The AK's rear sight is body geometry, so a stock scope rolling into
    ``SightPartData`` sits on top of it.  Claiming the slot with a part that draws
    nothing is the fix, and it must add NO fragment: no table row, no bounds, no
    sockets -- only a part, with ``bIsGestaltMode`` kept False on the clone.
    """
    spec = Spec.from_dict(_with_invisible_sight(spec_dict))
    assert spec.parts[1].fragment is None
    result = emit(spec, tmp_path / "invisible", catalog=catalog)
    assert result.lint_ok and not result.forced
    lint = json.loads((result.out_dir / "lint.json").read_text(encoding="utf-8"))
    by_part = {r["part"]: r for r in lint["results"]}
    proposal = by_part["AR_Sight_PL_None"]["proposal"]
    assert proposal["part_only"] is True and "fragment" not in proposal
    assert any("draws nothing" in note for note in result.notes)
    source = result.mod_file.read_text(encoding="utf-8")
    assert "definition.bIsGestaltMode = False" in source
    assert '"fragment": None' in source

    module = load_mod(result.out_dir, graph)
    fragments_before = len(graph.gestalt.GestaltInfos[0].Parts)
    bounds_before = len(graph.gestalt.GestaltPartBounds)
    sockets_before = len(graph.socket_names)
    sight_list = graph.balance.RuntimePartListCollection.SightPartData.WeightedParts
    sight_before = [e.Part for e in sight_list]

    record = module.menu_setup()
    assert record["ok"], record.get("error")
    # exactly the M2 barrel fragment was added; the sight part added nothing to the mesh
    assert len(graph.gestalt.GestaltInfos[0].Parts) == fragments_before + 1
    assert len(graph.gestalt.GestaltPartBounds) == bounds_before + 1
    assert len(graph.socket_names) == sockets_before + 3
    sight_record = next(p for p in record["parts"] if p["part"] == INVISIBLE_PART)
    assert sight_record["constructed"] is True and sight_record["fragment"] is None

    part = unrealsdk.find_object("WeaponPartDefinition", INVISIBLE_PART)
    assert part.bIsGestaltMode is False
    assert str(part.GestaltModeSkeletalMeshName) == "None", \
        "the inherited AR_Scope_Bandit is cleared: the gestalt assembly draws it whatever the flag says"
    assert part.ObjectFlags & KEEP_ALIVE_FLAG
    assert [e.Part for e in sight_list] == [*sight_before, part], "appended, stock rows kept"
    assert INVISIBLE_PART in module.OUR_PART_PATHS, "the save/validate hooks must know it"


def test_a_part_with_no_fragment_must_name_its_template(spec_dict: dict[str, Any]) -> None:
    spec_dict = _with_invisible_sight(spec_dict)
    del spec_dict["parts"][1]["template_part"]
    with pytest.raises(SpecError, match="template_part"):
        Spec.from_dict(spec_dict)


def test_the_harness_will_not_drive_a_part_with_no_fragment(spec_dict: dict[str, Any]) -> None:
    """parts[0] is what the harness reports as barrel/frag; it needs a fragment."""
    spec_dict = _with_invisible_sight(spec_dict)
    spec_dict["parts"].reverse()
    spec_dict["options"]["test_harness"] = True
    with pytest.raises(SpecError, match="parts\\[0\\]"):
        Spec.from_dict(spec_dict)


def test_harness_grants_the_part_with_no_fragment_too(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """With the sight slot claimed, the forced grant comes back with OUR sight part, and
    the A/B stock grant comes back with the template (``AR_Sight_None``) in that slot."""
    spec_dict = _with_invisible_sight(_multipart_spec(spec_dict))
    result = emit(Spec.from_dict(spec_dict), tmp_path / "multi_invisible", catalog=catalog)
    module = load_mod(result.out_dir, graph)
    module.menu_setup()
    assert len(module.HARNESS_TARGETS) == 5
    module.on_map_loaded(graph.controller, None, None, None)
    for _ in range(3000):
        if not module.seq_tick.enabled:
            break
        module.seq_tick(None, None, None, None)
    phases = {r["phase"]: r for r in module._status if isinstance(r.get("phase"), int)}
    assert phases[1]["stock_weapon"]["SightPartDefinition"] == SIGHT_NONE
    assert phases[2]["new_weapon"]["SightPartDefinition"] == INVISIBLE_PART
    assert phases[2]["new_weapon"]["ours"]["SightPartDefinition"] == INVISIBLE_PART
    assert phases[2]["restore_barrel_list"] == "restored" and module._barrel_backup is None


# ------------------------------------------------------------------ M7: a balance of its own
SHREDIFIER_TITLE = "GD_Weap_AssaultRifle.Name.Title_Vladof.Title_Legendary_Shredifier"
LEGENDARY_POOL = "GD_Itempools.WeaponPools.Pool_Weapons_AssaultRifles_06_Legendary"
NEW_BALANCE = "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_PL_Multi"
NEW_TITLE = "GD_Weap_AssaultRifle.Name.Title_Vladof.Title_Legendary_PL_Multi"
RED_TEXT = "Rush B. Don't stop.<br><font color=\"#5ff5ff\">7.62x39mm.</font>"


def _own_balance_spec(spec_dict: dict[str, Any]) -> dict[str, Any]:
    """The multi-part spec as its own gun: no register_in, one runtime balance whose lists
    are exactly our parts, a title with red text, a pool, and stat overrides on the barrel."""
    spec_dict = _with_invisible_sight(_multipart_spec(spec_dict))
    for part in spec_dict["parts"]:
        part["register_in"] = []
    spec_dict["parts"][0]["overrides"] = {
        "properties": {"bIsSpinningEnabled": False, "NumPhysicalBarrelsToFireFrom": 1},
        "weapon_attribute_effects": [
            {"attribute": "D_Attributes.Weapon.WeaponDamage", "modifier": "MT_Scale",
             "value": 0.18},
            {"attribute": "D_Attributes.Weapon.WeaponReloadSpeed", "modifier": "MT_Scale",
             "value": -0.15},
        ],
        "external_attribute_effects": [
            {"attribute": "D_Attributes.GameplayAttributes.FootSpeed", "modifier": "MT_Scale",
             "value": -0.14},
        ],
        "attribute_slot_upgrades": [{"slot": "WeaponSpread", "grade": -40}],
    }
    spec_dict["balances"] = [{
        "name": "AR_Vladof_5_PL_Multi",
        "outer": "GD_Weap_AssaultRifle.A_Weapons_Legendary",
        "template_balance": "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_Sherdifier",
        "part_lists": {
            "BarrelPartData": ["AR_Barrel_PL_Bent"],
            "BodyPartData": ["AR_Body_PL_Multi"],
            "GripPartData": ["AR_Grip_PL_Multi"],
            "StockPartData": ["AR_Stock_PL_Multi"],
            "SightPartData": ["AR_Sight_PL_None"],
        },
        "title": {
            "name": "Title_Legendary_PL_Multi",
            "outer": "GD_Weap_AssaultRifle.Name.Title_Vladof",
            "template": SHREDIFIER_TITLE,
            "part_name": "Multi-47",
            "red_text": RED_TEXT,
            "on_parts": ["AR_Barrel_PL_Bent"],
            "name_is_unique": True,
        },
        "pools": [LEGENDARY_POOL],
    }]
    return spec_dict


def _run_phases(module: ModuleType, graph: Any) -> dict[int, dict[str, Any]]:
    module.on_map_loaded(graph.controller, None, None, None)
    for _ in range(3000):
        if not module.seq_tick.enabled:
            break
        module.seq_tick(None, None, None, None)
    assert not module.seq_tick.enabled
    return {r["phase"]: r for r in module._status if isinstance(r.get("phase"), int)}


def test_runtime_balance_is_the_weapon_as_its_own_gun(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """M7: ``balances[]`` -- a WeaponBalanceDefinition cloned from the Shredifier's, with its
    OWN runtime part-list collection holding exactly our parts, a title with red text on the
    barrel, and an entry in the legendary pool.  The Shredifier itself is left untouched."""
    result = emit(Spec.from_dict(_own_balance_spec(spec_dict)), tmp_path / "own", catalog=catalog)
    assert result.lint_ok and not result.forced, result.lint_errors
    lint = json.loads((result.out_dir / "lint.json").read_text(encoding="utf-8"))
    first = lint["results"][0]
    assert first["proposal"]["new_balances"][0]["path"] == NEW_BALANCE
    assert first["proposal"]["register_in_runtime_lists"] == [
        f"{NEW_BALANCE}:RuntimePartList.BarrelPartData"]
    checks = {e["check"] for e in first["report"]["entries"]}
    assert "L10" in checks and "L6" in checks
    assert any(n.startswith("balance AR_Vladof_5_PL_Multi") for n in result.notes)

    module = load_mod(result.out_dir, graph)
    shred = graph.balance.RuntimePartListCollection
    fields = ["BarrelPartData", "BodyPartData", "GripPartData", "StockPartData", "SightPartData"]
    shred_before = {f: [e.Part for e in getattr(shred, f).WeightedParts] for f in fields}
    pool_before = len(graph.pool.BalancedItems)

    record = module.menu_setup()
    assert record["ok"], record.get("error")
    bal_record = record["balances"][0]
    assert bal_record["constructed"] is True

    balance = unrealsdk.find_object("WeaponBalanceDefinition", NEW_BALANCE)
    assert balance.ObjectFlags & KEEP_ALIVE_FLAG
    collection = balance.RuntimePartListCollection
    assert collection is not shred and collection.Outer is balance
    assert balance.WeaponPartListCollection is collection
    assert collection.ObjectFlags & KEEP_ALIVE_FLAG
    expected = {
        "BarrelPartData": [NEW_PART],
        "BodyPartData": ["GD_Weap_AssaultRifle.Body.AR_Body_PL_Multi"],
        "GripPartData": ["GD_Weap_AssaultRifle.Grip.AR_Grip_PL_Multi"],
        "StockPartData": ["GD_Weap_AssaultRifle.Stock.AR_Stock_PL_Multi"],
        "SightPartData": [INVISIBLE_PART],
    }
    for field, paths in expected.items():
        assert [e.Part._path_name() for e in getattr(collection, field).WeightedParts] == paths
        assert getattr(collection, field).bEnabled is True
    # fields the spec did not name keep the template's rows
    assert [e.Part for e in collection.ElementalPartData.WeightedParts] == \
        [e.Part for e in shred.ElementalPartData.WeightedParts]
    # and the Shredifier still rolls exactly what it always did (no register_in, no append)
    assert {f: [e.Part for e in getattr(shred, f).WeightedParts] for f in fields} == shred_before

    title = unrealsdk.find_object("WeaponNamePartDefinition", NEW_TITLE)
    assert str(title.PartName) == "Multi-47" and title.ObjectFlags & KEEP_ALIVE_FLAG
    assert title.bNameIsUnique is True, "name_is_unique in the spec -> bNameIsUnique on the clone"
    assert str(title.CustomPresentations[0].NoConstraintText) == RED_TEXT
    assert title.CustomPresentations[0] is not graph.shredifier_title.CustomPresentations[0]
    assert str(graph.shredifier_title.CustomPresentations[0].NoConstraintText) == "Speed kills."
    assert str(graph.shredifier_title.PartName) == "Shredifier"
    barrel = unrealsdk.find_object("WeaponPartDefinition", NEW_PART)
    assert list(barrel.TitleList) == [title]
    assert list(graph.template_part.TitleList) == [graph.shredifier_title]
    assert NEW_TITLE in module.OUR_PART_PATHS, "the title is a part: save/validate must know it"

    assert len(graph.pool.BalancedItems) == pool_before + 1
    assert graph.pool.BalancedItems[-1].InvBalanceDefinition is balance
    assert graph.pool.BalancedItems[-1].ItmPoolDefinition is None


def test_overrides_replace_the_clone_stats(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    result = emit(Spec.from_dict(_own_balance_spec(spec_dict)), tmp_path / "stats", catalog=catalog)
    module = load_mod(result.out_dir, graph)
    record = module.menu_setup()
    barrel_record = next(p for p in record["parts"] if p["part"] == NEW_PART)
    assert barrel_record["overrides"]["bIsSpinningEnabled"] is False
    barrel = unrealsdk.find_object("WeaponPartDefinition", NEW_PART)
    assert barrel.bIsSpinningEnabled is False and barrel.NumPhysicalBarrelsToFireFrom == 1
    rows = [(e.AttributeToModify._path_name(), e.ModifierType,
             e.BaseModifierValue.BaseValueConstant, e.BaseModifierValue.BaseValueAttribute,
             e.BaseModifierValue.BaseValueScaleConstant)
            for e in barrel.WeaponAttributeEffects]
    assert rows == [
        ("D_Attributes.Weapon.WeaponDamage", 0, 0.18, None, 1.0),
        ("D_Attributes.Weapon.WeaponReloadSpeed", 0, -0.15, None, 1.0),
    ]
    assert [(e.AttributeToModify._path_name(), e.BaseModifierValue.BaseValueConstant)
            for e in barrel.ExternalAttributeEffects] == \
        [("D_Attributes.GameplayAttributes.FootSpeed", -0.14)]
    assert [(str(e.SlotName), int(e.GradeIncrease), bool(e.bActivateSlot))
            for e in barrel.AttributeSlotUpgrades] == [("WeaponSpread", -40, True)]
    # the template keeps every one of its rows
    assert len(graph.template_part.WeaponAttributeEffects) == 2
    assert len(graph.template_part.AttributeSlotUpgrades) == 4
    assert graph.template_part.bIsSpinningEnabled is True


def test_harness_grants_from_the_runtime_balance(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """The harness grants OUR balance: phase 2's weapon is the whole set with our title, and
    the A/B stock grant is the same balance forced to the template parts."""
    result = emit(Spec.from_dict(_own_balance_spec(spec_dict)), tmp_path / "hb", catalog=catalog)
    module = load_mod(result.out_dir, graph)
    module.menu_setup()
    assert module.HARNESS_BALANCES[0] == NEW_BALANCE
    assert module.HARNESS_TARGET == {"balance": NEW_BALANCE, "field": "BarrelPartData"}
    assert {(t["balance"], t["field"]) for t in module.HARNESS_TARGETS} == {
        (NEW_BALANCE, f) for f in ("BarrelPartData", "BodyPartData", "GripPartData",
                                   "StockPartData", "SightPartData")}
    collection = unrealsdk.find_object("WeaponBalanceDefinition", NEW_BALANCE).RuntimePartListCollection
    before = {f: [e.Part for e in getattr(collection, f).WeightedParts]
              for f in ("BarrelPartData", "BodyPartData", "SightPartData")}

    phases = _run_phases(module, graph)
    assert all(not str(v).startswith("FAILED") for r in phases.values() for v in r.values()), phases
    stock, ours = phases[1]["stock_weapon"], phases[2]["new_weapon"]
    assert stock["balance"] == NEW_BALANCE and ours["balance"] == NEW_BALANCE
    assert stock["BarrelPartDefinition"] == TEMPLATE_PART
    assert stock["TitlePartDefinition"] == SHREDIFIER_TITLE
    assert ours["BarrelPartDefinition"] == NEW_PART
    assert ours["SightPartDefinition"] == INVISIBLE_PART
    assert ours["TitlePartDefinition"] == NEW_TITLE
    assert ours["ours"]["TitlePartDefinition"] == NEW_TITLE
    assert phases[3]["held"]["barrel"] == NEW_PART
    assert {f: [e.Part for e in getattr(collection, f).WeightedParts] for f in before} == before


def test_save_roundtrip_records_and_restores_the_balance(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """The runtime balance is no more cooked than the parts: it goes in the record and comes
    back before the game builds the weapon (F18), title included."""
    result = emit(Spec.from_dict(_own_balance_spec(spec_dict)), tmp_path / "sv", catalog=catalog)
    module = load_mod(result.out_dir, graph)
    module.menu_setup()
    _run_phases(module, graph)
    weapon = graph.pawn.Weapon
    assert weapon.DefinitionData.BalanceDefinition._path_name() == NEW_BALANCE

    module.on_generate_save(graph.controller, None, None, None)
    records = json.loads((result.out_dir / "_pipeline_saves" / "PipelineMeshes" / "Save0001.sav.json").read_text(encoding="utf-8"))
    record = records[str(int(weapon.DefinitionData.UniqueId))]
    assert record["BalanceDefinition"] == NEW_BALANCE
    assert record["BarrelPartDefinition"] == NEW_PART
    assert record["TitlePartDefinition"] == NEW_TITLE
    assert record["SightPartDefinition"] == INVISIBLE_PART

    saved = FakeStruct(UniqueId=int(weapon.DefinitionData.UniqueId), BalanceDefinition=None,
                       BarrelPartDefinition=None, TitlePartDefinition=None,
                       SightPartDefinition=None)
    args = FakeStruct(SaveGame=FakeStruct(SaveGameId=1, WeaponData=[
        FakeStruct(WeaponDefinitionData=saved)]))
    module.on_apply_save(graph.controller, args, None, None)
    assert saved.BalanceDefinition._path_name() == NEW_BALANCE
    assert saved.BarrelPartDefinition._path_name() == NEW_PART
    assert saved.TitlePartDefinition._path_name() == NEW_TITLE
    assert saved.SightPartDefinition._path_name() == INVISIBLE_PART
    assert not module._status[-1]["missing"]


def test_balance_spec_validation(spec_dict: dict[str, Any]) -> None:
    good = _own_balance_spec(spec_dict)
    Spec.from_dict(good)
    bad = json.loads(json.dumps(good))
    bad["balances"][0]["part_lists"]["NotAField"] = ["AR_Barrel_PL_Bent"]
    with pytest.raises(SpecError, match="NotAField"):
        Spec.from_dict(bad)
    bad = json.loads(json.dumps(good))
    bad["balances"][0]["part_lists"]["BarrelPartData"] = ["NoSuchPart"]
    with pytest.raises(SpecError, match="NoSuchPart"):
        Spec.from_dict(bad)
    bad = json.loads(json.dumps(good))
    bad["balances"][0]["title"]["on_parts"] = []
    with pytest.raises(SpecError, match="on_parts"):
        Spec.from_dict(bad)
    bad = json.loads(json.dumps(good))
    del bad["balances"][0]["part_lists"]["SightPartData"]  # the sight is now in no list at all
    with pytest.raises(SpecError, match="F5"):
        Spec.from_dict(bad)
    bad = json.loads(json.dumps(good))
    bad["parts"][0]["overrides"]["weapon_attribute_effects"][0]["modifier"] = "MT_Bogus"
    with pytest.raises(SpecError, match="MT_Bogus"):
        Spec.from_dict(bad)


def test_balance_that_shadows_a_cooked_one_is_refused(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any]
) -> None:
    bad = _own_balance_spec(spec_dict)
    bad["balances"][0]["name"] = "AR_Dahl_5_Veruc"  # a real legendary's path
    with pytest.raises(Exception) as excinfo:
        emit(Spec.from_dict(bad), tmp_path / "shadow", catalog=catalog)
    assert "already exists" in str(excinfo.value)


# ------------------------------------------------------------------ M7: first-person capture
def test_capture_view_sets_the_camera_explicitly_and_equip_picks_the_weapon(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """F22: a short weapon held close needs a first-person capture; the harness then sets
    the view explicitly (F12 may have left it either way) and can equip the A/B stock grant
    instead of ours, which is how the stock baseline is captured in the same pose."""
    spec_dict = _multipart_spec(spec_dict)
    spec_dict["options"].update(harness_capture_view="first", harness_equip="second_newest")
    result = emit(Spec.from_dict(spec_dict), tmp_path / "fp", catalog=catalog)
    source = result.mod_file.read_text(encoding="utf-8")
    assert 'HARNESS_CAPTURE_VIEW = "first"' in source
    emitted_spec = json.loads((result.out_dir / "spec.json").read_text(encoding="utf-8"))
    assert emitted_spec["options"]["harness_capture_view"] == "first"
    assert emitted_spec["options"]["harness_equip"] == "second_newest"

    module = load_mod(result.out_dir, graph)
    module.menu_setup()
    phases = _run_phases(module, graph)
    assert graph.console[-2:] == ["FOV 100", "SetBehindView(False)"]
    assert phases[3]["camera"] == "set (first person)"
    # and the view is HELD through the capture window: an F12 (Steam screenshots) once
    # flipped a first-person run back to third person two seconds after phase 3
    assert module.hold_tick.enabled
    module.kb_toggle_view()
    assert graph.console[-1] == "SetBehindView(True)"
    for _ in range(31):
        module.hold_tick(None, None, None, None)
    assert graph.console[-1] == "SetBehindView(False)", "re-asserted within 30 ticks"
    for _ in range(module.HARNESS_VIEW_HOLD_TICKS):
        module.hold_tick(None, None, None, None)
    assert not module.hold_tick.enabled
    # the held weapon is the A/B stock one, but ours was still granted and is on the pawn
    assert phases[3]["held"]["BarrelPartDefinition"] == TEMPLATE_PART
    assert phases[2]["new_weapon"]["BarrelPartDefinition"] == NEW_PART

    # third person, explicitly
    spec_dict["options"].update(harness_capture_view="third", harness_equip="newest")
    result = emit(Spec.from_dict(spec_dict), tmp_path / "tp", catalog=catalog)
    graph2 = build_graph(catalog)
    module = load_mod(result.out_dir, graph2)
    module.menu_setup()
    phases = _run_phases(module, graph2)
    assert graph2.console[-1] == "SetBehindView(True)"
    assert phases[3]["held"]["BarrelPartDefinition"] == NEW_PART

    # and the M2 behaviour when the option is absent: behindview alone decides
    spec_dict["options"].pop("harness_capture_view")
    spec_dict["options"]["harness_behindview"] = False
    result = emit(Spec.from_dict(spec_dict), tmp_path / "legacy", catalog=catalog)
    assert "HARNESS_CAPTURE_VIEW" not in result.mod_file.read_text(encoding="utf-8")
    graph3 = build_graph(catalog)
    module = load_mod(result.out_dir, graph3)
    module.menu_setup()
    _run_phases(module, graph3)
    assert graph3.console[-1] == "FOV 100", "no SetBehindView call at all"


def test_capture_view_and_equip_are_validated(spec_dict: dict[str, Any]) -> None:
    spec_dict["options"]["harness_capture_view"] = "sideways"
    with pytest.raises(SpecError, match="harness_capture_view"):
        Spec.from_dict(spec_dict)
    spec_dict["options"]["harness_capture_view"] = "first"
    spec_dict["options"]["harness_equip"] = "oldest"
    with pytest.raises(SpecError, match="harness_equip"):
        Spec.from_dict(spec_dict)


# ------------------------------------------------------------------ M7: own material + texture
VLADOF_MIC = "Common_GunMaterials.Materials.AssaultRifle.Mati_VladofLegendary"
MATERIAL_TEMPLATE = "GD_Weap_AssaultRifle.ManufacturerMaterials.Mat_Vladof_5_Legendary"
NEW_MIC = "Common_GunMaterials.Materials.AssaultRifle.Mati_PL_Multi"
NEW_MATERIAL_PART = "GD_Weap_AssaultRifle.ManufacturerMaterials.Mat_PL_Multi"


def _textured_spec(spec_dict: dict[str, Any]) -> dict[str, Any]:
    spec_dict = _own_balance_spec(spec_dict)
    spec_dict["extra_packages"] = [{"name": "PipelineTextures", "file": None}]
    spec_dict["materials"] = [{
        "name": "Mati_PL_Multi", "outer": "Common_GunMaterials.Materials.AssaultRifle",
        "parent": VLADOF_MIC,
        "texture_parameters": {"p_Diffuse": "PipelineTextures.AK47_Albedo",
                               "p_Masks": "PipelineTextures.Mask_White"},
        "vector_parameters": {"p_AColorHilight": [1.0, 1.0, 1.0, 1.0]},
        "scalar_parameters": {"p_ReplaceDecal": 0.0},
    }]
    spec_dict["parts"].append({
        "part_name": "Mat_PL_Multi", "slot": "WP_Material", "fragment": None,
        "outer": "GD_Weap_AssaultRifle.ManufacturerMaterials", "template_part": MATERIAL_TEMPLATE,
        "register_in": [],
        "overrides": {"object_properties": {"Material": NEW_MIC}},
    })
    spec_dict["balances"][0]["part_lists"]["MaterialPartData"] = ["Mat_PL_Multi"]
    return spec_dict


def test_runtime_material_gets_its_own_texture(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """M7 task 4: a loose texture package is loaded at the menu, a MIC is built on the
    Vladof legendary one with our textures, and a material part points at it."""
    result = emit(Spec.from_dict(_textured_spec(spec_dict)), tmp_path / "tex", catalog=catalog)
    assert result.lint_ok, result.lint_errors
    lint = json.loads((result.out_dir / "lint.json").read_text(encoding="utf-8"))
    first = lint["results"][0]["proposal"]
    assert first["extra_packages"] == ["PipelineTextures"]
    assert first["new_materials"][0]["path"] == NEW_MIC
    assert any("extra package PipelineTextures" in n for n in result.notes)

    module = load_mod(result.out_dir, graph)
    record = module.menu_setup()
    assert record["ok"], record.get("error")
    assert "PipelineTextures" in graph.world.loaded_packages
    assert record["extra_packages"] == ["PipelineTextures"]
    mat_record = record["materials"][0]
    assert mat_record["constructed"] and mat_record["parent"] == VLADOF_MIC
    assert mat_record["textures"] == ["p_Diffuse=PipelineTextures.AK47_Albedo",
                                      "p_Masks=PipelineTextures.Mask_White"]

    mic = unrealsdk.find_object("MaterialInstanceConstant", NEW_MIC)
    albedo = unrealsdk.find_object("Texture2D", "PipelineTextures.AK47_Albedo")
    assert mic.Parent._path_name() == VLADOF_MIC
    assert mic.TextureParameterValues["p_Diffuse"] is albedo
    assert albedo.ObjectFlags & KEEP_ALIVE_FLAG and mic.ObjectFlags & KEEP_ALIVE_FLAG
    colour = mic.VectorParameterValues["p_AColorHilight"]
    assert (colour.R, colour.G, colour.B, colour.A) == (1.0, 1.0, 1.0, 1.0)
    assert mic.ScalarParameterValues["p_ReplaceDecal"] == 0.0

    part = unrealsdk.find_object("WeaponPartDefinition", NEW_MATERIAL_PART)
    assert part.Material is mic and part.bIsGestaltMode is False
    template = unrealsdk.find_object("WeaponPartDefinition", MATERIAL_TEMPLATE)
    assert template.Material._path_name() == VLADOF_MIC, "the stock part is untouched"
    balance = unrealsdk.find_object("WeaponBalanceDefinition", NEW_BALANCE)
    assert [e.Part._path_name() for e in balance.RuntimePartListCollection.MaterialPartData.WeightedParts] == [NEW_MATERIAL_PART]

    # the harness grants it: the held weapon wears our material part
    phases = _run_phases(module, graph)
    assert phases[2]["new_weapon"]["MaterialPartDefinition"] == NEW_MATERIAL_PART
    assert phases[1]["stock_weapon"]["MaterialPartDefinition"] == MATERIAL_TEMPLATE


def test_extra_package_and_material_spec_validation(spec_dict: dict[str, Any]) -> None:
    good = _textured_spec(spec_dict)
    Spec.from_dict(good)
    bad = json.loads(json.dumps(good))
    bad["extra_packages"].append({"name": "PipelineMeshes"})
    with pytest.raises(SpecError, match="own package"):
        Spec.from_dict(bad)
    bad = json.loads(json.dumps(good))
    bad["materials"][0]["vector_parameters"]["p_X"] = [1.0, 1.0]
    with pytest.raises(SpecError, match="r, g, b, a"):
        Spec.from_dict(bad)
    bad = json.loads(json.dumps(good))
    bad["parts"][-1]["overrides"]["object_properties"] = {"Material": 5}
    with pytest.raises(SpecError, match="object_properties"):
        Spec.from_dict(bad)


def test_install_copies_extra_packages(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any]
) -> None:
    spec_dict = _textured_spec(spec_dict)
    spec_dict["package_file"] = "PipelineMeshes.upk"
    spec_dict["extra_packages"] = [{"name": "PipelineTextures", "file": "PipelineTextures.upk"}]
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    (spec_dir / "PipelineMeshes.upk").write_bytes(b"mesh bytes")
    (spec_dir / "PipelineTextures.upk").write_bytes(b"texture bytes")
    spec_path = spec_dir / "spec.json"
    spec_path.write_text(json.dumps(spec_dict), encoding="utf-8")
    result = emit(load_spec(spec_path), tmp_path / "mod", catalog=catalog)
    game = tmp_path / "game"
    (game / "sdk_mods").mkdir(parents=True)
    (game / "WillowGame" / "CookedPCConsole").mkdir(parents=True)
    installed = install(result, game, catalog=catalog)
    dest = game / "WillowGame" / "CookedPCConsole" / "PipelineTextures.upk"
    assert dest.read_bytes() == b"texture bytes" and dest in installed.files
    # a name in the exe's SHA1 table is refused even as an extra package (F12)
    spec_dict["extra_packages"] = [{"name": "Startup", "file": "PipelineTextures.upk"}]
    spec_path.write_text(json.dumps(spec_dict), encoding="utf-8")
    result = emit(load_spec(spec_path), tmp_path / "mod2", catalog=catalog, force=True)
    with pytest.raises(InstallRefused, match="SHA1"):
        install(result, game, catalog=catalog)


# ------------------------------------------------------------------ M7: aim-down-sights check
def test_harness_pose_ads_records_camera_and_socket_geometry(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """``harness_pose: ads``: phase 3 zooms, phase 5 records the camera POV and every socket
    of ours on the held weapon's first-person mesh, and only then writes ``done``."""
    spec_dict["fragments"][0]["socket_overrides"] = {"EyeSocket2": [0.0, 40.0, 11.5]}
    spec_dict["options"].update(test_harness=True, harness_auto=True, harness_pose="ads",
                                harness_capture_view="first")
    result = emit(Spec.from_dict(spec_dict), tmp_path / "ads", catalog=catalog)
    module = load_mod(result.out_dir, graph)
    module.menu_setup()
    module.on_map_loaded(graph.controller, None, None, None)
    ticks = 0
    for _ in range(5000):
        if not module.seq_tick.enabled:
            break
        module.seq_tick(None, None, None, None)
        ticks += 1
    assert not module.seq_tick.enabled
    records = [r for r in module._status if isinstance(r.get("phase"), int)]
    assert [r["phase"] for r in records] == [0, 1, 2, 3, 5]
    assert records[3]["zoom"] == "StartAltFire" and "done" not in records[3]
    assert "StartAltFire" in graph.console
    last = records[4]
    assert last["done"] is True
    check = last["sight_check"]
    assert check["zoomed"] is False and check["ironsights_socket"] == "EyeSocket2"
    assert check["zoom_socket"]["location"] == [100.0, 240.0, 61.5]
    assert check["camera"]["fov"] == 45.0 and check["camera"]["rotation"] == [0, 16384, 0]
    assert check["fire_start"] == [1.0, 2.0, 3.0]
    eye = check["sockets"][f"{NEW_FRAGMENT}_EyeSocket2"]
    assert eye["location"] == [100.0, 240.0, 61.5], "world = origin + the override"
    assert all(name.startswith(NEW_FRAGMENT + "_") for name in check["sockets"])

    # the idle pose is untouched: four phases, done on phase 3
    spec_dict["options"]["harness_pose"] = "idle"
    result = emit(Spec.from_dict(spec_dict), tmp_path / "idle", catalog=catalog)
    assert "_sight_check" not in result.mod_file.read_text(encoding="utf-8")
    spec_dict["options"]["harness_pose"] = "prone"
    with pytest.raises(SpecError, match="harness_pose"):
        Spec.from_dict(spec_dict)


def test_zoom_effect_overrides_replace_the_zoom_arrays(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """M8: ``zoom_*_attribute_effects`` edit the arrays that apply only while zooming;
    an empty list clears one (the stock "slow while aiming" effect), and a row whose array
    is empty on the template still gets a prototype from a sibling zoom array."""
    spec_dict = _with_invisible_sight(spec_dict)
    spec_dict["parts"][1]["overrides"] = {
        "weapon_attribute_effects": [
            {"attribute": "D_Attributes.Weapon.WeaponDamage", "modifier": "MT_Scale", "value": 1.0}],
        "zoom_weapon_attribute_effects": [
            {"attribute": "D_Attributes.Weapon.WeaponDamage", "modifier": "MT_Scale", "value": -0.5}],
        "zoom_external_attribute_effects": [],
    }
    result = emit(Spec.from_dict(spec_dict), tmp_path / "zoom", catalog=catalog)
    module = load_mod(result.out_dir, graph)
    record = module.menu_setup()
    sight = next(p for p in record["parts"] if p["part"] == INVISIBLE_PART)
    assert sight["overrides"]["ZoomExternalAttributeEffects"] == []
    part = unrealsdk.find_object("WeaponPartDefinition", INVISIBLE_PART)
    assert len(part.ZoomExternalAttributeEffects) == 0
    assert [(e.ModifierType, e.BaseModifierValue.BaseValueConstant)
            for e in part.ZoomWeaponAttributeEffects] == [(0, -0.5)]
    # WeaponAttributeEffects was empty on the template: the prototype came from a zoom array
    assert [e.BaseModifierValue.BaseValueConstant for e in part.WeaponAttributeEffects] == [1.0]
    template = unrealsdk.find_object("WeaponPartDefinition", SIGHT_NONE)
    assert len(template.ZoomExternalAttributeEffects) == 1, "the template keeps its rows"


def test_harness_timing_seconds_waits_on_the_wall_clock(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """M8: tick-counted waits scale with the frame rate; ``harness_timing: seconds`` makes
    the phase machine wait real seconds, driven here by a fake clock."""
    spec_dict = _multipart_spec(spec_dict)
    spec_dict["options"].update(harness_timing="seconds", harness_capture_view="first",
                                harness_pose="ads")
    result = emit(Spec.from_dict(spec_dict), tmp_path / "wall", catalog=catalog)
    source = result.mod_file.read_text(encoding="utf-8")
    assert "HARNESS_PHASE_SECONDS = (1.5, 1.5, 2.0)" in source and "_wait" not in source
    module = load_mod(result.out_dir, graph)
    module.menu_setup()
    clock = [1000.0]
    module.time = type("clock", (), {"time": staticmethod(lambda: clock[0]),
                                     "strftime": __import__("time").strftime})()
    module.on_map_loaded(graph.controller, None, None, None)
    fired: list[tuple[int, float]] = []
    for _ in range(200):
        if not module.seq_tick.enabled:
            break
        before = len(module._status)
        module.seq_tick(None, None, None, None)
        if len(module._status) > before and isinstance(module._status[-1].get("phase"), int):
            fired.append((module._status[-1]["phase"], clock[0]))
        clock[0] += 0.1
    assert [p for p, _ in fired] == [0, 1, 2, 3, 5]
    gaps = [round(b - a, 1) for (_, a), (_, b) in zip(fired, fired[1:])]
    assert gaps == [1.5, 1.5, 2.0, 1.0], gaps
    assert fired[0][1] - 1000.0 == pytest.approx(2.0, abs=0.11)
    assert module.hold_tick.enabled
    clock[0] += 31.0
    module.hold_tick(None, None, None, None)
    assert not module.hold_tick.enabled

    spec_dict["options"]["harness_timing"] = "frames"
    with pytest.raises(SpecError, match="harness_timing"):
        Spec.from_dict(spec_dict)


def test_constructed_parts_drop_the_templates_additional_fragments(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """M8: AR_Body_Vladof_4 draws Var1/Var2 beside its own fragment; a clone must not."""
    result = emit(Spec.from_dict(_multipart_spec(spec_dict)), tmp_path / "add", catalog=catalog)
    module = load_mod(result.out_dir, graph)
    record = module.menu_setup()
    body = next(p for p in record["parts"] if p["part"].endswith("AR_Body_PL_Multi"))
    assert body["additional_cleared"] == ["AR_Body_Vladof_Var1", "AR_Body_Vladof_Var2"]
    part = unrealsdk.find_object("WeaponPartDefinition", "GD_Weap_AssaultRifle.Body.AR_Body_PL_Multi")
    assert list(part.AdditionalGestaltModeSkeletalMeshNames) == ["None", "None"]
    template = unrealsdk.find_object("WeaponPartDefinition", "GD_Weap_AssaultRifle.Body.AR_Body_Vladof_4")
    assert list(template.AdditionalGestaltModeSkeletalMeshNames) == ["AR_Body_Vladof_Var1", "AR_Body_Vladof_Var2"]


def test_bounds_override_replaces_the_templates_box(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """M8: the item-card preview frames the weapon from the fragments' reference-pose bounds;
    a fragment of a different shape than its template needs its own box, and dz must not
    be added on top of it."""
    spec_dict["fragments"][0]["bounds_override"] = {
        "origin": [0.0, -51.1, 8.1], "extent": [0.9, 6.7, 3.7], "radius": 7.76}
    result = emit(Spec.from_dict(spec_dict), tmp_path / "bounds", catalog=catalog)
    assert any("bounds_override" in n for n in result.notes)
    module = load_mod(result.out_dir, graph)
    record = module.menu_setup()
    frag = record["fragments"][0]
    assert frag["bounds_override"]["radius"] == 7.76
    row = next(b for b in graph.gestalt.GestaltPartBounds
               if str(b.SkeletalMeshFragmentName) == NEW_FRAGMENT)
    box = row.ReferencePoseBounds
    assert (box.Origin.X, box.Origin.Y, box.Origin.Z) == (0.0, -51.1, 8.1)
    assert (box.BoxExtent.X, box.BoxExtent.Y, box.BoxExtent.Z) == (0.9, 6.7, 3.7)
    assert box.SphereRadius == 7.76, "dz (8.0 in this spec) is not added on top"
    # the template's own row is untouched
    template_row = next(b for b in graph.gestalt.GestaltPartBounds
                        if str(b.SkeletalMeshFragmentName) == "AR_Barrel_Vladof")
    assert template_row.ReferencePoseBounds.SphereRadius != 7.76
    bad = json.loads(json.dumps(spec_dict))
    bad["fragments"][0]["bounds_override"] = {"origin": [0, 0, 0], "extent": [1, 1]}
    with pytest.raises(SpecError, match="bounds_override"):
        Spec.from_dict(bad)


def test_clear_arrays_empties_the_clones_prefix_list(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """M8: the Vladof grip carries seven manufacturer prefixes ("Angry AK-47"); a clone
    with ``clear_arrays: ["PrefixList"]`` carries none, and the template keeps its seven."""
    spec_dict = _multipart_spec(spec_dict)
    grip = next(p for p in spec_dict["parts"] if p["part_name"] == "AR_Grip_PL_Multi")
    grip["overrides"] = {"clear_arrays": ["PrefixList"]}
    result = emit(Spec.from_dict(spec_dict), tmp_path / "prefix", catalog=catalog)
    assert any("PrefixList emptied" in n for n in result.notes)
    module = load_mod(result.out_dir, graph)
    record = module.menu_setup()
    grip_record = next(p for p in record["parts"] if p["part"].endswith("AR_Grip_PL_Multi"))
    assert grip_record["overrides"]["PrefixList"] == "cleared 7 entries"
    part = unrealsdk.find_object("WeaponPartDefinition", "GD_Weap_AssaultRifle.Grip.AR_Grip_PL_Multi")
    assert len(part.PrefixList) == 0
    template = unrealsdk.find_object("WeaponPartDefinition", "GD_Weap_AssaultRifle.Grip.AR_Grip_Vladof")
    assert len(template.PrefixList) == 7


def test_suppress_prefix_nulls_the_prefix_slot_for_our_balance_only(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """M8: the weapon type's fallback prefix ("Assault") survives cleared PrefixLists and
    bNameIsUnique; the name hooks empty the slot on our weapons and leave others alone."""
    spec_dict = _own_balance_spec(spec_dict)
    spec_dict["balances"][0]["suppress_prefix"] = True
    result = emit(Spec.from_dict(spec_dict), tmp_path / "noprefix", catalog=catalog)
    source = result.mod_file.read_text(encoding="utf-8")
    assert "on_weapon_name_parts" in source and "on_weapon_init.enable()" in source
    assert any("prefix suppressed" in n for n in result.notes)
    module = load_mod(result.out_dir, graph)
    module.menu_setup()
    assert module.on_weapon_init.enabled and module.on_weapon_name_parts.enabled
    balance = unrealsdk.find_object("WeaponBalanceDefinition", NEW_BALANCE)
    prefix = FakeObject("WeaponNamePartDefinition", "Prefix_Assault", None)
    ours = FakeObject("WillowWeapon", "W_ours", None, DefinitionData=FakeStruct(
        BalanceDefinition=balance, PrefixPartDefinition=prefix))
    theirs = FakeObject("WillowWeapon", "W_theirs", None, DefinitionData=FakeStruct(
        BalanceDefinition=graph.balance, PrefixPartDefinition=prefix))
    module.on_weapon_name_parts(ours, None, None, None)
    module.on_weapon_init(theirs, None, None, None)
    assert ours.DefinitionData.PrefixPartDefinition is None
    assert theirs.DefinitionData.PrefixPartDefinition is prefix
    assert module._status[-1]["prefix_hook"] == "ChooseRandomNameParts"
    # a second pass over an already-cleared weapon is a no-op (no record)
    n = len(module._status)
    module.on_weapon_init(ours, None, None, None)
    assert len(module._status) == n


def test_harness_keys_and_mission_are_options(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    spec_dict["options"].update(
        test_harness=True, harness_keys=["F5", "F6", "F7", "F8"],
        harness_mission="GD_Episode01.M_Ep1_Champion",
    )
    result = emit(Spec.from_dict(spec_dict), tmp_path / "keys", catalog=catalog)
    source = result.mod_file.read_text(encoding="utf-8")
    assert 'HARNESS_MISSION = "GD_Episode01.M_Ep1_Champion"' in source
    load_mod(result.out_dir, graph)
    assert [kb.key for kb in mods_base.built_mods[-1].keybinds] == ["F5", "F6", "F7", "F8"]
    assert Spec.from_dict(spec_dict).options.to_dict()["harness_keys"] == ["F5", "F6", "F7", "F8"]
    spec_dict["options"]["harness_keys"] = ["F5", "F5", "F7", "F8"]
    with pytest.raises(SpecError):
        Spec.from_dict(spec_dict).validate()
    spec_dict["options"].update(harness_keys=["F5", "F6", "F7", "F8"], harness_mission="Nope")
    with pytest.raises(SpecError):
        Spec.from_dict(spec_dict).validate()


def test_two_mods_hijacking_one_mission_restore_the_pristine_reward(
    tmp_path: Path, catalog: dict[str, Any], spec_dict: dict[str, Any], graph: Any
) -> None:
    """F26: mod B must not snapshot mod A's hijack as the original, and the last restore
    to land must leave the mission exactly as it was before either grant."""
    sys.modules.pop("_bl2_pipeline_shared", None)
    spec_dict["options"].update(test_harness=True, harness_auto=False)
    result_a = emit(Spec.from_dict(spec_dict), tmp_path / "a", catalog=catalog)
    spec_dict["mod"]["name"] = "PipelineOther"
    spec_dict["options"]["harness_keys"] = ["F5", "F6", "F7", "F8"]
    result_b = emit(Spec.from_dict(spec_dict), tmp_path / "b", catalog=catalog)
    mod_a = load_mod(result_a.out_dir, graph)
    mod_b = load_mod(result_b.out_dir, graph)
    mission = unrealsdk.find_object("MissionDefinition", mod_a.HARNESS_MISSION)
    pristine_stage = mission.GameStage
    assert list(mission.Reward.RewardItems) == []

    mod_a.grant()
    hijacked = list(mission.Reward.RewardItems)
    assert hijacked and hijacked[0]._path_name() == mod_a.HARNESS_BALANCES[0]
    mod_b.grant()
    shared = mod_a._reward_backups()
    assert shared is mod_b._reward_backups(), "one registry for every pipeline mod"
    assert list(shared[mod_a.HARNESS_MISSION]["reward_items"]) == [], (
        "mod B must find mod A's pristine snapshot, not take its own of the hijack"
    )
    # restores land in either order; the mission ends pristine and the second is a no-op
    mod_b._restore_reward()
    assert list(mission.Reward.RewardItems) == [] and mission.GameStage == pristine_stage
    mission.GameStage = 99  # a later change must not be clobbered by a stale restore
    mod_a._restore_reward()
    assert mission.GameStage == 99 and mod_a.HARNESS_MISSION not in shared
    # and a double press within one mod is still a single snapshot
    mod_a.grant()
    mod_a.grant()
    mod_a._restore_reward()
    assert list(mission.Reward.RewardItems) == [] and mission.GameStage == 99
