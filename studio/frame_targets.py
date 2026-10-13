"""Frame pictures -> framing targets: where the subject sits in a shot's first and last picture, and how the camera
looks at it, as numbers the camera is solved to (camera.move.frames) and the built frames are measured against.

Why (2026-10-11): the rocket reel's 25 storyboards held the booster upright in the middle of the frame at eye level -
the shot list said "just above the waves, the booster drops into frame from the top" and the build could not read
words. The user: "샷별로 첫 장면과 마지막 장면을 그림으로 그리고 그사이를 움직임으로 메꾸고, 카메라 움직임까지 더해서".
A picture says where the subject is and how the camera looks; a model reads it (the same Codex eyes that labelled the
84 study reels, screen_grammar.codex_json) and anyone can correct the reading (`frames mark`) on the check picture.

  frames measure --shot S [--subject "the rocket booster"]     read every frame picture (cached by picture hash)
  frames mark --shot S --concept c03 [--box x0 y0 x1 y1] [--whole ...] [--horizon v] [--view-deg d] [--lens-mm mm]
  frames apply --shot S [--path auto|straight|arc] [--follow 0..1] [--must-see id ...]   write camera.move (frame_to_frame)

The pictures give composition only. The subject's shape is the Blender model's; the azimuth around the subject is not
read from a picture (a cylinder looks alike from every side) - it stays the shot's unless marked.
"""
from __future__ import annotations

import math

from .common import StudioError, file_hash, now, read_json, stable_hash, write_json
from .project import load_shot, project_dir, shot_path

LENS_MM = {'wide': 24.0, 'normal': 35.0, 'tele': 70.0}   # what "wide / normal / tele" reads as, full-frame equivalent
SENSOR_MM = 36.0
LEVEL_DEG = 5.0          # a camera within this of level is level (its sign means nothing)
CENTRE_TOL = 0.08        # built frame vs picture: the subject's centre may sit this far off (share of the frame) ...
HEIGHT_RATIO = (0.75, 1.33)   # ... and be this much smaller or larger; beyond either it is another composition
EDGES = ('top', 'bottom', 'left', 'right')
VIEW_HEIGHTS = ('down', 'level', 'up')
SIDES = ('front', 'three_quarter', 'side', 'back', 'below', 'above')


def schema():
    box = {'anyOf': [{'type': 'array', 'items': {'type': 'number'}, 'minItems': 4, 'maxItems': 4}, {'type': 'null'}]}
    picture = {'type': 'object', 'additionalProperties': False,
               'required': ['index', 'subject_box', 'whole_box', 'cut_edges', 'horizon_v', 'view_height', 'view_deg', 'side', 'lens', 'fore',
                            'subject_tilt_deg', 'shows', 'objects', 'context', 'note'],
               'properties': {'index': {'type': 'integer'}, 'subject_box': box, 'whole_box': box,
                              'cut_edges': {'type': 'array', 'items': {'enum': list(EDGES)}},
                              'horizon_v': {'type': ['number', 'null']}, 'view_height': {'enum': list(VIEW_HEIGHTS)},
                              'view_deg': {'type': 'number'}, 'side': {'enum': list(SIDES)}, 'lens': {'enum': list(LENS_MM)},
                              'fore': {'type': ['string', 'null']},
                              'subject_tilt_deg': {'type': ['number', 'null']},
                              'objects': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                                                                     'required': ['name', 'box', 'cut_edges', 'state'],
                                                                     'properties': {'name': {'type': 'string'}, 'box': box,
                                                                                    'cut_edges': {'type': 'array', 'items': {'enum': list(EDGES)}},
                                                                                    'state': {'type': ['string', 'null']}}}},
                              'shows': {'type': 'array', 'items': {'type': 'string'}},
                              'context': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['name', 'box'],
                                                                     'properties': {'name': {'type': 'string'}, 'box': box}}},
                              'note': {'type': 'string'}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['pictures'],
            'properties': {'pictures': {'type': 'array', 'items': picture}}}


PROMPT = """You read target pictures for a video. Answer only from the attached images; run no commands.
Image 1 is index 0, image 2 index 1, and so on. Each is one moment of a shot. The subject is: {subject}.
For each image, in shares of THAT image (0,0 top-left, 1,1 bottom-right):
- subject_box: [x0, y0, x1, y1] of the part of the subject you can see; null if it is not in the picture.
- whole_box: where the whole subject would be if the picture were larger - values may go below 0 or above 1 when it
  runs off an edge (estimate from its known shape); equal to subject_box when nothing of it is cut; null if absent.
- cut_edges: the edges of the image the subject runs past (top, bottom, left, right); [] if none.
- horizon_v: the height of the horizon (where sea or ground meets sky) as a share from the top; null if none is seen.
- view_height: down (the camera looks down at the subject), level, or up (it looks up at it); view_deg: about how many
  degrees from horizontal it looks (0 for level, always positive).
- side: which side of the subject we see: front | three_quarter | side | back | below | above.
- lens: wide (strong perspective, the camera is close) | normal | tele (flat, the camera is far).
- fore: what sits in the foreground between the camera and the subject (spray, steam, a beam passing), or null.
- subject_tilt_deg: how far the subject's long axis leans on the image from vertical, in degrees: 0 upright, + its top
  leans right, - left (a lying subject is about +-90); null when the subject has no long axis.
- objects: every thing the picture shows, each once - from this list by its id: {elements}; anything the picture shows
  that is not on the list as "other: <a few words>" (an extra booster, a second stage, people). For each: box [x0, y0,
  x1, y1] as it shows (null if the listed thing is not in the picture), cut_edges (the image edges it runs past), state
  (a few words on how it stands - legs folded / deployed, fins open, engines firing, steam - or null).
- shows: of these parts - {parts} - the ids the picture clearly shows and gives a real share of the frame (what the
  picture is about, not a speck at its edge); [] if none.
- context: for each of these things around the subject - {context} - its name exactly as given and its box
  [x0, y0, x1, y1] as it shows (null if it is not in the picture).
- note: one sentence on what the composition does (where the eye goes, what moves).
Return every image, in order."""
TILT_TOL_DEG = 8.0       # the subject's lean on screen may differ this much from the picture's
CONTEXT_RATIO = (0.6, 1.6)   # a thing around the subject this much smaller or larger than in the picture changes the place


def _folder(path, shot_id):
    return project_dir(path) / 'concepts' / shot_id


def _store_path(path, shot_id):
    return _folder(path, shot_id) / 'frames.json'


def _store(path, shot_id):
    file = _store_path(path, shot_id)
    return read_json(file) if file.is_file() else {'schema_version': 1, 'subject': None, 'pictures': {}}


