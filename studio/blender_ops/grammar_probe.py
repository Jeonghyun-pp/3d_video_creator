"""Screen grammar T2: the camera's global motion between every pair of frames of a video, measured on the picture
alone (no scene) so a reference reel and our render are measured the same way.

Run: blender --background --factory-startup --python grammar_probe.py -- VIDEO OUT.json X0 Y0 X1 Y1 [WIDTH]
(X0..Y1: the content rect in canvas shares; Blender's Python is used only for numpy - the host has none.)

Per pair: a grid of patches is matched by phase correlation (sub-pixel), and a similarity motion is fitted to the
patch shifts by trimmed least squares: d = t + k*p + w*perp(p) about the frame centre, so t is translation, k the
scale step (push/pull/zoom) and w the roll. What the fit cannot explain - the residual - is depth-dependent motion
(parallax: the camera moved through space) or things moving on their own. Values are shares of the content width per
frame; grammar_core.camera turns a shot's series into the vocabulary.
"""
import json
import subprocess
import sys

import numpy as np

PATCH = 32                 # px at the analysis width; shifts up to PATCH/2 per frame are measurable
MIN_TEXTURE = 4.0          # luma std inside a patch below this: flat sky or black, no reliable shift
MIN_PEAK = 0.08            # phase-correlation peak below this: no clear match (occlusion, a cut, motion blur)
MIN_PATCHES = 6            # fewer good patches than this: the pair is not measured (ok false)
DUPLICATE_MAD = 0.35       # mean |difference| (0-255) below this: the same picture repeated (encoder noise only)


