"""Procedural ribbed rebar cage in pure bpy + numpy (runs inside Blender).

Ported from the 09_procedural_cad prototype without scene reset, rendering or
paths. Square column cage: longitudinal bars at the 4 corners (+4 mid-faces
for 8), closed ties with 135 degree hooks, hook extension max(6 db, 75 mm),
inner bend radius 2 db (ACI 318 25.3.2 / KDS 14 20 50 style). Bar dimensions
come from spec.rebar_cage_params (KS D 3504 table rows). Deterministic: the
same params give bit-identical vertex data.

params = {'name_prefix', 'column_mm', 'cover_mm', 'length_mm', 'tie_spacing_mm',
          'longitudinal_count': 4 | 8,
          'longitudinal': {'designation', 'd', 'rib_spacing', 'rib_height'},
          'tie': {...same keys...}}
"""
import hashlib
import math

import bpy
import numpy as np

MM = 0.001


def turtle(start, heading_deg, ops, step):
    """ops: ('L', length) | ('A', turn_deg, centreline_radius) -> dense Nx2 polyline (mm)."""
    p = np.array(start, float)
    h = math.radians(heading_deg)
    pts = [p.copy()]
    for op in ops:
        if op[0] == 'L':
            n = max(1, int(op[1] / step))
            for i in range(1, n + 1):
                pts.append(p + np.array([math.cos(h), math.sin(h)]) * op[1] * i / n)
            p = pts[-1].copy()
        else:
            turn, radius = math.radians(op[1]), op[2]
            sign = 1 if turn > 0 else -1
            centre = p + radius * np.array([math.cos(h + sign * math.pi / 2), math.sin(h + sign * math.pi / 2)])
            a0 = math.atan2(*(p - centre)[::-1])
            n = max(2, int(abs(turn) * radius / step))
            for i in range(1, n + 1):
                a = a0 + turn * i / n
                pts.append(centre + radius * np.array([math.cos(a), math.sin(a)]))
            p = pts[-1].copy()
            h += turn
    return np.array(pts)


def ribbed_tube(path, bar, sides, name, collection, incl_deg=60):
    """Sweep a deformed-bar section along path (Nx3, mm): 2 longitudinal ribs + inclined transverse ribs."""
    d, spacing, rib = bar['d'], bar['rib_spacing'], bar['rib_height']
    r0 = d / 2 - rib * 0.35  # core radius so the nominal diameter is roughly the mean
    seg = np.diff(path, axis=0)
    arc = np.r_[0, np.cumsum(np.linalg.norm(seg, axis=1))]
    tangent = np.gradient(path, axis=0)
    tangent /= np.linalg.norm(tangent, axis=1)[:, None]
    up = np.array([0, 0, 1.0]) if abs(tangent[0][2]) < 0.9 else np.array([1.0, 0, 0])
    normal = np.zeros_like(tangent)
    normal[0] = np.cross(tangent[0], up)
    normal[0] /= np.linalg.norm(normal[0])
    for i in range(1, len(tangent)):  # parallel-transport frames
        n = normal[i - 1] - tangent[i] * np.dot(normal[i - 1], tangent[i])
        normal[i] = n / np.linalg.norm(n)
    binormal = np.cross(tangent, normal)
    theta = np.linspace(0, 2 * np.pi, sides, endpoint=False)
    s, t = arc[:, None], theta[None, :]
    half = np.sin(t) >= 0
    phase = s / spacing + (np.tan(math.radians(90 - incl_deg)) * (r0 * t) / spacing) * np.where(half, 1, -1) + np.where(half, 0, 0.5)
    q = (phase - np.round(phase)) * spacing  # mm to the nearest transverse rib centreline
    width = max(0.9, spacing * 0.12)
    transverse = rib * np.clip(1 - (q / width) ** 2, 0, None) * np.clip(np.abs(np.sin(t)) * 1.6, 0, 1)
    to_rib = np.minimum(np.abs(np.angle(np.exp(1j * t))), np.abs(np.angle(np.exp(1j * (t - np.pi)))))
    longitudinal = rib * 0.9 * np.clip(1 - (to_rib / 0.12) ** 2, 0, None)
    radius = r0 + np.maximum(transverse, longitudinal)
    verts = path[:, None, :] + radius[..., None] * (np.cos(t)[..., None] * normal[:, None, :] + np.sin(t)[..., None] * binormal[:, None, :])
    verts = verts.reshape(-1, 3) * MM
    rings, count = len(path), sides
    faces = [(i * count + j, i * count + (j + 1) % count, i * count + (j + 1) % count + count, i * count + j + count)
             for i in range(rings - 1) for j in range(count)]
    faces.append(tuple(range(count))[::-1])  # sheared bar-end caps
    faces.append(tuple(range((rings - 1) * count, rings * count)))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts.tolist(), [], faces)
    mesh.update()
    mesh.shade_smooth()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    return obj


