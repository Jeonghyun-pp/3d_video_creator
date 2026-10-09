"""A jump that plays once and holds, and a head that turns to look up: reactions the library's clips do not have as
loops (floor_noise, 2026-10-09: no jump, no look-up - a child "jumping" read as walking).
Run: .venv/bin/python tests/studio/character_reactions_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import StudioError, blender_binary, read_json, write_json
from studio.project import init_project, shot_path

FRAMES = 60
SCENE = {'world': {'kind': 'blockout', 'color': [0.3, 0.3, 0.35], 'strength': 0.8, 'samples': 16},
         'materials': {'floor': {'color': [0.5, 0.5, 0.5]}},
         'primitives': [{'id': 'floor', 'shape': 'box', 'size': [12, 12, 0.2], 'at': [0, 0, -0.1], 'material': 'floor'}],
         'characters': [
             {'id': 'child', 'asset': 'quaternius_human', 'action': 'jump', 'at': [-1, 0, 0], 'height_m': 1.2, 'playback': 'once', 'start_frame': 10},
             {'id': 'adult', 'asset': 'quaternius_human', 'action': 'idle', 'at': [1.5, 0, 0], 'facing_deg': 90,
              'look_at': {'target': [1.5, 0.3, 6.0], 'from_frame': 20, 'blend_frames': 8}}]}
CHECK = r'''
import bpy, json, sys
sys.path.insert(0, sys.argv[-2])
import characters
scene = bpy.context.scene
rig = bpy.data.objects['child.rig']
def pose(f):
    scene.frame_set(f)
    return [tuple(round(c, 4) for c in (rig.matrix_world @ b.head)) for b in rig.pose.bones][:12]
out = {'changes_during': pose(15) != pose(25), 'holds_after': pose(50) == pose(58)}
adult = bpy.data.objects['adult.rig']
head = characters.head_bone(adult)
axis, sign = adult['studio_face_axis']
def up(f):   # how far the face (the rig's measured face axis) points upward
    scene.frame_set(f)
    m = (adult.matrix_world @ head.matrix).to_3x3()
    return round((m.col[axis] * sign).normalized().z, 3)
out['head_up_before'], out['head_up_after'] = up(10), up(40)
open(sys.argv[-1], 'w').write(json.dumps(out))
'''

checks = []
with tempfile.TemporaryDirectory(prefix='reactions-smoke-') as root:
    p = Path(init_project('reactions', {'request': 'reactions smoke', 'shots': [{'shot_id': 's', 'frame_count': FRAMES}]}, root)['project_path'])
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': SCENE, 'screen': {'subject': ['child', 'adult']},
                 'camera': {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None,
                            'keys': [{'frame': 0, 'location': [0, -8, 1.6], 'target': [0, 0, 1.0]}, {'frame': FRAMES - 1, 'location': [0, -8, 1.6], 'target': [0, 0, 1.0]}]}})
    write_json(shot_path(p, 's'), shot)
    built = build_shot(p, 's', None)
    version = p / 'shots/s/versions' / built['scene_version']
    check = Path(root) / 'check.py'; check.write_text(CHECK)
    out = Path(root) / 'out.json'
    subprocess.run([blender_binary(), '--background', '--factory-startup', '--disable-autoexec', str(version / 'scene.blend'), '--python-exit-code', '1',
                    '--python', str(check), '--', str(ROOT / 'studio/blender_ops'), str(out)], check=True, capture_output=True, timeout=600)
    m = json.loads(out.read_text())
    assert m['changes_during'] and m['holds_after'], m
    checks.append('jump_plays_once_then_holds')
    assert m['head_up_after'] > m['head_up_before'] + 0.3, m
    checks.append(f"head_turns_up ({m['head_up_before']} -> {m['head_up_after']})")
    walking_once = dict(SCENE['characters'][0], path=[[0, 0, 0], [0, 3, 0]], action='walk'); walking_once.pop('at')
    shot['scene'] = {**SCENE, 'characters': [walking_once]}
    write_json(shot_path(p, 's'), {**shot, 'revision': read_json(shot_path(p, 's'))['revision']})
    try:
        build_shot(p, 's', None)
        raise AssertionError('a walk along a path played once')
    except StudioError as error:
        assert 'loops' in error.message, error.message
    checks.append('walk_once_refused')
print('STUDIO_REACTIONS_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
