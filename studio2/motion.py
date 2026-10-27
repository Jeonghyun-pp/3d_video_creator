"""A cut's free motion and graphics: its picked pictures moved and marked locally into the clip v1's edit cuts in (route
composite: shots/<cut>/composite/<key>/clip.mp4 + clip.json; studio.edit.latest_composite).

What it draws, all from the cut as planned and the places located on its picture (studio2/locate.py):
- the camera: held, or a slow push in toward the located focus, or a pull back from it, by the cut's amount, eased; or
  carried along its way (travel: a ride) into the focus at a steady speed, the frame's edges streaked as things pass;
- the change: a state change crosses from the first picture (through the middle) to the last over change.at - a cut when
  the window is a moment; a moving thing travels its located path over change.at (a glowing speck, or its own picture
  keyed off its flat background), its label riding with it;
- the flows: moving glowing dashes along each located way, in the cut's colour row;
- the labels: the reference's letters (white, bold, an outline for any ground) at the reel's letter height (the cuts
  layer's label_height, chosen against the reference's measure: the label_text field of the cutaway grammar), a thin
  white leader to a ring on the part, placed beside the part and clear of the subtitle band and of each other;
- an outline of what changed, once it has (change.outline, a colour row), from the difference of the first and last.
The clip is as long as the cut's shot (duration_frames): lengths as planned.

Why (2026-10-26): stage 1's tools (one script per clip, positions typed by hand) made the clips that ranked first; this
is the same drawing, read from the plan and the located places instead of typed.
"""
from __future__ import annotations

import math
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from studio.common import REPO, StudioError, file_hash, now, read_json, stable_hash, write_json
from studio.project import load_project, load_shot, project_dir, shot_path

FONT = REPO / 'library' / 'fonts' / 'pretendard' / 'Pretendard-Bold.otf'
LABEL_FADE_S = 0.25
FLOW_FADE_S = 0.3
FLOW_SPEED = 0.16        # dashes travel this share of the frame height a second
SPECK_R = 0.0055         # a speck's core radius, a share of the frame height
SPRITE_H = 0.11          # a moving thing's own picture, a share of the frame height
MARGIN = 0.05            # labels keep this share of the width from the frame's sides
WAY_POINTS = 16          # a way is followed between two located plans at this many points along it
LABEL_STEP = 0.1         # labels without their own time appear this share of the cut apart, from LABEL_FIRST
LABEL_FIRST = 0.08


def ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def ramp(t, a, b):
    return ease((t - a) / (b - a)) if b > a else float(t >= a)


def along(points, k):
    """The point at fraction k of a polyline's length."""
    segs = [(points[i], points[i + 1], math.dist(points[i], points[i + 1])) for i in range(len(points) - 1)]
    total = sum(s[2] for s in segs) or 1.0
    d = max(0.0, min(1.0, k)) * total
    for a, b, length in segs:
        if d <= length or (a, b, length) == segs[-1]:
            f = d / length if length else 0.0
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        d -= length
    return points[-1]


def poly_len(points):
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def cover(image, w, h):
    """The picture filling the frame, cropped at its centre (never stretched)."""
    scale = max(w / image.width, h / image.height)
    resized = image.resize((max(w, round(image.width * scale)), max(h, round(image.height * scale))), Image.LANCZOS)
    x0, y0 = (resized.width - w) // 2, (resized.height - h) // 2
    return resized.crop((x0, y0, x0 + w, y0 + h))


def key_out(image, key=(0, 255, 0)):
    """A thing drawn on a flat key colour, the colour made transparent (its alpha from how far a pixel is from it)."""
    rgb = image.convert('RGB')
    r, g, b = rgb.split()
    dist = ImageChops.add(ImageChops.add(ImageChops.difference(r, Image.new('L', rgb.size, key[0])),
                                         ImageChops.difference(g, Image.new('L', rgb.size, key[1]))),
                          ImageChops.difference(b, Image.new('L', rgb.size, key[2])))
    alpha = dist.point(lambda v: 0 if v < 90 else 255 if v > 200 else int((v - 90) * 255 / 110))
    out = rgb.convert('RGBA')
    out.putalpha(alpha)
    return out.crop(out.getbbox() or (0, 0, *out.size))


# --- label size ------------------------------------------------------------------------------------------------------

def label_height_share(body):
    """The reel's label letter height (the cuts layer's label_height, chosen against the reference's measure)."""
    return body['label_height']


_FONTS = {}


