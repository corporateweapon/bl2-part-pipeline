"""``bl2_lint`` -- the collision linter (brief section 3.2).

Given a *proposal* (what the pipeline is about to build and register) and the
catalog produced by :mod:`bl2_catalog`, answer the questions that otherwise cost
hours of "patch installed, nothing changed" debugging:

===== ==============================================================================
 id    check
===== ==============================================================================
 L0    the proposal has the fields the other checks need
 L1    the new package name does not collide with a game package, hashed or not (F12)
 L2    the new fragment name does not already exist in the target weapon type
 L3    the new part path does not already exist, and nothing earlier-loaded wins (F1)
 L4    the proposed index range appends or tiles cleanly and is non-empty
 L5    the template fragment exists and carries the requested sockets (D2)
 L6    the part is registered into at least one real part list slot (F5)
 L7    object paths the proposal touches are not exported by several packages (F1)
 L8    registration happens at the main menu and roots against GC (F16, F17)
 L9    the mesh stays under the uint16 vertex ceiling (65,535) after the build
===== ==============================================================================

Usage::

    from bl2_catalog import load_catalog
    from bl2_lint import lint, load_proposal
    report = lint(load_proposal("proposal.json"), load_catalog())
    print(report.render())
    assert report.ok

CLI: ``python -m bl2_lint proposal.json [--catalog catalog/parts.json]``
(exit 0 when there are no errors, 1 otherwise).
"""

from __future__ import annotations

from typing import Any

from .checks import CHECK_TITLES, Context, run_checks
from .proposal import (
    FragmentProposal,
    MAX_GPU_VERTICES,
    PROPOSAL_SCHEMA_VERSION,
    Proposal,
    REQUIRED_FIELDS,
    load_proposal,
)
from .report import ERROR, Entry, NOTE, Report, SEVERITIES, WARNING

__all__ = [
    "CHECK_TITLES",
    "ERROR",
    "Entry",
    "FragmentProposal",
    "MAX_GPU_VERTICES",
    "NOTE",
    "PROPOSAL_SCHEMA_VERSION",
    "Proposal",
    "REQUIRED_FIELDS",
    "Report",
    "SEVERITIES",
    "WARNING",
    "lint",
    "load_proposal",
]


def lint(proposal: Proposal | dict[str, Any], catalog: dict[str, Any]) -> Report:
    """Run every check; returns a :class:`Report` (never raises on bad input)."""
    if isinstance(proposal, dict):
        proposal = Proposal.from_dict(proposal)
    meta = catalog.get("meta", {})
    report = Report(
        proposal=proposal.to_dict(),
        catalog_meta={
            "generated_utc": meta.get("generated_utc"),
            "load_order_source": meta.get("load_order_source"),
            "schema_version": catalog.get("schema_version"),
            "counts": meta.get("counts", {}),
        },
    )
    return run_checks(Context(proposal=proposal, catalog=catalog, report=report))
