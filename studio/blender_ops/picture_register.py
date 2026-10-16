"""Lay a painting back onto the clay render it was painted over (host: studio/picture3d.register): its edges against
the render's lines inside the region of named things (register_core.register: one global scale + shift; a painting of
another aspect cut to the clay's first, aspect_crop), then the
painting resampled at the output size with that similarity (mirrored borders: a clamped edge smears into streaks).
Blender's bundled python (numpy + OpenImageIO), no scene: blender -b --factory-startup --python picture_register.py --
<job.json>. job: {items: [{folder, painted, out}], output: [w, h], limit}; writes <out> and <folder>/register.json
(one entry per painting registered).
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import OpenImageIO as oiio

sys.path.insert(0, str(Path(__file__).parent))
import register_core as rc  # noqa: E402

REGION_GROW_PX = 12  # the named things' outlines (region.png, drawn at the id pass's 256 px) widened by this at the
                     # registration size: a painting's edge near an outline counts, its painted texture inside does not


def aspect_crop(buf, size):
    """The painting cut to the clay's aspect about its centre (the image tool may answer in another aspect: a 2:3 painting
    of a 9:16 clay), before any fit - stretched, the fit's one scale could never match both axes. -> (buf, [x, y, w, h])."""
    spec = buf.spec()
    w, h = spec.width, spec.height
    if abs(w * size[1] - h * size[0]) <= max(size):   # within a pixel of the clay's aspect
        return buf, [0, 0, w, h]
    if w * size[1] > h * size[0]:                       # wider than the clay: cut the sides
        cw, ch = round(h * size[0] / size[1]), h
    else:                                               # taller: cut top and bottom
        cw, ch = w, round(w * size[1] / size[0])
    x, y = (w - cw) // 2, (h - ch) // 2
    return oiio.ImageBufAlgo.cut(buf, oiio.ROI(x, x + cw, y, y + ch, 0, 1, 0, spec.nchannels)), [x, y, cw, ch]


def gray(path, size):
    """A grey image (0-1 floats) at size (w, h), from a path or an ImageBuf."""
    buf = path if isinstance(path, oiio.ImageBuf) else oiio.ImageBuf(str(path))
    spec = buf.spec()
    if (spec.width, spec.height) != tuple(size):
        buf = oiio.ImageBufAlgo.resize(buf, roi=oiio.ROI(0, size[0], 0, size[1], 0, 1, 0, spec.nchannels))
    px = buf.get_pixels(oiio.FLOAT)
    if px.ndim == 2:
        px = px[:, :, None]
    if px.shape[2] >= 3:
        return 0.299 * px[:, :, 0] + 0.587 * px[:, :, 1] + 0.114 * px[:, :, 2]
    return px[:, :, 0]


def size_of(path):
    spec = oiio.ImageBuf(str(path)).spec()
    return [spec.width, spec.height]


def one(item, output, limit):
    folder = Path(item['folder'])
    lines = folder / 'lines.png'
    spec = oiio.ImageBuf(str(lines)).spec()
    size = (spec.width, spec.height)
    ref = gray(lines, size) < 0.5
    region = rc.dilate(gray(folder / 'region.png', size) > 0.5, REGION_GROW_PX)
    if not region.any():   # nothing named in frame: the whole frame is the region
        region = np.ones(size[::-1], bool)
    painted, crop = aspect_crop(oiio.ImageBuf(str(item['painted'])), size)
    edges = rc.canny(gray(painted, size))
    before = rc.f1(edges & region, ref & region)['f1']
    fit = rc.register(edges, ref, region)
    W, H = output
    src = oiio.ImageBufAlgo.resize(painted, roi=oiio.ROI(0, W, 0, H, 0, 1, 0, 3))
    s, dx, dy = fit['scale'], fit['dx_px'] * W / size[0], fit['dy_px'] * H / size[1]
    matrix = (s, 0, 0, 0, s, 0, (1 - s) * W / 2 + dx, (1 - s) * H / 2 + dy, 1)   # p_out = p_in @ M (row vectors)
    moved = oiio.ImageBufAlgo.channels(oiio.ImageBufAlgo.warp(src, matrix, filtername='lanczos3', wrap='mirror', recompute_roi=False), (0, 1, 2))
    moved.write(str(item['out']), 'uint8')
    row = {'painted': str(item['painted']), 'out': str(item['out']), 'painted_size': size_of(item['painted']), 'crop': crop, **fit,
           'subject_f1_before': before,
           'over_limit': fit['unpainted_share'] > limit or fit['at_search_edge'], 'limit': limit}
    record = folder / 'register.json'
    rows = json.loads(record.read_text()) if record.is_file() else {}
    rows[Path(item['painted']).name] = row
    record.write_text(json.dumps(rows, indent=1))
    return row


if __name__ == '__main__' and '--' in sys.argv:
    job = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text())
    results = [one(item, job['output'], job['limit']) for item in job['items']]
    Path(job['result']).write_text(json.dumps(results, indent=1))
