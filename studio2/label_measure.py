"""How big the reference's labels are: the height of a label's letters as a share of the frame height, measured on the
reference reels the cutaway library was built from, kept as a pattern library (library/patterns/<name>.json: numbers and
their strength only, no frames, no names) and read through studio.patterns like every other reference number.

Frames: for each reel, cuts whose labeller saw a label (grammar.json), at the panel after the one it first showed in. A
reader (screen_grammar.codex_json, the studio's one way of having a model look) returns a tight box round each label's
letters and round the subtitle's; the letter height is the box's height over its lines. The reader's own error is
measured the same way on frames with labels drawn at known sizes (calibrate): a measure is only as good as its reader.

Why (2026-10-26, stage 1): the labels drawn in the stage-1 clips were about half the reference's size - the reference
names a part in white letters about as tall as its subtitles.
"""
from __future__ import annotations

import random
import statistics
import subprocess
from pathlib import Path

from studio.common import StudioError, now, read_json, write_json

PANEL_SHARES = {'A': 0.08, 'B': 0.35, 'C': 0.65, 'D': 0.92}   # where the labeller's panels sit in a cut (screen_grammar.KEY_SHARES)
NEXT_PANEL = {'A': 'B', 'B': 'C', 'C': 'D', 'D': 'D'}          # a label first seen in a panel is surely there by the next
PER_REEL = 6
PER_CALL = 6
ITEMS = ('label_text_height', 'subtitle_text_height', 'label_to_subtitle')

PROMPT = """Each attached image is one frame of a short vertical explainer video. For each image find every LABEL: a short
word or two naming a part of the picture, usually with a thin leader line or a pointer to that part. A label is not the
subtitle (the running sentence near the lower middle), not a logo or watermark in a corner, not a title over the whole
frame. For each label give its text and a TIGHT box around its letters only (not around a panel or its leader line) as
[x0, y0, x1, y1] in 0-1 of the image width and height, and how many lines of text it has. Also give the subtitle's tight
letter box and line count, or null when there is none. Give an entry for every image, in order (image 0 first)."""


