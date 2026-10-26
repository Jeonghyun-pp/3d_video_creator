"""The cuts layer: each cut of a v2 reel planned as a cutaway before any picture is drawn (decisions/cuts.json, bound to
the script; v1's decision ladder settles it like any layer - `decide write --layer cuts`, delegated runs with an agent note).

Per cut: what its viewer must learn and how it is proved (show_how, v1's decision chain), the place and scale it shows,
the cutaway (what it opens and how, what flows through it in which colour, which parts are named, its scale step from
the cut before, where the camera looks from and whether it holds, pushes or pulls), what changes on screen (a state, a
thing moving, nothing) and when, its lines and its length, and its first, middle and last pictures in words. Every value
of a kind is a row of the cutaway grammar (library/patterns/cutaway_grammar.json) or of v1's showing ways - a new kind
is a row there.

The lint holds what follows from what is written, deterministically (no model): each label names a part the cut shows;
scale steps chain from the scales; a state change has a last picture that differs from its first; a moving thing has its
path; proof rows name rows the cut has; the cut is as long as its lines take to say at v1's speech pace (and its runs
across cuts are v1's); a value no row has is refused. What the plan reader found on the cuts as they stand (v1's plan
read, `studio2 cuts read`) comes back as flow findings, answered like v1's: fixed, or departed with why.

Settled, the layer becomes the project's shots (one per cut, route `composite`: a clip made locally from pictures) and
its timeline, so v1's voice, captions, edit and readers work on them.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from studio.common import read_json, stable_hash

SCHEMA = read_json(Path(__file__).parent / 'schemas' / 'cuts.schema.json')
ROUTE = {'mode': 'composite', 'status': 'proposed', 'decided_by': 'agent', 'rule_id': 'v2_cuts', 'features': [],
         'reason': 'a v2 cut: drawn by the image tool, moved and marked locally (studio2/motion.py)'}
FRAMES = ('first', 'middle', 'last')
# A thing generated on its own and laid over the picture (drawn: sprite) takes neither the picture's light, its
# perspective nor what stands in front of it; at a speck's size that does not show, at a box's it does (2026-10-26, the
# dev preview: c1's wafer box at 0.12 of the frame tall sat pasted on, c8's at 0.26 floated). A thing that belongs to
# the scene is painted in the pictures (drawn: picture); a sprite is only for something this small (a share of the
# frame height, about three specks' glow).
SPRITE_MAX = 0.03
# what a proof row may name in its cut (show_how.proof, the reel's point.proof '<cut>/<row>'): a row kind -> whether the
# cut has it. A new kind of row is a line here.
PROOF_ROWS = {
    'opens': lambda cut, rest: not rest and cut['cutaway']['opens'] != 'none',
    'change': lambda cut, rest: not rest and cut['change']['kind'] != 'none',
    'flow': lambda cut, rest: rest.isdigit() and int(rest) < len(cut['cutaway']['flow']),
    'label': lambda cut, rest: rest in {l.get('part', f"flow{l.get('flow')}") for l in cut['cutaway']['labels']},
    'frames': lambda cut, rest: rest in cut['frames'],
}


def cut_sha(cut):
    """What a cut's pictures and clip are made for: its entry, its departures (answers, not plan) aside."""
    return stable_hash({k: v for k, v in cut.items() if k != 'departs'})[:16]


def picture_sha(cut, body):
    """What a cut's pictures are drawn from (studio2/prompts.py reads exactly these): the look, the place, the scale and
    step, what is opened and how, the parts, the ways the flows take, what changes and what moves, the pictures in words.
    The camera, the labels, the colours and the timing are drawn over the pictures: changing them keeps the pictures."""
    place = next((p for p in body['places'] if p['id'] == cut['place']), None)
    cutaway, change = cut['cutaway'], cut['change']
    return stable_hash({'look': body['look'], 'place': place, 'scale': cut['scale'],
                        'cutaway': {k: cutaway[k] for k in ('opens', 'what', 'parts', 'scale_step', 'view')},
                        'flow': [{'what': f['what'], 'path': f['path']} for f in cutaway['flow']],
                        # what changes is drawn only by an edit (a state's middle and last pictures); a move is drawn over them
                        'change': {k: change.get(k) for k in (('kind', 'what') if in_pictures(change) else ('kind',))},
                        'element': {k: (change.get('element') or {}).get(k) for k in ('what', 'drawn')}, 'frames': cut['frames']})[:16]


