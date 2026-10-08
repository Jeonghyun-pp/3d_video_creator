"""Charts in the edit layer (shot.charts): horizontal bars that grow in, each row a label, a bar and its value - the
data side of an explainer ("50 of 80 columns", budgets, counts), drawn in 2D like titles, never in the 3D scene.

Why (2026-10-08): the engine had arrows, outlines and titles but no way to show a quantity; data reels and most
explainers put one on screen. Bars grow with an ease-out over grow_frames (staggered a few frames per row), the value
counts with them, and the chart is checked against the title safe rect every frame like a title.
"""
from __future__ import annotations

from PIL import ImageDraw

from .common import StudioError

STAGGER = 4   # frames between rows starting to grow


def _rgb(srgb, default):
    return tuple(round(max(0.0, min(1.0, c)) * 255) for c in (srgb or default))


def _ease_out(u):
    u = min(1.0, max(0.0, u))
    return 1 - (1 - u) ** 3


def render_charts(charts, frame, font_at, width, height, safe, image, shot_id):
    """Draw the active charts of a frame onto image; returns their boxes. font_at(size, weight) gives the pinned font."""
    boxes = []
    for chart in charts:
        if not chart['start_frame'] <= frame < chart['end_frame']:
            continue
        x0, y0 = chart.get('anchor', (0.14, 0.2))
        x0, y0 = x0 * width, y0 * height
        w = chart.get('width_frac', 0.7) * width
        row = chart.get('row_frac', 0.05) * height
        font = font_at(max(10, round(row * 0.55)), chart.get('weight'))
        top = max(item['value'] for item in chart['items'])
        scale = chart.get('max', top)
        label_w = max(font.getlength(item['label']) for item in chart['items']) + row * 0.3
        value_w = font.getlength(f"{scale:,.0f}{chart.get('unit', '')}") + row * 0.3
        bar_w = w - label_w - value_w
        if bar_w < w * 0.2:
            raise StudioError('TEXT_OVERFLOW', f"{shot_id}: chart {chart['chart_id']}: labels leave no room for bars; widen it or shorten the labels")
        box = [x0, y0, x0 + w, y0 + row * len(chart['items'])]
        if box[0] < safe[0] - .5 or box[1] < safe[1] - .5 or box[2] > safe[2] + .5 or box[3] > safe[3] + .5:
            raise StudioError('TITLE_OUT_OF_SAFE', f"{shot_id}: chart {chart['chart_id']} {[round(v) for v in box]} leaves the title safe rect "
                              f"{[round(v) for v in safe]}", recovery='move its anchor or narrow it')
        draw = ImageDraw.Draw(image)
        text = (*_rgb(chart.get('text_srgb'), (1, 1, 1)), 255)
        for i, item in enumerate(chart['items']):
            u = _ease_out((frame - chart['start_frame'] - i * STAGGER) / max(1, chart.get('grow_frames', 18)))
            y = y0 + i * row
            draw.text((x0, y + row / 2), item['label'], font=font, fill=text, anchor='lm')
            length = bar_w * item['value'] / scale * u
            colour = (*_rgb(item.get('color_srgb') or chart.get('color_srgb'), (0.95, 0.75, 0.2)), 255)
            if length >= 1:
                draw.rounded_rectangle((x0 + label_w, y + row * 0.18, x0 + label_w + length, y + row * 0.82), radius=row * 0.12, fill=colour)
            draw.text((x0 + label_w + length + row * 0.2, y + row / 2), f"{item['value'] * u:,.0f}{chart.get('unit', '')}", font=font, fill=text, anchor='lm')
        boxes.append({'kind': 'chart', 'shot_id': shot_id, 'chart_id': chart['chart_id'], 'frame': frame, 'bbox': [round(v) for v in box]})
    return boxes
