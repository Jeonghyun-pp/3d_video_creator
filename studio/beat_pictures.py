"""The world's beat pictures are read for what they show, and held to what their lines explain (studio/explains.py).

A beat's lines explain things (their claims' `about`). Each picture of the beat is read once (the same reader as the
frame pictures, screen_grammar.codex_json) for which of those things it shows, which it shows opened, and how; the
reading sits beside the picture (pNN.read.json), bound to the picture's hash and to the list it was read against. The
world lint then asks: is every explained thing seen in some picture of its beat, and is what sits inside its subject
(the exposure survey) seen opened - by any method of show_core.SHOW_METHODS that opens.

Why (2026-10-13): world r06's pictures of "유압 팔이 엔진을 기울여 / 헬륨이 피스톤을 밀어" drew a closed booster and
only a person noticed. The user: "이런 걸 스스로 잡아낼 수 없다는 시스템에 문제가 있는 거 아니야?"
"""
from __future__ import annotations

from pathlib import Path

from .blender_ops.show_core import METHODS, how_choices, opens
from .common import StudioError, file_hash, now, read_json, stable_hash, write_json
from .project import project_dir

PROMPT = """You read pictures drawn for one beat of a video's story. Answer only from the attached images; run no commands.
Image 1 is index 0, image 2 index 1, and so on. What the narration says over this beat: {lines}
For each image:
- shows: of these things - {things} - the ids the picture clearly shows, large enough to tell what it is (not a speck
  at its edge); [] if none. Something that sits inside another thing counts only when the picture really shows it
  (cut open, see-through, moved apart, or through an opening) - never when it is only implied.
- opened: of the ids in shows, those the picture shows only because what is around them is cut open, moved apart or
  see-through; [] for a picture from outside.
- how: how the picture shows its main subject - {how}.
- note: one sentence on what the picture shows.
Return every image, in order."""


