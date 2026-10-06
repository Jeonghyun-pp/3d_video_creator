"""Host side of subject fidelity: compare measured geometry with the spec; silhouettes by mask IoU.

Scale is judged by dimensions; shape by silhouettes normalised to their bounding box, so a
uniformly mis-scaled model fails on dimensions, and a wrongly proportioned one fails on shape.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from .common import REPO, StudioError, file_hash, read_json, stable_hash, write_json

CANVAS = 512
DEFAULT_MIN_IOU = 0.85
# Assembly claim defaults (m): touching within 1 mm, interference above 0.5 mm, floating above 1 mm.
CONTACT_TOL_M = 0.001
INTERFERENCE_TOL_M = 0.0005
FLOATING_TOL_M = 0.001
CODE_FILES = ('studio/fidelity.py', 'studio/blender_ops/fidelity.py', 'studio/blender_ops/geom_checks.py')


def spec_sha256(spec):
    return stable_hash(spec)


def code_sha256():
    return stable_hash({name: file_hash(REPO / name) for name in CODE_FILES})


def _mm(value):
    return 'n/a' if value is None else f'{value * 1000:.1f} mm'


def judge_claim(claim, measured):
    """(passed, measured summary, expected text, note) for one assembly claim; distances reported in mm."""
    kind = claim['type']
    if measured.get('missing'):
        return False, {'missing_parts': measured['missing']}, kind, 'claim names parts that were not built'
    if kind == 'no_floating':
        tol = claim.get('tol_m', FLOATING_TOL_M)
        floating = {p: r for p, r in measured['parts'].items() if r['gap_m'] is None or r['gap_m'] > tol}
        note = '; '.join(f"{p} {_mm(r['gap_m'])} from {r['nearest']}" for p, r in sorted(floating.items()))
        return not floating, {p: r['gap_m'] for p, r in measured['parts'].items()}, f'every part within {_mm(tol)} of another', note
    distance, pen = measured.get('distance_m'), measured.get('penetration_m')
    if kind == 'contact':
        tol = claim.get('tol_m', CONTACT_TOL_M)
        ok = distance is not None and distance <= tol and (pen is None or pen <= max(tol, INTERFERENCE_TOL_M))
        return ok, {'gap': _mm(distance), 'penetration': _mm(pen)}, f'touching (gap <= {_mm(tol)}, no interference)', \
            '' if ok else f"{claim['a']} -> {claim['b']}: gap {_mm(distance)}, penetration {_mm(pen)}"
    if kind == 'no_interference':
        tol = claim.get('tol_m', INTERFERENCE_TOL_M)
        ok = pen is not None and pen <= tol
        return ok, {'penetration': _mm(pen)}, f'penetration <= {_mm(tol)}', \
            '' if ok else (f"{claim['a']} and {claim['b']} overlap by {_mm(pen)}" if pen is not None else 'open mesh: interference cannot be verified')
    if kind == 'clearance':
        need = claim['value_m']
        ok = distance is not None and distance >= need
        return ok, {'gap': _mm(distance)}, f'gap >= {_mm(need)}', '' if ok else f"{claim['a']} -> {claim['b']}: gap {_mm(distance)} < {_mm(need)}"
    if kind == 'through':
        tol = claim.get('tol_m', INTERFERENCE_TOL_M)
        protrude = measured['protrude_m']
        ok = min(protrude) >= -1e-6 and measured['centred_in_b'] and pen is not None and pen <= tol
        why = []
        if min(protrude) < -1e-6:
            why.append(f"does not pass fully through (protrusion {_mm(protrude[0])} / {_mm(protrude[1])})")
        if not measured['centred_in_b']:
            why.append('axis outside the part it should pass through')
        if pen is None or pen > tol:
            why.append(f'interferes with the hole by {_mm(pen)}')
        return ok, {'protrusion': [_mm(p) for p in protrude], 'penetration': _mm(pen)}, 'passes through without interference', \
            f"{claim['a']} through {claim['b']}: " + ', '.join(why) if why else ''
    if kind == 'cover':
        need = claim['value_m']
        ok = bool(measured.get('inside')) and measured.get('cover_m') is not None and measured['cover_m'] >= need - 1e-6
        return ok, {'inside': measured.get('inside'), 'cover': _mm(measured.get('cover_m'))}, f'inside with cover >= {_mm(need)}', \
            '' if ok else f"{claim['a']} in {claim['b']}: cover {_mm(measured.get('cover_m'))} < {_mm(need)}" + ('' if measured.get('inside') else ' (not inside)')
    raise StudioError('INPUT_INVALID', f'unknown assembly claim type {kind}')


def _fit(mask):
    box = mask.getbbox()
    if not box:
        return None
    return mask.crop(box)


def _canvas(mask, width=CANVAS):
    """Scale a cropped mask to the canvas width (aspect kept) and centre it vertically in a square canvas."""
    w, h = mask.size
    height = max(1, round(h * width / w))
    scaled = mask.resize((width, min(height, width * 2)), Image.NEAREST)
    canvas = Image.new('L', (width, max(width, scaled.height)), 0)
    canvas.paste(scaled, (0, (canvas.height - scaled.height) // 2))
    return canvas


def largest_component(mask):
    """Keep the largest white blob: stray lines from neighbouring views are not the subject."""
    work = mask.point(lambda v: 255 if v > 127 else 0)
    label, sizes = 1, {}
    pixels = work.load()
    for y in range(0, work.height, 3):
        for x in range(0, work.width, 3):
            if pixels[x, y] == 255 and label < 255:
                ImageDraw.floodfill(work, (x, y), label)
                sizes[label] = work.histogram()[label]
                label += 1
    if not sizes:
        return mask
    keep = max(sizes, key=sizes.get)
    return work.point(lambda v: 255 if v == keep else 0)


def model_mask(triangles):
    if not triangles:
        return None
    us = [p[0] for t in triangles for p in t]; vs = [p[1] for t in triangles for p in t]
    u0, v0 = min(us), min(vs)
    span = max(max(us) - u0, max(vs) - v0) or 1.0
    scale = (2 * CANVAS - 1) / span
    image = Image.new('L', (2 * CANVAS, 2 * CANVAS), 0)
    draw = ImageDraw.Draw(image)
    for t in triangles:
        draw.polygon([((x - u0) * scale, (2 * CANVAS - 1) - (y - v0) * scale) for x, y in t], fill=255)
    return _fit(image)


def reference_mask(path, crop_px=None, invert=False, outline=False, erase_px=()):
    return _fit(reference_full(path, crop_px, invert, outline, erase_px))


def reference_full(path, crop_px=None, invert=False, outline=False, erase_px=()):
    """Subject mask (255) in the cropped drawing's own pixel frame (not cropped to its bbox)."""
    image = ImageOps.grayscale(Image.open(path).convert('RGB'))
    if crop_px:
        image = image.crop(tuple(crop_px))
    if erase_px:
        draw = ImageDraw.Draw(image)
        for box in erase_px:
            draw.rectangle(tuple(box), fill=255)
    if outline:
        # Line drawing: lines dark; everything not reachable from the border without crossing a line is the subject.
        lines = image.point(lambda v: 0 if v < 160 else 255).filter(ImageFilter.MinFilter(3))
        padded = Image.new('L', (lines.width + 2, lines.height + 2), 255)
        padded.paste(lines, (1, 1))
        ImageDraw.floodfill(padded, (0, 0), 128)
        filled = padded.crop((1, 1, lines.width + 1, lines.height + 1)).point(lambda v: 0 if v == 128 else 255)
        return largest_component(filled)
    histogram = image.histogram()
    total = sum(histogram); cumulative = 0; threshold = 128
    weighted = sum(i * c for i, c in enumerate(histogram)); best = -1; sum_b = 0
    for t, count in enumerate(histogram):  # Otsu threshold
        cumulative += count; sum_b += t * count
        if cumulative == 0 or cumulative == total:
            continue
        mean_b, mean_f = sum_b / cumulative, (weighted - sum_b) / (total - cumulative)
        between = cumulative * (total - cumulative) * (mean_b - mean_f) ** 2
        if between > best:
            best, threshold = between, t
    dark_subject = image.point(lambda v: 255 if v <= threshold else 0)
    return ImageChops.invert(dark_subject) if invert else dark_subject


