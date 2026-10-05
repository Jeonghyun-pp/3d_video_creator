"""Builds (no render) through the real pipeline to check shot.camera.rig baking and guards.

Run: ../.venv/bin/python tests/studio/camera_rig_smoke.py
"""
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, shot_path
from studio.blender import build_shot, revise_shot

# Subjects fly an S-curve at 90 m/s; the author animates them and sets the authored flag,
# exactly like a hand-written chase scene. The camera comes only from shot.json.
AUTHOR = '''import bpy, math
scene = bpy.context.scene
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
def center(y): return (46*math.sin(y/97) + 13*math.sin(y/43+.6), y, 8.0)
pts = [center(y) for y in range(-50, 1800)]
arc = [0.0]
for a, b in zip(pts, pts[1:]): arc.append(arc[-1] + math.dist(a, b))
def at(s):
    i = next(k for k in range(len(arc)-1) if arc[k+1] >= s); u = (s-arc[i])/(arc[i+1]-arc[i])
    return tuple(p + (q-p)*u for p, q in zip(pts[i], pts[i+1]))
def plane(name):
    bpy.ops.mesh.primitive_cube_add(size=1); o = bpy.context.object; o.scale = (6, 4, 1.2); o.name = name; o['studio_id'] = name
    return o
pursuer, leader = plane('pursuer'), plane('leader')
for f in range(1, 121):
    t = (f-1)/30
    pursuer.location = at(220 + 90*t); leader.location = at(270 + 90*t)
    pursuer.keyframe_insert('location', frame=f); leader.keyframe_insert('location', frame=f)
for side in (-1, 1):
    bpy.ops.mesh.primitive_cube_add(size=1, location=(side*70, 700, 30)); w = bpy.context.object; w.scale = (2, 1500, 60); w.name = f'wall_{side}'; w['studio_id'] = f'wall_{side}'
curve = bpy.data.curves.new('route', 'CURVE'); curve.dimensions = '3D'; sp = curve.splines.new('POLY'); sp.points.add(39)
for i, p in enumerate(pts[200:1800:40]): sp.points[i].co = (*p, 1)
route = bpy.data.objects.new('route', curve); route['studio_id'] = 'route'; scene.collection.objects.link(route)
bpy.ops.object.camera_add(); scene.camera = bpy.context.object
scene['studio_authored_animation'] = True
'''

CHASE = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [], 'energy': 'high',
         'rig': {'type': 'chase', 'subject': 'pursuer', 'look_target': 'leader',
                 'offset_keys': [{'frame': 0, 'offset_m': [6, 6, 22]}, {'frame': 60, 'offset_m': [2, 16, 20]}, {'frame': 100, 'offset_m': [6, 6, 22]}],
                 'aim_keys': [{'frame': 0, 'blend': .15}, {'frame': 90, 'blend': .32}],
                 'lens_keys': [{'frame': 0, 'lens_mm': 24}, {'frame': 119, 'lens_mm': 27}],
                 'screen_anchor': {'x': .52, 'y': .66, 'weight': .72}, 'roll': {'follow_bank': .16, 'max_deg': 16},
                 'smoothing': {'position_s': .1, 'aim_s': .1},
                 'guards': {'clearance_ids': ['wall_-1', 'wall_1'], 'min_clearance_m': 2, 'min_subject_path_speed_mps': 30}}}

