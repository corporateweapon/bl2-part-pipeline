"""The object model behind the fake ``unrealsdk``.

Three types, each modelling one behaviour the generated mod leans on:

``FakeObject``   a UObject: class name (+ base classes, for ``find_all(exact=False)``),
                 an outer chain, ``ObjectFlags`` and arbitrary attributes.
``FakeStruct``   a value type. Assigning it into an array or reading it out copies it.
``WrappedArray`` a ``TArray`` view: ``append`` of a struct stores a *copy*, which is the
                 whole reason ``array.append(src); array[-1].Name = "new"`` is the idiom
                 the real mod uses.
"""

from __future__ import annotations

from typing import Any, Iterable

__all__ = ["FakeObject", "FakeStruct", "WrappedArray"]


def _copy_value(value: Any) -> Any:
    """Structs and arrays copy; object references are shared, as in the engine."""
    if isinstance(value, FakeStruct):
        return value.clone()
    if isinstance(value, WrappedArray):
        return WrappedArray(value)
    if isinstance(value, list):
        return [_copy_value(v) for v in value]
    return value


class FakeStruct:
    """A UStruct value: attribute bag with copy-on-assignment semantics."""

    def __init__(self, **fields: Any) -> None:
        for key, value in fields.items():
            setattr(self, key, value)

    def clone(self) -> FakeStruct:
        copy = FakeStruct()
        for key, value in self.__dict__.items():
            setattr(copy, key, _copy_value(value))
        return copy

    def __repr__(self) -> str:
        fields = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items())
        return f"FakeStruct({fields})"


class WrappedArray(list):
    """A ``TArray``: appending a struct appends a copy of it."""

    def append(self, value: Any) -> None:
        super().append(_copy_value(value))

    def extend(self, values: Iterable[Any]) -> None:
        for value in values:
            self.append(value)


class FakeObject:
    """A UObject. Unknown attributes raise ``AttributeError``, as in the SDK."""

    def __init__(
        self,
        cls: str,
        name: str,
        outer: FakeObject | None = None,
        *,
        bases: tuple[str, ...] = (),
        sep: str = ".",
        **attrs: Any,
    ) -> None:
        object.__setattr__(self, "_class", cls)
        object.__setattr__(self, "_bases", tuple(bases))
        object.__setattr__(self, "_sep", sep)
        self.Name = name
        self.Outer = outer
        self.ObjectFlags = 0
        for key, value in attrs.items():
            setattr(self, key, value)

    # -------------------------------------------------------------- identity
    @property
    def _class_chain(self) -> tuple[str, ...]:
        return (self._class, *self._bases, "Object")

    def _path_name(self) -> str:
        if self.Outer is None:
            return str(self.Name)
        return f"{self.Outer._path_name()}{self._sep}{self.Name}"

    def _clone(self, name: str, outer: FakeObject | None) -> FakeObject:
        copy = FakeObject(self._class, name, outer, bases=self._bases, sep=self._sep)
        for key, value in self.__dict__.items():
            if key in ("Name", "Outer"):
                continue
            setattr(copy, key, _copy_value(value))
        copy.Name = name
        copy.Outer = outer
        return copy

    def __repr__(self) -> str:
        return f"<{self._class} {self._path_name()}>"
