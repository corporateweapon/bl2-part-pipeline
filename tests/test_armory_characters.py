"""Character packs: the Armory as the framework for character skins (Armory 1.1.0), offline.

What it pins:

* the builder turns a bl2_charswap skin into a content-only pack (armory_pack.json with
  "characters", README, licence, the .upk files; no Python), schema 2, checked by the same
  load_character the Armory runs; a weapon-only pack stays schema 1
* the release carries the character runtime byte-identical to src/bl2_charswap/runtime.py
* installed beside a weapon pack, the Armory loads the skin: Characters page with one dropdown
  per vault hunter preset to the pack's skin, the 'characters' console command, the runtime's
  hooks on at import; weapons unaffected
* refusals: an unknown vault hunter, a package the pack does not ship, a path that could escape,
  a second pack with the same skin id; and while the dev PipelineCharacters mod is installed a
  character-only pack stands down (never two mods swapping one mesh)
* the dev PipelineCharacters template, now a thin host of the same runtime, still reads
  characters.json plus drop-in skins/*.json

    python -m pytest tests/test_armory_characters.py -q
"""

from __future__ import annotations

import filecmp
import importlib.util
import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests" / "fakes"))

import mods_base  # noqa: E402  (the fake)

from bl2_charswap import __main__ as charswap_cli  # noqa: E402
from bl2_charswap import skinpack  # noqa: E402
from bl2_partgen.pack import CHARACTER_RUNTIME, build_armory_release, build_pack, install_tree  # noqa: E402
from bl2_partgen.packdata import PACK_FILE  # noqa: E402
from tests.test_armory_packs import RUNTIME, _source_tree, load_runtime  # noqa: E402


def _skin(sid: str, character: str) -> dict[str, Any]:
    return {"id": sid, "label": f"Skin {sid}", "character": character, "description": "test skin",
            "packages": [f"PipelineMeshes{sid.upper()}"],
            "swaps": {f"CD_Base_{character}.Body": {
                "mesh": f"PipelineMeshes{sid.upper()}.Body",
                "material": {"name": f"MI_{sid}", "outer": f"PipelineMeshes{sid.upper()}",
                             "parent": "Item_ClassMods.Mat.Master_ClassMod",
                             "textures": {"p_Diffuse": f"PipelineMeshes{sid.upper()}.Albedo"}}}},
            "hide_mesh_prefixes": [f"CD_Heads_{character}"]}


