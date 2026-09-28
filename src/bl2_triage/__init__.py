"""bl2_triage: turn a verify run's raw evidence into a short verdict.

The verify loop leaves four kinds of evidence behind -- the report JSON, the mod's status
JSON, the driver log and ``unrealsdk.log`` -- and ``docs/FAILURE_MODES.md`` explains how to
tell thirty-odd silent failures apart from them. Reading all of that after every run is what
makes the loop expensive, and it is exactly the step a smaller model gets wrong. This package
encodes the discriminators so the reader gets a verdict, not the evidence::

    python -m bl2_triage                       # latest verify/armory report in scratch/
    python -m bl2_triage --report <json>       # a specific run
    python -m bl2_triage summarize <file>      # compact view of a report, status or lint JSON
    python -m bl2_triage --json                # machine-readable

Nothing here launches the game or touches the game folder except to *read* it.
"""

from bl2_triage.evidence import Evidence, load_evidence  # noqa: F401
from bl2_triage.rules import Finding, triage  # noqa: F401
from bl2_triage.summarize import summarize_file, summarize_report, summarize_status  # noqa: F401
