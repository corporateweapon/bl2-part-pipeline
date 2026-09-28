"""CLI: ``python -m bl2_triage [triage|summarize] ...``

    python -m bl2_triage                          # triage the latest report in scratch/
    python -m bl2_triage --report scratch/verify_report_20260920-190747.json
    python -m bl2_triage --status scratch/PipelineAWPHarness_status.json   # no report: status only
    python -m bl2_triage --sdk-log scratch/unrealsdk_gen_run1.log --driver-log scratch/gen_run1.log
    python -m bl2_triage --json                   # machine-readable
    python -m bl2_triage summarize <report|status|lint.json> [--since EPOCH]

Exit status: 0 for BUILD OK, 2 for BUILD, 3 for CONFIG, 4 for INSTRUMENT, 5 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bl2_triage import evidence as ev_mod  # noqa: E402
from bl2_triage.rules import triage  # noqa: E402
from bl2_triage.summarize import summarize_file, summarize_report, summarize_status  # noqa: E402

EXIT = {"BUILD OK": 0, "BUILD": 2, "CONFIG": 3, "INSTRUMENT": 4}


def render(ev: ev_mod.Evidence, verbose: bool = False) -> tuple[list[str], int]:
    t = triage(ev)
    lines: list[str] = []
    if ev.report:
        lines.extend(summarize_report(ev.report))
    elif ev.status:
        lines.extend(summarize_status(ev.status))
    else:
        lines.append("no report and no status records")
    lines.append("")
    lines.append(f"VERDICT: {t.verdict} -- {t.headline}")
    for f in t.findings:
        tag = f"[{f.kind}]"
        lines.append(f"  {f.code:8} {tag:12} {f.evidence}")
        if f.fix and f.kind != "info":
            lines.append(f"           fix: {f.fix}")
        for d in f.detail[: (6 if verbose else 3)]:
            lines.append(f"           - {d}")
    src = []
    if ev.report_path:
        src.append(f"report={ev.report_path.name}")
    if ev.status_path:
        src.append(f"status={ev.status_path.name} ({len(ev.status)} recs)")
    if ev.driver.lines:
        src.append(f"driver={len(ev.driver.lines)} lines" + (f" pid={ev.driver.pid}" if ev.driver.pid else ""))
    if ev.sdk.lines:
        src.append(f"sdk={len(ev.sdk.lines)} lines")
    if ev.mods:
        en = [m.name for m in ev.mods if m.enabled]
        src.append(f"enabled pipeline mods={en or 'none'}")
    lines.append("evidence: " + "; ".join(src))
    for n in ev.notes:
        lines.append(f"note: {n}")
    return lines, EXIT.get(t.verdict, 5)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "summarize":
        p = argparse.ArgumentParser(prog="bl2_triage summarize")
        p.add_argument("file", type=Path)
        p.add_argument("--since", type=float, default=None, help="drop status records older than this epoch")
        a = p.parse_args(argv[1:])
        print("\n".join(summarize_file(a.file, since=a.since)))
        return 0
    if argv and argv[0] == "triage":
        argv = argv[1:]
    p = argparse.ArgumentParser(prog="bl2_triage", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--report", type=Path, default=None, help="verify/armory report JSON (default: latest in scratch/)")
    p.add_argument("--status", type=Path, default=None, help="mod status JSON (default: the report's)")
    p.add_argument("--driver-log", type=Path, default=ev_mod.DRIVER_LOG)
    p.add_argument("--sdk-log", type=Path, default=ev_mod.SDK_LOG)
    p.add_argument("--launch-log", type=Path, default=ev_mod.LAUNCH_LOG)
    p.add_argument("--game", type=Path, default=ev_mod.GAME)
    p.add_argument("--spec", type=Path, default=None, help="spec.json of the mod under test (default: installed copy)")
    p.add_argument("--no-game", action="store_true", help="do not read the game folder")
    p.add_argument("--json", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    ev = ev_mod.load_evidence(report=a.report, status=a.status, driver_log=a.driver_log, sdk_log=a.sdk_log,
                              launch_log=a.launch_log, game=None if a.no_game else a.game, spec=a.spec)
    if a.json:
        t = triage(ev)
        d = t.to_dict()
        d["report"] = str(ev.report_path) if ev.report_path else None
        print(json.dumps(d, indent=1))
        return EXIT.get(t.verdict, 5)
    lines, code = render(ev, verbose=a.verbose)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
