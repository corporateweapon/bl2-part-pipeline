"""``unrealsdk.unreal``: the type names the generated mod imports for annotations."""

from __future__ import annotations

from .objects import FakeObject, FakeStruct, WrappedArray

__all__ = ["BoundFunction", "UObject", "WrappedArray", "WrappedStruct"]

#: the generated mod only uses these in annotations, so aliases are enough
UObject = FakeObject
WrappedStruct = FakeStruct


class BoundFunction:
    """A UFunction bound to an object; the mod never calls one, it only annotates it."""

    def __init__(self, name: str = "", obj: object | None = None) -> None:
        self.name = name
        self.obj = obj
