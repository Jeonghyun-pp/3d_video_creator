"""Composition styles: where a family of reference shots puts the horizon and how much sky it keeps (look_style's twin).

Per sampled frame (WIDTH px wide):
  vp_v        height (0 = top) of the vanishing point: oblique edgelets vote for the point their lines pass through
              (a street's kerbs, lane lines and facades converge there) - the horizon of a level camera
  sky_share   share of the frame above the first strong edge in each column (sky over the skyline)
  skyline_c   median height of that first edge in the central fifth of the frame (the skyline over the street)
A style keeps the quartiles of those values and only the sha256 of its sources (never frames or paths), so a reference
stays private. `check` measures a render and reports features outside [p25 - m*IQR, p75 + m*IQR] - advisory, like
look styles. Port of the 2026-10-05 verification analyser (median vp error 0.0022 of the frame height on synthetic
renders with known cameras), in pure Python: no numpy on the host.
"""
from __future__ import annotations

import math
from pathlib import Path
import random
import statistics

from .common import REPO, StudioError, check_id, file_hash, now, read_json, write_json

STYLES = REPO / 'library' / 'composition_styles'
WIDTH = 120
SAMPLES = 8
EDGE_QUANTILE = 0.93
SKY_QUANTILE = 0.80
EDGELETS = 700
GRID_X, GRID_V = 31, (-0.25, 0.9, 0.02)
VOTE_DEG = 2.5
FEATURES = ('vp_v', 'sky_share', 'skyline_c')
MARGIN_IQR = 0.5
FLOOR = {'vp_v': 0.03, 'sky_share': 0.03, 'skyline_c': 0.04}


