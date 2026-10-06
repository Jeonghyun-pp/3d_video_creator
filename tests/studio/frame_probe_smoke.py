"""Frame probe: the build looks at its own frames through the real camera and judges what shows, whatever made the scene.
A camera whose near plane slices the subject fails (FRAME_NEAR_CLIP_CUT); a camera that sees nothing fails (FRAME_EMPTY);
a key part behind a wall fails an explain shot (KEY_PART_INVISIBLE) and only warns on a mood shot; a clean shot passes
in under a second of probe time and leaves its id images for the agent to open.
Run: .venv/bin/python tests/studio/frame_probe_smoke.py
"""
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, shot_path

SCENE = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
         'materials': {'m': {'color': [0.7, 0.4, 0.2]}},
         'primitives': [{'id': 'pump', 'shape': 'box', 'size': [1, 1, 1], 'at': [0, 0, 0.5], 'material': 'm'},
                        {'id': 'valve', 'shape': 'box', 'size': [0.3, 0.3, 0.3], 'at': [0, 3, 0.15], 'material': 'm'},
                        {'id': 'wall', 'shape': 'box', 'size': [3, 0.2, 3], 'at': [0, 1.6, 1.5], 'material': 'm'},
                        {'id': 'floor', 'shape': 'box', 'size': [20, 20, 0.2], 'at': [0, 0, -0.1], 'material': 'm'}]}


def camera(location, target):
    return {'projection': 'perspective', 'movement': 'authored', 'target_anchor': None,
            'keys': [{'frame': 0, 'location': location, 'target': target}, {'frame': 29, 'location': location, 'target': target}]}


def build(p, **changes):
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': SCENE, 'camera': camera([0, -5, 1.5], [0, 0, 0.5])})
    shot.pop('key_parts', None)
    shot.update(changes)
    write_json(shot_path(p, 's'), shot)
    return build_shot(p, 's', None)


def fails(p, code, **changes):
    try:
        build(p, **changes)
    except StudioError as error:
        assert error.code == code, (code, error.code, error.message[:400])
        return error
    raise AssertionError(f'{code} expected, the build passed')


checks = []
with tempfile.TemporaryDirectory(prefix='frame-probe-smoke-') as root:
    p = Path(init_project('probe_test', {'request': 'frame probe smoke', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, root)['project_path'])
    built = build(p, key_parts=[{'id': 'pump'}])
    report = read_json(p / 'shots/s/versions' / built['scene_version'] / 'frame_report.json')
    assert not report['gate_failures'] and report['frames'][0]['key_px']['pump'] > 500, report['frames'][0]
    assert report['seconds'] < 3 and len(report['images']) >= 8, (report['seconds'], len(report['images']))
    checks.append(f"clean_shot_passes ({report['seconds']} s, {len(report['frames'])} frames)")

    fails(p, 'FRAME_EMPTY', camera=camera([0, -5, 1.5], [0, -10, 8]))   # looks up and away: sky only
    checks.append('camera_seeing_nothing_fails')

    error = fails(p, 'FRAME_NEAR_CLIP_CUT', camera=camera([0, -0.55, 0.5], [0, 0, 0.5]), key_parts=[{'id': 'pump'}])   # 5 cm from the face, clip 0.1 m
    checks.append('near_plane_slicing_the_subject_fails')

    fails(p, 'KEY_PART_INVISIBLE', key_parts=[{'id': 'valve'}])   # the wall hides it from the camera
    mood = build(p, key_parts=[{'id': 'valve'}], route={'mode': 'blender', 'role': 'mood', 'status': 'proposed', 'decided_by': 'agent',
                                                          'approval_evidence': None, 'approved_at': None})
    assert any(w.startswith('KEY_PART_INVISIBLE_MOOD') for w in mood['warnings']), mood['warnings']
    checks.append('hidden_key_part_fails_explain_warns_mood')

    fails(p, 'INPUT_INVALID', key_parts=[{'id': 'no_such_part'}])
    checks.append('unknown_key_part_refused')

    # A key part inside another key part keeps its own class, whatever order they are declared in (Astra's P0 report).
    from studio.mechanisms import harmonic_spec
    from studio.subjects import spec_path
    q = Path(init_project('nested', {'request': 'nested key parts', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, root)['project_path'])
    spec_path(q, 'hd').parent.mkdir(parents=True, exist_ok=True); write_json(spec_path(q, 'hd'), harmonic_spec('hd', 0.0005, 100))
    for order in ([{'id': 'hd/flexspline'}, {'id': 'hd'}], [{'id': 'hd'}, {'id': 'hd/flexspline'}]):
        shot = read_json(shot_path(q, 's'))
        shot.update({'scene': {'world': SCENE['world'], 'instances': [{'id': 'hd', 'subject': 'hd', 'at': [0, 0, 0]}]},
                     'camera': camera([0, -0.12, 0.08], [0, 0, 0]), 'key_parts': order})
        write_json(shot_path(q, 's'), shot)
        nested = build_shot(q, 's', None)
        rows = read_json(q / 'shots/s/versions' / nested['scene_version'] / 'frame_report.json')['frames']
        assert max(r['key_px'].get('hd/flexspline', 0) for r in rows) > 50 and max(r['key_px'].get('hd', 0) for r in rows) > 50, rows[0]['key_px']
    checks.append('nested_key_parts_keep_their_own_class_in_any_order')

print('STUDIO_FRAME_PROBE_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
