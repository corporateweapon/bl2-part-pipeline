# BL2 UPK SkeletalMesh codec — implementation notes

Package: `src/bl2_upk/` (`reader.py`, `props.py`, `skelmesh.py`, `patch.py`).
Reference mesh: `Weap_AssaultRifles.GestaltDef_AssaultRifle_GestaltSkeletalMesh`
in `scratch/decomp/Startup.upk` (export offset 29,915,482, SerialSize 1,337,044,
export-table entry at file offset 4,167,135, entry size 68, NetObjectCount 0).

## 1. What was verified

Every claim in `docs/UPK_SKELMESH_LAYOUT.md` §§2–6 was checked against the real
bytes. The spec is accurate; no field order, size or conditional needed changing.

Ground truth reproduced exactly by the parser:

| Item | Value | Offset in payload |
|---|---|---|
| NetIndex | 48 | 0 |
| Property block | `ReferencePoseBounds`, `Sockets` (48 object refs), `LODInfo` | 4 … 709 |
| `bUseFullPrecisionUVs` | 0 (absent from props; the LOD flag is 0) | 139,220 |
| Bounds origin | (−3.8e−06, −47.7397, 2.2525) | 710 |
| Materials | 1 entry, object index 0 → `None` | 738 |
| RefSkeleton | 38 bones, 52 B each, names match the expected list | 770 … 2,749 |
| SkeletalDepth | 4 | 2,750 |
| LODModels | 1 LOD | 2,754 |
| Sections | 1, NumTriangles 22,695 | 2,758 |
| IndexBuffer | `f0`=1, `DataTypeSize`=2, 68,085 × uint16 | 2,775 (data at 2,788) |
| Chunks | 1, BoneMap 38, NumRigid 27,209 / NumSoft 2, MaxBoneInfluences 4 | 139,038 |
| NumVertices | 27,211 | 139,150 |
| NumTexCoords | 4 | 139,212 |
| GPU vertex buffer | stride 44 = 28 + 4·4, 27,211 verts | 139,252 (data at 139,260) |
| Tail | 496 B | 1,336,548 |

Self-checks that hold: Σ section triangles × 3 == 68,085 == index count; GPU
vertex count == NumVertices; Σ chunk (NumRigid + NumSoft) == NumVertices; index
bulk `ElementSize == DataTypeSize == 2`; bulk stride `8 + Count·ElementSize` for
both the index and vertex arrays; max index 27,210 < NumVertices.

Cross-check against the umodel glTF export (`scratch/umodel_out/.../*.gltf`):
27,211 positions and, after umodel's ÷100 scale and Z-up→Y-up swap, the same
bounding box — our x → glTF x, our z → glTF y, our y → glTF z, all three axis
ranges matching to the last printed digit.

**Corpus validation:** all **1,660 `SkeletalMesh` exports across all 914
decompressed packages** (`scratch/decomp_all/`) parse and re-serialize
**byte-identically**, including 28 two-LOD meshes, 13 four-LOD meshes and 518
meshes with `bHasVertexColors` (so the multi-LOD TArray and the
ColorVertexBuffer branch are both exercised, not just assumed).

## 2. Deviations from the spec / things it did not say

1. **`bUsePackedPosition` is set but inert.** 1,418 of 1,726 LODs have
   `bUsePackedPosition == 1`, yet the serialized stride is always
   `28 + 4·NumTexCoords`, i.e. positions are plain 12-byte `FVector`s and
   `MeshExtension`/`MeshOrigin` are the identity (1,1,1)/(0,0,0). On PC at
   ArVer 832 the flag has no effect on the layout. The codec therefore treats
   the **stride as authoritative** and only raises if it disagrees with the
   unpacked layout. (The gestalt AR itself has the flag clear.)
2. **The class-default object has no native data.** `Engine.Default__SkeletalMesh`
   is NetIndex + property list and then the payload ends. `SkeletalMeshExport`
   detects this (`has_native_data = False`) instead of running off the end.
3. **Spec §5 row 1 says Sections are 13 B each** — confirmed, but note the
   `TriangleSorting` byte is *outside* the 12-byte `<hhii>` head, so a naive
   `struct` format of the whole record would be padded to 16 without `<`.
4. **Chunk rigid/soft ordering is not honoured.** UE3 normally lays out rigid
   vertices before soft ones inside a chunk; in the gestalt AR the only two
   multi-influence vertices are at indices 11,006 and 11,007, not at the end.
   The counts are still exactly recoverable by classifying each vertex by how
   many non-zero bone weights it has (27,209 / 2), which is what `sync_counts()`
   does — so nothing depends on the ordering assumption.
5. **§4's tail is not actually opaque** — see §4 below.
6. `VertexInfluences` is a count-0 `TArray` in every mesh in the corpus. The
   parser raises rather than guessing if it is ever non-zero.

## 3. Meaning of the fields that had to be inferred

