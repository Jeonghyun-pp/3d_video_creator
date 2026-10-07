"""Match a reference photo: solve its camera from point correspondences, and compare a render with the photo.

The camera is an orbit (blender_ops/view_match_core.py) so a solved view goes straight into the workbench
(view_render) and a shot's camera grammar. solve_pose fits it to 6-20 points the agent marks on the photo against
anchors of the built subject; compare measures silhouette and edge agreement between the photo and a render at that
view. Reference images stay local (licence local_only): nothing here sends them anywhere.
"""
from __future__ import annotations

import math
from pathlib import Path

from .blender_ops.view_match_core import full, pose_delta, project_points, residual_px
from .subject_fit import nelder_mead

MIN_POINTS = 6
BOUNDS = {   # the search box of solve_pose, in the order of the unit vector
    'azimuth_deg': (-180.0, 180.0), 'elevation_deg': (-85.0, 85.0), 'log_distance': (math.log(0.3), math.log(40.0)),   # x subject size
    'roll_deg': (-45.0, 45.0), 'log_lens': (math.log(8.0), math.log(400.0)),
    'target_x': (-0.5, 0.5), 'target_y': (-0.5, 0.5), 'target_z': (-0.5, 0.5)}   # x subject size, about the points' centre
START_AZIMUTHS = 12
START_ELEVATIONS = (-30.0, 0.0, 20.0, 45.0, 70.0)


def _check(points3d, points2d):
    if len(points3d) != len(points2d):
        raise ValueError(f'{len(points3d)} 3D points but {len(points2d)} image points')
    if len(points3d) < MIN_POINTS:
        raise ValueError(f'solve_pose needs >= {MIN_POINTS} correspondences (got {len(points3d)})')
    if not all(math.isfinite(v) for p in [*points3d, *points2d] for v in p):
        raise ValueError('non-finite coordinate')
    c = [sum(p[i] for p in points3d) / len(points3d) for i in range(3)]
    far = max(points3d, key=lambda p: math.dist(p, c))
    axis = [f - ci for f, ci in zip(far, c)]
    norm = math.hypot(*axis)
    if norm < 1e-9:
        raise ValueError('the 3D points coincide')
    off_line = max(math.hypot(*[(p[i] - c[i]) - axis[i] / norm * sum((p[j] - c[j]) * axis[j] / norm for j in range(3)) for i in range(3)])
                   for p in points3d)
    if off_line < 1e-3 * norm:
        raise ValueError('the 3D points lie on one line: the view cannot be solved')
    spread = max(max(p[k] for p in points2d) - min(p[k] for p in points2d) for k in range(2))
    if spread < 0.02:
        raise ValueError('the image points are bunched together (< 2 % of the image): mark points across the subject')
    return c, max(max(p[i] for p in points3d) - min(p[i] for p in points3d) for i in range(3))


def _camera(x, centre, size, base):
    v = {k: lo + (hi - lo) * t for (k, (lo, hi)), t in zip(BOUNDS.items(), x)}
    return {**base, 'target': [centre[0] + v['target_x'] * size, centre[1] + v['target_y'] * size, centre[2] + v['target_z'] * size],
            'distance_m': size * math.exp(v['log_distance']), 'azimuth_deg': v['azimuth_deg'], 'elevation_deg': v['elevation_deg'],
            'roll_deg': v['roll_deg'], 'lens_mm': math.exp(v['log_lens'])}


def _unit(value, key):
    lo, hi = BOUNDS[key]
    return min(1.0, max(0.0, (value - lo) / (hi - lo)))


