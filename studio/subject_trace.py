"""Read numbers off a registered drawing instead of typing them: datum registration, loft stations, planforms.

Principle: the LLM decides which parts exist and how they are built; the numbers a drawing shows are
measured from its pixels at the drawing's scale (px_per_m) in the model frame (datum). Every traced
value carries its pixel evidence; values another part hides in that view are reported, not guessed.
Results are candidates (subjects/<id>/candidates/trace_<n>.json); --apply merges them and lints.
"""
from __future__ import annotations

import copy
import math

from PIL import Image, ImageChops

from .common import StudioError, read_json, write_json
from .fidelity import IMAGE_DIRS, datum_iou, datum_mask, mask_iou, model_to_px, px_to_model, silhouette_reference
from .subjects import load_spec, resolve_pointer, set_pointer, spec_path

VIEW_AXES = {'side': ('length', 'height'), 'top': ('length', 'width'), 'front': ('width', 'height')}
SECTION_PLANE = {'x': (1, 2, 0), 'y': (0, 2, 1), 'z': (0, 1, 2)}  # loft (u index, v index, axis index)
AXIS = {'x': 0, 'y': 1, 'z': 2}
ORIENTATIONS = [(u, v) for u in IMAGE_DIRS for v in IMAGE_DIRS if IMAGE_DIRS[u][0] * IMAGE_DIRS[v][0] + IMAGE_DIRS[u][1] * IMAGE_DIRS[v][1] == 0]


def view_axes(spec, view):
    axes = {'length': 'y', 'width': 'x', 'height': 'z', **spec.get('axes', {})}
    u, v = VIEW_AXES[view]
    return AXIS[axes[u]], AXIS[axes[v]]


def silhouette_of(spec, view):
    found = [s for s in spec.get('silhouettes', []) if s['view'] == view]
    if not found:
        raise StudioError('FIT_NO_TARGET', f"{spec['subject_id']} has no {view} silhouette")
    if not found[0].get('px_per_m'):
        raise StudioError('FIT_NO_TARGET', f'{view} silhouette needs px_per_m (drawing scale) before numbers can be read from it')
    return found[0]


# ---- registration ----------------------------------------------------------------------------------

def _extreme(mask, direction):
    """Pixel coordinate (along that image axis) of the subject's extreme in an image direction."""
    box = mask.getbbox()
    return {'+x': box[2] - 0.5, '-x': box[0] + 0.5, '+y': box[3] - 0.5, '-y': box[1] + 0.5}[direction]


def register_by_rule(spec, silhouette, geometry, triangles, project):
    """Datum from the silhouette's register rule: part faces <-> drawing extremes, symmetry lines <-> axis 0."""
    rule, ppm = silhouette['register'], silhouette['px_per_m']
    reference = silhouette_reference(silhouette, project)
    box = reference.getbbox()
    if not box:
        raise StudioError('FIT_NO_TARGET', 'reference silhouette is empty')
    iu, iv = view_axes(spec, silhouette['view'])
    datum = {'px': [0.0, 0.0], 'model': [0.0, 0.0]}
    used = set()
    for key, index, default in (('u', iu, '+x'), ('v', iv, '-y')):
        axis_rule = rule[key]
        if axis_rule.get('symmetric'):
            direction = axis_rule.get('dir', '+x' if key == 'u' else '+y')
            lo, hi = (box[0], box[2]) if direction in ('+x', '-x') else (box[1], box[3])
            pixel, model = (lo + hi) / 2, 0.0
        else:
            part, face = axis_rule['anchor'].split('/')
            if 'xyz'.index(face[1]) != index:
                raise StudioError('INPUT_INVALID', f"register {key}: {axis_rule['anchor']} is not a face along the {silhouette['view']} {key} axis")
            bounds = geometry['parts'].get(part, {}).get('bounds')
            if not bounds:
                raise StudioError('INPUT_INVALID', f'register {key}: part {part} was not built')
            model = bounds[face[1]][1 if face[0] == '+' else 0]
            pixel = _extreme(reference, axis_rule['image'])
            # the model face is the +axis extreme exactly when the image extreme is where +axis points
            grows = axis_rule['image'] if face[0] == '+' else {'+x': '-x', '-x': '+x', '+y': '-y', '-y': '+y'}[axis_rule['image']]
            direction = grows
        if direction[1] in used:
            raise StudioError('INPUT_INVALID', 'register rules for u and v must use different image axes')
        used.add(direction[1])
        datum[f'{key}_dir'] = direction
        slot = 0 if direction[1] == 'x' else 1
        datum['px'][slot] = round(pixel, 2)
        datum['model'][0 if key == 'u' else 1] = round(model, 5)
    # px holds (x, y) of the datum: the u rule fixes one image axis, the v rule the other
    iou = datum_iou(triangles, reference, datum, ppm)[0] if triangles else 0.0
    return {'datum': datum, 'iou': round(iou, 4), 'method': 'rule'}