def subject_words(path, shot):
    """What the reader looks for: the world's description of the shot's subject element(s), else its subject ids."""
    from . import world as world_mod
    world = world_mod.load(path) or {}
    elements = {e['id']: e for e in world.get('elements', [])}
    ids = (shot.get('screen') or {}).get('subject') or []
    if any('/' in i for i in ids):   # parts of a subject: the world's words describe the whole, which is not what is framed
        raise StudioError('INPUT_INVALID', f"{shot['shot_id']} frames parts {ids}: say what they are in the picture's words "
                          '(frames measure --subject "the centre engine and its two hydraulic arms")')
    named = [elements[i]['description'].split(';')[0] for i in ids if i in elements]
    if named:
        return ' and '.join(named)
    if ids:
        return ' and '.join(i.replace('_', ' ') for i in ids)
    raise StudioError('INPUT_INVALID', f"{shot['shot_id']}: say what the subject is (--subject \"...\"); the shot names no screen.subject")


def context_elements(path, shot):
    """The shot's set and context elements the build has linked to scene ids: {name (the world's words): ids}. Their
    size in a picture says how near the place is (a ship half the frame tall is close, a dot is far)."""
    from . import world as world_mod
    world = world_mod.load(path) or {}
    out = {}
    for element in world.get('elements', []):
        if element['id'] in ((shot.get('screen') or {}).get('elements') or []) and element.get('function') in ('set', 'context') \
                and element.get('ids'):
            out[element['description'].split(',')[0]] = list(element['ids'])
    return out


LAYOUT_IOU = 0.5        # a thing in the picture and in the build is in the same place when their boxes overlap this much
EXTRA_SHARE = 0.10      # a thing the build shows this tall (share of the frame) that the picture does not draw is a difference
LAYOUT_MIN = 0.7        # share of the frame's things (by area) that must match for the moment to read as its picture
LAYOUT_FUNCTIONS = ('subject', 'set', 'context', 'phenomenon')   # world element functions a picture can draw


def layout_elements(path, shot):
    """The things a picture of this shot can show, {element id: {'words', 'ids'}}: the shot's world elements whose scene
    ids the build can measure (a subject element by its own id when the shot builds it, the others by their linked ids).
    Why (2026-10-12, rocket_landing s03): the booster sat exactly as drawn while the second stage above it - drawn by no
    picture - ran off the top of the frame; only the subject was measured, so the moment read "matches"."""
    from . import world as world_mod
    world = world_mod.load(path) or {}
    shown = set((shot.get('screen') or {}).get('elements') or [])
    built_subjects = {s['subject_id'] for s in shot.get('subjects') or []}
    out = {}
    for element in world.get('elements', []):
        if element['id'] not in shown or element.get('function') not in LAYOUT_FUNCTIONS:
            continue
        ids = list(element.get('ids') or ([element['id']] if element['id'] in built_subjects else []))
        if ids:
            out[element['id']] = {'words': element['description'].split(';')[0], 'ids': ids}
    return out


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _cut(box):
    return sorted(e for e, hit in zip(EDGES, (box[1] <= 0.002, box[3] >= 0.998, box[0] <= 0.002, box[2] >= 0.998)) if hit)


def compare_layout(drawn, built, at=None):
    """The whole frame against its picture, thing by thing: `drawn` the picture's objects reading [{name, box,
    cut_edges, state}], `built` {element id: box or None} from the build's id pass. Returns {score, rows, problems}:
    each thing in both (same place: box IoU >= LAYOUT_IOU, the same edges cut), only in the build (at least EXTRA_SHARE
    of the frame tall), only in the picture (an 'other:' thing the scene does not have, or a listed one the build does
    not show). score: the matched things' share of all the things' area."""
    t = f't={at}: ' if at is not None else ''
    rows, problems, matched, total = [], [], 0.0, 0.0
    drawn_by = {o['name']: o for o in drawn or [] if o.get('box')}
    area = lambda b: max(0.0, min(1.0, b[2]) - max(0.0, b[0])) * max(0.0, min(1.0, b[3]) - max(0.0, b[1]))  # noqa: E731
    for name in sorted(set(drawn_by) | {k for k, v in (built or {}).items() if v}):
        d, b = drawn_by.get(name), (built or {}).get(name)
        if d and b:
            iou = round(_iou(d['box'], b), 3)
            same_cut = sorted(d.get('cut_edges') or []) == _cut(b)
            ok = iou >= LAYOUT_IOU and same_cut
            rows.append({'name': name, 'kind': 'both', 'iou': iou, 'drawn_cut': sorted(d.get('cut_edges') or []), 'built_cut': _cut(b), 'ok': ok})
            weight = max(area(d['box']), area(b))
            total += weight; matched += weight if ok else 0.0
            if not ok:
                problems.append(f"{t}{name} overlaps its drawn place {iou:.2f} (needs {LAYOUT_IOU})" if iou < LAYOUT_IOU else
                                f"{t}{name} runs past {_cut(b) or 'no edge'}, in the picture past {sorted(d.get('cut_edges') or []) or 'no edge'}")
        elif b:
            tall = b[3] - b[1]
            rows.append({'name': name, 'kind': 'built_only', 'height': round(tall, 3), 'built_cut': _cut(b), 'ok': tall < EXTRA_SHARE})
            if tall >= EXTRA_SHARE:
                total += area(b)
                problems.append(f"{t}the build shows {name} ({tall:.2f} of the frame tall{', past ' + '/'.join(_cut(b)) if _cut(b) else ''}); "
                                'the picture does not draw it')
        elif name.startswith('other:') or name in (built or {}):
            rows.append({'name': name, 'kind': 'drawn_only', 'ok': False})
            total += area(d['box'])
            problems.append(f"{t}the picture draws {name.removeprefix('other:').strip()} at {[round(v, 2) for v in d['box']]}; "
                            + ('the scene has no such thing' if name.startswith('other:') else 'the build does not show it'))
    score = round(matched / total, 3) if total > 0 else None
    return {'score': score, 'rows': rows, 'problems': problems}


def part_candidates(path, shot):
    """What a picture may show, {id: words}: the shot's screen.details, and one id per feature of its subjects' specs
    (the feature's first part, in the feature's words) - so what a picture shows can become what the build must show."""
    out = {d['id']: d.get('why') or d['id'] for d in (shot.get('screen') or {}).get('details', [])}
    from .subjects import spec_path
    for entry in shot.get('subjects') or []:
        sid = entry.get('subject_id')
        file = spec_path(path, sid) if sid else None
        if not file or not file.is_file():
            continue
        for feature in read_json(file).get('features', []):
            if feature.get('part_ids'):
                out.setdefault(f"{sid}/{feature['part_ids'][0]}", f"{feature['id']}: {feature.get('description') or ''}".strip())
    return out


