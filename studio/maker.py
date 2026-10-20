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
KINDS = ('mechanism', 'world')                 # what a cut shows (shot list kind; cut_kind)
SHARED_FUNCTIONS = ('subject', 'set', 'agent')  # world element functions whose look must stay one across generated cuts


KEY_SIZES = ('close', 'detail')
WORKING_SIZES = ('medium', 'close', 'detail')   # a working part this large fills the frame: how it works is what is seen               # the key change shown this close is a motion the eye checks part by part


def _said(cut):
    """What the cut's lines claim: the things' ids and their parts."""
    return {t['id'] for t in cut['things']} | {p for t in cut['things'] for p in t['parts']}


def _working(cut, ident):
    """Is the id a working part of what the reel explains: the subject, a part or feature of it, or a subject element of
    the world (a mechanism planned before its spec is a world element of function subject - 2026-10-20, ten topics)."""
    return cut['kinds'].get(ident) in ('subject', 'part', 'feature') or cut['functions'].get(ident) == 'subject'


def _on_screen(cut):
    return set(cut['focus']) | {p for r in cut['show'] for p in r.get('parts') or []}


def cut_kind(cut):
    """mechanism - the cut shows how the thing works: a working part in focus, the key change, or an opening; world -
    the rest: the place, people, a result, a scale, a comparison. Read from the screen, never from the lines (the study
    set, 2026-10-20: two labellers agreed on 0.94 of 295 shots by what the frame shows)."""
    from .blender_ops.show_core import opens
    if any(r.get('key') or opens(r.get('how', 'exterior')) for r in cut['show']):
        return 'mechanism'
    held = [f for f in cut['focus'] if _working(cut, f)]
    # what fills the frame decides (the labellers' rule): a wide frame is the place around it; closer, the working
    # parts must be at least half of what it holds - a rider beside a cabin is the world
    return 'mechanism' if cut['size'] in WORKING_SIZES and held and 2 * len(held) >= len(cut['focus']) else 'world'


def _key_close(cut):
    """The key change shown close on what the lines claim: the motion is the explanation, checked part by part."""
    if cut['size'] not in KEY_SIZES:
        return None
    said = _said(cut)
    rows = [r for r in cut['show'] if r.get('key') and set(r.get('parts') or cut['focus']) & said]
    return '; '.join(f"the key change of {', '.join(r.get('parts') or cut['focus'])}, {cut['size']}" for r in rows) if rows else None


def _claimed(cut):
    """A working part the lines claim, on screen: its shape and place are facts - a part or feature of a spec at any
    size, the subject (or a subject element) only held large enough that its structure reads. Seen whole in a wide
    frame, the subject is the world around the line (M3: generated), the line's claim carried by the cuts that show
    how it works."""
    shown = _on_screen(cut)
    large = cut['size'] in WORKING_SIZES
    held = [t['id'] for t in cut['things'] if (t['kind'] in ('part', 'feature') or (large and _working(cut, t['id'])))
            and (t['id'] in shown or set(t['parts']) & shown)]
    return f"its lines claim {', '.join(held)}, on screen" if held else None


def _mechanism(cut):
    if cut_kind(cut) != 'mechanism':
        return None
    from .blender_ops.show_core import opens
    rows = [r['how'] for r in cut['show'] if opens(r.get('how', 'exterior'))]
    held = [f for f in cut['focus'] if _working(cut, f)]
    return ('it opens the subject (' + ', '.join(rows) + ')' if rows else
            f"{', '.join(held) or 'the key change'} in focus") + ' - how it works'


def _world_fact(cut):
    """A cut whose lines put a number on what it holds on screen (how many, how big): the count is a fact."""
    held = [i for i in cut['numbered'] if i in _on_screen(cut)]
    return f"its lines give a number for {', '.join(held)}, on screen" if held else None


