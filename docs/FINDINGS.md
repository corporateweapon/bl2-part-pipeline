# FINDINGS — discovery tasks D1–D7

Status legend: **UNANSWERED** (nothing verified), **PARTIAL** (some facts verified, gaps remain),
**ANSWERED** (verified from files on disk or a live game session; source noted per claim).

Rule from the brief: no pipeline code is written against anything not ANSWERED.

Evidence locations (all under `scratch/`, gitignored, regenerable):
- `decomp_all/` — all 914 base-game packages decompressed with Gildor's `decompress.exe -game=border`
- `all_exports.jsonl` — every SkeletalMesh / StaticMesh / GestaltSkeletalMeshDefinition / WeaponPartDefinition / … export across all 914 packages, produced by `scratch/scan_all.py` on top of `scratch/upkread.py` (own UE3 v832/46 table reader)
- `oe/data/BL2/` — OpenBLCMM object-explorer datapack (`data.db` SQLite + per-class property dumps)
- `gestalt_AR.txt`, `gestalt_AR_fragments.txt`, `gestalt_AR_skelmesh.txt` — property dumps of the assault-rifle gestalt definition and mesh
- `umodel_out/Startup/SkeletalMesh3/GestaltDef_AssaultRifle_GestaltSkeletalMesh.gltf` — umodel glTF export of the gestalt mesh
- `probe_results.json` — output of the runtime SDK probe (`sdk_mods/pipeline_probe`, template kept at `src/bl2_verify/probe_mod_template.py`)

---

## D0 — Environment (prerequisite, not in the brief's table)

**Status: ANSWERED** (2026-09-19)

