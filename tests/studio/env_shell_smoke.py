"""Environment shell end to end (Workbench only): a world that declares a sea and a day sky gets a ground disc reaching
the camera's horizon and a sky dome; the clay review shows them (sky above, sea below the horizon), the frame probe
reads the sky as background and the sea as what is around the subject, a generated take's @background mask covers both,
an author's own wide ground is kept, and a world without a shell builds nothing.
Run: .venv/bin/python tests/studio/env_shell_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import blender_binary, read_json, write_json
from studio.project import init_project, shot_path

FRAMES = 24
EYE, AIM = [0.0, -70.0, 9.0], [0.0, 0.0, 4.0]
SCENE = {'materials': {'hull': {'color': [0.6, 0.6, 0.62]}},
         'primitives': [{'id': 'ship', 'shape': 'box', 'size': [30, 12, 6], 'at': [0, 0, 3], 'material': 'hull'}]}
WORLD = {'schema_version': 1, 'kind': 'independent', 'place': 'open sea', 'time_light': {'time_of_day': 'midday'},
         'shell': {'ground': 'sea'}, 'beats': [], 'elements': []}
RENDER = r'''
import bpy, json, sys
import numpy as np
scene = bpy.context.scene
scene.render.engine = 'BLENDER_WORKBENCH'
scene.display.shading.light = 'STUDIO'; scene.display.shading.color_type = 'MATERIAL'
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 90, 160, 100
scene.render.filepath = sys.argv[-1] + '.png'
scene.frame_set(1)
bpy.ops.render.render(write_still=True)
img = bpy.data.images.load(scene.render.filepath)
px = np.array(img.pixels[:]).reshape(160, 90, 4)[::-1]   # top row first
top, bottom = px[5:20, 5:85, :3].mean((0, 1)), px[145:158, 5:85, :3].mean((0, 1))
objs = {o.name: {'tag': o.get('studio_env_shell'), 'role': o.get('studio_scene_role'), 'scale': list(o.scale)} for o in scene.objects if o.get('studio_env_shell')}
open(sys.argv[-1], 'w').write(json.dumps({'top': top.tolist(), 'bottom': bottom.tolist(), 'objects': objs}))
'''


def project(root, name, scene, world):
    p = Path(init_project(name, {'request': 'env shell smoke', 'shots': [{'shot_id': 's', 'frame_count': FRAMES}]}, root)['project_path'])
    meta = read_json(p / 'project.json')
    meta['policy'] = {'gates': {'FRAME_SUBJECT_SMALL': 'warn'}}; write_json(p / 'project.json', meta)
    if world:
        write_json(p / 'world.json', world)
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': scene, 'screen': {'subject': ['ship']},
                 'camera': {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None,
                            'keys': [{'frame': 0, 'location': EYE, 'target': AIM}, {'frame': FRAMES - 1, 'location': EYE, 'target': AIM}]}})
    write_json(shot_path(p, 's'), shot)
    return p


def look(version, root):
    script = Path(root) / 'render.py'; script.write_text(RENDER)
    out = Path(root) / f'look_{version.parent.parent.name}_{version.name}.json'
    subprocess.run([blender_binary(), '--background', '--factory-startup', '--disable-autoexec', str(version / 'scene.blend'), '--python-exit-code', '1',
                    '--python', str(script), '--', str(out)], check=True, capture_output=True, timeout=600)
    return json.loads(out.read_text())


checks = []
with tempfile.TemporaryDirectory(prefix='env-shell-smoke-') as root:
    p = project(root, 'shell', SCENE, WORLD)
    built = build_shot(p, 's', None)
    version = p / 'shots/s/versions' / built['scene_version']
    report = read_json(version / 'env_shell_report.json')
    assert report['built'] == ['ground', 'sky'] and report['shell'] == {'ground': 'sea', 'sky': 'day'}, report
    import math
    true_dip = math.degrees(math.sqrt(2 * report['camera_top_m'] / 6_371_000))
    assert abs(report['horizon_dip_deg'] - true_dip) <= 0.02, (report, true_dip)   # the disc's edge sits where a real horizon does
    assert any(w.startswith('ENV_SHELL_CLAY') for w in built['warnings']), built['warnings']   # no model paints it here: said
    checks.append(f"built ({report['seconds']} s, radius {report['radius_m']} m, dip {report['horizon_dip_deg']} deg)")
    seen = look(version, root)
    top, bottom = seen['top'], seen['bottom']
    assert top[2] > top[0] + 0.05 and bottom[2] > bottom[0] and sum(top) > sum(bottom) + 0.3, seen   # pale blue sky, dark sea
    roles = {v['tag']: v['role'] for v in seen['objects'].values()}
    assert roles == {'ground': 'environment_shell', 'sky': 'atmosphere'}, roles
    checks.append('clay_shows_sky_over_sea')
    frame = read_json(version / 'frame_report.json')
    row = frame['frames'][0]
    assert row['support_share'] > 0.2 and row['opaque_share'] < 0.95, row   # the sea is around the subject; the sky is background
    assert not any(w.startswith('CONTEXT_VOID') for w in built['warnings']), built['warnings']
    checks.append(f"probe: sea {round(row['support_share'], 2)} of the frame, sky background")

    from studio.generative.keep import build_keep_masks
    masks = build_keep_masks(p, 's', parts=[], frames=[0], height=160, background=True)
    from PIL import Image
    mask = Image.open(masks['pattern'] % 0).convert('L')
    w, h = mask.size
    sea, sky = mask.getpixel((w // 2, h - 4)), mask.getpixel((w // 2, 3))
    assert sea > 200 and sky > 200, (sea, sky)   # @background: the model paints the sea and the sky
    checks.append('background_mask_covers_sea_and_sky')

    theirs = json.loads(json.dumps(SCENE))
    theirs['primitives'].append({'id': 'seabox', 'shape': 'box', 'size': [2000, 2000, 2], 'at': [0, 0, -1], 'material': 'hull'})
    q = project(root, 'author_ground', theirs, WORLD)
    built_q = build_shot(q, 's', None)
    report_q = read_json(q / 'shots/s/versions' / built_q['scene_version'] / 'env_shell_report.json')
    assert report_q['built'] == ['sky'] and report_q['kept'][0]['kind'] == 'ground', report_q
    checks.append('author_ground_kept')

    r = project(root, 'no_shell', SCENE, {**WORLD, 'shell': None})
    built_r = build_shot(r, 's', None)
    assert not (r / 'shots/s/versions' / built_r['scene_version'] / 'env_shell_report.json').exists()
    checks.append('no_shell_declared_nothing_built')

    # stand-ins at their real size, and the scale a shot opening a new place shows
    elements = [{'id': 'crew', 'function': 'agent', 'description': 'a recovery crew member', 'why': 'how big the ship is',
                 'owner': {'shape': 'blender', 'look': 'generative'}, 'blockout': {'kind': 'cylinder', 'size_m': [0.5, 0.5, 1.75], 'scale_ref': True}},
                {'id': 'tug', 'function': 'set', 'description': 'a tug boat beside the ship', 'why': 'what tows it home',
                 'owner': {'shape': 'blender', 'look': 'generative'}, 'blockout': {'kind': 'box', 'size_m': [20, 7, 5]}}]
    staged = {**WORLD, 'elements': elements}
    near = json.loads(json.dumps(SCENE)); near['stand_ins'] = [{'element': 'crew', 'at': [4, -40, 0]}, {'element': 'tug', 'at': [-30, 15, 0]}]
    s1 = project(root, 'scale_seen', near, staged)
    shot = read_json(shot_path(s1, 's')); shot['screen']['role'] = 'establish'; write_json(shot_path(s1, 's'), shot)
    built_s = build_shot(s1, 's', None)
    version_s = s1 / 'shots/s/versions' / built_s['scene_version']
    refs = read_json(version_s / 'frame_report.json')['summary']['scale_refs']
    layout = read_json(version_s / 'layout_report.json')
    assert layout['built']['stand_ins'] == 2 and refs.get('crew', 0) >= 24, (layout['built'], refs)
    assert not any(w.startswith('SCALE_REF_MISSING') for w in built_s['warnings']), built_s['warnings']
    checks.append(f"stand_ins_built_crew_{refs['crew']}px")
    far = json.loads(json.dumps(SCENE)); far['stand_ins'] = [{'element': 'tug', 'at': [-30, 15, 0]}]
    s2 = project(root, 'scale_missing', far, staged)
    shot = read_json(shot_path(s2, 's')); shot['screen']['role'] = 'establish'; write_json(shot_path(s2, 's'), shot)
    built_m = build_shot(s2, 's', None)
    assert any(w.startswith('SCALE_REF_MISSING') and 'crew' in w for w in built_m['warnings']), built_m['warnings']
    checks.append('scale_ref_missing_said_with_the_worlds_crew')
    hidden = json.loads(json.dumps(SCENE)); hidden['stand_ins'] = [{'element': 'crew', 'at': [0, 9, 0]}]   # behind the hull
    s3 = project(root, 'scale_hidden', hidden, staged)
    shot = read_json(shot_path(s3, 's')); shot['screen']['role'] = 'establish'; write_json(shot_path(s3, 's'), shot)
    built_h = build_shot(s3, 's', None)
    refs_h = read_json(s3 / 'shots/s/versions' / built_h['scene_version'] / 'frame_report.json')['summary']['scale_refs']
    assert refs_h.get('crew', 0) < 24 and any(w.startswith('SCALE_REF_MISSING') for w in built_h['warnings']), (refs_h, built_h['warnings'])
    checks.append(f"scale_ref_hidden_by_the_hull_{refs_h.get('crew')}px")

print('STUDIO_ENV_SHELL_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
