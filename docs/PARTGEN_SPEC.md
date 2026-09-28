# Partgen spec schema (v1)

A *spec* is the JSON `bl2_partgen` turns into an SDK mod folder. It is a superset of the lint
proposal (`docs/LINT_PROPOSAL_SCHEMA.md`): the same mesh / fragment / part facts, plus the mod's
identity and the runtime options the generated code bakes in. One spec can carry several
fragments and several parts; the emitter lints **each part as its own proposal** and refuses to
write anything while any of them errors.

Machine-readable form: `src/bl2_partgen/spec.py` (`Spec`, `FragmentSpec`, `PartSpec`, `Options`).
The committed M2 spec: `specs/bent_barrel.json`.

```jsonc
{
  "schema_version": 1,
  "mod": {
    "name": "PipelineBentBarrel",          // folder name under sdk_mods/, must be alphanumeric
    "author": "44M0N",
    "version": "0.3.0",
    "description": "one line or a paragraph; it becomes the module docstring"
  },
  "package":      "PipelineMeshes",        // the NEW .upk (with or without the suffix)
  "mesh_path":    "PipelineMeshes.PL_AR_Gestalt_Mesh",
  "package_file": "../scratch/PipelineMeshes_m2.upk",   // optional, for install; relative to the spec
  "weapon_type":  "AssaultRifle",          // catalog key -> gestalt def path + fragment table
  "fragments": [
    {
      "name":              "AR_Barrel_PL_Bent",
      "template_fragment": "AR_Barrel_Vladof",
      "first_index":       68085,
      "num_primitives":    1164,
      "dz":                8.0,
      "sockets":           ["Muzzle", "EyeSocket2", "FrontSight"],   // or "all"
      "dz_sockets":        ["Muzzle"]
    }
  ],
  "parts": [
    {
      "part_name":     "AR_Barrel_PL_Bent",
      "slot":          "WP_Barrel",
      "outer":         "GD_Weap_AssaultRifle.Barrel",
      "fragment":      "AR_Barrel_PL_Bent",
      "template_part": "GD_Weap_AssaultRifle.Barrel.AR_Barrel_Vladof_Shredifier",
      "register_in": [
        { "balance": "GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_Sherdifier",
          "field":   "BarrelPartData" }
      ]
    }
  ],
  "options": {
    "register_at": "menu",
    "keep_alive": true,
    "save_roundtrip": true,
    "validate_override": true,
    "test_harness": false,
    "harness_auto": true,                  // only read when test_harness is true
    "harness_fov": 100,
    "harness_behindview": true,
    "force_template_fragment_on_map_load": "AR_Barrel_Vladof",
    "menu_ticks": 600,
    "status_path": null                    // absolute, or null for <mod>/status.json
  }
}
```

## Fields

### `mod`

| Field | Req | Meaning |
|---|---|---|
| `name` | yes | The mod folder name (`sdk_mods/<name>/`), the settings file name and the name `build_mod` gets. Alphanumeric plus `_`/`-`. |
| `author`, `version` | no | Copied into `__author__` / `__version__` and `pyproject.toml`. |
| `description` | no | Becomes the module docstring's opening paragraph; the first line (trimmed to 70 chars) becomes the `build_mod` description. |

### Top level

| Field | Req | Meaning |
|---|---|---|
| `package` | yes | Name of the new `.upk` the pipeline writes into `CookedPCConsole`. Also the runtime `load_package` argument, because the game keys on the file name (D3). |
| `mesh_path` | yes | Full object path of the `SkeletalMesh` inside it. |
| `weapon_type` | yes | Catalog key (`AssaultRifle`, `Pistol`, …). Resolves the gestalt definition path, the stock mesh path and the fragment table the templates come from. |
| `package_file` | no | Where the built `.upk` is on disk. Relative paths resolve against the spec's own directory. `install()` copies it; without it, install writes the mod only and says so. |

### `fragments[]`

