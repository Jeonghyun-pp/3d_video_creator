"""Run inside Blender (headless): spec builder `ops` (modeling/ops.py) are baked into the part mesh - closed results,
measured volumes and sizes, anchors / mirrors / array copies see the result, the same spec builds the same mesh, and
an op that cannot work names the part and op index."""
from pathlib import Path
import json
import math
import sys

import bmesh
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
from modeling import build_subject, mesh_hash, rebuild_parts, remove_subject  # noqa: E402

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
report = {}


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


def stats(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    closed = all(len(e.link_faces) == 2 for e in bm.edges)
    volume = bm.calc_volume(signed=True)
    lo = [min(v.co[i] for v in bm.verts) for i in range(3)]
    hi = [max(v.co[i] for v in bm.verts) for i in range(3)]
    faces = len(bm.faces)
    bm.free()
    return {'closed': closed, 'volume': volume, 'size': [h - l for l, h in zip(lo, hi)], 'hi': hi, 'faces': faces}


S, R = 0.2, 0.03   # block edge, bore radius
BORE = {'builder': 'revolve', 'params': {'profile': [[0, -0.15], [R, -0.15], [R, 0.15], [0, 0.15]], 'segments': 64}}


def spec(ops_block, extra=()):
    return {'subject_id': 'ops', 'builders': [
        {'part_id': 'block', 'builder': 'box', 'params': {'size': [S, S, S], 'smooth': False}, 'ops': ops_block},
        *extra]}


# 1. boolean difference: a bored block is closed, loses the bore's volume, keeps its outer size ----------------------
built = build_subject(spec([{'op': 'boolean', 'with': BORE}]))
st = stats(built['parts']['block'])
expected = S ** 3 - math.pi * R * R * S * (64 / (2 * math.pi) * math.sin(2 * math.pi / 64))   # inscribed 64-gon
check('bore_closed', st['closed'], st['closed'])
check('bore_volume', abs(st['volume'] / expected - 1) < 1e-3, round(st['volume'] / expected, 6))
check('bore_size', max(abs(s - S) for s in st['size']) < 1e-6, st['size'])
check('operand_removed', not [o for o in bpy.data.objects if '.with' in o.name] and not [c for c in bpy.data.collections if '.boolean' in c.name],
      [o.name for o in bpy.data.objects])
hash_a = mesh_hash(built['parts'].values())
remove_subject('ops')
again = build_subject(spec([{'op': 'boolean', 'with': BORE}]))
check('deterministic', mesh_hash(again['parts'].values()) == hash_a, hash_a[:12])
remove_subject('ops')

# 2. union of a boss raises the +z face anchor ---------------------------------------------------------------------
boss = {'builder': 'box', 'params': {'size': [0.05, 0.05, 0.1]}, 'transform': {'location': [0, 0, S / 2]}}
built = build_subject(spec([{'op': 'boolean', 'mode': 'union', 'with': boss}]))
block = built['parts']['block']
anchors = json.loads(block['studio_anchors']) if isinstance(block['studio_anchors'], str) else dict(block['studio_anchors'])
top = [v for k, v in anchors.items() if k.endswith('/+z')][0]
check('union_anchor_top', abs(top[2] - (S / 2 + 0.05)) < 1e-6, top)
remove_subject('ops')

# 3. bevel keeps the outer size, adds faces, stays closed; subdivide with creases keeps a box's size ------------------
built = build_subject(spec([{'op': 'bevel', 'width_m': 0.01, 'segments': 3}]))
st = stats(built['parts']['block'])
check('bevel', st['closed'] and st['faces'] > 6 and max(abs(s - S) for s in st['size']) < 1e-6, st)
remove_subject('ops')
built = build_subject(spec([{'op': 'subdivide', 'levels': 2, 'crease_angle_deg': 30}]))
creased = stats(built['parts']['block'])
check('subdivide_creased_keeps_size', creased['closed'] and max(abs(s - S) for s in creased['size']) < 1e-6, creased['size'])
check('crease_layer_removed', 'crease_edge' not in built['parts']['block'].data.attributes, list(built['parts']['block'].data.attributes.keys()))
remove_subject('ops')
built = build_subject(spec([{'op': 'subdivide', 'levels': 2}, {'op': 'shade', 'smooth': True}]))
soft = stats(built['parts']['block'])
check('subdivide_smooth_rounds', soft['size'][0] < S * 0.9, soft['size'])
check('shade_smooth', all(p.use_smooth for p in built['parts']['block'].data.polygons), True)
remove_subject('ops')

# 4. displace: same seed same mesh, another seed another mesh; solidify / remesh / weld run --------------------------
def displaced(seed):
    b = build_subject(spec([{'op': 'subdivide', 'levels': 3}, {'op': 'displace', 'strength_m': 0.004, 'scale_m': 0.03, 'seed': seed}]))
    h = mesh_hash(b['parts'].values())
    remove_subject('ops')
    return h


check('displace_seeded', displaced(1) == displaced(1) != displaced(2), True)
built = build_subject(spec([{'op': 'remesh_voxel', 'voxel_m': 0.01}, {'op': 'weld', 'dist_m': 0.001},
                            {'op': 'solidify', 'thickness_m': 0.005}]))
check('remesh_weld_solidify', len(built['parts']['block'].data.polygons) > 6, len(built['parts']['block'].data.polygons))
remove_subject('ops')

# 5. ops on an array item are shared by every copy; a mirror copies its source's result -----------------------------
item = {'builder': 'box', 'params': {'size': [0.02, 0.02, 0.02]}, 'ops': [{'op': 'bevel', 'width_m': 0.002}]}
built = build_subject(spec([{'op': 'boolean', 'with': BORE}], extra=[
    {'part_id': 'bolts', 'builder': 'array', 'params': {'count': 4, 'center': [0, 0, 0], 'axis': 'z', 'item': {**item, 'transform': {'location': [0.15, 0, 0]}}}},
    {'part_id': 'block_m', 'builder': 'mirror', 'params': {'source': 'block', 'axis': 'x'}}]))
copies = [c for c in built['parts']['bolts'].children]
check('array_ops_shared', len(copies) == 4 and len({c.data.name for c in copies}) == 1 and len(copies[0].data.polygons) > 6,
      [len(c.data.polygons) for c in copies])
check('mirror_ops', len(built['parts']['block_m'].data.polygons) == len(built['parts']['block'].data.polygons),
      len(built['parts']['block_m'].data.polygons))
remove_subject('ops')

# 6. changing an op value rebuilds the part (workbench set_spec_param) to the same mesh a fresh build makes -------------
first, second = spec([{'op': 'bevel', 'width_m': 0.01}]), spec([{'op': 'bevel', 'width_m': 0.02, 'segments': 5}])
build_subject(first)
rebuild_parts(second, ['block'])
from modeling import scene_parts  # noqa: E402
rebuilt = mesh_hash(scene_parts('ops').values())
remove_subject('ops')
check('rebuild_equals_fresh', rebuilt == mesh_hash(build_subject(second)['parts'].values()), rebuilt[:12])
remove_subject('ops')

# 7. rounded sections (fillet_core): loft rounded_rect, profile points + fillet_r, revolve fillet_m, sweep -----------------
A, B, RR, L = 0.1, 0.05, 0.02, 0.3
rounded = {'subject_id': 'ops', 'builders': [
    {'part_id': 'duct', 'builder': 'loft', 'params': {'segments': 128, 'stations': [
        {'s': 0, 'section': {'type': 'rounded_rect', 'a': A, 'b': B, 'r': RR}}, {'s': L, 'section': {'type': 'rounded_rect', 'a': A, 'b': B, 'r': RR}}]}},
    {'part_id': 'plate', 'builder': 'profile', 'params': {'length': L, 'fillet_segments': 16,
                                                          'profile': {'points': [[-A, -B], [A, -B], [A, B], [-A, B]], 'fillet_r': RR}}},
    {'part_id': 'cup', 'builder': 'revolve', 'params': {'closed_profile': True, 'cap_start': False, 'cap_end': False, 'segments': 64,
                                                        'fillet_m': 0.005, 'profile': [[0.03, 0], [0.05, 0], [0.05, 0.04], [0.03, 0.04]]}},
    {'part_id': 'runner', 'builder': 'sweep', 'params': {'profile': {'type': 'rounded_rect', 'a': 0.02, 'b': 0.01, 'r': 0.008}, 'segments': 32,
                                                         'path': [[0, 0, 0], [0.2, 0, 0.05], [0.3, 0.1, 0.1]]}}]}
built = build_subject(rounded)
section_area = 4 * A * B - (4 - math.pi) * RR * RR
for pid in ('duct', 'plate'):
    st = stats(built['parts'][pid])
    check(f'{pid}_rounded_volume', st['closed'] and abs(st['volume'] / (section_area * L) - 1) < 0.01, round(st['volume'] / (section_area * L), 5))
for pid in ('cup', 'runner'):
    check(f'{pid}_closed', stats(built['parts'][pid])['closed'], True)
remove_subject('ops')

# 8. a contrib mesh part takes the shading keys (the entry never sees them) and ops like any geometry part ----------------
import tempfile  # noqa: E402
import contrib_loader  # noqa: E402
with tempfile.TemporaryDirectory() as folder:
    Path(folder, 'impl.py').write_text('def slab(size=0.1, **unexpected):\n'
                                       '    assert not unexpected, unexpected\n'
                                       '    h = size / 2\n'
                                       '    v = [(x, y, z) for x in (-h, h) for y in (-h, h) for z in (-h, h)]\n'
                                       '    f = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]\n'
                                       '    return v, f\n')
    Path(folder, 'manifest.json').write_text(json.dumps({'kind': 'mesh', 'name': 'slab', 'entry': 'slab', 'params': {'size': 0.1}}))
    contrib_loader.TABLE = {'contrib:slab@draft': {'dir': folder, 'sha256': contrib_loader.code_sha(folder), 'kind': 'mesh', 'entry': 'slab',
                                                   'params': {'size': 0.1}}}
    built = build_subject({'subject_id': 'ops', 'builders': [
        {'part_id': 'flat', 'builder': 'contrib:slab@draft', 'params': {}},
        {'part_id': 'soft', 'builder': 'contrib:slab@draft', 'params': {'size': 0.2, 'smooth': True, 'sharp_angle_deg': 20},
         'ops': [{'op': 'bevel', 'width_m': 0.02, 'segments': 4, 'angle_deg': 60}]}]})
    flat, soft = built['parts']['flat'], built['parts']['soft']
    check('contrib_default_flat', not any(p.use_smooth for p in flat.data.polygons), True)
    check('contrib_smooth_and_ops', any(p.use_smooth for p in soft.data.polygons) and len(soft.data.polygons) > 6
          and abs(stats(soft)['size'][0] - 0.2) < 1e-6, len(soft.data.polygons))
    remove_subject('ops')
    contrib_loader.TABLE = {}

# 9. an op that cannot work names the part and the op index ---------------------------------------------------------
far = {'builder': 'box', 'params': {'size': [0.01, 0.01, 0.01]}, 'transform': {'location': [5, 0, 0]}}
try:
    build_subject(spec([{'op': 'bevel', 'width_m': 0.01}, {'op': 'boolean', 'mode': 'intersect', 'with': far}]))
    message = ''
except ValueError as exc:
    message = str(exc)
check('error_names_op', 'ops/block' in message and 'ops[1] boolean' in message, message)

print(json.dumps(report, indent=1, default=str))
print('SPEC OPS SMOKE OK')