def measure(project, shot_id, subject=None, force=False):
    """Read every frame picture of the shot (one Codex call for the new ones), keep the readings by picture hash, and
    draw the check picture (boxes and horizon on each)."""
    from .concept import frames
    from .screen_grammar import codex_json
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    pictures = frames(path, shot_id)
    if not pictures:
        raise StudioError('FRAME_PICTURES_MISSING', f'{shot_id}: no frame pictures',
                          recovery='make the first and last picture (Codex image tool), then concept add --frame start|end')
    words = subject or subject_words(path, shot)
    around = context_elements(path, shot)
    store = _store(path, shot_id)
    if store.get('subject') != words or sorted(store.get('context') or []) != sorted(around):
        force = True
    def kept(p):   # a reading made by hand or from the build (provenance) stays while the picture is the same (the user's choice, 2026-10-12)
        row = store['pictures'].get(p['concept_id']) or {}
        return bool(row.get('provenance')) and row.get('sha256') == p['sha256']
    todo = [p for p in pictures if not kept(p) and (force or (store['pictures'].get(p['concept_id']) or {}).get('sha256') != p['sha256'])]
    if todo:
        key = stable_hash({'subject': words, 'context': sorted(around), 'parts': sorted(part_candidates(path, shot)),
                           'elements': sorted(layout_elements(path, shot)),
                           'pictures': [p['sha256'] for p in todo]})[:16]
        out = _folder(path, shot_id) / 'frames_labels' / f'{key}.json'
        parts = part_candidates(path, shot)
        things = layout_elements(path, shot)
        prompt = PROMPT.format(subject=words, context='; '.join(f'"{n}"' for n in around) or 'none',
                               elements='; '.join(f'{k} ({v["words"]})' for k, v in things.items()) or 'none',
                               parts='; '.join(f'{i} ({w})' for i, w in parts.items()) or 'none')
        answer = read_json(out) if out.is_file() else codex_json([path / p['path'] for p in todo], prompt, schema(), out)
        readings = {r['index']: r for r in answer['answer']['pictures']}
        for i, picture in enumerate(todo):
            if i not in readings:
                raise StudioError('LABELLER_FAILED', f"{shot_id}: the reader skipped picture {picture['concept_id']}", retryable=True)
            reading = {k: v for k, v in readings[i].items() if k != 'index'}
            old = store['pictures'].get(picture['concept_id']) or {}
            same = old.get('sha256') == picture['sha256']   # the same picture read again: what was recorded about it stays
            store['pictures'][picture['concept_id']] = {
                'sha256': picture['sha256'], 'frame': picture['frame'], 'read': reading, 'read_at': now(), 'labels': str(out.relative_to(path)),
                **({k: old[k] for k in ('marked', 'departs', 'reviews') if k in old} if same else {})}
    store['subject'], store['context'] = words, sorted(around)
    write_json(_store_path(path, shot_id), store)
    check = check_picture(path, shot_id)
    return {'shot_id': shot_id, 'subject': words, 'read': [p['concept_id'] for p in todo], 'targets': targets(path, shot_id),
            'check': str(check), 'artifacts': [str(_store_path(path, shot_id)), str(check)]}


def mark(project, shot_id, concept_id, box=None, whole=None, horizon=None, view_deg=None, lens_mm=None, azimuth_deg=None, note=None,
         reading=None, subject=None, tilt_deg=None, shows=None, objects=None):
    """Correct a reading by hand (a person or the agent looking at the check picture): what is given replaces the
    reading, the rest stays; `view_deg` is signed (+ looks down, - looks up). A complete explicit manual reading can
    initialize a picture when the automated reader is unavailable; its provenance never claims automated measurement."""
    from .concept import frames
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    picture = next((p for p in frames(path, shot_id) if p['concept_id'] == concept_id), None)
    if picture is None:
        raise StudioError('INPUT_INVALID', f'{shot_id}: {concept_id} is not a current frame picture')
    if file_hash(path / picture['path']) != picture['sha256']:
        raise StudioError('CONCEPT_STALE', f'{shot_id}: {concept_id} changed after it was added')
    store = _store(path, shot_id)
    row = store['pictures'].get(concept_id)
    if reading is not None:
        from jsonschema import Draft202012Validator
        if not isinstance(reading, dict):
            raise StudioError('INPUT_INVALID', 'frames mark --reading: a JSON object is required')
        manual_schema = schema()['properties']['pictures']['items']
        errors = list(Draft202012Validator(manual_schema).iter_errors({'index': 0, **reading}))
        if errors:
            raise StudioError('INPUT_INVALID', f'frames mark --reading: {errors[0].message}')
        if not note or not note.strip():
            raise StudioError('INPUT_INVALID', 'a manual reading needs --note describing who read the picture and why')
        for key, b in [(k, reading[k]) for k in ('subject_box', 'whole_box')] + [(c['name'], c['box']) for c in reading['context']]:
            if b is not None and (not all(math.isfinite(v) for v in b) or b[2] <= b[0] or b[3] <= b[1]):
                raise StudioError('INPUT_INVALID', f'{key}: finite [x0, y0, x1, y1] with x1 > x0 and y1 > y0')
        if not math.isfinite(reading['view_deg']) or (reading['horizon_v'] is not None and not math.isfinite(reading['horizon_v'])):
            raise StudioError('INPUT_INVALID', 'manual view_deg and horizon_v must be finite')
        words = subject or store.get('subject') or subject_words(path, shot)
        around = sorted(context_elements(path, shot))
        if sorted(c['name'] for c in reading['context']) != around:
            raise StudioError('INPUT_INVALID', f'manual context must name every set/context element: {around} (box null when absent)')
        if store['pictures'] and store.get('subject') != words:
            raise StudioError('INPUT_INVALID', 'manual reading subject differs from the existing readings; frames measure --subject first')
        row = {'sha256': picture['sha256'], 'frame': picture['frame'], 'read': reading, 'read_at': now(),
               'provenance': {'kind': 'manual', 'note': note.strip(), 'reading_sha256': stable_hash(reading)},
               **({'departs': row['departs']} if row and row.get('sha256') == picture['sha256'] and row.get('departs') else {})}
        store['subject'], store['context'] = words, around
    elif row is None or row.get('sha256') != picture['sha256']:
        raise StudioError('INPUT_INVALID', f'{shot_id}: no current reading of {concept_id}; frames measure first, or give '
                          'a complete --reading JSON with --subject and --note')
    given = {'subject_box': box, 'whole_box': whole, 'horizon_v': horizon, 'view_deg_signed': view_deg, 'lens_mm': lens_mm,
             'azimuth_deg': azimuth_deg, 'subject_tilt_deg': tilt_deg, 'shows': list(shows) if shows is not None else None,
             'objects': list(objects) if objects is not None else None}
    for key in ('subject_box', 'whole_box'):
        if given[key] is not None and (len(given[key]) != 4 or given[key][2] <= given[key][0] or given[key][3] <= given[key][1]):
            raise StudioError('INPUT_INVALID', f'{key}: [x0, y0, x1, y1] with x1 > x0 and y1 > y0')
    changed = {k: v for k, v in given.items() if v is not None}
    if horizon == 'none':
        changed['horizon_v'] = None
    if not changed and reading is None:
        raise StudioError('INPUT_INVALID', 'frames mark: give at least one of --box --whole --horizon --view-deg --lens-mm --azimuth-deg '
                          '--tilt-deg --shows --objects')
    if changed:
        row['marked'] = {**(row.get('marked') or {}), **changed, 'at': now(), **({'note': note} if note else {})}
    store['pictures'][concept_id] = row
    write_json(_store_path(path, shot_id), store)
    check = check_picture(path, shot_id)
    return {'shot_id': shot_id, 'concept_id': concept_id, 'marked': row.get('marked'),
            **({'provenance': row['provenance']} if row.get('provenance') else {}), 'targets': targets(path, shot_id), 'check': str(check)}


