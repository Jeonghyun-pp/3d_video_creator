"""Does a cut look like it belongs to the reference series? A reader (screen_grammar.codex_json) sees a few of the
reference's own cuts (each a strip: six frames through the cut and its motion map, the subtitle band masked) and then
the cut, and says whether it belongs (yes / partly / no), what makes it closer and what further, and whether it opens
its subject up. The same for the reel: one frame of each of its cuts, in order.

The verdict is about the pictures: a cut that fails is redrawn from its plan (what it opens - cutaway.opens and what, the
scale step), never passed by a camera or label tweak - a clip made again from the same pictures gets the same verdict
back (judge_cut keys a failure by its pictures).

Calibration (calibrate) decides whether the verdict may be a floor: reference cuts the reader never saw as context must
pass, the v1 clay cuts and plain photoreal pictures with nothing opened must not, on every run. Its result and the rule it
chose (library/style_check.json) are read here, never remembered.

Where the reference's cuts come from: `style refs` makes the strips from the local reference reels the cutaway library
was measured from (the videos stay local; .studio/style_refs.json says where this machine keeps them).

Why (2026-10-26, the phase 0-3 review): 5 of the dev preview's 8 cuts came out as nearly one frontal view of a
cleanroom bay - nothing opened - where stage 1's cutaways (a bay in section with its air loop, a chip cut open) ranked
first against the reference 4 of 4 by this kind of reading (scratchpad ab1/judge.py).
"""
from __future__ import annotations

import random
import statistics
import subprocess
from pathlib import Path

from studio.common import REPO, StudioError, file_hash, now, read_json, stable_hash, write_json

POINTER = REPO / '.studio' / 'style_refs.json'   # where this machine keeps the reference cut strips (local, never committed)
TABLE = REPO / 'library' / 'style_check.json'
CONTEXT = 4          # reels whose cuts are shown with every reading (CONTEXT_PER_REEL each); the others are held out
CONTEXT_PER_REEL = 2 # 2026-10-27: with one cut of each of 4 reels as context the reader called 8 of 8 held-out reference
                     # cuts and a plain photo alike 'partly' (scratchpad p46/cal/out_absolute_v1): too narrow a view of
                     # a series whose cuts differ
PER_REEL = 3         # reference cuts taken from each reel, evenly spread over its explaining cuts
ROLES = ('explain', 'reveal', 'consequence', 'compare')   # cuts that show how something works (not titles, not faces)
MIN_LEN_S = 1.5
VERDICTS = ('yes', 'partly', 'no')

PROMPT = """The first {nref} images are cuts from one published series of short vertical explainer videos (the reference).
Each image is one cut: {frames} left to right ({shares} % through it), then M, its motion map (white where the picture
changed over the cut, black where it held). The last image is one cut made for a new video, laid out the same way.
Burned-in subtitles are masked with a black band; they are not part of the comparison.
Does the last cut look like it belongs to the reference series - its look, how it shows things (what is shown and how it
is opened up to show its inside), its graphics and its camera? score: 0 to 10, how much it looks like a cut of the series
(10: it could be one of its cuts; 5: something of it; 0: nothing like it) - use the whole range. belongs: yes, partly or
no, in words. closer: what makes it closer to the reference, in one sentence. further: what makes it further from the reference,
in one sentence ("nothing" when nothing does). opens: does the last cut open its subject up to show what is inside it (a
section cut, a cutaway, a see-through skin, parts pulled apart)?"""

REEL_PROMPT = """The first {nref} images are cuts from one published series of short vertical explainer videos (the
reference): each image is one cut, {frames} left to right, then M, its motion map. The last image shows a new video, one
frame from the middle of each of its cuts, in order (subtitles are masked or absent). Does the new video look like it
belongs to the reference series - its look, how it shows things (what is shown and how it is opened up), its graphics?
score: 0 to 10 (10: it could be a video of the series); belongs: yes, partly or no; closer and further: one sentence each; opens: how many of its cuts open their subject up to
show what is inside (a section cut, a cutaway, see-through, parts pulled apart) - give the count."""