@pytest.fixture()
def skin_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Two built skins (skin.json + a dummy .upk each) and their pack manifests."""
    root = tmp_path / "src"
    builds = tmp_path / "charswap"
    (root / "specs" / "characters").mkdir(parents=True)
    (root / "packs").mkdir(parents=True, exist_ok=True)
    for sid, character in (("sas", "Krieg"), ("mina", "Gaige")):
        out = builds / sid
        out.mkdir(parents=True)
        (out / "skin.json").write_text(json.dumps(_skin(sid, character)), encoding="utf-8")
        (out / f"PipelineMeshes{sid.upper()}.upk").write_bytes(f"skin {sid}".encode() * 32)
        (root / "specs" / "characters" / f"{sid}.json").write_text(json.dumps({"id": sid}),
                                                                   encoding="utf-8")
        (root / "packs" / f"{sid}.json").write_text(json.dumps({
            "id": sid, "name": f"Skin pack {sid}", "version": "1.0.0", "author": "tests",
            "license": "CC0-1.0", "description": "test", "weapons": [],
            "characters": [{"spec": f"../specs/characters/{sid}.json"}]}), encoding="utf-8")
    monkeypatch.setattr(skinpack, "build_dir", lambda spec: builds / str(spec["id"]))
    return root


@pytest.fixture()
def game(tmp_path: Path, catalog: dict[str, Any], skin_sources: Path) -> dict[str, Any]:
    """The Armory release with weapon pack GUNA and both skin packs installed."""
    wsrc = tmp_path / "wsrc"
    specs = _source_tree(wsrc)
    builds = [build_pack(wsrc / "packs" / "guna.json", tmp_path / "dist" / "packs", catalog=catalog)]
    builds += [build_pack(skin_sources / "packs" / f"{s}.json", tmp_path / "dist" / "packs",
                          catalog=catalog) for s in ("sas", "mina")]
    stage, _zip = build_armory_release(tmp_path / "dist", runtime_dir=RUNTIME, packs=builds,
                                       make_zip=False)
    folder = tmp_path / "game"
    install_tree(stage, folder)
    return {"game": folder, "specs": {"GUNA": specs["GUNA"]}, "builds": builds, "stage": stage}


@pytest.fixture(scope="module")
def catalog() -> dict[str, Any]:
    from bl2_catalog import load_catalog

    path = REPO / "catalog" / "parts.json"
    if not path.exists():
        pytest.skip("catalog/parts.json absent")
    return load_catalog(path)


def _pack(game: Path, pid: str) -> Path:
    return game / "sdk_mods" / "ArmoryPacks" / pid / PACK_FILE


# ------------------------------------------------------------------ builder and release
def test_character_pack_is_content_only(game: dict[str, Any]) -> None:
    sas = next(b for b in game["builds"] if b.pack_id == "sas")
    assert sas.zip_path is not None
    with zipfile.ZipFile(sas.zip_path) as z:
        names = sorted(z.namelist())
    # what goes in the game folder: content only (no Armory code); the rest is the zip's installer
    assert [n for n in names if n.split("/")[0] in ("sdk_mods", "WillowGame")] == [
        "WillowGame/CookedPCConsole/PipelineMeshesSAS.upk",
        "sdk_mods/ArmoryPacks/sas/LICENSE.txt", "sdk_mods/ArmoryPacks/sas/README.md",
        "sdk_mods/ArmoryPacks/sas/armory_pack.json"]
    assert [n for n in names if n.split("/")[0] not in ("sdk_mods", "WillowGame")] == [
        "HOW_TO_INSTALL.txt", "Install.bat", "Uninstall.bat", "_installer/armory_install.ps1"]
    data = json.loads(_pack(game["game"], "sas").read_text(encoding="utf-8"))
    assert data["schema"] == 2 and data["weapons"] == [] and data["armory_min"] == "1.1.0"
    assert data["characters"][0]["id"] == "sas" and data["characters"][0]["default"] is True
    guna = json.loads(_pack(game["game"], "guna").read_text(encoding="utf-8"))
    assert guna["schema"] == 1 and "characters" not in guna   # older Armories still read it
    readme = (game["game"] / "sdk_mods" / "ArmoryPacks" / "sas" / "README.md").read_text(encoding="utf-8")
    assert "characters set Krieg sas" in readme


def test_release_carries_the_character_runtime(game: dict[str, Any]) -> None:
    shipped = game["game"] / "sdk_mods" / "Armory" / "_render" / CHARACTER_RUNTIME
    assert filecmp.cmp(shipped, REPO / "src" / "bl2_charswap" / "runtime.py", shallow=False)


# ------------------------------------------------------------------ the Armory as host
def test_armory_wears_character_packs_beside_weapons(game: dict[str, Any], catalog: dict[str, Any]) -> None:
    module = load_runtime(game["game"], catalog, game["specs"])
    assert module.PROBLEMS == []
    assert [w["id"] for w in module.WEAPONS] == ["guna"]
    assert sorted(s["id"] for s in module.SKINS) == ["mina", "sas"]
    assert {p["id"]: p["characters"] for p in module.PACKS} == {"guna": [], "mina": ["mina"], "sas": ["sas"]}
    chars = module._chars
    assert chars.CHARACTERS == ["Gaige", "Krieg"] and chars.MOD_NAME == "Armory"
    assert chars._options["Krieg"].value == "Skin sas" and chars._options["Gaige"].value == "Skin mina"
    mod = mods_base.built_mods[-1]
    characters = mod.find_option("Characters")
    assert [c.identifier for c in characters.children][:2] == ["Gaige", "Krieg"]
    assert [kb.key for kb in mod.keybinds] == ["F5"], "no test keys ship in the Armory"
    assert all(h.enabled for h in module.CHARACTERS["hooks"])
    assert Path(chars.STATUS) == game["game"] / "sdk_mods" / "Armory" / "logs" / "characters.status.json"
    chars.characters_cmd.run("list")
    chars.characters_cmd.run("set Krieg default")
    assert chars._options["Krieg"].value == chars.DEFAULT


BAD_SKINS = [
    ("unknown hunter", lambda c: c.update(character="Bob"), "is not one of"),
    ("foreign package", lambda c: c.update(packages=["PipelineMeshesOTHER"]), "not among the pack's packages"),
    ("escaping path", lambda c: next(iter(c["swaps"].values())).update(mesh='x"); import os #'),
     "must be an object path"),
    ("bad id", lambda c: c.update(id="Has Space"), "must be 1-32"),
]


@pytest.mark.parametrize("what,edit,reason", BAD_SKINS, ids=[b[0] for b in BAD_SKINS])
def test_a_bad_character_pack_is_refused(game: dict[str, Any], catalog: dict[str, Any],
                                         what: str, edit: Any, reason: str) -> None:
    path = _pack(game["game"], "sas")
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data["characters"][0])
    path.write_text(json.dumps(data), encoding="utf-8")
    module = load_runtime(game["game"], catalog, game["specs"])
    assert [s["id"] for s in module.SKINS] == ["mina"]
    problem = next(p for p in module.PROBLEMS if p["folder"] == "sas")
    assert reason in problem["error"], problem["error"]


def test_a_second_skin_with_the_same_id_is_refused(game: dict[str, Any], catalog: dict[str, Any]) -> None:
    path = _pack(game["game"], "mina")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["characters"][0]["id"] = "sas"
    path.write_text(json.dumps(data), encoding="utf-8")
    module = load_runtime(game["game"], catalog, game["specs"])
    problem = next(p for p in module.PROBLEMS if p["folder"] == "sas")
    assert "already used by pack 'mina'" in problem["error"]


def test_dev_characters_mod_makes_the_armory_stand_down(game: dict[str, Any], catalog: dict[str, Any]) -> None:
    dev = game["game"] / "sdk_mods" / "PipelineCharacters"
    dev.mkdir()
    (dev / "__init__.py").write_text('"""dev"""\n', encoding="utf-8")
    module = load_runtime(game["game"], catalog, game["specs"])
    assert module.SKINS == [] and module.CHARACTERS is None
    assert [w["id"] for w in module.WEAPONS] == ["guna"], "weapons are unaffected"
    refused = {p["folder"]: p["error"] for p in module.PROBLEMS}
    assert set(refused) == {"mina", "sas"} and "PipelineCharacters" in refused["sas"]
    with pytest.raises(KeyError):
        mods_base.built_mods[-1].find_option("Characters")   # no Characters page at all


# ------------------------------------------------------------------ the dev host
def _dev_mod(root: Path) -> Path:
    mod_dir = root / "sdk_mods" / "PipelineCharacters"
    mod_dir.mkdir(parents=True)
    for f in ("__init__.py", "pyproject.toml"):
        shutil.copy2(charswap_cli.MOD_TEMPLATE / f, mod_dir / f)
    shutil.copy2(REPO / "src" / "bl2_charswap" / "runtime.py", mod_dir / "runtime.py")
    return mod_dir


def _import_dev(mod_dir: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, mod_dir / "__init__.py",
                                                  submodule_search_locations=[str(mod_dir)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_dev_template_reads_characters_json_and_drop_ins(tmp_path: Path) -> None:
    mods_base.built_mods.clear()
    mod_dir = _dev_mod(tmp_path)
    (mod_dir / "characters.json").write_text(json.dumps({"skins": [_skin("sas", "Krieg")]}), encoding="utf-8")
    (mod_dir / "skins").mkdir()
    (mod_dir / "skins" / "mina.json").write_text(json.dumps(dict(_skin("mina", "Gaige"), default=True)),
                                                 encoding="utf-8")
    module = _import_dev(mod_dir, "dev_characters_1")
    chars = module.runtime
    assert sorted(chars.BY_ID) == ["mina", "sas"] and chars.MOD_NAME == "PipelineCharacters"
    assert chars._options["Krieg"].value == chars.DEFAULT       # installer skins are not preset
    assert chars._options["Gaige"].value == "Skin mina"         # a drop-in marked default is
    mod = mods_base.built_mods[-1]
    assert mod.name == "PipelineCharacters" and sorted(kb.key for kb in mod.keybinds if kb.key) == ["F7", "F9"]


def test_install_copies_the_runtime(tmp_path: Path) -> None:
    out = tmp_path / "build"
    out.mkdir()
    (out / "skin.json").write_text(json.dumps(_skin("sas", "Krieg")), encoding="utf-8")
    (out / "PipelineMeshesSAS.upk").write_bytes(b"upk")
    game = tmp_path / "game"
    (game / "WillowGame" / "CookedPCConsole").mkdir(parents=True)
    charswap_cli.install({"id": "sas"}, out, game)
    mod_dir = game / "sdk_mods" / "PipelineCharacters"
    assert (mod_dir / "runtime.py").exists() and (mod_dir / "characters.json").exists()
