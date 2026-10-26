"""Where the marks go on a cut's picture: a reader (screen_grammar.codex_json) returns a box and a point for each part the
cut names, points along each flow's way and the moving thing's path, and where the cut's focus is; the marks are drawn
on the picture as an overlay and judged by a second reading (does each dot sit on its part, each line follow its way).
A mark judged off is located again with what was wrong (at most RETRIES times), then written up.

The middle and last pictures are edits of the first that keep its composition, so they take the first's places.
Kept in shots/<cut>/frames/<picture_sha>/locate/: first.json (the places), overlay.png, overlay_check.json.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from studio.common import REPO, StudioError, file_hash, now, read_json, write_json
from studio.project import project_dir

RETRIES = 2
MARK_COLOURS = [(255, 64, 64), (64, 200, 255), (255, 210, 0), (120, 255, 120), (255, 120, 255), (255, 160, 60)]

PROMPT = """The image is one picture of a short vertical explainer video. Locate things in it. Coordinates are 0-1 of the
image width (x, left to right) and height (y, top to bottom).
- parts: for each part below, a tight box [x0, y0, x1, y1] round it and one point on it where a label's dot would sit (on
  the part itself, on a clear area of it).
- flows: for each flow below, as many separate ways as it says (ways), spread across where it travels, each 4 to 10 points
  in the order it goes, following the picture's own geometry (each is drawn over the picture as a moving line, so the
  points must lie where that thing really travels in the picture).
- element: points along the path of the moving thing below, in order (null when there is none).
- focus: the one point the picture is about (where a slow push in should go).
{items}{reason}"""

CHECK = """Marks were drawn on this picture of a short explainer video: {marks}. For each mark answer ok (it sits on, or
follows, exactly what it names) and why in one short sentence."""


def _schema(parts, flows, element, ways=None):
    point = {'type': 'array', 'items': {'type': 'number', 'minimum': 0, 'maximum': 1}, 'minItems': 2, 'maxItems': 2}
    box = {'type': 'array', 'items': {'type': 'number', 'minimum': 0, 'maximum': 1}, 'minItems': 4, 'maxItems': 4}
    path = {'type': 'array', 'items': point, 'minItems': 2, 'maxItems': 12}
    return {'type': 'object', 'additionalProperties': False, 'required': ['parts', 'flows', 'element', 'focus'],
            'properties': {
                'parts': {'type': 'array', 'minItems': len(parts), 'maxItems': len(parts), 'items': {
                    'type': 'object', 'additionalProperties': False, 'required': ['id', 'box', 'point'],
                    'properties': {'id': {'enum': parts or ['none']}, 'box': box, 'point': point}}},
                'flows': {'type': 'array', 'minItems': len(flows), 'maxItems': len(flows), 'items': {
                    'type': 'object', 'additionalProperties': False, 'required': ['index', 'ways'],
                    'properties': {'index': {'type': 'integer', 'minimum': 0},
                                   'ways': {'type': 'array', 'items': path, 'minItems': min(ways or [1]), 'maxItems': max(ways or [1])}}}},
                'element': {'anyOf': [path, {'type': 'null'}]} if element else {'type': 'null'},
                'focus': point}}


def _items(cut):
    cutaway = cut['cutaway']
    rows = ['parts:'] + [f"  {p['id']}: {p['words']}" for p in cutaway['parts']] if cutaway['parts'] else []
    rows += ['flows:'] + [f"  {n}: {f['what']} - {f['path']} (ways: {f.get('ways', 1)})" for n, f in enumerate(cutaway['flow'])] if cutaway['flow'] else []
    element = cut['change'].get('element')
    if element:
        rows.append(f"element: {element['what']} - {element['path']}")
    return '\n'.join(rows)


def overlay(image, places, cut, target):
    """The located places drawn on the picture: a dot and name per part, a line per flow, the element's path, the focus."""
    font = ImageFont.truetype(str(REPO / 'library' / 'fonts' / 'pretendard' / 'Pretendard-Bold.otf'), 30)
    picture = Image.open(image).convert('RGB')
    w, h = picture.size
    scale = 1920 / h
    picture = picture.resize((round(w * scale), 1920))
    w, h = picture.size
    draw = ImageDraw.Draw(picture)
    marks = []
    for n, p in enumerate(places['parts']):
        colour = MARK_COLOURS[n % len(MARK_COLOURS)]
        x, y = p['point'][0] * w, p['point'][1] * h
        draw.ellipse([x - 12, y - 12, x + 12, y + 12], fill=colour, outline=(0, 0, 0), width=3)
        draw.text((x + 16, y - 16), p['id'], font=font, fill=colour, stroke_width=3, stroke_fill=(0, 0, 0))
        marks.append(f"part:{p['id']} = the {colour_name(colour)} dot labelled {p['id']}, meant to sit on "
                     f"{next(q['words'] for q in cut['cutaway']['parts'] if q['id'] == p['id'])}")
    from .motion import ways_of
    for f in places['flows']:
        for way in ways_of(f):
            pts = [(x * w, y * h) for x, y in way]
            draw.line(pts, fill=(0, 255, 255), width=8, joint='curve')
            draw.ellipse([pts[-1][0] - 10, pts[-1][1] - 10, pts[-1][0] + 10, pts[-1][1] + 10], fill=(0, 255, 255))
        flow = cut['cutaway']['flow'][f['index']]
        marks.append(f"flow:{f['index']} = the cyan line{'s' if len(ways_of(f)) > 1 else ''} (each ending in a dot), meant to follow "
                     f"{flow['what']}: {flow['path']}")
    if places.get('element'):
        pts = [(x * w, y * h) for x, y in places['element']]
        draw.line(pts, fill=(255, 0, 255), width=6)
        element = cut['change']['element']
        marks.append(f"element = the magenta line, the path {element['what']} will be drawn moving along (it is not in the picture - it is "
                     f"drawn over it later; judge only whether the line runs where that path goes): {element['path']}")
    target.parent.mkdir(parents=True, exist_ok=True)
    picture.save(target)
    return marks