def font_for(ink_px, word='가나다'):
    """The bold font size whose letters' ink is ink_px tall (Hangul, measured by the font itself)."""
    key = round(ink_px)
    if key not in _FONTS:
        size = max(8, key)
        probe = ImageDraw.Draw(Image.new('L', (8, 8)))
        while True:
            font = ImageFont.truetype(str(FONT), size)
            box = probe.textbbox((0, 0), word, font=font)
            if box[3] - box[1] >= ink_px or size > 400:
                break
            size += 1
        _FONTS[key] = font
    return _FONTS[key]


def ink_height(word, font):
    box = ImageDraw.Draw(Image.new('L', (8, 8))).textbbox((0, 0), word, font=font)
    return box[3] - box[1]


# --- drawing -----------------------------------------------------------------------------------------------------------

class Marks:
    """A glow layer (drawn at half size, blurred, added) and a sharp layer (over) for one frame."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.glow = Image.new('RGBA', (w // 2, h // 2), (0, 0, 0, 0))
        self.sharp = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        self.g, self.s = ImageDraw.Draw(self.glow), ImageDraw.Draw(self.sharp)

    def _half(self, pts):
        return [(x / 2, y / 2) for x, y in pts]

    def flow(self, pts, t, colour, alpha=1.0, width=None):
        if alpha <= 0 or len(pts) < 2:
            return
        width = width or max(4, round(self.h * 0.0034))
        dash, gap = self.h * 0.024, self.h * 0.018
        length, period = poly_len(pts), dash + gap
        off = (t * FLOW_SPEED * self.h) % period - period
        a = int(255 * alpha)
        light = tuple(min(255, c + 110) for c in colour)
        while off < length:
            s0, s1 = max(0.0, off), min(length, off + dash)
            if s1 > s0:
                p, q = along(pts, s0 / length), along(pts, s1 / length)
                self.g.line(self._half([p, q]), fill=colour + (a,), width=max(2, width * 3 // 2))
                self.s.line([p, q], fill=light + (a,), width=max(3, width // 2 + 1))
            off += period

    def speck(self, p, colour, alpha=1.0, ring=False, r=None):
        if alpha <= 0:
            return
        r = r or self.h * SPECK_R
        a = int(255 * alpha)
        x, y = p
        self.g.ellipse([(x - r * 2.6) / 2, (y - r * 2.6) / 2, (x + r * 2.6) / 2, (y + r * 2.6) / 2], fill=colour + (int(a * 0.9),))
        core = tuple(min(255, c + 90) for c in colour)
        self.s.ellipse([x - r, y - r, x + r, y + r], fill=core + (a,), outline=(80, 40, 0, a), width=2)
        if ring:
            big = r * 3.2
            self.s.ellipse([x - big, y - big, x + big, y + big], outline=(255, 255, 255, a), width=3)

    def sprite(self, picture, p, alpha=1.0):
        if alpha <= 0:
            return
        layer = picture if alpha >= 1 else picture.copy()
        if alpha < 1:
            layer.putalpha(layer.getchannel('A').point(lambda v: int(v * alpha)))
        self.sharp.alpha_composite(layer, (round(p[0] - picture.width / 2), round(p[1] - picture.height / 2)))

    def label(self, word, anchor, spot, font, alpha=1.0):
        """White bold letters with a dark outline at `spot` (their centre), a thin white leader to a ring on `anchor`."""
        if alpha <= 0:
            return
        a = int(255 * alpha)
        stroke = max(2, round(font.size * 0.07))
        x0, y0, x1, y1 = self.s.textbbox(spot, word, font=font, anchor='mm', stroke_width=stroke)
        pad = font.size * 0.25
        cx = min(max(anchor[0], x0 - pad), x1 + pad)
        cy = min(max(anchor[1], y0 - pad), y1 + pad)
        line = max(2, round(self.h * 0.0016))
        r = max(5, round(self.h * 0.0045))
        self.s.line([(cx, cy), anchor], fill=(0, 0, 0, a // 3), width=line + 2)
        self.s.line([(cx, cy), anchor], fill=(255, 255, 255, a), width=line)
        self.s.ellipse([anchor[0] - r, anchor[1] - r, anchor[0] + r, anchor[1] + r], outline=(255, 255, 255, a), width=line + 1)
        self.s.text(spot, word, font=font, anchor='mm', fill=(255, 255, 255, a), stroke_width=stroke, stroke_fill=(20, 24, 30, a))

    def mask(self, edge, colour, alpha=1.0):
        if alpha <= 0:
            return
        layer = Image.new('RGBA', edge.size, colour + (0,))
        layer.putalpha(edge.point(lambda v: int(v * alpha)))
        self.sharp.alpha_composite(layer)
        self.glow.alpha_composite(layer.resize(self.glow.size))

    def _glow(self):
        return self.glow.filter(ImageFilter.GaussianBlur(max(2, self.h // 380))).resize((self.w, self.h), Image.BILINEAR)

    def onto(self, base):
        glow = self._glow()
        out = base.convert('RGBA')
        out.alpha_composite(glow)
        out.alpha_composite(self.sharp)
        return out.convert('RGB')


def zoom(image, z, cx, cy):
    if abs(z - 1) < 1e-4:
        return image
    w, h = image.width / z, image.height / z
    x0 = min(max(cx * image.width - w / 2, 0), image.width - w)
    y0 = min(max(cy * image.height - h / 2, 0), image.height - h)
    return image.resize(image.size, Image.BICUBIC, box=(x0, y0, x0 + w, y0 + h))


STREAK_STEPS = (0.008, 0.016, 0.024, 0.032, 0.040, 0.048, 0.056)   # a travel's edge smear: copies zoomed this much further,
                                                                     # fine enough to read as one blur, not ghosts
STREAK_INNER = 0.35                 # the way ahead stays sharp inside this share of the half-diagonal from the focus


def camera_zoom(cam, t, seconds):
    """How much the picture is enlarged at t: a push eases in, a pull eases out, a travel runs at one speed (a ride does
    not settle; a speed change is made in the edit)."""
    amount, k = cam.get('amount') or 0.0, (t / seconds if seconds else 1.0)
    if cam['move'] == 'push':
        return 1 + amount * ease(k)
    if cam['move'] == 'pull':
        return 1 + amount * (1 - ease(k))
    if cam['move'] == 'travel':
        return 1 + amount * min(1.0, max(0.0, k))
    return 1.0


def streak(image, z, cx, cy, strength=1.0):
    """The picture zoomed by z toward (cx, cy) with what passes at its edges smeared along the travel (copies zoomed a
    little further, blended in by distance from the focus): the way ahead stays sharp."""
    sharp = zoom(image, z, cx, cy)
    smear = sharp
    for n, step in enumerate(STREAK_STEPS, 2):
        smear = Image.blend(smear, zoom(image, z * (1 + step * strength), cx, cy), 1 / n)
    return Image.composite(smear, sharp, _streak_mask(image.size, round(cx, 4), round(cy, 4)))


@lru_cache(maxsize=8)
def _streak_mask(size, cx, cy):
    """0 inside STREAK_INNER of the half-diagonal from the focus, rising to full at the frame's far reaches."""
    w, h = size
    mask = Image.new('L', (w, h))
    mask.putdata([max(0, min(255, round(255 * (math.hypot((x - cx * w) / (w / 2), (y - cy * h) / (h / 2)) / math.sqrt(2) - STREAK_INNER)
                                           / (1 - STREAK_INNER) * 2))) for y in range(h) for x in range(w)])
    return mask