def _schema(reel=False):
    return {'type': 'object', 'additionalProperties': False, 'required': ['score', 'belongs', 'closer', 'further', 'opens'],
            'properties': {'score': {'type': 'integer', 'minimum': 0, 'maximum': 10}, 'belongs': {'enum': list(VERDICTS)}, 'closer': {'type': 'string'}, 'further': {'type': 'string'},
                           'opens': {'type': 'integer', 'minimum': 0} if reel else {'type': 'boolean'}}}


def table():
    return read_json(TABLE) if TABLE.is_file() else {'min_score': None, 'floor': False, 'calibration': None}


def passes(answer, rule=None):
    """A reading passes at the calibrated score (min_score); before any calibration, a 'yes' in words."""
    rule = rule or table()
    if rule.get('min_score') is None:
        return answer.get('belongs') == 'yes'
    return (answer.get('score') or 0) >= rule['min_score']


# --- the reference's cuts --------------------------------------------------------------------------------------------

def _mask(image, ys, lines=1):
    """A black band over each panel's subtitle (centre ys, a share of the panel height), as the reference's are masked."""
    from PIL import ImageDraw
    from studio.screen_grammar import STRIP_HEIGHT
    top = 28
    half = 0.035 * max(1, lines) + 0.03
    draw = ImageDraw.Draw(image)
    for y in ys:
        draw.rectangle((0, max(top, top + (y - half) * STRIP_HEIGHT), image.width, min(top + STRIP_HEIGHT, top + (y + half) * STRIP_HEIGHT)), fill='black')
    return image


def strip_of(clip, target, mask_y, start_s=0.0, len_s=None, fps=30.0, subtitle=None):
    """A strip of a stretch of video (screen_grammar.strips: six frames and the motion map) with the subtitle band
    masked - its own subtitle where the labeller saw one, else the reference's usual place (mask_y)."""
    from PIL import Image
    from studio.screen_grammar import JUDGE_SHARES, strips
    target = Path(target)
    if len_s is None:
        len_s = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(clip)],
                                     capture_output=True, text=True, check=True).stdout.strip())
    raw = strips(Path(clip), [{'index': 0, 'start_s': start_s, 'len_s': len_s}], {'fps': fps}, target.parent / f'{target.stem}_raw',
                 shares=JUDGE_SHARES, motion=True)[0]
    sub = subtitle or {}
    image = _mask(Image.open(raw).convert('RGB'), [sub.get('center_y', mask_y)], sub.get('lines') or 1)
    image.save(target, quality=88)
    return target


def build_refs(measured, manifest, out, per_reel=PER_REEL, context=CONTEXT, context_per_reel=CONTEXT_PER_REEL):
    """The reference's cut strips (local): per reel `per_reel` explaining cuts evenly spread (never chosen by what they
    show), their subtitles masked; the first cut of the first `context` reels is shown with every reading, the others
    are held out (calibrate). Writes out/refs.json and the machine's pointer (.studio/style_refs.json)."""
    out = Path(out).resolve()
    files = {e['reel']: Path(manifest).resolve().parent / e['file'] for e in read_json(manifest)}
    items, ys = [], []
    reels = [g.parent.name for g in sorted(Path(measured).glob('*/grammar.json')) if g.parent.name in files and files[g.parent.name].is_file()]
    for group, reel in enumerate(reels):
        grammar = read_json(Path(measured) / reel / 'grammar.json')
        eligible = []
        for shot in grammar['shots']:
            label = next((l for l in shot.get('labels') or [] if l.get('run') == 1), None)
            if label and label['role'] in ROLES and shot['len_s'] >= MIN_LEN_S:
                eligible.append((shot, label))
        step = len(eligible) / per_reel if eligible else 0
        for k in range(min(per_reel, len(eligible))):
            shot, label = eligible[int(k * step + step / 2)]
            sub = label.get('subtitle') or {}
            if sub.get('present'):
                ys.append(sub['center_y'])
            n = len(items)
            target = strip_of(files[reel], out / 'strips' / f'r{n:02d}.jpg', 0.68, shot['start_s'], shot['len_s'], grammar['media']['fps'],
                              sub if sub.get('present') else None)
            items.append({'id': f'r{n:02d}', 'group': group, 'strip': str(target), 'len_s': shot['len_s'], 'role': label['role']})
    if not items:
        raise StudioError('INPUT_INVALID', f'no explaining cut with a local video under {measured} / {manifest}')
    shown = []
    for group in range(min(context, len(reels))):
        shown += [i['id'] for i in items if i['group'] == group][:context_per_reel]
    held = [i['id'] for i in items if i['group'] >= context]
    data = {'schema_version': 1, 'at': now(), 'items': items, 'context': shown, 'held_out': held,
            'mask_y': round(statistics.median(ys), 3) if ys else 0.68, 'reels': len(reels)}
    write_json(out / 'refs.json', data)
    write_json(POINTER, {'refs': str(out / 'refs.json')})
    return {'refs': str(out / 'refs.json'), 'cuts': len(items), 'context': shown, 'held_out': len(held), 'artifacts': [str(out / 'refs.json')]}


