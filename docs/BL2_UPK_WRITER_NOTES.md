# BL2 UPK package writer — implementation notes

Package: `src/bl2_upk/writer.py` (`PackageBuilder`, `clone_skeletal_mesh`).
Tests: `tests/test_writer.py`.
Outputs: `scratch/PipelineMeshes_clone.upk`, `scratch/PipelineMeshes_m1.upk`.

**Why a writer at all.** The 12 base packages, `Startup.upk` included, are SHA1-verified
by `Borderlands2.exe` (`docs/FAILURE_MODES.md` F12), so a mesh edit can never ship as a
patch to one of them. New package files are not in the hash table, so the pipeline emits a
*new* `.upk` that the game pulls in with `unrealsdk.load_package("<name>")`.

Everything below was derived from the template bytes, not assumed. Templates used:
`scratch/decomp/Startup.upk`, `MainGameRefs_SF.upk`, `Sanctuary_P.upk`.

---

## 1. File layout

Derived, not assumed: in all three templates the regions are **contiguous with no padding**,
in this order, and the payload region runs to EOF.

```
[0]                 summary            129 B for our group string / zero chunks
[NameOffset]        name table         NameCount  x (FString + uint64 flags)
[ImportOffset]      import table       ImportCount x 28 B
[ExportOffset]      export table       ExportCount x (68 + 4*NetObjectCount) B
[DependsOffset]     depends table      ExportCount x TArray<int32>
[HeadersSize]       export payloads    contiguous, in export-table order, to EOF
```

Checks that fix this (all three templates): `ImportOffset == NameOffset + sizeof(names)`,
`ExportOffset == ImportOffset + 28*ImportCount`, `DependsOffset == ExportOffset +
sizeof(export entries)`, `HeadersSize - DependsOffset == 4*ExportCount`, first payload
offset `== HeadersSize`, last payload end `== file size`, zero gaps.

**Name table comes before the import table**, which the summary field order (which lists
names, exports, imports) does not imply. Template bytes win.

## 2. Summary, field by field, as written

Values are those of `scratch/PipelineMeshes_clone.upk` (48 sockets + 1 mesh).

| Off | Field | Size | Value written | Source of the value |
|----|---|---|---|---|
| 0 | Tag | 4 | `0x9E2A83C1` | constant |
| 4 | Version / Licensee | 2+2 | `832` / `46` | constant |
| 8 | HeadersSize | 4 | `7550` | computed = DependsOffset + 4·ExportCount |
| 12 | PackageGroup | FString | `"None"` (len 5, `4E 6F 6E 65 00`) | all three templates |
| 21 | PackageFlags | 4 | `0x80880009` | see §3 |
| 25 | NameCount / NameOffset | 8 | `126` / `129` | computed |
| 33 | ExportCount / ExportOffset | 8 | `49` / `4022` | computed |
| 41 | ImportCount / ImportOffset | 8 | `3` / `3938` | computed |
| 49 | DependsOffset | 4 | `7354` | computed |
| 53 | ImportExportGuidsOffset | 4 | `7550` (= HeadersSize) | Startup + MainGameRefs |
| 57 | ImportGuidsCount | 4 | `0` | " |
| 61 | ExportGuidsCount | 4 | `0` | " |
| 65 | ThumbnailTableOffset | 4 | `0` | all three templates |
| 69 | Guid | 16 | fresh `os.urandom(16)` | per brief |
| 85 | GenerationCount | 4 | `1` | all three templates |
| 89 | Generations[0] | 12 | `(49, 126, 0)` = (ExportCount, NameCount, NetObjectCount) | see §5 |
| 101 | EngineVersion | 4 | `1712575` | copied from template |
| 105 | CookerVersion | 4 | `134` | copied from template (134 in all three) |
| 109 | CompressionFlags | 4 | `0` | we never compress |
| 113 | CompressedChunks count | 4 | `0` | " |
| 117 | PackageSource | 4 | random u32 | see §4 |
| 121 | AdditionalPackagesToCook | 4 | `0` (empty TArray\<FString\>) | see §4 |
| 125 | TextureAllocations | 4 | `0` (empty TArray) | see §4 |
| 129 | *(end of summary)* | | `== NameOffset` | |

