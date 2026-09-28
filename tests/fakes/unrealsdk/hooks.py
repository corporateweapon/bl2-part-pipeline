"""``unrealsdk.hooks``: the two names the generated mod imports."""

from __future__ import annotations

import enum

__all__ = ["Block", "Type"]


class _BlockSentinel:
    """Returned as ``(Block, value)`` to stop the engine call and override its result."""

    def __repr__(self) -> str:
        return "Block"


Block = _BlockSentinel()


class Type(enum.Enum):
    PRE = "pre"
    POST = "post"
    POST_UNCONDITIONAL = "post_unconditional"
