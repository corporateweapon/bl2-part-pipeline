"""PipelineCharacters -- character model swaps (skins built by bl2_charswap); the pipeline's dev host.

The swap logic lives in ``runtime.py`` beside this file (``src/bl2_charswap/runtime.py``, copied
by ``python -m bl2_charswap install``); the Armory carries the same module for character packs.
This host reads its skins from ``characters.json`` (written by the installer) plus drop-in
``skins/<id>.json`` files, and turns on the test keys (F7 behind view, F9 census).

Don't install this beside an Armory that has character packs: the Armory stands down its own
character support while this folder is in sdk_mods, so the two never swap the same mesh.

Console: ``characters list | set <character> <skin|default> | census | reapply``.
Status records: scratch/PipelineCharacters_status.json in the pipeline repo (beside the mod on
any other machine).
"""
from __future__ import annotations

import json
import os
from typing import Any

import unrealsdk
from mods_base import build_mod

from . import runtime

__version__ = "0.2.0"
__author__ = "44M0N"

MOD_NAME = "PipelineCharacters"
HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_STATUS = os.path.join(os.environ.get("BL2_PIPELINE_REPO", ""), "scratch", "PipelineCharacters_status.json")
# without the pipeline's scratch folder (any player's machine) the status is kept beside the mod
STATUS = (_PIPELINE_STATUS if os.path.isdir(os.path.dirname(_PIPELINE_STATUS))
          else os.path.join(HERE, "PipelineCharacters_status.json"))


def _load_skins() -> list[dict[str, Any]]:
    """Skins from ``characters.json`` (the pipeline's installer) plus drop-in ``skins/<id>.json``
    files (one skin each), a later file replacing an earlier id."""
    by_id: dict[str, dict[str, Any]] = {}
    path = os.path.join(HERE, "characters.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for skin in json.load(fh).get("skins", []):
                by_id[skin["id"]] = skin
    skin_dir = os.path.join(HERE, "skins")
    if os.path.isdir(skin_dir):
        for name in sorted(os.listdir(skin_dir)):
            if not name.lower().endswith(".json"):
                continue
            try:
                with open(os.path.join(skin_dir, name), encoding="utf-8") as fh:
                    skin = json.load(fh)
                by_id[skin["id"]] = skin
            except Exception as ex:  # noqa: BLE001 - one bad drop-in must not break the rest
                unrealsdk.logging.warning(f"[{MOD_NAME}] skipped skins/{name}: {ex!r}")
    return list(by_id.values())


SKINS = _load_skins()
wiring = runtime.configure(SKINS, log_name=MOD_NAME, status_path=STATUS, test_keys=True)

build_mod(
    name=MOD_NAME,
    author=__author__,
    version=__version__,
    description="Character model swaps: pick a replacement body/arms per vault hunter.",
    hooks=wiring["hooks"],
    keybinds=wiring["keybinds"],
    options=wiring["options"],
    commands=wiring["commands"],
)
for _h in wiring["hooks"]:
    _h.enable()