**HeadersSize is "end of all header data", not "end of the depends table."** Startup and
MainGameRefs have `ImportExportGuidsOffset == HeadersSize` (no Guids), but `Sanctuary_P.upk`
has 3 import Guids and puts `ImportExportGuidsOffset` 150 bytes *before* `HeadersSize`. With
zero Guids the two coincide, which is the case we emit.

### Name table entry

`FString` (int32 length incl. NUL, then Latin-1 bytes + NUL) followed by a `uint64` of name
flags. We always write **`0x0007001000000000`** — 28,571 of Startup's 28,576 names carry
exactly this; the other five carry `0x0007101000000000` (bit 0x1000_0000_0000 extra), which
nothing we emit needs. Name `None` is interned at index 0 so a property terminator always
exists.

### Import entry (28 B)

`ClassPackage FName(8) | ClassName FName(8) | Outer int32 | Name FName(8)`. Exactly three
rows are needed, copied in form from Startup's own rows 558, 149 and 151:

| # | objref | ClassPackage | ClassName | Outer | Name |
|---|---|---|---|---|---|
| 1 | −1 | `Core` | `Package` | 0 | `Engine` |
| 2 | −2 | `Core` | `Class` | −1 | `SkeletalMesh` |
| 3 | −3 | `Core` | `Class` | −1 | `SkeletalMeshSocket` |

Note `ClassPackage` is `Core` for the `Engine` package import too — the class of a UPackage
object is `Core.Package`, not `Engine.Package`.

### Export entry (68 B; `68 + 4·NetObjectCount` in general)

`Class i32 | Super i32 | Outer i32 | Name FName(8) | Archetype i32 | ObjectFlags u64 |
SerialSize i32 | SerialOffset i32 (absolute) | ExportFlags u32 | NetObjectCount TArray<i32> |
Guid 16 | PackageFlags u32`.

Values copied from Startup's `GestaltDef_AssaultRifle_GestaltSkeletalMesh` (export 45438)
and its `SkeletalMeshSocket_48..95` sub-objects (45454–45501):

| Field | Mesh | Socket |
|---|---|---|
| Class | `-2` (`Engine.SkeletalMesh`) | `-3` (`Engine.SkeletalMeshSocket`) |
| Super | 0 | 0 |
| Outer | 0 (package root) | mesh export ref |
| Archetype | **0** | **0** |
| ObjectFlags | `0x0007000400000000` | `0x0007000000000000` |
| ExportFlags | `0x00000001` | `0x00000001` |
| NetObjectCount | 0 (empty array) | 0 |
| Guid | 16 zero bytes | 16 zero bytes |
| PackageFlags | `0x00000000` | `0x00000000` |

**No archetype imports were needed.** The brief anticipated `Engine.Default__SkeletalMesh`
archetypes; Startup's mesh and socket exports both have `Archetype == 0`, and the strings
`Default__SkeletalMesh` / `Default__SkeletalMeshSocket` do not appear in its name table at
all. `clone_skeletal_mesh` raises rather than guessing if it is ever handed a source export
with a non-zero Archetype.

The mesh's `Outer` is `0`, i.e. the mesh sits at the package root. In Startup the same mesh
hangs off a `Weap_AssaultRifles` group (a `Core.Package` export), so the in-game path is
`Weap_AssaultRifles.GestaltDef_...` and the file name never appears in it. With `Outer == 0`
the file's own package *is* the outer, so the runtime path is
`PipelineMeshes.PL_AR_Gestalt_Mesh`. No shipped BL2 package has a `SkeletalMesh` at
`Outer == 0`, but `LightMapTexture2D`, `ShadowMapTexture2D`, `ObjectReferencer` and `World`
all appear there, so a non-`Package` class at the root is legal; umodel confirms it loads.

