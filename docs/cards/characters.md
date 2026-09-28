# Characters: put a model on a vault hunter (`bl2_charswap`)

```
python -m bl2_charswap build   specs/characters/<id>.json     # meshes + texture atlas -> scratch/charswap/<id>/
python -m bl2_charswap install specs/characters/<id>.json     # test it: packages -> CookedPCConsole, skin -> sdk_mods/PipelineCharacters
python -m bl2_charswap.run_check                              # unattended: launch, load, capture first person / behind / inventory
python bl2.py pack <pack id>                                  # share it: an Armory character pack (packs/<pack id>.json, "characters")
```

A **skin** replaces a vault hunter's third-person body and first-person arms with your model,
on the game's own skeleton, so every animation still plays. The head is hidden. Players pick
the skin per vault hunter under Mods > Armory > Characters, or keep the game's model.

## What you need

- A character model as **glTF (`.glb`) with its skeleton** (export with animations or skins
  enabled, or the file has no bones). Humanoid rigs work; the bone map in
  `src/bl2_charswap/rigmaps.py` knows the common Source 2 naming and climbs to the nearest
  mapped ancestor for anything else. A different naming convention means adding aliases there.
- Its textures as PNG: colour, and optionally ambient occlusion and a normal map.
- The vault hunter's mesh package decompressed into `scratch/decomp/` (`bl2 doctor --fix` does
  `Startup.upk`; character bodies live in their DLC package, see the table below).

## The spec (`specs/characters/<id>.json`)

Copy one of the two reference specs and edit it. Top level:

| Key | Meaning |
|---|---|
| `id`, `label`, `character`, `description` | Skin id (unique across packs), the dropdown name, the vault hunter (`Axton`, `Maya`, `Salvador`, `Zer0`, `Gaige`, `Krieg`), one line for the menu |
| `assets_root`, `glb` | Folder holding the model and textures (keep it under `assets/` or `scratch/`, both gitignored), and the glb inside it |
| `scale` | Source units to centimetres, times the height ratio you want. Measure: BL2 bodies are about 190 cm |
| `source_package` | The decompressed package the vault hunter's meshes live in |
| `texture_package` | Name of the new texture package the build writes (`PipelineTextures<Skin>`) |
| `hide_mesh_prefixes` | The head component prefixes to hide (`CD_Heads_Psycho`, `CD_Heads_Mechro`, ...) |
| `material` | The material the skin's MIC is parented to; leave the reference value |
| `meshes[]` | One entry per BL2 mesh you replace: the body and the first-person arms |

Per mesh: `name`, `package` (the new mesh package), `source_mesh` (the BL2 mesh being
replaced), `prim_tiles` (which glb primitives go to which atlas tile), `atlas` (tile sources:
colour, AO, normal, sizes, optional `style` for a cel-shaded bake). Tuning knobs:
`joint_offsets` nudges a joint and its subtree after the fit, `subtree_scale` scales one
(first-person hands usually want about 1.2), `keep_bones` cuts an arms mesh out of a body
model when the source has no separate arms, `textures_from` reuses another mesh's atlas.

| Vault hunter | Body mesh | Arms mesh | Heads | Package |
|---|---|---|---|---|
| Krieg | `Lilac_Char_Psycho.Mesh.Skel_PsychoBody` (27 bones) | `Skel_Psycho_Arms` (47) | `CD_Heads_Psycho*` | `DLC/Lilac/Compat/Content/GD_Lilac_Psycho_Streaming_SF.upk` |
| Gaige | `Tulip_Char_Mechromancer.Mesh.Skel_MechromancerBody` | `Hands_Mechromancer` | `CD_Heads_Mechro*` | `DLC/Tulip/.../GD_Tulip_Mechro_Streaming_SF.upk` |

The other four follow the same pattern; find their mesh paths with `python -m bl2_upk.reader`
on the character's streaming package.

## What build does

For each mesh it reads the glb, brings it to UE space (`(x, -y, z) * scale`), pose-fits the
source rest skeleton onto the vault hunter's (joint heads pinned to the BL2 joints, bone
directions aligned down the hierarchy, the rest rigid-following its parent; fingers align
direction only, since BL2's first-person hand is half again as long as most models'), skins the
mesh into that pose, merges the weights onto the BL2 bones, packs the UVs into a 2x2 atlas, and
writes one section and one chunk through the package writer so the skeleton, sockets and LOD
info stay the original's. The output is re-parsed and validated; umodel opens it.

## What the runtime does

The same code runs in the dev mod `PipelineCharacters` and inside the Armory. It loads the
skin's packages at the main menu, builds one material instance per mesh, and every half
second in a map re-points every live component wearing a source mesh (the pawn, the
first-person arms, the status-menu preview rig, co-op copies) and hides the head components.
The title screen stand-in is covered too, so the skin shows before CONTINUE. Console:
`characters list | set <character> <skin id|default> | census | reapply`.

While `sdk_mods/PipelineCharacters` is installed the Armory refuses character packs, so the two
never fight over one mesh. Park the dev mod before testing a pack.

## Things that bite

- **Winding.** UE3 front faces wind the opposite way from glTF, and the Y mirror already
  reverses the orientation, so the index order is written unchanged. Reversing it renders the
  model inside-out (translucent torso, carved-in pouches).
- **Head pitch.** `rigmaps.FIT` accepts `"@frame"` as the direction child: the source bone takes
  the BL2 bone's whole rest rotation instead of a direction alignment. The head needs it on
  Krieg, whose neck points 51 degrees forward.
- **Glass and flat normals** render as a white blob under BL2's toon shading: leave lens
  primitives out. Bake AO at half strength or dark clothing goes black.
- **Cel-shaded sources** have no colour map. Bake an albedo first (`atlas.style` helps).
- **Materials.** The game wraps whatever material you set in its own per-component instance and
  re-applies the character's skin material once after a swap; the runtime accepts any material
  whose parent chain reaches ours. Do not fight it.
- **Mod menu.** Never assign `option.value` inside a `mods_base` change callback (it re-fires
  and the game hangs at the legal screen). Compare UObjects by `_path_name()`, not identity.
- **Paths.** Blender and umodel fail on very long paths; work from a short folder under
  `scratch/`. Never edit the DLC package itself (F12).
