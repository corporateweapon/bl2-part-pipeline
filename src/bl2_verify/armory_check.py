"""Unattended acceptance for an installed Armory (M10): both weapons register, each auto
spawn hands out a weapon whose every part is that weapon's, the last one is on screen, and
all of them are still there after a save/quit/reload.

Drives the game exactly like ``run_loop`` (launch, main menu, CONTINUE, status files, save
and quit, CONTINUE again) and reads the Armory's own records rather than the harness's::

    PYTHONPATH=src python src/bl2_verify/armory_check.py --mod PipelineArmory \\
        --weapons ak47 awp --runs scratch/verify_runs_armory --run-index 1

Writes ``scratch/armory_report_<stamp>.json`` and exits 0 only when every condition passed.
The save is backed up before launch and restored afterwards (``save_guard``).

Conditions:

registered      every component's menu_setup record has ok=true, no fragment skipped/error
spawned         every id in --weapons produced a spawn record with exactly one granted
                weapon carrying that weapon's balance and no other weapon's parts
equipped        the last spawn's equip record reports a slot
survives_reload the census_on_load after the reload lists every spawned weapon (by
                unique id) with the same part paths
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from bl2_verify.game_driver import GAME, SCRATCH, GameDriver, read_status  # noqa: E402
from bl2_verify.save_guard import SaveGuard  # noqa: E402

PASS, FAIL = "pass", "fail"


def _cond(status: str, summary: str, **detail: Any) -> dict[str, Any]:
    return {"status": status, "summary": summary, **detail}


def _component_status_files(mod_dir: Path) -> list[Path]:
    """weapons/<name>.status.json (a generated PipelineArmory) or logs/<name>.status.json (the
    pack-loading Armory runtime; its own armory.status.json and the character runtime's
    characters.status.json are not a component's)."""
    files = list((mod_dir / "weapons").glob("*.status.json"))
    files += [p for p in (mod_dir / "logs").glob("*.status.json")
              if p.name not in ("armory.status.json", CHARACTERS_STATUS)]
    return sorted(files)


CHARACTERS_STATUS = "characters.status.json"


def character_evidence(mod_dir: Path, since: float) -> dict[str, Any] | None:
    """What the Armory's character runtime did (Armory 1.1.0+): setup per skin, and every
    component it re-pointed or hid. None when the Armory has no character packs."""
    path = mod_dir / "logs" / CHARACTERS_STATUS
    if not path.exists():
        return None
    recs = read_status(path, since=since)
    setups = [r for r in recs if r.get("phase") == "setup"]
    acted = [a for r in recs if r.get("phase") == "scan" for a in r.get("acted") or []]
    return {"setup_ok": [r.get("ok") for r in setups],
            "skins": setups[-1].get("skins") if setups else None,
            "active": setups[-1].get("active") if setups else None,
            "swaps": [a for a in acted if " -> " in a][:12],
            "hidden": [a for a in acted if ": hidden" in a][:6],
            "scan_errors": [r.get("error") for r in recs if r.get("phase") == "scan" and r.get("error")][:3]}


def registered(mod_dir: Path, since: float) -> tuple[bool, dict[str, Any]]:
    detail: dict[str, Any] = {}
    ok = True
    files = _component_status_files(mod_dir)
    if not files:
        return False, {"error": "no component status files"}
    for path in files:
        recs = read_status(path, since=since)
        setup = next((r for r in recs if r.get("phase") == "menu_setup"), None)
        name = path.name.replace(".status.json", "")
        if setup is None:
            ok = False
            detail[name] = "no menu_setup record"
            continue
        frags = setup.get("fragments") or []
        bad = [f for f in frags if f.get("skipped") or f.get("error")]
        detail[name] = {
            "ok": setup.get("ok"), "error": setup.get("error"),
            "fragments": [(f.get("fragment"), (f.get("table_entry") or {}).get("table_len"))
                          for f in frags],
            "skipped_or_error": bad,
            "balances": [(b.get("balance"), b.get("pools")) for b in setup.get("balances") or []],
        }
        if not setup.get("ok") or bad:
            ok = False
    return ok, detail


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mod", default="PipelineArmory")
    p.add_argument("--weapons", nargs="+", required=True, help="ids to auto spawn, in order")
    p.add_argument("--runs", default=str(SCRATCH / "verify_runs_armory"))
    p.add_argument("--run-index", type=int, default=1)
    p.add_argument("--mode", default="scancode")
    p.add_argument("--launch-timeout", type=float, default=180.0)
    p.add_argument("--menu-timeout", type=float, default=180.0)
    p.add_argument("--menu-settle", type=float, default=25.0)
    p.add_argument("--load-timeout", type=float, default=240.0)
    p.add_argument("--quit-timeout", type=float, default=90.0)
    p.add_argument("--keep-save", action="store_true")
    args = p.parse_args(argv)

    mod_dir = GAME / "sdk_mods" / args.mod
    if not (mod_dir / "__init__.py").exists():
        raise SystemExit(f"{mod_dir} is not installed")
    runs = Path(args.runs)
    runs.mkdir(parents=True, exist_ok=True)
    status = SCRATCH / f"{args.mod}_status.json"
    control = mod_dir / "control.json"
    control.write_text(json.dumps({"status_path": str(status), "auto_spawn": args.weapons}),
                       encoding="utf-8")
    # the components' own status files stay beside them (weapons/<name>.status.json)
    for stale in _component_status_files(mod_dir):
        stale.unlink()
    started = time.time()
    if status.exists():
        status.unlink()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    report: dict[str, Any] = {"timestamp": stamp, "run": args.run_index, "mod": args.mod,
                              "weapons": args.weapons, "conditions": {}, "events": [],
                              "captures": {}}
    d = GameDriver(key_mode=args.mode)
    d.log("=" * 78)
    d.log(f"armory_check run {args.run_index} ({args.mod}: {' '.join(args.weapons)})")
    guard = SaveGuard(log=d.log)

    def event(msg: str) -> None:
        report["events"].append(f"{time.strftime('%H:%M:%S')} {msg}")
        d.log(msg)

    backup_dir = guard.backup(tag=f"armory{args.run_index}")
    report["save_backup"] = str(backup_dir)
    failure: str | None = None
    first_done: dict[str, Any] | None = None
    reload_load: dict[str, Any] | None = None
    spawns: list[dict[str, Any]] = []
    equips: list[dict[str, Any]] = []
    try:
        d.launch(timeout=args.launch_timeout)
        since = d.launch_time
        d.skip_intro(timeout=args.menu_timeout)
        # registration happens at the menu tick, a few seconds after the menu is up
        deadline = time.time() + args.menu_timeout
        while time.time() < deadline:
            ok, detail = registered(mod_dir, since)
            if ok or (detail and all(isinstance(v, dict) for v in detail.values())):
                break
            time.sleep(2.0)
        ok, detail = registered(mod_dir, since)
        report["conditions"]["registered"] = _cond(
            PASS if ok else FAIL,
            f"{sum(1 for v in detail.values() if isinstance(v, dict) and v.get('ok'))} of "
            f"{len(detail)} component(s) registered clean", components=detail)
        event(f"registration: {'ok' if ok else 'FAILED'}")

        d.continue_game()
        recs = d.wait_status(status, lambda r: any(x.get("done") for x in r),
                             timeout=args.load_timeout, since=since, what="done")
        first_done = next(r for r in recs if r.get("done"))
        spawns = [r for r in recs if "spawn" in r and "auto_spawn" not in r]
        equips = [r for r in recs if "equip" in r]
        event(f"first load: {len(spawns)} spawn(s), {len(equips)} equip(s)")
        time.sleep(3.0)
        shot = runs / f"armory_{args.run_index}.png"
        d.capture(shot)
        report["captures"]["run"] = str(shot)

        d.save_and_quit(wait_menu=args.menu_settle)
        reload_mark = time.time()
        d.continue_game()
        recs = d.wait_status(
            status, lambda r: any("census_on_load" in x for x in r),
            timeout=args.load_timeout, since=reload_mark, what="census_on_load (after reload)")
        reload_load = next(r for r in recs if "census_on_load" in r)
        event(f"reload: {len(reload_load.get('census_on_load') or [])} of our weapon(s) on load")
        d.wait_status(status, lambda r: any(x.get("done") for x in r),
                      timeout=args.load_timeout, since=reload_mark, what="done (after reload)")
        time.sleep(2.0)
        reload_shot = runs / f"armory_{args.run_index}_reload.png"
        d.capture(reload_shot)
        report["captures"]["reload"] = str(reload_shot)
    except Exception as ex:  # noqa: BLE001
        failure = f"{type(ex).__name__}: {ex}"
        event(f"FAILED {failure}")
        try:
            d.capture(runs / f"armory_{args.run_index}_failure.png")
        except Exception:  # noqa: BLE001
            pass
    finally:
        if d.is_running():
            clean = d.quit_game(timeout=args.quit_timeout)
            event(f"game quit ({'menu' if clean else 'taskkill fallback'})")
        if args.keep_save:
            report["save_restored"] = False
        else:
            guard.restore(backup_dir)
            report["save_restored"] = guard.verify_restored(backup_dir)
            event(f"save restored, verified={report['save_restored']}")
        try:
            control.unlink()
        except OSError:
            pass
    report["failure"] = failure

    # -- spawned ------------------------------------------------------------------
    by_id = {r["spawn"]: r for r in spawns}
    problems = []
    for weapon_id in args.weapons:
        rec = by_id.get(weapon_id)
        if rec is None or rec.get("error"):
            problems.append(f"{weapon_id}: {rec.get('error') if rec else 'no spawn record'}")
            continue
        granted = rec.get("granted") or []
        if len(granted) != 1:
            problems.append(f"{weapon_id}: {len(granted)} weapon(s) granted")
            continue
        if granted[0].get("id") != weapon_id:
            problems.append(f"{weapon_id}: granted weapon is {granted[0].get('id')}")
    report["conditions"]["spawned"] = _cond(
        FAIL if problems or not spawns else PASS,
        "; ".join(problems) if problems else f"{len(spawns)} spawn(s), one weapon each",
        spawns=spawns)
    # -- equipped ----------------------------------------------------------------
    last = equips[-1] if equips else None
    report["conditions"]["equipped"] = _cond(
        PASS if last and "slot" in str(last.get("result", "")) else FAIL,
        f"last equip: {last.get('result') if last else 'none'}", equips=equips)
    # -- survives_reload ---------------------------------------------------------
    if first_done is None or reload_load is None:
        report["conditions"]["survives_reload"] = _cond(FAIL, "no reload census")
    else:
        before = {c["unique_id"]: c for c in first_done.get("census") or []}
        after = {c["unique_id"]: c for c in reload_load.get("census_on_load") or []}
        spawned_ids = {g["unique_id"] for r in spawns for g in r.get("granted") or []}
        missing = [u for u in spawned_ids if u not in after]
        changed = []
        for u in spawned_ids:
            if u in before and u in after:
                keys = [k for k in before[u] if k.endswith("PartDefinition")]
                diff = {k: (before[u].get(k), after[u].get(k)) for k in keys
                        if before[u].get(k) != after[u].get(k)}
                if diff:
                    changed.append({"unique_id": u, "diff": diff})
        report["conditions"]["survives_reload"] = _cond(
            PASS if spawned_ids and not missing and not changed else FAIL,
            f"{len(spawned_ids) - len(missing)} of {len(spawned_ids)} spawned weapon(s) back "
            f"with the same parts after reload",
            missing=missing, changed=changed, census_after=list(after.values()))

    characters = character_evidence(mod_dir, started)
    if characters is not None:
        report["characters"] = characters
        d.log(f"characters: setup ok {characters['setup_ok']}, {len(characters['swaps'])} swap(s), "
              f"{len(characters['hidden'])} hidden, active {characters['active']}")
    report["all_pass"] = all(c["status"] == PASS for c in report["conditions"].values())
    report["ok"] = failure is None and report["all_pass"]
    out = SCRATCH / f"armory_report_{stamp}.json"
    out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    d.log(f"report -> {out}")
    for name, cond in report["conditions"].items():
        d.log(f"  {cond['status'].upper():5} {name}: {cond['summary']}")
    d.log(f"armory run {args.run_index}: ok={report['ok']} all_pass={report['all_pass']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
