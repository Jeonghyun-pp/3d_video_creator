"""Characters: library people in a declarative scene walk without sliding, at their height, out of step with each other,
and the frame probe sees them (2026-10-08, archcut3: the inspectors were boxes swung at the hip).
Run: .venv/bin/python tests/studio/characters_smoke.py
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

FRAMES = 60
SCENE = {'world': {'kind': 'blockout', 'color': [0.3, 0.3, 0.35], 'strength': 0.8, 'samples': 16},
         'materials': {'floor': {'color': [0.5, 0.5, 0.5]}},
         'primitives': [{'id': 'floor', 'shape': 'box', 'size': [30, 30, 0.2], 'at': [0, 0, -0.1], 'material': 'floor'}],
         'characters': [
             {'id': 'walker_a', 'asset': 'quaternius_human', 'action': 'walk', 'path': [[-1.2, -6, 0], [-1.2, 6, 0]], 'speed_mps': 1.4, 'phase': 0.0},
             {'id': 'walker_b', 'asset': 'quaternius_human', 'action': 'walk', 'path': [[1.2, -6, 0], [1.2, 6, 0]], 'speed_mps': 1.4, 'phase': 0.5,
              'height_m': 1.62, 'appear_frame': 10},
             {'id': 'stander', 'asset': 'quaternius_human', 'action': 'idle', 'at': [4, 2, 0], 'facing_deg': 180}]}
CHECK = r'''
import bpy, json, sys
sys.path.insert(0, sys.argv[-2])
import characters
scene = bpy.context.scene
out = {}
for cid in ('walker_a', 'walker_b'):
    out[cid] = {'slip_m': characters.foot_slip(cid, %d, {'walker_a': 1.75, 'walker_b': 1.62}[cid])}
for cid in ('walker_a', 'walker_b', 'stander'):
    scene.frame_set(20)
    dg = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.objects[cid + '/figure']
    zs = [(mesh.matrix_world @ v.co).z for v in mesh.evaluated_get(dg).data.vertices]
    out.setdefault(cid, {})['height_m'] = round(max(zs) - min(zs), 3)
rig_a, rig_b = bpy.data.objects['walker_a.rig'], bpy.data.objects['walker_b.rig']
scene.frame_set(30)
pa = (rig_a.matrix_world @ rig_a.pose.bones['LeftFoot'].head) - rig_a.parent.matrix_world.translation
pb = (rig_b.matrix_world @ rig_b.pose.bones['LeftFoot'].head) - rig_b.parent.matrix_world.translation
out['left_foot_offset_diff_m'] = round((pa - pb).length, 3)
scene.frame_set(30)
out['walker_a_y_at_30'] = round(bpy.data.objects['walker_a'].matrix_world.translation.y, 3)
open(sys.argv[-1], 'w').write(json.dumps(out))
''' % FRAMES

checks = []
with tempfile.TemporaryDirectory(prefix='characters-smoke-') as root:
    p = Path(init_project('chars', {'request': 'characters smoke', 'shots': [{'shot_id': 's', 'frame_count': FRAMES}]}, root)['project_path'])
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': SCENE, 'screen': {'subject': ['walker_a', 'walker_b']}, 'key_parts': [{'id': 'walker_b', 'from_frame': 12, 'to_frame': 59}],
                 'camera': {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None,
                            'keys': [{'frame': 0, 'location': [0, -14, 2.2], 'target': [0, 0, 1.0]}, {'frame': FRAMES - 1, 'location': [0, -14, 2.2], 'target': [0, 0, 1.0]}]}})
    write_json(shot_path(p, 's'), shot)
    built = build_shot(p, 's', None)
    version = p / 'shots/s/versions' / built['scene_version']
    layout = read_json(version / 'layout_report.json')
    assert len(layout['characters']) == 3, layout.get('characters')
    assert built['frame']['subject_share_median'] > 0.005, built['frame']
    assert not any(w.startswith(('SUBJECT_UNDECLARED', 'KEY_PART')) for w in built['warnings']), built['warnings']
    checks.append(f"built_and_seen (subject share {built['frame']['subject_share_median']})")
    check = Path(root) / 'check.py'; check.write_text(CHECK)
    out = Path(root) / 'out.json'
    subprocess.run([blender_binary(), '--background', '--factory-startup', '--disable-autoexec', str(version / 'scene.blend'), '--python-exit-code', '1',
                    '--python', str(check), '--', str(ROOT / 'studio/blender_ops'), str(out)], check=True, capture_output=True, timeout=600)
    m = json.loads(out.read_text())
    for cid in ('walker_a', 'walker_b'):
        assert m[cid]['slip_m'] < 0.04, (cid, m[cid])   # a planted foot stays within 4 cm
    assert abs(m['stander']['height_m'] - 1.75) < 0.1, m   # height_m is the rest pose; an idle stance sags a few cm
    assert all(1.55 < m[c]['height_m'] < 1.95 for c in ('walker_a', 'walker_b')), m   # mid-stride a figure is a little taller or shorter
    assert m['left_foot_offset_diff_m'] > 0.2, m        # half a cycle apart: not in step
    assert abs(m['walker_a_y_at_30'] - (-6 + 1.4 * 29 / 30)) < 0.05, m
    checks.append(f"no_slide_heights_phase ({m['walker_a']['slip_m']} / {m['walker_b']['slip_m']} m slip)")
print('STUDIO_CHARACTERS_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
