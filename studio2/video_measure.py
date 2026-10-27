"""What a generated clip does over time, measured (never read): flicker, a jump or a frame pop, and the camera's path -
does it keep to the planned move's direction (a push that drifts back out), and is its size near what the plan asked.

Pillow and ffmpeg only (the venv has no numpy or OpenCV). Frames are read small and grey:
- brightness: the mean of each frame; flicker is how far one frame's mean leaves the line through its two neighbours
  (a light change over the shot is smooth, a flicker alternates);
- change: the mean absolute difference of each frame from the one before; a jump is one change many times the changes
  around it (a dropped frame, a pop), a repeat is a still pair between two moving ones (a duplicated frame);
- camera: every STEP-th frame, the zoom about the middle and the shift that best lay the first frame on it (a local
  search from the frame before's answer, the middle of the frame only): the camera's path over the clip. A push grows
  the picture, a pull shrinks it; the planned direction is the camera row's `measured.zoom` (+1 / -1 / 0) in the
  cutaway grammar, so a new move says its own direction and needs no code.

Each measure is a number; whether it is flagged, and whether that blocks, is library/video_checks.json's (calibrated by
studio2 video calibrate), never written here.
"""
from __future__ import annotations

import math
import statistics
import subprocess

from studio.common import StudioError

WIDTH, HEIGHT = 144, 256          # 9:16 at a size the search is quick on; the camera's shift is a share of WIDTH
STEP = 2                          # frames between the camera's measured pairs
MARGIN = 0.15                     # the middle of the frame the camera is measured on (a share cut from each side)
MOTION_FLOOR = 0.3                # a change (0-255) under this is codec noise: a jump is measured against at least this


def frames(clip, width=WIDTH, height=HEIGHT):
    """Every frame of the clip, grey, at width x height."""
    from PIL import Image
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(clip), '-vf', f'scale={width}:{height}:flags=area,format=gray',
                          '-f', 'rawvideo', '-pix_fmt', 'gray', '-'], capture_output=True)
    if raw.returncode:
        raise StudioError('QA_FAILED', raw.stderr.decode(errors='replace')[-800:])
    size = width * height
    data = raw.stdout
    return [Image.frombytes('L', (width, height), data[i:i + size]) for i in range(0, len(data) - size + 1, size)]


def _mean(im, box=None):
    from PIL import ImageStat
    return ImageStat.Stat(im.crop(box) if box else im).mean[0]


def flicker(images):
    """The largest brightness step out of line with its neighbours (0-255 levels), and where."""
    means = [_mean(im) for im in images]
    off = [abs(means[i] - (means[i - 1] + means[i + 1]) / 2) for i in range(1, len(means) - 1)]
    if not off:
        return {'score': None, 'at': None}
    worst = max(range(len(off)), key=off.__getitem__)
    return {'score': round(off[worst], 3), 'at': worst + 1}


def changes(images):
    from PIL import ImageChops
    return [_mean(ImageChops.difference(images[i], images[i - 1])) for i in range(1, len(images))]


def jumps(images, window=3):
    """jump: the largest frame change over the median of the changes around it (a dropped frame, a pop); repeat: the
    share of frame pairs that are still between two moving ones (a duplicated frame)."""
    d = changes(images)
    if len(d) < 3:
        return {'jump': None, 'at': None, 'repeat': None}
    ratios = []
    for i, v in enumerate(d):
        around = d[max(0, i - window):i] + d[i + 1:i + 1 + window]
        ratios.append(v / max(MOTION_FLOOR, statistics.median(around)))
    worst = max(range(len(ratios)), key=ratios.__getitem__)
    moving = max(MOTION_FLOOR, statistics.median(d))
    repeats = sum(1 for i in range(1, len(d) - 1) if d[i] < 0.2 * moving and d[i - 1] > 0.5 * moving and d[i + 1] > 0.5 * moving)
    return {'jump': round(ratios[worst], 3), 'at': worst + 1, 'repeat': round(repeats / len(d), 4)}