def solve_pose(points3d, points2d, width, height, sensor_mm=36.0, sensor_fit='AUTO', initial=None, max_evals=20000):
    """The orbit camera whose projection of ``points3d`` best matches ``points2d`` (normalised, top-left).

    Starts from an orbit grid (or ``initial``), keeps the best starts and refines them with Nelder-Mead in a bounded
    box (subject_fit.nelder_mead). Returns {camera, residual_px, evals}. Refuses fewer than six points, collinear 3D
    points and image points bunched in one spot - those have no single answer.
    """
    centre, size = _check(points3d, points2d)
    base = {'sensor_mm': sensor_mm, 'sensor_fit': sensor_fit, 'width': width, 'height': height}
    spread = max(max(p[k] for p in points2d) - min(p[k] for p in points2d) for k in range(2))
    cost = lambda x: residual_px(_camera(x, centre, size, base), points3d, points2d)  # noqa: E731
    starts = []
    if initial:
        rel = [(t - c) / size for t, c in zip(initial['target'], centre)]
        starts.append([_unit(initial['azimuth_deg'], 'azimuth_deg'), _unit(initial['elevation_deg'], 'elevation_deg'),
                       _unit(math.log(initial['distance_m'] / size), 'log_distance'), _unit(initial.get('roll_deg', 0.0), 'roll_deg'),
                       _unit(math.log(initial['lens_mm']), 'log_lens'), *(_unit(r, f'target_{a}') for r, a in zip(rel, 'xyz'))])
    lens = 50.0
    distance = size / spread * lens / sensor_mm   # the subject fills `spread` of the frame at this distance
    for i in range(START_AZIMUTHS):
        for el in START_ELEVATIONS:
            starts.append([_unit(-180.0 + 360.0 * (i + 0.5) / START_AZIMUTHS, 'azimuth_deg'), _unit(el, 'elevation_deg'),
                           _unit(math.log(distance / size), 'log_distance'), 0.5, _unit(math.log(lens), 'log_lens'), 0.5, 0.5, 0.5])
    ranked = sorted(starts, key=cost)[:6]
    evals = len(starts)
    best_x, best_f = None, math.inf
    per_start = max(500, (max_evals - evals) // (len(ranked) * 3))
    for x0 in ranked:
        x, f = x0, cost(x0)
        for step in (0.1, 0.03, 0.01):   # restarts with a smaller simplex: Nelder-Mead stalls in long valleys
            x, f, n = nelder_mead(cost, x, per_start, step=step, tol=1e-10)
            evals += n
        if f < best_f:
            best_x, best_f = x, f
    camera = _camera(best_x, centre, size, base)
    return {'camera': camera, 'residual_px': best_f, 'evals': evals}



# ---- reference views: a photo, its licence, its mask settings and the points marked on it ----------------------------

LICENCES = ('local_only', 'cleared')   # local_only: compared and measured here, never sent to a generation model
VIEW_KEYS = ('id', 'image', 'licence', 'subject_id', 'mask', 'points', 'parts', 'camera', 'residual_px', 'iou')
MASK_KEYS = ('invert', 'outline', 'erase_px', 'image')
POINT_TOLERANCE_PX = 2.0   # default: how far (px RMS) silhouette refinement may move hand-marked points
REFINE_BOX = {'azimuth_deg': 8.0, 'elevation_deg': 8.0, 'log_distance': 0.15, 'roll_deg': 4.0, 'log_lens': 0.15}


def view_path(project, view):
    from .common import StudioError, check_id, safe_path
    from .project import project_dir
    key, _, view_id = str(view).partition('/')
    if not view_id:
        raise StudioError('INPUT_INVALID', f'a reference view is named <reference key>/<view id> (got {view!r})')
    return safe_path(project_dir(project), f'references/{check_id(key)}/views/{check_id(view_id)}.json')


def check_view(project, record):
    """Problems of a view record (empty = usable): known keys, the image inside the project, a licence, points shape."""
    from .common import safe_path
    from .project import project_dir
    problems = [f'unknown key {k!r} (reads {list(VIEW_KEYS)})' for k in record if k not in VIEW_KEYS]
    problems += [f'mask: unknown key {k!r} (reads {list(MASK_KEYS)})' for k in (record.get('mask') or {}) if k not in MASK_KEYS]
    if record.get('licence') not in LICENCES:
        problems.append(f"licence must be one of {list(LICENCES)} (a photo of unknown origin is 'local_only')")
    try:
        if not safe_path(project_dir(project), record.get('image', '')).is_file():
            problems.append(f"image {record.get('image')!r} is not a file in the project")
    except Exception as exc:   # noqa: BLE001 - the path escapes the project
        problems.append(f'image: {exc}')
    for i, p in enumerate(record.get('points') or []):
        if not isinstance(p, dict) or len(p.get('px') or []) != 2 or (('anchor' in p) == ('xyz' in p)):
            problems.append(f'points[{i}] needs px [x, y] and exactly one of anchor (name from the anchors tool) or xyz')
    for part, box in (record.get('parts') or {}).items():
        if len(box) != 4 or box[0] >= box[2] or box[1] >= box[3]:
            problems.append(f'parts.{part}: a box is [x0, y0, x1, y1] in image pixels')
    return problems


def add_view(project, view, image, licence, subject_id, points=(), parts=None, mask=None):
    """Write (or replace) a reference view record; keeps a solved camera only if the points are unchanged."""
    from .common import StudioError, read_json, write_json
    path = view_path(project, view)
    old = read_json(path) if path.is_file() else {}
    record = {'id': view.partition('/')[2], 'image': image, 'licence': licence, 'subject_id': subject_id,
              'mask': mask or {}, 'points': list(points), 'parts': parts or {}}
    if old.get('points') == record['points'] and old.get('camera'):
        record.update({k: old[k] for k in ('camera', 'residual_px') if k in old})
    problems = check_view(project, record)
    if problems:
        raise StudioError('INPUT_INVALID', '; '.join(problems))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, record)
    return {'view': view, 'path': str(path), 'record': record}


