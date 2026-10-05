"""Exact-polygon extrusion: structural sections, plates, mullions, rails.

params = {
  profile: [[u, v], ...]                        # closed polygon (m), used vertex-for-vertex (no resampling)
         | {table: 'KS D 3502' | 'EN 10365', designation: 'H-300x300x10x15' | 'HEB 300'},
  length: m, axis: 'y' (default) | 'x' | 'z', start: 0 (m along axis) or centered: false,
  fillet_segments: 6 (root-radius arcs of table I/H sections),
  smooth: false, sharp_angle_deg: SHARP_ANGLE_DEG (31)
}
Section plane (u, v) -> world as in loft: axis y: (X, Z); axis x: (Y, Z); axis z: (X, Y).
Table sections are centred on (0, 0) with v = depth (h) and u = flange width (b); rows come from
studio/asset_factory/tables (each row cites its source). Corners stay exact because the polygon is
not resampled; caps are single n-gons.
"""
import json
import math
from pathlib import Path

from .loft import PLANE
from .primitives import SHARP_ANGLE_DEG, mesh_object, skin

TABLES = Path(__file__).resolve().parents[2] / 'asset_factory' / 'tables'


def table_row(standard, designation):
    for path in sorted(TABLES.glob('*.json')):
        table = json.loads(path.read_text())
        if table.get('standard') == standard:
            row = table['rows'].get(designation)
            if row is None:
                raise ValueError(f'{standard} has no row {designation!r} (have {sorted(table["rows"])})')
            return row
    raise ValueError(f'no table for standard {standard!r} in {TABLES}')


def i_section(h, b, tw, tf, r, segments=6):
    """Doubly symmetric I/H section with root fillets, counter-clockwise, metres."""
    if not (0 < tw < b and 0 < 2 * tf < h and r >= 0 and tw / 2 + r <= b / 2 and tf + r <= h / 2):
        raise ValueError(f'inconsistent I section h={h} b={b} tw={tw} tf={tf} r={r}')
    right = [(b / 2, -h / 2), (b / 2, -h / 2 + tf)]
    for cx, cy, a0, a1 in ((tw / 2 + r, -h / 2 + tf + r, -90, -180), (tw / 2 + r, h / 2 - tf - r, 180, 90)):
        steps = segments if r > 0 else 1
        for k in range(steps + 1):
            a = math.radians(a0 + (a1 - a0) * k / steps)
            right.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    right += [(b / 2, h / 2 - tf), (b / 2, h / 2)]
    dedup = [p for i, p in enumerate(right) if i == 0 or math.dist(p, right[i - 1]) > 1e-12]
    return dedup + [(-u, v) for u, v in reversed(dedup)]


def channel(h, b, tw, tf):
    """Channel (U) section, web on the left at u = -b/2, flanges opening to +u (sharp corners), CCW."""
    if not (0 < tw < b and 0 < 2 * tf < h):
        raise ValueError(f'inconsistent channel h={h} b={b} tw={tw} tf={tf}')
    u0 = -b / 2
    return [(u0, -h / 2), (u0 + b, -h / 2), (u0 + b, -h / 2 + tf), (u0 + tw, -h / 2 + tf),
            (u0 + tw, h / 2 - tf), (u0 + b, h / 2 - tf), (u0 + b, h / 2), (u0, h / 2)]


def angle(a, b, t):
    """Angle (L) section, heel at the origin, legs along +u (a) and +v (b), sharp corners, CCW."""
    if not (0 < t < min(a, b)):
        raise ValueError(f'inconsistent angle a={a} b={b} t={t}')
    return [(0, 0), (a, 0), (a, t), (t, t), (t, b), (0, b)]


SHAPES = {  # section families a table row can name with 'shape' (default 'i'); rows hold mm
    'i': lambda r, seg: i_section(r['h'], r['b'], r['tw'], r['tf'], r.get('r', 0), seg),
    'channel': lambda r, seg: channel(r['h'], r['b'], r['tw'], r['tf']),
    'angle': lambda r, seg: angle(r['a'], r['b'], r['t']),
    'polygon': lambda r, seg: [tuple(p) for p in r['polygon']],
}


def section_points(profile, fillet_segments=6):
    if isinstance(profile, dict):
        row = table_row(profile['table'], profile['designation'])
        shape = row.get('shape', 'i')
        if shape not in SHAPES:
            raise ValueError(f"table row {profile['designation']!r} has unknown shape {shape!r} (known: {sorted(SHAPES)})")
        mm_row = {k: (v * 0.001 if isinstance(v, (int, float)) and k != 'A_cm2' else v) for k, v in row.items()}
        if shape == 'polygon':
            mm_row['polygon'] = [(u * 0.001, v * 0.001) for u, v in row['polygon']]
        return SHAPES[shape](mm_row, fillet_segments)
    points = [(float(u), float(v)) for u, v in profile]
    if len(points) < 3:
        raise ValueError('profile needs >= 3 points')
    return points


def polygon_area(points):
    return 0.5 * abs(sum(points[i - 1][0] * points[i][1] - points[i][0] * points[i - 1][1] for i in range(len(points))))


def profile_extrude(name, params):
    points = section_points(params['profile'], int(params.get('fillet_segments', 6)))
    length = float(params['length'])
    if length <= 0:
        raise ValueError(f'{name}: length must be positive')
    axis = str(params.get('axis', 'y')).lower()
    if axis not in PLANE:
        raise ValueError(f'{name}: axis must be x, y or z')
    iu, iv, ia = PLANE[axis]
    start = -length / 2 if params.get('centered') else float(params.get('start', 0.0))
    rings = []
    for s in (start, start + length):
        ring = []
        for u, v in points:
            p = [0.0, 0.0, 0.0]
            p[iu], p[iv], p[ia] = u, v, s
            ring.append(tuple(p))
        rings.append(ring)
    verts, faces = skin(rings)
    return mesh_object(name, verts, faces, {'smooth': params.get('smooth', False), 'sharp_angle_deg': params.get('sharp_angle_deg', SHARP_ANGLE_DEG)})
