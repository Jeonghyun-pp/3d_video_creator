"""Story pitches: the agent writes many openings from the facts, groups them, and blind judges pick the best before any
story is written (`studio story pitch-*`, then `story directions`).

Why (2026-10-20): the axis draw (story_core.pick_directions) gave every topic the same three directions - ten topics,
one myth, one result-first, one question each - and its 'result first' slot lost to the study set eight times in ten;
a stake drawn from a list was forced onto bread as a food-safety aside. The user: "llm의 창의성을 활용할 방법 고민해봐".
So the code no longer says what kind of story to write: the agent first names the angles this topic can be seen from
(lenses), writes a few pitches per lens in separate passes (a model asked for many at once returns one safe answer
several times), groups the alike, and a blind tournament between pitches of different groups ranks them. The three
directions are the best pitches of three groups.

Files, one round per pitch run: <project>/story/pitches/pNN/
  brief.md      what the agent reads (request, key message, all facts, example lenses, the user's taste as reference)
  lenses.json   [{lens_id, question}]                  the agent's angles for this topic
  pitches.json  [{pitch_id, lens, opening, question, stake}]
  groups.json   {group_id: {label, pitch_ids}}
  judge/        pair_NN.md + brief.md for the judges;  judge_key.json beside it (which pitch is A and B)
  scores.json   {pitch_id: {wins, meetings, score}}
"""
from __future__ import annotations

import json
import random
import re

from .common import REPO, StudioError, json_arg, now, read_json, write_json
from .project import project_dir

PITCHES = 'story/pitches'
LENS_EXAMPLES = REPO / 'library' / 'pitch_lenses.json'
LENSES_MIN, LENSES_MAX = 5, 8      # fewer than 5 angles is one idea with variants; past 8 the agent pads
PER_LENS = (2, 4)                  # pitches written per lens: two at least so a lens is not one lucky line
PITCHES_MIN = 15                   # enough that a tournament has something to choose between
GROUPS_MIN = 3                     # the directions come from three different groups
MEETINGS = 4                       # blind pairs each pitch is in: one meeting says more about the opponent than the pitch
LENS_SAME = 0.8                    # two lenses sharing this share of their words ask one question twice
QUESTION = ("Two ideas for a short vertical explainer video (about 40 s). Each says what the viewer sees first, what they "
            "will wonder, and what is at stake. Which video would you watch to the end? Judge as someone scrolling past, "
            "not as an expert. The why says what the one you did not pick lacks, never what the other one is about. Answer "
            "as JSON: {\"pair\": \"<id>\", \"winner\": \"A\" | \"B\", \"why\": \"<one sentence>\"}.")


def _rounds(path):
    root = project_dir(path) / PITCHES
    return sorted(p for p in root.glob('p[0-9]*') if (p / 'brief.md').is_file()) if root.is_dir() else []


def _round(path, rnd=None):
    rounds = _rounds(path)
    if not rounds:
        raise StudioError('INPUT_INVALID', 'no pitch round yet', recovery=f'studio story pitch-brief --project {path}')
    if rnd is None:
        return rounds[-1]
    found = next((r for r in rounds if r.name == rnd), None)
    if found is None:
        raise StudioError('INPUT_INVALID', f'no pitch round {rnd} (rounds: {[r.name for r in rounds]})')
    return found


def _words(text):
    return set(re.findall(r'\w+', text.lower()))


STAGES = ('lenses.json', 'pitches.json', 'groups.json', 'judge_key.json', 'scores.json')   # each built on the ones before


def _clear_after(folder, name):
    """A stage rewritten: every stage built on it is of another set - removed, never read stale."""
    for later in STAGES[STAGES.index(name) + 1:]:
        (folder / later).unlink(missing_ok=True)


def _items(value, what, keys):
    items = json_arg(value)
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
        raise StudioError('INPUT_INVALID', f'{what} are a list of {{{", ".join(keys)}}}')
    for i in items:
        bad = [k for k in keys if not isinstance(i.get(k), str) or not i[k].strip()]
        if bad:
            raise StudioError('INPUT_INVALID', f"{what[:-1]} {i.get(keys[0])}: {bad} must be text")
    return items


def _sha(*texts):
    import hashlib
    return hashlib.sha256('\x1f'.join(texts).encode('utf-8')).hexdigest()[:16]


