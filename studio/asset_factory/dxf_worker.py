"""Runs in the CAD interpreter (ezdxf): DXF -> closed outlines per layer as JSON, in metres.

python -I dxf_worker.py --versions
python -I dxf_worker.py <in.dxf> <out.json> [units: auto|mm|cm|m|in]

Closed LWPOLYLINE / POLYLINE are kept vertex-for-vertex (bulges sampled every 5 degrees), CIRCLE becomes a
72-gon; open entities are reported, never guessed into outlines. Units come from $INSUNITS unless given.
"""
import importlib.metadata
import json
import math
import sys
from pathlib import Path

PACKAGES = ('ezdxf',)
UNITS = {1: 0.0254, 4: 0.001, 5: 0.01, 6: 1.0, 'in': 0.0254, 'mm': 0.001, 'cm': 0.01, 'm': 1.0}


def _bulge_points(p0, p1, bulge):
    if abs(bulge) < 1e-12:
        return [p0]
    angle = 4 * math.atan(bulge)
    chord = math.dist(p0, p1)
    radius = chord / (2 * math.sin(angle / 2))
    mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    dx, dy = (p1[0] - p0[0]) / chord, (p1[1] - p0[1]) / chord
    sagitta = radius * math.cos(angle / 2)
    cx, cy = mx - dy * sagitta, my + dx * sagitta
    a0 = math.atan2(p0[1] - cy, p0[0] - cx)
    steps = max(2, int(abs(math.degrees(angle)) / 5))
    return [(cx + abs(radius) * math.cos(a0 + angle * k / steps), cy + abs(radius) * math.sin(a0 + angle * k / steps)) for k in range(steps)]


def read(path, units='auto'):
    import ezdxf
    doc = ezdxf.readfile(path)
    code = doc.header.get('$INSUNITS', 0)
    scale = UNITS.get(units if units != 'auto' else code)
    if scale is None:
        raise ValueError(f'DXF units unknown ($INSUNITS={code}); pass units mm|cm|m|in')
    layers, open_entities = {}, []
    for e in doc.modelspace():
        kind, layer = e.dxftype(), e.dxf.layer
        points = None
        if kind == 'LWPOLYLINE' and e.closed:
            raw = list(e.get_points('xyb'))
            points = []
            for i, (x, y, b) in enumerate(raw):
                nx, ny, _ = raw[(i + 1) % len(raw)]
                points += _bulge_points((x, y), (nx, ny), b)
        elif kind == 'POLYLINE' and e.is_closed:
            points = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
        elif kind == 'CIRCLE':
            c, r = e.dxf.center, e.dxf.radius
            points = [(c.x + r * math.cos(2 * math.pi * k / 72), c.y + r * math.sin(2 * math.pi * k / 72)) for k in range(72)]
        else:
            open_entities.append({'type': kind, 'layer': layer})
            continue
        layers.setdefault(layer, []).append([[round(x * scale, 9), round(y * scale, 9)] for x, y in points])
    return {'units_scale_m': scale, 'insunits': code, 'layers': layers, 'skipped': open_entities[:200], 'skipped_count': len(open_entities)}


if __name__ == '__main__':
    if sys.argv[1:] == ['--versions']:
        print(json.dumps({name: importlib.metadata.version(name) for name in PACKAGES}))
        raise SystemExit(0)
    try:
        result = read(sys.argv[1], sys.argv[3] if len(sys.argv) > 3 else 'auto')
    except Exception as exc:
        print(f'DXF worker error: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(2)
    Path(sys.argv[2]).write_text(json.dumps(result), encoding='utf-8')
