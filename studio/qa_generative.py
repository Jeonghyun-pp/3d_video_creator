"""QA for generated clips. flicker / morph / text are warnings; structure is the hybrid gate (default FAIL).

Hybrid contract (routing.py): Blender renders the complete motion pass and the video-to-video model
restyles only the look, so the generated clip must keep the previs structure. structure() compares the
generated clip with the clay control pass by edge overlap and by tracking each label anchor; labels are
only placed from its measured anchors_2d when it passes. Pure Pillow + ffmpeg (no numpy/opencv).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import tempfile

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

from .common import StudioError

WIDTH = 448
# flicker: per-frame luma change (signalstats YDIF) flagged above max(3 x median, median + 8).
FLICKER_RATIO, FLICKER_FLOOR = 3.0, 8.0
# morph: consecutive-frame SSIM flagged when it drops more than this below the clip median.
MORPH_DROP = 0.15
# Structure edges: colour gradient of the frame blurred by EDGE_BLUR; a pixel is an edge when its gradient
# is >= max(EDGE_MIN, EDGE_FRACTION x the frame's strongest gradient). Relative, so a darker or flatter
# restyle is judged on its own contrast; the floor keeps codec noise on flat frames from becoming edges.
EDGE_BLUR, EDGE_MIN, EDGE_FRACTION = 1.5, 8, 0.4
EDGE_TOLERANCE_PX = 2          # IoU counts an edge as matched within this radius (MaxFilter 2r+1)
MIN_EDGE_PIXELS = 50           # previs frames with fewer edges carry no structure and are skipped
# IoU gate, calibrated 2026-10-04 at 448 px (median edge IoU; reproduce with tests/test_qa_generative.py), re-measured
# 2026-10-05 after the control clay lights became shadowless (control_pass.clay_lights):
#                                   testsrc 448x256   clay 10-04 (shadowed)   clay 10-05 (shadowless)
#   identical                           1.000              1.000                  1.000
#   gblur 1.5 + hue 90 + eq             0.694              1.000                  1.000     must pass
#   ... + temporal grain (noise 20)     0.708              0.867                  0.977     must pass
#   6 px (1.3 %) horizontal shift       0.341              0.415                  0.368     must fail
#   5 % horizontal shift                0.210              0.351                  0.435     must fail
#   25 % unrelated pattern overlay      0.175              0.021                  0.013     fails (texture swamps structure)
# 0.5 sits between the worst pass (0.694) and the worst shift (0.435). The 0.25 first proposed would let
# a 5 % shifted clay clip (0.351) through: horizontal edges survive a horizontal shift.
IOU_THRESHOLD = 0.5
ANCHOR_MAX_ERROR_RATIO = 0.01  # 1 % of frame width (4.5 px at 448)
SEARCH_PX = 24
PATCH_SIZES = (32, 64, 96, 128, 192)  # grow the template until it holds structure that pins a unique match
DISTINCT = 0.3                 # patch must self-correlate <= 1 - DISTINCT at every offset >4 px away (see track)
TEMPLATE_STD_MIN = 8           # tracking-map std (0-255) a patch needs before it counts as structure
MATCH_MIN = 0.7                # best NCC in the generated frame below this = anchor structure lost (fails)
SAMPLE_STEP = 3                # IoU on every 3rd frame
ANCHOR_SAMPLES = 40            # anchors tracked on at most this many evenly spaced sampled frames
COARSE_PX = 4                  # NCC search grid before the 1 px refinement


def _probe(video):
    probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_packets', '-show_entries',
                            'stream=width,height,nb_read_packets', '-of', 'json', str(video)], capture_output=True, text=True)
    if probe.returncode:
        raise StudioError('QA_FAILED', probe.stderr[-1000:])
    stream = json.loads(probe.stdout)['streams'][0]
    return stream['width'], stream['height'], int(stream['nb_read_packets'])


def _ffmpeg(args):
    result = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', *map(str, args)], capture_output=True)
    if result.returncode:
        raise StudioError('QA_FAILED', result.stderr.decode(errors='replace')[-1000:])
    return result.stdout


def _rgb_frames(video, width, height, step=1):
    raw = _ffmpeg(['-i', video, '-vf', f"select='not(mod(n\\,{step}))',scale={width}:{height}:flags=bicubic,format=rgb24",
                   '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    size = width * height * 3
    return [Image.frombytes('RGB', (width, height), raw[i:i + size]) for i in range(0, len(raw) - size + 1, size)]


def _scaled_height(video, width):
    w, h, _ = _probe(video)
    return max(2, round(h * width / w / 2) * 2)


def _flags(values, limit, first_frame, below=False):
    return [{'frame': first_frame + i, 'value': round(v, 4)} for i, v in enumerate(values) if (v < limit if below else v > limit)]


def flicker(video, width=WIDTH):
    """Luma flicker: frames whose mean |Y(n) - Y(n-1)| spikes against the clip's own median."""
    out = _ffmpeg(['-i', video, '-vf', f'scale={width}:-2,signalstats,metadata=mode=print:key=lavfi.signalstats.YDIF:file=-', '-f', 'null', '-'])
    ydif = [float(x) for x in re.findall(rb'lavfi\.signalstats\.YDIF=([0-9.]+)', out)][1:]   # frame 0 has no predecessor
    if not ydif:
        return {'status': 'not_measured', 'reason': 'fewer than two frames', 'flagged': [], 'warnings': []}
    median = statistics.median(ydif)
    limit = max(FLICKER_RATIO * median, median + FLICKER_FLOOR)
    flagged = _flags(ydif, limit, 1)
    return {'status': 'warning' if flagged else 'ok', 'median_ydif': round(median, 4), 'threshold': round(limit, 4), 'max_ydif': round(max(ydif), 4),
            'flagged': flagged, 'warnings': [f"flicker: luma jump at frame {f['frame']} (YDIF {f['value']} > {limit:.2f})" for f in flagged]}