def refs():
    if not POINTER.is_file() or not Path(read_json(POINTER)['refs']).is_file():
        raise StudioError('STYLE_REFS_MISSING', "the reference's cut strips are not made on this machine",
                          recovery='studio2 style refs --measured <grammar folders> --manifest <reference manifest> --out <local folder>')
    return read_json(read_json(POINTER)['refs'])


def context_strips(data):
    by = {i['id']: i for i in data['items']}
    return [Path(by[i]['strip']) for i in data['context']]


# --- reading ---------------------------------------------------------------------------------------------------------

def judge_strip(strip, data, target, reader=None, run=0, reel=False):
    """{belongs, closer, further, opens} of one strip (a cut's, or a reel's sheet) against the reference's context cuts,
    kept at target (read once for exactly these images and words)."""
    from studio.reel_judge import frames_words
    from studio.screen_grammar import codex_json
    images = context_strips(data) + [Path(strip)]
    prompt = (REEL_PROMPT if reel else PROMPT).format(nref=len(images) - 1, **frames_words()) + (f' (reading {run})' if run else '')
    asked = stable_hash({'prompt': prompt, 'images': [file_hash(i) for i in images]})
    target = Path(target)
    if not (target.is_file() and read_json(target).get('asked_sha') == asked):
        (reader or codex_json)(images, prompt, _schema(reel), target)
        write_json(target, {**read_json(target), 'asked_sha': asked})
    return read_json(target)['answer']


def _pictures_sha(path, cut):
    from .frames import needed, picked
    return stable_hash({f: file_hash(picked(path, cut, f)) for f in needed(cut) if picked(path, cut, f)})[:16]


def judge_cut(project, cut_id, reader=None):
    """The style verdict of a cut's made clip. A failure stands for its pictures: a clip made again from the same
    pictures (a camera, label or timing change) gets it back unread - the cut is redrawn from its plan."""
    from studio import decisions
    from studio.edit import latest_composite
    from studio.project import load_shot, project_dir
    from .frames import cut_of
    path = project_dir(project)
    body = decisions.envelope(path, 'cuts')['body']
    cut = cut_of(body, cut_id)
    shot = load_shot(path, cut_id)
    made = latest_composite(path, shot, shot['duration_frames'])
    if made is None:
        raise StudioError('RENDER_REQUIRED', f'{cut_id}: no clip made', recovery=f'studio2 motion render --cut {cut_id}')
    data = refs()
    folder = path / 'review' / 'style'
    pictures = _pictures_sha(path, cut)
    rule = table()
    kept = [read_json(f) for f in sorted(folder.glob(f'{cut_id}_*.json')) if not f.name.endswith('_read.json')]
    failed = next((k for k in kept if k['pictures_sha'] == pictures and not k['passes'] and k['clip'] != made['key']), None)
    if failed:
        return {**failed, 'cached': True, 'note': 'these pictures failed already - redraw the cut from its plan (what it opens, its '
                                                  'scale step: cuts write, then frames make), not its camera or labels'}
    target = folder / f"{cut_id}_{made['key']}.json"
    if target.is_file() and read_json(target).get('pictures_sha') == pictures and read_json(target).get('rule') == rule.get('min_score'):
        return {**read_json(target), 'cached': True}
    strip = strip_of(made['clip'], folder / f"{cut_id}_{made['key']}.jpg", data['mask_y'])
    answer = judge_strip(strip, data, folder / f"{cut_id}_{made['key']}_read.json", reader)
    ok = passes(answer, rule)
    row = {'cut': cut_id, 'clip': made['key'], 'pictures_sha': pictures, 'at': now(), **answer, 'passes': ok, 'rule': rule.get('min_score'),
           'floor': bool(rule.get('floor')), 'strip': str(strip)}
    write_json(target, row)
    return {**row, 'cached': False, **({} if ok else {'next': [
        f"STYLE_OFF: {cut_id} scores {answer['score']}/10 against the reference ({answer['further']}) - redraw it from its plan: what it "
        'opens and how (cutaway.opens / what, its scale step) in cuts write, then frames make; a camera or label change keeps the verdict']})}


