"""What a line explains changes on screen: each shot says its changes (direction.change: what, of which kind, from what
to what, when, why) or why it is still (direction.still); the build makes them (blender_ops/change_scene.py, by the
kind's row in library/change_kinds.json) and the screen is measured between each change's start and end (screen_lines).

Why (2026-10-22, submarine r2): the lines said the vents open, the tanks fill, the pump pushes water out and the trim
levels the boat; every tank was a still box, every check asked only whether a part was seen and large enough, and the
clip judge said the cuts did not read. A kind is a library row, never a branch here (tests/test_change_moves.py reads
the code for kind ids).
"""
from __future__ import annotations

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
    return out


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
    a = ((start['box'][2] - start['box'][0]) ** 2 + (start['box'][3] - start['box'][1]) ** 2) ** 0.5
    b = ((end['box'][2] - end['box'][0]) ** 2 + (end['box'][3] - end['box'][1]) ** 2) ** 0.5
    return abs(b - a) / max(a, b, 1e-9)


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
    return out


def words(change, book=None):
    """The change in words, for a prompt: 'the water in submarine_hull/mbt_fore rises from 0 to 0.9 (level)'."""
    kind = (book if book is not None else kinds()).get(change.get('kind')) or {}
    return (f"{', '.join(change.get('parts') or [])}: {change.get('kind')} from {change.get('from')} to {change.get('to')} "
            f"({kind.get('means', '')}) during {change.get('at')} of the shot")
