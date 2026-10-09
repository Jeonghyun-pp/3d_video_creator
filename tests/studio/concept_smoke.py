"""Concept frames end to end (no paid call): a delegated project cannot stage a shot before a concept is picked
(CONCEPT_UNPICKED); concepts are added with provenance and shown on one sheet; the agent's pick in a delegated run is
recorded as the agent's, never as approval; the storyboard sheet gets the target row (concept beside the hero frame),
the board puts every shot on one page, and approval keeps the hero frame and the concept in the contract.
Run: .venv/bin/python tests/studio/concept_smoke.py
"""
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from PIL import Image
from studio import concept, storyboard
from studio.common import StudioError, read_json, write_json
from studio.decisions import delegate
from studio.project import init_project, shot_path

layout_src = (ROOT / 'tests/studio/layout_smoke.py').read_text()
ns = {}
exec(layout_src[layout_src.index('Y0, LEVEL_H'):layout_src.index('PROBE = ')], ns)

checks = []
with tempfile.TemporaryDirectory(prefix='concept-smoke-') as root:
    p = Path(init_project('concept_test', {'request': 'concept smoke', 'shots': [{'shot_id': 's', 'frame_count': 60}]}, root)['project_path'])
    project = read_json(p / 'project.json'); project['policy'] = {'gates': {'FRAME_SUBJECT_SMALL': 'warn'}}; write_json(p / 'project.json', project)
    shot = read_json(shot_path(p, 's'))
    shot.update({'scene': ns['SCENE'], 'camera': ns['CAMERA'], 'actions': [ns['REVEAL']], 'fill_brief': ns['BRIEF']})
    write_json(shot_path(p, 's'), shot)
    delegate(p, '이번 테스트 결정은 에이전트에게 맡긴다', [])

    try:
        storyboard.propose(p, 's', [0, 1])
        raise AssertionError('staged without a concept')
    except StudioError as error:
        assert error.code == 'CONCEPT_UNPICKED', error.code
    checks.append('no_storyboard_before_a_concept')

    for n, colour in enumerate(((40, 60, 120), (200, 120, 40))):
        made = Path(root) / f'gen_{n}.png'
        Image.new('RGB', (540, 960), colour).save(made)
        added = concept.add(p, 's', made, prompt=f'night section, take {n}', source='codex_image_gen')
        assert added['licence'] == 'local_only' and added['ai_generated'] and (p / added['path']).is_file()
    assert [c['concept_id'] for c in concept.concepts(p, 's')] == ['c01', 'c02']
    sheet = concept.sheet(p)
    assert Path(sheet['sheet']).is_file() and sheet['shots']['s']['picked'] is None
    checks.append('concepts_added_with_provenance_and_sheet')

    try:
        concept.pick(p, 's', 'c02', user_words='ok')
        raise AssertionError('agent wrapper accepted as user words')
    except StudioError:
        pass
    picked = concept.pick(p, 's', 'c02', agent_note='warm interior reads the floor layers better')
    assert picked['decision']['by'] == 'agent' and 'user_words' not in picked['decision']
    checks.append('delegated_pick_is_the_agents_not_an_approval')

    first = storyboard.propose(p, 's', [0, 1], hero=0.5)
    env = storyboard.envelope(p, 's')
    assert env['body']['hero_frame'] == 30 and 30 in first['frames'] and env['sheet']['concept']['concept_id'] == 'c02', env
    with Image.open(first['image']) as image:
        assert image.height > 600, image.size            # the target row was appended under the frames
    checks.append('target_row_concept_beside_hero')

    boarded = storyboard.board(p)
    assert Path(boarded['image']).is_file() and '- s: proposed r01' in Path(boarded['sheet']).read_text(), Path(boarded['sheet']).read_text()
    checks.append('board_one_page')

    approved = storyboard.approve(p, 's', '이 구도로 좋아요', first['rev'])
    contract = storyboard.envelope(p, 's')['approval']['contract']
    assert contract['hero_frame'] == 30 and contract['concept']['concept_id'] == 'c02', contract
    checks.append('contract_keeps_hero_and_concept')

    (p / picked['path']).write_bytes(b'not the picked image')
    try:
        concept.require(p, 's')
        raise AssertionError('changed concept accepted')
    except StudioError as error:
        assert error.code == 'CONCEPT_STALE', error.code
    checks.append('changed_concept_is_stale')

print('CONCEPT_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
