"""Retarget one Source 2 glb onto a BL2 SkeletalMesh's RefSkeleton and write a new package.

Pure numpy.  Steps: read the glb (VRF export, Source inches, Z up, Y left; skeleton from the
skin's inverse bind matrices) -> UE space (x, -y, z) * scale -> pose-fit the CS2 rest skeleton
onto the BL2 rest skeleton (rigmaps.FIT: joints pinned, directions aligned, the rest rigid-follow
their parent) -> linear-blend-skin the mesh into that pose -> merge weights onto the BL2 bones
(rigmaps.WEIGHT_MAP) -> UV atlas -> one section / one chunk -> clone_skeletal_mesh(mutate=).
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

from bl2_upk.fragment import pack_normal
from bl2_upk.reader import Package
from bl2_upk.skelmesh import Chunk, GpuVertex, Section, SkeletalMeshExport, float_to_half
from bl2_upk.writer import clone_skeletal_mesh

from . import rigmaps

MIRROR = np.diag([1.0, -1.0, 1.0])   # Source (Y left) -> UE (Y right)
_CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


# ----------------------------------------------------------------------------- glb
def load_glb(path: str | Path) -> tuple[dict, bytes]:
    b = Path(path).read_bytes()
    ln = struct.unpack_from("<I", b, 12)[0]
    doc = json.loads(b[20:20 + ln])
    pos = 20 + ln
    bin_len = struct.unpack_from("<I", b, pos)[0]
    return doc, b[pos + 8: pos + 8 + bin_len]


def accessor(doc: dict, blob: bytes, idx: int) -> np.ndarray:
    acc = doc["accessors"][idx]
    bv = doc["bufferViews"][acc["bufferView"]]
    dtype = np.dtype(_CT[acc["componentType"]])
    n = _NCOMP[acc["type"]]
    count = acc["count"]
    start = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = bv.get("byteStride", 0)
    if stride and stride != dtype.itemsize * n:
        return np.stack([np.frombuffer(blob, dtype, n, start + i * stride) for i in range(count)])
    if n > 1:
        return np.frombuffer(blob, dtype, count * n, start).reshape(count, n)
    return np.frombuffer(blob, dtype, count, start)


def quat_to_mat(q) -> np.ndarray:
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def align(a, b) -> np.ndarray:
    """Minimal rotation matrix taking unit vector a onto unit vector b."""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if np.linalg.norm(v) < 1e-9:
        if c > 0:
            return np.eye(3)
        p = np.cross(a, [1, 0, 0])
        if np.linalg.norm(p) < 1e-6:
            p = np.cross(a, [0, 1, 0])
        p /= np.linalg.norm(p)
        return 2 * np.outer(p, p) - np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1 / (1 + c))


def bl2_rest_frames(mesh: SkeletalMeshExport) -> dict[str, np.ndarray]:
    """World rest rotation (3x3) per RefSkeleton bone."""
    Rw: list[np.ndarray] = []
    for i, b in enumerate(mesh.bones):
        R = quat_to_mat(b.orientation)
        Rw.append(R if i == 0 else Rw[b.parent_index] @ R)
    return {b.name.text: Rw[i] for i, b in enumerate(mesh.bones)}


def bl2_rest_positions(mesh: SkeletalMeshExport) -> dict[str, np.ndarray]:
    """World rest position per RefSkeleton bone (BL2 quats need no W flip)."""
    Rw: list[np.ndarray] = []
    Pw: list[np.ndarray] = []
    for i, b in enumerate(mesh.bones):
        R = quat_to_mat(b.orientation)
        p = np.array(b.position, float)
        if i == 0:
            Rw.append(R)
            Pw.append(p)
        else:
            Rw.append(Rw[b.parent_index] @ R)
            Pw.append(Pw[b.parent_index] + Rw[b.parent_index] @ p)
    return {b.name.text: Pw[i] for i, b in enumerate(mesh.bones)}


# ----------------------------------------------------------------------------- build
def build_mesh(
    glb: str | Path,
    source_package: str | Path,
    source_mesh: str,
    out_path: str | Path,
    package_name: str,
    mesh_name: str,
    prim_tiles: dict[str, str],
    tiles: dict[str, list[int]],
    atlas_size: int,
    scale: float,
    *,
    keep_bones: list[str] | None = None,
    joint_offsets: dict[str, list[float]] | None = None,
    subtree_scale: dict[str, float] | None = None,
    log=print,
) -> dict:
    """Retarget ``glb`` onto ``source_mesh``'s skeleton and write ``out_path``.

    ``prim_tiles`` maps ``"<glb mesh short name>:<primitive index>"`` to a tile name in ``tiles``
    (``{name: [x, y, w, h]}`` in atlas pixels); a tile named ``"*_solid"`` maps every UV of its
    primitives to the tile centre (for constant-colour materials such as lenses).

    ``keep_bones``: source joint names; when given, only triangles whose three vertices are each
    dominated by one of these joints (or a descendant) survive -- how a first-person arms mesh is
    cut out of a body that has no separate arms model.

    ``joint_offsets``: source joint -> UE-space (x, y, z) cm added to that joint and its whole
    subtree after the fit (nudging accessories such as a purse).  ``subtree_scale``: source joint
    -> factor scaling that joint's subtree (positions and skinned geometry) about the joint.
    """
    doc, blob = load_glb(glb)
    nodes = doc["nodes"]
    parent: dict[int, int] = {}
    for i, n in enumerate(nodes):
        for c in n.get("children", []):
            parent[c] = i
    name_of = {i: n.get("name", f"node{i}") for i, n in enumerate(nodes)}

    skin = doc["skins"][0]
    joints = skin["joints"]
    ibm = accessor(doc, blob, skin["inverseBindMatrices"]).reshape(-1, 4, 4)
    jname = [name_of[j] for j in joints]
    jidx = {n: k for k, n in enumerate(jname)}
    C = MIRROR * scale
    P0 = np.zeros((len(joints), 3))
    R0 = np.zeros((len(joints), 3, 3))
    for k in range(len(joints)):
        bind = np.linalg.inv(ibm[k].T)
        P0[k] = C @ bind[:3, 3]
        R0[k] = MIRROR @ bind[:3, :3] @ MIRROR
    jparent: dict[int, int | None] = {}
    for k in range(len(joints)):
        pn = parent.get(joints[k])
        jparent[k] = jidx[name_of[pn]] if pn is not None and name_of[pn] in jidx else None
    keep_set: set[int] | None = None
    if keep_bones:
        roots = {jidx[n] for n in keep_bones if n in jidx}
        keep_set = set()
        for k in range(len(joints)):
            a: int | None = k
            while a is not None and a not in roots:
                a = jparent[a]
            if a is not None:
                keep_set.add(k)
        log(f"[{mesh_name}] keep_bones: {len(keep_set)} of {len(joints)} joints")

    pkg = Package.from_file(source_package)
    target = SkeletalMeshExport.parse(pkg.read_export_bytes(pkg.find_export(source_mesh, "SkeletalMesh")), pkg)
    knames = target.bone_names
    KP = bl2_rest_positions(target)
    KR = bl2_rest_frames(target)
    kindex = {n: i for i, n in enumerate(knames)}
    log(f"[{mesh_name}] target {source_mesh}: {len(knames)} bones; glb joints {len(joints)}")

    # ---- pose fit, hierarchy order
    order: list[int] = []

    def visit(k: int) -> None:
        order.append(k)
        for c in range(len(joints)):
            if jparent[c] == k:
                visit(c)
    for r in [k for k in range(len(joints)) if jparent[k] is None]:
        visit(r)
    D = np.zeros((len(joints), 3, 3))
    P1 = np.zeros_like(P0)
    fitted = 0
    for k in order:
        nm = jname[k]
        p = jparent[k]
        Dp = D[p] if p is not None else np.eye(3)
        Pp1 = P1[p] if p is not None else np.zeros(3)
        Pp0 = P0[p] if p is not None else np.zeros(3)
        Dk = Dp.copy()
        Pk = Pp1 + Dp @ (P0[k] - Pp0)
        fit = rigmaps.FIT.get(nm)
        if fit is not None and fit[0] in KP:
            kb, cchild, kchild = fit
            if nm not in rigmaps.NO_PIN:
                Pk = KP[kb].copy()
            if cchild is not None and cchild != "@frame" and cchild not in jidx:
                alt = {"head_0": "head", "head": "head_0"}.get(cchild)   # CS2 vs Deadlock name
                cchild = alt if alt in jidx else cchild
            if cchild == "@frame":
                # both rigs run the bone along local X with matching Y/Z senses (checked on the
                # neck/head chain): match the whole frame, so pitch AND roll follow the BL2 bone
                Dk = KR[kb] @ R0[k].T
            elif cchild is not None and cchild in jidx and kchild in KP:
                v = Dp @ (P0[jidx[cchild]] - P0[k])
                d = KP[kchild] - KP[kb]
                if np.linalg.norm(v) > 1e-6 and np.linalg.norm(d) > 1e-6:
                    Dk = align(v, d) @ Dp
            fitted += 1
        D[k] = Dk
        P1[k] = Pk
    log(f"[{mesh_name}] fitted {fitted} joints")

    def subtree(root: int) -> list[int]:
        out = [root]
        for c in range(len(joints)):
            if jparent[c] == root:
                out += subtree(c)
        return out
    for nm, off in (joint_offsets or {}).items():
        if nm in jidx:
            ids = subtree(jidx[nm])
            P1[ids] += np.array(off, float)
            log(f"[{mesh_name}] offset {nm} (+{len(ids) - 1} children) by {off}")
    for nm, factor in (subtree_scale or {}).items():
        if nm in jidx:
            root = jidx[nm]
            ids = subtree(root)
            origin = P1[root].copy()
            P1[ids] = origin + (P1[ids] - origin) * float(factor)
            D[ids] = D[ids] * float(factor)
            log(f"[{mesh_name}] scaled {nm} subtree ({len(ids)} joints) x{factor}")

    # ---- weights: CS2 joint -> BL2 bone via nearest resolvable ancestor
    jk = np.zeros(len(joints), int)
    for k in range(len(joints)):
        a: int | None = k
        while a is not None:
            tgt = rigmaps.WEIGHT_MAP.get(jname[a])
            if tgt is not None and tgt in kindex:
                break
            a = jparent[a]
        jk[k] = kindex[rigmaps.WEIGHT_MAP[jname[a]]] if a is not None else 0

    # ---- gather + skin
    V_pos, V_nrm, V_tan, V_w, V_uv, V_bw, tris = [], [], [], [], [], [], []
    base = 0
    for mesh in doc["meshes"]:
        short = mesh["name"].split(".")[-1]
        for pi, prim in enumerate(mesh["primitives"]):
            tile = prim_tiles.get(f"{short}:{pi}")
            if tile is None:
                continue
            a = prim["attributes"]
            pos = accessor(doc, blob, a["POSITION"]).astype(float) @ C.T
            nrm = accessor(doc, blob, a["NORMAL"]).astype(float) @ MIRROR.T
            tan4 = accessor(doc, blob, a["TANGENT"]).astype(float)
            tan = tan4[:, :3] @ MIRROR.T
            tw = -tan4[:, 3]
            uv = accessor(doc, blob, a["TEXCOORD_0"]).astype(float)
            J = accessor(doc, blob, a["JOINTS_0"]).astype(int)
            Wt = accessor(doc, blob, a["WEIGHTS_0"]).astype(float)
            Wt = Wt / np.clip(Wt.sum(1, keepdims=True), 1e-9, None)
            idx = accessor(doc, blob, prim["indices"]).astype(int).reshape(-1, 3)
            if keep_set is not None:
                dom = J[np.arange(len(J)), np.argmax(Wt, axis=1)]
                ok = np.isin(dom, list(keep_set))
                idx = idx[ok[idx].all(axis=1)]
                if len(idx) == 0:
                    log(f"[{mesh_name}]   {short}[{pi}] -> {tile}: nothing on the kept bones, skipped")
                    continue
            keep, inv = np.unique(idx.reshape(-1), return_inverse=True)
            idx = inv.reshape(-1, 3)
            pos, nrm, tan, tw, uv, J, Wt = pos[keep], nrm[keep], tan[keep], tw[keep], uv[keep], J[keep], Wt[keep]
            n = len(pos)
            out_p = np.zeros_like(pos)
            out_n = np.zeros_like(nrm)
            out_t = np.zeros_like(tan)
            for s in range(4):
                js = J[:, s]
                w = Wt[:, s][:, None]
                Dj = D[js]
                out_p += w * (np.einsum("nij,nj->ni", Dj, pos - P0[js]) + P1[js])
                out_n += w * np.einsum("nij,nj->ni", Dj, nrm)
                out_t += w * np.einsum("nij,nj->ni", Dj, tan)
            out_n /= np.clip(np.linalg.norm(out_n, axis=1, keepdims=True), 1e-9, None)
            out_t /= np.clip(np.linalg.norm(out_t, axis=1, keepdims=True), 1e-9, None)
            KW = np.zeros((n, len(knames)))
            for s in range(4):
                np.add.at(KW, (np.arange(n), jk[J[:, s]]), Wt[:, s])
            x, y, w_, h_ = tiles[tile]
            if tile.endswith("_solid"):
                uvm = np.tile([(x + w_ / 2) / atlas_size, (y + h_ / 2) / atlas_size], (n, 1))
            else:
                uvw = uv - np.floor(uv)
                uvm = np.stack([uvw[:, 0] * w_ / atlas_size + x / atlas_size,
                                uvw[:, 1] * h_ / atlas_size + y / atlas_size], 1)
            V_pos.append(out_p)
            V_nrm.append(out_n)
            V_tan.append(out_t)
            V_w.append(tw)
            V_uv.append(uvm)
            V_bw.append(KW)
            # BL2 winds front faces the opposite way from glTF (on Krieg's own mesh the geometric
            # face normal opposes the vertex normal on 100 % of triangles).  The Y mirror already
            # reverses glTF's orientation, so the source index order is kept as is: reversing it
            # here put every triangle on the back face and the model rendered inside-out.
            tris.append(idx + base)
            base += n
            log(f"[{mesh_name}]   {short}[{pi}] -> {tile}: {n} verts, {len(idx)} tris")
    if not V_pos:
        raise SystemExit(f"{mesh_name}: no primitive matched prim_tiles {list(prim_tiles)}")
    pos = np.concatenate(V_pos)
    nrm = np.concatenate(V_nrm)
    tan = np.concatenate(V_tan)
    tw = np.concatenate(V_w)
    uv = np.concatenate(V_uv)
    KW = np.concatenate(V_bw)
    tris = np.concatenate(tris)
    if len(pos) >= 65535:
        raise SystemExit(f"{mesh_name}: {len(pos)} vertices; the LOD index buffer is uint16")
    log(f"[{mesh_name}] total {len(pos)} verts {len(tris)} tris; bbox {pos.min(0).round(1)} .. {pos.max(0).round(1)}")

    used = sorted(int(b) for b in np.nonzero(KW.sum(0) > 0)[0])
    bone_map = used
    slot_of = {b: s for s, b in enumerate(bone_map)}
    num_uv = target.lod0.num_tex_coords
    verts: list[GpuVertex] = []
    for i in range(len(pos)):
        top = np.argsort(-KW[i])[:4]
        w = KW[i][top]
        top = top[w > 0]
        w = w[w > 0]
        w255 = np.floor(w / w.sum() * 255).astype(int)
        w255[0] += 255 - w255.sum()
        slots = [slot_of[int(b)] for b in top] + [0] * (4 - len(top))
        wts = [int(v) for v in w255] + [0] * (4 - len(top))
        tx = pack_normal(tan[i], 0x80)
        tz = pack_normal(nrm[i], 0xFF if tw[i] > 0 else 0x00)
        h = (float_to_half(float(uv[i, 0])), float_to_half(float(uv[i, 1])))
        verts.append(GpuVertex(tx, tz, slots, wts, float(pos[i, 0]), float(pos[i, 1]), float(pos[i, 2]), [h] * num_uv))
    indices = [int(v) for v in tris.reshape(-1)]

    def mutate(m: SkeletalMeshExport) -> None:
        lod = m.lod0
        lod.vertices = verts
        lod.indices = indices
        lod.sections = [Section(0, 0, 0, len(tris), 0)]
        lod.chunks = [Chunk(0, list(bone_map), 0, len(verts), 4)]
        lod.active_bone_indices = list(bone_map)
        lod.required_bones = bytes(range(len(m.bones)))
        m.materials = [0]
        lo = np.minimum(pos.min(0), np.array(m.bounds_origin) - np.array(m.bounds_extent))
        hi = np.maximum(pos.max(0), np.array(m.bounds_origin) + np.array(m.bounds_extent))
        m.bounds_origin = tuple(float(v) for v in (lo + hi) / 2)
        m.bounds_extent = tuple(float(v) for v in (hi - lo) / 2 + 5)
        m.bounds_radius = float(np.linalg.norm((hi - lo) / 2 + 5))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    res = clone_skeletal_mesh(pkg, source_mesh, out_path, package_name, mesh_name, mutate=mutate)
    p2 = Package.from_file(out_path)
    m2 = SkeletalMeshExport.parse(p2.read_export_bytes(p2.find_export(mesh_name, "SkeletalMesh")), p2)
    m2.lod0.validate()
    log(f"[{mesh_name}] wrote {out_path} ({out_path.stat().st_size} bytes): {m2.lod0.num_vertices} verts, "
        f"{m2.lod0.triangle_count} tris, {len(m2.lod0.chunks[0].bone_map)} bones in chunk, sockets {res.socket_names}")
    return {"package": package_name, "mesh": res.mesh_path, "out": str(out_path), "verts": len(verts),
            "tris": int(len(tris)), "bones": [knames[b] for b in bone_map], "source_mesh": source_mesh}