def morph(video, width=WIDTH):
    """Morphing / boiling: consecutive-frame SSIM dropping well below the clip's own median."""
    graph = (f'[0:v]scale={width}:-2,format=gray,split[a][b];[a]trim=start_frame=1,setpts=PTS-STARTPTS[next];'
             '[b]setpts=PTS-STARTPTS[prev];[next][prev]ssim=stats_file=-:shortest=1')
    out = _ffmpeg(['-i', video, '-filter_complex', graph, '-f', 'null', '-'])
    ssim = [float(x) for x in re.findall(rb'All:([0-9.]+)', out)]
    if not ssim:
        return {'status': 'not_measured', 'reason': 'fewer than two frames', 'flagged': [], 'warnings': []}
    median = statistics.median(ssim)
    limit = median - MORPH_DROP
    flagged = _flags(ssim, limit, 1, below=True)   # value = SSIM(frame n, frame n-1)
    return {'status': 'warning' if flagged else 'ok', 'median_ssim': round(median, 4), 'threshold': round(limit, 4), 'min_ssim': round(min(ssim), 4),
            'flagged': flagged, 'warnings': [f"morph: structure jump at frame {f['frame']} (SSIM {f['value']} < {limit:.3f})" for f in flagged]}


def text(video, fps=1):
    """Generated text is usually gibberish; OCR one frame per second when tesseract is installed."""
    if not shutil.which('tesseract'):
        return {'status': 'not_available', 'warnings': []}
    with tempfile.TemporaryDirectory(prefix='qa-text-') as temp:
        _ffmpeg(['-i', video, '-vf', f'fps={fps}', str(Path(temp) / 'f_%04d.png')])
        found = []
        for index, image in enumerate(sorted(Path(temp).glob('f_*.png'))):
            result = subprocess.run(['tesseract', str(image), 'stdout', '--psm', '11'], capture_output=True, text=True)
            words = ' '.join(result.stdout.split())
            if sum(ch.isalnum() for ch in words) >= 3:
                found.append({'second': index / fps, 'text': words[:200]})
    return {'status': 'warning' if found else 'ok', 'frames': found,
            'warnings': [f"text: generated text at {f['second']:.0f}s: {f['text'][:60]!r}" for f in found]}


