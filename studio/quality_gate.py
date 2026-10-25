"""What a coloured render and a candidate need of a shot's quality, read on the version itself - not on whether the agent
ran the checks. Why (2026-10-24 audit): every shot goal lived inside `storyboard pick`, the reel watcher's verdicts were
read by no gate, and a departure passed on any twelve characters - a run that skipped takes and the watcher rendered
cuts no one had measured or watched. The user, 2026-10-24: run every device (skip it and the render stops); block
outright only the floors the watchers' verdicts bear out; a taste finding passes on a reason a reader takes with the
picture. Not forced, on the user's call ("강제했을때 오히려 안좋아질수도 있어?"): zero findings, a picture for every
shot, sizes or sides blocked outright, a still shot or an illustrative line refused.

A shot passes when
1) its version is measured (camera_dynamics.json of that version, SHOT_UNMEASURED);
2) a take of it was picked since it was last reopened (SHOT_UNPICKED) - a rebuild after the pick is its own version and
   is measured again;
3) no hard floor (library/shot_goals.json `hard`) holds on that version, unless the user's words allow it there
   (SHOT_FLOOR);
4) the reel's watcher judged it with its neighbours as they are (REEL_UNJUDGED, REEL_JUDGE_STALE) and each finding it
   left open is answered - an answer the reader took (studio/excuses.py), or one the user must settle (REEL_FINDING_OPEN);
5) every reason written in place of a fix in it was read and taken (excuses.require).
A generated cut is a person's to pass (video_review) and is not read here.
"""
from __future__ import annotations

from .common import StudioError, now, read_json, write_json
from .project import load_project, load_shot, project_dir, shot_path

ALLOWED = 'decisions/quality_allowed.json'   # a hard floor the user's words allow on one version of one shot
# what may wait on the user (SKILL's ask-only list): a finding answered this way keeps its shot from rendering until
# the user settles it - never a way round a fix
AWAITS = {'paid': "the fix is a paid generation or render (the user's cap and words)",
          'real_subject': "the fix changes a specific real subject's parts or places (#4, the user's words)",
          'frozen': "the fix is an edit to frozen code (the user's freeze words)"}


def _generated(shot):
    return (shot.get('route') or {}).get('mode') == 'generative'


def measured(path, shot):
    """The version's own measurement, or SHOT_UNMEASURED: one made before a measure a hard floor reads (read_roots)
    counts as none."""
    version = shot.get('scene_version')
    file = shot_path(path, shot['shot_id']).parent / 'versions' / str(version) / 'camera_dynamics.json'
    data = read_json(file) if file.is_file() else None
    if not data or data.get('version') != version or any(root not in data for root in read_roots()):
        raise StudioError('SHOT_UNMEASURED', f"{shot['shot_id']} {version}: not measured as built",
                          recovery=f"studio pattern dynamics measure --project P --shot {shot['shot_id']}")
    return data


def picked(path, shot_id):
    """SHOT_UNPICKED unless a take was picked after the shot was last reopened (the reel's watcher sent it back) - or
    every finding of the round that reopened it is answered (reel answer): kept as it is, the answer read later."""
    from .storyboard import envelope
    env = envelope(path, shot_id) or {}
    picks = [h['at'] for h in env.get('history') or [] if any(op.get('op') == 'pick' for op in h.get('ops') or [])]
    reopened = (env.get('reopened') or {}).get('at')
    if picks and reopened and max(picks) < reopened and _answered(path, shot_id, env['reopened'].get('round')):
        return
    if not picks or (reopened and max(picks) < reopened):
        why = f"reopened by the reel's watcher: {env['reopened']['why'][:200]}" if picks else 'no take of it was picked'
        raise StudioError('SHOT_UNPICKED', f'{shot_id}: {why}', recovery=f'storyboard takes --shot {shot_id}, storyboard judge, storyboard pick'
                          + (' - or keep it and answer each finding (reel answer)' if picks else ''))


def _answered(path, shot_id, round_no):
    """Every finding the round `round_no` left on the cut has an answer recorded on that round."""
    from .reel import CLAY
    from .reel_judge import findings
    record = project_dir(path) / CLAY / 'judge.json'
    row = next((r for r in (read_json(record) if record.is_file() else {}).get('rounds') or [] if r.get('round') == round_no), None)
    if row is None:
        return False
    items = findings(row, row.get('skip') or ()).get(shot_id) or []
    said = {a['item'] for a in (row.get('answers') or {}).get(shot_id, [])}
    return bool(items) and set(items) <= said