def register(spec, silhouette, triangles, project, search_px=None):
    """Datum that best overlays the current model on the drawing (orientation + translation search).

    The model must be roughly right (proportions, scale); the datum then pins the drawing so trace and
    fit work in the model frame. Returns {'datum', 'iou', 'orientation'}."""
    if not triangles:
        raise StudioError('FIT_NO_TARGET', 'model has no geometry in this view')
    reference = silhouette_reference(silhouette, project)
    box = reference.getbbox()
    if not box:
        raise StudioError('FIT_NO_TARGET', 'reference silhouette is empty')
    ppm = silhouette['px_per_m']
    us = [p[0] for t in triangles for p in t]; vs = [p[1] for t in triangles for p in t]
    model_centre = [(min(us) + max(us)) / 2, (min(vs) + max(vs)) / 2]
    ref_centre = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
    search = search_px or int(0.06 * max(reference.size))
    best = None
    for u_dir, v_dir in ORIENTATIONS:
        base = {'px': ref_centre, 'model': model_centre, 'u_dir': u_dir, 'v_dir': v_dir}
        model = datum_mask(triangles, reference.size, base, ppm)
        def score(dx, dy):
            return mask_iou(ImageChops.offset(model, dx, dy), reference)
        here = (score(0, 0), 0, 0)
        for step in (max(1, search // 6), max(1, search // 24), 1):
            cx, cy = here[1], here[2]
            for dx in range(cx - 3 * step, cx + 3 * step + 1, step):
                for dy in range(cy - 3 * step, cy + 3 * step + 1, step):
                    if abs(dx) <= search and abs(dy) <= search:
                        value = score(dx, dy)
                        if value > here[0]:
                            here = (value, dx, dy)
        if best is None or here[0] > best[0]:
            best = (here[0], {'px': [round(ref_centre[0] + here[1], 2), round(ref_centre[1] + here[2], 2)],
                              'model': [round(c, 5) for c in model_centre], 'u_dir': u_dir, 'v_dir': v_dir})
    return {'datum': best[1], 'iou': round(best[0], 4)}


# ---- scanning --------------------------------------------------------------------------------------

def _run(mask, start, direction, limit):
    """Pixels from `start` along `direction` while white; returns the last white offset (or -1)."""
    px = mask.load()
    x, y = start
    last = -1
    for k in range(limit):
        xi, yi = round(x + direction[0] * k), round(y + direction[1] * k)
        if not (0 <= xi < mask.width and 0 <= yi < mask.height) or px[xi, yi] < 128:
            break
        last = k
    return last


def _white(mask, point):
    x, y = round(point[0]), round(point[1])
    return 0 <= x < mask.width and 0 <= y < mask.height and mask.load()[x, y] >= 128


def _world_offset(spec, part_id):
    """Translation of a part's frame in the subject root (parents included); None if any frame is rotated or scaled."""
    builders = {b['part_id']: b for b in spec['builders']}
    total, cur = [0.0, 0.0, 0.0], part_id
    while cur is not None:
        transform = builders[cur].get('transform', {})
        if any(abs(a) > 1e-9 for a in transform.get('rotation_deg', (0, 0, 0))) or any(abs(s - 1) > 1e-9 for s in transform.get('scale', (1, 1, 1))):
            return None
        total = [t + l for t, l in zip(total, transform.get('location', (0, 0, 0)))]
        cur = builders[cur].get('parent')
    return total


def _blockers(spec, geometry, part_id, silhouette, iu, iv, u_value, v_lo, v_hi, tol):
    """Other parts that, at view coordinate u, reach outside [v_lo, v_hi]: the outline there is not this part's."""
    out = []
    for other, info in geometry['parts'].items():
        if other == part_id or other in silhouette.get('exclude_parts', []) or not info.get('bounds'):
            continue
        bu, bv = info['bounds']['xyz'[iu]], info['bounds']['xyz'[iv]]
        if bu[0] - tol <= u_value <= bu[1] + tol and (bv[0] < v_lo - tol or bv[1] > v_hi + tol):
            out.append(other)
    return out


def trace_loft(spec, part_id, view, geometry, project):
    """Half-widths (and centre offsets) of a loft's stations from one registered view."""
    silhouette = silhouette_of(spec, view)
    datum, ppm = silhouette.get('datum'), silhouette['px_per_m']
    if not datum:
        raise StudioError('FIT_NO_TARGET', f'{view} silhouette has no datum; run subject trace --register first')
    index, builder = next(((i, b) for i, b in enumerate(spec['builders']) if b['part_id'] == part_id), (None, None))
    if builder is None or builder['builder'] != 'loft':
        raise StudioError('INPUT_INVALID', f'{part_id} is not a loft part')
    offset = _world_offset(spec, part_id)
    if offset is None:
        raise StudioError('INPUT_INVALID', f'{part_id} (or a parent) is rotated or scaled; trace reads unrotated parts only')
    iu, iv = view_axes(spec, view)
    su, sv, sa = SECTION_PLANE[builder['params'].get('axis', 'y')]
    if sa != iu or iv not in (su, sv):
        raise StudioError('INPUT_INVALID', f'{part_id}: loft axis must run along the {view} view horizontal axis')
    key, centre_i = ('a', 0) if iv == su else ('b', 1)
    reference = silhouette_reference(silhouette, project)
    tol = 1.5 / ppm
    v_dir = IMAGE_DIRS[datum.get('v_dir', '-y')]
    rows = []
    for k, station in enumerate(builder['params']['stations']):
        section = station['section']
        pointer = f'/builders/{index}/params/stations/{k}/section/{key}'
        row = {'station': k, 's': station['s'], 'pointer': pointer}
        if section.get('type', 'ellipse') == 'points' or section.get(key, 0) == 0:
            rows.append({**row, 'status': 'skipped', 'reason': 'points section or pole'}); continue
        centre = section.get('center', [0, 0])
        u_world = station['s'] + offset[iu]
        v_centre = centre[centre_i] + offset[iv]
        current = section[key]
        blockers = _blockers(spec, geometry, part_id, silhouette, iu, iv, u_world, v_centre - current * 1.02, v_centre + current * 1.02, tol)
        if blockers:
            rows.append({**row, 'status': 'occluded', 'by': blockers, 'current': current}); continue
        start = model_to_px(datum, ppm, u_world, v_centre)
        if not _white(reference, start):
            rows.append({**row, 'status': 'not_found', 'reason': 'centre line is background in the drawing', 'current': current}); continue
        limit = int(max(reference.size))
        plus = _run(reference, start, v_dir, limit)
        minus = _run(reference, start, (-v_dir[0], -v_dir[1]), limit)
        hi = px_to_model(datum, ppm, start[0] + v_dir[0] * (plus + 0.5), start[1] + v_dir[1] * (plus + 0.5))[1]
        lo = px_to_model(datum, ppm, start[0] - v_dir[0] * (minus + 0.5), start[1] - v_dir[1] * (minus + 0.5))[1]
        half, mid = (hi - lo) / 2, (hi + lo) / 2 - offset[iv]
        rows.append({**row, 'status': 'traced', 'current': current, 'traced': round(half, 4),
                     'centre_current': centre[centre_i], 'centre_traced': round(mid, 4),
                     'evidence_px': [[round(start[0] - v_dir[0] * minus, 1), round(start[1] - v_dir[1] * minus, 1)],
                                     [round(start[0] + v_dir[0] * plus, 1), round(start[1] + v_dir[1] * plus, 1)]]})
    patch = {r['pointer']: r['traced'] for r in rows if r['status'] == 'traced' and abs(r['traced'] - r['current']) * ppm > 1.0}
    return {'part_id': part_id, 'view': view, 'kind': 'loft_stations', 'rows': rows, 'patch': patch}


def _fit_line(points):
    n = len(points)
    mx = sum(p[0] for p in points) / n; my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points)
    slope = sum((p[0] - mx) * (p[1] - my) for p in points) / sxx if sxx else 0.0
    return slope, my - slope * mx


def trace_planform(spec, part_id, view, geometry, project, samples=24):
    """Root/tip chord, quarter-chord sweep, span and root position of a wing part from a plan view."""
    silhouette = silhouette_of(spec, view)
    datum, ppm = silhouette.get('datum'), silhouette['px_per_m']
    if not datum:
        raise StudioError('FIT_NO_TARGET', f'{view} silhouette has no datum; run subject trace --register first')
    index, builder = next(((i, b) for i, b in enumerate(spec['builders']) if b['part_id'] == part_id), (None, None))
    if builder is None or builder['builder'] != 'wing':
        raise StudioError('INPUT_INVALID', f'{part_id} is not a wing part')
    params = builder['params']
    offset = _world_offset(spec, part_id)
    iu, iv = view_axes(spec, view)
    if offset is None or AXIS[params.get('span_axis', 'x').lstrip('-')] != iv or AXIS[params.get('chord_axis', 'y').lstrip('-')] != iu:
        raise StudioError('INPUT_INVALID', f'{part_id}: span must run along the {view} view vertical axis and chord along its horizontal axis, unrotated')
    lead = -1.0 if params.get('chord_axis', 'y').startswith('-') else 1.0  # leading edge toward +u (or -u)
    reference = silhouette_reference(silhouette, project)
    u_dir = IMAGE_DIRS[datum.get('u_dir', '+x')]
    bounds = geometry['parts'][part_id]['bounds']
    u_mid = sum(bounds['xyz'[iu]]) / 2
    semi = params['span'] / 2 if params.get('mirror', True) else params['span']
    sides = (1, -1) if params.get('mirror', True) else (1,)
    le, te, tips = [], [], []
    for side in sides:
        for k in range(samples):
            frac = 0.3 + 0.55 * k / (samples - 1)  # inside the fuselage and rounded tip are not planform
            v = offset[iv] + side * frac * semi
            start = model_to_px(datum, ppm, u_mid, v)
            if not _white(reference, start):
                continue
            limit = int(max(reference.size))
            plus = _run(reference, start, u_dir, limit)
            minus = _run(reference, start, (-u_dir[0], -u_dir[1]), limit)
            u_hi = px_to_model(datum, ppm, start[0] + u_dir[0] * (plus + 0.5), start[1] + u_dir[1] * (plus + 0.5))[0]
            u_lo = px_to_model(datum, ppm, start[0] - u_dir[0] * (minus + 0.5), start[1] - u_dir[1] * (minus + 0.5))[0]
            front, back = (u_hi, u_lo) if lead > 0 else (u_lo, u_hi)
            le.append((frac * semi, front)); te.append((frac * semi, back))
        # span: walk outward along v at mid-chord of the outer sample until the drawing ends
        v_dir = IMAGE_DIRS[datum.get('v_dir', '-y')]
        outer_mid = (le[-1][1] + te[-1][1]) / 2 if le else u_mid
        start = model_to_px(datum, ppm, outer_mid, offset[iv] + side * 0.85 * semi)
        if _white(reference, start):
            direction = (v_dir[0] * side, v_dir[1] * side)
            steps = _run(reference, start, direction, int(max(reference.size)))
            end = px_to_model(datum, ppm, start[0] + direction[0] * (steps + 0.5), start[1] + direction[1] * (steps + 0.5))[1]
            tips.append(abs(end - offset[iv]))
    if len(le) < 6:
        raise StudioError('FIT_NO_TARGET', f'{part_id}: the drawing does not show this wing where the model puts it (register again?)')
    le_slope, le0 = _fit_line(le)
    te_slope, te0 = _fit_line(te)
    traced_semi = sum(tips) / len(tips) if tips else semi
    root_chord = abs(le0 - te0)
    tip_chord = abs((le0 + le_slope * traced_semi) - (te0 + te_slope * traced_semi))
    qc0 = le0 - lead * 0.25 * root_chord
    qc_tip = (le0 + le_slope * traced_semi) - lead * 0.25 * tip_chord
    sweep = math.degrees(math.atan2(lead * (qc0 - qc_tip), traced_semi))
    span = traced_semi * (2 if params.get('mirror', True) else 1)
    patch = {f'/builders/{index}/params/span': round(span, 4), f'/builders/{index}/params/root_chord': round(root_chord, 4),
             f'/builders/{index}/params/tip_chord': round(tip_chord, 4), f'/builders/{index}/params/sweep_deg': round(sweep, 3)}
    location = list(builder.get('transform', {}).get('location', [0.0, 0.0, 0.0]))
    location[iu] = round(qc0 - (offset[iu] - location[iu]), 4)
    patch[f'/builders/{index}/transform/location'] = location
    current = {p: resolve_pointer(spec, p) for p in patch}
    return {'part_id': part_id, 'view': view, 'kind': 'planform', 'samples': len(le),
            'lines': {'le': [round(le0, 4), round(le_slope, 5)], 'te': [round(te0, 4), round(te_slope, 5)]},
            'current': current, 'patch': {p: v for p, v in patch.items() if v != current.get(p)}}


# ---- CLI -------------------------------------------------------------------------------------------

def _next_candidate(project, subject_id, prefix):
    folder = spec_path(project, subject_id).parent / 'candidates'
    folder.mkdir(parents=True, exist_ok=True)
    n = 1 + max([int(p.stem.rsplit('_', 1)[1]) for p in folder.glob(f'{prefix}_*.json') if p.stem.rsplit('_', 1)[1].isdigit()] or [0])
    return folder / f'{prefix}_{n:03d}.json'


def apply_patch(project, subject_id, patch):
    """Merge pointer->value changes into the spec, lint, write. Refuses a patch that breaks lint."""
    from .subjects import lint_spec
    spec = load_spec(project, subject_id)
    for pointer, value in patch.items():
        set_pointer(spec, pointer, value)
    lint = lint_spec(spec, project)
    if lint['errors']:
        raise StudioError('SUBJECT_SPEC_INVALID', 'Patched spec fails lint: ' + '; '.join(lint['errors'][:6]))
    write_json(spec_path(project, subject_id), spec)
    return spec


def trace_command(project, subject_id, parts=(), view='top', do_register=False, apply=False):
    from .subject_fit import SubjectSession
    spec = load_spec(project, subject_id)
    silhouette = silhouette_of(spec, view)
    with SubjectSession(project, subject_id) as session:
        geometry = session.geometry(spec, views=[view])
        result = {'subject_id': subject_id, 'view': view, 'traces': []}
        if do_register or not silhouette.get('datum'):
            registered = register_by_rule(spec, silhouette, geometry, geometry['silhouettes'][view], project) if silhouette.get('register') \
                else {**register(spec, silhouette, geometry['silhouettes'][view], project), 'method': 'search'}
            index = spec['silhouettes'].index(silhouette)
            result['register'] = {**registered, 'patch': {f'/silhouettes/{index}/datum': registered['datum']}}
            if not do_register:
                raise StudioError('FIT_NO_TARGET', f'{view} silhouette has no datum; rerun with --register (proposed IoU {registered["iou"]})')
            silhouette = dict(silhouette, datum=registered['datum'])
            spec = {**spec, 'silhouettes': [silhouette if s is spec['silhouettes'][index] else s for s in spec['silhouettes']]}
        kinds = {b['part_id']: b['builder'] for b in spec['builders']}
        unknown = [p for p in parts if kinds.get(p) not in ('loft', 'wing')]
        if unknown:
            raise StudioError('INPUT_INVALID', f'{unknown}: trace reads loft stations and wing planforms only')
        # Planforms first: lofts then know where the (updated) wings and tails hide their outline.
        for kind, fn in (('wing', trace_planform), ('loft', trace_loft)):
            for part in [p for p in parts if kinds[p] == kind]:
                trace = fn(spec, part, view, geometry, project)
                result['traces'].append(trace)
                spec = copy.deepcopy(spec)
                for pointer, value in trace['patch'].items():
                    set_pointer(spec, pointer, value)
            if kind == 'wing' and any(kinds[p] == 'wing' for p in parts):
                geometry = session.geometry(spec, views=[view])
    patch = {**result.get('register', {}).get('patch', {}), **{p: v for t in result['traces'] for p, v in t['patch'].items()}}
    result['patch'] = patch
    path = _next_candidate(project, subject_id, 'trace')
    write_json(path, result)
    if apply and patch:
        apply_patch(project, subject_id, patch)
    return {**result, 'applied': bool(apply and patch), 'candidate_path': str(path), 'artifacts': [str(path)]}
