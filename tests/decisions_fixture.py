"""Tests that build, render or deliver stand on settled decision layers, as every project must (gates are closed
until their layers exist: studio/decisions.require, 2026-10-21). settle() writes each layer as decided off the ladder
by the test itself - a record of a test, never a user's words - so the gates read real, fresh records instead of
being switched off."""
from studio import decisions
from studio.common import now, write_json
from studio.project import project_dir


BODIES = {'world': {'fixture': 'world', 'kind': 'none', 'beats': [], 'elements': []}}   # what readers of a settled body look up


def settle(project, layers=None, bodies=None):
    """Every layer (or those named, with the layers they stand on) decided off the ladder against its parents; `bodies`
    gives a layer the body a test reads back (the facts' claims, say)."""
    path = project_dir(project['project_path'] if isinstance(project, dict) else project)   # a path, or init_project's result
    wanted, todo = set(), list(layers or decisions.LAYERS)
    while todo:
        layer = todo.pop()
        if layer not in wanted:
            wanted.add(layer)
            todo += list(decisions.LAYERS[layer])
    root = path / 'decisions'
    root.mkdir(parents=True, exist_ok=True)
    for layer in decisions.LAYERS:   # in conversation order: each parent is written before its children
        if layer not in wanted:
            continue
        body = (bodies or {}).get(layer) or BODIES.get(layer, {'fixture': layer})
        parents = {p: decisions.body_sha256(decisions.envelope(path, p)['body']) for p in decisions.LAYERS[layer]}
        write_json(root / f'{layer}.json', {
            'schema_version': 1, 'layer': layer, 'off_ladder': True, 'body': body, 'approval': None, 'history': [],
            'status': 'decided', 'decided': {'by': 'test', 'body_sha256': decisions.body_sha256(body), 'parents': parents, 'at': now()}})
    return path


# a shot list row's motion (direction.move, decisions.move_lines): every shot says how it moves - a test's own choice
MOVE = {'recipe': 'pass_by', 'reveals': 'the subject as the camera passes', 'key_at': 0.5, 'why': 'a test shot passes its subject',
        'settings': {'height_share': 0.55, 'azimuth_deg': 90}}


STILL = 'a test shot holds; nothing in it changes'   # direction.still: the shot says why nothing changes (change_moves)


def moved(shot, **direction):
    """A shot list row with its motion said and why it is still (and any other direction values given)."""
    return {**shot, 'direction': {'still': STILL, **(shot.get('direction') or {}), **direction, 'move': MOVE}}


TEST_WORDS = '테스트: 이 완화로 진행해요'   # a test's words for a looser check, never a user's


def allowed(policy):
    """A policy that loosens a check, with the words that allow it (gates.require_weakening) - for a test about the looser
    behaviour itself."""
    from studio.gates import _weaker, weakening
    weaker = _weaker(policy)
    return {**policy, 'weakened': weakening(TEST_WORDS, weaker, 'test policy')} if weaker else policy


def allow_project_policy(project):
    """The project's policy as allowed(policy), written back."""
    from studio.common import read_json
    path = project_dir(project)
    data = read_json(path / 'project.json')
    data['policy'] = allowed({k: v for k, v in (data.get('policy') or {}).items() if k != 'weakened'})
    write_json(path / 'project.json', data)
    return path


def pick_concept(project, shot_id='s'):
    """A concept frame picked for the shot (every storyboard stands on one, studio/concept.require): a plain test
    picture, picked with a test's words."""
    from PIL import Image
    from studio import concept
    path = project_dir(project['project_path'] if isinstance(project, dict) else project)
    image = path / 'concepts' / f'_test_{shot_id}.png'
    image.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (108, 192), (120, 140, 160)).save(image)
    added = concept.add(path, shot_id, image, prompt='a test concept frame')
    return concept.pick(path, shot_id, added['concept_id'], user_words=TEST_WORDS)
