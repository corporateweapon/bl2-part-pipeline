"""Reader for OpenBLCMM object dumps (``scratch/oe/data/BL2/dumps/*.dump.N``).

The dumps are the text form of UnrealScript's ``obj dump``: a sequence of blocks

    *** Property dump for object 'Class Some.Object.Path' ***
    === <Class> properties ===
    Prop=value
    ArrayProp(0)=value
    === Object properties ===
    Outer=Package'Foo'
    ...

Values are UnrealScript struct literals: ``(A=1,B=(X=0,Y=0),C=((P=..),(P=..)))``,
object references ``WeaponPartDefinition'Some.Path'``, quoted strings and bare
scalars. :func:`parse_value` turns one into nested ``dict`` / ``list`` / ``str``.

Nothing in here touches the game; it is pure text parsing over files the caller
supplies, so it is cheap to unit-test.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "DumpObject",
    "ObjRef",
    "iter_dump_objects",
    "obj_ref",
    "parse_value",
    "split_top_level",
    "unquote",
]

_HEADER = re.compile(r"^\*\*\* Property dump for object '([^ ]+) (.+)' \*\*\*\s*$")
_SECTION = re.compile(r"^=== .* ===\s*$")
# Prop, Prop(3) or Prop[3]
_PROP = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:[(\[](\d+)[)\]])?=(.*)$")
_OBJREF = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)'(.*)'$")

#: property line that opens the per-object trailer we do not care about
_OBJECT_SECTION = "=== Object properties ==="


@dataclass(frozen=True)
class ObjRef:
    """A parsed ``Class'Path'`` reference."""

    cls: str
    path: str


@dataclass
class DumpObject:
    """One ``*** Property dump ***`` block.

    ``props`` holds scalar properties as raw (unparsed) strings; ``arrays`` holds
    indexed properties (``Foo(0)=``) as lists of raw strings, densely ordered by
    the index in the dump. ``trailer`` holds the ``=== Object properties ===``
    section (``Outer``, ``Name``, ``Class``, ...).
    """

    cls: str
    path: str
    props: dict[str, str] = field(default_factory=dict)
    arrays: dict[str, list[str]] = field(default_factory=dict)
    trailer: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """Leaf object name (the part after the last ``.`` or ``:``)."""
        return re.split(r"[.:]", self.path)[-1]

    @property
    def outer_path(self) -> str:
        """Everything before the leaf name, i.e. the containing object's path."""
        head = re.split(r"[.:]", self.path)[:-1]
        return ".".join(head)

    @property
    def package(self) -> str:
        """The first path segment, which is the object's top-level package name."""
        return re.split(r"[.:]", self.path)[0]

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.props.get(key, default)

    def array(self, key: str) -> list[str]:
        return self.arrays.get(key, [])


def unquote(value: str) -> str:
    """Strip one layer of double quotes, if present."""
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def obj_ref(value: str) -> ObjRef | None:
    """Parse ``Class'Path'``; return ``None`` for ``None``/scalars."""
    value = value.strip()
    match = _OBJREF.match(value)
    if match is None:
        return None
    return ObjRef(match.group(1), match.group(2))


def split_top_level(body: str, sep: str = ",") -> list[str]:
    """Split ``body`` on ``sep``, ignoring separators inside ``()``/quotes."""
    out: list[str] = []
    depth = 0
    quote: str | None = None
    start = 0
    for i, ch in enumerate(body):
        if quote is not None:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append(body[start:i])
            start = i + 1
    out.append(body[start:])
    return out


def parse_value(value: str) -> str | dict[str, object] | list[object]:
    """Parse an UnrealScript literal into ``str`` / ``dict`` / ``list``.

    ``(A=1,B=2)`` becomes a dict, ``((A=1),(A=2))`` a list, anything else is
    returned as the stripped source text (object refs and quoted strings stay
    verbatim; use :func:`obj_ref` / :func:`unquote` on them).
    """
    value = value.strip()
    if not (value.startswith("(") and value.endswith(")")):
        return value
    inner = value[1:-1].strip()
    if not inner:
        return {}
    items = split_top_level(inner)
    out_dict: dict[str, object] = {}
    is_struct = True
    for item in items:
        item = item.strip()
        match = _PROP.match(item)
        if match is None:
            is_struct = False
            break
        key, index, raw = match.group(1), match.group(2), match.group(3)
        if index is not None:
            bucket = out_dict.setdefault(key, [])
            if isinstance(bucket, list):
                bucket.append(parse_value(raw))
        else:
            out_dict[key] = parse_value(raw)
    if is_struct:
        return out_dict
    return [parse_value(item) for item in items]


def iter_dump_objects(
    paths: Iterable[Path] | Path,
    classes: Iterable[str] | None = None,
    path_prefixes: Iterable[str] | None = None,
) -> Iterator[DumpObject]:
    """Stream :class:`DumpObject` blocks out of one or more dump files.

    ``classes`` / ``path_prefixes`` filter cheaply on the header line so that
    multi-megabyte dumps never have to be materialised.
    """
    if isinstance(paths, Path):
        paths = [paths]
    wanted = set(classes) if classes is not None else None
    prefixes = tuple(path_prefixes) if path_prefixes is not None else None
    for file_path in paths:
        if not file_path.exists():
            continue
        current: DumpObject | None = None
        in_trailer = False
        with file_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                header = _HEADER.match(line)
                if header is not None:
                    if current is not None:
                        yield current
                    cls, obj_path = header.group(1), header.group(2)
                    keep = (wanted is None or cls in wanted) and (
                        prefixes is None or obj_path.startswith(prefixes)
                    )
                    current = DumpObject(cls=cls, path=obj_path) if keep else None
                    in_trailer = False
                    continue
                if current is None:
                    continue
                if _SECTION.match(line):
                    in_trailer = line.strip() == _OBJECT_SECTION
                    continue
                prop = _PROP.match(line.rstrip("\n"))
                if prop is None:
                    continue
                key, index, raw = prop.group(1), prop.group(2), prop.group(3)
                target = current.trailer if in_trailer else None
                if target is not None:
                    target[key] = raw
                elif index is None:
                    current.props[key] = raw
                else:
                    current.arrays.setdefault(key, []).append(raw)
        if current is not None:
            yield current