Our `Package` reader builds paths from the export table alone and therefore reports
`PL_AR_Gestalt_Mesh`, with no package prefix. `writer.qualified_path(package_name, path)`
adds it back; that is what the tests assert against.

### Depends table

`ExportCount` consecutive `TArray<int32>`, every one empty — i.e. `4·ExportCount` zero bytes.
Verified two ways on all three templates: the byte count matches exactly, and every count
word is zero (Startup: 0 non-zero out of 55,087).

## 3. PackageFlags

| Package | Value | Decoded |
|---|---|---|
| `Startup.upk` | `0x8488000D` | FilterEditorOnly, **StoreFullyCompressed**, RequireImportsAlreadyLoaded, DisallowLazyLoading, Cooked, ServerSideOnly, AllowDownload |
| `MainGameRefs_SF.upk` | `0x80880009` | FilterEditorOnly, RequireImportsAlreadyLoaded, DisallowLazyLoading, Cooked, AllowDownload |
| `Sanctuary_P.upk` | `0xA08A0009` | FilterEditorOnly, NoExportAllowed, RequireImportsAlreadyLoaded, DisallowLazyLoading, ContainsMap, Cooked, AllowDownload |

**The brief's noted value for `MainGameRefs_SF` (`0x82880009`) is wrong — the file says
`0x80880009`.** Template bytes win. The difference is bit `0x02000000` (`PKG_StoreCompressed`),
which that file does not set; it is not compressed.

We write **`0x80880009`**, `MainGameRefs_SF.upk`'s value verbatim, because it is the nearest
analogue: an uncompressed, cooked, non-map content package. That is exactly the minimal
cooked set the brief asked for — `PKG_AllowDownload | PKG_Cooked | PKG_FilterEditorOnly`
plus the two bits every template carries (`0x00080000 PKG_DisallowLazyLoading`,
`0x00800000 PKG_RequireImportsAlreadyLoaded`). Dropped deliberately: `0x04000000`
StoreFullyCompressed (we write raw payloads), `0x00000004` ServerSideOnly (Startup-only, and
meaningless for a client content package), `0x00020000` ContainsMap and `0x20000000`
NoExportAllowed (map-package bits).

`PackageBuilder` clears `PKG_StoreCompressed | PKG_StoreFullyCompressed` unconditionally,
whatever flags it is handed, so a template-copied value can never claim compression the file
does not have. (F11 in `FAILURE_MODES.md` is the neighbouring trap: a `.uncompressed_size`
sidecar makes the loader treat a file as fully compressed.)

## 4. PackageSource / AdditionalPackagesToCook / TextureAllocations

After `CompressedChunks` the summary continues with three fields our reader skips:

* **`PackageSource` (u32)** — differs per package (Startup `0x2E1DC7E8`, MainGameRefs
  `0x54D470E3`, Sanctuary_P `0x8498F35A`) with no derivable relationship to the name, so it
  is written as a fresh random u32 (overridable via `PackageBuilder(package_source=...)`).
  Neither umodel nor the table layout depends on it.
* **`AdditionalPackagesToCook` (`TArray<FString>`)** — `0` for Startup and MainGameRefs, 9
  entries for `Sanctuary_P` (its streaming sub-levels). We write `0`.