def _laid(a, b, zoom, dx, dy, box):
    """How far frame a, zoomed about the middle by `zoom` and shifted (dx, dy), is from frame b in box (mean |diff|)."""
    from PIL import Image, ImageChops
    w, h = a.size
    cx, cy = w / 2, h / 2
    moved = a.transform((w, h), Image.Transform.AFFINE, (1 / zoom, 0, cx - (cx + dx) / zoom, 0, 1 / zoom, cy - (cy + dy) / zoom),
                        resample=Image.Resampling.BILINEAR)
    return _mean(ImageChops.difference(moved.crop(box), b.crop(box)))


def _fit(a, b, start, box):
    """The zoom and shift (px) near `start` that best lay frame a on frame b: zoom, then shift, then both finer."""
    best = start
    cost = _laid(a, b, *best, box)
    for zstep, sstep in ((0.004, 1.0), (0.001, 0.5), (0.00025, 0.25)):
        for _ in range(3):
            z0, x0, y0 = best
            grid = [(z0 + zstep * k, x0 + sstep * i, y0 + sstep * j) for k in range(-3, 4) for i in (-1, 0, 1) for j in (-1, 0, 1)]
            moved = False
            for c in grid:
                e = _laid(a, b, *c, box)
                if e < cost - 1e-9:
                    best, cost, moved = c, e, True
            if not moved:
                break
    return best


def camera_path(images, step=STEP):
    """The camera over the clip: [{frame, growth (picture size against the first, 1 = the same), x, y (shift, a share of
    the width)}]. Each measured frame is laid against the first, the search starting from the frame before's answer,
    so a slow move adds up instead of being lost under the search step."""
    w, h = images[0].size
    box = (round(w * MARGIN), round(h * MARGIN), round(w * (1 - MARGIN)), round(h * (1 - MARGIN)))
    path = [{'frame': 0, 'growth': 1.0, 'x': 0.0, 'y': 0.0}]
    best = (1.0, 0.0, 0.0)
    for i in range(step, len(images), step):
        best = _fit(images[0], images[i], best, box)
        path.append({'frame': i, 'growth': round(best[0], 5), 'x': round(best[1] / w, 5), 'y': round(best[2] / w, 5)})
    return path


def camera(path, sign, asked_amount):
    """What the camera did against the plan. sign: the planned move's zoom direction (+1 push, -1 pull, 0 held, None
    unmeasured). reverse: of the furthest the camera went the planned way, the share it came back by the end (a push
    that drifts back out); amount: how far it went the planned way (picture growth, e.g. 0.08), against asked_amount
    as a ratio; held: for a held camera, the most it moved (growth or shift, a share)."""
    logs = [math.log(p['growth']) for p in path]
    drift = max(max(abs(p['growth'] - 1) for p in path), max(math.hypot(p['x'], p['y']) for p in path))
    out = {'moved': round(drift, 4), 'growth_end': round(path[-1]['growth'] - 1, 4), 'reverse': None, 'amount': None, 'ratio': None}
    if sign is None:
        return out
    if sign == 0:
        out['held'] = round(drift, 4)
        return out
    along = [sign * v for v in logs]
    peak_at = max(range(len(along)), key=along.__getitem__)
    peak = along[peak_at]
    out['amount'] = round(math.exp(peak) - 1, 4)
    out['reverse'] = round((peak - along[-1]) / peak, 4) if peak > 1e-3 else 0.0
    if asked_amount:
        out['ratio'] = round(out['amount'] / asked_amount, 3)
    return out


def measure(clip, sign=None, asked_amount=None):
    """Every measure of the clip (the numbers library/video_checks.json is read against)."""
    images = frames(clip)
    if len(images) < 4:
        raise StudioError('INPUT_INVALID', f'{clip}: {len(images)} frames - too few to measure')
    path = camera_path(images)
    return {'frames': len(images), 'flicker': flicker(images), 'jumps': jumps(images), 'camera': camera(path, sign, asked_amount),
            'path': path}
