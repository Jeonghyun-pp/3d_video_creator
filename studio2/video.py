"""The paid video of a cut: its approved first picture moved by an image-to-video model, checked, marked and put in
the edit only after a person passed its motion.

- make: only a cut the user approved on the sheet (studio2/sheet.approved: their words, bound to exactly this request),
  through v1's generative/clip.py - its adapter (build_arguments), the paid call with its ledger and budget (paid_call,
  never above the approved price) and its retime to the cut's frames; made once per request (shots/<cut>/video/<fp>/).
- check: a reader on three frames of the video and the three approved pictures, with the line: does the motion match
  what is said, does its middle stay near the approved middle, does nothing morph or melt, does it end near the last
  picture. Not calibrated on real takes yet, so its answers are advice for the person who reviews the motion.
- composite: our marks (labels, flows, specks) located again on the video's own frames (studio2/locate.py's reader,
  every second and at both ends, judged on the middle one), followed between them, and drawn over every frame
  (motion.draw) - a composite clip like the free one, made from the video.
- review: a person's words on the motion (v1's user-words path; never in a run without its user): passed, the marked
  video becomes the cut's clip (route.composite); turned down, the free clip stays.

Why (2026-10-26, the plan's phase 5; the user: "영상으로 만들려면 당연히 생성모델"; 2026-10-16: generated motion is checked by a
person and against the narration before a shot moves on).
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

from studio.common import StudioError, file_hash, now, read_json, stable_hash, write_json

# The video checks: a row is a floor only where calibration on real takes bore it out - none has been made yet.
CHECKS = {
    'motion_matches_line': {'asks': 'Does what moves or changes over the video (images 0-2) show what is said over it - "{line}" - '
                                    'and what its viewer should learn: "{point}"?', 'floor': False},
    'middle_near': {'asks': 'Is the video\'s middle frame (image 1) close to the approved middle picture (image 4): the same place, the '
                            'same framing and parts, the change about as far along?', 'floor': False},
    'no_morphing': {'asks': 'Do things keep their shape across the video\'s frames - nothing melts, bends, morphs, appears or vanishes '
                            'beyond what the line says changes?', 'floor': False},
    'ends_near_last': {'asks': 'Is the video\'s last frame (image 2) close to the approved last picture (image 5): the same place and '
                               'framing, the change done as it shows?', 'floor': False},
}
CHECK_HEAD = """Images 0, 1 and 2 are the first, middle and last frames of a short video made by a video model for one cut
of a vertical explainer video. Images 3, 4 and 5 are the pictures the user approved for that cut: its first, middle and
last. Answer each question only from what you see, ok true or false and why in one short sentence."""
LOCATE_EVERY_S = 1.0     # marks are located again on a video frame this often (and at both ends)
WAY_POINTS = 16          # a way is followed between readings at this many points along it


def _cut(path, cut_id):
    from studio import decisions
    from .frames import cut_of
    body = decisions.envelope(path, 'cuts')['body']
    return body, cut_of(body, cut_id)


def folder_of(path, cut_id, fingerprint):
    from studio.project import shot_path
    return shot_path(path, cut_id).parent / 'video' / fingerprint


def made(path, cut_id):
    """The made video of a cut's current approved request, or None."""
    from .sheet import approved
    approval = approved(path, cut_id)
    if approval is None:
        return None
    file = folder_of(path, cut_id, approval['fingerprint']) / 'video.json'
    return read_json(file) if file.is_file() else None