DEPART_ITEMS = ('place', 'size', 'edges', 'angle', 'context', 'tilt')   # what a built moment may keep different from its picture
MAX_DEPARTS_PER_PICTURE = 2      # beyond this the picture is no longer the target: redraw it (or the user says so)
VIEWPOINT_ITEMS = ('angle', 'tilt')   # the picture's viewpoint itself: kept different only in the user's words


def depart(project, shot_id, concept_id, item, why, user_words=None):
    """Keep one difference from a picture on purpose, with the reason (the shot's intent reads better another way):
    s12, 2026-10-11 - the picture shows the body's length, the narration says three of nine engines slow it, read best
    from straight below. The sheet shows every departure; must-see parts and the subject being on screen never depart.
    At most MAX_DEPARTS_PER_PICTURE per picture, and never the viewpoint (angle, tilt), without the user's words. Why
    (2026-10-11): s06's last picture - the pipe arriving at the engines - had size, place and edges departed; the built
    frame showed no engine and still read "matches". The user: "그냥 처음 샷이랑 끝 샷을 각각 보고, 그대로 적용한다음
    카메라 움직임을 정하면 안되는거야?" - the pictures are applied as they are; a picture that cannot be is redrawn."""
    if item not in DEPART_ITEMS:
        raise StudioError('INPUT_INVALID', f'frames depart --item {item!r}: one of {DEPART_ITEMS} (must-see parts never depart)')
    if not why or len(why.strip()) < 12:
        raise StudioError('INPUT_INVALID', 'frames depart needs --why: what the shot shows better this way (the narration, the intent)')
    path = project_dir(project)
    store = _store(path, shot_id)
    row = store['pictures'].get(concept_id)
    if row is None:
        raise StudioError('INPUT_INVALID', f'{shot_id}: no reading of {concept_id}; frames measure first')
    kept = [d for d in row.get('departs') or [] if d['item'] != item]
    words = (user_words or '').strip()
    if not words and item in VIEWPOINT_ITEMS:
        raise StudioError('FRAME_DEPART_LIMIT', f"{shot_id} {concept_id}: the picture's {item} is its viewpoint - match it (frames apply "
                          "rolls and turns the camera), else redraw the picture; keeping it different needs the user's words (--user-words)")
    loose = [d for d in kept if not d.get('user_words')]   # the user's own calls do not count against the limit
    if not words and len(loose) + 1 > MAX_DEPARTS_PER_PICTURE:
        raise StudioError('FRAME_DEPART_LIMIT', f"{shot_id} {concept_id}: already {[d['item'] for d in loose]} kept different; a third makes "
                          'the picture no target at all - in order: the camera (angle, lens, roll), the scene at that moment (where the '
                          "subject is, its pose), then redraw the picture; or the user's words (--user-words)")
    row['departs'] = kept + [{'item': item, 'why': why.strip(), 'at': now(), **({'user_words': words} if words else {})}]
    write_json(_store_path(path, shot_id), store)
    return {'shot_id': shot_id, 'concept_id': concept_id, 'departs': row['departs']}


def derive(project, shot_id, concept_id):
    """Read a picture painted over the built frame from the build itself (frames.md: draw over the model): its things'
    boxes from the id pass, the camera's angle, lens and lean from the move report - exact, no labeller. The picture then
    differs from its build only in look; what it was painted over is kept on the concept (derived_from)."""
    from .concept import frames
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    picture = next((c for c in frames(path, shot_id) if c['concept_id'] == concept_id), None)
    if picture is None or not picture.get('derived_from'):
        raise StudioError('INPUT_INVALID', f'{shot_id}: {concept_id} is not a current frame picture painted over a build (concept add --derived-from)')
    version = shot.get('scene_version')
    folder = shot_path(path, shot_id).parent / 'versions' / str(version)
    probe = read_json(folder / 'frame_report.json') if (folder / 'frame_report.json').is_file() else {}
    frame = round(picture['frame']['at'] * (shot['duration_frames'] - 1))
    probed = {f['frame']: f for f in probe.get('frames', [])}
    if frame not in probed:
        raise StudioError('INPUT_INVALID', f'{shot_id} {version}: frame {frame} was not probed; rebuild with the move through this picture')
    rows = (read_json(folder / 'camera_move_report.json') if (folder / 'camera_move_report.json').is_file() else {}).get('frames') or []
    row = min(rows, key=lambda r: abs(r['frame'] - frame)) if rows else {}
    move = (shot.get('camera') or {}).get('move') or {}
    params = move.get('params') or {}
    ids = (shot.get('screen') or {}).get('subject') or list(params.get('parts') or [params.get('target')])
    box = _box_at(probed[frame], ids)
    if box is None:
        raise StudioError('INPUT_INVALID', f'{shot_id}: the subject is not on screen at frame {frame}')
    view = row.get('view') or [0.0, float(params.get('azimuth_deg', 0.0))]
    lens = float(row.get('lens_mm') or 35.0)
    whole = (row.get('achieved') or {}).get('box') or box
    reading = {'subject_box': [round(v, 4) for v in box], 'whole_box': [round(v, 4) for v in whole], 'cut_edges': _cut(box),
               'horizon_v': None, 'view_height': 'level' if abs(view[0]) < LEVEL_DEG else ('down' if view[0] > 0 else 'up'),
               'view_deg': abs(round(view[0], 2)), 'side': 'side', 'lens': min(LENS_MM, key=lambda k: abs(LENS_MM[k] - lens)),
               'fore': None, 'subject_tilt_deg': row.get('tilt_deg'), 'shows': list(row.get('must_see') or []),
               'objects': [{'name': eid, 'box': b, 'cut_edges': _cut(b) if b else [], 'state': None}
                           for eid, info in layout_elements(path, shot).items() for b in [_box_at(probed[frame], info['ids'], fallback=False)]],
               'context': [{'name': name, 'box': _box_at(probed[frame], cids, fallback=False)} for name, cids in context_elements(path, shot).items()],
               'note': f'painted over {version} frame {frame}'}
    store = _store(path, shot_id)
    store['pictures'][concept_id] = {'sha256': picture['sha256'], 'frame': picture['frame'], 'read': reading, 'read_at': now(),
                                     'provenance': {'kind': 'derived', 'version': version, 'frame': frame,
                                                    'painted_over': picture['derived_from']},
                                     'marked': {'view_deg_signed': round(view[0], 2), 'lens_mm': lens, 'azimuth_deg': round(view[1], 2),
                                                'at': now(), 'note': 'from the build it was painted over'}}
    write_json(_store_path(path, shot_id), store)
    return {'shot_id': shot_id, 'concept_id': concept_id, 'version': version, 'frame': frame, 'reading': reading, 'targets': targets(path, shot_id)}


