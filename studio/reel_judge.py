"""The clay reel watched whole: every cut's clip as four keyframes, in order, judged by a model for how the reel runs -
which cuts do not read, repeat the cut before, are dull or harsh, whether the hook holds - and which few cuts to make
again (storyboard takes / judge / pick on each). A watcher's verdict, not the engine's: nothing is picked here.

Why (2026-10-22, submarine r2): every cut passed its own checks and the reel was seven thin side views in a row; a cut
judged alone never sees that it repeats its neighbour. The loop is bounded: REEL_JUDGE_ROUNDS rounds, then the agent
stops and shows the user the clay and what is still wrong.
"""
from __future__ import annotations

from .common import StudioError, file_hash, now, read_json, stable_hash, write_json
from .project import load_project, load_shot, project_dir

REEL_JUDGE_ROUNDS = 2   # rounds of redo the agent runs alone before it stops and shows the user the reel
REDO_MAX = 4            # cuts one round asks to make again: more is a new shot list, not a polish
ITEMS = ('reads', 'change_seen', 'names_change', 'dull', 'repeats_before', 'harsh')


def _schema(ids):
    shot = {'type': 'object', 'additionalProperties': False, 'required': ['shot_id', *ITEMS, 'note'],
            'properties': {'shot_id': {'enum': ids}, **{k: {'type': 'boolean'} for k in ITEMS}, 'note': {'type': 'string'}}}
    redo = {'type': 'object', 'additionalProperties': False, 'required': ['shot_id', 'why', 'try'],
            'properties': {'shot_id': {'enum': ids}, 'why': {'type': 'string'}, 'try': {'type': 'string'}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['shots', 'hook_holds', 'flow', 'redo'],
            'properties': {'shots': {'type': 'array', 'items': shot}, 'hook_holds': {'type': 'boolean'}, 'flow': {'type': 'string'},
                           'redo': {'type': 'array', 'items': redo, 'maxItems': REDO_MAX}}}


PROMPT = """You watch the grey clay blocking of a short vertical explainer reel, cut by cut, in order. Answer only from the
attached images; judge composition, motion and flow, never surfaces or colour. Each image is one cut: {frames} left to
right ({shares} % through it), then M, its motion map - white where the picture changed over the whole cut, black where it
held (a camera that moves whitens everything; a still camera shows only what moved) - its number at the top (shot N = the
N-th cut below).
The cuts: {cuts}
For each cut answer: reads (a viewer tells what is on screen and what happens), change_seen (what must change on screen
in it is seen changing; true when nothing must), names_change (a viewer can tell which thing is changing - it is large,
named or pointed at - true when nothing must change), dull (nothing new is seen over the cut: the frames and the motion
map show nothing happening or revealed), repeats_before (the same size and angle as the cut before it), harsh (a jump or
swing uncomfortable to watch), note (one sentence). Then: hook_holds (the first cut makes a viewer stay), flow (two or three sentences on how
the reel runs as a whole), and redo: at most {most} cuts that would most improve the reel if made again, each with why
and what to try (another motion - closer, from below, passing by, pushing in, letting the subject run off the frame -
or another key moment). Leave redo empty when the reel runs well."""


def frames_words():
    """{frames, shares}: how a judge's strip is laid out, in words (screen_grammar.JUDGE_SHARES, PANELS)."""
    from .screen_grammar import JUDGE_SHARES, PANELS
    n = len(JUDGE_SHARES)
    return {'frames': f'{n} keyframes {PANELS[0]}-{PANELS[n - 1]}', 'shares': ', '.join(str(round(x * 100)) for x in JUDGE_SHARES)}


def _reopen(path, row):
    """The cuts a round sends back are reopened on their storyboard record: why, what to try and the feel of the take
    they hold now (rejected) - storyboard takes / judge read it, and pick asks a reason to keep that feel. Why
    (2026-10-22): the watcher's redo list was written and nothing read it."""
    from .storyboard import _envelope_path, envelope
    for redo in row.get('redo') or []:
        shot = load_shot(path, redo['shot_id'])
        made = (((shot.get('camera') or {}).get('move') or {}).get('recipe') or {}).get('id')
        measured = path / 'shots' / redo['shot_id'] / 'versions' / str(shot.get('scene_version')) / 'camera_dynamics.json'
        felt = (read_json(measured).get('judged') or {}).get('recipe') if measured.is_file() else None
        env = envelope(path, redo['shot_id']) or {'schema_version': 1, 'shot_id': redo['shot_id'], 'history': [], 'status': 'none'}
        env['reopened'] = {'round': row['round'], 'why': redo['why'], 'try': redo['try'], 'rejected': felt or made, 'at': row['at']}
        write_json(_envelope_path(path, redo['shot_id']), env)


def judge(project, reader=None, force=False, user_words=None):
    """The reel's clay judged whole (review/clay/judge.json, its rounds kept): needs reel clay first. A third round is
    refused (REEL_JUDGE_ROUNDS) until the user's words open another - the agent stops and shows the reel."""
    from .screen_grammar import JUDGE_SHARES, codex_json, strips
    from .storyboard import _clip_seconds
    from .reel import CLAY
    path = project_dir(project)
    folder = path / CLAY
    record = folder / 'judge.json'
    data = read_json(record) if record.is_file() else {'schema_version': 1, 'rounds': []}
    if user_words:
        from .generative.review import check_user_words
        data['opened'] = {'user_words': check_user_words(user_words, 'reel judge'), 'at': now(), 'after': len(data['rounds'])}
    since = len(data['rounds']) - (data.get('opened') or {}).get('after', 0)
    cuts = []
    for entry in load_project(path)['shots']:
        clip = folder / f"{entry['shot_id']}.mp4"
        if clip.is_file():
            cuts.append((entry['shot_id'], clip, load_shot(path, entry['shot_id'])))
    if len(cuts) < 2:
        raise StudioError('INPUT_INVALID', 'reel judge: fewer than two cuts in the clay - reel clay --project first')
    from .change_moves import changes_of, words as change_words
    lines = '; '.join(f"shot {n} = {sid} ({(s.get('narration') or {}).get('text') or 'no line'}; must change on screen: "
                      f"{', '.join(change_words(c) for c in changes_of(s)) or 'nothing'})" for n, (sid, _c, s) in enumerate(cuts))
    images = [strips(clip, [{'index': n, 'start_s': 0.0, 'len_s': _clip_seconds(clip)}], {'fps': 30}, folder / 'judge' / f'cut{n}',
                     shares=JUDGE_SHARES, motion=True)[0]
              for n, (_sid, clip, _s) in enumerate(cuts)]
    prompt = PROMPT.format(cuts=lines, most=REDO_MAX, **frames_words())
    schema = _schema([sid for sid, _c, _s in cuts])
    key = stable_hash({'images': [file_hash(i) for i in images], 'prompt': prompt})[:16]
    done = next((r for r in data['rounds'] if r['key'] == key), None)
    if done and not force:
        return {**done, 'rounds': len(data['rounds'])}
    if since >= REEL_JUDGE_ROUNDS:
        raise StudioError('REEL_JUDGE_ROUNDS', f'the reel was judged {since} times since the user last looked - stop and show them '
                          'the clay and what is still wrong (review/clay/index.html, review/clay/judge.json)',
                          recovery='reel judge --user-words "<their words>" opens another round')
    target = folder / 'judge' / f'judge_{key}.json'
    if reader is None:
        codex_json(images, prompt, schema, target, force=force)
    else:
        reader(images, prompt, schema, target)
    got = read_json(target)
    answer = got['answer']
    row = {'key': key, 'round': len(data['rounds']) + 1, 'at': now(), 'reader': got.get('reader') or 'codex',
           'cuts': [sid for sid, _c, _s in cuts], **answer}
    data['rounds'].append(row)
    write_json(record, data)
    _reopen(path, row)
    return {**row, 'rounds': len(data['rounds']), 'artifacts': [str(record)],
            'next': [f"storyboard takes --shot {r['shot_id']} (try: {r['try']})" for r in answer['redo']]}
