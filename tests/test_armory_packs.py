"""The Armory runtime (``armory/Armory``) and weapon packs (``bl2_partgen.pack``), offline.

What it pins:

* every spec in specs/ renders the SAME component text after a trip through a pack
  (resolve -> JSON -> rebuild), so a pack behaves exactly like the verified component
* the release carries the renderer byte-identical to src/bl2_partgen (no drift, F28)
* two packs (Boxgun-derived GUNA and GUNB, own gestalts) installed into a fake game folder
  load as their own modules, register at the menu, spawn, census, and keep their save
  records in sdk_mods/_pipeline_saves/<package>/ and their status in Armory/logs/
* a bad pack is refused whole, with the reason, and never stops the other: schema or Armory
  version too new, an unknown feature, a missing or different .upk, a name that could escape
  into code, a harness build, a duplicate pack, a package another installed mod registers
* a pack that carries its .upk in packages/ gets it copied into CookedPCConsole
* the pack builder refuses harness specs, bad ids and missing packages, and strips local paths

    python -m pytest tests/test_armory_packs.py -q
"""

from __future__ import annotations

import filecmp
import importlib.util
import itertools
import json
import shutil
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests" / "fakes"))

import mods_base  # noqa: E402  (the fake)
import unrealsdk  # noqa: E402  (the fake)

from bl2.scaffold import scaffold  # noqa: E402
from bl2_catalog import load_catalog  # noqa: E402
from bl2_partgen.pack import (  # noqa: E402
    RENDER_MODULES,
    PackManifestError,
    build_armory_release,
    build_pack,
    install_tree,
)
from bl2_partgen.packdata import (  # noqa: E402
    PACK_FILE,
    load_weapon,
    render_weapon,
    resolved_from_dict,
    resolved_to_dict,
)
from bl2_partgen.resolve import resolve  # noqa: E402
from bl2_partgen.spec import Spec, is_armory, load_spec  # noqa: E402

BOXGUN = REPO / "specs" / "boxgun.json"
RUNTIME = REPO / "armory" / "Armory"
_counter = itertools.count()


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def catalog() -> dict[str, Any]:
    path = REPO / "catalog" / "parts.json"
    if not path.exists():
        pytest.skip("catalog/parts.json absent; run `python -m bl2_catalog`")
    return load_catalog(path)


def _weapon_spec(name: str) -> dict[str, Any]:
    """The Boxgun renamed to ``name``: its own package, parts, balance and title."""
    data = json.loads(BOXGUN.read_text(encoding="utf-8"))
    data["mod"]["name"] = f"Pipeline{name}"
    data["package"] = f"PipelineMeshes{name}"
    data["mesh_path"] = f"PipelineMeshes{name}.PL_AR_Gestalt_Mesh"
    data["package_file"] = f"../upk/PipelineMeshes{name}.upk"
    data["options"]["own_gestalt"] = True
    data["options"].pop("save_package", None)
    for part in data["parts"]:
        part["part_name"] = part["part_name"].replace("BOXGUN", name)
        if part.get("fragment"):
            part["fragment"] = part["fragment"].replace("BOXGUN", name)
    for frag in data["fragments"]:
        frag["name"] = frag["name"].replace("BOXGUN", name)
    for bal in data["balances"]:
        bal["name"] = bal["name"].replace("BOXGUN", name)
        bal["part_lists"] = {k: [v.replace("BOXGUN", name) for v in vs]
                             for k, vs in bal["part_lists"].items()}
        bal["title"]["name"] = bal["title"]["name"].replace("BOXGUN", name)
        bal["title"]["part_name"] = f"Gun {name}"
        bal["title"]["on_parts"] = [p.replace("BOXGUN", name) for p in bal["title"]["on_parts"]]
    return data


def _source_tree(root: Path) -> dict[str, dict[str, Any]]:
    """specs/, upk/ (dummy packages) and packs/ manifests for GUNA and GUNB."""
    (root / "specs").mkdir(parents=True)
    (root / "upk").mkdir()
    (root / "packs").mkdir()
    specs = {}
    for name in ("GUNA", "GUNB"):
        spec = _weapon_spec(name)
        specs[name] = spec
        (root / "specs" / f"{name.lower()}.json").write_text(json.dumps(spec), encoding="utf-8")
        (root / "upk" / f"PipelineMeshes{name}.upk").write_bytes(f"upk {name}".encode() * 64)
        (root / "packs" / f"{name.lower()}.json").write_text(json.dumps({
            "id": name.lower(), "name": f"Pack {name}", "version": "1.0.0", "author": "tests",
            "license": "CC0-1.0", "description": f"test pack {name}",
            "weapons": [{"id": name.lower(), "label": f"Gun {name}",
                         "spec": f"../specs/{name.lower()}.json"}],
        }), encoding="utf-8")
    return specs