| Field | Req | Meaning |
|---|---|---|
| `name` | yes | The new gestalt fragment name. Must not already exist in that weapon type (L2). |
| `template_fragment` | yes | Existing fragment it is cloned from: supplies the table row, the bounds and the socket mappings. |
| `first_index` / `num_primitives` | yes | The new fragment's index-buffer range, exactly as the writer's `.fragment.json` sidecar reports it. Ranges must not overlap each other (checked in the spec) or an existing fragment (L4). |
| `dz` | no | Vertical offset of the D7 standard deformation. Raises the bounds box by `dz/2` and the sphere radius by `dz`, and moves each `dz_sockets` socket by `dz`. Default `0.0` (no bounds edit at all). |
| `sockets` | no | **Original** (unmangled) socket names to clone, or `"all"` to take every socket the template maps. The gestalt system mangles them to `<fragment>_<socket>` (D2). Default `"all"`. |
| `dz_sockets` | no | Which of those move by `dz`. Default `["Muzzle"]`; names not in `sockets` are dropped. |
| `bounds_override` | no | `{origin: [x,y,z], extent: [ex,ey,ez], radius}` in mesh space (M8): the fragment's `ReferencePoseBounds` instead of a clone of the template's. The template's box is the template *geometry's* (the Shredifier barrel reaches y = −104), and the item-card preview frames the weapon from these boxes, so a fragment of a different shape needs its own. `dz` is not applied on top. Compute from the built mesh: axis-aligned box of the fragment's vertices, radius = half-diagonal. |

### `parts[]`

| Field | Req | Meaning |
|---|---|---|
| `part_name` | yes | Leaf name of the `WeaponPartDefinition` constructed at runtime. |
| `fragment` | yes, or `null` | Which of this spec's fragments it points at (`GestaltModeSkeletalMeshName`). **`null` means the part draws nothing**: it is cloned from a non-gestalt template (`AR_Sight_None`, `AR_Accessory_None`) and the generated mod keeps `bIsGestaltMode` False on the clone, so no fragment is added or looked up. That is how a spec claims a slot it wants empty (M6: the AK's rear sight is body geometry, so a stock scope rolling into `SightPartData` would sit on top of it). Such a part must name its `template_part`, and it cannot be `parts[0]` of a harness spec. The linter sees it as a `part_only` proposal (L3/L6/L7/L8 apply; L2/L4/L5 have nothing to check). |
| `slot` | no | `PartType` (`WP_Barrel`, `WP_Body`, …). Defaults to the template part's slot. |
| `outer` | no | Object path the part is constructed under. Defaults to the group most catalog parts of this weapon type + slot live in (`GD_Weap_AssaultRifle.Barrel`), falling back to the template part's own outer. The part path is `<outer>.<part_name>`. |
| `template_part` | no | Part the new one is cloned from, so stats/material/attributes come for free. Defaults to the catalog part that uses `template_fragment` in this weapon type and slot. |
| `register_in[]` | yes, unless a `balances[]` entry lists the part | `{balance, field}`: the `WeaponBalanceDefinition` whose **runtime** part-list collection gets the part, and the slot field in it (`BarrelPartData`, …). The emitter resolves the balance to its runtime collection for the linter; the generated mod reaches it as `balance.RuntimePartListCollection.<field>.WeightedParts`. A part in no list never rolls (F5). |
| `overrides` | no | Stat edits on the clone (M7). `properties` = `{name: scalar}` set with `setattr` (`bIsSpinningEnabled`, `NumPhysicalBarrelsToFireFrom`); `object_properties` = `{name: object path}` set with `find_object` + `setattr` and rooted (`Material` on a material part → a `materials[]` MIC); `clear_arrays` = `[name, …]` array properties emptied on the clone (`PrefixList` on a grip clone, so the weapon carries no prefix); `weapon_attribute_effects` / `external_attribute_effects` (and `zoom_weapon_attribute_effects` / `zoom_external_attribute_effects`, the same arrays applied only while the zoom button is held) = `[{attribute, modifier, value}]` **replace** the whole array with rows of `(AttributeToModify, ModifierType, BaseValueConstant=value, scale 1)`; `attribute_slot_upgrades` = `[{slot, grade, activate?}]` replaces `AttributeSlotUpgrades`. A list left out keeps the template's rows. `modifier` is `MT_Scale`, `MT_PreAdd` or `MT_PostAdd`. Rows are cloned from the template part's own rows (the SDK appends a struct by copying one), so the template needs at least one row of that kind. |

### `extra_packages[]` (M7)

