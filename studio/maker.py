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
CLOSE_SIZES = ('medium', 'close', 'detail')     # a focus this large shows its structure: a viewer reads it as the thing's shape
MOVING_FUNCTIONS = ('subject', 'set')         # world element functions whose action a line explains (not context, agent, phenomenon)
SHARED_FUNCTIONS = ('subject', 'set', 'agent')  # world element functions whose look must stay one across generated cuts


def _part_said(cut):
    said = [t['id'] for t in cut['things'] if t['kind'] in ('part', 'feature')]
    return f"its lines claim {', '.join(said)}" if said else None


def _explained_motion(cut):
    """The key change, a moment that opens the subject, or an action on what the lines speak of, held in focus: the
    motion is the explanation (water down a culvert, a chain onto the next tooth), and a model moves it plausibly wrong."""
    from .blender_ops.show_core import opens
    rows = [r for r in cut['show'] if r.get('key') or opens(r.get('how', 'exterior'))]
    if rows:
        return '; '.join(f"{'the key change' if r.get('key') else r['how']} of {', '.join(r['parts']) or 'the subject'}" for r in rows)
    return None


def _said_motion(cut):
    """An action on a thing the lines speak of, held in focus - the subject, a part, a set piece (not a place, a crowd
    or weather): the motion shows what the line says (water down a culvert, a chain onto the next tooth)."""
    said = {t['id'] for t in cut['things']} | {p for t in cut['things'] for p in t['parts']}
    moving = [f for f in cut['focus'] if f in said and (cut['kinds'].get(f) in ('subject', 'part', 'feature')
                                                       or cut['functions'].get(f) in MOVING_FUNCTIONS)]
    if cut['action'] and moving:
        return f"it moves {', '.join(moving)}, which its lines speak of ({cut['action'][:80]})"
    return None


def _structure_in_focus(cut):
    """The subject itself, or a subject element, held in focus at a size its structure reads: what a viewer takes as the
    thing's shape - a lock drawn with five chambers is wrong though no line counts them."""
    if cut['size'] not in CLOSE_SIZES:
        return None
    held = [f for f in cut['focus'] if cut['kinds'].get(f) in ('subject', 'part', 'feature') or cut['functions'].get(f) == 'subject']
    return f"{', '.join(held)} in focus, {cut['size']}" if held else None


# (rule id, maker, test (cut -> why or None), why the row says so). The principle: what a viewer takes as fact - what a
# line claims, a motion that explains, the structure of the thing in focus - Blender holds; places, moods, scale,
# people and results the model paints. Read from the shot list's screen (frame, show, direction) and the lines' claims,
# never from a world's per-video declaration (2026-10-20: the canal's "exact" lock made 91 % of its cuts non-generative).
MAKER_RULES = (
    ('part_said', 'blender', _part_said,
     'a line claims a part or feature: its state and place are facts, a model draws a plausible one'),
    ('explained_motion', 'blender', _explained_motion,
     'the key change, or a moment that opens the subject: a model keeps neither a cut nor a part moving right'),
    ('said_motion', 'blender', _said_motion,
     'an action on what the lines speak of, held in focus: the motion is the explanation, a model moves it plausibly wrong'),
    ('structure_in_focus', 'hybrid', _structure_in_focus,
     'the subject held in focus close enough that its structure reads: Blender keeps the shape, the model the look'),
    ('default', 'generative', lambda cut: 'a place, a scale, a mood, people or a result - nothing a line claims, no explaining motion, '
                                          'no subject structure in focus',
     'the user, 2026-10-20: generative first'),
)
FACT_RULES = ('part_said', 'explained_motion')   # never generative: what the narration claims, the key change and an opening
# (said_motion is Blender too, but a cut written generative over it is said, not refused: its action may be one a model draws well)


def _cut(path, entry, facts, script, world):
    from pathlib import Path
    from .blender_ops.show_core import show_of
    from .explains import line_things
    frame = entry.get('frame') or {}
    focus = list(frame.get('focus') or [])
    from .explains import _specs, resolve
    specs = _specs(path)
    kinds = {}
    for f in focus:   # what each focus id is: a subject spec, one of its parts or features (explains.resolve)
        thing = resolve(path, f, None, specs)
        if thing is not None:
            kinds[f] = thing['kind']
    return {'things': line_things(path, entry.get('line_ids') or [], facts, script, world) if facts and script else [],
            'show': show_of(entry), 'focus': focus, 'size': frame.get('size'),
            'action': str((entry.get('direction') or {}).get('action') or ''), 'kinds': kinds,
            'functions': {e['id']: e.get('function') for e in (world or {}).get('elements', [])}}


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
    known = {e['id'] for e in (world or {}).get('elements', [])}
    for entry in body.get('shots') or []:
        frame = entry.get('frame') or {}
        unknown = [f for f in frame.get('focus') or [] if f not in known and not _built_thing(path, f)]
        if not frame or unknown:   # the maker reads the cut's screen: without it, it reads the lines alone
            warnings.append(f"FRAME_UNDESCRIBED: {entry['shot_id']} " + (f"holds {unknown} in focus, which no element, subject or part is"
                            if unknown else 'says nothing of its screen (frame: {focus, size})') + ' - who makes the cut is read from it')
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


def _built_thing(path, ident):
    """A subject spec, or a part or feature of one, by id."""
    from .explains import resolve
    return resolve(path, ident) is not None


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
    modes = {r['shot_id']: r.get('kept') or r['mode'] for r in rows}
    for r in rows:
        anchor = look_anchor(path, body, r['shot_id'], modes, world)
        if anchor:
            r['shared_reference'] = {'from': anchor[0], 'ids': anchor[1]}
    total = sum(r['seconds'] for r in rows) or 1.0
    share = {m: round(sum(r['seconds'] for r in rows if r['mode'] == m) / total, 2) for m in MODES}
    return rows, share


def shareable(path, ident, world):
    """Does a thing keep one look across generated cuts: a world element that is a subject, set piece or person, or a
    subject spec, part or feature? (Not a place, weather or an unknown id.)"""
    element = next((e for e in (world or {}).get('elements', []) if e['id'] == ident), None)
    if element is not None:
        return element.get('function') in SHARED_FUNCTIONS
    from .explains import resolve
    return resolve(path, ident) is not None


def look_anchor(path, body, shot_id, modes, world):
    """(anchor shot id, [ids]) - the first earlier generated cut holding in focus a shareable thing this generated cut
    holds in focus too - else None. That cut's start picture sets the thing's look: this cut's start picture is drawn
    with it attached (concept.brief), and so its video starts from the same look. Why (the user, 2026-10-20, "응"): a
    thing that recurs stays generated, its look held by a shared picture; Veo's image to video reads only the first
    frame, so the look is shared where the picture is drawn, not sent to the video model."""
    order = [e['shot_id'] for e in body.get('shots') or []]
    entries = {e['shot_id']: e for e in body.get('shots') or []}
    if shot_id not in entries or modes.get(shot_id) != 'generative':
        return None
    mine = [f for f in (entries[shot_id].get('frame') or {}).get('focus') or [] if shareable(path, f, world)]
    for other in order[:order.index(shot_id)]:
        if modes.get(other) != 'generative':
            continue
        same = [f for f in mine if f in ((entries[other].get('frame') or {}).get('focus') or [])]
        if same:
            return other, same
    return None
