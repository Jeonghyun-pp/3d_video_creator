"""The clay seen by a viewer who does not know the plan - the test of whether the reel shows what it means to.

1. The blind viewer sees each cut's clay clip without its words or number (review/clay/see/, made by reel clay), as
   frames and a motion map, in order, with each cut's length - no narration, no plan, no purpose. For each cut it writes
   what it sees, what changes and what a viewer who knows nothing learns from it; for the reel, the one thing it says.
2. A reader of words alone - shown no picture - matches each account against the cut's point (direction.show_how.point,
   else what the cut is for) and the reel's account against the reel's point (the shot list's point.says, else the
   story's stake): seen yes | partly | no, and why.

A cut the viewer did not get (seen no) is POINT_UNSEEN; a reel whose one thing is not its point is REEL_POINT_UNSEEN
(SEE, read like reel_judge.WANTING). Each row of SEE says whether it is a floor: a floor only where calibration bore the
reading out (scratchpad show/V5.md). On a floor an unseen cut goes back to its plan (plan_read.reopen, level plan: only a
new plan of what it shows and a pick close it) and an unseen reel point stops a candidate; otherwise the finding is advice
the agent weighs. Seeing runs either way: a coloured render waits until its cut was seen as it is (REEL_UNSEEN).

Why the words are off (plan V5): the narration is drawn on the clay as captions; a viewer who reads them would answer
from the words, not the picture. Why the matcher sees no picture: it judges meaning between two texts, not the picture.

Why (2026-10-26, the user): "이게 최선이야?" - every check read the text; nothing asked whether someone who does not know
the plan learns from the picture what the plan says.
"""
from __future__ import annotations

from .common import StudioError, file_hash, now, read_json, stable_hash, write_json
from .project import load_project, load_shot, project_dir

SEE_RECORD = 'see.json'   # review/clay/see.json, its rounds kept
SEEN = ('yes', 'partly', 'no')
# what the matcher's answer makes a finding, and whether that finding is a floor (only where calibration bore it out:
# show/V5.md - a row not calibrated is advice). A new reading is a row.
# Calibrated 2026-10-26 (two runs): reference cuts matched to their own lines not 'no' 10/12 both runs, crossed with
# another reel's lines 'no' 11/12 both runs, the fab's old climax (a blob over a dark square) vs "a speck kills a chip"
# no / partly - POINT_UNSEEN a floor (its margin is one cut: lines a picture hardly carries read unseen, about 1 in 6).
# The reel's point had no reference bar and flipped no / partly on the fab: advice.
SEE = {'POINT_UNSEEN': {'of': 'cut', 'seen': 'no', 'floor': True,
                        'means': 'the viewer who does not know the plan did not get what the cut means to show'},
       'REEL_POINT_UNSEEN': {'of': 'reel', 'seen': 'no', 'floor': False,
                             'means': 'the one thing the viewer took from the reel is not its point'}}

BLIND_PROMPT = """You watch a short vertical video with the sound off and no captions. Answer only from the attached
images; know nothing else about it. It is grey clay blocking: the shapes are stand-ins for real things - judge what the
shapes, their places and their motion show, never surfaces or colour. Each image is one cut, in order: {frames} left to
right ({shares} % through it), then M, its motion map (white where the picture changed over the cut, black where it held).
The cuts: {cuts}
For each cut write: seen (what is on screen, plainly, in a sentence), changes (what moves or changes over the cut, or
"nothing"), learns (what a viewer who knows nothing about the subject learns from this cut alone, in one sentence - only
what the pictures show; do not guess a topic the pictures do not show). Then says: the one thing the video as a whole
tells, from its pictures alone, in one sentence."""

MATCH_PROMPT = """A viewer who did not know the plan watched a short explainer video cut by cut, with the sound off and no
captions, and wrote what each cut showed and taught them. For each cut, compare the viewer's account with what the cut
was meant to make a viewer know (its point):
- yes: the viewer got the point - the right thing doing the right thing - even in vaguer words;
- partly: the viewer got part of it (the thing but not what happens to it, or the setting but not the fact);
- no: the viewer missed it or took something else from the cut.
Judge meaning, not wording; the viewer may name things more plainly than the point does. Write why in one sentence.
{cuts}
Then the whole video: the viewer's one sentence was "{says}"; the video's point is "{point}". Answer seen and why the same way."""