def _photo(project, record):
    from PIL import Image
    from .common import safe_path
    from .project import project_dir
    return Image.open(safe_path(project_dir(project), record['image'])).convert('RGB')


def photo_mask(project, record):
    """The subject's silhouette in the photo (255): a given mask image, else fidelity.reference_full's threshold."""
    from PIL import Image
    from .common import safe_path
    from .fidelity import reference_full
    from .project import project_dir
    mask = record.get('mask') or {}
    if mask.get('image'):
        return Image.open(safe_path(project_dir(project), mask['image'])).convert('L').point(lambda v: 255 if v >= 128 else 0)
    return reference_full(safe_path(project_dir(project), record['image']), None, mask.get('invert', False), mask.get('outline', False),
                          mask.get('erase_px', ()))


def _render_mask(id_png, size):
    from PIL import Image
    alpha = Image.open(id_png).convert('RGBA').getchannel('A').resize(size, Image.NEAREST)
    return alpha.point(lambda v: 255 if v > 250 else 0)


def _bbox(mask):
    return mask.getbbox()


def _box_iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def compare_images(project, record, id_png, look_png, palette, out_dir):
    """Photo against a render at the photo's camera: silhouette IoU (raw, and best over small shifts), size ratio of the
    silhouettes, edge agreement (structure), per-part box IoU and centre error for parts marked on the photo, and a
    sheet (photo | render | overlay: photo silhouette green, render silhouette red)."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    from .fidelity import aligned_iou, mask_iou
    from .qa_generative import edge_map, edge_overlap
    photo = _photo(project, record)
    size = photo.size
    ref, got = photo_mask(project, record), _render_mask(id_png, size)
    rb, gb = _bbox(ref), _bbox(got)
    extent = ([round((gb[2] - gb[0]) / (rb[2] - rb[0]), 4), round((gb[3] - gb[1]) / (rb[3] - rb[1]), 4)] if rb and gb else None)
    look = Image.open(look_png).convert('RGB').resize(size, Image.BILINEAR)
    edges = edge_overlap(ImageChops.multiply(edge_map(photo), ref.filter(ImageFilter.MaxFilter(5))),
                         ImageChops.multiply(edge_map(look), got.filter(ImageFilter.MaxFilter(5))))
    parts = {}
    if record.get('parts'):
        ids = Image.open(id_png).convert('RGBA').resize(size, Image.NEAREST)
        for part, box in record['parts'].items():
            colours = [c for c, key in palette.items() if key.split('/', 1)[-1] == part]
            if not colours:
                parts[part] = {'problem': 'not in the render (part id unknown or hidden)'}
                continue
            rgb = tuple(int(colours[0][i:i + 2], 16) for i in (1, 3, 5))
            r, g, b, a = ids.split()
            hit = ImageChops.multiply(ImageChops.multiply(r.point(lambda v: 255 if v == rgb[0] else 0), g.point(lambda v: 255 if v == rgb[1] else 0)),
                                      ImageChops.multiply(b.point(lambda v: 255 if v == rgb[2] else 0), a.point(lambda v: 255 if v > 250 else 0)))
            pb = hit.getbbox()
            if pb is None:
                parts[part] = {'problem': 'hidden at this view'}
                continue
            centre = math.dist(((pb[0] + pb[2]) / 2, (pb[1] + pb[3]) / 2), ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2))
            parts[part] = {'box_iou': round(_box_iou(pb, box), 4), 'centre_error': round(centre / math.hypot(*size), 4), 'render_box': list(pb)}
    out_dir.mkdir(parents=True, exist_ok=True)
    overlay = photo.copy()
    draw_on = Image.new('RGB', size, (0, 0, 0))
    for mask, colour in ((ref, (0, 255, 0)), (got, (255, 0, 0))):
        edge = ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(3)), mask.filter(ImageFilter.MinFilter(3)))
        draw_on.paste(Image.new('RGB', size, colour), mask=edge)
        overlay.paste(Image.new('RGB', size, colour), mask=edge)
    sheet = Image.new('RGB', (size[0] * 3, size[1] + 28), (20, 24, 32))
    for i, (image, label) in enumerate(((photo, 'photo'), (look, 'render'), (overlay, 'overlay: photo green, render red'))):
        sheet.paste(image, (i * size[0], 28))
        ImageDraw.Draw(sheet).text((i * size[0] + 8, 8), label, fill='white')
    path = out_dir / f"{record['id']}_compare.png"
    sheet.save(path)
    return {'iou': round(mask_iou(ref, got), 4), 'aligned_iou': round(aligned_iou(got, ref, 0.05), 4), 'extent_ratio': extent,
            'edges': {k: round(v, 4) for k, v in edges.items()}, 'parts': parts, 'sheet': str(path)}


# ---- workbench host tools (studio/workbench.py dispatches them; the session renders, the host judges) ----------------

def _render(call, record, camera, passes, size, extra=None):
    view = {**camera, 'name': record['id'], 'frame_subject': record['subject_id']}   # a view's camera is in the subject's root frame
    result = call('preview', {'views': [view], 'passes': list(passes), 'size': int(size), 'subject_id': record['subject_id'], **(extra or {})})
    return result['images'][record['id']], result['palette']


def fit_camera(project, call, view, refine=True, size=256, max_evals=40, point_tolerance_px=POINT_TOLERANCE_PX):
    """reference_fit_camera: solve the photo's camera from its points (3D side: the session's anchors tool), then
    (refine) nudge the orbit and lens within REFINE_BOX for the best silhouette IoU at ``size`` px, keeping the marked
    points within ``point_tolerance_px`` (RMS; how accurately they were marked). Writes the camera
    into the view record; the scene is unchanged."""
    from .common import StudioError, read_json, write_json
    from .fidelity import mask_iou
    path = view_path(project, view)
    if not path.is_file():
        raise StudioError('INPUT_INVALID', f'no reference view {view} (add it with: studio reference view add)')
    record = read_json(path)
    width, height = _photo(project, record).size
    named = call('anchors', {'subject_id': record['subject_id']})['points']
    points3d, points2d = [], []
    for p in record.get('points') or []:
        if 'anchor' in p and p['anchor'] not in named:
            raise StudioError('INPUT_INVALID', f"anchor {p['anchor']!r} is not on the built subject (the anchors tool lists them)")
        points3d.append(named[p['anchor']] if 'anchor' in p else p['xyz'])
        points2d.append((p['px'][0] / width, p['px'][1] / height))
    try:
        solved = solve_pose(points3d, points2d, width, height, initial=record.get('camera'))
    except ValueError as exc:
        raise StudioError('INPUT_INVALID', f'reference_fit_camera: {exc}') from exc
    camera, out = solved['camera'], {'residual_px': round(solved['residual_px'], 3), 'evals': solved['evals']}
    if refine:
        ref = photo_mask(project, record).resize((max(2, int(size * width / max(width, height))), max(2, int(size * height / max(width, height)))))

        def at(x):
            c = dict(camera)
            for (key, half), t in zip(REFINE_BOX.items(), x):
                d = (2 * t - 1) * half
                if key == 'log_distance':
                    c['distance_m'] = camera['distance_m'] * math.exp(d)
                elif key == 'log_lens':
                    c['lens_mm'] = camera['lens_mm'] * math.exp(d)
                else:
                    c[key] = camera[key] + d
            return c
        # the silhouette refines only what the points leave open: leaving their residual tolerance costs like lost IoU
        tolerance = max(float(point_tolerance_px), 2 * solved['residual_px'])
        diag = math.hypot(width, height)

        def cost(x):
            c = at(x)
            iou = mask_iou(ref, _render_mask(_render(call, record, c, ['id'], size)[0]['id'], ref.size))
            return 1 - iou + max(0.0, residual_px(c, points3d, points2d) - tolerance) / diag * 50
        start = [0.5] * len(REFINE_BOX)
        before = cost(start)
        x, f, n = nelder_mead(cost, start, max_evals, step=0.25)
        out.update({'cost_points_only': round(before, 4), 'cost': round(f, 4), 'refine_evals': n + 1})
        if f < before:
            camera = at(x)
            out['residual_px_after_refine'] = round(residual_px(camera, points3d, points2d), 3)
    record.update({'camera': {k: (round(v, 6) if isinstance(v, float) else v) for k, v in camera.items()},
                   'residual_px': out['residual_px']})
    write_json(path, record)
    return {'view': view, 'camera': record['camera'], **out}


def compare_view(project, call, view, frame=None, size=768, lit=True, lit_samples=16, lit_light='studio', out_dir=None):
    """reference_compare: render the session at the view's solved camera (id + lit, or shaded) and measure it
    against the photo (compare_images). Exploration: no version, no budget."""
    from .common import StudioError, read_json
    path = view_path(project, view)
    if not path.is_file():
        raise StudioError('INPUT_INVALID', f'no reference view {view}')
    record = read_json(path)
    if not record.get('camera'):
        raise StudioError('INPUT_INVALID', f'{view} has no camera yet: run reference_fit_camera first')
    extra = {'lit_samples': lit_samples, 'lit_light': lit_light}
    if frame is not None:
        extra['frame'] = int(frame)
    images, palette = _render(call, record, record['camera'], ['id', 'lit' if lit else 'shaded'], size, extra)
    return {'view': view, **compare_images(project, record, images['id'], images['lit' if lit else 'shaded'], palette,
                                           Path(out_dir) if out_dir else path.parent / 'compare')}


# ---- photo_views: the build's fidelity check (studio/fidelity.py) -----------------------------------------------------

def projected(camera, solid, size, exclude=(), boxes_for=()):
    """What the camera sees of the subject's root-frame triangles at the photo's ``size``: the silhouette mask (255) and,
    for the parts in ``boxes_for``, the box [x0, y0, x1, y1] (x1 y1 exclusive) of their visible pixels - triangles are
    drawn far to near with part colours, so a part hidden behind another one does not count (like the render's id
    pass). Triangles with a corner behind the camera are skipped."""
    from PIL import Image, ImageChops, ImageDraw
    order = [part for part in sorted(solid) if part not in exclude and solid[part]]
    tris = []
    for index, part in enumerate(order, start=1):
        flat = project_points(camera, [p for t in solid[part] for p in t], depth=True)
        for k in range(0, len(flat), 3):
            corner = flat[k:k + 3]
            if all(c is not None for c in corner):
                tris.append((sum(c[2] for c in corner) / 3, index, [(c[0] * size[0], c[1] * size[1]) for c in corner]))
    ids = Image.new('RGB', size, (0, 0, 0))
    draw = ImageDraw.Draw(ids)
    for _, index, poly in sorted(tris, key=lambda t: -t[0]):   # far first: nearer triangles paint over
        draw.polygon(poly, fill=(index >> 16 & 255, index >> 8 & 255, index & 255))
    r, g, b = ids.split()
    mask = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v else 0)
    boxes = {}
    for part in boxes_for:
        if part not in order:
            continue
        index = order.index(part) + 1
        hit = ImageChops.multiply(ImageChops.multiply(r.point(lambda v: 255 if v == index >> 16 & 255 else 0),
                                                      g.point(lambda v: 255 if v == index >> 8 & 255 else 0)),
                                  b.point(lambda v: 255 if v == index & 255 else 0))
        box = hit.getbbox()
        if box:
            boxes[part] = list(box)
    return mask, boxes


def photo_measure(project, record, solid, exclude=()):
    """{iou, parts: {part: box_iou}, overlay image} of a built subject against one fitted reference view."""
    from PIL import Image
    from .fidelity import mask_iou
    ref = photo_mask(project, record)
    camera = {**record['camera'], 'width': ref.size[0], 'height': ref.size[1]}
    model, boxes = projected(camera, solid, ref.size, exclude, boxes_for=list(record.get('parts') or {}))
    parts = {part: (round(_box_iou(boxes[part], box), 4) if part in boxes else 0.0) for part, box in (record.get('parts') or {}).items()}
    overlay = Image.merge('RGB', (ref, model, Image.new('L', ref.size, 0)))   # red = photo only, green = model only, yellow = both
    return {'iou': round(mask_iou(ref, model), 4), 'parts': parts, 'overlay': overlay}
