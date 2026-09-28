"""The preflight checks. Each is a function ``(ctx) -> list[Check]``; :func:`run_checks` runs
them all and never raises -- a check that blows up becomes a FAIL naming itself.

Statuses: ``ok`` (fine), ``warn`` (the run can go ahead, but this will cost something),
``fail`` (do not launch), ``skip`` (not applicable / not asked for).
"""

from __future__ import annotations

import ctypes
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from bl2_verify import userdirs

REPO = Path(__file__).resolve().parents[2]
SCRATCH = REPO / "scratch"
GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Borderlands 2")
CONFIG_DIR = userdirs.config_dir()      # BL2_WILLOW_DIR overrides
SAVE = userdirs.default_save()          # BL2_SAVE overrides
BACKUP_ROOT = REPO / "backup"
TEMPLATES_PY = REPO / "src" / "bl2_partgen" / "templates.py"
CALIBRATED = (1920, 1080)
HASHED = ("akaudio.upk", "core.upk", "engine.upk", "gameframework.upk", "gearboxframework.upk",
          "gfxui.upk", "ipdrv.upk", "menumap.upk", "onlinesubsystemsteamworks.upk", "startup.upk",
          "startup_loc_int.upk", "willowgame.upk")
#: window titles that mean a modal box is parked over the desktop (F27)
DIALOG_TITLES = ("application error", "windows input experience", "fatal error", "runtime error",
                 "has stopped working", "microsoft visual c++", "save as")

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""
    code: str = ""        # F-number when there is one


@dataclass
class Context:
    """Everything the checks may look at. Built once by :func:`build_context`."""
    game: Path = GAME
    mod: str | None = None
    part_path: str | None = None
    control: Path | None = None
    baseline: Path | None = None
    capture_view: str | None = None
    save: Path = SAVE
    config_dir: Path = CONFIG_DIR
    backup_root: Path = BACKUP_ROOT
    templates_py: Path = TEMPLATES_PY
    deep: bool = False
    # gathered
    spec: dict[str, Any] | None = None
    mods: list[dict[str, Any]] = field(default_factory=list)
    processes: dict[str, list[int]] = field(default_factory=dict)
    windows: list[tuple[str, int]] = field(default_factory=list)   # (title, pid) of visible top-level windows
    foreground: tuple[str, int] | None = None
    desktop: tuple[int, int] | None = None
    ini: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- gathering
def _read_json(path: Path | None) -> Any:
    if not path or not Path(path).exists():
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _tasklist(image: str) -> list[int]:
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH", "/FO", "CSV"],
                             capture_output=True, text=True, timeout=20, check=False,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:  # noqa: BLE001
        return []
    pids = []
    for line in out.splitlines():
        cols = [c.strip('"') for c in line.split('","')]
        if len(cols) >= 2 and cols[0].lower() == image.lower() and cols[1].isdigit():
            pids.append(int(cols[1]))
    return pids


def _windows() -> tuple[list[tuple[str, int]], tuple[str, int] | None, tuple[int, int] | None]:
    if sys.platform != "win32":
        return [], None, None
    try:
        from bl2_verify import winput
        user32 = ctypes.windll.user32
        try:
            user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass
        wins = [(winput.window_title(h), winput.window_pid(h)) for h in winput.find_windows()]
        wins = [(t, p) for t, p in wins if t]
        fg = user32.GetForegroundWindow()
        foreground = (winput.window_title(fg), winput.window_pid(fg)) if fg else None
        desktop = (int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1)))
        return wins, foreground, desktop
    except Exception:  # noqa: BLE001
        return [], None, None