def judge_reel(project, reader=None):
    """Every made cut's verdict and the reel's own (one frame of each cut, in order) - the reel's is advice."""
    from studio import decisions
    from studio.edit import latest_composite
    from studio.project import load_shot, project_dir
    from .flow import sheet_of
    path = project_dir(project)
    body = decisions.envelope(path, 'cuts')['body']
    cuts, frames = [], []
    folder = path / 'review' / 'style'
    for cut in body['cuts']:
        shot = load_shot(path, cut['cut_id'])
        made = latest_composite(path, shot, shot['duration_frames'])
        if made is None:
            continue
        cuts.append(judge_cut(path, cut['cut_id'], reader))
        frame = folder / 'frames' / f"{cut['cut_id']}_{made['key']}.png"
        if not frame.is_file():
            frame.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f"{shot['duration_frames'] / 60:.3f}", '-i', str(made['clip']), '-frames:v', '1',
                            str(frame)], check=True)
        frames.append((cut['cut_id'], frame))
    if not cuts:
        raise StudioError('RENDER_REQUIRED', 'no cut of the reel is made', recovery='studio2 motion render --cut <cut>')
    sha = stable_hash([[n, file_hash(f)] for n, f in frames])[:16]
    sheet = sheet_of(frames, folder / f'reel_{sha}.jpg')
    reel = judge_strip(sheet, refs(), folder / f'reel_{sha}_read.json', reader, reel=True)
    summary = {'at': now(), 'cuts': {c['cut']: c['score'] for c in cuts}, 'passing': [c['cut'] for c in cuts if c['passes']],
               'failing': [c['cut'] for c in cuts if not c['passes']], 'reel': reel, 'min_score': table().get('min_score'),
               'floor': bool(table().get('floor'))}
    write_json(folder / 'reel.json', summary)
    return {**summary, 'artifacts': [str(folder / 'reel.json'), str(sheet)],
            'next': [c['next'][0] for c in cuts if c.get('next')]}


def failing(path, cut):
    """The kept failing verdict of a cut's current pictures under a calibrated floor, or None."""
    rule = table()
    if not rule.get('floor'):
        return None
    pictures = _pictures_sha(path, cut)
    for f in sorted((Path(path) / 'review' / 'style').glob(f"{cut['cut_id']}_*.json")):
        if f.name.endswith('_read.json') or f.name == 'reel.json':
            continue
        row = read_json(f)
        if row.get('pictures_sha') == pictures and not passes(row, rule):
            return row
    return None


# --- calibration -----------------------------------------------------------------------------------------------------

def still_clip(picture, target, seconds=3.0, push=0.05):
    """A picture as a short clip with a slow push (how a still is shown in a preview), for reading as a cut."""
    from PIL import Image
    from .motion import cover, ease, zoom
    target = Path(target)
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    w, h, frames = 540, 960, round(seconds * 30)
    base = cover(Image.open(picture).convert('RGB'), w, h)
    writer = subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{w}x{h}', '-r', '30', '-i', '-',
                               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20', str(target)], stdin=subprocess.PIPE)
    for i in range(frames):
        writer.stdin.write(zoom(base, 1 + push * ease(i / frames), 0.5, 0.5).tobytes())
    writer.stdin.close()
    writer.wait()
    return target


