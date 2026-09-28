"""A fake ``mods_base`` good enough to import and drive a generated mod offline.

``hook`` and ``keybind`` wrap the decorated function in a small object that records its
target and whether it is enabled -- which is exactly what the F8 test checks (every hook
must be enabled at import, because auto_enable will not do it on the first launch).
Calling the wrapper calls the function, so a test can fire a hook by hand.

``command`` mirrors the real argparse-backed ``ArgParseCommand`` closely enough that a test
can type a console line (``cmd.run("spawn awp --level 50")``); the option classes keep their
constructor shapes (``DropdownOption(identifier, value, choices)``, ``ButtonOption(identifier,
on_press=...)``, ``NestedOption(identifier, children)``) and record presses and value changes.
"""

from __future__ import annotations

import argparse
import shlex
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = [
    "ArgParseCommand",
    "BaseOption",
    "ButtonOption",
    "DropdownOption",
    "Hook",
    "Keybind",
    "Mod",
    "NestedOption",
    "build_mod",
    "built_mods",
    "command",
    "get_pc",
    "hook",
    "keybind",
    "set_pc",
]


class Hook:
    """What ``@hook(...)`` turns a function into."""

    def __init__(self, func: Callable[..., Any], target: str, hook_type: Any) -> None:
        self.func = func
        self.target = target
        self.type = hook_type
        self.enabled = False
        self.__name__ = getattr(func, "__name__", "hook")
        self.__doc__ = func.__doc__

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.func(*args, **kwargs)

    def __repr__(self) -> str:
        state = "enabled" if self.enabled else "disabled"
        return f"<Hook {self.__name__} {self.target} {self.type} {state}>"


class Keybind:
    """What ``@keybind(...)`` turns a function into."""

    def __init__(self, func: Callable[..., Any], description: str, key: str | None) -> None:
        self.func = func
        self.description = description
        self.key = key
        self.__name__ = getattr(func, "__name__", "keybind")

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.func(*args, **kwargs)

    def __repr__(self) -> str:
        return f"<Keybind {self.description!r} {self.key}>"


def hook(target: str, hook_type: Any = None) -> Callable[[Callable[..., Any]], Hook]:
    def decorator(func: Callable[..., Any]) -> Hook:
        return Hook(func, target, hook_type)

    return decorator


def keybind(description: str, key: str | None = None) -> Callable[[Callable[..., Any]], Keybind]:
    def decorator(func: Callable[..., Any]) -> Keybind:
        return Keybind(func, description, key)

    return decorator


# --------------------------------------------------------------------- console commands
class ArgParseCommand:
    """What ``@command("name")`` turns a function into: an argparse parser plus callback.

    The real one is fed the console line by the SDK; here ``run(line)`` plays that role
    (the text after the command name), and ``SystemExit`` from argparse is swallowed the
    way the real ``_handle_cmd`` does.
    """

    def __init__(self, func: Callable[[argparse.Namespace], None], cmd: str, **kwargs: Any) -> None:
        self.callback = func
        self.cmd = cmd
        self.parser = argparse.ArgumentParser(prog=cmd, exit_on_error=False, **kwargs)
        self.enabled = False
        self.__name__ = getattr(func, "__name__", cmd)

    def add_argument(self, *args: Any, **kwargs: Any) -> argparse.Action:
        return self.parser.add_argument(*args, **kwargs)

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False

    def run(self, line: str) -> None:
        """Feed the console line (without the command name), as the game would."""
        try:
            self.callback(self.parser.parse_args(shlex.split(line)))
        except (SystemExit, argparse.ArgumentError):
            pass

    def __call__(self, args: argparse.Namespace) -> None:
        self.callback(args)

    def __repr__(self) -> str:
        return f"<ArgParseCommand {self.cmd}>"


def command(cmd: str | None = None, **kwargs: Any) -> Callable[[Callable[..., Any]], ArgParseCommand]:
    def decorator(func: Callable[..., Any]) -> ArgParseCommand:
        return ArgParseCommand(func, cmd or func.__name__, **kwargs)

    return decorator


# --------------------------------------------------------------------- options
class BaseOption:
    def __init__(self, identifier: str, *, display_name: str | None = None,
                 description: str = "", description_title: str | None = None,
                 is_hidden: bool = False) -> None:
        self.identifier = identifier
        self.display_name = display_name or identifier
        self.description = description
        self.description_title = description_title or self.display_name
        self.is_hidden = is_hidden
        self.mod: Mod | None = None

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.identifier!r}>"


class DropdownOption(BaseOption):
    """``DropdownOption(identifier, value, choices)``; ``value`` is read and written."""

    def __init__(self, identifier: str, value: str, choices: list[str],
                 *, on_change_anytime: Callable[[Any, str], None] | None = None,
                 on_change_while_enabled: Callable[[Any, str], None] | None = None,
                 **kwargs: Any) -> None:
        super().__init__(identifier, **kwargs)
        self.choices = list(choices)
        self.default_value = value
        self._value = value
        self.on_change_anytime = on_change_anytime
        self.on_change_while_enabled = on_change_while_enabled

    @property
    def value(self) -> str:
        return self._value

    @value.setter
    def value(self, new: str) -> None:
        if self.on_change_anytime is not None:
            self.on_change_anytime(self, new)
        if self.on_change_while_enabled is not None:
            self.on_change_while_enabled(self, new)
        self._value = new


class ButtonOption(BaseOption):
    """``ButtonOption(identifier, on_press=...)``; ``press()`` is what the menu does."""

    def __init__(self, identifier: str, *, on_press: Callable[[Any], None] | None = None,
                 **kwargs: Any) -> None:
        super().__init__(identifier, **kwargs)
        self.on_press = on_press
        self.presses = 0

    def __call__(self, on_press: Callable[[Any], None]) -> ButtonOption:
        self.on_press = on_press
        return self

    def press(self) -> None:
        self.presses += 1
        if self.on_press is not None:
            self.on_press(self)


class NestedOption(BaseOption):
    def __init__(self, identifier: str, children: list[BaseOption], **kwargs: Any) -> None:
        super().__init__(identifier, **kwargs)
        self.children = list(children)


# --------------------------------------------------------------------- the mod
@dataclass
class Mod:
    name: str
    description: str = ""
    hooks: list[Hook] = field(default_factory=list)
    keybinds: list[Keybind] = field(default_factory=list)
    options: list[BaseOption] = field(default_factory=list)
    commands: list[ArgParseCommand] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def find_option(self, identifier: str) -> BaseOption:
        stack = list(self.options)
        while stack:
            option = stack.pop(0)
            if option.identifier == identifier:
                return option
            if isinstance(option, NestedOption):
                stack.extend(option.children)
        raise KeyError(identifier)


#: every mod built in this process, newest last
built_mods: list[Mod] = []


def build_mod(
    name: str = "",
    description: str = "",
    hooks: list[Hook] | None = None,
    keybinds: list[Keybind] | None = None,
    options: list[BaseOption] | None = None,
    commands: list[ArgParseCommand] | None = None,
    **extra: Any,
) -> Mod:
    mod = Mod(
        name=name,
        description=description,
        hooks=list(hooks or []),
        keybinds=list(keybinds or []),
        options=list(options or []),
        commands=list(commands or []),
        extra=extra,
    )
    for option in mod.options:
        option.mod = mod
    for cmd in mod.commands:
        cmd.enable()
    built_mods.append(mod)
    return mod


_pc: Any = None


def set_pc(controller: Any) -> None:
    """Install the object ``get_pc()`` answers with."""
    global _pc
    _pc = controller


def get_pc(possibly_loading: bool = False) -> Any:
    return _pc
