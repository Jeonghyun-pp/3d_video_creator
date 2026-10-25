"""Builds (no render): a fly-in from above a hall, through its roof and a bay's ceiling, to a box on a stand.

Checks: the recipe fly_in makes a fly_to move from the cut's journey (the places it passes and the slabs declared
passable at them); with both slabs declared, the engine cuts each as the camera reaches it (reveal cutters bound to the
camera's own cues) and the camera passes through nothing else (rig guard clean, no pass-through frame); the box is on
screen when the camera settles; the camera's jerk (studio/camera_follow.py, measured in the built scene) stays under the
followable floor. With the ceiling left undeclared the build stops with CAMERA_PASS_THROUGH naming it - the guard is
never switched off.
Run: .venv/bin/python tests/studio/fly_in_smoke.py
"""
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot  # noqa: E402
from studio.camera_comfort import goal  # noqa: E402
from studio.common import StudioError, read_json, write_json  # noqa: E402
from studio.project import init_project, shot_path  # noqa: E402
from studio.recipe_moves import camera_with, recipe_move  # noqa: E402
from tests.decisions_fixture import settle  # noqa: E402   builds stand on settled decisions (studio/decisions.require)

AUTHOR = '''import bpy
def box(name, loc, size):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc); o = bpy.context.object; o.scale = size; o.name = name; o['studio_id'] = name
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return o
box('hall/floor', (0, 0, -.2), (40, 30, .4))
box('hall/roof', (0, 0, 10.2), (40, 30, .4))        # the hall's roof: a closed slab over everything
box('bay/base', (0, 0, .025), (16, 12, .05))
box('bay/ceiling', (0, 0, 4.1), (16, 12, .2))       # the clean bay's ceiling under it
box('stand', (0, 0, .425), (.6, .6, .75))
foup = box('foup', (0, 0, 1.0), (.4, .4, .4))       # the box the wafers ride in
N = STUDIO_JOB['shot']['duration_frames']
for f, z in ((round(0.8 * N), 1.0), (N, 1.6)):       # lifted after the camera lands (a snatch, like the fab's hoist)
    foup.location.z = z; foup.keyframe_insert('location', index=2, frame=f)
bpy.ops.object.light_add(type='SUN', location=(0, 0, 30))
'''


def cut(passable_ceiling=True, settle_at=0.7):
    """The shot as the build reads it: its journey part with the places it passes (materialized), the recipe chosen."""
    passes = [{'place': 'hall', 'scale': 'building', 'passable': ['hall/roof']},
              {'place': 'bay', 'scale': 'room', **({'passable': ['bay/ceiling']} if passable_ceiling else {})},
              {'place': 'foup', 'scale': 'object'}]
    return {'shot_id': 'fly', 'frame': {'focus': ['foup'], 'size': 'close'},
            'show': [{'at': 'start', 'parts': ['foup'], 'how': 'exterior', 'why': 'the box the line is about'}],
            'direction': {'journey': {'from': 0, 'to': 2, 'role': 'travel', 'why': 'from outside the fab down to the box', 'passes': passes},
                          'move': {'recipe': 'fly_in', 'reveals': 'the box the wafers ride in', 'key_at': 0.85, 'settle_at': settle_at,
                                   'why': 'in through the roof and the clean ceiling to the box, then it opens',
                                   'settings': {'height_share': 0.5, 'azimuth_deg': 30, 'elevation_deg': 50, 'lens_mm': 24}}}}


def project(root, name, frames):
    p = Path(init_project(name, {'request': 'Fly-in smoke', 'shots': [{'shot_id': 'fly', 'frame_count': frames}]}, root)['project_path'])
    settle(p)
    (p / 'author.py').write_text(AUTHOR)
    return p


def build(p, **cut_args):
    planned = cut(**cut_args)
    made = recipe_move(planned)
    shot = read_json(shot_path(p, 'fly'))
    shot.update({k: planned[k] for k in ('frame', 'show', 'direction')})
    shot['camera'] = camera_with(shot.get('camera'), made['move'])
    write_json(shot_path(p, 'fly'), shot)
    result = build_shot(p, 'fly', p / 'author.py')
    return made, p / 'shots/fly/versions' / result['scene_version']


checks, numbers = [], {}
limit = goal('followable')['limits']['jerk']
with tempfile.TemporaryDirectory(prefix='fly-in-smoke-') as root:
    p = project(root, 'fly_in_test', 120)   # 4 s: 2.8 s down from above the roof, then still while the box opens
    made, version = build(p)
    assert made['move']['type'] == 'fly_to' and made['move']['params']['passable'] == ['hall/roof', 'bay/ceiling'], made['move']
    move = read_json(version / 'camera_move_report.json')
    rig = read_json(version / 'camera_rig_report.json')
    assert rig['gate_failures'] == [] and rig['summary']['pass_through_frames'] == 0, rig['gate_failures']
    passages = {r['slab']: r for r in move['passages']}
    assert set(passages) == {'hall/roof', 'bay/ceiling'}, move['passages']
    for row in passages.values():
        assert row['open_by_frame'] < row['camera_at_frame'], row       # open before the camera gets there
    reveals = {r['cutter'] for r in move['reveal']['reveals']}
    assert {r['cutter'] for r in passages.values()} <= reveals, move['reveal']
    checks += ['declared_slabs_cut_at_the_cameras_cues', 'camera_passes_through_nothing_else']
    landed = [s for s in rig['samples'] if s['frame'] >= round(0.72 * 120)]
    assert landed[0].get('target_visible') and landed[0].get('target_box'), landed[0]   # the box is on screen when it lands
    checks.append('destination_seen_on_arrival')
    turned = max(s['pitch_deg'] for s in landed) - min(s['pitch_deg'] for s in landed)
    numbers['landed_pitch_range_deg'] = round(turned, 3)
    assert turned < 0.1, numbers     # the box lifts after the landing: the camera holds, it does not tilt after it
    checks.append('landed_camera_holds_while_the_subject_moves')

    from studio.camera_taste import measure_version
    measured = measure_version(p, 'fly', version.name)
    follow = measured['follow']
    numbers['smooth'] = {'seconds': 4.0, 'settle_at': 0.7, 'jerk': follow['jerk'], 'jerk_at_s': follow['jerk_at_s'],
                         'arrive_s': follow['arrive_s'], 'scale_per_0.5s': measured['comfort']['scale']}
    assert follow['jerk'] < limit, numbers        # a fly-in an eye follows stays under the floor (stop and report otherwise)
    assert follow['arrive_s'] <= 0.7 * 4.0 + 0.3, numbers   # still by settle_at, give or take a window
    checks.append('fly_in_is_followable')

    try:
        build(p, passable_ceiling=False)
        raise AssertionError('a camera through an undeclared ceiling was accepted')
    except StudioError as error:
        assert 'CAMERA_PASS_THROUGH' in str(error) and 'bay/ceiling' in str(error), str(error)[:400]
        checks.append('undeclared_slab_refused')

    rushed = project(root, 'fly_in_rushed', 90)   # the same way down crammed into 1.8 s: a rush, past the floor
    _made, version = build(rushed, settle_at=0.6)
    follow = measure_version(rushed, 'fly', version.name)['follow']
    numbers['rushed'] = {'seconds': 3.0, 'settle_at': 0.6, 'jerk': follow['jerk'], 'jerk_at_s': follow['jerk_at_s']}
    assert follow['jerk'] >= limit, numbers
    checks.append('rushed_fly_in_is_past_the_floor')

print(json.dumps({'ok': True, 'checks': checks, 'numbers': numbers, 'limit': limit}))
