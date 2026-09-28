"""Drive the bone-motion probe, then answer the bone-map question from its numbers.

Two halves, deliberately separable:

``--run``      install `anim_probe_mod_template.py` beside the Armory, back the save up,
               launch, CONTINUE, let the Armory auto-spawn the weapon, let the probe
               fire and reload it, quit, restore the save.
``--analyse``  read `scratch/anim_probe.json` and print, per bone, how far it moves and
               how much it **rotates** relative to `Root` across the whole recording.

The rotation is the number that decides the bone map (M9 §4). A vertex bound to a bone
is carried by ``current_bone_transform * inverse(ref_bone_transform)``, so a bone that
only translates moves its geometry rigidly by that much, wherever the geometry sits --
safe. A bone that *rotates* moves geometry by roughly ``angle * distance_from_the_bone``,
and the AWP's bolt sits 12-28 units from `Jakobs_BoltHandle`, so a few degrees there is
centimetres of swing on the gun. ``--analyse`` reports both, plus the swing each bone
would produce at the AWP's own geometry distance.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from bl2_verify.game_driver import DriverError, GameDriver  # noqa: E402
from bl2_verify.save_guard import SaveGuard  # noqa: E402

PROBE_MOD = "PipelineAnimProbe"
OUT = REPO / "scratch" / "anim_probe.json"
CONTROL = REPO / "scratch" / "anim_probe_control.json"

#: how far the AWP's own bolt and magazine sit from the bone that would drive them,
#: measured off the retarget report (mesh space, add-on Blender frame)
AWP_DISTANCE = {"bolt": 20.0, "magazine": 8.0}

#: below this a bone is rigid; measured noise floor is ~0.002 u
MOVE_EPSILON = 0.05


# --------------------------------------------------------------------------- run


def install(game: Path) -> Path:
    """Drop the probe mod in as a disposable mod; it registers nothing (no gestalt)."""
    mod_dir = game / "sdk_mods" / PROBE_MOD
    mod_dir.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).with_name("anim_probe_mod_template.py")
    text = source.read_text(encoding="utf-8").replace("__REPO__", str(REPO))
    (mod_dir / "__init__.py").write_text(text, encoding="utf-8")
    settings = game / "sdk_mods" / "settings" / f"{PROBE_MOD}.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{\n "enabled": true\n}\n', encoding="utf-8")
    return mod_dir


def uninstall(game: Path) -> None:
    shutil.rmtree(game / "sdk_mods" / PROBE_MOD, ignore_errors=True)
    (game / "sdk_mods" / "settings" / f"{PROBE_MOD}.json").unlink(missing_ok=True)


def run(args: argparse.Namespace) -> int:
    game = Path(args.game)
    armory = game / "sdk_mods" / args.armory
    control = armory / "control.json"
    OUT.unlink(missing_ok=True)
    CONTROL.write_text(json.dumps({"weapon_name": args.weapon_name, "autoexit": True}),
                       encoding="utf-8")
    install(game)
    # the Armory spawns and equips the weapon for us; the probe waits for it by name
    control.write_text(json.dumps({"auto_spawn": [args.weapon]}), encoding="utf-8")

    # echo=False on purpose (F32): every printed line streams to the agent's console,
    # the desktop app raises itself to render it, and BL2 -- exclusive fullscreen --
    # minimises (F20). It was the foreground window 56 times in one aborted run.
    # scratch/driver.log still gets everything.
    driver = GameDriver(echo=args.echo)
    driver.log("=" * 78)
    driver.log(f"anim_probe: {args.weapon} via {args.armory}")
    guard = SaveGuard(log=driver.log)
    backup = guard.backup(tag="animprobe")
    driver.log(f"save backed up to {backup}")
    ok = False
    try:
        driver.launch(timeout=args.launch_timeout)
        driver.skip_intro(timeout=args.launch_timeout)
        driver.wait_main_menu(timeout=args.menu_timeout)
        driver.continue_game()
        deadline = time.time() + args.probe_timeout
        while time.time() < deadline:
            if OUT.exists():
                ok = True
                break
            if not driver.is_running():
                break
            time.sleep(2.0)
        driver.log(f"probe output present: {ok}")
    except DriverError as ex:
        driver.log(f"FAILED {ex}")
    finally:
        if driver.is_running():
            try:
                driver.quit_game(timeout=args.quit_timeout)
            except DriverError:
                driver.kill()
        control.unlink(missing_ok=True)
        uninstall(game)
        guard.restore(backup)
        driver.log(f"save restored, verified={guard.verify_restored(backup)}")
    if not ok:
        print("no probe output; see scratch/driver.log", flush=True)
        return 1
    return analyse(argparse.Namespace(path=str(OUT), json_out=args.json_out))


# ----------------------------------------------------------------------- analyse


def _conj(q: tuple[float, ...]) -> tuple[float, ...]:
    x, y, z, w = q
    return (-x, -y, -z, w)


def _qmul(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def _rotate(q: tuple[float, ...], v: list[float]) -> tuple[float, float, float]:
    x, y, z, _ = _qmul(_qmul(q, (v[0], v[1], v[2], 0.0)), _conj(q))
    return (x, y, z)


def _angle_between(a: Any, b: Any) -> float:
    """Degrees between two unit quaternions (shortest arc); 0.0 if either is unusable."""
    if not (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple))
            and len(a) == 4 and len(b) == 4):
        return 0.0
    dot = abs(sum(x * y for x, y in zip(a, b)))
    return math.degrees(2.0 * math.acos(max(-1.0, min(1.0, dot))))


def _local(bones: dict[str, Any], bone: str) -> tuple[float, float, float] | None:
    """Bone position in ROOT'S OWN FRAME.

    Subtracting Root's *position* is not enough: the whole weapon swings with recoil and
    sway, and a rigid bone 25 units out then reads tens of units of "travel" purely from
    that rotation -- which is exactly the false signal the first version of this analysis
    produced. Un-rotating by Root's quaternion as well leaves only motion the animation
    actually applies to the bone.
    """
    here, root = bones.get(bone), bones.get("Root")
    if not here or not root:
        return None
    if not isinstance(here.get("location"), list) or not isinstance(root.get("quat"), list):
        return None
    delta = [h - r for h, r in zip(here["location"], root["location"])]
    return _rotate(_conj(tuple(root["quat"])), delta)


def _local_quat(bones: dict[str, Any], bone: str) -> tuple[float, ...] | None:
    here, root = bones.get(bone), bones.get("Root")
    if not here or not root:
        return None
    if not isinstance(here.get("quat"), list) or not isinstance(root.get("quat"), list):
        return None
    return _qmul(_conj(tuple(root["quat"])), tuple(here["quat"]))


def analyse(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.path).read_text(encoding="utf-8"))
    samples = data.get("samples") or []
    if not samples:
        print(f"no samples in {args.path}: {data.get('error') or data.get('events')}")
        return 1
    weapon = data.get("weapon", {})
    phases = sorted({s["phase"] for s in samples})
    print(f"weapon: {weapon.get('name')}  ({weapon.get('balance')})")
    print(f"mesh:   {weapon.get('mesh')}")
    print(f"samples: {len(samples)} over {samples[-1]['t'] - samples[0]['t']:.1f}s, "
          f"phases {phases}")
    triggered = [e for e in data.get("events", []) if "reload_via" in e]
    if triggered and not triggered[0].get("reload_via"):
        print("NOTE: no reload was triggered -- the 'reload' phase is idle, not a reload")
    print()

    ref = data.get("bones_ref", {})
    rows = []
    for bone in data.get("bones", []):
        base, base_q = _local(ref, bone), _local_quat(ref, bone)
        if base is None:
            continue
        per_phase = {p: 0.0 for p in phases}
        spin = 0.0
        peak = None
        for sample in samples:
            here = _local(sample["bones"], bone)
            if here is not None:
                moved = math.dist(base, here)
                if moved > per_phase.get(sample["phase"], 0.0):
                    per_phase[sample["phase"]] = moved
                if peak is None or moved > peak[0]:
                    peak = (moved, [round(h - b, 3) for h, b in zip(here, base)], sample["phase"])
            here_q = _local_quat(sample["bones"], bone)
            if here_q is not None and base_q is not None:
                spin = max(spin, _angle_between(here_q, base_q))
        rows.append((bone, per_phase, spin, peak))

    rows.sort(key=lambda r: -max(r[1].values() or [0.0]))
    head = "".join(f"{p:>10}" for p in phases)
    print(f"{'bone':<26}{head}{'spin':>8}")
    for bone, per_phase, spin, _peak in rows:
        line = "".join(f"{per_phase.get(p, 0.0):10.3f}" for p in phases)
        print(f"{bone:<26}{line}{spin:8.2f}")

    movers = [r for r in rows if max(r[1].values() or [0.0]) > MOVE_EPSILON]
    print(f"\nbones the animation actually drives (> {MOVE_EPSILON} u in root-local space):")
    if not movers:
        print("  none -- either no clip played, or this weapon type does not drive any of them")
    for bone, per_phase, spin, peak in movers:
        where = max(per_phase, key=lambda p: per_phase[p])
        kind = "pure translation" if spin < 1.0 else f"ROTATES {spin:.1f} deg"
        print(f"  {bone}: {peak[0]:.3f} u during '{where}', offset {peak[1]}, {kind}")
        if spin < 1.0:
            print(f"      -> safe to bind geometry to at any distance from the bone")
        else:
            for label, distance in AWP_DISTANCE.items():
                print(f"      -> {math.radians(spin) * distance:.2f} u of swing "
                      f"{distance:.0f} u away ({label}'s distance)")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            {"weapon": weapon,
             "bones": [{"bone": b, "per_phase": {k: round(v, 4) for k, v in p.items()},
                        "spin_deg": round(s, 3),
                        "peak_offset": pk[1] if pk else None} for b, p, s, pk in rows]},
            indent=1), encoding="utf-8")
        print(f"\n-> {args.json_out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="anim_probe", description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="store_true", help="drive the game and then analyse")
    parser.add_argument("--analyse", action="store_true", help="analyse an existing capture")
    parser.add_argument("--path", default=str(OUT))
    parser.add_argument("--json-out", default=str(REPO / "scratch" / "anim_probe_summary.json"))
    parser.add_argument("--game", default=r"C:\Program Files (x86)\Steam\steamapps\common\Borderlands 2")
    parser.add_argument("--armory", default="PipelineArmory")
    parser.add_argument("--weapon", default="awp", help="Armory id to auto-spawn")
    parser.add_argument("--weapon-name", default="AWP", help="name the probe waits to see in hand")
    parser.add_argument("--launch-timeout", type=float, default=180.0)
    parser.add_argument("--menu-timeout", type=float, default=180.0)
    parser.add_argument("--probe-timeout", type=float, default=180.0)
    parser.add_argument("--quit-timeout", type=float, default=90.0)
    parser.add_argument("--echo", action="store_true",
                        help="stream the driver log to stdout; off by default "
                             "because doing so steals focus from the game (F32)")
    args = parser.parse_args(argv)
    if args.run:
        return run(args)
    return analyse(args)


if __name__ == "__main__":
    raise SystemExit(main())
