"""The clay reel watched whole: every cut's clip as four keyframes, in order, judged by a model for how the reel runs -
which cuts do not read, repeat the cut before, are dull or harsh, whether the hook holds - and which few cuts to make
again (storyboard takes / judge / pick on each). A watcher's verdict, not the engine's: nothing is picked here.

Why (2026-10-22, submarine r2): every cut passed its own checks and the reel was seven thin side views in a row; a cut
judged alone never sees that it repeats its neighbour.

Every cut the watcher finds wanting is the agent's to fix, not the user's to hear about (2026-10-23, the user: "이것들은
나한테 보고할게 아니라 에이전트가 문제점 찾았으면 적절하게 고칠수 있어야하는거 아니야?" - r3 ended with two repeats,
a mark that never drew and an underfilled cut listed for the user, all fixable): each such cut is reopened, the redo picks
with the watcher's own try. The loop runs while the findings fall; REEL_JUDGE_STALL rounds that leave as many as the
round before (or REEL_JUDGE_ROUNDS in all) stop it - then each finding still open is written up with its cause and
evidence, and only then does the user see the reel.
"""
from __future__ import annotations

from .common import StudioError, file_hash, now, read_json, stable_hash, write_json
from .project import load_project, load_shot, project_dir

REEL_JUDGE_ROUNDS = 8   # rounds the agent runs alone at most, findings falling or not (a bound on time, never the aim)
REEL_JUDGE_STALL = 2    # rounds in a row whose findings did not fall: what is left is not fixed by another try
REDO_MAX = 4            # cuts the watcher ranks most worth making again: their try leads; every finding is reopened
REOPEN_LEVEL = 'camera'   # the watcher sends a cut back to its camera takes; a cut sent back to what it shows is plan_read.reopen
ITEMS = ('reads', 'change_seen', 'names_change', 'rushed', 'dull', 'repeats_before', 'harsh', 'lost')
WANTING = {'reads': False, 'change_seen': False, 'names_change': False, 'dull': True, 'repeats_before': True, 'harsh': True,
           'lost': True}   # a finding
# Each cut's length and its frames' times (2026-10-26, plan R4): the strips carried no durations, so a 2 s cut and a 6 s
# cut looked alike; a long cut is not dull for its length (calibrated: 15 long cuts of the cutaway reference read neither
# rushed nor dull). `rushed` is asked and recorded, never a finding: in calibration it did not tell the fab clay's short
# cuts (rushed 0-0.6 over three wordings) from the reference's short cuts (scratchpad cutlen/R4.md)
# harsh is motion an eye cannot follow, never speed alone; lost is not knowing where we are or what to look at (2026-10-25,
# plan M6: the old 'a jump or swing uncomfortable to watch' would call a fly-in to a visible place harsh)


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
named or pointed at - true when nothing must change), rushed (the cut ends before what it shows has been seen: the change
or reveal it is about is still under way, or only completes, at its last frame, with no frame that holds the result; or
the camera is still travelling to what the cut is about when it ends - judge the picture by the cut's length and its
frames' times, never the time words or captions need; a short cut whose picture reads at its first frames, or whose
action reads while it runs, is not rushed), dull
(nothing new is seen over the cut: the frames and the motion map show nothing happening or revealed; a cut is
never dull for its length while something keeps changing or being revealed in it), repeats_before (the same size and angle as the cut before it), harsh (motion an eye
cannot follow: the picture lurches - a burst from still, a lunge from the whole thing to one detail between two
neighbouring frames, a stop dead, a shake or a swing - or it moves with no clear destination; a destination in view does
not excuse a lurch, and a fast, even flight toward a place in view that eases in and stops is not harsh for its speed),
lost (a viewer cannot tell where the camera is or what to look at), note (one sentence). Then: hook_holds (the first cut makes a viewer stay), flow (two or three sentences on how
the reel runs as a whole), and redo: at most {most} cuts that would most improve the reel if made again, each with why
and what to try (another motion - closer, from below, passing by, pushing in, letting the subject run off the frame -
or another key moment). Leave redo empty when the reel runs well."""


def frames_words():
    """{frames, shares}: how a judge's strip is laid out, in words (screen_grammar.JUDGE_SHARES, PANELS)."""
    from .screen_grammar import JUDGE_SHARES, PANELS
    n = len(JUDGE_SHARES)
    return {'frames': f'{n} keyframes {PANELS[0]}-{PANELS[n - 1]}', 'shares': ', '.join(str(round(x * 100)) for x in JUDGE_SHARES)}


def timed(length_s):
    """How long a cut runs and when its strip's frames are, in words ('2.4 s; frames at 0.12, ... s')."""
    from .screen_grammar import JUDGE_SHARES
    return f"{length_s:.1f} s; frames at {', '.join(f'{x * length_s:.2f}' for x in JUDGE_SHARES)} s"


def findings(row, skip=()):
    """{shot_id: [items]} a round found wanting (WANTING), the cuts in `skip` aside (a generated cut: a person passes its
    motion, video-review)."""
    out = {}
    for shot in row.get('shots') or []:
        wanting = [k for k, bad in WANTING.items() if k in shot and shot[k] is bad]
        if wanting and shot['shot_id'] not in skip:
            out[shot['shot_id']] = wanting
    return out


def stalled(rounds):
    """How many rounds in a row, to the last, left as many findings as the round before them."""
    counts = [sum(len(v) for v in findings(r, r.get('skip') or ()).values()) for r in rounds]
    n = 0
    while n + 1 < len(counts) and counts[-1 - n] >= counts[-2 - n]:
        n += 1
    return n