def ride_frame(picture, marks, z, cx, cy):
    """A travel's frame: the picture zoomed and smeared at its edges, the marks (drawn where follow_zoom put them) laid on
    sharp - a label rides with what it names but never streaks with the walls passing behind it."""
    return marks.onto(streak(picture, z, cx, cy))


def follow_zoom(plan, z, cx, cy, w, h):
    """The plan's marks where the picture zoomed by z toward (cx, cy) puts their parts (zoom's own crop), each label at its
    own letter size and its own offset from its part, kept inside the safe rect: the letters stay at the reference's
    height however far a ride runs in (fab c04b, 2026-10-27: a label grown 1.6x ran off the frame)."""
    if abs(z - 1) < 1e-4:
        return plan
    x0 = min(max(cx * w - w / z / 2, 0), w - w / z)
    y0 = min(max(cy * h - h / z / 2, 0), h - h / z)
    at = lambda p: ((p[0] - x0) * z, (p[1] - y0) * z)   # noqa: E731
    sx0, sy0, sx1, sy1 = plan['safe']
    probe = ImageDraw.Draw(Image.new('L', (8, 8)))
    labels = []
    for word, anchor, spot, share, present in plan['labels']:
        moved = at(anchor)
        bx0, by0, bx1, by1 = probe.textbbox((0, 0), word, font=plan['font'], anchor='mm')
        x = min(max(moved[0] + spot[0] - anchor[0], sx0 - bx0), sx1 - bx1)
        y = min(max(moved[1] + spot[1] - anchor[1], sy0 - by0), sy1 - by1)
        labels.append((word, moved, (x, y), share, present))
    out = {**plan, 'labels': labels, 'flows': [([at(p) for p in pts], colour) for pts, colour in plan['flows']]}
    if plan.get('element') and plan['element'].get('path'):
        out['element'] = {**plan['element'], 'path': [at(p) for p in plan['element']['path']]}
    return out


