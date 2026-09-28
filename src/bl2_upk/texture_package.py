"""CLI: build a loose ``Texture2D``-only package from a glTF binary's material textures.

    python -m bl2_upk.texture_package --glb model.glb --material ak47 \\
        --out scratch/PipelineTextures.upk --name PipelineTextures --prefix AK47 \\
        --flat Mask_White=FFFFFF --flat Mask_Black=000000

Writes ``<prefix>_Albedo`` (DXT1, sRGB, from ``baseColorTexture``) and
``<prefix>_Normal`` (DXT5, linear, ``TC_Normalmap``, green channel flipped from the
glTF/OpenGL +Y convention to Unreal's -Y) plus any ``--flat NAME=RRGGBB`` 16x16
constants (handy as mask textures for the Master_Gun material), and a
``<out>.textures.json`` sidecar listing what went in.  The glb itself never enters
the repo; only the package it produces is installed, and that is gitignored too.
"""

from __future__ import annotations

import argparse
import io
import json
import struct
import sys
from pathlib import Path

from .texture import TextureSpec, build_texture_package


def read_glb(path: str | Path) -> tuple[dict, bytes]:
    with open(path, "rb") as handle:
        magic, _version, _length = struct.unpack("<4sII", handle.read(12))
        if magic != b"glTF":
            raise SystemExit(f"{path}: not a glb")
        chunk_len, chunk_type = struct.unpack("<I4s", handle.read(8))
        doc = json.loads(handle.read(chunk_len))
        chunk_len, chunk_type = struct.unpack("<I4s", handle.read(8))
        if chunk_type != b"BIN\0":
            raise SystemExit(f"{path}: second chunk is not BIN")
        blob = handle.read(chunk_len)
    return doc, blob


def glb_image(doc: dict, blob: bytes, texture_index: int):
    """The Pillow image behind ``textures[texture_index]`` (EXT_texture_webp aware)."""
    from PIL import Image

    tex = doc["textures"][texture_index]
    source = tex.get("source")
    if source is None:
        source = tex.get("extensions", {}).get("EXT_texture_webp", {}).get("source")
    if source is None:
        raise SystemExit(f"texture {texture_index} has no image source")
    image = doc["images"][source]
    view = doc["bufferViews"][image["bufferView"]]
    start = view.get("byteOffset", 0)
    data = blob[start:start + view["byteLength"]]
    return Image.open(io.BytesIO(data)), image.get("name", f"image{source}")


