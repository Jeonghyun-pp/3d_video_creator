"""A generated clip is looked at by a person against what the shot says before the shot moves on (the review gate).

`video review sheet` puts the clip beside its narration: frames at the start, at each line's moment and at the end,
each read (optionally, the free reader) for the things the lines claim, with the 3D's own facts beside them
(studio/picture3d.py facts.json). `video review pass` records the verdict bound to the clip's and the narration's hashes,
in the user's own words (generative.review.check_user_words) - or an agent's note only when the user delegated
`video_review` (project.delegation.scope). When the project's ladder requires `video_review`, a take without a matching
pass never reaches a final cut (edit.latest_render, profile candidate: VIDEO_REVIEW_MISSING, never softened; a hybrid
shot falls back to its Blender pass with a warning); a preview cut shows it with a warning that it is unreviewed.

A line's moment in the clip is its share of the narration's characters (the words are spoken at an even pace over
the shot) until a narration track's word timing is recorded per shot - said on the sheet.
Why (2026-10-16): generated clips looked right and still drifted from the 3D's camera; the user: "움직임 자체는
사람이 한번 확인하고 그 장면에서 설명할 내용이랑도 맞는지 확인하고 넘어가긴 해야겠다".
"""
from __future__ import annotations

import html
import json
import subprocess
from pathlib import Path

from .common import StudioError, file_hash, now, read_json, stable_hash, write_json
from .project import load_project, load_shot, project_dir

DELEGATION_SCOPE = 'video_review'   # the delegation scope that lets the agent pass a clip with its note


def _record(path, shot_id):
    return path / 'decisions' / 'video_review' / f'{shot_id}.json'


def _lines(path, shot):
    """[(line text, its share of the shot: start, middle, end)] in order, by the narration's characters."""
    from .decisions import envelope
    body = {layer: (envelope(path, layer) or {}).get('body') or {} for layer in ('script', 'shotlist')}
    entry = next((s for s in body['shotlist'].get('shots') or [] if s.get('shot_id') == shot['shot_id']), None)
    by_id = {l['line_id']: l.get('text') or '' for l in body['script'].get('lines') or []}
    texts = [by_id.get(i, '') for i in (entry or {}).get('line_ids') or []] or [((shot.get('narration') or {}).get('text') or '')]
    texts = [t for t in texts if t]
    total = sum(len(t) for t in texts) or 1
    out, seen = [], 0
    for t in texts:
        out.append((t, seen / total, (seen + len(t) / 2) / total, (seen + len(t)) / total))
        seen += len(t)
    return out


def narration_sha(path, shot):
    return stable_hash([t for t, *_ in _lines(path, shot)])


def _duration(clip):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(clip)],
                         capture_output=True, text=True, timeout=60)
    try:
        return float(out.stdout.strip())
    except ValueError:
        raise StudioError('INPUT_INVALID', f'{clip}: not a video ffprobe can read') from None


