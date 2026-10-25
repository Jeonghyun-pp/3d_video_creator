"""The story contract (story.json): where the reel hooks, turns and peaks, settled before the script.

On the decision ladder the story is a layer (decisions.py: brief -> facts -> story -> script); its approval writes
story.json. A delegated run has no ladder: the story is still the user's pick (story pick, their words), written here.
The tools read story.json: the script's lines name its beats, the shots follow them, the reel flow checks read where
the climax is (studio/reel_flow.py).
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil

from .common import StudioError, json_arg, now, read_json, write_json
from .project import load_project, project_dir

STORY_FILE = 'story.json'
DIRECTIONS = 'story/directions'   # rNN/plan.json (the draw), dK.json (a written direction), dK.png (its opening picture), sheet.html
DIRECTION_COUNT = 3   # the user compares this many: a third adds a real alternative, a fourth tires the choice (no study
                      # gives the number; 3-4 is the usual size of an options page, and the user asked to spend little time)
FACTS_SHOWN = 12      # claims a direction brief quotes: enough to build on, short enough to read


def load(path):
    file = project_dir(path) / STORY_FILE
    return read_json(file) if file.is_file() else None


def body_of(record):
    return {k: record[k] for k in ('logline', 'direction', 'pitch', 'thread', 'stake', 'beats') if k in record} if record else None


def materialize(path, body, decided_by='user', user_words=None, picked=None):
    """story.json: the body and who settled it (the ladder's approval, or a pick in the user's words)."""
    path = project_dir(path)
    record = {'schema_version': 1, **body, 'decided_by': decided_by, **({'user_words': user_words} if user_words else {}),
              **({'picked': picked} if picked else {}), 'written_at': now()}
    write_json(path / STORY_FILE, record)
    return [STORY_FILE]


CHAIN_UNSET = ("STORY_CHAIN_UNSET: the story names no thread and no beat says how it follows the last (story_core.chain_lint, "
               "2026-10-19) - it stands as approved; a new story or a re-plan writes them (references/story.md, the thread and the chain)")
STAKE_UNSET = ("STORY_STAKE_UNSET: the story names no stake and no beat says what is seen or what it comes back to "
               "(story_core.stake_lint, 2026-10-20) - it stands as approved; a new story or a re-plan writes them "
               "(references/story.md, the stake and the screen)")
UNSET = {'chain': CHAIN_UNSET, 'stake': STAKE_UNSET}   # one per story_core.GROUPS


def unset_lines(groups):
    return [UNSET[g] for g in sorted(groups)]


def missing(path):
    """STORY_MISSING (a warning): a project with shots and no story - made before the story existed, or skipped.
    STORY_<GROUP>_UNSET (a warning): a story written before a rule group (story_core.GROUPS) - its approval stands."""
    path = project_dir(path)
    record = load(path)
    if record is not None:
        from .story_core import unset_groups
        return unset_lines(unset_groups(body_of(record)))
    if not load_project(path).get('shots'):
        return []
    return ["STORY_MISSING: no story.json - the shots follow the sentences with nothing saying where the reel hooks, turns and "
            "peaks (studio story directions; on the ladder the story layer)"]


def check(project):
    """The story's lint as the ladder runs it, for a project whatever its route (delegated or not)."""
    from .decisions import validate_body
    from .story_core import lint_story, load_axes
    path = project_dir(project)
    record = load(path)
    if record is None:
        return {'story': None, 'errors': [], 'warnings': missing(path)}
    body = body_of(record)
    validate_body('story', body)
    from .story_core import unset_groups
    unset = unset_groups(body)
    errors, warnings = lint_story(body, load_project(path)['output'].get('target_seconds'), load_axes(), off=unset)
    return {'story': str(path / STORY_FILE), 'errors': errors, 'warnings': warnings + unset_lines(unset)}


# --- directions: the code draws, the agent writes, the user picks -------------------------------------------------

def _rounds(path):
    root = project_dir(path) / DIRECTIONS
    return sorted(p for p in root.glob('r[0-9]*') if (p / 'plan.json').is_file()) if root.is_dir() else []


