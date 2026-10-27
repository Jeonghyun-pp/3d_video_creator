"""The paid video of a cut: its approved first picture moved by an image-to-video model, checked, marked and put in
the edit only after a person passed its motion.

- make: only a cut the user approved on the sheet (studio2/sheet.approved: their words, bound to exactly this request),
  through v1's generative/clip.py - its adapter (build_arguments), the paid call with its ledger and budget (paid_call,
  never above the approved price) and its retime to the cut's frames; made once per request (shots/<cut>/video/<fp>/).
- check: a reader on three frames of the video and the three approved pictures, with the line: does the motion match
  what is said, does its middle stay near the approved middle, does nothing morph or melt, does it end near the last
  picture (not calibrated: advice). Then the hold: a reader on frames across the clip against the approved first picture
  and what the clip was asked (`asked`, kept with it at make) - do things keep their shape, count and identity, and is a
  contact the request names (a grip, a carry, a ride) kept; and the clip measured (studio2/video_measure.py): flicker,
  a jump or a repeat, the camera against its planned move (reversing, far below or above the asked size, a held camera
  moving). Each of these reads its limit and whether it is a floor from library/video_checks.json, set by calibration
  on labelled clips (`video calibrate`): a floor only where every labelled good and bad were told apart on every run.
  A floor failing refuses the person's pass (VIDEO_CHECK_FLOOR); the rest is advice for the person.
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

from studio.common import REPO, StudioError, file_hash, now, read_json, stable_hash, write_json

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
last. Labels, leader lines, rings, outlines, flowing dashes and glowing specks in images 3-5 are ours: they are drawn over
the video afterwards, so the video never has them - never count their absence against it; compare the scene itself.
Answer each question only from what you see, ok true or false and why in one short sentence."""
# Why the marks sentence (2026-10-27): fab c03's check said the video "lacks the approved nitrogen and wafer labels and blue
# gas outline" - its middle and last were preview frames, which carry the marks the video is composited with later.
LOCATE_EVERY_S = 1.0     # marks are located again on a video frame this often (and at both ends)
HOLD_FRAMES = 6          # video frames the hold reader sees, evenly from the first to the last
TABLE = REPO / 'library' / 'video_checks.json'
HOLD_HEAD = """Image 0 is the picture the user approved as the first frame of one cut of a vertical explainer video. Images 1-{n} are
{n} frames of the video a video model made from it, in time order from its first frame to its last. The model was asked:
"{prompt}"{holds}
Labels, leader lines, rings, outlines and glowing marks are drawn over the video afterwards: never count their absence.
Answer each question only from what you see, ok true or false and why in one short sentence."""
HOLD_ASKS = {
    'shape_kept': 'Does every thing keep its shape, its count and what it is across images 1-{n}, as image 0 shows it? It fails when a '
                  'thing melts, smears into a blur that changes its outline, merges with another (25 separate thin wafers becoming one '
                  'solid metal cylinder), splits, changes count, grows or loses parts, or turns into something else. What the request '
                  'says moves may move; motion blur that leaves the outline the same before and after is fine; a thing that moves out '
                  'of the frame is not lost.',
}
# Why (2026-10-27, the fab proof's first paid clips): c03's 25 wafers came back one solid cylinder, c01's box hung below
# the hoist meant to clamp it and the hand vanished, c03's push drifted back out, c04 never made its trip - the three-frame
# check above read c03 as keeping its shape. A person still judges every clip (review); these say what to look for.
# A contact is not read: the reader called fab c02's riding box floating on both runs (2026-10-27); it is measured on the
# parts located on the clip, for the pair the plan names (part_measures; library/video_checks.json `replaced`).


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
           'source': {k: source[k] for k in ('width', 'height', 'fps', 'frames', 'duration')}, 'qa': qa, 'asked': asked_of(cut, request),
           'made_at': now()}
    write_json(folder / 'video.json', row)
    return {**row, 'cached': False, 'artifacts': [str(clip), str(folder / 'video.json')],
            'next': [f'studio2 video check --cut {cut_id}', f'studio2 video composite --cut {cut_id}', 'show the person the marked clip; '
                     f'studio2 video review --cut {cut_id} --user-words "<their words>"']}


