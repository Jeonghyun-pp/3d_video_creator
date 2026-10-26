"""v1's reel-flow rules (studio/reel_flow.py) on v2's cuts: the same rules, each fact they read taken from the cuts
layer's own fields and, once a cut is made, from its located pictures - never a field v1's 3D build writes.

What a row says, and where v2 reads it:
- what happens and when (rule 1's hook, rule 7's quiet): change.at, and a moving thing's own window (element.at);
- the key change (rule 11): the cut whose change says key: true, at the start of its window; made, its size is the long
  side of the changed parts' located boxes (in frame heights) and nothing is hidden in front of a part the overlay judge
  read the mark on (located and judged ok), unknown otherwise;
- what the cut opens (cutaway.opens), its role, its scale step, a new place (handover new_place when the place changes);
- made: its focus height (the changed parts, else the named ones, else every part - their located boxes' union), whether
  the camera rests (camera.move still), and which other cuts show the same view of the same place (rule 3, read from the
  pictures: same_views - SAME_PICTURE and PICTURE_REPEATS, answered by changing what is opened or the scale step).
A cut not made is said as not made (REEL_FLOW_UNMADE), never as an unmeasured 3D build.

Why (2026-10-26, the dev preview): v1's reel flow raised HOOK_WEAK, KEY_CHANGE_UNPLANNED and REEL_FLOW_UNMEASURED on a
reel whose opening cut moves at 1.3 s - it read direction.change, show rows and build reports v2 never writes.
"""
from __future__ import annotations

from pathlib import Path

from studio.common import StudioError, file_hash, now, read_json, stable_hash, write_json

FIX_WORDS = {   # how v1's hook and key-change findings are fixed in a cut (reel_flow.FIX_WORDS)
    'hook': ("What happens is the opening cut's change: a change (change.at) or a moving thing's window (element.at) starting in "
             'the first seconds, or its key change (change.key) - planned in the cuts layer, drawn in its pictures'),
    'key': ('mark the change that is the point with change.key: true on one of those cuts (its window starts at the moment; its '
            'parts are what changes)'),
}
ASPECT = 1080 / 1920   # a box's width counted in frame heights on the 9:16 feed (the project's own when it is read)


def _windows(cut):
    """[start, end] shares of the cut where something changes on screen: its change, its moving thing's own window."""
    change = cut['change']
    if change['kind'] == 'none':
        return []
    out = [list(change['at'])] if change.get('at') else []
    element = change.get('element') or {}
    if element.get('at') and list(element['at']) not in out:
        out.append(list(element['at']))
    return out


def _row(cut, after, line_beat, spoken):
    """The plan's row of one cut (reel_flow.lines reads it as it reads a shot list's)."""
    from studio.story_core import beats_of
    length = cut['duration_s']
    windows = _windows(cut)
    key = bool(cut['change'].get('key')) and cut['change']['kind'] != 'none'
    at = (cut['change'].get('at') or [0, 1])
    return {'shot_id': cut['cut_id'], 'len_s': length, 'role': cut.get('role'), 'scale_step': cut['cutaway']['scale_step'],
            # v1 reads a hand-over on the cut that hands over: this one, when the next cut is at another place
            'handover': 'new_place' if after is not None and after['place'] != cut['place'] else None,
            'story_beats': list(beats_of(cut['line_ids'], line_beat)), 'departs': [d['item'] for d in cut.get('departs') or []],
            'spoken_s': spoken, 'opens': cut['cutaway']['opens'] != 'none', 'key_show': key,
            'key_times': [round(at[0] * length, 3)] if key else [],
            'parts': sorted(set(cut['change'].get('parts') or []) | set(cut.get('carries') or [])), 'elements': [], 'thread_to': [],
            'change_times': [round(w[0] * length, 3) for w in windows], 'change_spans': [[round(a * length, 3), round(b * length, 3)] for a, b in windows],
            'journey': None, 'settle_at': None}


def plan_rows(path, body, lines=None):
    """Rows of the cuts as planned (reel_flow.lines), each line's spoken seconds through the cuts it runs into."""
    from studio import decisions
    from .cuts import as_shots
    lines = lines if lines is not None else (decisions.envelope(path, 'script') or {}).get('body', {}).get('lines') or []
    line_beat = {l['line_id']: l.get('story_beat_id') for l in lines}
    spoken = decisions.speech_occupancy(as_shots(body), {l['line_id']: l.get('text', '') for l in lines}, decisions.reference_chars_per_s(path)) \
        if lines else {}
    cuts = body['cuts']
    return [_row(c, cuts[n + 1] if n + 1 < len(cuts) else None, line_beat, spoken.get(c['cut_id']) if lines else None)
            for n, c in enumerate(cuts)]


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None