def _round(path, rnd=None):
    rounds = _rounds(path)
    if not rounds:
        raise StudioError('INPUT_INVALID', 'no story directions yet (studio story directions)')
    if rnd is None:
        return rounds[-1]
    found = next((r for r in rounds if r.name == rnd), None)
    if found is None:
        raise StudioError('INPUT_INVALID', f'no direction round {rnd} (rounds: {[r.name for r in rounds]})')
    return found


def _claims(path):
    """(the first FACTS_SHOWN claims, how many more there are)."""
    from .decisions import envelope
    facts = (envelope(path, 'facts') or {}).get('body') or (read_json(path / 'sources.json') if (path / 'sources.json').is_file() else {})
    claims = [c['text'] for c in facts.get('claims', [])]
    return claims[:FACTS_SHOWN], max(0, len(claims) - FACTS_SHOWN)


def _brief(path):
    """(request, key message, length s): the brief layer's body as it stands (proposed or approved - the directions are
    drawn before the ladder is approved), else the project's (a run off the ladder)."""
    from .decisions import envelope
    project = load_project(path)
    layer = (envelope(path, 'brief') or {}).get('body') or {}
    kept = project.get('brief') or {}
    return (layer.get('topic') or kept.get('request') or '', layer.get('key_message') or kept.get('key_message') or '',
            layer.get('length_s') or project['output'].get('target_seconds'))


def _brief_text(path, slot, axes_table, taste=''):
    """The brief for one direction: the pitch it grew from (story_pitch), the facts and the story rules."""
    from .story_core import rule
    request, key_message, length = _brief(path)
    pitch = slot['pitch']
    threads = ', '.join(sorted(((axes_table.get('thread') or {}).get('values') or {})))
    claims, more = _claims(path)
    low, high = rule('CLIMAX_BAND')
    return '\n'.join(filter(None, [
        f"Write story direction {slot['slot']} of {DIRECTION_COUNT} for a {length} s vertical explainer: {request}",
        f"What it must leave the viewer with: {key_message}",
        'Facts it may use (sourced; say nothing beyond them):\n' + '\n'.join(f'- {c}' for c in claims)
        + (f'\n- ... and {more} more (studio decide show --project {path} --layer facts)' if more else '') if claims else '',
        f"This direction is a pitch that won its blind tournament (group: {pitch['label']}) - write the story it promises, "
        f"not a safe average:\n- opening: {pitch['opening']}\n- question: {pitch['question']}\n- at stake: {pitch['stake']}",
        taste,
        'First write the story freely in 4-6 plain sentences: what the viewer sees first, what they wonder, where it gets '
        'hard, where it turns, the moment everything builds to, how it ends. Only then turn it into the story body '
        '(schemas/studio-v1/decision.schema.json $defs/story) with pitch {pitch_id: ' + repr(pitch['pitch_id']) + ', line, group}.',
        "Then hold it together: name the thread (thread {kind, carrier, carrier_ids}) - the one thing the viewer keeps hold "
        f"of, of a kind in library/story_axes.json thread ({threads}) that fits the pitch; and make every beat after the hook follow the one before it "
        "(link): so - it happens because of the last; but - it turns against the last (a doubt, a complication, what goes "
        f"wrong); and - only to name the next stop, never more than {rule('AND_RUN_MAX')} in a row - a 'but' for every "
        f"{rule('BUT_EVERY_S'):.0f} s of the reel. Each beat says the question it leaves (raises) and which earlier questions it answers "
        "(answers); the hook's question is the reel's - answered late, at the climax, and the last beat comes back to it. "
        "Bad: 'the lever moves, and the pulley moves, and the chain moves' (any order would do). Good: 'press the lever - but "
        "nothing shifts; so what is missing? - the pulley has moved, but the chain still sits there; so turn the crank - the "
        "chain follows the pulley onto the next sprocket' (each beat needs the one before).",
        "Make the viewer care and see it (story_core.stake_lint): the hook is an event on screen, not a question asked "
        "(on_screen {event, kind: contradiction | failure | payoff_first | anomaly}; its question goes in raises) - every "
        "winning plan of the 2026-10-20 test opened on something happening, the losers on a sentence. At least one 'but' "
        "beat goes wrong on screen (on_screen kind failure or change), not only in words. Name what is at stake "
        "(stake {who, what_is_at_risk, closed_by}) - the pitch's own, never one added to fit a list - and close on it: the last "
        "beat is stake.closed_by and returns_to the hook - the opening picture again, now understood. No more than "
        f"{rule('TOUR_RUN_MAX')} beats in a row that neither raise nor answer a question (a tour). Bad hook: 'have you ever "
        "wondered how a lift knows where to stop?' (a line). Good hook: 'the lift doors open on a bare shaft wall - it "
        "stopped between floors' (an event; raises: why did it stop there?).",
        f"Shape: the first beat is the hook, over in {rule('HOOK_MAX_S'):.0f} s; at least one turn or climax, the climax at "
        f'{low}-{high} of the reel; tension that rises and falls (not one level); no more than '
        f"{rule('EXPLAIN_RUN_MAX')} explaining beats in a row; shares that add up to 1; a picture_idea for every beat "
        '(the picture that carries it, in words). Rules and reasons: studio/story_core.STORY_RULES.',
        f"Then: studio story add --project {path} --round <round> --slot {slot['slot']} --body <file> --prose <file> "
        "(an opening picture is optional: --picture <file>, or --paint)"]))