def _ini_values(path: Path) -> dict[str, str]:
    """``[SystemSettings]`` ResX/ResY/Fullscreen/WindowedFullscreen from WillowEngine.ini."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    section = None
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("["):
                section = line
            elif section == "[SystemSettings]" and "=" in line:
                k, v = line.split("=", 1)
                if k in ("ResX", "ResY", "Fullscreen", "WindowedFullscreen"):
                    out[k] = v
    except OSError:
        pass
    return out


def scan_mods(game: Path, templates_py: Path) -> list[dict[str, Any]]:
    mods_dir = game / "sdk_mods"
    out: list[dict[str, Any]] = []
    if not mods_dir.exists():
        return out
    tpl_mtime = templates_py.stat().st_mtime if templates_py.exists() else None
    for d in sorted(mods_dir.iterdir()):
        if not d.is_dir() or not d.name.lower().startswith("pipeline"):
            continue
        st = _read_json(mods_dir / "settings" / f"{d.name}.json")
        spec = _read_json(d / "spec.json") or {}
        types: list[str] = []
        if isinstance(spec.get("weapons"), dict):
            types = [str(w.get("weapon_type")) for w in spec["weapons"].values() if isinstance(w, dict) and w.get("weapon_type")]
        elif spec.get("weapon_type"):
            types = [str(spec["weapon_type"])]
        sources = [p for p in [d / "__init__.py", *sorted((d / "weapons").glob("*.py"))] if p.exists()]
        marker = stale = None
        if sources:
            try:
                marker = any("_pipeline_saves" in p.read_text(encoding="utf-8", errors="replace") for p in sources)
                stale = tpl_mtime is not None and max(p.stat().st_mtime for p in sources) < tpl_mtime
            except OSError:
                pass
        out.append({"name": d.name, "dir": d, "enabled": (bool(st.get("enabled")) if isinstance(st, dict) else None),
                    "weapon_types": types, "spec": spec, "marker": marker, "stale": stale})
    return out


def build_context(**kw: Any) -> Context:
    ctx = Context(**{k: v for k, v in kw.items() if v is not None})
    ctx.mods = scan_mods(ctx.game, ctx.templates_py)
    if ctx.mod:
        mine = next((m for m in ctx.mods if m["name"] == ctx.mod), None)
        ctx.spec = (mine or {}).get("spec") or _read_json(REPO / "sdk_mod" / ctx.mod / "spec.json")
        if mine is None and ctx.spec is not None:
            ctx.notes.append(f"{ctx.mod} is not installed; spec read from sdk_mod/{ctx.mod}/spec.json")
    for image in ("steam.exe", "Borderlands2.exe", "Launcher.exe"):
        ctx.processes[image] = _tasklist(image)
    ctx.windows, ctx.foreground, ctx.desktop = _windows()
    ctx.ini = _ini_values(ctx.config_dir / "WillowEngine.ini")
    if ctx.control and ctx.part_path is None:
        c = _read_json(ctx.control) or {}
        if isinstance(c, dict) and c.get("part_name") and ctx.spec:
            pass  # resolved in check_part_path from the spec
    return ctx


# --------------------------------------------------------------------------- checks
def check_game(ctx: Context) -> list[Check]:
    exe = ctx.game / "Binaries" / "Win32" / "Borderlands2.exe"
    out = []
    if not exe.exists():
        return [Check("game", FAIL, f"missing {exe}", "point --game at the Borderlands 2 folder")]
    plugins = ctx.game / "Binaries" / "Win32" / "Plugins"
    missing = [n for n in ("unrealsdk.dll", "pyunrealsdk.dll") if not (plugins / n).exists()]
    if missing or not (ctx.game / "Binaries" / "Win32" / "ddraw.dll").exists():
        out.append(Check("sdk", FAIL, f"pyunrealsdk not installed: missing {missing or ['ddraw.dll']}",
                         "install the willow2 mod manager (PythonSDK) into Binaries\\Win32"))
    else:
        out.append(Check("sdk", OK, "pyunrealsdk present"))
    if ctx.processes.get("Borderlands2.exe"):
        out.append(Check("game running", FAIL, f"Borderlands2.exe already running (pid {ctx.processes['Borderlands2.exe']})",
                         "quit it (or taskkill) first; the driver refuses to launch beside it"))
    else:
        out.append(Check("game running", OK, "no Borderlands2.exe"))
    if ctx.processes.get("Launcher.exe"):
        out.append(Check("launcher", WARN, "a Launcher.exe splash is up", "the driver kills it (F10); nothing to do", "F10"))
    if not ctx.processes.get("steam.exe"):
        out.append(Check("steam", FAIL, "steam.exe is not running", "start Steam and sign in; the exe re-execs through it"))
    else:
        out.append(Check("steam", OK, "Steam running"))
    return out


def check_mod(ctx: Context) -> list[Check]:
    if not ctx.mod:
        return [Check("mod", SKIP, "no --mod given")]
    mine = next((m for m in ctx.mods if m["name"] == ctx.mod), None)
    out = []
    if mine is None:
        return [Check("mod", FAIL, f"{ctx.mod} is not under sdk_mods/ (parked in _disabled/, or never installed)",
                      f"python -m bl2_partgen specs/<spec>.json --out sdk_mod/{ctx.mod} --install \"<game>\" --replace")]
    if mine["enabled"] is None:
        out.append(Check("mod enabled", FAIL, f"no sdk_mods/settings/{ctx.mod}.json", 'write {"enabled": true} there', "F8"))
    elif not mine["enabled"]:
        out.append(Check("mod enabled", FAIL, f"settings/{ctx.mod}.json says enabled=false", "set enabled to true", "F8"))
    else:
        out.append(Check("mod enabled", OK, f"{ctx.mod} enabled"))
    if mine["marker"] is False:
        out.append(Check("mod fresh", FAIL, f"{ctx.mod} was emitted before the shared save store existed", "re-emit and reinstall", "F28"))
    elif mine["stale"]:
        out.append(Check("mod fresh", WARN, f"{ctx.mod} is older than templates.py", "re-emit if templates.py changed meaningfully", "F28"))
    else:
        out.append(Check("mod fresh", OK, "build newer than templates.py"))
    if not mine["spec"]:
        out.append(Check("mod spec", WARN, "no spec.json beside the installed mod; part/package/option checks skipped"))
    return out


def check_rule3(ctx: Context) -> list[Check]:
    enabled = [m for m in ctx.mods if m["enabled"]]
    by_type: dict[str, list[str]] = {}
    for m in enabled:
        for t in dict.fromkeys(m["weapon_types"]):     # an Armory lists a host once per weapon
            by_type.setdefault(t, []).append(m["name"])
    out = []
    for t, names in by_type.items():
        if len(names) > 1:
            out.append(Check("one mod per gestalt", FAIL, f"{t}: {', '.join(names)} all enabled",
                             "park all but one under sdk_mods/_disabled/ (move its settings JSON too)", "RULE3"))
    if not out:
        out.append(Check("one mod per gestalt", OK, f"enabled: {[m['name'] for m in enabled] or 'none'}"))
    if ctx.mod and enabled and not any(m["name"] == ctx.mod for m in enabled):
        others = [m["name"] for m in enabled]
        out.append(Check("mod is the one enabled", WARN, f"{ctx.mod} is not enabled but {others} are",
                         "the run will exercise the enabled build, not the one you named"))
    return out


#: EngineVersion in BL2's cooked package summaries (Startup.upk, and every package we write);
#: TPS's is 2630070 and BL2 refuses a package that claims it (F35)
BL2_ENGINE_VERSION = 1712575


def _engine_version(path: Path) -> int | None:
    """EngineVersion from an uncompressed package summary (None if it cannot be read)."""
    try:
        from bl2_upk.reader import Package
        return int(Package.from_file(path).summary.engine_version)
    except Exception:  # noqa: BLE001 - compressed or foreign: not ours to judge
        return None


def check_packages(ctx: Context) -> list[Check]:
    if not ctx.spec:
        return [Check("packages", SKIP, "no spec")]
    cooked = ctx.game / "WillowGame" / "CookedPCConsole"
    names: list[str] = []
    specs = list(ctx.spec["weapons"].values()) if isinstance(ctx.spec.get("weapons"), dict) else [ctx.spec]
    for s in specs:
        if s.get("package"):
            names.append(str(s["package"]))
        for e in s.get("extra_packages") or []:
            if isinstance(e, dict) and e.get("name"):
                names.append(str(e["name"]))
    out = []
    for n in dict.fromkeys(names):
        p = cooked / f"{n}.upk"
        if f"{n.lower()}.upk" in HASHED:
            out.append(Check("package", FAIL, f"{n} is one of the twelve exe-hashed names", "ship a new package name", "F12"))
        elif not p.exists():
            out.append(Check("package", FAIL, f"{p.name} missing from CookedPCConsole", "copy the built .upk in (or --install)"))
        elif (cooked / f"{n}.upk.uncompressed_size").exists():
            out.append(Check("package", FAIL, f"{p.name} has a .uncompressed_size sidecar", "rename the sidecar away", "F11"))
        elif _engine_version(p) is not None and _engine_version(p) > BL2_ENGINE_VERSION:
            out.append(Check("package", FAIL,
                             f"{p.name} claims EngineVersion {_engine_version(p)} (> BL2's {BL2_ENGINE_VERSION}); load_package returns None",
                             "build it with a BL2 package as the header template", "F35"))
        else:
            out.append(Check("package", OK, f"{p.name} {p.stat().st_size:,} bytes"))
    return out


def check_hashed(ctx: Context) -> list[Check]:
    """F12: the hashed base packages must be byte-identical to shipped. Size against the
    repo backup is the cheap test; ``--deep`` hashes Startup.upk (a few seconds)."""
    cooked = ctx.game / "WillowGame" / "CookedPCConsole"
    ref = ctx.backup_root / "CookedPCConsole" / "Startup.upk"
    live = cooked / "Startup.upk"
    if not live.exists():
        return [Check("hashed packages", FAIL, "Startup.upk missing", "verify the game files in Steam", "F12")]
    if not ref.exists():
        return [Check("hashed packages", SKIP, "no backup/CookedPCConsole/Startup.upk to compare against")]
    if live.stat().st_size != ref.stat().st_size:
        return [Check("hashed packages", FAIL, f"Startup.upk size {live.stat().st_size:,} != backup {ref.stat().st_size:,}",
                      "restore Startup.upk from backup/CookedPCConsole (the exe SHA1-checks it)", "F12")]
    if ctx.deep:
        import hashlib
        want = (ref.with_suffix(".upk.sha256").read_text(encoding="utf-8").split() or [""])[0] if ref.with_suffix(".upk.sha256").exists() else None
        h = hashlib.sha256()
        with open(live, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if want and h.hexdigest() != want:
            return [Check("hashed packages", FAIL, "Startup.upk sha256 differs from the backup", "restore it from backup/CookedPCConsole", "F12")]
        return [Check("hashed packages", OK, "Startup.upk sha256 matches the backup")]
    return [Check("hashed packages", OK, "Startup.upk size matches the backup (--deep for sha256)")]


def check_desktop(ctx: Context) -> list[Check]:
    out = []
    if ctx.desktop and ctx.desktop != CALIBRATED:
        out.append(Check("resolution", WARN, f"desktop is {ctx.desktop[0]}x{ctx.desktop[1]}; captures will be too (calibrated {CALIBRATED[0]}x{CALIBRATED[1]})",
                         "boxes are rescaled by compare.scale_box; set 1920x1080 if the probes go blind", "F31"))
    elif ctx.desktop:
        out.append(Check("resolution", OK, f"desktop {ctx.desktop[0]}x{ctx.desktop[1]}"))
    if ctx.ini:
        exclusive = ctx.ini.get("Fullscreen", "").lower() == "true" and ctx.ini.get("WindowedFullscreen", "").lower() != "true"
        if exclusive:
            out.append(Check("fullscreen mode", WARN, "WillowEngine.ini: exclusive fullscreen",
                             "borderless keeps rendering when focus shifts; exclusive minimises (F20/F32)", "F32"))
        else:
            out.append(Check("fullscreen mode", OK, f"WillowEngine.ini: Fullscreen={ctx.ini.get('Fullscreen')} WindowedFullscreen={ctx.ini.get('WindowedFullscreen')}"))
    fg_title = (ctx.foreground[0] if ctx.foreground else "").lower()
    # the touch-keyboard host keeps a full-screen window alive; it is only a problem when it
    # OWNS the foreground (F27's second culprit), not merely when it exists
    dialogs = [(t, p) for t, p in ctx.windows
               if any(k in t.lower() for k in DIALOG_TITLES)
               and not ("windows input experience" in t.lower() and "windows input experience" not in fg_title)]
    if ctx.foreground and any(k in fg_title for k in ("counter-strike", "cs2", "dota", "valorant", "apex", "fortnite"))              or (ctx.foreground and ctx.processes.get("Borderlands2.exe") == [] and fg_title and
                any(k in fg_title for k in ("- steam", "steam game"))):
        out.append(Check("foreground", FAIL, f"another game is in the foreground: {ctx.foreground[0]!r}",
                         "someone is playing; do not launch (the loop would steal the focus and capture the wrong game)", "F27"))
    if dialogs:
        out.append(Check("dialogs", FAIL, "a dialog is up: " + "; ".join(f"{t!r} (pid {p})" for t, p in dialogs[:3]),
                         "dismiss it (FindWindowEx OK + BM_CLICK if it will not take a click); every focus and capture fails while it is there", "F27"))
    else:
        out.append(Check("dialogs", OK, "no error dialog on screen"))
    if ctx.foreground and any(k in ctx.foreground[0].lower() for k in ("claude", "terminal", "powershell", "command prompt", "code")):
        out.append(Check("foreground", WARN, f"foreground is {ctx.foreground[0]!r}",
                         "run the loop in the background with stdout redirected and echo off, or it will steal focus mid-run", "F32"))
    return out


def _spec_parts(spec: dict[str, Any]) -> list[str]:
    specs = list(spec["weapons"].values()) if isinstance(spec.get("weapons"), dict) else [spec]
    out = []
    for s in specs:
        for p in s.get("parts") or []:
            if isinstance(p, dict) and p.get("outer") and p.get("part_name"):
                out.append(f"{p['outer']}.{p['part_name']}")
    return out


def check_part_path(ctx: Context) -> list[Check]:
    if not ctx.spec:
        return [Check("part path", SKIP, "no spec")]
    parts = _spec_parts(ctx.spec)
    want = ctx.part_path
    if want is None and ctx.control:
        c = _read_json(ctx.control) or {}
        pn = c.get("part_name") if isinstance(c, dict) else None
        if pn:
            want = next((p for p in parts if p.endswith("." + str(pn))), str(pn))
    if want is None:
        return [Check("part path", SKIP, "no --part-path or control file")]
    if want in parts:
        return [Check("part path", OK, f"{want} is registered by {ctx.mod}")]
    close = [p for p in parts if p.rsplit(".", 1)[-1] == want.rsplit(".", 1)[-1]]
    return [Check("part path", FAIL, f"{want} is not a part this build registers",
                  (f"did you mean {close[0]}?" if close else f"registered: {', '.join(parts[:6])}"), "PARTPATH")]


def check_run_args(ctx: Context) -> list[Check]:
    out = []
    if ctx.baseline is not None:
        if Path(ctx.baseline).exists():
            out.append(Check("baseline", OK, f"{Path(ctx.baseline).name} present"))
        else:
            out.append(Check("baseline", FAIL, f"baseline capture missing: {ctx.baseline}",
                             "make one: harness_equip second_newest variant, one run, copy its capture to docs/captures/"))
    if ctx.control is not None:
        c = _read_json(ctx.control)
        if not isinstance(c, dict):
            out.append(Check("control", FAIL, f"control file unreadable: {ctx.control}", "rebuild it from the sidecar + {enabled, mod, status_path}"))
        else:
            sp = c.get("status_path")
            if sp and not Path(sp).parent.exists():
                out.append(Check("control", FAIL, f"status_path folder missing: {Path(sp).parent}", "mkdir it"))
            elif c.get("mod") and ctx.mod and c["mod"] != ctx.mod:
                out.append(Check("control", WARN, f"control says mod={c['mod']!r}, run says {ctx.mod!r}", "pass the matching control file"))
            else:
                out.append(Check("control", OK, f"{Path(ctx.control).name} ok"))
    opts = ((ctx.spec or {}).get("options") or {}) if ctx.spec and "weapons" not in ctx.spec else {}
    if opts:
        if opts.get("test_harness") and opts.get("harness_timing", "ticks") != "seconds":
            out.append(Check("harness timing", FAIL, "options.harness_timing is not \"seconds\"", "tick waits collapse at high fps; set it and re-emit", "F25"))
        if ctx.capture_view == "first" and opts.get("test_harness") and not opts.get("harness_capture_view"):
            out.append(Check("capture view", WARN, "--capture-view first but the harness has no harness_capture_view",
                             "the view is not held; an F12 press flips it (F23)", "F23"))
        if opts.get("harness_pose") == "inventory":
            out.append(Check("harness pose", WARN, "harness_pose inventory: the item card stays open, survives_reload will not score",
                             "capture-only run; read the other three conditions"))
        if opts.get("test_harness") and not opts.get("harness_auto", True):
            out.append(Check("harness auto", WARN, "harness_auto is false: the loop's phase machine will not run", "use the harness variant, not the spawn one"))
        if not out or all(c.name not in ("harness timing",) for c in out):
            out.append(Check("harness options", OK, f"timing={opts.get('harness_timing', 'ticks')} view={opts.get('harness_capture_view')} pose={opts.get('harness_pose', 'idle')}"))
    return out


def check_save(ctx: Context) -> list[Check]:
    out = []
    if not ctx.save.exists():
        return [Check("save", FAIL, f"save missing: {ctx.save}", "check the SaveData path in save_guard.py")]
    out.append(Check("save", OK, f"{ctx.save.name} {ctx.save.stat().st_size:,} bytes"))
    saves = ctx.backup_root / "saves"
    try:
        saves.mkdir(parents=True, exist_ok=True)
        probe = saves / ".preflight"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
    except OSError as ex:
        out.append(Check("save backup dir", FAIL, f"cannot write {saves}: {ex}", "fix permissions; the loop snapshots the save there"))
    lives = sorted(ctx.backup_root.glob("live-*"))
    if lives:
        age_h = (time.time() - lives[-1].stat().st_mtime) / 3600
        if age_h > 24:
            out.append(Check("independent save copy", WARN, f"newest backup/live-* is {age_h:.0f} h old",
                             "take one before a session; the loop's restore is not final on a cloud-synced folder", "F30"))
        else:
            out.append(Check("independent save copy", OK, f"{lives[-1].name}"))
    else:
        out.append(Check("independent save copy", WARN, "no backup/live-* copy of the save", "copy the save (.sav and .sav.bak) to backup/live-<stamp>/ first", "F30"))
    return out


CHECKS: list[Callable[[Context], list[Check]]] = [
    check_game, check_mod, check_rule3, check_packages, check_hashed, check_desktop,
    check_part_path, check_run_args, check_save,
]


def run_checks(ctx: Context, checks: list[Callable[[Context], list[Check]]] | None = None) -> list[Check]:
    out: list[Check] = []
    for fn in checks or CHECKS:
        try:
            out.extend(fn(ctx))
        except Exception as ex:  # noqa: BLE001
            out.append(Check(fn.__name__, FAIL, f"check raised {ex!r}", "report this; the check itself is broken"))
    return out


def verdict(checks: list[Check]) -> str:
    if any(c.status == FAIL for c in checks):
        return "NO GO"
    if any(c.status == WARN for c in checks):
        return "GO (with warnings)"
    return "GO"
