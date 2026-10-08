"""Reference critique: what differs most between a reference frame and ours, region by region, ranked - and the shot
value that would close each difference, proposed (never applied).

Why (archcut3, 2026-10-08): every gate passed and all three shots were still below the reference; the differences a
person saw (a toy-like city on a pink ground, a blue cast, people too small, a ceiling too bright) lived in no number,
so a run ended with a list of differences instead of closing them. The gates measure what is broken; this measures
what is different from the picture the request points at.

Regions: a grid of bands (top / upper / middle / lower / bottom, each left / centre / right) - the same pixels in both
frames, which is right once the camera is matched (reference_fit_camera, screen targets) - plus, when the caller gives
our id pass, our classes (subject, support, each key part) applied to both frames, and part boxes marked on a reference
view. Per region: mean CIE L*, a*, b* (sRGB D65 -> Lab through PIL's ImageCms), chroma, contrast (L* spread) and edge
density (qa_generative.edge_map). PIL only: the host has no numpy.

Scale of a difference (one unit = one noticeable step, from CIE76 practice and the look_style floors): 6 L*, 6 in
a* or b*, 8 chroma, 5 L* spread, edge density x1.6. A difference is ranked by its size in those units times the
region's share of the frame (sqrt, so a small region with a large miss still ranks). Below one unit nothing is said.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageCms, ImageFilter, ImageStat

from .blender_ops.regions_core import BANDS, COLUMNS, l_star_to_y

SIZE = 360                     # long side the two frames are compared at (enough for region means, cheap)
UNITS = {'L': 6.0, 'a': 6.0, 'b': 6.0, 'chroma': 8.0, 'contrast': 5.0, 'edges': math.log(1.6)}
EDGE_FLOOR = 0.02              # edge density below this is 'flat' in both: ratios of near-zero densities say nothing
WHAT = {'L': 'brightness (L*)', 'a': 'green-red (a*)', 'b': 'blue-yellow (b*)', 'chroma': 'colourfulness (chroma)',
        'contrast': 'contrast (L* spread)', 'edges': 'detail (edge density)'}
TOP = 12                       # differences listed
GLOBAL_SHARE = 0.6             # a shift seen in this share of the frame's band cells is a grade, not a region


def _lab(image):
    srgb, lab = ImageCms.createProfile('sRGB'), ImageCms.createProfile('LAB')
    return ImageCms.applyTransform(image.convert('RGB'), ImageCms.buildTransform(srgb, lab, 'RGB', 'LAB'))


def _load(image, size):
    image = image if isinstance(image, Image.Image) else Image.open(image)
    return image.convert('RGB').resize(size, Image.BILINEAR)


def _edges(image):
    """Edge pixels (255) of an image (the same detector qa_generative uses for structure)."""
    from .qa_generative import edge_map
    return edge_map(image)


def measure(image, lab, edges, mask):
    """{L, a, b, chroma, contrast, edges, share} of the pixels under mask (L 0-100, a/b signed)."""
    count = ImageStat.Stat(mask).sum[0] / 255
    if count < 4:
        return None
    stat = ImageStat.Stat(lab, mask)
    L = stat.mean[0] * 100 / 255
    a, b = stat.mean[1] - 128, stat.mean[2] - 128   # PIL stores Lab a*/b* offset by 128
    edge = ImageStat.Stat(edges, mask).mean[0] / 255
    return {'L': L, 'a': a, 'b': b, 'chroma': math.hypot(a, b), 'contrast': stat.stddev[0] * 100 / 255,
            'edges': edge, 'share': count / (mask.size[0] * mask.size[1])}


def grid_regions(size):
    w, h = size
    out = {}
    for band, y0, y1 in BANDS:
        for column, x0, x1 in COLUMNS:
            mask = Image.new('L', size, 0)
            mask.paste(255, (round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)))
            out[f'{band}-{column}'] = mask
    return out


def class_regions(id_png, palette, size):
    """Our classes from an id pass ({'#rrggbb': class or part key}): one mask per class, the same pixels used on both."""
    ids = Image.open(id_png).convert('RGBA').resize(size, Image.NEAREST)
    r, g, b, alpha = ids.split()
    out = {}
    for colour, name in palette.items():
        rgb = tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))
        hit = ImageChops.multiply(ImageChops.multiply(r.point(lambda v, c=rgb[0]: 255 if v == c else 0), g.point(lambda v, c=rgb[1]: 255 if v == c else 0)),
                                  ImageChops.multiply(b.point(lambda v, c=rgb[2]: 255 if v == c else 0), alpha.point(lambda v: 255 if v > 250 else 0)))
        if hit.getbbox():
            out[f'class:{name}'] = hit if f'class:{name}' not in out else ImageChops.lighter(out[f'class:{name}'], hit)
    return out


def _delta(metric, ref, ours):
    if metric == 'edges':
        return math.log(max(ours['edges'], EDGE_FLOOR) / max(ref['edges'], EDGE_FLOOR))
    return ours[metric] - ref[metric]


def compare(reference, ours, regions=None, size=None):
    """Rows {region, metric, ref, ours, delta, units, rank_score} for every region and metric, largest first.
    regions: {name: mask at `size`} beside the grid (class masks, part boxes)."""
    ref_image = reference if isinstance(reference, Image.Image) else Image.open(reference)
    w, h = ref_image.size
    size = size or ((SIZE, round(SIZE * h / w)) if w >= h else (round(SIZE * w / h), SIZE))
    ref_image, our_image = _load(ref_image, size), _load(ours, size)
    labs, edges = (_lab(ref_image), _lab(our_image)), (_edges(ref_image), _edges(our_image))
    rows, measured = [], {}
    given = {k: (m if m.size == size else m.resize(size, Image.NEAREST)) for k, m in (regions or {}).items()}   # masks at any size
    for name, mask in {'frame': Image.new('L', size, 255), **grid_regions(size), **given}.items():
        ref_m = measure(ref_image, labs[0], edges[0], mask)
        our_m = measure(our_image, labs[1], edges[1], mask)
        if ref_m is None or our_m is None:
            continue
        measured[name] = (ref_m, our_m)
        for metric, unit in UNITS.items():
            delta = _delta(metric, ref_m, our_m)
            units = abs(delta) / unit
            rows.append({'region': name, 'metric': metric, 'ref': round(ref_m[metric], 3), 'ours': round(our_m[metric], 3),
                         'delta': round(delta, 3), 'units': round(units, 2), 'rank_score': round(units * math.sqrt(ref_m['share']), 3)})
    rows.sort(key=lambda r: -r['rank_score'])
    return rows, measured, size


def _is_cell(region):
    return region not in ('frame',) and not region.startswith(('class:', 'part:'))


def global_shifts(rows):
    """A metric that moved the same way in most band cells is a whole-frame shift (a grade or exposure), not a region;
    its size is the frame-wide difference."""
    cells = [r for r in rows if _is_cell(r['region'])]
    frame = {r['metric']: r for r in rows if r['region'] == 'frame'}
    total = len({r['region'] for r in cells}) or 1
    out = {}
    for metric in UNITS:
        same = [r for r in cells if r['metric'] == metric and r['units'] >= 1]
        for sign in (1, -1):
            hit = [r for r in same if r['delta'] * sign > 0]
            if len(hit) >= GLOBAL_SHARE * total:
                out[metric] = {'sign': sign, 'cells': len(hit), 'mean_delta': frame[metric]['delta'] if metric in frame else
                               round(sum(r['delta'] for r in hit) / len(hit), 3)}
    return out


def residual_rows(rows, shifts):
    """Band cells with the whole-frame shift taken out: what still differs where, once the grade is matched (the pink
    ground under an overall too-bright frame)."""
    out = []
    for r in rows:
        if r['metric'] in shifts and _is_cell(r['region']) and r['metric'] != 'edges':
            delta = r['delta'] - shifts[r['metric']]['mean_delta']
            unit = UNITS[r['metric']]
            r = {**r, 'delta': round(delta, 3), 'residual_of': round(shifts[r['metric']]['mean_delta'], 3), 'units': round(abs(delta) / unit, 2)}
            r['rank_score'] = round(r['units'] * math.sqrt(1 / 15), 3)
        out.append(r)
    return out


def propose(shot, row, shifts):
    """The shot value that would close one difference (shot_edit / storyboard op grammar), or a hint when the fix is
    modelling or materials rather than a value."""
    grade = ((shot or {}).get('render') or {}).get('grade') or {}
    metric, region = row['metric'], row['region']
    if region == 'frame' and metric in shifts:
        if metric == 'L':
            ev = math.log2(max(l_star_to_y(row['ref']), 1e-4) / max(l_star_to_y(row['ours']), 1e-4))
            return {'op': 'set', 'path': '/render/grade/exposure_offset_ev', 'value': round(grade.get('exposure_offset_ev', 0.0) + ev, 2),
                    'why': f"the whole frame is {'darker' if ev > 0 else 'brighter'} than the reference by about {abs(ev):.2f} EV"}
        if metric == 'b':
            warmer = row['delta'] < 0   # ours bluer: a higher scene white point warms the picture
            step = 500 * max(1, round(abs(row['delta']) / 6))
            current = grade.get('white_balance_k')
            return {'op': 'set', 'path': '/render/grade/white_balance_k', 'value': (current + (step if warmer else -step)) if current else None,
                    'why': f"the whole frame is {'bluer' if warmer else 'yellower'} than the reference (b* {row['delta']:+.1f}); "
                           f"{'raise' if warmer else 'lower'} the white balance about {step} K" + ('' if current else ' from the look preset value')}
        if metric in ('chroma', 'a'):
            return {'hint': f'whole-frame {WHAT[metric]} differs: grade saturation / the look preset, or the materials together'}
    if metric == 'L':
        return {'op': 'add', 'path': '/screen/light/regions',
                'value': {'region': region, 'luminance': round(row['ref'], 1), 'tol': 4.0},
                'why': f"{region} is {'brighter' if row['delta'] > 0 else 'darker'} than the reference (L* {row['ours']:.0f} vs {row['ref']:.0f}"
                       f"{', beyond the whole-frame shift' if 'residual_of' in row else ''}): "
                       f"a light, a practical or that surface's material"}
    if metric == 'edges':
        return {'hint': f"{region} has {'more' if row['delta'] > 0 else 'less'} fine detail than the reference: "
                        f"{'model the missing tier or add surface texture (SKILL #8)' if row['delta'] < 0 else 'simplify, soften or defocus it'}"}
    if metric in ('a', 'b', 'chroma'):
        return {'hint': f"{region} colour differs ({WHAT[metric]} {row['ours']:.0f} vs {row['ref']:.0f}): the material there, a coloured light, or the sky/world"}
    return {'hint': f'{region} {WHAT[metric]} differs: light ratio or material roughness'}


def size_rows(view_parts, our_boxes, size, ref_size):
    """Part boxes marked on a reference view against our boxes of the same part (id pass): height and centre."""
    rows = []
    for part, box in (view_parts or {}).items():
        ours = our_boxes.get(part)
        if not ours:
            rows.append({'region': f'part:{part}', 'metric': 'presence', 'units': 3.0, 'rank_score': 3.0, 'ref': 1, 'ours': 0, 'delta': -1})
            continue
        ref_h, our_h = (box[3] - box[1]) / ref_size[1], (ours[3] - ours[1]) / size[1]
        delta = our_h - ref_h
        rows.append({'region': f'part:{part}', 'metric': 'height_share', 'ref': round(ref_h, 4), 'ours': round(our_h, 4), 'delta': round(delta, 4),
                     'units': round(abs(delta) / 0.02, 2), 'rank_score': round(abs(delta) / 0.02, 2),
                     'proposal': {'op': 'add', 'path': '/screen/targets', 'value': {'id': f'{part}-height'.replace('/', '-'), 'metric': 'height_share',
                                                                                    'of': part, 'value': round(ref_h, 4), 'tol': 0.02},
                                  'why': f'{part} is {abs(delta) / ref_h:.0%} {"smaller" if delta < 0 else "larger"} on screen than in the reference'}})
    return rows


def critique(reference, ours, *, shot=None, regions=None, extra_rows=(), out_dir=None, label='frame'):
    """The ranked differences (at most TOP, each at least one unit) with a proposal and, with out_dir, a crop pair."""
    rows, _measured, size = compare(reference, ours, regions)
    shifts = global_shifts(rows)
    ranked = [r for r in residual_rows(rows, shifts) if r['region'] != 'frame' or r['metric'] in shifts]
    said = []
    for row in sorted(ranked + list(extra_rows), key=lambda r: -r['rank_score']):
        if row['units'] < 1:
            continue
        if row['region'] == 'frame':
            row = {**row, 'rank_score': max(row['rank_score'], row['units'])}
        said.append({**row, 'region': 'whole frame' if row['region'] == 'frame' else row['region'],
                     'proposal': row.get('proposal') or propose(shot, row, shifts)})
        if len(said) >= TOP:
            break
    sheet = None
    if out_dir is not None and said:
        sheet = _sheet(reference, ours, said, size, Path(out_dir), label, regions)
    return {'differences': said, 'shifts': shifts, 'size': list(size), 'sheet': sheet,
            'score': round(sum(r['rank_score'] for r in rows if r['units'] >= 1), 2)}


def _box(region, size, regions):
    if regions and region in regions:
        mask = regions[region]
        return (mask if mask.size == size else mask.resize(size, Image.NEAREST)).getbbox()
    for band, y0, y1 in BANDS:
        for column, x0, x1 in COLUMNS:
            if region == f'{band}-{column}':
                return (round(x0 * size[0]), round(y0 * size[1]), round(x1 * size[0]), round(y1 * size[1]))
    return (0, 0, size[0], size[1])


def _sheet(reference, ours, said, size, out_dir, label, regions):
    """One row per difference: reference crop | our crop | the numbers."""
    from PIL import ImageDraw
    big = (size[0] * 2, size[1] * 2)
    ref_image, our_image = _load(reference if isinstance(reference, Image.Image) else Image.open(reference), big), _load(ours, big)
    cell = 220
    sheet = Image.new('RGB', (cell * 2 + 420, cell * len(said)), (20, 20, 24))
    draw = ImageDraw.Draw(sheet)
    for i, row in enumerate(said):
        box = _box(row['region'], size, regions) if row['region'] != 'whole frame' else (0, 0, size[0], size[1])
        box = tuple(2 * v for v in box)
        for j, image in enumerate((ref_image, our_image)):
            crop = image.crop(box)
            crop.thumbnail((cell, cell))
            sheet.paste(crop, (j * cell, i * cell))
        prop = row['proposal']
        text = [f"{i + 1}. {row['region']} - {WHAT.get(row['metric'], row['metric'])}",
                f"ref {row['ref']}  ours {row['ours']}  ({row['units']} units)",
                (prop.get('why') or prop.get('hint') or '')[:60], (prop.get('why') or prop.get('hint') or '')[60:120]]
        for k, line in enumerate(text):
            draw.text((cell * 2 + 10, i * cell + 10 + k * 18), line, fill=(230, 232, 236))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f'{label}_critique.png'
    sheet.save(path)
    return str(path)


# ---- videos, the CLI, the workbench tool --------------------------------------------------------------------------

def frame_at(video, seconds, dest):
    """One frame of a video at a time (ffmpeg, accurate seek)."""
    from .audio import run_media
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(video), '-ss', f'{seconds:.3f}', '-frames:v', '1', str(dest)])
    return dest


def critique_videos(reference, ours, times, out_dir, *, shots=None, reference_offset_s=0.0):
    """The same moments of two videos (ours at t, the reference at t + offset), one critique each. shots: [(shot, start_s,
    end_s)] to read each moment's shot values for the proposals."""
    out_dir = Path(out_dir)
    rows = []
    for t in times:
        shot = next((s for s, a, b in (shots or []) if a <= t < b), None)
        ref = frame_at(reference, t + reference_offset_s, out_dir / f'ref_{t:.2f}.png')
        got = frame_at(ours, t, out_dir / f'ours_{t:.2f}.png')
        result = critique(ref, got, shot=shot, out_dir=out_dir, label=f't{t:.2f}')
        rows.append({'t': round(t, 3), 'shot_id': (shot or {}).get('shot_id'), **result})
    return {'moments': rows, 'score': round(sum(r['score'] for r in rows), 2),
            'top': sorted(({'t': r['t'], 'shot_id': r['shot_id'], **d} for r in rows for d in r['differences']), key=lambda d: -d['rank_score'])[:TOP]}


