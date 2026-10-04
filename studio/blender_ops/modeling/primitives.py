"""Shared mesh construction + the box primitive.

Every builder produces plain (verts, faces) in metres and hands them to
``mesh_object``, which owns orientation, smoothing and linking so all builders
behave identically.

Smoothing in Blender 5.x: meshes have no auto-smooth flag any more. We call
``Mesh.shade_smooth()`` and then ``Mesh.set_sharp_from_angle(angle)``, which
writes the ``sharp_edge`` attribute for edges whose dihedral angle exceeds the
threshold. Pure mesh data, no modifier, no operator context, works headless
with --factory-startup and is deterministic.
"""
import math

import bmesh
import bpy

SHARP_ANGLE_DEG = 30.0


def axis_index(axis):
    try:
        return 'xyz'.index(str(axis).lower())
    except ValueError:
        raise ValueError(f'axis must be x, y or z, got {axis!r}') from None


def signed_volume(verts, faces):
    """Divergence-theorem volume about the vertex centroid (sign = orientation)."""
    if not verts:
        return 0.0
    n = len(verts)
    cx = sum(v[0] for v in verts) / n
    cy = sum(v[1] for v in verts) / n
    cz = sum(v[2] for v in verts) / n
    total = 0.0
    for face in faces:
        a = verts[face[0]]
        for i in range(1, len(face) - 1):
            b, c = verts[face[i]], verts[face[i + 1]]
            ax, ay, az = a[0] - cx, a[1] - cy, a[2] - cz
            bx, by, bz = b[0] - cx, b[1] - cy, b[2] - cz
            qx, qy, qz = c[0] - cx, c[1] - cy, c[2] - cz
            total += ax * (by * qz - bz * qy) - ay * (bx * qz - bz * qx) + az * (bx * qy - by * qx)
    return total / 6.0


def skin(rings, cap_start=True, cap_end=True, wrap=False):
    """Bridge equal-count closed rings into (verts, faces).

    A ring with one point is a pole (collapsed tip): it is joined with a
    triangle fan and never capped. ``wrap`` joins the last ring back to the
    first (closed sweep / torus) and disables caps. Winding is consistent;
    ``mesh_object`` makes it point outward.
    """
    counts = {len(r) for r in rings if len(r) > 1}
    if len(counts) != 1:
        raise ValueError(f'rings must share one vertex count (>1), got {sorted(counts)}')
    verts, index = [], []
    for ring in rings:
        index.append(list(range(len(verts), len(verts) + len(ring))))
        verts.extend(ring)
    faces = []
    pairs = list(zip(index, index[1:])) + ([(index[-1], index[0])] if wrap else [])
    for a, b in pairs:
        if len(a) == 1 and len(b) == 1:
            raise ValueError('two adjacent collapsed stations')
        n = max(len(a), len(b))
        for j in range(n):
            k = (j + 1) % n
            if len(a) == 1:
                faces.append((a[0], b[k], b[j]))
            elif len(b) == 1:
                faces.append((a[j], a[k], b[0]))
            else:
                faces.append((a[j], a[k], b[k], b[j]))
    if not wrap:
        if cap_start and len(index[0]) > 2:
            faces.append(tuple(reversed(index[0])))
        if cap_end and len(index[-1]) > 2:
            faces.append(tuple(index[-1]))
    return verts, faces


def mesh_object(name, verts, faces, params=None, collection=None):
    """Create a linked mesh object; faces are flipped if they point inward.

    params keys honoured: smooth (True), sharp_angle_deg (30).
    """
    params = params or {}
    verts = [tuple(float(c) for c in v) for v in verts]
    for v in verts:
        if not all(math.isfinite(c) for c in v):
            raise ValueError(f'{name}: non-finite vertex {v}')
    faces = [tuple(f) for f in faces]
    if signed_volume(verts, faces) < 0:
        faces = [tuple(reversed(f)) for f in faces]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.validate(clean_customdata=False)
    mesh.update()
    apply_smoothing(mesh, params)
    obj = bpy.data.objects.new(name, mesh)
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def apply_smoothing(mesh, params):
    if params.get('smooth', True):
        mesh.shade_smooth()
        mesh.set_sharp_from_angle(angle=math.radians(float(params.get('sharp_angle_deg', SHARP_ANGLE_DEG))))
    else:
        mesh.shade_flat()


def box(name, params):
    """params: {size: [x, y, z] (m), bevel_m: 0, bevel_segments: 3, smooth}."""
    sx, sy, sz = (float(v) for v in params['size'])
    if min(sx, sy, sz) <= 0:
        raise ValueError(f'{name}: box size must be positive')
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(sx, sy, sz), verts=bm.verts)
    bevel = float(params.get('bevel_m', 0.0))
    if bevel > 0:
        bevel = min(bevel, 0.499 * min(sx, sy, sz))
        bmesh.ops.bevel(bm, geom=list(bm.edges) + list(bm.verts), offset=bevel, offset_type='OFFSET',
                        segments=int(params.get('bevel_segments', 3)), profile=0.5, affect='EDGES',
                        clamp_overlap=True)
    bm.verts.ensure_lookup_table()
    verts = [tuple(v.co) for v in bm.verts]
    faces = [[v.index for v in f.verts] for f in bm.faces]
    bm.free()
    return mesh_object(name, verts, faces, params)