# A datum pins one drawing pixel to one model point and says which image direction each model view
# axis grows in; with px_per_m the drawing is then registered exactly - no alignment search, and traced
# numbers come out in the model's own frame.
IMAGE_DIRS = {'+x': (1, 0), '-x': (-1, 0), '+y': (0, 1), '-y': (0, -1)}


def silhouette_reference(silhouette, project):
    return reference_full(Path(project) / silhouette['image'], silhouette.get('crop_px'), silhouette.get('invert', False),
                          silhouette.get('outline', False), silhouette.get('erase_px', ()))


def model_to_px(datum, px_per_m, u, v):
    (du_x, du_y), (dv_x, dv_y) = IMAGE_DIRS[datum.get('u_dir', '+x')], IMAGE_DIRS[datum.get('v_dir', '-y')]
    du, dv = (u - datum['model'][0]) * px_per_m, (v - datum['model'][1]) * px_per_m
    return datum['px'][0] + du * du_x + dv * dv_x, datum['px'][1] + du * du_y + dv * dv_y


def px_to_model(datum, px_per_m, x, y):
    (du_x, du_y), (dv_x, dv_y) = IMAGE_DIRS[datum.get('u_dir', '+x')], IMAGE_DIRS[datum.get('v_dir', '-y')]
    dx, dy = x - datum['px'][0], y - datum['px'][1]
    return datum['model'][0] + (dx * du_x + dy * du_y) / px_per_m, datum['model'][1] + (dx * dv_x + dy * dv_y) / px_per_m