def frames(video, rect, width):
    probe = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
                                       '-of', 'json', video], capture_output=True, text=True, check=True).stdout)['streams'][0]
    cw = max(2, int(round((rect[2] - rect[0]) * probe['width'])) // 2 * 2)
    ch = max(2, int(round((rect[3] - rect[1]) * probe['height'])) // 2 * 2)
    cx, cy = int(round(rect[0] * probe['width'])), int(round(rect[1] * probe['height']))
    height = max(PATCH * 2, int(round(width * ch / cw)) // 2 * 2)
    proc = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', video, '-vf', f'crop={cw}:{ch}:{cx}:{cy},scale={width}:{height}',
                             '-f', 'rawvideo', '-pix_fmt', 'gray', '-'], stdout=subprocess.PIPE)
    size = width * height
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        yield np.frombuffer(buf, np.uint8).reshape(height, width).astype(np.float32)
    proc.wait()


def grid(h, w):
    ys = np.arange(PATCH // 2, h - PATCH // 2 + 1, PATCH * 3 // 4)
    xs = np.arange(PATCH // 2, w - PATCH // 2 + 1, PATCH * 3 // 4)
    return [(int(y), int(x)) for y in ys for x in xs]


WINDOW = np.outer(np.hanning(PATCH), np.hanning(PATCH)).astype(np.float32)


def patches(img, centres):
    half = PATCH // 2
    return np.stack([img[y - half:y + half, x - half:x + half] for y, x in centres])


def shifts(a, b):
    """Shift of each patch from a to b (b(x) = a(x - d) -> d), with peak strength."""
    fa = np.fft.fft2((a - a.mean(axis=(1, 2), keepdims=True)) * WINDOW)
    fb = np.fft.fft2((b - b.mean(axis=(1, 2), keepdims=True)) * WINDOW)
    r = fb * np.conj(fa)
    r /= np.abs(r) + 1e-9
    c = np.real(np.fft.ifft2(r))
    n = c.shape[0]
    flat = c.reshape(n, -1).argmax(axis=1)
    py, px = np.unravel_index(flat, (PATCH, PATCH))
    peak = c.reshape(n, -1)[np.arange(n), flat]

    def sub(arr, i, j, axis):   # parabolic sub-pixel refinement along one axis
        if axis == 0:
            m, z, p = arr[(i - 1) % PATCH, j], arr[i, j], arr[(i + 1) % PATCH, j]
        else:
            m, z, p = arr[i, (j - 1) % PATCH], arr[i, j], arr[i, (j + 1) % PATCH]
        d = m - 2 * z + p
        return 0.0 if abs(d) < 1e-9 else 0.5 * (m - p) / d

    dy = np.array([py[k] + sub(c[k], py[k], px[k], 0) for k in range(n)])
    dx = np.array([px[k] + sub(c[k], py[k], px[k], 1) for k in range(n)])
    dy = np.where(dy > PATCH / 2, dy - PATCH, dy)
    dx = np.where(dx > PATCH / 2, dx - PATCH, dx)
    return dx, dy, peak


def fit(points, dx, dy, weights):
    """Similarity fit d = t + k*p + w*perp(p); trimmed once at 2.5x the median residual."""
    px, py = points[:, 0], points[:, 1]
    keep = np.ones(len(px), bool)
    for _ in range(2):
        a = np.zeros((2 * keep.sum(), 4)); b = np.zeros(2 * keep.sum()); wt = np.repeat(weights[keep], 2)
        a[0::2, 0] = 1; a[0::2, 2] = px[keep]; a[0::2, 3] = -py[keep]
        a[1::2, 1] = 1; a[1::2, 2] = py[keep]; a[1::2, 3] = px[keep]
        b[0::2] = dx[keep]; b[1::2] = dy[keep]
        sw = np.sqrt(wt)
        sol, *_ = np.linalg.lstsq(a * sw[:, None], b * sw, rcond=None)
        res = np.hypot(dx - (sol[0] + sol[2] * px - sol[3] * py), dy - (sol[1] + sol[2] * py + sol[3] * px))
        limit = max(0.5, 2.5 * float(np.median(res[keep])))
        new = res <= limit
        if new.sum() < MIN_PATCHES or (new == keep).all():
            break
        keep = new
    return sol, float(np.median(res[keep])), float(keep.mean())


def measure(video, rect, width=192):
    out, prev, centres, points = [], None, None, None
    for img in frames(video, rect, width):
        if prev is None:
            h, w = img.shape
            centres = grid(h, w)
            points = np.array([(x - w / 2, y - h / 2) for y, x in centres], np.float64)
            prev, prev_p = img, patches(img, centres)
            continue
        mad = float(np.abs(img - prev).mean())
        if mad < DUPLICATE_MAD:   # a repeated frame (24 fps material delivered at 30/60): no new picture, no motion
            out.append({'ok': False, 'duplicate': True, 'mad': round(mad, 4)})
            prev = img
            continue
        cur_p = patches(img, centres)
        textured = (prev_p.std(axis=(1, 2)) >= MIN_TEXTURE) & (cur_p.std(axis=(1, 2)) >= MIN_TEXTURE)
        dx, dy, peak = shifts(prev_p, cur_p)
        good = textured & (peak >= MIN_PEAK)
        row = {'ok': False, 'mad': round(mad, 4)}
        if good.sum() >= MIN_PATCHES:
            sol, residual, inliers = fit(points[good], dx[good], dy[good], peak[good])
            row = {'ok': True, 'tx': round(float(sol[0]) / w, 6), 'ty': round(float(sol[1]) / w, 6),
                   'ds': round(float(np.log1p(sol[2])), 6), 'rot': round(float(sol[3]), 6),
                   'residual': round(residual / w, 6), 'inliers': round(inliers, 3), 'patches': int(good.sum()), 'mad': round(mad, 4)}
        out.append(row)
        prev, prev_p = img, cur_p
    return out


if __name__ == '__main__':
    args = sys.argv[sys.argv.index('--') + 1:]
    video, target = args[0], args[1]
    rect = [float(v) for v in args[2:6]]
    width = int(args[6]) if len(args) > 6 else 192
    rows = measure(video, rect, width)
    with open(target, 'w') as f:
        json.dump({'schema_version': 1, 'video': video, 'content_rect': rect, 'width': width, 'pairs': rows}, f)
