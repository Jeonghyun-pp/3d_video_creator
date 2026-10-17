"""The reel as a whole: its clay cut watched with captions, its picture flow, and a shot redone as options.

The user's third look (2026-10-17, "기획단계에서 너무 시간 안쓰고싶어"): the whole reel as clay - every built shot's
Workbench clip with its number and lines on it, one after another - on one page with the reel flow's findings. They
answer with shot numbers and a word each; `reel redo` makes that shot again as 2-3 options drawn differently
(concept variants), each solved and built as a take, shown side by side; the pick in their words becomes the shot.
Workbench only: no look render, allowed unattended (seconds a shot).
"""
from __future__ import annotations

from copy import deepcopy
import html
import json
from pathlib import Path
import subprocess

from .common import REPO, StudioError, font_file, now, read_json, write_json
from .project import load_project, load_shot, project_dir, shot_path

CLAY = 'review/clay'
REDO = 'redo'                  # redo/<shot>/rNN/plan.json
CAPTION_SIZE = 0.042           # caption type height as a share of the frame height (a phone's subtitle size)
LABEL_SIZE = 0.03


# --- the clay cut -------------------------------------------------------------------------------------------------

def _wrap(draw, text, font, width):
    lines, line = [], ''
    for ch in text:
        if draw.textlength(line + ch, font=font) > width and line:
            cut = line.rfind(' ')
            lines.append(line[:cut] if cut > 0 else line)
            line = (line[cut + 1:] if cut > 0 else '') + ch
        else:
            line += ch
    return lines + ([line] if line else [])


def _probe(clip):
    out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height,r_frame_rate',
                          '-of', 'json', str(clip)], capture_output=True, text=True, check=True)
    s = json.loads(out.stdout)['streams'][0]
    num, den = s['r_frame_rate'].split('/')
    return int(s['width']), int(s['height']), float(num) / float(den or 1)