REVIEW_ITEMS = ('objects', 'extent', 'state', 'viewpoint')   # what an eye checks between a picture and its built moment


def review(project, shot_id, concept_id, answers, why, by, version=None):
    """Record an eye's verdict on a picture against the built moment, side by side: the same things (objects), shown
    as far (extent, what is cut), standing the same way (state: legs, fins, flames), from the same viewpoint. Why
    (2026-10-12): s03, s04 and s21 read "matches" on every number while the user saw a cut-off rocket, deployed legs on
    the pad and another viewpoint; numbers measure what is declared, an eye sees the frame."""
    from .project import load_shot
    path = project_dir(project)
    unknown = sorted(set(answers) - set(REVIEW_ITEMS))
    if unknown or set(REVIEW_ITEMS) - set(answers):
        raise StudioError('INPUT_INVALID', f'frames review answers every one of {REVIEW_ITEMS} with yes/no (got {sorted(answers)})')
    if not why or len(why.strip()) < 12:
        raise StudioError('INPUT_INVALID', 'frames review needs --why: what differs, or what was compared')
    store = _store(path, shot_id)
    row = store['pictures'].get(concept_id)
    if row is None:
        raise StudioError('INPUT_INVALID', f'{shot_id}: no reading of {concept_id}')
    version = version or load_shot(path, shot_id).get('scene_version')
    entry = {'version': version, **{k: bool(answers[k]) for k in REVIEW_ITEMS}, 'why': why.strip(), 'by': by, 'at': now()}
    row['reviews'] = [r for r in row.get('reviews') or [] if r.get('version') != version] + [entry]
    write_json(_store_path(path, shot_id), store)
    return {'shot_id': shot_id, 'concept_id': concept_id, 'review': entry, 'matches': all(entry[k] for k in REVIEW_ITEMS)}


def undepart(project, shot_id, concept_id, items=None):
    """Take departures back (all, or the named items): the picture is applied as drawn again."""
    path = project_dir(project)
    store = _store(path, shot_id)
    row = store['pictures'].get(concept_id)
    if row is None:
        raise StudioError('INPUT_INVALID', f'{shot_id}: no reading of {concept_id}')
    row['departs'] = [d for d in row.get('departs') or [] if items and d['item'] not in items]
    write_json(_store_path(path, shot_id), store)
    return {'shot_id': shot_id, 'concept_id': concept_id, 'departs': row['departs']}


def abandoned(departs):
    """A picture departed from beyond the limit (or on its viewpoint) without the user's words: no longer a target."""
    loose = [d for d in departs or [] if not d.get('user_words')]
    return len(loose) > MAX_DEPARTS_PER_PICTURE or any(d['item'] in VIEWPOINT_ITEMS for d in loose)


def tangent_v(lens_mm, aspect):
    """tan of half the vertical view (sensor fitted to the long side, as Blender's AUTO)."""
    tan_long = SENSOR_MM / (2 * lens_mm)
    return tan_long if aspect < 1 else tan_long / aspect


def elevation_from_horizon(horizon_v, lens_mm, aspect):
    """The camera's elevation (camera_frame_core.basis: + above the subject looking down) from where a level horizon
    crosses the frame: a camera pitched up by p sees it at 0.5 + tan(p) / (2 tan(half vertical view))."""
    pitch_up = math.degrees(math.atan((horizon_v - 0.5) * 2 * tangent_v(lens_mm, aspect)))
    return -pitch_up


def target_of(reading, marked, size, azimuth_deg):
    """One picture's reading (+ hand marks) -> {height_share, centre, view, lens_mm, check}. The whole subject's box
    places the camera (a subject running off the top is solved with its centre above the frame); the visible box and
    cut edges are what the built frame is checked against."""
    r = {**reading, **{k: v for k, v in (marked or {}).items() if k not in ('at', 'note')}}
    box = r.get('subject_box')
    if box is None:
        return None
    if (marked or {}).get('subject_box'):   # a hand-marked box says by itself which edges it runs past
        r['cut_edges'] = [e for e, hit in zip(EDGES, (box[1] <= 0.005, box[3] >= 0.995, box[0] <= 0.005, box[2] >= 0.995)) if hit]
    whole = r.get('whole_box') or box
    aspect = size[0] / size[1]
    lens = float(r.get('lens_mm') or LENS_MM[r['lens']])
    if r.get('view_deg_signed') is not None:
        elevation, source = float(r['view_deg_signed']), 'marked'
    elif r.get('horizon_v') is not None:
        elevation, source = elevation_from_horizon(r['horizon_v'], lens, aspect), 'horizon'
    else:
        elevation = {'up': -1, 'down': 1, 'level': 0}[r['view_height']] * abs(float(r['view_deg']))
        source = 'read'
    cut = set(r.get('cut_edges') or [])
    edges = {name: round(box[i], 4) for i, name in enumerate(('left', 'top', 'right', 'bottom')) if name not in cut} if cut and not \
        (marked or {}).get('whole_box') else None   # cut by the frame: place the edges it shows, not a guessed whole
    return {'height_share': round(whole[3] - whole[1], 4),
            'centre': [round((whole[0] + whole[2]) / 2, 4), round((whole[1] + whole[3]) / 2, 4)],
            **({'edges': edges} if edges else {}),
            'view': {'elevation_deg': round(elevation, 2), 'azimuth_deg': float(r.get('azimuth_deg', azimuth_deg))},
            'lens_mm': lens, 'elevation_from': source,
            'context': {c['name']: c['box'] for c in r.get('context') or [] if c.get('box')},
            'tilt_deg': r.get('subject_tilt_deg'), 'shows': list(r.get('shows') or []), 'objects': list(r.get('objects') or []),
            'check': {'box': [round(v, 4) for v in box], 'cut_edges': sorted(r.get('cut_edges') or [])}}


