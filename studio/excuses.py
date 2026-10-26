"""Reasons written in place of a fix, read by a reader with the picture (library/excuses.json says where each kind is
written). A departure, a still shot's why, an unmarked change, an illustrative sentence, a pick's reason, an answer to
the reel's watcher, a reel-wide departure, a corrected beat reading: each passed on its length alone (2026-10-24 audit:
"held on purpose" closed a finding; four characters made a shot still). The user, 2026-10-24: a taste finding passes on a
reason the reader takes with the picture; one that only fills the length is refused.

The reader sees the cut as the reel's watcher does (screen_grammar.strips: six frames and the motion map of its clay
clip), the finding and the reason, and answers accepted and why. A reason recorded in the user's words is the user's
call and is not read. Readings are kept by the reason, its finding and the clip's bytes: a new version is read again.
"""
from __future__ import annotations

from pathlib import Path

from .common import REPO, StudioError, file_hash, now, read_json, stable_hash, write_json
from .project import load_project, load_shot, project_dir

TABLE = REPO / 'library' / 'excuses.json'
READINGS = 'decisions/excuses.json'
PROMPT = """You check reasons an agent gave for leaving a finding unfixed in a short vertical explainer reel. The images
are {are}: each strip is one cut, {frames} left to right ({shares} % through it), then M, its motion map
(white where the picture changed over the cut, black where it held). Judge {excuse}.
{items}
For each reason answer accepted and why (one sentence). Accept a reason only when the images bear it out and it says
what the cut or the reel gains by staying as it is. Refuse a reason that restates the finding ("held on purpose", "it is
intended"), that the images contradict, that names a gain the images do not show, or that is fluent but empty."""


def prompt_for(media, items):
    """The reader's prompt for cuts made of `media` (reel.MEDIA, as reel clay recorded each; clay when not recorded)."""
    from .reel import medium_words
    from .reel_judge import frames_words
    return PROMPT.format(items=items, are=medium_words(media, 'are', 'strip'), excuse=medium_words(media, 'excuse', 'strip'), **frames_words())


def _media(path, ids):
    from .reel import CLAY
    made = project_dir(path) / CLAY / 'clay.json'
    rows_ = {r['shot_id']: r for r in (read_json(made).get('shots') or [])} if made.is_file() else {}
    return [(rows_.get(i) or {}).get('medium') for i in ids]


def rows():
    return read_json(TABLE)['rows']


def _walk(value, path):
    for key in path.split('.'):
        value = (value or {}).get(key) if isinstance(value, dict) else None
    return value


def _shot_reasons(shot, row):
    value = _walk(shot, row['path'])
    items = value if isinstance(value, list) else [value] if value else []
    out = []
    for item in items:
        text = item.get(row['text']) if isinstance(item, dict) and row.get('text') else item if isinstance(item, str) else None
        if isinstance(text, str) and text.strip():
            out.append({'code': str(item.get(row['code'])) if isinstance(item, dict) and row.get('code') else row['id'], 'text': text.strip()})
    return out


def _pick_reasons(path, shot_id):
    from .storyboard import envelope
    picks = [(h, op) for h in (envelope(path, shot_id) or {}).get('history') or [] for op in h.get('ops') or [] if op.get('op') == 'pick']
    if not picks or picks[-1][0].get('user_words'):
        return []
    op = picks[-1][1]
    return ([{'code': code, 'text': why} for code, why in (op.get('because') or {}).items()]
            + ([{'code': 'TAKE_REPEATS_REEL', 'text': op['repeat_why']}] if op.get('repeat_why') else []))


def _answer_reasons(path, shot_id):
    from .reel import CLAY
    record = project_dir(path) / CLAY / 'judge.json'
    rounds = [r for r in (read_json(record) if record.is_file() else {}).get('rounds') or [] if shot_id in (r.get('cuts') or [])]
    if not rounds:
        return []
    note = next((s.get('note') or '' for s in rounds[-1].get('shots') or [] if s['shot_id'] == shot_id), '')
    return [{'code': f"{a['item']} (the watcher: {note})", 'text': a['why']}
            for a in (rounds[-1].get('answers') or {}).get(shot_id, []) if not a.get('awaits')]