def brief(project):
    """Open a pitch round and write what the agent reads: the request, the key message, every fact, example lenses
    (examples only - the lenses are the agent's, from this topic's facts) and the user's story taste as a reference."""
    from .story import _brief
    from .decisions import envelope
    from .taste_picks import prompt_block
    path = project_dir(project)
    request, key_message, length = _brief(path)
    facts = (envelope(path, 'facts') or {}).get('body') or (read_json(path / 'sources.json') if (path / 'sources.json').is_file() else {})
    claims = facts.get('claims') or []
    if not claims:
        raise StudioError('INPUT_INVALID', 'pitches are written from the facts: propose the facts layer first')
    examples = read_json(LENS_EXAMPLES)['examples'] if LENS_EXAMPLES.is_file() else []
    out = project_dir(path) / PITCHES / f'p{len(_rounds(path)) + 1:02d}'
    out.mkdir(parents=True, exist_ok=True)
    text = '\n'.join(filter(None, [
        f'# Pitches for a {length} s vertical explainer: {request}',
        f'What it must leave the viewer with: {key_message}',
        '## Facts (sourced; a pitch says nothing beyond them)\n' + '\n'.join(f"- {c['claim_id']}: {c['text']}" for c in claims),
        f"## 1. Lenses: name {LENSES_MIN}-{LENSES_MAX} angles this topic can be seen from - questions only this topic's facts "
        'answer, each a different way in. Examples of the kind (never copy them as they are; most topics need their own):\n'
        + '\n'.join(f'- {e}' for e in examples)
        + f'\nThen: studio story pitch-lenses --project {path} --round {out.name} --lenses <file> ([{{lens_id, question}}])',
        f"## 2. Pitches: for EACH lens, in its own pass (a fresh start each time, the other lenses out of mind), write "
        f"{PER_LENS[0]}-{PER_LENS[1]} pitches: opening - what the viewer SEES happen in the first seconds (an event, never a "
        'question or a line); question - what they now want to know; stake - who loses what if it goes wrong (it comes '
        "from the pitch, not from a list). Bad: 'opening: have you ever wondered how a lift knows where to stop?'. Good: "
        "'opening: the lift doors open on a bare shaft wall - it stopped between floors; question: why did it stop there?; "
        "stake: a rider late for work'."
        f'\nThen per lens: studio story pitch-add --project {path} --round {out.name} --pitches <file> '
        '([{pitch_id, lens, opening, question, stake}])',
        f'## 3. Group the alike: studio story pitch-group --project {path} --round {out.name} --groups <file> '
        f'({{group_id: {{label, pitch_ids}}}}; every pitch in one group, at least {GROUPS_MIN} groups)',
        f'## 4. Tournament: studio story pitch-judge-pack, {MEETINGS} blind pairs per pitch for 3 judges, then '
        'studio story pitch-judged --verdicts <file>; then studio story directions',
        ('## The user so far (a reference, not a rule)\n' + prompt_block(['story'])) if prompt_block(['story']) else '',
    ]))
    (out / 'brief.md').write_text(text + '\n', encoding='utf-8')
    return {'round': out.name, 'brief': str(out / 'brief.md'), 'claims': len(claims),
            'next': f'studio story pitch-lenses --project {path} --round {out.name} --lenses <file>'}


def lenses(project, value, rnd=None):
    """The agent's lenses for this topic: [{lens_id, question}], LENSES_MIN-LENSES_MAX of them, no two asking one
    question (LENS_SAME of their words shared)."""
    folder = _round(project_dir(project), rnd)
    items = _items(value, 'lenses', ('lens_id', 'question'))
    if not LENSES_MIN <= len(items) <= LENSES_MAX:
        raise StudioError('INPUT_INVALID', f'lenses are a list of {LENSES_MIN}-{LENSES_MAX} {{lens_id, question}}')
    ids = [i['lens_id'] for i in items]
    if len(set(ids)) != len(ids):
        raise StudioError('INPUT_INVALID', 'every lens needs its own lens_id')
    for i in items:
        if len(str(i.get('question') or '')) < 8:
            raise StudioError('INPUT_INVALID', f"lens {i['lens_id']}: its question is a sentence a viewer could ask")
    for n, a in enumerate(items):
        for b in items[n + 1:]:
            wa, wb = _words(a['question']), _words(b['question'])
            if wa and wb and len(wa & wb) / min(len(wa), len(wb)) >= LENS_SAME:
                raise StudioError('INPUT_INVALID', f"LENS_REPEATED: {a['lens_id']} and {b['lens_id']} ask one question - make one of them another way in")
    write_json(folder / 'lenses.json', [{'lens_id': i['lens_id'], 'question': i['question']} for i in items])
    if (folder / 'pitches.json').is_file():   # pitches of a lens no longer named go with it; the rest is regrouped
        kept = [p for p in read_json(folder / 'pitches.json') if p['lens'] in set(ids)]
        write_json(folder / 'pitches.json', kept)
    _clear_after(folder, 'pitches.json')
    return {'round': folder.name, 'lenses': ids,
            'next': f'write each lens\'s pitches in its own pass: studio story pitch-add --project {project} --round {folder.name} --pitches <file>'}


