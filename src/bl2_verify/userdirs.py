"""Where Borderlands 2 keeps the current user's config, saves and logs.

Nothing in the pipeline hardcodes a user name, a Steam id or a cloud-sync folder. The
locations are found at import time and every one of them can be overridden:

``BL2_WILLOW_DIR``  the ``...\\My Games\\Borderlands 2\\WillowGame`` folder (Config, SaveData, Logs)
``BL2_SAVE``        the ``.sav`` the verify loop plays and guards

Without the overrides, the WillowGame folder is looked for under ``Documents`` in the user
profile and under OneDrive (the game follows a redirected Documents folder), and the save is
the most recently written ``.sav`` in the first Steam-id folder of ``SaveData``: that is the
character the game's CONTINUE button loads.
"""
from __future__ import annotations

import os
from pathlib import Path

_WILLOW = Path("My Games") / "Borderlands 2" / "WillowGame"


def willow_dir() -> Path:
    """The user's ``WillowGame`` folder (Config, SaveData, Logs); may not exist yet."""
    override = os.environ.get("BL2_WILLOW_DIR")
    if override:
        return Path(override)
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    candidates = [home / "Documents" / _WILLOW]
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        base = os.environ.get(var)
        if base:
            candidates.append(Path(base) / "Documents" / _WILLOW)
    candidates.append(home / "OneDrive" / "Documents" / _WILLOW)
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


def config_dir() -> Path:
    return willow_dir() / "Config"


def launch_log() -> Path:
    return willow_dir() / "Logs" / "Launch.log"


def default_save() -> Path:
    """The save the loop plays: ``BL2_SAVE``, else the newest ``.sav`` of the first Steam id."""
    override = os.environ.get("BL2_SAVE")
    if override:
        return Path(override)
    save_data = willow_dir() / "SaveData"
    if save_data.is_dir():
        for steam_dir in sorted(p for p in save_data.iterdir() if p.is_dir() and p.name.isdigit()):
            saves = sorted(steam_dir.glob("Save*.sav"), key=lambda p: p.stat().st_mtime, reverse=True)
            if saves:
                return saves[0]
    # a plausible placeholder so callers can still derive sibling folders from it
    return save_data / "steamid" / "Save0001.sav"
