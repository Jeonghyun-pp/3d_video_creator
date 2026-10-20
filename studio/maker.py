"""Who makes each cut: a generative video model, Blender, or both (hybrid) - decided at the shot list, per cut.

Why (2026-10-20): the user, after the canal clay came out far below the pictures: "사람들이 보기 좋은 영상을 만든다는
관점에서는 엄청 상세한 부품정도만 blender 로 하고, 생성모델을 많이 쓰는게 오히려 좋을수도" - and chose generative
first. Building a lake, hills and clouds in Blender and solving a camera in them took most of the run, and a model
paints all of that better. What stays Blender is what the narration claims as fact about parts: a model draws a
plausible part in a plausible state, and the state or the place is what the line says (10-14: "the narration is true";
10-16 video test: a foot and the ground came out plausibly wrong).

One table, MAKER_RULES: the first row a cut meets decides; a new kind of cut is one more row. Every row reads only what
the shot list and the layers before it already say (the lines' claims and what they are about, the cut's show rows,
its world elements). The shot list may say otherwise (`maker: {mode, why}`), except into generative where a row of
FACT_RULES holds (MAKER_FACT_GENERATED).
"""
from __future__ import annotations

MODES = ('generative', 'hybrid', 'blender')
EXACT_REQUIREMENTS = ('exact', 'labelled')   # world element requirements whose shape a cut must keep when it is about the element
# (world.OWNER_RULES). motion_bound is not one: a bound motion is the cut's key change (key_or_opened); the canal world
# marked its lake, ship and water motion_bound, and every cut came out hybrid (2026-10-20)


def _part_said(cut):
    said = [t['id'] for t in cut['things'] if t['kind'] in ('part', 'feature')]
    return f"its lines claim {', '.join(said)}" if said else None


def _key_or_opened(cut):
    from .blender_ops.show_core import opens
    rows = [r for r in cut['show'] if r.get('key') or opens(r.get('how', 'exterior'))]
    if not rows:
        return None
    return '; '.join(f"{'the key change' if r.get('key') else r['how']} of {', '.join(r['parts']) or 'the subject'}" for r in rows)


def _exact_element(cut):
    """An element whose shape is required exact or labelled, that the cut is about: its lines speak of it, or a show
    row names it (a thing in the back of a wide view is the model's to paint)."""
    about = {t['id'] for t in cut['things']} | {p.split('/')[0] for r in cut['show'] for p in r.get('parts') or []}
    held = [f"{e['id']} ({', '.join(r for r in e.get('requirements') or [] if r in EXACT_REQUIREMENTS)})"
            for e in cut['elements'] if e['id'] in about and set(e.get('requirements') or []) & set(EXACT_REQUIREMENTS)]
    return f"it is about elements that must keep their shape: {', '.join(held)}" if held else None


def _subject_said(cut):
    said = [t['id'] for t in cut['things'] if t['kind'] == 'subject'
            or (t['kind'] == 'element' and any(e['id'] == t['id'] and e.get('function') == 'subject' for e in cut['elements']))]
    return f"its lines speak of {', '.join(said)} as a whole" if said else None


# (rule id, maker, test (cut -> why or None), why the row says so)
MAKER_RULES = (
    ('part_said', 'blender', _part_said,
     'a line claims a part or feature: its state and place are facts, a model draws a plausible one'),
    ('key_or_opened', 'blender', _key_or_opened,
     'the key change, or a moment that opens the subject (section, x-ray, explode): a model keeps neither a cut nor a part moving right'),
    ('exact_element', 'hybrid', _exact_element,
     'an element whose shape is required exact or labelled, and the cut is about it: Blender keeps the shape, the model the look'),
    ('subject_said', 'hybrid', _subject_said,
     'the lines speak of the subject as a whole: what it is stays Blender, how it looks is the model\'s'),
    ('default', 'generative', lambda cut: 'a place, a scale, a mood, a hook, people or a result - nothing a line claims of a part',
     'the user, 2026-10-20: generative first'),
)
FACT_RULES = ('part_said', 'key_or_opened')   # never generative: what the narration claims must be seen as it is


