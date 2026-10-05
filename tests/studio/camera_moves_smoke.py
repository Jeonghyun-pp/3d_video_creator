"""Builds (no render) a shot whose camera is a semantic move: a road slab with an opening, a concourse below.

Checks that dive_through compiles to a guarded flythrough that really passes the opening, that the style
timing shapes the travel (burst then settle), that a blocked path is repaired or fails with a decision
error, and that a bad reference fails loudly. Run: .venv/bin/python tests/studio/camera_moves_smoke.py
"""
from pathlib import Path
import math
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, shot_path
from studio.blender import build_shot

AUTHOR = '''import bpy
scene = bpy.context.scene
def box(name, loc, size):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc); o = bpy.context.object; o.scale = size; o.name = name; o['studio_id'] = name
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return o
# road slab z -1..0 with a 12 x 8 opening at the origin (four slabs around it)
box('road_w', (-23, 0, -.5), (34, 60, 1)); box('road_e', (23, 0, -.5), (34, 60, 1))
box('road_s', (0, -17, -.5), (12, 26, 1)); box('road_n', (0, 17, -.5), (12, 26, 1))
marker = bpy.data.objects.new('road.opening', None); marker.empty_display_type = 'CUBE'; marker.empty_display_size = 1
marker.scale = (6, 4, .5); marker.location = (0, 0, -.5); marker['studio_id'] = 'road.opening'; scene.collection.objects.link(marker)
box('concourse', (0, 6, -20), (30, 30, .5))
box('kiosk', (0, 12, -18.5), (2, 2, 3))
%s
bpy.ops.object.light_add(type='SUN', location=(0, 0, 30))
scene['studio_authored_animation'] = True
'''

BLOCKER = "box('blocker', (0, -3.5, 6), (3, 3, 3))"


def camera(params, **extra):
    return {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [], 'energy': 'high',
            'move': {'type': 'dive_through', 'params': params, 'style': 'archcutaway', **extra}}


def build(p, author_text, cam):
    author = p / 'author.py'; author.write_text(author_text)
    shot = read_json(shot_path(p, 'dive')); shot['camera'] = cam; write_json(shot_path(p, 'dive'), shot)
    return build_shot(p, 'dive', author)


checks = []
with tempfile.TemporaryDirectory(prefix='camera-moves-smoke-') as root:
    p = Path(init_project('moves_test', {'request': 'Camera move smoke', 'shots': [{'shot_id': 'dive', 'frame_count': 90}]}, root)['project_path'])
    built = build(p, AUTHOR % '', camera({'opening': 'road.opening', 'below': 'kiosk'}, lens_mm=18, lens_end_mm=24))
    version = p / 'shots/dive/versions' / built['scene_version']
    move = read_json(version / 'camera_move_report.json')
    rig = read_json(version / 'camera_rig_report.json')
    assert move['ok'] and move['rig']['type'] == 'flythrough', move
    assert move['style'] == 'archcutaway' and move['rig']['timing']['profile'] == 'burst_settle', move['rig']
    assert rig['gate_failures'] == [], rig['gate_failures']
    assert rig['summary']['target_visible_ratio'] is not None
    snapshot = read_json(version / 'shot.snapshot.json')
    assert 'move' in snapshot['camera'] and 'rig' not in snapshot['camera'], 'snapshot must keep the declared move'
    checks += ['dive_compiles_to_guarded_flythrough', 'style_timing_applied', 'snapshot_keeps_move']

    eyes = [s['camera'] for s in rig['samples']]
    crossing = [e for e in eyes if -1.0 <= e[2] <= 0.0]
    assert crossing and all(abs(e[0]) < 6 and abs(e[1]) < 4 for e in crossing), crossing[:5]
    assert eyes[0][2] > 0 > eyes[-1][2]
    checks.append('camera_passes_through_opening')

    steps = [math.dist(a, b) for a, b in zip(eyes, eyes[1:])]
    head, total = sum(steps[:27]), sum(steps)
    assert head / total > 0.5, ('burst share', head / total)
    assert max(steps[-15:]) < max(steps) * 0.25, 'tail must settle'
    lens = rig['summary']['lens_range_mm']
    assert 17.9 < lens[0] and lens[1] < 24.1, lens
    checks += ['burst_then_settle', 'lens_rides_move']

    # camera fit: probe the built shot, fit timing to the style, apply as a camera revision (new version).
    from studio.camera_fit import camera_fit
    fitted = camera_fit(p, 'dive', apply=True)
    assert fitted['timing']['profile'] == 'burst_settle' and fitted['evals'] > 0, fitted
    assert Path(fitted['candidate']).is_file()
    applied = read_json(shot_path(p, 'dive'))
    assert applied['camera']['move']['timing'] == fitted['timing'] and applied['scene_version'] != built['scene_version']
    print({'fit': {k: fitted[k] for k in ('objective', 'level_ratio', 'before', 'after', 'hints')}})
    checks.append('camera_fit_applies_as_revision')

    # Something in the descent path: repaired to clearance, or a decision error - never a silent clip.
    try:
        blocked = build(p, AUTHOR % BLOCKER, camera({'opening': 'road.opening', 'below': 'kiosk'}, clearance_m=1.5,
                                                     guards={'clearance_ids': ['blocker'], 'min_clearance_m': 0.5}))
        moved = read_json(p / 'shots/dive/versions' / blocked['scene_version'] / 'camera_move_report.json')
        assert moved['repairs'], 'blocked waypoint must be reported as repaired'
        checks.append('blocked_path_repaired')
    except StudioError as error:
        assert error.code == 'CAMERA_RIG_GUARD_FAILED', error.code
        checks.append('blocked_path_refused')

    try:
        build(p, AUTHOR % '', camera({'opening': 'no.such.hole', 'below': 'kiosk'}))
        raise AssertionError('missing reference must fail')
    except StudioError as error:
        assert error.code == 'CAMERA_MOVE_FAILED', error.code
        checks.append('missing_reference_fails')

print({'ok': True, 'checks': checks})
