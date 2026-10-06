"""Gear geometry and planetary layout, pure Python (no bpy): the numbers a gear needs come from the gear's own
definition (module, tooth counts, pressure angle) - never typed by hand.

  gear_outline(m, z)          external involute gear, one closed polygon (metres)
  internal_tooth(m, z_ring)   one tooth of an internal (ring) gear, centred on +x, for a rotational array
  planetary_layout(m, zs, zp, zr, n)   planet centres and phases so every tooth meets a gap (sun, planets, ring)
  planetary_ratio(zs, zr)     carrier turns per sun turn with the ring fixed
"""
from __future__ import annotations

import math

PRESSURE_DEG = 20.0


def _inv(a):
    return math.tan(a) - a


def _flank_angle(rb, r):
    """Polar angle of the involute at radius r (from its start on the base circle)."""
    t = math.sqrt(max(0.0, (r / rb) ** 2 - 1.0))
    return t - math.atan(t)


def gear_outline(m, z, pressure_deg=PRESSURE_DEG, flank_points=6, root_points=3):
    """CCW polygon of an external involute spur gear: tooth k centred at 2*pi*k/z (tooth 0 on +x)."""
    if z < 8:
        raise ValueError(f'gear: {z} teeth undercut too much (need >= 8)')
    alpha = math.radians(pressure_deg)
    rp, ra, rf = m * z / 2, m * z / 2 + m, m * z / 2 - 1.25 * m
    rb = rp * math.cos(alpha)
    half = math.pi / (2 * z) + _inv(alpha)          # half tooth angle on the base circle
    tip = half - _flank_angle(rb, ra)
    if tip <= 0:
        raise ValueError(f'gear: teeth come to a point (z={z}, m={m})')
    start = max(rb, rf)
    radii = [start + (ra - start) * i / (flank_points - 1) for i in range(flank_points)]
    points = []
    for k in range(z):
        c = 2 * math.pi * k / z
        if rf < rb:
            points.append((rf, c - half))                                   # radial foot below the base circle
        points += [(r, c - half + _flank_angle(rb, r)) for r in radii]      # rising flank
        points += [(ra, c - tip + 2 * tip * i / 3) for i in range(1, 3)]   # tip land
        points += [(r, c + half - _flank_angle(rb, r)) for r in reversed(radii)]
        if rf < rb:
            points.append((rf, c + half))
        nxt = 2 * math.pi * (k + 1) / z - half
        points += [(rf, c + half + (nxt - c - half) * i / (root_points + 1)) for i in range(1, root_points + 1)]
    return [(r * math.cos(a), r * math.sin(a)) for r, a in points]


def internal_tooth(m, z_ring, pressure_deg=PRESSURE_DEG, flank_points=5, depth_extra=0.25):
    """One tooth of an internal gear with z_ring teeth, centred on +x: the material between two gaps that receive an
    external gear's teeth. Its root reaches past the ring's inner radius so a rim can be joined to it."""
    alpha = math.radians(pressure_deg)
    rp = m * z_ring / 2
    rb = rp * math.cos(alpha)
    r_in, r_out = rp - m, rp + 1.25 * m + depth_extra * m           # tip (inner) and root (outer, into the rim)
    gap_half = math.pi / (2 * z_ring) + _inv(alpha)                  # a gap is shaped like an external tooth
    tooth_half_at = lambda r: math.pi / z_ring - (gap_half - _flank_angle(rb, max(r, rb)))  # noqa: E731
    radii = [r_in + (r_out - r_in) * i / (flank_points - 1) for i in range(flank_points)]
    side = [(r, tooth_half_at(r)) for r in radii]
    outline = [(r, -a) for r, a in side] + [(r, a) for r, a in reversed(side)]
    return [(r * math.cos(a), r * math.sin(a)) for r, a in outline]


def planetary_ratio(zs, zr):
    return zs / (zs + zr)


def planetary_layout(m, zs, zp, zr, n):
    """Centres and phase angles (radians) of n planets around a sun of zs teeth inside a ring of zr teeth, with the
    sun's tooth 0 on +x and the ring's tooth 0 on +x rotated by ring_phase."""
    if zr != zs + 2 * zp:
        raise ValueError(f'planetary: ring teeth {zr} must equal sun + 2 x planet ({zs + 2 * zp}) for one module')
    if zp < 17:   # an undercut planet root lets the ring's tooth tips in (measured: 13 teeth in a 46-tooth ring collide)
        raise ValueError(f'planetary: planets need >= 17 teeth inside a ring (got {zp})')
    if (zs + zr) % n:
        raise ValueError(f'planetary: (sun + ring) {zs + zr} must divide by {n} planets for equal spacing')
    centre = m * (zs + zp) / 2
    planets = []
    for i in range(n):
        theta = 2 * math.pi * i / n
        # along the line of centres a sun tooth must meet a planet gap: sun phase there + planet phase = half a tooth
        sun_teeth = theta * zs / (2 * math.pi)
        planet_phase = theta + math.pi - (2 * math.pi / zp) * (0.5 - sun_teeth)
        planets.append({'centre': [centre * math.cos(theta), centre * math.sin(theta)], 'theta': theta, 'phase': planet_phase})
    # the ring meets planet 0 on the far side (direction 0): there the planet shows (0 - phase) * zp / 2pi teeth
    shown = (0.0 - planets[0]['phase']) * zp / (2 * math.pi)
    ring_phase = (2 * math.pi / zr) * ((shown + 0.5) % 1.0)          # where the planet shows a tooth, the ring has a gap
    return {'centre_distance': centre, 'planets': planets, 'ring_phase': ring_phase, 'ratio': planetary_ratio(zs, zr)}


