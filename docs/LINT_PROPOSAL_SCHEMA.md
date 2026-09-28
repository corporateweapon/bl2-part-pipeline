# Lint proposal schema (v1)

A *proposal* is the JSON the pipeline hands to `bl2_lint` before it builds a package: what mesh it is
about to ship, which gestalt fragment it adds, which part definition it will construct at runtime and
where that part gets registered. It is the M2 sidecar (`scratch/PipelineMeshes_m2.fragment.json`,
written by `src/bl2_verify/m2_build.py`) plus the registration plan the SDK mod needs.

Machine-readable form: `src/bl2_lint/proposal.py` (`Proposal`, `REQUIRED_FIELDS`).
A ready-to-run copy of the M2 proposal below: `src/bl2_lint/proposal.example.json`.

```jsonc
{
  "package":            "PipelineMeshes",
  "mesh_path":          "PipelineMeshes.PL_AR_Gestalt_Mesh",
  "weapon_type":        "AssaultRifle",
  "fragment":           "AR_Barrel_PL_Bent",
  "first_index":        68085,
  "num_primitives":     1164,
  "template_fragment":  "AR_Barrel_Vladof",
  "part_path":          "GD_Weap_AssaultRifle.Barrel.AR_Barrel_PL_Bent",
  "part_slot":          "WP_Barrel",
  "sockets":            ["Muzzle", "EyeSocket2", "FrontSight"],
  "register_in_lists":  ["GD_Weap_AssaultRifle.A_Weapons_Legendary.AR_Vladof_5_Sherdifier:WeaponPartListCollectionDefinition_39.BarrelPartData"],
  "register_at":        "menu",
  "keep_alive":         true,
  "objects":            []
}
```

## Fields

| Field | Type | Required | Meaning |
|---|---|---|---|
| `package` | string | yes | Name of the **new** `.upk` file the pipeline will write into `CookedPCConsole`, with or without the `.upk` suffix. It is also the runtime package name (`load_package("PipelineMeshes")`), because the game keys on the file name, not the internal one (D3). |
| `mesh_path` | string | yes | Full object path of the `SkeletalMesh` inside that package, e.g. `PipelineMeshes.PL_AR_Gestalt_Mesh`. |
| `weapon_type` | string | yes | Catalog key of the gestalt definition the fragment belongs to: one of `AssaultRifle`, `Pistol`, `SMG`, `Shotgun`, `SniperRifle`, `Launcher`, `Grenades`, `Shields`, `Artifact` (plus the DLC `LilacBuzzAxe`). |
| `fragment` | string | yes | Name of the new gestalt fragment. Must not already exist in that weapon type's fragment table. |
| `first_index` | int | yes | Index-buffer offset of the new fragment's triangles. Equal to the mesh's current index count when appending. |
| `num_primitives` | int | yes | Triangle count of the new fragment (`> 0`). The fragment occupies `[first_index, first_index + 3*num_primitives)`. |
| `template_fragment` | string | yes | Existing fragment in the same weapon type the new one is derived from — supplies bounds, socket mappings and the part-definition template. |
| `part_path` | string | yes | Full object path of the `WeaponPartDefinition` that will be constructed at runtime. |
| `part_slot` | string | no | Expected `PartType` (`WP_Barrel`, `WP_Body`, ...). Informational. |
| `sockets` | string[] | no | **Original** (unmangled) socket names the new fragment needs, e.g. `Muzzle`. The gestalt system mangles them to `<fragment>_<socket>` (D2). |
| `register_in_lists` | string[] | yes | Where the part is registered so it can roll (F5). Each entry is a part-list object path, optionally suffixed with the slot field: `<list path>.BarrelPartData` for a `WeaponPartListCollectionDefinition`, `<list path>.WeightedParts` for a `WeaponPartListDefinition`. |
| `register_at` | string | yes for a clean run | Must be `"menu"`. Registering after a map load crashes when a save already references the part (F16). |
| `keep_alive` | bool | yes for a clean run | Must be `true`. Every loaded/constructed object and its outer chain needs `ObjectFlags \|= 0x4000` or the next map load dies in GC (F17). |
| `objects` | string[] | no | Extra object paths to run the multi-owner (shadowing) check over — character meshes, MICs, anything the mod re-points. |
| `fragments` | object[] | no | **Several fragments in one package** (M6). Each entry is `{fragment, template_fragment, first_index, num_primitives, sockets?, new_vertices?}`. L2, L4 and L5 run over every entry; L4 checks they tile consecutively from the mesh's current index count. See below. |
| `new_vertices` | int | no | How many vertices this fragment adds to the mesh. Per fragment inside `fragments`, or at the top level for the single-fragment form. Feeds L9. |
| `total_vertices` | int | no | Vertex count of the mesh **after** the build, straight out of the sidecar. The most direct input to L9. |
| `base_vertex_count` | int | no | Vertex count of the mesh the fragments are appended to, when the package has not been built yet: L9 then budgets `base_vertex_count + Σ new_vertices`. |
| `register_in_runtime_lists` | string[] | no | Part lists the mod **constructs at runtime** (its own `WeaponBalanceDefinition`, M7): `<balance path>:<collection>.<SlotField>`. L6 accepts these in place of `register_in_lists` — with a note, since the catalog cannot check a list that does not exist yet — and still requires the slot-field suffix. |
| `new_balances` | object[] | no | Runtime balances the mod constructs: `{path, template, collection?, title?, title_template?, pools?}`. L10 requires `path` to be new and `template` to be a catalog balance, and `title` (a `WeaponNamePartDefinition` path) to be new. `bl2_partgen` puts these on the **first** part's proposal only, so a spec gets one L10 report rather than one per part. |
| `new_materials` | object[] | no | Runtime `MaterialInstanceConstant`s the mod constructs: `{path, parent, textures?}`. L10 notes them; the catalog has no materials or textures to check against. On the first part's proposal only. |
| `extra_packages` | string[] | no | Further packages the mod loads at the menu tick. L1 checks each name against the package files and the exe's SHA1 table exactly like `package`. On the first part's proposal only. |
| `part_only` | bool | no | The part adds **no fragment**: it is a clone of a non-gestalt part (`AR_Sight_None`) that draws nothing, used to claim a slot and leave it empty. `fragment`, `first_index`, `num_primitives` and `template_fragment` are then not required, L0 says so in a note, and L2/L4/L5 have nothing to look at; L3, L6, L7 and L8 still apply. `bl2_partgen` emits one of these for every spec part whose `fragment` is `null`. |

