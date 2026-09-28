"""``unrealsdk.logging``: collects lines instead of writing to unrealsdk.log."""

from __future__ import annotations

__all__ = ["dev_warning", "error", "info", "misc", "records", "warning"]

records: list[tuple[str, str]] = []


def _record(level: str, *args: object) -> None:
    records.append((level, " ".join(str(a) for a in args)))


def info(*args: object) -> None:
    _record("info", *args)


def warning(*args: object) -> None:
    _record("warning", *args)


def error(*args: object) -> None:
    _record("error", *args)


def dev_warning(*args: object) -> None:
    _record("dev_warning", *args)


def misc(*args: object) -> None:
    _record("misc", *args)