def asked_of(cut, request):
    """What the clip was asked, kept with it: the plan may change after it is made, and the checks read the clip against
    its own request - the camera, the holds (a pair a hold names is measured), the prompt, and the parts that are located
    on its frames."""
    return {'camera': cut['cutaway']['camera'], 'holds': list((cut.get('video') or {}).get('holds') or []), 'prompt': request['prompt'],
            'negative_prompt': request.get('negative_prompt', ''),
            'parts': [{'id': p['id'], 'words': p['words']} for p in cut['cutaway']['parts']]}


def asked_from_request(folder):
    """What a clip made before `asked` was kept (2026-10-27: fab c01-c04) was asked, from its own records only: the prompt
    and negative prompt its request/request.json sent, the camera read back from those words by the grammar's terms
    (prompts.camera_said: unknown, never guessed, when they name none or two), no holds (it was sent none as a pair), and
    the parts located on its frames (its locate readings, named by their ids). None without a request."""
    from .prompts import camera_said
    folder = Path(folder)
    file = folder / 'request' / 'request.json'
    if not file.is_file():
        return None
    arguments = read_json(file).get('arguments') or {}
    prompt = arguments.get('prompt') or ''
    parts = []
    for reading in sorted((folder / 'locate').glob('read_*.json')):
        parts += [p['id'] for p in (read_json(reading).get('answer') or {}).get('parts') or [] if p['id'] not in parts]
    return {'camera': camera_said(prompt), 'holds': [], 'prompt': prompt, 'negative_prompt': arguments.get('negative_prompt') or '',
            'parts': [{'id': p, 'words': p} for p in parts],
            'from': 'request/request.json (prompt; camera read back from its words) and the parts located on the clip (locate/read_*.json): '
                    'made before what a clip was asked was kept with it'}


def table():
    """library/video_checks.json: each check's measure, limit and whether it is a floor (set by calibrate)."""
    return read_json(TABLE)


def planned(camera):
    """(sign, asked amount) of a camera plan for the measures: its row's measured.zoom, and camera.amount where the row
    says the amount is the picture's growth. A row without `measured` is not measured (None)."""
    from .grammar import row
    measured = (row('camera.move', camera.get('move'), None) or {}).get('measured') if camera.get('move') else None
    if not measured:
        return None, None
    return measured['zoom'], (camera.get('amount') if measured.get('amount') else None)


def _value(measured, path):
    v = measured
    for k in path.split('.'):
        v = (v or {}).get(k)
    return v


def _where(measured, measure, value):
    """Which part or pair a part measure's worst value is, and when (a share of the clip), for the finding."""
    head, _, key = measure.partition('.')
    if head == 'contact':
        for pair in (measured.get('contact') or {}).get('pairs') or []:
            if pair['worst'].get(key) == value:
                at = max((f for f in pair['frames'] if key in f), key=lambda f: f[key], default=None)
                return f" - {pair['held']} against {pair['holder']} ({pair['kind']})" + (f", worst at {at['t']:.0%} of the clip" if at else '')
    if head == 'shape' and key in ('likeness', 'detail'):
        for pid, row in ((measured.get('shape') or {}).get('parts') or {}).items():
            hit = next((t for t, v in row.get(key) or [] if v == value), None)
            if hit is not None:
                return f' - {pid}, at {hit:.0%} of the clip against its first frame'
    if head == 'shape' and key == 'count':
        counts = (measured.get('shape') or {}).get('counts') or {}
        return ' - ' + '; '.join(f'{pid}: {row}' for pid, row in counts.items() if len(set(row)) > 1) if counts else ''
    return ''


def verdicts(measured, answers, checks):
    """Each check of the table on one clip: {id: {ok, value, floor, finding}} (ok None: not measured / not read)."""
    out = {}
    for cid, c in checks.items():
        if 'measure' in c:
            v = _value(measured, c['measure'])
            limit = c.get('limit')
            if v is None or limit is None:
                ok = None
            else:
                ok = not (v > limit if c['flag'] == 'above' else v < limit)
            why = f"{c['measure']} {v} {'over' if c['flag'] == 'above' else 'under'} {limit}{_where(measured, c['measure'], v)}" if ok is False else ''
        else:
            a = answers.get(cid)
            v, ok = (None, None) if a is None else (a['ok'], bool(a['ok']))
            why = (a or {}).get('why', '')
        out[cid] = {'ok': ok, 'value': v, 'floor': bool(c.get('floor')),
                    'finding': f"{c['code']}: {c['means']} - {why}" if ok is False else None}
    return out


