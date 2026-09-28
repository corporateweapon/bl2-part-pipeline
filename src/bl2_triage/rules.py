"""The discriminators from ``docs/FAILURE_MODES.md`` as functions over :class:`Evidence`.

Every rule returns zero or more :class:`Finding`; :func:`triage` runs them all and reduces
them to one verdict. The rules deliberately only look at what the evidence loader already
digested -- if a rule needs something new, extend ``evidence.py`` rather than reading files
here, so the tests can keep building ``Evidence`` objects by hand.

Verdicts, in the order they are decided:

``BUILD OK``      every scored condition passed
``BUILD``         at least one finding says the build, the spec or the registration is wrong
``INSTRUMENT``    the run failed but the mod registered / the build is fine; findings say why
``CONFIG``        the game folder is in a state the rules refuse (F8, rule 3, F28)
``UNDETERMINED``  a condition failed and no rule claims it; the findings list what was checked
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from bl2_triage.evidence import CALIBRATED, Evidence

CATALOG_PATH = Path(__file__).with_name("failure_modes.json")
CATALOG: dict[str, dict[str, str]] = {
    k: v for k, v in json.loads(CATALOG_PATH.read_text(encoding="utf-8")).items() if not k.startswith("_")
}

PASS, FAIL, SKIP = "pass", "fail", "skip"


@dataclass
class Finding:
    code: str                 # F-number or a named catalog key, or "INFO"
    kind: str                 # instrument | build | config | info
    evidence: str             # one line: what was seen
    fix: str = ""             # one line: what to do (from the catalog unless overridden)
    detail: list[str] = field(default_factory=list)

    @classmethod
    def from_catalog(cls, code: str, evidence: str, **kw: Any) -> "Finding":
        entry = CATALOG.get(code, {})
        return cls(code=code, kind=kw.pop("kind", entry.get("kind", "info")), evidence=evidence,
                   fix=kw.pop("fix", entry.get("fix", "")), **kw)


def _frac(cond: dict[str, Any]) -> float | None:
    cmp = cond.get("compare") or {}
    v = cmp.get("diff_fraction")
    return float(v) if v is not None else None


def registration_known(ev: Evidence) -> bool | None:
    """True when something proves the mod registered, False when the evidence says it did not,
    None when nothing for this run can tell (no SDK log, no status records, no report condition)."""
    if ev.sdk.registration_done:
        return True
    setup = ev.menu_setup
    if setup is not None:
        return setup.get("ok") is not False and not setup.get("error")
    reg = ev.condition("registered")
    if reg.get("status") == PASS:
        return True
    if reg.get("status") == FAIL:
        return False
    if ev.sdk.lines and ev.sdk.sdk_loaded:
        return False
    return None


# --------------------------------------------------------------------------- rules
def rule_registration(ev: Evidence) -> list[Finding]:
    out: list[Finding] = []
    setup = ev.menu_setup
    if setup is None:
        if ev.sdk.registration_done:
            out.append(Finding("INFO", "info",
                               f"unrealsdk.log: registration done for {', '.join(ev.sdk.registration_done)}"
                               + (f" at {ev.sdk.registration_at}" if ev.sdk.registration_at else "")))
        elif ev.condition("registered").get("status") == PASS:
            out.append(Finding("INFO", "info", "report: " + str(ev.condition("registered").get("summary"))))
        elif registration_known(ev) is False:
            out.append(Finding.from_catalog(
                "REGFAIL", "no menu_setup record in the status file and no 'registration done' in unrealsdk.log",
                detail=ev.sdk.pipeline_errors[:3] or ev.sdk.tracebacks[:3]))
        return out
    problems: list[str] = []
    for f in setup.get("fragments") or []:
        if isinstance(f, dict) and (f.get("error") or f.get("skipped")):
            problems.append(f"fragment {f.get('fragment')}: {f.get('error') or f.get('skipped')}")
    for p in setup.get("parts") or []:
        if isinstance(p, dict) and p.get("constructed") is False:
            problems.append(f"part {p.get('part')}: not constructed")
    for b in setup.get("balances") or []:
        if isinstance(b, dict) and b.get("constructed") is False:
            problems.append(f"balance {b.get('balance')}: not constructed")
    for m in setup.get("materials") or []:
        if isinstance(m, dict) and m.get("constructed") is False:
            problems.append(f"material {m.get('material')}: not constructed")
    if setup.get("error"):
        problems.append(str(setup["error"]).splitlines()[0][:200])
    if setup.get("ok") is not False and not [p for p in problems if "already" not in p]:
        n_frag = len(setup.get("fragments") or [])
        n_part = len(setup.get("parts") or [])
        out.append(Finding("INFO", "info",
                           f"registered at the menu: {n_frag} fragment(s), {n_part} part(s), "
                           f"{len(setup.get('balances') or [])} balance(s) (menu_setup ok)"))
    else:
        out.append(Finding.from_catalog("REGFAIL", "menu_setup did not complete clean", detail=problems[:6]))
    return out


def rule_mod_state(ev: Evidence) -> list[Finding]:
    out: list[Finding] = []
    name = ev.mod_name
    mine = next((m for m in ev.mods if m.name == name), None) if name else None
    if mine is not None:
        if mine.enabled is None:
            out.append(Finding.from_catalog("F8", f"{name}: no sdk_mods/settings/{name}.json"))
        elif mine.enabled is False:
            out.append(Finding.from_catalog("F8", f"{name}: settings say enabled=false"))
    # F28 on every ENABLED build (a stale parked build cannot hurt until it is enabled) and on the mod under test
    for m in ev.mods:
        if not (m.enabled or m is mine):
            continue
        if m.has_saves_marker is False:
            out.append(Finding.from_catalog("F28", f"{m.name} lacks the _pipeline_saves marker (emitted before 2026-09-20)"))
        elif m.stale_vs_templates:
            out.append(Finding.from_catalog("F28", f"{m.name} is older than src/bl2_partgen/templates.py",
                                            kind="info", fix="re-emit if templates.py changed meaningfully; the file date alone is the hint"))
    # rule 3: one pipeline mod per host gestalt
    enabled = [m for m in ev.mods if m.enabled]
    by_type: dict[str, list[str]] = {}
    for m in enabled:
        for t in dict.fromkeys(m.weapon_types):        # an Armory lists a host once per weapon
            by_type.setdefault(t, []).append(m.name)
    for t, names in by_type.items():
        if len(names) > 1:
            out.append(Finding.from_catalog("RULE3", f"{t}: {', '.join(names)} are all enabled"))
    return out


def rule_crash(ev: Evidence) -> list[Finding]:
    out: list[Finding] = []
    d, s = ev.driver, ev.sdk
    if s.sdk_loaded and s.lines_after_init <= 3 and d.process_exited and (d.exited_after_s or 0) < 30:
        out.append(Finding.from_catalog("F12", f"game exited {d.exited_after_s}s after launch; unrealsdk.log ends right after pyunrealsdk init"))
    for line in ev.launch_log_fatal:
        low = line.lower()
        if "virtual memory" in low:
            out.append(Finding.from_catalog("F17", f"Launch.log: {line}"))
        elif "fatal" in low:
            out.append(Finding.from_catalog("F16", f"Launch.log: {line}"))
    if ev.failure and "game exited while waiting" in ev.failure and not out:
        out.append(Finding("CRASH", "build", f"driver: {ev.failure}",
                           fix="the game died mid-run; read the last 40 lines of unrealsdk.log and Launch.log",
                           detail=s.tracebacks[:3]))
    return out


def _physical_desktop() -> tuple[int, int] | None:
    """The primary display in physical pixels (DPI-aware), or None off Windows."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001 - older Windows / already set
            user32.SetProcessDPIAware()
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:  # noqa: BLE001
        return None