def focus_parts(cut):
    """What the cut is about, for its focus box: the parts its change touches, else the parts it names, else all."""
    parts = [p['id'] for p in cut['cutaway']['parts']]
    changed = [p for p in cut['change'].get('parts') or [] if p in parts]
    named = [l['part'] for l in cut['cutaway']['labels'] if 'part' in l and l['part'] in parts]
    return changed or named or parts


def measured(cut, places, located_ok, row, aspect=ASPECT):
    """A plan row with what the made cut's located pictures say (module docstring)."""
    boxes = {p['id']: p['box'] for p in places.get('parts') or [] if p.get('box')}
    focus = _union([boxes[p] for p in focus_parts(cut) if p in boxes])
    out = dict(row)
    if focus is None:
        return out
    height = focus[3] - focus[1]
    out.update({'height': height, 'env': 0.0, 'fills': height >= 0.95 and focus[2] - focus[0] >= 0.95,
                'still': cut['cutaway']['camera']['move'] == 'still', 'view_start': None, 'view_end': None})
    if row.get('key_show'):
        parts = [p for p in cut['change'].get('parts') or [] if p in boxes] or focus_parts(cut)
        box = _union([boxes[p] for p in parts if p in boxes])
        size = max(box[3] - box[1], (box[2] - box[0]) * aspect) if box else None
        out['key_moments'] = [{'at': (cut['change'].get('at') or [0])[0], 'parts': parts, 'size': size,
                               'hidden': 0.0 if located_ok else None}]
    return out


def built_row(path, entry, fps, chars_per_s=None):
    """One composite shot's row for reel_flow.built (named there in ROUTE_ROWS): the cut's plan row, measured from its
    located pictures once its clip is made; unmade, it says so."""
    from studio import decisions
    from studio.decisions import CHARS_PER_S, spoken_seconds
    from studio.edit import latest_composite
    from studio.project import load_project, load_shot
    from .locate import places_of
    path = Path(path)
    body = decisions.envelope(path, 'cuts')['body']
    order = [c['cut_id'] for c in body['cuts']]
    if entry['shot_id'] not in order:
        return {'shot_id': entry['shot_id'], 'len_s': entry['frame_count'] / fps, 'unmade': True}
    n = order.index(entry['shot_id'])
    cut = body['cuts'][n]
    lines = (decisions.envelope(path, 'script') or {}).get('body', {}).get('lines') or []
    shot = load_shot(path, entry['shot_id'])
    said = ((shot.get('narration') or {}).get('text') or '').strip()
    row = _row(cut, body['cuts'][n + 1] if n + 1 < len(body['cuts']) else None, {l['line_id']: l.get('story_beat_id') for l in lines},
               spoken_seconds([said], chars_per_s or CHARS_PER_S) if said else None)
    row.update({'len_s': entry['frame_count'] / fps, 'continues_into': (shot.get('narration') or {}).get('continues_into') or [],
                'visible': None, 'off_picture': None})
    if latest_composite(path, shot, entry['frame_count']) is None:
        return {**row, 'unmade': True}
    places = places_of(path, cut)
    if places is None:
        return {**row, 'unmade': True}
    out = load_project(path)['output']
    row = measured(cut, places['places'], bool(places.get('ok')), row, out['width'] / out['height'])
    same = same_views_of(path, body)
    if same is not None:
        row['same_as'] = {other: True for group in same for other in group if entry['shot_id'] in group and other != entry['shot_id']}
    return row


def plan_lines(path, body, story=None):
    """The reel-flow findings of the cuts as planned (the shot list's own reading, v1 decisions.lint), and the open ones
    a story-told reel answers (DIRECTION_UNRESOLVED)."""
    from studio import decisions
    from studio.reel_flow import lines, read_reference, silenced, unresolved
    story = story if story is not None else (decisions.envelope(path, 'story') or {}).get('body')
    found = lines(plan_rows(path, body), story, silenced(path), read_reference(path), FIX_WORDS)
    return found + (unresolved(found) if (story or {}).get('beats') else [])


# --- the same view, read from the pictures ---------------------------------------------------------------------------

SAME_PROMPT = """The image shows the cuts of one short vertical explainer video in order: each tile is a cut's first
picture, named by the label above it. Group the cuts that show THE SAME VIEW OF THE SAME PLACE - the same thing seen from
the same side, at about the same distance, with the same things in about the same parts of the frame - so that cutting
from one to the other would look like the same picture again. Cuts that show the same place from another side, much
closer or much further back, cut open differently, or a different thing are NOT the same view. Give each group of two
or more such cuts with why in one sentence; leave out every cut that is in no group."""
TILE = (216, 384)