@pytest.fixture()
def installed(tmp_path: Path, catalog: dict[str, Any]) -> dict[str, Any]:
    """A fake game folder with the Armory release and both packs extracted into it."""
    src = tmp_path / "src"
    specs = _source_tree(src)
    builds = [build_pack(src / "packs" / f"{n}.json", tmp_path / "dist" / "packs", catalog=catalog)
              for n in ("guna", "gunb")]
    stage, _zip = build_armory_release(tmp_path / "dist", runtime_dir=RUNTIME, packs=builds,
                                       make_zip=False)
    game = tmp_path / "game"
    install_tree(stage, game)
    return {"game": game, "specs": specs, "builds": builds, "stage": stage}


def load_runtime(game: Path, catalog: dict[str, Any], specs: dict[str, dict[str, Any]]) -> ModuleType:
    """Import the installed Armory as a package, in a fresh fake world holding both packages."""
    from bl2_preflight.augment import augment_world
    from tests.fakes.graph import build_graph

    graph = build_graph(catalog)
    for spec in specs.values():
        augment_world(graph.world, catalog, spec)
    unrealsdk.set_world(graph.world)
    mods_base.set_pc(graph.controller)
    mods_base.built_mods.clear()
    sys.modules.pop("_bl2_pipeline_shared", None)
    mod_dir = game / "sdk_mods" / "Armory"
    name = f"armory_rt_{next(_counter)}"
    spec = importlib.util.spec_from_file_location(
        name, mod_dir / "__init__.py", submodule_search_locations=[str(mod_dir)])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    module._graph = graph  # type: ignore[attr-defined]
    return module


def _pack_json(game: Path, pack_id: str) -> Path:
    return game / "sdk_mods" / "ArmoryPacks" / pack_id / PACK_FILE