def reasons(path, shot_id=None):
    """[{row, shot_id, code, text, answers}] for one cut, or (shot_id None) the reel-wide ones and the beat readings."""
    path = project_dir(path)
    out = []
    for row in rows():
        found = []
        if shot_id is not None:
            if row['source'] == 'shot':
                found = _shot_reasons(load_shot(path, shot_id), row)
            elif row['source'] == 'pick':
                found = _pick_reasons(path, shot_id)
            elif row['source'] == 'reel_answer':
                found = _answer_reasons(path, shot_id)
        elif row['source'] == 'reel_depart':
            from .reel_flow import reel_departs
            found = [{'code': d['code'], 'text': d['why']} for d in reel_departs(path) if not d.get('user_words')]
        elif row['source'] == 'beat_mark':
            found = [{'code': f.name, 'text': read_json(f)['provenance']['note'], 'picture': _picture_of(f)}
                     for f in sorted(path.glob('**/*.read.json')) if (read_json(f).get('provenance') or {}).get('kind') == 'manual']
        out += [{'row': row['id'], 'shot_id': shot_id, 'answers': row['answers'], **r} for r in found]
    return out


def _picture_of(reading_file):
    """The picture a reading is of (beat_pictures.reading_path: the picture's own name, suffix .read.json)."""
    stem = str(reading_file)[:-len('.read.json')]
    return next((stem + s for s in ('.png', '.jpg', '.jpeg', '.webp') if Path(stem + s).is_file()), stem + '.png')


def _clip(path, shot_id):
    """The cut's clay clip, or None when the clay does not show its current version."""
    from .reel import CLAY
    made = project_dir(path) / CLAY / 'clay.json'
    rows_ = {r['shot_id']: r for r in (read_json(made).get('shots') or [])} if made.is_file() else {}
    row = rows_.get(shot_id)
    clip = project_dir(path) / CLAY / f'{shot_id}.mp4'
    from .reel import shown_version
    if not row or row.get('version') != shown_version(load_shot(path, shot_id)) or not clip.is_file():
        return None
    return clip


def _images(path, shot_id):
    """What the reader sees: the cut's strip, or every cut's strip for a reel-wide reason."""
    from .reel import CLAY
    from .screen_grammar import JUDGE_SHARES, strips
    from .storyboard import _clip_seconds
    ids = [shot_id] if shot_id else [e['shot_id'] for e in load_project(path)['shots']]
    clips = [(sid, _clip(path, sid)) for sid in ids]
    if shot_id and clips[0][1] is None:
        raise StudioError('EXCUSE_UNREAD', f'{shot_id}: the clay does not show its current version - its reasons cannot be read',
                          recovery='reel clay --project P')
    return [strips(clip, [{'index': n, 'start_s': 0.0, 'len_s': _clip_seconds(clip)}], {'fps': 30},
                   project_dir(path) / CLAY / 'excuses' / sid, shares=JUDGE_SHARES, motion=True)[0]
            for n, (sid, clip) in enumerate(clips) if clip is not None]


def _key(reason, clip_sha):
    return stable_hash({k: reason.get(k) for k in ('row', 'shot_id', 'code', 'text')} | {'clip': clip_sha})[:16]


def _sha(path, reason):
    if reason.get('picture'):
        return file_hash(reason['picture']) if Path(reason['picture']).is_file() else None
    if reason['shot_id']:
        clip = _clip(path, reason['shot_id'])
        return file_hash(clip) if clip else None
    clips = [_clip(path, e['shot_id']) for e in load_project(path)['shots']]   # a reel-wide reason: every cut as it is
    return stable_hash([file_hash(c) if c else None for c in clips])


def readings(path):
    file = project_dir(path) / READINGS
    return read_json(file).get('readings', {}) if file.is_file() else {}


