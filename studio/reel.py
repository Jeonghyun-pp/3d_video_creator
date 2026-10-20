"""The reel as a whole: its clay cut watched with captions, its picture flow, and a shot redone as options.

The user's third look (2026-10-17, "기획단계에서 너무 시간 안쓰고싶어"): the whole reel as clay - every built shot's
Workbench clip with its number and lines on it, one after another - on one page with the reel flow's findings. They
answer with shot numbers and a word each; `reel redo` makes that shot again as 2-3 options drawn differently
(concept variants), each solved and built as a take, shown side by side; the pick in their words becomes the shot.
Workbench only: no look render, allowed unattended (seconds a shot).
"""
from __future__ import annotations

import html
import json
from pathlib import Path
import subprocess

from .common import REPO, StudioError, font_file, lock, now, put_back, read_json, write_json
from .project import load_project, load_shot, project_dir, shot_path

CLAY = 'review/clay'
REDO = 'redo'                  # redo/<shot>/rNN/plan.json
CAPTION_SIZE = 0.042           # caption type height as a share of the frame height (a phone's subtitle size)
LABEL_SIZE = 0.03
CAPTION_ROWS = 3               # caption rows at most: a phone subtitle runs two lines, three at a push; past that the
                               # last row ends in an ellipsis (the page shows the whole line)
CAPTION_BOX_MAX = 0.3          # the caption box never covers more than this share of the frame's height (it would hide the shot)


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