def _gradient(image):
    """Colour gradient max_c(|dC/dx| + |dC/dy|) of the pre-blurred frame.

    Colour, not luma: a restyle that recolours two neighbouring surfaces to equal brightness keeps their
    boundary as a colour edge, so recolouring alone never reads as lost structure.
    """
    soft = image.filter(ImageFilter.GaussianBlur(EDGE_BLUR))
    channels = ImageChops.add(ImageChops.difference(soft, ImageChops.offset(soft, 1, 0)), ImageChops.difference(soft, ImageChops.offset(soft, 0, 1))).split()
    gradient = channels[0]
    for channel in channels[1:]:
        gradient = ImageChops.lighter(gradient, channel)
    return ImageChops.multiply(gradient, _interior(image.size))   # offset wraps; frame borders are not structure


def _edges(gradient):
    """IoU edges: the frame's strong edges, relative to its own contrast."""
    level = max(EDGE_MIN, EDGE_FRACTION * gradient.getextrema()[1])
    return gradient.point(lambda v: 255 if v >= level else 0)


def _track_map(gradient):
    """Tracking map: the continuous gradient, softened and stretched to full range for NCC precision.

    Continuous, not thresholded: a boundary near any threshold flips on/off between previs and restyle,
    while NCC lets strong boundaries dominate and is blind to a restyle's overall contrast change.
    """
    return ImageOps.autocontrast(gradient.filter(ImageFilter.GaussianBlur(2)), cutoff=(0, 0.5))


_INTERIOR = {}


def _interior(size):
    if size not in _INTERIOR:
        mask = Image.new('L', size, 0)
        mask.paste(255, (2, 2, size[0] - 2, size[1] - 2))
        _INTERIOR[size] = mask
    return _INTERIOR[size]


def edge_map(image):
    """Strong edges (255) of a still RGB image - the map the generation gate compares (also used by photo_match)."""
    return _edges(_gradient(image.convert('RGB')))


def edge_overlap(a, b):
    """{iou, preservation, extra} of two edge maps within EDGE_TOLERANCE_PX (see _overlap)."""
    return _overlap(a, b)


def _count(binary):
    return binary.histogram()[255]


def _overlap(a, b):
    """Edge agreement with EDGE_TOLERANCE_PX between a (the previs) and b (the generated frame): an edge pixel
    matches if the other map has an edge within the radius. iou (the gate), preservation = share of a's edges
    found in b (structure kept), extra = share of b's edges not near any of a's (invented detail)."""
    size = 2 * EDGE_TOLERANCE_PX + 1
    na, nb = _count(a), _count(b)
    if na + nb == 0:
        return {'iou': 1.0, 'preservation': 1.0, 'extra': 0.0}
    a_hit = _count(ImageChops.multiply(a, b.filter(ImageFilter.MaxFilter(size))))
    b_hit = _count(ImageChops.multiply(b, a.filter(ImageFilter.MaxFilter(size))))
    matched = (a_hit + b_hit) / 2
    return {'iou': matched / (na + nb - matched), 'preservation': a_hit / na if na else 1.0, 'extra': 1 - b_hit / nb if nb else 0.0}



class _Template:
    """Zero-mean normalised cross-correlation of one patch against crops of a map (Pillow only)."""

    def __init__(self, image, x, y, size):
        half = size // 2
        self.size, self.half = size, half
        self.patch = image.crop((x - half, y - half, x - half + size, y - half + size))
        stat = ImageStat.Stat(self.patch)
        self.n = size * size
        self.mean = stat.sum[0] / self.n
        self.var = stat.sum2[0] - self.n * self.mean ** 2

    def ncc(self, image, x, y):
        crop = image.crop((x - self.half, y - self.half, x - self.half + self.size, y - self.half + self.size))
        stat = ImageStat.Stat(crop)
        mean = stat.sum[0] / self.n
        var = stat.sum2[0] - self.n * mean ** 2
        # A near-flat crop has a tiny denominator, and the 8-bit products' rounding would then dominate:
        # a region with under a quarter of the patch's structure is not a match, whatever its shape.
        if var < 0.25 * self.var or self.var <= 0:
            return 0.0
        cross = ImageStat.Stat(ImageChops.multiply(self.patch, crop)).sum[0] * 255   # multiply divides by 255
        return (cross - self.n * self.mean * mean) / math.sqrt(self.var * var)