def locate_times(seconds):
    """Where on a clip its parts are located: every LOCATE_EVERY_S and at both ends (the composite's marks and the
    check's part measures read the same frames)."""
    count = max(3, math.ceil(seconds / LOCATE_EVERY_S) + 1)
    return [min(seconds - 1 / 30, seconds * i / (count - 1)) for i in range(count)]


def located_on(clip, frames_n, parts, folder, placer=None):
    """The asked parts located on the clip's frames at locate_times: ([frame pictures], [video_parts.frame_parts]). A
    composite's reading of the same frame (locate/read_NN.json, the same picture, every asked part in it) is used as it
    is; otherwise the parts alone are read (check/locate/read_NN.json, kept by the picture and the parts)."""
    from PIL import Image
    from studio.screen_grammar import codex_json
    from . import locate as loc
    from .video_parts import frame_parts
    folder = Path(folder)
    seconds = frames_n / 30
    ids = [p['id'] for p in parts]
    items = 'parts:\n' + '\n'.join(f"  {p['id']}: {p['words']}" for p in parts)
    schema = loc._schema(ids, [], None)
    prompt = loc.PROMPT.format(items=items, reason='')
    images, located = [], []
    for n, t in enumerate(locate_times(seconds)):
        png = _grab(clip, t, folder / 'locate' / f'frame_{n:02d}.png')
        sha = file_hash(png)
        shared = folder / 'locate' / f'read_{n:02d}.json'
        answer = None
        if shared.is_file() and read_json(shared).get('image_sha256') == sha:
            got = read_json(shared)['answer']
            if set(ids) <= {p['id'] for p in got.get('parts') or []}:
                answer = got
        if answer is None:
            own = folder / 'check' / 'locate' / f'read_{n:02d}.json'
            key = stable_hash({'image': sha, 'parts': items})[:16]
            if not (own.is_file() and read_json(own).get('key') == key):
                (placer or codex_json)([png], prompt, schema, own)
                write_json(own, {**read_json(own), 'key': key, 'image_sha256': sha})
            answer = read_json(own)['answer']
        answer = {**answer, 'parts': [p for p in {q['id']: q for q in reversed(answer.get('parts') or [])}.values() if p['id'] in ids]}
        images.append(Image.open(png).convert('RGB'))
        located.append(frame_parts(round(t / seconds, 4) if seconds else 0.0, answer))
    return images, located


def part_measures(clip, frames_n, asked, folder, reader=None, placer=None, tag=''):
    """The asked parts on the clip (studio2/video_parts.py): {contact, shape} - the pairs the asked holds name, measured
    frame by frame; each part's crops against the clip's first frame, and the count reader on them. {} without parts."""
    from . import video_parts as vp
    parts = asked.get('parts') or []
    if not parts:
        return {}
    images, located = located_on(clip, frames_n, parts, folder, placer)
    w, h = images[0].size
    contact = vp.contact(located, asked.get('holds'), table().get('contact_kinds') or {}, aspect=h / w)
    shape = vp.shape(images, located)
    crops = vp.crops(images, located)
    counted = vp.count_read(vp.strips(crops, Path(folder) / f'parts{tag}'), {p['id']: p['words'] for p in parts}, Path(folder) / f'parts{tag}',
                            reader, tag)
    shape.update(count=counted['count'], counts=counted['parts'])
    return {'contact': contact, 'shape': shape, 'located': located}


