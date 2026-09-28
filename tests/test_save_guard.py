"""Tests for ``bl2_verify.save_guard``.

The verify loop saves the game with test weapons in the character's inventory, so the guard is
the only thing standing between a run and the user's save. Everything runs against temporary
directories -- the real ``SaveData`` folder is never touched and nothing launches the game.

What is pinned here:

* a backup copies ``Save0005.sav`` **and** ``Save0005.sav.bak``, with a manifest of sha256s
* restore puts the exact original bytes back over a modified save
* ``verify_restored`` reports the truth in both directions
* the guard never deletes a save: after backup + restore the live file still exists, and the
  backup directory is still there too

Runnable with pytest or directly::

    python -m pytest tests/test_save_guard.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bl2_verify.save_guard import MANIFEST, SaveGuard, main as guard_main, sha256  # noqa: E402

ORIGINAL = b"SAVE-ORIGINAL-" + bytes(range(256)) * 4
ORIGINAL_BAK = b"SAVE-BAK-" + bytes(range(256)) * 2
MODIFIED = b"SAVE-WITH-TEST-WEAPONS-" + bytes(range(256)) * 4


@pytest.fixture()
def guard(tmp_path: Path) -> SaveGuard:
    save_dir = tmp_path / "SaveData" / "7656119"
    save_dir.mkdir(parents=True)
    (save_dir / "Save0005.sav").write_bytes(ORIGINAL)
    (save_dir / "Save0005.sav.bak").write_bytes(ORIGINAL_BAK)
    (save_dir / "Save0001.sav").write_bytes(b"another character")
    return SaveGuard(save_dir / "Save0005.sav", tmp_path / "backup" / "saves", log=lambda m: None)


def test_backup_copies_save_and_bak(guard: SaveGuard) -> None:
    dest = guard.backup(tag="run1")
    assert dest.is_dir() and dest.name.endswith("-run1")
    assert (dest / "Save0005.sav").read_bytes() == ORIGINAL
    assert (dest / "Save0005.sav.bak").read_bytes() == ORIGINAL_BAK
    # every slot: CONTINUE loads the last-played character, whichever slot that is (F38)
    assert (dest / "Save0001.sav").exists(), "the other character's save is snapshotted too"
    man = json.loads((dest / MANIFEST).read_text(encoding="utf-8"))
    assert {"Save0005.sav", "Save0005.sav.bak", "Save0001.sav"} <= set(man["files"])
    assert man["files"]["Save0005.sav"]["sha256"] == sha256(guard.save)
    assert man["tag"] == "run1"


def test_restore_puts_the_original_bytes_back(guard: SaveGuard) -> None:
    dest = guard.backup()
    guard.save.write_bytes(MODIFIED)  # the game saved our test weapons
    guard.save.with_suffix(".sav.bak").write_bytes(MODIFIED)
    assert not guard.verify_restored(dest)
    assert guard.restore(dest) is True
    assert guard.save.read_bytes() == ORIGINAL
    assert guard.save.with_suffix(".sav.bak").read_bytes() == ORIGINAL_BAK
    assert guard.verify_restored(dest) is True


def test_restore_uses_the_run_backup_by_default(guard: SaveGuard) -> None:
    guard.backup()
    guard.save.write_bytes(MODIFIED)
    assert guard.restore() is True
    assert guard.save.read_bytes() == ORIGINAL


def test_restore_without_a_backup_is_a_no_op(guard: SaveGuard) -> None:
    guard.save.write_bytes(MODIFIED)
    assert guard.restore() is False
    assert guard.save.read_bytes() == MODIFIED, "nothing to restore from: leave the file alone"
    assert guard.verify_restored() is False


def test_backup_without_a_save_raises(tmp_path: Path) -> None:
    g = SaveGuard(tmp_path / "missing" / "Save0005.sav", tmp_path / "backup", log=lambda m: None)
    with pytest.raises(FileNotFoundError):
        g.backup()


def test_restore_from_a_missing_directory_raises(guard: SaveGuard, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        guard.restore(tmp_path / "nope")


def test_nothing_is_ever_deleted(guard: SaveGuard) -> None:
    dest = guard.backup()
    guard.save.write_bytes(MODIFIED)
    guard.restore(dest)
    assert guard.save.exists() and guard.save.with_suffix(".sav.bak").exists()
    assert (guard.save.parent / "Save0001.sav").exists()
    assert dest.is_dir() and (dest / "Save0005.sav").exists()


def test_backup_without_a_bak_sibling(tmp_path: Path) -> None:
    save_dir = tmp_path / "sd"
    save_dir.mkdir()
    (save_dir / "Save0005.sav").write_bytes(ORIGINAL)
    g = SaveGuard(save_dir / "Save0005.sav", tmp_path / "b", log=lambda m: None)
    dest = g.backup()
    assert [p.name for p in sorted(dest.iterdir())] == [MANIFEST, "Save0005.sav"]
    (save_dir / "Save0005.sav").write_bytes(MODIFIED)
    assert g.restore(dest) is True
    assert (save_dir / "Save0005.sav").read_bytes() == ORIGINAL


def test_list_and_latest(guard: SaveGuard) -> None:
    assert guard.list_backups() == []
    first = guard.backup(tag="a")
    second = guard.backup(tag="b")
    names = [p.name for p in guard.list_backups()]
    assert names == sorted(names)
    assert {first.name, second.name} <= set(names)
    assert guard.latest() is not None


def test_cli_backup_list_restore(guard: SaveGuard, capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["--save", str(guard.save), "--backup-root", str(guard.backup_root)]
    assert guard_main([*argv, "--backup", "--tag", "cli"]) == 0
    printed = Path(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed.is_dir()
    assert guard_main([*argv, "--list"]) == 0
    assert "cli" in capsys.readouterr().out
    guard.save.write_bytes(MODIFIED)
    assert guard_main([*argv, "--restore", "latest"]) == 0
    assert guard.save.read_bytes() == ORIGINAL


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_a_run_on_another_slot_is_restored_too(guard: SaveGuard) -> None:
    """F38: the run played Save0001 (the character last played), not the guarded Save0005."""
    other = guard.save.parent / "Save0001.sav"
    before = other.read_bytes()
    dest = guard.backup()
    other.write_bytes(MODIFIED)
    assert not guard.verify_restored(dest)
    guard.restore(dest)
    assert other.read_bytes() == before and guard.verify_restored(dest)