def in_pictures(change):
    """Whether what changes is painted in the pictures (a state, or a thing of the scene that moves: drawn picture) - the
    middle and last pictures are edits of the first - rather than drawn over them."""
    return change['kind'] == 'state' or (change.get('element') or {}).get('drawn') == 'picture'


def proof_ok(cut, row):
    kind, _, rest = row.partition('/')
    test = PROOF_ROWS.get(kind)
    return bool(test and test(cut, rest))


def as_shots(body):
    """The cuts as v1 shot-list rows, for v1's checks of lines and lengths and its plan reader (shot_id = cut_id)."""
    return [{'shot_id': c['cut_id'], 'duration_s': c['duration_s'], 'line_ids': list(c['line_ids']),
             **({'line_at_s': list(c['line_at_s'])} if c.get('line_at_s') else {}), **({'role': c['role']} if c.get('role') else {}),
             'purpose': c['purpose'], 'direction': {'show_how': deepcopy(c['show_how'])},
             'pattern': {'departs': deepcopy(c.get('departs') or [])}, 'v2_cut': c}
            for c in body['cuts']]


def as_plan(body):
    """The cuts layer as the plan reader reads a shot list (studio.plan_read): its point and its cuts as shots."""
    point = deepcopy(body['point'])
    point.setdefault('opening', {'does': '(not written)', 'why': ''})
    return {'point': point, 'journey': [], 'shots': as_shots(body)}


# --- lint ---------------------------------------------------------------------------------------------------------

def _values(table, field):
    from .grammar import values
    return values(field, table)


def _value_lines(body, table):
    import json as _json
    from studio.common import REPO
    from studio.patterns import ROLES
    ways = {w['id'] for w in _json.loads((REPO / 'library' / 'showing_ways.json').read_text(encoding='utf-8'))['ways']}
    places = {p['id'] for p in body['places']}
    known = {f: _values(table, f) for f in ('opens', 'camera.move', 'change.kind', 'view', 'scale_step')}
    colours, scales = set(table['colours']['rows']), table['scales']['order']
    out = []
    for c in body['cuts']:
        cid, cut = c['cut_id'], c['cutaway']
        given = {'opens': cut['opens'], 'camera.move': cut['camera']['move'], 'change.kind': c['change']['kind'], 'view': cut['view'],
                 'scale_step': cut['scale_step']}
        out += [f"CUT_VALUE_UNKNOWN: {cid} {f} {v!r} is not a row of the cutaway grammar ({', '.join(known[f])}) - a new kind is a row there"
                for f, v in given.items() if v not in known[f]]
        named = [('flow colour', f['colour']) for f in cut['flow']] + [('change outline', c['change'].get('outline')),
                                                                      ('element colour', (c['change'].get('element') or {}).get('colour'))]
        named = [(what, v) for what, v in named if v is not None]
        out += [f"CUT_VALUE_UNKNOWN: {cid} {what} {v!r} is not a colour row ({', '.join(sorted(colours))})" for what, v in named if v not in colours]
        if c['scale'] not in scales:
            out.append(f"CUT_VALUE_UNKNOWN: {cid} scale {c['scale']!r} is not one of the grammar's scales ({', '.join(scales)})")
        if c['place'] not in places:
            out.append(f"CUT_PLACE_UNKNOWN: {cid} shows place {c['place']!r}, which the cuts layer does not describe ({', '.join(sorted(places))})")
        if c.get('role') and c['role'] not in ROLES:
            out.append(f"CUT_VALUE_UNKNOWN: {cid} role {c['role']!r} is not one of {', '.join(ROLES)}")
        way = c['show_how']['way']
        out += [f"WAY_UNKNOWN: {cid} way {w!r} is not a row of library/showing_ways.json - a new way is a row there"
                for w in (way['id'], way['rejected']['id']) if w not in ways]
    return out