* **`TextureAllocations`** — `TArray` of `{SizeX, SizeY, NumMips, Format, TexCreateFlags,
  TArray<int32> ExportIndices}` (20 B + 4 + 4·n each). Startup has 96 entries (not 5, as the
  brief's earlier note said), MainGameRefs 1, Sanctuary_P 62. We write `0`: the count-0 array
  is a valid empty allocation hint and we ship no textures.

## 5. NetIndex and NetObjectCount

Two separate things, and the template makes the relationship explicit:

* The **payload** `NetIndex` (first int32 of every UObject export) is content. Startup's mesh
  has 48; its 48 sockets have 100…147. `clone_skeletal_mesh` copies every one verbatim.
* The **export-table** `NetObjectCount` is a `TArray<int32>` length prefix; it is `0` on the
  mesh and on every socket, so each entry is 68 bytes flat.
* The **generation** `NetObjectCount` is `0` in `Startup.upk` and `MainGameRefs_SF.upk`
  despite those non-zero payload `NetIndex` values. (`Sanctuary_P.upk`, a map package, has
  12,661 — networked actors.)

So the brief's suggestion (renumber `NetIndex` 0..N−1 and set the generation
`NetObjectCount = N`) was **not** followed: the template does the opposite. We copy the
source `NetIndex` values unchanged and write generation `NetObjectCount = 0`. Startup's own
mesh is the proof that a cooked content package can carry non-zero `NetIndex`es under a
zero generation count, and umodel loads our output either way.

## 6. What `clone_skeletal_mesh` remaps

A new package has a new name table and a new object-index space, so *nothing* copied from the
source can keep its indices. Rewritten:

1. **Property tags**, recursively — tag name, tag type, `StructProperty` struct name,
   `ByteProperty` enum name, and `NameProperty` / enum-`ByteProperty` values. Recursion
   matters: `ReferencePoseBounds` is a `BoxSphereBounds` whose body is itself a tagged list,
   and `LODInfo[0].TriangleSortSettings[0]` is three levels down and holds two enum FNames
   plus a `NameProperty`. `props.py` keeps such bodies as opaque `raw`, so `writer._Remapper`
   re-walks them.
   *Detection rule:* a `StructProperty` body is treated as a tagged list only if its struct
   name is not in a native-struct blocklist (`Vector`, `Rotator`, `Quat`, `Color`, `Guid`, …)
   **and** it parses and consumes the body exactly; an `ArrayProperty` body is treated as
   `count` consecutive tagged lists only if that parse consumes the body exactly. Anything
   else is copied byte for byte, so the heuristic cannot silently corrupt data.
2. **Object references** — `ObjectProperty`-family values, the `Sockets` array (POD int32
   object refs; UE3 does not store array element types, so this is driven by property name),
   the native `Materials` array, and each export row's Class / Outer / Archetype. References
   with no counterpart in the new package become `0` (`None`). That is what happens to
   `Materials`: the gestalt AR's single entry is already `0`, and a material living in a
   package we do not import would otherwise dangle.
3. **Bone names** — `RefSkeleton[i].Name`, 38 of them.
4. **The mesh tail's `NameIndexMap`** — `int32 count`, then `count × (FName 8 B, int32)`
   (`BL2_UPK_CODEC_NOTES.md` §4). The FName halves are rewritten, the int32 values and
   everything after the map (`PerPolyBoneKDOPs`, `BoneBreakNames`, `BoneBreakOptions`,
   `ClothingAssets`, `CachedStreamingTextureFactors`) are copied verbatim. Skipping this was
   the one failure mode that would have produced a file that reads fine and names the wrong
   bones at runtime.
5. **Socket payloads** — `SocketName` and `BoneName` FName values; `RelativeLocation` /
   `RelativeRotation` / `RelativeScale` are plain floats and are copied.

Socket exports are **named after their `SocketName`**, not after the source export name
(`SkeletalMeshSocket_48`…), so the object paths are usable:
`PipelineMeshes.PL_AR_Gestalt_Mesh.AR_Barrel_Vladof_Muzzle`. All 48 `SocketName`s of the
gestalt AR are unique; if two ever collided, the second gets UE3's own FName instance number
rather than a mangled string.

### Payload offsets

Bulk-data offsets inside a SkeletalMesh (`RawPointIndices.OffsetInFile`) are **absolute**, so
`SkeletalMeshExport.serialize()` needs the export's final file offset — which is only known
after the header size is known. `PackageBuilder` therefore accepts a payload as either
`bytes` or a callable `(absolute_offset) -> bytes`, lays the file out, and calls each callable
twice: once at a provisional offset purely to measure the length (the only offset-dependent
field is a fixed-width int32, so the length cannot change), then once at the real offset. A
length that changes between the two calls is an error, not a silent corruption.

