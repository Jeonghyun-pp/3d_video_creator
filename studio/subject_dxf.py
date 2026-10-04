"""CAD drawings as subject sources: DXF outlines -> exact silhouettes and builder parameters.

A DXF already carries true coordinates, so the silhouette it produces is registered exactly (px_per_m
and a datum at the DXF origin are known, not estimated), and its closed outlines become `profile`
points or `wall` openings without anyone reading numbers off a picture. Parsing runs in the CAD
interpreter (asset_factory/dxf_worker.py, ezdxf); this side renders and proposes.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageDraw

from .common import StudioError, check_id, file_hash, read_json, write_json
from .project import project_dir
from .subjects import lint_spec, load_spec, spec_path

DEFAULT_MAX_PX = 2400
MARGIN_PX = 20


def _area(points):
    return 0.5 * sum(points[i - 1][0] * points[i][1] - points[i][0] * points[i - 1][1] for i in range(len(points)))


def read_dxf(path, units='auto'):
    from .asset_factory.factory import DXF_WORKER, cad_python
    python = cad_python()
    if not python.is_file():
        raise StudioError('ENVIRONMENT_MISSING', f'CAD interpreter not found: {python}', recovery='See studio/asset_factory/requirements-cad.txt')
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'outlines.json'
        run = subprocess.run([str(python), '-I', str(DXF_WORKER), str(path), str(out), units], capture_output=True, text=True, timeout=300)
        if run.returncode != 0 or not out.is_file():
            raise StudioError('INPUT_INVALID', f'DXF could not be read: {(run.stderr or run.stdout)[-1500:]}')
        return read_json(out)


def outline_set(outlines):
    """Largest outline is the boundary; outlines inside it are holes (openings)."""
    if not outlines:
        raise StudioError('INPUT_INVALID', 'layer has no closed outlines')
    ordered = sorted(outlines, key=lambda p: abs(_area(p)), reverse=True)
    outer = ordered[0]
    xs = [p[0] for p in outer]; ys = [p[1] for p in outer]
    box = (min(xs), min(ys), max(xs), max(ys))
    holes = [p for p in ordered[1:] if all(box[0] <= x <= box[2] and box[1] <= y <= box[3] for x, y in p)]
    return outer, holes, box


def render(outer, holes, box, px_per_m):
    """Silhouette PNG (subject black on white) with the datum that maps DXF (0, 0) exactly."""
    width = int((box[2] - box[0]) * px_per_m) + 2 * MARGIN_PX
    height = int((box[3] - box[1]) * px_per_m) + 2 * MARGIN_PX
    to_px = lambda x, y: (MARGIN_PX + (x - box[0]) * px_per_m, height - MARGIN_PX - (y - box[1]) * px_per_m)  # noqa: E731
    image = Image.new('L', (width, height), 255)
    draw = ImageDraw.Draw(image)
    draw.polygon([to_px(x, y) for x, y in outer], fill=0)
    for hole in holes:
        draw.polygon([to_px(x, y) for x, y in hole], fill=255)
    origin = to_px(0.0, 0.0)
    return image, {'px': [round(origin[0], 4), round(origin[1], 4)], 'model': [0.0, 0.0], 'u_dir': '+x', 'v_dir': '-y'}


def wall_candidate(outer, holes, box):
    """wall params when the boundary and every hole are axis-aligned rectangles (x along length, y up)."""
    def rect(points):
        xs = sorted({round(p[0], 6) for p in points}); ys = sorted({round(p[1], 6) for p in points})
        return (xs[0], ys[0], xs[-1], ys[-1]) if len(xs) == 2 and len(ys) == 2 else None
    if rect(outer) is None or any(rect(h) is None for h in holes):
        return None
    x0, y0 = box[0], box[1]
    return {'length': round(box[2] - box[0], 6), 'height': round(box[3] - box[1], 6),
            'openings': [{'x': round(r[0] - x0, 6), 'z': round(r[1] - y0, 6), 'w': round(r[2] - r[0], 6), 'h': round(r[3] - r[1], 6)}
                         for r in sorted(rect(h) for h in holes)]}


def from_dxf(project, subject_id, dxf, layer, view='front', units='auto', px_per_m=None, license=None, apply=False):
    path = Path(dxf).resolve()
    if not path.is_file():
        raise StudioError('INPUT_INVALID', f'DXF not found: {dxf}')
    data = read_dxf(path, units)
    if layer not in data['layers']:
        raise StudioError('INPUT_INVALID', f'layer {layer!r} has no closed outlines (layers: {sorted(data["layers"])})')
    outer, holes, box = outline_set(data['layers'][layer])
    size = max(box[2] - box[0], box[3] - box[1])
    px_per_m = float(px_per_m or round((DEFAULT_MAX_PX - 2 * MARGIN_PX) / size, 3))
    image, datum = render(outer, holes, box, px_per_m)
    folder = spec_path(project, subject_id).parent / 'refs'
    folder.mkdir(parents=True, exist_ok=True)
    name = f'{check_id(path.stem.lower().replace(" ", "_"))}_{check_id(layer.lower())}_{view}.png'
    image.save(folder / name)
    source_id = f'cad.{check_id(path.stem.lower().replace(" ", "_"))}'
    relative = str((folder / name).relative_to(project_dir(project)))
    silhouette = {'view': view, 'image': relative, 'source_id': source_id, 'px_per_m': px_per_m, 'datum': datum, 'min_iou': 0.95}
    result = {'subject_id': subject_id, 'dxf': str(path), 'dxf_sha256': file_hash(path), 'layer': layer, 'units_scale_m': data['units_scale_m'],
              'bbox_m': [round(v, 6) for v in box], 'outer_points': len(outer), 'holes': len(holes), 'skipped_open_entities': data['skipped_count'],
              'silhouette': silhouette, 'profile_points': [[round(x, 6), round(y, 6)] for x, y in outer],
              'wall': wall_candidate(outer, holes, box), 'image': str(folder / name)}
    candidates = spec_path(project, subject_id).parent / 'candidates'
    out = candidates / f'dxf_{check_id(layer.lower())}_{view}.json'
    write_json(out, result)
    if apply:
        if not license:
            raise StudioError('INPUT_INVALID', 'Applying a CAD source needs --license (who may use this drawing)')
        spec = load_spec(project, subject_id)
        spec['sources'] = [s for s in spec['sources'] if s['id'] != source_id] + [{'id': source_id, 'kind': 'cad', 'path': str(path), 'license': license,
                                                                                    'note': f'layer {layer}, sha256 {result["dxf_sha256"][:12]}'}]
        spec['silhouettes'] = [s for s in spec.get('silhouettes', []) if s['view'] != view] + [silhouette]
        lint = lint_spec(spec, project)
        if lint['errors']:
            raise StudioError('SUBJECT_SPEC_INVALID', 'Spec with the CAD silhouette fails lint: ' + '; '.join(lint['errors'][:6]))
        write_json(spec_path(project, subject_id), spec)
    return {**result, 'candidate_path': str(out), 'applied': apply, 'artifacts': [str(out), str(folder / name)]}