def caption(clip, target, label, text, fps=None):
    """The clip with its shot label (top left) and its lines (bottom, wrapped) drawn on every frame: Pillow draws, ffmpeg
    reads and writes (no ffmpeg text filter is needed)."""
    from PIL import Image, ImageDraw, ImageFont
    width, height, rate = _probe(clip)
    face = str(font_file({}, REPO))
    big = ImageFont.truetype(face, max(12, round(height * CAPTION_SIZE)))
    small = ImageFont.truetype(face, max(10, round(height * LABEL_SIZE)))
    reader = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', str(clip), '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], stdout=subprocess.PIPE)
    writer = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{width}x{height}', '-r', str(fps or rate),
                               '-i', '-', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20', str(target)], stdin=subprocess.PIPE)
    size = width * height * 3
    pad = round(height * 0.015)
    while True:
        raw = reader.stdout.read(size)
        if len(raw) < size:
            break
        frame = Image.frombytes('RGB', (width, height), raw)
        draw = ImageDraw.Draw(frame, 'RGBA')
        draw.rounded_rectangle((pad, pad, 2 * pad + draw.textlength(label, font=small), 2 * pad + small.size), pad // 2, fill=(0, 0, 0, 150))
        draw.text((1.5 * pad, 1.3 * pad), label, font=small, fill='white')
        rows = _wrap(draw, text, big, width - 4 * pad) if text else []
        if rows:
            step = round(big.size * 1.3)
            top = height - 4 * pad - step * len(rows)
            draw.rounded_rectangle((pad, top - pad, width - pad, height - 2 * pad), pad, fill=(0, 0, 0, 160))
            for n, row in enumerate(rows):
                draw.text(((width - draw.textlength(row, font=big)) / 2, top + n * step), row, font=big, fill='white')
        writer.stdin.write(frame.tobytes())
    writer.stdin.close()
    reader.stdout.close()
    reader.wait(); writer.wait()
    if writer.returncode != 0 or not Path(target).is_file():
        raise StudioError('CLAY_FAILED', f'captioning {clip} failed')
    return target


def clay(project, shots=None, clip_of=None):
    """Every built shot's clay clip (storyboard's animatic: the layout profile, Workbench), captioned and cut together,
    on one page with the reel flow (review/clay/index.html, reel_clay.mp4). clip_of(path, shot_id, version) -> the
    clip's project-relative path (tests)."""
    from .reel_flow import built as reel_flow
    from .storyboard import _animatic
    path = project_dir(project)
    data = load_project(path)
    out = path / CLAY
    out.mkdir(parents=True, exist_ok=True)
    clip_of = clip_of or (lambda p, s, v: _animatic(p, s, v)['animatic'])
    rows, skipped = [], []
    for entry in data['shots']:
        if shots and entry['shot_id'] not in shots:
            continue
        shot = load_shot(path, entry['shot_id'])
        version = shot.get('scene_version')
        if not version:
            skipped.append(entry['shot_id'])
            continue
        source = path / clip_of(path, entry['shot_id'], version)
        target = out / f"{entry['shot_id']}.mp4"
        text = (shot.get('narration') or {}).get('text') or ''
        caption(source, target, f"{entry['shot_id']}  {version}", text, data['output'].get('fps'))
        rows.append({'shot_id': entry['shot_id'], 'version': version, 'clip': target.name, 'text': text,
                     'seconds': round(entry['frame_count'] / data['output']['fps'], 2)})
    if not rows:
        raise StudioError('INPUT_INVALID', 'no built shot to show (shot build first)')
    listing = out / 'list.txt'
    listing.write_text(''.join(f"file '{r['clip']}'\n" for r in rows), encoding='utf-8')
    reel = out / 'reel_clay.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(listing), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                    '-crf', '20', str(reel)], check=True, cwd=out)
    flow = reel_flow(path)['warnings']
    (out / 'index.html').write_text(_page(data.get('project_id') or path.name, rows, flow, skipped, path), encoding='utf-8')
    write_json(out / 'clay.json', {'schema_version': 1, 'made_at': now(), 'shots': rows, 'skipped': skipped, 'flow': flow})
    return {'reel': str(reel), 'page': str(out / 'index.html'), 'shots': [r['shot_id'] for r in rows], 'skipped': skipped,
            'warnings': flow, 'artifacts': [str(reel), str(out / 'index.html')]}


def _page(title, rows, flow, skipped, path):
    from .storyboard_brief import CSS
    e = lambda v: html.escape(str(v))   # noqa: E731
    cards = ''.join(f"<section class='card' id='{e(r['shot_id'])}'><div class='head'><b>{e(r['shot_id'])}</b><span class='chip'>{e(r['version'])}</span>"
                    f"<span class='chip'>{r['seconds']} s</span></div><div class='narr'>“{e(r['text'])}”</div>"
                    f"<video src='{e(r['clip'])}' controls muted playsinline preload='metadata' style='width:min(100%,360px);border-radius:8px'></video></section>"
                    for r in rows)
    notes = ''.join(f"<div class='warn'>{e(w)}</div>" for w in flow) or "<div class='ok'>흐름 경고 없음</div>"
    return ("<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>클레이 영상</title><style>{CSS}</style></head><body><main><h1>{e(title)} — 클레이 영상</h1>"
            "<p class='sub'><b>다시 할 샷 번호와 한마디를 말해 주세요</b> (예: \"s09 더 멀리서, 바다가 보이게\"). 그 샷만 다른 안 2~3개로 다시 만들어 고르게 합니다."
            f" 질감·불꽃·안개는 아직 없는 미리보기입니다.</p><video src='reel_clay.mp4' controls muted playsinline style='width:min(100%,400px);border-radius:10px'></video>"
            f"<h2>그림 흐름</h2>{notes}"
            + (f"<p class='sub'>빌드 없는 샷: {e(', '.join(skipped))}</p>" if skipped else '')
            + f"<h2>샷별</h2>{cards}<p class='sub'>다시 하기: <code>studio reel redo start --project {e(path)} --shot sNN --user-words \"…\"</code></p></main></body></html>")


# --- one shot again, as options ----------------------------------------------------------------------------------

def _rounds(path, shot_id):
    root = project_dir(path) / REDO / shot_id
    return sorted(p for p in root.glob('r[0-9]*') if (p / 'plan.json').is_file()) if root.is_dir() else []


def _plan(path, shot_id, rnd=None):
    rounds = _rounds(path, shot_id)
    if not rounds:
        raise StudioError('INPUT_INVALID', f'{shot_id}: no redo yet (reel redo start)')
    folder = rounds[-1] if rnd is None else next((r for r in rounds if r.name == rnd), None)
    if folder is None:
        raise StudioError('INPUT_INVALID', f'{shot_id}: no redo round {rnd}')
    return folder, read_json(folder / 'plan.json')


def _moments(path, shot_id):
    from .concept import frames
    pictured = [c['frame'] for c in frames(path, shot_id)]
    return pictured or [{'role': 'start', 'at': 0.0}, {'role': 'end', 'at': 1.0}]


def start(project, shot_id, user_words, count=3):
    """A redo of one shot in the user's words: count options drawn differently (concept variants on CONCEPT_AXES, away
    from what the shot has been drawn as), each with a prompt per pictured moment that carries the user's words."""
    from .concept import brief, variants_of
    from .generative.review import check_user_words
    words = check_user_words(user_words, 'redo')
    path = project_dir(project)
    load_shot(path, shot_id)
    if not 2 <= int(count) <= 4:
        raise StudioError('INPUT_INVALID', 'a redo makes 2-4 options')
    moments = _moments(path, shot_id)
    drawn = variants_of(path, shot_id, moments[0], int(count))
    options = []
    for d in drawn:
        prompts = []
        for m in moments:
            base = brief(path, shot_id, m['role'], m['at'] if m['role'] == 'key' else None)
            lines = base['prompt'].split('\n')
            prompts.append({'frame': m, 'prompt': '\n'.join(lines[:-1] + [d['line'], f'The user asked for this shot: {words}', lines[-1]]),
                            'attach': [a['path'] for a in base['attach']]})
        options.append({'option': d['variant'], 'role': d['role'], 'axes': d['axes'], 'moments': prompts})
    folder = project_dir(path) / REDO / shot_id / f'r{len(_rounds(path, shot_id)) + 1:02d}'
    folder.mkdir(parents=True, exist_ok=True)
    plan = {'schema_version': 1, 'shot_id': shot_id, 'round': folder.name, 'user_words': words, 'started_at': now(), 'options': options}
    write_json(folder / 'plan.json', plan)
    return {**plan, 'next': f'draw each option\'s moments: reel redo add --project {path} --shot {shot_id} --option <k> --frame <role> '
                            '(--image <file> | --paint), then reel redo build', 'artifacts': [str(folder / 'plan.json')]}


def add(project, shot_id, option, frame, image=None, paint=False, at=None, rnd=None, runner=None):
    """One option's picture of one moment, added as a concept variant tagged with the redo (a given image, or painted by
    the image tool from the option's prompt, the option's earlier moment attached so it is one shot)."""
    from .concept import _frame, add as concept_add, concepts
    path = project_dir(project)
    folder, plan = _plan(path, shot_id, rnd)
    chosen = next((o for o in plan['options'] if o['option'] == int(option)), None)
    if chosen is None or plan.get('picked'):
        raise StudioError('INPUT_INVALID', f"{shot_id} {folder.name}: no open option {option}")
    moment = _frame(frame, at)
    row = next((m for m in chosen['moments'] if m['frame'] == moment), None)
    if row is None:
        raise StudioError('INPUT_INVALID', f'{shot_id}: option {option} has no {frame} moment')
    tag = {'round': folder.name, 'option': int(option)}
    if paint:
        from .picture3d import paint_into
        earlier = [c['path'] for c in concepts(path, shot_id) if (c.get('variant') or {}).get('redo') == tag and c.get('frame')
                   and c['frame']['at'] < moment['at']]
        target = folder / f"o{option}_{moment['role']}_{moment['at']:.2f}.png"
        made, how, tail = paint_into(target, [path / a for a in row['attach'] + earlier[-1:]], row['prompt'], runner=runner,
                                     label=f'redo {shot_id} o{option}')
        if made is None:
            raise StudioError('PAINT_FAILED', f'{shot_id} option {option} {frame}: nothing painted ({tail[-200:]})')
        image = target
    if image is None:
        raise StudioError('INPUT_INVALID', 'give --image or --paint')
    return concept_add(path, shot_id, image, row['prompt'], frame=moment['role'], at=moment['at'] if moment['role'] == 'key' else None,
                       extra={'variant': {'axes': chosen['axes'], 'redo': tag}})


def _option_pictures(path, shot_id, tag, moments):
    from .concept import concepts
    mine = [c for c in concepts(path, shot_id) if (c.get('variant') or {}).get('redo') == tag]
    latest = {c['frame']['at']: c for c in mine}
    return [latest.get(m['at']) for m in moments]


def _choose(path, shot_id, ids):
    """Mark exactly these variant pictures chosen (frames() then reads them as the moments' pictures); -> the records as
    they were, to put back."""
    from .concept import _folder, concepts
    before = {}
    for c in concepts(path, shot_id):
        if not c.get('variant'):
            continue
        before[c['concept_id']] = deepcopy(c)
        variant = {k: v for k, v in c['variant'].items() if k != 'chosen'}
        if c['concept_id'] in ids:
            variant['chosen'] = {'by': 'redo', 'at': now()}
        write_json(_folder(path, shot_id) / f"{c['concept_id']}.json", {**c, 'variant': variant})
    return before


def _restore(path, shot_id, records):
    from .concept import _folder
    for cid, record in records.items():
        write_json(_folder(path, shot_id) / f'{cid}.json', record)


def build(project, shot_id, rnd=None, times=(0.0, 0.5, 1.0), measure_fn=None, apply_fn=None, variants_fn=None):
    """Each option with all its moments drawn: read its pictures, solve the camera to them (frames measure, frames
    apply), keep that camera as a take, put the shot and the pictures back; then build the takes side by side
    (storyboard variants). Nothing is picked here."""
    from .frame_targets import apply as frames_apply, measure as frames_measure
    from .storyboard import variants
    path = project_dir(project)
    folder, plan = _plan(path, shot_id, rnd)
    moments = [m['frame'] for m in plan['options'][0]['moments']]
    original = shot_path(path, shot_id).read_text(encoding='utf-8')
    takes, missing = [], []
    for o in plan['options']:
        pictures = _option_pictures(path, shot_id, {'round': folder.name, 'option': o['option']}, moments)
        if not all(pictures):
            missing.append(o['option'])
            continue
        records = _choose(path, shot_id, {c['concept_id'] for c in pictures})
        try:
            (measure_fn or frames_measure)(path, shot_id)
            (apply_fn or frames_apply)(path, shot_id)
            camera = load_shot(path, shot_id)['camera']
        finally:   # the shot and its pictures stay as they were until the user picks
            shot_path(path, shot_id).write_text(original, encoding='utf-8')
            _restore(path, shot_id, records)
        axes = ', '.join(f'{k} {v}' for k, v in o['axes'].items())
        takes.append({'id': f"o{o['option']}", 'label': axes, 'why': f"{o['role']}: {plan['user_words']}", 'change': {'camera': camera},
                      'pictures': [c['concept_id'] for c in pictures]})
    if len(takes) < 2:
        raise StudioError('INPUT_INVALID', f'{shot_id} {folder.name}: {len(takes)} option(s) fully drawn; draw options {missing} (reel redo add)')
    sheet = (variants_fn or variants)(path, shot_id, [{k: t[k] for k in ('id', 'label', 'why', 'change')} for t in takes], list(times))
    plan['built'] = {'sheet': sheet['rev'], 'takes': [{'id': t['id'], 'pictures': t['pictures']} for t in takes], 'at': now()}
    write_json(folder / 'plan.json', plan)
    return {**sheet, 'round': folder.name, 'missing': missing}


def pick(project, shot_id, option, user_words, rnd=None, pick_fn=None):
    """The user's option becomes the shot: its take (storyboard pick, their words) and its pictures (the moments'
    pictures from now on); the pick goes to the taste against the options passed over."""
    from .concept import _folder, concepts
    from .generative.review import check_user_words
    from .storyboard import pick as storyboard_pick
    from .taste_picks import record
    words = check_user_words(user_words, 'redo pick')
    path = project_dir(project)
    folder, plan = _plan(path, shot_id, rnd)
    built = plan.get('built')
    take = next((t for t in (built or {}).get('takes', []) if t['id'] == f'o{int(option)}'), None)
    if take is None or plan.get('picked'):
        raise StudioError('INPUT_INVALID', f"{shot_id} {folder.name}: option {option} is not built (or the round is picked)")
    result = (pick_fn or storyboard_pick)(path, shot_id, built['sheet'], take['id'], words)
    for c in concepts(path, shot_id):   # the picked option's pictures are the moments' pictures now
        if c.get('variant'):
            variant = {k: v for k, v in c['variant'].items() if k != 'chosen'}
            if c['concept_id'] in take['pictures']:
                variant['chosen'] = {'by': 'user', 'at': now()}
            write_json(_folder(path, shot_id) / f"{c['concept_id']}.json", {**c, 'variant': variant})
    chosen = next(o for o in plan['options'] if o['option'] == int(option))
    record('redo', chosen['axes'], [o['axes'] for o in plan['options'] if o['option'] != int(option)], words, path, ref=f'{shot_id} {folder.name}')
    plan['picked'] = {'option': int(option), 'user_words': words, 'at': now()}
    write_json(folder / 'plan.json', plan)
    return {'shot_id': shot_id, 'round': folder.name, 'picked': int(option), 'storyboard': result}


def register_commands(subparsers):
    parser = subparsers.add_parser('reel', help='The reel as a whole: clay cut with captions, picture flow, redo a shot as options')
    commands = parser.add_subparsers(dest='reel_command', required=True)
    p = commands.add_parser('flow', help='how the built pictures run shot to shot (studio/reel_flow.REEL_FLOW)')
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: __import__('studio.reel_flow', fromlist=['built']).built(a.project))
    p = commands.add_parser('clay', help='every built shot as clay with its number and lines, cut together, on one page')
    p.add_argument('--project', required=True); p.add_argument('--shots', type=lambda v: [x for x in v.split(',') if x])
    p.set_defaults(handler=lambda a: clay(a.project, a.shots))
    redo = commands.add_parser('redo', help="one shot again as 2-4 options drawn differently, in the user's words")
    steps = redo.add_subparsers(dest='redo_command', required=True)
    q = steps.add_parser('start'); q.add_argument('--project', required=True); q.add_argument('--shot', required=True)
    q.add_argument('--user-words', required=True); q.add_argument('--count', type=int, default=3)
    q.set_defaults(handler=lambda a: start(a.project, a.shot, a.user_words, a.count))
    q = steps.add_parser('add'); q.add_argument('--project', required=True); q.add_argument('--shot', required=True)
    q.add_argument('--option', required=True, type=int); q.add_argument('--frame', required=True, choices=('start', 'end', 'key'))
    q.add_argument('--at', type=float); q.add_argument('--image'); q.add_argument('--paint', action='store_true'); q.add_argument('--round')
    q.set_defaults(handler=lambda a: add(a.project, a.shot, a.option, a.frame, a.image, a.paint, a.at, a.round))
    q = steps.add_parser('build'); q.add_argument('--project', required=True); q.add_argument('--shot', required=True); q.add_argument('--round')
    q.set_defaults(handler=lambda a: build(a.project, a.shot, a.round))
    q = steps.add_parser('pick'); q.add_argument('--project', required=True); q.add_argument('--shot', required=True)
    q.add_argument('--option', required=True, type=int); q.add_argument('--user-words', required=True); q.add_argument('--round')
    q.set_defaults(handler=lambda a: pick(a.project, a.shot, a.option, a.user_words, a.round))
    return commands