def directions(project, n=DIRECTION_COUNT):
    """The n best judged pitches of n different groups (story_pitch.best) become the round's directions, each briefed
    for the agent. Nothing is decided: the directions are judged against the references next, and the user picks."""
    from .story_core import load_axes
    from .story_pitch import best
    path = project_dir(project)
    rounds = _rounds(path)
    used = [s['pitch']['pitch_id'] for r in rounds for s in read_json(r / 'plan.json')['slots'] if 'pitch' in s]
    pitch_round, chosen = best(path, n, used)   # a new round goes on to pitches no earlier round made directions
    out = project_dir(path) / DIRECTIONS / f'r{len(rounds) + 1:02d}'
    out.mkdir(parents=True, exist_ok=True)
    axes_table = load_axes()
    slots = []
    for k, p in enumerate(chosen, 1):
        slot = {'slot': k, 'role': 'pitch', 'pitch': p}
        slot['brief'] = _brief_text(path, slot, axes_table)
        slots.append(slot)
    plan = {'schema_version': 2, 'round': out.name, 'pitch_round': pitch_round, 'drawn_at': now(), 'slots': slots}
    write_json(out / 'plan.json', plan)
    return {'round': out.name, 'slots': [{k: s[k] for k in ('slot', 'pitch', 'brief')} for s in slots],
            'next': f'write each: studio story add --project {path} --round {out.name} --slot <k> --body <file> --prose <file>; then judge '
                    f'them blind before the user sees them: studio story judge-pack --project {path} --round {out.name}',
            'artifacts': [str(out / 'plan.json')]}


def _text(value):
    """Text given inline or as a file."""
    try:
        if Path(str(value)).is_file():
            return Path(value).read_text(encoding='utf-8').strip()
    except OSError:   # a long text is no file name
        pass
    return str(value).strip()


def _hook_prompt(body, shape):
    hook = body['beats'][0]
    return (f"One {shape} frame, the opening picture of a short explainer video: {hook.get('picture_idea') or hook['what_happens']}. "
            f"The story it opens: {body['logline']}. A striking, readable picture - no text, no letters, no labels.")


