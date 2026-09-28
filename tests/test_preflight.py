"""Tests for ``bl2_preflight`` -- the fail-fast checks and the offline dry run.

The checks run on a hand-built :class:`Context` (no game folder, no processes, no
windows), so each discriminator is pinned to the state that trips it. The dry run is
exercised on a mod emitted into ``tmp_path`` from ``specs/bent_barrel.json`` (as
``test_partgen`` does) and, when the AK/AWP specs are present, on those too -- which is
what proves the catalog augmentation covers a real spec.

    python -m pytest tests/test_preflight.py -q
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bl2_preflight import checks as ck  # noqa: E402
from bl2_preflight.__main__ import main as cli_main  # noqa: E402
from bl2_preflight.dryrun import dry_run  # noqa: E402


# --------------------------------------------------------------------------- fixtures
def game_dir(tmp_path: Path, *, sdk: bool = True, mods: dict[str, dict] | None = None,
             packages: tuple[str, ...] = ("PipelineMeshes",), startup_size: int = 100) -> Path:
    game = tmp_path / "game"
    (game / "Binaries" / "Win32" / "Plugins").mkdir(parents=True)
    (game / "Binaries" / "Win32" / "Borderlands2.exe").write_bytes(b"exe")
    if sdk:
        for n in ("Plugins/unrealsdk.dll", "Plugins/pyunrealsdk.dll", "ddraw.dll"):
            (game / "Binaries" / "Win32" / n).write_bytes(b"dll")
    cooked = game / "WillowGame" / "CookedPCConsole"
    cooked.mkdir(parents=True)
    (cooked / "Startup.upk").write_bytes(b"s" * startup_size)
    for p in packages:
        (cooked / f"{p}.upk").write_bytes(b"pkg")
    (game / "sdk_mods" / "settings").mkdir(parents=True)
    for name, m in (mods or {}).items():
        d = game / "sdk_mods" / name
        d.mkdir()
        (d / "__init__.py").write_text(m.get("source", "# _pipeline_saves\n"), encoding="utf-8")
        (d / "spec.json").write_text(json.dumps(m.get("spec", {})), encoding="utf-8")
        if "enabled" in m:
            (game / "sdk_mods" / "settings" / f"{name}.json").write_text(json.dumps({"enabled": m["enabled"]}), encoding="utf-8")
    return game


def spec(weapon_type: str = "AssaultRifle", **opts) -> dict:
    return {"weapon_type": weapon_type, "package": "PipelineMeshes", "extra_packages": [],
            "parts": [{"outer": "GD_Weap_AssaultRifle.Barrel", "part_name": "AK_Barrel"},
                      {"outer": "GD_Weap_AssaultRifle.Body", "part_name": "AK_Body"}],
            "options": {"test_harness": True, "harness_timing": "seconds", "harness_capture_view": "first", **opts}}


def ctx_for(tmp_path: Path, game: Path, **kw) -> ck.Context:
    backup = tmp_path / "backup"
    (backup / "CookedPCConsole").mkdir(parents=True, exist_ok=True)
    (backup / "CookedPCConsole" / "Startup.upk").write_bytes(b"s" * 100)
    save = tmp_path / "Save0005.sav"
    save.write_bytes(b"save")
    templates = tmp_path / "templates.py"
    if not templates.exists():
        templates.write_text("x", encoding="utf-8")
        old = time.time() - 60          # older than any build written by this test
        import os
        os.utime(templates, (old, old))
    c = ck.Context(game=game, save=save, backup_root=backup, templates_py=templates,
                   config_dir=tmp_path / "nocfg", **kw)
    c.mods = ck.scan_mods(game, templates)
    mine = next((m for m in c.mods if m["name"] == c.mod), None) if c.mod else None
    c.spec = (mine or {}).get("spec")
    return c


def by_name(results: list[ck.Check]) -> dict[str, ck.Check]:
    out: dict[str, ck.Check] = {}
    for r in results:
        out.setdefault(r.name, r)
    return out


# --------------------------------------------------------------------------- checks
def test_clean_context_is_go(tmp_path: Path):
    game = game_dir(tmp_path, mods={"PipelineX": {"enabled": True, "spec": spec()}})
    c = ctx_for(tmp_path, game, mod="PipelineX", part_path="GD_Weap_AssaultRifle.Barrel.AK_Barrel")
    c.processes = {"steam.exe": [1]}
    c.desktop = (1920, 1080)
    (tmp_path / "backup" / "live-now").mkdir()
    res = ck.run_checks(c)
    assert ck.verdict(res) == "GO", [r for r in res if r.status != "ok"]


def test_game_and_process_states(tmp_path: Path):
    game = game_dir(tmp_path, sdk=False)
    c = ctx_for(tmp_path, game)
    c.processes = {"steam.exe": [], "Borderlands2.exe": [42], "Launcher.exe": [7]}
    r = by_name(ck.run_checks(c, [ck.check_game]))
    assert r["sdk"].status == "fail"
    assert r["game running"].status == "fail" and "42" in r["game running"].detail
    assert r["launcher"].status == "warn" and r["launcher"].code == "F10"
    assert r["steam"].status == "fail"


def test_mod_states(tmp_path: Path):
    game = game_dir(tmp_path, mods={
        "PipelineA": {"spec": spec()},                                    # no settings file
        "PipelineB": {"enabled": False, "spec": spec()},
        "PipelineC": {"enabled": True, "spec": spec(), "source": "# old build, no marker\n"},
    })
    for name, want, code in (("PipelineA", "fail", "F8"), ("PipelineB", "fail", "F8")):
        r = by_name(ck.check_mod(ctx_for(tmp_path, game, mod=name)))
        assert r["mod enabled"].status == want and r["mod enabled"].code == code
    r = by_name(ck.check_mod(ctx_for(tmp_path, game, mod="PipelineC")))
    assert r["mod fresh"].status == "fail" and r["mod fresh"].code == "F28"
    r = by_name(ck.check_mod(ctx_for(tmp_path, game, mod="PipelineZ")))
    assert r["mod"].status == "fail" and "not under sdk_mods" in r["mod"].detail


def test_stale_build_is_a_warning(tmp_path: Path):
    game = game_dir(tmp_path, mods={"PipelineA": {"enabled": True, "spec": spec()}})
    c = ctx_for(tmp_path, game, mod="PipelineA")
    old = time.time() - 3600
    import os
    os.utime(game / "sdk_mods" / "PipelineA" / "__init__.py", (old, old))
    c.mods = ck.scan_mods(game, c.templates_py)
    r = by_name(ck.check_mod(c))
    assert r["mod fresh"].status == "warn"


def test_rule3_two_mods_one_gestalt(tmp_path: Path):
    game = game_dir(tmp_path, mods={
        "PipelineArmory": {"enabled": True, "spec": {"weapons": {"ak": spec(), "awp": spec("SniperRifle")}}},
        "PipelineAWP": {"enabled": True, "spec": spec("SniperRifle")},
        "PipelineAK": {"enabled": False, "spec": spec()},
    })
    c = ctx_for(tmp_path, game, mod="PipelineAK")
    r = ck.check_rule3(c)
    fails = [x for x in r if x.status == "fail"]
    assert len(fails) == 1 and "SniperRifle" in fails[0].detail and fails[0].code == "RULE3"
    assert any(x.name == "mod is the one enabled" and x.status == "warn" for x in r)


def test_packages_missing_hashed_and_sidecar(tmp_path: Path):
    s = spec()
    s["extra_packages"] = [{"name": "PipelineTextures"}, {"name": "Startup"}]
    game = game_dir(tmp_path, mods={"PipelineA": {"enabled": True, "spec": s}}, packages=("PipelineMeshes", "PipelineTextures"))
    (game / "WillowGame" / "CookedPCConsole" / "PipelineTextures.upk.uncompressed_size").write_text("1")
    c = ctx_for(tmp_path, game, mod="PipelineA")
    r = ck.check_packages(c)
    status = {x.detail.split(" ")[0]: (x.status, x.code) for x in r}
    assert status["PipelineMeshes.upk"] == ("ok", "")
    assert status["PipelineTextures.upk"] == ("fail", "F11")
    assert status["Startup"] == ("fail", "F12")
    s["extra_packages"] = [{"name": "PipelineNope"}]
    c.spec = s
    assert any(x.status == "fail" and "PipelineNope.upk missing" in x.detail for x in ck.check_packages(c))


def test_hashed_startup_size_mismatch(tmp_path: Path):
    game = game_dir(tmp_path, startup_size=99)
    c = ctx_for(tmp_path, game)
    r = ck.check_hashed(c)[0]
    assert r.status == "fail" and r.code == "F12"
    game = game_dir(tmp_path / "b", startup_size=100)
    c = ctx_for(tmp_path / "b", game)
    assert ck.check_hashed(c)[0].status == "ok"


def test_desktop_dialogs_and_foreground(tmp_path: Path):
    game = game_dir(tmp_path)
    c = ctx_for(tmp_path, game)
    c.desktop = (2560, 1440)
    c.windows = [("Borderlands 2", 5), ("OneDrive.exe - Application Error", 9), ("Windows Input Experience", 11)]
    c.foreground = ("Claude", 3)
    c.ini = {"Fullscreen": "True", "WindowedFullscreen": "False"}
    r = by_name(ck.check_desktop(c))
    assert r["resolution"].status == "warn" and r["resolution"].code == "F31"
    assert r["fullscreen mode"].status == "warn" and r["fullscreen mode"].code == "F32"
    # the touch-keyboard host is listed but not in front: only the real error box counts
    assert r["dialogs"].status == "fail" and "OneDrive" in r["dialogs"].detail and "Windows Input Experience" not in r["dialogs"].detail
    assert r["foreground"].status == "warn" and r["foreground"].code == "F32"
    c.foreground = ("Windows Input Experience", 11)
    r = by_name(ck.check_desktop(c))
    assert "Windows Input Experience" in r["dialogs"].detail
    c.foreground = ("Counter-Strike 2", 99)
    r = by_name(ck.check_desktop(c))
    assert r["foreground"].status == "fail" and "another game" in r["foreground"].detail
    c.windows, c.foreground, c.desktop = [], ("Borderlands 2", 5), (1920, 1080)
    c.ini = {"Fullscreen": "False", "WindowedFullscreen": "True"}
    r = by_name(ck.check_desktop(c))
    assert all(x.status == "ok" for x in r.values())


def test_part_path_typo_is_caught_before_launch(tmp_path: Path):
    game = game_dir(tmp_path, mods={"PipelineA": {"enabled": True, "spec": spec()}})
    c = ctx_for(tmp_path, game, mod="PipelineA", part_path="GD_Weap_AssaultRifles.Barrel.AK_Barrel")
    r = ck.check_part_path(c)[0]
    assert r.status == "fail" and r.code == "PARTPATH" and "did you mean GD_Weap_AssaultRifle.Barrel.AK_Barrel" in r.fix
    c.part_path = "GD_Weap_AssaultRifle.Barrel.AK_Barrel"
    assert ck.check_part_path(c)[0].status == "ok"
    # from the control file's part_name
    control = tmp_path / "control.json"
    control.write_text(json.dumps({"part_name": "AK_Body", "mod": "PipelineA"}), encoding="utf-8")
    c.part_path, c.control = None, control
    assert ck.check_part_path(c)[0].status == "ok"


def test_run_args_and_harness_options(tmp_path: Path):
    s = spec(harness_timing="ticks", harness_pose="inventory")
    s["options"].pop("harness_capture_view")
    game = game_dir(tmp_path, mods={"PipelineA": {"enabled": True, "spec": s}})
    c = ctx_for(tmp_path, game, mod="PipelineA", baseline=tmp_path / "missing.png", capture_view="first")
    r = by_name(ck.check_run_args(c))
    assert r["baseline"].status == "fail"
    assert r["harness timing"].status == "fail" and r["harness timing"].code == "F25"
    assert r["capture view"].status == "warn" and r["capture view"].code == "F23"
    assert r["harness pose"].status == "warn"


def test_save_checks(tmp_path: Path):
    game = game_dir(tmp_path)
    c = ctx_for(tmp_path, game)
    r = by_name(ck.check_save(c))
    assert r["save"].status == "ok"
    assert r["independent save copy"].status == "warn" and r["independent save copy"].code == "F30"
    c.save = tmp_path / "nope.sav"
    assert ck.check_save(c)[0].status == "fail"


def test_a_broken_check_never_takes_preflight_down(tmp_path: Path):
    def boom(ctx):  # noqa: ANN001
        raise RuntimeError("nope")
    r = ck.run_checks(ctx_for(tmp_path, game_dir(tmp_path)), [boom])
    assert r[0].status == "fail" and "nope" in r[0].detail


# --------------------------------------------------------------------------- dry run
@pytest.fixture(scope="module")
def catalog_present() -> None:
    if not (REPO / "catalog" / "parts.json").exists():
        pytest.skip("catalog/parts.json absent")


def test_dry_run_of_the_bent_barrel_harness(tmp_path: Path, catalog_present: None):
    from bl2_catalog import load_catalog
    from bl2_partgen import emit, load_spec
    sp = load_spec(REPO / "specs" / "bent_barrel_harness.json")
    out = emit(sp, tmp_path / "mod", catalog=load_catalog(REPO / "catalog" / "parts.json"))
    r = dry_run(out.out_dir)
    assert r.ok, r.lines()
    assert r.phases[:4] == [0, 1, 2, 3]
    assert list(r.registered.values())[0]["rooted"] is True


def test_dry_run_refuses_a_missing_mod(tmp_path: Path):
    r = dry_run(tmp_path / "nothing")
    assert not r.ok and "no __init__.py" in r.problems[0]


def test_dry_run_reports_an_unsupported_host(tmp_path: Path):
    d = tmp_path / "mod"
    d.mkdir()
    (d / "__init__.py").write_text("", encoding="utf-8")
    (d / "spec.json").write_text(json.dumps({"weapon_type": "SMG"}), encoding="utf-8")
    r = dry_run(d)
    assert not r.ok and "SMG" in r.problems[0] and "cannot judge" in r.problems[0]


@pytest.mark.parametrize("spec_name", ["ak47_harness.json", "awp_harness.json", "deagle_harness.json"])
def test_dry_run_of_a_real_weapon_spec_via_catalog_augmentation(tmp_path: Path, catalog_present: None, spec_name: str):
    """The AK and AWP specs reference templates, balances, pools and materials the fake
    world does not carry; the augmenter builds them from the catalog and the mod registers."""
    path = REPO / "specs" / spec_name
    if not path.exists():
        pytest.skip(f"{spec_name} absent")
    from bl2_catalog import load_catalog
    from bl2_partgen import emit, load_spec
    spec = load_spec(path)
    wav = spec.resolved_fire_sound_file()
    if wav is not None and not wav.exists():
        pytest.skip(f"{spec_name}: its fire sound {wav.name} is a local, non-redistributable file")
    out = emit(spec, tmp_path / "mod", catalog=load_catalog(REPO / "catalog" / "parts.json"))
    r = dry_run(out.out_dir)
    assert r.ok, r.lines()
    rec = list(r.registered.values())[0]
    assert rec["balances"] == 1 and rec["parts"] >= 6
    assert r.built and any(b.startswith("part ") for b in r.built)   # templates the fake world lacked
    assert any("Master_ClassMod" in a for a in r.assumed)


def test_cli(tmp_path: Path, capsys, catalog_present: None):
    game = game_dir(tmp_path, mods={"PipelineA": {"enabled": True, "spec": spec()}})
    code = cli_main(["--mod", "PipelineA", "--game", str(game), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] in ("GO", "GO (with warnings)", "NO GO")
    assert any(c["name"] == "mod enabled" and c["status"] == "ok" for c in out["checks"])
    assert code in (0, 1, 2)
    assert cli_main(["dry-run", str(tmp_path / "nothing")]) == 2
    assert "no __init__.py" in capsys.readouterr().out