def change_edge(a, b, thresh=45, keep=0.25):
    """The outline of the region that differs between two pictures of one composition (stage 1's, unchanged)."""
    w, h = a.size
    sw, sh = w // 4, h // 4
    d = ImageChops.difference(a, b).convert('L').filter(ImageFilter.GaussianBlur(4)).resize((sw, sh))
    m = d.point(lambda v: 255 if v > thresh else 0).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    px = m.load()
    rows = [[x for x in range(sw) if px[x, y]] for y in range(sh)]
    span = max((r[-1] - r[0] for r in rows if r), default=1)
    f = Image.new('L', (sw, sh), 0)
    fd = ImageDraw.Draw(f)
    for y, r in enumerate(rows):
        if r and len(r) > keep * span:
            fd.line([(r[0], y), (r[-1], y)], fill=255)
    fp = f.load()
    for x in range(sw):
        col = [y for y in range(sh) if fp[x, y]]
        if col:
            fd.line([(x, col[0]), (x, col[-1])], fill=255)
    m = f.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5)).resize((w, h), Image.BILINEAR)
    m = m.point(lambda v: 255 if v > 127 else 0)
    return ImageChops.subtract(m.filter(ImageFilter.MaxFilter(7)), m)


# --- layout --------------------------------------------------------------------------------------------------------

def caption_band(path, w, h):
    """(top, bottom) px of where v1's edit puts the subtitle (its safe rect's bottom, two lines of its caption size)."""
    from studio.edit import _safe_rect, caption_font
    style_file = project_dir(path) / 'style.json'
    style = read_json(style_file) if style_file.is_file() else {}
    _l, _t, _r, bottom = _safe_rect(style, w, h)
    size = caption_font(style, project_dir(path), w, h)[0].size
    return bottom - 2 * 1.28 * size - 2 * max(6, w * 0.015) - h * 0.01, bottom + h * 0.01


def safe_rect(path, w, h):
    """(x0, y0, x1, y1) px of the frame no platform UI covers (v1's blender.ui_rect: the style's title-safe rect, else the
    9:16 feed's) - a label's letters stay inside it."""
    from studio.blender import ui_rect
    style_file = project_dir(path) / 'style.json'
    x0, y0, x1, y1 = ui_rect(read_json(style_file) if style_file.is_file() else {}, (w, h))
    return x0 * w, y0 * h, x1 * w, y1 * h


def place_labels(rows, w, h, band, font, safe=None):
    """A spot for each label's letters: beside its part (outward from the frame's middle first, then the other side, above
    then below), inside the UI-safe rect, out of the subtitle band and clear of the labels placed before it; when no spot
    beside it is clear, the nearest spot inside the safe rect."""
    sx0, sy0, sx1, sy1 = safe or (w * MARGIN, h * 0.06, w * (1 - MARGIN), h * 0.94)
    taken, out = [], []
    probe = ImageDraw.Draw(Image.new('L', (8, 8)))
    for word, anchor in rows:
        bx0, by0, bx1, by1 = probe.textbbox((0, 0), word, font=font, anchor='mm')
        bw, bh = bx1 - bx0 + font.size * 0.4, by1 - by0 + font.size * 0.4
        side = 1 if anchor[0] >= w / 2 else -1
        dx, dy = bw / 2 + h * 0.03, bh * 1.6
        candidates = [(anchor[0] + s * dx * k, anchor[1] + v * dy * k) for k in (1, 1.6, 2.4) for s in (side, -side) for v in (-1, 1, 0)]
        chosen = None
        for x, y in candidates:
            x = min(max(x, sx0 + bw / 2), sx1 - bw / 2)
            box = (x - bw / 2, y - bh / 2, x + bw / 2, y + bh / 2)
            if box[1] < sy0 or box[3] > sy1 or (box[3] > band[0] and box[1] < band[1]):
                continue
            if any(not (box[2] < t[0] or box[0] > t[2] or box[3] < t[1] or box[1] > t[3]) for t in taken):
                continue
            if box[0] <= anchor[0] <= box[2] and box[1] <= anchor[1] <= box[3]:
                continue   # never over its own ring
            chosen = (x, y, box)
            break
        if chosen is None:   # nowhere clear beside it: the nearest spot inside the safe rect
            x = min(max(anchor[0], sx0 + bw / 2), sx1 - bw / 2)
            y = min(max(anchor[1] - dy, sy0 + bh / 2), sy1 - bh / 2)
            chosen = (x, y, (x - bw / 2, y - bh / 2, x + bw / 2, y + bh / 2))
        taken.append(chosen[2])
        out.append((chosen[0], chosen[1]))
    return out


# --- the clip ------------------------------------------------------------------------------------------------------

