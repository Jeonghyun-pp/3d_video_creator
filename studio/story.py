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
    return {k: record[k] for k in ('logline', 'direction', 'beats') if k in record} if record else None


def materialize(path, body, decided_by='user', user_words=None, picked=None):
    """story.json: the body and who settled it (the ladder's approval, or a pick in the user's words)."""
    path = project_dir(path)
    record = {'schema_version': 1, **body, 'decided_by': decided_by, **({'user_words': user_words} if user_words else {}),
              **({'picked': picked} if picked else {}), 'written_at': now()}
    write_json(path / STORY_FILE, record)
    return [STORY_FILE]


def missing(path):
    """STORY_MISSING (a warning): a project with shots and no story - made before the story existed, or skipped."""
    path = project_dir(path)
    if load(path) is not None or not load_project(path).get('shots'):
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
    errors, warnings = lint_story(body, load_project(path)['output'].get('target_seconds'), load_axes())
    return {'story': str(path / STORY_FILE), 'errors': errors, 'warnings': warnings}


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
    from .decisions import envelope
    facts = (envelope(path, 'facts') or {}).get('body') or (read_json(path / 'sources.json') if (path / 'sources.json').is_file() else {})
    return [c['text'] for c in facts.get('claims', [])][:FACTS_SHOWN]


def _brief_text(path, slot, axes_table, taste):
    from .story_core import STORY_RULES, rule
    project = load_project(path)
    brief = project.get('brief') or {}
    length = project['output'].get('target_seconds')
    drawn = '\n'.join(f"- {axis} = {value}: {axes_table[axis]['values'][value]['means']} ({axes_table[axis]['means']})"
                      for axis, value in slot['axes'].items())
    claims = _claims(path)
    low, high = rule('CLIMAX_BAND')
    return '\n'.join(filter(None, [
        f"Write story direction {slot['slot']} of {DIRECTION_COUNT} for a {length} s vertical explainer: {brief.get('request') or ''}",
        f"What it must leave the viewer with: {brief.get('key_message') or ''}",
        'Facts it may use (sourced; say nothing beyond them):\n' + '\n'.join(f'- {c}' for c in claims) if claims else '',
        f'This direction was drawn on these axes - write the story they describe, not a safe average:\n{drawn}',
        taste,
        'First write the story freely in 4-6 plain sentences: what the viewer sees first, what they wonder, where it gets '
        'hard, where it turns, the moment everything builds to, how it ends. Only then turn it into the story body '
        '(schemas/studio-v1/decision.schema.json $defs/story) with direction set to the drawn values above.',
        f"Shape: the first beat is the hook, over in {rule('HOOK_MAX_S'):.0f} s; at least one turn or climax, the climax at "
        f'{low}-{high} of the reel; tension that rises and falls (not one level); no more than '
        f"{rule('EXPLAIN_RUN_MAX')} explaining beats in a row; shares that add up to 1; a picture_idea for every beat "
        '(the picture that carries it, in words). Rules and reasons: studio/story_core.STORY_RULES.',
        f"Then: studio story add --project {path} --round <round> --slot {slot['slot']} --body <file> --prose <file> --paint"]))


