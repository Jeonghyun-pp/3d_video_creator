"""The shot list read as a chain of decisions, in words, by a model - a cheap first filter before anything is built.

For each cut the reader gets the chain the shot list wrote (studio/show_how.py): the story beat's picture and event, the
lines said over it, what its viewer must learn (show_how.point), the rows that prove it, the showing way and the one not
picked, its beat's picture kept or left, its place and scale (journey), its framing and its motion with why - and for the
reel its point, the scene that proves it and what the first cut does. It answers per cut whether the place and the motion
follow from the proof or decorate it (derived) and whether another place, scale or way would show the point better
(stronger), and for the reel whether the proving scene is carried by the cuts that name it and whether the first cut does
what the opening says.

What it finds is a flow finding (reel_flow.DEPARTS), answered like the others: fixed, or departed with why (the cut's
pattern departs, read with its picture by studio/excuses.py). It is no floor: the words are not the picture - the viewer
who does not know the plan judges the picture (studio/reel_see.py). Each answer holds for the cut as it was read (its
shot list entry, its answers aside): a cut changed since is read again.

Why (2026-10-26): the fab reel's first cut flew in through the roof while its beat's picture was a box snatched from a
reaching hand - every field was filled, nothing read whether they followed from each other.
"""
from __future__ import annotations

from .common import StudioError, now, read_json, stable_hash, write_json
from .project import project_dir

RECORD = 'decisions/plan_read.json'
# what each finding is, and the answer item that makes it one (a reading's field, read as wanting when it has this value)
FINDINGS = {'PLAN_NOT_DERIVED': ('derived', False), 'PLAN_STRONGER': ('stronger', True)}
REEL_FINDINGS = {'PROOF_NOT_CARRIED': ('proof_carried', False), 'OPENING_UNDONE': ('opening_done', False)}

PROMPT = """You read the plan of a short vertical explainer reel before anything is built. It is written as a chain of
decisions for each cut: the story beat's picture, what the viewer must learn from the cut, the rows on screen that prove
it, the way it shows them, where and at what scale the camera is, and how it moves. A sound chain runs top-down: the
place, the scale and the motion follow from what must be proved; a motion chosen for its own look - a flight, a sweep, an
orbit that the proof does not need - is decoration, however well it is described. Judge only from what is written.
Only what the build makes (a cut's changes, its show moments, its framing) is what will be on screen; its
purpose, its motion's why and the words said over it are claims. Something the claims say happens that no row makes
will not be seen.

The reel: it proves "{says}". The scene that proves it: {proof}. What the first cut does: {opening}.

{cuts}

For each cut answer:
- derived: true when its place, scale and motion follow from its proof - they put on screen what the viewer must learn,
  where it is best seen; false when the motion or the place is decoration, or when the cut leaves its beat's picture for
  something that proves less without a reason that holds.
- derived_why: one sentence.
- stronger: better true only when another place, scale or showing way would clearly prove this cut's point better (inside
  the thing rather than outside it, closer, held instead of moved, or the beat's own picture); then what and why in one
  sentence each. Otherwise false, with what and why empty.
- note: one sentence.
Then for the reel: proof_carried (the rows the proving scene names make that whole scene - every change it says, large
and clear enough to prove the point; false when they make something weaker or only part of it, however the cut's
purpose describes it) with why, and opening_done (the first cut does what
the opening says) with why."""


