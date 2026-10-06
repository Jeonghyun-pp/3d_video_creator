"""Fill briefs end to end (builds only, no render): the topic's brief fills declared levels; the gates catch an empty
seen level, a subject buried under background, and fill that is not in the brief; N+1 - a car-park brief on another
topic builds with the same code.
Run: .venv/bin/python tests/studio/fill_smoke.py
"""
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, shot_path
from studio.blender import build_shot

AUTHOR = '''import bpy, sys
from pathlib import Path
sys.path.insert(0, str(Path(STUDIO_JOB['library_root']).parent / 'studio' / 'blender_ops'))
import fill_brief
scene = bpy.context.scene
def box(name, loc, size):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc); o = bpy.context.object; o.scale = size; o.name = name; o['studio_id'] = name
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return o
for k, z in enumerate((0.0, -7.0)):          # two open-fronted levels, floors at z, walls at the sides
    box(f'floor{k}', (0, 20, z - 0.25), (20, 40, 0.5))
box('roof', (0, 20, 6.25), (20, 40, 0.5)); box('wall_l', (-10.25, 20, -1), (0.5, 40, 15)); box('wall_r', (10.25, 20, -1), (0.5, 40, 15))
box('back', (0, 40.25, -1), (20, 0.5, 15))
fill_brief.declare_levels([{'level_id': 'L1', 'z': 0.0, 'rects': [[-9, 0, 9, 39]], 'obstacles': [[0, 20, 1.0]]},
                           {'level_id': 'L2', 'z': -7.0, 'rects': [[-9, 0, 9, 39]]}])
if STUDIO_JOB['shot'].get('goal', '').startswith('stray'):
    ghost = box('ghost', (0, 30, 0.5), (1, 1, 1)); ghost['studio_fill_item'] = 'ghost'; ghost['studio_fill_level'] = 'L1'
cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam')); scene.collection.objects.link(cam); scene.camera = cam
cam.data.lens = 24; cam.location = (0, -22, -0.5); cam.rotation_euler = (1.5708, 0, 0)
bpy.ops.object.light_add(type='SUN', location=(0, 0, 30))
scene['studio_authored_animation'] = True
'''


def item(item_id, role, element, layout, **extra):
    return {'item_id': item_id, 'role': role, 'element': element, 'layout': layout, 'why': 'smoke', 'source': 'agent', **extra}


STATION = {'status': 'proposed', 'topic': 'what carries the deck', 'levels': [
    {'level_id': 'L1', 'items': [item('cols', 'subject', 'rooftop_unit', 'along_edge', pitch_m=6),
                                 item('sign', 'identity', 'sign_panel', 'line_across', count=3, at=0.6),
                                 item('people', 'ambient', 'pedestrian_*', 'density', density_per_100m2=1.0)]},
    {'level_id': 'L2', 'items': [item('bus', 'identity', 'bus', 'along_edge', edge='inner', count=1)]}]}
CARPARK = {'status': 'proposed', 'topic': 'how the sprinkler mains reach every bay', 'levels': [
    {'level_id': 'L1', 'items': [item('mains', 'subject', 'vent_duct', 'along_edge', edge='outer', count=2, height_m=4.6),
                                 item('cars', 'identity', 'car', 'grid', pitch_m=6)]},
    {'level_id': 'L2', 'items': [item('cars2', 'identity', 'taxi', 'grid', pitch_m=7)]}]}

checks = []
with tempfile.TemporaryDirectory(prefix='fill-smoke-') as root:
    p = Path(init_project('fill_test', {'request': 'fill smoke', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, root)['project_path'])
    author = p / 'author.py'; author.write_text(AUTHOR)

    def build(brief, goal='fill smoke'):
        shot = read_json(shot_path(p, 's')); shot['fill_brief'] = brief; shot['goal'] = goal; write_json(shot_path(p, 's'), shot)
        built = build_shot(p, 's', author)
        return p / 'shots/s/versions' / built['scene_version']

    version = build(STATION)
    report = read_json(version / 'fill_report.json')
    env = read_json(version / 'environment_report.json')['fill'][0]
    assert report['gate_failures'] == [] and set(report['levels_seen']) == {'L1', 'L2'}, report
    assert report['counts']['L1']['subject'] >= 10 and report['counts']['L1']['identity'] == 3 and report['counts']['L2']['identity'] == 1, report['counts']
    assert any('FILL_BRIEF_UNAPPROVED' in w for w in report['warnings'])
    checks += ['brief_fills_declared_levels', 'proposed_brief_builds_with_warning']
    assert build(STATION).name != version.name and read_json(build(STATION) / 'environment_report.json')['fill'][0]['digest'] == env['digest']
    checks.append('same_brief_same_fill')

    empty = json.loads(json.dumps(STATION)); empty['levels'][1]['items'] = []
    try:
        build(empty); raise AssertionError('empty seen level accepted')
    except StudioError as error:
        assert error.code == 'FILL_LEVEL_EMPTY', error.code
    checks.append('seen_empty_level_refused')
    empty['levels'][1].update({'void': True, 'note': 'plant level, not part of the story'})
    assert read_json(build(empty) / 'fill_report.json')['gate_failures'] == []
    checks.append('void_level_declared_passes')

    buried = json.loads(json.dumps(STATION))
    buried['levels'][0]['items'] = [item('cols', 'subject', 'rooftop_unit', 'cluster', count=2, at=0.9),
                                    item('crowd', 'ambient', 'bus', 'grid', pitch_m=3.2)]
    try:
        build(buried); raise AssertionError('buried subject accepted')
    except StudioError as error:
        assert error.code == 'FILL_SUBJECT_HIDDEN', error.code
    checks.append('subject_buried_by_background_refused')

    try:
        build(STATION, goal='stray fill'); raise AssertionError('off-brief fill accepted')
    except StudioError as error:
        assert error.code == 'FILL_OFF_BRIEF', error.code
    checks.append('fill_outside_the_brief_refused')

    # A look-first project softens the taste gates: the same empty level builds and says so (studio/gates.py).
    project = read_json(p / 'project.json'); project['policy'] = {'strictness': 'look-first'}; write_json(p / 'project.json', project)
    empty['levels'][1].pop('void'); empty['levels'][1].pop('note')
    relaxed = read_json(build(empty) / 'fill_report.json')
    assert relaxed['gate_failures'] == [] and any(w.startswith('FILL_LEVEL_EMPTY (warning by policy)') for w in relaxed['warnings']), relaxed['warnings']
    project['policy'] = {'strictness': 'explain-strict'}; write_json(p / 'project.json', project)
    checks.append('look_first_policy_warns')

    car = read_json(build(CARPARK) / 'fill_report.json')   # N+1: another topic, same code
    assert car['gate_failures'] == [] and car['counts']['L1']['subject'] == 2 and car['counts']['L1']['identity'] > 6, car['counts']
    checks.append('n_plus_1_car_park_topic')

print('STUDIO_FILL_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