def hold_read(clip, first, asked, folder, reader=None, frames_n=None, tag=''):
    """The hold reader on HOLD_FRAMES frames across the clip and the approved first picture: {shape_kept} (HOLD_ASKS)."""
    from studio.screen_grammar import codex_json
    seconds = (frames_n or _frame_count(clip)) / 30
    shots = [_grab(clip, min(seconds - 1 / 30, seconds * i / (HOLD_FRAMES - 1)), Path(folder) / 'hold' / f'frame_{i}.png') for i in range(HOLD_FRAMES)]
    from .prompts import said
    holds = ''.join(f' It must hold: "{said(h)}"' for h in asked.get('holds') or [])
    prompt = HOLD_HEAD.format(n=HOLD_FRAMES, prompt=' '.join(asked['prompt'].split()), holds=holds) + '\n' + \
        '\n'.join(f'- {k}: {a.format(n=HOLD_FRAMES)}' for k, a in HOLD_ASKS.items())
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['answers', 'note'], 'properties': {
        'answers': {'type': 'array', 'minItems': len(HOLD_ASKS), 'maxItems': len(HOLD_ASKS), 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['id', 'ok', 'why', 'applies', 'pair'],
            'properties': {'id': {'enum': list(HOLD_ASKS)}, 'ok': {'type': 'boolean'}, 'why': {'type': 'string'},
                           'applies': {'type': 'boolean'}, 'pair': {'type': 'string'}}}}, 'note': {'type': 'string'}}}
    target = Path(folder) / 'hold' / f'read{tag}.json'
    (reader or codex_json)([Path(first)] + shots, prompt, schema, target)
    got = {}
    for a in read_json(target)['answer']['answers']:
        applies = a.get('applies', True)
        got[a['id']] = {'ok': bool(a['ok']), 'why': a['why'], 'applies': applies,
                        'pair': a.get('pair', '')}
    return got


def _frame_count(clip):
    out = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_frames',
                          '-of', 'csv=p=0', str(clip)], capture_output=True, text=True, check=True).stdout
    return int(out.strip().split(',')[0])


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


def _clip(path, cut_id, video=None):
    """The made clip to check: the current approved request's (made), or the one made for an earlier request of the cut
    (`video`, its fingerprint: the plan may change after a clip is made - fab c04 became a ride)."""
    if video is None:
        return _need(path, cut_id)
    file = folder_of(path, cut_id, video) / 'video.json'
    if not file.is_file():
        raise StudioError('VIDEO_UNMADE', f'{cut_id}: no paid video {video}', recovery=f'the clips made for {cut_id}: shots/{cut_id}/video/')
    return read_json(file)


def _asked(path, cut_id, row):
    """What the clip was asked (its video.json `asked`); a clip made before it was kept gets it, once, from its own request
    (asked_from_request) - written into its video.json, said where it came from."""
    if 'asked' in row:
        return row['asked']
    folder = folder_of(path, cut_id, row['fingerprint'])
    asked = asked_from_request(folder)
    if asked is None:
        raise StudioError('VIDEO_ASKED_UNKNOWN', f'{cut_id}: its video.json keeps no record of what the clip was asked and it has no '
                          'request/request.json', recovery='a clip whose request is lost cannot be checked against it: make it again')
    write_json(folder / 'video.json', {**row, 'asked': asked})
    row['asked'] = asked
    return asked


def _approval_of(path, cut_id, fingerprint):
    """The user's approval that a clip was made from (approvals.json, by its request's fingerprint), or None."""
    from .sheet import SHEET
    file = Path(path) / SHEET / 'approvals.json'
    rows = [a for a in (read_json(file)['approvals'] if file.is_file() else []) if a['cut'] == cut_id and a['fingerprint'] == fingerprint]
    return rows[-1] if rows else None


