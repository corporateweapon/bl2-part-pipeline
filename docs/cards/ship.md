# Ship: the Armory, releases

## The public Armory (1.0): runtime + weapon packs

```
python bl2.py pack <pack id> [--install]   # packs/<id>.json -> dist/packs/ArmoryPack-<id>-<ver>.zip
python bl2.py armory [--pack id] [--install]   # dist/Armory-<ver>.zip: runtime + default pack(s)
```

The shareable Armory is **not** emitted from `specs/armory.json`. It's the hand-written runtime
in `armory/Armory/__init__.py`, plus a copy of the renderer (`spec`, `resolve`, `templates`,
`alt_fire_template`, `rage_template`, `packdata` from `src/bl2_partgen`, copied to
`Armory/_render/` at release time). At startup it reads every
`sdk_mods/ArmoryPacks/<id>/armory_pack.json`, checks it, renders each weapon's component with
`render_mod(component=True)` and runs it as `Armory.packs.<pack>.<weapon>`. For every weapon the
rendered text is identical to what `emit_armory` writes (`tests/test_armory_packs.py` pins this
for every spec in `specs/`). A pack is data only: per weapon, the spec and `resolved_to_dict()`
of its resolved form (`src/bl2_partgen/packdata.py`), plus the `.upk` files. Player docs:
`armory/Armory/README.md`. Creator docs: `armory/CREATING_PACKS.md`. The default pack is the Boxgun (`packs/boxgun.json`).

The runtime refuses, with the reason, in `armory packs` and Mods → Armory → Packs: a newer
schema, `armory_min` or feature; a name that could escape into code; an object another pack
owns; a package a `sdk_mods/Pipeline*` mod also registers (rule 3, enforced at runtime); a
missing `.upk` or one with the wrong SHA1. Weapon hooks are on while the Armory is installed.
They are not tied to the mod-menu toggle, which only gates F5. Component status files are
written to `Armory/logs/<MOD_NAME>.status.json` (`armory_check --mod Armory` reads them there).
Before installing it, park `PipelineArmory` and every other build of a packed weapon.

## The development Armory (PipelineArmory)

```
python bl2.py ship <id> --label "Name"            # add to specs/armory.json, emit PipelineArmory
python bl2.py ship <id> --install --check         # + install, armory_check (registered / spawned / equipped / survives_reload), triage
```

The Armory (`specs/armory.json` → `sdk_mods/PipelineArmory`) is the shape players get: one SDK
mod, every weapon a component (`weapons/<id>.py`), console `armory list | spawn <id> [--level N]
| census`, a menu page, F5. A weapon goes in **after** it passed its own acceptance; the Armory
adds nothing and fixes nothing. Never enable the Armory beside a harness or player build of one
of its weapons (double registration): park them in `sdk_mods/_disabled/` with their settings JSON.
Lint L11 refuses two weapons that point one host gestalt at two different packages, unless they
use `options.own_gestalt` (every Armory weapon does since 2026-09-23; see retarget.md).

Save records live in `sdk_mods/_pipeline_saves/<package>/<save>.json`, merged on save and
retired (not deleted) on load (F29). A save opened without the mod loses the weapons; every
multiplayer client needs the mod.

**What may ship.** Only models, textures and sounds you have the right to redistribute. The
**Boxgun** (`examples/boxgun/`, CC0, built by `make_boxgun.py`) is the released example.

Release checklist, weapon: non-harness spec only (grep the emitted `__init__.py` for `_write(`,
F-key hooks, view holds — none may ship), version bumped, licence text for the model, requirements
(pyunrealsdk version, game build), install steps (`.upk` files → `CookedPCConsole`, mod folder →
`sdk_mods`, enable, reach the main menu before loading), save and multiplayer warnings, how to
obtain it (pool + console line), uninstall steps, screenshots.

Release checklist, pipeline: `pip install -e .`, `python -m bl2_blender.packaging` for the Blender
extension zip, Gildor's binaries linked not bundled, `bl2 doctor` clean on a fresh clone, tests green
(`.github/workflows/tests.yml` runs the offline suite and a dry Boxgun build).