def add(project, rnd, slot, body, prose=None, picture=None, paint=False, runner=None):
    """A written direction: its body (checked against the schema, its draw and the story rules) and its opening picture
    (a given image, or one painted by the image tool from the hook beat's picture idea)."""
    from .decisions import validate_body
    from .story_core import lint_story, load_axes
    path = project_dir(project)
    folder = _round(path, rnd)
    plan = read_json(folder / 'plan.json')
    drawn = next((s for s in plan['slots'] if s['slot'] == int(slot)), None)
    if drawn is None:
        raise StudioError('INPUT_INVALID', f"{folder.name} has slots {[s['slot'] for s in plan['slots']]}, not {slot}")
    if plan.get('picked'):
        raise StudioError('DECISION_STALE', f"{folder.name} was picked ({plan['picked']['slot']}); draw a new round")
    body = deepcopy(json_arg(body))
    validate_body('story', body)
    if 'axes' in drawn and body.get('direction') != drawn['axes']:   # a round drawn on the axes (before 2026-10-20's pitches)
        raise StudioError('INPUT_INVALID', f"STORY_DIRECTION_DIFFERS: slot {slot} was drawn as {drawn['axes']}, the body says "
                                           f"{body.get('direction')} - write the drawn direction (a mix is the user's, at story pick --body)")
    if 'pitch' in drawn and (body.get('pitch') or {}).get('pitch_id') != drawn['pitch']['pitch_id']:
        raise StudioError('INPUT_INVALID', f"STORY_PITCH_DIFFERS: slot {slot} is pitch {drawn['pitch']['pitch_id']}, the body says "
                                           f"{(body.get('pitch') or {}).get('pitch_id')} - write the slot's pitch (a mix is the user's, at story pick --body)")
    errors, warnings = lint_story(body, load_project(path)['output'].get('target_seconds'), load_axes())
    before = folder / f"d{drawn['slot']}.json"
    lost = (read_json(before).get('judged') or {}) if before.is_file() else {}
    if lost and not lost.get('won') and read_json(before).get('rewrite'):
        raise StudioError('INPUT_INVALID', f"direction {slot} was rewritten once after losing its pairs and lost again - it stays as "
                                           'judged (the sheet folds it); the next best pitches make a new round',
                          recovery='studio story directions (the next pitches of the round), else studio story pitch-brief')
    from .story_judge import story_text
    record = {'slot': drawn['slot'], 'role': drawn['role'], 'body': body, 'errors': errors, 'warnings': warnings, 'written_at': now(),
              **({'prose': _text(prose)} if prose else {})}
    # the judges read the story as told (story_text); the same story written again - only its picture added - keeps
    # their verdict, and is no rewrite: a verdict belongs to the text it was given, not to the act of writing
    unchanged = before.is_file() and lost and story_text(read_json(before)) == story_text(record)
    if unchanged:
        record['judged'] = lost
    record.update({'rewrite': {'after': lost['why'][:6]}} if lost and not lost.get('won') and not unchanged
                  else {'rewrite': read_json(before)['rewrite']} if before.is_file() and read_json(before).get('rewrite') else {})
    image = folder / f"d{drawn['slot']}.png"
    if picture:
        shutil.copyfile(picture, image)
        record['picture'] = {'path': image.name, 'source': 'given'}
    elif paint:
        from .picture3d import paint_into
        shape = 'vertical 9:16' if load_project(path)['output']['height'] > load_project(path)['output']['width'] else 'wide 16:9'
        made, how, tail = paint_into(image, [], _hook_prompt(body, shape), runner=runner, label=f'story {folder.name} d{slot}')
        if made is None:
            warnings.append(f'STORY_PICTURE_MISSING: the opening picture was not painted ({tail[-200:]}); the card shows words only')
        else:
            record['picture'] = {'path': image.name, 'source': 'painted', 'found_by': how}
    write_json(folder / f"d{drawn['slot']}.json", record)
    return {'round': folder.name, 'slot': drawn['slot'], 'errors': errors, 'warnings': warnings,
            **({'rewrite': f"recorded as the one rewrite after losing its pairs ({len(record['rewrite']['after'])} judges' reasons kept)"} if record.get('rewrite') else {}),
            'artifacts': [str(folder / f"d{drawn['slot']}.json")]}


def _written(folder):
    return [read_json(p) for p in sorted(folder.glob('d[0-9]*.json'))]


def _curve(beats, width=220, height=44):
    from .story_core import positions
    points = ' '.join(f"{at * width:.0f},{height - 4 - b['tension'] * (height - 8):.0f}" for b, at in zip(beats, positions(beats)))
    return (f"<svg viewBox='0 0 {width} {height}' width='{width}' height='{height}' role='img' aria-label='tension'>"
            f"<polyline points='{points}' fill='none' stroke='currentColor' stroke-width='2'/></svg>")


