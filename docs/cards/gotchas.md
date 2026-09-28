# Gotchas (search `docs/FAILURE_MODES.md` by F-number; `bl2_triage` names them for you)

- **F12** the exe SHA1-checks twelve base packages over their uncompressed bytes; one byte changed
  = instant exit. Ship new `.upk` files, never touch those.
- **F13** export flags must be 0 for top-level exports or nothing in the package exists.
- **F16/F17** register at the main menu, before any save loads; root everything against GC
  (`ObjectFlags |= 0x4000`) or the next map load dies with "Ran out of virtual memory".
- **F18/F19** runtime parts are not save-encodable: the generated hooks record and restore them,
  and force `ValidateWeaponDefinition` for our parts, or the weapon loses parts / disappears.
- **F24** "None" parts and stock bodies draw inherited fragment names regardless of
  `bIsGestaltMode`; the emitter clears them. Junk still showing = dump the template part.
- **F22/F23** capture in first person; F12 is Steam's screenshot key **and** the view toggle,
  the harness holds the view.
- **F25** tick-counted waits collapse at high fps: `harness_timing: "seconds"`.
- **F27/F32** a system dialog, or the console that launched the loop, in front breaks every focus and capture;
  drive with `echo=False`, run in the background, prefer borderless fullscreen.
- **F28** a generated mod is only as new as its last emit; after any `templates.py` change re-emit
  every build, parked ones too.
- **F31** every pixel box is calibrated at 1920×1080; boxes are rescaled to the capture now, but a
  foreign resolution with blind menu probes is still a NO GO.
- **glTF** unparenting resets `matrix_basis` (39× scale bug); bone-parented attachment nodes sit
  at the bone *tail*; armatures import posed while the mesh is at rest. The engine handles all
  three; check attachments against geometry in all three axes anyway.
- **SDK** out params must be passed (`make_struct` placeholders) and come back in the returned
  tuple; `PlayerCamera` is None (use `GetPlayerViewPoint`); appending a struct copies it, so clone
  rows from the *template's* array, never from the array being cleared.
- **Names** parts' `PrefixList` and the weapon type's fallback prefix both produce a prefix;
  `clear_arrays` plus `balances[].suppress_prefix` gives the bare name.
- **Animation** does not exist in this pipeline: the mesh is driven by the host type's clips, and
  authoring an AnimSet is a mesh-writer-sized project, not polish.
- **Runtime objects** (`objects[]`, `freeze{}`): `ApplyStatusEffect` needs a `DamageType` class
  (`CheatApplyStatusEffect` is a no-op in shipping); negative `MT_Scale` divides (-1.25 -> /2.25);
  `EStatusEffectType` has an unused `STATUS_EFFECT_Slow`; statuses on a time-dilated *player* drop
  ~2.4 s in; a weapon takes its type from generation, not the balance (`own_gestalt` hook, F34).
- **Packages we write** must carry BL2's EngineVersion (F35); particle ports keep FName numbers.
- **Own glbs** (Deadlock, hand-prepared): a bone-parented attachment empty lands off by the
  bone-tail compensation meant for CS2 exports; derive the muzzle from `ring front` instead.
  A model with no colour map (cel-shaded games) needs an albedo baked in Blender first.
- **Audio** never use `winsound.PlaySound`: Win32 `PlaySound` has ONE slot per process, so any
  call from any mod cuts off the clip playing. `bl2_partgen/wave_mixer.py` (winmm `waveOut` via
  ctypes, one stream per play, embedded in every generated mod) overlaps freely; `slide_sound`
  and `stamina` carry a copy. Never `waveOutSetVolume`: scale samples at load instead.
- **RMB** reaches `WillowPlayerController:StartAltFire` (via BL2's GBA_ZoomIn); `alt_fire`
  blocks it there. `ReloadCnt` = rounds left in the clip. Prove input paths with
  `bl2 verify --input-test rmb,...` and `options.harness_trace`, not with SDK calls alone.
