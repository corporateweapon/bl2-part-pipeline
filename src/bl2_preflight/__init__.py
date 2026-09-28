"""bl2_preflight: fail in five seconds, not after a three-minute game run.

Two things, both offline:

* :mod:`bl2_preflight.checks` -- everything about the machine, the game folder and the
  run's arguments that a verify run would otherwise discover a few minutes in: the game or
  a launcher already running, Steam absent, the mod not enabled (F8), two pipeline mods on
  one host gestalt (rule 3), a stale build (F28), a package missing, a hashed base package
  touched (F12), a dialog over the desktop (F27), the agent's own window in front (F32), a
  foreign desktop resolution (F31), a ``--part-path`` the build does not register, a missing
  baseline capture, harness options the loop needs (F23/F25).
* :mod:`bl2_preflight.dryrun` -- import the emitted mod against ``tests/fakes`` and run
  ``menu_setup`` (and the harness phase machine when the spec has one) exactly as the tests
  do, so a spec mistake is caught without the game.

    python -m bl2_preflight --mod PipelineAWPHarness --part-path GD_Weap_SniperRifles.Barrel.AWP_Barrel
    python -m bl2_preflight --mod PipelineAWPHarness --dry-run
    python -m bl2_preflight dry-run sdk_mod/PipelineAWPHarness
"""

from bl2_preflight.checks import Check, run_checks  # noqa: F401
from bl2_preflight.dryrun import DryRunResult, dry_run  # noqa: F401
