"""Frame probe, the pure part (no bpy): which frames to look at, and what a frame's class pixels say about it.

The probe (frame_probe.py) renders an id pass through the real scene camera - clip planes, lens shift and all - and
counts pixels per class: key parts (one class each), subject, support (everything else that renders) and background.
It looks at the picture, not at how the scene was made, so it judges an authored script, a declared scene and a
workbench edit the same way.

Codes:
  FRAME_EMPTY            hard   a sampled frame shows (almost) nothing (a subject out of frame for a while is the
                                shot's choice - a reveal, a dive - and is judged by FRAME_SUBJECT_SMALL over the shot)
  FRAME_NEAR_CLIP_CUT    hard   the near clip plane removes a visible share of subject / key pixels
  KEY_PART_INVISIBLE     hard on explain shots, a warning on mood shots: a key part never shows in its window
  FRAME_EDGE_CUT         softenable: a key part touches the frame border in more than a third of its frames
  FRAME_SUBJECT_SMALL    softenable: the subject's median share of the frame is small
  KEY_PART_SMALL         softenable: a key part shows, but never at a readable size
  DETAIL_NOT_SHOWN       softenable (broken): a detail the shot explains (shot.screen.details) never shows at its
                         declared on-screen thickness - visible pixels over the long side of what shows, in output
                         pixels, occlusion included; hidden altogether is 0. Being in the scene is not being shown
                         (floor_noise, 2026-10-09: a 3 cm buffer layer spanned the frame and was 10 px thick)
  MUST_SEE_SHORT         softenable (taste): such a detail shows, a little thinner than declared - within
                         SHORTFALL_SHARE of its min_px (px_verdict): said, not refused
  FRAME_MODEL_EDGE       softenable (taste): void - background where the camera looks below the horizon - on three or more
                         frame borders: a doll's house in empty space (sky above a street is world, not void), unless
                         the shot declares screen.diorama
Key parts the shot declares (shot.key_parts) are judged on all key-part codes; parts the probe infers (a rig's look
target, what an approved storyboard kept in frame) only on KEY_PART_INVISIBLE - they must show, their size is the shot's.
"""
from __future__ import annotations

import statistics

# Initial values, set from the robot_joint and jet shots; measure them on the corpus and record changes in
# docs/BUILD_REPORT.md (the thresholds are the rule's numbers, the codes above are the rule).
THRESHOLDS = {
    'empty_opaque_share': 0.01,        # FRAME_EMPTY: less than 1 % of the frame renders anything
    'near_clip_cut_share': 0.005,      # FRAME_NEAR_CLIP_CUT: the near plane removes >= 0.5 % of the frame's subject/key pixels
    'key_min_px': 20,                  # KEY_PART_INVISIBLE: fewer pixels than this (at the probe's 256 px height) is not seen
    'key_readable_share': 0.004,       # KEY_PART_SMALL: never above 0.4 % of the frame (about a 16 px square at 256 px)
    'edge_frames_share': 1 / 3,        # FRAME_EDGE_CUT: touching the border in more than a third of the window's frames
    'subject_small_share': 0.02,       # FRAME_SUBJECT_SMALL: median subject share under 2 % (the winch fixture, a readable product shot, is 2.9 %)
}
HARD = ('FRAME_EMPTY', 'FRAME_NEAR_CLIP_CUT')
SOFTENABLE = ('FRAME_EDGE_CUT', 'FRAME_SUBJECT_SMALL', 'KEY_PART_SMALL', 'DETAIL_NOT_SHOWN', 'FRAME_MODEL_EDGE', 'MUST_SEE_SHORT')
MODEL_EDGE_SIDES = 3          # FRAME_MODEL_EDGE: background reaches at least this many frame borders ...
MODEL_EDGE_FRAMES = 0.5       # ... in more than this share of the judged frames
# Support share is measured (rows, notes) but not judged: a canyon filling a jet chase is a composition, a bench covering
# a gear is a mistake, and the share alone cannot tell them apart. A rule needs evidence first (docs/BUILD_REPORT.md).
SIDES = ('left', 'right', 'top', 'bottom')


def sample_count(count):
    return 8 if count <= 240 else 12


def pick_frames(count, required=()):
    """Evenly spaced frames over the shot plus the ones that matter (storyboard focus frames, key-part windows, move
    cues), deduplicated and sorted - deterministic for the same inputs."""
    if count <= 0:
        return []
    n = min(count, sample_count(count))
    even = {round(i * (count - 1) / max(1, n - 1)) for i in range(n)}
    return sorted(even | {int(f) for f in required if 0 <= int(f) < count})