def make(project, cut_id):
    """The cut's paid video, only as the user approved it (module docstring)."""
    from studio.generative import clip as clipmod
    from studio.project import load_project, load_shot, project_dir
    from .sheet import approved, request_of
    path = project_dir(project)
    body, cut = _cut(path, cut_id)
    approval = approved(path, cut_id)
    if approval is None:
        raise StudioError('PAID_UNAPPROVED', f'{cut_id}: no approval of its paid video as it would be sent now (none on the sheet, reopened, '
                          'or the cut changed after the words)', recovery='studio2 sheet build, show it to the user, sheet approve with their words')
    request = request_of(path, body, cut)
    folder = folder_of(path, cut_id, request['fingerprint'])
    if (folder / 'video.json').is_file():
        return {**read_json(folder / 'video.json'), 'cached': True}
    if request['usd'] > approval['usd'] + 1e-9:
        raise StudioError('BUDGET_EXCEEDED', f"{cut_id}: ${request['usd']} is more than the approved ${approval['usd']}")
    project_data = load_project(path)
    shot = load_shot(path, cut_id)
    arguments = clipmod.build_arguments(path, project_data, request['spec'], request['prompt'])
    record = clipmod.paid_call(request['endpoint'], arguments, folder / 'request', project_id=project_data['project_id'], allow_paid=True,
                               max_usd=approval['usd'], budget_usd=(project_data.get('route_policy') or {}).get('budget_usd'),
                               duration_seconds=request['billed_s'])
    videos = [f for f in record['files'] if Path(f['path']).suffix.lower() in ('.mp4', '.mov', '.webm')]
    if not videos:
        raise StudioError('GENERATION_REJECTED', f"{request['endpoint']} returned no video file")
    raw = folder / 'raw' / Path(videos[0]['path']).name
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(Path(videos[0]['path']).read_bytes())
    clip = folder / 'clip.mp4'
    out = project_data['output']
    source = clipmod.retime(raw, clip, shot['duration_frames'], out['width'], out['height'])
    from studio.qa_generative import flicker, morph
    qa = {name: {k: v for k, v in check(clip).items() if k != 'frames'} for name, check in (('flicker', flicker), ('morph', morph))}
    row = {'schema_version': 1, 'status': 'complete', 'cut': cut_id, 'fingerprint': request['fingerprint'], 'endpoint': request['endpoint'],
           'request_id': record['request_id'], 'usd': record['estimated_usd'], 'approval': {k: approval[k] for k in ('sheet', 'user_words', 'at')},
           'frame_count': shot['duration_frames'], 'clip': str(clip), 'clip_sha256': file_hash(clip), 'raw_sha256': file_hash(raw),
           'source': {k: source[k] for k in ('width', 'height', 'fps', 'frames', 'duration')}, 'qa': qa, 'made_at': now()}
    write_json(folder / 'video.json', row)
    return {**row, 'cached': False, 'artifacts': [str(clip), str(folder / 'video.json')],
            'next': [f'studio2 video check --cut {cut_id}', f'studio2 video composite --cut {cut_id}', 'show the person the marked clip; '
                     f'studio2 video review --cut {cut_id} --user-words "<their words>"']}