def _cut(path, entry, facts, script, world):
    from .blender_ops.show_core import show_of
    from .explains import line_things
    elements = {e['id']: e for e in (world or {}).get('elements', [])}
    return {'things': line_things(path, entry.get('line_ids') or [], facts, script, world) if facts and script else [],
            'show': show_of(entry), 'elements': [elements[i] for i in entry.get('elements') or [] if i in elements]}


def propose(path, entry, facts, script, world):
    """{mode, rule, why} the table gives a shot list entry."""
    cut = _cut(path, entry, facts, script, world)
    for rule, mode, test, reason in MAKER_RULES:
        said = test(cut)
        if said:
            return {'mode': mode, 'rule': rule, 'why': f'{said} - {reason}'}
    raise AssertionError('MAKER_RULES ends in a default row')


def applies(entry, shot):
    """Does the shot list's maker decide this shot's route? When the list writes one (`maker`), or the shot has nothing a
    build made (no version) and its route was never the user's or another layer's choice beyond a plan. A built shot an
    earlier plan routed keeps its route: the table never takes a build away unasked (review 2026-10-20: re-approving the
    rocket's list would have turned its built hybrid shots generative); the sheet says what it would propose."""
    if entry.get('maker'):
        return True
    route = (shot or {}).get('route') or {}
    return not (shot or {}).get('scene_version') and route.get('rule_id') in (None, 'S_shotlist_maker', 'W_world_owners')


def maker_of(path, entry, facts, script, world):
    """The cut's maker: the shot list's own (maker), else the table's. -> {mode, why, rule?, given: bool}"""
    given = entry.get('maker')
    if given:
        return {**given, 'given': True}
    return {**propose(path, entry, facts, script, world), 'given': False}


def lines(path, body, facts, script, world):
    """(errors, warnings) of the shot list's makers: MAKER_FACT_GENERATED (error, never softened) - a cut written
    generative whose lines claim a part or that shows the key change or an opening; MAKER_DIFFERS (said) - a cut whose
    written maker is not the table's, with the why it gives."""
    errors, warnings = [], []
    for entry in body.get('shots') or []:
        given = entry.get('maker')
        if not given:
            continue
        table = propose(path, entry, facts, script, world)
        if given['mode'] == 'generative' and table['rule'] in FACT_RULES:
            errors.append(f"MAKER_FACT_GENERATED: {entry['shot_id']} is written generative, but {table['why']} - make it "
                          f"{table['mode']} (or hybrid), or move the claim to a cut a model may make")
        elif given['mode'] != table['mode']:
            warnings.append(f"MAKER_DIFFERS: {entry['shot_id']} is {given['mode']} where the table says {table['mode']} "
                            f"({table['rule']}); why: {given.get('why')}")
    return errors, warnings


def table(path, body, facts, script, world):
    """Sheet rows: [{shot_id, mode, why, given}] and the share each maker takes of the reel's seconds."""
    from .project import load_shot, shot_path
    rows = []
    for e in body.get('shots') or []:
        row = {'shot_id': e['shot_id'], 'seconds': float(e.get('duration_s') or 0), **maker_of(path, e, facts, script, world)}
        shot = load_shot(path, e['shot_id']) if shot_path(path, e['shot_id']).is_file() else None
        if shot is not None and not applies(e, shot) and (shot.get('route') or {}).get('mode'):
            row['kept'] = shot['route']['mode']   # a built shot keeps its route; the table's proposal is only said
        rows.append(row)
    total = sum(r['seconds'] for r in rows) or 1.0
    share = {m: round(sum(r['seconds'] for r in rows if r['mode'] == m) / total, 2) for m in MODES}
    return rows, share
