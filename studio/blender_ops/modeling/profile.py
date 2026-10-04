"""Exact-polygon extrusion: structural sections, plates, mullions, rails.

params = {
  profile: [[u, v], ...]                        # closed polygon (m), used vertex-for-vertex (no resampling)
         | {table: 'KS D 3502' | 'EN 10365', designation: 'H-300x300x10x15' | 'HEB 300'},
  length: m, axis: 'y' (default) | 'x' | 'z', start: 0 (m along axis) or centered: false,
  fillet_segments: 6 (root-radius arcs of table I/H sections),
  smooth: false, sharp_angle_deg: 30
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
from .primitives import mesh_object, skin

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


def section_points(profile, fillet_segments=6):
    if isinstance(profile, dict):
        row = table_row(profile['table'], profile['designation'])
        mm = 0.001
        return i_section(row['h'] * mm, row['b'] * mm, row['tw'] * mm, row['tf'] * mm, row.get('r', 0) * mm, fillet_segments)
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
    return mesh_object(name, verts, faces, {'smooth': params.get('smooth', False), 'sharp_angle_deg': params.get('sharp_angle_deg', 30)})
