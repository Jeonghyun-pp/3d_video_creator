"""What a line explains changes on screen: each shot says its changes (direction.change: what, of which kind, from what
to what, when, why) or why it is still (direction.still); the build makes them (blender_ops/change_scene.py, by the
kind's row in library/change_kinds.json) and the screen is measured between each change's start and end (screen_lines).

Why (2026-10-22, submarine r2): the lines said the vents open, the tanks fill, the pump pushes water out and the trim
levels the boat; every tank was a still box, every check asked only whether a part was seen and large enough, and the
clip judge said the cuts did not read. A kind is a library row, never a branch here (tests/test_change_moves.py reads
the code for kind ids).
"""
from __future__ import annotations

import math

from pathlib import Path

from .common import REPO, read_json

KINDS = REPO / 'library' / 'change_kinds.json'
MAKES = ('fill', 'transform', 'drive', 'simulate')            # how a kind is made (blender_ops/change_scene.py)
MEASURES = ('area', 'travel', 'outline', 'size')              # how a kind is judged on screen (screen_lines)
NEEDS = ('axis', 'toward')                                    # what a kind may ask a change to say
AXES = ('x', 'y', 'z', 'length', 'width', 'height')   # the world's, or the object's own (blender_ops/change_scene._axis_vector)
SMALL_FACTOR = 2.0   # a change the screen shows under this many times its kind's floor, not declared exaggerated: said


def kinds():
    return {k['id']: k for k in read_json(KINDS)['kinds']}


def changes_of(entry):
    return list((entry.get('direction') or {}).get('change') or [])


def check_change(change, book=None, narration=''):
    """Errors of one direction.change row as written: a known kind, values in its range, a window inside the shot, what
    the kind needs, words the shot's lines say."""
    book = book if book is not None else kinds()
    cid = change.get('id', '?')
    kind = book.get(change.get('kind'))
    if kind is None:
        return [f"change {cid}: kind {change.get('kind')!r} is not one of {sorted(book)} (library/change_kinds.json)"]
    out = []
    low, high = kind['value']['range']
    for end in ('from', 'to'):
        value = change.get(end)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= high:
            out.append(f"change {cid}: {end} {value!r} is outside {low}..{high} ({kind['value']['unit']})")
    if change.get('from') == change.get('to'):
        out.append(f'change {cid}: from and to are the same - nothing changes')
    at = change.get('at') or []
    if len(at) != 2 or not 0 <= at[0] < at[1] <= 1:
        out.append(f"change {cid}: at {at} must be [start, end] within 0-1 of the shot, start before end")
    for need in kind.get('needs') or []:
        if need not in NEEDS:
            out.append(f'change kind {kind["id"]}: needs {need!r} is not one of {NEEDS}')
        elif need == 'axis' and change.get('axis') not in AXES:
            out.append(f"change {cid}: a {kind['id']} turns about an axis - axis: one of {AXES}")
        elif need == 'toward' and not (isinstance(change.get('toward'), list) and len(change['toward']) == 3 and any(change['toward'])):
            out.append(f"change {cid}: a {kind['id']} goes somewhere - toward: [x, y, z]")
    if change.get('axis') is not None and change['axis'] not in AXES:
        out.append(f"change {cid}: axis {change['axis']!r} is not one of {AXES}")
    if change.get('axis') is not None and 'axis' not in (kind.get('needs') or []) and not kind.get('keeps_axis'):
        out.append(f"change {cid}: a {kind['id']} reads no axis")
    words = (change.get('with_words') or '').strip()
    if words and words not in (narration or ''):
        out.append(f'change {cid}: with_words {words!r} is not in the lines said over the shot')
    if not change.get('parts'):
        out.append(f'change {cid}: names no parts - what changes?')
    return out


ON_A_PART = ('label', 'outline')   # a shot's own mark of these kinds stands `on` a part; the others run `along` a way