- Game: `C:\Program Files (x86)\Steam\steamapps\common\Borderlands 2\`, build 8639, 32-bit UE3.
  Package format: **UE3 version 832, licensee 46** (read from every package header; umodel reports the same).
- `WillowGame\CookedPCConsole\`: 914 `.upk` (4.1 GB), 4 `.tfc`, 2 Wwise `.pck`.
  **Every package is LZO-compressed**, not just the 11 with `.uncompressed_size` sidecars: those 11 are
  *fully* compressed (whole file, tag then chunk table; `Startup.upk` 17.8 MB → 60.1 MB), the other
  903 are chunk-compressed (plain summary, `CompressionFlags=2`, chunk table in the header).
  Gildor's `decompress.exe` handles both.
- **No `GD_Weap_*` / weapon-mesh packages exist by name.** All weapon definitions and meshes are cooked
  into `Startup.upk` (see D3).
- `DLC\*` folders contain only `Compat` / `Lic` stubs → **no DLC content installed.** Sprint 1 = base game only.
- PythonSDK / willow2-mod-manager 3.8 (unrealsdk 3.2.0, pyunrealsdk 1.10.0, Python 3.14 embedded)
  installed at `Binaries\Win32\Plugins\`, mods in `sdk_mods\`. Log: `Binaries\Win32\Plugins\unrealsdk.log`.
  Console key = Tilde (user config under OneDrive `My Games\Borderlands 2\WillowGame\Config\`).
- OpenBLCMM 1.4.1 at `tools\OpenBLCMM\`; BL2 OE datapack (2023-04-20, 345 MB jar) downloaded there.
- Blender 5.1, Python 3.14/3.12, git 2.53 present.
- Tools acquired this session into `tools/` (gitignored): Gildor `decompress.exe` (2020-09-30) and
  `umodel`/`umodel_64` (2023-07-07) from gildor.org; UE Explorer 1.6.2 MSI (extracted, not installed).
- **Not acquired:** UPK Explorer 2.5.6.0 and TFC Installer 2.5.6.0 are Nexus-hosted (login-gated download)
  and closed-source. See D4/D5 for why the plan no longer depends on them.
- Backup: `backup/CookedPCConsole/Startup.upk` (+ sidecar), sha256
  `d714e65f…1124`, byte-identical to the live file at time of backup.
- **Load test (M0 accept (a)) PASSED 2026-09-19 13:14:** live `Startup.upk` replaced by the decompressed 60,134,127-byte
  file (original renamed to `Startup.upk.orig`, sidecar renamed to `Startup.upk.uncompressed_size.orig`). Game reached
  the main menu, the probe ran all 7 steps with results identical to the compressed baseline
  (`scratch/probe_results_uncompressed_startup.json`). → the patcher can emit plain uncompressed packages; no LZO
  compressor is needed. The game directory currently runs on the uncompressed file.
- **Package integrity check (found via the first M1 attempt, 13:33):** `Borderlands2.exe` holds a table of
  `<name>\0<SHA1>` for 12 base packages (core, engine, akaudio, gfxui, gameframework, gearboxframework, ipdrv,
  onlinesubsystemsteamworks, willowgame, menumap, **startup**, startup_loc_int), shader `.usf` files and DLC
  license files. `startup.upk`'s entry equals the SHA1 of the *decompressed* file, which is why the uncompressed
  stock copy passed and both the vertex-edited copy and a one-byte header-changed copy crashed (F12). Tool:
  `src/bl2_upk/exe_sha.py` (list/check/set). Consequence: **no edits to hashed packages; new package files only**
  (map/DLC packages prove unhashed files load). Editing the exe table was declined by the session's permission
  policy and is left as a user decision.

---

## D1 — Are BL2 weapon part meshes StaticMesh or SkeletalMesh? Does it differ by slot?

**Status: ANSWERED** (from `Startup.upk` export table + OE dumps + umodel export)

**Neither, in the sense the brief assumed. A weapon part has no mesh object of its own.** BL2 weapons use
Gearbox's *gestalt* system:

- One `SkeletalMesh` per weapon type holds every manufacturer's geometry for every slot, overlapped in one
  vertex/index buffer. For assault rifles: `Weap_AssaultRifles.GestaltDef_AssaultRifle_GestaltSkeletalMesh`
  (export in `Startup.upk`, 1,337,044 bytes serialized; 27,211 verts, 22,695 tris, 38 bones, 4 UV sets,
  **1 material slot** (`Materials(0)=None`, the weapon's material part supplies the MIC at runtime)).
  Same pattern for Pistol, SMG, Shotgun, SniperRifle, Launcher, Grenades, Shields, Artifacts (9 gestalt defs total).
- A `GestaltSkeletalMeshDefinition` (`Weap_AssaultRifles.GestaltDef_AssaultRifle`) maps **fragment names** to
  **triangle-index ranges** in that mesh:
  `GestaltInfos(0).Parts[i] = (SkeletalMeshFragmentName, MaterialIndex, FirstIndex, NumPrimitives)`,
  plus `GestaltPartBounds[i]` (per-fragment reference-pose bounds) and `GestaltSocketMappings[i]` (see D2).
  The AR mesh has **47 fragments**; the ranges tile the index buffer exactly (last fragment ends at index
  68,085 = 22,695 × 3). Full table: `scratch/gestalt_AR_fragments.txt`.
- A `WeaponPartDefinition` selects its geometry by **name string**: `bIsGestaltMode=True`,
  `GestaltModeSkeletalMeshName=AR_Barrel_Vladof` (Shredifier barrel). `NongestaltSkeletalMesh=None` for
  every base-game AR part. Additional slots `AdditionalGestaltModeSkeletalMeshNames[0..1]` exist (unused here).
- Slots do not differ in mechanism: body/barrel/grip/stock/sight/accessory/elemental are all fragments of the
  same mesh (`AR_Body_*`, `AR_Barrel_*`, `AR_Grip_*`, `AR_Stock_*`, `AR_Scope_*`, `Acc_*`).
- `StaticMesh` is used only for ammo pickups (`Weap_AmmoPickups.Meshes.*`) and one grenade mesh.
- Separate per-part `SkeletalMesh` objects (`Weap_AssaultRifles.Barrel.AR_Barrel_Alien`, 66 KB) exist only in
  three map packages (`Boss_Volcano_Combat`, `Boss_Volcano_Combat_Monster`, `ThresherRaid_P`) — non-gestalt
  NPC weapon meshes, irrelevant to player weapons.

**Fragment sharing (which parts use which range)** — from `WeaponPartDefinition.dump.1`:

| Fragment | Tris | Used by |
|---|---|---|
| `AR_Barrel_Vladof` | 1,164 | `AR_Barrel_Vladof_Shredifier`, `AR_Barrel_Vladof_Minigun` |
| `AR_Barrel_Vladof_Alt` | 486 | `AR_Barrel_Vladof` (the common one), `AR_Barrel_Vladof_EvilSmasher` |
| `AR_Barrel_Dahl` | 584 | Dahl, Hail, HammerBuster, Veruc |
| `AR_Barrel_Jakobs` | 666 | Jakobs, Scorpio, Stomper |
| `AR_Barrel_Bandit` | 460 | Bandit, Madhouse |
| `AR_Barrel_Torgue` | 1,384 | 6 Torgue barrels incl. KerBlaster |
| `AR_Barrel_Alien` | 740 | Alien, Alien_Dahl |

→ **There is no barrel used by exactly one weapon.** The narrowest blast radius is `AR_Barrel_Vladof`
(Shredifier + Minigun), which is also the barrel the previous AK-47 work sits on. That is the M1 target.

**Consequence for the pipeline:** "export a barrel FBX, edit, reimport" is really "extract triangles
[FirstIndex, FirstIndex+3·NumPrimitives) of one big skeletal mesh, edit, write them back". An in-place
edit that keeps vertex/triangle counts is a **same-size binary patch inside one export** (no table changes).
A *new* part means appending vertices+triangles to that export and adding a fragment entry (see D6 for
where the entry can live).

## D2 — Socket / attach-point naming; are sockets in the FBX or on the part definition?

**Status: ANSWERED** (OE dumps `SkeletalMeshSocket.dump.1`, `gestalt_AR.txt`; umodel glTF)

- Sockets are **`SkeletalMeshSocket` sub-objects of the gestalt mesh** (`…_GestaltSkeletalMesh:SkeletalMeshSocket_48`
  … `_95`, 48 of them), listed in the mesh's `Sockets` property array. They are **not** on the part definition
  and **not** in umodel's FBX/glTF export (glTF carries only the 38-bone skeleton).
- Naming convention: `<FragmentName>_<OriginalSocketName>`, produced at cook time via
  `GestaltSocketMappings(i)=(SkeletalMeshFragmentName, OriginalSocketName, MangledSocketName)`.
  Part definitions refer to the *original* name (e.g. `ShellCasingSocket=EjectPort`), the gestalt system
  resolves it to the mangled one for the equipped fragment.
- Per-slot socket sets on AR:
  barrel → `Muzzle` (bone `Barrel`), `EyeSocket2`, `FrontSight` (bone `Root`); Vladof_Alt also `Muzzle2`.
  body → `RearSight`, `EjectPort` (+`EyeSocket2` on Jakobs). scope → `EyeSocket1`, `SightFX` (bone `Scope`), `FrontScope`.
  elemental → `elemental`.
- Example: `AR_Barrel_Vladof_Muzzle` … bone `Barrel`; `AR_Barrel_Bandit_Muzzle` = (53.25, 0, 2.25) on `Barrel`;
  `AR_Barrel_Jakobs_Muzzle` = (68.25, 0, 3.0) on `Barrel`.
- Skeleton (38 bones, from glTF): Root, WeaponOffset, Trigger, Mag, Mag_Spinner, Mag_Bullet01–07, Barrel_Spinner,
  ChargeHandle, hammer, Loader, AmmoLid, Scope, LeverHandle, Slide, MagRelease, **Barrel**, BarrelFlap01–05,
  AlienBarrelFlap× 10, JacobSpinner. Barrel-slot geometry should be weighted to `Barrel` (and `Barrel_Spinner`
  for spinning Vladof barrels — the Shredifier part has `BoneControllers(0).BoneName="Barrel_Spinner"`).
- Coordinate frame: gestalt mesh bounds origin ≈ (0, −47.7, 2.3), extent (30, 99, 36); barrel fragments sit at
  **negative Y** (e.g. `AR_Barrel_Vladof` bounds origin (−0.28, −60.4, 4.0), extent (5.3, 43.4, 11.0)). So −Y is
  "forward / muzzle", +Z up, X sideways.

## D3 — Which UPK owns a given part mesh; runtime package load order?

**Status: ANSWERED (on-disk); runtime confirmation pending probe** (`scratch/all_exports.jsonl`)

- `Weap_AssaultRifles.GestaltDef_AssaultRifle_GestaltSkeletalMesh`, `Weap_AssaultRifles.GestaltDef_AssaultRifle`,
  and every `GD_Weap_AssaultRifle.*` `WeaponPartDefinition` are exported by **exactly one package: `Startup.upk`**.
  Zero duplicates in the other 913 packages. All 9 gestalt defs and their meshes are Startup-only.
- `Startup.upk` is the always-loaded startup package (`[Engine.StartupPackages]` in `DefaultEngine.ini` plus
  engine defaults). It loads before any map, so for AR parts **the load-order shadowing failure (F1) cannot
  occur** — there is no earlier package to win.
- Where F1 *does* live: character/creature meshes. 160 SkeletalMesh paths are exported by more than one package
  (e.g. `Char_Bandit_Marauder.Mesh.Skel_BanditMarauder` in 25 map/combat packages). That is the case the linter
  must cover if the pipeline is ever pointed at non-weapon meshes; for sprint 1 it reduces to
  "assert the target object exists in exactly one package".
- `WeaponPartDefinition` exports also appear in 33 map/combat packages (NPC-specific parts such as
  `GD_Sage_Weapons.*`), never duplicating Startup's.

**New package files load (probe p8, 13:44):** a copy of `UI_Stash_SF.upk` dropped into `CookedPCConsole` as
`PipelineProbe.upk` (no hash-table entry, no ini change) is found by `unrealsdk.load_package("PipelineProbe")`
and its objects resolve as `PipelineProbe.<name>`. The package name is the *file name*, not the internal name.

Runtime check (probe `p1_packages`, main menu, 2026-09-19 13:10): 2,454 `Package` objects, 840 top-level, in
creation order `Core, Transient, Engine, GameFramework, GFxUI, GearboxFramework, IpDrv, XAudio2, AkAudio, WinDrv,
OnlineSubsystemSteamworks, WillowGame, EngineResources, …`; `Weap_AssaultRifles` is #493 and `Startup` is present.
The gestalt def reads back at runtime exactly as on disk (47 parts / 47 bounds / 48 socket mappings / 48 sockets,
`Materials=[None]`). Full list in `scratch/probe_results_baseline.json`.

## D4 — Does UPK Explorer have a CLI / project file / scriptable interface?

**Status: ANSWERED — GUI-only.** (web research; sources: Nexus page 587, PCGamingWiki "UPK Explorer",
734 Nexus comments)

- UPK Explorer 2.5.6.0 (12 Sep 2026) is a closed-source WinForms .NET 6 app. No CLI, batch mode, script,
  project or manifest file. Author FCH823, 24 Aug 2026: *"No (because that would complicate the UI even
  further) but I might add support for import/export json for these arrays at some point."*
- "Advanced Mode" unlocks: object import/export, package editor (rename objects, edit properties / name table /
  import table, save a copy), FBX import/export (experimental; needs FBX 2014/2015, 32-bit FBX, tangents
  exported; "meshes for some games require the same number of material sections or they crash"), audio.
- It cannot safely restructure packages: FCH823 refuses delete/merge features because *"references that are
  stored in unsupported data would be incorrectly deleted"* / *"unsupported data that can't be properly
  re-indexed"*. Adding objects works via its game-patch format but is exactly the fragile path the brief warned about.
- The shadowing fix is documented by the tool authors on PCGamingWiki Troubleshooting: *"Mods not appearing
  ingame — Usually, this is due to the object being preloaded by another UPK … resolve this by adding new
  names and assigning them to your modified objects."* An unanswered PCGamingWiki talk-page post describes a
  BL2 user hitting exactly this with a character mesh.
- Download requires a Nexus account (not attempted; no credentials handling by the agent).

**Brief §7 rule triggered: "If D4 comes back GUI-only, stop and revise the milestone plan."**
Short version: UPK Explorer / TFC Installer are demoted to
optional cross-check tools; the pipeline's patcher is our own Python (`bl2_upk`), which D1/D3 make tractable
because the whole edit is one export inside one always-loaded package.

## D5 — What does a TFC "game patch" output contain? Any of it text/diffable?

**Status: PARTIAL** (documentation only; no tool on disk)

- From the tool docs: a game patch is one `.PackagePatch` file per modified package (self-contained, binary),
  next to `GameProfile.xml` (+ `GameProfile.IdRemappings.xml`, `PackageExtensions.xml`, and for texture packs
  `[Game].TFCMapping` + `LocalMips_XXX.tfc` / `Texture2D_XXX.tfc`). The XML files are text; the patch payload is not.
- TFC Installer 2.5.6.0 applies a folder containing `GameProfile.xml`, backs up the original UPKs, patches them
  in place (and updates `uncompressed_size` / TOC as needed), and can disable exe SHA checks. "Restore Backup"
  reverts one mod (reverse order); "Uninstall all" reverts everything. No CLI, no persistent install log found.
- Not producible here without a Nexus download. Under the revised plan this is a cross-check, not a dependency.

## D6 — Can the SDK construct a WeaponPartDefinition at runtime and have it survive save/load?

**Status: PARTIAL** (API verified; runtime probe pending; save/load not yet tested)

- pyunrealsdk 1.10 exposes `construct_object(cls, outer, name, flags, template_obj)`, `find_object`,
  `find_all(cls, exact)`, `find_class`, `load_package`, `make_struct` (verified against the installed stubs in
  `sdk_mods\.stubs\unrealsdk\__init__.pyi` and the pybind source).
- **Runtime registration works (probe run 2026-09-19 13:10, main menu, all 7 steps ok):**
  - p3: `construct_object("WeaponPartDefinition", outer=GD_Weap_AssaultRifle.Barrel, name="AR_Barrel_PipelineProbe",
    template=Shredifier barrel)` → `GD_Weap_AssaultRifle.Barrel.AR_Barrel_PipelineProbe`, resolvable via
    `find_object`, `GestaltModeSkeletalMeshName` settable, `PartType` and `TitleList` inherited from the template.
  - p4: `GestaltInfos[0].Parts.append(copy)` + rename → 48 parts, tail `{AR_Barrel_PipelineProbeFrag, first 23484,
    num 1164}`; `GestaltPartBounds` likewise 48. Struct types: `GestaltSkeletalMeshDefinition:GestaltPart`,
    `:GestaltPartBoundsEntry`, `:GestaltSocketRemapEntry`.
  - p5: `construct_object("SkeletalMeshSocket", outer=gestalt mesh, template=Sockets[0])`, set `SocketName`/`BoneName`,
    `mesh.Sockets.append(s)` → 49 sockets; `GestaltSocketMappings.append` → 49 mappings.
  - p7: Shredifier's `RuntimePartListCollection` is `…AR_Vladof_5_Sherdifier:WeaponPartListCollectionDefinition_39`,
    `BarrelPartData.WeightedParts` struct `WeaponPartListDefinition:PartGradeWeightData` (so `bl2_partgen` can append
    a weighted entry the same way).
  - **Renderer honours a runtime mesh swap (14:14):** after `GestaltDef_AssaultRifle.GestaltSkeletalMesh` is set to
    `PipelineMeshes.PL_AR_Gestalt_Mesh` (loaded from our own package), a Shredifier granted afterwards reports
    `FirstPersonMesh.SkeletalMesh` and `ThirdPersonMesh.SkeletalMesh` = the new mesh and draws the bent barrel
    Weapons created before the swap keep the old mesh (F14). Sockets on the new mesh resolve
    (48 loaded; muzzle flash position not yet checked).
  - **Runtime-added fragment renders (M2, 16:4x):** a fragment appended to the mesh on disk and registered at runtime
    (Parts/Bounds/SocketMappings entries + constructed sockets + constructed WeaponPartDefinition appended to the
    Shredifier barrel list) is rolled onto a granted Shredifier and drawn. Two hard rules found on
    the way: register at the **main menu** (F16) and **root** every loaded/constructed object against the level-transition
    GC with `ObjectFlags |= 0x4000` (F17).
  - **Save/load of a custom part (F18):** the weapon survives reload but the runtime part slot comes back `None`. A
    Sanity-Saver-style restore hook is required; that is M5's emitter work.
  - Not yet shown: muzzle-flash/socket positions on the new fragment; multiplayer (out of scope).
    The M1 item is a stock Shredifier, so it trivially survives save/load; the mesh swap itself must be re-applied by the
    mod every session (it is not saved anywhere). `LODModels` is native-only from Python, confirming the mesh payload
    itself can only change on disk — which is what the package writer now does.
- Save/load: the game serializes parts by *index into the part list* (Sanity Saver README: it "stores every
  single part slot" the game fails to serialize). A runtime-constructed part is not in any cooked part list, so
  by default it will not round-trip. Known mitigations: Sanity Saver (apple1417; hooks
  `WillowPlayerController.ApplyWeaponSaveGameData` / `ServerSetWeaponSaveGameData` / `GeneratePlayerSaveGame`
  and blocks `ValidateWeaponDefinition`), or the Athena mod's private equivalent (closed source, Nexus 661,
  "Sanity Saver not required (mod handles its own item serialization)" — mechanism NOT FOUND).
- Athena mod internals: **NOT FOUND** (no public source). Whether it ships .upk patches or constructs at runtime
  is unknown.

## D7 — Minimum viable detectable mesh change

**Status: ANSWERED (decision)**

Target fragment `AR_Barrel_Vladof` (1,164 tris, bounds origin (−0.28, −60.4, 4.0), extent (5.3, 43.4, 11.0)).
Standard deformation for M1/M2: **translate every vertex of the fragment with Y < −80 (the front ~half of the
barrel) by +8 units in Z and scale the front section's X extent ×1.5** — a visibly bent, flared muzzle that
cannot be confused with a material or lighting change, keeps vertex/triangle counts unchanged (same-size
patch), and stays inside the mesh's overall bounds so no bounds update is needed for M1. Verification
measure: silhouette diff of the first-person weapon capture vs. baseline (bl2_verify).

## Audio: custom clips play concurrently (2026-09-25)

**The question.** Custom audio (Shiv / AWP / Deagle shots, the slide and dash clips) played one
clip at a time: starting any clip cut off the one playing, across mods.

**Root cause, precisely.** Not the engine. Every mod played through `winsound.PlaySound(path,
SND_FILENAME | SND_ASYNC)`, and Win32 `PlaySound` keeps exactly one internal waveOut stream per
process: any call, from any module, stops the current clip before starting the new one. Wwise,
AkEvents, voice limits and priorities were never involved, because the clips never enter the
engine (BL2's audio is two Wwise `.pck` banks with no loose-file path).

**The fix.** `src/bl2_partgen/wave_mixer.py`: winmm `waveOut` through ctypes (stdlib only, so it
runs on the SDK's embedded Python 3.14 / Win32 unchanged). `waveOutOpen` hands out an independent
stream per call and the Windows audio engine mixes any number of them. A `Clip` is decoded once
(volume applied to the samples at load, cached per path + level); `play()` queues the start on one
worker thread and returns in ~0.05 ms; the worker opens/writes (~8 ms after the first ~30 ms open,
paid at import by `preload`) and sweeps finished streams. `Voice.stop()` / `stop_all()` reset
streams; `loop=True` repeats until stopped (slide_sound's "loop while sliding").

Measured on the dev machine (64-bit Python; the SDK's is 32-bit, first in-game run pending):
four clips of mixed formats (44.1 kHz stereo + 48 kHz mono) overlapped, every one played its full
length; a legacy `PlaySound` call mid-stream did not touch a waveOut stream (so migration can be
piecemeal); a burst of 8 starts of one clip all played; the Shiv fire + alt-fire clips overlapped
from the installed `shiv.py`'s embedded copy.

**Where it lives.** The template embeds the file verbatim (`_WAVE_MIXER_SECTION`, exec'd into a
`wave_mixer` module object so the mod stays one file and the Armory pack runtime renders it the
same way; `wave_mixer.py` is in `pack.RENDER_MODULES`). `slide_sound` and `stamina` carry a copy
beside their `__init__.py` and import it relatively. Tests: `tests/test_wave_mixer.py` (device
tests skip without winmm).

**Deliberately not done.** `waveOutSetVolume` on a handle: on a Vista+ audio session its scope is
not something to gamble the game's own mix on, so volume is applied to the samples instead. Wwise
route (custom `.wem` spliced into a `.pck`, triggered through a hijacked AkEvent): would give
positional audio and the in-game sliders, but needs the game's exact Wwise version, a pck
repacker and an answer on pck integrity checks; not worth it for concurrency alone.