def build_rebar_cage(params, collection=None, material=None):
    """Create tie + longitudinal bar objects (metres, cage base at z=0) and return them in build order."""
    collection = collection or bpy.context.scene.collection
    prefix = params.get('name_prefix', 'rebar_cage')
    col_b, cover, length, spacing = params['column_mm'], params['cover_mm'], params['length_mm'], params['tie_spacing_mm']
    long_bar, tie_bar = params['longitudinal'], params['tie']
    dl, dt = long_bar['d'], tie_bar['d']
    side = col_b - 2 * cover - dt          # tie centreline square
    bend = 2 * dt + dt / 2                 # centreline bend radius (inner 2 db)
    hook = max(6 * dt, 75.0)
    straight = side - 2 * bend
    if straight <= 0 or params['longitudinal_count'] not in (4, 8) or spacing <= 0 or length <= spacing:
        raise ValueError('Rebar cage parameters leave no straight tie leg, or count is not 4/8, or spacing/length invalid')
    ops = [('L', hook), ('A', -135, bend), ('L', straight), ('A', -90, bend), ('L', straight), ('A', -90, bend),
           ('L', straight), ('A', -90, bend), ('L', straight), ('A', -135, bend), ('L', hook)]
    tie2d = turtle((0, 0), 135, ops, 1.0)
    tie2d -= (tie2d.min(0) + tie2d.max(0)) / 2  # bbox of the polyline is the square; hooks stay inside
    objects = []

    def finish(obj, role, bar):
        obj['studio_rebar_role'] = role
        obj['studio_rebar_designation'] = bar['designation']
        if material is not None:
            obj.data.materials.append(material)
        objects.append(obj)

    ramp = np.linspace(0, dt * 1.05, len(tie2d))  # lap the two hook ends past each other
    for k in range(int(length // spacing)):
        z = spacing / 2 + k * spacing
        obj = ribbed_tube(np.c_[tie2d, z + ramp], tie_bar, 16, f"{prefix}_tie_{tie_bar['designation']}_{k:02d}", collection)
        if k % 2:
            obj.rotation_euler[2] = math.pi  # alternate hook corners between successive ties
        finish(obj, 'tie', tie_bar)
    in_face = side / 2 - dt / 2
    corner = (side / 2 - bend) + (bend - dt / 2 - dl / 2) / math.sqrt(2)
    positions = [(sx * corner, sy * corner) for sx in (-1, 1) for sy in (-1, 1)]
    if params['longitudinal_count'] == 8:
        positions += [(0, in_face - dl / 2), (0, -(in_face - dl / 2)), (in_face - dl / 2, 0), (-(in_face - dl / 2), 0)]
    rings = int(length / 2) + 1
    for i, (x, y) in enumerate(positions):
        path = np.c_[np.full(rings, x), np.full(rings, y), np.linspace(0, length, rings)]
        finish(ribbed_tube(path, long_bar, 28, f"{prefix}_long_{long_bar['designation']}_{i}", collection), 'longitudinal', long_bar)
    return objects


def geometry_hash(objects):
    """SHA-256 over vertex coordinates, faces and world matrices (names excluded)."""
    bpy.context.view_layer.update()
    digest = hashlib.sha256()
    for obj in objects:
        mesh = obj.data
        co = np.empty(len(mesh.vertices) * 3, np.float32)
        mesh.vertices.foreach_get('co', co)
        loops = np.empty(len(mesh.loops), np.int32)
        mesh.loops.foreach_get('vertex_index', loops)
        digest.update(co.tobytes())
        digest.update(loops.tobytes())
        digest.update(np.array(obj.matrix_world, np.float64).tobytes())
    return digest.hexdigest()