def check_shot_marks(marks, known):
    """Errors of direction.marks as written (its kinds: the schema): what each stands on (a label and an outline on a
    part, a flow and an arrow along a way), the ids known, the window inside the shot."""
    out = []
    for n, mark in enumerate(marks):
        need = 'on' if mark.get('kind') in ON_A_PART else 'along'
        if not mark.get(need):
            out.append(f"mark {n}: a {mark['kind']} needs {need!r} - " + ('the part it stands on' if need == 'on' else 'its way: ids or [x, y, z] points'))
        named = [mark['on']] if need == 'on' and mark.get('on') else [a for a in mark.get('along') or [] if isinstance(a, str)]
        unknown = [i for i in named if i not in known]
        if unknown:
            out.append(f'mark {n}: names {unknown}, which nothing models (a subject part or a world element)')
        at = mark.get('at')
        if at is not None and not (len(at) == 2 and 0 <= at[0] < at[1] <= 1):
            out.append(f'mark {n}: at {at} must be [start, end] within 0-1 of the shot, start before end')
    return out


def _expanded(ident, world):
    """A declared id and, for a world element, the ids its links build."""
    for e in (world or {}).get('elements', []):
        if e['id'] == ident and e.get('ids'):
            return [ident, *e['ids']]
    return [ident]


def plan_lines(path, body, script_lines, world=None, book=None):
    """Errors of a shot list's changes: CHANGE_UNDECIDED (a shot says neither its changes nor why it is still), a row
    check_change refuses, CHANGE_UNSEEN_PLAN (no show row holds the changing part on screen while it changes), and
    CHANGE_INSIDE_CLOSED (the part sits inside its subject - the exposure survey - and the moment showing it does not
    open). Default closed: a shot list that leaves the change out is held, never built from a still box."""
    from .blender_ops.show_core import at_share, held_at, opens, show_of
    from .decisions import known_show_parts
    from .explains import need, resolve
    book = book if book is not None else kinds()
    known = known_show_parts(path, world)
    out = []
    for s in body['shots']:
        sid = s['shot_id']
        direction = s.get('direction') or {}
        rows = changes_of(s)
        out += [f'{sid}: {e}' for e in check_shot_marks(direction.get('marks') or [], known)]
        if not rows and not (direction.get('still') or '').strip():
            out.append(f"CHANGE_UNDECIDED: {sid} says neither what changes on screen (direction.change) nor why it is still "
                       "(direction.still) - what its lines explain happens in the 3D, or the shot holds on purpose")
            continue
        # every line said over the shot, one that runs on from the shot before too (its words may start a change here)
        narration = ' '.join(script_lines.get(i, '') for i in s.get('line_ids') or [])
        ids = [c.get('id') for c in rows]
        if len(set(ids)) != len(ids):
            out.append(f'{sid}: change ids {ids} repeat')
        for change in rows:
            errors = check_change(change, book, narration)
            out += [f'{sid}: {e}' for e in errors]
            if errors:
                continue
            unknown = [p for p in change['parts'] if p not in known]
            if unknown:
                out.append(f"{sid}: change {change['id']} names {unknown}, which nothing models (a subject part or a world element)")
                continue
            start, end = change['at']
            during = [r for r in show_of(s) if r is held_at(show_of(s), start) or start <= at_share(r['at']) <= end]

            def holds(row, part):   # through the world's links: an element and the parts that build it are one thing
                mine = _expanded(part, world)
                return any(p == q or q.startswith(p + '/') or p.startswith(q + '/')
                           for shown in row['parts'] for p in _expanded(shown, world) for q in mine)
            for part in change['parts']:
                shown = [r for r in during if r is not None and holds(r, part)]
                if not shown:
                    out.append(f"CHANGE_UNSEEN_PLAN: {sid}: change {change['id']} changes {part} at {start}-{end} and no show row holds it "
                               'on screen then - add the moment that shows it')
                    continue
                for ident in _expanded(part, world):
                    thing = resolve(path, ident, world)
                    needs = need(path, thing) if thing else {}
                    if needs.get('problem') and thing['kind'] not in ('element', 'subject'):
                        # whether it sits inside is unmeasured: said, never passed as outside (2026-10-22 r3: a tank filling
                        # behind a closed hull passed the plan and showed 10 px)
                        out.append(f"CHANGE_INSIDE_UNMEASURED: {sid}: change {change['id']} happens to {ident} and whether it sits inside "
                                   f"its subject is not measured ({needs['problem']}) - studio subject exposure --subject {thing['subject']}")
                        break
                    if needs.get('inside') and not any(opens(r['how']) for r in shown):
                        out.append(f"CHANGE_INSIDE_CLOSED: {sid}: change {change['id']} happens to {ident}, inside its subject, and the "
                                   f"moment showing it ({', '.join(r['how'] for r in shown)}) does not open it - show it opened "
                                   '(section, cutaway, xray, transparency)')
                        break
                    if needs.get('inside') and not change.get('marks') and not change.get('unmarked'):
                        # a part inside a big thing reads only when the viewer is told which it is (2026-10-23, r3: s04, s05
                        # and s09 were seen changing and nobody could tell what changed - the judge's names_change 0/4)
                        out.append(f"CHANGE_UNMARKED: {sid}: change {change['id']} happens to {ident}, inside its subject, with no mark "
                                   'telling which thing changes - name it (marks: label), show its flow or draw its edge, or say why not (unmarked)')
                        break
    return out