def targets(project, shot_id):
    """Every read frame picture as a framing target, in time order: [{at, role, concept_id, sha256, ...target_of}]."""
    from .concept import frames
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    store = _store(path, shot_id)
    move = (shot.get('camera') or {}).get('move') or {}
    azimuth = float((move.get('params') or {}).get('azimuth_deg', 0.0))
    held = {(f.get('picture') or {}).get('concept_id'): f['view'].get('azimuth_deg') for f in move.get('frames') or []}
    if move.get('frames'):   # a frame_to_frame move already holds the side it looks from (pictures do not tell it)
        azimuth = float(move['frames'][0]['view'].get('azimuth_deg', azimuth))
    out = []
    for picture in frames(path, shot_id):
        row = store['pictures'].get(picture['concept_id'])
        if not row or row['sha256'] != picture['sha256']:
            continue
        side = held.get(picture['concept_id'])
        target = target_of(row['read'], row.get('marked'), picture['size'], float(side) if side is not None else azimuth)
        if target:
            out.append({'at': picture['frame']['at'], 'role': picture['frame']['role'], 'concept_id': picture['concept_id'],
                        'sha256': picture['sha256'], **target, **({'departs': row['departs']} if row.get('departs') else {}),
                        **({'reviews': row['reviews']} if row.get('reviews') else {})})
    return out


def apply(project, shot_id, path_kind='auto', follow=0.5, target=None, must_see=None, pace='subject'):
    """Write the shot's camera as a frame_to_frame move through its picture targets (style, timing, guards and parts
    carry over). The last picture must show every screen.details part unless `must_see` says otherwise (a shot ends on
    what it explains). A rebuild chooses each view near its picture; storyboard propose shows pictures against frames."""
    from .routing import _write_shot
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    wanted = targets(path, shot_id)
    if len(wanted) < 2:
        raise StudioError('FRAME_PICTURES_MISSING', f'{shot_id}: {len(wanted)} read frame picture(s); the camera needs a first and a last',
                          recovery='concept add --frame start / --frame end, then frames measure')
    on_frames = [round(t['at'] * (shot['duration_frames'] - 1)) for t in wanted]
    if len(set(on_frames)) < len(on_frames):   # two poses on one frame: the camera would have to be in two places
        raise StudioError('FRAME_KEYS_COINCIDE', f"{shot_id}: pictures at {[t['at'] for t in wanted]} fall on frames {on_frames} of "
                          f"{shot['duration_frames']}; two land on the same frame", recovery='move a picture (concept add --at) or lengthen the shot')
    old = (shot.get('camera') or {}).get('move') or {}
    params = old.get('params') or {}
    subject = target or (params.get('target') if isinstance(params.get('target'), str) else None) \
        or next(iter((shot.get('screen') or {}).get('subject') or []), None)
    if not subject:
        raise StudioError('INPUT_INVALID', f'{shot_id}: name the scene group the pictures frame (--target)')
    parts = params.get('parts') if old.get('type') == 'frame_to_frame' else (old.get('focus') or {}).get('parts')
    details = [d['id'] for d in (shot.get('screen') or {}).get('details', [])]
    last = details if must_see is None else list(must_see)
    unknown = sorted(set(last) - set(details))
    if unknown:
        raise StudioError('INPUT_INVALID', f'{shot_id}: must-see {unknown} are not screen.details (they carry the min_px they must show at)')
    undeclared = sorted({i for t in wanted for i in t.get('shows') or []} - set(details))
    if undeclared:   # what a picture shows is what the build must show: declared, with the size it shows at
        raise StudioError('FRAME_SHOWS_UNDECLARED', f'{shot_id}: the pictures show {undeclared}, which screen.details does not name',
                          recovery='add each to screen.details with min_px and why (what the picture shows it for), or correct the '
                                   'reading (frames mark --shows ...)')
    move = {'type': 'frame_to_frame', 'params': {'target': subject, 'path': path_kind, 'follow': float(follow), 'pace': pace,
                                                 **({'parts': list(parts)} if parts else {})},
            'frames': [{'at': t['at'], 'height_share': t['height_share'], 'centre': t['centre'], 'view': t['view'], 'lens_mm': t['lens_mm'],
                        **({'edges': t['edges']} if t.get('edges') else {}),
                        **({'tilt_deg': t['tilt_deg']} if t.get('tilt_deg') is not None else {}),
                        **({'must_see': seen} if (seen := sorted(set(t.get('shows') or []) | (set(last) if n == len(wanted) - 1 else set())))
                           else {}),
                        'picture': {'concept_id': t['concept_id'], 'sha256': t['sha256']}} for n, t in enumerate(wanted)],
            **{k: old[k] for k in ('style', 'timing', 'guards', 'clearance_m', 'motion_blur_shutter') if k in old}}
    updated = dict(shot)
    updated['camera'] = {**shot['camera'], 'movement': 'rig', 'keys': [], 'move': move}
    updated['camera'].pop('rig', None)
    _write_shot(path, shot_id, updated, shot['revision'])
    return {'shot_id': shot_id, 'move': move, 'shot': str(shot_path(path, shot_id))}


def compare(target, box, elevation_deg=None, must_see_px=None, context_boxes=None, tilt_deg=None):
    """Problems of a built frame against its picture target: subject centre, size, cut edges, camera up/down; the
    must-see parts' thickness ({id: (px, min_px)}); the things around it against their size in the picture. What the
    picture's departures name (target['departs'], frames depart: a difference kept on purpose, with its reason) is not
    reported; a must-see part and an absent subject never depart."""
    problems = []
    for ident, (px, need) in sorted((must_see_px or {}).items()):
        if px < need:
            problems.append(('must_see', f"t={target['at']}: must-see {ident} shows {px:.0f} px thick, it needs {need} px"))
    for name, built in sorted((context_boxes or {}).items()):
        drawn = (target.get('context') or {}).get(name)
        if not drawn or not built:
            continue
        ratio = (built[3] - built[1]) / max(1e-6, drawn[3] - drawn[1])
        if not CONTEXT_RATIO[0] <= ratio <= CONTEXT_RATIO[1]:
            problems.append(('context', f"t={target['at']}: {name} {built[3] - built[1]:.2f} of the frame tall, the picture {drawn[3] - drawn[1]:.2f} - "
                            'make the subject smaller at this moment (the camera stands back), then raise the camera'))
    want = target['check']
    if box is None:
        return [f"t={target['at']}: the subject is not on screen; the picture has it at {want['box']}"]
    centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    goal = ((want['box'][0] + want['box'][2]) / 2, (want['box'][1] + want['box'][3]) / 2)
    off = math.dist(centre, goal)
    if off > CENTRE_TOL:
        problems.append(('place', f"t={target['at']}: subject centre at ({centre[0]:.2f}, {centre[1]:.2f}), the picture ({goal[0]:.2f}, {goal[1]:.2f})"))
    height, goal_h = box[3] - box[1], want['box'][3] - want['box'][1]
    if goal_h > 0 and not HEIGHT_RATIO[0] <= height / goal_h <= HEIGHT_RATIO[1]:
        problems.append(('size', f"t={target['at']}: subject {height:.2f} of the frame tall, the picture {goal_h:.2f}"))
    cut = sorted(e for e, hit in zip(EDGES, (box[1] <= 0.002, box[3] >= 0.998, box[0] <= 0.002, box[2] >= 0.998)) if hit)
    if cut != want['cut_edges']:
        problems.append(('edges', f"t={target['at']}: subject runs past {cut or 'no edge'}, in the picture past {want['cut_edges'] or 'no edge'}"))
    wanted = target['view']['elevation_deg']
    if elevation_deg is not None and abs(wanted) >= LEVEL_DEG and (abs(elevation_deg) < LEVEL_DEG or (elevation_deg > 0) != (wanted > 0)):
        look = lambda e: 'level' if abs(e) < LEVEL_DEG else ('down' if e > 0 else 'up')  # noqa: E731
        problems.append(('angle', f"t={target['at']}: the camera looks {look(elevation_deg)} ({elevation_deg:.0f} deg), the picture {look(wanted)} ({wanted:.0f} deg)"))
    if target.get('tilt_deg') is not None and tilt_deg is not None and abs(((tilt_deg - target['tilt_deg'] + 180) % 360) - 180) > TILT_TOL_DEG:
        problems.append(('tilt', f"t={target['at']}: the subject leans {tilt_deg:+.0f} deg on screen, in the picture {target['tilt_deg']:+.0f} deg"))
    departed = {d['item'] for d in target.get('departs') or []}
    return [text for kind, text in problems if kind not in departed or kind == 'must_see']