def datum_mask(triangles, size, datum, px_per_m):
    image = Image.new('L', size, 0)
    draw = ImageDraw.Draw(image)
    for t in triangles:
        draw.polygon([model_to_px(datum, px_per_m, u, v) for u, v in t], fill=255)
    return image


def datum_iou(triangles, reference, datum, px_per_m):
    """IoU of the datum-placed model and the reference on a canvas covering both, so geometry the model
    has beyond the drawing's frame counts against it (clipping it would inflate the score).
    Returns (iou, model_canvas, reference_canvas)."""
    corners = [model_to_px(datum, px_per_m, u, v) for t in triangles for u, v in t] or [(0, 0)]
    left = math.floor(min(0, min(x for x, _ in corners))); top = math.floor(min(0, min(y for _, y in corners)))
    right = math.ceil(max(reference.width, max(x for x, _ in corners) + 1)); bottom = math.ceil(max(reference.height, max(y for _, y in corners) + 1))
    size = (right - left, bottom - top)
    shifted = {**datum, 'px': [datum['px'][0] - left, datum['px'][1] - top]}
    model = datum_mask(triangles, size, shifted, px_per_m)
    ref = Image.new('L', size, 0); ref.paste(reference, (-left, -top))
    return mask_iou(model, ref), model, ref


def mask_iou(a, b):
    inter = ImageChops.multiply(a, b).histogram()[255]
    union = ImageChops.lighter(a, b).histogram()[255]
    return inter / union if union else 0.0


def dimension_values(spec, geometry):
    """{dimension id: measured metres} exactly as the dimension checks read them."""
    values = {}
    for dim in spec['dimensions']:
        axis = dim.get('measure', 'length')
        if dim.get('part_ids'):
            found = [geometry['parts'].get(p, {}).get(axis) for p in dim['part_ids']]
            found = [v for v in found if v is not None]
            values[dim['id']] = max(found) if found else None
        else:
            values[dim['id']] = geometry['whole'].get(axis)
    return values