def _edit_pack(game: Path, pack_id: str, edit: Any) -> None:
    path = _pack_json(game, pack_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(json.dumps(data), encoding="utf-8")


# ------------------------------------------------------------------ the pack format
def test_every_spec_renders_identically_through_a_pack(catalog: dict[str, Any]) -> None:
    checked = 0
    for path in sorted((REPO / "specs").glob("*.json")):
        if is_armory(json.loads(path.read_text(encoding="utf-8"))):
            continue
        spec = load_spec(path)
        resolved = resolve(spec, catalog)
        blob = json.loads(json.dumps({"spec": spec.to_dict(), "resolved": resolved_to_dict(resolved)}))
        again = resolved_from_dict(Spec.from_dict(blob["spec"]), blob["resolved"])
        assert render_weapon(again, "x") == render_weapon(resolved, "x"), path.name
        checked += 1
    assert checked >= 5


def test_release_carries_the_renderer_unchanged(installed: dict[str, Any]) -> None:
    render = installed["stage"] / "sdk_mods" / "Armory" / "_render"
    for name in RENDER_MODULES:
        assert filecmp.cmp(render / name, REPO / "src" / "bl2_partgen" / name, shallow=False), name
    assert (render / "__init__.py").exists()
    assert not (installed["stage"] / "sdk_mods" / "Armory" / "logs").exists()


def test_pack_zip_mirrors_the_game_folder(tmp_path: Path, catalog: dict[str, Any]) -> None:
    src = tmp_path / "src"
    _source_tree(src)
    build = build_pack(src / "packs" / "guna.json", tmp_path / "dist", catalog=catalog)
    assert build.zip_path is not None
    names = set(zipfile.ZipFile(build.zip_path).namelist())
    assert {"sdk_mods/ArmoryPacks/guna/armory_pack.json", "sdk_mods/ArmoryPacks/guna/README.md",
            "sdk_mods/ArmoryPacks/guna/LICENSE.txt",
            "WillowGame/CookedPCConsole/PipelineMeshesGUNA.upk"} <= names
    pack = build.manifest
    assert pack["packages"][0]["sha1"] and pack["packages"][0]["bytes"] > 0
    spec = pack["weapons"][0]["spec"]
    assert spec["package_file"] is None and spec["options"]["status_path"] is None, "no local paths"
    assert "CC0" in (build.pack_dir / "LICENSE.txt").read_text(encoding="utf-8") or \
        "creativecommons" in (build.pack_dir / "LICENSE.txt").read_text(encoding="utf-8")


def test_builder_refuses_harness_specs_bad_ids_and_missing_packages(
    tmp_path: Path, catalog: dict[str, Any]
) -> None:
    src = tmp_path / "src"
    _source_tree(src)
    manifest = src / "packs" / "guna.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))

    (src / "upk" / "PipelineMeshesGUNA.upk").rename(src / "upk" / "moved.upk")
    with pytest.raises(PackManifestError, match="not found"):
        build_pack(manifest, tmp_path / "d1", catalog=catalog)
    assert build_pack(manifest, tmp_path / "d2", catalog=catalog, with_packages=False)\
        .manifest["installable"] is False
    (src / "upk" / "moved.upk").rename(src / "upk" / "PipelineMeshesGUNA.upk")

    bad = dict(data, weapons=[dict(data["weapons"][0], id="Has Spaces")])
    manifest.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(PackManifestError, match="weapon id"):
        build_pack(manifest, tmp_path / "d3", catalog=catalog)

    manifest.write_text(json.dumps(data), encoding="utf-8")
    spec_path = src / "specs" / "guna.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["options"]["test_harness"] = True
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(PackManifestError, match="test-harness"):
        build_pack(manifest, tmp_path / "d4", catalog=catalog)


# ------------------------------------------------------------------ the runtime
def test_packs_load_as_their_own_modules(installed: dict[str, Any], catalog: dict[str, Any]) -> None:
    module = load_runtime(installed["game"], catalog, installed["specs"])
    assert module.PROBLEMS == []
    assert [w["id"] for w in module.WEAPONS] == ["guna", "gunb"]
    assert [p["id"] for p in module.PACKS] == ["guna", "gunb"]
    names = [w["module"].__name__ for w in module.WEAPONS]
    assert names == [f"{module.__name__}.packs.guna.guna", f"{module.__name__}.packs.gunb.gunb"]
    assert all(sys.modules[n] is w["module"] for n, w in zip(names, module.WEAPONS))
    # a component's MOD_DIR is its pack folder (sounds live there)
    guna = module.WEAPONS[0]["module"]
    assert Path(guna.MOD_DIR) == installed["game"] / "sdk_mods" / "ArmoryPacks" / "guna"
    assert Path(guna.SAVE_DIR) == installed["game"] / "sdk_mods" / "_pipeline_saves" / "PipelineMeshesGUNA"
    # weapon hooks are on at import and NOT tied to the mod toggle (F18)
    hooks = [h for c in module.COMPONENTS for h in c["hooks"]]
    assert hooks and all(h.enabled for h in hooks)
    mod = mods_base.built_mods[-1]
    assert len(mods_base.built_mods) == 1 and mod.name == "Armory"
    assert mod.hooks == []
    assert [kb.key for kb in mod.keybinds] == ["F5"]
    assert mod.find_option("Weapon").choices == ["Gun GUNA", "Gun GUNB"]
    packs = mod.find_option("Packs")
    assert [c.identifier for c in packs.children] == ["Pack GUNA 1.0.0", "Pack GUNB 1.0.0"]
    rendered = installed["game"] / "sdk_mods" / "Armory" / "logs" / "rendered" / "guna.guna.py"
    assert rendered.exists() and "PACKAGE = \"PipelineMeshesGUNA\"" in rendered.read_text(encoding="utf-8")
    module.armory_cmd.run("packs")
    module.armory_cmd.run("list")


def test_packs_register_spawn_and_census(installed: dict[str, Any], catalog: dict[str, Any]) -> None:
    module = load_runtime(installed["game"], catalog, installed["specs"])
    graph = module._graph
    records = {w["id"]: w["module"].menu_setup() for w in module.WEAPONS}
    assert all(r["ok"] for r in records.values()), records
    reg = module.registration()
    assert reg["guna"]["ok"] and reg["gunb"]["ok"]
    for w in module.WEAPONS:
        balance = unrealsdk.find_object("WeaponBalanceDefinition", w["balance"])
        assert balance.ObjectFlags & 0x4000, "balance not rooted (F17)"

    mission = graph.mission
    module.armory_cmd.run("spawn guna --level 40")
    row = [r for r in module._status if r.get("spawn") == "guna"][-1]
    assert "error" not in row and row["level"] == 40
    assert row["granted"][0]["id"] == "guna"
    assert row["granted"][0]["BarrelPartDefinition"].endswith("GUNA_Barrel")
    module.armory_cmd.run("spawn gunb")
    row = [r for r in module._status if r.get("spawn") == "gunb"][-1]
    assert row["granted"][0]["balance"] == module.BY_ID["gunb"]["balance"]
    for _ in range(module.RESTORE_TICKS + 1):
        module.restore_tick(None, None, None, None)
    assert list(mission.Reward.RewardItems) == [], "reward table restored (F26)"
    assert [r["id"] for r in module.census()] == ["guna", "gunb"]

    # the menu button spawns the selected weapon
    option = module.weapon_option
    option.value = "Gun GUNB"
    mods_base.built_mods[-1].find_option("Spawn selected").press()
    assert [r for r in module._status if r.get("spawn")][-1]["spawn"] == "gunb"


def test_save_records_and_logs_land_in_the_game_folders(
    installed: dict[str, Any], catalog: dict[str, Any]
) -> None:
    game = installed["game"]
    module = load_runtime(game, catalog, installed["specs"])
    for w in module.WEAPONS:
        assert w["module"].menu_setup()["ok"]
    module.spawn("guna", equip=False)
    module.spawn("gunb", equip=False)
    for w in module.WEAPONS:
        w["module"].on_generate_save(module._graph.controller, None, None, None)
    saves = game / "sdk_mods" / "_pipeline_saves"
    guna = json.loads((saves / "PipelineMeshesGUNA" / "Save0001.sav.json").read_text(encoding="utf-8"))
    gunb = json.loads((saves / "PipelineMeshesGUNB" / "Save0001.sav.json").read_text(encoding="utf-8"))
    assert any("GUNA_Barrel" in v for rec in guna.values() for v in rec.values())
    assert not any("GUNB" in v for rec in guna.values() for v in rec.values())
    assert any("GUNB_Barrel" in v for rec in gunb.values() for v in rec.values())
    logs = game / "sdk_mods" / "Armory" / "logs"
    assert (logs / "PipelineGUNA.status.json").exists()
    assert (logs / "armory.status.json").exists()


REFUSALS = [
    ("schema", lambda d: d.update(schema=99), "schema 99"),
    ("armory_min", lambda d: d.update(armory_min="9.0.0"), "needs Armory 9.0.0"),
    ("feature", lambda d: d["weapons"][0]["features"].append("teleport"), "teleport"),
    ("harness", lambda d: d["weapons"][0]["spec"]["options"].update(test_harness=True), "test-harness"),
    ("injection", lambda d: d["weapons"][0]["resolved"]["parts"][0].update(
        part_path='GD.X"\nimport os\n#'), "part path"),
    ("damaged", lambda d: d["weapons"][0]["resolved"]["parts"].pop(), "damaged or hand-edited"),
    ("not a pack", lambda d: d.update(format="something"), "not an Armory pack"),
]


@pytest.mark.parametrize("what,edit,reason", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_a_bad_pack_is_refused_and_the_other_still_loads(
    installed: dict[str, Any], catalog: dict[str, Any], what: str, edit: Any, reason: str
) -> None:
    _edit_pack(installed["game"], "guna", edit)
    module = load_runtime(installed["game"], catalog, installed["specs"])
    assert [w["id"] for w in module.WEAPONS] == ["gunb"]
    assert len(module.PROBLEMS) == 1 and module.PROBLEMS[0]["folder"] == "guna"
    assert reason in module.PROBLEMS[0]["error"], module.PROBLEMS[0]["error"]
    packs = mods_base.built_mods[-1].find_option("Packs")
    assert any(c.identifier.startswith("NOT LOADED") for c in packs.children)
    assert not any(n.startswith(f"{module.__name__}.packs.guna") for n in sys.modules)


def test_missing_or_different_upk_is_refused(installed: dict[str, Any], catalog: dict[str, Any]) -> None:
    cooked = installed["game"] / "WillowGame" / "CookedPCConsole"
    (cooked / "PipelineMeshesGUNA.upk").write_bytes(b"a different build")
    (cooked / "PipelineMeshesGUNB.upk").unlink()
    module = load_runtime(installed["game"], catalog, installed["specs"])
    assert module.WEAPONS == []
    errors = {p["folder"]: p["error"] for p in module.PROBLEMS}
    assert "not the build this pack was made with" in errors["guna"]
    assert "missing from WillowGame" in errors["gunb"]
    # an empty Armory still builds its menu and does not crash on the spawn key
    assert module.weapon_option.choices == ["(no weapons installed)"]
    module.kb_spawn()


def test_upk_carried_in_the_pack_is_copied_into_cooked(
    installed: dict[str, Any], catalog: dict[str, Any]
) -> None:
    game = installed["game"]
    cooked = game / "WillowGame" / "CookedPCConsole" / "PipelineMeshesGUNA.upk"
    carried = game / "sdk_mods" / "ArmoryPacks" / "guna" / "packages" / "PipelineMeshesGUNA.upk"
    carried.parent.mkdir()
    shutil.move(str(cooked), str(carried))
    module = load_runtime(game, catalog, installed["specs"])
    assert cooked.exists() and filecmp.cmp(cooked, carried, shallow=False)
    assert any("copied" in n for n in module.PACKS[0]["notes"])


def test_package_registered_by_another_installed_mod_is_refused(
    installed: dict[str, Any], catalog: dict[str, Any]
) -> None:
    legacy = installed["game"] / "sdk_mods" / "PipelineArmory"
    (legacy / "weapons").mkdir(parents=True)
    (legacy / "__init__.py").write_text('"""old armory"""\n', encoding="utf-8")
    (legacy / "weapons" / "guna.py").write_text('MOD_NAME = "x"\nPACKAGE = "PipelineMeshesGUNA"\n',
                                                encoding="utf-8")
    module = load_runtime(installed["game"], catalog, installed["specs"])
    assert [w["id"] for w in module.WEAPONS] == ["gunb"]
    assert "sdk_mods\\PipelineArmory" in module.PROBLEMS[0]["error"]


def test_a_second_copy_of_a_pack_is_refused(installed: dict[str, Any], catalog: dict[str, Any]) -> None:
    packs = installed["game"] / "sdk_mods" / "ArmoryPacks"
    shutil.copytree(packs / "guna", packs / "guna (1)")
    module = load_runtime(installed["game"], catalog, installed["specs"])
    assert [w["id"] for w in module.WEAPONS] == ["guna", "gunb"]
    assert module.PROBLEMS[0]["folder"] == "guna (1)"
    assert "already loaded" in module.PROBLEMS[0]["error"]


def test_two_packs_claiming_one_balance_are_refused(
    installed: dict[str, Any], catalog: dict[str, Any]
) -> None:
    guna = json.loads(_pack_json(installed["game"], "guna").read_text(encoding="utf-8"))
    balance = guna["weapons"][0]["resolved"]["balances"][0]["path"]
    _edit_pack(installed["game"], "gunb",
               lambda d: d["weapons"][0]["resolved"]["balances"][0].update(path=balance))
    module = load_runtime(installed["game"], catalog, installed["specs"])
    assert [w["id"] for w in module.WEAPONS] == ["guna"]
    assert "already owned by pack 'guna'" in module.PROBLEMS[0]["error"]


def test_no_packs_folder_is_not_an_error(installed: dict[str, Any], catalog: dict[str, Any]) -> None:
    shutil.rmtree(installed["game"] / "sdk_mods" / "ArmoryPacks")
    module = load_runtime(installed["game"], catalog, installed["specs"])
    assert module.WEAPONS == [] and module.PROBLEMS == []
    module.armory_cmd.run("spawn guna")
    assert "unknown weapon" in module._status[-1]["error"]


def test_a_weapon_scaffolded_from_the_boxgun_shares_nothing_with_it(tmp_path: Path) -> None:
    """``bl2 new <id> --from boxgun`` is how a pack starts: its package, parts, balance and
    save-record folder must all be its own, or it would collide with the default pack."""
    (tmp_path / "recipes").mkdir()
    (tmp_path / "specs").mkdir()
    shutil.copy2(REPO / "recipes" / "boxgun.json", tmp_path / "recipes")
    for path in (REPO / "specs").glob("boxgun*.json"):
        shutil.copy2(path, tmp_path / "specs")
    scaffold(tmp_path, "mygun", "boxgun", None, "My Gun", prefix="PQMYGUN")
    spec = json.loads((tmp_path / "specs" / "mygun.json").read_text(encoding="utf-8"))
    assert spec["package"] == "PipelineMeshesPQMYGUN"
    assert "save_package" not in spec["options"]
    names = json.dumps({k: spec[k] for k in ("package", "mesh_path", "fragments", "parts", "balances")})
    assert "BOXGUN" not in names and "Boxgun" not in names
    recipe = json.loads((tmp_path / "recipes" / "mygun.json").read_text(encoding="utf-8"))
    assert recipe["output"]["package_name"] == "PipelineMeshesPQMYGUN" if "output" in recipe \
        else "Boxgun" not in json.dumps(recipe).replace("recipes/boxgun.json", "").replace(
            "the BOXGUN", "")


def test_load_weapon_round_trips_the_real_boxgun_pack(catalog: dict[str, Any]) -> None:
    spec = load_spec(BOXGUN)
    entry = json.loads(json.dumps({"spec": spec.to_dict(), "resolved": resolved_to_dict(resolve(spec, catalog))}))
    assert load_weapon(entry).balances[0].path.endswith("AR_Vladof_5_BOXGUN")
