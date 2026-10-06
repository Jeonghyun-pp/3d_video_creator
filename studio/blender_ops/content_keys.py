"""Which keys of a graphic, a title and a fill-brief item are read for its kind - pure Python (no bpy).

The schema declares every key these objects may hold; whether one is read depends on the kind: an outline follows a
target and has no anchors, only a dimension has ticks and text, a screen arrow is drawn from points_2d, a held title
has no scale curve, a density fill has no count. A key set where its kind ignores it is a silent no-op, so shot
validation and fill lint refuse it. Checked against the code: graphics by a Blender smoke on recording specs
(tests/studio/content_keys_smoke.py), titles and fill layouts by unit tests that run their pure readers.
"""
from __future__ import annotations

GRAPHIC_COMMON = {'graphic_id', 'kind', 'space', 'start_frame', 'end_frame', 'time_binding', 'color_srgb', 'opacity', 'draw_on_s'}
GRAPHIC_WORLD = {'radius_m'}
GRAPHIC_WORLD_BY_KIND = {
    'outline': {'target'},
    'arrow': {'anchors', 'head_m'},
    'dimension': {'anchors', 'tick_m', 'text'},
    'highlight': {'anchors'},
    'draw_line': {'anchors'},
}
GRAPHIC_SCREEN_ARROW = {'points_2d', 'shaft_frac', 'head_ratio', 'fade_frames'}   # space 'screen' draws arrows only

TITLE_ALWAYS = {'title_id', 'text', 'start_frame', 'end_frame', 'anim', 'fade_in_frames', 'fade_out_frames', 'anchor', 'weight',
                'width_frac', 'color_srgb'}
TITLE_BY_ANIM = {'hold': set(), 'recede': {'scale_curve'}}

FILL_ITEM_ALWAYS = {'item_id', 'role', 'element', 'why', 'source', 'layout', 'facing', 'height_m'}
FILL_ITEM_BY_LAYOUT = {
    'along_edge': {'edge', 'count', 'pitch_m'},
    'line_across': {'at', 'count', 'pitch_m'},
    'grid': {'count', 'pitch_m'},
    'cluster': {'at', 'count'},
    'density': {'density_per_100m2'},
}


def graphic_reads(graphic):
    if graphic.get('space') == 'screen':
        return GRAPHIC_COMMON | GRAPHIC_SCREEN_ARROW
    return GRAPHIC_COMMON | GRAPHIC_WORLD | GRAPHIC_WORLD_BY_KIND.get(graphic.get('kind'), set())


def title_reads(title):
    return TITLE_ALWAYS | TITLE_BY_ANIM.get(title.get('anim', 'hold'), set())


def fill_item_reads(item):
    return FILL_ITEM_ALWAYS | FILL_ITEM_BY_LAYOUT.get(item.get('layout'), set())


def content_unread(shot):
    out = []
    for g in shot.get('graphics') or []:
        out += [f"graphics/{g.get('graphic_id')}/{k} ({g.get('space', 'world')} {g.get('kind')} does not read it)" for k in sorted(set(g) - graphic_reads(g))]
    for t in shot.get('titles') or []:
        out += [f"titles/{t.get('title_id')}/{k} (anim {t.get('anim', 'hold')} does not read it)" for k in sorted(set(t) - title_reads(t))]
    for level in (shot.get('fill_brief') or {}).get('levels', []):
        for item in level.get('items', []):
            out += [f"fill_brief/{level.get('level_id')}/{item.get('item_id', item.get('element'))}/{k} (layout {item.get('layout')} does not read it)"
                    for k in sorted(set(item) - fill_item_reads(item))]
    return out