def _quantile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def frame_features(image):
    from PIL import ImageFilter
    luma = image.convert('L').filter(ImageFilter.BoxBlur(1))
    w, h = luma.size
    px = luma.load()
    gx, gy = {}, {}
    mags = []
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            dx = (px[x + 1, y] - px[x - 1, y]) / 2.0
            dy = (px[x, y + 1] - px[x, y - 1]) / 2.0
            gx[x, y], gy[x, y] = dx, dy
            mags.append(math.hypot(dx, dy))
    if not mags or max(mags) == 0:
        return {'vp_v': 0.5, 'sky_share': 0.0, 'skyline_c': 0.0}
    strong = _quantile(mags, EDGE_QUANTILE)
    edgelets = []
    for (x, y), dx in gx.items():
        dy = gy[x, y]
        m = math.hypot(dx, dy)
        if m <= strong:
            continue
        tx, ty = -dy / m, dx / m                                    # along the edge
        angle = math.degrees(math.atan2(abs(tx), abs(ty)))
        if 12 < angle < 80:                                         # oblique: converging lines, not verticals or horizontals
            edgelets.append((x, y, tx, ty, m))
    r = random.Random(0)
    if len(edgelets) > EDGELETS:
        edgelets = r.sample(edgelets, EDGELETS)
    cos_limit = math.cos(math.radians(VOTE_DEG))
    best, best_v = -1.0, 0.5
    v = GRID_V[0]
    while v <= GRID_V[1] + 1e-9:
        cy = v * h
        for i in range(GRID_X):
            cx = i * w / (GRID_X - 1)
            score = 0.0
            for x, y, tx, ty, m in edgelets:
                vx, vy = cx - x, cy - y
                n = math.hypot(vx, vy) + 1e-6
                if abs(vx * tx + vy * ty) / n > cos_limit:
                    score += m
            if score > best:
                best, best_v = score, v
        v += GRID_V[2]
    sky_edge = _quantile(mags, SKY_QUANTILE)
    first = []
    for x in range(1, w - 1):
        row = next((y for y in range(1, h - 1) if math.hypot(gx[x, y], gy[x, y]) > sky_edge), h)
        first.append(row)
    centre = sorted(first[int(0.4 * len(first)):int(0.6 * len(first))])
    return {'vp_v': round(best_v, 4), 'sky_share': round(sum(first) / (len(first) * h), 4),
            'skyline_c': round(centre[len(centre) // 2] / h, 4) if centre else 0.0}


def features(video, start=None, end=None, samples=SAMPLES):
    from .qa_generative import _ffmpeg, _scaled_height
    from PIL import Image
    height = _scaled_height(video, WIDTH)
    args = (['-ss', f'{start}'] if start is not None else []) + ['-i', video] + (['-t', f'{end - (start or 0)}'] if end is not None else [])
    raw = _ffmpeg(args + ['-vf', f'scale={WIDTH}:{height}:flags=area,format=rgb24', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    size = WIDTH * height * 3
    frames = [Image.frombytes('RGB', (WIDTH, height), raw[i:i + size]) for i in range(0, len(raw) - size + 1, size)]
    if not frames:
        raise StudioError('INPUT_INVALID', f'No frames read from {video}')
    picks = sorted({round(i * (len(frames) - 1) / max(1, samples - 1)) for i in range(samples)})
    return [frame_features(frames[i]) for i in picks]


def _quartiles(values):
    ordered = sorted(values)
    q = statistics.quantiles(ordered, n=4, method='inclusive') if len(ordered) > 1 else [ordered[0]] * 3
    return {'p25': round(q[0], 4), 'median': round(q[1], 4), 'p75': round(q[2], 4), 'n': len(ordered)}


def learn(name, videos, ranges=None):
    """ranges: [[start_s, end_s] | None] per video (e.g. only the establishing aerial of a reel)."""
    name = check_id(name)
    rows, sources = [], []
    for i, video in enumerate(videos):
        span = (ranges or [None] * len(videos))[i] or [None, None]
        measured = features(video, *span)
        rows += measured
        sources.append({'sha256': file_hash(Path(video)), 'range_s': span, 'frames': len(measured)})
    style = {'schema_version': 1, 'name': name, 'created_at': now(), 'n_references': len(videos), 'sources': sources,
             'overfit_risk': len(videos) < 2, 'features': {f: _quartiles([r[f] for r in rows]) for f in FEATURES}}
    write_json(STYLES / f'{name}.json', style)
    return {'style': name, 'path': str(STYLES / f'{name}.json'), 'features': style['features'],
            'warnings': ['STYLE_SINGLE_REFERENCE: learned from one video; numbers describe that video only'] if style['overfit_risk'] else []}


def load(name):
    path = STYLES / f'{check_id(name)}.json'
    if not path.is_file():
        raise StudioError('INPUT_INVALID', f'No composition style {name} (learn it with composition style learn)')
    return read_json(path)


def judge(style, measured):
    out = {}
    for feature in FEATURES:
        q = style['features'][feature]
        spread = max(q['p75'] - q['p25'], FLOOR[feature])
        low, high = q['p25'] - MARGIN_IQR * spread, q['p75'] + MARGIN_IQR * spread
        value = statistics.median(r[feature] for r in measured)
        out[feature] = {'value': round(value, 4), 'low': round(low, 4), 'high': round(high, 4), 'ok': low <= value <= high,
                        'direction': 'low' if value < low else 'high' if value > high else 'in'}
    return out


def check(name, video, start=None, end=None):
    verdict = judge(load(name), features(video, start, end))
    misses = [f for f, v in verdict.items() if not v['ok']]
    return {'style': name, 'video_sha256': file_hash(Path(video)), 'ok': not misses, 'misses': misses, 'verdict': verdict,
            'warnings': [f"composition_style {name}: {f} {verdict[f]['value']} is {verdict[f]['direction']} "
                         f"(style {verdict[f]['low']}..{verdict[f]['high']})" for f in misses]}


def style_for(shot, style):
    """The composition style a shot is judged against: shot.render.composition_style, else style.look.composition_style."""
    return shot.get('render', {}).get('composition_style') or ((style or {}).get('look') or {}).get('composition_style')


def register_commands(subparsers):
    parser = subparsers.add_parser('composition', help='Composition styles learned from reference shots: horizon and sky (numbers only)')
    commands = parser.add_subparsers(dest='composition_command', required=True)
    style = commands.add_parser('style').add_subparsers(dest='style_command', required=True)
    p = style.add_parser('learn'); p.add_argument('--name', required=True); p.add_argument('--video', action='append', required=True)
    p.add_argument('--range', action='append', help='start,end seconds for the matching --video')
    p.set_defaults(handler=lambda a: learn(a.name, a.video, [[float(x) for x in r.split(',')] for r in a.range] if a.range else None))
    p = style.add_parser('show'); p.add_argument('--name', required=True)
    p.set_defaults(handler=lambda a: load(a.name))
    p = style.add_parser('check'); p.add_argument('--name', required=True); p.add_argument('--video', required=True)
    p.add_argument('--start', type=float); p.add_argument('--end', type=float)
    p.set_defaults(handler=lambda a: check(a.name, a.video, a.start, a.end))
