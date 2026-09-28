"""``emit(spec, out_dir)``: lint a spec, then write the SDK mod folder.

The emitter refuses to write anything while the linter reports an error, because
every one of those errors is a failure mode that looks identical from inside the
game ("installed it, nothing changed"). ``force=True`` overrides, and the reason
is recorded in ``lint.json`` next to the generated code.
"""

from __future__ import annotations

import array
import json
import shutil
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bl2_catalog import load_catalog
from bl2_lint import Report, lint

from .resolve import ResolvedSpec, resolve, to_proposals
from .spec import Spec, load_spec
from .armory_templates import render_armory, render_armory_pyproject, render_armory_readme
from .spec import ArmorySpec, load_armory
from .templates import render_mod, render_pyproject, render_readme, render_settings

__all__ = ["EmitRefused", "EmitResult", "LintedProposal", "emit", "lint_spec"]


class ArmoryConflict(RuntimeError):
    """Two weapons of an Armory would construct or mutate the same object (lint L11)."""


class EmitRefused(RuntimeError):
    """The spec does not lint clean and ``force`` was not given."""

    def __init__(self, message: str, results: list["LintedProposal"]) -> None:
        super().__init__(message)
        self.results = results


@dataclass
class LintedProposal:
    """One part's lint proposal and the report the linter gave it."""

    part_name: str
    proposal: dict[str, Any]
    report: Report

    @property
    def ok(self) -> bool:
        return self.report.ok

    def to_dict(self) -> dict[str, Any]:
        return {"part": self.part_name, "proposal": self.proposal, "report": self.report.to_dict()}


@dataclass
class EmitResult:
    """What :func:`emit` wrote, and what the linter thought of it."""

    name: str
    out_dir: Path
    files: list[Path] = field(default_factory=list)
    lint_results: list[LintedProposal] = field(default_factory=list)
    spec: Spec | None = None
    resolved: ResolvedSpec | None = None
    forced: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def mod_file(self) -> Path:
        return self.out_dir / "__init__.py"

    @property
    def settings_file(self) -> Path:
        return self.out_dir / "settings" / f"{self.name}.json"

    @property
    def lint_ok(self) -> bool:
        return all(result.ok for result in self.lint_results)

    @property
    def lint_errors(self) -> list[str]:
        return [
            f"[{result.part_name}] {entry.check} {entry.message}"
            for result in self.lint_results
            for entry in result.report.errors
        ]

    @property
    def lint_warnings(self) -> list[str]:
        return [
            f"[{result.part_name}] {entry.check} {entry.message}"
            for result in self.lint_results
            for entry in result.report.warnings
        ]


def lint_spec(
    spec: Spec, catalog: dict[str, Any] | None = None, resolved: ResolvedSpec | None = None
) -> list[LintedProposal]:
    """Lint every part of a spec as its own proposal (``docs/LINT_PROPOSAL_SCHEMA.md``)."""
    catalog = catalog if catalog is not None else load_catalog()
    resolved = resolved if resolved is not None else resolve(spec, catalog)
    proposals = to_proposals(resolved)
    return [
        LintedProposal(part_name=part.part_name, proposal=proposal, report=lint(proposal, catalog))
        for part, proposal in zip(resolved.parts, proposals)
    ]


def _catalog_stamp(catalog: dict[str, Any]) -> str:
    meta = catalog.get("meta", {})
    counts = meta.get("counts", {})
    return (
        f"parts.json v{catalog.get('schema_version')} generated {meta.get('generated_utc')} "
        f"({counts.get('parts', '?')} parts, {counts.get('weapon_types', '?')} weapon types)"
    )