def _reopen(path, row):
    """Each cut a round found wanting is reopened on its storyboard record: why, what to try and the feel of the take it
    holds now (rejected) - storyboard takes / judge read it, and pick asks a reason to keep that feel. A redo pick says
    the watcher's why and try; another finding says its verdicts and note. Why (2026-10-22): the watcher's redo list was
    written and nothing read it; 2026-10-23: cuts judged repeating but not ranked in the redo were never touched."""
    from .storyboard import _envelope_path, envelope
    picks = {r['shot_id']: r for r in row.get('redo') or []}
    notes = {s['shot_id']: s.get('note') or '' for s in row.get('shots') or []}
    wanting = findings(row, row.get('skip') or ())
    for sid in [*picks, *(s for s in wanting if s not in picks)]:
        redo = picks.get(sid) or {'shot_id': sid, 'why': f"the watcher found it {', '.join(wanting[sid])}: {notes[sid]}",
                                  'try': 'what the note says is missing'}
        shot = load_shot(path, redo['shot_id'])
        made = (((shot.get('camera') or {}).get('move') or {}).get('recipe') or {}).get('id')
        measured = path / 'shots' / redo['shot_id'] / 'versions' / str(shot.get('scene_version')) / 'camera_dynamics.json'
        felt = (read_json(measured).get('judged') or {}).get('recipe') if measured.is_file() else None
        env = envelope(path, redo['shot_id']) or {'schema_version': 1, 'shot_id': redo['shot_id'], 'history': [], 'status': 'none'}
        if (env.get('reopened') or {}).get('level') == 'plan' and env['reopened']['at'] > max(
                [h['at'] for h in env.get('history') or [] if any(op.get('op') == 'pick' for op in h.get('ops') or [])] or ['']):
            continue   # sent back to its plan and not made again since: a camera redo does not replace that
        env['reopened'] = {'level': REOPEN_LEVEL, 'round': row['round'], 'why': redo['why'], 'try': redo['try'], 'rejected': felt or made,
                           'at': row['at']}
        write_json(_envelope_path(path, redo['shot_id']), env)


def judge(project, reader=None, force=False, user_words=None):
    """The reel's clay judged whole (review/clay/judge.json, its rounds kept): needs reel clay first. Another round is
    refused once REEL_JUDGE_STALL rounds did not lessen the findings (REEL_JUDGE_STALLED) or REEL_JUDGE_ROUNDS ran
    (REEL_JUDGE_ROUNDS), until the user's words open more - the agent writes up what is left and shows the reel."""
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
    from .show_how import purpose_words   # what each cut is for (stored as goal) and what its viewer must learn
    lines = '; '.join(f"shot {n} = {sid} ({timed(_clip_seconds(c))}; for: {purpose_words(s)}; {(s.get('narration') or {}).get('text') or 'no line'}; "
                      f"must change on screen: {', '.join(change_words(c) for c in changes_of(s)) or 'nothing'})" for n, (sid, c, s) in enumerate(cuts))
    images = [strips(clip, [{'index': n, 'start_s': 0.0, 'len_s': _clip_seconds(clip)}], {'fps': 30}, folder / 'judge' / f'cut{n}',
                     shares=JUDGE_SHARES, motion=True)[0]
              for n, (_sid, clip, _s) in enumerate(cuts)]
    prompt = PROMPT.format(cuts=lines, most=REDO_MAX, **frames_words())
    schema = _schema([sid for sid, _c, _s in cuts])
    key = stable_hash({'images': [file_hash(i) for i in images], 'prompt': prompt})[:16]
    done = next((r for r in data['rounds'] if r['key'] == key), None)
    if done and not force:
        return {**done, 'rounds': len(data['rounds'])}
    alone = data['rounds'][len(data['rounds']) - since:]
    if stalled(alone) >= REEL_JUDGE_STALL or since >= REEL_JUDGE_ROUNDS:
        code = 'REEL_JUDGE_STALLED' if stalled(alone) >= REEL_JUDGE_STALL else 'REEL_JUDGE_ROUNDS'
        raise StudioError(code, f'the reel was judged {since} times since the user last looked, the last {stalled(alone)} without fewer '
                          'findings - for each finding still open write what keeps failing, its cause (your direction or the '
                          'engine) and its evidence, then show the user the clay (review/clay/index.html, review/clay/judge.json)',
                          recovery='reel judge --user-words "<their words>" opens more rounds')
    target = folder / 'judge' / f'judge_{key}.json'
    if reader is None:
        codex_json(images, prompt, schema, target, force=force)
    else:
        reader(images, prompt, schema, target)
    got = read_json(target)
    answer = got['answer']
    skip = [sid for sid, _c, s in cuts if (s.get('route') or {}).get('mode') == 'generative']
    # the version each clip shows (reel clay's record): a verdict holds for a cut while it and its neighbours are these
    # versions (quality_gate.judged)
    made = {r['shot_id']: r.get('version') for r in (read_json(folder / 'clay.json').get('shots') or [])} if (folder / 'clay.json').is_file() else {}
    row = {'key': key, 'round': len(data['rounds']) + 1, 'at': now(), 'reader': got.get('reader') or 'codex',
           'cuts': [sid for sid, _c, _s in cuts], 'versions': {sid: made.get(sid) for sid, _c, _s in cuts}, 'skip': skip, **answer}
    data['rounds'].append(row)
    write_json(record, data)
    _reopen(path, row)
    wanting = findings(row, skip)
    return {**row, 'rounds': len(data['rounds']), 'artifacts': [str(record)], 'findings': wanting,
            'next': [f"storyboard takes --shot {r['shot_id']} (try: {r['try']})" for r in answer['redo']]
                    + [f"{sid}: found {', '.join(v)} - find why and fix it (direction, engine or cheat)" for sid, v in wanting.items()
                       if sid not in {r['shot_id'] for r in answer['redo']}]}
