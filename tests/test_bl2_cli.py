"""Tests for the ``bl2`` front door: the weapon conventions, the scaffold, and the command
chains in ``--dry`` mode (nothing here launches Blender or the game).

    python -m pytest tests/test_bl2_cli.py -q
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bl2.cli import main as cli_main  # noqa: E402
from bl2.scaffold import rename_value, scaffold  # noqa: E402
from bl2.weapon import Weapon, list_weapons  # noqa: E402


@pytest.fixture()
def mini_repo(tmp_path: Path) -> Path:
    """A copy of the repo's recipes and specs (no scratch, no game) under tmp_path."""
    root = tmp_path / "repo"
    for d in ("recipes", "specs", "docs/captures", "scratch", "sdk_mod"):
        (root / d).mkdir(parents=True)
    for p in (REPO / "recipes").glob("*.json"):
        shutil.copy(p, root / "recipes" / p.name)
    for p in (REPO / "specs").glob("*.json"):
        shutil.copy(p, root / "specs" / p.name)
    return root


# --------------------------------------------------------------------------- conventions
def test_weapon_conventions_for_the_awp(mini_repo: Path):
    w = Weapon("awp", mini_repo)
    assert w.recipe == mini_repo / "recipes" / "awp.json"
    assert w.variants() == ["player", "harness", "spawn"]
    assert w.mod_name("harness") == "PipelineAWPHarness" and w.mod_name("player") == "PipelineAWP"
    assert w.package_name == "PipelineMeshesAWP"
    assert w.package == mini_repo / "scratch" / "PipelineMeshesAWP.upk"
    assert w.sidecar == mini_repo / "scratch" / "PipelineMeshesAWP.fragment.json"
    assert w.part_path() == "GD_Weap_SniperRifles.Barrel.AWP_Barrel"
    assert w.status_file() == mini_repo / "scratch" / "PipelineAWPHarness_status.json"
    assert w.baseline.name == "awp_stock_firstperson.png"
    assert sorted(list_weapons(mini_repo)) == ["ak47", "awp", "boxgun", "deagle", "shiv"]


def test_control_and_proposal_generation(mini_repo: Path):
    w = Weapon("awp", mini_repo)
    w.sidecar.write_text(json.dumps({"package": "PipelineMeshesAWP", "total_vertices": 60497, "fragments": []}), encoding="utf-8")
    c = json.loads(w.write_control().read_text(encoding="utf-8"))
    assert c["mod"] == "PipelineAWPHarness" and c["part_name"] == "AWP_Barrel" and c["total_vertices"] == 60497
    assert c["status_path"].endswith("PipelineAWPHarness_status.json")
    assert w.write_proposal() is None                      # no emitted lint.json yet
    mod_dir = mini_repo / "sdk_mod" / "PipelineAWPHarness"
    mod_dir.mkdir(parents=True)
    (mod_dir / "lint.json").write_text(json.dumps({"results": [
        {"part": "AWP_Body", "proposal": {"part_path": "x.AWP_Body"}},
        {"part": "AWP_Barrel", "proposal": {"part_path": "GD_Weap_SniperRifles.Barrel.AWP_Barrel"}}]}), encoding="utf-8")
    assert json.loads(w.write_proposal().read_text(encoding="utf-8"))["part_path"].endswith("AWP_Barrel")


# --------------------------------------------------------------------------- scaffold
def test_rename_tokens():
    assert rename_value("PipelineMeshesAWP", "awp", "m4", "M4") == "PipelineMeshesM4"
    assert rename_value("AWP_Barrel", "awp", "m4", "M4") == "M4_Barrel"
    assert rename_value("Sniper_Jakobs_5_AWP", "awp", "m4", "M4") == "Sniper_Jakobs_5_M4"
    assert rename_value("awp_retarget", "awp", "m4", "M4") == "awp_retarget"      # lower-case token untouched for awp
    assert rename_value("PipelineAK47Harness", "ak47", "m4", "M4") == "PipelineM4Harness"
    assert rename_value("AK_Barrel", "ak47", "m4", "M4") == "M4_Barrel"
    assert rename_value("scratch/PipelineMeshes_ak47.upk", "ak47", "m4", "M4") == "scratch/PipelineMeshes_m4.upk"