| Field | Observed | Interpretation / handling |
|---|---|---|
| `FStaticLODModel.Size` | **0 in all 1,726 LODs** | UE3's transient "bytes used by this LOD" cache; the cooker writes 0. Not recomputed — the parsed value is preserved verbatim on serialize. Documented as unknown-but-constant rather than guessed. |
| `IndexBuffer.f0` (int32 before `DataTypeSize`) | **1 in all 1,726 LODs** | `FMultiSizeIndexContainer`'s `bNeedsCPUAccess` (stored as int32). Constant in shipped data; preserved verbatim. |
| `IndexBuffer.DataTypeSize` (u8) | **2 everywhere** | uint16 indices. The following bulk-array `ElementSize` always equals it; the parser asserts that rather than trusting one of the two. |
| `RawPointIndices` flags | **0x00 everywhere** | No `BULKDATA_StoreInSeparateFile` (0x01), `_Unused` (0x20) or `_SeparateStorage` (0x40) bit, so the payload would be inline. `ElementCount`/`SizeOnDisk` are 0 in every mesh — the cooker strips the raw point indices — but `OffsetInFile` is still written as a real absolute offset pointing just past the 16-byte header (29,915,482 + 139,212 = 30,054,694 for the gestalt AR). Bits 0x04/0x08 were never observed set. |
| `RawPointIndices.OffsetInFile` | absolute, file-relative | Written as `export_offset + (position of the byte after the 16-byte bulk header)`. This is why `serialize()` takes an `export_offset` argument. |
| GPU vertex `TangentX`/`TangentZ` | packed u32 | Kept as raw u32. Per spec, unpack a byte as `b/127.5 − 1`; no UE3 XOR fixup. |
| GPU vertex UVs | raw `half2` | Kept as raw `uint16` pairs; `half_to_float` / `float_to_half` convert (stdlib `struct` format `"<e"`). **UV sets 2 and 3 of the gestalt meshes are not UVs**: vertex 0 reads (0.5806, 0.3237), (−0.4333, −0.0473), (−29568.0, 0.2404), (−748.0, 0.2404). The out-of-range channels are gestalt part/fragment payload data, so they must be copied bit-exactly rather than round-tripped through float32 — which is why they are stored raw. |
| `Chunk.MaxBoneInfluences` | 1 / 2 / 3 / 4 | Recomputed as `max(old, max influences in range)` so it can only grow, never silently shrink below what the runtime expects. |
| Array property element type | not stored by UE3 | `props.py` decodes an `ArrayProperty` to a list of int32 when `DataSize − 4 == Count · 4` (this is the `Sockets` object-ref case); otherwise the body is kept raw. Raw bytes are always retained, so the heuristic can never break the round-trip. |

## 4. The tail after `LODModels`

For the gestalt AR the tail is **496 bytes**, starting at payload offset
1,336,548, first bytes `26 00 00 00 5B 4F 00 00 00 00 00 00 …`.

It is **not** opaque — it decodes completely and the test asserts that every byte
is accounted for:

| Bytes | Field | Value here |
|---|---|---|
| 4 | `NameIndexMap` count | **38** (one per bone) |
| 38 × 12 | `TMap<FName,int32>` entries | `Root→0`, `WeaponOffset→1`, … `JacobSpinner→37`; exactly the RefSkeleton order |
| 4 | `PerPolyBoneKDOPs` count | 0 |
| 4 | `BoneBreakNames` count | 0 |
| 4 | `BoneBreakOptions` count | 0 |
| 4 | `ClothingAssets` count | 0 |
| 4 + 4×4 | `CachedStreamingTextureFactors` | count 4, values 134.463, 142.185, 1.23306, 1.21661 — one per UV set |

Total 4 + 456 + 16 + 20 = 496. Tail size therefore tracks bone count and UV-set
count (e.g. 64 B for the 2-bone/1-UV grenade gestalt, 100 B for the artifact
gestalt). The codec still stores it as `bytes` and copies it verbatim, because
nothing we edit changes it — but the decode above is asserted in the test so a
future structural surprise fails loudly.

Note: `VertexInfluences` (the trailing count-0 `TArray` of `FStaticLODModel`)
belongs to the LOD, not the tail; it sits at 1,336,544.

## 5. Editing semantics

`SkeletalMeshExport.serialize(export_offset)` recomputes, from the mutable data:

* `NumVertices` = `len(lod.vertices)`;
* vertex bulk `ElementSize` = `28 + 4·NumTexCoords` and `Count` = vertex count;
* index bulk `ElementSize` = `DataTypeSize`, `Count` = `len(lod.indices)`;
* `NumTexCoords` / the vertex buffer's copy of it, from `len(vertex.uvs)`;
* the **last** section's `NumTriangles`, absorbing any change in index count
  (an index count that is not a multiple of 3 is an error);
* per chunk, `NumRigidVertices` / `NumSoftVertices` by classifying each vertex in
  that chunk's index range (1 non-zero weight → rigid, more → soft), and
  `MaxBoneInfluences`;
* `RawPointIndices` `ElementCount` = `len(payload)//4`, `SizeOnDisk` =
  `len(payload)`, `OffsetInFile` = absolute position of the payload.

Not recomputed (preserved verbatim, deliberately): `Size` (always 0, meaning
unknown), `IndexBuffer.f0`, `ActiveBoneIndices`, `RequiredBones`, `BoneMap`,
`Bounds`, and the tail. A mesh edit that adds bones or grows the bounding box
must update those itself.

`patch.replace_export_payload(..., strategy="relocate_to_end")` appends the
payload at the current end of file and rewrites only `SerialSize` (entry byte 32)
and `SerialOffset` (entry byte 36) of that one export entry; entry stride is
`68 + 4·NetObjectCount`. Because the bulk `OffsetInFile` is absolute, callers
must serialize with `export_offset = os.path.getsize(source_package)` before
calling it — the test does exactly that and then asserts
`mesh2.serialize(entry.off) == payload_read_back`, which proves the stored
absolute offsets match where the export actually landed. Both strategies re-read
the output with `Package` and re-parse the mesh before returning.
