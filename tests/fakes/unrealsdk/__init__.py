"""A fake ``unrealsdk`` good enough to import and drive a generated mod offline.

It models the handful of behaviours the generated code depends on, and no more:

* an in-memory object graph with attribute access and an outer chain
  (:class:`FakeObject`, ``_path_name()``, ``Outer``, ``ObjectFlags``)
* ``find_object`` / ``find_all`` / ``construct_object`` / ``load_package``
* :class:`WrappedArray` -- appending a *struct* to a real ``WrappedArray`` copies it, so
  ``array.append(src); array[-1].Name = "x"`` edits the copy and not ``src``. Get this
  wrong and the mod looks like it corrupts the game's own fragment table.
* ``unrealsdk.logging``, ``unrealsdk.hooks`` (``Block``, ``Type``) and
  ``unrealsdk.unreal`` (``UObject``, ``WrappedStruct``, ``BoundFunction``) as submodules

Build a graph with :func:`tests.fakes.graph.build_graph`, install it with
:func:`set_world`, and the generated mod can be imported and stepped through.
"""

from __future__ import annotations

from typing import Any, Iterator

from . import hooks, logging, unreal  # noqa: F401 - imported for `unrealsdk.logging` etc.
from .objects import FakeObject, FakeStruct, WrappedArray

__all__ = [
    "FakeObject",
    "FakeStruct",
    "World",
    "WrappedArray",
    "construct_object",
    "find_all",
    "find_object",
    "hooks",
    "load_package",
    "logging",
    "make_struct",
    "set_world",
    "unreal",
    "world",
]


class World:
    """Everything ``find_object`` can see, plus the packages that can still be loaded."""

    def __init__(self) -> None:
        self.objects: dict[str, FakeObject] = {}
        #: packages that exist on disk but are not loaded yet: name -> [objects]
        self.pending_packages: dict[str, list[FakeObject]] = {}
        self.loaded_packages: list[str] = []
        self.constructed: list[FakeObject] = []

    # ------------------------------------------------------------------ building
    def add(self, obj: FakeObject) -> FakeObject:
        self.objects[obj._path_name()] = obj
        return obj

    def make(
        self, cls: str, name: str, outer: FakeObject | None = None, **attrs: Any
    ) -> FakeObject:
        obj = FakeObject(cls, name, outer, **attrs)
        return self.add(obj)

    # ------------------------------------------------------------------ lookup
    def _matches(self, obj: FakeObject, cls: str, exact: bool) -> bool:
        return obj._class == cls if exact else cls in obj._class_chain

    def find(self, cls: str, path: str, exact: bool = False) -> FakeObject:
        obj = self.objects.get(path)
        if obj is None or not self._matches(obj, cls, exact):
            raise ValueError(f"Couldn't find object {cls} {path}")
        return obj

    def all(self, cls: str, exact: bool = True) -> Iterator[FakeObject]:
        for obj in list(self.objects.values()):
            if self._matches(obj, cls, exact):
                yield obj


_world = World()


def set_world(new_world: World) -> World:
    """Install the object graph the module-level functions answer from."""
    global _world
    _world = new_world
    return _world


def world() -> World:
    return _world


# -------------------------------------------------------------------- the SDK surface
def find_object(cls: str, path: str) -> FakeObject:
    """Raises ``ValueError`` when the object is absent, like the real SDK."""
    return _world.find(cls, path)


def find_all(cls: str, exact: bool = True) -> Iterator[FakeObject]:
    return _world.all(cls, exact=exact)


def construct_object(
    cls: str,
    outer: FakeObject | None = None,
    name: str = "",
    flags: int = 0,
    template: FakeObject | None = None,
) -> FakeObject:
    """Clone ``template`` under ``outer``; the copy is a live object in the world."""
    obj = (
        template._clone(name, outer)
        if template is not None
        else FakeObject(cls, name, outer, bases=_BASES.get(cls, ()))
    )
    obj._class = template._class if template is not None else cls
    obj.ObjectFlags = int(flags)
    if cls == "MaterialInstanceConstant" and template is None:
        _material_instance(obj)
    _world.add(obj)
    _world.constructed.append(obj)
    return obj


#: base classes of objects constructed without a template, so find_all/find_object by a
#: base class still match them
_BASES = {
    "MaterialInstanceConstant": ("MaterialInstance", "MaterialInterface"),
}


def _material_instance(obj: FakeObject) -> None:
    """The UnrealScript surface a runtime MIC gets driven through."""
    obj.Parent = None
    obj.TextureParameterValues = {}
    obj.VectorParameterValues = {}
    obj.ScalarParameterValues = {}

    def set_parent(parent: FakeObject) -> None:
        obj.Parent = parent

    obj.SetParent = set_parent
    obj.SetTextureParameterValue = lambda name, value: obj.TextureParameterValues.__setitem__(str(name), value)
    obj.SetVectorParameterValue = lambda name, value: obj.VectorParameterValues.__setitem__(str(name), value)
    obj.SetScalarParameterValue = lambda name, value: obj.ScalarParameterValues.__setitem__(str(name), float(value))


def make_struct(name: str, **fields: Any) -> FakeStruct:
    """A struct value by name; the fake does not know the real field layout."""
    struct = FakeStruct(**fields)
    struct._struct_name = name  # type: ignore[attr-defined]
    return struct


def load_package(name: str, flags: int = 0) -> FakeObject:
    """Bring a package's objects into the world and return the package object."""
    for obj in _world.pending_packages.pop(name, []):
        _world.add(obj)
    if name not in _world.loaded_packages:
        _world.loaded_packages.append(name)
    return _world.find("Package", name)
