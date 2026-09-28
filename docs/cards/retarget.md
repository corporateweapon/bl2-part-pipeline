# Retarget: recipe → package

```
python bl2.py new <id> --from boxgun --glb <path> --label "Name"     # copy + rename a worked example
python bl2.py measure <id>      # place + frame checks + labelled preview with cut candidates
python bl2.py build <id>        # split, export, sockets/bounds into the specs, emit, offline dry run
```

A recipe (`recipes/<id>.json`, schema in `src/bl2_retarget/recipe.py`; `recipes/boxgun.json` is
the worked example) holds everything that is per weapon:

- **source**: the glb, `body_match` (which mesh is the gun), `attachments` (glTF empties to
  measure; bone-tail offsets are removed), `frame` preset: `cs2_gltf` (180° about Z, ×100) or
  `identity` (already in the add-on frame at UE scale; muzzle −Y, up +Z, cm).
- **host**: `mesh_path` of the gestalt, `reference_fragment` (imported for its armature and
  sockets), `fragment_table` (null for the assault rifle; other hosts need their table dumped
  from the OpenBLCMM Object Explorer into `scratch/`: export the host's
  `GestaltSkeletalMeshDefinition` as text and save it as `scratch/gestalt_<type>_fragments.txt`).
- **placement**: a source vertex group's centroid is put on a gestalt bone head (`trigger` →
  `Trigger`). Scale 1.0 keeps real proportions; the report says what scale would reach the
  stock muzzle socket.
- **bones**: source group → gestalt bone, **only** where the bone's rest head sits inside the
  geometry or the clip is pure translation (the sniper's were measured with `anim_probe`);
  everything else is `Root`. A bone 12+ units from its geometry swings it across the gun.
- **checks**: geometry statements that must hold after placement (longest axis Y, muzzle at −Y,
  magazine down, highest point forward for sights or over the receiver for a scope, attachments
  on the geometry in all three axes). Fix the frame or anchor when one fails, never the cuts.
- **fragments**: in **export order** (that fixes each fragment's index range), one per slot,
  each with a face `rule`: `front_y_lt` for a barrel (frontmost vertex, keeps tube faces whole),
  `cy_gt` stock, `cy_gt`+`cz_lt` grip, `cz_gt`+`cy_between` scope; `rule_order` is the order the
  rules are tried (first match wins, `"remainder"` is the body). `subset_of` + `majority_group`
  duplicates the magazine out of the body; `export: false` keeps it out of the package.
- **sockets**: resolved in order, coordinates come out in UE mesh space: `ring front/rear` of a
  fragment (muzzle crown, scope rings, `xz_from_attachment`), `extreme highest/rearmost` with a
  `where` filter (sights), `attachment` + `push_outside` (eject port outside the receiver wall),
  `bone_centroid`, `sight_line` (EyeSocket2 on the iron-sight line), `resolved`/`literal` fallbacks.

Read the labelled previews (`scratch/captures/<prefix>_labelled_side.png`, `_top.png`): measure
mode shows the placed gun with histogram valleys as bands and the current rules as lines; the
build shows every fragment coloured with its cut planes and a legend. Choose cuts in the thinnest
bands. `python -m bl2_retarget summarize <report>` prints the same numbers.

Vertex budget: the gestalt index buffer is uint16, so stock + new vertices < 65,535 per package
(AR stock 27,211; sniper 26,335). Lint L9 states the headroom.

**One package per weapon.** Every weapon has its own mesh package and sets
`options.own_gestalt`: its mod clones the host `GestaltSkeletalMeshDefinition` and weapon type,
so any number of weapons can share one host without touching the stock gestalt or each other.
A weapon that moves package but whose players already have it in saves sets
`options.save_package` to the old package name, so records stay under `_pipeline_saves/<old>/`
(the Boxgun: `PipelineMeshesBoxgun`, saves under `PipelineMeshes`). Package groups
(`packages/<Name>.json`, several recipes in one package with one shared gestalt) still build
and pass L11, but no weapon uses one any more.

`apply-spec` fills only what a spec lacks and reports differences; sockets you tuned by hand in
a spec stay unless `--overwrite`.
