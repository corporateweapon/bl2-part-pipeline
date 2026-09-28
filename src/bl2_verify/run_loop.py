"""One unattended iteration of the M5 verify loop.

Given an already-installed package + SDK mod (installation itself is ``bl2_partgen.install``'s
job -- pass ``--skip-install`` here), this drives the game through the whole evidence chain and
writes ``scratch/verify_report_<timestamp>.json``:

1. snapshot the character save (``save_guard``)
2. launch the game, dismiss the intro, wait for the main menu **and** for the mod's
   ``menu_setup`` record (the part has to be registered before any save is deserialised -- F16)
3. CONTINUE into the character, wait for the mod's ``done`` record, capture ``run_<n>.png``
4. save and quit to the main menu, CONTINUE again
5. wait for the second ``phase 0`` record and assert the custom part is present in its
   ``census_before`` -- that is the save/quit/reload evidence (F18/F19)
6. capture ``run_<n>_reload.png``, quit the game, restore the save
7. score the four conditions from section 3.5 of the brief

=========================  ==========================================================
condition                  how it is decided
=========================  ==========================================================
changed_vs_baseline        ``compare`` weapon crop vs ``docs/captures/m1_stock_full.png``
                           must exceed ``DIFFER_MIN`` (>2 % of crop pixels)
stable_across_runs         same crop vs the previous run's capture must stay under
                           ``MATCH_MAX`` (<0.5 %); *skipped* on the first run, since
                           there is nothing to compare against yet
lint_clean                 ``python -m bl2_lint <proposal>`` exits 0; *skipped* with a
                           note when no proposal is passed
survives_reload            the post-reload census lists a weapon whose barrel is the
                           custom part
=========================  ==========================================================

Exit status: 0 when no condition **failed** (skips are reported as skips and named in the
report), 1 otherwise. ``all_pass`` in the report is only true when all four actually passed,
which is what the milestone's "all four, twice" acceptance test reads.

Usage::

    python src/bl2_verify/run_loop.py --skip-install \\
        --control scratch/m2_control.json --proposal src/bl2_lint/proposal.example.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bl2_verify import compare as cmp_mod  # noqa: E402
from bl2_verify.game_driver import GameDriver, read_status  # noqa: E402
from bl2_verify.save_guard import SAVE, SaveGuard  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Borderlands 2")
SCRATCH = REPO / "scratch"
RUNS = SCRATCH / "verify_runs"
BASELINE = REPO / "docs" / "captures" / "m1_stock_full.png"
DEFAULT_STATUS = SCRATCH / "m2_status.json"
DEFAULT_CONTROL = SCRATCH / "m2_control.json"

PASS, FAIL, SKIP = "pass", "fail", "skip"
#: the game's settings file (resolution); --hud-watch runs back it up and restore it
SAVE_INI = SAVE.parents[2] / "Config" / "WillowEngine.ini"


def _cond(status: str, summary: str, **detail: Any) -> dict[str, Any]:
    return {"status": status, "summary": summary, **detail}


def next_run_index(runs: Path) -> int:
    runs.mkdir(parents=True, exist_ok=True)
    used = []
    for p in runs.glob("run_*.png"):
        stem = p.stem.split("_")
        if len(stem) >= 2 and stem[1].isdigit():
            used.append(int(stem[1]))
    return (max(used) + 1) if used else 1


def run_captures(runs: Path, index: int) -> list[Path]:
    """Every frame of run ``index``'s burst: ``run_<i>.png`` plus ``run_<i>_f*.png``."""
    first = runs / f"run_{index}.png"
    if not first.exists():
        return []
    return [first, *sorted(runs.glob(f"run_{index}_f*.png"))]


def previous_run_captures(runs: Path, index: int) -> list[Path]:
    for i in range(index - 1, 0, -1):
        shots = run_captures(runs, i)
        if shots:
            return shots
    return []