def calibrate(set_file, out, runs=2, reader=None, write=False):
    """The reader on cuts whose answer is known: set_file {items: [{name, expect: true|false, clip | picture | ref, why}]}
    (ref: a held-out reference cut's id). A score separates when every expected pass scores at or over it and every
    expected fail under it, on every run, with at least one of each; the floor is the middle of the separating scores,
    and there is none when no score separates (the best is shown, as advice). -> the table."""
    data = refs()
    items = read_json(set_file)['items']
    by = {i['id']: i for i in data['items']}
    out = Path(out)
    readings = []
    for run in range(runs):
        for item in items:
            if 'ref' in item:
                if item['ref'] in data['context']:
                    raise StudioError('INPUT_INVALID', f"{item['name']}: {item['ref']} is shown as context - a held-out cut only")
                strip = Path(by[item['ref']]['strip'])
            else:
                clip = Path(item['clip']) if 'clip' in item else still_clip(item['picture'], out / 'clips' / f"{item['name']}.mp4")
                strip = strip_of(clip, out / 'strips' / f"{item['name']}.jpg", data['mask_y'])
            answer = judge_strip(strip, data, out / 'readings' / f"{item['name']}_run{run}.json", reader, run)
            readings.append({'item': item['name'], 'run': run, 'expect': item['expect'], **answer})
    positives = [r['score'] for r in readings if r['expect']]
    negatives = [r['score'] for r in readings if not r['expect']]
    scores = {}
    for t in range(0, 11):   # pass at score >= t
        scores[t] = {'pass_got': sum(x >= t for x in positives), 'fail_got': sum(x < t for x in negatives)}
    separating = [t for t, v in scores.items() if positives and negatives and v['pass_got'] == len(positives) and v['fail_got'] == len(negatives)]
    if separating:
        chosen = (min(separating) + max(separating)) // 2
    else:   # no score separates on every run: the best one is shown, never a floor
        chosen = max(scores, key=lambda t: (scores[t]['pass_got'] + scores[t]['fail_got'], -t))
    misses = [f"{r['item']} run {r['run']}: {r['score']} ({r['further'] if r['expect'] else r['closer']})"
              for r in readings if (r['score'] >= chosen) != r['expect']]
    result = {'at': now(), 'runs': runs, 'items': len(items), 'positives': sorted(positives), 'negatives': sorted(negatives),
              'separating': separating, 'min_score': chosen, 'floor': bool(separating), 'at_min_score': scores[chosen],
              'pass_expected': len(positives), 'fail_expected': len(negatives), 'misses': misses,
              'opens_agree': sum(r['opens'] == r['expect'] for r in readings) / max(1, len(readings))}
    write_json(out / 'calibration.json', {**result, 'readings': readings})
    if write:
        write_json(TABLE, {'schema_version': 1, 'why': 'whether a cut looks like it belongs to the reference series (studio2/style.py): '
                                                       'the score calibration chose, a floor only where it separated every reading',
                           'min_score': chosen, 'floor': bool(separating),
                           'calibration': {k: v for k, v in result.items() if k != 'misses'}})
    return {**result, 'artifacts': [str(out / 'calibration.json')]}


def register_commands(subparsers):
    parser = subparsers.add_parser('style', help="Does a cut (and the reel) look like it belongs to the reference series?")
    commands = parser.add_subparsers(dest='style_command', required=True)
    p = commands.add_parser('refs', help="the reference's cut strips, made locally from its reels")
    p.add_argument('--measured', required=True); p.add_argument('--manifest', required=True); p.add_argument('--out', required=True)
    p.add_argument('--per-reel', type=int, default=PER_REEL)
    p.set_defaults(handler=lambda a: build_refs(a.measured, a.manifest, a.out, a.per_reel))
    p = commands.add_parser('cut', help="one made cut's verdict")
    p.add_argument('--project', required=True); p.add_argument('--cut', required=True)
    p.set_defaults(handler=lambda a: judge_cut(a.project, a.cut))
    p = commands.add_parser('reel', help="every made cut's verdict and the reel's")
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: judge_reel(a.project))
    p = commands.add_parser('calibrate', help='the reader on cuts whose answer is known: may the verdict be a floor?')
    p.add_argument('--set', required=True); p.add_argument('--out', required=True); p.add_argument('--runs', type=int, default=2)
    p.add_argument('--write', action='store_true')
    p.set_defaults(handler=lambda a: calibrate(a.set, a.out, a.runs, write=a.write))
    return commands