def _pictures(path, cut, w, h):
    from .frames import needed, picked
    out = {}
    for frame in needed(cut):
        png = picked(path, cut, frame)
        if png is None:
            raise StudioError('FRAMES_UNPICKED', f"{cut['cut_id']}: its {frame} picture is not picked",
                              recovery=f"studio2 frames make / checks run / frames pick --cut {cut['cut_id']} --frame {frame}")
        out[frame] = png
    return out


def plan_of(path, cut, places, w, h, frames, body=None):
    """Everything the frame function draws, in pixels: the camera, the change, the flows, the labels."""
    from .grammar import colour, load
    table = load()
    px = lambda p: (p[0] * w, p[1] * h)   # noqa: E731
    cam = cut['cutaway']['camera']
    change = cut['change']
    at = change.get('at') or [0.0, 1.0]
    parts = {p['id']: px(p['point']) for p in places['parts']}
    present = {p['id']: 1.0 if p.get('shown', True) is not False else 0.0 for p in places['parts']}   # its part in this picture
    from studio import decisions
    body = body or decisions.envelope(path, 'cuts')['body']
    font = font_for(label_height_share(body) * h)
    band = caption_band(path, w, h)
    ways = {f['index']: [[px(q) for q in way] for way in ways_of(f)] for f in places['flows']}

    def anchor_of(label):   # a part's located point, or the middle of the flow's first way
        if 'part' in label:
            return parts.get(label['part'])
        return along(ways[label['flow']][0], 0.5) if ways.get(label['flow']) else None
    labels = [(l['word'], anchor_of(l), l.get('at', LABEL_FIRST + LABEL_STEP * n)) for n, l in enumerate(cut['cutaway']['labels'])
              if anchor_of(l) is not None]
    safe = safe_rect(path, w, h)
    spots = place_labels([(word, anchor) for word, anchor, _ in labels], w, h, band, font, safe)
    flows = [(way, colour(cut['cutaway']['flow'][i]['colour'], table)) for i, drawn in ways.items() for way in drawn]
    element = change.get('element')
    probe = ImageDraw.Draw(Image.new('L', (8, 8)))
    fixed = [(x, y, probe.textbbox((x, y), word, font=font, anchor='mm')) for (word, _a, _s), (x, y) in zip(labels, spots)]
    from .cuts import in_pictures
    # the window the pictures cross in: a state's change, or a thing of the scene painted where it starts and where it ends
    cross = (list((element or {}).get('at') or at) if (element or {}).get('drawn') == 'picture' else at) if in_pictures(change) else None
    return {'seconds': frames / 30, 'camera': cam, 'focus': places.get('focus') or [0.5, 0.5], 'at': at, 'kind': change['kind'], 'cross': cross,
            'flows': flows, 'labels': [(word, anchor, spot, share, present.get(l.get('part'), 1.0)) for (word, anchor, share), spot, l
                                       in zip(labels, spots, [l for l in cut['cutaway']['labels'] if anchor_of(l) is not None])], 'font': font,
            'element': element and {'path': [px(q) for q in places.get('element') or []], 'label': element.get('label'),
                                    'drawn': element['drawn'], 'colour': colour(element.get('colour', 'orange'), table) or (255, 150, 40),
                                    'at': list(element.get('at') or at), 'size': element.get('size'), 'count': element.get('count', 1)},
            'outline': colour(change['outline'], table) if change.get('outline') else None, 'band': band, 'safe': safe, 'fixed': fixed}


def ways_of(located):
    """A located flow's ways (one path each); a record from before flows had ways holds one, as points."""
    return located.get('ways') or [located['points']]


def specks_at(element, t, seconds, h):
    """[(point, alpha)] of a stream of specks at time t: `count` specks set off one after another over the element's
    window, each travelling the path in a share of it, a little apart from the others (their offsets fixed by their
    number); one speck travels the whole window."""
    ea, eb = element['at'][0] * seconds, element['at'][1] * seconds
    count = max(1, element.get('count') or 1)
    span = (eb - ea) * (0.45 if count > 1 else 1.0)
    out = []
    for k in range(count):
        t0 = ea + ((eb - ea) - span) * (k / (count - 1) if count > 1 else 0.0)
        t1 = t0 + span
        if t < t0 - 0.2 or t > t1 + 0.45:
            continue
        k_t = ramp(t, t0, t1) if t1 > t0 else float(t >= t0)
        x, y = along(element['path'], k_t)
        dx, dy = (math.sin(k * 2.39) * h * 0.012, math.cos(k * 1.71) * h * 0.006) if count > 1 else (0.0, 0.0)
        alpha = 1.0 if t0 <= t <= t1 + 0.15 else (ramp(t, t0 - 0.2, t0) if t < t0 else 1 - ramp(t, t1 + 0.15, t1 + 0.45))
        out.append(((x + dx, y + dy), alpha))
    return out


