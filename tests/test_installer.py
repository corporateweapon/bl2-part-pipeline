"""The double-click installer (armory/installer) that every Armory and pack zip carries, on Windows.

Runs the real Install.bat / Uninstall.bat (PowerShell 5.1 behind them) against a FAKE game
folder -- never the real one -- with spaces in every path. What it pins:

* the zips carry Install.bat, Uninstall.bat, _installer/armory_install.ps1, HOW_TO_INSTALL.txt,
  and those stay out of the game folder
* installing the Armory then a pack writes exactly the files the pipeline's own install_tree
  writes, byte for byte, and switches the Armory on in the Mods menu (without overriding a choice)
* re-running is harmless; uninstalling a pack removes only that pack; uninstalling the Armory
  removes sdk_mods/Armory (logs too) and leaves other packs and the game alone
* refusals: no mod manager in the game folder; a package that would write a base-game file
* Steam detection finds the real Borderlands 2 on this machine (read only)

    python -m pytest tests/test_installer.py -q
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests" / "fakes"))

from bl2_partgen.pack import build_armory_release, build_pack, install_tree  # noqa: E402
from tests.test_armory_packs import RUNTIME, _source_tree  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32" or shutil.which("powershell") is None,
                                reason="the installer is Windows PowerShell")


@pytest.fixture(scope="module")
def catalog() -> dict[str, Any]:
    from bl2_catalog import load_catalog

    path = REPO / "catalog" / "parts.json"
    if not path.exists():
        pytest.skip("catalog/parts.json absent")
    return load_catalog(path)


def _fake_game(root: Path) -> Path:
    game = root / "Steam Library" / "Borderlands 2"
    (game / "Binaries" / "Win32").mkdir(parents=True)
    (game / "Binaries" / "Win32" / "Borderlands2.exe").write_bytes(b"MZ fake")
    (game / "sdk_mods").mkdir()
    (game / "sdk_mods" / "mods_base.sdkmod").write_bytes(b"sdkmod")
    cooked = game / "WillowGame" / "CookedPCConsole"
    cooked.mkdir(parents=True)
    (cooked / "Startup.upk").write_bytes(b"base game")
    return game


def _run(bat: Path, game: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    # as Explorer runs a double-clicked .bat: cmd /s /c ""<bat>" <args>" (spaces in both paths)
    line = f'cmd /s /c ""{bat}" -GameDir "{game}" -NoPause {" ".join(extra)}"'
    return subprocess.run(line, capture_output=True, text=True, timeout=120)


def _files(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


@pytest.fixture()
def zips(tmp_path: Path, catalog: dict[str, Any]) -> dict[str, Path]:
    """The Armory release (no bundled pack) and pack GUNA, 'extracted' into download folders."""
    src = tmp_path / "src"
    _source_tree(src)
    pack = build_pack(src / "packs" / "guna.json", tmp_path / "dist" / "packs", catalog=catalog)
    release, _zip = build_armory_release(tmp_path / "dist", runtime_dir=RUNTIME, make_zip=False)
    downloads = tmp_path / "My Downloads"
    out = {"armory": downloads / "Armory 1.1.0", "guna": downloads / "ArmoryPack guna"}
    shutil.copytree(release, out["armory"])
    shutil.copytree(pack.stage, out["guna"])
    return out


def test_zips_carry_the_installer(zips: dict[str, Path]) -> None:
    for folder in zips.values():
        for name in ("Install.bat", "Uninstall.bat", "_installer/armory_install.ps1", "HOW_TO_INSTALL.txt"):
            assert (folder / name).exists(), (folder, name)
    how = (zips["guna"] / "HOW_TO_INSTALL.txt").read_text(encoding="ascii")
    assert "Install.bat" in how and "drag the \"sdk_mods\" and \"WillowGame\" folders" in how
    assert "WillowGame\\CookedPCConsole\\PipelineMeshesGUNA.upk" in how


def test_install_matches_the_pipeline_install_then_uninstall(tmp_path: Path, zips: dict[str, Path]) -> None:
    game = _fake_game(tmp_path)
    expected_root = _fake_game(tmp_path / "expected")
    for folder in (zips["armory"], zips["guna"]):
        install_tree(folder, expected_root)
    for folder in (zips["armory"], zips["guna"]):
        result = _run(folder / "Install.bat", game)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "each verified" in result.stdout
    got, want = _files(game), _files(expected_root)
    settings = "sdk_mods\\settings\\Armory.json"
    assert '"enabled": true' in got.pop(settings).decode()
    assert got == want, "the installer writes exactly what install_tree writes"
    assert not (game / "Install.bat").exists() and not (game / "_installer").exists()

    # again: harmless; a choice already in settings is kept
    (game / "sdk_mods" / "settings" / "Armory.json").write_text('{"enabled": false}', encoding="utf-8")
    assert _run(zips["armory"] / "Install.bat", game).returncode == 0
    assert "false" in (game / "sdk_mods" / "settings" / "Armory.json").read_text(encoding="utf-8")

    # uninstall the pack: only its files go
    (game / "sdk_mods" / "Armory" / "logs").mkdir(exist_ok=True)
    (game / "sdk_mods" / "Armory" / "logs" / "armory.status.json").write_text("[]", encoding="utf-8")
    result = _run(zips["guna"] / "Uninstall.bat", game, "-Yes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (game / "sdk_mods" / "ArmoryPacks" / "guna").exists()
    assert not (game / "WillowGame" / "CookedPCConsole" / "PipelineMeshesGUNA.upk").exists()
    assert (game / "sdk_mods" / "Armory" / "__init__.py").exists()

    # uninstall the Armory: its folder (logs too) goes, the game's files stay
    assert _run(zips["armory"] / "Uninstall.bat", game, "-Yes").returncode == 0
    assert not (game / "sdk_mods" / "Armory").exists()
    assert (game / "WillowGame" / "CookedPCConsole" / "Startup.upk").read_bytes() == b"base game"
    assert (game / "sdk_mods" / "mods_base.sdkmod").exists()


def test_refuses_without_the_mod_manager(tmp_path: Path, zips: dict[str, Path]) -> None:
    game = _fake_game(tmp_path)
    (game / "sdk_mods" / "mods_base.sdkmod").unlink()
    result = _run(zips["armory"] / "Install.bat", game)
    assert result.returncode == 1 and "willow2-mod-manager" in result.stdout
    assert not (game / "sdk_mods" / "Armory").exists()


def test_refuses_a_package_that_would_touch_a_base_game_file(tmp_path: Path, zips: dict[str, Path]) -> None:
    game = _fake_game(tmp_path)
    evil = zips["guna"]
    (evil / "WillowGame" / "CookedPCConsole" / "Startup.upk").write_bytes(b"replacement")
    result = _run(evil / "Install.bat", game)
    assert result.returncode == 1 and "refusing to touch" in result.stdout
    assert (game / "WillowGame" / "CookedPCConsole" / "Startup.upk").read_bytes() == b"base game"
    assert not (game / "sdk_mods" / "ArmoryPacks").exists(), "nothing written before the refusal"


def test_steam_detection_finds_this_machines_game() -> None:
    real = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Borderlands 2")
    if not (real / "Binaries" / "Win32" / "Borderlands2.exe").exists():
        pytest.skip("no Borderlands 2 on this machine")
    script = REPO / "armory" / "installer" / "armory_install.ps1"
    result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                             "-DetectOnly", "-NoPause", "-Source", str(REPO / "armory")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(result.stdout.strip().splitlines()[-1]) == real