def add(project, value, rnd=None):
    """Pitches for one or more lenses, added to the round (a pitch_id given again replaces that pitch). Each opens on
    something seen (an event, not a question), asks a question and names a stake."""
    from .story_core import SPOKEN_ONLY
    folder = _round(project_dir(project), rnd)
    if not (folder / 'lenses.json').is_file():
        raise StudioError('INPUT_INVALID', 'name the lenses first (studio story pitch-lenses)')
    known = {lens['lens_id'] for lens in read_json(folder / 'lenses.json')}
    items = _items(value, 'pitches', ('pitch_id', 'lens', 'opening', 'question', 'stake'))
    for p in items:
        if p['lens'] not in known:
            raise StudioError('INPUT_INVALID', f"pitch {p['pitch_id']}: lens {p['lens']} is not one of {sorted(known)}")
        if p['opening'].strip().endswith(SPOKEN_ONLY):
            raise StudioError('INPUT_INVALID', f"HOOK_UNSEEN: pitch {p['pitch_id']} opens on a question - open on what the viewer "
                                               'sees happen; the question goes in question')
    file = folder / 'pitches.json'
    kept = {p['pitch_id']: p for p in (read_json(file) if file.is_file() else [])}
    kept.update({p['pitch_id']: {k: p[k] for k in ('pitch_id', 'lens', 'opening', 'question', 'stake')} for p in items})
    write_json(file, list(kept.values()))
    _clear_after(folder, 'pitches.json')   # new pitches: the grouping and the tournament are of another set
    return {'round': folder.name, 'pitches': len(kept)}


def _pitches(folder):
    file = folder / 'pitches.json'
    if not file.is_file():
        raise StudioError('INPUT_INVALID', 'no pitches yet (studio story pitch-add)')
    return {p['pitch_id']: p for p in read_json(file)}


def group(project, value, rnd=None):
    """Group the alike: {group_id: {label, pitch_ids}} - every pitch in exactly one group, at least GROUPS_MIN groups;
    the pitch set itself is checked here (PITCHES_MIN in all, PER_LENS per lens)."""
    folder = _round(project_dir(project), rnd)
    pitches = _pitches(folder)
    lens_ids = [lens['lens_id'] for lens in read_json(folder / 'lenses.json')]
    orphans = sorted(i for i, p in pitches.items() if p['lens'] not in lens_ids)
    if orphans:
        raise StudioError('INPUT_INVALID', f'pitches {orphans} name lenses no longer named')
    counts = {lens: sum(p['lens'] == lens for p in pitches.values()) for lens in lens_ids}
    thin = {lens: c for lens, c in counts.items() if not PER_LENS[0] <= c <= PER_LENS[1]}
    if len(pitches) < PITCHES_MIN or thin:
        raise StudioError('INPUT_INVALID', f'{len(pitches)} pitches (at least {PITCHES_MIN}), per lens {counts} '
                                           f'({PER_LENS[0]}-{PER_LENS[1]} each)', recovery='add pitches for the thin lenses (studio story pitch-add)')
    groups = json_arg(value)
    if not isinstance(groups, dict) or len(groups) < GROUPS_MIN:
        raise StudioError('INPUT_INVALID', f'groups are {{group_id: {{label, pitch_ids}}}}, at least {GROUPS_MIN}')
    seen = [i for g in groups.values() for i in g.get('pitch_ids') or []]
    twice = sorted({i for i in seen if seen.count(i) > 1})
    unknown = sorted(set(seen) - set(pitches))
    left = sorted(set(pitches) - set(seen))
    if twice or unknown or left or any(not isinstance(g, dict) or not str(g.get('label') or '').strip() for g in groups.values()):
        raise StudioError('INPUT_INVALID', f'every pitch in exactly one labelled group: twice {twice}, unknown {unknown}, in none {left}')
    big = [gid for gid, g in groups.items() if 2 * len(g['pitch_ids']) > len(pitches)]
    if big:   # its pitches meet only others: more than half of all leaves some of them short of MEETINGS
        raise StudioError('INPUT_INVALID', f"group {big[0]} holds {len(groups[big[0]]['pitch_ids'])} of {len(pitches)} pitches - over half, "
                                           'they cannot each meet pitches of other groups; split it')
    write_json(folder / 'groups.json', {gid: {'label': g['label'], 'pitch_ids': list(g['pitch_ids'])} for gid, g in groups.items()})
    _clear_after(folder, 'groups.json')
    return {'round': folder.name, 'groups': {gid: len(g['pitch_ids']) for gid, g in groups.items()},
            'next': f'studio story pitch-judge-pack --project {project} --round {folder.name}'}


