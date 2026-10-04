"""CAD worker: B-rep with build123d/bd_warehouse -> GLB + factory.json with gates.

Runs ONLY under the CAD interpreter (.venvs/cad, see requirements-cad.txt):
    python cad_worker.py JOB_JSON OUTPUT_DIR
JOB_JSON is the host's resolve_spec() result plus 'spec_sha256'; every
dimension comes from its table rows. Outputs (no timestamps, no paths):
    OUTPUT_DIR/<asset_id>.glb   one node + mesh per part, node name = part id,
                                metres, glTF Y-up (Blender re-imports as CAD Z-up)
    OUTPUT_DIR/factory.json     parts, anchors (mm, CAD frame), gates
Exit status 1 when any gate fails (factory.json is still written), 2 on error.
"""
import importlib.metadata
import itertools
import json
from pathlib import Path
import sys

from build123d import BuildSketch, Locations, Location, Rectangle, extrude, fillet
from bd_warehouse.fastener import HexHeadScrew, HexNut, PlainWasher
from OCP.BRep import BRep_Tool
from OCP.BRepLib import BRepLib_ToolTriangulatedShape
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location
import numpy as np
import pygltflib
import trimesh

LINEAR_DEFLECTION_MM = 0.01
ANGULAR_DEFLECTION_RAD = 0.1
BBOX_LIMIT_MM = 0.5
INTERFERENCE_LIMIT_MM3 = 1e-3
PACKAGES = ('build123d', 'bd_warehouse', 'cadquery-ocp-novtk', 'trimesh', 'numpy', 'pygltflib')


# ---------------------------------------------------------------- builders
def build_bolt_set(spec, rows):
    """Bolt head bearing face at z=0, shank along -Z; washers, grip, nut stacked below."""
    bolt_row = rows['main']
    length = spec['length_mm']
    parts = []
    bolt = HexHeadScrew(bolt_row['thread'], length=length, fastener_type='iso4014', simple=True)
    k = bolt_row['k']
    parts.append({'part_id': 'bolt', 'shape': bolt, 'row': 'main', 'explode_vector': [0, 0, 1],
                  'anchors': {'head_top': [0, 0, k], 'bearing_face': [0, 0, 0], 'tip': [0, 0, -length]},
                  'checks': lambda bb, shape: {'s': bb['size'][1], 'k': bb['max'][2], 'length': -bb['min'][2]}})
    cursor = 0.0

    def washer(part_id, top, sign):
        row = rows['washer']
        shape = PlainWasher(spec['designation'], fastener_type='iso7089').moved(Location((0, 0, top - row['h'])))
        parts.append({'part_id': part_id, 'shape': shape, 'row': 'washer', 'explode_vector': [0, 0, sign],
                      'anchors': {'top': [0, 0, top], 'bottom': [0, 0, top - row['h']]},
                      'checks': lambda bb, s: {'d2': bb['size'][0], 'h': bb['size'][2], 'd1': 2 * _min_cylinder_radius(s)}})
        return top - row['h']

    if spec['washers'] >= 1:
        cursor = washer('washer_head', cursor, 1)
    cursor -= spec['grip_mm']
    if spec['washers'] == 2:
        cursor = washer('washer_nut', cursor, -1)
    if spec['nut']:
        m = rows['nut']['m']
        nut = HexNut(rows['nut']['thread'], fastener_type='iso4032', simple=True).moved(Location((0, 0, cursor - m)))
        parts.append({'part_id': 'nut', 'shape': nut, 'row': 'nut', 'explode_vector': [0, 0, -1],
                      'anchors': {'top': [0, 0, cursor], 'bottom': [0, 0, cursor - m]},
                      'checks': lambda bb, s: {'s': bb['size'][1], 'm': bb['size'][2]}})
    return parts


def h_section(h, b, tw, tf, r, length):
    """Hot-rolled H/I section per EN 10365 geometry, root radius r at web-flange junctions.

    Profile in XY (flange width along X, depth along Y), extruded along +Z.
    """
    with BuildSketch() as sketch:
        with Locations((0, h / 2 - tf / 2), (0, -h / 2 + tf / 2)):
            Rectangle(b, tf)
        Rectangle(tw, h - 2 * tf + 0.01)
        corners = [v for v in sketch.vertices() if abs(abs(v.X) - tw / 2) < 1e-6 and abs(abs(v.Y) - (h / 2 - tf)) < 1e-6]
        if len(corners) != 4:
            raise ValueError(f'Expected 4 root-radius corners, found {len(corners)}')
        fillet(corners, radius=r)
    return extrude(sketch.sketch, amount=length)


def build_hbeam(spec, rows):
    row, length = rows['main'], spec['length_mm']
    shape = h_section(row['h'], row['b'], row['tw'], row['tf'], row['r'], length)
    return [{'part_id': 'beam', 'shape': shape, 'row': 'main', 'explode_vector': [0, 1, 0],
             'anchors': {'end_a': [0, 0, 0], 'end_b': [0, 0, length], 'top_flange_mid': [0, row['h'] / 2, length / 2],
                         'bottom_flange_mid': [0, -row['h'] / 2, length / 2]},
             'checks': lambda bb, s: {'b': bb['size'][0], 'h': bb['size'][1], 'length': bb['size'][2],
                                      'A_cm2': s.volume / length / 100.0}}]