def sheet(project, rnd=None):
    """One page: the round's written directions side by side (opening picture, logline, beats, tension), the story
    rules' findings, and how to answer. The user says a number and a line of why."""
    from .story_core import stories_one_idea
    from .storyboard_brief import CSS, _e
    path = project_dir(project)
    folder = _round(path, rnd)
    written = _written(folder)
    if not written:
        raise StudioError('INPUT_INVALID', f'{folder.name}: no direction written yet (studio story add)')
    same = stories_one_idea(written)
    labels = {'taste': '지금까지 고른 취향에 가까운 안', 'contrast': '취향과 가장 다른 안', 'explore': '아직 안 해 본 방식',
              'pitch': '피치 토너먼트에서 고른 안'}
    cards, folded = [], []
    judged_any = any(d.get('judged') for d in written)
    winners = [d for d in written if (d.get('judged') or {}).get('won')]
    for d in written:
        b = d['body']
        figure = (f"<img src='{_e(d['picture']['path'])}' alt='opening of direction {d['slot']}' style='width:100%;aspect-ratio:9/16;object-fit:cover;border-radius:8px'>"
                  if d.get('picture') else "<div class='miss'>그림 없음</div>")
        joins = {'so': '그래서', 'but': '그런데', 'and': '그리고'}
        beats = ''.join(f"<li>{('<span class=chip>' + joins[x['link']] + '</span> ') if x.get('link') in joins else ''}"
                        f"<b>{_e(x['function'])}</b> · {_e(x['what_happens'])}"
                        f"{('<div class=sub>? ' + _e(x['raises']) + '</div>') if x.get('raises') else ''}</li>" for x in b['beats'])
        thread = b.get('thread')
        thread_line = (f"<p class='sub'>줄기: <b>{_e(thread['kind'])}</b> · {_e(thread['carrier'])}</p>" if thread else '')
        issues = ''.join(f"<div class='warn'>{_e(x)}</div>" for x in d['errors'] + d['warnings'])
        axes = (f"{(b.get('pitch') or {}).get('group', '')} · {(b.get('pitch') or {}).get('line', '')}" if b.get('pitch')
                else ', '.join(f'{k} {v}' for k, v in (b.get('direction') or {}).items()))
        verdict = d.get('judged')
        mark = (f"<span class='chip'>기준 영상과 짝 비교 {verdict['wins']}/{verdict['pairs']} 승</span>" if verdict else '')
        if judged_any and not verdict:
            mark = "<span class='chip'>짝 비교 대기</span>"
        target = folded if winners and verdict and not verdict['won'] else cards
        target.append(f"<section class='card'><div class='head'><b>{d['slot']}안</b>{mark}<span class='chip'>{_e(labels.get(d['role'], d['role']))}</span>"
                     f"<span class='chip'>{_e(axes)}</span></div><div style='display:grid;grid-template-columns:minmax(0,1fr) minmax(0,2fr);gap:14px'>"
                     f"<div>{figure}</div><div><p class='narr'>{_e(b['logline'])}</p>{('<p class=sub>' + _e(d['prose']) + '</p>') if d.get('prose') else ''}"
                     f"{thread_line}<ol>{beats}</ol><div class='sub'>긴장 곡선</div>{_curve(b['beats'])}{issues}</div></div></section>")
    page = (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>이야기 고르기</title><style>{CSS}</style></head><body><main><h1>이야기 {len(written)}안 — {folder.name}</h1>"
            "<p class='sub'><b>번호와 한 줄 이유를 말해 주세요</b> (예: \"2안, 실패로 시작하는 게 재밌어요\"). 섞고 싶으면 \"1안 시작에 3안 전개\"처럼 말해 주세요.</p>"
            + ''.join(f"<p class='warn'>{_e(x)}</p>" for x in same)
            + ('' if judged_any else "<p class='sub'>기준 영상과의 짝 비교 전입니다 (studio story judge-pack).</p>")
            + ("<p class='warn'>모든 안이 기준 영상과의 짝 비교에서 졌습니다.</p>" if judged_any and not winners
               and all(d.get('judged') for d in written) else '')
            + ''.join(cards)
            + (f"<details><summary>짝 비교에서 진 안 {len(folded)}개 (펼치기)</summary>{''.join(folded)}</details>" if folded else '')
            + '</main></body></html>')
    out = folder / 'sheet.html'
    out.write_text(page, encoding='utf-8')
    return {'round': folder.name, 'sheet': str(out), 'warnings': same, 'artifacts': [str(out)]}