def rule_resolution(ev: Evidence) -> list[Finding]:
    """F31. Since ``compare.scale_box`` every box is rescaled to the capture, so a foreign
    resolution is only a finding when the probes actually went blind; otherwise a note."""
    bad = sorted({(w, h) for _, w, h in ev.driver.captures if (w, h) != CALIBRATED})
    if not bad:
        return []
    seen = ", ".join(f"{w}x{h}" for w, h in bad)
    stable = _frac(ev.condition("stable_across_runs")) or 0.0
    blind = ev.driver.menu_never_confirmed or (ev.driver.menu_probe_zero > 5 and ev.condition("stable_across_runs").get("status") == FAIL)
    physical = _physical_desktop()
    cropped = [f"{w}x{h}" for w, h in bad if physical and w < physical[0] and h < physical[1]
               and abs(w / h - physical[0] / physical[1]) < 0.01]
    if (blind or stable > 0.5) and cropped:
        return [Finding.from_catalog("F36", f"captures came back {', '.join(cropped)} on a "
                                     f"{physical[0]}x{physical[1]} desktop: a DPI crop, not a scaled frame")]
    if blind or stable > 0.5:
        return [Finding.from_catalog("F31", f"captures came back {seen} (calibrated at {CALIBRATED[0]}x{CALIBRATED[1]}) and the probes went blind")]
    return [Finding("F31", "info", f"captures at {seen}; boxes rescaled from {CALIBRATED[0]}x{CALIBRATED[1]} by compare.scale_box")]


