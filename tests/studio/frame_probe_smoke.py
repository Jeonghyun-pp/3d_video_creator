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
    shot.pop('concealed_parts', None)
    shot.update(changes)
    if shot.get('screen') is None:
        shot.pop('screen', None)
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

    # Concealed parts: what an intact view must not show (2026-10-07: an engine exterior showed its valve train).
    hidden = build(p, key_parts=[{'id': 'pump'}], concealed_parts=[{'id': 'valve'}])   # behind the wall: hidden, passes
    hidden_report = read_json(p / 'shots/s/versions' / hidden['scene_version'] / 'frame_report.json')
    assert hidden_report['summary']['concealed_parts'] == ['valve'], hidden_report['summary']
    shown = fails(p, 'CONCEALED_PART_VISIBLE', concealed_parts=[{'id': 'pump'}])   # in plain view
    assert 'pump' in shown.message, shown.message
    fails(p, 'INPUT_INVALID', key_parts=[{'id': 'pump'}], concealed_parts=[{'id': 'pump'}])   # must show and must not
    checks.append('concealed_part_hidden_passes_shown_fails_contradiction_refused')

    # Screen targets (shot.screen, screen_core.py): measured on the same id pass, judged only when declared.
    on = build(p, key_parts=[{'id': 'pump'}], screen={'targets': [{'id': 'mid', 'metric': 'center_x', 'of': 'pump', 'value': 0.5, 'tol': 0.05}]})
    shot_report = read_json(p / 'shots/s/versions' / on['scene_version'] / 'frame_report.json')
    assert on['screen']['targets'][0]['score'] > 0.9 and not any('SCREEN_' in w for w in on['warnings']), (on['screen'], on['warnings'])
    w, h = shot_report['summary']['size_px']
    motion = shot_report['screen']['motion']
    for row in shot_report['frames']:   # the projected box centre and the id pass agree within 1.5 px (a box's silhouette = its corners' hull)
        if row['frame'] in motion['frames'] and row['shapes']['key:pump']['px']:
            box, centre = row['shapes']['key:pump']['bbox'], motion['centers']['key:pump'][motion['frames'].index(row['frame'])]
            assert abs((box[0] + box[2]) / 2 - centre[0]) * w <= 1.5 and abs((box[1] + box[3]) / 2 - centre[1]) * h <= 1.5, (row['frame'], box, centre)
    assert shot_report['seconds'] < 3, shot_report['seconds']
    off = build(p, key_parts=[{'id': 'pump'}], screen={'targets': [{'id': 'left', 'metric': 'center_x', 'of': 'pump', 'value': 0.2, 'tol': 0.05}]})
    assert any(w.startswith('SCREEN_TARGET_MISSED') for w in off['warnings']), off['warnings']   # taste: built, said
    checks.append(f"screen_targets_measured_projection_agrees_miss_warns ({shot_report['seconds']} s)")
    role = lambda r: {'mode': 'blender', 'role': r, 'status': 'proposed', 'decided_by': 'agent', 'approval_evidence': None, 'approved_at': None}  # noqa: E731
    low = dict(camera=camera([0, -5, 3.2], [0, 0, 3.0]), key_parts=[{'id': 'pump'}], screen=None)   # pump low in the frame, under the caption area
    fails(p, 'KEY_PART_UNDER_UI', route=role('explain'), **low)
    covered = build(p, route=role('mood'), **low)
    assert any(w.startswith('KEY_PART_UNDER_UI') for w in covered['warnings']), covered['warnings']
    checks.append('key_part_under_the_platform_ui_fails_explain_warns_mood')


    # Declared ids, one rule (2026-10-08, archcut3): a scene of primitives names its own subject with screen.subject and
    # group ids (st.slab = st.slab.0, st.slab.1); a keep id that matches nothing is refused at build time with the
    # closest built ids; a scene with no subject at all says so (SUBJECT_UNDECLARED, taste).
    section = {**SCENE, 'primitives': SCENE['primitives'] + [{'id': f'st.slab.{i}', 'shape': 'box', 'size': [2, 2, 0.2], 'at': [-1.5 + 3 * i, 0, 2.2], 'material': 'm'}
                                                               for i in range(2)]}
    bare = build(p, scene=section, screen=None)
    assert any(w.startswith('SUBJECT_UNDECLARED') for w in bare['warnings']), bare['warnings']
    named = build(p, scene=section, screen={'subject': ['st.slab']})
    assert named['frame']['subject_share_median'] > 0 and not any(w.startswith('SUBJECT_UNDECLARED') for w in named['warnings']), (named['frame'], named['warnings'])
    wrong = fails(p, 'INPUT_INVALID', scene=section, screen={'subject': ['st.slab'], 'keep': ['st.slb']})
    assert 'st.slab' in wrong.message and 'closest built' in wrong.message, wrong.message
    checks.append('screen_subject_group_ids_count_unknown_keep_refused_with_candidates')

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
    shot = read_json(shot_path(q, 's'))   # an instance id with '_' builds as 'h-d': the declared spelling still resolves
    shot.update({'scene': {'world': SCENE['world'], 'instances': [{'id': 'h_d', 'subject': 'hd', 'at': [0, 0, 0]}]}, 'key_parts': [{'id': 'h_d/flexspline'}]})
    write_json(shot_path(q, 's'), shot)
    spelled = build_shot(q, 's', None)
    rows = read_json(q / 'shots/s/versions' / spelled['scene_version'] / 'frame_report.json')['frames']
    assert max(r['key_px'].get('h_d/flexspline', 0) for r in rows) > 50, rows[0]['key_px']
    checks.append('declared_instance_spelling_resolves_to_its_layout_id')

print('STUDIO_FRAME_PROBE_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