def _fit(draw, text, font, width, height):
    """The caption's rows: wrapped, at most CAPTION_ROWS and no taller than CAPTION_BOX_MAX of the frame, the last row
    cut with an ellipsis when the line runs on."""
    rows = _wrap(draw, text, font, width)
    step = round(font.size * 1.3)
    keep = max(1, min(CAPTION_ROWS, int(CAPTION_BOX_MAX * height // max(step, 1))))
    if len(rows) <= keep:
        return rows
    rows = rows[:keep]
    last = rows[-1]
    while last and draw.textlength(last + '…', font=font) > width:
        last = last[:-1]
    rows[-1] = last.rstrip() + '…'
    return rows


def _probe(clip):
    out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height,r_frame_rate',
                          '-of', 'json', str(clip)], capture_output=True, text=True, check=True)
    s = json.loads(out.stdout)['streams'][0]
    num, den = s['r_frame_rate'].split('/')
    return int(s['width']), int(s['height']), float(num) / float(den or 1)


def caption(clip, target, label, text, fps=None, graphics=None):
    """The clip with its shot label (top left) and its lines (bottom, wrapped) drawn on every frame: Pillow draws, ffmpeg
    reads and writes (no ffmpeg text filter is needed). graphics: the shot's explainer graphics layer (a folder of
    frame_NNNNNN.png, studio/graphics.py), laid over each frame under the words, as the edit lays it."""
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
    index = 0
    while True:
        raw = reader.stdout.read(size)
        if len(raw) < size:
            break
        frame = Image.frombytes('RGB', (width, height), raw)
        layer = Path(graphics) / f'frame_{index:06d}.png' if graphics else None
        if layer is not None and layer.is_file():
            with Image.open(layer) as drawn:
                over = drawn.convert('RGBA').resize((width, height))
            frame = Image.alpha_composite(frame.convert('RGBA'), over).convert('RGB')
        index += 1
        draw = ImageDraw.Draw(frame, 'RGBA')
        draw.rounded_rectangle((pad, pad, 2 * pad + draw.textlength(label, font=small), 2 * pad + small.size), pad // 2, fill=(0, 0, 0, 150))
        draw.text((1.5 * pad, 1.3 * pad), label, font=small, fill='white')
        rows = _fit(draw, text, big, width - 4 * pad, height) if text else []
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


def clay(project, shots=None, clip_of=None, remote=False):
    """Every built shot's clay clip (storyboard's animatic: the layout profile, Workbench), captioned and cut together,
    on one page with the reel flow (review/clay/index.html, reel_clay.mp4). clip_of(path, shot_id, version) -> the
    clip's project-relative path (tests). remote: the clips are rendered on the rented GPU server, every one queued
    before any is waited for (one server boots once for the queue; the month's cap is checked as each joins)."""
    from .reel_flow import built as reel_flow
    from .storyboard import _animatic, animatic_clip
    path = project_dir(project)
    data = load_project(path)
    out = path / CLAY
    out.mkdir(parents=True, exist_ok=True)
    queued = {}
    if remote and clip_of is None:
        from .jobs import submit_render
        for entry in data['shots']:
            version = load_shot(path, entry['shot_id']).get('scene_version')
            if (not shots or entry['shot_id'] in shots) and version and not animatic_clip(path, entry['shot_id'], version).is_file():
                try:   # a shot its gates refuse is said on the page like any failed clip; the others still render
                    queued[entry['shot_id']] = submit_render(path, entry['shot_id'], version, profile='layout', remote=True)
                except (StudioError, OSError, subprocess.CalledProcessError) as error:
                    queued[entry['shot_id']] = error

    def remote_clip(p, s, v):
        if isinstance(queued.get(s), Exception):
            raise queued[s]
        return _animatic(p, s, v, remote=True, job=queued.get(s))['animatic']
    clip_of = clip_of or (remote_clip if remote else (lambda p, s, v: _animatic(p, s, v)['animatic']))
    rows, skipped, failed, graphics_failed = [], [], [], []
    for entry in data['shots']:
        if shots and entry['shot_id'] not in shots:
            continue
        shot = load_shot(path, entry['shot_id'])
        version = shot.get('scene_version')
        generated = (shot.get('route') or {}).get('mode') == 'generative'
        if not version and not generated:
            skipped.append(entry['shot_id'])
            continue
        target = out / f"{entry['shot_id']}.mp4"
        text = (shot.get('narration') or {}).get('text') or ''
        try:   # one shot's clip failing is said on the page; the others are still cut together
            if generated and not version:   # a cut a model makes (studio/maker.py): its take, else its picture held
                source, version = generated_clip(path, shot, entry['frame_count'], data['output'], out)
            else:
                source = path / clip_of(path, entry['shot_id'], version)
            # the shot's explainer graphics (a flow, an arrow) belong to what the clay shows: they explain (2026-10-20)
            layer = None
            if shot.get('graphics'):
                try:
                    layer = graphics_of(path, entry['shot_id'], _probe(source)[1])
                except (StudioError, OSError, subprocess.CalledProcessError) as error:   # the clip is still shown, said
                    graphics_failed.append({'shot_id': entry['shot_id'], 'error': f'{type(error).__name__}: {error}'[:300]})
            caption(source, target, f"{entry['shot_id']}  {version}", text, data['output'].get('fps'), graphics=layer)
        except (StudioError, OSError, subprocess.CalledProcessError) as error:
            failed.append({'shot_id': entry['shot_id'], 'version': version, 'error': f'{type(error).__name__}: {error}'[:300]})
            continue
        rows.append({'shot_id': entry['shot_id'], 'version': version, 'clip': target.name, 'text': text,
                     'seconds': round(entry['frame_count'] / data['output']['fps'], 2)})
    if not rows:
        raise StudioError('INPUT_INVALID', 'no built shot to show (shot build first)' + (f'; failed: {failed}' if failed else ''))
    reel = out / 'reel_clay.mp4'
    _join([out / r['clip'] for r in rows], reel, data['output'].get('fps'))
    measured = reel_flow(path)
    flow = measured['errors'] + measured['warnings']
    (out / 'index.html').write_text(_page(data.get('project_id') or path.name, rows, flow, skipped, path, failed), encoding='utf-8')
    write_json(out / 'clay.json', {'schema_version': 1, 'made_at': now(), 'shots': rows, 'skipped': skipped, 'failed': failed, 'flow': flow})
    return {'reel': str(reel), 'page': str(out / 'index.html'), 'shots': [r['shot_id'] for r in rows], 'skipped': skipped,
            'failed': failed, 'warnings': flow + [f"CLAY_SHOT_FAILED: {f['shot_id']} {f['version']}: {f['error']}" for f in failed]
            + [f"CLAY_GRAPHICS_FAILED: {g['shot_id']} shown without its graphics: {g['error']}" for g in graphics_failed],
            'artifacts': [str(reel), str(out / 'index.html')]}


def generated_clip(path, shot, frames, output, out):
    """(clip, label) of a generated cut in the clay: its take when one is made and usable (edit.latest_generated), else
    its start picture held for the cut's length, labelled 'not generated yet' - so the clay runs the whole reel."""
    from .edit import latest_generated
    try:
        take = latest_generated(path, shot, frames, final=False)
    except StudioError:
        take = None
    if take and take.get('clip'):
        return Path(take['clip']), 'generated'
    from .generative.inputs import first_picture
    picture = first_picture(path, shot['shot_id'])
    if not picture:
        raise StudioError('INPUT_MISSING', f"{shot['shot_id']}: a generated cut with no take and no start picture")
    held = out / f"{shot['shot_id']}_held.mp4"
    fps = output.get('fps') or 30
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-loop', '1', '-i', str(path / picture), '-frames:v', str(frames), '-r', str(fps),
                    '-vf', f"scale={output['width']}:{output['height']}:force_original_aspect_ratio=decrease,"
                           f"pad={output['width']}:{output['height']}:(ow-iw)/2:(oh-ih)/2,setsar=1",
                    '-pix_fmt', 'yuv420p', str(held)], check=True)
    return held, 'not generated yet (picture)'


