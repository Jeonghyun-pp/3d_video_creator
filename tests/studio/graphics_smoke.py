"""Explainer graphics: built as Grease Pencil, invisible to the beauty scene and control passes, rendered alone.

Checks: arrow / dimension / outline (Line Art) / drawn-on line build from anchors; graphic objects carry role
'graphic' and are hidden in the saved scene; the control clay is identical with and without graphics; the
graphics layer is transparent before a graphic starts and drawn after; a second render is reused.
Run: ../.venv/bin/python tests/studio/graphics_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PIL import Image
from studio.common import blender_binary, read_json, write_json
from studio.project import init_project, shot_path
from studio.blender import build_shot
from studio.generative.control import build_control
from studio.graphics import render_graphics

AUTHOR = '''import bpy
from mathutils import Vector
scene = bpy.context.scene
def box(name, loc, size):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc); o = bpy.context.object; o.scale = size; o.name = name; o['studio_id'] = name
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return o
box('floor', (0, 0, -0.25), (20, 20, 0.5)); box('column', (0, 2, 2), (1, 1, 4)); box('beam', (0, 2, 4.25), (8, 1, 0.5))
box('title', (-3, 2, 6), (2, 0.2, 1))['studio_scene_role'] = 'graphic'   # an authored 3D title: beauty only, never the layer
cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam')); scene.collection.objects.link(cam); scene.camera = cam
cam.location = (6, -10, 4); cam.rotation_euler = (Vector((0, 2, 2)) - cam.location).to_track_quat('-Z', 'Y').to_euler()
bpy.ops.object.light_add(type='SUN', location=(0, 0, 10))
scene['studio_authored_animation'] = True
'''
GRAPHICS = [
    {'graphic_id': 'load', 'kind': 'arrow', 'anchors': [[0, 2, 7.5], [0, 2, 4.6]], 'start_frame': 10, 'draw_on_s': 0.4},
    {'graphic_id': 'height', 'kind': 'dimension', 'anchors': [[1.2, 1.5, 0], [1.2, 1.5, 4]], 'start_frame': 5, 'text': 'auto'},
    {'graphic_id': 'col_outline', 'kind': 'outline', 'target': 'column', 'start_frame': 5, 'color_srgb': [1.0, 0.85, 0.1]},
    {'graphic_id': 'path', 'kind': 'draw_line', 'anchors': [[-4, 0, 0.05], [0, 0, 0.05], [0, 2, 0.05]], 'start_frame': 15, 'end_frame': 28, 'draw_on_s': 0.3},
]
PROBE = '''import bpy, json
objs = [o for o in bpy.data.objects if o.get('studio_graphic_layer')]
print('PROBE ' + json.dumps({'graphics': sorted(o.name for o in objs), 'types': sorted({o.type for o in objs}), 'hidden': all(o.hide_render for o in objs)}))
'''


def brightest_red(path):
    """Max red among opaque pixels: strokes keep their flat colour (unlit, sRGB in = sRGB out)."""
    image = Image.open(path).convert('RGBA')
    return max((r for r, g, b, a in image.getdata() if a > 200), default=0)


def alpha_pixels(path):
    return sum(1 for a in Image.open(path).getchannel('A').getdata() if a > 128)


checks = []
with tempfile.TemporaryDirectory(prefix='graphics-smoke-') as root:
    p = Path(init_project('graphics_test', {'request': 'Graphics smoke', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, root)['project_path'])
    author = p / 'author.py'; author.write_text(AUTHOR)
    plain = build_shot(p, 's', author)
    clay_plain = read_json(Path(build_control(p, 's', kinds=('depth', 'clay'), height=200)['control_dir']) / 'control.json')['files']['clay']['sha256']
    shot = read_json(shot_path(p, 's')); shot['graphics'] = GRAPHICS; write_json(shot_path(p, 's'), shot)
    built = build_shot(p, 's', author)
    version = p / 'shots/s/versions' / built['scene_version']
    report = read_json(version / 'graphics_report.json')['graphics']
    assert {r['graphic_id']: r['strokes'] for r in report} == {'load': 2, 'height': 3, 'col_outline': 0, 'path': 1}, report
    assert next(r for r in report if r['graphic_id'] == 'height')['text'] == '4.0 m', report
    out = subprocess.run([blender_binary(), '-b', str(version / 'scene.blend'), '--python-expr', PROBE.replace('\n', '\n')], capture_output=True, text=True)
    script = p / 'probe.py'; script.write_text(PROBE)
    out = subprocess.run([blender_binary(), '-b', str(version / 'scene.blend'), '--python', str(script)], capture_output=True, text=True).stdout
    state = json.loads(next(line for line in out.splitlines() if line.startswith('PROBE '))[6:])
    assert len(state['graphics']) == 4 and state['types'] == ['GREASEPENCIL'] and state['hidden'], state
    checks += ['graphics_built_from_anchors', 'graphics_hidden_role_graphic']

    clay_graphics = read_json(Path(build_control(p, 's', kinds=('depth', 'clay'), height=200)['control_dir']) / 'control.json')['files']['clay']['sha256']
    assert clay_graphics == clay_plain, 'graphics leaked into the control clay'
    checks.append('control_clay_unchanged_by_graphics')

    layer = render_graphics(p, 's', height=320)
    frames = Path(layer['frames_dir'])
    before, after, outline_only = alpha_pixels(frames / 'frame_000002.png'), alpha_pixels(frames / 'frame_000020.png'), alpha_pixels(frames / 'frame_000006.png')
    assert layer['frames'] == 30 and before == 0 and after > outline_only > 0, (before, outline_only, after)  # before == 0: no title
    red = brightest_red(frames / 'frame_000020.png')
    assert red >= 220, red   # default accent (0.95, 0.15, 0.10) sRGB -> ~242; lit strokes measured near black
    checks.append('authored_graphic_mesh_not_in_layer_and_flat_colour')
    texts = read_json(frames / 'graphics_render.json')['texts']
    assert '2' not in texts and texts['20'][0]['text'] == '4.0 m', sorted(texts)[:3]   # shown from start_frame 5 only
    checks.append('dimension_value_placed_from_start')
    again = render_graphics(p, 's', height=320)
    assert again['status'] == 'reused'
    checks += ['graphics_layer_transparent_then_drawn', 'outline_lineart_renders', 'graphics_render_reused']

    # Screen-space arrow with a camera moving along its view (focus of expansion = frame centre): drawn in the frame (constant size, linear fades); an arrow on the
    # flight line (through the focus of expansion) is refused as illegible.
    moving = AUTHOR + "cam.keyframe_insert('location', frame=1); cam.location = (4.2, -6.4, 3.4); cam.keyframe_insert('location', frame=30)\n"
    author.write_text(moving)
    side = {'graphic_id': 'side', 'kind': 'arrow', 'space': 'screen', 'points_2d': [[0.2, 0.3], [0.2, 0.5]], 'start_frame': 4, 'end_frame': 26,
            'draw_on_s': 0.2, 'fade_frames': 6}
    shot = read_json(shot_path(p, 's')); shot['graphics'] = [side]; write_json(shot_path(p, 's'), shot)
    built = build_shot(p, 's', author)
    row = read_json(p / 'shots/s/versions' / built['scene_version'] / 'graphics_report.json')['graphics'][0]
    assert row['space'] == 'screen' and not row['legibility']['failures'] and row['legibility']['min_flow_angle_deg'] >= 25, row
    layer = render_graphics(p, 's', height=320)
    frames = Path(layer['frames_dir'])
    alphas = {f: max(Image.open(frames / f'frame_{f:06d}.png').getchannel('A').getdata()) for f in (3, 6, 16, 26)}
    assert alphas[3] == 0 and 0 < alphas[6] < alphas[16] and alphas[26] < alphas[16], alphas   # fades, no pop
    box = Image.open(frames / 'frame_000016.png').getchannel('A').getbbox()
    w, h = Image.open(frames / 'frame_000016.png').size
    assert abs((box[0] + box[2]) / 2 / w - 0.2) < 0.03 and abs(box[1] / h - 0.3) < 0.03, (box, w, h)   # where it was asked, in the frame
    checks.append('screen_arrow_drawn_in_frame_with_fades')
    axis = {**side, 'graphic_id': 'axis', 'points_2d': [[0.5, 0.25], [0.5, 0.45]]}
    shot = read_json(shot_path(p, 's')); shot['graphics'] = [axis]; write_json(shot_path(p, 's'), shot)
    try:
        bad = build_shot(p, 's', author)
        raise AssertionError('arrow on the flight line accepted: ' + json.dumps(read_json(p / 'shots/s/versions' / bad['scene_version'] / 'graphics_report.json')))
    except Exception as error:   # the build reports the gate
        assert 'GRAPHIC_ILLEGIBLE' in str(error), str(error)[:300]
    checks.append('arrow_on_the_flight_line_refused')

print('STUDIO_GRAPHICS_SMOKE ' + json.dumps({'ok': True, 'checks': checks, 'alpha_px': {'f2': before, 'f6': outline_only, 'f20': after}}))