def moving_spot(p, word, font, w, h, safe, fixed):
    """Where a moving thing's label goes this frame: beside it (right and up first), inside the safe rect, off the
    fixed labels' boxes; else the first place inside the safe rect."""
    sx0, sy0, sx1, sy1 = safe
    probe = ImageDraw.Draw(Image.new('L', (8, 8)))
    bx0, by0, bx1, by1 = probe.textbbox((0, 0), word, font=font, anchor='mm')
    bw, bh = bx1 - bx0, by1 - by0
    first = None
    for sx, sy in ((1, -1), (-1, -1), (1, 1), (-1, 1), (1, -2.2), (-1, -2.2), (1, 2.2), (-1, 2.2)):
        x = min(max(p[0] + sx * (bw / 2 + h * 0.025), sx0 + bw / 2), sx1 - bw / 2)
        y = min(max(p[1] + sy * (bh / 2 + h * 0.012), sy0 + bh / 2), sy1 - bh / 2)
        box = (x - bw / 2, y - bh / 2, x + bw / 2, y + bh / 2)
        first = first or (x, y)
        if all(box[2] < f[0] or box[0] > f[2] or box[3] < f[1] or box[1] > f[3] for _x, _y, f in fixed):
            return (x, y)
    return first


def draw(marks, plan, t, sprite=None):
    """The cut's marks at time t onto `marks` (flows, the moving thing, its label and the labels), in the plan's
    pixels - over its pictures (frame_function) or over a generated video's frame (studio2/video.py)."""
    w, h, seconds = marks.w, marks.h, plan['seconds']
    fade = ramp(t, 0, FLOW_FADE_S)
    for pts, colour in plan['flows']:
        marks.flow(pts, t, colour or (25, 140, 255), alpha=0.9 * fade)
    element = plan['element']
    if element and element['path']:
        ea, eb = element['at'][0] * seconds, element['at'][1] * seconds
        k = ramp(t, ea, eb) if eb > ea else float(t >= ea)
        if element['drawn'] in ('sprite', 'picture'):   # a thing in the scene: there from the start (a picture: painted in)
            visible = 1.0
        else:   # a speck appears for its trip, stays a moment where it lands, then fades
            visible = 1.0 if ea <= t <= eb + 0.15 else (ramp(t, ea - 0.2, ea) if t < ea else 1 - ramp(t, eb + 0.15, eb + 0.45))
        p = along(element['path'], k)
        if element['drawn'] == 'picture':
            pass   # painted in the pictures: the crossing moves it, its label rides its path
        elif element['drawn'] == 'sprite' and sprite is not None:
            marks.sprite(sprite, p, visible)
        else:
            specks = specks_at(element, t, seconds, h)
            for q, alpha in specks:
                marks.speck(q, element['colour'], alpha, ring=len(specks) == 1, r=(element.get('size') or 0) * h / 2 or None)
            p, visible = specks[0] if specks else (p, 0.0)
        if element.get('label') and visible > 0:
            spot = moving_spot(p, element['label'], plan['font'], w, h, plan['safe'], plan['fixed'])
            marks.label(element['label'], p, spot, plan['font'], visible * ramp(t, ea, ea + LABEL_FADE_S))
    for word, anchor, spot, share, present in plan['labels']:
        if present > 0:
            marks.label(word, anchor, spot, plan['font'], present * ramp(t, share * seconds, share * seconds + LABEL_FADE_S))
    return marks


def plans_of(path, cut, located, w, h, frames, body=None):
    """{frame: plan} of each picture the cut is made of, from its located places (locate.places_of: `places` the first's,
    `edits` the others')."""
    out = {'first': plan_of(path, cut, located['places'], w, h, frames, body)}
    for frame, places in (located.get('edits') or {}).items():   # an edit moves parts: flows, path and focus are the first's
        out[frame] = plan_of(path, cut, {**located['places'], 'parts': places['parts']}, w, h, frames, body)
    return out


def _resample(points, n=None):
    n = n or WAY_POINTS
    return [along(points, k / (n - 1)) for k in range(n)] if len(points) >= 2 else list(points)


def _lerp(p, q, k):
    return (p[0] + (q[0] - p[0]) * k, p[1] + (q[1] - p[1]) * k)