`[{name, file?}]`: further `.upk` files the mod loads with `load_package` and roots at the menu tick,
before the materials and parts that reference their objects. `file` (relative to the spec) is what
`install` copies into `CookedPCConsole` under the same rules as `package_file` (F12 names refused,
`--replace` for differing bytes, stale `.uncompressed_size` sidecars renamed — F11). L1 checks each
name. The M7 texture package: `python -m bl2_upk.texture_package --glb … --out scratch/PipelineTextures.upk --name PipelineTextures`.

### `materials[]` (M7)

A `MaterialInstanceConstant` constructed at runtime, the way skin mods do it: an **empty** MIC,
`SetParent(parent)` to a stock MIC (so the compiled shader is the parent's — a clone with a template
would carry `bHasStaticPermutationResource` without the resource), then `SetTextureParameterValue` /
`SetVectorParameterValue` / `SetScalarParameterValue` for every parameter listed. Constructed at the
menu tick after the extra packages and before the parts, rooted (F17), idempotent.

| Field | Req | Meaning |
|---|---|---|
| `name` / `outer` | yes | The MIC path is `<outer>.<name>`. |
| `parent` | yes | Stock `MaterialInterface` to parent to (`Common_GunMaterials.Materials.AssaultRifle.Mati_VladofLegendary`). |
| `texture_parameters` | no | `{parameter: Texture2D path}`; textures are rooted. On BL2's `Master_Gun` materials the parameters are `p_Diffuse`, `p_Masks`, `p_NormalScopesEmissive`, `p_Decal`, `p_Pattern`, `P_SimpleReflect`. |
| `vector_parameters` | no | `{parameter: [r, g, b, a]}` (`LinearColor`); the zone colours `p_AColorHilight` … `p_CColorShadow` on gun materials. |
| `scalar_parameters` | no | `{parameter: float}`. |

A material reaches a weapon through a **material part**: a part with `fragment: null`, template
`GD_Weap_AssaultRifle.ManufacturerMaterials.Mat_Vladof_5_Legendary`, `overrides.object_properties.Material`
= the MIC path, listed in a balance's `MaterialPartData`.

### `fire_sound` (optional)

`{"wav": "../scratch/.../shot.wav", "volume": 15, "mute_stock": true}`. A 16-bit PCM wav
played through Windows on every shot of a player weapon of this spec's balances: BL2's audio
is Wwise banks with no loose-file path, so this goes round the engine. Playback is
`src/bl2_partgen/wave_mixer.py` (winmm `waveOut` through ctypes, stdlib only), embedded
verbatim in the generated module as the `wave_mixer` module object: every shot is its own
stream, so shots, alt shots, dashes and slides overlap instead of cutting each other off.
(The first cut used `winsound.PlaySound`, and Win32 `PlaySound` owns ONE slot per process,
which is why only one custom clip ever played at a time.) Needs `balances`. The emitter bakes
`volume` (percent, default 15: game audio is much quieter than a normalised clip) into
`sounds/<mod>_fire.wav` beside the module (`weapons/sounds/` in an Armory); the mixer decodes
it once at import and pays the first device open then. Hooks: `WillowWeapon:FireAmmunition`
PRE plays it and, with `mute_stock`, blanks the weapon type's `FireSounds` events; POST
restores them, so other weapons of the shared type keep their sound. Limits: ignores the
in-game volume slider, not positional. A wav derived from another game's files stays in
`scratch/` like the meshes.

### `alt_fire` (optional, Shiv's shotgun)

RMB as a second trigger instead of aim-down-sights. Every key is optional:

```
"alt_fire": {"ammo_cost": 3, "damage_scale": 3.0, "accuracy_impulse_scale": 3.0,
             "recoil_pitch_deg": 6.0, "recoil_seconds": 0.12,
             "knockback_hip": 180.0, "knockback_alt": 520.0, "lift_hip": 60.0, "lift_alt": 160.0,
             "block_zoom": true, "arm_window_s": 1.0,
             "sound": "../scratch/.../alt.wav", "volume": 18}
```