def hard_rows():
    """The goal rows that hold a hard floor: each one's `hard` names its code and what it reads of a version's
    camera_dynamics.json ({code, reads: dotted path}). A new floor is a row (tests: a new row needs no code here)."""
    from .camera_comfort import goals
    return [row for row in goals() if isinstance(row.get('hard'), dict)]


def read_roots():
    """The top-level measures the hard floors read: a version measured without one of them counts as unmeasured."""
    return sorted({row['hard']['reads'].split('.')[0] for row in hard_rows()})


def _walk(data, dotted):
    for key in dotted.split('.'):
        data = data.get(key) if isinstance(data, dict) else None
    return data


def held(data):
    """[{goal, code, text}] the hard floors hold on a measure (a version's camera_dynamics.json, or a take carrying its
    read roots). What a row reads is a list of lines (each one holds) or a number (it holds at or past the row's limit
    of the same name; the time it peaked is read beside it as <name>_at_s)."""
    out = []
    for row in hard_rows():
        code, reads = row['hard']['code'], row['hard']['reads']
        value = _walk(data, reads)
        if isinstance(value, list):
            out += [{'goal': row['id'], 'code': code, 'text': str(t)} for t in value]
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            name = reads.split('.')[-1]
            limit = (row.get('limits') or {})[name]
            if value >= limit:
                at = _walk(data, reads + '_at_s')
                tip = (row.get('try') or {}).get(name) if isinstance(row.get('try'), dict) else row.get('try')
                out.append({'goal': row['id'], 'code': code,
                            'text': f"{reads} {value:g} is at or past the {row['id']} limit {limit:g}"
                                    + (f' (from {at} s)' if at is not None else '') + (f' - {tip}' if tip else '')})
    return out


def allowed(path):
    file = project_dir(path) / ALLOWED
    return read_json(file).get('allowed', []) if file.is_file() else []


def allow(project, shot_id, code, user_words):
    """A hard floor kept on one version of one shot, in the user's words (never the agent's): it passes that version only."""
    from .generative.review import check_user_words
    path = project_dir(project)
    version = load_shot(path, shot_id).get('scene_version')
    rows = [r for r in allowed(path) if not (r['shot_id'] == shot_id and r['code'] == code)]
    rows.append({'shot_id': shot_id, 'version': version, 'code': code, 'user_words': check_user_words(user_words, f'{shot_id} {code}'), 'at': now()})
    write_json(path / ALLOWED, {'schema_version': 1, 'allowed': rows})
    return {'allowed': rows[-1]}


def judged(path, shot_id, versions):
    """The latest reel-judge round that watched this cut with its neighbours as they are: (round, None) or (None, why)."""
    from .reel import CLAY
    record = project_dir(path) / CLAY / 'judge.json'
    rounds = (read_json(record) if record.is_file() else {}).get('rounds') or []
    seen = [r for r in rounds if shot_id in (r.get('cuts') or [])]
    if not seen:
        return None, 'REEL_UNJUDGED'
    last = seen[-1]
    cuts = last['cuts']
    at = cuts.index(shot_id)
    near = [c for c in cuts[max(0, at - 1):at + 2]]
    then = last.get('versions') or {}
    stale = [c for c in near if then.get(c) != versions.get(c)]
    return (None, 'REEL_JUDGE_STALE: ' + ', '.join(f"{c} watched as {then.get(c)}, now {versions.get(c)}" for c in stale)) if stale else (last, None)