def judge_pack(project, rnd=None, refs=None, seed=None):
    """Blind pairs of the round's written directions against the reference narrations (story_judge.pack)."""
    from .story_judge import pack
    folder = _round(project_dir(project), rnd)
    written = _written(folder)
    if not written:
        raise StudioError('INPUT_INVALID', f'{folder.name}: no direction written yet (studio story add)')
    todo = [d for d in written if not d.get('judged')] or written   # a rewrite is judged again; all judged: judge all again
    return {'round': folder.name, 'directions': [d['slot'] for d in todo], **pack(folder, todo, refs, seed=seed)}


def judge_record(project, verdicts, rnd=None):
    """The judges' verdicts onto each written direction (judged), STORY_LOSES on those that lost (story_judge.judged)."""
    from .story_judge import judged, loses_line
    folder = _round(project_dir(project), rnd)
    written = _written(folder)
    rows = judged(folder, written, json_arg(verdicts))
    out = []
    for d in written:
        row = rows.get(d['slot'])
        if row is None:
            continue
        d['judged'] = row
        d['warnings'] = [w for w in d['warnings'] if not w.startswith('STORY_LOSES')] + ([] if row['won'] else [loses_line(row)])
        write_json(folder / f"d{d['slot']}.json", d)
        out.append({'slot': d['slot'], 'wins': row['wins'], 'pairs': row['pairs'], 'won': row['won']})
    lost = [r['slot'] for r in out if not r['won']]
    return {'round': folder.name, 'directions': out,
            'next': (f"rewrite {lost} once from the judges' reasons (story add, the same slot), pack and judge again; then the sheet"
                     if lost else 'studio story sheet')}


def taste_axes(body):
    """What a story's pick says about the user's taste: its drawn axes (a story drawn on them), else how it opens and
    what it follows - the hook's on-screen kind (as 'opening', apart from the old axis 'hook') and the thread's kind."""
    if body.get('direction'):
        return body['direction']
    out = {'opening': (body['beats'][0].get('on_screen') or {}).get('kind'), 'thread': (body.get('thread') or {}).get('kind')}
    return {k: v for k, v in out.items() if v} or None


def _not_delegated(path, words):
    """The story is the user's pick, never a delegated run's: words that are the delegation's own (recorded on the
    project, or left by the launcher for the run) are the run speaking, not the user picking."""
    from .decisions import delegation_words
    if words.strip() in delegation_words(path):
        raise StudioError('INPUT_INVALID', "story pick takes the user's own answer to the directions sheet, not the delegation's words - "
                                           'a delegated run stops at the sheet (story pick is never delegated)',
                          recovery='show the sheet and wait for the user to say a number and why')


def _shown(folder, written):
    """The sheet the user picked from exists and is newer than every written direction it shows."""
    sheet_file = folder / 'sheet.html'
    newest = max((folder / f"d{d['slot']}.json").stat().st_mtime for d in written.values())
    if not sheet_file.is_file() or sheet_file.stat().st_mtime < newest:
        raise StudioError('DECISION_STALE', f'{folder.name}: the directions were not shown as they are now (story sheet first)',
                          recovery=f'studio story sheet --round {folder.name}, show it, then pick')