def _schema(n):
    box = {'type': 'array', 'items': {'type': 'number', 'minimum': 0, 'maximum': 1}, 'minItems': 4, 'maxItems': 4}
    text = {'type': 'object', 'additionalProperties': False, 'required': ['text', 'box', 'lines'],
            'properties': {'text': {'type': 'string'}, 'box': box, 'lines': {'type': 'integer', 'minimum': 1}}}
    frame = {'type': 'object', 'additionalProperties': False, 'required': ['image', 'labels', 'subtitle'],
             'properties': {'image': {'type': 'integer', 'minimum': 0}, 'labels': {'type': 'array', 'items': text},
                            'subtitle': {'anyOf': [text, {'type': 'null'}]}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['frames'],
            'properties': {'frames': {'type': 'array', 'items': frame, 'minItems': n, 'maxItems': n}}}


def height_of(entry):
    """The letter height of one label or subtitle (a share of the frame height): its box's height over its lines."""
    x0, y0, x1, y1 = entry['box']
    return (y1 - y0) / max(1, entry['lines']) if y1 > y0 else None


def frames_to_read(measured, manifest, per_reel=PER_REEL, seed=20261026):
    """[(reel, video, seconds)] - per reel up to per_reel cuts the labeller saw a label in, evenly spread."""
    references = Path(manifest).parent
    files = {e['reel']: references / e['file'] for e in read_json(manifest)}
    out = []
    for grammar_file in sorted(Path(measured).glob('*/grammar.json')):
        reel = grammar_file.parent.name
        if reel not in files or not files[reel].is_file():
            continue
        cuts = []
        for shot in read_json(grammar_file)['shots']:
            label = next((g for lab in shot['labels'][:1] for g in lab['graphics'] if g['kind'] == 'label'), None)
            if label:
                cuts.append(shot['start_s'] + PANEL_SHARES[NEXT_PANEL[label['first_panel']]] * shot['len_s'])
        if len(cuts) > per_reel:
            step = len(cuts) / per_reel
            cuts = [cuts[int(i * step)] for i in range(per_reel)]
        out += [(reel, files[reel], t) for t in cuts]
    return out


def grab(video, seconds, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{seconds:.3f}', '-i', str(video), '-frames:v', '1', str(target)], check=True)
    return target


def read_frames(images, out, reader=None):
    """[{labels, subtitle}] per image, asked PER_CALL images at a time (reader(images, prompt, schema, target) in tests)."""
    from studio.screen_grammar import codex_json
    got = []
    for start in range(0, len(images), PER_CALL):
        batch = images[start:start + PER_CALL]
        target = Path(out) / 'readings' / f'read_{start:03d}.json'
        (reader or codex_json)(batch, PROMPT, _schema(len(batch)), target)
        answer = sorted(read_json(target)['answer']['frames'], key=lambda f: f['image'])
        got += answer[:len(batch)] + [{'labels': [], 'subtitle': None}] * (len(batch) - len(answer))
    return got


def rows_of(readings, groups, reader_ratio=1.0):
    """One row per frame: its labels' median letter height, its subtitle's, and their ratio. reader_ratio: how much taller
    the reader reads letters than their ink (calibrate) - taken out, so the heights are ink, as the renderer draws them."""
    out = []
    for reading, group in zip(readings, groups):
        heights = [h / reader_ratio for h in (height_of(l) for l in reading['labels']) if h]
        sub = height_of(reading['subtitle']) / reader_ratio if reading.get('subtitle') and height_of(reading['subtitle']) else None
        if not heights:
            continue
        label = statistics.median(heights)
        out.append({'group': group, 'reel': group, 'label_text_height': round(label, 4),
                    'subtitle_text_height': round(sub, 4) if sub else None,
                    'label_to_subtitle': round(label / sub, 3) if sub else None})
    return out


def rebuild(full, name, calibration=None):
    """The library again from kept readings (<out>/labels_full.json) and the reader's calibration (calibration.json): the
    reader's measured bias taken out, nothing read again."""
    data = read_json(full)
    ratio = read_json(calibration)['summary']['ratio_median'] if calibration else 1.0
    frames = data['frames']
    rows = rows_of([f['reading'] for f in frames], [f['reel'] for f in frames], ratio)
    lib, items = library(rows, name, len(frames), ratio)
    data.update({'rows': rows, 'items': items, 'reader_ratio': ratio})
    write_json(full, data)
    from studio.patterns import LIBRARY
    return {'library': str(LIBRARY / f'{name}.json'), 'reader_ratio': ratio,
            'items': {k: {s: v.get(s) for s in ('n', 'groups', 'p25', 'median', 'p75', 'strength')} for k, v in lib['sections']['all']['items'].items()}}


def library(rows, name, frames, reader_ratio=1.0):
    from studio.patterns import LIBRARY, group_patterns, reels_by_group, shareable
    items = group_patterns(rows, reels=reels_by_group(rows), numeric=ITEMS, categorical=())
    data = {'schema_version': 1, 'name': name, 'built_at': now(), 'kind': 'measure',
            'measures': 'the height of a label\'s letters, of the subtitle\'s and their ratio, as shares of the frame height (studio2/label_measure.py)',
            'support': {'groups': len(reels_by_group(rows)), 'frames': frames, 'frames_with_labels': len(rows)},
            'reader_ratio': reader_ratio,
            'limits': ['letter boxes are a reader\'s (codex); heights are its boxes over reader_ratio, how much taller it read labels drawn '
                       'at known sizes than their ink (studio2 grammar calibrate-labels)'],
            'sections': {'all': {'shots': len(rows), 'labelled_shots': len(rows), 'items': shareable(items)}}}
    LIBRARY.mkdir(parents=True, exist_ok=True)
    write_json(LIBRARY / f'{name}.json', data)
    return data, items


def measure(measured, manifest, name, out, reader=None, per_reel=PER_REEL):
    """Read the reference's label sizes and write library/patterns/<name>.json (shareable) and <out>/labels_full.json
    (local: per-reel values, every reading)."""
    picked = frames_to_read(measured, manifest, per_reel)
    if not picked:
        raise StudioError('INPUT_INVALID', f'no labelled cut with a local video under {measured} / {manifest}')
    out = Path(out)
    images = [grab(video, t, out / 'frames' / f'{reel}_{t:08.3f}.png') for reel, video, t in picked]
    readings = read_frames(images, out, reader)
    groups = [reel for reel, _v, _t in picked]
    rows = rows_of(readings, groups)
    data, items = library(rows, name, len(picked))
    write_json(out / 'labels_full.json', {'at': now(), 'frames': [{'reel': r, 'seconds': t, 'image': str(i), 'reading': x}
                                                                    for (r, _v, t), i, x in zip(picked, images, readings)],
                                          'rows': rows, 'items': items})
    from studio.patterns import LIBRARY
    return {'library': str(LIBRARY / f'{name}.json'),
            'items': {k: {s: v.get(s) for s in ('n', 'groups', 'p25', 'median', 'p75', 'strength')} for k, v in data['sections']['all']['items'].items()},
            'frames': len(picked), 'with_labels': len(rows), 'artifacts': [str(out / 'labels_full.json')]}


def calibrate(backgrounds, out, heights=(0.02, 0.03, 0.04, 0.05), reader=None, seed=7):
    """The reader's error on labels of known size: each background gets one label drawn at each letter height (its ink
    height, measured by the font itself), read like a reference frame. -> rows (drawn, read, ratio)."""
    from PIL import Image, ImageDraw, ImageFont
    from studio.common import REPO
    font_path = REPO / 'library' / 'fonts' / 'pretendard' / 'Pretendard-Bold.otf'
    rng = random.Random(seed)
    words = ['필터', '이중 바닥', '웨이퍼', '배수관', '트랩', '죽은 칩']
    out = Path(out)
    images, drawn = [], []
    for n, background in enumerate(backgrounds):
        for h in heights:
            image = Image.open(background).convert('RGB').resize((1080, 1920))
            draw = ImageDraw.Draw(image)
            word = rng.choice(words)
            size = 10
            while True:   # the font size whose ink is h of the frame tall
                font = ImageFont.truetype(str(font_path), size)
                box = draw.textbbox((0, 0), word, font=font)
                if box[3] - box[1] >= h * 1920:
                    break
                size += 1
            x, y = rng.randint(80, 600), rng.randint(200, 1400)
            draw.text((x, y), word, font=font, fill=(255, 255, 255), stroke_width=max(2, size // 18), stroke_fill=(20, 20, 20))
            ink = draw.textbbox((x, y), word, font=font)
            target = out / 'calibration' / f'cal_{n}_{int(h * 1000):03d}.png'
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target)
            images.append(target)
            drawn.append((ink[3] - ink[1]) / 1920)
    readings = read_frames(images, out / 'calibration', reader)
    rows = []
    for image, want, reading in zip(images, drawn, readings):
        got = [height_of(l) for l in reading['labels']]
        got = [g for g in got if g]
        rows.append({'image': image.name, 'drawn': round(want, 4), 'read': round(statistics.median(got), 4) if got else None,
                     'ratio': round(statistics.median(got) / want, 3) if got else None})
    ratios = [r['ratio'] for r in rows if r['ratio']]
    summary = {'n': len(rows), 'read': len(ratios), 'ratio_median': round(statistics.median(ratios), 3) if ratios else None,
               'ratio_p10_p90': [round(q, 3) for q in statistics.quantiles(ratios, n=10)[::8]] if len(ratios) >= 4 else None}
    write_json(out / 'calibration.json', {'at': now(), 'rows': rows, 'summary': summary})
    return {'rows': rows, 'summary': summary, 'artifacts': [str(out / 'calibration.json')]}


def register(commands):
    p = commands.add_parser('measure-labels', help="the reference's label letter height (a reader's letter boxes) -> a pattern library")
    p.add_argument('--measured', required=True, help='the folder of grammar.json files the cutaway library was built from')
    p.add_argument('--manifest', required=True, help="the reference manifest (its videos, local)")
    p.add_argument('--name', default='cutaway_labels_v1'); p.add_argument('--out', required=True, help='a local folder (frames, readings)')
    p.add_argument('--per-reel', type=int, default=PER_REEL)
    p.set_defaults(handler=lambda a: measure(a.measured, a.manifest, a.name, a.out, per_reel=a.per_reel))
    p = commands.add_parser('rebuild-labels', help="the label library again from kept readings, the reader's measured bias taken out")
    p.add_argument('--full', required=True); p.add_argument('--name', default='cutaway_labels_v1'); p.add_argument('--calibration')
    p.set_defaults(handler=lambda a: rebuild(a.full, a.name, a.calibration))
    p = commands.add_parser('calibrate-labels', help="the label reader's error on labels drawn at known sizes")
    p.add_argument('--background', action='append', required=True); p.add_argument('--out', required=True)
    p.set_defaults(handler=lambda a: calibrate(a.background, a.out))
