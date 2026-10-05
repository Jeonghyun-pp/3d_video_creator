"""Look styles: how dense and how bright a family of reference shots looks, as numbers (motion_style's twin).

Per sampled frame (448 px wide, 8 frames spread over the range):
  bright_points_per_mp  small bright blobs (lit windows, car lights, lamps): connected pixels with luma >=
                        max(p99.5 of the frame, BRIGHT_MIN) whose area is <= POINT_MAX_FRAC of the frame, per megapixel
  luma_p50 / luma_p95   overall key and highlights (0-255)
  sat_mean              mean HSV saturation (0-1)
  dark_fine_edge        share of fine-gradient pixels inside the darker half of the frame (detail in the shadows)
A style keeps the quartiles of those per-frame values and only the sha256 of its sources: the reference stays
private and is never sent anywhere. `check` measures a render or a generated clip and reports which features fall
outside [p25 - m*IQR, p75 + m*IQR] - advisory, never a gate.
"""
from __future__ import annotations

from pathlib import Path
import statistics

from .common import REPO, StudioError, check_id, file_hash, now, read_json, write_json

STYLES = REPO / 'library' / 'look_styles'
WIDTH = 448
SAMPLES = 8
BRIGHT_MIN = 200
POINT_MAX_FRAC = 0.0005
FINE_EDGE = 16
FEATURES = ('bright_points_per_mp', 'luma_p50', 'luma_p95', 'sat_mean', 'dark_fine_edge')
MARGIN_IQR = 0.5
FLOOR = {'bright_points_per_mp': 25.0, 'luma_p50': 6.0, 'luma_p95': 8.0, 'sat_mean': 0.03, 'dark_fine_edge': 0.01}


def _percentile(histogram, total, q):
    target, running = total * q, 0
    for value, count in enumerate(histogram):
        running += count
        if running >= target:
            return value
    return 255


def _bright_points(luma, threshold, max_area):
    """Connected bright components (4-neighbour) no larger than max_area pixels."""
    w, h = luma.size
    data = luma.load()
    bright = {(x, y) for y in range(h) for x in range(w) if data[x, y] >= threshold}
    seen, count = set(), 0
    for start in bright:
        if start in seen:
            continue
        stack, area = [start], 0
        seen.add(start)
        while stack:
            x, y = stack.pop()
            area += 1
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if n in bright and n not in seen:
                    seen.add(n)
                    stack.append(n)
        if area <= max_area:
            count += 1
    return count


def frame_features(image):
    from .qa_generative import _gradient
    luma = image.convert('L')
    w, h = luma.size
    total = w * h
    hist = luma.histogram()
    p50, p95, p995 = (_percentile(hist, total, q) for q in (0.5, 0.95, 0.995))
    points = _bright_points(luma, max(p995, BRIGHT_MIN), max(4, POINT_MAX_FRAC * total))
    sat = image.convert('HSV').getchannel('S')
    sat_mean = sum(v * c for v, c in enumerate(sat.histogram())) / total / 255
    grad = _gradient(image).load()
    lum = luma.load()
    dark = [(x, y) for y in range(h) for x in range(w) if lum[x, y] < p50]
    fine = sum(1 for x, y in dark if grad[x, y] >= FINE_EDGE)
    return {'bright_points_per_mp': round(points / (total / 1e6), 2), 'luma_p50': p50, 'luma_p95': p95,
            'sat_mean': round(sat_mean, 4), 'dark_fine_edge': round(fine / len(dark), 4) if dark else 0.0}


def features(video, start=None, end=None, samples=SAMPLES):
    """Per-frame features of `samples` frames evenly spread over [start, end) seconds (whole clip by default)."""
    from .qa_generative import _ffmpeg, _scaled_height
    height = _scaled_height(video, WIDTH)
    args = []
    if start is not None:
        args += ['-ss', f'{start}']
    args += ['-i', video]
    if end is not None:
        args += ['-t', f'{end - (start or 0)}']
    raw = _ffmpeg(args + ['-vf', f'scale={WIDTH}:{height}:flags=bicubic,format=rgb24', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    from PIL import Image
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
    """ranges: [[start_s, end_s] | None] per video (e.g. only the night aerial of a reel)."""
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
        raise StudioError('INPUT_INVALID', f'No look style {name} (learn it with look style learn)')
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
            'warnings': [f"look_style {name}: {f} {verdict[f]['value']} is {verdict[f]['direction']} "
                         f"(style {verdict[f]['low']}..{verdict[f]['high']})" for f in misses]}


def style_for(shot, style):
    """The look style a shot is judged against: shot.render.look_style, else style.look.look_style, else None."""
    return shot.get('render', {}).get('look_style') or ((style or {}).get('look') or {}).get('look_style')


def register_commands(subparsers):
    parser = subparsers.add_parser('look', help='Look styles learned from reference shots (numbers only, never sent anywhere)')
    commands = parser.add_subparsers(dest='look_command', required=True)
    style = commands.add_parser('style').add_subparsers(dest='style_command', required=True)
    p = style.add_parser('learn'); p.add_argument('--name', required=True); p.add_argument('--video', action='append', required=True)
    p.add_argument('--range', action='append', help='start,end seconds for the matching --video')
    p.set_defaults(handler=lambda a: learn(a.name, a.video, [[float(x) for x in r.split(',')] for r in a.range] if a.range else None))
    p = style.add_parser('show'); p.add_argument('--name', required=True)
    p.set_defaults(handler=lambda a: load(a.name))
    p = style.add_parser('check'); p.add_argument('--name', required=True); p.add_argument('--video', required=True)
    p.add_argument('--start', type=float); p.add_argument('--end', type=float)
    p.set_defaults(handler=lambda a: check(a.name, a.video, a.start, a.end))
