"""``bl2_partgen`` -- the SDK emitter: a weapon spec becomes a mod folder.

Turns a *spec* (``docs/PARTGEN_SPEC.md``) into a readable, commented PythonSDK mod
folder that registers new gestalt fragments and ``WeaponPartDefinition``s at runtime
and keeps them alive across a save/quit/reload.

The generated mod is the generic form of ``src/bl2_verify/m2_mod_template.py``, which
was verified in game end to end: menu-time registration (F16), GC rooting (F17), the
save round-trip (F18) and the validation override (F19).

Typical use::

    from bl2_partgen import emit, install, load_spec
    result = emit("specs/bent_barrel.json", "sdk_mod/PipelineBentBarrel")
    install(result, r"C:\\Games\\Borderlands 2")      # never against the real game in tests

CLI::

    python -m bl2_partgen specs/bent_barrel.json --out sdk_mod/PipelineBentBarrel
    python -m bl2_partgen specs/bent_barrel.json --out sdk_mod/X --install "<game>" --replace
    python -m bl2_partgen specs/bent_barrel.json --lint-only

Every part of the spec is linted with :mod:`bl2_lint` first and the emitter refuses to
write while any error stands; ``--force`` overrides and records that it did.
"""

from __future__ import annotations

from .emit import (
    ArmoryConflict,
    ArmoryEmitResult,
    EmitRefused,
    EmitResult,
    LintedProposal,
    emit,
    emit_armory,
    lint_spec,
)
from .install import InstallRefused, InstallResult, install, install_armory
from .resolve import ResolveError, ResolvedSpec, resolve, to_proposals
from .spec import (
    ArmorySpec,
    ArmoryWeapon,
    FragmentSpec,
    Options,
    PartSpec,
    RegisterTarget,
    SPEC_SCHEMA_VERSION,
    Spec,
    SpecError,
    is_armory,
    load_armory,
    load_spec,
)

__all__ = [
    "ArmoryConflict",
    "ArmoryEmitResult",
    "ArmorySpec",
    "ArmoryWeapon",
    "EmitRefused",
    "EmitResult",
    "FragmentSpec",
    "InstallRefused",
    "InstallResult",
    "LintedProposal",
    "Options",
    "PartSpec",
    "RegisterTarget",
    "ResolveError",
    "ResolvedSpec",
    "SPEC_SCHEMA_VERSION",
    "Spec",
    "SpecError",
    "emit",
    "emit_armory",
    "install",
    "install_armory",
    "is_armory",
    "load_armory",
    "lint_spec",
    "load_spec",
    "resolve",
    "to_proposals",
]