def _grab(clip, seconds, target):
    """One frame at `seconds`; a time at the very end may decode nothing, so it steps back a frame at a time (up to 0.5 s)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    for back in range(16):
        if target.is_file():
            break
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{max(0.0, seconds - back / 30):.3f}', '-i', str(clip), '-frames:v', '1', str(target)],
                       check=True)
    if not target.is_file():
        raise StudioError('INPUT_INVALID', f'{clip}: no frame near {seconds:.2f} s')
    return target


def _need(path, cut_id):
    row = made(path, cut_id)
    if row is None:
        raise StudioError('VIDEO_UNMADE', f'{cut_id}: no paid video made for its approved request', recovery=f'studio2 video make --cut {cut_id}')
    return row


def check(project, cut_id, reader=None):
    """The video against the approved pictures and the line (CHECKS): {answers, failures (floors), warnings (advice)}."""
    from studio import decisions
    from studio.project import project_dir
    from studio.screen_grammar import codex_json
    from .sheet import SHEET, approved
    path = project_dir(project)
    body, cut = _cut(path, cut_id)
    row = _need(path, cut_id)
    folder = folder_of(path, cut_id, row['fingerprint'])
    seconds = row['frame_count'] / 30
    frames = [_grab(row['clip'], t, folder / 'check' / f'{name}.png') for name, t in
              (('first', 0.0), ('middle', seconds / 2), ('last', seconds - 1 / 30))]
    sheet_dir = path / SHEET / approved(path, cut_id)['sheet']
    pictures = read_json(sheet_dir / 'sheet.json')
    shown = next(r for r in pictures['cuts'] if r['cut'] == cut_id)['pictures']
    approved_pictures = [sheet_dir / shown[name]['file'] for name in ('first', 'middle', 'last')]
    lines = {l['line_id']: l['text'] for l in decisions.envelope(path, 'script')['body']['lines']}
    line = ' '.join(lines[i] for i in cut['line_ids'] if i in lines)
    asks = {k: r['asks'].format(line=line, point=cut['show_how']['point']) for k, r in CHECKS.items()}
    prompt = CHECK_HEAD + '\n' + '\n'.join(f'- {k}: {a}' for k, a in asks.items())
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['answers', 'note'], 'properties': {
        'answers': {'type': 'array', 'minItems': len(asks), 'maxItems': len(asks), 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['id', 'ok', 'why'],
            'properties': {'id': {'enum': list(asks)}, 'ok': {'type': 'boolean'}, 'why': {'type': 'string'}}}}, 'note': {'type': 'string'}}}
    target = folder / 'check' / 'read.json'
    (reader or codex_json)(frames + approved_pictures, prompt, schema, target)
    got = {a['id']: {'ok': a['ok'], 'why': a['why']} for a in read_json(target)['answer']['answers']}
    wrong = [k for k in CHECKS if not got.get(k, {}).get('ok')]
    result = {'cut': cut_id, 'answers': got, 'failures': [k for k in wrong if CHECKS[k]['floor']],
              'warnings': [k for k in wrong if not CHECKS[k]['floor']], 'qa': row['qa'], 'at': now()}
    write_json(folder / 'check.json', result)
    return result


# --- our marks over the video -------------------------------------------------------------------------------------

def _resample(points, n=WAY_POINTS):
    from .motion import along
    return [along(points, k / (n - 1)) for k in range(n)] if len(points) >= 2 else list(points)


def _lerp(p, q, k):
    return (p[0] + (q[0] - p[0]) * k, p[1] + (q[1] - p[1]) * k)


def _lerp_plan(a, b, k):
    """The plan between two located plans of one cut: anchors, label spots, flow ways and the moving path followed."""
    if k <= 0 or b is None:
        return a
    if k >= 1:
        return b
    out = dict(a)
    if len(a['labels']) == len(b['labels']):
        out['labels'] = [(w, _lerp(an, bn, k), _lerp(sa, sb, k), share) for (w, an, sa, share), (_w, bn, sb, _s) in zip(a['labels'], b['labels'])]
    if len(a['flows']) == len(b['flows']):
        out['flows'] = [([_lerp(p, q, k) for p, q in zip(_resample(pa), _resample(pb))], colour) for (pa, colour), (pb, _c) in zip(a['flows'], b['flows'])]
    if a.get('element') and b.get('element') and a['element']['path'] and b['element']['path']:
        out['element'] = {**a['element'], 'path': [_lerp(p, q, k) for p, q in zip(_resample(a['element']['path']), _resample(b['element']['path']))]}
    return out


def composite(project, cut_id, reader=None, checker=None):
    """The video with our marks: located on its frames (every LOCATE_EVERY_S and both ends), the overlay judged on the
    middle one, followed between them and drawn over every frame. Not the cut's clip until a person passes it (review)."""
    from PIL import Image
    from studio.common import REPO
    from studio.project import load_project, project_dir
    from studio.screen_grammar import codex_json
    from studio.timings import timed
    from . import locate as loc
    from .cuts import cut_sha
    from .motion import Marks, code_sha, draw, plan_of
    path = project_dir(project)
    body, cut = _cut(path, cut_id)
    row = _need(path, cut_id)
    folder = folder_of(path, cut_id, row['fingerprint'])
    frames_n = row['frame_count']
    seconds = frames_n / 30
    count = max(3, math.ceil(seconds / LOCATE_EVERY_S) + 1)
    times = [min(seconds - 1 / 30, seconds * i / (count - 1)) for i in range(count)]
    parts = [p['id'] for p in cut['cutaway']['parts']]
    flows = list(range(len(cut['cutaway']['flow'])))
    schema = loc._schema(parts, flows, cut['change'].get('element'), [f.get('ways', 1) for f in cut['cutaway']['flow']])
    prompt = loc.PROMPT.format(items=loc._items(cut), reason='')
    located = []
    for n, t in enumerate(times):
        png = _grab(row['clip'], t, folder / 'locate' / f'frame_{n:02d}.png')
        target = folder / 'locate' / f'read_{n:02d}.json'
        if not (target.is_file() and read_json(target).get('image_sha256') == file_hash(png)):
            (reader or codex_json)([png], prompt, schema, target)
            write_json(target, {**read_json(target), 'image_sha256': file_hash(png)})
        located.append(read_json(target)['answer'])
    missing = sorted({p for places in located for p in loc.unlocated(places, parts)})
    if missing:
        raise StudioError('PLACES_OFF', f"{cut_id}: not located on every video frame: {', '.join(missing)}",
                          recovery='locate again (delete its readings) or write the cut so its parts are what the video shows')
    for places in located:
        places['parts'] = list({p['id']: p for p in places['parts']}.values())
    middle = len(times) // 2
    marks = loc.overlay(folder / 'locate' / f'frame_{middle:02d}.png', located[middle], cut, folder / 'locate' / 'overlay.png')
    off = []
    if marks:
        ctarget = folder / 'locate' / 'overlay_check.json'
        check_schema = {'type': 'object', 'additionalProperties': False, 'required': ['marks'], 'properties': {'marks': {
            'type': 'array', 'minItems': len(marks), 'maxItems': len(marks), 'items': {
                'type': 'object', 'additionalProperties': False, 'required': ['mark', 'ok', 'why'],
                'properties': {'mark': {'enum': [m.split(' = ')[0] for m in marks]}, 'ok': {'type': 'boolean'}, 'why': {'type': 'string'}}}}}}
        (checker or codex_json)([folder / 'locate' / 'overlay.png'], loc.CHECK.format(marks='; '.join(marks)), check_schema, ctarget)
        off = [m for m in read_json(ctarget)['answer']['marks'] if not m['ok']]
    if off:
        raise StudioError('PLACES_OFF', f"{cut_id}: marks off on the video's middle frame ({'; '.join(m['mark'] + ': ' + m['why'] for m in off)})",
                          recovery='locate again (delete its readings) or write the cut so its marks follow what the video shows')
    out = load_project(path)['output']
    w, h = out['width'], out['height']
    plans = [plan_of(path, cut, places, w, h, frames_n, body) for places in located]
    key = stable_hash({'video': row['clip_sha256'], 'places': located, 'code': [code_sha(), file_hash(Path(__file__))], 'cut': cut_sha(cut)})[:16]
    from studio.project import shot_path
    target_dir = shot_path(path, cut_id).parent / 'composite' / key
    manifest = target_dir / 'clip.json'
    if manifest.is_file() and read_json(manifest).get('status') == 'complete':
        return {**read_json(manifest), 'cached': True}
    target_dir.mkdir(parents=True, exist_ok=True)
    clip = target_dir / 'clip.mp4'
    reader_proc = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', str(row['clip']), '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{w}x{h}', '-'],
                                   stdout=subprocess.PIPE)
    writer = subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{w}x{h}', '-r', '30', '-i', '-',
                               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-movflags', '+faststart', str(clip)], stdin=subprocess.PIPE)
    with timed('render', f'composite video {cut_id}'):
        for i in range(frames_n):
            raw = reader_proc.stdout.read(w * h * 3)
            if len(raw) < w * h * 3:
                break
            t = i / 30
            j = max(0, min(len(times) - 2, next((k for k in range(len(times) - 1) if t <= times[k + 1]), len(times) - 2)))
            span = times[j + 1] - times[j]
            plan = _lerp_plan(plans[j], plans[j + 1], (t - times[j]) / span if span > 0 else 0.0)
            frame = draw(Marks(w, h), plan, t).onto(Image.frombytes('RGB', (w, h), raw))
            writer.stdin.write(frame.tobytes())
        writer.stdin.close()
        writer.wait()
        reader_proc.wait()
    if writer.returncode != 0:
        raise StudioError('RENDER_FAILED', f'{cut_id}: ffmpeg exit {writer.returncode}')
    result = {'schema_version': 1, 'status': 'complete', 'cut_id': cut_id, 'cut_sha': cut_sha(cut), 'key': key, 'frame_count': frames_n, 'fps': 30,
              'width': w, 'height': h, 'clip_path': str(clip.relative_to(path)), 'clip_sha256': file_hash(clip), 'generated_pictures': True,
              'generated_video': True, 'video': row['fingerprint'], 'located_at': [round(t, 3) for t in times], 'made_at': now()}
    write_json(manifest, result)
    write_json(folder / 'composite.json', {'key': key, 'at': now()})
    return {**result, 'cached': False, 'artifacts': [str(clip), str(manifest)],
            'next': [f'show the person {clip}; studio2 video review --cut {cut_id} --user-words "<their words>" [--reject]']}