Needs `balances`; `sound` needs `fire_sound` (it rides on its winsound player and stock mute,
baked to `sounds/<mod>_alt_fire.wav`, `volume` defaults to fire_sound's). Generated hooks
(section in `src/bl2_partgen/alt_fire_template.py`, spliced in only for specs that have it):

* `StartAltFire` / `StopAltFire` PRE on both `WillowGame.WillowPlayerController` and
  `Engine.PlayerController`: for a held weapon of our balances, **Block** (no zoom) and pull
  the ordinary trigger (`StartFire(0)` / `StopFire(0)`), marking the next shot as alt for
  `arm_window_s`. Fewer than `ammo_cost` rounds in the clip (`ReloadCnt`) = a dry click.
  Any other weapon zooms as usual.
* `WillowWeapon:FireAmmunition` PRE, alt shot: `ShotCost = ammo_cost`, `InstantHitDamage x
  damage_scale`, `PerShotAccuracyImpulse x accuracy_impulse_scale`; POST restores them. If the
  engine spent fewer rounds than `ammo_cost`, POST takes the rest off `ReloadCnt`.
* POST, both shots: `Pawn.AddVelocity` against the aim (`knockback_*` uu/s, plus `lift_*` up
  so a grounded player leaves floor friction); the alt shot also starts a view kick of
  `recoil_pitch_deg` spread over `recoil_seconds` (a self-disabling viewport Tick hook).

Harness helpers: `options.harness_fire_test.alt: true` (+ `alt_delay_s`, `alt_presses`) presses
RMB through the controller after the hip test; `options.harness_trace: [function paths]` writes
a status record for the first three calls of each (find which engine path an input takes);
`run_loop --input-test lmb,rmb` / `bl2 verify --input-test` press **real** mouse buttons after
the scored capture and put the mod's `alt_fire` / `trace` records in the report.

### `rage` (optional, The Busted Flush)

Shiv's Rage from Deadlock as a legendary effect. `{}` enables it with the defaults:

```
"rage": {"max": 100, "gain_hit": 5, "gain_alt_hit": 10, "gain_kill": 15,
         "decay_delay_s": 10, "decay_per_s": 2.5, "hit_window_s": 1.0,
         "damage_scale": 1.25, "speed_scale": 1.2,
         "hud": {"x": 0.5, "y": 0.80, "w": 300, "h": 16, "segments": 10, "text_scale": 1.0,
                 "enraged_text": "+25% DAMAGE   +20% SPEED"}}
```

Needs `balances`. Hooks (section in `src/bl2_partgen/rage_template.py`): `FireAmmunition` PRE
numbers our shots (alt or hip, read from the `alt_fire` section); `WillowAIPawn`/`WillowPawn`
`TakeDamage` PRE credits one gain per shot when the player is `InstigatedBy` and the causer is
our weapon (or our pawn within `hit_window_s` of our shot), and while Enraged scales `Damage`
and re-issues the call (parameters only, CPF_Parm) with a guard; `Died` PRE credits a kill made
by the player while holding the weapon; a viewport `Tick` drains after the delay, enters/leaves
Enraged and scales `GroundSpeed` (another writer's value becomes the new base); `PostRender`
draws the meter while the weapon is held or Rage is above zero, gated on play. Harness:
`options.harness_rage_test: {start_s, hold_s}` drives hip/alt hits, Enraged damage on the
nearest AI, decay and a kill, then holds Enraged `hold_s` for a capture.

### `balances[]` (M7)

A `WeaponBalanceDefinition` constructed at runtime, so the weapon drops as **its own gun** rather than
as a variant of the balance the parts were registered into. Cloned from `template_balance` (rarity,
manufacturer, base definition come along), then given its own `RuntimePartListCollection` — a clone of
the template's — with every field in `part_lists` replaced by exactly the listed parts. The cooked
`WeaponPartListCollection` is pointed at the same object. Registered at the menu tick after the parts,
GC-rooted (F17), idempotent.

| Field | Req | Meaning |
|---|---|---|
| `name` / `outer` | yes | The balance path is `<outer>.<name>`; it must be new (L10). |
| `template_balance` | yes | Catalog balance to clone. |
| `part_lists` | yes | `{field: [part, …]}` for the `*PartData` fields. A part is a **spec part name** or the full path of a stock part. Fields not named keep the template's rows; a part listed here needs no `register_in`. Each row is a clone of the template collection's row for that field (same weight index), with `Part` replaced. |
| `title` | no | `{name, outer, template, part_name, red_text?, on_parts, name_is_unique?}` (`name_is_unique` sets `bNameIsUnique`, so the card shows the title alone; the weapon type otherwise supplies a fallback prefix such as "Assault" when no part carries one): a `WeaponNamePartDefinition` cloned from `template`, named `part_name` on the item card, with `red_text` as `NoConstraintText` on its own clone of the template's first `CustomPresentations` entry. `on_parts` names the spec parts whose `TitleList` becomes `[title]` — a weapon's title comes from its parts. The title is a part: it joins `OUR_PART_PATHS`, so the save record/restore and the validation override cover `TitlePartDefinition` too. |
| `pools` | no | `ItemPoolDefinition` paths; the balance is appended to `BalancedItems` as a clone of the last entry (`InvBalanceDefinition` = the balance, `ItmPoolDefinition` = None). |
| `collection_name` | no | Leaf name of the runtime collection under the balance (`RuntimePartList`). |
| `suppress_prefix` | no | `true`: POST hooks on `WillowWeapon:ChooseRandomNameParts` and `InitializeInternal` null `PrefixPartDefinition` on every weapon of this balance, so the card reads the bare title. Needed because the weapon type's `PrefixList` supplies a fallback prefix ("Assault") when no part carries one, and `bNameIsUnique` does not hide it (M8 §8). |

The save round-trip records `BalanceDefinition` beside the slot fields for weapons of ours (F18: a
runtime balance is not cooked either) and restores it in the same PRE hook. The test harness grants
from the first `balances[]` entry, and its force/restore covers every field of it that holds one of
our parts (the A/B "stock" grant is the same balance forced to the template parts).

### `options`

| Field | Default | Meaning |
|---|---|---|
| `register_at` | `"menu"` | Must stay `"menu"`: registering after a map load crashes a save that already references the part (F16, L8). |
| `keep_alive` | `true` | Must stay `true`: unrooted objects are collected at the next level transition (F17, L8). `false` emits the `keep_alive()` helper as a no-op, which is only ever useful for reproducing F17. |
| `save_roundtrip` | `true` | Emit the `GeneratePlayerSaveGame` / `ApplyPlayerSaveGameData` hooks (F18). |
| `validate_override` | `true` | Emit the `ValidateWeaponDefinition` override (F19). Without it a restored weapon is deleted on load. |
| `test_harness` | `false` | Emit the F9/F10/F11 keybinds, the grant/equip helpers and the `_weapon_census()` that `bl2_verify` reads. Off for anything a player runs. It also switches the save/validate hooks to the M2 record key names (`save_hook` / `load_hook` / `validate_hook` instead of `hook`), so one status format serves both mods. |
| `harness_auto` | `true` | **`test_harness` only.** Emit the per-map phase machine (`seq_tick`), a record-for-record reproduction of `src/bl2_verify/m2_mod_template.py`, so the unattended verify loop can read this mod's status file in place of `pipeline_m2`'s. See below. |
| `harness_fov` | `100` | **`test_harness` + `harness_auto` only.** The `ConsoleCommand("FOV <n>")` phase 3 issues before the capture. |
| `harness_behindview` | `true` | **`test_harness` + `harness_auto` only.** Whether phase 3 also calls `SetBehindView(True)`. |
| `harness_capture_view` | `null` | **`test_harness` + `harness_auto` only.** `"first"` or `"third"`: phase 3 sets the camera to exactly that view (`SetBehindView(view == "third")`) instead of only ever switching third person on. First person is what the verify loop captures for a short weapon held close: the weapon fills a fixed region and the pawn is outside the crop (F22, M7 §3). `null` keeps `harness_behindview` in charge. Pass the matching `--capture-view` to `run_loop`. |
| `harness_pose` | `"idle"` | **`test_harness` + `harness_auto` only.** `"ads"`: after the camera step phase 3 aims down sights (`StartAltFire`), waits 60 ticks for the zoom blend, and a phase-5 `sight_check` record carries the camera POV (location, rotation, FOV), `bZoomed`, the zoom/ironsights socket names, `GetPhysicalFireStartLoc` and the world location/rotation of every socket of ours on the held weapon's first-person mesh; `done` follows it. The iron-sight alignment (M7 §5) is measured from these numbers. |
| `harness_mission` | `"GD_Episode01.M_Ep1_Champion"` | **`test_harness` only.** The mission whose reward table `grant()` hijacks. Every pipeline mod in the interpreter shares one pristine snapshot per mission path (a `_bl2_pipeline_shared` module in `sys.modules`), so two spawn/harness mods may hijack the same mission and whichever restores first puts the stock table back; the other's restore is a no-op (F26). |
| `harness_keys` | `["F9","F10","F11","F12"]` | **`test_harness` only.** Grant, equip newest, equip second newest, toggle first/third person. Four distinct keys; two spawn/harness mods enabled together must not share them (`specs/ak47_spawn.json` uses F5-F8 beside the AWP's F9-F12). |
| `harness_timing` | `"ticks"` | **`test_harness` + `harness_auto` only.** `"seconds"`: every wait of the phase machine (arm delay 2 s, phases 1.5 / 1.5 / 2 s, the ADS blend 1 s, the view hold 30 s) is measured on the wall clock instead of in viewport ticks. Tick counts scale with the frame rate: at ~375 fps the whole M2 sequence ran in 0.8 s and the equip fired before the granted weapon existed (M8 §6). Use `"seconds"` for anything unattended; the default keeps the M2 mod byte-identical. |
| `harness_equip` | `"newest"` | **`test_harness` + `harness_auto` only.** Which of our weapons phase 2 equips for the capture: `"newest"` (built from our parts) or `"second_newest"` (the A/B stock grant). The latter is how a **stock baseline** capture is taken in the same pose and view (`specs/ak47_harness_fp_baseline.json`). |
| `force_template_fragment_on_map_load` | `null` | A fragment name to re-assert on this spec's template parts on every map load, for text mods that retarget them (the AK-47 mod retargets the Shredifier barrel). It must name a *stock* fragment, not one of this spec's own. |
| `menu_ticks` | `600` | Viewport ticks to count at the main menu before registering. |
| `status_path` | `null` | **Absolute** path baked into the generated mod as its status file, so the verify loop can point it at `scratch/<name>_status.json` without dropping a `control.json` beside the mod. `control.json`'s own `status_path` still wins at runtime. A relative path is refused: the generated mod runs with the *game's* working directory, not the spec's. |

### The test harness (`test_harness` + `harness_auto`)

`harness_auto` makes the generated mod a drop-in replacement for the hand-written
`pipeline_m2` as the status source of `src/bl2_verify/run_loop.py`. The map-load hook arms
`seq_tick`, which counts 120 viewport ticks (the pawn and its inventory are not ready the
instant the loading movie goes away) and then runs one phase per wait, writing one status
record per phase with exactly M2's keys:

| phase | record keys | what it does |
|---|---|---|
| 0 | `census_before`, `force_stock_fragment`, `force_barrel_stock`, `grant_stock` | census the weapons of ours the pawn is carrying, put the stock fragment back on the template part, force the part list to the **template** part and grant a weapon |
| 1 | `stock_weapon`, `force_barrel_new`, `grant_new` | describe what that granted, force the list to **our** part, grant again |
| 2 | `new_weapon`, `restore_barrel_list`, `equip_newest` | describe that one, put the part list back exactly as it was, equip the newest |
| 3 | `camera`, `held`, `done: true` | `ConsoleCommand("FOV <harness_fov>")`, `SetBehindView(True)` (or `SetBehindView(<harness_capture_view> == "third")` when that option is set), describe what is held, unhook |

Census rows (`census_before`, `stock_weapon`, `new_weapon`, `held`) carry M2's keys —
`weapon`, `slot`, `barrel`, `frag`, `FirstPersonMesh`, `ThirdPersonMesh` — plus `unique_id`,
`ours` and one key per populated slot field. `barrel`/`frag` report the slot the harness
drives, derived from the first part's first `register_in` field (`BarrelPartData` →
`BarrelPartDefinition`); the name stays `barrel` because that is what the loop reads.

The grant is deterministic because `force_barrel()` temporarily sets **every**
`WeightedParts[i].Part` of that one list to the wanted part — the engine rolls one of the
weighted entries, so with all of them the same the granted weapon is certain to carry it.
The backup is taken once, at the first force, and phase 2 restores the list verbatim; the
mod never leaves it altered. This is harness-only, as are the FOV and behind-view calls: a
mod a player runs must let the other entries keep rolling and must not touch the camera.

`harness_auto: false` keeps the keybinds and the census but drops the phase machine,
`force_barrel()` and the camera calls entirely.

## CLI

```bash
python -m bl2_partgen specs/bent_barrel.json --out sdk_mod/PipelineBentBarrel
python -m bl2_partgen specs/bent_barrel.json --lint-only          # lint, write nothing
python -m bl2_partgen specs/bent_barrel.json --out <dir> --force  # emit despite lint errors
python -m bl2_partgen specs/bent_barrel.json --out <dir> --install "<game>" [--replace]
```

Exit code `0` when everything was written (and installed), `1` on a refusal. `--force` records
`{"forced": true}` and the outstanding errors in the emitted `lint.json`.

## What the emitter writes

```
<out>/__init__.py              the mod
<out>/pyproject.toml           name/version/description for the SDK
<out>/settings/<name>.json     {"enabled": true} -- install copies it to sdk_mods/settings/ (F8)
<out>/README.md               what it registers, where it installs, what it writes at runtime
<out>/spec.json                the spec as parsed, for provenance
<out>/lint.json                every proposal and its lint report
```

## The Armory (M10): several weapons in one mod

`python -m bl2_partgen specs/armory.json` (an armory JSON is recognised by its `weapons` list)
renders every weapon spec in *component* mode into `weapons/<id>.py` and one `__init__.py` that
wires all component hooks into a single `build_mod`, enables them (F8) and adds the spawn surface.

```jsonc
{
  "mod": {"name": "PipelineArmory", "author": "...", "version": "0.1.0", "description": "..."},
  "weapons": [
    {"id": "ak47", "spec": "ak47.json", "label": "AK-47",
     "sidecar": "../scratch/PipelineMeshes_ak47.fragment.json"},
    {"id": "awp",  "spec": "awp.json",  "label": "AWP",
     "sidecar": "../scratch/PipelineMeshesAWP.fragment.json"}
  ],
  "spawn": {"keybind": "F5", "mission": "GD_Episode01.M_Ep1_Champion"}
}
```

| field | required | meaning |
|---|---|---|
| `weapons[].id` | yes | Python identifier; becomes `weapons/<id>.py` and the console name (`armory spawn <id>`). |
| `weapons[].spec` | yes | The weapon's own spec, relative to the armory file. Unchanged: it stays the single source of truth for that weapon. Its `options.test_harness` is ignored inside an armory. |
| `weapons[].label` | no | Menu label (default: the id). Unique. |
| `weapons[].sidecar` | no | The weapon's `.fragment.json`, relative to the armory file (what `--sidecar` is for a single spec). |
| `spawn.keybind` | no | One key that spawns the weapon selected in the menu dropdown (`F5`). |
| `spawn.mission` | no | The mission whose reward table `spawn()` borrows; restored through the shared registry (F26). |

Rules: every weapon needs its own `balances[]` (the Armory spawns by balance); lint **L11** refuses
two weapons that construct or mutate the same object path (package, gestalt definition, part,
balance, title, material), which in practice means one weapon per host gestalt until two can
share a package. Exactly one Armory, and no separate player build of any weapon in it, may be
enabled at a time.

Spawning: console `armory list | spawn <id> [--level N] [--no-equip] | census`; mod menu
**Armory** page (Weapon dropdown + Spawn selected button); the keybind. Every spawn/equip/census
is a status record (`sdk_mods/<armory>/status.json`, or `control.json`'s `status_path`);
`control.json {"auto_spawn": ["ak47", "awp"]}` runs the unattended sequence
`src/bl2_verify/armory_check.py` reads (census on load, spawns, census + `done`).

Per-save part records (F18) of every build emitted from M10 on live in
`sdk_mods/_pipeline_saves/<package>/<save>.json`, shared by a weapon's player, harness, spawn
and armory-component builds; `src/bl2_verify/migrate_save_records.py` merges older per-mod
`saves/` folders into it.