def check(project, cut_id, reader=None, placer=None, video=None):
    """The video against the approved pictures and the line (CHECKS), then against what it was asked: the hold reader,
    the measures over time, and its parts located on its frames (contact, shape, count): {answers, measured, verdicts,
    failures (floors), warnings (advice)}. `video`: a clip made for an earlier request of the cut (its fingerprint)."""
    from studio import decisions
    from studio.project import project_dir
    from studio.screen_grammar import codex_json
    from .sheet import SHEET
    path = project_dir(project)
    body, cut = _cut(path, cut_id)
    row = _clip(path, cut_id, video)
    asked = _asked(path, cut_id, row)       # before any reading: a clip with no record of its request stops here
    folder = folder_of(path, cut_id, row['fingerprint'])
    approval = _approval_of(path, cut_id, row['fingerprint'])
    if approval is None:
        raise StudioError('PAID_UNAPPROVED', f"{cut_id}: no approval on record for the request of video {row['fingerprint']}")
    seconds = row['frame_count'] / 30
    frames = [_grab(row['clip'], t, folder / 'check' / f'{name}.png') for name, t in
              (('first', 0.0), ('middle', seconds / 2), ('last', seconds - 1 / 30))]
    sheet_dir = path / SHEET / approval['sheet']
    pictures = read_json(sheet_dir / 'sheet.json')
    shown = next(r for r in pictures['cuts'] if r['cut'] == cut_id)['pictures']
    approved_pictures = [sheet_dir / shown[name]['file'] for name in ('first', 'middle', 'last')]
    from_preview = [f"image {n} is a frame of the free preview (the cut has no {name} picture): judge its framing and where things "
                    'are, not its marks' for n, name in ((3, 'first'), (4, 'middle'), (5, 'last')) if shown[name].get('from') == 'preview']
    lines = {l['line_id']: l['text'] for l in decisions.envelope(path, 'script')['body']['lines']}
    line = ' '.join(lines[i] for i in cut['line_ids'] if i in lines)
    asks = {k: r['asks'].format(line=line, point=cut['show_how']['point']) for k, r in CHECKS.items()}
    prompt = CHECK_HEAD + ''.join(f'\n{said[:1].upper()}{said[1:]}.' for said in from_preview) + '\n' + '\n'.join(f'- {k}: {a}' for k, a in asks.items())
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['answers', 'note'], 'properties': {
        'answers': {'type': 'array', 'minItems': len(asks), 'maxItems': len(asks), 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['id', 'ok', 'why'],
            'properties': {'id': {'enum': list(asks)}, 'ok': {'type': 'boolean'}, 'why': {'type': 'string'}}}}, 'note': {'type': 'string'}}}
    target = folder / 'check' / 'read.json'
    (reader or codex_json)(frames + approved_pictures, prompt, schema, target)
    got = {a['id']: {'ok': a['ok'], 'why': a['why']} for a in read_json(target)['answer']['answers']}
    wrong = [k for k in CHECKS if not got.get(k, {}).get('ok')]
    from . import video_measure
    sign, amount = planned(asked['camera'])
    measured = video_measure.measure(row['clip'], sign, amount)
    measured.update(part_measures(row['clip'], row['frame_count'], asked, folder, reader, placer))
    got.update(hold_read(row['clip'], approved_pictures[0], asked, folder, reader, row['frame_count']))
    checks = table()['checks']
    judged = verdicts(measured, got, checks)
    wrong += [k for k, v in judged.items() if v['ok'] is False]
    floors = {k for k in CHECKS if CHECKS[k]['floor']} | {k for k, v in judged.items() if v['floor']}
    result = {'cut': cut_id, 'video': row['fingerprint'], 'answers': got, 'measured': {k: v for k, v in measured.items() if k not in ('path', 'located')},
              'verdicts': judged, 'findings': [v['finding'] for v in judged.values() if v['finding']],
              'unmeasured': [k for k, v in judged.items() if v['ok'] is None],
              'failures': [k for k in wrong if k in floors], 'warnings': [k for k in wrong if k not in floors], 'qa': row['qa'], 'at': now()}
    write_json(folder / 'measure_path.json', {'camera': measured['path'], 'located': measured.get('located')})
    write_json(folder / 'check.json', result)
    return result


# --- our marks over the video -------------------------------------------------------------------------------------

def composite(project, cut_id, reader=None, checker=None):
    """The video with our marks: located on its frames (every LOCATE_EVERY_S and both ends), the overlay judged on the
    middle one, followed between them and drawn over every frame. Not the cut's clip until a person passes it (review)."""
    from PIL import Image
    from studio.project import load_project, project_dir
    from studio.screen_grammar import codex_json
    from studio.timings import timed
    from . import locate as loc
    from .cuts import cut_sha
    from .motion import Marks, code_sha, draw, lerp_plan, plan_of
    path = project_dir(project)
    body, cut = _cut(path, cut_id)
    row = _need(path, cut_id)
    folder = folder_of(path, cut_id, row['fingerprint'])
    frames_n = row['frame_count']
    seconds = frames_n / 30
    times = locate_times(seconds)
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
            plan = lerp_plan(plans[j], plans[j + 1], (t - times[j]) / span if span > 0 else 0.0)
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
    if accept:
        checked = read_json(folder / 'check.json') if (folder / 'check.json').is_file() else None
        if checked is None or 'verdicts' not in checked:
            raise StudioError('VIDEO_UNCHECKED', f'{cut_id}: its video was not checked (shape, contact, time)',
                              recovery=f'studio2 video check --cut {cut_id}, then show the person its findings with the clip')
        if checked['failures']:
            raise StudioError('VIDEO_CHECK_FLOOR', f"{cut_id}: the video fails a calibrated floor ({', '.join(checked['failures'])}): "
                              + '; '.join(f for f in checked.get('findings', []) if f), recovery='turn it down (--reject) and change the request: '
                              'frames make / cuts write for the cut, then a new sheet - library/video_checks.json says why each is a floor')
    record = {'cut': cut_id, 'key': key, 'video': row['fingerprint'], 'accepted': bool(accept), 'user_words': words, 'at': now()}
    log = folder / 'reviews.json'
    write_json(log, {'reviews': (read_json(log)['reviews'] if log.is_file() else []) + [record]})
    if accept:
        _name(path, cut_id, key, cut_sha(cut))
    return {**record, 'artifacts': [str(log)],
            'next': [] if accept else [f'the free clip stays; what the person said goes into the next request: frames make / sheet build for {cut_id}']}


