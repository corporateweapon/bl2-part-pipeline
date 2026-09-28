# Verify: install, run, read

```
python bl2.py install <id>                 # harness build into the game (+ preflight)
python bl2.py verify <id> --run-index N    # preflight (NO GO stops) -> run_loop (~3 min) -> triage
python -m bl2_triage                       # verdict on the latest report, any time
python -m bl2_triage summarize <json>      # 20-line view of a report / status / lint file
```

**Leave the machine alone while it runs.** The loop sends keys to the foreground window for
three minutes. If you stop a run early the game may need closing by hand; the save is restored
by the loop (`SaveGuard`). If your save folder is cloud-synced (F30), copy the saves to
`backup/live-<stamp>/` before a session.

The four conditions, all four twice on runs N and N+1: changed vs the stock baseline (> 2 %),
stable across runs (< 0.5 %), lint clean, survives save/quit/reload. Make the baseline once
(`harness_equip: second_newest` variant, copy its capture to `docs/captures/<id>_stock_firstperson.png`).
Run against a clean save backup when the live backpack is full.

Read verdicts, not evidence. `bl2_triage` says one of:

- **BUILD OK** — all conditions passed.
- **INSTRUMENT** — the build is fine, the run failed: a dialog over the game (F27), the launching
  console in front (F32: run in the background, stdout redirected), foreign resolution with blind
  probes (F31), a third-person crop (F22), an F12 press (F23), a full backpack, an elemental roll,
  the item card left open, a `--part-path` the build does not register. Apply the named fix, rerun.
- **BUILD** — the spec or build is wrong: registration failed (the error names the object), parts
  missing on load (F29), lint errors, the crash signatures (F12/F16/F17). Fix the spec, `bl2 build`.
- **CONFIG** — mod not enabled (F8), two mods on one host gestalt, a stale build (F28).
- **UNDETERMINED** — open the raw status JSON; this is the only time you should.

Preflight (`python -m bl2_preflight --mod <Mod> --part-path … --dry-run`) is the same knowledge
before the launch: five seconds instead of three minutes. Only `--force` overrides a NO GO.

Then play it by hand: only a human sees a wrong feel, a casing spawning inside the receiver, or a
rate of fire that reads right on the card and wrong in the hand.