def test_scaffold_from_the_awp(mini_repo: Path):
    res = scaffold(mini_repo, "m4", "awp", glb="C:/models/m4.glb", label="M4A1")
    assert set(res["written"]) == {"recipes\\m4.json", "specs\\m4.json", "specs\\m4_harness.json", "specs\\m4_spawn.json"} \
        or set(res["written"]) == {"recipes/m4.json", "specs/m4.json", "specs/m4_harness.json", "specs/m4_spawn.json"}
    recipe = json.loads((mini_repo / "recipes" / "m4.json").read_text(encoding="utf-8"))
    assert recipe["name"] == "M4" and recipe["source"]["glb"] == "C:/models/m4.glb"
    assert [f["name"] for f in recipe["fragments"]][:2] == ["M4_Body", "M4_Barrel"]
    assert recipe["output"]["package_name"] == "PipelineMeshesM4"
    assert recipe["rule_order"] == ["M4_Barrel", "M4_Scope", "M4_Stock", "M4_Grip"]
    spec = json.loads((mini_repo / "specs" / "m4_harness.json").read_text(encoding="utf-8"))
    assert spec["mod"]["name"] == "PipelineM4Harness" and spec["package"] == "PipelineMeshesM4"
    assert spec["balances"][0]["name"] == "Sniper_Jakobs_5_M4" and spec["balances"][0]["title"]["part_name"] == "M4A1"
    assert all("socket_overrides" not in f and "bounds_override" not in f for f in spec["fragments"])
    assert spec["parts"][0]["template_part"] == "GD_Weap_SniperRifles.Barrel.SR_Barrel_Jakobs_Skullmasher"   # host templates kept
    w = Weapon("m4", mini_repo)
    assert w.part_path() == "GD_Weap_SniperRifles.Barrel.M4_Barrel"
    with pytest.raises(FileExistsError):
        scaffold(mini_repo, "m4", "awp", None, None)
    assert "m4" in list_weapons(mini_repo)


# --------------------------------------------------------------------------- dry chains
def run_dry(mini_repo: Path, capsys, *args: str) -> list[str]:
    code = cli_main(["--dry", "--root", str(mini_repo), "--game", "C:/game", "--blender", "C:/blender.exe", *args])
    out = capsys.readouterr().out
    assert code == 0, out
    return [l for l in out.splitlines() if l.startswith("$ ")]


def test_build_chain(mini_repo: Path, capsys):
    cmds = run_dry(mini_repo, capsys, "build", "awp", "--skip-render")
    mods = [c.split(" -m ")[1].split()[0] for c in cmds]
    assert mods == ["bl2_retarget", "bl2_retarget", "bl2_retarget", "bl2_retarget",
                    "bl2_partgen", "bl2_partgen", "bl2_partgen", "bl2_preflight"]
    assert "--measure-only" not in cmds[1] and "--skip-render" in cmds[1]
    assert "apply-spec" in cmds[3] and "awp_harness.json" in cmds[3]
    assert all("--sidecar" in c for c in cmds[4:7])
    assert cmds[7].endswith("PipelineAWPHarness")


def test_verify_chain_and_unknown_weapon(mini_repo: Path, capsys):
    cmds = run_dry(mini_repo, capsys, "verify", "ak47", "--run-index", "3")
    assert "bl2_preflight" in cmds[0] and "--part-path GD_Weap_AssaultRifle.Barrel.AK_Barrel" in cmds[0]
    assert "run_loop.py" in cmds[1] and "--run-index 3" in cmds[1] and "--capture-view first" in cmds[1]
    assert "--package PipelineMeshes" in cmds[1]
    assert "bl2_triage" in cmds[2]
    code = cli_main(["--dry", "--root", str(mini_repo), "verify", "nope"])
    assert code == 2 and "unknown weapon" in capsys.readouterr().out


def test_measure_install_ship_status_chains(mini_repo: Path, capsys):
    cmds = run_dry(mini_repo, capsys, "measure", "awp")
    assert "--measure-only" in cmds[1] and "summarize" in cmds[2]
    cmds = run_dry(mini_repo, capsys, "install", "awp", "--variant", "player")
    assert "specs\\awp.json" in cmds[0] or "specs/awp.json" in cmds[0]
    assert "--install C:/game --replace" in cmds[0] and "bl2_preflight --mod PipelineAWP " in cmds[1]
    cmds = run_dry(mini_repo, capsys, "ship", "awp", "--install", "--check")
    assert "armory.json" in cmds[0] and "--install" in cmds[0]
    assert "bl2_preflight --mod PipelineArmory" in cmds[1]  # the check launches only on a GO
    assert "armory_check.py" in cmds[2] and "--weapons awp" in cmds[2]
    cmds = run_dry(mini_repo, capsys, "status")
    assert "bl2_preflight" in cmds[0] and "bl2_triage" in cmds[1]
    cmds = run_dry(mini_repo, capsys, "triage", "--json")
    assert cmds[0].endswith("-m bl2_triage --json")


def test_new_then_chains_work_on_the_scaffold(mini_repo: Path, capsys):
    assert cli_main(["--root", str(mini_repo), "new", "m4", "--from", "ak47", "--glb", "x.glb"]) == 0
    out = capsys.readouterr().out
    assert "to do:" in out and "bl2 measure m4" in out
    cmds = run_dry(mini_repo, capsys, "build", "m4")
    assert any("PipelineM4Harness" in c for c in cmds)
