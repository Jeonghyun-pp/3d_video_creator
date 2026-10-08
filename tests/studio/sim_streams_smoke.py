"""Streams and cloth (2026-10-08): particles, smoke and liquid from one simulation zone and a cloth sheet, all baked at
build into the .blend; the saved file shows geometry mid-shot, replays the same frame in any visiting order, and
passes check_baked. Run: .venv/bin/python tests/studio/sim_streams_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import blender_binary, read_json, write_json
from studio.project import init_project, shot_path

FRAMES = 48
SCENE = {'world': {'kind': 'blockout', 'color': [0.3, 0.3, 0.35], 'strength': 0.8, 'samples': 16},
         'materials': {'m': {'color': [0.5, 0.5, 0.5]}},
         'primitives': [{'id': 'floor', 'shape': 'box', 'size': [12, 12, 0.2], 'at': [0, 0, -0.1], 'material': 'm'},
                        {'id': 'sheet', 'shape': 'box', 'size': [1.6, 0.02, 1.2], 'at': [3, 0, 2.4], 'material': 'm'}],
         'bind': [{'select': 'floor', 'instance_id': 'floor', 'part_id': 'floor'}, {'select': 'sheet', 'instance_id': 'sheet', 'part_id': 'sheet'}]}


def action(aid, params):
    return {'action_id': aid, 'type': 'simulate', 'targets': [{'instance_id': 'floor', 'part_id': 'floor'}], 'start_frame': 2, 'end_frame': FRAMES - 1,
            'easing': 'linear', 'params': params}


ACTIONS = [action('sparks', {'kind': 'particles', 'region': [[-3, 0, 1], [-2.8, 0.2, 1.2]], 'count': 200, 'direction': [1, 0, 1], 'speed_mps': 3, 'glow': 4,
                             'color_srgb': [1, 0.6, 0.2]}),
           action('fume', {'kind': 'smoke', 'region': [[0, 0, 0], [0.4, 0.4, 0.2]], 'count': 80, 'size_m': 0.2, 'voxel_m': 0.08}),
           action('pour', {'kind': 'liquid', 'region': [[1.5, 0, 2], [1.6, 0.1, 2.05]], 'count': 150, 'size_m': 0.05, 'voxel_m': 0.03}),
           action('drape', {'kind': 'cloth', 'target_object_id': 'sheet', 'subdivide': 8, 'collide_object_ids': ['floor']})]
CHECK = r'''
import bpy, json, sys
sys.path.insert(0, sys.argv[-2])
from simulate import check_baked
scene = bpy.context.scene
def measure(name):
    obj = next(o for o in scene.objects if o.name == name or o.get('studio_id') == name)
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    if obj.type == 'MESH' and any(m.type == 'CLOTH' for m in obj.modifiers):
        me = ev.to_mesh(); low = min((ev.matrix_world @ v.co).z for v in me.vertices); ev.to_mesh_clear(); return round(low, 4)
    geo = ev.evaluated_geometry()
    counts = {'points': len(geo.pointcloud.points) if geo.pointcloud else 0, 'mesh': len(geo.mesh.vertices) if geo.mesh else 0,
              'volume': geo.volume is not None, 'instances': len(geo.instances_pointcloud().points) if geo.instances_pointcloud() else 0}
    return counts
names = {'sparks': 'StudioSim_sparks', 'fume': 'StudioSim_fume', 'pour': 'StudioSim_pour', 'drape': 'sheet'}
out = {'visits': []}
for f in (40, 20, 40):
    scene.frame_set(f)
    out['visits'].append({k: measure(v) for k, v in names.items()})
out['problems'] = check_baked(scene)
open(sys.argv[-1], 'w').write(json.dumps(out))
'''

checks = []
with tempfile.TemporaryDirectory(prefix='sim-streams-smoke-') as root:
    p = Path(init_project('streams', {'request': 'sim streams smoke', 'shots': [{'shot_id': 's', 'frame_count': FRAMES}]}, root)['project_path'])
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': SCENE, 'actions': ACTIONS, 'screen': {'subject': ['sheet']},
                 'camera': {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None,
                            'keys': [{'frame': 0, 'location': [0, -12, 3], 'target': [0, 0, 1]}, {'frame': FRAMES - 1, 'location': [0, -12, 3], 'target': [0, 0, 1]}]}})
    write_json(shot_path(p, 's'), shot)
    built = build_shot(p, 's', None)
    version = p / 'shots/s/versions' / built['scene_version']
    check = Path(root) / 'check.py'; check.write_text(CHECK); out = Path(root) / 'out.json'
    run = subprocess.run([blender_binary(), '--background', '--factory-startup', '--disable-autoexec', str(version / 'scene.blend'), '--python-exit-code', '1',
                          '--python', str(check), '--', str(ROOT / 'studio/blender_ops'), str(out)], capture_output=True, text=True, timeout=900)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    m = json.loads(out.read_text())
    first, second, third = m['visits']   # frame 40, then 20, then 40 again
    assert first == third, (first, third)                                  # the same frame whatever the order
    assert first['sparks']['instances'] > 20, first['sparks']
    assert first['fume']['volume'], first['fume']
    assert first['pour']['mesh'] > 50, first['pour']
    assert first['drape'] < 0.2, first['drape']                             # the sheet fell from 1.8 m onto the floor
    assert first['sparks'] != second['sparks'], (first['sparks'], second['sparks'])   # and the shot moves between frames
    assert not m['problems'], m['problems']
    checks.append(f"baked_replayed_in_any_order ({first['sparks']['instances']} sparks, {first['pour']['mesh']} liquid vertices, drape {first['drape']} m)")
print('STUDIO_SIM_STREAMS_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