def _box_at(frame_row, ids, fallback=True):
    """The union box of `ids` and their descendant probe classes, else the subject class."""
    from .blender_ops.ids_core import in_group

    shapes = frame_row.get('shapes') or {}
    roots = [str(i) for i in ids]
    boxes = [shape['bbox'] for name, shape in shapes.items()
             if name.startswith('key:') and (shape or {}).get('bbox')
             and any(name[4:] == root or in_group(name[4:], root) for root in roots)]
    if not boxes and fallback and (shapes.get('subject') or {}).get('bbox'):
        boxes = [shapes['subject']['bbox']]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def moments(path, shot, frame_report, rig_report, move_report=None, version=None):
    """Each frame picture's moment in a built version: [{at, frame, concept_id, box, elevation_deg, problems}] - the
    probe's box of what shows (occlusion counted) and the rig's pitch, compared with the picture's reading."""
    move = (shot.get('camera') or {}).get('move') or {}
    if not move.get('frames'):
        return []
    read = {t['concept_id']: t for t in targets(path, shot['shot_id'])}
    params = move.get('params') or {}
    ids = list(params.get('parts') or [params.get('target')])
    probed = {f['frame']: f for f in (frame_report or {}).get('frames', [])}
    pitch = {s['frame']: s.get('pitch_deg') for s in (rig_report or {}).get('samples', [])}
    leans = {r['frame']: r.get('tilt_deg') for r in (move_report or {}).get('frames') or []}   # camera_moves: the lean as built
    out = []
    for want in move['frames']:
        picture = read.get((want.get('picture') or {}).get('concept_id'))
        frame = round(want['at'] * (shot['duration_frames'] - 1))
        if picture is None or frame not in probed:
            continue
        box = _box_at(probed[frame], ids)
        elevation = -pitch[frame] if pitch.get(frame) is not None else None
        from .blender_ops.frame_probe_core import thickness_px
        size = (frame_report or {}).get('output_size') or [1080, 1920]
        need = {d['id']: d.get('min_px', 0) for d in (shot.get('screen') or {}).get('details', [])}
        must = {i: (thickness_px((probed[frame].get('shapes') or {}).get(f'key:{i}'), size), need.get(i, 0)) for i in want.get('must_see') or []}
        around = {name: _box_at(probed[frame], ids, fallback=False) for name, ids in context_elements(path, shot).items()}
        built = {eid: _box_at(probed[frame], info['ids'], fallback=False) for eid, info in layout_elements(path, shot).items()}
        layout = compare_layout(picture.get('objects'), built, want['at']) if picture.get('objects') else None
        review = next((r for r in reversed(picture.get('reviews') or []) if version is None or r.get('version') == version), None)
        out.append({'at': want['at'], 'frame': frame, 'concept_id': picture['concept_id'], 'box': box, 'elevation_deg': elevation,
                    'problems': compare(picture, box, elevation, must, around, leans.get(frame)), 'departs': picture.get('departs') or [],
                    'abandoned': abandoned(picture.get('departs')), 'layout': layout, 'review': review})
    return out


def build_lines(path, shot, frame_report, rig_report, move_report=None):
    """FRAME_TARGET_MISS, FRAME_LAYOUT_MISS and FRAME_PICTURE_ABANDONED lines (taste) for a built version."""
    rows = moments(path, shot, frame_report, rig_report, move_report)
    layout = [f"FRAME_LAYOUT_MISS: {shot['shot_id']} picture {row['concept_id']} - {row['layout']['score']} of the frame's things as "
              f"drawn (needs {LAYOUT_MIN}): " + '; '.join(row['layout']['problems'][:4]) + ' - in order: the camera, the scene at that '
              'moment, then draw the picture over the built frame (concept add --derived-from)'
              for row in rows if row['layout'] and row['layout']['score'] is not None and row['layout']['score'] < LAYOUT_MIN]
    return [f"FRAME_TARGET_MISS: {shot['shot_id']} {problem} (picture {row['concept_id']}; frames mark corrects a misread picture, "
            'frames apply rewrites the move)' for row in rows for problem in row['problems']] + [
        f"FRAME_PICTURE_ABANDONED: {shot['shot_id']} picture {row['concept_id']} kept different in {[d['item'] for d in row['departs']]} "
        f"without the user's words - more than {MAX_DEPARTS_PER_PICTURE}, or its viewpoint: apply it as drawn (camera, then the scene at "
        'that moment), else redraw it' for row in rows if row['abandoned']] + layout


