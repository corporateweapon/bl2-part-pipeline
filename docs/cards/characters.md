# Characters: swap a vault hunter's model (`bl2_charswap`)

```
python -m bl2_charswap build   specs/characters/ct_krieg.json   # meshes + texture atlas -> scratch/charswap/<id>/
python -m bl2_charswap install specs/characters/ct_krieg.json   # dev: packages -> CookedPCConsole, skin -> sdk_mods/PipelineCharacters
python -m bl2_charswap.run_check                                 # unattended: launch, load, capture FP / behind / inventory, census
python bl2.py pack ct_sas                                        # share it: an Armory character pack (packs/ct_sas.json)
```

**Two hosts, one runtime.** The swap logic is `src/bl2_charswap/runtime.py`. The dev mod
`PipelineCharacters` (the template, test keys F7/F9 on) and the **Armory** (1.1.0+, which ships
it as `_render/charswap_runtime.py` and wears the skins of its character packs) both call
`runtime.configure(skins, ...)`. Edit the runtime, never a copy; `bl2 armory` refuses to release
while it has uncommitted changes. While `sdk_mods/PipelineCharacters` is installed the Armory
refuses its character packs (the reason is under Mods > Armory > Packs), so the two never swap
one mesh; park the dev mod to test packs.

**What a skin is.** One Source 2 glb (VRF export of the *agents/* model with `--gltf_export_animations`,
which is what makes VRF write the skeleton) plus, per BL2 mesh it replaces, a table of glb primitives ->
atlas tiles and the tile sources (colour, AO, normal PNGs decompiled from the vtex files). The spec lives in
`specs/characters/`; the Valve assets live outside git in `scratch/charswap_assets/<id>/`.

**What build does.** For each mesh: read the glb, bring it to UE space (`(x, -y, z) * scale`, scale =
2.54 in->cm x height ratio), pose-fit the CS2 rest skeleton onto the BL2 RefSkeleton (`rigmaps.FIT`: joint
heads pinned to the BL2 joints, bone directions aligned down the hierarchy, everything else rigid-follows
its parent), linear-blend-skin the mesh into that pose, merge the weights onto the BL2 bones
(`rigmaps.WEIGHT_MAP`, unresolved bones climb to the nearest mapped ancestor), remap UVs into the atlas,
and write ONE section / ONE chunk through `clone_skeletal_mesh(mutate=)` so the RefSkeleton, sockets,
LODInfo and mirror table are the original's. Fingers only resolve on the arms skeleton (47 bones); on
the body (27 bones) they collapse onto the hands. Output is re-parsed and validated; umodel opens it.

**What the mod does.** `PipelineCharacters` reads `characters.json`, loads every skin's packages at the
menu tick (roots them, F17), builds one MIC per mesh (parent `Item_ClassMods.Mat.Master_ClassMod`,
p_Diffuse/p_Normal). One dropdown per vault hunter under Mods -> PipelineCharacters -> Characters.
Every ~0.5 s in a map it re-points *every* live SkeletalMeshComponent wearing a source mesh (pawn body,
first-person arms, the inventory preview, co-op copies) and hides the character's separate head
components on those actors. "Default" restores. A `ShowStatusMenu` hook scans every tick for ~1.5 s after
a menu opens: the status menu spawns a fresh body+head preview rig on the pawn and it is swapped on its
first frames (verified in the status file). Note BL2's inventory itself draws no 3D character (a skin item
only shows a card); the rig renders at the Quick Change station. Console: `characters list | set <character> <skin id|label|default> | census | reapply`. Keys: F7 behind
view, F9 census -> status file; "open inventory" and "grant a skin item" (mission-reward hijack, also pays the
reward money) ship unbound and can be bound in the mod menu for testing.

**Second skin: Mina (Deadlock) over Gaige** (`specs/characters/mina_gaige.json`). Deadlock hero rigs
are the CS2 rig's family with renames (`head` not `head_0`, `scapula_0_L`, `neck_0_TWIST`, skirt / purse /
rib / hair jiggle chains): `rigmaps` carries the aliases, everything else climbs to a mapped ancestor.
Deadlock heroes ship no first-person arms: `keep_bones` cuts the arms mesh out of the body (triangles
whose vertices are all dominated by the listed joints or their descendants) and `textures_from` reuses
the body's atlas. Deadlock "rough" maps are alpha-only (no normal detail): `normal_size: 256` keeps the
flat normal atlas cheap. Gaige: body `Tulip_Char_Mechromancer.Mesh.Skel_MechromancerBody`, arms
`Hands_Mechromancer`, heads `CD_Heads_Mechro*`, package `DLC/Tulip/.../GD_Tulip_Mechro_Streaming_SF.upk`.
`run_check --character N` picks the N-th SELECT CHARACTER row (0-based; presses slower than 0.5 s).

**Tuning knobs (spec, per mesh).** `joint_offsets: {source joint: [x, y, z]}` nudges a joint and its subtree
after the fit (Mina's purse 5 cm inward). `subtree_scale: {joint: factor}` scales a subtree's joints and
skinned geometry about the joint (Mina's first-person hands x1.2: BL2 first-person hands are big).
Fingers are never position-pinned (`rigmaps.NO_PIN`): BL2's first-person hand is ~50 % longer than a
Source 2 hand and pinning every knuckle stretched the fingers into claws; they align direction and keep
their own length. `atlas.style: {levels, saturation, smooth, ao_bands, ao_floor}` bakes a cel look into
the albedo (blur, luminance quantised to N bands with hue kept, saturation bump, 2-band AO).

**Krieg facts.** Body `Lilac_Char_Psycho.Mesh.Skel_PsychoBody` (27 bones), arms `Skel_Psycho_Arms`
(47: fingers `L_finger0..4` = thumb..pinky, segments `N`, `N1`, `N2`), heads `CD_Heads_Psycho*`. Faces +X,
Y right; relaxed A-pose; RefSkeleton quats need no W flip. CS2 SAS: hands and head land within 1 cm of
Krieg's joints after the fit; the arms mesh fits 41 of 47 joints.

**Winding.** BL2/UE3 front faces wind the opposite way from glTF: on Krieg's stock body the geometric face
normal opposes the vertex normal on every triangle (`cross(e1, e2) . n < 0`). The Source->UE Y mirror
already reverses glTF's orientation, so the source index order is written unchanged. Reversing it (the
"obvious" mirror fix) culls every front face and the model renders inside-out: the torso looks translucent,
pouches look carved in, sleeves show their lining. Bitangent sign: UE stores W = the UV-derived handedness
(checked on the stock mesh), and the mirror negates glTF's w; both meshes verify at 100 %.

**Fit notes.** `rigmaps.FIT` accepts `"@frame"` as the direction child: the CS2 bone takes the BL2 bone's
whole rest rotation (pitch + roll) instead of a direction alignment. The head needs it: Krieg's Neck bone
points 51 deg forward but his head sits ~20 deg forward, so aligning the neck direction pitched the CT head
into the chest. The CS2 lens (glass) primitive is left out: its flat normal renders as a white specular blob
under BL2's toon shading. AO is baked at half strength for the same reason (full bake = flat black vest).

**Main menu.** The title screen shows the character on a `PlayerStandIn` actor in `menumap`; the scan
runs there too (it is not gated on a map load, which never fires at the menu), so the skin shows before
CONTINUE and follows SELECT CHARACTER switches.

**Gotchas (mod).** Never assign `option.value` inside a mods_base `on_change_anytime` callback (it re-fires
the callback: the game hung at the legal screen). UObject wrappers do not compare by identity: compare
`_path_name()`. The game wraps whatever material you set in its own per-component MIC, and the skin system
re-applies Krieg's skin MIC once after a swap: accept any material whose Parent chain reaches ours.

**Gotchas.** Blender and umodel fail on the very long scratchpad paths (MAX_PATH); work from
`scratch/ct/`. The `characters/models/ctm_*` vmdl is a stub; the mesh is under `agents/models/`. VRF's
material export crashes on the current CS2 shaders; decompile the vtex files directly. Never edit the
DLC package (F12); never let the glb or textures into the repo.
