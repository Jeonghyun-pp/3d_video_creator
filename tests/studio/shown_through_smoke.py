"""Blender: a part a moment shows that stands in front of another part it shows fades, never cut or moved
(show_core.SHOWN_THROUGH, show_scene.apply), and the camera and the build read both as shown.

A closed housing with a sleeve inside it and a pipe wholly inside the sleeve; a section moment shows the sleeve and the
pipe. The section cuts the housing, not the sleeve; the sleeve gets a see window and a see-through row (alpha 0.45); from
the picture's view the pipe is not hidden (what would be SHOW_NOT_OPENING), the camera's measure (frame_probe.measure_at)
reads the pipe through the sleeve and the sleeve where it is drawn, and the build's probe does the same.
Without the rule (2026-10-17) the sleeve hid the pipe whole and the shot stopped.
Run: blender --background --factory-startup --python tests/studio/shown_through_smoke.py
"""
from pathlib import Path
import json
import sys
import tempfile

import bmesh
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import camera_frame  # noqa: E402
import camera_frame_core as frame_core  # noqa: E402
import frame_probe  # noqa: E402
import reveal  # noqa: E402
import show_core  # noqa: E402
import show_scene  # noqa: E402

PARTS = ['unit/sleeve', 'unit/pipe']
SIZE, LENS, ON = (1080, 1920), 35.0, 31          # the section moment at 0.5 of 60 frames: scene frame 31
report = {}


def check(name, cond, detail):
    report[name] = detail
    assert cond, f'{name}: {detail}'


def box(ident, centre, size, parent):
    mesh = bpy.data.meshes.new(ident)
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0); bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(ident, mesh); bpy.context.scene.collection.objects.link(obj)
    obj.location, obj.scale = centre, size
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action='DESELECT'); obj.select_set(True); bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj['studio_id'], obj['studio_subject_id'], obj['studio_part_id'] = ident, 'unit', ident.split('/')[1]
    obj.parent = parent
    return obj


def vertices(obj, frame):
    bpy.context.scene.frame_set(frame)
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh(); n = len(mesh.vertices); ev.to_mesh_clear()
    return n


for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj)
scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 60
root = bpy.data.objects.new('unit', None); scene.collection.objects.link(root)
root['studio_id'] = root['studio_subject_id'] = 'unit'
housing = box('unit/housing', (0, 0, 1.5), (1.6, 1.6, 3.0), root)
sleeve = box('unit/sleeve', (0, 0, 1.5), (0.5, 0.5, 1.6), root)
pipe = box('unit/pipe', (0, 0, 1.5), (0.18, 0.18, 1.0), root)            # wholly inside the sleeve
cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam')); scene.collection.objects.link(cam); scene.camera = cam
section = {'at': 0.5, 'parts': PARTS, 'how': 'section', 'why': 'the pipe runs inside its sleeve'}
frames = [{'at': at, 'height_share': 0.6, 'centre': [0.5, 0.5], 'view': {'elevation_deg': 10.0, 'azimuth_deg': 0.0}, 'lens_mm': LENS,
           'must_see': PARTS if row else [], **({'show': row} if row else {})} for at, row in zip((0.0, 0.5, 1.0), (None, section, None))]
job = {'fps': 30, 'shot': {'shot_id': 's', 'duration_frames': 60, 'actions': [],
                           'camera': {'move': {'type': 'frame_to_frame', 'params': {'target': 'unit'}, 'frames': frames}}}}

rows = show_scene.apply(job)
check('housing_opened_not_the_sleeve', list(rows[0]['in_front']) == ['unit/housing'], rows[0]['in_front'])
check('sleeve_shown_through', list(rows[0].get('shown_through') or {}) == ['unit/sleeve'], rows[0].get('shown_through'))
check('sleeve_has_a_see_window', camera_frame.passes_through(sleeve, ON), list(sleeve.get(camera_frame.SEE_WINDOW) or []))
check('sleeve_solid_before', not camera_frame.passes_through(sleeve, 1), list(sleeve.get(camera_frame.SEE_WINDOW) or []))
reveal.apply(job['shot'])
check('sleeve_never_cut', vertices(sleeve, ON) == 8, vertices(sleeve, ON))

