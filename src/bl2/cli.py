"""``bl2``: the pipeline's front door.

    bl2 doctor [--fix]                   the machine: game, SDK, Blender, tools, decompressed Startup, catalog
    bl2 weapons                          every weapon id the repo knows, and what each is missing
    bl2 new <id> --from boxgun --glb <path> [--label "Name"] [--prefix MYGUN]
    bl2 measure <id>                     retarget --measure-only + summarize (labelled preview with valleys)
    bl2 build <id> [--skip-render]       retarget -> apply-spec -> lint+emit every spec -> offline dry run
                                         (a member of packages/<Name>.json builds the whole package group)
    bl2 install <id> [--variant harness] emit+install one variant into the game (then preflight)
    bl2 verify <id> [--run-index N]      preflight (NO GO stops) -> run_loop -> triage
    bl2 ship <id> [--label] [--install] [--check]   add to the Armory, emit, optionally install + armory_check
    bl2 pack <pack id> [--install]       build a weapon pack for the Armory from packs/<id>.json (dist/packs/)
    bl2 armory [--pack id] [--install]   build the Armory release zip with its default pack(s) (dist/)
    bl2 status                           what is installed/enabled, the latest run's verdict
    bl2 preflight ... / bl2 triage ... / bl2 retarget ...   pass-through to the modules

``--dry`` before the command prints the chain instead of running it. Every step is one of
the existing modules run as ``python -m <module>`` with ``PYTHONPATH=src``; this file only
knows the conventions (:mod:`bl2.weapon`) and the order.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from bl2.scaffold import scaffold
from bl2.weapon import Weapon, list_weapons, repo_root

GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Borderlands 2")
BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")


class Runner:
    def __init__(self, root: Path, dry: bool = False) -> None:
        self.root = root
        self.dry = dry
        self.env = dict(os.environ)
        self.env["PYTHONPATH"] = str(root / "src") + os.pathsep + self.env.get("PYTHONPATH", "")
        self.env.setdefault("BL2_PIPELINE_ROOT", str(root))

    def module(self, module: str, *args: str, check: bool = True, quiet: bool = False) -> int:
        cmd = [sys.executable, "-m", module, *args]
        return self.run(cmd, check=check, quiet=quiet)

    def script(self, rel: str, *args: str, check: bool = True) -> int:
        return self.run([sys.executable, str(self.root / rel), *args], check=check)

    def run(self, cmd: list[str], check: bool = True, quiet: bool = False) -> int:
        shown = " ".join(_q(c) for c in cmd)
        print(f"$ {shown}", flush=True)
        if self.dry:
            return 0
        res = subprocess.run(cmd, cwd=str(self.root), env=self.env, check=False,
                             capture_output=quiet, text=quiet)
        if quiet and res.returncode != 0:
            print((res.stdout or "") + (res.stderr or ""))
        if check and res.returncode != 0:
            raise StepFailed(f"{cmd[2] if len(cmd) > 2 else cmd[0]} exited {res.returncode}")
        return res.returncode


class StepFailed(RuntimeError):
    pass


def _q(s: str) -> str:
    return f'"{s}"' if " " in s else s


def _weapon(args: argparse.Namespace, root: Path) -> Weapon:
    w = Weapon(args.weapon, root)
    if not w.exists():
        known = ", ".join(list_weapons(root)) or "none"
        raise StepFailed(f"unknown weapon {args.weapon!r} (known: {known}); `bl2 new {args.weapon} --from boxgun` starts one")
    return w


# --------------------------------------------------------------------------- doctor
def cmd_doctor(a: argparse.Namespace, root: Path, r: Runner) -> int:
    rows: list[tuple[str, str, str]] = []

    def row(name: str, ok: bool | None, detail: str, fix: str = "") -> None:
        rows.append((name, "ok " if ok else ("warn" if ok is None else "FAIL"), detail + (f"  -> {fix}" if fix and not ok else "")))

    game = Path(a.game)
    row("game", (game / "Binaries/Win32/Borderlands2.exe").exists(), str(game), "pass --game")
    sdk = all((game / "Binaries/Win32" / p).exists() for p in ("Plugins/unrealsdk.dll", "Plugins/pyunrealsdk.dll", "ddraw.dll"))
    row("pyunrealsdk", sdk, "Binaries\\Win32\\Plugins", "install the willow2 mod manager")
    row("sdk_mods", (game / "sdk_mods").exists(), str(game / "sdk_mods"))
    row("blender", Path(a.blender).exists(), str(a.blender), "install Blender 5.1 or pass --blender")
    for mod, why in (("numpy", "package codec"), ("PIL", "DDS textures, captures, previews")):
        try:
            __import__(mod)
            row(f"python {mod}", True, why)
        except ImportError:
            row(f"python {mod}", False, why, f"pip install {'Pillow' if mod == 'PIL' else mod}")
    row("gildor decompress", (root / "tools/gildor/decompress.exe").exists(), "tools/gildor/decompress.exe", "download from gildor.org into tools/gildor/")
    row("umodel", (root / "tools/gildor/umodel_64.exe").exists() or (root / "tools/gildor/umodel.exe").exists(), "tools/gildor/umodel", "download from gildor.org")
    startup = root / "scratch/decomp/Startup.upk"
    src_startup = game / "WillowGame/CookedPCConsole/Startup.upk"
    row("decompressed Startup", startup.exists(), str(startup), "bl2 doctor --fix (runs decompress)")
    catalog = root / "catalog/parts.json"
    row("catalog", catalog.exists(), str(catalog), "python -m bl2_catalog (needs scratch/oe dumps)")
    row("OpenBLCMM dumps", (root / "scratch/oe").exists(), "scratch/oe", "export from the OpenBLCMM Object Explorer (only for a new host type or a catalog rebuild)")
    row("sniper fragment table", (root / "scratch/gestalt_SR_fragments.txt").exists(), "scratch/gestalt_SR_fragments.txt (sniper host recipes)", "dump it from the OpenBLCMM Object Explorer (docs/cards/retarget.md)")
    row("Source2Viewer-CLI", (root / "tools" / "vrf" / "Source2Viewer-CLI.exe").exists(), "optional: only for Source 2 textures")
    backup = root / "backup/CookedPCConsole/Startup.upk"
    row("Startup backup", backup.exists(), str(backup), "copy the pristine Startup.upk there (F12 reference)")
    fakes = (root / "tests/fakes/unrealsdk").exists()
    row("offline fakes", fakes, "tests/fakes (dry runs)")

    width = max(len(n) for n, _, _ in rows)
    for name, status, detail in rows:
        print(f"  {status} {name:<{width}}  {detail}")
    fails = [n for n, s, _ in rows if s == "FAIL"]
    if a.fix and not startup.exists() and (root / "tools/gildor/decompress.exe").exists() and src_startup.exists():
        (root / "scratch/decomp").mkdir(parents=True, exist_ok=True)
        r.run([str(root / "tools/gildor/decompress.exe"), "-game=border", f"-out={root / 'scratch/decomp'}", str(src_startup)])
        fails = [f for f in fails if f != "decompressed Startup"]
    print(f"doctor: {'READY' if not fails else 'NOT READY: ' + ', '.join(fails)}")
    return 0 if not fails else 2


# --------------------------------------------------------------------------- weapons / new
def cmd_weapons(a: argparse.Namespace, root: Path, r: Runner) -> int:
    ids = list_weapons(root)
    if not ids:
        print("no weapons yet: bl2 new <id> --from boxgun --glb <model.glb>")
        return 0
    for wid in ids:
        print("\n".join(Weapon(wid, root).describe()))
    return 0


def cmd_new(a: argparse.Namespace, root: Path, r: Runner) -> int:
    if r.dry:
        print(f"$ scaffold {a.weapon} from {a.source} (prefix {a.prefix or a.weapon.upper()})")
        return 0
    res = scaffold(root, a.weapon, a.source, a.glb, a.label, a.prefix, force=a.force)
    print("written: " + ", ".join(res["written"]))
    for n in res["notes"]:
        print(f"  {n}")
    print("to do:")
    for t in res["todo"]:
        print(f"  - {t}")
    print(f"then: bl2 measure {a.weapon}")
    return 0


# --------------------------------------------------------------------------- measure / build
def cmd_measure(a: argparse.Namespace, root: Path, r: Runner) -> int:
    w = _weapon(a, root)
    r.module("bl2_retarget", "check", str(w.recipe))
    r.module("bl2_retarget", "run", str(w.recipe), "--measure-only", "--blender", a.blender)
    r.module("bl2_retarget", "summarize", str(w.report))
    return 0


def cmd_build(a: argparse.Namespace, root: Path, r: Runner) -> int:
    w = _weapon(a, root)
    group = w.group()
    members = [Weapon(i, root) for i in group["recipes"]] if group else [w]
    for m in members:
        r.module("bl2_retarget", "check", str(m.recipe))
    extra = ["--skip-render"] if a.skip_render else []
    run = ["bl2_retarget", "run", *[str(m.recipe) for m in members], "--blender", a.blender, *extra]
    if group:
        run += ["--out", str(w.package), "--package-name", group["package_name"], "--mesh-name", group.get("mesh_name", "PL_AR_Gestalt_Mesh")]
        print(f"package group {group['_name']}: {' + '.join(group['recipes'])} -> {w.package}")
    r.module(*run)
    side = w.sidecar
    for m in members:
        r.module("bl2_retarget", "summarize", str(m.report))
        specs = [str(m.spec(v)) for v in m.variants()]
        if specs:
            r.module("bl2_retarget", "apply-spec", str(m.report), *specs)
        for v in m.variants():
            mod = m.mod_name(v)
            args = ["bl2_partgen", str(m.spec(v)), "--out", str(root / "sdk_mod" / (mod or f"Pipeline{m.id}"))]
            if side and (side.exists() or r.dry):
                args += ["--sidecar", str(side)]
            r.module(*args)
        if "harness" in m.variants() and m.mod_dir("harness"):
            r.module("bl2_preflight", "dry-run", str(m.mod_dir("harness")))
    print(f"built {', '.join(m.id for m in members)}: next `bl2 install {w.id}` then `bl2 verify {w.id}`")
    return 0



# --------------------------------------------------------------------------- install / verify
def cmd_install(a: argparse.Namespace, root: Path, r: Runner) -> int:
    w = _weapon(a, root)
    v = a.variant
    if v not in w.variants() and not r.dry:
        raise StepFailed(f"{w.id} has no {v} spec ({w.spec(v)})")
    mod = w.mod_name(v) or f"Pipeline{w.id}"
    args = ["bl2_partgen", str(w.spec(v)), "--out", str(root / "sdk_mod" / mod), "--install", a.game, "--replace"]
    side = w.sidecar
    if side and (side.exists() or r.dry):
        args += ["--sidecar", str(side)]
    r.module(*args)
    r.module("bl2_preflight", "--mod", mod, "--game", a.game, "--dry-run", check=False)
    print(f"installed {mod}; other pipeline mods on the same host gestalt must be parked (see preflight above)")
    return 0


def cmd_verify(a: argparse.Namespace, root: Path, r: Runner) -> int:
    w = _weapon(a, root)
    mod = w.mod_name("harness")
    if not mod and not r.dry:
        raise StepFailed(f"{w.id} has no harness spec; the loop drives the harness build")
    mod = mod or f"Pipeline{w.id}Harness"
    if not r.dry:
        w.write_control("harness")
        if w.write_proposal("harness") is None:
            print(f"  (no lint.json under sdk_mod/{mod}; lint_clean will be skipped -- run `bl2 build {w.id}` first)")
    part = w.part_path("harness") or "<part path>"
    pre = ["bl2_preflight", "--mod", mod, "--part-path", part, "--control", str(w.control),
           "--capture-view", a.capture_view, "--game", a.game]
    if w.baseline.exists() or r.dry:
        pre += ["--baseline", str(w.baseline)]
    code = r.module(*pre, check=False)
    if code == 2 and not a.force:
        raise StepFailed("preflight said NO GO; fix it or pass --force")
    loop = ["src/bl2_verify/run_loop.py", "--skip-install", "--mod", mod, "--status", str(w.status_file("harness")),
            "--control", str(w.control), "--part-path", part, "--runs", str(w.runs),
            "--capture-view", a.capture_view]
    if w.proposal.exists() or r.dry:
        loop += ["--proposal", str(w.proposal)]
    if w.baseline.exists() or r.dry:
        loop += ["--baseline", str(w.baseline)]
    if a.run_index is not None:
        loop += ["--run-index", str(a.run_index)]
    if w.package_name:
        loop += ["--package", w.package_name]
    if getattr(a, "input_test", None):
        loop += ["--input-test", a.input_test, "--input-delay", str(a.input_delay)]
    r.script(*loop, check=False)
    return r.module("bl2_triage", "--game", a.game, check=False)


# --------------------------------------------------------------------------- ship / status
def cmd_ship(a: argparse.Namespace, root: Path, r: Runner) -> int:
    w = _weapon(a, root)
    armory = root / "specs" / "armory.json"
    data = json.loads(armory.read_text(encoding="utf-8")) if armory.exists() else {
        "mod": {"name": "PipelineArmory", "author": "44M0N", "version": "0.1.0", "description": "The pipeline weapons in one SDK mod."},
        "weapons": [], "spawn": {"keybind": "F5", "mission": "GD_Episode01.M_Ep1_Champion"}}
    ids = [x.get("id") for x in data.get("weapons") or []]
    side = w.sidecar
    if w.id not in ids:
        entry = {"id": w.id, "spec": w.spec("player").name, "label": a.label or w.id.upper(),
                 "sidecar": f"../scratch/{side.name}" if side else None}
        if not r.dry:
            data.setdefault("weapons", []).append(entry)
            armory.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        print(f"armory: added {entry}")
    else:
        print(f"armory: {w.id} already listed")
    args = ["bl2_partgen", str(armory), "--out", str(root / "sdk_mod" / "PipelineArmory")]
    if a.install:
        args += ["--install", a.game, "--replace"]
    r.module(*args)
    if a.check:
        all_ids = [x["id"] for x in (data.get("weapons") or [])] if not r.dry else [w.id]
        code = r.module("bl2_preflight", "--mod", "PipelineArmory", "--game", a.game, check=False)
        if code == 2 and not a.force:
            raise StepFailed("preflight said NO GO; fix it or pass --force")
        r.script("src/bl2_verify/armory_check.py", "--mod", "PipelineArmory", "--weapons", *all_ids,
                 "--run-index", str(a.run_index or 1), check=False)
        r.module("bl2_triage", "--game", a.game, check=False)
    elif a.install:
        print("installed the Armory; park every other pipeline mod, then `bl2 ship <id> --check`")
    return 0


def cmd_pack(a: argparse.Namespace, root: Path, r: Runner) -> int:
    manifest = root / "packs" / f"{a.pack}.json"
    if not manifest.exists():
        raise StepFailed(f"no pack manifest {manifest}; copy packs/boxgun.json and edit it "
                         "(docs: armory/CREATING_PACKS.md)")
    args = ["bl2_partgen.pack", "pack", str(manifest), "--out", str(root / "dist" / "packs")]
    if a.no_packages:
        args.append("--no-packages")
    if a.install:
        args += ["--install", a.game]
    return r.module(*args)


def cmd_armory(a: argparse.Namespace, root: Path, r: Runner) -> int:
    args = ["bl2_partgen.pack", "release", "--out", str(root / "dist")]
    for pack in a.pack or ["boxgun"]:
        args += ["--pack", str(root / "packs" / f"{pack}.json")]
    if a.no_packages:
        args.append("--no-packages")
    if a.install:
        args += ["--install", a.game]
    return r.module(*args)


def cmd_status(a: argparse.Namespace, root: Path, r: Runner) -> int:
    r.module("bl2_preflight", "--game", a.game, check=False)
    print()
    return r.module("bl2_triage", "--game", a.game, check=False)


def cmd_passthrough(module: str):
    def run(a: argparse.Namespace, root: Path, r: Runner) -> int:
        return r.module(module, *a.rest, check=False)
    return run


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bl2", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry", action="store_true", help="print the command chain, run nothing")
    p.add_argument("--root", default=None, help="repo root (default: this checkout, or $BL2_PIPELINE_ROOT)")
    p.add_argument("--game", default=str(GAME))
    p.add_argument("--blender", default=str(BLENDER))
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="check the machine")
    d.add_argument("--fix", action="store_true", help="decompress Startup.upk if it is missing")
    d.set_defaults(fn=cmd_doctor)

    sub.add_parser("weapons", help="list the weapons the repo knows").set_defaults(fn=cmd_weapons)

    n = sub.add_parser("new", help="scaffold a weapon from a worked example")
    n.add_argument("weapon")
    n.add_argument("--from", dest="source", default="boxgun", help="the worked example to copy (default: boxgun, assault-rifle host); any weapon id in recipes/ works")
    n.add_argument("--glb", default=None)
    n.add_argument("--label", default=None, help="card name, e.g. \"M4A1\"")
    n.add_argument("--prefix", default=None, help="fragment/part prefix (default: ID upper-cased)")
    n.add_argument("--force", action="store_true")
    n.set_defaults(fn=cmd_new)

    for name, fn, extra in (("measure", cmd_measure, ()), ("build", cmd_build, ("--skip-render",))):
        s = sub.add_parser(name)
        s.add_argument("weapon")
        for e in extra:
            s.add_argument(e, action="store_true")
        s.set_defaults(fn=fn)

    i = sub.add_parser("install")
    i.add_argument("weapon")
    i.add_argument("--variant", default="harness", choices=("harness", "player", "spawn"))
    i.set_defaults(fn=cmd_install)

    v = sub.add_parser("verify")
    v.add_argument("weapon")
    v.add_argument("--run-index", type=int, default=None)
    v.add_argument("--capture-view", default="first", choices=("first", "third"))
    v.add_argument("--force", action="store_true", help="launch even on a NO GO preflight")
    v.add_argument("--input-test", default=None, metavar="lmb,rmb,...",
                   help="after the scored capture press these real mouse buttons (run_loop --input-test)")
    v.add_argument("--input-delay", type=float, default=0.0,
                   help="seconds before the first --input-test press")
    v.set_defaults(fn=cmd_verify)

    s = sub.add_parser("ship")
    s.add_argument("weapon")
    s.add_argument("--label", default=None)
    s.add_argument("--install", action="store_true")
    s.add_argument("--check", action="store_true", help="run armory_check after installing")
    s.add_argument("--run-index", type=int, default=None)
    s.add_argument("--force", action="store_true", help="run the check even on a NO GO preflight")
    s.set_defaults(fn=cmd_ship)

    pk = sub.add_parser("pack", help="build a weapon pack (dist/packs/) from packs/<id>.json")
    pk.add_argument("pack")
    pk.add_argument("--no-packages", action="store_true", help="check build without the .upk files")
    pk.add_argument("--install", action="store_true", help="also extract it into --game")
    pk.set_defaults(fn=cmd_pack)

    ar = sub.add_parser("armory", help="build the Armory release zip (dist/) with its default pack(s)")
    ar.add_argument("--pack", action="append", default=[], help="pack id to bundle (default: boxgun)")
    ar.add_argument("--no-packages", action="store_true", help="check build without the .upk files")
    ar.add_argument("--install", action="store_true", help="also extract it into --game")
    ar.set_defaults(fn=cmd_armory)

    sub.add_parser("status").set_defaults(fn=cmd_status)
    for name, module in (("preflight", "bl2_preflight"), ("triage", "bl2_triage"), ("retarget", "bl2_retarget"),
                         ("partgen", "bl2_partgen"), ("lint", "bl2_lint")):
        pt = sub.add_parser(name, help=f"pass-through to python -m {module}")
        pt.add_argument("rest", nargs=argparse.REMAINDER)
        pt.set_defaults(fn=cmd_passthrough(module))
    return p


def main(argv: list[str] | None = None) -> int:
    a, unknown = build_parser().parse_known_args(argv)
    if hasattr(a, "rest"):
        a.rest = [*a.rest, *unknown]        # pass-through commands take every remaining flag
    elif unknown:
        build_parser().error(f"unrecognized arguments: {' '.join(unknown)}")
    root = Path(a.root).resolve() if a.root else repo_root()
    r = Runner(root, dry=a.dry)
    try:
        return a.fn(a, root, r)
    except StepFailed as ex:
        print(f"bl2: {ex}")
        return 2
    except (FileExistsError, FileNotFoundError) as ex:
        print(f"bl2: {ex}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