def run_lint(proposal: Path) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    res = subprocess.run([sys.executable, "-m", "bl2_lint", str(proposal)],
                         cwd=str(REPO), env=env, capture_output=True, text=True, check=False)
    return res.returncode, (res.stdout + res.stderr).strip()


# --------------------------------------------------------------------------- status predicates
def has_menu_setup(recs: list[dict[str, Any]]) -> bool:
    return any(r.get("phase") == "menu_setup" for r in recs)


def has_done(recs: list[dict[str, Any]]) -> bool:
    return any(r.get("done") is True for r in recs)


def phase0_records(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in recs if r.get("phase") == 0]


def census_has_part(census: list[dict[str, Any]], part_path: str) -> list[dict[str, Any]]:
    return [w for w in census if w.get("barrel") == part_path]


def part_path_from_control(control: Path) -> str:
    try:
        ctl = json.loads(control.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "GD_Weap_AssaultRifle.Barrel.AR_Barrel_PL_Bent"
    return f"GD_Weap_AssaultRifle.Barrel.{ctl.get('part_name', 'AR_Barrel_PL_Bent')}"


#: which status record the capture's wall-clock offset is measured from
ANCHORS = ("done", "maploaded")

#: the harness camera the crop is calibrated for (``options.harness_capture_view``)
VIEWS = ("third", "first")


def weapon_box(args: argparse.Namespace) -> tuple[int, int, int, int]:
    """The crop both pixel conditions measure: ``--weapon-box`` if given, else the view's.

    F22: the third-person box was cut for the long M1/M2 barrel and is mostly pawn for a
    short weapon held close.  The first-person box (``compare.FIRST_PERSON_BOX``) holds the
    weapon and nothing that breathes.
    """
    if args.weapon_box:
        parts = tuple(int(v) for v in str(args.weapon_box).split(","))
        if len(parts) != 4:
            raise SystemExit("--weapon-box needs x0,y0,x1,y1")
        return parts  # type: ignore[return-value]
    return cmp_mod.FIRST_PERSON_BOX if args.capture_view == "first" else cmp_mod.WEAPON_BOX


def _anchor_time(recs: list[dict[str, Any]], anchor: str) -> float | None:
    """Wall-clock ``t`` of the record the capture is timed from."""
    if anchor == "maploaded":
        match = lambda r: r.get("hook") == "WillowClientDisableLoadingMovie"  # noqa: E731
    else:
        match = lambda r: r.get("done") is True  # noqa: E731
    return next((float(r["t"]) for r in reversed(recs)
                 if match(r) and r.get("t") is not None), None)


def _settle(d: GameDriver, recs: list[dict[str, Any]], pose_settle: float,
            event: Any, anchor: str = "done") -> float:
    """Sleep until ``pose_settle`` seconds after the mod WROTE the anchor record.

    Not after the loop *noticed* it.  ``wait_status`` polls, so the moment the loop sees
    a record is anywhere inside one poll interval -- 2 s by default, which is about
    three cycles of the pawn's idle animation.  That jitter, not the build, is what
    ``stable_across_runs`` was measuring: two runs of the same package captured at
    different points of the same breathing loop, and no shift realigns them because the
    motion is sub-pixel and rotational (F21).  Both clocks are this machine's, so
    sleeping to an absolute ``t + pose_settle`` pins every run to the same phase.

    *Which* record to anchor on matters as much as the absolute sleep.  ``done`` is
    written 330 viewport **ticks** after the map load, and that tick-to-wall-clock
    mapping drifts with the frame rate: measured at 6.872 s and 6.818 s for the two map
    loads of a single run, a 54 ms spread.  54 ms is ~6 % of the idle animation's cycle,
    which is exactly the size of the residual it leaves.  ``maploaded`` anchors on the
    map-load hook instead -- the same event the animation itself starts from -- so the
    tick drift is no longer between the anchor and the capture.  It needs a
    ``pose_settle`` big enough to be past phase 3 (``done`` lands at ~6.9 s, so >= 11).

    Falls back to the old relative sleep when the record carries no ``t``.
    """
    stamp = _anchor_time(recs, anchor)
    if stamp is None:
        time.sleep(pose_settle)
        return float(pose_settle)
    target = stamp + pose_settle
    late = time.time() - target
    if late > 0:
        event(f"capture is {late:.2f}s later than the {pose_settle}s mark "
              f"(status poll noticed the record late); raise --pose-settle if this grows")
    else:
        time.sleep(-late)
    return round(time.time() - stamp, 3)


# --------------------------------------------------------------------------- the loop
CRLF = chr(13) + chr(10)


def _set_game_res(ini: Path, res: str) -> None:
    """Exclusive fullscreen at WxH in [SystemSettings] for one launch (restored after the run).

    A live `setres` into a window stalls BL2's render loop until input arrives, so each
    resolution gets its own launch instead; the driver's boxes rescale to the capture (F31).
    """
    w, h = (int(v) for v in res.lower().split("x"))
    want = {"Fullscreen": "True", "WindowedFullscreen": "False", "ResX": str(w), "ResY": str(h)}
    lines = ini.read_text(encoding="utf-8", errors="surrogateescape").splitlines(keepends=True)
    section = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            continue
        if section == "[SystemSettings]" and "=" in stripped:
            key = stripped.split("=", 1)[0]
            if key in want:
                ending = line[len(line.rstrip(CRLF)):]
                lines[i] = f"{key}={want[key]}{ending}"
    ini.write_text("".join(lines), encoding="utf-8", errors="surrogateescape")


class _HudOnlyDone(Exception):
    """--hud-only: the HUD captures were the point of this run; skip the reload phase."""


def _client_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """The game's client area in physical screen pixels (the capture is DPI-aware too)."""
    try:
        import ctypes
        from ctypes import wintypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001 - already set / older Windows
            pass
        rect = wintypes.RECT()
        ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect))
        origin = wintypes.POINT(0, 0)
        ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(origin))
        return origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom
    except Exception:  # noqa: BLE001
        return None


