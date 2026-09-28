"""Back up and restore the character save the verify loop plays with.

The loop grants test weapons and then saves the game (``save_and_quit`` is the only way to get a
clean reload, and the reload is one of the four conditions the brief demands). That would leave
the user's character carrying pipeline test gear, so every run copies **every** save slot in the
save folder (``Save*.sav`` and their ``.bak``) to ``backup/saves/<timestamp>/`` *before* launching
and copies them back afterwards -- unless the caller asks to keep the modified save
(``--keep-save`` in ``run_loop``). Every slot, not just ``SAVE``: the loop presses CONTINUE,
which loads whichever character was played LAST, so the slot it plays is the player's choice,
not ours (F38: a run played and saved the user's Gaige in Save0001 while only Save0005 was
guarded).

Nothing here ever deletes a save: restore overwrites the live file from a backup, and the backup
directories accumulate. ``prune`` exists but only ever removes *backup* copies, never the live
save, and only when asked.

CLI::

    python -m bl2_verify.save_guard --backup            # snapshot now, print the directory
    python -m bl2_verify.save_guard --list
    python -m bl2_verify.save_guard --restore <dir>     # or --restore latest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from bl2_verify import userdirs

REPO = Path(__file__).resolve().parents[2]
SAVE = userdirs.default_save()   # BL2_SAVE / BL2_WILLOW_DIR override the discovery
BACKUP_ROOT = REPO / "backup" / "saves"
MANIFEST = "manifest.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class SaveGuard:
    """Snapshot / restore every save slot beside ``save`` (each ``.sav`` and its ``.bak``)."""

    def __init__(self, save: Path = SAVE, backup_root: Path = BACKUP_ROOT,
                 log: Any = None) -> None:
        self.save = Path(save)
        self.backup_root = Path(backup_root)
        self.backup_dir: Path | None = None
        self._log = log or (lambda msg: print(msg, flush=True))

    # ------------------------------------------------------------------ helpers
    def members(self) -> list[Path]:
        """Every save slot in the save folder: ``Save*.sav`` and ``Save*.sav.bak`` (F38)."""
        folder = self.save.parent
        out = sorted(p for p in folder.glob("Save*.sav*")
                     if p.is_file() and (p.suffix == ".sav" or p.name.endswith(".sav.bak")))
        return out or [p for p in (self.save,) if p.exists()]

    def state(self) -> dict[str, dict[str, Any]]:
        return {p.name: {"size": p.stat().st_size, "mtime": p.stat().st_mtime,
                         "sha256": sha256(p)} for p in self.members()}

    # ------------------------------------------------------------------ backup
    def backup(self, tag: str = "") -> Path:
        if not self.save.exists():
            raise FileNotFoundError(f"save not found: {self.save}")
        stamp = time.strftime("%Y%m%d-%H%M%S") + (f"-{tag}" if tag else "")
        dest = self.backup_root / stamp
        dest.mkdir(parents=True, exist_ok=True)
        files = {}
        for p in self.members():
            shutil.copy2(p, dest / p.name)
            files[p.name] = {"size": p.stat().st_size, "sha256": sha256(p)}
        manifest = {"source_dir": str(self.save.parent), "taken": time.time(),
                    "taken_iso": time.strftime("%Y-%m-%d %H:%M:%S"), "tag": tag, "files": files}
        (dest / MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        self.backup_dir = dest
        self._log(f"save backup -> {dest} ({', '.join(files)})")
        return dest

    # ------------------------------------------------------------------ restore
    def restore(self, src: Path | None = None) -> bool:
        """Copy a backup back over the live save. Returns True when anything was restored."""
        src = Path(src) if src is not None else self.backup_dir
        if src is None:
            self._log("save restore skipped: no backup taken in this run")
            return False
        if not src.is_dir():
            raise FileNotFoundError(f"backup directory not found: {src}")
        restored = []
        for p in sorted(src.iterdir()):
            if p.name == MANIFEST or not p.is_file():
                continue
            target = self.save.parent / p.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            restored.append(p.name)
        self._log(f"save restored from {src} ({', '.join(restored) or 'nothing'})")
        return bool(restored)

    def verify_restored(self, src: Path | None = None) -> bool:
        """True when the live save matches the backup byte for byte."""
        src = Path(src) if src is not None else self.backup_dir
        if src is None:
            return False
        man = json.loads((src / MANIFEST).read_text(encoding="utf-8"))
        for name, info in man["files"].items():
            live = self.save.parent / name
            if not live.exists() or sha256(live) != info["sha256"]:
                return False
        return True

    # ------------------------------------------------------------------ listing
    def list_backups(self) -> list[Path]:
        if not self.backup_root.is_dir():
            return []
        return sorted((p for p in self.backup_root.iterdir()
                       if p.is_dir() and (p / MANIFEST).exists()), key=lambda p: p.name)

    def latest(self) -> Path | None:
        backups = self.list_backups()
        return backups[-1] if backups else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bl2_verify.save_guard", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--save", default=str(SAVE))
    p.add_argument("--backup-root", default=str(BACKUP_ROOT))
    p.add_argument("--backup", action="store_true")
    p.add_argument("--restore", metavar="DIR", help="a backup directory, or 'latest'")
    p.add_argument("--list", action="store_true")
    p.add_argument("--tag", default="")
    args = p.parse_args(argv)

    g = SaveGuard(Path(args.save), Path(args.backup_root))
    if args.backup:
        print(g.backup(args.tag))
        return 0
    if args.restore:
        src = g.latest() if args.restore == "latest" else Path(args.restore)
        if src is None:
            print("no backups found", file=sys.stderr)
            return 1
        return 0 if g.restore(src) else 1
    if args.list:
        for b in g.list_backups():
            man = json.loads((b / MANIFEST).read_text(encoding="utf-8"))
            print(f"{b.name}  {man['taken_iso']}  {', '.join(man['files'])}")
        return 0
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
