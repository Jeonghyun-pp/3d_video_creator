"""Shot titles: 2D text composited in the edit (Pillow), never placed in the 3D scene.

A title placed in the world reads only for the camera it was placed for: re-time or re-frame the move and it
drifts off screen (samsung s01 v0018: 78 % above the frame for frames 1-30, measured). A 2D title is anchored to
the frame, so it is legible for any camera, and it is checked against the title safe rectangle every frame.

  anim 'hold'    constant size
  anim 'recede'  shrinks toward its anchor along a monotone (PCHIP) scale curve - the "title flies into the
                 scene" move of explainer reels, without a 3D object
  count          a number in the text ('{n}') runs from count.from to count.to (eased) - "80 columns", "2026"
  box            a filled panel behind the text (a callout, a lower third)
The master is rasterized once, large; each frame is a premultiplied, fractional-box resize of it placed so the
ink's alpha centroid lands on the anchor (sub-pixel, so a slowly shrinking title does not shimmer).
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .blender_ops.camera_rig_core import monotone_cubic   # pure math, no Blender
from .common import StudioError

TITLE_SAFE = (.11, .14, .89, .65)          # critical text zone of a 9:16 feed (platform UI covers the rest)
RECEDE_CURVE = (1.0, .80, .69, .59, .48, .38, .24, .16, .07)   # measured from the reference title (scale per 1/8 of the span)
MASTER_SCALE = 2.0                          # master raster at 2x the largest frame size: every frame is a downscale
LINE_SPACING = 1.12


def safe_rect(style, width, height):
    value = style.get('title_safe_rect_normalized', TITLE_SAFE)
    if len(value) != 4 or not 0 <= value[0] < value[2] <= 1 or not 0 <= value[1] < value[3] <= 1:
        raise StudioError('INVALID_STYLE', 'Invalid title_safe_rect_normalized')
    return tuple(v * (width if i % 2 == 0 else height) for i, v in enumerate(value))


def face(font_path, weight, size):
    """The face whose style name is `weight` (ExtraBold, Heavy, ...): searched in the font's collection, then in the
    other font files of its folder (a family shipped as one file per weight). No weight: the pinned file itself.
    A weight the family does not have is an error, not a silent fallback."""
    if not weight:
        return ImageFont.truetype(str(font_path), size)
    from .common import font_faces
    wanted, seen = weight.replace(' ', '').lower(), []
    files = [Path(font_path)] + [f for f in font_faces(Path(font_path).parent) if f != Path(font_path)]
    for file in files:
        for index in range(64):
            try:
                font = ImageFont.truetype(str(file), size, index=index)
            except OSError:
                break
            family, style_name = font.getname()
            seen.append(style_name)
            if style_name.replace(' ', '').lower() == wanted and not family.startswith('.'):
                return font
    raise StudioError('FONT_UNAVAILABLE', f'{Path(font_path).parent.name}/{Path(font_path).name} has no {weight!r} face (has: {sorted(set(seen))})')


def scale_at(title, frame):
    """Scale of the title at a shot frame (1 = width_frac of the frame)."""
    if title.get('anim', 'hold') == 'hold':
        return 1.0
    curve = title.get('scale_curve') or RECEDE_CURVE
    span = max(1, title['end_frame'] - title['start_frame'] - 1)
    x = min(1.0, max(0.0, (frame - title['start_frame']) / span))
    # monotone_cubic needs non-decreasing values: fit the negated (shrinking) curve
    f = monotone_cubic([(i / (len(curve) - 1), -v) for i, v in enumerate(curve)])
    return -f(x)


def alpha_at(title, frame):
    fade_in, fade_out = title.get('fade_in_frames', 0), title.get('fade_out_frames', 0)
    a = 1.0
    if fade_in:
        a = min(a, (frame - title['start_frame'] + 1) / (fade_in + 1))
    if fade_out:
        a = min(a, (title['end_frame'] - frame) / (fade_out + 1))
    return max(0.0, min(1.0, a))


def _number(value, decimals):
    return f'{value:,.{decimals}f}'


def text_at(title, frame):
    """The title's text at a frame: '{n}' replaced by its count, eased (smoothstep) from count.from to count.to over
    count.start_frame..count.end_frame (default the title's own span)."""
    count = title.get('count')
    if not count:
        return title['text']
    a, b = count.get('start_frame', title['start_frame']), count.get('end_frame', title['end_frame'] - 1)
    u = min(1.0, max(0.0, (frame - a) / max(1, b - a)))
    u = u * u * (3 - 2 * u)
    return title['text'].replace('{n}', _number(count['from'] + (count['to'] - count['from']) * u, count.get('decimals', 0)))


def widest_text(title):
    """The text the font is sized for: a counter sized for its longest value, so it never jumps as it counts."""
    count = title.get('count')
    if not count:
        return title['text']
    return max((title['text'].replace('{n}', _number(v, count.get('decimals', 0))) for v in (count['from'], count['to'])), key=len)


def state(title, frame):
    """None when inactive, else what the frame shows (also the overlay cache signature)."""
    if not title['start_frame'] <= frame < title['end_frame']:
        return None
    return {'title_id': title['title_id'], 'scale': round(scale_at(title, frame), 6), 'alpha': round(alpha_at(title, frame), 4),
            **({'text': text_at(title, frame)} if title.get('count') else {})}


class Masters:
    """One Master per title (and per counted text): built on first use."""

    def __init__(self, titles, font_path, width):
        self.titles, self.font_path, self.width, self.cache = {t['title_id']: t for t in titles}, font_path, width, {}

    def get(self, title, frame):
        text = text_at(title, frame)
        key = (title['title_id'], text)
        if key not in self.cache:
            self.cache[key] = Master(title, self.font_path, self.width, text)
        return self.cache[key]


class Master:
    """One large raster of a title with its ink box and alpha centroid (master pixels)."""

    def __init__(self, title, font_path, width, text=None):
        lines = (text or title['text']).split('\n')
        probe = face(font_path, title.get('weight'), 200)
        ink = max(probe.getlength(line) for line in widest_text(title).split('\n'))
        size = max(10, round(200 * title.get('width_frac', .70) * width * MASTER_SCALE / max(1.0, ink)))
        font = face(font_path, title.get('weight'), size)
        pad = round(size * .25)
        line_h = round(size * LINE_SPACING)
        w = round(max(font.getlength(line) for line in lines)) + 2 * pad
        h = line_h * len(lines) + 2 * pad
        colour = tuple(round(max(0, min(1, c)) * 255) for c in title.get('color_srgb', (1, 1, 1)))
        box_spec = title.get('box')
        if box_spec:   # the panel is part of the title: it scales and fades with it
            extra = round(size * box_spec.get('padding_frac', 0.35))
            w, h, pad = w + 2 * extra, h + 2 * extra, pad + extra
        image = Image.new('RGBA', (w, h))
        draw = ImageDraw.Draw(image)
        if box_spec:
            fill = tuple(round(max(0, min(1, c)) * 255) for c in box_spec.get('fill_srgb', (0, 0, 0)))
            draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=round(size * box_spec.get('radius_frac', 0.2)),
                                   fill=(*fill, round(255 * box_spec.get('opacity', 1.0))))
        for i, line in enumerate(lines):
            draw.text((w / 2, pad + i * line_h), line, font=font, fill=(*colour, 255), anchor='mt')
        alpha = image.getchannel('A')
        box = alpha.getbbox()
        if box is None:
            raise StudioError('TEXT_OVERFLOW', f"title {title['title_id']} has no visible glyphs")
        total = sx = sy = 0
        data = alpha.load()
        for y in range(box[1], box[3]):
            for x in range(box[0], box[2]):
                a = data[x, y]
                if a:
                    total += a; sx += a * (x + .5); sy += a * (y + .5)
        self.image = image.convert('RGBa')          # premultiplied: resampling never bleeds black into the edges
        self.ink = box
        self.centroid = (sx / total, sy / total)
        self.unit = 1 / MASTER_SCALE                # master pixels -> frame pixels at scale 1
        self._padded = {}

    def padded(self, margin):
        """The master with `margin` transparent pixels on every side: a frame's sampling box may reach past the
        ink by a pixel of the (small) output, which is many master pixels."""
        if margin not in self._padded:
            image = Image.new('RGBa', (self.image.width + 2 * margin, self.image.height + 2 * margin))
            image.paste(self.image, (margin, margin))
            self._padded[margin] = image
        return self._padded[margin]


