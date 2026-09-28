"""Report objects shared by every linter check."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["ERROR", "Entry", "NOTE", "Report", "SEVERITIES", "WARNING"]

ERROR = "error"
WARNING = "warning"
NOTE = "note"

#: most severe first -- render order and the `ok` test both key off this
SEVERITIES = (ERROR, WARNING, NOTE)

_MARK = {ERROR: "ERROR  ", WARNING: "WARNING", NOTE: "note   "}


@dataclass(frozen=True)
class Entry:
    """One linter finding: a check id, a severity and a one-line message."""

    check: str
    severity: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class Report:
    """The result of :func:`bl2_lint.lint`."""

    entries: list[Entry] = field(default_factory=list)
    proposal: dict[str, Any] = field(default_factory=dict)
    catalog_meta: dict[str, Any] = field(default_factory=dict)

    def add(self, check: str, severity: str, message: str, **detail: Any) -> Entry:
        entry = Entry(check=check, severity=severity, message=message, detail=detail)
        self.entries.append(entry)
        return entry

    @property
    def errors(self) -> list[Entry]:
        return [e for e in self.entries if e.severity == ERROR]

    @property
    def warnings(self) -> list[Entry]:
        return [e for e in self.entries if e.severity == WARNING]

    @property
    def notes(self) -> list[Entry]:
        return [e for e in self.entries if e.severity == NOTE]

    @property
    def ok(self) -> bool:
        """True when nothing is an error (warnings and notes do not block)."""
        return not self.errors

    def by_check(self, check: str) -> list[Entry]:
        return [e for e in self.entries if e.check == check]

    def has(self, check: str, severity: str | None = None) -> bool:
        return any(
            e.check == check and (severity is None or e.severity == severity) for e in self.entries
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": {s: len([e for e in self.entries if e.severity == s]) for s in SEVERITIES},
            "entries": [e.to_dict() for e in self.entries],
            "proposal": self.proposal,
            "catalog": self.catalog_meta,
        }

    def render(self) -> str:
        """Human-readable report; the CLI prints exactly this."""
        lines: list[str] = []
        head = self.proposal.get("part_path") or self.proposal.get("fragment") or "<proposal>"
        lines.append(f"bl2_lint: {head}")
        source = self.catalog_meta.get("load_order_source")
        generated = self.catalog_meta.get("generated_utc")
        if generated:
            lines.append(f"  catalog generated {generated} (load order: {source})")
        if not self.entries:
            lines.append("  no findings")
        for severity in SEVERITIES:
            for entry in (e for e in self.entries if e.severity == severity):
                lines.append(f"  [{_MARK[severity]}] {entry.check}: {entry.message}")
                for key, value in sorted(entry.detail.items()):
                    rendered = _render_value(value)
                    if rendered is not None:
                        lines.append(f"            {key}: {rendered}")
        counts = {s: len([e for e in self.entries if e.severity == s]) for s in SEVERITIES}
        verdict = "PASS" if self.ok else "FAIL"
        lines.append(
            f"  {verdict} -- {counts[ERROR]} error(s), {counts[WARNING]} warning(s), "
            f"{counts[NOTE]} note(s)"
        )
        return "\n".join(lines)


def _render_value(value: Any) -> str | None:
    if value is None or value == [] or value == {}:
        return None
    if isinstance(value, list):
        if len(value) > 8:
            return ", ".join(str(v) for v in value[:8]) + f", ... (+{len(value) - 8} more)"
        return ", ".join(str(v) for v in value)
    return str(value)