def _group_of(folder):
    file = folder / 'groups.json'
    if not file.is_file():
        raise StudioError('INPUT_INVALID', 'group the pitches first (studio story pitch-group)')
    return {i: gid for gid, g in read_json(file).items() for i in g['pitch_ids']}, read_json(file)


def pitch_text(p):
    return f"{p['opening'].rstrip('.')}. {p['question'].rstrip('?')}? At stake: {p['stake']}"


def _schedule_once(ids, group_of, meetings, rng):
    count = {i: 0 for i in ids}
    size = {g: sum(group_of[i] == g for i in ids) for g in set(group_of.values())}
    pairs = set()
    while True:   # pair the neediest first, from the largest group (its pitches have the fewest partners)
        open_ = [i for i in ids if count[i] < meetings]
        added = False
        for a in sorted(open_, key=lambda i: (count[i], -size[group_of[i]], rng.random())):
            if count[a] >= meetings:
                continue
            others = [b for b in ids if b != a and group_of[b] != group_of[a] and count[b] < meetings
                      and (a, b) not in pairs and (b, a) not in pairs]
            if others:
                b = min(others, key=lambda i: (count[i], -size[group_of[i]], rng.random()))
                pairs.add((a, b)); count[a] += 1; count[b] += 1; added = True
        if not added:
            return sorted(pairs), sorted(i for i in ids if count[i] < meetings)


def schedule(ids, group_of, meetings=MEETINGS, seed=0, tries=50):
    """(pairs [(a, b)], short [ids]): every pitch meets `meetings` others of other groups, no pair twice; a greedy pass
    can strand a few, so it is tried again from other orders and the fullest kept. short: pitches left under."""
    rng = random.Random(seed)
    best = None
    for _ in range(tries):
        pairs, short = _schedule_once(ids, group_of, meetings, rng)
        if best is None or len(short) < len(best[1]):
            best = (pairs, short)
        if not short:
            break
    return best


def judge_pack(project, rnd=None, seed=None):
    """Blind pairs between pitches of different groups, MEETINGS per pitch, for the judges; which side is which is kept
    beside the judge folder, never in it."""
    from .story_judge import JUDGES, write_pairs
    folder = _round(project_dir(project), rnd)
    pitches = _pitches(folder)
    group_of, groups = _group_of(folder)
    rng = random.Random(f'{project_dir(project).name}:{folder.name}:{seed}')
    pairs, short = schedule(sorted(pitches), group_of, seed=rng.random())
    files, key = [], {}
    for n, (a, b) in enumerate(pairs, 1):
        if rng.random() < 0.5:
            a, b = b, a
        pair = f'pair_{n:02d}'
        files.append((pair, pitch_text(pitches[a]), pitch_text(pitches[b])))
        key[pair] = {'A': a, 'B': b, 'sha': _sha(pitch_text(pitches[a]), pitch_text(pitches[b]))}
    write_pairs(folder / 'judge', files, QUESTION)
    write_json(folder / 'judge_key.json', {'schema_version': 1, 'packed_at': now(), 'judges': JUDGES, 'pairs': key,
                                           'groups_sha': _sha(json.dumps(groups, sort_keys=True))})
    _clear_after(folder, 'judge_key.json')
    return {'round': folder.name, 'pairs': len(key), 'brief': str(folder / 'judge' / 'brief.md'),
            **({'short': short, 'warning': f'{short} meet fewer than {MEETINGS} pitches of other groups (their score is a share of fewer pairs)'} if short else {}),
            'next': f'give judge/brief.md and the pair files to {JUDGES} judges who know nothing of the pitches; gather '
                    f'[{{judge, pair, winner, why}}] in one file: studio story pitch-judged --project {project} --round {folder.name} --verdicts <file>'}