def require(path, shot_id=None):
    """EXCUSE_UNREAD / EXCUSE_REJECTED for the cut's reasons (or the reel's, shot_id None)."""
    path = project_dir(path)
    kept = readings(path)
    for reason in reasons(path, shot_id):
        got = kept.get(_key(reason, _sha(path, reason)))
        where = reason['shot_id'] or 'the reel'
        if got is None:
            raise StudioError('EXCUSE_UNREAD', f"{where}: the reason for {reason['code']} ({reason['row']}) was not read with the picture: "
                              f"\"{reason['text'][:160]}\"", recovery='studio reel excuses --project P')
        if not got['accepted']:
            raise StudioError('EXCUSE_REJECTED', f"{where}: the reason for {reason['code']} ({reason['row']}) was refused - {got['why'][:200]}",
                              recovery='fix what the finding says, or give the reason the picture bears out, then reel excuses again')


def _schema(n):
    item = {'type': 'object', 'additionalProperties': False, 'required': ['n', 'accepted', 'why'],
            'properties': {'n': {'type': 'integer', 'minimum': 0, 'maximum': max(0, n - 1)}, 'accepted': {'type': 'boolean'}, 'why': {'type': 'string'}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['reasons'],
            'properties': {'reasons': {'type': 'array', 'items': item, 'minItems': n, 'maxItems': n}}}


def judge(project, reader=None, force=False):
    """Read every reason not yet read (each cut's with its strip, the reel's with every strip, a beat correction with its
    picture) and keep the answers. reader(images, prompt, schema, target) stands in for the model in tests."""
    path = project_dir(project)
    kept = readings(path)
    groups = [(sid, reasons(path, sid)) for sid in [e['shot_id'] for e in load_project(path)['shots']]
              if (load_shot(path, sid).get('route') or {}).get('mode') != 'generative'] + [(None, reasons(path, None))]
    read = []
    for sid, found in groups:
        todo = [r for r in found if force or _key(r, _sha(path, r)) not in kept]
        for picture in sorted({r.get('picture') for r in todo if r.get('picture')}):   # a beat picture is its own image
            batch = [r for r in todo if r.get('picture') == picture]
            read += _read(path, batch, [picture], reader, force, kept)
        batch = [r for r in todo if not r.get('picture')]
        if batch:
            read += _read(path, batch, _images(path, sid), reader, force, kept)
    write_json(path / READINGS, {'schema_version': 1, 'readings': kept})
    return {'read': read, 'refused': [r for r in read if not r['accepted']], 'artifacts': [str(path / READINGS)]}


def _read(path, batch, images, reader, force, kept):
    from .reel_judge import frames_words
    from .screen_grammar import codex_json
    items = '\n'.join(f"{n}. {r['shot_id'] or 'the whole reel'} - finding: {r['answers']} ({r['code']}); the reason: \"{r['text']}\""
                      for n, r in enumerate(batch))
    shots = sorted({r['shot_id'] for r in batch if r.get('shot_id')}) or [e['shot_id'] for e in load_project(path)['shots']]
    prompt = prompt_for(_media(path, shots) if not any(r.get('picture') for r in batch) else ['clay'], items)
    target = project_dir(path) / 'review' / 'clay' / 'excuses' / f"read_{stable_hash({'p': prompt, 'i': [file_hash(i) for i in images]})[:16]}.json"
    (reader or (lambda i, p, s, t: codex_json(i, p, s, t, force=force)))(images, prompt, _schema(len(batch)), target)
    answers = {a['n']: a for a in read_json(target)['answer']['reasons']}
    out = []
    for n, r in enumerate(batch):
        a = answers.get(n) or {'accepted': False, 'why': 'not answered'}
        kept[_key(r, _sha(path, r))] = {'accepted': bool(a['accepted']), 'why': a['why'], 'reason': r, 'at': now()}
        out.append({**r, 'accepted': bool(a['accepted']), 'why': a['why']})
    return out
