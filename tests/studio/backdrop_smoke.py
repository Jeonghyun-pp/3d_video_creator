"""shot.scene.backdrop on a data-built shot (the harmonic reducer, macro push): a still image (here a synthetic one - no
paid call) becomes a camera-fixed card past every mesh, covering the frame at every sampled frame, blurred, seen by
camera rays only, scene role 'atmosphere'. A missing image is refused before Blender runs.
Run: .venv/bin/python tests/studio/backdrop_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import StudioError, blender_binary, read_json, write_json
from studio.mechanisms import harmonic_spec
from studio.project import init_project, shot_path
from studio.subjects import spec_path

source = (ROOT / 'tests/studio/harmonic_smoke.py').read_text()
ns = {}
exec(source[source.index('SCENE = '):source.index('PROBE = ')], ns)
CAMERA = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
          'move': {'type': 'macro_push', 'params': {'target': 'hd', 'detail': [0.0264, 0.0, 0.003], 'detail_size_m': 0.012}, 'lens_mm': 50}}
PROBE = '''import bpy, json
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector
scene = bpy.context.scene
card = bpy.data.objects['StudioBackdrop']
out = {'parent': card.parent == scene.camera, 'role': card.get('studio_scene_role'), 'glossy': card.visible_glossy, 'camera_ray': card.visible_camera,
       'covers': [], 'behind': [], 'blurred': None}
for f in (1, 45, 90):
    scene.frame_set(f)
    corners = [card.matrix_world @ v.co for v in card.data.vertices]
    views = [world_to_camera_view(scene, scene.camera, c) for c in corners]
    out['covers'].append(min(v.x for v in views) <= 0 and max(v.x for v in views) >= 1 and min(v.y for v in views) <= 0 and max(v.y for v in views) >= 1)
    eye = scene.camera.matrix_world.translation
    far = max((o.matrix_world @ Vector(c) - eye).length for o in scene.objects if o.type == 'MESH' and o is not card for c in o.bound_box)
    out['behind'].append((card.matrix_world.translation - eye).length > far)
px = card.active_material.node_tree.nodes['Image Texture'].image.pixels[:]
out['blurred'] = max(px[0::4]) - min(px[0::4]) < 0.9      # the hard black/white checker is softened
scene.frame_set(1)
support = bpy.data.objects['StudioSupport']
subject_low = min((o.matrix_world @ Vector(c)).z for o in scene.objects if o.type == 'MESH' and o.get('studio_subject_id') for c in o.bound_box)
support_top = max((support.matrix_world @ Vector(c)).z for c in support.bound_box)
out['contact_gap'] = subject_low - support_top
ys = [(support.matrix_world @ Vector(c)).y for c in support.bound_box]
subject_ys = [(o.matrix_world @ Vector(c)).y for o in scene.objects if o.type == 'MESH' and o.get('studio_subject_id') for c in o.bound_box]
out['reach'] = {'front': min(subject_ys) - min(ys), 'behind': max(ys) - max(subject_ys)}   # camera at azimuth 0 looks from -Y
lamps = [o for o in scene.objects if o.type == 'LIGHT' and o.get('studio_keep_light')]
out['lamps'] = sorted(o.name for o in lamps if not o.hide_render)
out['key_warmer'] = bpy.data.objects['StudioBackdropKey'].data.color[2] < bpy.data.objects['StudioBackdropFill'].data.color[2]
dome = bpy.data.objects['StudioBackdropDome']
out['dome'] = {'camera': dome.visible_camera, 'glossy': dome.visible_glossy, 'role': dome.get('studio_scene_role')}
print('PROBE ' + json.dumps(out))
'''

checks = []
with tempfile.TemporaryDirectory(prefix='backdrop-smoke-') as root:
    p = Path(init_project('backdrop_test', {'request': 'backdrop smoke', 'shots': [{'shot_id': 's', 'frame_count': 90}]}, root)['project_path'])
    spec_path(p, 'hd').parent.mkdir(parents=True, exist_ok=True)
    write_json(spec_path(p, 'hd'), harmonic_spec('hd', 0.00125, 40))
    (p / 'backdrops').mkdir()
    image = Image.new('RGB', (360, 640))
    image.putdata([(255, 255, 255) if (x // 8 + y // 8) % 2 else (0, 0, 0) for y in range(640) for x in range(360)])
    image.save(p / 'backdrops/test.png')
    scene = {**ns['SCENE'], 'materials': {'bench': {'color': [0.5, 0.5, 0.52], 'catalog': 'brushed_stainless', 'metallic': 1.0, 'roughness': 0.3}},
             'backdrop': {'image': 'backdrops/test.png', 'blur_px': 10,
                          'relation': {'support': {'kind': 'bench', 'material': 'bench'}, 'view': {'elevation_deg': 30},
                                       'light': {'key_side': 'left', 'key_kelvin': 3200, 'fill_kelvin': 6500, 'reflections': True}}}}
    shot = read_json(shot_path(p, 's')); shot.update({'scene': scene, 'camera': CAMERA, 'actions': [{**ns['DRIVE'], 'end_frame': 89}]})
    write_json(shot_path(p, 's'), shot)
    version = p / 'shots/s/versions' / build_shot(p, 's', None)['scene_version']
    report = read_json(version / 'backdrop_report.json')
    script = version.parent / 'probe.py'; script.write_text(PROBE)
    out = subprocess.run([blender_binary(), '-b', str(version / 'scene.blend'), '--python', str(script)], capture_output=True, text=True).stdout
    state = json.loads(next(line for line in out.splitlines() if line.startswith('PROBE '))[6:])
    assert state['parent'] and state['role'] == 'atmosphere' and not state['glossy'] and state['camera_ray'], state
    assert all(state['covers']) and all(state['behind']) and state['blurred'], state
    checks.append('camera_fixed_card_behind_everything_covers_the_frame_blurred')
    assert abs(state['contact_gap']) < 1e-6, state['contact_gap']                      # the subject stands on the bench
    assert state['reach']['front'] > 4 * state['reach']['behind'] > 0, state['reach']   # the place shows past the back edge
    assert state['lamps'] == ['StudioBackdropFill', 'StudioBackdropKey'] and state['key_warmer'], state   # kept through the look, 3200 K key
    assert state['dome'] == {'camera': False, 'glossy': True, 'role': 'atmosphere'}, state['dome']
    checks.append('relation_support_lights_and_reflections')

    shot['scene']['backdrop']['image'] = 'backdrops/missing.png'; write_json(shot_path(p, 's'), shot)
    try:
        build_shot(p, 's', None); raise AssertionError('a missing backdrop image was accepted')
    except StudioError as error:
        assert error.code == 'LAYOUT_INVALID' and 'backdrop image' in error.message, (error.code, error.message[:200])
    checks.append('missing_image_refused_before_blender')

print('STUDIO_BACKDROP_SMOKE ' + json.dumps({'ok': True, 'checks': checks, 'report': report}))