def rule_focus(ev: Evidence) -> list[Finding]:
    d = ev.driver
    if not d.foreground and not d.focus_failures:
        return []
    out: list[Finding] = []
    counts: dict[str, int] = {}
    for title, _ in d.foreground:
        counts[title] = counts.get(title, 0) + 1
    for title, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if "claude" in title.lower() or "code" == title.lower():
            out.append(Finding.from_catalog("F32", f"foreground was {title!r} {n} time(s) (the agent's own window)"))
        else:
            out.append(Finding.from_catalog("F27", f"foreground was {title!r} {n} time(s) while the game was alive"))
    if not out and d.focus_failures:
        out.append(Finding.from_catalog("F20", f"{d.focus_failures} focus failure(s) with no foreground owner logged"))
    return out


def rule_stale_frames(ev: Evidence) -> list[Finding]:
    h = ev.burst_hashes
    if len(h) >= 2 and len(set(h)) == 1:
        return [Finding.from_catalog("F20", f"all {len(h)} frames of the capture burst are byte-identical")]
    if ev.failure and "would not stay in the foreground" in ev.failure:
        return [Finding.from_catalog("F20", f"driver: {ev.failure}")]
    return []


def rule_stability(ev: Evidence) -> list[Finding]:
    cond = ev.condition("stable_across_runs")
    if cond.get("status") != FAIL:
        return []
    frac = _frac(cond)
    view = (ev.report or {}).get("params", {}).get("capture_view") or "third"
    out: list[Finding] = []
    if view == "third" and frac is not None and 0.005 <= frac <= 0.03:
        out.append(Finding.from_catalog("F22", f"third-person crop differs by {frac:.2%} (pawn idle animation range)"))
    tolerance = (cond.get("compare") or {}).get("tolerance", 0)
    if view == "first" and not tolerance and frac is not None and 0.005 <= frac <= 0.05:
        out.append(Finding.from_catalog("F33", f"first-person crop differs by {frac:.2%} with no sway tolerance (pre-F33 compare)"))
    if frac is not None and frac > 0.5 and (ev.driver.menu_never_confirmed or ev.driver.menu_probe_zero > 5):
        out.append(Finding.from_catalog("F31", f"stable_across_runs at {frac:.0%} with blind menu probes: the boxes miss the weapon"))
    held = (ev.report or {}).get("held") or {}
    ours = held.get("ours") or {}
    elem = ours.get("ElementalPartDefinition") or held.get("ElementalPartDefinition")
    if elem and "None" not in str(elem):
        out.append(Finding.from_catalog("ELEMENTAL", f"held weapon's elemental part is {elem}"))
    if not out and frac is not None:
        out.append(Finding("INFO", "info",
                           f"stable_across_runs failed at {frac:.2%}; no instrument rule claims it (a real mesh/texture change, or a different map spot)"))
    return out


