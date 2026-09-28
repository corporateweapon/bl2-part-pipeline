"""python -m bl2_charswap build|install <spec.json> [--game <dir>] [--out <dir>]

build: meshes + texture atlas -> scratch/charswap/<id>/ (skin.json beside the packages).
install: the dev host -- packages into CookedPCConsole, skin into sdk_mods/PipelineCharacters
(with runtime.py, the swap logic the Armory also carries). To share a skin, build an Armory
character pack instead: packs/<id>.json with "characters", then `bl2 pack <id>`.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from bl2_upk.texture import build_texture_package

from .atlas import build_atlas
from .retarget import build_mesh

REPO = Path(__file__).resolve().parents[2]
GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Borderlands 2")
MOD_NAME = "PipelineCharacters"
MOD_TEMPLATE = Path(__file__).resolve().parent / "mod_template"


def _abs(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else REPO / p


def load_spec(path: Path) -> dict:
    spec = json.loads(path.read_text(encoding="utf-8"))
    spec["_path"] = str(path)
    return spec


def out_dir(spec: dict, override: str | None) -> Path:
    return Path(override) if override else REPO / "scratch" / "charswap" / spec["id"]


def build(spec: dict, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    assets = _abs(spec["assets_root"])
    glb = assets / spec["glb"]
    texture_specs = []
    swaps: dict[str, dict] = {}
    packages = [spec["texture_package"]]
    meshes = []
    for m in spec["meshes"]:
        by_name = {mm["name"]: mm for mm in spec["meshes"]}
        tex_owner = m.get("textures_from", m["name"])       # share another mesh's atlas + textures
        atlas = by_name[tex_owner]["atlas"]
        result = build_mesh(
            glb, _abs(spec["source_package"]), m["source_mesh"], out / f"{m['package']}.upk",
            m["package"], m["name"], m["prim_tiles"], atlas["tiles"], int(atlas["size"]),
            float(spec["scale"]), keep_bones=m.get("keep_bones"),
            joint_offsets=m.get("joint_offsets"), subtree_scale=m.get("subtree_scale"),
        )
        meshes.append(result)
        packages.append(m["package"])
        if tex_owner == m["name"]:
            texture_specs += build_atlas(atlas, assets, m["name"], preview_dir=out / "atlas")
        swaps[m["source_mesh"]] = {
            "mesh": result["mesh"],
            "material": {
                "name": f"Mati_{m['name']}",
                "outer": spec["material"]["outer"],
                "parent": spec["material"]["parent"],
                "textures": {
                    "p_Diffuse": f"{spec['texture_package']}.{tex_owner}_Albedo",
                    "p_Normal": f"{spec['texture_package']}.{tex_owner}_Normal",
                },
            },
        }
    tex_out = out / f"{spec['texture_package']}.upk"
    side = build_texture_package(tex_out, spec["texture_package"], texture_specs)
    print(f"[{spec['id']}] wrote {tex_out} ({tex_out.stat().st_size} bytes): "
          + ", ".join(f"{t['name']} {t['size'][0]}x{t['size'][1]}" for t in side["textures"]))
    manifest = {
        "id": spec["id"], "label": spec["label"], "character": spec["character"],
        "description": spec.get("description", ""),
        "packages": packages, "swaps": swaps,
        "hide_mesh_prefixes": spec.get("hide_mesh_prefixes", []),
        "meshes": meshes,
    }
    (out / "skin.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"[{spec['id']}] manifest {out / 'skin.json'}")
    return manifest


def install(spec: dict, out: Path, game: Path) -> None:
    manifest_path = out / "skin.json"
    if not manifest_path.exists():
        raise SystemExit(f"{manifest_path} missing: run build first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cooked = game / "WillowGame" / "CookedPCConsole"
    for pkg in manifest["packages"]:
        src = out / f"{pkg}.upk"
        shutil.copy2(src, cooked / f"{pkg}.upk")
        stale = cooked / f"{pkg}.upk.uncompressed_size"
        if stale.exists():
            stale.unlink()
        print(f"installed {cooked / (pkg + '.upk')} ({src.stat().st_size} bytes)")
    mod_dir = game / "sdk_mods" / MOD_NAME
    mod_dir.mkdir(parents=True, exist_ok=True)
    for f in ("__init__.py", "pyproject.toml"):
        shutil.copy2(MOD_TEMPLATE / f, mod_dir / f)
    shutil.copy2(Path(__file__).resolve().parent / "runtime.py", mod_dir / "runtime.py")
    chars_path = mod_dir / "characters.json"
    chars = {"skins": []}
    if chars_path.exists():
        try:
            chars = json.loads(chars_path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    chars["skins"] = [s for s in chars.get("skins", []) if s.get("id") != manifest["id"]] + [
        {k: manifest[k] for k in ("id", "label", "character", "description", "packages", "swaps", "hide_mesh_prefixes")}
    ]
    chars_path.write_text(json.dumps(chars, indent=1), encoding="utf-8")
    settings = game / "sdk_mods" / "settings" / f"{MOD_NAME}.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    # a freshly installed skin is selected for its character (mods_base option layout:
    # options -> nested "Characters" -> <character>: <label>); the menu can change it later
    data["enabled"] = True
    data.setdefault("options", {}).setdefault("Characters", {})[manifest["character"]] = manifest["label"]
    settings.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8", newline="\n")
    print(f"installed {mod_dir} with {len(chars['skins'])} skin(s); settings {settings}: "
          f"{manifest['character']} -> {manifest['label']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bl2_charswap", description=__doc__)
    ap.add_argument("action", choices=("build", "install"))
    ap.add_argument("spec")
    ap.add_argument("--game", default=str(GAME))
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    spec = load_spec(_abs(args.spec))
    out = out_dir(spec, args.out)
    if args.action == "build":
        build(spec, out)
    else:
        install(spec, out, Path(args.game))
    return 0


if __name__ == "__main__":
    sys.exit(main())
