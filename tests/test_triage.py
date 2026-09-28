"""Tests for ``bl2_triage`` -- the verdict over a run's evidence.

Everything runs on synthetic reports, status records and log text; nothing reads the game
folder (``load_evidence`` is exercised with explicit paths into ``tmp_path`` and ``game=None``).
The point is to pin each discriminator to the shape of evidence that triggers it, and to pin
the verdict reduction: a passing run is BUILD OK whatever else was noticed, an instrument
failure never reads as a build failure, and a registration failure always does.

    python -m pytest tests/test_triage.py -q
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bl2_triage import evidence as ev_mod  # noqa: E402
from bl2_triage.__main__ import main as cli_main  # noqa: E402
from bl2_triage.evidence import DriverSlice, Evidence, InstalledMod, SdkSlice, parse_driver_log, parse_sdk_log  # noqa: E402
from bl2_triage.rules import CATALOG, triage  # noqa: E402
from bl2_triage.summarize import summarize_file, summarize_report, summarize_status  # noqa: E402

T0 = 1_789_949_000.0


# --------------------------------------------------------------------------- fixtures
def cond(status: str, summary: str = "", frac: float | None = None) -> dict:
    c = {"status": status, "summary": summary}
    if frac is not None:
        c["compare"] = {"diff_fraction": frac}
    return c


def report(**over) -> dict:
    rep = {
        "timestamp": "20260920-190747", "started": T0, "run": 8,
        "params": {"mod": "PipelineAWPHarness", "capture_view": "first",
                   "part_path": "GD_Weap_SniperRifles.Barrel.AWP_Barrel", "status": "scratch/x_status.json"},
        "conditions": {
            "changed_vs_baseline": cond("pass", "67% differs", 0.67),
            "stable_across_runs": cond("pass", "0.005%", 0.00005),
            "lint_clean": cond("pass", "-> 0"),
            "survives_reload": cond("pass", "1 weapon(s) still carry the part"),
        },
        "failure": None, "ok": True, "all_pass": True, "save_restored": True,
        "captures": {"run": "scratch/run_8.png"},
    }
    rep.update(over)
    return rep


def status(ok: bool = True, phases: bool = True, gap: float = 1.5, equip: str = "equipped slot 3") -> list[dict]:
    recs = [{"phase": "menu_setup", "mod": "PipelineAWPHarness", "ok": ok, "t": T0 + 20,
             "fragments": [{"fragment": "AWP_Barrel", "template": "SR_Barrel_Jakobs"}],
             "parts": [{"part": "GD_Weap_SniperRifles.Barrel.AWP_Barrel", "constructed": True}],
             "balances": [{"balance": "GD_Weap_SniperRifles.A_Weapons_Legendary.Sniper_Jakobs_5_AWP", "constructed": True,
                           "part_lists": {"BarrelPartData": ["x"]}, "pools": ["p"]}],
             "materials": []},
            {"load_hook": "ApplyPlayerSaveGameData", "save": "Save0005.sav", "restored": ["1:Barrel=x"], "missing": [], "t": T0 + 60}]
    if phases:
        t = T0 + 90
        recs += [{"phase": 0, "census_before": [], "t": t},
                 {"phase": 1, "stock_weapon": {}, "t": t + gap},
                 {"phase": 2, "new_weapon": {}, "equip_newest": equip, "t": t + 2 * gap},
                 {"phase": 3, "camera": "set (first person)", "done": True, "held": {"slot": 3, "ours": {"a": "b"}}, "t": t + 3 * gap}]
    return recs


def ev_with(rep: dict | None = None, recs: list[dict] | None = None, **fields) -> Evidence:
    e = Evidence(report=rep, status=recs or [])
    for k, v in fields.items():
        setattr(e, k, v)
    return e


# --------------------------------------------------------------------------- verdicts
def test_passing_run_is_build_ok_even_with_instrument_noise():
    e = ev_with(report(), status(), driver=DriverSlice(foreground=[("Claude", 1)], focus_failures=1))
    t = triage(e)
    assert t.verdict == "BUILD OK"
    assert any(f.code == "F32" for f in t.findings)   # still reported, not hidden


def test_registration_failure_is_build():
    recs = status(ok=False)
    recs[0]["parts"][0]["constructed"] = False
    recs[0]["error"] = "RuntimeError: template part missing\nTraceback..."
    rep = report(failure="DriverError: timeout after 180.0s waiting for menu_setup", ok=False, all_pass=False)
    t = triage(ev_with(rep, recs))
    assert t.verdict == "BUILD"
    f = next(f for f in t.findings if f.code == "REGFAIL")
    assert any("not constructed" in d for d in f.detail)
    assert any("RuntimeError" in d for d in f.detail)


def test_menu_never_reached_but_registered_is_instrument():
    rep = report(failure="DriverError: never reached the main menu within 180.0s", ok=False, all_pass=False,
                 conditions={"survives_reload": cond("fail", "no census")})
    sdk = SdkSlice(lines=["x"] * 50, sdk_loaded=True, registration_done=["PipelineAWP"], registration_at="2026-09-20 17:57:31Z")
    drv = DriverSlice(foreground=[("Windows Input Experience", 123)] * 47, focus_failures=47)
    t = triage(ev_with(rep, [], sdk=sdk, driver=drv))
    assert t.verdict == "INSTRUMENT"
    assert "build is fine" in t.headline
    codes = [f.code for f in t.findings]
    assert "F27" in codes and "REGFAIL" not in codes


def test_menu_never_reached_without_registration_is_regfail():
    rep = report(failure="DriverError: never reached the main menu within 180.0s", ok=False, all_pass=False,
                 conditions={"survives_reload": cond("fail", "no census")})
    sdk = SdkSlice(lines=["x"] * 50, sdk_loaded=True, tracebacks=["ModuleNotFoundError: No module named 'foo'"])
    t = triage(ev_with(rep, [], sdk=sdk))
    assert t.verdict == "BUILD"
    f = next(f for f in t.findings if f.code == "REGFAIL")
    assert "ModuleNotFoundError" in f.detail[0]


def test_menu_never_reached_with_no_evidence_is_not_blamed_on_the_build():
    rep = report(failure="DriverError: never reached the main menu within 180.0s", ok=False, all_pass=False,
                 conditions={"survives_reload": cond("fail", "no census")})
    t = triage(ev_with(rep, [], driver=DriverSlice(foreground=[("OneDrive.exe - Application Error", 5)], focus_failures=9)))
    assert t.verdict == "INSTRUMENT"
    assert not any(f.code == "REGFAIL" for f in t.findings)


def test_agent_window_is_f32_and_dialog_is_f27():
    drv = DriverSlice(foreground=[("Claude", 1), ("Claude", 1), ("OneDrive.exe - Application Error", 7)], focus_failures=3)
    t = triage(ev_with(report(), status(), driver=drv))
    by = {f.code: f for f in t.findings}
    assert "Claude" in by["F32"].evidence and "2 time(s)" in by["F32"].evidence
    assert "OneDrive" in by["F27"].evidence


def test_resolution_is_a_note_when_the_run_passed_and_f31_when_probes_went_blind():
    drv = DriverSlice(captures=[("run_8.png", 2560, 1440)])
    t = triage(ev_with(report(), status(), driver=drv))
    f = next(f for f in t.findings if f.code == "F31")
    assert f.kind == "info"
    rep = report(ok=False, all_pass=False)
    rep["conditions"]["stable_across_runs"] = cond("fail", "77%", 0.77)
    drv = DriverSlice(captures=[("run_8.png", 2560, 1440)], menu_never_confirmed=True, menu_probe_zero=40)
    t = triage(ev_with(rep, status(), driver=drv))
    assert t.verdict == "INSTRUMENT"
    assert any(f.code == "F31" and f.kind == "instrument" for f in t.findings)


def test_third_person_stability_range_is_f22_and_old_reports_default_to_third():
    rep = report(ok=False, all_pass=False)
    rep["params"].pop("capture_view")
    rep["conditions"]["stable_across_runs"] = cond("fail", "1.4%", 0.014)
    t = triage(ev_with(rep, status()))
    assert t.verdict == "INSTRUMENT"
    assert any(f.code == "F22" for f in t.findings)
    rep["params"]["capture_view"] = "first"
    t = triage(ev_with(rep, status()))
    assert not any(f.code == "F22" for f in t.findings)
    # a first-person report from before the sway-tolerant compare is F33 ...
    assert any(f.code == "F33" for f in t.findings) and t.verdict == "INSTRUMENT"
    # ... one made with the tolerance is not explained by anything
    rep["conditions"]["stable_across_runs"]["compare"]["tolerance"] = 2
    t = triage(ev_with(rep, status()))
    assert not any(f.code in ("F22", "F33") for f in t.findings)
    assert t.verdict == "UNDETERMINED"


def test_elemental_roll_hint():
    rep = report(ok=False, all_pass=False, held={"ours": {"ElementalPartDefinition": "GD_Weap_AssaultRifle.elemental.AR_Elemental_Slag"}})
    rep["conditions"]["stable_across_runs"] = cond("fail", "2.3%", 0.023)
    t = triage(ev_with(rep, status()))
    assert any(f.code == "ELEMENTAL" for f in t.findings)


def test_identical_burst_is_f20():
    t = triage(ev_with(report(ok=False, all_pass=False, conditions={"changed_vs_baseline": cond("fail", "0%", 0.0)}),
                       status(), burst_hashes=["abc"] * 5))
    assert t.verdict == "INSTRUMENT"
    assert any(f.code == "F20" for f in t.findings)


def test_view_toggle_after_phase3_is_f23():
    recs = status()
    recs.append({"keybind": "F12", "behind_view": True, "t": T0 + 100})
    t = triage(ev_with(report(), recs))
    assert any(f.code == "F23" for f in t.findings)


def test_tick_timed_phases_are_f25():
    t = triage(ev_with(report(), status(gap=0.08)))
    assert any(f.code == "F25" for f in t.findings)
    t = triage(ev_with(report(), status(gap=1.5)))
    assert not any(f.code == "F25" for f in t.findings)


def test_full_backpack_signature():
    t = triage(ev_with(report(), status(equip="no weapon to equip")))
    assert any(f.code == "BACKPACK" for f in t.findings)


def test_reload_census_timeout_is_instrument_not_build():
    rep = report(failure="DriverError: timeout after 240.0s waiting for second phase-0 census in x.json", ok=False, all_pass=False)
    rep["conditions"]["survives_reload"] = cond("fail", "no reload census")
    t = triage(ev_with(rep, status()))
    assert t.verdict == "INSTRUMENT"
    assert any(f.code == "RELOAD" for f in t.findings)


def test_part_path_typo_is_caught():
    rep = report(ok=False, all_pass=False)
    rep["params"]["part_path"] = "GD_Weap_AssaultRifles.Barrel.AK_Barrel"
    rep["conditions"]["survives_reload"] = cond("fail", "no weapon with ... in the post-reload census")
    rep["census_after_reload"] = [{"barrel": "GD_Weap_AssaultRifle.Barrel.AK_Barrel", "ours": {"BarrelPartDefinition": "x"}}]
    t = triage(ev_with(rep, status()))
    assert t.verdict == "INSTRUMENT"
    f = next(f for f in t.findings if f.code == "PARTPATH")
    assert "AssaultRifles" in f.evidence


def test_missing_parts_on_load_is_f29():
    recs = status()
    recs[1]["missing"] = ["GD_Weap_SniperRifles.Barrel.AWP_Barrel"]
    rep = report(ok=False, all_pass=False)
    rep["conditions"]["survives_reload"] = cond("fail", "gone")
    t = triage(ev_with(rep, recs))
    assert t.verdict == "BUILD"
    assert any(f.code == "F29" for f in t.findings)
    # ... unless every missing path belongs to another mod's weapon: a parked Armory's AK in the
    # save while the Boxgun harness runs alone (seen on Boxgun run 1) is a note, not a build fault
    spec = {"parts": [{"outer": "GD_Weap_SniperRifles.Barrel", "part_name": "AWP_Barrel"}]}
    recs[1]["missing"] = ["GD_Weap_AssaultRifle.Barrel.AK_Barrel", "GD_Weap_AssaultRifle.Body.AK_Body"]
    t = triage(ev_with(report(), recs, spec=spec))
    assert t.verdict == "BUILD OK" and not any(f.code == "F29" for f in t.findings)
    assert any("OTHER weapons" in f.evidence for f in t.findings)


def test_spec_options_off_are_named():
    rep = report(ok=False, all_pass=False)
    rep["conditions"]["survives_reload"] = cond("fail", "gone")
    t = triage(ev_with(rep, status(), spec={"options": {"save_roundtrip": False, "validate_override": False}}))
    codes = {f.code for f in t.findings}
    assert {"F18", "F19"} <= codes


def test_inventory_pose_reload_failure():
    rep = report(ok=False, all_pass=False)
    rep["conditions"]["survives_reload"] = cond("fail", "gone")
    t = triage(ev_with(rep, status(), spec={"options": {"harness_pose": "inventory"}}))
    assert any(f.code == "INVENTORY" for f in t.findings)
    assert t.verdict == "INSTRUMENT"


def test_crash_signatures():
    sdk = SdkSlice(lines=["a", "b"], sdk_loaded=True, lines_after_init=1)
    drv = DriverSlice(process_exited=True, exited_after_s=3.2)
    rep = report(failure="DriverError: game exited while waiting for menu_setup", ok=False, all_pass=False)
    t = triage(ev_with(rep, [], sdk=sdk, driver=drv))
    assert t.verdict == "BUILD"
    assert any(f.code == "F12" for f in t.findings)
    t = triage(ev_with(rep, [], launch_log_fatal=["Critical: Ran out of virtual memory"]))
    assert any(f.code == "F17" for f in t.findings)


def test_lint_errors_are_build():
    rep = report(ok=False, all_pass=False)
    rep["conditions"]["lint_clean"] = {"status": "fail", "summary": "-> 1", "output": ["  [ERROR  ] L2: fragment name collides", "  [note   ] L6: x"]}
    t = triage(ev_with(rep, status()))
    assert t.verdict == "BUILD"
    f = next(f for f in t.findings if f.code == "LINT")
    assert "L2" in f.detail[0]


def test_mod_state_rules():
    mods = [InstalledMod("PipelineAWPHarness", None, ["SniperRifle"], [], True, False),
            InstalledMod("PipelineArmory", True, ["AssaultRifle", "SniperRifle"], ["ak47", "awp"], True, False),
            InstalledMod("PipelineAWP", True, ["SniperRifle"], [], False, True)]
    t = triage(ev_with(report(), status(), mods=mods))
    codes = [f.code for f in t.findings]
    assert "F8" in codes      # the mod under test has no settings file
    assert "RULE3" in codes   # two enabled mods on SniperRifle
    # one Armory carrying two weapons on the same host is not a rule-3 clash with itself
    solo = [InstalledMod("PipelineArmory", True, ["AssaultRifle", "AssaultRifle", "SniperRifle"], ["ak47", "boxgun", "awp"], True, False)]
    assert not any(f.code == "RULE3" for f in triage(ev_with(report(), status(), mods=solo)).findings)
    assert "F28" in codes     # PipelineAWP lacks the marker
    e = ev_with(rep := report(failure="DriverError: timeout after 180.0s waiting for menu_setup", ok=False, all_pass=False),
                [], mods=mods[:1])
    rep["conditions"] = {"survives_reload": cond("fail", "x")}
    assert triage(e).verdict == "CONFIG"


def test_save_not_restored_is_flagged():
    t = triage(ev_with(report(save_restored=False, save_backup="backup/saves/x"), status()))
    assert any(f.code == "SAVE" for f in t.findings)


def test_every_catalog_entry_has_kind_and_fix():
    for code, entry in CATALOG.items():
        assert entry["kind"] in {"instrument", "build", "config", "info"}, code
        assert entry["symptom"] and entry["fix"], code


def test_a_broken_rule_never_takes_the_verdict_down(monkeypatch):
    from bl2_triage import rules
    def boom(ev):  # noqa: ANN001
        raise ValueError("nope")
    monkeypatch.setattr(rules, "RULES", [*rules.RULES, boom])
    t = triage(ev_with(report(), status()))
    assert t.verdict == "BUILD OK"
    assert any(f.code == "RULE" and "boom" in f.evidence for f in t.findings)


# --------------------------------------------------------------------------- parsers
DRIVER_TEXT = """2026-09-20 16:10:00 +    0.0s launched Borderlands2.exe pid=111 key_mode=scancode
2026-09-20 16:10:05 +    5.0s capture menu_probe.png (saved C:\\x\\menu_probe.png 1920x1080)
2026-09-20 16:10:05 +    5.0s menu probe: yellow=0 in (140, 125, 330, 185) -> False
2026-09-20 16:10:06 +    6.0s focus -> False
2026-09-20 16:10:06 +    6.0s foreground is 'Windows Input Experience' (pid 222)
2026-09-20 16:13:00 +  180.0s menu probe never confirmed; continuing on the timer
2026-09-20 16:13:10 +  190.0s process exited
2026-09-20 16:13:11 +  191.0s save restored, verified=True
2026-09-20 16:20:00 +    0.0s launched Borderlands2.exe pid=333 key_mode=scancode
2026-09-20 16:20:09 +    9.0s capture run_2.png (saved C:\\x\\run_2.png 2560x1440)
2026-09-20 16:23:00 +  180.0s process exited
"""

SDK_TEXT = """date       time          thread                                           location@line    v|
2026-09-20 20:10:00.000Z   812c                                    unrealsdk::init@59   INFO| unrealsdk v3.2.0 (b1852aa4)
2026-09-20 20:10:00.100Z   774c                                  pyunrealsdk::init@63   INFO| pyunrealsdk v1.10.0 (c72c5558) loaded
2026-09-20 20:10:15.000Z   774c              ~ sdk_mods\\PipelineArmory\\__init__.py@96   INFO| [PipelineArmory] armed: 2 weapon(s)
2026-09-20 20:10:16.000Z   774c      ~ Win32/Plugins/../../../sdk_mods/__main__.py@409   ERR| Traceback (most recent call last):
2026-09-20 20:10:16.001Z   774c      ~ Win32/Plugins/../../../sdk_mods/__main__.py@409   ERR|   File "x.py", line 1
2026-09-20 20:10:16.002Z   774c      ~ Win32/Plugins/../../../sdk_mods/__main__.py@409   ERR| ModuleNotFoundError: No module named 'Mods.UserFeedback'
2026-09-20 20:10:20.000Z   81b0          ~ sdk_mods\\PipelineArmory\\weapons\\ak47.py@297  INFO| [PipelineAK47] registration done
"""


def test_parse_driver_log_slices_the_run_nearest_the_report():
    first = time.mktime(time.strptime("2026-09-20 16:10:00", "%Y-%m-%d %H:%M:%S"))
    d = parse_driver_log(DRIVER_TEXT, started=first - 3)
    assert d.pid == 111
    assert d.foreground == [("Windows Input Experience", 222)]
    assert d.focus_failures == 1 and d.menu_probe_zero == 1 and d.menu_never_confirmed
    assert d.captures == [("menu_probe.png", 1920, 1080)]
    assert d.process_exited and d.exited_after_s == 190.0 and d.save_restored is True
    second = time.mktime(time.strptime("2026-09-20 16:20:00", "%Y-%m-%d %H:%M:%S"))
    d2 = parse_driver_log(DRIVER_TEXT, started=second + 2)
    assert d2.pid == 333 and d2.captures == [("run_2.png", 2560, 1440)]
    assert parse_driver_log(DRIVER_TEXT).pid == 333   # no report: the last launch


def test_parse_sdk_log():
    s = parse_sdk_log(SDK_TEXT)
    assert s.sdk_loaded
    assert s.registration_done == ["PipelineAK47"]
    assert s.registration_at.startswith("2026-09-20 20:10:20")
    assert s.tracebacks == ["ModuleNotFoundError: No module named 'Mods.UserFeedback'"]
    assert s.lines_after_init == 5
    assert s.init_at is not None


# --------------------------------------------------------------------------- loader + CLI
def test_load_evidence_ignores_logs_from_other_launches(tmp_path: Path):
    rep = report()
    rep["params"]["status"] = str(tmp_path / "st.json")
    (tmp_path / "rep.json").write_text(json.dumps(rep), encoding="utf-8")
    (tmp_path / "st.json").write_text(json.dumps(status()), encoding="utf-8")
    (tmp_path / "driver.log").write_text(DRIVER_TEXT, encoding="utf-8")   # launches hours away from T0
    (tmp_path / "sdk.log").write_text(SDK_TEXT, encoding="utf-8")
    e = ev_mod.load_evidence(report=tmp_path / "rep.json", driver_log=tmp_path / "driver.log",
                             sdk_log=tmp_path / "sdk.log", launch_log=None, game=None)
    assert len(e.status) == 6
    assert e.driver.lines == [] and e.sdk.lines == []
    assert len(e.notes) == 2
    assert triage(e).verdict == "BUILD OK"


def test_cli_summarize_and_triage(tmp_path: Path, capsys):
    rep = report()
    rep["params"]["status"] = str(tmp_path / "st.json")
    (tmp_path / "verify_report_20260920-190747.json").write_text(json.dumps(rep), encoding="utf-8")
    (tmp_path / "st.json").write_text(json.dumps(status()), encoding="utf-8")
    assert cli_main(["summarize", str(tmp_path / "st.json")]) == 0
    out = capsys.readouterr().out
    assert "menu_setup ok=True" in out and "harness phases: [0, 1, 2, 3]" in out
    assert len(out.splitlines()) < 20
    assert cli_main(["summarize", str(tmp_path / "verify_report_20260920-190747.json")]) == 0
    assert "PASS  survives_reload" in capsys.readouterr().out
    code = cli_main(["--report", str(tmp_path / "verify_report_20260920-190747.json"), "--no-game",
                     "--driver-log", str(tmp_path / "none.log"), "--sdk-log", str(tmp_path / "none.log"),
                     "--launch-log", str(tmp_path / "none.log")])
    out = capsys.readouterr().out
    assert code == 0 and "VERDICT: BUILD OK" in out
    code = cli_main(["--report", str(tmp_path / "verify_report_20260920-190747.json"), "--no-game", "--json",
                     "--driver-log", str(tmp_path / "none.log"), "--sdk-log", str(tmp_path / "none.log"),
                     "--launch-log", str(tmp_path / "none.log")])
    assert json.loads(capsys.readouterr().out)["verdict"] == "BUILD OK"


def test_summaries_are_short():
    assert len(summarize_report(report())) <= 12
    assert len(summarize_status(status())) <= 16


def test_invisible_weapon_is_f34_even_when_every_condition_passes():
    """Laser runs 6/7: empty hands pass every pixel condition; the census knows better."""
    rep = report()
    rep["params"]["package"] = "PipelineMeshesLaser"
    recs = status() + [{"phase": 3, "held": {
        "ours": {"BarrelPartDefinition": "GD_Weap_AssaultRifle.Barrel.LASER_Barrel"},
        "balance": "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Dahl_5_LASER",
        "FirstPersonMesh": "Weap_AssaultRifles.GestaltDef_AssaultRifle_GestaltSkeletalMesh"}}]
    t = triage(ev_with(rep, recs))
    assert t.verdict == "BUILD" and any(f.code == "F34" for f in t.findings)
    recs[-1]["held"]["FirstPersonMesh"] = "PipelineMeshesLaser.PL_AR_Gestalt_Mesh"
    t = triage(ev_with(rep, recs))
    assert not any(f.code == "F34" for f in t.findings)


def test_refused_package_is_f35():
    recs = status(ok=False)
    recs[0]["error"] = ("AttributeError(\"'NoneType' object has no attribute '_path_name'\")\n"
                        "  File x, in load_extra_packages\n")
    t = triage(ev_with(report(ok=False, all_pass=False), recs))
    assert any(f.code == "F35" for f in t.findings)
