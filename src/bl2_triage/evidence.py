"""Load and pre-digest a verify run's evidence.

Everything the rules look at is collected here into one :class:`Evidence` object so the rules
themselves stay pure functions over plain data (and so tests can build an ``Evidence`` by hand
without a game folder). Each source is optional: a missing file leaves its field empty and the
rules that need it stay silent.

Sources, in the order a run produces them:

* the **report** written by ``run_loop`` (``scratch/verify_report_<stamp>.json``) or by
  ``armory_check`` (``scratch/armory_report_<stamp>.json``) -- the scored conditions
* the mod's **status JSON** -- every record the generated mod wrote (``menu_setup``, the
  save/load/validate hooks, the harness phases)
* the **driver log** (``scratch/driver.log``) -- appended across runs; the slice belonging to
  this run starts at its ``launched Borderlands2.exe`` line
* **unrealsdk.log** -- rewritten by every launch, so the whole file is the latest run; older
  copies can be passed explicitly
* the game's **Launch.log** -- the engine's own log, where fatal errors land
* the **game folder** -- which pipeline mods are enabled, and what each installed build is
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from bl2_verify import userdirs

REPO = Path(__file__).resolve().parents[2]
SCRATCH = REPO / "scratch"
GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Borderlands 2")
SDK_LOG = GAME / "Binaries" / "Win32" / "Plugins" / "unrealsdk.log"
DRIVER_LOG = SCRATCH / "driver.log"
LAUNCH_LOG = userdirs.launch_log()   # BL2_WILLOW_DIR overrides
TEMPLATES_PY = REPO / "src" / "bl2_partgen" / "templates.py"

#: the resolution every pixel box in ``bl2_verify.compare`` / ``game_driver`` is calibrated at
CALIBRATED = (1920, 1080)

_DRIVER_LINE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2}) (?P<time>\d{2}:\d{2}:\d{2}) (?:\+\s*(?P<rel>[\d.]+)s)?\s*(?P<msg>.*)$")
_CAPTURE = re.compile(r"capture (?P<name>\S+) \(saved (?P<path>.+?) (?P<w>\d+)x(?P<h>\d+)\)")
_FOREGROUND = re.compile(r"foreground is '(?P<title>.*)' \(pid (?P<pid>\d+)\)")
_LAUNCHED = re.compile(r"launched \S+ pid=(?P<pid>\d+)")
_SDK_LINE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+Z)\s+\S+\s+(?P<loc>.*?)\s+(?P<lvl>[A-Z]+)\|\s?(?P<msg>.*)$")


@dataclass
class DriverSlice:
    """The driver-log lines of one run, pre-digested."""
    lines: list[str] = field(default_factory=list)
    launched_at: datetime | None = None
    pid: int | None = None
    captures: list[tuple[str, int, int]] = field(default_factory=list)   # (name, w, h)
    foreground: list[tuple[str, int]] = field(default_factory=list)      # (title, pid)
    focus_failures: int = 0
    menu_probe_zero: int = 0
    menu_never_confirmed: bool = False
    process_exited: bool = False
    exited_after_s: float | None = None
    killed_launcher: bool = False
    not_on_screen: int = 0
    save_restored: bool | None = None


@dataclass
class SdkSlice:
    """What ``unrealsdk.log`` says about the latest launch."""
    lines: list[str] = field(default_factory=list)
    sdk_loaded: bool = False
    registration_done: list[str] = field(default_factory=list)   # mod names that logged it
    registration_at: str | None = None
    tracebacks: list[str] = field(default_factory=list)          # first line of each ERR block
    pipeline_errors: list[str] = field(default_factory=list)     # ERR lines naming a Pipeline mod
    lines_after_init: int = 0
    init_at: datetime | None = None   # local time of the init banner, when parseable


@dataclass
class InstalledMod:
    name: str
    enabled: bool | None            # None = no settings file
    weapon_types: list[str]         # from spec.json (an Armory lists every component's)
    armory_ids: list[str]
    has_saves_marker: bool | None   # F28: the shared-store marker only newer templates emit
    stale_vs_templates: bool | None # __init__.py older than templates.py


@dataclass
class Evidence:
    report: dict[str, Any] | None = None
    report_path: Path | None = None
    status: list[dict[str, Any]] = field(default_factory=list)
    status_path: Path | None = None
    driver: DriverSlice = field(default_factory=DriverSlice)
    sdk: SdkSlice = field(default_factory=SdkSlice)
    launch_log_fatal: list[str] = field(default_factory=list)
    mods: list[InstalledMod] = field(default_factory=list)
    burst_hashes: list[str] = field(default_factory=list)
    spec: dict[str, Any] | None = None   # the installed/emitted spec.json of the mod under test
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ convenience
    @property
    def kind(self) -> str:
        if not self.report:
            return "none"
        return "armory" if "weapons" in self.report else "verify"

    @property
    def mod_name(self) -> str | None:
        if self.report:
            return self.report.get("mod") or (self.report.get("params") or {}).get("mod")
        for r in self.status:
            if r.get("phase") == "menu_setup":
                return r.get("mod")
        return None

    @property
    def conditions(self) -> dict[str, dict[str, Any]]:
        return (self.report or {}).get("conditions", {}) or {}

    def condition(self, name: str) -> dict[str, Any]:
        return self.conditions.get(name, {})

    @property
    def failure(self) -> str | None:
        return (self.report or {}).get("failure")

    @property
    def menu_setup(self) -> dict[str, Any] | None:
        return next((r for r in self.status if r.get("phase") == "menu_setup"), None)

    def phases(self) -> list[dict[str, Any]]:
        return [r for r in self.status if isinstance(r.get("phase"), int)]

    def options(self) -> dict[str, Any]:
        return ((self.spec or {}).get("options") or {}) if self.spec else {}


# --------------------------------------------------------------------------- loaders
def _read_json(path: Path | None) -> Any:
    if not path or not Path(path).exists():
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def latest_report(scratch: Path = SCRATCH) -> Path | None:
    cands = sorted(list(scratch.glob("verify_report_*.json")) + list(scratch.glob("armory_report_*.json")),
                   key=lambda p: p.stat().st_mtime)
    return cands[-1] if cands else None


def parse_driver_log(text: str, started: float | None = None) -> DriverSlice:
    """Slice one run out of the appended driver log.

    The run starts at a ``launched ... pid=`` line: the last one before/around ``started``
    (the report's epoch) when given, else the last one in the file.
    """
    raw = text.splitlines()
    launches: list[tuple[int, datetime]] = []
    for i, line in enumerate(raw):
        m = _DRIVER_LINE.match(line)
        if m and _LAUNCHED.search(m.group("msg")):
            launches.append((i, datetime.strptime(f"{m.group('date')} {m.group('time')}", "%Y-%m-%d %H:%M:%S")))
    start_idx = 0
    launched_at = None
    if launches:
        chosen = launches[-1]
        if started is not None:
            t0 = datetime.fromtimestamp(started)
            # the launch nearest the report's own start, never one more than a minute before it
            near = [l for l in launches if (l[1] - t0).total_seconds() >= -60]
            if near:
                chosen = min(near, key=lambda l: abs((l[1] - t0).total_seconds()))
        start_idx, launched_at = chosen
    # the slice ends at the next launch, if any
    end_idx = len(raw)
    for i, _ in launches:
        if i > start_idx:
            end_idx = i
            break
    d = DriverSlice(lines=raw[start_idx:end_idx], launched_at=launched_at)
    for line in d.lines:
        m = _DRIVER_LINE.match(line)
        msg = m.group("msg") if m else line
        rel = float(m.group("rel")) if m and m.group("rel") else None
        if (lm := _LAUNCHED.search(msg)):
            d.pid = int(lm.group("pid"))
        elif (cm := _CAPTURE.search(msg)):
            d.captures.append((cm.group("name"), int(cm.group("w")), int(cm.group("h"))))
        elif (fm := _FOREGROUND.search(msg)):
            d.foreground.append((fm.group("title"), int(fm.group("pid"))))
        elif msg.startswith("focus -> False"):
            d.focus_failures += 1
        elif "menu probe: yellow=0" in msg or "menu probe yellow=0" in msg:
            d.menu_probe_zero += 1
        elif "menu probe never confirmed" in msg:
            d.menu_never_confirmed = True
        elif msg == "process exited" or msg.startswith("process exited"):
            d.process_exited = True
            d.exited_after_s = rel
        elif "killed stray Launcher.exe" in msg:
            d.killed_launcher = True
        elif "not on screen" in msg or "left the screen" in msg:
            d.not_on_screen += 1
        elif msg.startswith("save restored, verified="):
            d.save_restored = msg.endswith("True")
    return d


def parse_sdk_log(text: str) -> SdkSlice:
    s = SdkSlice()
    lines = text.splitlines()
    # the latest launch starts at the last init banner
    starts = [i for i, l in enumerate(lines) if "unrealsdk::init" in l and "unrealsdk v" in l]
    lines = lines[starts[-1]:] if starts else lines
    s.lines = lines
    if lines:
        m0 = _SDK_LINE.match(lines[0])
        if m0:
            try:
                s.init_at = datetime.fromisoformat(m0.group("ts").replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
            except ValueError:
                s.init_at = None
    init_idx = None
    in_err = False
    for i, line in enumerate(lines):
        m = _SDK_LINE.match(line)
        msg = m.group("msg") if m else line
        lvl = m.group("lvl") if m else ""
        if "pyunrealsdk" in msg and "loaded" in msg:
            s.sdk_loaded = True
            init_idx = i
        rd = re.search(r"\[(?P<mod>[^\]]+)\] registration done", msg)
        if rd:
            s.registration_done.append(rd.group("mod"))
            s.registration_at = s.registration_at or (m.group("ts") if m else None)
        if lvl == "ERR":
            if "Traceback" in msg:
                in_err = True
            elif in_err and re.match(r"^\w+(Error|Exception)\b", msg.strip()):
                s.tracebacks.append(msg.strip()[:200])
                in_err = False
            if "Pipeline" in msg or "pipeline" in msg:
                s.pipeline_errors.append(msg.strip()[:200])
        elif lvl and lvl != "ERR":
            in_err = False
    s.lines_after_init = (len(lines) - init_idx - 1) if init_idx is not None else 0
    return s


def scan_launch_log(path: Path | None) -> list[str]:
    if not path or not Path(path).exists():
        return []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits = []
    for line in text.splitlines():
        low = line.lower()
        if "ran out of virtual memory" in low or "fatal error" in low or "critical:" in low:
            hits.append(line.strip()[:200])
    return hits[-5:]


def scan_installed_mods(game: Path = GAME, templates_py: Path = TEMPLATES_PY) -> list[InstalledMod]:
    mods_dir = game / "sdk_mods"
    if not mods_dir.exists():
        return []
    out: list[InstalledMod] = []
    tpl_mtime = templates_py.stat().st_mtime if templates_py.exists() else None
    for d in sorted(mods_dir.iterdir()):
        if not d.is_dir() or not d.name.lower().startswith("pipeline"):
            continue
        settings = mods_dir / "settings" / f"{d.name}.json"
        enabled: bool | None = None
        st = _read_json(settings)
        if isinstance(st, dict):
            enabled = bool(st.get("enabled", False))
        spec = _read_json(d / "spec.json") or {}
        types: list[str] = []
        ids: list[str] = []
        if "weapons" in spec and isinstance(spec["weapons"], dict):
            for wid, w in spec["weapons"].items():
                ids.append(wid)
                if isinstance(w, dict) and w.get("weapon_type"):
                    types.append(str(w["weapon_type"]))
        elif spec.get("weapon_type"):
            types.append(str(spec["weapon_type"]))
        sources = [d / "__init__.py", *sorted((d / "weapons").glob("*.py"))]
        sources = [p for p in sources if p.exists()]
        marker: bool | None = None
        stale: bool | None = None
        if sources:
            try:
                marker = any("_pipeline_saves" in p.read_text(encoding="utf-8", errors="replace") for p in sources)
                newest = max(p.stat().st_mtime for p in sources)
                stale = (tpl_mtime is not None) and (newest < tpl_mtime)
            except OSError:
                pass
        out.append(InstalledMod(d.name, enabled, types, ids, marker, stale))
    return out


def hash_files(paths: list[Path]) -> list[str]:
    out = []
    for p in paths:
        try:
            out.append(hashlib.sha1(Path(p).read_bytes()).hexdigest())
        except OSError:
            continue
    return out


def report_started(rep: dict[str, Any] | None) -> float | None:
    """Epoch the run started: ``started`` when the report has it, else its ``timestamp`` stamp."""
    if not rep:
        return None
    if rep.get("started"):
        return float(rep["started"])
    ts = rep.get("timestamp")
    if isinstance(ts, str):
        try:
            return datetime.strptime(ts, "%Y%m%d-%H%M%S").timestamp()
        except ValueError:
            return None
    return None


def load_evidence(report: Path | None = None, status: Path | None = None,
                  driver_log: Path | None = DRIVER_LOG, sdk_log: Path | None = SDK_LOG,
                  launch_log: Path | None = LAUNCH_LOG, game: Path | None = GAME,
                  spec: Path | None = None, scratch: Path = SCRATCH) -> Evidence:
    ev = Evidence()
    if report is None:
        report = latest_report(scratch)
    if report is not None:
        ev.report_path = Path(report)
        ev.report = _read_json(ev.report_path)
        if ev.report is None:
            ev.notes.append(f"report unreadable: {report}")
    params = (ev.report or {}).get("params") or {}
    if status is None and params.get("status"):
        cand = Path(params["status"])
        status = cand if cand.is_absolute() else (REPO / cand)
    if status is not None and Path(status).exists():
        ev.status_path = Path(status)
        recs = _read_json(ev.status_path)
        if isinstance(recs, list):
            started = report_started(ev.report)
            if started:
                recs = [r for r in recs if isinstance(r, dict) and float(r.get("t", 0)) >= float(started) - 5]
            ev.status = [r for r in recs if isinstance(r, dict)]
    if driver_log and Path(driver_log).exists():
        try:
            ev.driver = parse_driver_log(Path(driver_log).read_text(encoding="utf-8", errors="replace"),
                                         started=report_started(ev.report))
        except OSError:
            pass
    if sdk_log and Path(sdk_log).exists():
        try:
            ev.sdk = parse_sdk_log(Path(sdk_log).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
        # unrealsdk.log is rewritten by every launch: only trust it for the run it belongs to
        t0 = report_started(ev.report)
        if t0 is not None and ev.sdk.init_at is not None:
            gap = (ev.sdk.init_at - datetime.fromtimestamp(t0)).total_seconds()
            if gap < -60 or gap > 900:
                ev.notes.append(f"unrealsdk.log is from another launch ({ev.sdk.init_at:%Y-%m-%d %H:%M}); ignored -- pass --sdk-log for this run's copy")
                ev.sdk = SdkSlice()
        if ev.driver.launched_at is not None and t0 is not None:
            dgap = (ev.driver.launched_at - datetime.fromtimestamp(t0)).total_seconds()
            if abs(dgap) > 600:
                ev.notes.append(f"driver.log has no launch near this report (nearest {ev.driver.launched_at:%H:%M:%S}); its lines are ignored")
                ev.driver = DriverSlice()
    ev.launch_log_fatal = scan_launch_log(launch_log)
    if game:
        ev.mods = scan_installed_mods(game)
    # burst hashes (F20): the frames of this run's capture burst
    burst = ((ev.report or {}).get("captures") or {}).get("burst") or []
    ev.burst_hashes = hash_files([Path(p) if Path(p).is_absolute() else REPO / p for p in burst])
    # the spec of the mod under test: explicit, else the installed copy, else sdk_mod/<name>/spec.json
    name = ev.mod_name
    for cand in ([Path(spec)] if spec else []) + (
            [game / "sdk_mods" / name / "spec.json", REPO / "sdk_mod" / name / "spec.json"] if (name and game) else []):
        s = _read_json(cand)
        if isinstance(s, dict):
            ev.spec = s
            break
    return ev
