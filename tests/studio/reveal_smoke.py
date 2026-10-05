"""Builds (no render): a closed road slab opens progressively (reveal) before a dive_through camera reaches it.

Checks: the road is closed at frame 1 and open when the camera passes the mouth; the reveal interval follows
the camera cues; the author's own keys survive (authored-animation flag set); a reveal that starts after the
camera has passed is refused by the per-frame pass-through guard; camera fit honours `arrive`.
Run: .venv/bin/python tests/studio/reveal_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.common import StudioError, blender_binary, read_json, write_json
from studio.project import init_project, shot_path
from studio.blender import build_shot

AUTHOR = '''import bpy
scene = bpy.context.scene
def box(name, loc, size):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc); o = bpy.context.object; o.scale = size; o.name = name; o['studio_id'] = name
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return o
box('road', (0, 0, -.5), (60, 60, 1))                       # one closed slab: nothing is open at frame 1
box('lane', (3, 0, .01), (.3, 60, .02))                       # a marking on top, crossing the future hole
cutter = box('road.cutter', (0, 0, -.5), (12, 8, 3))         # the hole at full size; the reveal scales it from ~0
marker = bpy.data.objects.new('road.opening', None); marker.empty_display_type = 'CUBE'; marker.empty_display_size = 1
marker.scale = (6, 4, .5); marker.location = (0, 0, -.5); marker['studio_id'] = 'road.opening'; scene.collection.objects.link(marker)
box('concourse', (0, 6, -20), (30, 30, .5)); box('kiosk', (0, 12, -18.5), (2, 2, 3))
car = box('car', (-20, -10, .8), (2, 4, 1.4))
car.keyframe_insert('location', frame=1); car.location.x = 20; car.keyframe_insert('location', frame=90)
cap = bpy.data.materials.new('cap'); cap.diffuse_color = (.8, .2, .1, 1)
bpy.ops.object.light_add(type='SUN', location=(0, 0, 30))
scene['studio_authored_animation'] = True
'''

REVEAL = {'action_id': 'open_road', 'type': 'reveal', 'targets': [{'instance_id': 'road', 'part_id': 'road'}],
          'start_frame': 0, 'end_frame': 1, 'easing': 'ease_in_out',
          'params': {'cutter_object_id': 'road.cutter', 'cap_material_id': 'cap',
                     'cutter_keys': [{'t': 0, 'scale': [0.02, 0.02, 1]}, {'t': 1, 'scale': [1, 1, 1]}], 'also_cut_overlapping': True}}


def camera(**extra):
    return {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [], 'energy': 'high',
            'move': {'type': 'dive_through', 'params': {'opening': 'road.opening', 'below': 'kiosk', 'above_m': 14, 'back_m': 10},
                     'style': 'archcutaway', 'lens_mm': 20,
                     'guards': {'max_hidden_s': 3.0},  # the target is under the road until it opens: hidden by design
                     **extra}}


def probe(version, frames, origin=(3, 2, 5)):
    """Is the road open at the opening centre (ray straight down hits the concourse, not the road)?"""
    script = version.parent.parent / 'probe_reveal.py'
    script.write_text('''import bpy, json
from mathutils import Vector
scene = bpy.context.scene; out = {}
for f in %r:
    scene.frame_set(f + 1); dg = bpy.context.evaluated_depsgraph_get()
    origin, name = Vector(%r), None  # inside the final opening (default: outside the tiny starting hole)
    for _ in range(8):  # step over hidden helpers (the cutter itself)
        hit, loc, n, i, obj, m = scene.ray_cast(dg, origin, Vector((0, 0, -1)))
        if not hit:
            break
        if not obj.original.hide_render:
            name = obj.original.name; break
        origin = loc + Vector((0, 0, -1e-3))
    out[f] = name
car = bpy.data.objects['car']; scene.frame_set(90); out['car_x_90'] = round(car.matrix_world.translation.x, 3)
print('PROBE ' + json.dumps(out))
''' % (list(frames), tuple(origin)))
    out = subprocess.run([blender_binary(), '--background', '--factory-startup', str(version / 'scene.blend'), '--python', str(script)],
                         capture_output=True, text=True).stdout
    return json.loads(next(line for line in out.splitlines() if line.startswith('PROBE '))[6:])


checks = []
with tempfile.TemporaryDirectory(prefix='reveal-smoke-') as root:
    p = Path(init_project('reveal_test', {'request': 'Reveal smoke', 'shots': [{'shot_id': 'dive', 'frame_count': 90}]}, root)['project_path'])
    author = p / 'author.py'; author.write_text(AUTHOR)

    def build(cam, actions):
        shot = read_json(shot_path(p, 'dive')); shot['camera'] = cam; shot['actions'] = actions; write_json(shot_path(p, 'dive'), shot)
        result = build_shot(p, 'dive', author)
        return result, p / 'shots/dive/versions' / result['scene_version']

    bound = json.loads(json.dumps(REVEAL))
    bound['time_binding'] = {'start_cue_id': 'cam-wp0', 'end_cue_id': 'cam-mouth', 'start_offset_frames': 0, 'end_offset_frames': -4}
    arrive = [{'cue': 'cam-mouth', 'not_before_s': 1.4}]
    timing = {'profile': 'burst_settle', 'head_frac': 0.45, 'burst_frac': 0.2, 'burst_share': 0.6, 'hold_frac': 0.2}
    built, version = build(camera(arrive=arrive, timing=timing), [bound])
    move = read_json(version / 'camera_move_report.json')
    rig = read_json(version / 'camera_rig_report.json')
    cues, resolved = move['camera_cues'], move['reveal']['resolved'][0]
    assert rig['gate_failures'] == [] and rig['summary']['pass_through_frames'] == 0, rig['gate_failures']
    assert resolved['start_frame'] == 0 and resolved['end_frame'] == cues['cam-mouth'] - 4, (resolved, cues)
    assert 'arrive_violations' not in move, move.get('arrive_violations')
    checks += ['reveal_bound_to_camera_cues', 'camera_clears_opened_road']

    state = probe(version, [0, resolved['end_frame'], cues['cam-mouth']])
    assert state['0'] == 'lane', state                                  # closed at the start (marking on the road)
    assert 'lane' in move['reveal']['reveals'][0]['targets'], move['reveal']  # picked up by overlap, not listed
    assert state[str(cues['cam-mouth'])] not in ('road', 'lane'), state   # open (marking cut too) when the camera gets there
    assert abs(state['car_x_90'] - 20) < 1e-3, state                     # the author's own keys are untouched
    checks += ['road_closed_then_open', 'author_keys_preserved']

    # Nothing is cut before the action starts: at the opening centre (where the tiny t=0 hole would sit) the road
    # stays whole until the first cutter key (the boolean is keyed off before it).
    delayed = json.loads(json.dumps(REVEAL))
    delayed['time_binding'] = {'start_cue_id': 'cam-wp0', 'end_cue_id': 'cam-mouth', 'start_offset_frames': 12, 'end_offset_frames': -4}
    built, version = build(camera(arrive=arrive, timing=timing), [delayed])
    row = read_json(version / 'camera_move_report.json')['reveal']['reveals'][0]
    assert row['cut_from_frame'] == 13 and all(v > 0 for v in row['signed_volumes'].values()), row
    state = probe(version, [0, 6, 11, 13], origin=(0.05, 0.05, 5))
    assert [state[k] for k in ('0', '6', '11')] == ['road'] * 3, state
    assert state['13'] not in ('road', 'lane'), state
    checks.append('no_hole_before_start')

    # An inside-out target (faces wound inward) is refused: a MANIFOLD cut of it leaves seams and stray faces.
    author.write_text(AUTHOR + "import bmesh\nroad = bpy.data.objects['road']; bm = bmesh.new(); bm.from_mesh(road.data)\n"
                      "bmesh.ops.reverse_faces(bm, faces=bm.faces); bm.to_mesh(road.data); bm.free()\n")
    try:
        build(camera(arrive=arrive, timing=timing), [bound])
        raise AssertionError('inside-out target accepted')
    except StudioError as error:
        assert 'inside-out' in str(error), str(error)[:300]
        checks.append('inside_out_target_refused')
    author.write_text(AUTHOR)

    # A reveal that starts only after the camera passed the mouth: the camera would cross a closed road.
    late = json.loads(json.dumps(REVEAL))
    late['time_binding'] = {'start_cue_id': 'cam-inside', 'end_cue_id': 'cam-inside', 'start_offset_frames': 2, 'end_offset_frames': 12}
    try:
        build(camera(timing=timing), [late])
        raise AssertionError('camera through a closed road accepted')
    except StudioError as error:
        assert error.code == 'CAMERA_RIG_GUARD_FAILED' and 'passes_through_geometry' in str(error), str(error)[:300]
        checks.append('late_reveal_refused')

    # Build without a slow head: arrive is violated (advisory at build); camera fit re-times the head to satisfy it.
    built, version = build(camera(arrive=arrive), [bound])
    assert read_json(version / 'camera_move_report.json').get('arrive_violations'), 'early arrival must be reported'
    from studio.camera_fit import camera_fit
    fitted = camera_fit(p, 'dive', apply=True)
    assert fitted['arrive'] and fitted['arrive'][0]['early_penalty'] == 0, fitted['arrive']
    assert fitted['timing'].get('head_frac', 0) > 0, fitted['timing']
    final = p / 'shots/dive/versions' / fitted['applied']['scene_version']
    assert 'arrive_violations' not in read_json(final / 'camera_move_report.json')
    checks.append('fit_honours_arrive')

print({'ok': True, 'checks': checks})
