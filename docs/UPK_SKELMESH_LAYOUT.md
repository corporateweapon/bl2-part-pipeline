# BL2 package + SkeletalMesh binary layout (UE3 ArVer 832 / LicenseeVer 46)

Source: `gildor2/UEViewer` @ master, 2026-09-19 (extracted by an agent; line refs are to that snapshot).
Key result: **BL2 (licensee 46) takes the stock UE3 path everywhere in the skeletal-mesh code.** Every
`GAME_Borderlands` deviation in `UnMesh3.cpp` is gated on `ArLicenseeVer >= 57` (BL1) or is
texture/animation/typeinfo-only. Our reader (`scratch/upkread.py`, 914/914 packages parsed with these
tables) already agrees with §1.

## 1. Package summary (v832)

| # | Field | Size |
|---|---|---|
| 1 | Tag `0x9E2A83C1` | 4 |
| 2 | Version (lo16=832, hi16=46) | 4 |
| 3 | HeadersSize | 4 |
| 4 | PackageGroup | FString |
| 5 | PackageFlags | 4 |
| 6 | NameCount, NameOffset, ExportCount, ExportOffset | 16 |
| 7 | ImportCount, ImportOffset | 8 |
| 8 | DependsOffset | 4 |
| 9 | f38, f3C, f40 (ArVer ≥ 623) | 12 |
| 10 | unk38 (ArVer ≥ 584) | 4 |
| 11 | Guid | 16 |
| 12 | GenerationCount + Generations[N] (ExportCount, NameCount, NetObjectCount) | 4 + 12N |
| 13 | EngineVersion, CookerVersion | 8 |
| 14 | CompressionFlags | 4 |
| 15 | CompressedChunks TArray<{UncompOff, UncompSize, CompOff, CompSize}> | 4 + 16N |
| 16 | U3unk60 | 4 |

FString: int32 len; len>0 → ANSI bytes incl. NUL; len<0 → UTF-16 units incl. NUL. FName in data = int32 index + int32 number (8 bytes; display `Name_{number-1}` when number≠0).
Name table entry: FString + uint64 flags. Import: ClassPackage FName, ClassName FName, Outer int32, Name FName (28 bytes).
Export (68 + 4·NetObjectCount bytes): Class int32, Super int32, Outer int32, Name FName, Archetype int32, ObjectFlags u64, SerialSize int32, SerialOffset int32 (absolute), ExportFlags u32, NetObjectCount TArray<int32>, Guid 16, PackageFlags u32.
Borderlands compression quirk (UnPackageUE3Reader.h:148): a chunk with CompressedSize == UncompressedSize is stored raw with no chunk header.

## 2. Export payload prefix (any UObject)

int32 NetIndex, then tagged property list ending with FName `None` (8 bytes). Nothing else. `Class` exports have no property list.

## 3. FPropertyTag (v832)

Name FName(8) | Type FName(8) | DataSize int32 | ArrayIndex int32 | [StructName FName if StructProperty] | [BoolValue u8 if BoolProperty] | [EnumName FName if ByteProperty].
Values: Int 4, Float 4, Bool none (in tag), Name 8, Object int32 index, Str FString, Byte 1 (raw) or 8 (enum FName), Struct = raw/tagged struct bytes, Array = int32 count + elements (POD raw; struct elements are nested tagged lists). Always `seek(valueStart + DataSize)` after reading.

## 4. USkeletalMesh native data (after the property list)

| Field | Bytes |
|---|---|
| Bounds (Origin, BoxExtent, SphereRadius) | 28 |
| Materials TArray<int32 objref> | 4 + 4n |
| MeshOrigin FVector | 12 |
| RotOrigin FRotator (Pitch, Yaw, Roll int32) | 12 |
| RefSkeleton TArray<FMeshBone> | 4 + 52n |
| SkeletalDepth int32 | 4 |
| LODModels TArray<FStaticLODModel> | var |
| tail (NameIndexMap TMap<FName,int32> = 4+12n, PerPolyKDOPs, …) | **opaque; copy verbatim** |

FMeshBone (52): Name FName 8, Flags u32, Orientation FQuat 16 (X,Y,Z,W), Position FVector 12, NumChildren int32, ParentIndex int32, BoneColor int32 (default 0xFFFFFFFF). No Length/Size at v832.
Properties (tagged, not native): `Sockets` (array of object refs), `LODInfo`, `bHasVertexColors` (drives §5 step 11).

## 5. FStaticLODModel (v832, PC)

| # | Field | Encoding |
|---|---|---|
| 1 | Sections | TArray, 13 B each: MaterialIndex i16, ChunkIndex i16, FirstIndex i32, NumTriangles **i32**, TriangleSorting u8 |
| 2 | IndexBuffer | int32 f0 (bNeedsCPUAccess?), u8 DataTypeSize (2/4), then bulk array: int32 ElementSize, int32 Count, data |
| 3 | ActiveBoneIndices | TArray<i16> |
| 4 | Chunks | TArray: BaseVertexIndex i32, RigidVertices TArray<61 B> (usually empty), SoftVertices TArray<68 B> (usually empty), BoneMap TArray<i16>, NumRigidVertices i32, NumSoftVertices i32, MaxBoneInfluences i32 |
| 5 | Size i32 | 4 |
| 6 | NumVertices i32 | 4 |
| 7 | RequiredBones | TArray<u8> |
| 8 | RawPointIndices | FIntBulkData: Flags u32, ElementCount i32, SizeOnDisk i32, **OffsetInFile i32 (ABSOLUTE)**, then inline payload unless flags & (0x01 separate file \| 0x40 separate place \| 0x20 unused) or count 0 |
| 9 | NumTexCoords i32 | 4 |
| 10 | VertexBufferGPUSkin | NumTexCoords i32, bUseFullPrecisionUVs i32, bUsePackedPosition i32, MeshExtension FVector, MeshOrigin FVector, bulk array (int32 ElementSize, int32 Count, data) |
| 11 | ColorVertexBuffer | bulk TArray<FColor BGRA> — **only if bHasVertexColors** |
| 12 | VertexInfluences | TArray (normally count 0) |

GPU vertex (v832): TangentX u32 packed, TangentZ u32 packed, BoneIndex u8[4], BoneWeight u8[4], Position FVector 12, UV[NumTexCoords] as half2 (4 B) or float2 (8 B). Stride = 28 + 4·N (half) / 28 + 8·N (float). PackedNormal byte → value = b/127.5 − 1 (no XOR fixup in UE3).
Rigid vertex (61): Pos 12, Normal[3] 12, UV[4] float2 32, Color 4, BoneIndex u8. Soft vertex (68): Pos, Normal[3], UV[4], Color, BoneIndex[4], BoneWeight[4]. Both always 4 UV sets regardless of NumTexCoords.

Self-checks: bulk stride `8 + Count·ElementSize`; `Σ Sections.NumTriangles·3 == IndexBuffer.Count`; `GPU VertexCount == NumVertices`; `RawPointIndices.ElementCount == NumVertices` (warn only); parse end ≤ SerialOffset+SerialSize.

## 6. USkeletalMeshSocket

Ordinary UObject export (NetIndex + tagged props): SocketName Name, BoneName Name, RelativeLocation Struct Vector, RelativeRotation Struct Rotator, RelativeScale Struct Vector (default 1,1,1).

## 7. Unknowns

Post-LODModels tail; depends table; IndexBuffer.f0 semantics; bulk flag bits 0x04/0x08; any gestalt-specific data (none seen in UEViewer; the 47-fragment table lives in tagged properties of the GestaltSkeletalMeshDefinition, not in the mesh).
