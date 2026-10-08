"""Decals: words on a surface in the scene - a station sign, a red inspection tag on a column, a plate on a machine
(shot.scene.decals). The rule stays that words never live in lit 3D text or in generated pixels (SKILL #2: 3D text
bloomed and leaked into control passes; generation garbles letters); a decal is the words drawn here, in the project's
pinned font, as an image on a flat card: lit with the scene, role 'decal' (out of every control pass a model reads),
and kept from Blender in any generated take (generative/keep.py).

Why (2026-10-08, archcut3): the reference's red tag "현장 점검" hangs on a column; the only way to show it was bare 2D
red letters in the title layer, which did not sit on the column or move with it.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .common import StudioError, file_hash, safe_path, stable_hash

PX_PER_M = 400          # raster density of a drawn decal (a 0.5 m tag is 200 px wide: sharp at a 1080 frame's close-up)
MAX_PX = 2048


def _rgb(srgb):
    return tuple(round(max(0.0, min(1.0, c)) * 255) for c in srgb)


def draw(row, font_path, out_dir):
    """The decal image for a text row (cached by content): text centred on a filled card with padding."""
    from .titles import face
    width_m = row['size_m']
    key = stable_hash({k: row.get(k) for k in ('text', 'size_m', 'weight', 'color_srgb', 'fill_srgb', 'padding_frac', 'aspect')}
                      | {'font': file_hash(font_path)})[:16]
    path = Path(out_dir) / f'{key}.png'
    if path.is_file():
        return path
    width = min(MAX_PX, max(64, round(width_m * PX_PER_M)))
    lines = row['text'].split('\n')
    pad = round(width * row.get('padding_frac', 0.1))
    probe = face(font_path, row.get('weight'), 100)
    ink = max(probe.getlength(line) for line in lines)
    size = max(8, int(100 * (width - 2 * pad) / max(1.0, ink)))
    font = face(font_path, row.get('weight'), size)
    line_h = round(size * 1.15)
    height = round(width * row['aspect']) if row.get('aspect') else line_h * len(lines) + 2 * pad
    fill = row.get('fill_srgb', (0.86, 0.1, 0.08))   # null: bare letters on a transparent card
    image = Image.new('RGBA', (width, height), (*_rgb(fill), 255) if fill is not None else (0, 0, 0, 0))
    canvas = ImageDraw.Draw(image)
    top = (height - line_h * len(lines)) / 2
    for i, line in enumerate(lines):
        canvas.text((width / 2, top + i * line_h), line, font=font, fill=(*_rgb(row.get('color_srgb', (1, 1, 1))), 255), anchor='mt')
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def resolve(path, rows, style):
    """shot.scene.decals -> rows the Blender layout places: each with its image (drawn here, or a project image) and
    its sha256 (in the layout hash, so a changed word is a changed scene)."""
    from .edit import font_file
    out = []
    for row in rows or []:
        if ('text' in row) == ('image' in row):
            raise StudioError('INPUT_INVALID', f"decal {row['id']}: give text (drawn in the project font) or image, not both")
        if 'text' in row:
            image = draw(row, font_file(style or {}, path), path / 'decals')
        else:
            image = safe_path(path, row['image'])
            if not image.is_file():
                raise StudioError('INPUT_INVALID', f"decal {row['id']}: {row['image']} is not a file in the project")
        with Image.open(image) as picture:
            aspect = picture.height / picture.width
        out.append({**row, 'image_path': str(image), 'sha256': file_hash(image), 'aspect': round(aspect, 5)})
    return out