MARK_LABEL = {'occlusion_policy': 'always', 'placement': 'anchor', 'leader': True}   # a mark's label stays on the part it names,
                                                                                # seen through what is in front (it is opened)


def _mark(ident, mark, window, part):
    """One mark as a label or a graphic of its own kind (graphics.py): a label on the middle of `part`, a flow or an arrow
    along its way (named parts at their middles: a part's origin may be its subject's), an outline of `part`."""
    if mark['kind'] == 'label':
        return 'labels', {'label_id': ident, 'text': mark.get('text') or part.split('/')[-1], 'anchor': f'{part}/center', **window, **MARK_LABEL}
    return 'graphics', {'graphic_id': ident, 'kind': mark['kind'], **window,
                        **({'anchors': [f'{a}/center' if isinstance(a, str) else a for a in mark['along']]} if mark.get('along')
                           else {'target': part})}


def marks_for(shot):
    """(labels, graphics) the shot's marks become - ids 'mark_<change>_<n>' / 'mark_shot_<n>', so a later list replaces
    them. A change's marks are timed to its window (timed) and stand on its first part; the shot's own marks
    (direction.marks: what a moment explains with no change - the push of the water on a boat that hangs still) hold
    over their `at` share of the shot and stand on their `on` part (2026-10-23, r3 s06: its row promised the up and down
    arrows "as graphics" and there was nowhere to write them)."""
    out = {'labels': [], 'graphics': []}
    for change in timed(shot):
        for n, mark in enumerate(change.get('marks') or []):
            window = {'start_frame': change['start_frame'], 'end_frame': change['end_frame'] + 1}
            field, row = _mark(f"mark_{change['id']}_{n}", mark, window, change['parts'][0])
            out[field].append(row)
    count = shot['duration_frames']
    for n, mark in enumerate((shot.get('direction') or {}).get('marks') or []):
        start, end = mark.get('at') or (0.0, 1.0)
        first = min(count - 1, round(start * count))
        field, row = _mark(f'mark_shot_{n}', mark, {'start_frame': first, 'end_frame': min(count, max(round(end * count), first + 1))}, mark.get('on'))
        out[field].append(row)
    return out['labels'], out['graphics']


def with_marks(shot):
    """The shot with its change marks as labels and graphics: earlier marks taken out, what the shot has besides kept."""
    labels, graphics = marks_for(shot)
    keep = lambda rows, key: [r for r in rows or [] if not str(r.get(key, '')).startswith('mark_')]   # noqa: E731
    return {**shot, 'labels': keep(shot.get('labels'), 'label_id') + labels, 'graphics': keep(shot.get('graphics'), 'graphic_id') + graphics}