def _hud_watch(d: GameDriver, args: argparse.Namespace, runs: Path, index: int,
               status: Path, event: Any) -> list[dict[str, Any]]:
    """Capture the frame for every {"hud_capture": name} the mod writes, cropped to the game's
    client area, until {"hud_test": "done"} or --hud-watch seconds pass."""
    from PIL import Image

    from bl2_verify import winput

    mark, seen, out = time.time(), set(), []
    refocused = 0
    while time.time() - mark < args.hud_watch:
        # setres into a window recreates it and focus goes elsewhere; BL2 pauses when it is
        # not the active window, which froze the test. Focusing sends no input.
        h = d.hwnd()
        if h and not winput.is_active(h):
            d.focus(quiet=True)
            refocused += 1
        recs = read_status(status, since=mark - 30)
        for r in recs:
            name = r.get("hud_capture")
            if name and name not in seen:
                seen.add(name)
                full = runs / f"run_{index}_hud_{name}_full.png"
                d.capture(full, focus=False)
                box = _client_rect(d.hwnd() or 0)
                shot = runs / f"run_{index}_hud_{name}.png"
                with Image.open(full) as im:
                    (im.crop(box) if box and box[2] > box[0] else im).save(shot)
                out.append({"name": name, "shot": str(shot), "client": box, "clip": r.get("clip")})
                event(f"hud capture {name}: client {box}, canvas {r.get('clip')}")
        ends = [r for r in recs if r.get("hud_test") in ("done", "error")]
        if ends:
            if ends[0].get("hud_test") == "error":
                event(f"hud test error: {ends[0]}")
            break
        time.sleep(0.15)
    event(f"hud watch: {len(out)} capture(s), game refocused {refocused} time(s)")
    return out


