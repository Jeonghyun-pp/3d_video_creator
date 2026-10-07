"""Sweep a 2D profile along a 3D polyline with parallel-transport frames.

params = {
  profile: {type: circle, r} | {points: [[u, v], ...]} | any loft section dict
           (ellipse/superellipse/rect with a, b),
  path: [[x, y, z], ...] (m), closed: false,
  segments: 16 (profile vertices), twist_deg: 0 (linear over path length),
  path_smooth: 'none' | 'catmull_rom' (a smooth curve through the path points, path_samples per segment: 8),
  scale: 1 | [[u, s], ...] (profile scale along the normalised length: tapers, flares, bulges),
  cap_start: true, cap_end: true, smooth: true, sharp_angle_deg: 30
}
Frames are rotation-minimising (each normal is the previous one rotated by the
minimal rotation between consecutive tangents), so there are no Frenet flips
at inflections or on straight runs. Closed paths spread the residual holonomy
angle evenly so the seam matches. The first normal is the world axis least
aligned with the first tangent, projected (deterministic).
"""
import math

from mathutils import Quaternion, Vector

from path_core import catmull_rom, scale_at  # studio/blender_ops on sys.path

from .loft import section_ring
from .primitives import mesh_object, skin


def _profile(profile, count):
    if profile.get('type') == 'circle':
        r = float(profile['r'])
        return [(r * math.cos(2 * math.pi * k / count), r * math.sin(2 * math.pi * k / count)) for k in range(count)]
    if 'points' in profile and 'type' not in profile:
        profile = dict(profile, type='points')
    return section_ring(profile, count)


def frames(path, closed=False):
    """Return [(tangent, normal, binormal)] per path point."""
    pts = [Vector(p) for p in path]
    n = len(pts)
    tangents = []
    for i in range(n):
        if closed:
            t = pts[(i + 1) % n] - pts[i - 1]
        elif i == 0:
            t = pts[1] - pts[0]
        elif i == n - 1:
            t = pts[-1] - pts[-2]
        else:
            t = (pts[i + 1] - pts[i]).normalized() + (pts[i] - pts[i - 1]).normalized()
            if t.length < 1e-12:
                t = pts[i + 1] - pts[i]
        if t.length < 1e-12:
            raise ValueError('sweep path has coincident consecutive points')
        tangents.append(t.normalized())
    t0 = tangents[0]
    ref = min((Vector(a) for a in ((1, 0, 0), (0, 1, 0), (0, 0, 1))), key=lambda a: abs(a.dot(t0)))
    normal = (ref - t0 * ref.dot(t0)).normalized()
    normals = [normal]
    for i in range(1, n):
        q = tangents[i - 1].rotation_difference(tangents[i])
        normal = q @ normals[-1]
        normal = (normal - tangents[i] * normal.dot(tangents[i])).normalized()
        normals.append(normal)
    if closed:
        back = tangents[-1].rotation_difference(tangents[0]) @ normals[-1]
        b0 = tangents[0].cross(normals[0])
        residual = math.atan2(back.dot(b0), back.dot(normals[0]))  # angle of transported normal vs start
        for i in range(n):
            q = Quaternion(tangents[i], -residual * i / n)
            normals[i] = q @ normals[i]
    return [(t, nrm, t.cross(nrm)) for t, nrm in zip(tangents, normals)]


def sweep(name, params):
    path = params['path']
    if len(path) < 2:
        raise ValueError(f'{name}: sweep path needs >= 2 points')
    closed = bool(params.get('closed', False))
    smooth_path = params.get('path_smooth', 'none')
    if smooth_path not in ('none', 'catmull_rom'):
        raise ValueError(f"{name}: path_smooth is 'none' or 'catmull_rom'")
    if smooth_path == 'catmull_rom':
        path = catmull_rom(path, int(params.get('path_samples', 8)), closed)
    profile = _profile(params['profile'], int(params.get('segments', 16)))
    scale = params.get('scale', 1.0)
    fr = frames(path, closed)
    lengths = [0.0]
    for a, b in zip(path, path[1:]):
        lengths.append(lengths[-1] + math.dist(a, b))
    total = lengths[-1] + (math.dist(path[-1], path[0]) if closed else 0.0)
    twist = math.radians(float(params.get('twist_deg', 0.0)))
    rings = []
    for p, (t, nrm, bi), s in zip(path, fr, lengths):
        a = twist * s / total if total > 0 else 0.0
        ca, sa = math.cos(a), math.sin(a)
        u_ax, v_ax = nrm * ca + bi * sa, -nrm * sa + bi * ca
        base = Vector(p)
        k = scale_at(scale, s / total if total > 0 else 0.0)
        rings.append([tuple(base + (u_ax * u + v_ax * v) * k) for u, v in profile])
    verts, faces = skin(rings, params.get('cap_start', True), params.get('cap_end', True), wrap=closed)
    return mesh_object(name, verts, faces, params)
