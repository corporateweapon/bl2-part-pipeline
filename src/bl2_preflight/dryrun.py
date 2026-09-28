"""Offline dry run: import an emitted mod against ``tests/fakes`` and drive it like the game.

Exactly what ``tests/test_partgen.py`` / ``tests/test_armory.py`` do, packaged for a mod
folder on disk (emitted or installed), so a spec mistake -- a template that does not exist, a
socket the mesh lacks, a part list field the balance has no room for, a harness that never
reaches ``done`` -- shows up in a second instead of after a launch.

The fake world is built from ``catalog/parts.json`` and knows the assault-rifle, sniper, pistol and shotgun
gestalts (``tests/fakes/graph.py``). A weapon on another host type fails here for that
reason alone, and the result says so rather than blaming the spec.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

REPO = Path(__file__).resolve().parents[2]
FAKES = REPO / "tests" / "fakes"
_counter = itertools.count()
KEEP_ALIVE_FLAG = 0x4000
SUPPORTED_HOSTS = ("AssaultRifle", "SniperRifle", "Pistol", "Shotgun")


@dataclass
class DryRunResult:
    ok: bool
    mod_dir: Path
    kind: str = "weapon"               # weapon | armory
    registered: dict[str, Any] = field(default_factory=dict)   # component -> menu_setup record summary
    phases: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    built: list[str] = field(default_factory=list)     # objects the augmenter built from the catalog
    assumed: list[str] = field(default_factory=list)   # objects it had to assume (catalog cannot vouch)

    def lines(self) -> list[str]:
        out = [f"dry run {self.mod_dir.name} ({self.kind}): {'OK' if self.ok else 'FAILED'}"]
        for name, rec in self.registered.items():
            out.append(f"  {name}: menu_setup ok={rec.get('ok')} fragments={rec.get('fragments')} parts={rec.get('parts')} "
                       f"balances={rec.get('balances')} rooted={rec.get('rooted')}")
        if self.phases:
            out.append(f"  harness phases: {self.phases}")
        for p in self.problems:
            out.append(f"  PROBLEM: {p}")
        if self.built:
            out.append(f"  from catalog: {len(self.built)} object(s) built for the fake world")
        if self.assumed:
            out.append(f"  assumed present (not checkable offline): " + "; ".join(self.assumed[:8])
                       + (f" (+{len(self.assumed) - 8} more)" if len(self.assumed) > 8 else ""))
        for n in self.notes:
            out.append(f"  note: {n}")
        return out


class _FakeClock:
    """Stands in for ``time`` inside the mod so second-timed harness waits pass per tick."""

    def __init__(self) -> None:
        self.now = 1_000_000.0

    def time(self) -> float:
        self.now += 0.25
        return self.now

    def __getattr__(self, name: str) -> Any:
        import time as _time
        return getattr(_time, name)


def _load(mod_dir: Path, graph: Any, package: bool) -> ModuleType:
    import mods_base  # the fake, once FAKES is on sys.path
    import unrealsdk

    unrealsdk.set_world(graph.world)
    mods_base.set_pc(graph.controller)
    mods_base.built_mods.clear()
    sys.modules.pop("_bl2_pipeline_shared", None)
    name = f"dryrun_mod_{next(_counter)}"
    spec = importlib.util.spec_from_file_location(
        name, mod_dir / "__init__.py", submodule_search_locations=[str(mod_dir)] if package else None)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {mod_dir / '__init__.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _summarise_setup(rec: dict[str, Any], world: Any) -> tuple[dict[str, Any], list[str]]:
    problems: list[str] = []
    for f in rec.get("fragments") or []:
        if isinstance(f, dict) and (f.get("error") or (f.get("skipped") and "already" not in str(f["skipped"]))):
            problems.append(f"fragment {f.get('fragment')}: {f.get('error') or f.get('skipped')}")
    for p in rec.get("parts") or []:
        if isinstance(p, dict) and p.get("constructed") is False:
            problems.append(f"part {p.get('part')}: not constructed")
    for b in rec.get("balances") or []:
        if isinstance(b, dict) and b.get("constructed") is False:
            problems.append(f"balance {b.get('balance')}: not constructed")
    if rec.get("error"):
        problems.append(str(rec["error"]).splitlines()[0][:300])
    if rec.get("ok") is False and not problems:
        problems.append("menu_setup returned ok=false without naming a cause")
    # F17: everything constructed must be rooted
    unrooted = []
    for p in rec.get("parts") or []:
        path = p.get("part") if isinstance(p, dict) else None
        if path:
            obj = _find(world, path)
            if obj is not None and not (int(getattr(obj, "ObjectFlags", 0)) & KEEP_ALIVE_FLAG):
                unrooted.append(path)
    if unrooted:
        problems.append(f"not rooted against GC (F17): {', '.join(unrooted[:4])}")
    summary = {"ok": rec.get("ok"), "fragments": len(rec.get("fragments") or []), "parts": len(rec.get("parts") or []),
               "balances": len(rec.get("balances") or []), "rooted": not unrooted}
    return summary, problems


def _find(world: Any, path: str) -> Any:
    try:
        import unrealsdk
        return unrealsdk.find_object("Object", path)
    except Exception:  # noqa: BLE001
        return None


def _drive_harness(module: ModuleType, graph: Any, result: DryRunResult) -> None:
    if not hasattr(module, "on_map_loaded") or not hasattr(module, "seq_tick"):
        return
    module.time = _FakeClock()  # type: ignore[attr-defined]
    module.on_map_loaded(graph.controller, None, None, None)
    for _ in range(5000):
        if not getattr(module.seq_tick, "enabled", False):
            break
        module.seq_tick(None, None, None, None)
    else:
        result.problems.append("harness phase machine never finished within 5000 ticks")
    recs = getattr(module, "_status", [])
    phases = [r for r in recs if isinstance(r.get("phase"), int)]
    result.phases = [r["phase"] for r in phases]
    if phases and not any(r.get("done") is True for r in phases):
        result.problems.append("harness never wrote done: true")
    for r in phases:
        for k, v in r.items():
            if isinstance(v, str) and v.startswith("FAILED"):
                first = v.splitlines()[0]
                if "FakeObject' object has no attribute" in first:
                    # a controller/pawn method the fake does not model (the pose steps:
                    # ShowStatusMenu_Inventory, StartAltFire, ...) -- not a spec problem
                    result.notes.append(f"phase {r['phase']} {k} not modelled offline: {first[first.find('attribute'):][:80]}")
                else:
                    result.problems.append(f"phase {r['phase']} {k}: {first[:160]}")


def dry_run(mod_dir: Path | str, catalog_path: Path | None = None) -> DryRunResult:
    mod_dir = Path(mod_dir)
    result = DryRunResult(ok=False, mod_dir=mod_dir)
    if not (mod_dir / "__init__.py").exists():
        result.problems.append(f"no __init__.py under {mod_dir}")
        return result
    spec = {}
    try:
        spec = json.loads((mod_dir / "spec.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result.notes.append("no spec.json beside the mod; host type assumed AssaultRifle")
    is_armory = isinstance(spec.get("weapons"), dict)
    result.kind = "armory" if is_armory else "weapon"
    hosts = ([w.get("weapon_type") for w in spec["weapons"].values()] if is_armory else [spec.get("weapon_type") or "AssaultRifle"])
    unsupported = [h for h in hosts if h not in SUPPORTED_HOSTS]
    if unsupported:
        result.problems.append(f"the fake world has no {', '.join(map(str, unsupported))} gestalt (tests/fakes/graph.py knows {SUPPORTED_HOSTS}); "
                               "the dry run cannot judge this build")
        return result
    if str(FAKES) not in sys.path:
        sys.path.insert(0, str(FAKES))
    if str(REPO / "src") not in sys.path:
        sys.path.insert(0, str(REPO / "src"))
    try:
        from bl2_catalog import load_catalog
        from tests.fakes.graph import build_graph
    except Exception as ex:  # noqa: BLE001
        result.problems.append(f"cannot import the fakes: {ex!r}")
        return result
    try:
        catalog = load_catalog(catalog_path) if catalog_path else load_catalog()
    except Exception as ex:  # noqa: BLE001
        result.problems.append(f"catalog unavailable: {ex!r} (python -m bl2_catalog)")
        return result
    try:
        graph = build_graph(catalog)
        if spec:
            from bl2_preflight.augment import augment_world
            result.built, result.assumed = augment_world(graph.world, catalog, spec)
        module = _load(mod_dir, graph, package=is_armory)
    except Exception as ex:  # noqa: BLE001
        result.problems.append(f"import failed: {ex!r}")
        result.notes.extend(traceback.format_exc().strip().splitlines()[-3:])
        return result
    try:
        if is_armory:
            for w in module.WEAPONS:
                rec = w["module"].menu_setup()
                summary, problems = _summarise_setup(rec, graph.world)
                result.registered[w["id"]] = summary
                result.problems.extend(f"{w['id']}: {p}" for p in problems)
        else:
            rec = module.menu_setup()
            summary, problems = _summarise_setup(rec, graph.world)
            result.registered[mod_dir.name] = summary
            result.problems.extend(problems)
            _drive_harness(module, graph, result)
    except Exception as ex:  # noqa: BLE001
        result.problems.append(f"driving the mod raised {ex!r}")
        result.notes.extend(traceback.format_exc().strip().splitlines()[-3:])
    result.ok = not result.problems
    return result