def key_lines(body, world=None):
    """KEY_CHANGE_UNSAID (a warning): a key show moment holds parts no change of the shot says anything happens to - the
    key change of a cut is kept as motion (maker.implied_keeps) and measured only when a change names it."""
    from .blender_ops.show_core import show_of
    out = []
    for s in body.get('shots') or []:
        changed = {q for c in changes_of(s) for p in c['parts'] for q in _expanded(p, world)}
        for row in show_of(s):   # through the world's links, as plan_lines reads them
            idle = [p for p in row.get('parts') or [] if row.get('key') and not set(_expanded(p, world)) & changed]
            if idle:
                out.append(f"KEY_CHANGE_UNSAID: {s['shot_id']}'s key moment at {row['at']} shows {idle} and no change says what happens "
                           'to them (direction.change) - nothing measures that they move')
    return out


def measure_ids(change, kind):
    """The ids whose screen the change is judged on: the body a fill makes, the stream a flow makes, else the parts."""
    if kind['makes'] == 'fill':
        return [f'{p}/fill' for p in change['parts']]
    if kind['makes'] == 'simulate':
        return [f"StudioSim_change_{change['id']}"]
    return list(change['parts'])


def timed(shot, book=None):
    """The shot's changes with their frames (start_frame, end_frame) and the ids measured: `at` of the shot, moved to
    where `with_words` sits in the shot's narration (by its characters: an estimate the voice's timing refines - said
    in `timed`), the window kept."""
    book = book if book is not None else kinds()
    count = shot['duration_frames']
    text = (shot.get('narration') or {}).get('text') or ''
    out = []
    for change in changes_of(shot):
        start, end = change['at']
        note = None
        words_said = change.get('with_words')
        if words_said and words_said in text:
            length = end - start
            start = min(max(0.0, text.index(words_said) / max(1, len(text))), 1.0 - length)
            end, note = start + length, 'started where its words sit in the line (by characters; the voice timing refines it)'
        first = round(start * (count - 1))
        out.append({**change, 'start_frame': first, 'end_frame': max(round(end * (count - 1)), first + 1),
                    'measure_ids': measure_ids(change, book[change['kind']]), **({'timed': note} if note else {})})
    return out


def _seen(rows, ids):
    """{px, box, centre} of the ids on one probed frame (their key classes together), or None when none shows."""
    shapes = [((rows or {}).get('shapes') or {}).get(f'key:{i}') for i in ids]
    shapes = [s for s in shapes if s and s.get('px')]
    if not shapes:
        return None
    boxes = [s['bbox'] for s in shapes]
    box = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
    px = sum(s['px'] for s in shapes)
    centre = [sum(s['centroid'][k] * s['px'] for s in shapes) / px for k in (0, 1)]
    return {'px': px, 'box': box, 'centre': centre}


def _iou(a, b):
    w, h = max(0.0, min(a[2], b[2]) - max(a[0], b[0])), max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - w * h
    return w * h / union if union > 0 else 0.0


def screen_change(measure, start, end):
    """How much the screen changed between two sightings ({px, box, centre}), on the kind's measure."""
    if measure == 'area':
        return abs(end['px'] - start['px']) / max(end['px'], start['px'], 1)
    if measure == 'travel':
        return ((end['centre'][0] - start['centre'][0]) ** 2 + (end['centre'][1] - start['centre'][1]) ** 2) ** 0.5
    if measure == 'outline':
        return 1.0 - _iou(start['box'], end['box'])
    # size: the most it grew or shrank across or up the screen - a squeeze that keeps its length changes one side of the
    # box and barely its diagonal (2026-10-23, r3 s09: a pressure hull squeezed 15 % read 0.009 by the diagonal)
    sides = [(start['box'][2] - start['box'][0], end['box'][2] - end['box'][0]), (start['box'][3] - start['box'][1], end['box'][3] - end['box'][1])]
    return max(abs(b - a) / max(a, b, 1e-9) for a, b in sides)