def _part_lines(c):
    """Labels name parts the cut shows; flows run along parts it shows; a change changes parts it shows."""
    cid, cut = c['cut_id'], c['cutaway']
    parts = [p['id'] for p in cut['parts']]
    out = [f"duplicate part {p} in {cid}" for p in sorted(set(parts)) if parts.count(p) > 1]
    for l in cut['labels']:   # a label names a part the cut shows or one of its flows - one of the two
        if ('part' in l) == ('flow' in l):
            out.append(f"CUT_LABEL_UNSHOWN: {cid} label {l['word']!r} names {'both a part and a flow' if 'part' in l else 'nothing'} - name one")
        elif 'part' in l and l['part'] not in parts:
            out.append(f"CUT_LABEL_UNSHOWN: {cid} labels {l['part']!r} ({l['word']}), which is not a part the cut shows ({', '.join(parts) or 'none'})")
        elif 'flow' in l and not l['flow'] < len(cut['flow']):
            out.append(f"CUT_LABEL_UNSHOWN: {cid} labels flow {l['flow']} ({l['word']}), and the cut has {len(cut['flow'])} flows")
    out += [f"CUT_PART_UNSHOWN: {cid} flow {f['what']!r} runs along {p!r}, which is not a part the cut shows"
            for f in cut['flow'] for p in f.get('along') or [] if p not in parts]
    out += [f"CUT_PART_UNSHOWN: {cid} changes {p!r}, which is not a part the cut shows"
            for p in c['change'].get('parts') or [] if p not in parts]
    return out


def _change_lines(c):
    """What a change needs written: its words and window; a state its last picture, different from the first; a moving
    thing its path."""
    cid, change, frames = c['cut_id'], c['change'], c['frames']
    out = []
    if change['kind'] == 'none':
        if change.get('at') or change.get('element') or change.get('what') or change.get('key'):
            out.append(f"CHANGE_UNDECIDED: {cid} says nothing changes, but gives what/at/element/key - choose the change's kind")
        return out
    if not change.get('what') or not change.get('at'):
        out.append(f"CHANGE_UNDECIDED: {cid} {change['kind']} change: say what changes (what) and when (at [start, end] of the cut)")
    elif change['at'][0] > change['at'][1]:
        out.append(f"CHANGE_UNDECIDED: {cid} change at {change['at']}: it starts after it ends")
    element = change.get('element') or {}
    if element.get('drawn') == 'sprite' and not (element.get('size') or 1.0) <= SPRITE_MAX:
        out.append(f"CUT_ELEMENT_PASTED: {cid} lays {element['what']!r} over the picture as a sprite "
                   f"{'at ' + format(element['size'], '.2f') if element.get('size') else 'of no stated size'} of the frame height (a "
                   f"sprite is only for something at most {SPRITE_MAX} tall) - a thing of the scene is painted in the pictures (drawn: "
                   'picture: at the start of its path in the first, at its end in the last), a small thing is a speck')
    if change['kind'] == 'state' or element.get('drawn') == 'picture':
        last = frames.get('last')
        if last is None:
            out.append(f"CHANGE_FRAMES_SAME: {cid} changes ({change.get('what')}) in its pictures but has no last picture - the last is the first "
                       'with only that changed')
        elif ' '.join(last['shows'].split()).lower() == ' '.join(frames['first']['shows'].split()).lower():
            out.append(f"CHANGE_FRAMES_SAME: {cid}'s first and last pictures say the same - the last shows the first with {change.get('what')}")
    if change['kind'] == 'move' and not element:
        out.append(f"CHANGE_UNDECIDED: {cid} something moves ({change.get('what')}): say what moves and its path (element)")
    window = (change.get('element') or {}).get('at')
    if window and window[0] > window[1]:
        out.append(f"CHANGE_UNDECIDED: {cid} its moving thing's window {window} starts after it ends")
    return out


