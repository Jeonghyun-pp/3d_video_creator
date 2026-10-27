"""What the located parts of a generated clip do over time: the contacts the plan names, and each part's shape against
the clip's own first frame. The parts are located on frames across the clip by the reader that places our marks
(studio2/video.located_on, the composite's frames and readings): a box and whether it is shown, per part per frame.

- contact: a hold of the plan that names its pair (video.holds: {says, held, holder, kind, at}) is measured frame by
  frame over its window (`at`, a share of the clip; the whole clip by default). Four measures, the same for every kind:
    apart    the gap between the held thing's box and the holder's, over the held thing's size (0 when they touch);
    outside  the share of the held thing's box outside the holder's;
    drift    how far the held thing moved against the holder since the window's first frame, over the holder's size;
    gone     the share of the window's frames where one of the pair is shown and the other is not.
  A kind (library/video_checks.json contact_kinds) says which of them it keeps: a grip or a carry stays against its
  holder and keeps its place on it (apart, drift), a thing in a box stays inside it (outside, drift), a rider stays on
  what it rides but moves along it (apart). A new kind is a row naming the measures it keeps, never code.
- shape: each part cut out of every frame it is shown in, grey, against its crop on the clip's first frame: likeness
  (the correlation of the two crops at one size, 1 = the same picture) and detail (its edge strength over the first's:
  a blur, a merge or a smear loses edges). Why the clip's own first frame (2026-10-27, fab c03): the approved first
  picture already showed the wafers as a tight stack, and a reader comparing with it called the stack kept.
- count: a narrow reader question on the crops - how many separate items make up the part in each tile of its strip -
  asked in COUNT_ASKS' three wordings; the median per tile against the first tile's count (a stack of plates becoming one
  solid thing falls from many to 1).

Each is a number per clip; whether it is flagged, and whether that blocks, is library/video_checks.json's (calibrated
by studio2 video calibrate), never written here. Pillow only (the venv has no numpy or OpenCV).
"""
from __future__ import annotations

import math
import statistics
from pathlib import Path

from studio.common import read_json, write_json

SIDE = 48            # a crop's side for likeness (small: the reader's box is loose and a crop a few pixels off still matches)
DETAIL_SIDE = 96     # a crop's side for detail (edges need more pixels)
TILE = 220           # a strip tile's height for the count reader
MIN_PX = 6           # a crop narrower or lower than this (px of the frame) is too small to compare
MEASURES = ('apart', 'outside', 'drift', 'gone')
COUNT_ASKS = (
    'For each numbered tile, count the separate items that make up this part - each one you can see as its own piece, '
    'with its own edge (separate thin discs, plates, blades, slats or rods each count one); a single solid thing is 1.',
    'In each numbered tile, how many distinct pieces form this part? Count every piece whose own outline or edge you can '
    'see; where the pieces have fused into one mass with no edges between them, that mass is 1.',
    'How many individual objects of this part are visible in each numbered tile? Stacked plates count one each when the '
    'gaps between them show; one smooth block counts 1; nothing of it in the tile counts 0.',
)
COUNT_HEAD = """Each image is one part of a short video, cut out of {n} frames of it and laid left to right in time order as
numbered tiles. {parts}
{ask}
Answer only from what each tile shows."""


# --- the located frames -----------------------------------------------------------------------------------------

def frame_parts(t, answer):
    """One located frame as {t, parts: {id: {box, shown}}} from a reader's answer (locate.PROMPT's)."""
    from .locate import shown
    return {'t': t, 'parts': {p['id']: {'box': [float(v) for v in p['box']], 'shown': shown(p)} for p in answer.get('parts') or []}}


def _box(b, aspect):
    """A 0-1 box in units of the frame's width on both axes (y times height/width), so sizes and gaps are square."""
    x0, y0, x1, y1 = b
    return min(x0, x1), min(y0, y1) * aspect, max(x0, x1), max(y0, y1) * aspect


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _centre(b):
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


# --- contact ----------------------------------------------------------------------------------------------------

def pairs_of(holds):
    """The holds that name a pair (objects with held, holder and kind); a sentence alone names none."""
    return [h for h in holds or [] if isinstance(h, dict) and h.get('held') and h.get('holder') and h.get('kind')]