`mesh.package` is left pointing at the *source* package after remapping. That is deliberate:
`serialize()` only consults it to look up a `None` name for the property terminator, and the
new package's `None` FName is passed explicitly.

## 7. What umodel objected to

`tools/gildor/umodel_64.exe -game=border` accepted the very first file the writer produced;
nothing had to be fixed for it. Recorded for completeness:

* **Two "unknown property" lines** appear on every export —
  `StructProperty: unknown USkeletalMesh3 ReferencePoseBounds` and
  `BoolProperty: unknown FSkeletalMeshLODInfo bDisableCompressions`. **These are not writer
  bugs:** stock `Startup.upk` produces the identical two lines. They are gaps in umodel's
  BL2 typeinfo, and it skips the properties by `DataSize` as the format allows.
* `umodel` keys the package name off the **file name**, not `PackageGroup` or anything in the
  header. `-list … PipelineMeshes.upk` on a directory holding `PipelineMeshes_clone.upk`
  fails with `unable to find package`. The same is true of the game: a package intended to
  load as `PipelineMeshes` must be shipped as `PipelineMeshes.upk`. The scratch artifacts
  keep the brief's names (`PipelineMeshes_clone.upk`, `PipelineMeshes_m1.upk`); the tests
  copy them into a scratch directory under their deployable names before invoking umodel.

### Surprise worth recording

**umodel's glTF vertex order is a permutation of the GPU vertex-buffer order** — it rebuilds
vertices per chunk. Our vertex *i* is generally not glTF vertex *i*. The `.bin` is byte-identical
between stock Startup and our clone, so the permutation is deterministic and not a symptom of
anything wrong; but the mutate test has to match changed vertices **by position**, not by index.

The other trap in validating the bend: the mesh **bounding box does not change**. The gestalt
AR packs 47 fragments into one mesh, and the Vladof barrel tip is nowhere near an extreme of
that atlas, so comparing glTF `min`/`max` alone is blind to the edit. The test decodes the
POSITION accessor and compares all 27,211 vertices.

## 8. Validation summary

`python tests/test_writer.py` (also collected by pytest):

* summary layout, table adjacency, contiguous payloads, zeroed depends table, generation row;
* name-table flags on every row, no duplicate names, the three import rows verbatim;
* export paths, ObjectFlags, Archetype 0, NetObjectCount 0;
* mesh re-parses, all LOD self-checks pass, 38 bone names identical, 27,211 vertices and
  68,085 indices with bit-identical positions/UVs/tangents, tail `NameIndexMap` naming the
  same 38 bones in order, `serialize(entry.off)` reproducing the stored bytes;
* nested property names three levels deep (`TRISORT_None`, `TSA_X_Axis`, `None`);
* `Sockets` pointing at the 48 new socket exports; each socket's `SocketName` / `BoneName` /
  transforms / `NetIndex` identical to the original, and every `BoneName` a real bone;
* `PackageBuilder` refusing an out-of-range `Outer`;
* umodel `-list` showing the SkeletalMesh + 48 SkeletalMeshSockets;
* umodel `-export -gltf` producing 27,211 vertices / 38 joints with bounds **and a `.bin`
  byte-identical** to the stock Startup export;
* the `mutate` path moving exactly 281 `AR_Barrel_Vladof` vertices, surviving re-parse, and
  showing up in umodel's glTF as exactly 281 changed vertices, each `x*1.5` and `y+0.08`.

Corpus sweep (not in the test suite, run once): 40 SkeletalMeshes sampled at random across
`scratch/decomp_all/` — varying LOD counts, vertex colours and socket counts — all cloned,
re-read, self-checked, re-serialized byte-stably, and loaded by umodel. 40/40.