def _scale_lines(body, table):
    """The scale steps chain: the first cut is 'first'; every other cut's step is what its scale is against the cut
    before's (wider, closer, same)."""
    order = table['scales']['order']
    out = []
    for n, c in enumerate(body['cuts']):
        step = c['cutaway']['scale_step']
        if n == 0:
            if step != 'first':
                out.append(f"SCALE_STEP_BROKEN: {c['cut_id']} opens the reel: its scale_step is first, not {step}")
            continue
        before = body['cuts'][n - 1]
        if c['scale'] not in order or before['scale'] not in order:
            continue   # an unknown scale is said by the value check
        a, b = order.index(before['scale']), order.index(c['scale'])
        want = 'same' if a == b else 'closer' if b > a else 'wider'
        if step != want:
            out.append(f"SCALE_STEP_BROKEN: {c['cut_id']} says {step} but goes from {before['scale']} ({before['cut_id']}) to {c['scale']} - that is {want}")
    return out


def _proof_lines(body):
    ids = {c['cut_id']: c for c in body['cuts']}
    out = []
    for c in body['cuts']:
        out += [f"SHOW_HOW_UNDECIDED: {c['cut_id']} proof {r!r} names no row it has (opens, change, flow/<n>, label/<part>, frames/<name>)"
                for r in c['show_how']['proof'] if not proof_ok(c, r)]
    for row in body['point']['proof']['rows']:
        cid, _, rest = row.partition('/')
        if cid not in ids or not proof_ok(ids[cid], rest):
            out.append(f"POINT_UNDECIDED: the reel's proof row {row!r} names no row of a cut ('<cut>/<row>')")
    return out


def _label_lines(body, table):
    """The reel's label height against the reference's middle half (advice: the reference's reels differ, so it is shown
    as measured with its strength, never a floor)."""
    from .grammar import read
    try:
        got = read('label_text', table)
    except Exception:   # noqa: BLE001 - no measure of the reference: nothing to compare with
        return []
    low, high = (got.get('all.label_text_height.p25') or {}).get('measured'), (got.get('all.label_text_height.p75') or {}).get('measured')
    if low is None or high is None or low <= body['label_height'] <= high:
        return []
    strength = (got.get('all.label_text_height.p25') or {}).get('strength')
    return [f"LABEL_SIZE_OFF: labels {body['label_height']:.3f} of the frame height tall; the reference's middle half is {low:.3f}-{high:.3f} "
            f"({strength}: its reels differ)"]


def _caption_lines(path, table):
    """The captions' letter height (style.captions.letter_height) against the reference's subtitle letters' middle half
    (advice, as the labels': the reference's reels differ)."""
    from studio.common import read_json as _read
    from .grammar import read
    style_file = path / 'style.json'
    chosen = ((_read(style_file) if style_file.is_file() else {}).get('captions') or {}).get('letter_height')
    try:
        got = read('subtitle_text', table)
    except Exception:   # noqa: BLE001 - no measure of the reference: nothing to compare with
        return []
    low, high = (got.get('all.subtitle_text_height.p25') or {}).get('measured'), (got.get('all.subtitle_text_height.p75') or {}).get('measured')
    if low is None or high is None or (chosen and low <= chosen <= high):
        return []
    said = f'{chosen:.3f} of the frame height tall' if chosen else "at v1's old size (size_frac of the width, about 60 % of the reference's)"
    return [f"CAPTION_SIZE_OFF: caption letters {said}; the reference's subtitle letters' middle half is {low:.3f}-{high:.3f} - "
            "set style.captions.letter_height with its evidence (studio2 project init chooses the reference's median)"]