def contact(located, holds, kinds, aspect):
    """Each named pair over its window: {apart, outside, drift, gone (each the worst over the pairs whose kind keeps it,
    None where none does), pairs: [{held, holder, kind, keeps, frames: [{t, apart, outside, drift, gone}], worst}]}."""
    out = {m: None for m in MEASURES}
    out['pairs'] = []
    for h in pairs_of(holds):
        keeps = list((kinds.get(h['kind']) or {}).get('keeps') or [])
        a, b = h.get('at') or [0.0, 1.0]
        rows, first = [], None
        for f in located:
            if not a - 1e-6 <= f['t'] <= b + 1e-6:
                continue
            held, holder = f['parts'].get(h['held']), f['parts'].get(h['holder'])
            shown = [bool(held and held['shown']), bool(holder and holder['shown'])]
            if not any(shown):
                continue                     # both out of the frame together: nothing is lost (it moved out)
            if not all(shown):
                rows.append({'t': round(f['t'], 4), 'gone': 1})
                continue
            H, R = _box(held['box'], aspect), _box(holder['box'], aspect)
            size_h, size_r = math.sqrt(max(_area(H), 1e-9)), math.sqrt(max(_area(R), 1e-9))
            gx = max(0.0, R[0] - H[2], H[0] - R[2])
            gy = max(0.0, R[1] - H[3], H[1] - R[3])
            inter = max(0.0, min(H[2], R[2]) - max(H[0], R[0])) * max(0.0, min(H[3], R[3]) - max(H[1], R[1]))
            (hx, hy), (rx, ry) = _centre(H), _centre(R)
            rel = (hx - rx, hy - ry)
            first = first or rel
            rows.append({'t': round(f['t'], 4), 'apart': round(math.hypot(gx, gy) / size_h, 4),
                         'outside': round(1 - inter / max(_area(H), 1e-9), 4),
                         'drift': round(math.hypot(rel[0] - first[0], rel[1] - first[1]) / size_r, 4), 'gone': 0})
        worst = {}
        for m in keeps:
            if m not in MEASURES:
                continue
            if m == 'gone':
                worst[m] = round(sum(r['gone'] for r in rows) / len(rows), 4) if rows else None
            else:
                vals = [r[m] for r in rows if m in r]
                worst[m] = max(vals) if vals else None
        out['pairs'].append({'held': h['held'], 'holder': h['holder'], 'kind': h['kind'], 'at': [a, b], 'keeps': keeps, 'frames': rows,
                             'worst': worst})
        for m, v in worst.items():
            if v is not None:
                out[m] = v if out[m] is None else max(out[m], v)
    return out


# --- shape ------------------------------------------------------------------------------------------------------

def crops(images, located):
    """{part: [(t, crop RGB or None)]} - each part cut out of each located frame it is shown in (images: the located
    frames' pictures, in the same order)."""
    out = {}
    for im, f in zip(images, located):
        w, h = im.size
        for pid, p in f['parts'].items():
            x0, y0, x1, y1 = p['box']
            box = (round(min(x0, x1) * w), round(min(y0, y1) * h), round(max(x0, x1) * w), round(max(y0, y1) * h))
            small = box[2] - box[0] < MIN_PX or box[3] - box[1] < MIN_PX
            out.setdefault(pid, []).append((f['t'], None if (small or not p['shown']) else im.convert('RGB').crop(box)))
    return out


def _pixels(crop, side):
    return list(crop.convert('L').resize((side, side)).getdata())


def likeness(a, b):
    """The correlation of two crops at SIDE x SIDE, grey (1 the same picture, 0 unrelated)."""
    pa, pb = _pixels(a, SIDE), _pixels(b, SIDE)
    ma, mb = statistics.fmean(pa), statistics.fmean(pb)
    da, db = [v - ma for v in pa], [v - mb for v in pb]
    num = sum(x * y for x, y in zip(da, db))
    den = math.sqrt(sum(x * x for x in da) * sum(y * y for y in db))
    return num / den if den > 1e-9 else (1.0 if math.sqrt(sum(x * x for x in da)) < 1e-9 and math.sqrt(sum(y * y for y in db)) < 1e-9 else 0.0)


def detail(crop):
    """A crop's edge strength: the mean of its edges at DETAIL_SIDE x DETAIL_SIDE (0-255)."""
    from PIL import ImageFilter, ImageStat
    return ImageStat.Stat(crop.convert('L').resize((DETAIL_SIDE, DETAIL_SIDE)).filter(ImageFilter.FIND_EDGES).crop(
        (1, 1, DETAIL_SIDE - 1, DETAIL_SIDE - 1))).mean[0]