def _search(template, image, x, y, search, refine=True):
    """Best NCC offset over +-search on a COARSE_PX grid, refined to 1 px. Returns (offset, score, coarse map).

    The tracking maps are blurred (sigma 2), so every correlation peak is wider than the grid spacing.
    """
    grid = range(-search, search + 1, COARSE_PX)
    coarse = {(dx, dy): template.ncc(image, x + dx, y + dy) for dx in grid for dy in grid}
    best = max(coarse, key=coarse.get)
    score = coarse[best]
    if refine:
        span = range(-COARSE_PX + 1, COARSE_PX)
        fine = {(best[0] + i, best[1] + j): template.ncc(image, x + best[0] + i, y + best[1] + j) for i in span for j in span}
        best = max(fine, key=fine.get)
        score = fine[best]
    return best, score, coarse


def track(previs_map, generated_map, x, y, search=SEARCH_PX):
    """Track the previs patch around (x, y) into the generated frame.

    Returns {'dx', 'dy', 'patch_px', 'ncc'}, {'lost': ...} when the best match is below MATCH_MIN (the
    structure is not there; counts as a failure), or None when no patch size is distinctive in the previs.
    Distinctive: every offset more than 4 px away correlates at most 1 - DISTINCT with the patch itself.
    A straight edge (aperture problem), a flat face or a repeated pattern fails it, and the patch grows.
    """
    for size in PATCH_SIZES:
        template = _Template(previs_map, x, y, size)
        if template.var < template.n * TEMPLATE_STD_MIN ** 2:   # flat: nothing to correlate
            continue
        _, _, own = _search(template, previs_map, x, y, search, refine=False)
        rival = max((v for k, v in own.items() if math.hypot(*k) > 4), default=-1.0)
        if rival > 1 - DISTINCT:
            continue
        best, score, _ = _search(template, generated_map, x, y, search)
        result = {'dx': best[0], 'dy': best[1], 'patch_px': size, 'ncc': round(score, 4)}
        if score < MATCH_MIN:
            result['lost'] = True
        return result
    return None


