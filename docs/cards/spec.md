# Spec: parts, balance, title, stats

`specs/<id>.json` (player build), `<id>_harness.json` (the loop's instrumented build),
`<id>_spawn.json` (playable with F9/F10/F11 grant/equip, no camera hijack). Full key reference:
`docs/PARTGEN_SPEC.md`. `python -m bl2_partgen <spec> --lint-only --sidecar <sidecar>` must be 0
errors; `bl2 build` emits every variant with the sidecar and dry-runs the harness offline.

- **fragments[]**: name, `template_fragment` (whose socket contract and bounds the new one
  inherits), `sockets: "all"`, `socket_overrides` and `bounds_override` (filled by the retarget;
  the item-card preview frames the bounds, so a new shape needs its own).
- **parts[]**: one per slot the balance rolls. A slot you want empty gets a part with
  `"fragment": null` cloned from the host's `*_None` part (draws nothing). The emitter clears
  inherited fragment names and `AdditionalGestaltModeSkeletalMeshNames` on every clone (F24: a
  "None" part otherwise draws a stock scope block or elemental capacitor). Stats ride on one
  part's `overrides`: `weapon_attribute_effects` (rows cloned from the template), `external_`,
  `zoom_weapon_`/`zoom_external_` (hold-RMB bonuses), `attribute_slot_upgrades`, `properties`,
  `clear_arrays: ["PrefixList"]` for a bare name. Rate of fire is `WeaponFireInterval` (bigger is
  slower); there is no `WeaponFireRate` attribute. Replacing `weapon_attribute_effects` drops the
  template legendary's gimmick too.
- **materials[]** (optional): a runtime MIC parented to `Item_ClassMods.Mat.Master_ClassMod`
  (plain diffuse + normal; the gun masters treat diffuse as intensity); textures from
  `python -m bl2_upk.texture_package` in an `extra_packages` entry. Skip both to keep the
  template's stock material part (the Boxgun does).
- **balances[]**: own balance cloned from a legendary of the same manufacturer
  (`template_balance`), `part_lists` naming your parts per `<Slot>PartData` — a slot left out
  keeps the template's `bEnabled` (leave `ElementalPartData` out for no elemental roll), naming it enables it —
  `title` (name, `part_name`, `red_text` HTML, `on_parts`), `suppress_prefix`, `pools`.
- **options**: `register_at: menu`, `keep_alive`, `save_roundtrip`, `validate_override` all true
  (F16–F19); harness builds: `test_harness`, `harness_auto`, `harness_timing: "seconds"` (F25),
  `harness_capture_view: "first"` (F22/F23), `harness_pose` (`ads`/`hip` record camera and socket
  world positions, `inventory` opens the card), `harness_equip: second_newest` for a stock
  baseline run. Pin `ElementalPartData` in the harness spec so a Slag roll cannot fail a run.
- **aim-down-sights**: iron sights — the game derives the eye from `RearSight`→`FrontSight`
  (`EyeSocket2`'s position is ignored, removing it hides the gun) and draws the FP weapon ~3
  units above the socket maths; either raise the sight sockets by 3 or put the sight line
  through the hip camera. Scoped — the camera sits on the sight part's `EyeSocket1` directly.
  Measure with `harness_pose` before moving anything.