def _bake_wav(source: Path | None, volume: int, dest: Path, what: str) -> Path:
    """Copy a 16-bit PCM wav to ``dest`` with ``volume`` (percent) baked into the samples."""
    if source is None or not source.is_file():
        raise EmitRefused(f"{what} not found: {source}", [])
    try:
        with wave.open(str(source), "rb") as reader:
            params = reader.getparams()
            frames = reader.readframes(params.nframes)
    except (wave.Error, EOFError) as ex:
        raise EmitRefused(f"{what} {source} is not a PCM wav: {ex}", []) from ex
    if params.sampwidth != 2:
        raise EmitRefused(f"{what} {source} is {8 * params.sampwidth}-bit; "
                          "the fire sound must be 16-bit PCM", [])
    samples = array.array("h")
    samples.frombytes(frames)
    scale = volume / 100.0
    for i, sample in enumerate(samples):
        v = int(sample * scale)
        samples[i] = 32767 if v > 32767 else (-32768 if v < -32768 else v)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as writer:
        writer.setparams(params)
        writer.writeframes(samples.tobytes())
    return dest


def write_fire_sound(spec: Spec, module_dir: Path) -> Path | None:
    """Copy ``spec.fire_sound.wav`` to ``<module_dir>/sounds/<mod>_fire.wav`` at its volume,
    and ``spec.alt_fire.sound`` (if any) to ``<mod>_alt_fire.wav``.

    The level is baked into the samples here, so the mod plays the copy as-is (the embedded
    wave_mixer could scale at load, but a baked file is what the player hears and can check).
    16-bit PCM only. Returns the fire wav's path (the alt wav sits beside it).
    """
    if spec.fire_sound is None:
        return None
    dest = _bake_wav(spec.resolved_fire_sound_file(), spec.fire_sound.volume,
                     module_dir / "sounds" / f"{spec.name}_fire.wav", "fire_sound.wav")
    if spec.alt_fire is not None and spec.alt_fire.sound:
        volume = spec.alt_fire.volume or spec.fire_sound.volume
        _bake_wav(spec.resolved_alt_fire_sound_file(), volume,
                  module_dir / "sounds" / f"{spec.name}_alt_fire.wav", "alt_fire.sound")
    return dest


