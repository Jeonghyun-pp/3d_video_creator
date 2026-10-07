"""Build-time look passes through the real pipeline (no render). Run: .venv/bin/python tests/studio/look_smoke.py"""
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, shot_path
from studio.blender import build_shot, revise_shot

AUTHOR = '''import bpy, json
scene = bpy.context.scene
scene.render.resolution_x, scene.render.resolution_y = 1080, 1920
def cube(name, loc, size=.2, mech=False):
    bpy.ops.mesh.primitive_cube_add(size=size, location=loc); o = bpy.context.object; o.name = name; o['studio_id'] = 'm/' + name
    if mech: o['studio_mechanical'] = True
    return o
bpy.ops.mesh.primitive_plane_add(size=4); floor = bpy.context.object; floor.name = 'floor'; floor['studio_id'] = 'm/floor'
cube('pawl', (0, 0, .1), mech=True); cube('ratchet', (.2, 0, .1), mech=True)
cube('box', (-.6, .3, .1)); cube('floating', (.6, .3, .12))
scene['studio_guard_pairs'] = json.dumps([['m/pawl', 'm/ratchet']])
bpy.ops.object.camera_add(location=(0, -3, 1.2)); cam = bpy.context.object
cam.rotation_euler = (1.25, 0, 0); scene.camera = cam
scene['studio_authored_animation'] = True
'''

checks = []
with tempfile.TemporaryDirectory(prefix='look-smoke-') as root:
    p = Path(init_project('look_test', {'request': 'look regression', 'shots': [{'shot_id': 'a', 'frame_count': 12}]}, root)['project_path'])
    author = p / 'author.py'; author.write_text(AUTHOR)
    flat = build_shot(p, 'a', author)
    assert flat['look']['preset'] == 'flat_stylized' and flat['look']['applied'] == [], flat['look']
    checks.append('flat_default_noop')
    shot = read_json(shot_path(p, 'a')); shot['render']['look_preset'] = 'photoreal_product'; write_json(shot_path(p, 'a'), shot)
    built = build_shot(p, 'a', author)
    report = read_json(p / 'shots/a/versions' / built['scene_version'] / 'look_report.json')
    assert report['applied'] == ['scale_audit', 'perfection', 'camera_realism', 'lighting', 'compositor'], report['applied']
    perfection = report['passes']['perfection']
    assert perfection['reverted_by_guard'] == [] and report['gate_failures'] == [], (perfection, report['gate_failures'])
    assert 'm/floating' in perfection['passes']['contact']['floating'], perfection['passes']['contact']
    assert report['passes']['lighting']['view_transform'] == 'AgX', report['passes']['lighting']
    checks += ['photoreal_passes_in_order', 'guard_pair_intact', 'floating_reported', 'agx_lighting']
    again = build_shot(p, 'a', author)
    assert read_json(p / 'shots/a/versions' / again['scene_version'] / 'look_report.json')['scene_state_sha256'] == report['scene_state_sha256']
    checks.append('deterministic_scene_state')
    current = read_json(shot_path(p, 'a')); change = p / 'c.json'
    write_json(change, {'base_revision': current['revision'], 'scope': 'camera', 'targets': [], 'change': {'camera': {'movement': 'static'}},
                        'preserve': ['geometry', 'materials']})
    revised = revise_shot(p, 'a', change)
    # a revision starts from the authored checkpoint: the look is applied again, never skipped as 'unchanged'
    assert revised['look']['applied'] == report['applied'] and revised['look']['skipped'] == [], revised['look']
    assert revised['look']['scene_state_sha256'] == report['scene_state_sha256'], revised['look']
    checks.append('revision_reapplies_the_look')
    # Per-shot light (shot.render.lighting) through the build, and the screen's light target judged on the applied rig.
    shot = read_json(shot_path(p, 'a'))
    shot['render']['lighting'] = {'rig': {'key': {'azimuth_deg': -60, 'elevation_deg': 25}, 'fill': {'irradiance_ratio_of_key': 0.25}}}
    shot['screen'] = {'light': {'key_azimuth_deg': -60, 'key_elevation_deg': 25, 'stops': {'fill': -1}}}
    write_json(shot_path(p, 'a'), shot)
    lit = build_shot(p, 'a', author)
    rig = {r['name']: r for r in read_json(p / 'shots/a/versions' / lit['scene_version'] / 'look_report.json')['passes']['lighting']['rig']}
    assert (rig['key']['azimuth_deg'], rig['key']['from'], rig['fill']['stops_vs_key']) == (-60, 'shot', -2.0), rig
    assert [r['id'] for r in lit['screen']['light']] == ['key_azimuth_deg', 'key_elevation_deg', 'fill_stops'], lit['screen']
    assert any('light.fill_stops' in w for w in lit['warnings']), lit['warnings']   # asked 1 stop under, the rig is 2: said
    checks.append('shot_light_rig_built_and_screen_light_judged')
    shot = read_json(shot_path(p, 'a')); shot['render'].pop('lighting'); shot.pop('screen')
    shot['render']['look_preset'] = 'previs_clay'; write_json(shot_path(p, 'a'), shot)
    clay = build_shot(p, 'a', author)
    assert 'clay' in clay['look']['applied'], clay['look']
    checks.append('previs_clay')
print('STUDIO_LOOK_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