def graphics_of(path, shot_id, height):
    """The folder of the shot's graphics layer at about this height (rendered once, reused: studio/graphics.py)."""
    from .graphics import render_graphics
    return render_graphics(path, shot_id, height=max(2, int(height)))['frames_dir']


def _join(clips, target, fps=None):
    """Clips of any sizes and rates cut into one: each scaled into the first clip's frame (padded, never stretched) at
    one rate, then joined."""
    width, height, rate = _probe(clips[0])
    rate = fps or rate
    inputs = [x for c in clips for x in ('-i', str(c))]
    fit = ''.join(f'[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,'
                  f'setsar=1,fps={rate}[v{i}];' for i in range(len(clips)))
    graph = fit + ''.join(f'[v{i}]' for i in range(len(clips))) + f'concat=n={len(clips)}:v=1:a=0[out]'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *inputs, '-filter_complex', graph, '-map', '[out]', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', '-crf', '20', str(target)], check=True)
    return target


def _page(title, rows, flow, skipped, path, failed=()):
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
            + ''.join(f"<div class='warn'>{e(f['shot_id'])} 클립을 만들지 못함: {e(f['error'])}</div>" for f in failed)
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


def start(project, shot_id, user_words, count=3, fixed=None):
    """A redo of one shot in the user's words: count options drawn differently (concept variants on CONCEPT_AXES, away
    from what the shot has been drawn as), each with a prompt per pictured moment that carries the user's words. fixed
    ('size=close', 'size=medium|close'): the axis values their words settle, held by every option - the options differ on
    the rest, never against what was asked."""
    from .concept import brief, variant_axes, variants_of
    from .generative.review import check_user_words
    words = check_user_words(user_words, 'redo')
    path = project_dir(project)
    load_shot(path, shot_id)
    if not 2 <= int(count) <= 4:
        raise StudioError('INPUT_INVALID', 'a redo makes 2-4 options')
    held = {}
    for part in (str(fixed).split(',') if fixed else []):   # size=medium|close: one of these values
        axis, _, values = part.partition('=')
        held[axis] = [next(iter(variant_axes(f'{axis}={v}').values())) for v in values.split('|')]
    moments = _moments(path, shot_id)
    drawn = variants_of(path, shot_id, moments[0], int(count), fixed=held)
    if len(drawn) < 2:
        raise StudioError('INPUT_INVALID', f'--fixed {fixed}: leaves {len(drawn)} option(s) on the axes - fix fewer axes')
    options = []
    for d in drawn:
        prompts = []
        for m in moments:
            base = brief(path, shot_id, m['role'], m['at'] if m['role'] == 'key' else None, framing=False)   # the option's own size
            lines = base['prompt'].split('\n')
            prompts.append({'frame': m, 'prompt': '\n'.join(lines[:-1] + [d['line'], f'The user asked for this shot: {words}', lines[-1]]),
                            'attach': [a['path'] for a in base['attach']]})
        options.append({'option': d['variant'], 'role': d['role'], 'axes': d['axes'], 'moments': prompts})
    folder = project_dir(path) / REDO / shot_id / f'r{len(_rounds(path, shot_id)) + 1:02d}'
    folder.mkdir(parents=True, exist_ok=True)
    plan = {'schema_version': 1, 'shot_id': shot_id, 'round': folder.name, 'user_words': words, 'fixed': held, 'started_at': now(),
            'options': options}
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
    """Mark exactly these variant pictures chosen (frames() then reads them as the moments' pictures); the caller puts
    the records back (common.put_back)."""
    from .concept import _folder, concepts
    for c in concepts(path, shot_id):
        if not c.get('variant'):
            continue
        variant = {k: v for k, v in c['variant'].items() if k != 'chosen'}
        if c['concept_id'] in ids:
            variant['chosen'] = {'by': 'redo', 'at': now()}
        write_json(_folder(path, shot_id) / f"{c['concept_id']}.json", {**c, 'variant': variant})