def directions(project, n=DIRECTION_COUNT, seed=None):
    """Draw n directions on the story axes (story_core.pick_directions: one near the user's taste, one far from it, one
    new) and write each as a brief for the agent. Nothing is decided: the user picks after the sheet."""
    from .story_core import load_axes, pick_directions
    from .taste_picks import preference, prompt_block
    path = project_dir(project)
    axes_table = load_axes()
    rounds = _rounds(path)
    used = [s['axes'] for r in rounds for s in read_json(r / 'plan.json')['slots']]
    seed = len(rounds) if seed is None else seed
    drawn = pick_directions(axes_table, n, preference('story'), used, seed)
    taste = prompt_block(['story'])
    out = project_dir(path) / DIRECTIONS / f'r{len(rounds) + 1:02d}'
    out.mkdir(parents=True, exist_ok=True)
    slots = [{**s, 'brief': _brief_text(path, s, axes_table, taste if s['role'] == 'taste' else '')} for s in drawn]
    plan = {'schema_version': 1, 'round': out.name, 'seed': seed, 'drawn_at': now(), 'slots': slots}
    write_json(out / 'plan.json', plan)
    return {'round': out.name, 'slots': [{k: s[k] for k in ('slot', 'role', 'axes', 'brief')} for s in slots],
            'next': f'write each: studio story add --project {path} --round {out.name} --slot <k> --body <file> --prose <file> --paint',
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
    if body.get('direction') != drawn['axes']:
        raise StudioError('INPUT_INVALID', f"STORY_DIRECTION_DIFFERS: slot {slot} was drawn as {drawn['axes']}, the body says "
                                           f"{body.get('direction')} - write the drawn direction (a mix is the user's, at story pick --body)")
    errors, warnings = lint_story(body, load_project(path)['output'].get('target_seconds'), load_axes())
    record = {'slot': drawn['slot'], 'role': drawn['role'], 'body': body, 'errors': errors, 'warnings': warnings, 'written_at': now(),
              **({'prose': _text(prose)} if prose else {})}
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
    return {'round': folder.name, 'slot': drawn['slot'], 'errors': errors, 'warnings': warnings, 'artifacts': [str(folder / f"d{drawn['slot']}.json")]}


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
    plan = read_json(folder / 'plan.json')
    written = _written(folder)
    if not written:
        raise StudioError('INPUT_INVALID', f'{folder.name}: no direction written yet (studio story add)')
    same = stories_one_idea(written)
    labels = {'taste': '지금까지 고른 취향에 가까운 안', 'contrast': '취향과 가장 다른 안', 'explore': '아직 안 해 본 방식'}
    cards = []
    for d in written:
        b = d['body']
        figure = (f"<img src='{_e(d['picture']['path'])}' alt='opening of direction {d['slot']}' style='width:100%;aspect-ratio:9/16;object-fit:cover;border-radius:8px'>"
                  if d.get('picture') else "<div class='miss'>그림 없음</div>")
        beats = ''.join(f"<li><b>{_e(x['function'])}</b> · {_e(x['what_happens'])}</li>" for x in b['beats'])
        issues = ''.join(f"<div class='warn'>{_e(x)}</div>" for x in d['errors'] + d['warnings'])
        axes = ', '.join(f'{k} {v}' for k, v in (b.get('direction') or {}).items())
        cards.append(f"<section class='card'><div class='head'><b>{d['slot']}안</b><span class='chip'>{_e(labels.get(d['role'], d['role']))}</span>"
                     f"<span class='chip'>{_e(axes)}</span></div><div style='display:grid;grid-template-columns:minmax(0,1fr) minmax(0,2fr);gap:14px'>"
                     f"<div>{figure}</div><div><p class='narr'>{_e(b['logline'])}</p>{('<p class=sub>' + _e(d['prose']) + '</p>') if d.get('prose') else ''}"
                     f"<ol>{beats}</ol><div class='sub'>긴장 곡선</div>{_curve(b['beats'])}{issues}</div></div></section>")
    page = (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>이야기 고르기</title><style>{CSS}</style></head><body><main><h1>이야기 {len(written)}안 — {folder.name}</h1>"
            "<p class='sub'><b>번호와 한 줄 이유를 말해 주세요</b> (예: \"2안, 실패로 시작하는 게 재밌어요\"). 섞고 싶으면 \"1안 시작에 3안 전개\"처럼 말해 주세요.</p>"
            + ''.join(f"<p class='warn'>{_e(x)}</p>" for x in same) + ''.join(cards) + '</main></body></html>')
    out = folder / 'sheet.html'
    out.write_text(page, encoding='utf-8')
    return {'round': folder.name, 'sheet': str(out), 'warnings': same, 'artifacts': [str(out)]}


def pick(project, rnd, slot, user_words, body=None):
    """The user's direction becomes the story: on the decision ladder it is proposed and approved in their words; in a
    project off the ladder (a delegated run) it is written to story.json with their words. The pick goes to the taste
    with the directions passed over. body: the user's mix of directions, when they asked for one."""
    from .decisions import adopted, approve, propose, validate_body
    from .generative.review import check_user_words
    from .story_core import lint_story, load_axes
    from .taste_picks import record
    words = check_user_words(user_words, 'story pick')
    path = project_dir(project)
    folder = _round(path, rnd)
    plan = read_json(folder / 'plan.json')
    if plan.get('picked'):
        raise StudioError('DECISION_STALE', f"{folder.name} was already picked ({plan['picked']['slot']})")
    written = {d['slot']: d for d in _written(folder)}
    if int(slot) not in written:
        raise StudioError('INPUT_INVALID', f'{folder.name}: direction {slot} is not written (written: {sorted(written)})')
    chosen = deepcopy(json_arg(body)) if body is not None else written[int(slot)]['body']
    validate_body('story', chosen)
    if adopted(path):
        result = propose(path, 'story', chosen)
        if result['errors']:
            raise StudioError('DECISION_INVALID', 'story: ' + '; '.join(result['errors'][:6]), recovery='write the direction again (story add) or mix it right')
        settled = approve(path, 'story', words, result['rev'])
        how = {'ladder': settled['rev']}
    else:
        errors, _ = lint_story(chosen, load_project(path)['output'].get('target_seconds'), load_axes())
        if errors:
            raise StudioError('DECISION_INVALID', 'story: ' + '; '.join(errors[:6]), recovery='write the direction again (story add) or mix it right')
        picked = {'round': folder.name, 'slot': int(slot), **({'mixed': True} if body is not None else {})}
        how = {'written': materialize(path, chosen, 'user', words, picked)}
    passed = [d['body'].get('direction') for k, d in written.items() if k != int(slot) and d['body'].get('direction')]
    if chosen.get('direction'):
        record('story', chosen['direction'], passed, words, path, ref=f'{folder.name} d{slot}')
    plan['picked'] = {'slot': int(slot), 'user_words': words, 'at': now(), **({'mixed': True} if body is not None else {})}
    write_json(folder / 'plan.json', plan)
    return {'round': folder.name, 'picked': int(slot), **how, 'story': str(path / STORY_FILE)}


def register_commands(subparsers):
    parser = subparsers.add_parser('story', help='The story before the script: directions drawn on axes, picked by the user')
    commands = parser.add_subparsers(dest='story_command', required=True)
    p = commands.add_parser('check', help="story.json against the story rules (studio/story_core.STORY_RULES)")
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: check(a.project))
    p = commands.add_parser('directions', help='draw story directions on the axes (library/story_axes.json) and brief each')
    p.add_argument('--project', required=True); p.add_argument('--n', type=int, default=DIRECTION_COUNT); p.add_argument('--seed', type=int)
    p.set_defaults(handler=lambda a: directions(a.project, a.n, a.seed))
    p = commands.add_parser('add', help='a written direction: its story body, its prose and its opening picture')
    p.add_argument('--project', required=True); p.add_argument('--round'); p.add_argument('--slot', required=True, type=int)
    p.add_argument('--body', required=True); p.add_argument('--prose'); p.add_argument('--picture')
    p.add_argument('--paint', action='store_true', help='paint the opening picture with the image tool (Codex)')
    p.set_defaults(handler=lambda a: add(a.project, a.round, a.slot, a.body, a.prose, a.picture, a.paint))
    p = commands.add_parser('sheet', help='the directions side by side, one page, for the user to pick')
    p.add_argument('--project', required=True); p.add_argument('--round')
    p.set_defaults(handler=lambda a: sheet(a.project, a.round))
    p = commands.add_parser('pick', help="the user's direction (their words) becomes the story")
    p.add_argument('--project', required=True); p.add_argument('--round'); p.add_argument('--slot', required=True, type=int)
    p.add_argument('--user-words', required=True); p.add_argument('--body', help="the user's mix of directions (a story body)")
    p.set_defaults(handler=lambda a: pick(a.project, a.round, a.slot, a.user_words, a.body))
    return commands