def _camera_lines(body, table):
    """A push or a pull says how much; how much against what the reference does (advice, only on strong evidence)."""
    from .grammar import read
    errors, warnings = [], []
    try:
        growth = read('camera.amount', table)
    except Exception:   # noqa: BLE001 - no reference library here: nothing to compare with
        growth = {}
    low, high = (growth.get('all.subject_growth.p10') or {}).get('value'), (growth.get('all.subject_growth.p90') or {}).get('value')
    for c in body['cuts']:
        cam = c['cutaway']['camera']
        if cam['move'] == 'still':
            if cam.get('amount'):
                errors.append(f"CAMERA_UNDECIDED: {c['cut_id']} holds still but gives an amount {cam['amount']}")
            continue
        if not cam.get('amount'):
            errors.append(f"CAMERA_UNDECIDED: {c['cut_id']} {cam['move']}s: say how much (amount, the share the picture grows or shrinks)")
            continue
        scale = 1 + cam['amount'] if cam['move'] == 'push' else 1 / (1 + cam['amount'])
        if low is not None and high is not None and not low <= scale <= high:
            warnings.append(f"CAMERA_AMOUNT_OFF: {c['cut_id']} {cam['move']} {cam['amount']} changes the size x{scale:.2f}; the reference's cuts "
                            f"change it x{low:.2f}-x{high:.2f} (p10-p90)")
    return errors, warnings


def _order_lines(body, script_lines):
    order = {i: n for n, i in enumerate(script_lines)}
    seen = [(c['cut_id'], order[i]) for c in body['cuts'] for i in c['line_ids'] if i in order]
    return [f"CUTS_OUT_OF_ORDER: {b} says a line before {a}'s" for (a, i), (b, j) in zip(seen, seen[1:]) if j < i]


def lint(path, body, parents):
    """(errors, warnings) of the cuts layer against its script and the grammar (module docstring)."""
    from studio import decisions
    from .grammar import load
    table = load()
    script = parents.get('script') or {}
    lines = {l['line_id']: l['text'] for l in script.get('lines') or []}
    errors, warnings = [], []
    ids = [c['cut_id'] for c in body['cuts']]
    errors += [f'duplicate cut {i}' for i in sorted(set(ids)) if ids.count(i) > 1]
    used = [i for c in body['cuts'] for i in c['line_ids']]
    errors += [f'script line {i} is in no cut' for i in lines if i not in used]
    errors += [f'unknown script line {i}' for i in sorted(set(used) - set(lines))] if lines else []
    errors += _order_lines(body, list(lines))
    shots = as_shots(body)
    errors += decisions.span_lines(shots)
    errors += decisions.length_lines({'shots': shots}, lines, decisions.reference_chars_per_s(path), decisions.project_shot_floor(path))
    errors += _value_lines(body, table)
    for c in body['cuts']:
        errors += _part_lines(c) + _change_lines(c)
    errors += _scale_lines(body, table) + _proof_lines(body)
    e, w = _camera_lines(body, table)
    errors += e; warnings += w + _label_lines(body, table) + _caption_lines(path, table)
    for c in body['cuts']:
        at = c.get('line_at_s')
        if at and (len(at) != len(c['line_ids']) or at != sorted(at) or at[-1] >= c['duration_s']):
            errors.append(f"{c['cut_id']}: line_at_s {at} must give each of its {len(c['line_ids'])} lines a start, in order, inside its {c['duration_s']} s")
    thread = ((decisions.envelope(path, 'story') or {}).get('body') or {}).get('thread') or {}
    carriers = set(thread.get('carrier_ids') or [])
    errors += [f"CUT_CARRIER_UNKNOWN: {c['cut_id']} carries {x!r}, which is not one of the story thread's carriers ({', '.join(sorted(carriers)) or 'none'})"
               for c in body['cuts'] for x in c.get('carries') or [] if x not in carriers]
    from studio.plan_read import lines as plan_lines   # what the plan reader found on the cuts as they stand (flow findings)
    warnings += plan_lines(path, as_plan(body))
    from .flow import plan_lines as flow_lines   # v1's reel-flow rules read from the cuts (the hook, the key change, the pace)
    warnings += flow_lines(path, body)
    return errors, warnings