def colour_name(rgb):
    return {(255, 64, 64): 'red', (64, 200, 255): 'light blue', (255, 210, 0): 'yellow', (120, 255, 120): 'green',
            (255, 120, 255): 'pink', (255, 160, 60): 'orange'}.get(tuple(rgb), 'coloured')


def locate(project, cut_id, reason=None, reader=None, checker=None, force=False):
    """Locate the cut's marks on its picked first picture and judge the overlay. -> {places, check, ok}."""
    from studio.screen_grammar import codex_json
    from . import frames as frames_mod
    path = project_dir(project)
    body = frames_mod.settled(path)
    cut = frames_mod.cut_of(body, cut_id)
    first = frames_mod.picked(path, cut, 'first')
    if first is None:
        raise StudioError('FIRST_UNPICKED', f'{cut_id}: marks are located on the picked first picture', recovery=f'frames pick --cut {cut_id} --frame first ...')
    base = frames_mod.folder(path, cut) / 'locate'
    record = base / 'first.json'
    done = read_json(record) if record.is_file() else None
    asked = asked_sha(first, cut)
    same = bool(done) and done.get('asked') == asked   # the same picture and the same things to locate
    if same and not reason and not force:
        return {**done, 'cached': True}
    tries = done.get('tries', 0) if same else 0
    if reason and tries > RETRIES:
        raise StudioError('TRIES_SPENT', f'{cut_id}: located {tries} times - write up which mark keeps landing off and why')
    parts = [p['id'] for p in cut['cutaway']['parts']]
    flows = list(range(len(cut['cutaway']['flow'])))
    prompt = PROMPT.format(items=_items(cut), reason=f'\nThe last try was wrong: {reason}. Correct that.' if reason else '')
    target = base / f'read_{tries + 1}.json'
    (reader or codex_json)([first], prompt, _schema(parts, flows, cut['change'].get('element'), [f.get('ways', 1) for f in cut['cutaway']['flow']]), target)
    places = read_json(target)['answer']
    marks = overlay(first, places, cut, base / 'overlay.png')
    check_schema = {'type': 'object', 'additionalProperties': False, 'required': ['marks'], 'properties': {'marks': {
        'type': 'array', 'minItems': len(marks), 'maxItems': len(marks), 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['mark', 'ok', 'why'],
            'properties': {'mark': {'enum': [m.split(' = ')[0] for m in marks] or ['none']}, 'ok': {'type': 'boolean'}, 'why': {'type': 'string'}}}}}}
    verdict = {'marks': []}
    if marks:
        ctarget = base / f'overlay_check_{tries + 1}.json'
        (checker or codex_json)([base / 'overlay.png'], CHECK.format(marks='; '.join(marks)), check_schema, ctarget)
        verdict = read_json(ctarget)['answer']
    off = [m for m in verdict['marks'] if not m['ok']]
    row = {'cut': cut_id, 'image_sha256': file_hash(first), 'asked': asked, 'tries': tries + 1, 'at': now(), 'places': places, 'check': verdict['marks'],
           'ok': not off, 'reason': reason}
    write_json(record, row)
    return {**row, 'cached': False, 'overlay': str(base / 'overlay.png'),
            'next': [f"locate again: locate --cut {cut_id} --reason \"{'; '.join(m['mark'] + ': ' + m['why'] for m in off)}\""] if off else []}


def asked_sha(first, cut):
    """What a location is of: the picture and the things located on it (the cut's parts, flows and their ways, the moving
    thing's path). Its tries are counted, and its places hold, for exactly this."""
    from studio.common import stable_hash
    return stable_hash({'image': file_hash(first), 'items': _items(cut)})[:16]


def places_of(path, cut):
    """The located places of a cut's picked first picture, for the things the cut locates now, judged on (ok), or None."""
    from . import frames as frames_mod
    first = frames_mod.picked(path, cut, 'first')
    record = frames_mod.folder(path, cut) / 'locate' / 'first.json'
    if first is None or not record.is_file():
        return None
    row = read_json(record)
    return row if row.get('asked') == asked_sha(first, cut) else None


def register_commands(subparsers):
    parser = subparsers.add_parser('locate', help="Where a cut's marks go on its picked first picture, drawn and judged")
    parser.add_argument('--project', required=True); parser.add_argument('--cut', required=True); parser.add_argument('--reason')
    parser.add_argument('--force', action='store_true')
    parser.set_defaults(handler=lambda a: locate(a.project, a.cut, a.reason, force=a.force))
    return parser