def structure(previs_clay, generated, anchors=None, width=WIDTH, iou_threshold=IOU_THRESHOLD, step=SAMPLE_STEP):
    """Hybrid structure gate: default FAIL; passes only when edges overlap and every measured anchor stays put."""
    height = _scaled_height(previs_clay, width)
    _, _, previs_count = _probe(previs_clay)
    _, _, generated_count = _probe(generated)
    reasons = []
    if previs_count != generated_count:
        reasons.append(f'frame count {generated_count} != previs {previs_count}; conform timing before structure QA')
    previs_frames = _rgb_frames(previs_clay, width, height, step)
    generated_frames = _rgb_frames(generated, width, height, step)
    anchor_stride = max(1, math.ceil(len(previs_frames) / ANCHOR_SAMPLES))
    by_frame = {}
    for row in anchors or []:
        if row.get('visible'):
            by_frame.setdefault(row['frame'], []).append(row)
    per_frame, errors, offsets, lost = [], [], {}, []
    for index, (p, g) in enumerate(zip(previs_frames, generated_frames)):
        frame = index * step
        pg, gg = _gradient(p), _gradient(g)
        pe, ge = _edges(pg), _edges(gg)
        if _count(pe) >= MIN_EDGE_PIXELS:
            agree = _overlap(pe, ge)
            per_frame.append({'frame': frame, 'iou': round(agree['iou'], 4), 'preservation': round(agree['preservation'], 4),
                              'extra': round(agree['extra'], 4)})
        if frame in by_frame and index % anchor_stride == 0:
            ps, gs = _track_map(pg), _track_map(gg)
            for row in by_frame[frame]:
                found = track(ps, gs, round(row['u'] * width), round(row['v'] * height))
                if found and found.get('lost'):
                    lost.append({'frame': frame, 'label_id': row['label_id'], **found})
                elif found:
                    dx, dy = found['dx'], found['dy']
                    errors.append({'frame': frame, 'label_id': row['label_id'], 'dx_px': dx, 'dy_px': dy, 'patch_px': found['patch_px'],
                                   'ncc': found['ncc'], 'error_ratio': round(math.hypot(dx, dy) / width, 5)})
                    offsets.setdefault(row['label_id'], []).append((frame, dx / width, dy / height))
    ious = [r['iou'] for r in per_frame]
    iou = {'median': round(statistics.median(ious), 4) if ious else None, 'min': min(ious) if ious else None,
           'threshold': iou_threshold, 'frames_measured': len(ious), 'per_frame': per_frame}
    # Recorded, not gated (2026-10-05, BUILD_REPORT "H0"): neither separates a restyle that keeps the layout from one
    # that moved it once the model replaces whole surfaces (samsung A/B s03: preservation 0.29 vs 5 %-shifted clay 0.55).
    for key in ('preservation', 'extra'):
        values = [r[key] for r in per_frame]
        iou[key] = {'median': round(statistics.median(values), 4) if values else None,
                    'min': min(values) if values else None, 'max': max(values) if values else None}
    if not ious:
        reasons.append('previs has no measurable edges; structure unverified')
    elif iou['median'] < iou_threshold:
        reasons.append(f"median edge IoU {iou['median']} < {iou_threshold}")
    anchor_error = {'status': 'not_requested', 'max_ratio': None, 'median_ratio': None, 'threshold_ratio': ANCHOR_MAX_ERROR_RATIO}
    if anchors is not None:
        requested = sum(len(rows) for frame, rows in by_frame.items()
                        if frame % step == 0 and frame // step < len(previs_frames) and (frame // step) % anchor_stride == 0)
        ratios = [e['error_ratio'] for e in errors]
        anchor_error = {'status': 'measured' if ratios or lost else 'unverified', 'threshold_ratio': ANCHOR_MAX_ERROR_RATIO,
                        'max_ratio': max(ratios) if ratios else None, 'median_ratio': round(statistics.median(ratios), 5) if ratios else None,
                        'max_px': round(max(ratios) * width, 2) if ratios else None, 'measured': len(ratios), 'lost': len(lost),
                        'requested': requested, 'samples': errors, 'lost_samples': lost}
        sampled = {row['label_id'] for frame, rows in by_frame.items() for row in rows
                   if frame % step == 0 and frame // step < len(previs_frames) and (frame // step) % anchor_stride == 0}
        unverified = sorted(sampled - {e['label_id'] for e in errors + lost})
        anchor_error['unverified_labels'] = unverified
        if unverified:   # per label: a label that was never measured is not known to be stable
            reasons.append(f'anchors never trackable (flat or ambiguous patches), stability unverified: {unverified}')
        if lost:
            reasons.append(f'{len(lost)} anchor samples lost (best NCC < {MATCH_MIN}): structure around the label is not in the clip')
        if ratios and anchor_error['max_ratio'] > ANCHOR_MAX_ERROR_RATIO:
            reasons.append(f"anchor drift {anchor_error['max_ratio']:.4f} x width > {ANCHOR_MAX_ERROR_RATIO}")
    return {'passed': not reasons, 'reasons': reasons, 'width': width, 'height': height, 'sample_step': step,
            'frame_count': {'previs': previs_count, 'generated': generated_count}, 'iou': iou, 'anchor_error': anchor_error,
            'anchors_2d': _anchors_2d(anchors or [], offsets)}


RICH_SAMPLES = 8          # frames measured per clip (evenly spaced)
COVER_TILE = 32           # coverage: share of 32 px tiles that hold structure edges
COVER_MIN = 0.02          # a tile "holds structure" when >= 2 % of its pixels are edges
DISTINCT_TILE = 64        # distinct tiles: 64 px tiles a tracker could pin (the track() distinctiveness test)
FINE_EDGE = 16            # fine edges: absolute gradient >= 16/255 - faint creases and small parts the relative rule drops


def _distinct(gradient, tile=DISTINCT_TILE):
    """How many tile centres pass track()'s distinctiveness test: structure a restyle cannot slide along."""
    track_map = _track_map(gradient)
    w, h = track_map.size
    count = 0
    for y in range(tile // 2 + SEARCH_PX, h - tile // 2 - SEARCH_PX, tile):
        for x in range(tile // 2 + SEARCH_PX, w - tile // 2 - SEARCH_PX, tile):
            template = _Template(track_map, x, y, tile)
            if template.var < template.n * TEMPLATE_STD_MIN ** 2:
                continue
            _, _, own = _search(template, track_map, x, y, SEARCH_PX, refine=False)
            if max((v for k, v in own.items() if math.hypot(*k) > 4), default=-1.0) <= 1 - DISTINCT:
                count += 1
    return count


def richness(video, width=WIDTH, samples=RICH_SAMPLES, start=0, count=None):
    """How much structure a clip hands a video-to-video model, measured the way structure() will judge it.

    edge_fraction: share of pixels that are structure edges (structure()'s own edge rule); coverage: share of
    32 px tiles holding edges (empty sky / flat walls are what a model will invent); distinct_tiles: 64 px
    tiles a tracker could pin (anything else can slide or morph without a check noticing). Run on the clay
    control of a hybrid shot before paying for generation; on a reference clip it is an upper bound
    (texture counts there too)."""
    height = _scaled_height(video, width)
    _, _, total = _probe(video)
    count = count or total - start
    step = max(1, count // samples)
    frames = _rgb_frames(video, width, height, step) if start == 0 and count == total else None
    if frames is None:
        raw = _ffmpeg(['-ss', f'{start / 30:.4f}', '-i', video, '-frames:v', count, '-vf',
                       f"select='not(mod(n\\,{step}))',scale={width}:{height}:flags=bicubic,format=rgb24", '-fps_mode', 'passthrough',
                       '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
        size = width * height * 3
        frames = [Image.frombytes('RGB', (width, height), raw[i:i + size]) for i in range(0, len(raw) - size + 1, size)]
    rows = []
    for index, frame in enumerate(frames[:samples]):
        gradient = _gradient(frame)
        edges = _edges(gradient)
        tiles = [edges.crop((x, y, x + COVER_TILE, y + COVER_TILE)) for y in range(0, height - COVER_TILE + 1, COVER_TILE)
                 for x in range(0, width - COVER_TILE + 1, COVER_TILE)]
        covered = sum(1 for t in tiles if _count(t) >= COVER_MIN * COVER_TILE * COVER_TILE)
        fine = _count(gradient.point(lambda v: 255 if v >= FINE_EDGE else 0))
        rows.append({'frame': start + index * step, 'edge_fraction': round(_count(edges) / (width * height), 5),
                     'fine_edge_fraction': round(fine / (width * height), 5),
                     'coverage': round(covered / max(1, len(tiles)), 4), 'distinct_tiles': _distinct(gradient),
                     'below_min_edges': _count(edges) < MIN_EDGE_PIXELS})
    med = lambda key: round(statistics.median(r[key] for r in rows), 5) if rows else None  # noqa: E731
    return {'width': width, 'frames_measured': len(rows), 'edge_fraction': med('edge_fraction'), 'fine_edge_fraction': med('fine_edge_fraction'),
            'coverage': med('coverage'),
            'distinct_tiles': med('distinct_tiles'), 'frames_below_min_edges': sum(r['below_min_edges'] for r in rows), 'per_frame': rows}


def _anchors_2d(anchors, offsets):
    """Generated-frame label anchors: previs u,v plus the measured offset, linearly interpolated between samples."""
    rows = []
    for row in anchors:
        samples = offsets.get(row['label_id'], [])
        du = dv = 0.0
        if samples:
            before = [s for s in samples if s[0] <= row['frame']] or samples[:1]
            after = [s for s in samples if s[0] >= row['frame']] or samples[-1:]
            a, b = before[-1], after[0]
            t = 0 if b[0] == a[0] else (row['frame'] - a[0]) / (b[0] - a[0])
            du, dv = a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t
        rows.append({**row, 'u': row['u'] + du, 'v': row['v'] + dv, 'measured': bool(samples)})
    return rows


def generative_checks(video, previs=None, anchors=None):
    """All generated-clip checks. Only structure (hybrid, needs the clay previs) can fail the clip."""
    result = {'flicker': flicker(video), 'morph': morph(video), 'text': text(video)}
    result['structure'] = structure(previs, video, anchors) if previs else {'passed': None, 'reasons': ['no previs: structure not run']}
    result['warnings'] = [w for key in ('flicker', 'morph', 'text') for w in result[key]['warnings']]
    result['passed'] = result['structure']['passed']
    return result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video', type=Path)
    parser.add_argument('--previs', type=Path, help='clay control video of the same shot')
    parser.add_argument('--anchors', type=Path, help='anchors.json from the control pass')
    args = parser.parse_args(argv)
    anchors = json.loads(args.anchors.read_text())['frames'] if args.anchors else None
    print(json.dumps(generative_checks(args.video, args.previs, anchors), indent=2))


if __name__ == '__main__':
    main()