def place(master, title, scale, width, height):
    """(layer RGBA at integer offset, (x, y) offset, ink bbox in frame pixels as floats)."""
    k = master.unit * scale
    ax, ay = title.get('anchor', (.5, .49))
    x0 = ax * width - master.centroid[0] * k        # frame position of master pixel (0, 0)
    y0 = ay * height - master.centroid[1] * k
    ink = (x0 + master.ink[0] * k, y0 + master.ink[1] * k, x0 + master.ink[2] * k, y0 + master.ink[3] * k)
    left, top = math.floor(ink[0]) - 1, math.floor(ink[1]) - 1
    w, h = math.ceil(ink[2]) + 1 - left, math.ceil(ink[3]) + 1 - top
    margin = math.ceil(2 / k) + 2
    box = ((left - x0) / k + margin, (top - y0) / k + margin, (left + w - x0) / k + margin, (top + h - y0) / k + margin)
    layer = master.padded(margin).resize((max(1, w), max(1, h)), Image.BOX, box=box).convert('RGBA')
    return layer, (left, top), ink


def render_titles(titles, frame, masters, width, height, safe, image, shot_id):
    """Composite the active titles of `frame` onto image; returns text boxes for the manifest."""
    boxes = []
    for title in titles:
        now = state(title, frame)
        if now is None or now['alpha'] <= 0:
            continue
        layer, offset, ink = place(masters.get(title, frame), title, now['scale'], width, height)
        if ink[0] < safe[0] - .5 or ink[1] < safe[1] - .5 or ink[2] > safe[2] + .5 or ink[3] > safe[3] + .5:
            raise StudioError('TITLE_OUT_OF_SAFE', f"{shot_id}: title {title['title_id']} frame {frame} ink "
                              f"{[round(v, 1) for v in ink]} leaves the title safe rect {[round(v) for v in safe]}",
                              recovery='lower width_frac or move the anchor toward the frame centre')
        if now['alpha'] < 1:
            layer.putalpha(layer.getchannel('A').point(lambda a, m=now['alpha']: round(a * m)))
        canvas = Image.new('RGBA', image.size)
        canvas.paste(layer, offset)
        image.alpha_composite(canvas)
        boxes.append({'kind': 'title', 'shot_id': shot_id, 'title_id': title['title_id'], 'text': text_at(title, frame),
                      'frame': frame, 'bbox': [math.floor(ink[0]), math.floor(ink[1]), math.ceil(ink[2]), math.ceil(ink[3])]})
    return boxes