def review(project, cut_id, user_words, accept=True):
    """A person's words on the marked video's motion: passed, it becomes the cut's clip; turned down, the free clip stays."""
    from studio.generative.review import check_user_words
    from studio.project import project_dir
    from .cuts import cut_sha
    from .motion import _name
    words = check_user_words(user_words, 'video review')
    path = project_dir(project)
    _body, cut = _cut(path, cut_id)
    row = _need(path, cut_id)
    folder = folder_of(path, cut_id, row['fingerprint'])
    if not (folder / 'composite.json').is_file():
        raise StudioError('RENDER_REQUIRED', f'{cut_id}: its video carries no marks yet', recovery=f'studio2 video composite --cut {cut_id}')
    key = read_json(folder / 'composite.json')['key']
    record = {'cut': cut_id, 'key': key, 'video': row['fingerprint'], 'accepted': bool(accept), 'user_words': words, 'at': now()}
    log = folder / 'reviews.json'
    write_json(log, {'reviews': (read_json(log)['reviews'] if log.is_file() else []) + [record]})
    if accept:
        _name(path, cut_id, key, cut_sha(cut))
    return {**record, 'artifacts': [str(log)],
            'next': [] if accept else [f'the free clip stays; what the person said goes into the next request: frames make / sheet build for {cut_id}']}


def register_commands(subparsers):
    parser = subparsers.add_parser('video', help='The paid video of an approved cut: made, checked, marked, passed by a person')
    commands = parser.add_subparsers(dest='video_command', required=True)
    for name, handler, helptext in (('make', lambda a: make(a.project, a.cut), "the approved cut's paid video (veo-3.1 image-to-video)"),
                                    ('check', lambda a: check(a.project, a.cut), 'the video against the approved pictures and the line'),
                                    ('composite', lambda a: composite(a.project, a.cut), 'our marks located on its frames and drawn over it')):
        p = commands.add_parser(name, help=helptext)
        p.add_argument('--project', required=True); p.add_argument('--cut', required=True)
        p.set_defaults(handler=handler)
    p = commands.add_parser('review', help="a person's words on the marked video's motion")
    p.add_argument('--project', required=True); p.add_argument('--cut', required=True); p.add_argument('--user-words', required=True)
    p.add_argument('--reject', action='store_true', help='the person turned the motion down: the free clip stays')
    p.set_defaults(handler=lambda a: review(a.project, a.cut, a.user_words, not a.reject))
    return commands
