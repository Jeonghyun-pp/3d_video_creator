"""Revolve a (radius, height) profile about an axis.

params = {
  profile: [[r, h], ...]   # r >= 0 distance from axis, h along axis (m); r == 0 -> pole
  axis: 'z' (default) | 'x' | 'y',
  segments: 48, angle_deg: 360 (partial sweeps start at angle 0, toward +u),
  closed_profile: false    # true: profile is a closed loop (tyre, torus), no caps
  cap_start: true, cap_end: true,  # flat disks where the profile ends off-axis
  smooth: true, sharp_angle_deg: SHARP_ANGLE_DEG (31)
}
Angle 0 points along u, rotating toward v, with (u, v) = (x, y) for axis z,
(y, z) for axis x, (z, x) for axis y. Partial revolves get planar side faces
(profile closed back through the axis) so the result stays a solid.
"""
import math

from .primitives import mesh_object, skin

FRAME = {'z': (0, 1, 2), 'x': (1, 2, 0), 'y': (2, 0, 1)}  # (u, v, axis)


def revolve(name, params):
    axis = str(params.get('axis', 'z')).lower()
    if axis not in FRAME:
        raise ValueError(f'{name}: axis must be x, y or z')
    iu, iv, ia = FRAME[axis]
    profile = [(float(r), float(h)) for r, h in params['profile']]
    if len(profile) < 2 or any(r < 0 for r, _ in profile):
        raise ValueError(f'{name}: profile needs >= 2 points with r >= 0')
    segs = int(params.get('segments', 48))
    angle = float(params.get('angle_deg', 360.0))
    full = abs(angle) >= 360.0 - 1e-9
    closed_profile = bool(params.get('closed_profile', False))

    def point(r, h, t):
        p = [0.0, 0.0, 0.0]
        p[iu], p[iv], p[ia] = r * math.cos(t), r * math.sin(t), h
        return tuple(p)

    if full:
        # Rings = profile points (circles); poles where r == 0.
        thetas = [2 * math.pi * k / segs for k in range(segs)]
        rings = [[point(r, h, 0.0)] if r < 1e-12 else [point(r, h, t) for t in thetas] for r, h in profile]
        verts, faces = skin(rings, params.get('cap_start', True), params.get('cap_end', True), wrap=closed_profile)
        return mesh_object(name, verts, faces, params)

    # Partial revolve: rings = angular slices of the profile (closed through the axis).
    loop = list(profile)
    if not closed_profile:
        if loop[-1][0] > 1e-12:
            loop.append((0.0, loop[-1][1]))
        if loop[0][0] > 1e-12:
            loop.insert(0, (0.0, loop[0][1]))
    thetas = [math.radians(angle) * k / segs for k in range(segs + 1)]
    rings = [[point(r, h, t) for r, h in loop] for t in thetas]
    verts, faces = skin(rings, True, True)
    if not closed_profile:
        # Points on the axis are duplicated per slice; merge them so the solid is watertight.
        verts, faces = _merge_axis(verts, faces, iu, iv)
    return mesh_object(name, verts, faces, params)


def _merge_axis(verts, faces, iu, iv):
    remap, keep, seen = {}, [], {}
    for i, v in enumerate(verts):
        key = (round(v[0], 9), round(v[1], 9), round(v[2], 9)) if abs(v[iu]) < 1e-12 and abs(v[iv]) < 1e-12 else i
        if key not in seen:
            seen[key] = len(keep)
            keep.append(v)
        remap[i] = seen[key]
    out = []
    for f in faces:
        g = []
        for i in f:
            j = remap[i]
            if not g or g[-1] != j:
                g.append(j)
        if len(g) > 1 and g[0] == g[-1]:
            g.pop()
        if len(g) >= 3:
            out.append(tuple(g))
    return keep, out