BUILDERS = {'bolt_set': build_bolt_set, 'hbeam': build_hbeam}


# ---------------------------------------------------------------- measurement
def _min_cylinder_radius(shape):
    radii = [f.radius for f in shape.faces() if f.geom_type.name == 'CYLINDER' and f.radius]
    return min(radii)


def _bbox(shape):
    # Measured before tessellation so the exact geometry, not the mesh, is the reference.
    box = shape.bounding_box(optimal=True)
    lo, hi = [box.min.X, box.min.Y, box.min.Z], [box.max.X, box.max.Y, box.max.Z]
    return {'min': lo, 'max': hi, 'size': [b - a for a, b in zip(lo, hi)]}


def tessellate(shape):
    """Per-face triangulation with outward per-vertex normals (CAD frame, mm)."""
    BRepMesh_IncrementalMesh(shape.wrapped, LINEAR_DEFLECTION_MM, False, ANGULAR_DEFLECTION_RAD, False)
    positions, normals, indices = [], [], []
    for face in shape.faces():
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face.wrapped, location)
        if triangulation is None:
            raise ValueError('Face without triangulation')
        BRepLib_ToolTriangulatedShape.ComputeNormals_s(face.wrapped, triangulation)
        transform = location.Transformation()
        # ComputeNormals returns the surface normal; a reversed face's outward side is opposite.
        flip = face.wrapped.Orientation() == TopAbs_REVERSED
        base = len(positions)
        for i in range(1, triangulation.NbNodes() + 1):
            positions.append(triangulation.Node(i).Transformed(transform).Coord())
            normal = triangulation.Normal(i).Transformed(transform)
            normals.append(tuple(-v for v in normal.Coord()) if flip else normal.Coord())
        for i in range(1, triangulation.NbTriangles() + 1):
            a, b, c = triangulation.Triangle(i).Get()
            indices.append((base + a - 1, base + c - 1, base + b - 1) if flip else (base + a - 1, base + b - 1, base + c - 1))
    return np.array(positions, float), np.array(normals, float), np.array(indices, np.uint32)


def _to_gltf(points, scale):
    """CAD Z-up (x, y, z) -> glTF Y-up (x, z, -y)."""
    return np.stack([points[:, 0], points[:, 2], -points[:, 1]], axis=1) * scale


def write_glb(path, meshes):
    """meshes: [(name, positions_mm, normals, indices)] -> one root node per part, no extra nodes."""
    blob, views, accessors, gl_meshes, nodes = bytearray(), [], [], [], []

    def add(array, target, component, kind, minmax=False):
        while len(blob) % 4:
            blob.append(0)
        data = array.tobytes()
        views.append(pygltflib.BufferView(buffer=0, byteOffset=len(blob), byteLength=len(data), target=target))
        blob.extend(data)
        accessor = pygltflib.Accessor(bufferView=len(views) - 1, componentType=component, count=len(array), type=kind)
        if minmax:
            accessor.min, accessor.max = array.min(axis=0).tolist(), array.max(axis=0).tolist()
        accessors.append(accessor)
        return len(accessors) - 1

    for name, positions, normals, indices in meshes:
        pos = add(_to_gltf(positions, 0.001).astype(np.float32), pygltflib.ARRAY_BUFFER, pygltflib.FLOAT, pygltflib.VEC3, True)
        nor = add(_to_gltf(normals, 1.0).astype(np.float32), pygltflib.ARRAY_BUFFER, pygltflib.FLOAT, pygltflib.VEC3)
        idx = add(indices.reshape(-1).astype(np.uint32), pygltflib.ELEMENT_ARRAY_BUFFER, pygltflib.UNSIGNED_INT, pygltflib.SCALAR)
        gl_meshes.append(pygltflib.Mesh(name=name, primitives=[pygltflib.Primitive(attributes=pygltflib.Attributes(POSITION=pos, NORMAL=nor), indices=idx)]))
        nodes.append(pygltflib.Node(name=name, mesh=len(gl_meshes) - 1))
    gltf = pygltflib.GLTF2(asset=pygltflib.Asset(generator='studio.asset_factory.cad_worker', version='2.0'),
                           scene=0, scenes=[pygltflib.Scene(nodes=list(range(len(nodes))))], nodes=nodes, meshes=gl_meshes,
                           accessors=accessors, bufferViews=views, buffers=[pygltflib.Buffer(byteLength=len(blob))])
    gltf.set_binary_blob(bytes(blob))
    Path(path).write_bytes(b''.join(gltf.save_to_bytes()))


