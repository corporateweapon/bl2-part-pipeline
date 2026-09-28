"""``bl2_verify.migrate_save_records``: old per-mod ``saves/`` records merge into the shared
``sdk_mods/_pipeline_saves/<package>/`` folder, newest winning, M2-era mods skipped."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bl2_verify import migrate_save_records as m  # noqa: E402


def _mod(sdk_mods: Path, name: str, package: str, records: dict, age: float) -> None:
    folder = sdk_mods / name
    (folder / "saves").mkdir(parents=True)
    (folder / "__init__.py").write_text(f'MOD_NAME = "{name}"\nPACKAGE = "{package}"\n',
                                        encoding="utf-8")
    path = folder / "saves" / "Save0005.sav.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))


def test_merge_keys_by_package_and_newest_wins(tmp_path: Path) -> None:
    sdk_mods = tmp_path / "sdk_mods"
    _mod(sdk_mods, "PipelineAK47Spawn", "PipelineMeshes",
         {"1": {"BarrelPartDefinition": "A.old"}, "2": {"BarrelPartDefinition": "A.two"}}, age=100)
    _mod(sdk_mods / "_disabled", "PipelineAK47Harness", "PipelineMeshes",
         {"1": {"BarrelPartDefinition": "A.new"}}, age=10)
    _mod(sdk_mods, "PipelineAWP", "PipelineMeshesAWP", {"9": {"BarrelPartDefinition": "S.x"}}, age=5)
    _mod(sdk_mods, "PipelineBentBarrelHarness", "PipelineMeshes", {"7": {"BarrelPartDefinition": "Bent"}}, age=1)
    shared = sdk_mods / "_pipeline_saves" / "PipelineMeshes"
    shared.mkdir(parents=True)
    (shared / "Save0005.sav.json").write_text(json.dumps({"3": {"BarrelPartDefinition": "A.live"}}),
                                              encoding="utf-8")

    assert m.main(["--game", str(tmp_path), "--dry-run"]) == 0
    assert json.loads((shared / "Save0005.sav.json").read_text(encoding="utf-8")) == {
        "3": {"BarrelPartDefinition": "A.live"}}, "dry run writes nothing"

    assert m.main(["--game", str(tmp_path)]) == 0
    ak = json.loads((shared / "Save0005.sav.json").read_text(encoding="utf-8"))
    assert ak == {
        "1": {"BarrelPartDefinition": "A.new"},   # newer file wins
        "2": {"BarrelPartDefinition": "A.two"},
        "3": {"BarrelPartDefinition": "A.live"},  # what the new builds wrote already stays
    }
    awp = json.loads((sdk_mods / "_pipeline_saves" / "PipelineMeshesAWP" / "Save0005.sav.json")
                     .read_text(encoding="utf-8"))
    assert awp == {"9": {"BarrelPartDefinition": "S.x"}}
    assert "7" not in ak, "M2-era bent-barrel records are skipped"