# --- strain wave (harmonic) gearing -------------------------------------------------------------------------------
# A flexspline (thin external-toothed ring, zf teeth) is pushed into an ellipse by a wave generator inside it, so its
# teeth mesh with a rigid circular spline (zc = zf + 2 internal teeth) only around the ellipse's major axis. Turning the
# wave generator once walks the mesh around the ring and the flexspline lags by two teeth: ratio -(zc - zf) / zf.
# Involute teeth with a full addendum collide (radial room between the pitch circles is one module); short trapezoid
# teeth do not. Measured 2026-10-06 (2D polygons, flexspline 60/100/160 teeth, 30 positions over a tooth pitch at four
# wave angles): no overlap with addendum 0.5 m, tooth thickness 0.4 of the pitch, 30 deg flanks and a deflection of one
# module - still none at +15 % deflection; a circular spline half a tooth off overlaps everywhere.
HARMONIC = {'addendum': 0.5, 'dedendum': 0.5, 'thickness': 0.4, 'flank_deg': 30.0, 'deflection': 1.0}   # in modules / shares


def harmonic_ratio(zf, zc):
    """Flexspline turns per wave generator turn, circular spline fixed (negative: the other way)."""
    return -(zc - zf) / zf


def trapezoid_tooth(rp, z, external, addendum, dedendum, thickness, flank_deg):
    """[(r, angle)] of one tooth centred on angle 0: root - tip - tip - root (counter-clockwise across the tooth)."""
    pitch = 2 * math.pi / z
    t = math.tan(math.radians(flank_deg))
    half = thickness * pitch / 2 * rp                         # half tooth width at the pitch circle (arc length)
    sign = 1 if external else -1
    r_tip, r_root = rp + sign * addendum, rp - sign * dedendum
    w_tip = max(0.02 * pitch * rp, half - addendum * t) / r_tip
    w_root = (half + dedendum * t) / r_root
    return [(r_root, -w_root), (r_tip, -w_tip), (r_tip, w_tip), (r_root, w_root)]


def toothed_outline(m, z, external, phase=0.0, root_points=3, **shape):
    """[(r, angle)] around a toothed boundary (z trapezoid teeth, counter-clockwise), the module-scaled HARMONIC shape
    unless overridden: tooth k centred at phase + 2 pi k / z."""
    p = {**HARMONIC, **shape}
    rp = m * z / 2
    tooth = trapezoid_tooth(rp, z, external, p['addendum'] * m, p['dedendum'] * m, p['thickness'], p['flank_deg'])
    pitch = 2 * math.pi / z
    out = []
    for k in range(z):
        c = phase + pitch * k
        out += [(r, c + a) for r, a in tooth]
        end, start = c + tooth[-1][1], c + pitch + tooth[0][1]
        out += [(tooth[-1][0], end + (start - end) * i / (root_points + 1)) for i in range(1, root_points + 1)]
    return out


def toothed_ring_mesh(m, z, external, length, wall, phase=0.0, **shape):
    """(verts, faces) of a toothed ring along z, centred: the toothed boundary and a plain circle `wall` metres beyond the
    roots (inside for external teeth, outside for internal), joined by quads - a closed solid with no n-gon caps."""
    outline = toothed_outline(m, z, external, phase, **shape)
    p = {**HARMONIC, **shape}
    rp = m * z / 2
    r_plain = rp - p['dedendum'] * m - wall if external else rp + p['dedendum'] * m + wall
    n = len(outline)
    verts = []
    for zc in (-length / 2, length / 2):
        verts += [(r * math.cos(a), r * math.sin(a), zc) for r, a in outline]
        verts += [(r_plain * math.cos(a), r_plain * math.sin(a), zc) for _, a in outline]
    bottom_t, bottom_p, top_t, top_p = 0, n, 2 * n, 3 * n
    faces = []
    for i in range(n):
        j = (i + 1) % n
        faces.append((bottom_t + i, bottom_t + j, top_t + j, top_t + i))          # toothed side
        faces.append((bottom_p + j, bottom_p + i, top_p + i, top_p + j))          # plain side
        faces.append((bottom_t + j, bottom_t + i, bottom_p + i, bottom_p + j))    # bottom cap
        faces.append((top_t + i, top_t + j, top_p + j, top_p + i))                # top cap
    return verts, faces


def wave_cam_outline(inner_radius, deflection, clearance, points=96):
    """The wave generator's outline in its own frame: r = inner_radius - clearance + deflection cos 2a - the shape the
    flexspline's bore takes, minus a running clearance (it never touches the ring it pushes)."""
    return [((inner_radius - clearance + deflection * math.cos(2 * a)) * math.cos(a),
             (inner_radius - clearance + deflection * math.cos(2 * a)) * math.sin(a)) for a in (2 * math.pi * i / points for i in range(points))]


def wave_deform(x, y, local_turn, deflection):
    """A flexspline point (its own frame) pushed radially by deflection cos 2(angle - local_turn): local_turn is the wave
    generator's angle minus the flexspline's (radians). The same as the two shape keys cos 2a and sin 2a weighted by
    cos 2t and sin 2t, which is how Blender carries it."""
    r, a = math.hypot(x, y), math.atan2(y, x)
    r2 = r + deflection * math.cos(2 * (a - local_turn))
    return r2 * math.cos(a), r2 * math.sin(a)