def _schema(ids):
    stronger = {'type': 'object', 'additionalProperties': False, 'required': ['better', 'what', 'why'],
                'properties': {'better': {'type': 'boolean'}, 'what': {'type': 'string'}, 'why': {'type': 'string'}}}
    cut = {'type': 'object', 'additionalProperties': False, 'required': ['shot_id', 'derived', 'derived_why', 'stronger', 'note'],
           'properties': {'shot_id': {'enum': ids}, 'derived': {'type': 'boolean'}, 'derived_why': {'type': 'string'},
                          'stronger': stronger, 'note': {'type': 'string'}}}
    verdict = {'type': 'object', 'additionalProperties': False, 'required': ['holds', 'why'],
               'properties': {'holds': {'type': 'boolean'}, 'why': {'type': 'string'}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['cuts', 'proof_carried', 'opening_done'],
            'properties': {'cuts': {'type': 'array', 'items': cut, 'minItems': len(ids), 'maxItems': len(ids)},
                           'proof_carried': verdict, 'opening_done': verdict}}


def entry_sha(entry):
    """What a reading of a cut holds for: its shot list entry, its answers (pattern: what it carries and departs) aside -
    answering a finding is not a new plan."""
    return stable_hash({k: v for k, v in entry.items() if k != 'pattern'})[:16]


def reel_sha(body):
    """What a reading of the reel holds for: its point and its first cut."""
    return stable_hash({'point': body.get('point'), 'first': entry_sha(body['shots'][0]) if body.get('shots') else None})[:16]


def _row_words(shot, ref):
    from .change_moves import words as change_words
    from .show_how import _row
    row = _row(shot, ref)
    if row is None:
        return f'{ref} (no such row)'
    if ref == 'frame':
        return f"its framing: {', '.join(row.get('focus') or [])} {row.get('size', '')}"
    if ref.startswith('show@'):
        return f"at {row['at']} it shows {', '.join(row['parts'])} {row['how']} - {row['why']}"
    return f"{change_words(row)} - {row.get('why', '')}"


def chain(body, story=None, lines=None):
    """Each cut's chain of decisions in words, in order (what the reader gets; the sheet shows the same chain)."""
    beats = {b['story_beat_id']: b for b in (story or {}).get('beats') or []}
    line_by = {l['line_id']: l for l in lines or []}
    places = body.get('journey') or []
    out = []
    for n, s in enumerate(body.get('shots') or []):
        from .story_core import beats_of
        told = s.get('story_beat_ids') or beats_of(s.get('line_ids') or [], {i: l.get('story_beat_id') for i, l in line_by.items()})
        d = s.get('direction') or {}
        how = d.get('show_how') or {}
        move = d.get('move') or {}
        part = d.get('journey') or {}
        rows = [f"cut {n} = {s['shot_id']} ({s['duration_s']} s)"]
        for b in told:
            beat = beats.get(b) or {}
            rows.append(f"  beat {b}: {beat.get('what_happens', '')} | its picture: {beat.get('picture_idea') or '(none)'}"
                        + (f" | on screen: {(beat.get('on_screen') or {}).get('event')}" if beat.get('on_screen') else ''))
        said = ' '.join(line_by[i]['text'] for i in s.get('line_ids') or [] if i in line_by)
        rows.append(f"  said over it: {said or '(no words)'}")
        rows.append(f"  what it is for: {s.get('purpose', '')}")
        rows.append(f"  the viewer must learn: {how.get('point') or '(not written)'}")
        from .blender_ops.show_core import show_of
        made = [c['id'] for c in d.get('change') or []] + [f"show@{r['at']}" for r in show_of(s)]
        rows.append('  on screen (what the build makes): ' + ('; '.join(_row_words(s, r) for r in made) or 'nothing changes, no moment named'))
        rows.append('  it proves its point by: ' + (', '.join(how.get('proof') or []) or '(not written)'))
        way = how.get('way') or {}
        rows.append(f"  showing way: {way.get('id') or '(not written)'}"
                    + (f" - not {way['rejected']['id']}, because {way['rejected']['why']}" if way.get('rejected') else ''))
        picture = how.get('picture')
        rows.append('  the beat\'s picture: ' + ('kept' if picture == 'beat' else f"left - {picture['departs']}" if isinstance(picture, dict)
                                                else '(not said)'))
        if part and all(0 <= part.get(k, -1) < len(places) for k in ('from', 'to')):
            a, b = places[part['from']], places[part['to']]
            rows.append(f"  place: {part['role']} from {a['place']} ({a['scale']}) to {b['place']} ({b['scale']}) - {part.get('why', '')}")
        frame = s.get('frame') or {}
        rows.append(f"  framing: {', '.join(frame.get('focus') or []) or '(not written)'} {frame.get('size', '')}")
        rows.append(f"  motion: {move.get('recipe') or '(not written)'}, revealing {move.get('reveals', '')}, key at {move.get('key_at')} "
                    f"- why: {move.get('why', '')}" if move else '  motion: (not written)')
        out.append('\n'.join(rows))
    return out


def prompt_for(body, story=None, lines=None, chain_of=chain):
    point = body.get('point') or {}
    proof = point.get('proof') or {}
    return PROMPT.format(says=point.get('says') or '(not written)',
                         proof=f"{proof.get('what', '(not written)')} (rows: {', '.join(proof.get('rows') or [])})",
                         opening=f"{(point.get('opening') or {}).get('does', '(not written)')} - {(point.get('opening') or {}).get('why', '')}",
                         cuts='\n\n'.join(chain_of(body, story, lines)))


def _inputs(path, layer='shotlist'):
    """(the plan as a shot list, the story, the script's lines, its chain in words): the shot list itself, or a layer whose
    code gives its plan as one (decisions.LAYER_CODE: as_plan, chain - v2's cuts)."""
    from .decisions import envelope, layer_code
    body = (envelope(path, layer) or {}).get('body')
    if not body:
        raise StudioError('DECISION_MISSING', f'plan read: no {layer} to read', recovery=f'decide propose --layer {layer} first')
    code = layer_code(layer)
    plan, chain_of = (code.as_plan(body), code.chain) if code is not None else (body, chain)
    return plan, (envelope(path, 'story') or {}).get('body'), ((envelope(path, 'script') or {}).get('body') or {}).get('lines') or [], chain_of


def records(path):
    file = project_dir(path) / RECORD
    return read_json(file).get('rounds', []) if file.is_file() else []


def read(project, reader=None, force=False, layer='shotlist'):
    """Read the shot list as it stands (decisions/shotlist.json; or another layer that plans cuts, _inputs) and keep the
    answers (decisions/plan_read.json, its rounds kept). reader(images, prompt, schema, target) stands in for the model in
    tests; the same plan is not asked again."""
    from .screen_grammar import codex_json
    path = project_dir(project)
    body, story, lines, chain_of = _inputs(path, layer)
    prompt = prompt_for(body, story, lines, chain_of)
    schema = _schema([s['shot_id'] for s in body['shots']])
    key = stable_hash({'prompt': prompt, 'schema': schema})[:16]
    rounds = records(path)
    done = next((r for r in rounds if r['key'] == key), None)
    if done and not force:
        return {**done, 'findings': findings_of(done)}
    target = path / 'review' / 'plan_read' / f'read_{key}.json'
    (reader or (lambda i, p, s, t: codex_json(i, p, s, t, force=force)))([], prompt, schema, target)
    got = read_json(target)
    answer = got['answer']
    entries = {s['shot_id']: entry_sha(s) for s in body['shots']}
    row = {'key': key, 'round': len(rounds) + 1, 'at': now(), 'reader': got.get('reader') or 'codex', 'reel_sha': reel_sha(body),
           'cuts': [{**c, 'entry_sha': entries[c['shot_id']]} for c in answer['cuts']],
           'proof_carried': answer['proof_carried'], 'opening_done': answer['opening_done']}
    write_json(path / RECORD, {'schema_version': 1, 'rounds': rounds + [row]})
    return {**row, 'findings': findings_of(row), 'artifacts': [str(path / RECORD), str(target)]}


def _wanting(cut, field, bad):
    value = cut.get(field)
    return (value.get('better') if isinstance(value, dict) else value) is bad


def findings_of(row):
    """{shot_id or 'reel': [codes]} a reading found."""
    out = {}
    for c in row.get('cuts') or []:
        codes = [code for code, (field, bad) in FINDINGS.items() if _wanting(c, field, bad)]
        if codes:
            out[c['shot_id']] = codes
    reel = [code for code, (field, bad) in REEL_FINDINGS.items() if (row.get(field) or {}).get('holds') is bad]
    if reel:
        out['reel'] = reel
    return out


def latest(path, entry):
    """The latest reading of this cut as it stands now (its entry unchanged since), or None."""
    sha = entry_sha(entry)
    for row in reversed(records(path)):
        cut = next((c for c in row.get('cuts') or [] if c['shot_id'] == entry['shot_id']), None)
        if cut is not None and cut.get('entry_sha') == sha:
            return cut
    return None


def lines(path, body, silenced=()):
    """The flow findings the latest readings left on the shot list as it stands: a cut read as it is now, the reel read with
    its point and first cut as they are now (a departure answers one: the cut's pattern departs, studio reel depart)."""
    from .show_how import _departs
    out = []
    for s in body.get('shots') or []:
        cut = latest(path, s)
        if cut is None:
            continue
        if _wanting(cut, 'derived', False) and not _departs(s, 'PLAN_NOT_DERIVED') and 'PLAN_NOT_DERIVED' not in silenced:
            out.append(f"PLAN_NOT_DERIVED: {s['shot_id']}: the plan reader finds its place or motion does not follow from its proof - "
                       f"{cut['derived_why']} (fix the chain, or say why: pattern departs plan_not_derived)")
        if _wanting(cut, 'stronger', True) and not _departs(s, 'PLAN_STRONGER') and 'PLAN_STRONGER' not in silenced:
            out.append(f"PLAN_STRONGER: {s['shot_id']}: the plan reader would prove its point by {cut['stronger']['what']} - "
                       f"{cut['stronger']['why']} (take it, or say why not: pattern departs plan_stronger)")
    row = next((r for r in reversed(records(path)) if r.get('reel_sha') == reel_sha(body)), None)
    for code, (field, bad) in REEL_FINDINGS.items():
        if row and (row.get(field) or {}).get('holds') is bad and code not in silenced:
            out.append(f"{code}: the plan reader finds {'the proving scene is not carried by the cuts it names' if field == 'proof_carried' else 'the first cut does not do what the opening says'} - "
                       f"{row[field]['why']} (fix it, or say why: studio reel depart --code {code})")
    return out


def unread(path, body):
    """The cuts of the shot list no reading covers as they stand now."""
    return [s['shot_id'] for s in body.get('shots') or [] if latest(path, s) is None]


# what a cut shows and where - its plan, as a reopen at the plan level asks it changed (V6): its proof and showing way
# (direction.show_how), its show moments, its changes and its place in the journey. Its motion, framing and answers are
# not its plan: another camera on the same plan is a camera redo (reel_judge._reopen, level camera).
PLAN_FIELDS = (('show',), ('direction', 'show_how'), ('direction', 'change'), ('direction', 'journey'))


def plan_sha(entry):
    def at(keys):
        value = entry
        for k in keys:
            value = (value or {}).get(k) if isinstance(value, dict) else None
        return value
    return stable_hash({'.'.join(k): at(k) for k in PLAN_FIELDS})[:16]


def _entry(path, shot_id):
    from .decisions import envelope
    return next((s for s in ((envelope(path, 'shotlist') or {}).get('body') or {}).get('shots') or [] if s['shot_id'] == shot_id), None)


def reopen(project, shot_id, why, try_=None, source='agent'):
    """The cut goes back to what it shows (level plan): its point was not seen (studio/reel_see.py), or the stronger way the
    plan reader offered is taken. Only a shot list whose plan of the cut differs (PLAN_FIELDS) and a pick after it close it
    (quality_gate.picked: SHOT_REPLAN_NEEDED, then SHOT_UNPICKED) - another camera on the same plan does not."""
    from .storyboard import _envelope_path, envelope
    path = project_dir(project)
    entry = _entry(path, shot_id)
    if entry is None:
        raise StudioError('INPUT_INVALID', f'plan reopen: the shot list plans no {shot_id}')
    if not why or len(why.strip()) < 12:
        raise StudioError('INPUT_INVALID', 'plan reopen --why: a sentence - what the viewer did not get, or the stronger way taken')
    env = envelope(path, shot_id) or {'schema_version': 1, 'shot_id': shot_id, 'history': [], 'status': 'none'}
    env['reopened'] = {'level': 'plan', 'why': why.strip(), 'try': (try_ or 'what shows the point better').strip(), 'source': source,
                       'plan_sha': plan_sha(entry), 'at': now()}
    write_json(_envelope_path(path, shot_id), env)
    return {'shot_id': shot_id, 'reopened': env['reopened'],
            'next': f'decide revise --layer shotlist: {shot_id} anew from its point (show_how, show, change, journey), then build, takes, pick'}


def register_commands(subparsers):
    parser = subparsers.add_parser('plan', help='The shot list read as a chain of decisions (studio/plan_read.py)')
    commands = parser.add_subparsers(dest='plan_command', required=True)
    p = commands.add_parser('read', help="each cut's chain - beat picture, point, proof, way, place, motion - read by a model: derived or "
                                         'decoration, a stronger way; the reel: its proof carried, its opening done')
    p.add_argument('--project', required=True); p.add_argument('--force', action='store_true')
    p.set_defaults(handler=lambda a: read(a.project, force=a.force))
    p = commands.add_parser('reopen', help='a cut back to what it shows (its point unseen, or a stronger way taken): only a new plan of '
                                           'it and a pick close it')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--why', required=True)
    p.add_argument('--try', dest='try_')
    p.set_defaults(handler=lambda a: reopen(a.project, a.shot, a.why, a.try_))
    return commands
