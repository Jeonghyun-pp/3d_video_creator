"""Storyboard takes (Workbench only, no look render): three different camera takes of the driven reducer built side by
side on one sheet, the shot left as it was, nothing approvable until the user picks; the pick (their words) makes that
take the shot without a rebuild, and the normal sheet -> approve follows.
Run: .venv/bin/python tests/studio/storyboard_variants_smoke.py
"""
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio import storyboard
from studio.common import StudioError, read_json, write_json
from studio.mechanisms import planetary_spec
from studio.project import init_project, load_shot, shot_path
from studio.subjects import spec_path

source = (ROOT / 'tests/studio/mechanism_smoke.py').read_text()
ns = {}
exec(source[source.index('SUN_TURNS'):source.index('PROBE = ')], ns)
TAKES = [
    {'id': 'A', 'label': 'turntable: the whole reducer turning', 'why': 'shows all three planets riding the carrier',
     'change': {'camera': {**ns['CAMERA'], 'move': {'type': 'turntable', 'params': {'target': 'reducer', 'sweep_deg': 60}, 'lens_mm': 50}}}},
    {'id': 'B', 'label': 'macro push into one planet', 'why': 'teeth meeting are the point of the shot',
     'change': {'camera': {**ns['CAMERA'], 'move': {'type': 'macro_push', 'params': {'target': 'reducer', 'detail': 'reducer/planet_0'}, 'lens_mm': 50}}}},
    {'id': 'C', 'label': 'slide past, low angle', 'why': 'parallax separates ring, planets and carrier',
     'change': {'camera': {**ns['CAMERA'], 'move': {'type': 'slide', 'params': {'target': 'reducer', 'elevation_deg': 8}, 'lens_mm': 50}}}},
]


def refused(call, code):
    try:
        call(); raise AssertionError(f'expected {code}')
    except StudioError as error:
        assert error.code == code, (error.code, error.message)


checks = []
with tempfile.TemporaryDirectory(prefix='storyboard-takes-') as root:
    p = Path(init_project('takes_test', {'request': 'takes smoke', 'shots': [{'shot_id': 's', 'frame_count': 48}]}, root)['project_path'])
    spec_path(p, 'reducer').parent.mkdir(parents=True, exist_ok=True)
    write_json(spec_path(p, 'reducer'), planetary_spec('reducer', 0.002, 18, 27, 72, 3))
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': ns['SCENE'], 'camera': ns['CAMERA'], 'actions': [ns['DRIVE']]})
    write_json(shot_path(p, 's'), shot)
    before = storyboard.content_sha256(load_shot(p, 's'))

    takes = storyboard.variants(p, 's', TAKES, [0, 0.5, 1], captions={0: 'start', 1: 'end'})
    assert takes['rev'] == 'v01' and [t['id'] for t in takes['takes']] == ['A', 'B', 'C'] and Path(takes['image']).is_file(), takes
    assert len({t['version'] for t in takes['takes']}) == 3
    assert storyboard.content_sha256(load_shot(p, 's')) == before, 'takes changed the shot'
    eyes = read_json(Path(takes['sheet']).parent.parent.parent.parent / 'storyboard' / 's.json')['variants']['takes']
    assert eyes[0]['eyes'] != eyes[1]['eyes'] != eyes[2]['eyes']
    checks += ['three_takes_one_sheet', 'shot_left_as_it_was']

    refused(lambda: storyboard.approve(p, 's', '좋아요 이걸로 갈게요', 'r01'), 'DECISION_STALE')
    refused(lambda: storyboard.revise(p, 's', '조금 더 가까이 가주세요', [{'op': 'camera.closer', 'factor': 0.8}]), 'DECISION_STALE')
    refused(lambda: storyboard.pick(p, 's', 'v01', 'D', '네 번째 걸로 갈게요'), 'INPUT_INVALID')
    checks.append('nothing_approvable_before_a_pick')

    picked = storyboard.pick(p, 's', 'v01', 'B', '두 번째 거, 톱니 맞물리는 데로 들어가는 게 좋아요')
    shot = load_shot(p, 's')
    assert shot['camera']['move']['type'] == 'macro_push' and picked['version'] == takes['takes'][1]['version'], picked   # reused, not rebuilt
    assert picked['rev'] == 'r01'
    checks.append('pick_reuses_the_take')

    closer = storyboard.revise(p, 's', '조금만 더 가까이', [{'op': 'camera.closer', 'factor': 0.8}])
    assert closer['changes'] == ['/camera/move/params/distance_scale: 1.0 → 0.8'], closer['changes']           # a fitted move's knob has its default
    wider_end = storyboard.revise(p, 's', '끝에서 너무 잘려요, 덜 잘리게', [{'op': 'set', 'path': '/camera/move/params/detail_fill', 'value': 0.4}])
    assert wider_end['changes'] == ['/camera/move/params/detail_fill: 0.6 → 0.4'], wider_end['changes']   # any value, not only the named knobs
    assert load_shot(p, 's')['camera']['move']['params'] == {'target': 'reducer', 'detail': 'reducer/planet_0', 'distance_scale': 0.8, 'detail_fill': 0.4}
    refused(lambda: storyboard.revise(p, 's', '이름을 틀리게 적은 값', [{'op': 'set', 'path': '/camera/move/params/detail_fil', 'value': 0.4}]), 'INPUT_INVALID')
    checks.append('set_reaches_any_declared_value')
    approved = storyboard.approve(p, 's', '좋아요 이걸로', wider_end['rev'])
    refused(lambda: storyboard.pick(p, 's', 'v01', 'A', '첫 번째로 바꿀래요'), 'DECISION_STALE')
    checks += ['fitted_knob_edit', 'approved_after_pick', 'old_takes_closed']

print('STUDIO_STORYBOARD_VARIANTS_SMOKE ' + json.dumps({'ok': True, 'checks': checks, 'takes': takes['takes'], 'approved': approved['version']}))
