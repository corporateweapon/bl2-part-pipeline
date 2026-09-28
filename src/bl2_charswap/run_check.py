"""Unattended in-game check for a character skin: launch BL2 with PipelineCharacters, load the
newest save, capture first person / behind view / inventory, write a census, save-quit, close.

    python -m bl2_charswap.run_check [--no-quit] [--stamp X]

Captures land in scratch/charswap/runs/; evidence in scratch/PipelineCharacters_status.json.
The save is backed up first (bl2_verify.save_guard) and should be restored afterwards.
"""
import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
from bl2_verify.game_driver import GameDriver, read_status  # noqa: E402
from bl2_verify.save_guard import SaveGuard  # noqa: E402

STATUS = REPO / "scratch" / "PipelineCharacters_status.json"
OUT = REPO / "scratch" / "charswap" / "runs"


def phase(recs, name, **match):
    return [r for r in recs if r.get("phase") == name and all(r.get(k) == v for k, v in match.items())]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-quit", action="store_true")
    ap.add_argument("--stamp", default=time.strftime("%H%M%S"))
    ap.add_argument("--character", type=int, default=None, metavar="N",
                    help="pick the N-th row (0-based) of SELECT CHARACTER before CONTINUE")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    d = GameDriver()
    d.log("=" * 60)
    d.log("charswap_run: launching")
    guard = SaveGuard(log=d.log)
    print("save backup:", guard.backup(tag=f"charswap_{args.stamp}"))
    d.launch(timeout=180)
    since = d.launch_time
    d.skip_intro(timeout=180)
    recs = d.wait_status(STATUS, lambda r: bool(phase(r, "setup")), timeout=180, since=since, what="setup")
    setup = phase(recs, "setup")[0]
    print("SETUP:", json.dumps({k: v for k, v in setup.items() if k != "t"}, indent=1)[:2500])
    if not setup.get("ok"):
        print("setup failed; leaving the game up")
        return 2
    if args.character is not None:
        # main menu: CONTINUE, NEW GAME, MATCHMAKING, NETWORK OPTIONS, SOCIAL, SELECT CHARACTER, ...
        d.focus()
        d.press_seq(["down"] * 5, delay=0.6)
        d.press("enter", after=4.0)
        d.press_seq(["down"] * args.character, delay=1.2)
        d.capture(OUT / f"{args.stamp}_cs_select.png")
        d.press("enter", after=4.0)           # selecting returns to the main menu with that save current
        time.sleep(3.0)
        d.focus()
        d.press_seq(["up"] * 6, delay=0.7)     # highlight back on CONTINUE (fast presses get dropped)
        time.sleep(1.0)
    d.continue_game()
    recs = d.wait_status(STATUS, lambda r: bool(phase(r, "map_loaded")), timeout=240, since=since, what="map_loaded")
    time.sleep(8.0)
    for r in read_status(STATUS, since=since):
        if r.get("phase") == "scan":
            print("SCAN:", r.get("reason"), *r.get("acted", []), sep="\n   ")
    d.capture(OUT / f"{args.stamp}_cs_firstperson.png")
    d.press("f7", after=2.5)
    d.capture(OUT / f"{args.stamp}_cs_behind.png")
    d.press("f7", after=1.5)
    d.press("f9", after=1.0)                      # census before the menu
    d.press("f6", after=4.0)                      # inventory
    d.capture(OUT / f"{args.stamp}_cs_inventory.png")
    time.sleep(12.0)                              # the 3D preview appears late
    d.capture(OUT / f"{args.stamp}_cs_inventory_late.png")
    d.press("right", after=6.0)
    d.capture(OUT / f"{args.stamp}_cs_inventory_backpack.png")
    time.sleep(1.0)
    for r in read_status(STATUS, since=since):
        if r.get("phase") == "scan" and r.get("reason") == "tick":
            print("TICK SCAN:", *r.get("acted", []), sep="\n   ")
    cen = phase(read_status(STATUS, since=since), "census")
    if cen:
        print("CENSUS:")
        for row in cen[-1]["components"]:
            if "Psycho" in row or "PipelineMeshes" in row or "Head" in row:
                print("   ", row)
    d.press("esc", after=2.0)
    if not args.no_quit:
        d.save_and_quit(wait_menu=20.0)
        d.quit_game()
    return 0


if __name__ == "__main__":
    sys.exit(main())