def check_picture(path, shot_id):
    """The frame pictures side by side with what was read on them: the visible box (yellow), the whole subject
    (orange, may run off), the horizon (cyan) and the reading in words. People correct readings from this picture."""
    from PIL import Image, ImageDraw, ImageFont
    from .common import REPO, font_file
    from .concept import frames
    path = project_dir(path)
    store = _store(path, shot_id)
    font = ImageFont.truetype(str(font_file({}, REPO)), 18)
    h, tiles = 640, []
    for picture in frames(path, shot_id):
        with Image.open(path / picture['path']) as src:
            image = src.convert('RGB')
        image = image.resize((max(1, round(image.width * h / image.height)), h))
        pad = 80
        tile = Image.new('RGB', (image.width + 2 * pad, h + 2 * pad + 60), (24, 24, 26))
        tile.paste(image, (pad, pad))
        draw = ImageDraw.Draw(tile)
        row = store['pictures'].get(picture['concept_id'])
        to_px = lambda b: (pad + b[0] * image.width, pad + b[1] * h, pad + b[2] * image.width, pad + b[3] * h)  # noqa: E731
        words = f"{picture['frame']['role']} t={picture['frame']['at']}  {picture['concept_id']}"
        if row and row['sha256'] == picture['sha256']:
            r = {**row['read'], **(row.get('marked') or {})}
            if r.get('whole_box'):
                draw.rectangle(to_px(r['whole_box']), outline=(255, 140, 0), width=2)
            if r.get('subject_box'):
                draw.rectangle(to_px(r['subject_box']), outline=(255, 215, 0), width=4)
            if r.get('horizon_v') is not None:
                y = pad + r['horizon_v'] * h
                draw.line((pad, y, pad + image.width, y), fill=(0, 220, 255), width=3)
            t = target_of(row['read'], row.get('marked'), picture['size'], 0.0)
            if t:
                look = t['view']['elevation_deg']
                words += (f"\n{'up' if look < -LEVEL_DEG else 'down' if look > LEVEL_DEG else 'level'} {abs(look):.0f} deg "
                          f"({t['elevation_from']}), {t['lens_mm']:.0f} mm, height {t['height_share']}" + (' (marked)' if row.get('marked') else ''))
        else:
            words += '\nnot read yet (frames measure)'
        draw.multiline_text((pad, h + pad + 8), words, fill=(235, 235, 235), font=font)
        tiles.append(tile)
    if not tiles:
        raise StudioError('FRAME_PICTURES_MISSING', f'{shot_id}: no frame pictures')
    sheet = Image.new('RGB', (sum(t.width for t in tiles), max(t.height for t in tiles)), (24, 24, 26))
    x = 0
    for tile in tiles:
        sheet.paste(tile, (x, 0)); x += tile.width
    out = _folder(path, shot_id) / 'frames_check.png'
    sheet.save(out)
    return out


def register_commands(subparsers):
    parser = subparsers.add_parser('frames', help="Frame pictures -> framing targets: read where the subject sits in a shot's first and "
                                                  'last picture, correct the reading, write the camera move that reproduces them')
    commands = parser.add_subparsers(dest='frames_command', required=True)
    p = commands.add_parser('measure', help='Read every frame picture of a shot (Codex, cached by picture hash) and draw the check picture')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--subject', help='what to look for, in words (default: the world\'s subject element of the shot)')
    p.add_argument('--force', action='store_true')
    p.set_defaults(handler=lambda a: measure(a.project, a.shot, a.subject, a.force))
    p = commands.add_parser('mark', help='Correct a reading by hand after looking at the check picture')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--concept', required=True)
    p.add_argument('--box', type=float, nargs=4); p.add_argument('--whole', type=float, nargs=4)
    p.add_argument('--horizon', help='share from the top, or "none"'); p.add_argument('--view-deg', type=float, help='+ looks down, - looks up')
    p.add_argument('--lens-mm', type=float); p.add_argument('--azimuth-deg', type=float); p.add_argument('--note')
    p.add_argument('--tilt-deg', type=float, help="the subject's lean on the picture from vertical (+ top leans right)")
    p.add_argument('--shows', nargs='*', help='ids the picture clearly shows (screen.details / feature parts)')
    p.add_argument('--objects', help='JSON file: every thing the picture shows [{name, box, cut_edges, state}] (world element ids, "other: ...")')
    p.add_argument('--reading', help='JSON file containing a complete manual picture reading (schema fields without index); needs --note')
    p.add_argument('--subject', help='what the manual reading frames, in words (required for part shots without existing readings)')
    p.set_defaults(handler=lambda a: mark(a.project, a.shot, a.concept, a.box, a.whole,
                                          a.horizon if a.horizon in (None, 'none') else float(a.horizon), a.view_deg, a.lens_mm, a.azimuth_deg, a.note,
                                          read_json(a.reading) if a.reading else None, a.subject, a.tilt_deg, a.shows,
                                          read_json(a.objects) if a.objects else None))
    p = commands.add_parser('apply', help='Write camera.move as frame_to_frame through the picture targets (rebuild to solve it)')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--path', choices=('auto', 'straight', 'arc'), default='auto',
                   help='auto: an arc around the subject when the poses look from different sides, else straight')
    p.add_argument('--follow', type=float, default=0.5,
                   help='share of the subject\'s travel the camera shares: 0 keeps its place (the subject crosses the frame), 1 rides with it')
    p.add_argument('--pace', choices=('subject', 'timing'), default='subject', help='subject: the camera moves as the subject travels')
    p.add_argument('--must-see', nargs='*', help='screen.details parts the last picture must show (default all; none: give it empty)')
    p.add_argument('--target', help='the scene group the pictures frame (default: the move target or screen.subject)')
    p.set_defaults(handler=lambda a: apply(a.project, a.shot, a.path, a.follow, a.target, a.must_see, a.pace))
    p = commands.add_parser('depart', help='Keep one difference from a picture on purpose, with the reason (shown on the sheet)')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--concept', required=True)
    p.add_argument('--item', required=True, choices=DEPART_ITEMS); p.add_argument('--why', required=True)
    p.add_argument('--user-words', help="the user's own words, needed past the limit or for the viewpoint (angle, tilt)")
    p.set_defaults(handler=lambda a: depart(a.project, a.shot, a.concept, a.item, a.why, a.user_words))
    p = commands.add_parser('derive', help='Read a picture painted over the built frame (concept add --derived-from) from the build itself')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--concept', required=True)
    p.set_defaults(handler=lambda a: derive(a.project, a.shot, a.concept))
    p = commands.add_parser('review', help="An eye's verdict: the picture beside its built moment - same things, extent, state, viewpoint")
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--concept', required=True)
    for item in REVIEW_ITEMS:
        p.add_argument(f'--{item}', required=True, choices=('yes', 'no'))
    p.add_argument('--why', required=True); p.add_argument('--by', required=True, help='who looked: the reviewer agent, or "user"')
    p.set_defaults(handler=lambda a: review(a.project, a.shot, a.concept, {i: getattr(a, i) == 'yes' for i in REVIEW_ITEMS}, a.why, a.by))
    p = commands.add_parser('undepart', help='Take departures back: the picture is applied as drawn again')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--concept', required=True)
    p.add_argument('--item', action='append', choices=DEPART_ITEMS, help='only these (repeatable); default all')
    p.set_defaults(handler=lambda a: undepart(a.project, a.shot, a.concept, a.item))
    p = commands.add_parser('show')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.set_defaults(handler=lambda a: {'targets': targets(a.project, a.shot), 'store': _store(project_dir(a.project), a.shot)})