def iou(a, b):
    if a is None or b is None:
        return 0.0
    ca, cb = _canvas(a), _canvas(b)
    h = max(ca.height, cb.height)
    pa, pb = Image.new('L', (CANVAS, h), 0), Image.new('L', (CANVAS, h), 0)
    pa.paste(ca, (0, (h - ca.height) // 2)); pb.paste(cb, (0, (h - cb.height) // 2))
    inter = ImageChops.multiply(pa, pb).histogram()[255]
    union = ImageChops.lighter(pa, pb).histogram()[255]
    return inter / union if union else 0.0


def model_mask_scaled(triangles, px_per_m):
    """Model silhouette at the drawing's scale (pixels per metre), cropped to its bbox."""
    if not triangles:
        return None
    us = [p[0] for t in triangles for p in t]; vs = [p[1] for t in triangles for p in t]
    u0, v1 = min(us), max(vs)
    w = int((max(us) - u0) * px_per_m) + 3; h = int((v1 - min(vs)) * px_per_m) + 3
    image = Image.new('L', (w, h), 0)
    draw = ImageDraw.Draw(image)
    for t in triangles:
        draw.polygon([((x - u0) * px_per_m + 1, (v1 - y) * px_per_m + 1) for x, y in t], fill=255)
    return _fit(image)


def aligned_iou(model, reference, search=0.12):
    """IoU at true scale, maximised over translations up to `search` x the larger size (coarse then fine)."""
    if model is None or reference is None:
        return 0.0
    W = max(model.width, reference.width); H = max(model.height, reference.height)
    pad = int(search * max(W, H))
    size = (W + 2 * pad, H + 2 * pad)
    base = Image.new('L', size, 0); base.paste(reference, (pad + (W - reference.width) // 2, pad + (H - reference.height) // 2))
    ref_count = base.histogram()[255]
    def score(dx, dy):
        layer = Image.new('L', size, 0); layer.paste(model, (pad + (W - model.width) // 2 + dx, pad + (H - model.height) // 2 + dy))
        inter = ImageChops.multiply(base, layer).histogram()[255]
        return inter / (ref_count + layer.histogram()[255] - inter)
    best = (score(0, 0), 0, 0)
    for step in (max(1, pad // 6), max(1, pad // 24), 1):
        cx, cy = best[1], best[2]
        for dx in range(cx - 3 * step, cx + 3 * step + 1, step):
            for dy in range(cy - 3 * step, cy + 3 * step + 1, step):
                if abs(dx) <= pad and abs(dy) <= pad:
                    value = score(dx, dy)
                    if value > best[0]:
                        best = (value, dx, dy)
    return best[0]


def best_orientation_iou(model, reference):
    """Drawings may face either way; orientation is not identity."""
    if model is None or reference is None:
        return 0.0, None
    options = {'as_is': reference, 'mirror_h': ImageOps.mirror(reference), 'mirror_v': ImageOps.flip(reference),
               'rotate_180': reference.rotate(180, expand=True)}
    scores = {name: iou(model, ref) for name, ref in options.items()}
    name = max(scores, key=scores.get)
    return round(scores[name], 4), name


def deviation_target(spec, kind, item_id):
    """The declared deviation for one check, or None (see subject.schema deviations)."""
    return next((d for d in spec.get('deviations', []) if d['check'] == f'{kind}:{item_id}'), None)


def build_report(spec, geometry, project, out_dir=None):
    checks, failures, applied = [], [], []
    def check(kind, item_id, passed, measured, expected, note='', original=None, **extra):
        """original = {'passed', 'expected'} as judged without a deviation (factor / min_iou deviations)."""
        row = {'kind': kind, 'id': item_id, 'passed': passed, 'measured': measured, 'expected': expected, **extra}
        deviation = deviation_target(spec, kind, item_id)
        if deviation:
            if deviation.get('waive'):
                original = {'passed': passed, 'expected': expected}
                passed = True
                row.update({'passed': True, 'expected': 'waived'})
            original = original or {'passed': passed, 'expected': expected}
            row['deviation'] = {'id': deviation['id'], 'reason': deviation['reason'], 'original_expected': original['expected'],
                                'original_passed': original['passed']}
            applied.append({'id': deviation['id'], 'check': deviation['check'], 'reason': deviation['reason'],
                            'needed': original['passed'] is False, 'passed': passed})
        if note:
            row['note'] = note
        checks.append(row)
        if passed is False:
            failures.append(f'{kind} {item_id}: ' + (note if kind == 'assembly' and note else f'measured {measured}, expected {expected}'))
    def factor(kind, item_id):
        deviation = deviation_target(spec, kind, item_id)
        return deviation.get('factor', 1.0) if deviation else 1.0
    measured_dims = dimension_values(spec, geometry)
    for dim in spec['dimensions']:
        value = measured_dims[dim['id']]
        within = lambda target: value is not None and abs(value - target) <= target * dim['tol_pct'] / 100  # noqa: E731
        target = dim['value_m'] * factor('dimension', dim['id'])
        check('dimension', dim['id'], within(target), value, f"{round(target, 6)} m ±{dim['tol_pct']}%", 'assumed' if dim['source_id'] == 'assumed' else '',
              original={'passed': within(dim['value_m']), 'expected': f"{dim['value_m']} m ±{dim['tol_pct']}%"})
    for prop in spec.get('proportions', []):
        a, b = measured_dims.get(prop['numerator']), measured_dims.get(prop['denominator'])
        value = round(a / b, 4) if a and b else None
        within = lambda target: value is not None and abs(value - target) <= target * prop['tol_pct'] / 100  # noqa: E731
        target = prop['value'] * factor('proportion', prop['id'])
        check('proportion', prop['id'], within(target), value, f"{round(target, 4)} ±{prop['tol_pct']}%",
              original={'passed': within(prop['value']), 'expected': f"{prop['value']} ±{prop['tol_pct']}%"})
    for feature in spec['features']:
        present = [p for p in feature['part_ids'] if geometry['parts'].get(p, {}).get('objects')]
        tagged = any(feature['id'] in geometry['parts'].get(p, {}).get('features', []) for p in feature['part_ids'])
        screen = geometry.get('screen_px', {}).get(feature['id'])
        detail_required = feature.get('min_screen_px') is None or screen is None or screen >= feature['min_screen_px']
        extra = {'detail_required': detail_required}
        if feature['verify'] == 'visual':
            extra['needs_review'] = True  # geometry presence only; a reviewer must confirm it in feature_checks
        if feature['verify'] in ('presence', 'dimension', 'silhouette', 'visual', 'assembly'):
            check('feature', feature['id'], bool(present) and tagged, {'parts_present': present, 'tagged': tagged, 'max_screen_px': screen},
                  'all parts built and tagged', '' if detail_required else 'below min_screen_px in this shot: simplification allowed', **extra)
        elif feature['verify'] == 'count':
            count = sum(geometry['parts'].get(p, {}).get('copies', geometry['parts'].get(p, {}).get('objects', 0)) for p in feature['part_ids'])
            check('feature', feature['id'], count == feature['count'], count, feature['count'], **extra)
    measured_claims = {m['index']: m for m in geometry.get('assembly', [])}
    for i, claim in enumerate(spec.get('assembly_claims', [])):
        label = claim.get('id') or f"{claim['type']}:{claim.get('a', '*')}" + (f"->{claim['b']}" if claim.get('b') else '')
        if i not in measured_claims:
            check('assembly', label, False, None, claim['type'], 'claim was not measured; rebuild with the current spec')
            continue
        passed, value, expected, note = judge_claim(claim, measured_claims[i])
        check('assembly', label, passed, value, expected, note)
    out_dir = Path(out_dir) if out_dir else None
    for silhouette in spec.get('silhouettes', []):
        triangles = geometry['silhouettes'].get(silhouette['view'], [])
        if silhouette.get('datum'):
            full = silhouette_reference(silhouette, project)
            score, model_full, full = datum_iou(triangles, full, silhouette['datum'], silhouette['px_per_m'])
            score = round(score, 4)
            base = silhouette.get('min_iou', DEFAULT_MIN_IOU)
            threshold = (deviation_target(spec, 'silhouette', silhouette['view']) or {}).get('min_iou', base)
            check('silhouette', silhouette['view'], score >= threshold, score, f'IoU >= {threshold}', 'registered by datum',
                  original={'passed': score >= base, 'expected': f'IoU >= {base}'})
            if out_dir:
                Image.merge('RGB', (full, model_full, Image.new('L', full.size, 0))).save(out_dir / f"silhouette_{silhouette['view']}.png")
            continue
        reference = reference_mask(Path(project) / silhouette['image'], silhouette.get('crop_px'), silhouette.get('invert', False),
                                   silhouette.get('outline', False), silhouette.get('erase_px', ()))
        if silhouette.get('px_per_m'):
            model = model_mask_scaled(triangles, silhouette['px_per_m'])
            options = {'as_is': reference, 'mirror_h': ImageOps.mirror(reference), 'mirror_v': ImageOps.flip(reference),
                       'rotate_180': reference.rotate(180, expand=True)} if reference is not None else {}
            scores = {name: aligned_iou(model, ref) for name, ref in options.items()}
            orientation = max(scores, key=scores.get) if scores else None
            score = round(scores[orientation], 4) if scores else 0.0
        else:
            model = model_mask(triangles)
            score, orientation = best_orientation_iou(model, reference)
        base = silhouette.get('min_iou', DEFAULT_MIN_IOU)
        threshold = (deviation_target(spec, 'silhouette', silhouette['view']) or {}).get('min_iou', base)
        check('silhouette', silhouette['view'], score >= threshold, score, f'IoU >= {threshold}', f'orientation {orientation}',
              original={'passed': score >= base, 'expected': f'IoU >= {base}'})
        if out_dir and model is not None and reference is not None:
            ref = {'as_is': reference, 'mirror_h': ImageOps.mirror(reference), 'mirror_v': ImageOps.flip(reference),
                   'rotate_180': reference.rotate(180, expand=True)}[orientation]
            a, b = _canvas(model), _canvas(ref)
            h = max(a.height, b.height)
            pa, pb = Image.new('L', (CANVAS, h), 0), Image.new('L', (CANVAS, h), 0)
            pa.paste(a, (0, (h - a.height) // 2)); pb.paste(b, (0, (h - b.height) // 2))
            # red = reference only, green = model only, yellow = both
            Image.merge('RGB', (pb, pa, Image.new('L', (CANVAS, h), 0))).save(out_dir / f"silhouette_{silhouette['view']}.png")
    ious = [c['measured'] for c in checks if c['kind'] == 'silhouette']
    return {'schema_version': 1, 'subject_id': spec['subject_id'], 'identity': spec['identity'], 'passed': not failures,
            'failures': failures, 'checks': checks,
            'summary': {'failures_n': len(failures), 'mean_silhouette_iou': round(sum(ious) / len(ious), 4) if ious else None,
                        'needs_review': [c['id'] for c in checks if c.get('needs_review')], 'stylized': bool(applied)},
            'deviations_applied': applied,
            # declared but not needed: the check passes without it, so the declaration only hides future drift
            'unused_deviations': [a['id'] for a in applied if not a['needed']],
            'assumed': [d['id'] for d in spec['dimensions'] if d['source_id'] == 'assumed'],
            'spec_sha256': spec_sha256(spec), 'code_sha256': code_sha256()}


def require_fidelity(project, shot, version=None, purpose='render'):
    """Gate for look/final renders and paid generation: every subject in the shot passed fidelity."""
    if not shot.get('subjects'):
        return None
    version = version or shot.get('scene_version')
    report_path = Path(project) / 'shots' / shot['shot_id'] / 'versions' / str(version) / 'fidelity_report.json'
    if not report_path.is_file():
        raise StudioError('FIDELITY_FAILED', f"{shot['shot_id']} {version}: no fidelity report; rebuild the shot with its subject specs")
    report = read_json(report_path)
    from .subjects import spec_path
    stale = []
    for r in report['subjects']:
        current = spec_path(project, r['subject_id'])
        if 'spec_sha256' not in r or not current.is_file() or spec_sha256(read_json(current)) != r['spec_sha256']:
            stale.append(r['subject_id'])
    if stale:
        raise StudioError('FIDELITY_STALE', f"{purpose} blocked: subject spec changed after {version} was measured: {stale}",
                          recovery='Rebuild the shot (shot build --base) so fidelity is measured against the current spec')
    failed = [r for r in report['subjects'] if not r['passed']]
    if failed:
        raise StudioError('FIDELITY_FAILED', f"{purpose} blocked: " + '; '.join(f for r in failed for f in r['failures'][:4]),
                          recovery='Fix the geometry (stations, planform, parts) per references/subject_fidelity.md; never loosen the spec to pass')
    return report