def flip_green(image):
    """glTF normal maps are +Y (OpenGL); Unreal's are -Y."""
    from PIL import ImageChops

    rgba = image.convert("RGBA")
    r, g, b, a = rgba.split()
    return __import__("PIL.Image", fromlist=["merge"]).merge("RGBA", (r, ImageChops.invert(g), b, a))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bl2_upk.texture_package", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--glb", default=None, help="glb to read the material's textures from")
    parser.add_argument("--material", default=None, help="glTF material name (with --glb)")
    parser.add_argument("--albedo", default=None,
                        help="PNG to use as the albedo instead of the glb's (a VRF export of the "
                             "CS2 vtex: the glb's embedded webp copy is alpha-damaged, M8)")
    parser.add_argument("--normal", default=None, help="PNG to use as the normal map instead of the glb's")
    parser.add_argument("--ao", default=None,
                        help="PNG ambient-occlusion map multiplied into the albedo (the BL2 master "
                             "material has no AO input)")
    parser.add_argument("--out", required=True, help="package file to write")
    parser.add_argument("--name", required=True, help="package name (= file stem)")
    parser.add_argument("--prefix", default="Tex", help="export name prefix")
    parser.add_argument("--size", type=int, default=0,
                        help="downscale the source images to this square size (0 = as is)")
    parser.add_argument("--normal-size", type=int, default=0,
                        help="downscale the normal map to this square size (default: --size)")
    parser.add_argument("--no-normal", action="store_true", help="skip the normal map")
    parser.add_argument("--keep-normal-green", action="store_true",
                        help="do not flip the normal map's green channel")
    parser.add_argument("--flat", action="append", default=[], metavar="NAME=RRGGBB",
                        help="add a 16x16 constant-colour DXT1 texture")
    args = parser.parse_args(argv)

    from PIL import Image, ImageChops

    if args.glb:
        doc, blob = read_glb(args.glb)
        material = next((m for m in doc.get("materials", []) if m.get("name") == args.material), None)
        if material is None:
            raise SystemExit(f"material {args.material!r} not in {[m.get('name') for m in doc.get('materials', [])]}")
    elif not args.albedo:
        raise SystemExit("give --glb (+ --material) or --albedo")
    else:
        material = {}
    pbr = material.get("pbrMetallicRoughness", {})
    specs: list[TextureSpec] = []
    provenance: dict[str, str] = {}

    if args.albedo:
        albedo, albedo_name = Image.open(args.albedo), f"file:{Path(args.albedo).name}"
    else:
        base = pbr.get("baseColorTexture")
        if base is None:
            raise SystemExit(f"material {args.material!r} has no baseColorTexture")
        albedo, albedo_name = glb_image(doc, blob, base["index"])
        albedo_name = f"glb:{albedo_name}"
    albedo = albedo.convert("RGB")
    if args.ao:
        ao = Image.open(args.ao).convert("L").resize(albedo.size, Image.LANCZOS)
        albedo = ImageChops.multiply(albedo, Image.merge("RGB", (ao, ao, ao)))
        albedo_name += f" x ao:{Path(args.ao).name}"
    if args.size:
        albedo = albedo.resize((args.size, args.size), Image.LANCZOS)
    specs.append(TextureSpec(f"{args.prefix}_Albedo", albedo, "PF_DXT1", source_file_path=albedo_name))
    provenance[f"{args.prefix}_Albedo"] = albedo_name

    normal = material.get("normalTexture")
    if args.normal:
        normal = {"file": args.normal}
    if normal is not None and not args.no_normal:
        if "file" in normal:
            nrm, nrm_name = Image.open(normal["file"]), f"file:{Path(normal['file']).name}"
        else:
            nrm, nrm_name = glb_image(doc, blob, normal["index"])
            nrm_name = f"glb:{nrm_name}"
        normal_size = args.normal_size or args.size
        if normal_size:
            nrm = nrm.resize((normal_size, normal_size), Image.LANCZOS)
        if not args.keep_normal_green:
            nrm = flip_green(nrm)
        specs.append(TextureSpec(f"{args.prefix}_Normal", nrm.convert("RGBA"), "PF_DXT5",
                                 srgb=False, compression_settings="TC_Normalmap",
                                 source_file_path=nrm_name))
        provenance[f"{args.prefix}_Normal"] = nrm_name

    for flat in args.flat:
        name, _, hexcolour = flat.partition("=")
        if len(hexcolour) != 6:
            raise SystemExit(f"--flat {flat!r}: expected NAME=RRGGBB")
        colour = tuple(int(hexcolour[i:i + 2], 16) for i in (0, 2, 4))
        specs.append(TextureSpec(name, Image.new("RGB", (16, 16), colour), "PF_DXT1",
                                 source_file_path=f"flat:{hexcolour}"))
        provenance[name] = f"flat #{hexcolour}"

    sidecar = build_texture_package(args.out, args.name, specs)
    sidecar["provenance"] = provenance
    sidecar["glb"] = str(args.glb) if args.glb else None
    sidecar_path = Path(args.out).with_suffix(".textures.json")
    sidecar_path.write_text(json.dumps(sidecar, indent=1), encoding="utf-8")
    print(f"wrote {args.out} ({Path(args.out).stat().st_size} bytes) + {sidecar_path.name}")
    for row in sidecar["textures"]:
        print(f"  {row['path']}: {row['format']} {row['size'][0]}x{row['size'][1]}, {len(row['mips'])} mips")
    return 0


if __name__ == "__main__":
    sys.exit(main())
