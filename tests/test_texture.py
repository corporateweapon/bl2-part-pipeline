"""Tests for ``bl2_upk.texture``: the Texture2D codec and the loose texture package.

* every ``Texture2D`` in the decompressed Startup.upk re-serializes byte for byte
  (skipped when ``scratch/decomp/Startup.upk`` is absent)
* a package built from Pillow images re-parses with inline mips at the right absolute
  offsets, the right mip tail (4x4 minimum for DXT), and the expected properties
* the glb CLI's helpers: green-flip for normal maps, flat textures

Run with ``python -m pytest tests/test_texture.py -q``.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

Image = pytest.importorskip("PIL.Image", reason="Pillow is required")

from bl2_upk.reader import Package  # noqa: E402
from bl2_upk.texture import (  # noqa: E402
    HASH_A, HASH_B, TextureSpec, build_texture_package, dxt_mip_chain, parse_texture2d,
    serialize_texture2d,
)
from bl2_upk.texture_package import flip_green  # noqa: E402

STARTUP = REPO / "scratch" / "decomp" / "Startup.upk"


@pytest.fixture(scope="module")
def startup() -> Package:
    if not STARTUP.exists():
        pytest.skip("scratch/decomp/Startup.upk absent (run `python bl2.py doctor --fix`)")
    return Package.from_file(STARTUP)


def test_every_startup_texture_round_trips(startup: Package) -> None:
    exports = startup.exports_of_class("Texture2D")
    assert len(exports) > 500
    inline = streamed = 0
    for export in exports:
        tex = parse_texture2d(startup, export)
        assert serialize_texture2d(tex, startup, export.off) == startup.read_export_bytes(export), export.path
        assert tex.hash_b == HASH_B and tex.trailer == 0
        if all(m.inline for m in tex.mips):
            inline += 1
        else:
            streamed += 1
    assert inline > 200 and streamed > 300


def _sample(size: int = 64):
    from PIL import ImageDraw
    im = Image.new("RGBA", (size, size), (40, 40, 40, 255))
    d = ImageDraw.Draw(im)
    d.rectangle((size // 8, size // 8, size * 7 // 8, size * 7 // 8), fill=(220, 60, 30, 255))
    d.ellipse((size // 3, size // 3, size * 2 // 3, size * 2 // 3), fill=(30, 200, 90, 128))
    return im


def test_dxt_mip_chain_ends_in_4x4_blocks() -> None:
    levels = dxt_mip_chain(_sample(64).convert("RGB"), "PF_DXT1")
    assert [(w, h) for w, h, _ in levels] == [(64, 64), (32, 32), (16, 16), (8, 8), (4, 4), (4, 4), (4, 4)]
    assert [len(b) for _, _, b in levels] == [2048, 512, 128, 32, 8, 8, 8]
    levels = dxt_mip_chain(_sample(16), "PF_DXT5")
    assert [len(b) for _, _, b in levels] == [256, 64, 16, 16, 16]
    levels = dxt_mip_chain(_sample(8), "PF_A8R8G8B8")
    assert [(w, h, len(b)) for w, h, b in levels] == [(8, 8, 256), (4, 4, 64), (2, 2, 16), (1, 1, 4)]
    with pytest.raises(Exception):
        dxt_mip_chain(Image.new("RGB", (48, 48)), "PF_DXT1")


def test_texture_package_round_trips(tmp_path: Path) -> None:
    out = tmp_path / "PipelineTexturesTest.upk"
    sidecar = build_texture_package(out, "PipelineTexturesTest", [
        TextureSpec("Test_DXT1", _sample().convert("RGB"), "PF_DXT1", source_file_path="a.png"),
        TextureSpec("Test_Normal", _sample(), "PF_DXT5", srgb=False,
                    compression_settings="TC_Normalmap", address_x="TA_Clamp"),
        TextureSpec("Test_Raw", _sample(8), "PF_A8R8G8B8"),
    ])
    assert [t["path"] for t in sidecar["textures"]] == [
        "PipelineTexturesTest.Test_DXT1", "PipelineTexturesTest.Test_Normal",
        "PipelineTexturesTest.Test_Raw"]
    pkg = Package.from_file(out)
    assert [e.clsname for e in pkg.exports] == ["Texture2D"] * 3
    assert [(i.class_name, i.name) for i in pkg.imports] == [("Package", "Engine"), ("Class", "Texture2D")]
    by_name = {e.name: e for e in pkg.exports}

    tex = parse_texture2d(pkg, by_name["Test_DXT1"])
    assert tex.size == (64, 64) and tex.format == "PF_DXT1" and len(tex.mips) == 7
    assert tex.hash_a == HASH_A and tex.hash_b == HASH_B and tex.trailer == 0
    assert all(m.inline and m.data is not None and m.size_on_disk == len(m.data) for m in tex.mips)
    assert tex.mips[0].size_on_disk == 2048 and tex.mips[-1].size_x == 4
    # inline offsets are absolute file positions: the data really is there
    data = out.read_bytes()
    assert data[tex.mips[0].offset:tex.mips[0].offset + 8] == tex.mips[0].data[:8]
    assert serialize_texture2d(tex, pkg, by_name["Test_DXT1"].off) == pkg.read_export_bytes(by_name["Test_DXT1"])
    props = {t.name.text: t for t in tex.tags}
    assert props["MipTailBaseIdx"].value == 6 and props["NeverStream"].value is True
    assert str(props["LODGroup"].value) == "TEXTUREGROUP_Weapon"
    assert "SRGB" not in props and props["SourceFilePath"].value == "a.png"
    assert struct.unpack_from("<i", pkg.read_export_bytes(by_name["Test_DXT1"]), 0)[0] == -1

    nrm = parse_texture2d(pkg, by_name["Test_Normal"])
    props = {t.name.text: t for t in nrm.tags}
    assert props["SRGB"].value is False
    assert str(props["CompressionSettings"].value) == "TC_Normalmap"
    assert str(props["AddressX"].value) == "TA_Clamp" and "AddressY" not in props
    raw = parse_texture2d(pkg, by_name["Test_Raw"])
    assert raw.format == "PF_A8R8G8B8" and [m.size_x for m in raw.mips] == [8, 4, 2, 1]
    # BGRA: the (220, 60, 30) rectangle pixel at (1, 1) comes out B, G, R, A
    assert raw.mips[0].data[(1 * 8 + 1) * 4:(1 * 8 + 1) * 4 + 4] == bytes((30, 60, 220, 255))


def test_flip_green_inverts_only_green() -> None:
    im = Image.new("RGBA", (2, 2), (10, 20, 30, 40))
    assert flip_green(im).getpixel((0, 0)) == (10, 235, 30, 40)