def _blind_schema(ids):
    cut = {'type': 'object', 'additionalProperties': False, 'required': ['shot_id', 'seen', 'changes', 'learns'],
           'properties': {'shot_id': {'enum': ids}, 'seen': {'type': 'string'}, 'changes': {'type': 'string'}, 'learns': {'type': 'string'}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['cuts', 'says'],
            'properties': {'cuts': {'type': 'array', 'items': cut, 'minItems': len(ids), 'maxItems': len(ids)}, 'says': {'type': 'string'}}}


def _match_schema(ids):
    verdict = {'seen': {'enum': list(SEEN)}, 'why': {'type': 'string'}}
    cut = {'type': 'object', 'additionalProperties': False, 'required': ['shot_id', 'seen', 'why'],
           'properties': {'shot_id': {'enum': ids}, **verdict}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['cuts', 'reel'],
            'properties': {'cuts': {'type': 'array', 'items': cut, 'minItems': len(ids), 'maxItems': len(ids)},
                           'reel': {'type': 'object', 'additionalProperties': False, 'required': ['seen', 'why'], 'properties': verdict}}}


def blind_prompt(cuts):
    """cuts: [(shot_id, length_s)] in order."""
    from .reel_judge import frames_words, timed
    return BLIND_PROMPT.format(cuts='; '.join(f'image {n} = {sid} ({timed(s)})' for n, (sid, s) in enumerate(cuts)), **frames_words())


def match_prompt(rows, says, point):
    """rows: [(shot_id, blind account {seen, changes, learns}, the cut's point)]."""
    cuts = '\n'.join(f"- {sid}: the viewer saw {a['seen']!r}, changes {a['changes']!r}, learned {a['learns']!r}. Its point: {p!r}"
                     for sid, a, p in rows)
    return MATCH_PROMPT.format(cuts=cuts, says=says, point=point)


def point_of(shot):
    """What a cut means its viewer to know: its show_how.point, else what it is for (its goal)."""
    return ((shot.get('direction') or {}).get('show_how') or {}).get('point') or shot.get('goal') or ''


def reel_point(path):
    """The reel's point: the shot list's point.says, else the story's stake, else None."""
    from .decisions import envelope
    said = (((envelope(path, 'shotlist') or {}).get('body') or {}).get('point') or {}).get('says')
    stake = (((envelope(path, 'story') or {}).get('body') or {}).get('stake') or {}).get('what_is_at_risk')
    return said or stake


def _cuts(path):
    from .reel import CLAY
    from .storyboard import _clip_seconds
    made = project_dir(path) / CLAY / 'clay.json'
    rows = {r['shot_id']: r for r in (read_json(made).get('shots') or [])} if made.is_file() else {}
    cuts, missing = [], []
    for entry in load_project(path)['shots']:
        row = rows.get(entry['shot_id'])
        clip = project_dir(path) / CLAY / row['see_clip'] if row and row.get('see_clip') else None
        if clip is None or not clip.is_file():
            missing.append(entry['shot_id'])
            continue
        cuts.append({'shot_id': entry['shot_id'], 'clip': clip, 'seconds': _clip_seconds(clip), 'version': row.get('version'),
                     'shot': load_shot(path, entry['shot_id'])})
    return cuts, missing


def records(path):
    from .reel import CLAY
    file = project_dir(path) / CLAY / SEE_RECORD
    return read_json(file).get('rounds', []) if file.is_file() else []


def findings_of(row):
    """{shot_id or 'reel': [codes]} of a round (a generated cut, whose picture a person passes, aside)."""
    out = {}
    for code, rule in SEE.items():
        if rule['of'] == 'cut':
            for c in row['match']['cuts']:
                if c['seen'] == rule['seen'] and c['shot_id'] not in (row.get('skip') or ()):
                    out.setdefault(c['shot_id'], []).append(code)
        elif row['match'].get('reel') and row['match']['reel']['seen'] == rule['seen'] and row.get('point'):
            out.setdefault('reel', []).append(code)
    return out


def see(project, viewer=None, matcher=None, force=False):
    """The clay seen blind and matched (review/clay/see.json). viewer / matcher (images, prompt, schema, target) stand in
    for the model in tests; the same clay and plan are not asked again."""
    from .reel import CLAY
    from .screen_grammar import JUDGE_SHARES, codex_json, strips
    path = project_dir(project)
    folder = path / CLAY / 'see'
    cuts, missing = _cuts(path)
    if len(cuts) < 1:
        raise StudioError('INPUT_INVALID', 'reel see: no cut of the clay has its picture without words - reel clay --project P first',
                          recovery='reel clay --project P (it makes review/clay/see/ beside the captioned clips)')
    images = [strips(c['clip'], [{'index': n, 'start_s': 0.0, 'len_s': c['seconds']}], {'fps': 30}, folder / 'strips' / f"cut{n}",
                     shares=JUDGE_SHARES, motion=True)[0] for n, c in enumerate(cuts)]
    ids = [c['shot_id'] for c in cuts]
    prompt = blind_prompt([(c['shot_id'], c['seconds']) for c in cuts])
    blind_key = stable_hash({'images': [file_hash(i) for i in images], 'prompt': prompt})[:16]
    target = folder / f'blind_{blind_key}.json'
    if force or not target.is_file():
        (viewer or (lambda i, p, s, t: codex_json(i, p, s, t, force=force)))(images, prompt, _blind_schema(ids), target)
    blind = read_json(target)['answer']
    accounts = {a['shot_id']: a for a in blind['cuts']}
    point = reel_point(path)
    mprompt = match_prompt([(c['shot_id'], accounts[c['shot_id']], point_of(c['shot'])) for c in cuts], blind['says'],
                           point or '(no point written)')
    key = stable_hash({'blind': blind_key, 'match': mprompt})[:16]
    rounds = records(path)
    done = next((r for r in rounds if r['key'] == key), None)
    if done and not force:
        return {**done, 'findings': findings_of(done), 'next': _next(done, findings_of(done)), 'rounds': len(rounds)}
    mtarget = folder / f'match_{key}.json'
    if force or not mtarget.is_file():
        (matcher or (lambda i, p, s, t: codex_json(i, p, s, t, force=force)))([], mprompt, _match_schema(ids), mtarget)
    match = read_json(mtarget)['answer']
    row = {'key': key, 'round': len(rounds) + 1, 'at': now(), 'cuts': ids, 'versions': {c['shot_id']: c['version'] for c in cuts},
           'skip': [c['shot_id'] for c in cuts if (c['shot'].get('route') or {}).get('mode') == 'generative'],
           'unseen_clips': missing, 'point': point, 'blind': blind, 'match': match}
    write_json(path / CLAY / SEE_RECORD, {'schema_version': 1, 'rounds': rounds + [row]})
    found = findings_of(row)
    for sid, codes in found.items():   # a floor sends the cut back to what it shows
        if sid != 'reel' and any(SEE[c]['floor'] for c in codes):
            from .plan_read import _entry, reopen
            if _entry(path, sid) is not None:
                why = next(c['why'] for c in match['cuts'] if c['shot_id'] == sid)
                reopen(path, sid, f"the viewer who did not know the plan did not get its point ({point_of(next(c['shot'] for c in cuts if c['shot_id'] == sid))}): {why}",
                       f"what it shows, from its point: the viewer saw {accounts[sid]['seen']}", source='reel_see')
    return {**row, 'findings': found, 'next': _next(row, found), 'rounds': len(rounds) + 1,
            'artifacts': [str(path / CLAY / SEE_RECORD), str(target), str(mtarget)]}


def _next(row, found):
    out = []
    for sid, codes in found.items():
        floor = any(SEE[c]['floor'] for c in codes)
        if sid == 'reel':
            out.append(f"the reel's point was not seen ({row['match']['reel']['why']}) - "
                       + ('a candidate stops until it is: replan the cuts that carry the proof' if floor else
                          'advice (not calibrated as a floor): weigh replanning the cuts that carry the proof'))
        else:
            out.append(f"{sid}: its point was not seen - " + ('sent back to its plan (plan reopen): decide revise its show_how, show, change '
                                                             'or journey, then build and pick' if floor else
                                                             'advice (not calibrated as a floor): weigh plan reopen --shot ' + sid))
    return out


def seen(path, shot_id, versions=None):
    """REEL_UNSEEN unless a round saw this cut at its current version (a generated cut, a person's to pass, is not asked)."""
    path = project_dir(path)
    shot = load_shot(path, shot_id)
    if (shot.get('route') or {}).get('mode') == 'generative':
        return None
    version = (versions or {}).get(shot_id, shot.get('scene_version'))
    row = next((r for r in reversed(records(path)) if shot_id in r['cuts'] and r['versions'].get(shot_id) == version), None)
    if row is None:
        raise StudioError('REEL_UNSEEN', f'{shot_id} {version}: not seen by the viewer who does not know the plan',
                          recovery='reel clay --project P, then reel see --project P')
    return row


def reel_seen(path):
    """REEL_POINT_UNSEEN where it is a floor and the latest round that saw every cut as it is says the reel's point was
    not seen."""
    path = project_dir(path)
    versions = {e['shot_id']: load_shot(path, e['shot_id']).get('scene_version') for e in load_project(path)['shots']}
    row = next((r for r in reversed(records(path)) if r['versions'] == versions), None)
    if row and SEE['REEL_POINT_UNSEEN']['floor'] and 'REEL_POINT_UNSEEN' in findings_of(row).get('reel', []):
        raise StudioError('REEL_POINT_UNSEEN', f"the viewer took \"{row['blind']['says']}\" from the reel, not its point "
                          f"\"{row['point']}\": {row['match']['reel']['why']}", recovery='replan the cuts that carry the proof, build, see again')
