"""Wall (or slab, panel) with rectangular openings, built face by face - no booleans.

params = {
  length: m along X (0..length), height: m along Z (0..height), thickness: m along Y (centred),
  openings: [{x, z, w, h}]   # lower-left corner in the wall plane (m); may touch the wall edges
}
The wall plane is cut into a grid by every opening edge; solid cells get front/back faces and every
grid edge between a solid and an empty cell gets a side face. The result is closed and manifold for
any set of non-overlapping openings, deterministic, with exact corners (volume = L*H*t - sum(w*h*t)).
"""
import bmesh

from .primitives import mesh_object


def wall(name, params):
    length, height, t = float(params['length']), float(params['height']), float(params['thickness'])
    if min(length, height, t) <= 0:
        raise ValueError(f'{name}: length, height and thickness must be positive')
    openings = [(float(o['x']), float(o['z']), float(o['w']), float(o['h'])) for o in params.get('openings', [])]
    for i, (x, z, w, h) in enumerate(openings):
        if w <= 0 or h <= 0 or x < -1e-9 or z < -1e-9 or x + w > length + 1e-9 or z + h > height + 1e-9:
            raise ValueError(f'{name}: opening {i} must lie inside the wall')
        for j, (x2, z2, w2, h2) in enumerate(openings[:i]):
            if x < x2 + w2 and x2 < x + w and z < z2 + h2 and z2 < z + h:
                raise ValueError(f'{name}: openings {j} and {i} overlap')
    xs = sorted({0.0, length, *[c for x, _, w, _ in openings for c in (x, x + w)]})
    zs = sorted({0.0, height, *[c for _, z, _, h in openings for c in (z, z + h)]})
    xs = [x for i, x in enumerate(xs) if i == 0 or x - xs[i - 1] > 1e-9]
    zs = [z for i, z in enumerate(zs) if i == 0 or z - zs[i - 1] > 1e-9]

    def solid(i, k):
        if not (0 <= i < len(xs) - 1 and 0 <= k < len(zs) - 1):
            return False
        cx, cz = (xs[i] + xs[i + 1]) / 2, (zs[k] + zs[k + 1]) / 2
        return not any(x < cx < x + w and z < cz < z + h for x, z, w, h in openings)

    verts, index = [], {}

    def vid(i, k, side):
        key = (i, k, side)
        if key not in index:
            index[key] = len(verts)
            verts.append((xs[i], -t / 2 if side == 0 else t / 2, zs[k]))
        return index[key]

    faces = []
    for i in range(len(xs) - 1):
        for k in range(len(zs) - 1):
            if solid(i, k):
                faces.append((vid(i, k, 0), vid(i + 1, k, 0), vid(i + 1, k + 1, 0), vid(i, k + 1, 0)))
                faces.append((vid(i, k + 1, 1), vid(i + 1, k + 1, 1), vid(i + 1, k, 1), vid(i, k, 1)))
    for i in range(len(xs) - 1):  # horizontal grid edges (between cells k-1 and k)
        for k in range(len(zs)):
            if solid(i, k - 1) != solid(i, k):
                faces.append((vid(i, k, 0), vid(i + 1, k, 0), vid(i + 1, k, 1), vid(i, k, 1)))
    for i in range(len(xs)):  # vertical grid edges (between cells i-1 and i)
        for k in range(len(zs) - 1):
            if solid(i - 1, k) != solid(i, k):
                faces.append((vid(i, k, 0), vid(i, k, 1), vid(i, k + 1, 1), vid(i, k + 1, 0)))
    bm = bmesh.new()
    bverts = [bm.verts.new(v) for v in verts]
    for f in faces:
        bm.faces.new([bverts[i] for i in f])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.verts.index_update()
    out_faces = [[v.index for v in f.verts] for f in bm.faces]
    bm.free()
    return mesh_object(name, verts, out_faces, {'smooth': False})
