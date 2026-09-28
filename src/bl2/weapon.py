"""The naming conventions one weapon id fans out to, so no command needs a path.

Given ``awp``:

======================  ====================================================
recipe                  ``recipes/awp.json``
player spec             ``specs/awp.json``           (mod ``PipelineAWP``)
harness spec            ``specs/awp_harness.json``   (mod ``PipelineAWPHarness``)
spawn spec (optional)   ``specs/awp_spawn.json``     (mod ``PipelineAWPSpawn``)
package                 ``spec.package`` -> ``scratch/PipelineMeshesAWP.upk`` (recipe output)
sidecar                 the package path with ``.upk`` -> ``.fragment.json``
retarget report         recipe ``output.report``
status file             ``scratch/<HarnessMod>_status.json``
control file            ``scratch/awp_control.json``   (sidecar + enabled/mod/status_path)
proposal                ``scratch/awp_proposal.json``  (first part's lint proposal)
part path               the first spec part that carries a fragment
baseline capture        ``docs/captures/boxgun_stock_firstperson.png``
verify runs             ``scratch/verify_runs_awp_fp``
======================  ====================================================
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    env = os.environ.get("BL2_PIPELINE_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[2]


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@dataclass
class Weapon:
    id: str
    root: Path

    # ------------------------------------------------------------------ files
    @property
    def recipe(self) -> Path:
        return self.root / "recipes" / f"{self.id}.json"

    def spec(self, variant: str = "player") -> Path:
        suffix = {"player": "", "harness": "_harness", "spawn": "_spawn"}[variant]
        return self.root / "specs" / f"{self.id}{suffix}.json"

    def variants(self) -> list[str]:
        return [v for v in ("player", "harness", "spawn") if self.spec(v).exists()]

    def spec_data(self, variant: str = "player") -> dict[str, Any] | None:
        return _read(self.spec(variant))

    def mod_name(self, variant: str = "harness") -> str | None:
        d = self.spec_data(variant)
        return ((d or {}).get("mod") or {}).get("name")

    @property
    def package_name(self) -> str | None:
        d = self.spec_data("player") or self.spec_data("harness")
        return (d or {}).get("package")

    # ------------------------------------------------------------------ package groups
    def group(self) -> dict[str, Any] | None:
        """The ``packages/<Name>.json`` group this weapon's recipe belongs to, if any."""
        pk = self.root / "packages"
        if not pk.exists():
            return None
        for p in sorted(pk.glob("*.json")):
            g = _read(p)
            if g and self.id in (g.get("recipes") or []):
                g["_path"] = str(p)
                g["_name"] = p.stem
                return g
        return None

    @property
    def package(self) -> Path | None:
        g = self.group()
        if g and g.get("out"):
            p = Path(g["out"])
            return p if p.is_absolute() else self.root / p
        r = _read(self.recipe)
        if r and (r.get("output") or {}).get("package"):
            p = Path(r["output"]["package"])
            return p if p.is_absolute() else self.root / p
        d = self.spec_data("player") or self.spec_data("harness")
        if d and d.get("package_file"):
            return (self.spec("player").parent / d["package_file"]).resolve()
        return None

    @property
    def sidecar(self) -> Path | None:
        p = self.package
        return p.with_suffix(".fragment.json") if p else None

    @property
    def report(self) -> Path:
        r = _read(self.recipe) or {}
        rel = (r.get("output") or {}).get("report") or f"scratch/{self.id}_retarget_report.json"
        p = Path(rel)
        return p if p.is_absolute() else self.root / p

    def status_file(self, variant: str = "harness") -> Path:
        return self.root / "scratch" / f"{self.mod_name(variant) or self.id}_status.json"

    @property
    def control(self) -> Path:
        return self.root / "scratch" / f"{self.id}_control.json"

    @property
    def proposal(self) -> Path:
        return self.root / "scratch" / f"{self.id}_proposal.json"

    @property
    def baseline(self) -> Path:
        return self.root / "docs" / "captures" / f"{self.id}_stock_firstperson.png"

    @property
    def runs(self) -> Path:
        return self.root / "scratch" / f"verify_runs_{self.id}_fp"

    def mod_dir(self, variant: str = "harness") -> Path | None:
        name = self.mod_name(variant)
        return (self.root / "sdk_mod" / name) if name else None

    # ------------------------------------------------------------------ derived from the spec
    def part_path(self, variant: str = "harness") -> str | None:
        d = self.spec_data(variant) or self.spec_data("player")
        for p in (d or {}).get("parts") or []:
            if isinstance(p, dict) and p.get("fragment") and p.get("outer") and p.get("part_name"):
                return f"{p['outer']}.{p['part_name']}"
        return None

    def first_part_name(self, variant: str = "harness") -> str | None:
        pp = self.part_path(variant)
        return pp.rsplit(".", 1)[-1] if pp else None

    # ------------------------------------------------------------------ generated inputs for the loop
    def write_control(self, variant: str = "harness") -> Path:
        """``scratch/<id>_control.json`` = the sidecar plus what the loop reads for provenance."""
        side = self.sidecar
        data: dict[str, Any] = {}
        if side and side.exists():
            data.update(_read(side) or {})
        data.update({
            "part_name": self.first_part_name(variant) or data.get("part_name"),
            "enabled": True, "mod": self.mod_name(variant),
            "status_path": str(self.status_file(variant)),
            "note": "read by run_loop for provenance only; the mod bakes its own status path",
        })
        self.control.parent.mkdir(parents=True, exist_ok=True)
        self.control.write_text(json.dumps(data, indent=1), encoding="utf-8")
        return self.control

    def write_proposal(self, variant: str = "harness") -> Path | None:
        """``scratch/<id>_proposal.json`` = the first part's proposal from the emitted lint.json."""
        mod_dir = self.mod_dir(variant)
        lint = _read(mod_dir / "lint.json") if mod_dir else None
        if not lint:
            return None
        want = self.first_part_name(variant)
        results = lint.get("results") or []
        chosen = next((r for r in results if r.get("part") == want), results[0] if results else None)
        if not chosen or not chosen.get("proposal"):
            return None
        self.proposal.write_text(json.dumps(chosen["proposal"], indent=1), encoding="utf-8")
        return self.proposal

    def exists(self) -> bool:
        return self.recipe.exists() or self.spec("player").exists() or self.spec("harness").exists()

    def describe(self) -> list[str]:
        out = [f"weapon {self.id}"]
        out.append(f"  recipe   {'ok ' if self.recipe.exists() else 'MISSING'} {self.recipe.relative_to(self.root)}")
        for v in ("player", "harness", "spawn"):
            p = self.spec(v)
            if p.exists() or v != "spawn":
                out.append(f"  spec/{v:<7} {'ok ' if p.exists() else 'MISSING'} {p.relative_to(self.root)}"
                           + (f"  mod={self.mod_name(v)}" if p.exists() else ""))
        g = self.group()
        if g:
            out.append(f"  group    {g['_name']}: {' + '.join(g['recipes'])} -> {g.get('out')}")
        pk, sc = self.package, self.sidecar
        out.append(f"  package  {'ok ' if pk and pk.exists() else 'MISSING'} {pk}")
        out.append(f"  sidecar  {'ok ' if sc and sc.exists() else 'MISSING'} {sc}")
        out.append(f"  part     {self.part_path() or 'MISSING (no part with a fragment)'}")
        out.append(f"  baseline {'ok ' if self.baseline.exists() else 'MISSING'} {self.baseline.relative_to(self.root)}")
        return out


def list_weapons(root: Path) -> list[str]:
    ids = {p.stem for p in (root / "recipes").glob("*.json")} if (root / "recipes").exists() else set()
    for p in (root / "specs").glob("*.json"):
        stem = p.stem
        if stem in ("armory", "bent_barrel", "bent_barrel_harness"):
            continue
        for suffix in ("_harness", "_spawn", "_harness_ads", "_harness_fp_baseline", "_harness_inventory"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        if "_harness" in stem:
            continue
        ids.add(stem)
    return sorted(ids)