def glb_bounds_mm(path):
    """Independent re-read of the GLB (trimesh) -> {node name: CAD-frame bounds in mm}."""
    scene = trimesh.load(str(path), force='scene')
    out = {}
    for node in scene.graph.nodes_geometry:
        transform, geometry = scene.graph[node]
        g = trimesh.transform_points(scene.geometry[geometry].vertices, transform) * 1000.0
        cad = np.stack([g[:, 0], -g[:, 2], g[:, 1]], axis=1)
        out[node] = {'min': cad.min(axis=0).tolist(), 'max': cad.max(axis=0).tolist()}
    return out


# ---------------------------------------------------------------- gates
def table_gate(parts, rows):
    checks, passed = [], True
    for part in parts:
        row = rows[part['row']]
        tolerance = row.get('gate_tolerance_mm', 0.01)
        for key, measured in part['measured'].items():
            if key == 'A_cm2':
                expected, limit = row['A_cm2'], row['area_tolerance_rel'] * row['A_cm2']
            elif key == 'length':
                expected, limit = part['expected_length'], tolerance
            elif key in row:
                expected, limit = row[key], tolerance
            else:
                continue
            ok = abs(measured - expected) <= limit
            passed &= ok
            checks.append({'part_id': part['part_id'], 'key': key, 'measured': round(measured, 4), 'expected': expected,
                           'limit': round(limit, 6), 'passed': ok})
    return {'passed': passed, 'checks': checks}


def interference_gate(parts):
    pairs, worst = [], 0.0
    for a, b in itertools.combinations(parts, 2):
        common = a['shape'] & b['shape']
        volume = abs(common.volume) if common is not None else 0.0
        worst = max(worst, volume)
        pairs.append({'parts': [a['part_id'], b['part_id']], 'overlap_mm3': round(volume, 6)})
    return {'passed': worst <= INTERFERENCE_LIMIT_MM3, 'limit_mm3': INTERFERENCE_LIMIT_MM3, 'max_overlap_mm3': round(worst, 6), 'pairs': pairs}


def bbox_gate(parts, glb_bounds):
    errors, passed = [], set(glb_bounds) == {part['part_id'] for part in parts}
    for part in parts:
        glb = glb_bounds.get(part['part_id'])
        error = None if glb is None else max(abs(x - y) for x, y in zip(glb['min'] + glb['max'], part['bbox']['min'] + part['bbox']['max']))
        passed &= error is not None and error < BBOX_LIMIT_MM
        errors.append({'part_id': part['part_id'], 'max_error_mm': None if error is None else round(error, 6)})
    return {'passed': bool(passed), 'limit_mm': BBOX_LIMIT_MM, 'glb_nodes': sorted(glb_bounds), 'parts': errors}


def run(job, out_dir):
    spec, rows = job['spec'], job['rows']
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    parts = BUILDERS[spec['kind']](spec, rows)
    for part in parts:
        part['bbox'] = _bbox(part['shape'])
        part['expected_length'] = spec.get('length_mm')
        part['measured'] = part.pop('checks')(part['bbox'], part['shape'])
    glb = out_dir / f"{spec['asset_id']}.glb"
    write_glb(glb, [(part['part_id'], *tessellate(part['shape'])) for part in parts])
    gates = {'bbox': bbox_gate(parts, glb_bounds_mm(glb)), 'table': table_gate(parts, rows), 'interference': interference_gate(parts)}
    report = {
        'schema': 1, 'asset_id': spec['asset_id'], 'kind': spec['kind'], 'spec': spec, 'spec_sha256': job.get('spec_sha256'),
        'standards': job['standards'], 'rows': rows, 'glb': glb.name, 'units': {'glb': 'meters', 'anchors': 'millimeters', 'frame': 'CAD Z-up; glTF stored Y-up'},
        'tessellation': {'linear_deflection_mm': LINEAR_DEFLECTION_MM, 'angular_deflection_rad': ANGULAR_DEFLECTION_RAD},
        'versions': versions(),
        'parts': [{'part_id': p['part_id'], 'node_name': p['part_id'], 'standard': rows[p['row']]['standard'],
                   'designation': rows[p['row']]['designation'], 'volume_mm3': round(p['shape'].volume, 3),
                   'bbox_mm': {k: [round(v, 4) for v in p['bbox'][k]] for k in ('min', 'max', 'size')},
                   'explode_vector': p['explode_vector'], 'anchors_mm': p['anchors']} for p in parts],
        'gates': gates, 'passed': all(g['passed'] for g in gates.values()),
    }
    (out_dir / 'factory.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return report


def versions():
    return {name: importlib.metadata.version(name) for name in PACKAGES}


if __name__ == '__main__':
    if sys.argv[1:] == ['--versions']:
        print(json.dumps(versions()))
        raise SystemExit(0)
    try:
        result = run(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')), sys.argv[2])
    except Exception as exc:  # report, never a partial success
        print(f'CAD worker error: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps({'passed': result['passed'], 'gates': {k: v['passed'] for k, v in result['gates'].items()}}))
    raise SystemExit(0 if result['passed'] else 1)