def _outline_change(version_dir, frame_report, ids, start, end):
    """1 - the overlap of the ids' own pixels in the probe's id pictures at two frames (reel_flow.silhouette), or None
    without the pictures: a turn keeps its box and changes its outline."""
    from .reel_flow import _class_colours, silhouette
    if version_dir is None:
        return None
    pictures = [Path(version_dir) / 'frame_probe' / f'frame_{f:04d}_id.png' for f in (start, end)]
    if not all(p.is_file() for p in pictures):
        return None
    colours = _class_colours((frame_report or {}).get('summary') or {})
    wanted = [colours[f'key:{i}'] for i in ids if f'key:{i}' in colours]
    a, b = (silhouette(p, wanted) for p in pictures)
    union = len(a | b)
    return 1.0 - len(a & b) / union if union else 0.0


def screen_lines(shot, frame_report, change_report=None, output_size=(1080, 1920), book=None, version_dir=None):
    """[{code, text}] of a built version's changes, read on the probe's frames at each change's start and end:
    CHANGE_UNMADE (the build made nothing for it), CHANGE_HIDDEN (at neither end does it show thick enough to read),
    CHANGE_UNSHOWN (it shows, and the screen changed less than its kind's floor), CHANGE_SMALL (under twice the floor and
    not declared exaggerated - say so, or make it larger)."""
    from .blender_ops.show_core import SHOW_MIN_SHARE
    book = book if book is not None else kinds()
    frames = {r['frame']: r for r in (frame_report or {}).get('frames') or []}
    made = {r['id']: r for r in (change_report or {}).get('changes') or []}
    width, height = output_size
    out = []
    for change in timed(shot, book):
        kind, cid = book[change['kind']], f"{shot['shot_id']} change {change['id']}"
        if change['id'] not in made:
            out.append({'code': 'CHANGE_UNMADE', 'text': f'{cid}: the build made nothing for it (change_report.json)'})
            continue
        ends = [_seen(frames.get(change['start_frame']), change['measure_ids']), _seen(frames.get(change['end_frame']), change['measure_ids'])]

        def readable(s):
            return bool(s) and min((s['box'][2] - s['box'][0]) * width, (s['box'][3] - s['box'][1]) * height) >= SHOW_MIN_SHARE * height
        # every probed frame of the window: a thing that crosses the frame (in at one side, out the other) is seen between
        # its ends - and coming into or leaving the frame is itself the change (2026-10-22 r3: a stone falling past the lens)
        window = [_seen(frames[f], change['measure_ids']) for f in sorted(frames) if change['start_frame'] <= f <= change['end_frame']]
        if not any(readable(s) for s in window):
            out.append({'code': 'CHANGE_HIDDEN', 'text': f"{cid}: {', '.join(change['measure_ids'])} is not on screen thick enough to read "
                        f"anywhere in frames {change['start_frame']}-{change['end_frame']} - open the part, or frame it"})
            continue
        if not (readable(ends[0]) and readable(ends[1])):
            continue   # it enters or leaves the frame while it changes: the screen shows the change by that
        none = {'px': 0, 'box': [0, 0, 0, 0], 'centre': [0.5, 0.5]}
        outline = _outline_change(version_dir, frame_report, change['measure_ids'], change['start_frame'], change['end_frame']) \
            if kind['measure'] == 'outline' else None
        moved = outline if outline is not None else screen_change(kind['measure'], ends[0] or none, ends[1] or none)
        floor = kind['screen_min']
        if moved < floor:
            out.append({'code': 'CHANGE_UNSHOWN', 'text': f"{cid}: the screen changes {moved:.3f} ({kind['measure']}) between its start and "
                        f"end, under the {floor} a {change['kind']} needs to read - make it larger (declare it exaggerated), longer, or closer"})
        elif moved < SMALL_FACTOR * floor and not change.get('exaggerated'):
            out.append({'code': 'CHANGE_SMALL', 'text': f"{cid}: the screen changes {moved:.3f} ({kind['measure']}), under twice the "
                        f"{floor} floor - make it larger and say it is exaggerated (exaggerated: why), or keep it and say why"})
    return out + small_lines(shot, frame_report, change_report, book=book)