def rule_view_toggle(ev: Evidence) -> list[Finding]:
    done_t = next((r.get("t") for r in ev.status if r.get("done") is True), None)
    if done_t is None:
        return []
    for r in ev.status:
        if r.get("keybind") == "F12" and r.get("behind_view") is True and float(r.get("t", 0)) >= float(done_t):
            return [Finding.from_catalog("F23", "an F12 view-toggle record landed after phase 3 (behind_view=true)")]
    return []


def rule_phase_timing(ev: Evidence) -> list[Finding]:
    ph = ev.phases()
    ts = [float(r["t"]) for r in ph[:4] if "t" in r]
    if len(ts) >= 3:
        deltas = [b - a for a, b in zip(ts, ts[1:])]
        if all(d < 0.9 for d in deltas):
            return [Finding.from_catalog("F25", "harness phases 0->1->2->3 completed " + ", ".join(f"{d:.2f}s" for d in deltas) + " apart")]
    return []


def rule_backpack(ev: Evidence) -> list[Finding]:
    for r in ev.phases():
        eq = r.get("equip_newest")
        if isinstance(eq, str) and not re.search(r"slot \d", eq):
            return [Finding.from_catalog("BACKPACK", f"phase {r.get('phase')}: equip_newest -> {eq!r}")]
    return []


def rule_inventory(ev: Evidence) -> list[Finding]:
    if ev.options().get("harness_pose") == "inventory" and ev.condition("survives_reload").get("status") == FAIL:
        return [Finding.from_catalog("INVENTORY", "harness_pose=inventory and survives_reload failed")]
    return []


def rule_save_roundtrip(ev: Evidence) -> list[Finding]:
    out: list[Finding] = []
    ours = set()
    for part in ((ev.spec or {}).get("parts") or []):
        if isinstance(part, dict) and part.get("outer") and part.get("part_name"):
            ours.add(f"{part['outer']}.{part['part_name']}")
    for r in ev.status:
        if r.get("load_hook") and r.get("missing"):
            missing = [str(m) for m in r["missing"]]
            foreign = [m for m in missing if m not in ours]
            if ours and len(foreign) == len(missing):
                # the save carries weapons another pipeline mod registered (its records are shared
                # per package); with that mod parked they cannot be restored -- expected while a
                # single harness is the only enabled build, and the loop restores the save after
                out.append(Finding("INFO", "info",
                                   f"load hook could not restore {len(missing)} part path(s) of OTHER weapons in the save "
                                   f"(e.g. {foreign[0]}); their mod is parked, the loop restores the save afterwards",
                                   detail=foreign[:3]))
            else:
                out.append(Finding.from_catalog("F29", f"load hook could not restore {len(missing)} part path(s)",
                                                detail=missing[:5]))
            break
    if ev.condition("survives_reload").get("status") == FAIL:
        opts = ev.options()
        if opts and opts.get("save_roundtrip") is False:
            out.append(Finding.from_catalog("F18", "spec options.save_roundtrip is false"))
        if opts and opts.get("validate_override") is False:
            out.append(Finding.from_catalog("F19", "spec options.validate_override is false"))
        if not any(r.get("load_hook") for r in ev.status) and ev.status:
            out.append(Finding("INFO", "build", "no load-hook record at all: the reload never ran through the mod's hooks",
                               fix="check the mod stayed enabled across the quit; F8 if its settings file went missing"))
    return out


def rule_part_path(ev: Evidence) -> list[Finding]:
    """survives_reload failed, yet the post-reload census holds a custom weapon: the
    ``--part-path`` the loop was told to look for is not the path the build registered."""
    if ev.condition("survives_reload").get("status") != FAIL:
        return []
    want = ((ev.report or {}).get("params") or {}).get("part_path")
    census = (ev.report or {}).get("census_after_reload") or []
    have = sorted({str(w.get("barrel")) for w in census if isinstance(w, dict) and w.get("ours")})
    if want and have and want not in have:
        return [Finding("PARTPATH", "instrument",
                        f"post-reload census holds {', '.join(have)} but --part-path asked for {want}",
                        fix="rerun with --part-path set to the barrel path the build registers (see menu_setup parts)")]
    return []