checks = []
with tempfile.TemporaryDirectory(prefix='camera-rig-smoke-') as root:
    p = Path(init_project('rig_test', {'request': 'Camera rig regression', 'shots': [{'shot_id': 'chase', 'frame_count': 120}]}, root)['project_path'])
    author = p / 'author.py'; author.write_text(AUTHOR)
    shot = read_json(shot_path(p, 'chase')); shot['camera'] = CHASE; write_json(shot_path(p, 'chase'), shot)
    built = build_shot(p, 'chase', author)
    version = p / 'shots/chase/versions' / built['scene_version']
    report = read_json(version / 'camera_rig_report.json')
    s = report['summary']
    assert report['gate_failures'] == [], report['gate_failures']
    assert s['subject_in_margin_ratio'] == 1 and s['target_visible_ratio'] == 1, s
    assert 23.9 < s['lens_range_mm'][0] and s['lens_range_mm'][1] < 27.1, s
    assert 0 < s['max_abs_roll_deg'] <= 16, s
    assert built['camera_rig']['near_field_ratio'] is not None
    checks += ['chase_guards_pass_through_build', 'lens_keys', 'roll_clamp', 'clearance_measured']
    first = report['samples']

    # Deterministic: same inputs -> same samples.
    again = build_shot(p, 'chase', author)
    assert read_json(p / 'shots/chase/versions' / again['scene_version'] / 'camera_rig_report.json')['samples'] == first
    checks.append('deterministic_bake')

    # Keys are per frame and LINEAR.
    probe = p / 'probe.py'
    probe.write_text('''import bpy, json, sys
sys.path.insert(0, %r)
from scene_tools import curves
cam = bpy.context.scene.camera
rows = [(c.data_path, len(c.keyframe_points), {k.interpolation for k in c.keyframe_points}) for o in (cam, cam.data) for c in curves(o.animation_data.action)]
print('PROBE ' + json.dumps([[a, b, sorted(c)] for a, b, c in rows]))
''' % str(ROOT / 'studio/blender_ops'))
    import subprocess
    from studio.common import blender_binary
    out = subprocess.run([blender_binary(), '--background', '--factory-startup', str(version / 'scene.blend'), '--python', str(probe)], capture_output=True, text=True).stdout
    rows = json.loads(next(line for line in out.splitlines() if line.startswith('PROBE '))[6:])
    assert rows and all(n == 120 and modes == ['LINEAR'] for _, n, modes in rows), rows
    assert {r[0] for r in rows} >= {'location', 'rotation_quaternion', 'lens'}, rows
    checks.append('per_frame_linear_keys')

    # Guard: a camera pushed through the wall must fail and never be promoted.
    bad = json.loads(json.dumps(CHASE)); bad['rig']['offset_keys'] = [{'frame': 0, 'offset_m': [70, 6, 22]}]; bad['rig'].pop('screen_anchor')
    shot = read_json(shot_path(p, 'chase')); before = shot['scene_version']; shot['camera'] = bad; write_json(shot_path(p, 'chase'), shot)
    try:
        build_shot(p, 'chase', author)
    except StudioError as error:
        assert error.code == 'CAMERA_RIG_GUARD_FAILED', error.code
    else:
        raise AssertionError('wall penetration accepted')
    assert read_json(shot_path(p, 'chase'))['scene_version'] == before
    checks.append('clearance_guard_rejects')

    # Guard: subject leaving frame.
    lost = json.loads(json.dumps(CHASE)); lost['rig']['screen_anchor'] = {'x': .99, 'y': .5, 'weight': 1}
    shot['camera'] = lost; write_json(shot_path(p, 'chase'), shot)
    try:
        build_shot(p, 'chase', author)
    except StudioError as error:
        assert error.code == 'CAMERA_RIG_GUARD_FAILED'
    else:
        raise AssertionError('subject outside margin accepted')
    checks.append('margin_guard_rejects')

    # Guard: a 30 m orbit around a subject swinging out to x = 59 m crosses the x = -70 m wall between two
    # frames; only the per-frame pass-through check (no clearance ids declared) sees it.
    wide = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
            'rig': {'type': 'orbit', 'subject': 'pursuer', 'orbit': {'radius_m': 30, 'height_m': 8, 'start_deg': 0, 'deg_per_s': 45},
                    'guards': {'subject_margin': 0}}}
    shot['camera'] = wide; write_json(shot_path(p, 'chase'), shot)
    try:
        build_shot(p, 'chase', author)
    except StudioError as error:
        assert error.code == 'CAMERA_RIG_GUARD_FAILED' and 'passes_through_geometry' in str(error), str(error)[:300]
    else:
        raise AssertionError('orbit through the wall accepted')
    checks.append('pass_through_guard_rejects')

    # Orbit and flythrough reuse the same module with no code change.
    shot['camera'] = json.loads(json.dumps(wide)); shot['camera']['rig']['orbit']['radius_m'] = 10  # subject reaches x = -58.8; the wall face is at -69
    shot['camera']['rig']['lens_keys'] = [{'frame': 0, 'lens_mm': 24}]
    write_json(shot_path(p, 'chase'), shot); build_shot(p, 'chase', author)
    shot['camera'] = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [], 'energy': 'high',
                      'rig': {'type': 'flythrough', 'path': 'route', 'speed_mps': 100, 'start_offset_m': 60, 'look_ahead_m': 25,  # ahead of the 90 m/s jets: at 60 m/s from the path start the pursuer flew through the camera 'lens_keys': [{'frame': 0, 'lens_mm': 20}],
                              'guards': {'clearance_ids': ['wall_-1', 'wall_1'], 'min_clearance_m': 2}}}
    write_json(shot_path(p, 'chase'), shot); fly = build_shot(p, 'chase', author)
    assert fly['camera_rig']['min_clearance_m'] > 2
    checks += ['orbit_builds', 'flythrough_builds']

    # Revision: camera scope can swap rig back to static keys.
    current = read_json(shot_path(p, 'chase'))
    change = p / 'change.json'
    write_json(change, {'base_revision': current['revision'], 'scope': 'camera', 'targets': [],
                        'change': {'camera': {'movement': 'static', 'rig': None, 'energy': None,
                                              'keys': [{'frame': 0, 'location': [60, -40, 40], 'target': [0, 200, 8]}]}},
                        'preserve': ['geometry', 'materials']})
    revised = revise_shot(p, 'chase', change)
    assert 'rig' not in read_json(shot_path(p, 'chase'))['camera'] and revised['camera_rig'] is None
    checks.append('revise_rig_to_keys')

print('STUDIO_CAMERA_RIG_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