def workbench_critique(project, call, image, frame=None, size=512, out_dir=None, shot=None):
    """reference_critique (workbench): our shot camera at a frame (lit + id passes, free) against a reference frame of
    the same moment; our id classes are regions too."""
    from .common import safe_path
    from .project import project_dir
    ref = safe_path(project_dir(project), image)
    result = call('preview', {'views': ['shot'], 'passes': ['lit', 'id'], 'size': int(size), **({'frame': int(frame)} if frame is not None else {})})
    images = result['images']['shot']
    with Image.open(ref) as photo:
        w, h = photo.size
    cmp_size = (SIZE, round(SIZE * h / w)) if w >= h else (round(SIZE * w / h), SIZE)
    regions = class_regions(images['id'], result['palette'], cmp_size)
    return critique(ref, images['lit'], shot=shot, regions=regions, out_dir=out_dir, label=f"f{frame if frame is not None else 'x'}")


def _times(text):
    return [float(t) for t in text.split(',') if t.strip()]


def register_commands(subparsers):
    parser = subparsers.add_parser('critique', help='Ranked differences from a reference, region by region, with the shot value that would close each')
    commands = parser.add_subparsers(dest='critique_command', required=True)
    p = commands.add_parser('frames', help='one reference frame against one of ours')
    p.add_argument('--reference', required=True, type=Path); p.add_argument('--ours', required=True, type=Path)
    p.add_argument('--project'); p.add_argument('--shot'); p.add_argument('--out', type=Path)
    p.set_defaults(handler=lambda a: _frames(a))
    v = commands.add_parser('video', help='the same moments of a reference video and ours')
    v.add_argument('--reference', required=True, type=Path); v.add_argument('--ours', required=True, type=Path)
    v.add_argument('--times', required=True, help='seconds in our video, comma separated'); v.add_argument('--offset', type=float, default=0.0)
    v.add_argument('--project'); v.add_argument('--out', type=Path, required=True)
    v.set_defaults(handler=lambda a: _video(a))


def _shot(project, shot_id):
    if not (project and shot_id):
        return None
    from .project import load_shot
    return load_shot(project, shot_id)


def _frames(a):
    return critique(a.reference, a.ours, shot=_shot(a.project, a.shot), out_dir=a.out or a.ours.parent / 'critique', label=a.ours.stem)


def _video(a):
    shots = None
    if a.project:
        from .project import load_project, load_shot
        project = load_project(a.project)
        fps = project['output']['fps']
        shots = [(load_shot(a.project, s['shot_id']), s['start_frame'] / fps, (s['start_frame'] + s['frame_count']) / fps) for s in project['shots']]
    return critique_videos(a.reference, a.ours, _times(a.times), a.out, shots=shots, reference_offset_s=a.offset)
