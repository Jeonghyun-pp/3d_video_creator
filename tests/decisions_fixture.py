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

# the reel's journey (studio/journey.py): a test reel stays at one place, and each cut says so - a test's own choice
JOURNEY = [{'place': 'here', 'scale': 'object', 'why': 'a test reel tells its story at one place'}]
STAY = {'from': 0, 'to': 0, 'role': 'show', 'why': 'a test cut shows what is at its place'}

# the reel's point (studio/show_how.py): what a test reel proves - its proof names a moment a test may not show
# (POINT_UNSHOWN is a flow finding, a warning a story-told reel answers)
POINT = {'says': 'a test reel proves its one point', 'proof': {'what': 'a test scene shows it', 'rows': ['proof/show@end']},
         'opening': {'does': 'a test reel opens on its subject', 'why': 'a test reel has no question to ask'}}
REEL = {'journey': JOURNEY, 'point': POINT}   # what every shot list says above its cuts


def moved(shot, **direction):
    """A shot list row with its screen and motion said and why it is still (and any other direction values given)."""
    frame = shot.get('frame') or {'focus': (shot.get('elements') or [shot['shot_id']])[:1], 'size': 'medium'}   # what it holds
    return {**shot, 'frame': frame, 'direction': {'still': STILL, 'journey': STAY, **(shot.get('direction') or {}), **direction, 'move': MOVE}}


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


def passed_quality(project):
    """What the render gates read of a reel that passed them (studio/quality_gate.py): every shot's current version
    measured, a take picked, and one reel-judge round that watched every cut as it is with nothing found."""
    from studio.common import now, read_json, write_json
    from studio.project import load_project, shot_path
    from studio.reel import CLAY
    path = project_dir(project)
    versions = {}
    for entry in load_project(path)['shots']:
        sid = entry['shot_id']
        version = read_json(shot_path(path, sid)).get('scene_version')
        versions[sid] = version
        folder = shot_path(path, sid).parent / 'versions' / str(version)
        folder.mkdir(parents=True, exist_ok=True)
        write_json(folder / 'camera_dynamics.json', {'version': version, 'comfort': None, 'follow': None, 'nothing': [], 'small': [], 'small_key': [], 'masked': [], 'key_moving': [], 'dead_tail': []})
        write_json(path / 'decisions' / 'storyboard' / f'{sid}.json', {'schema_version': 1, 'shot_id': sid, 'status': 'proposed',
                                                                       'history': [{'at': now(), 'user_words': TEST_WORDS, 'ops': [{'op': 'pick'}]}]})
    verdict = {'reads': True, 'change_seen': True, 'names_change': True, 'dull': False, 'repeats_before': False, 'harsh': False, 'note': ''}
    write_json(path / CLAY / 'judge.json', {'schema_version': 1, 'rounds': [{'round': 1, 'key': 'fixture', 'at': now(), 'cuts': list(versions),
                                                                             'versions': versions, 'skip': [], 'redo': [],
                                                                             'shots': [{'shot_id': s, **verdict} for s in versions]}]})
    from studio import excuses   # and each reason written in place of a fix, read and taken
    kept = {excuses._key(r, excuses._sha(path, r)): {'accepted': True, 'why': 'fixture', 'reason': r, 'at': now()}
            for sid in [*versions, None] for r in excuses.reasons(path, sid)}
    write_json(path / excuses.READINGS, {'schema_version': 1, 'readings': kept})