def _limit(goods, bads, flag):
    """The limit that tells labelled goods from bads for a measure flagged above (or below) it: (limit, separates, misses)."""
    if not goods or not bads:
        return None, False, None
    if flag == 'above' and max(goods) < min(bads):
        return round((max(goods) + min(bads)) / 2, 4), True, 0
    if flag == 'below' and min(goods) > max(bads):
        return round((min(goods) + max(bads)) / 2, 4), True, 0
    values = sorted(set(goods + bads))
    cuts = [(a + b) / 2 for a, b in zip(values, values[1:])] or values
    def misses(t):
        return sum((g > t) if flag == 'above' else (g < t) for g in goods) + sum((b <= t) if flag == 'above' else (b >= t) for b in bads)
    best = min(cuts, key=misses)
    return round(best, 4), False, misses(best)


def _seconds(clip):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(clip)], capture_output=True,
                         text=True, check=True).stdout
    return float(out.strip())


def calibrate(set_file, out, runs=2, reader=None, write=False, placer=None, only=None):
    """The checks on clips whose answer a person gave by watching them: set_file {items: [{name, clip, first (the
    approved first picture; no hold reader without it), asked {camera, prompt, holds, parts}, labels {label: true good |
    false bad}, why}]} (a label an item leaves out is unclear for it and not counted). A check is judged against the label
    its row names (`label`, else its own id): the contact measures against the person's contact_kept, say. Each run
    measures every clip, locates its asked parts afresh (placer) and reads it (reader). A measure is a floor when one
    limit puts every labelled good on its pass side and every labelled bad on its fail side in every run (the limit is
    the middle of the gap); a reading when it answers every good ok and every bad not ok in every run. Otherwise it stays
    advice, its best limit shown. `only`: the checks to calibrate (the rest keep their rows). -> the table."""
    from concurrent.futures import ThreadPoolExecutor
    from . import video_measure
    items = read_json(set_file)['items']
    base = table()
    rows = {k: c for k, c in base['checks'].items() if not only or k in only}
    out = Path(out)
    answers_needed = any('measure' not in c for c in rows.values())
    parts_needed = any(c.get('measure', '').split('.')[0] in ('contact', 'shape') for c in rows.values())

    def label(item, cid):
        """An item's label for a check: its own (the way the person said it broke, or the break that was made), else its
        family's (`label`) where that is good - a good contact is good on every contact measure; a bad family label says
        it broke, not which way, and counts for none of them alone."""
        labels = item.get('labels') or {}
        if cid in labels:
            return labels[cid]
        family = labels.get(rows[cid].get('label', cid))
        return True if family is True else None

    def one(item, run):
        sign, amount = planned(item['asked']['camera'])
        measured = video_measure.measure(item['clip'], sign, amount)
        if parts_needed and item['asked'].get('parts'):
            measured.update(part_measures(item['clip'], round(_seconds(item['clip']) * 30), item['asked'], out / item['name'] / f'run{run}',
                                          reader, placer, tag=''))
        answers = hold_read(item['clip'], item['first'], item['asked'], out / item['name'], reader, tag=f'_run{run}') \
            if answers_needed and item.get('first') else {}
        judged = verdicts(measured, answers, rows)
        return {'item': item['name'], 'run': run, 'values': {k: v['value'] for k, v in judged.items()}, 'answers': answers,
                'labels': {cid: label(item, cid) for cid in rows if label(item, cid) is not None},
                'parts': {k: measured[k] for k in ('contact', 'shape') if k in measured}}
    with ThreadPoolExecutor(max_workers=2) as pool:   # the reads hold pool.slot: at most 2 on the machine
        readings = list(pool.map(lambda job: one(*job), [(item, run) for run in range(runs) for item in items]))
    checks = {}
    for cid, c in rows.items():
        good = [r['values'][cid] for r in readings if r['labels'].get(cid) is True and r['values'][cid] is not None]
        bad = [r['values'][cid] for r in readings if r['labels'].get(cid) is False and r['values'][cid] is not None]
        if 'measure' in c:
            limit, separates, missed = _limit(good, bad, c['flag'])
        else:
            separates = bool(good) and bool(bad) and all(good) and not any(bad)
            limit, missed = None, (sum(not g for g in good) + sum(bool(b) for b in bad)) if good or bad else None
        row = {k: v for k, v in c.items() if k not in ('calibration',)}
        if 'measure' in c and limit is not None:
            row['limit'] = limit
        row['floor'] = separates
        measured_on = lambda want: sorted({r['item'] for r in readings if r['labels'].get(cid) is want and r['values'][cid] is not None})
        row['calibration'] = {'at': now(), 'runs': runs, 'good': sorted(good), 'bad': sorted(bad),
                              'good_items': measured_on(True), 'bad_items': measured_on(False),
                              'unmeasured_items': sorted({r['item'] for r in readings if r['labels'].get(cid) is not None and r['values'][cid] is None}),
                              'separates': separates, 'misses': missed,
                              # how far apart the closest labelled good and bad lie (a thin gap is a floor to re-calibrate first)
                              'gap': (round((min(bad) - max(good)) if c['flag'] == 'above' else (min(good) - max(bad)), 4)
                                      if 'measure' in c and good and bad else None)}
        checks[cid] = row
    result = {**base, 'checks': {**base['checks'], **checks}}
    write_json(out / 'calibration.json', {'checks': checks, 'readings': readings})
    if write:
        write_json(TABLE, result)
    return {'checks': checks, 'readings': readings, 'artifacts': [str(out / 'calibration.json')] + ([str(TABLE)] if write else [])}