def lerp_plan(a, b, k):
    """The plan between two located plans of one cut: label anchors, spots and presence, flow ways and the moving path
    followed (two pictures crossing, or two frames of a video)."""
    if k <= 0 or b is None:
        return a
    if k >= 1:
        return b
    out = dict(a)
    if [l[0] for l in a['labels']] == [l[0] for l in b['labels']]:
        out['labels'] = [(w, _lerp(an, bn, k), _lerp(sa, sb, k), share, pa + (pb - pa) * k)
                         for (w, an, sa, share, pa), (_w, bn, sb, _s, pb) in zip(a['labels'], b['labels'])]
    if len(a['flows']) == len(b['flows']):
        out['flows'] = [([_lerp(p, q, k) for p, q in zip(_resample(pa), _resample(pb))], colour) for (pa, colour), (pb, _c) in zip(a['flows'], b['flows'])]
    if a.get('element') and b.get('element') and a['element']['path'] and b['element']['path']:
        out['element'] = {**a['element'], 'path': [_lerp(p, q, k) for p, q in zip(_resample(a['element']['path']), _resample(b['element']['path']))]}
    return out


def plan_at(plans, t):
    """The plan at time t: the first picture's until its pictures cross, the last's after, followed between them on the
    crossing's own schedule (through the middle's at its half) - a label stays on its part while the part moves."""
    first = plans['first']
    if not first.get('cross') or 'last' not in plans:
        return first
    a, b = first['cross'][0] * first['seconds'], first['cross'][1] * first['seconds']
    if 'middle' not in plans:
        return lerp_plan(first, plans['last'], ramp(t, a, b) if b > a else float(t >= a))
    half = (a + b) / 2
    return lerp_plan(first, plans['middle'], ramp(t, a, half)) if t < half else lerp_plan(plans['middle'], plans['last'], ramp(t, half, b))


def frame_function(plan, pictures, w, h, plans=None):
    first = cover(Image.open(pictures['first']).convert('RGB'), w, h)
    middle = cover(Image.open(pictures['middle']).convert('RGB'), w, h) if 'middle' in pictures else None
    last = cover(Image.open(pictures['last']).convert('RGB'), w, h) if 'last' in pictures else None
    sprite = None
    if 'sprite' in pictures:
        raw = key_out(Image.open(pictures['sprite']))
        size = ((plan['element'] or {}).get('size') or SPRITE_H) * h / max(1, raw.height)
        sprite = raw.resize((max(1, round(raw.width * size)), max(1, round(raw.height * size))), Image.LANCZOS)
    edge = change_edge(first, last) if plan['outline'] and last is not None else None
    seconds = plan['seconds']
    a, b = (plan.get('cross') or plan['at'])[0] * seconds, (plan.get('cross') or plan['at'])[1] * seconds
    cam = plan['camera']

    def base(t):
        if last is None or not plan.get('cross'):
            return first
        if middle is None:
            return Image.blend(first, last, ramp(t, a, b)) if b > a else (last if t >= a else first)
        half = (a + b) / 2
        return Image.blend(first, middle, ramp(t, a, half)) if t < half else Image.blend(middle, last, ramp(t, half, b))

    def frame(t):
        z = camera_zoom(cam, t, seconds)
        now = plan_at(plans, t) if plans else plan
        if cam['move'] == 'travel':   # the picture rides in; the marks follow their parts at their own size
            return ride_frame(base(t), draw(Marks(w, h), follow_zoom(now, z, *plan['focus'], w, h), t, sprite), z, *plan['focus'])
        marks = draw(Marks(w, h), now, t, sprite)
        if edge is not None:
            marks.mask(edge, plan['outline'], ramp(t, b, b + 0.4))
        return zoom(marks.onto(base(t)), z, *plan['focus'])
    return frame


def code_sha():
    return file_hash(Path(__file__))


