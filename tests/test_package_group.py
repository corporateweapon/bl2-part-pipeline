"""Package groups: two weapons on one host gestalt in ONE package.

* the Armory's L11 accepts two weapons that share a package (same package stem, same mesh
  path) and still refuses two that re-point the gestalt at different packages
* the ``bl2`` front door builds a whole group from any member and points every member's
  emit at the shared sidecar
* when the group has been built (``scratch/PipelineMeshes_ak47.fragment.json`` carries the
  Boxgun's fragments after the AK's), the AK's ranges are the ones it has alone

    python -m pytest tests/test_package_group.py -q
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
from bl2.weapon import Weapon  # noqa: E402


@pytest.fixture(scope="module")
def catalog():
    from bl2_catalog import load_catalog
    path = REPO / "catalog" / "parts.json"
    if not path.exists():
        pytest.skip("catalog absent")
    return load_catalog(path)


def _shared(spec: dict) -> dict:
    """The pre-2026-09-23 shape: re-point the stock gestalt, AK + Boxgun in PipelineMeshes."""
    spec["options"]["own_gestalt"] = False
    spec["options"].pop("save_package", None)
    spec["package"], spec["mesh_path"] = "PipelineMeshes", "PipelineMeshes.PL_AR_Gestalt_Mesh"
    return spec


def _armory(tmp_path: Path, weapons: list[tuple[str, str]], shared: bool = True) -> Path:
    """An armory spec beside copies of the named specs (id, spec file); ``shared`` turns them
    back into the package-group shape the L11 tests are about."""
    d = tmp_path / "specs"
    d.mkdir(parents=True, exist_ok=True)
    for _, spec in weapons:
        data = json.loads((REPO / "specs" / spec).read_text(encoding="utf-8"))
        (d / spec).write_text(json.dumps(_shared(data) if shared else data), encoding="utf-8")
    arm = {"mod": {"name": "PipelineArmoryTest", "author": "t", "version": "0.0.1", "description": "t"},
           "weapons": [{"id": i, "spec": s, "label": i} for i, s in weapons],
           "spawn": {"keybind": "F5", "mission": "GD_Episode01.M_Ep1_Champion"}}
    p = d / "armory.json"
    p.write_text(json.dumps(arm), encoding="utf-8")
    return p


def test_l11_accepts_a_shared_package_and_refuses_two_packages_on_one_host(tmp_path: Path, catalog):
    from bl2_partgen import emit_armory
    from bl2_partgen.emit import ArmoryConflict

    # ak47 + boxgun: both PipelineMeshes on the AR gestalt -> allowed
    p = _armory(tmp_path / "ok", [("ak47", "ak47.json"), ("boxgun", "boxgun.json")])
    res = emit_armory(p, tmp_path / "ok" / "out", catalog=catalog)
    assert {w.name for w in res.weapons} == {"PipelineAK47", "PipelineBOXGUN"}
    # ak47 + a boxgun pointed at its own package: two meshes for one gestalt -> refused
    d = tmp_path / "bad"
    p = _armory(d, [("ak47", "ak47.json"), ("boxgun", "boxgun.json")])
    spec = json.loads((d / "specs" / "boxgun.json").read_text(encoding="utf-8"))
    spec["package"], spec["mesh_path"] = "PipelineMeshesBoxgun", "PipelineMeshesBoxgun.PL_AR_Gestalt_Mesh"
    (d / "specs" / "boxgun.json").write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ArmoryConflict, match="L11"):
        emit_armory(p, d / "out", catalog=catalog)


def test_l11_accepts_the_committed_own_gestalt_rifles(tmp_path: Path, catalog):
    """The committed specs (2026-09-23): AK and Boxgun each in their own package, own gestalt."""
    from bl2_partgen import emit_armory

    p = _armory(tmp_path, [("ak47", "ak47.json"), ("boxgun", "boxgun.json")], shared=False)
    res = emit_armory(p, tmp_path / "out", catalog=catalog)
    assert {w.name for w in res.weapons} == {"PipelineAK47", "PipelineBOXGUN"}


def test_group_membership_and_dry_build(tmp_path: Path, capsys):
    root = tmp_path / "repo"
    for d in ("recipes", "specs", "packages", "scratch", "sdk_mod"):
        (root / d).mkdir(parents=True)
    for sub in ("recipes", "specs"):
        for p in (REPO / sub).glob("*.json"):
            shutil.copy(p, root / sub / p.name)
    # the repo no longer ships a group (2026-09-23: every weapon has its own package); the
    # mechanism is still supported, so the test brings its own
    (root / "packages" / "PipelineMeshes.json").write_text(json.dumps({
        "package_name": "PipelineMeshes", "mesh_name": "PL_AR_Gestalt_Mesh",
        "out": "scratch/PipelineMeshes_ak47.upk", "recipes": ["ak47", "boxgun"]}), encoding="utf-8")
    w = Weapon("boxgun", root)
    g = w.group()
    assert g and g["_name"] == "PipelineMeshes" and g["recipes"] == ["ak47", "boxgun"]
    assert w.package == root / "scratch" / "PipelineMeshes_ak47.upk"
    assert w.sidecar == Weapon("ak47", root).sidecar
    assert Weapon("awp", root).group() is None
    code = cli_main(["--dry", "--root", str(root), "--game", "C:/g", "--blender", "C:/b.exe", "build", "boxgun"])
    out = capsys.readouterr().out
    assert code == 0
    run = next(l for l in out.splitlines() if " -m bl2_retarget run " in l)
    assert "ak47.json" in run and "boxgun.json" in run and run.index("ak47.json") < run.index("boxgun.json")
    assert "--package-name PipelineMeshes" in run
    emits = [l for l in out.splitlines() if " -m bl2_partgen " in l]
    assert len(emits) == 6 and all("PipelineMeshes_ak47.fragment.json" in l for l in emits)


def test_built_group_keeps_the_first_members_ranges():
    side = REPO / "scratch" / "PipelineMeshes_ak47.fragment.json"
    if not side.exists():
        pytest.skip("group not built on this machine")
    d = json.loads(side.read_text(encoding="utf-8"))
    names = [f["fragment"] for f in d["fragments"]]
    if "BOXGUN_Body" not in names:
        pytest.skip("sidecar is a solo AK build")
    spec = json.loads((REPO / "specs" / "ak47.json").read_text(encoding="utf-8"))
    ranges = {f["fragment"]: f["first_index"] for f in d["fragments"]}
    for f in spec["fragments"]:
        assert ranges[f["name"]] == f["first_index"], f["name"]
    assert names.index("BOXGUN_Body") > names.index("AK_Mag")