def judged(project, verdicts, rnd=None):
    """Score each pitch: the share of its pairs it won, each pair settled by the majority of its judges."""
    from .story_judge import votes
    folder = _round(project_dir(project), rnd)
    pitches = _pitches(folder)
    if not (folder / 'judge_key.json').is_file():
        raise StudioError('INPUT_INVALID', 'no pairs packed for these pitches', recovery=f'studio story pitch-judge-pack --project {project} --round {folder.name}')
    packed = read_json(folder / 'judge_key.json')
    key = packed['pairs']
    _, groups = _group_of(folder)
    if (any(k[s] not in pitches for k in key.values() for s in 'AB')
            or any(k['sha'] != _sha(pitch_text(pitches[k['A']]), pitch_text(pitches[k['B']])) for k in key.values())
            or packed.get('groups_sha') != _sha(json.dumps(groups, sort_keys=True))):
        raise StudioError('DECISION_STALE', 'the pitches or their groups changed since the pairs were packed', recovery='studio story pitch-judge-pack again')
    by_pair = votes(key, json_arg(verdicts))
    scores = {i: {'wins': 0, 'meetings': 0, 'why_lost': []} for i in pitches}
    for pair, k in key.items():
        cast = by_pair[pair]
        a_votes = sum(v['winner'] == 'A' for v in cast)
        winner, loser = (k['A'], k['B']) if a_votes * 2 > len(cast) else (k['B'], k['A'])
        scores[winner]['wins'] += 1
        for side in (k['A'], k['B']):
            scores[side]['meetings'] += 1
        win_side = 'A' if k['A'] == winner else 'B'
        scores[loser]['why_lost'] += [v['why'] for v in cast if v['winner'] == win_side and v.get('why')]
    for row in scores.values():
        row['score'] = round(row['wins'] / row['meetings'], 3) if row['meetings'] else 0.0
    write_json(folder / 'scores.json', {'schema_version': 1, 'judged_at': now(), 'scores': scores})
    ranked = sorted(scores, key=lambda i: (-scores[i]['score'], i))
    return {'round': folder.name, 'top': [{'pitch_id': i, **{k: scores[i][k] for k in ('wins', 'meetings', 'score')}} for i in ranked[:6]],
            'next': f'studio story directions --project {project}'}


def best(project, n=3, used=()):
    """The best pitch of each group, the n best groups: [{pitch_id, opening, question, stake, lens, group, label, score}].
    Refused before the tournament: the directions are the judged pitches, never a draw."""
    path = project_dir(project)
    rounds = _rounds(path)
    if not rounds or not (rounds[-1] / 'scores.json').is_file():
        raise StudioError('INPUT_INVALID', f"STORY_PITCHES_UNJUDGED: the directions are the best judged pitches - "
                                           f"{'no pitch round yet' if not rounds else rounds[-1].name + ' is not judged'}",
                          recovery=f'studio story pitch-brief --project {path}, then lenses, pitches, groups and the tournament')
    folder = rounds[-1]
    pitches = _pitches(folder)
    group_of, groups = _group_of(folder)
    scores = read_json(folder / 'scores.json')['scores']
    top = {}
    for i in sorted((i for i in pitches if i not in set(used)), key=lambda i: (-scores[i]['score'], i)):   # best first: the first seen of a group is its best
        top.setdefault(group_of[i], i)
    chosen = sorted(top.values(), key=lambda i: (-scores[i]['score'], i))[:n]
    if len(chosen) < n:
        raise StudioError('INPUT_INVALID', f'{folder.name} has {len(chosen)} groups with a pitch not yet made a direction - fewer than {n}',
                          recovery=f'a new pitch round: studio story pitch-brief --project {path}')
    return folder.name, [{**pitches[i], 'group': group_of[i], 'label': groups[group_of[i]]['label'], 'score': scores[i]['score']}
                         for i in chosen]