def small_lines(shot, frame_report, change_report, goal=None, book=None):
    """CHANGE_TOO_SMALL lines: a change whose changing part, at its largest in the change's window, stands less tall or
    covers less of the frame than the change_seen goal's size floor (a marked change: area times marked_area_factor). The
    screen may change by its kind's floor and still be a speck in a big frame (2026-10-23, r3: a bow tank's water 0.078
    of the frame, a burst of scattered air 0.0039 of it - the watcher saw neither)."""
    from .camera_comfort import goal as goal_row
    book = book if book is not None else kinds()
    floor = (goal if goal is not None else goal_row('change_seen'))['size_floor']
    frames = {r['frame']: r for r in (frame_report or {}).get('frames') or []}
    made = {r['id'] for r in (change_report or {}).get('changes') or []}
    out = []
    for change in timed(shot, book):
        if change['id'] not in made:
            continue
        seen = [(f, _seen(frames[f], change['measure_ids'])) for f in sorted(frames) if change['start_frame'] <= f <= change['end_frame']]
        seen = [(f, s) for f, s in seen if s]
        if not seen:
            continue   # not on screen at all: CHANGE_HIDDEN's
        tall = max(s['box'][3] - s['box'][1] for _f, s in seen)
        areas = [a for a in (_share(frames[f], s) for f, s in seen) if a is not None]
        area = max(areas) if areas else None   # unknown without the frame's own size: the height alone is read
        need = floor['area'] * (floor['marked_area_factor'] if change.get('marks') else 1.0)
        if tall < floor['height'] or (area is not None and area < need):
            out.append({'code': 'CHANGE_TOO_SMALL', 'ids': list(change['measure_ids']), 'text': f"{shot['shot_id']} change {change['id']}: at its largest it stands {tall:.3f} of the "
                        f"frame tall and covers {area if area is not None else 'an unmeasured share'} of it (floor {floor['height']} tall, {need:g} of the frame) - close on it while it "
                        'changes, mark it (name, flow), or give it a shot of its own'})
    return out


KEY_MOMENT_S = 0.25   # a key moment without a change of its own is read this long either side (one camera_follow window)


def key_windows(shot, length_s):
    """[(start_s, end_s)] of the shot's key change: each direction.change on a key show row's parts over its window, else
    each key moment KEY_MOMENT_S either side."""
    from .blender_ops.show_core import at_share, show_of
    from .decisions import _on
    rows = [r for r in show_of(shot) if r.get('key')]
    parts = [p for r in rows for p in r.get('parts') or []]
    changes = [c for c in changes_of(shot) if c.get('at') and _on(c.get('parts') or [], parts)]
    if changes:
        return [(c['at'][0] * length_s, c['at'][1] * length_s) for c in changes]
    return [(max(0.0, at_share(r['at']) * length_s - KEY_MOMENT_S), min(length_s, at_share(r['at']) * length_s + KEY_MOMENT_S)) for r in rows]


def key_moving_lines(shot, follow, length_s):
    """KEY_CHANGE_MOVING: the camera moves (camera_follow: its 0.25 s screen motion above rest) for a window or more of the
    key change - the change reads as reframing, or is missed while the eye follows the camera (CHANGE_MASKED for any change
    that needs a still reference; this for the key one whatever its kind). Read with the picture (a reason)."""
    from .camera_follow import WINDOW_S, moving_over
    out = []
    for start, end in key_windows(shot, length_s):
        moving = moving_over(follow, start, end)
        if follow and moving >= min(end - start, WINDOW_S):
            out.append(f"KEY_CHANGE_MOVING: {shot['shot_id']} - the camera moves {moving:.2f} s of its key change ({start:.2f}-{end:.2f} s) - "
                       'settle first (settle_at before the change), or hold over it')
    return out