# (rule id, maker, test (cut -> why or None), why the row says so). The principle (the user, 2026-10-20, "엄청 상세한
# 부품정도만 blender 로 하고, 생성모델을 많이 쓰는게"; rules M1-M3): only the moment a viewer checks as fact - the key change
# close on a claimed part - is Blender alone; how the thing works is Blender's structure under the model's picture
# (hybrid); the world around it the model paints. Read from the shot list's screen (frame, show, direction, kind) and the
# lines' claims, never from a world's per-video declaration. Ten topics planned blind (2026-10-20) came out 86 % Blender
# when every opening and every said motion was Blender's.
MAKER_RULES = (
    ('key_close', 'blender', _key_close,
     'the key change close on a claimed part: a model moves a part plausibly wrong, and here the motion is the explanation'),
    ('claimed', 'hybrid', _claimed,
     'a line claims a working part on screen: Blender keeps its shape and place, the model the look'),
    ('world_fact', 'hybrid', _world_fact,
     'a line gives a number for what is on screen: Blender keeps the count, the model the look'),
    ('mechanism', 'hybrid', _mechanism,
     'the cut shows how the thing works: Blender keeps the structure, the model the look'),
    ('default', 'generative', lambda cut: 'the world around the mechanism - a place, people, a result, a scale - and nothing on '
                                          'screen a line claims',
     'the user, 2026-10-20: generative first'),
)
FACT_RULES = ('key_close', 'claimed', 'world_fact')   # never generative: what the narration claims and the key change


def _numbered(facts, script, line_ids):
    """About ids of the claims the lines say whose text gives a number."""
    from .decisions import _digits
    claims = {c['claim_id']: c for c in (facts or {}).get('claims', [])}
    lines = {l['line_id']: l for l in (script or {}).get('lines', [])}
    return sorted({a for i in line_ids for c in lines.get(i, {}).get('claim_ids') or []
                   if _digits(claims.get(c, {}).get('text', '')) for a in claims.get(c, {}).get('about') or []})


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
            'functions': {e['id']: e.get('function') for e in (world or {}).get('elements', [])},
            'numbered': _numbered(facts, script, entry.get('line_ids') or [])}


def kind_of(path, entry, facts, script, world):
    """The cut's kind as its screen shows it (cut_kind)."""
    return cut_kind(_cut(path, entry, facts, script, world))


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
    generative over a row of FACT_RULES (the key change close on a claimed part, a claimed working part on screen, a
    number said of what is on screen); CUT_KIND_DIFFERS - a written frame.kind its screen does not show; MAKER_DIFFERS (said) - a cut whose
    written maker is not the table's, with the why it gives."""
    errors, warnings = [], []
    known = {e['id'] for e in (world or {}).get('elements', [])}
    for entry in body.get('shots') or []:
        frame = entry.get('frame') or {}
        unknown = [f for f in frame.get('focus') or [] if f not in known and not _built_thing(path, f)]
        if not frame or unknown:   # the maker reads the cut's screen: without it, it reads the lines alone
            warnings.append(f"FRAME_UNDESCRIBED: {entry['shot_id']} " + (f"holds {unknown} in focus, which no element, subject or part is"
                            if unknown else 'says nothing of its screen (frame: {focus, size})') + ' - who makes the cut is read from it')
        written = frame.get('kind')
        if written:
            seen = cut_kind(_cut(path, entry, facts, script, world))
            if written != seen:
                warnings.append(f"CUT_KIND_DIFFERS: {entry['shot_id']} is written {written} but its screen shows {seen} "
                                f"({'a working part in focus, the key change or an opening' if seen == 'mechanism' else 'no working part in focus, no key change, no opening'}) - "
                                'say what the frame holds (frame.focus, show) or the kind it is')
        given = entry.get('maker')
        if not given:
            continue
        table = propose(path, entry, facts, script, world)
        if given['mode'] == 'generative' and table['rule'] in FACT_RULES:
            errors.append(f"MAKER_FACT_GENERATED: {entry['shot_id']} is written generative, but {table['why']} - make it "
                          f"{table['mode']}{' (or hybrid)' if table['mode'] == 'blender' else ''}, or move the claim to a cut a model may make")
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
