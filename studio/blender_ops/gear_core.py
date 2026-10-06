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