def build(project, shot_id, rnd=None, times=(0.0, 0.5, 1.0), measure_fn=None, apply_fn=None, variants_fn=None, subject=None):
    """Each option with all its moments drawn: read its pictures, solve the camera to them (frames measure, frames
    apply), keep that camera as a take, put the shot and the pictures back; then build the takes side by side
    (storyboard variants). Nothing is picked here. subject: what the pictures show in words (frames measure --subject),
    needed when the shot frames parts and no words were given for them; kept with the round for its next build."""
    from .frame_targets import apply as frames_apply, measure as frames_measure
    from .storyboard import variants
    path = project_dir(project)
    folder, plan = _plan(path, shot_id, rnd)
    if subject:
        plan['subject'] = subject
        write_json(folder / 'plan.json', plan)
    words = plan.get('subject')
    moments = [m['frame'] for m in plan['options'][0]['moments']]
    readings = path / 'concepts' / shot_id / 'frames.json'   # what frames measure rewrites while an option is solved
    takes, missing = [], []
    for o in plan['options']:
        pictures = _option_pictures(path, shot_id, {'round': folder.name, 'option': o['option']}, moments)
        if not all(pictures):
            missing.append(o['option'])
            continue
        # one hold while the shot, its pictures and their readings are the option's: nothing else reads them half-swapped;
        # put back when the solve ends, or by the next taker of the lock if this process is killed inside (common.put_back)
        from .concept import _folder, concepts
        records = [_folder(path, shot_id) / f"{c['concept_id']}.json" for c in concepts(path, shot_id) if c.get('variant')]
        with lock(path / '.project.lock', blocking=False, reentrant=True), put_back(path, [shot_path(path, shot_id), readings, *records]):
            _choose(path, shot_id, {c['concept_id'] for c in pictures})
            (measure_fn or frames_measure)(path, shot_id, words)
            (apply_fn or frames_apply)(path, shot_id)
            camera = load_shot(path, shot_id)['camera']
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
    p = commands.add_parser('rules', help='the direction rules, their thresholds and the study set\'s numbers per role - read before a shot list')
    p.add_argument('--project')
    p.set_defaults(handler=lambda a: __import__('studio.reel_flow', fromlist=['rules']).rules(a.project))
    p = commands.add_parser('flow', help='how the built pictures run shot to shot (studio/reel_flow.REEL_FLOW)')
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: __import__('studio.reel_flow', fromlist=['built']).built(a.project))
    p = commands.add_parser('depart', help='a flow finding the whole reel departs from on purpose (the user\'s words, or a delegated run\'s reason)')
    p.add_argument('--project', required=True); p.add_argument('--code', required=True); p.add_argument('--why')
    p.add_argument('--user-words'); p.add_argument('--agent-note'); p.add_argument('--remove', action='store_true')
    p.set_defaults(handler=lambda a: __import__('studio.reel_flow', fromlist=['depart']).depart(a.project, a.code, a.why, a.user_words,
                                                                                               a.agent_note, a.remove))
    p = commands.add_parser('clay', help='every built shot as clay with its number and lines, cut together, on one page')
    p.add_argument('--project', required=True); p.add_argument('--shots', type=lambda v: [x for x in v.split(',') if x])
    p.add_argument('--remote', action='store_true', help='render the clips on the rented GPU server (studio gpu enable first)')
    p.set_defaults(handler=lambda a: clay(a.project, a.shots, remote=a.remote))
    redo = commands.add_parser('redo', help="one shot again as 2-4 options drawn differently, in the user's words")
    steps = redo.add_subparsers(dest='redo_command', required=True)
    q = steps.add_parser('start'); q.add_argument('--project', required=True); q.add_argument('--shot', required=True)
    q.add_argument('--user-words', required=True); q.add_argument('--count', type=int, default=3)
    q.add_argument('--fixed', help="axis values the user's words settle, held by every option: size=close, size=medium|close (concept.CONCEPT_AXES)")
    q.set_defaults(handler=lambda a: start(a.project, a.shot, a.user_words, a.count, a.fixed))
    q = steps.add_parser('add'); q.add_argument('--project', required=True); q.add_argument('--shot', required=True)
    q.add_argument('--option', required=True, type=int); q.add_argument('--frame', required=True, choices=('start', 'end', 'key'))
    q.add_argument('--at', type=float); q.add_argument('--image'); q.add_argument('--paint', action='store_true'); q.add_argument('--round')
    q.set_defaults(handler=lambda a: add(a.project, a.shot, a.option, a.frame, a.image, a.paint, a.at, a.round))
    q = steps.add_parser('build'); q.add_argument('--project', required=True); q.add_argument('--shot', required=True); q.add_argument('--round')
    q.add_argument('--subject', help='what the pictures show, in words - a shot that frames parts (frames measure --subject); kept for the round')
    q.set_defaults(handler=lambda a: build(a.project, a.shot, a.round, subject=a.subject))
    q = steps.add_parser('pick'); q.add_argument('--project', required=True); q.add_argument('--shot', required=True)
    q.add_argument('--option', required=True, type=int); q.add_argument('--user-words', required=True); q.add_argument('--round')
    q.set_defaults(handler=lambda a: pick(a.project, a.shot, a.option, a.user_words, a.round))
    return commands