# --- the plan reader's words --------------------------------------------------------------------------------------

def _cut_words(c):
    cut, change = c['cutaway'], c['change']
    rows = [f"opens: {cut['opens']} - {cut['what']}"]
    rows += [f"flow/{n}: {f['what']} drawn {f['colour']}, {f['path']}" for n, f in enumerate(cut['flow'])]
    rows += [f"label/{l['part']}: {l['word']} on {next((p['words'] for p in cut['parts'] if p['id'] == l['part']), l['part'])}" if 'part' in l
             else f"label/flow{l['flow']}: {l['word']} on {cut['flow'][l['flow']]['what'] if l['flow'] < len(cut['flow']) else 'flow ' + str(l['flow'])}"
             for l in cut['labels']]
    rows.append(f"change: {change['kind']}" + (f" - {change.get('what')} at {change.get('at')}" if change['kind'] != 'none' else ''))
    element = change.get('element')
    if element:   # what the renderer draws over the picture (studio2/motion.py): a thing in the scene from the start, a speck for its trip
        window = element.get('at') or change.get('at')
        rows.append(f"{element['what']}: in the picture from the first frame at the start of its path, moved {element['path']} during {window} of the cut"
                    if element['drawn'] == 'sprite' else
                    f"{element['what']}: in the pictures, at the start of its path in the first and at its end in the last, the picture "
                    f"crossing from one to the other during {window} of the cut (a video model moves it in the paid clip)"
                    if element['drawn'] == 'picture' else
                    f"{element['what']}: appears and moves {element['path']} during {window} of the cut")
    rows += [f"frames/{k}: {v['shows']}" for k, v in c['frames'].items()]
    return rows


def chain(body, story=None, lines=None):
    """Each cut's chain of decisions in words, in order, for the plan reader (studio.plan_read.read): the v2 rows."""
    from studio.story_core import beats_of
    beats = {b['story_beat_id']: b for b in (story or {}).get('beats') or []}
    line_by = {l['line_id']: l for l in lines or []}
    out = []
    for n, s in enumerate(body['shots']):
        c = s['v2_cut']
        how = c['show_how']
        rows = [f"cut {n} = {c['cut_id']} ({c['duration_s']} s)"]
        for b in beats_of(c['line_ids'], {i: l.get('story_beat_id') for i, l in line_by.items()}):
            beat = beats.get(b) or {}
            rows.append(f"  beat {b}: {beat.get('what_happens', '')} | its picture: {beat.get('picture_idea') or '(none)'}")
        rows.append(f"  said over it: {' '.join(line_by[i]['text'] for i in c['line_ids'] if i in line_by) or '(no words)'}")
        rows.append(f"  what it is for: {c['purpose']}")
        rows.append(f"  the viewer must learn: {how['point']}")
        rows.append('  on screen (what the pictures and the drawn marks make): ' + '; '.join(_cut_words(c)))
        rows.append(f"  it proves its point by: {', '.join(how['proof'])}")
        rows.append(f"  showing way: {how['way']['id']} - not {how['way']['rejected']['id']}, because {how['way']['rejected']['why']}")
        picture = how.get('picture')
        rows.append("  the beat's picture: " + ('kept' if picture == 'beat' else f"left - {picture['departs']}" if isinstance(picture, dict) else '(not said)'))
        rows.append(f"  place: {c['place']} at the scale of a {c['scale']} ({c['cutaway']['scale_step']} than the cut before), seen {c['cutaway']['view']}")
        cam = c['cutaway']['camera']
        rows.append(f"  motion: the camera {cam['move']}" + (f" by {cam.get('amount')}" if cam.get('amount') else '')
                    + (f"; {c['change'].get('element', {}).get('what')} moves {c['change']['element']['path']}" if c['change'].get('element') else ''))
        out.append('\n'.join(rows))
    return out


