"""Compact, fixed-shape summaries of the pipeline's JSON artefacts.

The point is a read that costs a screenful, not the file: a 56 KB status JSON becomes ~20
lines, a verify report ~12. Every function returns a list of lines; the CLI prints them.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _short(path: Any, n: int = 60) -> str:
    s = str(path)
    return s if len(s) <= n else "..." + s[-(n - 3):]


def _leaf(path: str) -> str:
    return path.rsplit(".", 1)[-1] if isinstance(path, str) else str(path)


# --------------------------------------------------------------------------- reports
def summarize_report(rep: dict[str, Any]) -> list[str]:
    p = rep.get("params") or {}
    kind = "armory" if "weapons" in rep else "verify"
    mod = rep.get("mod") or p.get("mod")
    stamp = rep.get("timestamp", "?")
    out = [f"{kind} run {rep.get('run', '?')}  mod={mod}  {stamp}"
           + (f"  elapsed={rep['elapsed_s']}s" if rep.get("elapsed_s") is not None else "")]
    if kind == "verify":
        out.append(f"  view={p.get('capture_view')} box={p.get('weapon_box')} part={_leaf(p.get('part_path', ''))} "
                   f"baseline={_short(p.get('baseline', ''), 40)}")
    else:
        out.append(f"  weapons={', '.join(rep.get('weapons') or [])}")
    for name, c in (rep.get("conditions") or {}).items():
        out.append(f"  {str(c.get('status', '?')).upper():5} {name}: {c.get('summary', '')}")
    if rep.get("failure"):
        out.append(f"  FAILURE: {rep['failure']}")
    held = rep.get("held") or {}
    if held:
        ours = held.get("ours") or {}
        out.append(f"  held: slot {held.get('slot')} balance={_leaf(held.get('balance', '?'))} "
                   f"ours={len(ours)} slot(s) uid={held.get('unique_id')}")
    out.append(f"  ok={rep.get('ok')} all_pass={rep.get('all_pass')} save_restored={rep.get('save_restored')}")
    caps = rep.get("captures") or {}
    if caps:
        out.append("  captures: " + ", ".join(f"{k}={_short(v, 45)}" for k, v in caps.items() if not isinstance(v, list)))
    return out


# --------------------------------------------------------------------------- status files
def summarize_status(recs: list[dict[str, Any]], since: float | None = None) -> list[str]:
    if since is not None:
        recs = [r for r in recs if float(r.get("t", 0)) >= since]
    out: list[str] = []
    ts = [float(r["t"]) for r in recs if "t" in r]
    span = f"{datetime.fromtimestamp(ts[0]):%H:%M:%S}-{datetime.fromtimestamp(ts[-1]):%H:%M:%S}" if ts else "?"
    out.append(f"status: {len(recs)} record(s) {span}")
    setup = next((r for r in recs if r.get("phase") == "menu_setup"), None)
    if setup:
        frags = setup.get("fragments") or []
        parts = setup.get("parts") or []
        bad_f = [f.get("fragment") for f in frags if isinstance(f, dict) and (f.get("error") or f.get("skipped"))]
        bad_p = [_leaf(p.get("part", "")) for p in parts if isinstance(p, dict) and p.get("constructed") is False]
        out.append(f"  menu_setup ok={setup.get('ok')} mod={setup.get('mod')} mesh={str(setup.get('mesh', '')).split(' ')[0]}")
        out.append(f"    fragments {len(frags)}: " + ", ".join(str(f.get("fragment")) for f in frags if isinstance(f, dict))
                   + (f"  PROBLEM: {bad_f}" if bad_f else ""))
        out.append(f"    parts {len(parts)}: " + ", ".join(_leaf(p.get("part", "")) for p in parts if isinstance(p, dict))
                   + (f"  NOT CONSTRUCTED: {bad_p}" if bad_p else ""))
        for b in setup.get("balances") or []:
            if isinstance(b, dict):
                out.append(f"    balance {_leaf(b.get('balance', ''))} constructed={b.get('constructed')} "
                           f"lists={len(b.get('part_lists') or {})} pools={len(b.get('pools') or [])}")
        for m in setup.get("materials") or []:
            if isinstance(m, dict):
                out.append(f"    material {_leaf(m.get('material', ''))} constructed={m.get('constructed')}")
        if setup.get("error"):
            out.append(f"    ERROR: {str(setup['error']).splitlines()[0][:160]}")
    else:
        out.append("  menu_setup: MISSING")
    loads = [r for r in recs if r.get("load_hook")]
    saves = [r for r in recs if r.get("save_hook")]
    for r in loads:
        out.append(f"  load {r.get('save')}: restored={len(r.get('restored') or [])} missing={len(r.get('missing') or [])} "
                   f"retired={len(r.get('retired') or [])} promoted={len(r.get('promoted') or [])}"
                   + (f"  ERROR {str(r['error']).splitlines()[0][:100]}" if r.get("error") else ""))
    if saves:
        last = saves[-1]
        out.append(f"  save hook x{len(saves)}: last custom_weapons={len(last.get('custom_weapons') or {})} "
                   f"kept={len(last.get('kept_from_previous') or [])}")
    validates = [r for r in recs if r.get("validate_hook")]
    if validates:
        out.append(f"  validate hook x{len(validates)} forced={sum(1 for r in validates if r.get('forced'))}")
    phases = [r for r in recs if isinstance(r.get("phase"), int)]
    if phases:
        pts = [float(r.get("t", 0)) for r in phases]
        deltas = ", ".join(f"{b - a:.1f}s" for a, b in zip(pts, pts[1:]))
        out.append(f"  harness phases: {[r['phase'] for r in phases]} gaps: {deltas}")
        for r in phases:
            for k in ("equip_newest", "camera", "restore_barrel_list", "sight_check"):
                if k in r:
                    v = r[k]
                    v = json.dumps(v)[:100] if isinstance(v, (dict, list)) else str(v)[:100]
                    out.append(f"    phase {r['phase']} {k}: {v}")
        held = next((r.get("held") for r in reversed(phases) if r.get("held")), None)
        if held:
            out.append(f"  held: {_leaf(held.get('weapon', ''))} slot {held.get('slot')} barrel={_leaf(held.get('barrel', ''))} "
                       f"ours={len(held.get('ours') or {})} balance={_leaf(held.get('balance', '?'))}")
    spawns = [r for r in recs if r.get("spawn")]
    for r in spawns:
        out.append(f"  spawn {r['spawn']}: granted={len(r.get('granted') or [])} level={r.get('level')} "
                   + (f"ERROR {str(r['error'])[:80]}" if r.get("error") else ""))
    keys = [r for r in recs if r.get("keybind")]
    if keys:
        out.append("  keybinds: " + ", ".join(f"{r['keybind']}" + ("(view)" if "behind_view" in r else "") for r in keys))
    errors = [r for r in recs if r.get("error") and not r.get("phase") == "menu_setup"]
    for r in errors[:3]:
        which = next((k for k in ("load_hook", "save_hook", "validate_hook", "keybind", "spawn") if k in r), "record")
        out.append(f"  ERROR in {which}: {str(r['error']).splitlines()[0][:140]}")
    return out


# --------------------------------------------------------------------------- lint.json
def summarize_lint(lint: dict[str, Any]) -> list[str]:
    out = [f"lint ok={lint.get('ok')} forced={lint.get('forced')} catalog={str(lint.get('catalog', ''))[:40]}"]
    counts: dict[str, int] = {}
    firsts: dict[str, str] = {}
    for res in lint.get("results") or []:
        rep = res.get("report") if isinstance(res.get("report"), dict) else res
        for item in rep.get("entries") or rep.get("findings") or []:
            if isinstance(item, dict):
                sev = str(item.get("severity") or item.get("level") or "?").lower()
                code = str(item.get("check") or item.get("code") or "?")
                key = f"{sev} {code}"
                counts[key] = counts.get(key, 0) + 1
                firsts.setdefault(key, f"{res.get('part', '')}: {str(item.get('message') or '')[:100]}")
    for key in sorted(counts):
        out.append(f"  {key} x{counts[key]}: {firsts[key]}")
    notes = lint.get("resolve_notes") or []
    if notes:
        out.append(f"  resolve notes: {len(notes)}")
    return out


def summarize_file(path: Path, since: float | None = None) -> list[str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return summarize_status(data, since=since)
    if isinstance(data, dict) and "conditions" in data:
        return summarize_report(data)
    if isinstance(data, dict) and "results" in data and "ok" in data:
        return summarize_lint(data)
    return [f"{path}: unrecognised JSON shape ({type(data).__name__}, keys {list(data)[:8] if isinstance(data, dict) else len(data)})"]