def sheet(project, shot_id, clip, reader=None, read=False):
    """The review page for one generated clip: frames at the start, each line's moment and the end, with the line said
    there, the claims and the 3D's facts (picture3d), and (read=True) what the reader sees of the claimed things."""
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    clip = Path(clip).expanduser().resolve()
    if not clip.is_file():
        raise StudioError('INPUT_INVALID', f'no clip {clip}')
    sha = file_hash(clip)
    folder = path / 'concepts' / shot_id / 'video_review' / sha[:12]
    folder.mkdir(parents=True, exist_ok=True)
    seconds = _duration(clip)
    lines = _lines(path, shot)
    stops = [('시작', '', 0.0)] + [(f'문장 {i + 1}', t, mid) for i, (t, _a, mid, _b) in enumerate(lines)] + [('끝', '', 1.0)]
    frames = []
    for i, (label, text, share) in enumerate(stops):
        at = min(max(share * seconds, 0.0), max(seconds - 0.05, 0.0))
        image = folder / f'f{i:02d}.png'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{at:.3f}', '-i', str(clip), '-frames:v', '1', str(image)],
                       capture_output=True, timeout=120)
        frames.append({'label': label, 'line': text, 'seconds': round(at, 2), 'image': image.name if image.is_file() else None})
    claims = (read_json(path / 'concepts' / shot_id / 'picture3d' / 'claims.json') if (path / 'concepts' / shot_id / 'picture3d' / 'claims.json').is_file() else {}).get('things') or {}
    facts = read_json(path / 'concepts' / shot_id / 'picture3d' / 'facts.json') if (path / 'concepts' / shot_id / 'picture3d' / 'facts.json').is_file() else {}
    if read and claims:
        from .picture3d import CLAIM_STATES, _read_schema
        from .screen_grammar import codex_json
        ask = '\n'.join(['One frame of a generated explainer clip. Answer only from what is visible. For each item: present '
                         '(yes/no/unsure), state (one of ' + ', '.join(CLAIM_STATES) + '), count (visible, or null). Items:']
                        + [f"- id={k}: {v.get('words') or k}" for k, v in claims.items()])
        from .outside_model import gather

        def read_frame(f):   # every frame's question asked in one round where the studio must ask (outside_model.gather)
            if f['image']:
                f['read'] = (reader or codex_json)([folder / f['image']], ask, _read_schema(claims), folder / f"{Path(f['image']).stem}_read.json")['answer']['answers']
        gather(frames, read_frame)
    data = {'shot_id': shot_id, 'clip': str(clip), 'clip_sha256': sha, 'narration_sha256': narration_sha(path, shot), 'seconds': seconds,
            'frames': frames, 'claims': {k: {'state': v.get('state'), 'count': v.get('count')} for k, v in claims.items()},
            'facts': {'errors': facts.get('gate_failures') or [], 'warnings': facts.get('warnings') or []},
            'timing': 'line moments by the narration\'s characters (even pace)', 'made_at': now()}
    write_json(folder / 'review.json', data)
    def figure(f):
        image = f"<img src='{html.escape(f['image'])}'>" if f['image'] else '<div>없음</div>'
        seen = ''.join(f"<br>{html.escape(a['id'])}: {a['present']}, {a['state']}" for a in f.get('read') or [])
        return (f"<figure>{image}<figcaption><b>{html.escape(f['label'])}</b> {f['seconds']} s<br>{html.escape(f['line'])}"
                f"{seen}</figcaption></figure>")
    cells = ''.join(figure(f) for f in frames)
    claim_rows = ''.join(f"<li>{html.escape(k)}: 말한 상태 {html.escape(str(v['state']))}, 개수 {html.escape(str(v['count']))}</li>" for k, v in data['claims'].items())
    fact_rows = ''.join(f"<li class='bad'>{html.escape(f['code'])}: {html.escape(f['text'])}</li>" for f in data['facts']['errors'])
    (folder / 'index.html').write_text(
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{shot_id} 영상 검토</title><style>body{{font:14px sans-serif;margin:0 16px;background:#f6f6f4;color:#1d1d1f}}"
        "@media (prefers-color-scheme:dark){body{background:#151517;color:#eee}}"
        ".row{display:flex;gap:8px;overflow-x:auto}figure{margin:0;width:180px;flex:0 0 auto}img{width:180px;border-radius:6px}"
        "figcaption{font-size:12px}.bad{color:#c33}</style></head><body>"
        f"<h1>{shot_id} 생성 영상 검토</h1><p><video src='{html.escape(str(clip))}' controls width='270'></video></p>"
        f"<p>움직임이 이 샷에서 설명할 내용과 맞는지 직접 확인해 주세요. 문장 시점은 글자 수 비율로 잡았습니다.</p>"
        f"<div class='row'>{cells}</div><h2>문장이 말하는 것</h2><ul>{claim_rows or '<li>없음</li>'}</ul>"
        f"<h2>3D 사실 점검</h2><ul>{fact_rows or '<li>문제 없음</li>'}</ul>"
        f"<p>통과: studio video-review pass --project {html.escape(str(project))} --shot {shot_id} --clip {html.escape(str(clip))} --user-words \"...\"</p>"
        "</body></html>", encoding='utf-8')
    return {'shot_id': shot_id, 'clip_sha256': sha, 'frames': len(frames), 'artifacts': [str(folder / 'index.html'), str(folder / 'review.json')],
            'next': f'studio video-review pass --project {project} --shot {shot_id} --clip {clip} --user-words "..."'}


def pass_(project, shot_id, clip, user_words=None, agent_note=None):
    """Record that a person looked at the clip against the shot's narration and passed it - bound to the clip's and the
    narration's hashes. Only the user's own words, or the agent's note when the user delegated `video_review`."""
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    clip = Path(clip).expanduser().resolve()
    if not clip.is_file():
        raise StudioError('INPUT_INVALID', f'no clip {clip}')
    if user_words:
        from .generative.review import check_user_words
        decision = {'by': 'user', 'user_words': check_user_words(user_words, 'video review pass')}
    elif agent_note and DELEGATION_SCOPE in ((load_project(path).get('delegation') or {}).get('scope') or []):
        decision = {'by': 'agent', 'agent_note': agent_note.strip()}
    else:
        raise StudioError('INPUT_INVALID', "video review pass needs the user's own words (--user-words); the agent passes a clip only when "
                                           f"the user delegated '{DELEGATION_SCOPE}' (--agent-note)")
    row = {'schema_version': 1, 'shot_id': shot_id, 'clip': str(clip), 'clip_sha256': file_hash(clip),
           'narration_sha256': narration_sha(path, shot), 'decision': {**decision, 'at': now()}}
    write_json(_record(path, shot_id), row)
    return {**row, 'artifacts': [str(_record(path, shot_id))]}


def required(path):
    from .decisions import ladder_requires
    return ladder_requires(path, DELEGATION_SCOPE)


def require(path, shot, clip):
    """VIDEO_REVIEW_MISSING (never softened) when the project's ladder requires the review and this clip has no pass
    bound to it and to the shot's narration as it stands."""
    path = project_dir(path)
    if not required(path):
        return None
    file = _record(path, shot['shot_id'])
    row = read_json(file) if file.is_file() else None
    clip = Path(clip)
    if row is None or not clip.is_file() or row['clip_sha256'] != file_hash(clip) or row['narration_sha256'] != narration_sha(path, shot):
        why = 'no review pass' if row is None else 'the pass was for another clip or narration'
        raise StudioError('VIDEO_REVIEW_MISSING', f"{shot['shot_id']}: {why}; a generated clip is looked at against the shot's lines first",
                          recovery=f"studio video-review sheet --project {path} --shot {shot['shot_id']} --clip {clip}, then video-review pass")
    return row


def register_commands(subparsers):
    parser = subparsers.add_parser('video-review', help='A generated clip looked at against what the shot says, before the shot moves on')
    commands = parser.add_subparsers(dest='video_review_command', required=True)
    p = commands.add_parser('sheet', help='Frames at each line of the narration, the claims and the 3D facts beside the clip')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--clip', required=True)
    p.add_argument('--read', action='store_true', help='read each frame for the claimed things (the free reader)')
    p.set_defaults(handler=lambda a: sheet(a.project, a.shot, a.clip, read=a.read))
    p = commands.add_parser('pass', help="Record the verdict in the user's own words (bound to the clip and the narration)")
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--clip', required=True)
    p.add_argument('--user-words'); p.add_argument('--agent-note')
    p.set_defaults(handler=lambda a: pass_(a.project, a.shot, a.clip, a.user_words, a.agent_note))