def register_commands(subparsers):
    parser = subparsers.add_parser('video', help='The paid video of an approved cut: made, checked, marked, passed by a person')
    commands = parser.add_subparsers(dest='video_command', required=True)
    for name, handler, helptext in (('make', lambda a: make(a.project, a.cut), "the approved cut's paid video (veo-3.1 image-to-video)"),
                                    ('composite', lambda a: composite(a.project, a.cut), 'our marks located on its frames and drawn over it')):
        p = commands.add_parser(name, help=helptext)
        p.add_argument('--project', required=True); p.add_argument('--cut', required=True)
        p.set_defaults(handler=handler)
    p = commands.add_parser('check', help='the video against the approved pictures, the line and what it was asked (shape, contact, time)')
    p.add_argument('--project', required=True); p.add_argument('--cut', required=True)
    p.add_argument('--video', help='a clip made for an earlier request of the cut (its fingerprint, shots/<cut>/video/<fingerprint>)')
    p.set_defaults(handler=lambda a: check(a.project, a.cut, video=a.video))
    p = commands.add_parser('review', help="a person's words on the marked video's motion")
    p.add_argument('--project', required=True); p.add_argument('--cut', required=True); p.add_argument('--user-words', required=True)
    p.add_argument('--reject', action='store_true', help='the person turned the motion down: the free clip stays')
    p.set_defaults(handler=lambda a: review(a.project, a.cut, a.user_words, not a.reject))
    p = commands.add_parser('calibrate', help='the checks on labelled clips: may each be a floor?')
    p.add_argument('--set', required=True); p.add_argument('--out', required=True); p.add_argument('--runs', type=int, default=2)
    p.add_argument('--write', action='store_true', help='write library/video_checks.json')
    p.add_argument('--checks', help='only these checks, comma separated (the rest keep their rows)')
    p.set_defaults(handler=lambda a: calibrate(a.set, a.out, a.runs, write=a.write, only=a.checks.split(',') if a.checks else None))
    return commands