def rule_lint(ev: Evidence) -> list[Finding]:
    cond = ev.condition("lint_clean")
    if cond.get("status") != FAIL:
        return []
    errs = [l.strip() for l in cond.get("output") or [] if "[ERROR" in l]
    return [Finding("LINT", "build", f"bl2_lint failed with {len(errs)} error(s)", fix="fix the spec; --lint-only shows the same list",
                    detail=errs[:5])]


def rule_driver_failure(ev: Evidence) -> list[Finding]:
    """The report's ``failure`` text, classified with what the SDK log says about registration."""
    f = ev.failure
    if not f:
        return []
    registered = registration_known(ev)
    if "second phase-0" in f or "phase-0 census" in f:
        return [Finding("RELOAD", "instrument",
                        f"driver: {f}",
                        fix="the save-and-quit keys did not reach the pause menu (an open item card with harness_pose "
                            "inventory, a dialog, or lost focus); nothing about the build is decided by this")]
    if "never reached the main menu" in f or "waiting for menu_setup" in f or "waiting for done" in f:
        if registered is True:
            return [Finding("INFO", "instrument", f"driver: {f} -- but the mod had registered, so the build is fine",
                            fix="rerun; the focus/resolution findings above say what to clear first")]
        if registered is None:
            return [Finding("INFO", "instrument", f"driver: {f}; registration could not be checked (no unrealsdk.log or status records for this run)",
                            fix="rerun with the game folder readable, or pass --sdk-log for this run's copy")]
        return [Finding.from_catalog("REGFAIL", f"driver: {f}; no registration in unrealsdk.log", detail=ev.sdk.tracebacks[:3])]
    if "already running" in f:
        return [Finding("INFO", "instrument", f"driver: {f}", fix="quit the game (or kill Borderlands2.exe) and rerun")]
    if "would not stay in the foreground" in f or "capture" in f:
        return []  # F20/F27/F32 cover it
    return [Finding("INFO", "instrument" if registered else "build", f"driver: {f}")]


def rule_launcher(ev: Evidence) -> list[Finding]:
    return [Finding.from_catalog("F10", "a stray Launcher.exe was killed before launch", kind="info")] if ev.driver.killed_launcher else []


def rule_save_restore(ev: Evidence) -> list[Finding]:
    if ev.report and ev.report.get("save_restored") is False:
        return [Finding("SAVE", "config", "the character save was NOT restored after the run",
                        fix=f"restore by hand from {ev.report.get('save_backup')}")]
    return []


def _census_rows(obj: Any):
    if isinstance(obj, dict):
        if "FirstPersonMesh" in obj and "ours" in obj:
            yield obj
        for v in obj.values():
            yield from _census_rows(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _census_rows(v)


def rule_package_refused(ev: Evidence) -> list[Finding]:
    """F35: load_package returned None for one of our packages (a foreign EngineVersion)."""
    for rec in ev.status:
        err = str(rec.get("error") or "")
        if rec.get("phase") == "menu_setup" and "load_extra_packages" in err and "NoneType" in err:
            return [Finding.from_catalog("F35", "menu_setup: load_package returned None in load_extra_packages")]
    return []


def rule_wrong_mesh(ev: Evidence) -> list[Finding]:
    """F34: a weapon carrying our parts is drawn from a mesh outside our package."""
    package = ((ev.report or {}).get("params") or {}).get("package")
    if not package:
        return []
    for rec in ev.status:
        for row in _census_rows(rec):
            mesh = row.get("FirstPersonMesh")
            if row.get("ours") and mesh and not str(mesh).startswith(f"{package}."):
                return [Finding.from_catalog(
                    "F34", f"{row.get('balance') or row.get('weapon')} carries our parts but its "
                           f"FirstPersonMesh is {mesh}, not in {package}")]
    return []


def rule_save_slots(ev: Evidence) -> list[Finding]:
    """F38. The save backup must hold every slot: CONTINUE loads the last-played character."""
    import json as _json
    from pathlib import Path as _Path

    backup = (ev.report or {}).get("save_backup")
    if not backup:
        return []
    manifest = _Path(backup) / "manifest.json"
    try:
        data = _json.loads(manifest.read_text(encoding="utf-8"))
        folder = _Path(data["source_dir"])
        slots = {p.name for p in folder.glob("Save*.sav")}
        guarded = {n for n in data.get("files", {}) if n.endswith(".sav")}
    except Exception:  # noqa: BLE001 - no manifest / folder gone: nothing to judge
        return []
    missing = sorted(slots - guarded)
    if missing:
        return [Finding.from_catalog("F38", f"the backup guarded {sorted(guarded)} but the folder also "
                                     f"holds {missing}; check those were not played and saved")]
    return []


def rule_hud_watch(ev: Evidence) -> list[Finding]:
    """F37. A HUD run (report has hud_captures) that captured nothing."""
    report = ev.report or {}
    if "hud_captures" in report and not report["hud_captures"]:
        return [Finding.from_catalog("F37", "the HUD watch captured no frames")]
    return []


RULES: list[Callable[[Evidence], list[Finding]]] = [
    rule_registration, rule_mod_state, rule_crash, rule_resolution, rule_focus, rule_stale_frames,
    rule_stability, rule_view_toggle, rule_phase_timing, rule_backpack, rule_inventory,
    rule_save_roundtrip, rule_part_path, rule_lint, rule_driver_failure, rule_launcher, rule_save_restore,
    rule_wrong_mesh, rule_package_refused, rule_hud_watch, rule_save_slots,
]


@dataclass
class Triage:
    verdict: str
    headline: str
    findings: list[Finding]
    conditions: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict, "headline": self.headline,
            "findings": [f.__dict__ for f in self.findings],
            "conditions": {k: {"status": v.get("status"), "summary": v.get("summary")} for k, v in self.conditions.items()},
        }


