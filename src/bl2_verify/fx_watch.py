"""Screenshot the game when the harness reports a freeze / shatter (cryo FX check).

``python -m bl2_verify.fx_watch --status scratch/<Mod>_status.json --out scratch/cryo_shots``
polls the status file; on each new ``{"freeze": "frozen"}`` record it grabs the screen at
+0.3 s and +1.2 s, and on ``{"freeze": "shatter"}`` at +0.1 s and +0.6 s (``capture.ps1``,
a GDI grab: it never takes the focus). Stops after ``--timeout`` seconds.
Meant to run beside ``bl2 verify`` with the probe's ``look_at`` on.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

CAPTURE = Path(__file__).resolve().parent / "capture.ps1"
SHOTS = {"frozen": (0.3, 1.2), "shatter": (0.1, 0.6), "thawed": (0.2,), "fire": (0.25, 0.6, 1.0)}


def grab(path: Path) -> None:
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(CAPTURE), "-Out", str(path)], capture_output=True, timeout=30,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=float, default=400.0)
    args = ap.parse_args(argv)
    status, out = Path(args.status), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    start = time.time()
    seen = 0
    events = 0
    while time.time() - start < args.timeout:
        try:
            records = json.loads(status.read_text(encoding="utf-8")) if status.exists() else []
        except (OSError, ValueError):
            records = []
        # the status file is rewritten per launch: only records stamped after we started count
        fresh = [r for r in records if isinstance(r, dict) and r.get("t", 0) >= start]
        for record in fresh[seen:]:
            kind = record.get("freeze") or ("fire" if record.get("fire") == "start" else None)
            if kind in SHOTS:
                events += 1
                t0 = time.time()
                for delay in SHOTS[kind]:
                    time.sleep(max(0.0, t0 + delay - time.time()))
                    grab(out / f"{events:02d}_{kind}_{int(delay * 1000)}ms.png")
        seen = len(fresh)
        time.sleep(0.1)
    print(f"{events} event(s) captured into {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