def pick(project, rnd, slot, user_words, body=None, migrate=False, proxy=None):
    """The user's direction becomes the story: on the decision ladder it is proposed and approved in their words; in a
    project off the ladder (a delegated run) it is written to story.json with their words. The pick goes to the taste
    with the directions passed over. body: the user's mix of directions, when they asked for one.

    Everything approve would refuse is checked before anything is written, and the ladder's propose + approve run under
    one hold that puts the old story back if either fails (decisions.settle): a failed pick leaves no proposed story for
    the script to depend on. A ladder adopted without the story keeps its shape unless migrate: taking a story there
    makes the script, and everything after it, wait for lines that name its beats (references/story.md, migration).

    proxy: who picked when the user handed the pick to a stand-in for a test (user_words: their words allowing it, never
    a pick of theirs) - recorded as such on the story and the round, and never taken into the user's taste. Why
    (2026-10-20): an overnight E2E the user asked for ("자고올테니 무료e2e끝내놓을수 있어?") with the pick left to a blind
    stand-in; a pick in their name would have been words they never said."""
    from .decisions import adopted, approve, envelope, gated, lint, parents_of, propose, settle, state, validate_body
    from .decisions import write as decisions_write
    from .generative.review import check_user_words
    from .story_core import lint_story, load_axes
    from .taste_picks import record
    words = check_user_words(user_words, 'story pick')
    path = project_dir(project)
    _not_delegated(path, words)
    folder = _round(path, rnd)
    plan = read_json(folder / 'plan.json')
    if plan.get('picked'):
        raise StudioError('DECISION_STALE', f"{folder.name} was already picked ({plan['picked']['slot']})")
    written = {d['slot']: d for d in _written(folder)}
    if int(slot) not in written:
        raise StudioError('INPUT_INVALID', f'{folder.name}: direction {slot} is not written (written: {sorted(written)})')
    _shown(folder, written)
    chosen = deepcopy(json_arg(body)) if body is not None else written[int(slot)]['body']
    validate_body('story', chosen)
    picked = {'round': folder.name, 'slot': int(slot), **({'mixed': True} if body is not None else {}), **({'proxy': proxy} if proxy else {})}
    if adopted(path):
        if not gated(path, 'story') and not migrate:
            raise StudioError('INPUT_INVALID', 'this ladder was adopted without a story: picking one now makes the script and every '
                                               'layer after it wait for lines that name its beats',
                              recovery='keep going without (STORY_MISSING warns), or pick with --migrate and then revise the '
                                       'script (story_beat_id on every line) and approve down the ladder (references/story.md)')
        errors = lint(path, 'story', chosen)['errors']
        if errors:
            raise StudioError('DECISION_INVALID', 'story: ' + '; '.join(errors[:6]), recovery='write the direction again (story add) or mix it right')
        for parent in parents_of(path, 'story'):
            if state(path, parent)['state'] != 'approved':
                raise StudioError('DECISION_UNAPPROVED', f'story is bound to {parent}, which is not approved', recovery=f'approve {parent} first')

        def steps():
            proposed = propose(path, 'story', chosen)
            return approve(path, 'story', words, proposed['rev'])
        settled = settle(path, 'story', steps, 'story_pick_undone', keep=(STORY_FILE,), detail={'round': folder.name, 'slot': int(slot)})
        how = {'ladder': settled['rev'], **({'migrated': True} if migrate and envelope(path, 'story') else {})}
    else:
        errors, _ = lint_story(chosen, load_project(path)['output'].get('target_seconds'), load_axes())
        if errors:
            raise StudioError('DECISION_INVALID', 'story: ' + '; '.join(errors[:6]), recovery='write the direction again (story add) or mix it right')
        # kept where every layer is (decisions/story.json, decided in the user's words), then story.json with the pick -
        # one hold: a record the story's rules refuse, or a failed write, leaves neither (decisions.settle)
        def steps():
            out = decisions_write(path, 'story', chosen, user_words=words, _picked=True)
            if out['state']['state'] != 'decided':
                raise StudioError('DECISION_INVALID', 'story: ' + '; '.join(out.get('errors', [])[:6] or [out['state']['reason']]),
                                  recovery='write the direction again (story add) or mix it right')
            return materialize(path, chosen, 'user', words, picked)
        how = {'written': settle(path, 'story', steps, 'story_pick_undone', keep=(STORY_FILE,), detail={'round': folder.name, 'slot': int(slot)})}
    passed = [t for k, d in written.items() if k != int(slot) for t in [taste_axes(d['body'])] if t]
    if taste_axes(chosen) and not proxy:   # a stand-in's pick says nothing of the user's taste
        record('story', taste_axes(chosen), passed, words, path, ref=f'{folder.name} d{slot}')
    plan['picked'] = {'slot': int(slot), 'user_words': words, 'at': now(), **({'mixed': True} if body is not None else {}),
                      **({'proxy': proxy} if proxy else {})}
    write_json(folder / 'plan.json', plan)
    return {'round': folder.name, 'picked': int(slot), **how, 'story': str(path / STORY_FILE)}