def tail_lines(shot, follow, length_s, spoken_s=0.0, goal=None):
    """DEAD_TAIL: after the last thing that happens - the camera coming to rest, a change ending, the key moment, the words
    ending - the cut holds still and silent longer than the settled goal's tail_s. Read with the picture; a rest the
    director planned is said in direction.rest."""
    from .blender_ops.show_core import at_share, show_of
    from .camera_comfort import goal as goal_row
    limit = (goal if goal is not None else goal_row('settled'))['limits']['tail_s']
    ends = [(follow or {}).get('arrive_s') or 0.0, spoken_s or 0.0] + [c['at'][1] * length_s for c in changes_of(shot) if c.get('at')] \
        + [at_share(r['at']) * length_s for r in show_of(shot) if r.get('key')]
    tail = length_s - max(ends)
    if tail > limit:
        return [f"DEAD_TAIL: {shot['shot_id']} holds {tail:.2f} s still and silent after its last change, words and camera motion "
                f'(the study set holds at most {limit:g} s in nine cuts of ten) - end the cut sooner, let something happen, or say '
                'why the reel rests here (direction.rest)']
    return []


def key_small_lines(shot, frame_report, change_report, goal=None, book=None):
    """The CHANGE_TOO_SMALL lines of the key change only (a show row marked key names its parts): the change_seen goal's
    hard floor. Any other small change passes on a reason read with the picture (the user, 2026-10-24: a small change
    blocked everywhere sends every cut close and loses the whole)."""
    key = {p for row in shot.get('show') or [] if row.get('key') for p in row.get('parts') or []}
    return [r for r in small_lines(shot, frame_report, change_report, goal, book)
            if any(i == p or i.startswith(p + '/') or p.startswith(i + '/') for i in r.get('ids') or [] for p in key)]


def _share(row, seen):
    """The share of the frame a sighting's pixels cover (the probe's own frame size, from the share it reports), or None."""
    shapes = (row or {}).get('shapes') or {}
    whole = shapes.get('all') or {}
    if whole.get('px') and whole.get('share'):
        return round(seen['px'] * whole['share'] / whole['px'], 5)
    return None


def _union_shape(shapes):
    """{bbox, centroid} of several probe shapes together (the classes a shot is about); {} when none shows. The centroid
    weighs each by its pixels (its box middle when the probe gave no centroid)."""
    shapes = [s for s in shapes if s and s.get('bbox')]
    if not shapes:
        return {}
    boxes = [s['bbox'] for s in shapes]
    weights = [s.get('px') or 1.0 for s in shapes]
    middles = [s.get('centroid') or [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2] for s, b in zip(shapes, boxes)]
    return {'bbox': [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)],
            'centroid': [sum(w * m[k] for w, m in zip(weights, middles)) / sum(weights) for k in (0, 1)]}