def rules(project):
    """The direction rules with the reference's numbers behind them (v1's reel rules: per role its cut length, focus
    height and resting share - targets to aim at; the checks are the net under them) and the reference's cut lengths
    as measured, shown with their strength."""
    from studio.reel_flow import rules as v1_rules
    from studio.project import project_dir
    from .grammar import read as grammar_read
    out = v1_rules(project_dir(project))
    try:
        out['cut_length'] = grammar_read('duration_s')
    except Exception as error:   # noqa: BLE001 - no reference library: said
        out['cut_length'] = {'why': str(error)}
    return out


def read(project, reader=None, force=False):
    """v1's plan reader on the cuts as they stand (decisions/plan_read.json, the same record)."""
    from studio import plan_read
    return plan_read.read(project, reader=reader, force=force, layer='cuts')


# --- projection into shots ------------------------------------------------------------------------------------------

def _direction(c, table):
    cut, change = c['cutaway'], c['change']
    reveal = cut['opens'] if cut['opens'] in ('section_cut', 'explode', 'transparency', 'real_to_diagram', 'none') else None
    cam = cut['camera']
    out = {'show_how': deepcopy(c['show_how']), 'scale_step': cut['scale_step'],
           'camera': f"{cam['move']}" + (f" by {cam['amount']} of the picture" if cam.get('amount') else '') + f", seen {cut['view']}"}
    if reveal:
        out['reveal'] = reveal
    if change['kind'] != 'none':
        at = change.get('at') or [0, 1]
        out['action'] = f"{change.get('what', change['kind'])} ({change['kind']}, from {at[0]:g} to {at[1]:g} of the cut)"
    return out


def materialize(path, body):
    """One shot per cut (route composite) and the timeline, v1's way: each shot's narration is its own lines (a line run on
    from the cut before is voiced there), each sentence linked as the script links it; a shot's clip is kept while its
    cut is unchanged (route.composite.cut_sha)."""
    from studio import decisions
    from studio.project import default_shot, load_project, load_shot, shot_path, validate_shot
    from studio.common import write_json
    from .grammar import load
    table = load()
    project = load_project(path)
    fps = project['output']['fps']
    lines = {l['line_id']: l for l in decisions.envelope(path, 'script')['body']['lines']}
    shots = as_shots(body)
    own, runs_on = decisions.own_lines(shots), decisions.continues_into(shots)
    timeline, offset, written = [], 0, []
    for c in body['cuts']:
        sid = c['cut_id']
        frames = max(1, round(c['duration_s'] * fps))
        file = shot_path(path, sid)
        shot = load_shot(path, sid) if file.is_file() else default_shot(sid, frames, project['brief'])
        if file.is_file():
            shot['revision'] += 1
        said = [i for i, _ in own[sid]]
        text = ' '.join(lines[i]['text'].strip() for i in said)
        shot['duration_frames'] = frames
        shot['goal'] = c['purpose']
        from studio.story_core import beats_of
        told = beats_of(c['line_ids'], {i: l.get('story_beat_id') for i, l in lines.items()})
        if told:
            shot['story_beat_ids'] = list(told)
        shot['direction'] = _direction(c, table)
        if c.get('role'):
            shot['screen'] = {**(shot.get('screen') or {}), 'role': c['role']}
        route = deepcopy(ROUTE)
        kept = (shot.get('route') or {}).get('composite')
        if kept and kept.get('cut_sha') == cut_sha(c):   # the clip made for this very cut still stands
            route['composite'] = kept
        shot['route'] = route
        shot['narration'] = {**shot['narration'], 'text': text, 'sentence_claims': decisions._sentence_links([lines[i] for i in said], text)}
        if c.get('line_at_s') and said:
            shot['narration']['segments'] = [{'text': lines[i]['text'].strip(), 'at_s': at} for i, at in own[sid]]
        else:
            shot['narration'].pop('segments', None)
        if runs_on.get(sid):
            shot['narration']['continues_into'] = list(runs_on[sid])
        else:
            shot['narration'].pop('continues_into', None)
        if not said:
            carried = next((k for k, into in runs_on.items() if sid in into), None)
            shot['narration']['silent'] = f'the line said in {carried} runs on over it' if carried else 'a picture with no words of its own'
        else:
            shot['narration'].pop('silent', None)
        validate_shot(shot)
        write_json(file, shot)
        written.append(sid)
        timeline.append({'shot_id': sid, 'start_frame': offset, 'frame_count': frames})
        offset += frames

    def change(project):
        project['shots'] = timeline
    decisions._project_update(path, change)
    return {'shots': written, 'timeline_frames': offset}