def register_commands(subparsers):
    parser = subparsers.add_parser('story', help='The story before the script: directions drawn on axes, picked by the user')
    commands = parser.add_subparsers(dest='story_command', required=True)
    p = commands.add_parser('check', help="story.json against the story rules (studio/story_core.STORY_RULES)")
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: check(a.project))
    from . import story_pitch
    p = commands.add_parser('pitch-brief', help='open a pitch round: what the agent reads before naming lenses and writing pitches')
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: story_pitch.brief(a.project))
    p = commands.add_parser('pitch-lenses', help="the agent's angles on this topic [{lens_id, question}]")
    p.add_argument('--project', required=True); p.add_argument('--round'); p.add_argument('--lenses', required=True)
    p.set_defaults(handler=lambda a: story_pitch.lenses(a.project, a.lenses, a.round))
    p = commands.add_parser('pitch-add', help='pitches for one or more lenses [{pitch_id, lens, opening, question, stake}]')
    p.add_argument('--project', required=True); p.add_argument('--round'); p.add_argument('--pitches', required=True)
    p.set_defaults(handler=lambda a: story_pitch.add(a.project, a.pitches, a.round))
    p = commands.add_parser('pitch-group', help='group the alike pitches {group_id: {label, pitch_ids}}')
    p.add_argument('--project', required=True); p.add_argument('--round'); p.add_argument('--groups', required=True)
    p.set_defaults(handler=lambda a: story_pitch.group(a.project, a.groups, a.round))
    p = commands.add_parser('pitch-judge-pack', help='blind pairs between pitches of different groups, for judges')
    p.add_argument('--project', required=True); p.add_argument('--round'); p.add_argument('--seed', type=int)
    p.set_defaults(handler=lambda a: story_pitch.judge_pack(a.project, a.round, a.seed))
    p = commands.add_parser('pitch-judged', help="the judges' verdicts onto the pitches: each pitch's share of pairs won")
    p.add_argument('--project', required=True); p.add_argument('--round'); p.add_argument('--verdicts', required=True)
    p.set_defaults(handler=lambda a: story_pitch.judged(a.project, a.verdicts, a.round))
    p = commands.add_parser('directions', help='the best judged pitches of different groups become the directions, each briefed')
    p.add_argument('--project', required=True); p.add_argument('--n', type=int, default=DIRECTION_COUNT)
    p.set_defaults(handler=lambda a: directions(a.project, a.n))
    p = commands.add_parser('add', help='a written direction: its story body, its prose and its opening picture')
    p.add_argument('--project', required=True); p.add_argument('--round'); p.add_argument('--slot', required=True, type=int)
    p.add_argument('--body', required=True); p.add_argument('--prose'); p.add_argument('--picture')
    p.add_argument('--paint', action='store_true', help='paint the opening picture with the image tool (Codex)')
    p.set_defaults(handler=lambda a: add(a.project, a.round, a.slot, a.body, a.prose, a.picture, a.paint))
    p = commands.add_parser('judge-pack', help='blind pairs of the written directions against reference narrations, for judges')
    p.add_argument('--project', required=True); p.add_argument('--round'); p.add_argument('--refs', help='reference narrations (default ~/.config/studio/story_refs.json)')
    p.add_argument('--seed', type=int)
    p.set_defaults(handler=lambda a: judge_pack(a.project, a.round, a.refs, a.seed))
    p = commands.add_parser('judged', help="the judges' verdicts [{judge, pair, winner, why}] onto the written directions")
    p.add_argument('--project', required=True); p.add_argument('--round'); p.add_argument('--verdicts', required=True)
    p.set_defaults(handler=lambda a: judge_record(a.project, a.verdicts, a.round))
    p = commands.add_parser('sheet', help='the directions side by side, one page, for the user to pick')
    p.add_argument('--project', required=True); p.add_argument('--round')
    p.set_defaults(handler=lambda a: sheet(a.project, a.round))
    p = commands.add_parser('pick', help="the user's direction (their words) becomes the story")
    p.add_argument('--project', required=True); p.add_argument('--round'); p.add_argument('--slot', required=True, type=int)
    p.add_argument('--user-words', required=True); p.add_argument('--body', help="the user's mix of directions (a story body)")
    p.add_argument('--migrate', action='store_true', help='a ladder adopted without a story takes one (the script must then name its beats)')
    p.add_argument('--proxy', help="who picked, when the user handed the pick to a stand-in for a test (--user-words: their words allowing it)")
    p.set_defaults(handler=lambda a: pick(a.project, a.round, a.slot, a.user_words, a.body, a.migrate, a.proxy))
    return commands
