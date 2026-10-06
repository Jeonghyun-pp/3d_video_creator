"""Expressive settings as data: a grade, compositor ops and engine settings declared in shot.render are applied after
the look and compose with it (exposure offset on the metered exposure, ops spliced before the look's output, its own
ops kept), survive the render profile, are recorded in expressive_report.json, and reach the saved scene. A
position-changing op with anchored labels is refused; a shot that declares nothing is not touched.
Run: .venv/bin/python tests/studio/expressive_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import StudioError, blender_binary, read_json, write_json
from studio.project import init_project, shot_path

SCENE = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
         'materials': {'m': {'color': [0.7, 0.4, 0.2]}},
         'primitives': [{'id': 'pump', 'shape': 'box', 'size': [1, 1, 1], 'at': [0, 0, 0.5], 'material': 'm'},
                        {'id': 'floor', 'shape': 'box', 'size': [20, 20, 0.2], 'at': [0, 0, -0.1], 'material': 'm'}]}
CAMERA = {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None,
          'keys': [{'frame': 0, 'location': [0, -5, 1.5], 'target': [0, 0, 0.5]}, {'frame': 11, 'location': [0, -5, 1.5], 'target': [0, 0, 0.5]}]}
EXPRESSIVE = {'grade': {'exposure_offset_ev': 0.5, 'gamma': 1.1, 'curve': [[0, 0], [0.5, 0.58], [1, 1]]},
              'compositor': {'ops': [{'op': 'glare', 'type': 'Bloom', 'threshold': 0.8, 'strength': 0.4}, {'op': 'soften', 'factor': 0.3}]},
              'engine_settings': {'cycles': {'max_bounces': 6, 'blur_glossy': 0.5}, 'render': {'use_motion_blur': True, 'motion_blur_shutter': 0.3}}}
PROBE = '''import bpy, json, sys
sys.path.insert(0, %r)
import render_profile
s = bpy.context.scene
tree = s.compositing_node_group
render_profile.apply_render_profile(s, {'engine': 'CYCLES', 'device': 'CPU', 'samples': 4}) if hasattr(render_profile, 'apply_render_profile') else None
print('PROBE ' + json.dumps({'exposure': s.view_settings.exposure, 'gamma': s.view_settings.gamma, 'curve': s.view_settings.use_curve_mapping,
      'nodes': sorted(n.name for n in tree.nodes) if tree else [], 'bounces': s.cycles.max_bounces, 'blur': s.cycles.blur_glossy,
      'mblur': [s.render.use_motion_blur, round(s.render.motion_blur_shutter, 3)]}))
''' % str(ROOT / 'studio/blender_ops')


def build(p, render, labels=None):
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': SCENE, 'camera': CAMERA, 'labels': labels or []})
    shot['render'] = {k: v for k, v in shot['render'].items() if k not in EXPRESSIVE}
    shot['render'].update({'look_preset': 'photoreal_product', **render})
    write_json(shot_path(p, 's'), shot)
    return build_shot(p, 's', None)


checks = []
with tempfile.TemporaryDirectory(prefix='expressive-smoke-') as root:
    p = Path(init_project('expr', {'request': 'expressive smoke', 'shots': [{'shot_id': 's', 'frame_count': 12}]}, root)['project_path'])
    plain = build(p, {})
    plain_dir = p / 'shots/s/versions' / plain['scene_version']
    assert not (plain_dir / 'expressive_report.json').exists()
    look = read_json(plain_dir / 'look_report.json')
    again = build(p, {})
    assert read_json(p / 'shots/s/versions' / again['scene_version'] / 'look_report.json')['scene_state_sha256'] == look['scene_state_sha256']
    checks.append('nothing_declared_nothing_touched')

    graded = build(p, EXPRESSIVE)
    version = p / 'shots/s/versions' / graded['scene_version']
    report = read_json(version / 'expressive_report.json')
    offset = next(r for r in report['applied'] if r['path'] == 'grade.exposure_offset_ev')
    assert abs(offset['after'] - offset['before'] - 0.5) < 1e-6, offset
    script = version.parent / 'probe.py'; script.write_text(PROBE)
    out = subprocess.run([blender_binary(), '-b', str(version / 'scene.blend'), '--python', str(script)], capture_output=True, text=True).stdout
    state = json.loads(next(line for line in out.splitlines() if line.startswith('PROBE '))[6:])
    assert abs(state['exposure'] - offset['after']) < 1e-5 and abs(state['gamma'] - 1.1) < 1e-6 and state['curve'], state
    ours = [n for n in state['nodes'] if n.startswith('StudioExpressive_')]
    assert len(ours) == 2 and len(state['nodes']) > len(ours) + 2, state['nodes']   # the look's own nodes are still there
    assert state['bounces'] == 6 and abs(state['blur'] - 0.5) < 1e-6 and state['mblur'] == [True, 0.3], state
    checks += ['offset_composes_with_the_metered_exposure', 'ops_spliced_before_the_look_output', 'engine_settings_survive_the_render_profile']

    try:
        build(p, {'compositor': {'ops': [{'op': 'lens_distortion', 'distortion': 0.02}]}},
              labels=[{'label_id': 'l', 'anchor': 'pump/center', 'start_frame': 0, 'end_frame': 11, 'text': 'pump', 'slot': 'upper_left',
                       'occlusion_policy': 'hide'}])
        raise AssertionError('lens distortion with anchored labels was built')
    except StudioError as error:
        assert 'EXPRESSIVE_APPLY_FAILED' in error.message or error.code == 'EXPRESSIVE_APPLY_FAILED', (error.code, error.message[-400:])
    checks.append('position_changing_op_with_anchored_labels_refused')

print('STUDIO_EXPRESSIVE_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
