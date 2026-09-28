"""Texture atlases for a skin: albedo (x AO) and normal (green flipped), one Texture2D pair per mesh."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageFilter


def cel_shade(col: Image.Image, ao: Image.Image | None, style: dict) -> Image.Image:
    """Bake a toon look into an albedo tile: smooth the colour, quantise its luminance into a few
    bands (hue kept), and multiply a two- or three-band AO instead of the smooth bake."""
    levels = int(style.get("levels", 6))
    sat = float(style.get("saturation", 1.15))
    smooth = int(style.get("smooth", 2))
    c = col.convert("RGB")
    if smooth:
        c = c.filter(ImageFilter.GaussianBlur(smooth))
    a = np.asarray(c).astype(float) / 255.0
    lum = a @ np.array([0.299, 0.587, 0.114])
    q = np.floor(lum * levels) / levels + 0.5 / levels
    scale = np.where(lum > 1e-4, q / np.maximum(lum, 1e-4), 1.0)[..., None]
    mix = float(style.get("mix", 1.0))                 # 1 = full bands, 0 = original shading
    orig = np.asarray(col.convert("RGB")).astype(float) / 255.0
    a = np.clip(orig * (1 - mix) + a * scale * mix, 0, 1)
    grey = (a @ np.array([0.299, 0.587, 0.114]))[..., None]
    a = np.clip(grey + (a - grey) * sat, 0, 1)
    out = Image.fromarray((a * 255).astype(np.uint8))
    if ao is not None:
        bands = int(style.get("ao_bands", 2))
        ao_a = np.asarray(ao.convert("L")).astype(float) / 255.0
        lo = float(style.get("ao_floor", 0.7))
        ao_q = lo + (1 - lo) * np.floor(ao_a * bands + 0.999) / bands
        ao_img = Image.fromarray((np.clip(ao_q, 0, 1) * 255).astype(np.uint8))
        out = ImageChops.multiply(out, Image.merge("RGB", (ao_img, ao_img, ao_img)))
    return out

from bl2_upk.texture import TextureSpec
from bl2_upk.texture_package import flip_green


def build_atlas(atlas: dict, root: Path, name: str, preview_dir: Path | None = None, log=print) -> list[TextureSpec]:
    """``atlas``: {"size": N, "tiles": {tile: [x, y, w, h]}, "sources": {tile: {"color", "ao", "normal"} |
    {"solid": [r, g, b]}}}.  Paths are relative to ``root``.  Returns the two TextureSpecs."""
    size = int(atlas["size"])
    nsize = int(atlas.get("normal_size", size))     # normal atlas may be smaller (same layout)
    nf = nsize / size
    albedo = Image.new("RGB", (size, size), (0, 0, 0))
    normal = Image.new("RGB", (nsize, nsize), (128, 128, 255))
    for tile, (x, y, w, h) in atlas["tiles"].items():
        src = atlas["sources"].get(tile)
        if src is None:
            raise SystemExit(f"{name}: tile {tile!r} has no source")
        if "solid" in src:
            albedo.paste(Image.new("RGB", (w, h), tuple(src["solid"])), (x, y))
            continue
        col = Image.open(root / src["color"]).convert("RGB").resize((w, h), Image.LANCZOS)
        style = atlas.get("style")
        if src.get("style") is False:                   # per-tile opt-out (hair, skin, eyes)
            style = None
        elif isinstance(src.get("style"), dict):
            style = {**(style or {}), **src["style"]}
        if style:
            ao_img = Image.open(root / src["ao"]).convert("L").resize((w, h), Image.LANCZOS) if src.get("ao") else None
            albedo.paste(cel_shade(col, ao_img, style), (x, y))
        elif src.get("ao"):
            # half-strength AO: BL2's toon shading on top of a full bake reads as flat black
            ao = Image.open(root / src["ao"]).convert("L").resize((w, h), Image.LANCZOS)
            ao = ao.point(lambda v: 128 + v // 2)
            col = ImageChops.multiply(col, Image.merge("RGB", (ao, ao, ao)))
            albedo.paste(col, (x, y))
        else:
            albedo.paste(col, (x, y))
        if src.get("normal"):
            nw, nh = max(1, round(w * nf)), max(1, round(h * nf))
            normal.paste(Image.open(root / src["normal"]).convert("RGB").resize((nw, nh), Image.LANCZOS),
                         (round(x * nf), round(y * nf)))
    normal = flip_green(normal)
    if preview_dir is not None:
        preview_dir.mkdir(parents=True, exist_ok=True)
        albedo.save(preview_dir / f"{name}_Albedo.png")
        normal.save(preview_dir / f"{name}_Normal.png")
    log(f"[{name}] atlas {size}x{size} (normal {nsize}x{nsize}): {len(atlas['tiles'])} tiles")
    return [
        TextureSpec(f"{name}_Albedo", albedo, "PF_DXT1", lod_group="TEXTUREGROUP_Character",
                    source_file_path=f"{name} atlas albedo x ao"),
        TextureSpec(f"{name}_Normal", normal.convert("RGBA"), "PF_DXT5", lod_group="TEXTUREGROUP_CharacterNormalMap",
                    srgb=False, compression_settings="TC_Normalmap", source_file_path=f"{name} atlas normal"),
    ]
