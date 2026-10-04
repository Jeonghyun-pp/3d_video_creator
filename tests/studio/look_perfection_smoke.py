"""Run inside Blender, no renderer/GPU required. Covers look_perfection bevel/contact/snap/jitter + guard + revert."""
from pathlib import Path
import sys
import json
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
from look_perfection import apply_perfection, revert_perfection, _pair_distance

bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene


def cube(name, loc, size=0.2, **props):
    bpy.ops.mesh.primitive_cube_add(size=size, location=loc)
    obj = bpy.context.object; obj.name = name; obj['studio_id'] = 'test/' + name
    for k, v in props.items():
        obj[k] = v
    return obj


floor = cube('floor', (0, 0, -0.05), 1); floor.scale = (10, 10, 0.1)          # top at z=0
pawl = cube('pawl', (0, 1, 0.1), studio_mechanical=True)                       # x in [-0.1, 0.1]
ratchet = cube('ratchet', (0.2, 1, 0.1), studio_mechanical=True)               # x in [0.1, 0.3]: gap 0
floating = cube('floating', (1, 0, 0.12))                                      # bottom 2 cm above floor
boxes = [cube(f'box_{i}', (-1 + 0.5 * i, -1, 0.1)) for i in range(3)]          # resting on floor
scene['studio_guard_pairs'] = json.dumps([['test/pawl', 'test/ratchet']])
bpy.context.view_layer.update()

objs = [o for o in scene.objects]
mats = lambda: {o.name: [list(r) for r in o.matrix_world] for o in objs}
rest = mats()
dist0 = _pair_distance(pawl, ratchet, bpy.context.evaluated_depsgraph_get())
assert dist0 == 0.0, dist0

spec = {'bevel': {}, 'snap': {'max_gap_m': 0.03}, 'jitter': {'allow': ['test/box_*', 'test/floating']}}
r1 = apply_perfection(scene, spec)
m1 = mats()
dist1 = _pair_distance(pawl, ratchet, bpy.context.evaluated_depsgraph_get())
assert r1['passes']['bevel']['applied'] >= 1, r1['passes']['bevel']
assert any(m.name == 'StudioLook_Bevel' for o in objs for m in o.modifiers)
assert dist1 == 0.0, dist1
assert r1['reverted_by_guard'] == [], r1['reverted_by_guard']
assert r1['gate_failures'] == [], r1['gate_failures']
assert 'test/floating' in r1['passes']['contact']['floating'], r1['passes']['contact']
snapped = {m['id']: m for m in r1['passes']['snap']['moved']}
assert 'test/floating' in snapped and snapped['test/floating']['gap_after_mm'] == 0.0, r1['passes']['snap']
moved = {m['id']: m for m in r1['passes']['jitter']['moved']}
assert moved and all(0.0 < abs(m['rot_deg']) <= 0.4 for m in moved.values()), r1['passes']['jitter']
assert m1['pawl'] == rest['pawl'] and m1['ratchet'] == rest['ratchet']           # mechanical never moves

r2 = apply_perfection(scene, spec)
assert mats() == m1, 'apply twice not deterministic'
assert json.dumps(r2, sort_keys=True) == json.dumps(r1, sort_keys=True), 'report not deterministic'
assert sum(1 for o in objs for m in o.modifiers if m.name == 'StudioLook_Bevel') == r1['passes']['bevel']['applied']

rv = revert_perfection(scene)
assert mats() == rest, 'revert did not restore matrices'
assert not any(m.name.startswith('StudioLook_') for o in objs for m in o.modifiers)
assert not any('studio_look_rest' in o for o in objs)

print('STUDIO_LOOK_PERFECTION_SMOKE ' + json.dumps({
    'ok': True, 'bevel_applied': r1['passes']['bevel']['applied'], 'guard_distance': dist1,
    'floating': r1['passes']['contact']['floating'], 'snapped': sorted(snapped), 'jittered': sorted(moved),
    'max_rot_deg': max(abs(m['rot_deg']) for m in moved.values()), 'reverted_by_guard': r1['reverted_by_guard'],
    'reverted': rv['reverted'],
    'checks': ['bevel', 'guard_pair_zero', 'floating_reported', 'snap_zero_gap', 'jitter_le_0.4deg',
               'mechanical_static', 'deterministic_reapply', 'revert_restores']}))