def drift(path, body):
    """Where the timeline or a shot no longer says what the settled cuts say."""
    from studio.project import load_project, load_shot
    project = load_project(path)
    fps = project['output']['fps']
    if [e['shot_id'] for e in project['shots']] != [c['cut_id'] for c in body['cuts']]:
        return ['project.shots timeline']
    return [f"{c['cut_id']} duration_frames" for c in body['cuts'] if load_shot(path, c['cut_id'])['duration_frames'] != max(1, round(c['duration_s'] * fps))]


def sheet_lines(body, path=None):
    out = [f"**Look:** {body['look']}", f"**Labels:** letters {body['label_height']:.3f} of the frame height", '', '**Places:**'] + [f"- `{p['id']}` {p['words']}" for p in body['places']]
    out += ['', f"**The reel proves:** {body['point']['says']} — by {body['point']['proof']['what']} ({', '.join(body['point']['proof']['rows'])})", '',
            '| cut | s | lines | learns | opens | flow | labels | scale | camera | change |', '|---|---|---|---|---|---|---|---|---|---|']
    for c in body['cuts']:
        cut, change = c['cutaway'], c['change']
        out.append(f"| {c['cut_id']} | {c['duration_s']:g} | {', '.join(c['line_ids'])} | {c['show_how']['point']} | {cut['opens']} | "
                   f"{'; '.join(f['what'] + ' (' + f['colour'] + ')' for f in cut['flow']) or '-'} | {', '.join(l['word'] for l in cut['labels']) or '-'} | "
                   f"{c['scale']} ({cut['scale_step']}) | {cut['camera']['move']} {cut['camera'].get('amount') or ''} | "
                   f"{change['kind']}{': ' + change['what'] if change.get('what') else ''} |")
    return out


def write(project, body, agent_note=None, user_words=None):
    from studio import decisions
    return decisions.write(project, 'cuts', body, user_words=user_words, agent_note=agent_note)


def register_commands(subparsers):
    parser = subparsers.add_parser('cuts', help='The cuts layer: each cut planned as a cutaway before any picture')
    commands = parser.add_subparsers(dest='cuts_command', required=True)
    p = commands.add_parser('write', help="settle the cuts (v1 decide write: the user's words, or a delegated run's agent note)")
    p.add_argument('--project', required=True); p.add_argument('--body', required=True)
    p.add_argument('--agent-note'); p.add_argument('--user-words')
    p.set_defaults(handler=lambda a: write(a.project, a.body, a.agent_note, a.user_words))
    p = commands.add_parser('lint', help='what the cuts body would be refused or warned for, without writing it')

    def lint_handler(a):
        from studio import decisions
        from studio.common import json_arg
        from studio.project import project_dir
        body = json_arg(a.body)
        decisions.validate_body('cuts', body)
        return decisions.lint(project_dir(a.project), 'cuts', body)
    p.add_argument('--project', required=True); p.add_argument('--body', required=True)
    p.set_defaults(handler=lint_handler)
    p = commands.add_parser('rules', help="the direction rules and the reference's numbers to aim at (cut lengths, per role) before writing cuts")
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: rules(a.project))
    p = commands.add_parser('read', help="v1's plan reader on the cuts: derived or decoration, a stronger way; the reel's proof and opening")
    p.add_argument('--project', required=True); p.add_argument('--force', action='store_true')
    p.set_defaults(handler=lambda a: read(a.project, force=a.force))
    return commands