def schema():
    picture = {'type': 'object', 'additionalProperties': False, 'required': ['index', 'shows', 'opened', 'how', 'note'],
               'properties': {'index': {'type': 'integer'}, 'shows': {'type': 'array', 'items': {'type': 'string'}},
                              'opened': {'type': 'array', 'items': {'type': 'string'}}, 'how': {'enum': list(METHODS)},
                              'note': {'type': 'string'}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['pictures'],
            'properties': {'pictures': {'type': 'array', 'items': picture}}}


def _layers(path):
    from .decisions import envelope
    return {layer: (envelope(path, layer) or {}).get('body') for layer in ('facts', 'script', 'world')}


def _beat(world, beat_id):
    beat = next((b for b in (world or {}).get('beats', []) if b['beat_id'] == beat_id), None)
    if beat is None:
        raise StudioError('INPUT_INVALID', f'no beat {beat_id} in the world', recovery='studio decide show --layer world')
    return beat


def explained(path, beat, facts, script, world):
    """The things a beat's lines explain (explains.line_things)."""
    from .explains import line_things
    return line_things(path, beat['line_ids'], facts, script, world)


def candidates(path, beat, facts, script, world):
    """{id: words} the reader picks from: what the beat explains, the world's elements, the subjects."""
    from .explains import _part_words, _specs
    specs = _specs(path)
    out = {}
    for thing in explained(path, beat, facts, script, world):
        out[thing['id']] = thing['words']
        for part in thing['parts']:   # a feature's own parts too: the reader may see one gear, not 'the gears'
            sid, part_id = part.split('/', 1)
            out.setdefault(part, f"{sid}: {_part_words(specs[sid], part_id)}")
    for element in (world or {}).get('elements', []):
        out.setdefault(element['id'], element['description'])
    for sid, spec in specs.items():
        out.setdefault(sid, str(spec.get('identity') or sid))
    return out


def reading_path(path, picture):
    return Path(path, picture).with_suffix('.read.json')


def reading(path, picture, sha256, key):
    """The picture's reading if it was made of these bytes against this list, else None."""
    file = reading_path(path, picture)
    if not file.is_file():
        return None
    row = read_json(file)
    return row if row.get('sha256') == sha256 and row.get('candidates') == key else None


def _key(cands):
    return stable_hash({'candidates': sorted(cands.items()), 'prompt': stable_hash(PROMPT), 'how': how_choices(), 'schema': schema()})[:16]


def _lines_text(script, beat):
    lines = {l['line_id']: l['text'] for l in (script or {}).get('lines', [])}
    return ' '.join(lines.get(i, '') for i in beat['line_ids']).strip() or '(no words)'


def read(project, beat_id, pictures=None, force=False):
    """Read the beat's pictures (its world pictures, or the given paths - one just drawn, before the world names it)."""
    from .screen_grammar import codex_json
    path = project_dir(project)
    layers = _layers(path)
    beat = _beat(layers['world'], beat_id)
    rows = [{'path': p, 'sha256': file_hash(path / p)} for p in pictures] if pictures else [
        {'path': p['path'], 'sha256': p['sha256']} for p in beat['pictures']]
    cands = candidates(path, beat, layers['facts'], layers['script'], layers['world'])
    key = _key(cands)
    todo = [r for r in rows if force or reading(path, r['path'], r['sha256'], key) is None]
    if todo:
        for r in todo:
            if file_hash(path / r['path']) != r['sha256']:
                raise StudioError('INPUT_INVALID', f"{r['path']} changed since the world named it", recovery='add the picture again (concept add-beat)')
        out = path / 'concepts' / 'world' / 'readings' / f"{stable_hash({'key': key, 'pictures': [r['sha256'] for r in todo]})[:16]}.json"
        prompt = PROMPT.format(lines=_lines_text(layers['script'], beat), how=how_choices(),
                               things='; '.join(f'{i} ({w})' for i, w in cands.items()) or 'none')
        answer = read_json(out) if out.is_file() and not force else codex_json([path / r['path'] for r in todo], prompt, schema(), out)
        got = {r['index']: r for r in answer['answer']['pictures']}
        for i, r in enumerate(todo):
            if i not in got:
                raise StudioError('LABELLER_FAILED', f"{beat_id}: the reader skipped {r['path']}", retryable=True)
            read = {k: v for k, v in got[i].items() if k != 'index'}
            read['shows'] = [s for s in read['shows'] if s in cands]
            read['opened'] = [s for s in read['opened'] if s in read['shows']]
            write_json(reading_path(path, r['path']), {'sha256': r['sha256'], 'candidates': key, 'beat_id': beat_id, 'read': read,
                                                       'read_at': now(), 'labels': str(out.relative_to(path))})
    return {'beat_id': beat_id, 'read': [r['path'] for r in todo], 'judged': judge(path, beat, layers, extra=rows if pictures else None)}


def mark(project, beat_id, picture, shows=None, opened=None, how=None, note=None):
    """Correct a reading by hand (the reader missed or invented something); the correction keeps its provenance."""
    if not note or len(note.strip()) < 4:
        raise StudioError('INPUT_INVALID', 'mark-beat needs --note: what the reader got wrong')
    path = project_dir(project)
    layers = _layers(path)
    beat = _beat(layers['world'], beat_id)
    cands = candidates(path, beat, layers['facts'], layers['script'], layers['world'])
    sha = file_hash(path / picture)
    old = reading(path, picture, sha, _key(cands)) or {'read': {'shows': [], 'opened': [], 'how': 'exterior', 'note': ''}}
    read = dict(old['read'])
    for name, value in (('shows', shows), ('opened', opened)):
        if value is not None:
            unknown = [v for v in value if v not in cands]
            if unknown:
                raise StudioError('INPUT_INVALID', f'{unknown} are not among what this beat is read against: {sorted(cands)}')
            read[name] = list(value)
    if how is not None:
        if how not in METHODS:
            raise StudioError('INPUT_INVALID', f'how must be one of {list(METHODS)}')
        read['how'] = how
    read['opened'] = [s for s in read['opened'] if s in read['shows']]
    write_json(reading_path(path, picture), {'sha256': sha, 'candidates': _key(cands), 'beat_id': beat_id, 'read': read, 'read_at': now(),
                                             'provenance': {'kind': 'manual', 'note': note.strip(), 'at': now()}})
    return {'beat_id': beat_id, 'picture': picture, 'read': read, 'judged': judge(path, beat, layers)}


def _shown(read, thing):
    ids = {thing['id'], *thing['parts']}
    return bool(ids & set(read['shows']))


def _opened(read, thing):
    return bool({thing['id'], *thing['parts']} & set(read['opened'])) or bool(opens(read['how']))


def judge(path, beat, layers, extra=None):
    """{things: [{id, words, inside, hidden_by, problem, seen_in, closed_in}], unread: [picture paths]} for a beat."""
    from .explains import need
    things = explained(path, beat, layers['facts'], layers['script'], layers['world'])
    key = _key(candidates(path, beat, layers['facts'], layers['script'], layers['world']))
    pictures = [*({'path': p['path'], 'sha256': p['sha256']} for p in beat['pictures']), *(extra or [])]
    reads, unread = {}, []
    for p in pictures:
        row = reading(path, p['path'], p['sha256'], key)
        if row is None:
            unread.append(p['path'])
        else:
            reads[p['path']] = row['read']
    out = []
    for thing in things:
        n = need(path, thing)
        seen = [p for p, r in reads.items() if _shown(r, thing)]
        opened = [p for p in seen if _opened(reads[p], thing)]
        out.append({'id': thing['id'], 'words': thing['words'], 'inside': n['inside'], 'hidden_by': n['hidden_by'], 'problem': n['problem'],
                    'seen_in': seen, 'opened_in': opened})
    return {'things': out, 'unread': unread}


def met(row):
    """The thing is shown as it must be: seen, and opened when it sits inside."""
    return bool(row['opened_in'] if row['inside'] else row['seen_in'])


def lint(path, world, script, facts):
    """World lint errors: what the beats' lines explain is seen in the beats' pictures, opened when it is inside."""
    from .decisions import about_lines
    from .explains import exposure_command, open_words
    if not facts:
        return []
    errors = [e for e in about_lines(path, facts, world) if e.startswith('ABOUT_UNKNOWN')]
    layers = {'facts': facts, 'script': script, 'world': world}
    asked = set()
    for beat in world['beats']:
        verdict = judge(path, beat, layers)
        if not verdict['things']:
            continue
        errors += [f"BEAT_PICTURE_UNREAD: {beat['beat_id']}: {p} is not read against what its lines explain - "
                   f"studio concept read-beat --project {path} --beat {beat['beat_id']}" for p in verdict['unread']]
        for row in verdict['things']:
            subject = row['id'].split('/')[0]
            if row['problem'] and (row['problem'], subject) not in asked:
                asked.add((row['problem'], subject))
                errors.append(f"{row['problem']}: {beat['beat_id']} explains {row['id']}; whether it is inside its subject is not measured "
                              f"- {exposure_command(subject, path)}")
            if verdict['unread'] or met(row):
                continue
            if row['seen_in']:
                errors.append(f"BEAT_INSIDE_CLOSED: {beat['beat_id']} explains {row['id']} ({row['words']}), which sits inside "
                              f"{', '.join(row['hidden_by']) or 'its subject'} (no outside side shows it); {', '.join(row['seen_in'])} "
                              f"show it closed - draw it opened ({open_words()}): studio concept brief --project {path} --beat {beat['beat_id']}")
            else:
                errors.append(f"BEAT_EXPLAINS_UNSEEN: {beat['beat_id']} explains {row['id']} ({row['words']}) and none of its pictures "
                              f"shows it - studio concept brief --project {path} --beat {beat['beat_id']}")
    return errors


MAX_DRAWS = 3   # pictures drawn for one thing that still miss it, before the agent stops redrawing and shows the user: a
                # picture the image tool draws wrong three times is a wording or method question, not one more try (a
                # Codex picture took 2-4 minutes on 2026-10-13)


def want_lines(path, things):
    """Prompt lines for what a picture must show: each thing in words, and, for what sits inside its subject (the
    exposure survey), that it must be opened - in any of the table's opening ways, never one named here."""
    from .blender_ops.show_core import SHOW_METHODS, opening_methods
    from .explains import need
    out, inner = [], []
    for thing in things:
        n = need(path, thing)
        out.append(f"- {thing['id']}: {thing['words']}")
        if n['inside']:
            inner.append(f"{thing['id']} sits inside {', '.join(n['hidden_by']) or 'its subject'}: no side of the closed "
                         f"{thing['subject']} shows it")
    lines = ['It must clearly show (each large enough to tell what it is):', *out]
    if inner:
        lines += [*inner, 'So the picture must open the subject up or see through to those, in one of these ways:',
                  *(f"- {how}: {SHOW_METHODS[how]['draw']}" for how in opening_methods())]
    return lines


def drawn_for(path, beat_id, thing, inside, key):
    """Pictures drawn for this thing in this beat (add-beat --for) whose reading says they miss it - not shown, or shown
    closed when it sits inside."""
    out = []
    for record in sorted(Path(path, 'concepts', 'world').glob('*/p*.json')):
        if record.name.endswith('.read.json'):
            continue
        row = read_json(record)
        if not isinstance(row, dict) or row.get('beat_id') != beat_id or thing['id'] not in (row.get('for') or []):
            continue
        r = reading(path, row['path'], row['sha256'], key)
        if r is not None and not (_shown(r['read'], thing) and (not inside or _opened(r['read'], thing))):
            out.append(row['path'])
    return out


def brief(project, beat_id, things=None):
    """What to draw for a beat picture: what its lines explain that no picture shows yet as it must (or `things`), with
    the reasons (inside -> opened) and the missed tries; and the next commands. The picture is drawn with the agent's
    image tool, added with add-beat --for, and read with read-beat."""
    path = project_dir(project)
    layers = _layers(path)
    beat = _beat(layers['world'], beat_id)
    verdict = judge(path, beat, layers)
    all_things = {t['id']: t for t in explained(path, beat, layers['facts'], layers['script'], layers['world'])}
    rows = {r['id']: r for r in verdict['things']}
    if things:
        unknown = [t for t in things if t not in all_things]
        if unknown:
            raise StudioError('INPUT_INVALID', f'{beat_id} does not explain {unknown} (it explains {sorted(all_things)})')
        wanted = [all_things[t] for t in things]
    else:
        wanted = [all_things[i] for i, r in rows.items() if not met(r)]
    key = _key(candidates(path, beat, layers['facts'], layers['script'], layers['world']))
    misses = {t['id']: drawn_for(path, beat_id, t, rows[t['id']]['inside'], key) for t in wanted}
    spent = {t: m for t, m in misses.items() if len(m) >= MAX_DRAWS}
    if spent:
        raise StudioError('DRAW_LIMIT', f"{beat_id}: {', '.join(f'{t} missed in {len(m)} pictures drawn for it' for t, m in spent.items())}",
                          recovery='show the user these pictures and ask how to show it (other words, another opening, '
                                   'a reference picture), then concept brief again with their answer')
    world = layers['world'] or {}
    out = load_project_output(path)
    lines = [f"One {out} picture for one beat of a short explainer video.",
             f"What the narration says over it: {_lines_text(layers['script'], beat)}"]
    lines += [f"The beat: {beat['description']}" for _ in [0] if beat.get('description')]
    lines += [f"Where and when: {world['place']}" for _ in [0] if world.get('place')]
    lines += [f"Light: {world['time_light']}" for _ in [0] if world.get('time_light')]
    if wanted:
        lines += want_lines(path, wanted)
    missed = sorted({p for m in misses.values() for p in m})
    if missed:
        lines.append(f"Pictures drawn before missed this ({', '.join(missed)}): make it unmistakable.")
    avoid = ((_brief_body(path) or {}).get('must_avoid')) or []
    if avoid:
        lines.append('Avoid: ' + '; '.join(avoid) + '.')
    from .reference_use import attachments, collect_hint, consented
    attach, compare, missing = attachments(path, wanted)
    if attach:
        lines.append('The attached photos show what these things really look like: take their shape and parts, never a logo, '
                     'a lettering or a face from them.')
    lines.append('No text, no letters, no labels in the picture.')
    ids = ','.join(t['id'] for t in wanted)
    collect = [collect_hint(path, t) for t in wanted if t['id'] in missing]
    return {'beat_id': beat_id, 'things': [t['id'] for t in wanted], 'prompt': '\n'.join(lines), 'attach': attach,
            'compare_only': compare, 'done': not wanted and not verdict['unread'],
            'next': [*(['first collect photos of what has none (outside the sandbox), then brief again:', *collect] if collect else []),
                     *(["photos exist but the user has not allowed attaching them: studio reference attach-consent --project "
                        f"{path} --user-words \"<the user's words>\""] if compare and not consented(path) else []),
                     f"draw it with the image tool, then: studio concept add-beat --project {path} --beat {beat_id} --image <file> "
                     f"--prompt <this prompt>" + (f" --for {ids}" if ids else ''),
                     f"studio concept read-beat --project {path} --beat {beat_id} --picture <the added path>",
                     'when it shows them: add the picture to the beat (decide revise --layer world) and lint']}


def load_project_output(path):
    from .project import load_project
    out = load_project(path)['output']
    return 'vertical' if out['height'] > out['width'] else 'horizontal' if out['width'] > out['height'] else 'square'


def _brief_body(path):
    from .decisions import envelope
    return (envelope(path, 'brief') or {}).get('body')
