"""Run inside Blender (headless): the shape kits.
casting - members blended by SDF with metric fillets and rounds, cored, then machined exactly: closed, exact bore and
machined face, fillet adds and round removes material, same spec same mesh, doubling every length doubles the part.
subd - a closed cage smoothed into a molded cover, creased edges staying crisp. sweep - a smooth tapered runner."""
from pathlib import Path
import json
import math
import sys
import time

import bmesh
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
from modeling import build_subject, mesh_hash, remove_subject  # noqa: E402

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
    out = {'closed': closed, 'volume': bm.calc_volume(signed=True), 'faces': len(bm.faces),
           'lo': [min(v.co[i] for v in bm.verts) for i in range(3)], 'hi': [max(v.co[i] for v in bm.verts) for i in range(3)]}
    bm.free()
    return out


def housing(k=1.0, fillet=0.006, round_=0.003, cuts=True, voxel=0.0025):
    L = lambda v: v * k  # noqa: E731
    params = {'voxel_m': L(voxel), 'fillet_m': L(fillet), 'round_m': L(round_), 'adaptivity': 0.0,
              'members': [{'builder': 'box', 'params': {'size': [L(0.16), L(0.10), L(0.04)]}},
                          {'builder': 'revolve', 'params': {'profile': [[0, 0], [L(0.03), 0], [L(0.03), L(0.06)], [0, L(0.06)]], 'segments': 64},
                           'transform': {'location': [0, 0, L(0.01)]}},
                          {'builder': 'box', 'params': {'size': [L(0.012), L(0.09), L(0.03)]}, 'transform': {'location': [L(0.055), 0, L(0.03)]}}],
              'subtract': [{'builder': 'box', 'params': {'size': [L(0.10), L(0.05), L(0.012)]}, 'transform': {'location': [L(-0.01), 0, 0]}}]}
    if cuts:
        params['cuts'] = [{'builder': 'revolve', 'params': {'profile': [[0, L(-0.1)], [L(0.012), L(-0.1)], [L(0.012), L(0.2)], [0, L(0.2)]], 'segments': 64}},
                          {'builder': 'box', 'params': {'size': [L(0.3), L(0.3), L(0.1)]}, 'transform': {'location': [0, 0, L(0.065 + 0.05)]}}]
    return {'subject_id': 'cast', 'builders': [{'part_id': 'housing', 'builder': 'casting', 'params': params}]}


t0 = time.perf_counter()
built = build_subject(housing())
seconds = time.perf_counter() - t0
obj = built['parts']['housing']
st = stats(obj)
check('closed', st['closed'], st['faces'])
check('seconds', seconds < 3.0, round(seconds, 3))
check('machined_top_exact', abs(st['hi'][2] - 0.065) < 1e-6, st['hi'][2])   # the face cut is exact, not voxel-rounded
bore = [v.co for v in obj.data.vertices if abs(math.hypot(v.co.x, v.co.y) - 0.012) < 2e-4]
inner = 0.012 * math.cos(math.pi / 64)   # the cut is a 64-gon: its faces lie between the apothem and the radius
check('bore_exact', len(bore) >= 64 and all(inner - 1e-6 <= math.hypot(v.x, v.y) <= 0.012 + 1e-6 for v in bore), len(bore))
check('outer_size_cast', abs((st['hi'][0] - st['lo'][0]) - 0.16) < 0.0025 * 2, st['hi'][0] - st['lo'][0])   # within a voxel or so per side
hash_a = mesh_hash([obj])
remove_subject('cast')
check('deterministic', mesh_hash([build_subject(housing())['parts']['housing']]) == hash_a, hash_a[:12])
remove_subject('cast')

volume = {}
for label, f, r in (('plain', 0.0, 0.0), ('fillet', 0.008, 0.0), ('round', 0.0, 0.006)):
    volume[label] = stats(build_subject(housing(fillet=f, round_=r, cuts=False))['parts']['housing'])['volume']
    remove_subject('cast')
check('fillet_adds', volume['fillet'] > volume['plain'] * 1.002, volume)
check('round_removes', volume['round'] < volume['plain'] * 0.998, volume)

big = stats(build_subject(housing(k=2.0))['parts']['housing'])
check('scales', abs((big['hi'][2] - big['lo'][2]) / (st['hi'][2] - st['lo'][2]) - 2.0) < 0.02, [big['hi'][2] - big['lo'][2], st['hi'][2] - st['lo'][2]])
remove_subject('cast')
check('no_leftovers', not [o for o in bpy.data.objects if '.member' in o.name or '.subtract' in o.name or '.with' in o.name]
      and not [g for g in bpy.data.node_groups if g.name.endswith('.casting')], [o.name for o in bpy.data.objects])

# subd: a box cage; creased top rim keeps the cover's top edge crisp (reaches the cage), the rest rounds inside it
cage = [[x, y, z] for x in (-0.05, 0.05) for y in (-0.03, 0.03) for z in (0.0, 0.02)]
faces = [[0, 1, 3, 2], [4, 6, 7, 5], [0, 4, 5, 1], [2, 3, 7, 6], [0, 2, 6, 4], [1, 5, 7, 3]]
top = [[1, 3, 1.0], [3, 7, 1.0], [7, 5, 1.0], [5, 1, 1.0]]
soft = stats(build_subject({'subject_id': 'kit', 'builders': [{'part_id': 'cover', 'builder': 'subd', 'params': {'verts': cage, 'faces': faces, 'levels': 3}}]})['parts']['cover'])
remove_subject('kit')
crisp = stats(build_subject({'subject_id': 'kit', 'builders': [{'part_id': 'cover', 'builder': 'subd',
                                                               'params': {'verts': cage, 'faces': faces, 'levels': 3, 'creases': top}}]})['parts']['cover'])
remove_subject('kit')
check('subd_closed_inside_cage', soft['closed'] and soft['hi'][0] < 0.05 and soft['hi'][2] < 0.02, soft)
check('subd_crease_reaches_cage', crisp['closed'] and abs(crisp['hi'][2] - 0.02) < 1e-6 and crisp['volume'] > soft['volume'], [crisp['hi'], crisp['volume'], soft['volume']])

# sweep: a runner through four points, smooth, tapering to half its radius
runner = build_subject({'subject_id': 'kit', 'builders': [{'part_id': 'runner', 'builder': 'sweep', 'params': {
    'profile': {'type': 'circle', 'r': 0.01}, 'segments': 24, 'path_smooth': 'catmull_rom', 'path_samples': 12, 'scale': [[0, 1.0], [1, 0.5]],
    'path': [[0, 0, 0], [0.1, 0, 0.05], [0.15, 0.08, 0.1], [0.1, 0.16, 0.12]]}}]})['parts']['runner']
rs = stats(runner)
def ring_radius(ring):
    c = sum((v.co for v in ring), runner.data.vertices[0].co * 0) / len(ring)
    return sum((v.co - c).length for v in ring) / len(ring)
vs = runner.data.vertices
r0, r1 = ring_radius(vs[:24]), ring_radius(vs[len(vs) - 24:])
check('sweep_smooth_taper', rs['closed'] and len(vs) == 24 * 37 and abs(r0 - 0.01) < 1e-4 and abs(r1 - 0.005) < 1e-4, [len(vs), r0, r1])
remove_subject('kit')

print(json.dumps(report, indent=1, default=str))
print('CASTING SMOKE OK')