def emit(
    spec: Spec | dict[str, Any] | str | Path,
    out_dir: Path | str,
    *,
    catalog: dict[str, Any] | None = None,
    force: bool = False,
    clean: bool = True,
    spec_name: str | None = None,
    sidecar: dict[str, Any] | str | Path | None = None,
) -> EmitResult:
    """Generate the SDK mod folder for ``spec`` into ``out_dir``.

    ``spec`` may be a :class:`~bl2_partgen.spec.Spec`, a dict, or a path to a spec JSON.
    ``sidecar`` is an optional ``.fragment.json`` from the package build, whose
    (possibly several) fragment ranges then win over the spec's.
    Raises :class:`EmitRefused` when the linter finds an error and ``force`` is False.
    """
    if isinstance(spec, (str, Path)):
        spec_name = spec_name or str(spec)
        spec = load_spec(spec)
    elif isinstance(spec, dict):
        spec = Spec.from_dict(spec)
    spec_name = spec_name or "<spec>"

    catalog = catalog if catalog is not None else load_catalog()
    resolved = resolve(spec, catalog, sidecar)
    results = lint_spec(spec, catalog, resolved)

    errors = [
        f"[{r.part_name}] {e.check} {e.message}" for r in results for e in r.report.errors
    ]
    if errors and not force:
        raise EmitRefused(
            "refusing to emit: the linter found "
            f"{len(errors)} error(s):\n  " + "\n  ".join(errors),
            results,
        )

    out_dir = Path(out_dir)
    if clean and out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "settings").mkdir(parents=True, exist_ok=True)

    written: list[Path] = []

    def _write(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)

    _write(out_dir / "__init__.py", render_mod(resolved, spec_name, _catalog_stamp(catalog)))
    _write(out_dir / "pyproject.toml", render_pyproject(resolved))
    _write(out_dir / "settings" / f"{spec.name}.json", render_settings())
    _write(out_dir / "README.md", render_readme(resolved, spec_name))
    _write(
        out_dir / "spec.json",
        json.dumps(spec.to_dict(), indent=1, sort_keys=True) + "\n",
    )
    sound = write_fire_sound(spec, out_dir)
    if sound is not None:
        written.append(sound)
    _write(
        out_dir / "lint.json",
        json.dumps(
            {
                "ok": not errors,
                "forced": bool(errors and force),
                "catalog": _catalog_stamp(catalog),
                "resolve_notes": resolved.notes,
                "results": [r.to_dict() for r in results],
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
    )

    return EmitResult(
        name=spec.name,
        out_dir=out_dir,
        files=written,
        lint_results=results,
        spec=spec,
        resolved=resolved,
        forced=bool(errors and force),
        notes=list(resolved.notes),
    )


# ---------------------------------------------------------------------- the Armory (M10)
@dataclass
class ArmoryEmitResult:
    """What :func:`emit_armory` wrote: one mod folder, one component per weapon."""

    name: str
    out_dir: Path
    armory: ArmorySpec
    weapons: list[EmitResult] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    forced: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def mod_file(self) -> Path:
        return self.out_dir / "__init__.py"

    @property
    def settings_file(self) -> Path:
        return self.out_dir / "settings" / f"{self.name}.json"

    @property
    def lint_ok(self) -> bool:
        return all(w.lint_ok for w in self.weapons)

    @property
    def lint_errors(self) -> list[str]:
        return [f"{w.name}: {e}" for w in self.weapons for e in w.lint_errors]

    @property
    def lint_warnings(self) -> list[str]:
        return [f"{w.name}: {e}" for w in self.weapons for e in w.lint_warnings]


def _armory_paths(resolved: ResolvedSpec) -> dict[str, str]:
    """Every object path a weapon constructs or mutates -> what it is (for L11)."""
    spec = resolved.spec
    paths: dict[str, str] = {spec.package_stem: "package"}
    if spec.options.own_gestalt:
        # its own clone of the definition inside its own package: nothing shared to collide on
        paths[f"{spec.package_stem}.GestaltDef_{spec.name}"] = "own gestalt definition"
    else:
        paths[resolved.gestalt_def_path] = "gestalt definition (re-pointed at this weapon's mesh)"
    for name in resolved.extra_packages:
        paths[name] = "extra package"
    for part in resolved.parts:
        paths[part.part_path] = "part"
    for bal in resolved.balances:
        paths[bal.path] = "balance"
        if bal.title is not None:
            paths[bal.title.path] = "title"
    for mat in resolved.materials:
        paths[mat.path] = "material"
    return paths


def check_armory_conflicts(armory: ArmorySpec, resolved: list[ResolvedSpec]) -> None:
    """L11: no two weapons may construct or mutate the same object path, and each weapon
    needs its own balance (the Armory spawns by balance)."""
    seen: dict[str, tuple[str, str]] = {}
    for weapon, res in zip(armory.weapons, resolved):
        if not res.balances:
            raise ArmoryConflict(
                f"weapon {weapon.id}: its spec has no balances[]; the Armory spawns by "
                "balance, so every weapon needs its own"
            )
        for path, kind in _armory_paths(res).items():
            if path in seen:
                other, other_kind = seen[path]
                # Two weapons built into ONE package (a package group: `bl2_retarget run a b`)
                # legitimately share the package, its extra packages and the gestalt
                # definition -- both re-point it at the same mesh, and fragment names differ.
                shared_ok = (kind == other_kind and kind in ("package", "extra package")) or (
                    kind == other_kind and kind.startswith("gestalt definition")
                    and _shares_package(res, resolved[[w.id for w in armory.weapons].index(other)]))
                if shared_ok:
                    continue
                raise ArmoryConflict(
                    f"L11: weapons {other} and {weapon.id} both touch {path} ({other_kind} / "
                    f"{kind}); two weapons on one host gestalt must ship in one package "
                    "(a package group) so both re-point the gestalt at the same mesh, or set "
                    "options.own_gestalt so each draws from its own"
                )
            seen[path] = (weapon.id, kind)


def _shares_package(a: ResolvedSpec, b: ResolvedSpec) -> bool:
    return a.spec.package_stem == b.spec.package_stem and a.spec.mesh_path == b.spec.mesh_path


def emit_armory(
    armory: ArmorySpec | str | Path,
    out_dir: Path | str,
    *,
    catalog: dict[str, Any] | None = None,
    force: bool = False,
    clean: bool = True,
    spec_name: str | None = None,
) -> ArmoryEmitResult:
    """Generate the Armory mod folder: ``__init__.py`` + ``weapons/<id>.py`` per weapon."""
    if isinstance(armory, (str, Path)):
        spec_name = spec_name or str(armory)
        armory = load_armory(armory)
    spec_name = spec_name or "<armory>"
    catalog = catalog if catalog is not None else load_catalog()

    weapons: list[EmitResult] = []
    resolved_all: list[ResolvedSpec] = []
    errors: list[str] = []
    for weapon in armory.weapons:
        spec = load_spec(weapon.spec)
        resolved = resolve(spec, catalog, str(weapon.sidecar) if weapon.sidecar else None)
        results = lint_spec(spec, catalog, resolved)
        weapons.append(EmitResult(
            name=spec.name, out_dir=Path(out_dir) / "weapons", lint_results=results,
            spec=spec, resolved=resolved, notes=[f"{weapon.id}: {n}" for n in resolved.notes],
        ))
        resolved_all.append(resolved)
        errors += [f"{weapon.id}: [{r.part_name}] {e.check} {e.message}"
                   for r in results for e in r.report.errors]
    if errors and not force:
        raise EmitRefused(
            "refusing to emit: the linter found "
            f"{len(errors)} error(s):\n  " + "\n  ".join(errors),
            [r for w in weapons for r in w.lint_results],
        )
    check_armory_conflicts(armory, resolved_all)

    out_dir = Path(out_dir)
    if clean and out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "settings").mkdir(parents=True, exist_ok=True)
    (out_dir / "weapons").mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def _write(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)

    stamp = _catalog_stamp(catalog)
    _write(out_dir / "weapons" / "__init__.py",
           '"""One module per weapon, rendered by bl2_partgen in component mode."""\n')
    for weapon, result in zip(armory.weapons, weapons):
        assert result.resolved is not None
        _write(out_dir / "weapons" / f"{weapon.id}.py",
               render_mod(result.resolved, str(weapon.spec), stamp, component=True))
        assert result.spec is not None
        sound = write_fire_sound(result.spec, out_dir / "weapons")
        if sound is not None:
            written.append(sound)
    _write(out_dir / "__init__.py", render_armory(armory, resolved_all, spec_name))
    _write(out_dir / "pyproject.toml", render_armory_pyproject(armory))
    _write(out_dir / "settings" / f"{armory.name}.json", render_settings())
    _write(out_dir / "README.md", render_armory_readme(armory, resolved_all, spec_name))
    _write(
        out_dir / "spec.json",
        json.dumps(
            {"armory": armory.to_dict(),
             "weapons": {w.id: r.spec.to_dict() for w, r in zip(armory.weapons, weapons)
                         if r.spec is not None}},
            indent=1, sort_keys=True,
        ) + "\n",
    )
    _write(
        out_dir / "lint.json",
        json.dumps(
            {"ok": not errors, "forced": bool(errors and force), "catalog": stamp,
             "weapons": {w.id: {"resolve_notes": r.resolved.notes if r.resolved else [],
                                "results": [x.to_dict() for x in r.lint_results]}
                         for w, r in zip(armory.weapons, weapons)}},
            indent=1, sort_keys=True,
        ) + "\n",
    )
    return ArmoryEmitResult(
        name=armory.name, out_dir=out_dir, armory=armory, weapons=weapons, files=written,
        forced=bool(errors and force), notes=[n for w in weapons for n in w.notes],
    )