def triage(ev: Evidence) -> Triage:
    findings: list[Finding] = []
    for rule in RULES:
        try:
            findings.extend(rule(ev))
        except Exception as ex:  # noqa: BLE001 -- a rule must never take the verdict down with it
            findings.append(Finding("RULE", "info", f"{rule.__name__} raised {ex!r}"))
    conds = ev.conditions
    statuses = [c.get("status") for c in conds.values()]
    failed = [k for k, c in conds.items() if c.get("status") == FAIL]
    kinds = {f.kind for f in findings}
    registered = registration_known(ev) is True

    if any(f.code == "F34" for f in findings):
        # the pixel conditions cannot see an invisible weapon (both runs empty-handed pass)
        verdict, headline = "BUILD", "our weapon draws nothing: it was built from a foreign gestalt (F34)"
    elif ev.report and not ev.failure and statuses and all(s == PASS for s in statuses):
        verdict, headline = "BUILD OK", "all conditions passed"
    elif ev.report and not ev.failure and statuses and FAIL not in statuses:
        skipped = [k for k, c in conds.items() if c.get("status") == SKIP]
        verdict, headline = "BUILD OK", f"no condition failed; skipped: {', '.join(skipped)}"
    elif "build" in kinds:
        verdict = "BUILD"
        codes = [f.code for f in findings if f.kind == "build"]
        headline = f"the build or spec is wrong ({', '.join(dict.fromkeys(codes))})"
    elif "config" in kinds and not registered:
        verdict = "CONFIG"
        headline = "the game folder is not in a runnable state (" + ", ".join(dict.fromkeys(f.code for f in findings if f.kind == "config")) + ")"
    elif "instrument" in kinds:
        verdict = "INSTRUMENT"
        codes = [f.code for f in findings if f.kind == "instrument"]
        headline = ("the build is fine; the run failed on the instrument (" if registered else "the run failed on the instrument (") \
            + ", ".join(dict.fromkeys(codes)) + ")"
    elif failed or ev.failure:
        verdict = "UNDETERMINED"
        headline = "a condition failed and no rule claims it: " + (", ".join(failed) or str(ev.failure))
    elif not ev.report:
        verdict = "NO RUN"
        headline = "no report found; pass --report or --status"
    else:
        verdict, headline = "BUILD OK", "no failures"
    # findings order: build, config, instrument, info
    order = {"build": 0, "config": 1, "instrument": 2, "info": 3}
    findings.sort(key=lambda f: order.get(f.kind, 4))
    return Triage(verdict, headline, findings, conds)
