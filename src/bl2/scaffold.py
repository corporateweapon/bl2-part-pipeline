"""``bl2 new``: start a weapon from the closest worked example, renamed.

The recipe and the specs of the source weapon are copied with every occurrence of the
source's naming tokens replaced by the new weapon's, and the numbers that belong to the old
mesh removed (socket and bounds overrides, which ``bl2 build`` fills back in from the new
retarget). What is left to edit by hand is listed in the output: the glb, the cut rules, the
bone map, the stats, the title and red text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

#: per source weapon: the tokens its files use, in replacement order (longest first)
TOKENS: dict[str, list[str]] = {
    "awp": ["AWP"],
    "ak47": ["AK47", "AK_", "ak47"],
    # the Armory's default pack and the template for a new pack: "Boxgun" is in its package
    # name (PipelineMeshesBoxgun), which a scaffold must not keep or the two packs collide
    "boxgun": ["BOXGUN", "Boxgun", "boxgun"],
}


def rename_value(value: str, source: str, new_id: str, prefix: str) -> str:
    out = value
    for tok in TOKENS.get(source, [source.upper()]):
        if tok == "AK_":
            out = out.replace("AK_", f"{prefix}_")
        elif tok.islower():
            out = out.replace(tok, new_id)
        else:
            # a token starts a word or a CamelCase hump (not after another capital) and does
            # not continue into lower case: PipelineMeshesAWP, AWP_Barrel, Sniper_Jakobs_5_AWP
            out = re.sub(rf"(?<![A-Z]){re.escape(tok)}(?![a-z])", prefix, out)
    return out


def rename_json(data: Any, source: str, new_id: str, prefix: str) -> Any:
    if isinstance(data, dict):
        return {rename_value(k, source, new_id, prefix): rename_json(v, source, new_id, prefix) for k, v in data.items()}
    if isinstance(data, list):
        return [rename_json(v, source, new_id, prefix) for v in data]
    if isinstance(data, str):
        return rename_value(data, source, new_id, prefix)
    return data


def strip_mesh_numbers(spec: dict[str, Any]) -> list[str]:
    """Remove the socket/bounds overrides that belong to the old mesh. The index ranges stay:
    the spec loader requires them, and the sidecar of the new build overrides them anyway."""
    removed: list[str] = []
    for f in spec.get("fragments") or []:
        for key in ("socket_overrides", "bounds_override"):
            if key in f:
                f.pop(key)
                removed.append(f"{f.get('name')}.{key}")
    return removed


def scaffold(root: Path, new_id: str, source: str, glb: str | None, label: str | None,
             prefix: str | None = None, force: bool = False) -> dict[str, Any]:
    prefix = prefix or new_id.upper()
    label = label or prefix
    written: list[str] = []
    notes: list[str] = []
    src_recipe = root / "recipes" / f"{source}.json"
    if not src_recipe.exists():
        raise FileNotFoundError(f"no recipe to start from: {src_recipe}")
    targets = {root / "recipes" / f"{new_id}.json": src_recipe}
    for suffix in ("", "_harness", "_spawn"):
        s = root / "specs" / f"{source}{suffix}.json"
        if s.exists():
            targets[root / "specs" / f"{new_id}{suffix}.json"] = s
    clash = [str(t.relative_to(root)) for t in targets if t.exists()]
    if clash and not force:
        raise FileExistsError(f"already exists (pass --force to overwrite): {', '.join(clash)}")
    for dest, src in targets.items():
        data = rename_json(json.loads(src.read_text(encoding="utf-8")), source, new_id, prefix)
        if dest.parent.name == "recipes":
            data["name"] = prefix
            data["_comment"] = f"Scaffolded from recipes/{source}.json by `bl2 new`; every number below belongs to the {source.upper()} until you measure and re-cut."
            if glb:
                data["source"]["glb"] = glb
            data["source"]["body_match"] = new_id.lower()
        else:
            removed = strip_mesh_numbers(data)
            if removed:
                notes.append(f"{dest.name}: removed {len(removed)} old-mesh value(s); `bl2 build` fills socket/bounds overrides from the retarget")
            mod = data.get("mod") or {}
            mod["description"] = f"{label}: scaffolded from {source} by `bl2 new`; stats, title and red text still describe the {source.upper()}."
            mod["version"] = "0.0.1"
            # save_package keeps an OLD weapon reading records from a folder it used to share
            # (the Boxgun's is PipelineMeshes); a new weapon keeps its own, under its package
            if (data.get("options") or {}).pop("save_package", None) is not None:
                notes.append(f"{dest.name}: dropped options.save_package (the {source}'s legacy "
                             "save-record folder); this weapon keeps records under its own package")
            for b in data.get("balances") or []:
                t = b.get("title") or {}
                if "part_name" in t:
                    t["part_name"] = label
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        written.append(str(dest.relative_to(root)))
    todo = [
        f"recipes/{new_id}.json: source.glb" + (" (set)" if glb else " (MISSING: set it)") + ", body_match, attachments, host, bone map, checks",
        f"recipes/{new_id}.json: fragments[].rule + rule_order -- run `bl2 measure {new_id}` and read the labelled preview first",
        f"specs/{new_id}*.json: parts[].overrides (stats), balances[].title.red_text, pools, template parts for this host",
        f"docs/captures/{new_id}_stock_firstperson.png: one baseline run with harness_equip second_newest",
    ]
    return {"written": written, "notes": notes, "todo": todo, "prefix": prefix}