def nothing_lines(shot, frame_report, goal=None):
    """SHOT_NOTHING_SHOWS: a still shot (nothing changes in it: direction.still) whose subject neither grows nor shrinks
    nor moves on screen between the probe's first and last frames past the something_shows goal's limits - a held
    picture of nothing new. A pull-back or a turn round it reveals; a hold does not. Why (2026-10-23, r3): the one still
    shot whose picture never changed was the one the reel's watcher called dull four times out of four. The subject is
    what the shot is about (reel_flow.focus_classes, as every flow measure reads it), never the place it shows: on
    2026-10-26 a held cut whose worker walked half the frame measured 'nothing changes' on the ceiling and floor."""
    import math
    from .camera_comfort import goal as goal_row
    from .reel_flow import focus_classes
    if not (shot.get('direction') or {}).get('still'):
        return []
    rows = sorted((frame_report or {}).get('frames') or [], key=lambda r: r['frame'])
    if len(rows) < 2:
        return []
    limits = (goal if goal is not None else goal_row('something_shows'))['limits']
    classes = focus_classes(shot, (frame_report or {}).get('summary') or {})
    first, last = (_union_shape([(r.get('shapes') or {}).get(c) for c in classes]) for r in (rows[0], rows[-1]))
    if not (first.get('bbox') and last.get('bbox')):
        return []
    tall = [b['bbox'][3] - b['bbox'][1] for b in (first, last)]
    grew = abs(tall[1] - tall[0]) / max(max(tall), 1e-6)
    moved = math.dist(first['centroid'], last['centroid'])
    if grew < limits['size'] and moved < limits['shift']:
        return [{'code': 'SHOT_NOTHING_SHOWS', 'text': f"{shot['shot_id']}: nothing changes in it and its picture holds - the subject's size changes "
                 f"{grew:.3f} and its middle moves {moved:.3f} first to last (limits {limits['size']}, {limits['shift']}) - reveal something "
                 'while the line speaks, or say why the reel rests here'}]
    return []


def masked_lines(shot, frame_report, change_report, pairs, fps, goal=None, book=None):
    """CHANGE_MASKED lines of a built version: a change whose kind needs a still reference (change_kinds needs_reference:
    a move, a turn) that the camera's own motion over the change's window rivals (the camera-only fit of grammar_synth's
    pairs, camera_comfort) while nothing still stands in frame (the probe's support share over the window under the
    goal's support_min) - the sinking reads as the camera rising. Why (2026-10-23, r3): the reel's watcher four times
    out of four could not tell s05's descent from its push, nor s09's squeeze from its approach."""
    from .camera_comfort import goal as goal_row
    book = book if book is not None else kinds()
    row = goal if goal is not None else goal_row('reference')
    frames = {r['frame']: r for r in (frame_report or {}).get('frames') or []}
    made = {r['id'] for r in (change_report or {}).get('changes') or []}
    first = min((p.get('frame', 0) for p in pairs), default=0)
    out = []
    for change in timed(shot, book):
        if not book[change['kind']].get('needs_reference') or change['id'] not in made:
            continue
        lo, hi = change['start_frame'], change['end_frame']
        steps = [p['camera'] for p in pairs if p.get('ok') and p.get('camera') and lo <= p.get('frame', first) - first < hi]
        if not steps:
            continue
        camera = math.hypot(sum(c['tx'] for c in steps), sum(c['ty'] for c in steps)) + abs(sum(c['ds'] for c in steps))
        ends = [_seen(frames.get(lo), change['measure_ids']), _seen(frames.get(hi), change['measure_ids'])]
        if not all(ends):
            continue   # it enters or leaves the frame while it changes: the frame is its reference
        moved = screen_change(book[change['kind']]['measure'], *ends)
        window = [frames[f].get('support_share') or 0.0 for f in sorted(frames) if lo <= f <= hi]
        still = sum(window) / len(window) if window else 0.0
        if camera >= row['mask_share'] * max(moved, 1e-6) and still < row['support_min']:
            out.append({'code': 'CHANGE_MASKED', 'text': f"{shot['shot_id']} change {change['id']}: the camera moves {camera:.3f} over the "
                        f"change's window against the change's own {moved:.3f} on screen, and nothing still stands in frame "
                        f"(support {still:.2f}) - {row['try']}"})
    return out


def words(change, book=None):
    """The change in words, for a prompt: 'the water in submarine_hull/mbt_fore rises from 0 to 0.9 (level)'."""
    kind = (book if book is not None else kinds()).get(change.get('kind')) or {}
    return (f"{', '.join(change.get('parts') or [])}: {change.get('kind')} from {change.get('from')} to {change.get('to')} "
            f"({kind.get('means', '')}) during {change.get('at')} of the shot")