Unknown keys are ignored and preserved (`Proposal.extra`), so the M2 sidecar's `dz`, `reparsed_ok`
and `out` can stay in the file.

## Several fragments in one package

`bl2_upk.fragment.build_multi_fragment_package` (and the add-on's `export_fragments`) append more
than one fragment to the same cloned mesh and write **one** sidecar listing them all. The sidecar
repeats the first fragment's keys at the top level, so everything that consumed a single-fragment
sidecar keeps working; a multi-fragment-aware consumer reads `fragments` instead:

```jsonc
{
  "package":        "PipelineMeshes",
  "mesh_path":      "PipelineMeshes.PL_AR_Gestalt_Mesh",
  "fragment":       "PL_Test_Cyl",          // == fragments[0], for old consumers
  "first_index":    68085,
  "num_primitives": 44,
  "total_vertices": 28551,
  "total_indices":  71745,
  "fragments": [
    {"fragment": "PL_Test_Cyl",       "template_fragment": "AR_Barrel_Vladof",
     "first_index": 68085, "num_primitives":   44, "new_vertices":   24, "new_geometry": true},
    {"fragment": "PL_Test_Box",       "template_fragment": "AR_Barrel_Vladof",
     "first_index": 68217, "num_primitives":   12, "new_vertices":    8, "new_geometry": true},
    {"fragment": "AR_Barrel_PL_Bent", "template_fragment": "AR_Barrel_Vladof",
     "first_index": 68253, "num_primitives": 1164, "new_vertices": 1308, "dz": 8.0}
  ]
}
```

The other fields (`weapon_type`, `part_path`, `register_in_lists`, …) stay per-*part*: one proposal
is still linted per part, and `bl2_partgen` emits one per part from the spec
(`bl2_partgen.resolve.load_sidecar` reads either sidecar shape).

## Checks

`lint(proposal, catalog)` returns a `Report` with `ok` (true when there are no **error** entries),
`entries` (each with `check`, `severity`, `message`, `detail`) and `render()`.

| id | severity | what it catches |
|---|---|---|
| L0 | error | required field missing, or `weapon_type` is not a catalog key |
| L1 | error | `package` collides with an existing package file (case-insensitive), or with one of the 12 packages the exe SHA1-verifies (F12) |
| L2 | error / warning | `fragment` already exists in the target weapon type (error: it shadows and the gestalt lookup is ambiguous); exists in a *different* weapon type (warning: the name is not globally unique) |
| L3 | error + note | `part_path` already exists in the catalog; the note names the earlier-loaded package that wins (F1) |
| L4 | error / warning / note | `num_primitives <= 0`, negative `first_index`, or a range overlapping an existing fragment (errors); unaligned or past-the-end ranges (warnings); appending or filling a hole (notes) |
| L5 | error / warning | `template_fragment` does not exist (error); a requested socket is not mapped on the template, or a template mapping has no socket object (warnings) |
| L6 | error / warning | `register_in_lists` empty, unknown list path, or unknown slot field (errors); bare list path with no slot, disabled slot, or a list belonging to another weapon type (warnings) — F5 |
| L7 | warning | an object path the proposal touches is exported by more than one package; names every owner and which one loads first (F1) |
| L8 | error | `register_at != "menu"` (F16) or `keep_alive` is not `true` (F17) |
| L10 | error / warning / note | a `new_balances` path that already exists in the catalog, or a template balance that does not (errors); a template with no runtime part-list collection or of another weapon type (warnings); otherwise a note naming the balance, its title and pools — F1 for balances |
| L9 | error / warning / note | the mesh's vertex count after the build: `>= 65,535` is an error (the LOD index buffer is uint16, so the package cannot be built), `> 60,000` a warning, otherwise a note with the headroom; a note saying "not checked" when the proposal carries no vertex counts |

Warnings and notes never fail the run; only errors do.

L2, L4 and L5 iterate over `fragments` when the proposal has one, and each entry they report
carries a `fragment` detail. L4 walks a cursor: the first fragment is expected at the mesh's
current `index_count`, the next where that one ended, and an overlap with an **earlier fragment of
the same proposal** is as much an error as an overlap with a stock one.

## CLI

```bash
python -m bl2_lint proposal.json                       # uses catalog/parts.json
python -m bl2_lint proposal.json --catalog other.json
python -m bl2_lint proposal.json --json                # machine-readable report
```

Exit code `0` when `report.ok`, `1` otherwise. Run it as the last step before every patch build and
from the verify loop.