def render(project, cut_id, force=False):
    """The cut's clip from its picked pictures and judged places (cached by what it is made from)."""
    from studio import decisions
    from .cuts import cut_sha
    from .frames import cut_of
    from .locate import places_of
    path = project_dir(project)
    decisions.require(path, 'cut_preview')
    body = decisions.envelope(path, 'cuts')['body']
    cut = cut_of(body, cut_id)
    data = load_project(path)
    w, h = data['output']['width'], data['output']['height']
    shot = load_shot(path, cut_id)
    frames = shot['duration_frames']
    pictures = _pictures(path, cut, w, h)
    places = places_of(path, cut)
    if places is None:
        raise StudioError('PLACES_UNLOCATED', f'{cut_id}: its marks are not located on its picked first picture', recovery=f'studio2 locate --cut {cut_id}')
    if not places['ok']:
        off = '; '.join(f"{m['mark']}: {m['why']}" for m in places['check'] if not m['ok'])
        raise StudioError('PLACES_OFF', f'{cut_id}: the overlay judge found marks off ({off})', recovery=f'studio2 locate --cut {cut_id} --reason "..."')
    key = stable_hash({'cut': cut_sha(cut), 'pictures': {k: file_hash(v) for k, v in pictures.items()}, 'places': places['places'],
                       'edits': places.get('edits') or {},
                       'code': code_sha(), 'size': [w, h], 'frames': frames, 'label': label_height_share(body),
                       'band': caption_band(path, w, h)})[:16]
    folder = shot_path(path, cut_id).parent / 'composite' / key
    manifest = folder / 'clip.json'
    if manifest.is_file() and not force and read_json(manifest).get('status') == 'complete':
        _name(path, cut_id, key, cut_sha(cut))
        return {**read_json(manifest), 'cached': True}
    folder.mkdir(parents=True, exist_ok=True)
    plans = plans_of(path, cut, places, w, h, frames, body)
    plan = plans['first']
    frame = frame_function(plan, pictures, w, h, plans)
    clip = folder / 'clip.mp4'
    from studio.timings import timed
    with timed('render', f'composite {cut_id}'):
        writer = subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{w}x{h}', '-r', '30',
                                   '-i', '-', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-movflags', '+faststart', str(clip)],
                                  stdin=subprocess.PIPE)
        for i in range(frames):
            writer.stdin.write(frame(i / 30).tobytes())
        writer.stdin.close()
        writer.wait()
    if writer.returncode != 0:
        raise StudioError('RENDER_FAILED', f'{cut_id}: ffmpeg exit {writer.returncode}')
    font = plan['font']
    row = {'schema_version': 1, 'status': 'complete', 'cut_id': cut_id, 'cut_sha': cut_sha(cut), 'key': key, 'frame_count': frames, 'fps': 30,
           'width': w, 'height': h, 'clip_path': str(clip.relative_to(path)), 'clip_sha256': file_hash(clip), 'generated_pictures': True,
           'pictures': {k: {'path': str(v.relative_to(path)), 'sha256': file_hash(v)} for k, v in pictures.items()},
           'label': {'height_share': label_height_share(body), 'font_size': font.size, 'ink_px': ink_height('가나다', font)},
           'made_at': now(), 'code_sha256': code_sha()}
    write_json(manifest, row)
    _name(path, cut_id, key, cut_sha(cut))
    return {**row, 'cached': False, 'artifacts': [str(clip), str(manifest)]}


def _name(path, cut_id, key, sha):
    """The shot names the clip made for it (route.composite), as a build names its version."""
    from studio.project import validate_shot
    file = shot_path(path, cut_id)
    shot = read_json(file)
    if (shot.get('route') or {}).get('composite') == {'key': key, 'cut_sha': sha}:
        return
    shot['route']['composite'] = {'key': key, 'cut_sha': sha}
    validate_shot(shot)
    write_json(file, shot)


def stills(project, cut_id, shares=(0.1, 0.4, 0.7, 0.95)):
    """Frames of the made clip at shares of its length (what to open and look at), as one strip."""
    from studio.edit import latest_composite
    path = project_dir(project)
    shot = load_shot(path, cut_id)
    made = latest_composite(path, shot, shot['duration_frames'])
    if made is None:
        raise StudioError('RENDER_REQUIRED', f'{cut_id}: no clip made', recovery=f'studio2 motion render --cut {cut_id}')
    out = made['manifest_path'].parent / 'strip.jpg'
    tiles = []
    for share in shares:
        t = share * shot['duration_frames'] / 30
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{t:.3f}', '-i', str(made['clip']), '-frames:v', '1', '-vf', 'scale=360:640',
                              '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True, check=True).stdout
        tiles.append(Image.frombytes('RGB', (360, 640), raw))
    strip = Image.new('RGB', (360 * len(tiles), 640))
    for n, tile in enumerate(tiles):
        strip.paste(tile, (360 * n, 0))
    strip.save(out, quality=88)
    return {'strip': str(out), 'artifacts': [str(out)]}


def register_commands(subparsers):
    parser = subparsers.add_parser('motion', help="A cut's free motion and graphics: its pictures moved and marked into its clip")
    commands = parser.add_subparsers(dest='motion_command', required=True)
    p = commands.add_parser('render'); p.add_argument('--project', required=True); p.add_argument('--cut', required=True)
    p.add_argument('--force', action='store_true')
    p.set_defaults(handler=lambda a: render(a.project, a.cut, a.force))
    p = commands.add_parser('stills', help='four frames of the made clip, one strip, to look at')
    p.add_argument('--project', required=True); p.add_argument('--cut', required=True)
    p.set_defaults(handler=lambda a: stills(a.project, a.cut))
    return commands