def require_shot(path, shot_id, versions=None):
    """Every reason a coloured render or a candidate of this shot is refused, raised as the first one met (see module)."""
    from . import excuses
    from .reel_judge import findings
    path = project_dir(path)
    shot = load_shot(path, shot_id)
    if _generated(shot):
        return
    data = measured(path, shot)
    picked(path, shot_id)
    holding = [h for h in held(data)
               if not any(a['shot_id'] == shot_id and a['code'] == h['code'] and a['version'] == shot.get('scene_version') for a in allowed(path))]
    if holding:
        raise StudioError('SHOT_FLOOR', f"{shot_id} {shot.get('scene_version')}: {holding[0]['code']} - {holding[0]['text'][:300]}",
                          recovery='direct the shot again so the floor holds (the goal row says what to try); only the user\'s words keep '
                                   f'it (reel allow --shot {shot_id} --code {holding[0]["code"]} --user-words …)')
    versions = versions or {e['shot_id']: load_shot(path, e['shot_id']).get('scene_version') for e in load_project(path)['shots']}
    row, why = judged(path, shot_id, versions)
    if row is None:
        raise StudioError(why.split(':')[0], f'{shot_id}: {why}', recovery='reel clay --project P, then reel judge --project P')
    open_items = findings(row, row.get('skip') or ()).get(shot_id) or []
    answers = {a['item']: a for a in (row.get('answers') or {}).get(shot_id, [])}
    waiting = [i for i in open_items if (answers.get(i) or {}).get('awaits')]
    if waiting:
        a = answers[waiting[0]]
        raise StudioError('REEL_FINDING_OPEN', f"{shot_id}: {waiting[0]} waits on the user - {AWAITS[a['awaits']]}: {a['why'][:200]}",
                          recovery='the user settles it (their words, their cap); until then this shot does not render')
    unanswered = [i for i in open_items if i not in answers]
    if unanswered:
        raise StudioError('REEL_FINDING_OPEN', f"{shot_id}: the reel's watcher found it {', '.join(unanswered)} in round {row['round']} - "
                          f"{next((s.get('note') for s in row['shots'] if s['shot_id'] == shot_id), '')[:200]}",
                          recovery='fix it and judge the reel again, or answer it with what the cut gains (reel answer --shot … --item … --why …)')
    excuses.require(path, shot_id)


def require(path, shot=None):
    """require_shot for one shot, or for every shot of the reel (a candidate), with the reel's own reasons (excuses)."""
    from . import excuses
    path = project_dir(path)
    ids = [shot['shot_id']] if shot else [e['shot_id'] for e in load_project(path)['shots']]
    versions = {e['shot_id']: load_shot(path, e['shot_id']).get('scene_version') for e in load_project(path)['shots']}
    for sid in ids:
        require_shot(path, sid, versions)
    excuses.require(path, None)


def answer(project, shot_id, item, why, awaits=None):
    """A finding of the latest round that watched the cut, answered: what the cut gains as it is (read by the reader with
    the cut's strip), or `awaits` one of AWAITS - then the shot waits on the user and does not render."""
    from .reel import CLAY
    from .reel_judge import ITEMS
    path = project_dir(project)
    if item not in ITEMS:
        raise StudioError('INPUT_INVALID', f'reel answer --item: one of {ITEMS}')
    if awaits is not None and awaits not in AWAITS:
        raise StudioError('INPUT_INVALID', f'reel answer --awaits: one of {sorted(AWAITS)} (what only the user settles)')
    if not why or len(why.strip()) < 12:
        raise StudioError('INPUT_INVALID', 'reel answer --why: a sentence - what the cut gains as it is, or what the user must settle')
    record = path / CLAY / 'judge.json'
    data = read_json(record) if record.is_file() else {'rounds': []}
    rounds = [r for r in data['rounds'] if shot_id in (r.get('cuts') or [])]
    if not rounds:
        raise StudioError('REEL_UNJUDGED', f'{shot_id}: no round watched it', recovery='reel judge --project P')
    row = rounds[-1]
    kept = [a for a in (row.setdefault('answers', {}).get(shot_id) or []) if a['item'] != item]
    row['answers'][shot_id] = kept + [{'item': item, 'why': why.strip(), **({'awaits': awaits} if awaits else {}), 'at': now()}]
    write_json(record, data)
    return {'shot_id': shot_id, 'round': row['round'], 'answers': row['answers'][shot_id]}


def status(project):
    """Each shot's first refusal (or None) and the reel's - what a render or a candidate would meet now."""
    from . import excuses
    path = project_dir(project)
    versions = {e['shot_id']: load_shot(path, e['shot_id']).get('scene_version') for e in load_project(path)['shots']}
    out = {}
    for sid in versions:
        try:
            require_shot(path, sid, versions)
            out[sid] = None
        except StudioError as error:
            out[sid] = {'code': error.code, 'message': error.message[:300], 'recovery': error.recovery}
    try:
        excuses.require(path, None)
        reel = None
    except StudioError as error:
        reel = {'code': error.code, 'message': error.message[:300], 'recovery': error.recovery}
    return {'shots': out, 'reel': reel, 'open': sorted(s for s, v in out.items() if v) + (['reel'] if reel else [])}