def key_windows(key_parts, count):
    """{id: (from, to)} with defaults to the whole shot."""
    return {k['id']: (int(k.get('from_frame', 0)), int(k.get('to_frame', count - 1))) for k in key_parts}


def required_frames(key_parts, count, extra=()):
    """Every key part's window midpoint and ends, plus extra frames (focus, cues)."""
    out = set(extra)
    for lo, hi in key_windows(key_parts, count).values():
        out |= {lo, hi, (lo + hi) // 2}
    return out


LEVELS = (0.0, 0.2140, 1.0)           # linear values that land at sRGB 0, 0.5, 1 under the Standard view transform
SRGB_LEVELS = (0.0, 0.5, 1.0)


def palette():
    """Class colours of the id pass, as (linear, sRGB): support, subject, then key parts (key:<id>, then hide:<id>, in the
    report's summary order). 27 well-separated colours (per channel 0 / 0.5 / 1 in sRGB). Pure, so what reads the saved
    id pictures (studio/reel_flow.py) decodes them by the same table the probe paints them with."""
    import itertools
    combos = list(itertools.product(range(3), repeat=3))
    combos.remove((0, 0, 0)); combos.remove((2, 2, 2))
    order = [(1, 1, 1), (2, 0, 0)] + [c for c in combos if c not in ((1, 1, 1), (2, 0, 0))]
    return [(tuple(LEVELS[i] for i in c), tuple(SRGB_LEVELS[i] for i in c)) for c in order]


def hidden_share(alone, opaque, seen=None, alpha=1.0):
    """The share of a part hidden in front: 1 - what shows / what it covers alone. opaque: its pixels with see-through
    skins drawn solid; seen: with the skins left out (None when none is open); alpha: how clear the skins are (show_scene
    studio_see_alpha, 1 fully clear) - of what a skin covers, 1 - alpha stays hidden. Rule 12 (2026-10-19): see-through
    is the last resort, not a way round the camera."""
    if not alone:
        return None
    shown = opaque + (alpha * max(0, seen - opaque) if seen is not None else 0)
    return round(max(0.0, 1.0 - min(1.0, shown / alone)), 4)


PROBE_HEIGHT_PX = 256   # the id pass's height (frame_probe): one of its pixels is output_height / this output pixels
SHORTFALL_SHARE = 0.15  # a shown part this share or less under its min_px is short, not missing: it reads, a little thin -
                        # said (MUST_SEE_SHORT), never a refusal. User, 2026-10-17: "사소한건 그냥 넘어가도 될것같고" (a piston
                        # 30.4 px against 32 had stopped a whole shot)


def px_verdict(px, need, share=SHORTFALL_SHARE):
    """'ok' when a part shows at least `need` px thick, 'short' when within `share` of it, else 'thin'."""
    if not need or px >= need:
        return 'ok'
    return 'short' if px >= need * (1.0 - share) else 'thin'


def thickness_px(shape, output_size):
    """How thick a class shows, in output pixels - one conservative rule for the build's check (this) and the camera
    (camera_frame.shown_px, which rasterises its visible points on the same grid: raster_shape): the least of
      - the median over the columns it covers of its vertical extent there ('col_v', a share of the frame height),
      - its area over the long side of what shows (a ring face-on, a rail split by what stands in front: the column
        extent spans the hole, the area does not),
      - the short side of what shows.
    Why (2026-10-17): the camera read an engine bell at 34 px where this check, then area over the long side alone, read
    30 and refused the build the camera had accepted; a column measure alone read a face-on ring 4x thicker. Rows from
    before 'col_v': area over the long side and the short side."""
    shape = (shape or {}).get('piece') or shape   # a group whose members stand apart: the member shown best (best_piece)
    if not shape or not shape.get('bbox') or not shape.get('share'):
        return 0.0
    x0, y0, x1, y1 = shape['bbox']
    w, h = (x1 - x0) * output_size[0], (y1 - y0) * output_size[1]
    long_side, short_side = max(w, h), min(w, h)
    if not long_side:
        return 0.0
    area = shape['share'] * output_size[0] * output_size[1]
    out = min(area / long_side, short_side)
    if shape.get('col_v') is not None:
        out = min(out, shape['col_v'] * output_size[1])
    return out


SCATTER_GAP = 1.0   # a group's members stand apart when each is at least its own size (long side) from the nearest other:
                    # twelve valves along a 970 m lock, one chamber's water each - a viewer knows the thing from one of them.
                    # Links of a chain, tiles of a lake touch their neighbours: one thing, judged whole (2026-10-20)


def box_gap(a, b):
    """The distance between two axis-aligned boxes ((min xyz), (max xyz)); 0 when they touch or overlap."""
    return sum(max(0.0, a[0][i] - b[1][i], b[0][i] - a[1][i]) ** 2 for i in range(3)) ** 0.5


def stand_apart(boxes, gap=SCATTER_GAP):
    """Do these members' boxes stand apart (SCATTER_GAP): two or more, each at least `gap` x its own long side from the
    nearest other? Then the group is several of one thing, and a frame that shows one of them shows it (best_piece)."""
    if len(boxes) < 2:
        return False
    for i, a in enumerate(boxes):
        size = max(a[1][k] - a[0][k] for k in range(3))
        if min(box_gap(a, b) for j, b in enumerate(boxes) if j != i) < gap * size:
            return False
    return True


def pieces(cells):
    """The connected pieces (4-neighbours) of a set of grid cells {(column, row)}, largest first."""
    left, out = set(cells), []
    while left:
        start = left.pop()
        piece, todo = {start}, [start]
        while todo:
            c, r = todo.pop()
            for n in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
                if n in left:
                    left.discard(n)
                    piece.add(n)
                    todo.append(n)
        out.append(piece)
    return sorted(out, key=len, reverse=True)


def best_piece(cells, grid, output_size):
    """The probe-format shape (cells_shape) of the piece of `cells` that shows thickest (thickness_px): how a group whose
    members stand apart is read - by the member a frame shows best, not by all of them spread over the frame as one
    thin shape (area over a long side that runs from one to the next). One rule for the build (frame_probe._shapes)
    and the camera (camera_frame.probe, on the same grid)."""
    best, best_px = {'px': 0}, -1.0
    for piece in pieces(cells):
        shape = cells_shape(piece, grid)
        px = thickness_px(shape, output_size)
        if px > best_px:
            best, best_px = shape, px
    return best


def grid_size(output_size):
    """(columns, rows) of the id pass (PROBE_HEIGHT_PX rows, the output's aspect)."""
    return max(16, round(PROBE_HEIGHT_PX * output_size[0] / output_size[1])), PROBE_HEIGHT_PX


def cells_shape(cells, grid):
    """The probe-format shape of a set of occupied cells {(column, row)} on a grid (columns, rows): {share, bbox,
    col_v} - what frame_probe._shapes gives a class from its id pass (no numpy)."""
    if not cells:
        return {'px': 0}
    gw, gh = grid
    spans = {}
    for c, r in cells:
        lo, hi = spans.get(c, (r, r))
        spans[c] = (min(lo, r), max(hi, r))
    extents = sorted(hi - lo + 1 for lo, hi in spans.values())
    n = len(extents)
    median = extents[n // 2] if n % 2 else (extents[n // 2 - 1] + extents[n // 2]) / 2
    cols, rows = [c for c, _r in cells], [r for _c, r in cells]
    return {'px': len(cells), 'share': len(cells) / (gw * gh), 'col_v': median / gh,
            'bbox': [min(cols) / gw, min(rows) / gh, (max(cols) + 1) / gw, (max(rows) + 1) / gh]}


RASTER_SUB = 2   # sub-cells per cell side when points are rasterised: a cell counts when half its sub-cells are hit


def raster_cells(screen, grid, sub=RASTER_SUB):
    """The grid cells screen points (x, y as shares of the frame, 0,0 top-left) cover, as a render covers a pixel: the
    points are dropped into sub x sub finer cells and a cell counts when at least half of its fine cells are hit - a cell
    a part only grazes does not count, as a render whose pixel centre it misses (2026-10-17: counting any cell a point
    touched read a 32 px rod as 43). Points off the frame are left out."""
    gw, gh = grid
    fine = {(min(gw * sub - 1, int(x * gw * sub)), min(gh * sub - 1, int(y * gh * sub)))
            for x, y, *_ in screen if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0}
    hits = {}
    for c, r in fine:
        hits[(c // sub, r // sub)] = hits.get((c // sub, r // sub), 0) + 1
    return {cell for cell, n in hits.items() if n * 2 >= sub * sub}


def raster_fill(screen, grid, sub=RASTER_SUB):
    """(fine cells hit, points in frame): how densely points cover what they fall on (camera_frame.shown_px samples more
    when each fine cell holds too few)."""
    gw, gh = grid
    inside = [(x, y) for x, y, *_ in screen if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0]
    return len({(min(gw * sub - 1, int(x * gw * sub)), min(gh * sub - 1, int(y * gh * sub))) for x, y in inside}), len(inside)


def column_thickness(mask):
    """cells_shape's col_v from a numpy bool mask (rows, columns): the median over the columns it covers of its
    vertical extent there, as a share of its height."""
    import numpy as np
    cols = np.nonzero(mask.any(axis=0))[0]
    if not len(cols):
        return 0.0
    sub = mask[:, cols]
    top = sub.argmax(axis=0)
    bottom = sub.shape[0] - 1 - sub[::-1].argmax(axis=0)
    return float(np.median(bottom - top + 1)) / mask.shape[0]


def metrics(counts, borders, total_px):
    """One frame's row from its class pixel counts.

    counts: {'subject': n, 'support': n, 'background': n, 'key': {id: n}, 'concealed': {id: n}}  (key and concealed
            pixels are not in 'subject'; both are part of the subject, so the shares add them back)
    borders: {side: {'subject': bool, 'key': [ids]}} - which classes touch each border
    """
    keys, concealed = counts.get('key', {}), counts.get('concealed', {})
    subject = counts.get('subject', 0) + sum(keys.values()) + sum(concealed.values())
    opaque = subject + counts.get('support', 0)
    share = lambda n: round(n / total_px, 6) if total_px else 0.0  # noqa: E731
    return {'opaque_share': share(opaque), 'subject_share': share(subject), 'support_share': share(counts.get('support', 0)),
            'key_px': dict(keys), 'key_share': {k: share(v) for k, v in keys.items()}, 'concealed_px': dict(concealed),
            'subject_edges': [s for s in SIDES if borders.get(s, {}).get('subject')],
            'background_edges': [s for s in SIDES if borders.get(s, {}).get('background')],
            'key_edges': {k: [s for s in SIDES if k in borders.get(s, {}).get('key', [])] for k in keys}}


def judge(rows, key_parts, role, has_subject, count, exempt_frames=(), concealed_parts=(), output_size=(1080, 1920), diorama=False,
          details=()):
    """(failures, notes): failures are {'code', ...} dicts (the caller splits them by gate severity); notes are
    measurements kept with no verdict. role: 'explain' | 'mood' | None (the shot's route role). concealed_parts: parts
    that must not show in their window (an intact exterior hides its valve train) - any role, more than max_px is broken."""
    t = THRESHOLDS
    failures = []
    judged = [r for r in rows if r['frame'] not in set(exempt_frames)]   # whip windows: a deliberate blur of nothing
    empty = [r['frame'] for r in judged if r['opaque_share'] < t['empty_opaque_share']]
    if empty:
        failures.append({'code': 'FRAME_EMPTY', 'frames': empty[:20], 'hint': 'the camera sees nothing here'})
    cut = [(r['frame'], r['near_cut_share']) for r in rows if (r.get('near_cut_share') or 0) >= t['near_clip_cut_share']]
    if cut:
        failures.append({'code': 'FRAME_NEAR_CLIP_CUT', 'frames': [f for f, _ in cut][:20], 'max_cut_share': max(s for _, s in cut),
                         'hint': 'the near clip plane slices the subject: move the camera back or lower camera clip_start'})
    windows = key_windows(key_parts, count)
    for part in key_parts:
        if part.get('source') in ('details', 'focus', 'context'):   # classes for measuring (details below; the shot's focus for
            # patterns; the things around the subject a frame picture shows, for their size)
            continue
        lo, hi = windows[part['id']]
        inside = [r for r in rows if lo <= r['frame'] <= hi]
        if not inside:
            continue
        min_px = max(t['key_min_px'], int(part.get('min_px', 0)))
        best = max(r['key_px'].get(part['id'], 0) for r in inside)
        if best < min_px:
            code = 'KEY_PART_INVISIBLE' if role == 'explain' else 'KEY_PART_INVISIBLE_MOOD'
            failures.append({'code': code, 'part': part['id'], 'frames': [lo, hi], 'max_px': best, 'min_px': min_px,
                             'hint': 'the key part is hidden or off frame for its whole window'})
            continue
        if part.get('source', 'declared') != 'declared':   # implied keys (rig target, storyboard focus) must show; size is the shot's call
            continue
        if max(r['key_share'].get(part['id'], 0) for r in inside) < t['key_readable_share']:
            failures.append({'code': 'KEY_PART_SMALL', 'part': part['id'], 'frames': [lo, hi], 'hint': 'move closer or longer lens'})
        edged = [r['frame'] for r in inside if r['key_edges'].get(part['id'])]
        if len(edged) > t['edge_frames_share'] * len(inside):
            failures.append({'code': 'FRAME_EDGE_CUT', 'part': part['id'], 'frames': edged[:20], 'hint': 'the frame border cuts the key part'})
    for detail in details:   # what the shot explains shows at its declared thickness in some frame of its window
        lo, hi = int(detail.get('from_frame', 0)), int(detail.get('to_frame', count - 1))
        inside = [r for r in rows if lo <= r['frame'] <= hi]
        best = max((thickness_px(r.get('shapes', {}).get(f"key:{detail['id']}"), output_size) for r in inside), default=0.0)
        verdict = px_verdict(best, detail['min_px'])
        if verdict == 'short':
            failures.append({'code': 'MUST_SEE_SHORT', 'part': detail['id'], 'frames': [lo, hi], 'thickness_px': round(best, 1),
                             'min_px': detail['min_px'], 'why': detail.get('why'),
                             'hint': f'shows, within {round(SHORTFALL_SHARE * 100)} % of its declared thickness: come a little closer if it must read fully'})
        elif verdict == 'thin':
            failures.append({'code': 'DETAIL_NOT_SHOWN', 'part': detail['id'], 'frames': [lo, hi], 'thickness_px': round(best, 1),
                             'min_px': detail['min_px'], 'why': detail.get('why'),
                             'hint': 'hidden or too thin on screen: turn a cut face toward the camera, come closer or use a longer lens; '
                                     'being in the scene is not being shown'})
    hidden_windows = key_windows(concealed_parts, count)
    for part in concealed_parts:   # SKILL rule #6: broken output, not taste - never softened
        lo, hi = hidden_windows[part['id']]
        allowed = int(part.get('max_px', 0))
        shown = [(r['frame'], r.get('concealed_px', {}).get(part['id'], 0)) for r in rows if lo <= r['frame'] <= hi]
        shown = [(f, n) for f, n in shown if n > allowed]
        if shown:
            failures.append({'code': 'CONCEALED_PART_VISIBLE', 'part': part['id'], 'frames': [f for f, _ in shown][:20],
                             'max_px': max(n for _, n in shown), 'allowed_px': allowed,
                             'hint': 'a part the shot declares hidden shows through: close the shell (gap, missing cover, cut) or end the window earlier'})
    if not diorama and judged:   # the model's own edge in a void: the world stops at the frame (SKILL: the subject lives in a world)
        boxed = [r['frame'] for r in judged if len(r.get('background_edges', [])) >= MODEL_EDGE_SIDES]
        if len(boxed) > MODEL_EDGE_FRAMES * len(judged):
            failures.append({'code': 'FRAME_MODEL_EDGE', 'frames': boxed[:20],
                             'hint': 'the model ends inside the frame with void below the horizon around it: continue the structure past '
                                     'the frame (repeats, neighbouring units, floors above and below) and give it ground and surroundings '
                                     '(kits, terrain) - or declare screen.diorama if a model in empty space is meant'})
    if has_subject and judged:
        median = statistics.median(r['subject_share'] for r in judged)
        if median < t['subject_small_share']:
            failures.append({'code': 'FRAME_SUBJECT_SMALL', 'median_share': round(median, 4), 'hint': 'the subject is small in the frame'})
    notes = {'subject_share_median': round(statistics.median(r['subject_share'] for r in rows), 4) if rows else None,
             'support_share_median': round(statistics.median(r['support_share'] for r in rows), 4) if rows else None,
             'frames': [r['frame'] for r in rows]}
    return failures, notes


def is_warning_by_role(failure):
    """KEY_PART_INVISIBLE (and KEY_PART_UNDER_UI, marked by_role) on a mood shot is a warning whatever the project policy:
    the shot explains nothing exact."""
    return failure['code'] == 'KEY_PART_INVISIBLE_MOOD' or bool(failure.get('by_role'))