forward, _r, _u = frame_core.basis(10.0, 0.0)
aim = (0.0, 0.0, 1.5)
eye = tuple(aim[i] - 9 * c for i, c in enumerate(forward))
diagnosis = camera_frame.diagnose_part('unit/pipe', ['unit'], eye, (10.0, 0.0), LENS, SIZE, ON, shown=['unit/sleeve'])
check('pipe_not_hidden', diagnosis['hidden_share'] < camera_frame.HIDDEN_SHARE, diagnosis['hidden_share'])
measured = frame_probe.measure_at(PARTS, eye, aim, LENS, 0.0, ON, SIZE, subjects=['unit'])
check('camera_reads_both', measured['unit/pipe'] >= 20 and measured['unit/sleeve'] > measured['unit/pipe'], measured)
prepared = camera_frame.probe_set(['unit'], ['unit'], PARTS, ON)
probed = camera_frame.probe(['unit'], ['unit'], PARTS, eye, (10.0, 0.0), LENS, SIZE, ON, prepared=prepared)['must_see_px']
check('probe_reads_both', probed['unit/pipe'] > 0 and probed['unit/sleeve'] > 0, probed)

clay = bpy.data.materials.new('clay'); sleeve.data.materials.append(clay)
faded = show_scene.see_through(job)
check('sleeve_faded_at_its_alpha', any(r['how'] == 'shown_through' and r['objects'] == ['unit/sleeve'] for r in faded), faded)
scene.frame_set(ON)
check('sleeve_keyed_open', sleeve.get(show_scene.SEE_PROP) == 1.0, sleeve.get(show_scene.SEE_PROP))
scene.frame_set(1)

# the build's own probe on this scene, from that pose: both read where the camera read them
_pose = __import__('frame_candidates')._pose
_pose(cam, eye, aim, LENS, 0.0)
scene.render.resolution_x, scene.render.resolution_y = SIZE
with tempfile.TemporaryDirectory() as out:
    probe_job = {'shot': {'duration_frames': 60}, 'probe': {'role': 'explain', 'subjects': ['unit'], 'frames': [ON - 1],
                                                            'key_parts': [{'id': p} for p in PARTS]}}
    try:   # a floating test model trips the framing gates (FRAME_MODEL_EDGE); the report is written first
        frame_probe.probe(probe_job, out)
    except ValueError as error:
        assert 'DETAIL' not in str(error) and 'KEY_PART' not in str(error), error
    built = json.loads((Path(out) / 'frame_report.json').read_text())
row = next(r for r in built['frames'] if r['frame'] == ON - 1)
check('build_reads_the_sleeve_drawn', row['key_px'].get('unit/sleeve', 0) > 0, row['key_px'])
check('build_reads_the_pipe_through', row['key_px'].get('unit/pipe', 0) > 0, row['key_px'])

# the same unit seen through a transparency opening: the housing fades in front of the faded sleeve - the sleeve is read
# with its own skin drawn and the housing's left out (drawn, the housing would hide it whole)
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj)
root = bpy.data.objects.new('unit', None); scene.collection.objects.link(root)
root['studio_id'] = root['studio_subject_id'] = 'unit'
box('unit/housing', (0, 0, 1.5), (1.6, 1.6, 3.0), root)
box('unit/sleeve', (0, 0, 1.5), (0.5, 0.5, 1.6), root)
box('unit/pipe', (0, 0, 1.5), (0.18, 0.18, 1.0), root)
cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam')); scene.collection.objects.link(cam); scene.camera = cam
frames[1]['show'] = {**section, 'how': 'transparency'}
job['shot']['actions'] = []
rows = show_scene.apply(job)
check('glass_housing_and_faded_sleeve', list(rows[0]['in_front']) == ['unit/housing'] and list(rows[0].get('shown_through') or {}) == ['unit/sleeve'], rows[0])
measured = frame_probe.measure_at(PARTS, eye, aim, LENS, 0.0, ON, SIZE, subjects=['unit'])
check('sleeve_read_behind_the_glass_housing', measured['unit/sleeve'] > measured['unit/pipe'] >= 20, measured)
keyed = frame_probe.measure_at(PARTS, eye, aim, LENS, 0.0, ON, SIZE, subjects=['unit'], keys=['unit', *PARTS])
check('whole_subject_key_is_read_through', keyed == measured, keyed)   # its glass housing is no part drawn over the sleeve
camera_frame.add_see_window(bpy.data.objects['unit/pipe'], (1, 60))       # the pipe faded too (a cheat fading both)
both = frame_probe.measure_at(PARTS, eye, aim, LENS, 0.0, ON, SIZE, subjects=['unit'])
check('two_faded_parts_each_read_alone', both['unit/pipe'] >= 20 and both['unit/sleeve'] > both['unit/pipe'], both)
print('SHOWN_THROUGH_SMOKE', json.dumps(report, default=str))