def shape(images, located):
    """{likeness, detail (each the worst over the parts and frames), parts: {id: {likeness: [...], detail: [...], worst_at}}}:
    each shown crop of a part against its crop on the clip's first frame it is shown in."""
    out = {'likeness': None, 'detail': None, 'parts': {}}
    for pid, row in crops(images, located).items():
        shown = [(t, c) for t, c in row if c is not None]
        if len(shown) < 2:
            continue
        (t0, c0), rest = shown[0], shown[1:]
        d0 = detail(c0)
        like = [(t, round(likeness(c0, c), 4)) for t, c in rest]
        det = [(t, round(detail(c) / d0, 4) if d0 > 1e-6 else 1.0) for t, c in rest]
        worst = min(range(len(rest)), key=lambda i: min(like[i][1], det[i][1]))
        out['parts'][pid] = {'first_at': t0, 'likeness': like, 'detail': det, 'worst_at': rest[worst][0]}
        for key, vals in (('likeness', like), ('detail', det)):
            low = min(v for _t, v in vals)
            out[key] = low if out[key] is None else min(out[key], low)
    return out


# --- count (a narrow reader) ------------------------------------------------------------------------------------

def strips(crops_by_part, folder):
    """{part: png}: each part's shown crops side by side in time order, numbered 1..n (at TILE px high)."""
    from PIL import Image, ImageDraw
    folder = Path(folder) / 'count'
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for pid, row in crops_by_part.items():
        shown = [c for _t, c in row if c is not None]
        if not shown:
            continue
        tiles = [c.resize((max(1, round(c.width * TILE / c.height)), TILE)) for c in shown]
        gap = 12
        strip = Image.new('RGB', (sum(t.width for t in tiles) + gap * (len(tiles) + 1), TILE + 40), (255, 255, 255))
        draw = ImageDraw.Draw(strip)
        x = gap
        for n, t in enumerate(tiles, 1):
            strip.paste(t, (x, 36))
            draw.text((x + 4, 6), str(n), fill=(0, 0, 0))
            x += t.width + gap
        target = folder / f'strip_{pid}.png'
        strip.save(target)
        write_json(target.with_suffix('.json'), {'part': pid, 'tiles': len(tiles), 'at': [t for t, c in row if c is not None]})
        out[pid] = str(target)
    return out


def count_read(strip_files, words, folder, reader=None, tag=''):
    """The count reader on each part's strip, asked COUNT_ASKS' three ways: {count (the lowest ratio over the parts and
    tiles of a tile's count against the first's: 1 the same, 0.05 twenty become one), parts: {id: [median per tile]},
    asks: [[...]]}."""
    from studio.screen_grammar import codex_json
    ids = list(strip_files)
    if not ids:
        return {'count': None, 'parts': {}, 'asks': []}
    n = {pid: _tiles(strip_files[pid]) for pid in ids}
    named = ' '.join(f'Image {k}: the part "{pid}" ({words.get(pid) or pid}), {n[pid]} tiles.' for k, pid in enumerate(ids))
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['parts'], 'properties': {'parts': {
        'type': 'array', 'minItems': len(ids), 'maxItems': len(ids), 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['id', 'counts'],
            'properties': {'id': {'enum': ids}, 'counts': {'type': 'array', 'items': {'type': 'integer', 'minimum': 0, 'maximum': 200},
                                                           'minItems': 1, 'maxItems': max(n.values())}}}}}}
    asks = []
    for k, ask in enumerate(COUNT_ASKS):
        target = Path(folder) / 'count' / f'read{tag}_{k}.json'
        (reader or codex_json)([Path(strip_files[pid]) for pid in ids], COUNT_HEAD.format(n=max(n.values()), parts=named, ask=ask), schema, target)
        asks.append({p['id']: list(p['counts']) for p in read_json(target)['answer']['parts']})
    out = {'count': None, 'parts': {}, 'asks': asks}
    for pid in ids:
        per = [a.get(pid) or [] for a in asks]
        tiles = [statistics.median_low([p[i] for p in per if i < len(p)]) for i in range(n[pid]) if any(i < len(p) for p in per)]
        out['parts'][pid] = tiles
        if len(tiles) < 2:
            continue
        c0 = tiles[0]
        ratios = [min(c, c0) / max(c, c0) if max(c, c0) else 1.0 for c in tiles[1:]]
        low = round(min(ratios), 4)
        out['count'] = low if out['count'] is None else min(out['count'], low)
    write_json(Path(folder) / 'count' / f'count{tag}.json', out)
    return out


def _tiles(strip):
    """How many tiles a strip holds: its sidecar (written with it by strips)."""
    return read_json(Path(strip).with_suffix('.json'))['tiles']