def _input_test(d: GameDriver, args: argparse.Namespace, runs: Path, index: int,
                status: Path, since: float, event: Any) -> dict[str, Any]:
    """Press real mouse buttons in game (``--input-test lmb,rmb``) and collect the mod's
    status records written meanwhile (``alt_fire`` / ``fire_sound`` / ``trace`` keys).

    Runs after the scored capture, so it can never change a condition; it exists because an
    SDK call such as ``StartAltFire()`` is not proof that the player's own button takes the
    same engine path (BL2 routes RMB through its GBA_ZoomIn game binding).
    """
    from bl2_verify import winput

    time.sleep(args.input_delay)
    mark = time.time()
    pressed: list[str] = []
    for token in [t.strip().lower() for t in args.input_test.split(",") if t.strip()]:
        if not d._game_in_front():
            pressed.append(f"{token}:not-sent")
            time.sleep(args.input_gap)
            continue
        winput.mouse_button({"lmb": "left", "rmb": "right"}[token], hold=0.12)
        pressed.append(token)
        time.sleep(args.input_gap)
    d.capture(runs / f"run_{index}_inputs.png")
    time.sleep(1.0)
    recs = [r for r in read_status(status, since=mark)
            if any(k in r for k in ("alt_fire", "fire_sound", "trace", "shot"))]
    event(f"input test: pressed {','.join(pressed)}; {len(recs)} mod record(s)")
    return {"pressed": pressed, "records": recs}


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    control = Path(args.control)
    status = Path(args.status)
    runs = Path(args.runs)
    index = args.run_index if args.run_index else next_run_index(runs)
    part_path = args.part_path or part_path_from_control(control)
    started = time.time()
    stamp = time.strftime("%Y%m%d-%H%M%S")

    box = weapon_box(args)
    report: dict[str, Any] = {
        "timestamp": stamp,
        "started": started,
        "run": index,
        "params": {
            "package": args.package, "mod": args.mod, "control": str(control),
            "status": str(status), "proposal": args.proposal, "baseline": str(args.baseline),
            "part_path": part_path, "skip_install": bool(args.skip_install),
            "keep_save": bool(args.keep_save),
            "burst": int(args.burst), "burst_interval": float(args.burst_interval),
            "capture_anchor": args.capture_anchor, "pose_settle": float(args.pose_settle),
            "capture_view": args.capture_view, "weapon_box": list(box),
        },
        "conditions": {}, "captures": {}, "events": [],
    }

    d = GameDriver(key_mode=args.mode)
    d.log("=" * 78)
    d.log(f"run_loop run {index} starting (control={control.name} part={part_path})")
    guard = SaveGuard(log=d.log)

    def event(msg: str) -> None:
        report["events"].append(f"{time.strftime('%H:%M:%S')} {msg}")
        d.log(msg)

    # -- install -------------------------------------------------------------
    if not args.skip_install:
        raise SystemExit("install is bl2_partgen.install's job; re-run with --skip-install")
    mod_dir = GAME / "sdk_mods" / args.mod
    pkg = GAME / "WillowGame" / "CookedPCConsole" / f"{args.package}.upk"
    for p in (mod_dir / "__init__.py", pkg):
        if not p.exists():
            raise SystemExit(f"--skip-install but {p} is missing; install it first")
    event(f"install skipped; using {mod_dir.name} + {pkg.name}")
    report["params"]["control_contents"] = json.loads(control.read_text(encoding="utf-8"))

    backup_dir = guard.backup(tag=f"run{index}")
    ini = SAVE_INI
    ini_copy = runs / f"run_{index}_WillowEngine.ini"
    if (args.hud_watch or args.game_res) and ini.exists():
        runs.mkdir(parents=True, exist_ok=True)
        ini_copy.write_bytes(ini.read_bytes())
        event(f"game settings backed up: {ini.name}")
        if args.game_res:
            _set_game_res(ini, args.game_res)
            event(f"game resolution for this run: {args.game_res} exclusive fullscreen")
    report["save_backup"] = str(backup_dir)

    census: list[dict[str, Any]] = []
    reload_hits: list[dict[str, Any]] = []
    shot = runs / f"run_{index}.png"
    reload_shot = runs / f"run_{index}_reload.png"
    failure: str | None = None

    try:
        # -- launch ----------------------------------------------------------
        d.launch(timeout=args.launch_timeout)
        since = d.launch_time
        d.skip_intro(timeout=args.menu_timeout)  # ends at the main menu, probe-confirmed
        d.wait_status(status, has_menu_setup, timeout=args.menu_timeout, since=since,
                      what="menu_setup")
        event("menu_setup recorded (part registered before any save loads)")

        # -- first play ------------------------------------------------------
        d.continue_game()
        recs = d.wait_status(status, has_done, timeout=args.load_timeout, since=since,
                             poll=args.pose_poll, what="done")
        held = next((r.get("held") for r in recs if r.get("done") is True), None)
        report["held"] = held
        event(f"first load done; held={held.get('barrel') if isinstance(held, dict) else held}")
        report["capture_offset_s"] = _settle(d, recs, args.pose_settle, event,
                                             args.capture_anchor)
        shots = d.capture_burst(runs, f"run_{index}", count=args.burst,
                                interval=args.burst_interval)
        report["captures"]["run"] = str(shot)
        report["captures"]["burst"] = [str(p) for p in shots]

        # -- optional HUD watch: capture every frame the mod cues ----------------
        if args.hud_watch:
            report["hud_captures"] = _hud_watch(d, args, runs, index, status, event)
            if args.hud_only:
                raise _HudOnlyDone()

        # -- optional input test: real mouse buttons, after the scored capture ----
        if args.input_test:
            report["input_test"] = _input_test(d, args, runs, index, status, since, event)
            report["captures"]["inputs"] = str(runs / f"run_{index}_inputs.png")

        # -- save / quit / reload --------------------------------------------
        d.save_and_quit(wait_menu=args.menu_settle)  # ends back at the main menu
        reload_mark = time.time()
        d.continue_game()
        d.wait_status(status, lambda r: len(phase0_records(r)) >= 2, timeout=args.load_timeout,
                      since=since, what="second phase-0 census")
        recs = read_status(status, since=since)
        census = phase0_records(recs)[1].get("census_before", []) or []
        reload_hits = census_has_part(census, part_path)
        report["census_after_reload"] = census
        event(f"reload census: {len(census)} weapon(s), {len(reload_hits)} with {part_path} "
              f"({time.time() - reload_mark:.0f}s after CONTINUE)")
        recs = d.wait_status(status, has_done, timeout=args.load_timeout, since=reload_mark,
                             poll=args.pose_poll, what="done (after reload)")
        _settle(d, recs, args.pose_settle, event, args.capture_anchor)
        d.capture(reload_shot)
        report["captures"]["reload"] = str(reload_shot)
    except _HudOnlyDone:
        event("HUD-only run: skipping save/quit/reload")
    except Exception as ex:  # noqa: BLE001
        failure = f"{type(ex).__name__}: {ex}"
        event(f"FAILED {failure}")
        try:
            d.capture(runs / f"run_{index}_failure.png")
            report["captures"]["failure"] = str(runs / f"run_{index}_failure.png")
        except Exception:  # noqa: BLE001
            pass
    finally:
        # -- leave the machine clean ------------------------------------------
        if d.is_running():
            clean = d.quit_game(timeout=args.quit_timeout)
            event(f"game quit ({'menu' if clean else 'taskkill fallback'})")
        if (args.hud_watch or args.game_res) and ini_copy.exists():
            if ini.read_bytes() != ini_copy.read_bytes():
                ini.write_bytes(ini_copy.read_bytes())
                event(f"game settings restored: {ini.name} (setres had changed it)")
            else:
                event(f"game settings unchanged: {ini.name}")
            report["settings_restored"] = ini.read_bytes() == ini_copy.read_bytes()
        if args.keep_save:
            event("save kept as-is (--keep-save)")
            report["save_restored"] = False
        else:
            guard.restore(backup_dir)
            report["save_restored"] = guard.verify_restored(backup_dir)
            event(f"save restored, verified={report['save_restored']}")

    report["failure"] = failure

    # -- condition 1: the change landed ---------------------------------------
    baseline = Path(args.baseline)
    if not shot.exists():
        report["conditions"]["changed_vs_baseline"] = _cond(FAIL, "no capture from this run")
    elif not baseline.exists():
        report["conditions"]["changed_vs_baseline"] = _cond(SKIP, f"baseline missing: {baseline}")
    else:
        res = cmp_mod.compare(baseline, shot, box)
        report["conditions"]["changed_vs_baseline"] = _cond(
            PASS if res.differs else FAIL,
            f"{res.diff_fraction:.3%} of the weapon crop differs from the stock baseline "
            f"(need >{cmp_mod.DIFFER_MIN:.1%})", compare=res.to_dict())

    # -- condition 2: reproducible --------------------------------------------
    mine = run_captures(runs, index)
    prev = previous_run_captures(runs, index)
    if not mine:
        report["conditions"]["stable_across_runs"] = _cond(FAIL, "no capture from this run")
    elif not prev:
        report["conditions"]["stable_across_runs"] = _cond(
            SKIP, "first run: no previous capture to compare against")
    else:
        res = cmp_mod.best_pair(prev, mine, box, tolerance=cmp_mod.SWAY_TOLERANCE_PX)
        report["conditions"]["stable_across_runs"] = _cond(
            PASS if res.matches else FAIL,
            f"{res.diff_fraction:.3%} of the weapon crop differs between the closest frames of "
            f"this run's burst and {Path(res.a).name} beyond {res.tolerance} px of sway "
            f"(need <{cmp_mod.MATCH_MAX:.1%})",
            compare=res.to_dict(),
            compared=[len(prev), len(mine)])

    # -- condition 3: linter ---------------------------------------------------
    if args.proposal:
        code, out = run_lint(Path(args.proposal))
        report["conditions"]["lint_clean"] = _cond(
            PASS if code == 0 else FAIL, f"python -m bl2_lint {Path(args.proposal).name} -> {code}",
            output=out.splitlines()[-25:])
    else:
        report["conditions"]["lint_clean"] = _cond(SKIP, "no --proposal given")

    # -- condition 4: survives save/quit/reload --------------------------------
    if reload_hits:
        report["conditions"]["survives_reload"] = _cond(
            PASS, f"{len(reload_hits)} weapon(s) still carry {part_path} after save/quit/reload",
            weapons=reload_hits)
    else:
        report["conditions"]["survives_reload"] = _cond(
            FAIL, f"no weapon with {part_path} in the post-reload census "
                  f"({len(census)} weapon(s) seen)", census=census)

    statuses = [c["status"] for c in report["conditions"].values()]
    report["all_pass"] = all(s == PASS for s in statuses)
    report["ok"] = FAIL not in statuses and failure is None
    report["elapsed_s"] = round(time.time() - started, 1)

    out_path = SCRATCH / f"verify_report_{stamp}.json"
    out_path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    report["report_path"] = str(out_path)
    d.log(f"report -> {out_path}")
    for name, c in report["conditions"].items():
        d.log(f"  {c['status'].upper():5} {name}: {c['summary']}")
    d.log(f"run {index}: ok={report['ok']} all_pass={report['all_pass']} "
          f"in {report['elapsed_s']}s")
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bl2_verify.run_loop", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--skip-install", action="store_true",
                   help="assume the package and mod are already installed (required for now)")
    p.add_argument("--package", default="PipelineMeshes", help="installed .upk base name")
    p.add_argument("--mod", default="pipeline_m2", help="sdk_mods folder name")
    p.add_argument("--control", default=str(DEFAULT_CONTROL))
    p.add_argument("--status", default=str(DEFAULT_STATUS))
    p.add_argument("--proposal", default=None, help="proposal JSON for bl2_lint")
    p.add_argument("--baseline", default=str(BASELINE))
    p.add_argument("--runs", default=str(RUNS))
    p.add_argument("--run-index", type=int, default=0, help="force the run number")
    p.add_argument("--part-path", default=None, help="default: from the control file")
    p.add_argument("--keep-save", action="store_true", help="do NOT restore the character save")
    p.add_argument("--mode", default="scancode", help="key injection mode (scancode|vk)")
    p.add_argument("--launch-timeout", type=float, default=180.0)
    p.add_argument("--menu-timeout", type=float, default=180.0)
    p.add_argument("--menu-settle", type=float, default=25.0)
    p.add_argument("--load-timeout", type=float, default=240.0)
    p.add_argument("--quit-timeout", type=float, default=90.0)
    p.add_argument("--pose-settle", type=float, default=4.0,
                   help="seconds after the mod's own 'done' timestamp at which the capture "
                        "burst starts; see _settle()")
    p.add_argument("--capture-anchor", choices=ANCHORS, default="done",
                   help="which status record --pose-settle is measured from; see _settle()")
    p.add_argument("--pose-poll", type=float, default=0.25,
                   help="status poll interval for the 'done' waits. The default 2.0 used "
                        "elsewhere is far coarser than the pawn's idle animation, so the "
                        "capture has to be pinned to the record's own timestamp instead")
    p.add_argument("--capture-view", choices=VIEWS, default="third",
                   help="which harness camera the captures are taken in; picks the weapon crop "
                        "(compare.WEAPON_BOX for third person, compare.FIRST_PERSON_BOX for "
                        "first) unless --weapon-box overrides it. Must match the installed "
                        "mod's options.harness_capture_view")
    p.add_argument("--weapon-box", default=None, metavar="X0,Y0,X1,Y1",
                   help="explicit crop for both pixel conditions (calibration aid)")
    p.add_argument("--burst", type=int, default=5,
                   help="frames captured per run for the reproducibility check")
    p.add_argument("--burst-interval", type=float, default=0.35,
                   help="seconds between burst frames. The point of the burst is to sample "
                        "several phases of the idle animation (F21), so this must NOT be a "
                        "multiple of the animation's period or every frame lands on the same "
                        "two phases and more frames buy nothing -- which is exactly what the "
                        "0.35 s default does to the M6 AK pose, whose period is ~0.7 s: its five "
                        "frames land on two phases and best_pair bottoms out around 1.7 "
                        "percent. 0.11 s over 14 frames samples 13 distinct phases of the "
                        "same cycle instead")
    p.add_argument("--input-test", default="", metavar="lmb,rmb,...",
                   help="after the scored capture, press these real mouse buttons in order "
                        "and record what the mod wrote (not a scored condition)")
    p.add_argument("--hud-watch", type=float, default=0.0, metavar="SECONDS",
                   help="after the scored capture, capture every frame the mod cues with a "
                        "hud_capture record (harness_hud_test), for up to SECONDS")
    p.add_argument("--game-res", default="", metavar="WxH",
                   help="launch in exclusive fullscreen at WxH for this run only (WillowEngine.ini "
                        "is backed up first and restored byte for byte afterwards)")
    p.add_argument("--hud-only", action="store_true",
                   help="with --hud-watch: stop after the HUD captures (no save/quit/reload)")
    p.add_argument("--input-delay", type=float, default=0.0,
                   help="seconds to wait before the first --input-test press (let an in-mod "
                        "fire test finish first)")
    p.add_argument("--input-gap", type=float, default=1.6,
                   help="seconds after each --input-test press")
    args = p.parse_args(argv)
    report = run_once(args)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