def sheet_of(pictures, target):
    """One image of the cuts' first pictures in order, each tile named (pictures: [(name, png)])."""
    from PIL import Image, ImageDraw, ImageFont
    from studio.common import REPO
    font = ImageFont.truetype(str(REPO / 'library' / 'fonts' / 'pretendard' / 'Pretendard-Bold.otf'), 26)
    per_row = min(6, max(1, len(pictures)))
    rows = (len(pictures) + per_row - 1) // per_row
    head = 36
    sheet = Image.new('RGB', (per_row * (TILE[0] + 8) + 8, rows * (TILE[1] + head + 8) + 8), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    for n, (name, png) in enumerate(pictures):
        x, y = 8 + (n % per_row) * (TILE[0] + 8), 8 + (n // per_row) * (TILE[1] + head + 8)
        with Image.open(png) as im:
            from .motion import cover
            sheet.paste(cover(im.convert('RGB'), *TILE), (x, y + head))
        draw.text((x + 4, y + 4), name, font=font, fill=(255, 230, 80))
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, quality=90)
    return target


def read_same_views(pictures, out, reader=None, run=0):
    """[[name, ...], ...] - groups of cuts the reader sees as one view of one place (pictures: [(name, png)]), read once
    for exactly these pictures (kept at out/same_<sha>_<run>.json)."""
    from studio.screen_grammar import codex_json
    names = [n for n, _ in pictures]
    sha = stable_hash({'pictures': [[n, file_hash(p)] for n, p in pictures], 'prompt': SAME_PROMPT})[:16]
    target = Path(out) / f'same_{sha}_{run}.json'
    if not target.is_file():
        sheet = sheet_of(pictures, Path(out) / f'same_{sha}.jpg')
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['groups'], 'properties': {'groups': {
            'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['cuts', 'why'], 'properties': {
                'cuts': {'type': 'array', 'items': {'enum': names}, 'minItems': 2}, 'why': {'type': 'string'}}}}}}
        (reader or codex_json)([sheet], SAME_PROMPT + (f' (reading {run})' if run else ''), schema, target)
    groups = read_json(target)['answer']['groups']
    return [sorted(set(g['cuts']), key=names.index) for g in groups if len(set(g['cuts'])) >= 2]


def _firsts(path, body):
    from .frames import picked
    out = [(c['cut_id'], picked(path, c, 'first')) for c in body['cuts']]
    return [(n, p) for n, p in out if p is not None]


def same_views_of(path, body):
    """The kept reading of the reel's picked first pictures as they are now, or None when it was not read."""
    pictures = _firsts(path, body)
    if len(pictures) < 2:
        return None
    sha = stable_hash({'pictures': [[n, file_hash(p)] for n, p in pictures], 'prompt': SAME_PROMPT})[:16]
    target = Path(path) / 'review' / 'flow' / f'same_{sha}_0.json'
    return read_same_views(pictures, target.parent) if target.is_file() else None


def same_views(project, reader=None):
    """Read which cuts show one view of one place (rule 3 from the pictures), kept for the reel flow."""
    from studio import decisions
    from studio.project import project_dir
    path = project_dir(project)
    body = decisions.envelope(path, 'cuts')['body']
    pictures = _firsts(path, body)
    if len(pictures) < 2:
        raise StudioError('FRAMES_UNPICKED', 'the same view is read on the picked first pictures of two cuts or more')
    groups = read_same_views(pictures, path / 'review' / 'flow', reader)
    return {'groups': groups, 'cuts': [n for n, _ in pictures], 'at': now(),
            'next': ['studio2 reel flow --project P (SAME_PICTURE / PICTURE_REPEATS: change what a cut opens or its scale step)'] if groups else []}


def built(project):
    from studio.reel_flow import built as v1_built
    return v1_built(project)


def register_commands(subparsers):
    parser = subparsers.add_parser('flow', help="v1's reel-flow rules read from the cuts (planned, and made: their located pictures)")
    commands = parser.add_subparsers(dest='flow_command', required=True)
    p = commands.add_parser('plan', help='the findings of the cuts as planned')
    p.add_argument('--project', required=True)

    def plan_handler(a):
        from studio import decisions
        from studio.project import project_dir
        path = project_dir(a.project)
        return {'warnings': plan_lines(path, decisions.envelope(path, 'cuts')['body'])}
    p.set_defaults(handler=plan_handler)
    p = commands.add_parser('built', help='the findings of the made cuts (reel_flow.built reads them through this module)')
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: built(a.project))
    p = commands.add_parser('same', help='which cuts show one view of one place, read from their first pictures')
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: same_views(a.project))
    return commands
