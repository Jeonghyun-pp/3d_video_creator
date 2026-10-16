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

from .blender_ops.camera_moves_core import END_ON_DEG, LENS_CLASSES, frame_solve, intent_of as _core_intent_of, segment_of, size_class, size_kept
from .blender_ops.show_core import METHODS, at_share, held_at, how_choices, moment_at, opens, same_view, show_of
from . import cheat_core
from .common import StudioError, file_hash, now, read_json, stable_hash, write_json
from .project import load_project, load_shot, project_dir, shot_path

LENS_MM = dict(LENS_CLASSES)   # what "wide / normal / tele" reads as, full-frame equivalent (camera_moves_core: the intent's too)
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
                            'subject_tilt_deg', 'shows', 'objects', 'context', 'note', 'how', 'opened'],
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
                              'note': {'type': 'string'},
                              # how the picture shows the subject (studio/blender_ops/show_core.py) and what it opens
                              'how': {'enum': list(METHODS)}, 'opened': {'type': 'array', 'items': {'type': 'string'}}}}
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
- how: how the picture shows the subject - {how}.
- opened: of the parts listed for shows, the ids the picture shows only because it is opened or see-through (inside
  the subject); [] for an exterior picture.
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


SUBJECT_ORDER = ('subject', 'agent', 'set', 'phenomenon', 'context')   # which of a shot's elements it is about, when it builds none


def _linked_subject(path, shot):
    """The scene id of the shot's main element (SUBJECT_ORDER) once the world links it to a built stand-in, else None."""
    from . import world as world_mod
    elements = {e['id']: e for e in (world_mod.load(path) or {}).get('elements', [])}
    shown = [elements[i] for i in (shot.get('screen') or {}).get('elements') or [] if i in elements]
    for role in SUBJECT_ORDER:
        for element in shown:
            if element['function'] == role and element.get('ids'):
                return element['ids'][0]
    return None


def subject_ids(path, shot):
    """The ids subject_words describes: screen.subject, else the subjects the shot builds, else the shown elements of
    the first SUBJECT_ORDER function that has any."""
    from . import world as world_mod
    elements = {e['id']: e for e in (world_mod.load(path) or {}).get('elements', [])}
    ids = (shot.get('screen') or {}).get('subject') or [x['subject_id'] for x in shot.get('subjects') or []]
    if not ids:   # nothing built: what the shot shows, the world's subject first (a clay or generated shot still has one)
        shown = [i for i in (shot.get('screen') or {}).get('elements') or [] if i in elements]
        for role in SUBJECT_ORDER:
            ids = [i for i in shown if elements[i]['function'] == role]
            if ids:
                break
    return list(ids)


def subject_words(path, shot):
    """What the reader looks for: the world's description of the shot's subject element(s), else its subject ids."""
    from . import world as world_mod
    world = world_mod.load(path) or {}
    elements = {e['id']: e for e in world.get('elements', [])}
    ids = subject_ids(path, shot)
    if any('/' in i for i in ids):   # parts of a subject: the world's words describe the whole, which is not what is framed
        raise StudioError('INPUT_INVALID', f"{shot['shot_id']} frames parts {ids}: say what they are in the picture's words "
                          '(frames measure --subject "the centre engine and its two hydraulic arms")')
    named = [elements[i]['description'].split(';')[0] for i in ids if i in elements]
    if named:
        return ' and '.join(named)
    if ids:
        return ' and '.join(i.replace('_', ' ') for i in ids)
    raise StudioError('INPUT_INVALID', f"{shot['shot_id']}: say what the subject is (--subject \"...\"); the shot names no screen.subject")


CONTEXT_FUNCTIONS = ('set', 'context', 'agent')   # world element functions that stand around a subject (A5, 2026-10-14:
                                                  # people were left out, so a crowd drawn a third of the frame tall built a dot)


def context_elements(path, shot):
    """The shot's elements around its subject (CONTEXT_FUNCTIONS) the build has linked to scene ids: {name (the
    world's words): ids}. Their size in a picture says how near the place is (a ship half the frame tall is close, a dot
    is far). The elements the subject words describe are the subject, not around it."""
    from . import world as world_mod
    world = world_mod.load(path) or {}
    own = set(subject_ids(path, shot))
    out = {}
    for element in world.get('elements', []):
        if element['id'] in ((shot.get('screen') or {}).get('elements') or []) and element.get('function') in CONTEXT_FUNCTIONS \
                and element.get('ids') and element['id'] not in own:
            out[(element.get('description') or element['id']).split(',')[0]] = list(element['ids'])
    return out


LAYOUT_IOU = 0.5        # a thing in the picture and in the build is in the same place when their boxes overlap this much
EXTRA_SHARE = 0.10      # a thing the build shows this tall (share of the frame) that the picture does not draw is a difference
LAYOUT_MIN = 0.7        # share of the frame's things (by area) that must match for the moment to read as its picture
LAYOUT_FUNCTIONS = ('subject', 'agent', 'set', 'context', 'phenomenon')   # world element functions a picture can draw


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


def reading_gaps(path, shot, store, words=None):
    """What the shot asks of a reading of its pictures that the stored readings were not asked: {'subject': [read, now]
    or None, 'unread': [names], 'recorded': bool}. The subject words are compared with `words` (measure: given or the shot's), else with
    the shot's own words unless the readings were made for words given by hand (they say what the camera frames: a part,
    a moment). The things a picture is read for - around the subject (context_elements) and in the frame
    (layout_elements) - that the reader was not asked to place are unread: no box, so never compared. Asked is what the
    store records; a store from before that ('recorded' False) stands for it by the names its readings placed - which
    cannot tell a thing not asked from one asked and not drawn, so it is said, never acted on (measure re-reads on it
    only when recorded)."""
    if words is None and not store.get('subject_given'):
        try:
            words = subject_words(path, shot)
        except StudioError:   # parts of a subject: only words given by hand describe them
            words = None
    subject = [store.get('subject'), words] if words is not None and store.get('subject') != words else None
    asked = set(store.get('context') or [])
    if 'elements' in store:
        asked |= set(store['elements'])
    else:
        asked |= {o['name'] for row in store['pictures'].values()
                  for o in [*((row.get('read') or {}).get('objects') or []), *((row.get('marked') or {}).get('objects') or [])]}
    things, around = layout_elements(path, shot), context_elements(path, shot)
    # the subject is read as the subject (its box); a thing read both by its element id and by its words is named once
    unread = {e for e in things if e not in asked} - set(subject_ids(path, shot))
    unread |= {name for name, ids in around.items() if name not in asked
               and not any(set(things[e]['ids']) == set(ids) for e in things)}
    return {'subject': subject, 'unread': sorted(unread), 'recorded': 'elements' in store}


def measured_ids(path, shot, store):
    """The scene ids the stored readings measured as the subject (the ids the subject words were made from, through the
    world's links), or None when that is not known: words given by hand, or words the shot no longer has."""
    from . import world as world_mod
    if store.get('subject_given') or not store.get('subject'):
        return None
    try:
        if store['subject'] != subject_words(path, shot):
            return None
    except StudioError:
        return None
    elements = {e['id']: e for e in (world_mod.load(path) or {}).get('elements', [])}
    ids = store.get('subject_ids') or subject_ids(path, shot)
    return sorted({s for i in ids for s in ((elements.get(i) or {}).get('ids') or [i])})


def target_stale(path, shot, store, framed):
    """FRAME_TARGET_STALE: the line when the move frames (`framed`: move.target, or its parts) other scene ids than the
    readings measured as the subject (a spelling of one is the same thing), else None. Why (2026-10-14): a control room
    shot's pictures were read for the cheering engineers; the move framed the room, sized to the crowd's box."""
    from .blender_ops.ids_core import spellings
    measured = measured_ids(path, shot, store)
    if not measured:
        return None
    same = {s for m in measured for s in spellings(m)}
    off = [r for r in framed if r and not (set(spellings(r)) & same)]
    if not off:
        return None
    return (f"{shot['shot_id']}: the pictures were read for {store['subject']!r} ({measured}); the move frames {off} - "
            'frames apply --target one of those, or frames measure --subject "<what the camera frames>" (then frames apply)')


def reading_checks(path, shot):
    """The shot's pictures' readings against what the shot builds now, judged before the build: [{code, text}] -
    FRAME_READING_STALE (broken: read for other subject words), FRAME_READING_PARTIAL (taste: things linked after the
    reading), FRAME_READING_UNRECORDED (taste: a store too old to tell which things were asked - its own code, so a whole
    project of old stores does not read as that many shots linked late), FRAME_TARGET_STALE (broken: the move frames another thing than was read). [] for a shot without pictures.
    Why (2026-10-14): a shot's pictures were read before the structure beside its subject was linked; it stood on the
    other side of the subject in the build and nothing compared it."""
    move = (shot.get('camera') or {}).get('move') or {}
    if not move.get('frames'):
        return []
    store = _store(path, shot['shot_id'])
    if not store['pictures']:
        return []
    out, gaps = [], reading_gaps(path, shot, store)
    if gaps['subject']:
        out.append({'code': 'FRAME_READING_STALE', 'text': f"{shot['shot_id']}: the pictures were read for {gaps['subject'][0]!r}; the "
                    f"shot's subject is now {gaps['subject'][1]!r} - frames measure reads them again, then frames apply"})
    if gaps['unread'] and gaps['recorded']:
        out.append({'code': 'FRAME_READING_PARTIAL', 'text': f"{shot['shot_id']}: the pictures were read before {gaps['unread']} were "
                    'part of the shot: the reading has no box for them, so their size and side are not compared - frames measure reads them again'})
    elif gaps['unread']:
        # one short line per shot: the store's age, not this shot's fault - said, never acted on (no forced re-read)
        out.append({'code': 'FRAME_READING_UNRECORDED', 'text': f"{shot['shot_id']}: {len(gaps['unread'])} unplaced {gaps['unread']}: the store "
                    'predates recording what the reader was asked - not compared (frames measure --force)'})
    params = move.get('params') or {}
    framed = list(params.get('parts') or [params.get('target')])
    line = target_stale(path, shot, store, framed)
    if line:
        out.append({'code': 'FRAME_TARGET_STALE', 'text': line})
    out += [{'code': 'FRAME_SUBJECT_MISREAD', 'text': text} for text in _own_misreads(path, shot, framed)]
    return out


def _own_misreads(path, shot, framed):
    """FRAME_SUBJECT_MISREAD judged on the reading alone, before the build: a picture whose subject box is more than
    MISREAD_RATIO times the box the same reading gives the framed thing itself (misread's `own`)."""
    from .blender_ops.ids_core import spellings
    things = layout_elements(path, shot)
    same = {s for i in framed if i for s in spellings(i)}
    own = [e for e, info in things.items() if same & {s for i in info['ids'] for s in spellings(i)}]
    output = load_project(path)['output']
    out = []
    for t in targets(path, shot['shot_id']):
        box = next((o['box'] for o in t.get('objects') or [] if o.get('box') and o['name'] in own), None)
        why = misread(t['check']['box'], t['check']['cut_edges'], (output['width'], output['height']), own=box) if box else None
        if why:
            out.append(f"{shot['shot_id']} picture {t['concept_id']} t={t['at']}: {why} - the reader boxed more than the camera frames: "
                       'frames measure --subject "<the target in the picture\'s words>" (or frames mark --box), then frames apply')
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
    gaps = reading_gaps(path, shot, store, words)   # the same condition the build refuses or warns on (reading_checks)
    if gaps['subject'] or (gaps['unread'] and gaps['recorded']) or sorted(store.get('context') or []) != sorted(around):
        force = True
    def kept(p):   # a reading made by hand or from the build (provenance) stays while the picture is the same (the user's choice, 2026-10-12)
        row = store['pictures'].get(p['concept_id']) or {}
        return bool(row.get('provenance')) and row.get('sha256') == p['sha256']
    todo = [p for p in pictures if not kept(p) and (force or (store['pictures'].get(p['concept_id']) or {}).get('sha256') != p['sha256'])]
    if todo:
        key = stable_hash({'subject': words, 'context': sorted(around), 'parts': sorted(part_candidates(path, shot)),
                           'elements': sorted(layout_elements(path, shot)), 'prompt': stable_hash(PROMPT), 'schema': schema(),
                           'pictures': [p['sha256'] for p in todo]})[:16]
        out = _folder(path, shot_id) / 'frames_labels' / f'{key}.json'
        parts = part_candidates(path, shot)
        things = layout_elements(path, shot)
        prompt = PROMPT.format(how=how_choices(), subject=words, context='; '.join(f'"{n}"' for n in around) or 'none',
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
                **({k: old[k] for k in ('marked', 'departs', 'reviews', 'cheats', 'chosen') if k in old} if same else {})}
    store['subject'], store['context'] = words, sorted(around)
    # what the reader was asked, kept with the readings: the build compares it with what the shot asks now
    store['elements'], store['subject_given'] = sorted(layout_elements(path, shot)), bool(subject)
    store['subject_ids'] = [] if subject else subject_ids(path, shot)
    write_json(_store_path(path, shot_id), store)
    check = check_picture(path, shot_id)
    return {'shot_id': shot_id, 'subject': words, 'read': [p['concept_id'] for p in todo], 'targets': targets(path, shot_id),
            'check': str(check), 'artifacts': [str(_store_path(path, shot_id)), str(check)]}


def mark(project, shot_id, concept_id, box=None, whole=None, horizon=None, view_deg=None, lens_mm=None, azimuth_deg=None, note=None,
         reading=None, subject=None, tilt_deg=None, shows=None, objects=None, how=None, opened=None):
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
        # a picture read by hand without how / opened shows the outside (target_of reads it so)
        errors = list(Draft202012Validator(manual_schema).iter_errors({'index': 0, 'how': 'exterior', 'opened': [], **reading}))
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
               **({k: row[k] for k in ('departs', 'cheats') if row and row.get('sha256') == picture['sha256'] and row.get(k)})}
        if subject or not store.get('subject'):   # words given here, or the shot's own (else the store's stay as recorded)
            store['subject_given'] = bool(subject)
            store['subject_ids'] = [] if subject else subject_ids(path, shot)
        store['subject'], store['context'] = words, around
    elif row is None or row.get('sha256') != picture['sha256']:
        raise StudioError('INPUT_INVALID', f'{shot_id}: no current reading of {concept_id}; frames measure first, or give '
                          'a complete --reading JSON with --subject and --note')
    given = {'subject_box': box, 'whole_box': whole, 'horizon_v': horizon, 'view_deg_signed': view_deg, 'lens_mm': lens_mm,
             'azimuth_deg': azimuth_deg, 'subject_tilt_deg': tilt_deg, 'shows': list(shows) if shows is not None else None,
             'objects': list(objects) if objects is not None else None, 'how': how,
             'opened': list(opened) if opened is not None else None}
    if how is not None and how not in METHODS:
        raise StudioError('INPUT_INVALID', f'--how {how!r}: one of {list(METHODS)}')
    for key in ('subject_box', 'whole_box'):
        if given[key] is not None and (len(given[key]) != 4 or given[key][2] <= given[key][0] or given[key][3] <= given[key][1]):
            raise StudioError('INPUT_INVALID', f'{key}: [x0, y0, x1, y1] with x1 > x0 and y1 > y0')
    changed = {k: v for k, v in given.items() if v is not None}
    if horizon == 'none':
        changed['horizon_v'] = None
    if not changed and reading is None:
        raise StudioError('INPUT_INVALID', 'frames mark: give at least one of --box --whole --horizon --view-deg --lens-mm --azimuth-deg '
                          '--tilt-deg --shows --objects --how --opened')
    if changed:
        row['marked'] = {**(row.get('marked') or {}), **changed, 'at': now(), **({'note': note} if note else {})}
    store['pictures'][concept_id] = row
    write_json(_store_path(path, shot_id), store)
    check = check_picture(path, shot_id)
    return {'shot_id': shot_id, 'concept_id': concept_id, 'marked': row.get('marked'),
            **({'provenance': row['provenance']} if row.get('provenance') else {}), 'targets': targets(path, shot_id), 'check': str(check)}


CHEAT_KEYS = ('kind', 'target', 'value', 'why', 'by', 'at', 'under')   # what a stored cheat carries into move.frames[].cheats
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
    # the user left the camera's lens and angle to the agent (project.policy.viewpoint_cheat, B2 2026-10-14): a viewpoint
    # departure then needs no words per shot - it is still shown on the sheet and still counts against the limit
    policy = cheat_core.viewpoint_policy(path) if not words and item in VIEWPOINT_ITEMS else None
    if not words and item in VIEWPOINT_ITEMS and not policy:
        raise StudioError('FRAME_DEPART_LIMIT', f"{shot_id} {concept_id}: the picture's {item} is its viewpoint - match it (frames apply "
                          "rolls and turns the camera), else redraw the picture; keeping it different needs the user's words (--user-words) "
                          'or the project\'s viewpoint policy (policy.viewpoint_cheat)')
    loose = [d for d in kept if not d.get('user_words')]   # the user's own calls do not count against the limit
    if not words and len(loose) + 1 > MAX_DEPARTS_PER_PICTURE:
        raise StudioError('FRAME_DEPART_LIMIT', f"{shot_id} {concept_id}: already {[d['item'] for d in loose]} kept different; a third makes "
                          'the picture no target at all - in order: the camera (angle, lens, roll), the scene at that moment (where the '
                          "subject is, its pose), then redraw the picture; or the user's words (--user-words)")
    row['departs'] = kept + [{'item': item, 'why': why.strip(), 'at': now(), **({'user_words': words} if words else {}),
                              **({'by_policy': 'viewpoint_cheat'} if policy else {})}]
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


def abandoned(departs, policy=None):
    """A picture departed from beyond the limit (or on its viewpoint) without the user's words: no longer a target. A
    viewpoint departure made under the project's viewpoint policy (`policy`: cheat_core.viewpoint_policy, still in force)
    is allowed, and still counts against the limit."""
    loose = [d for d in departs or [] if not d.get('user_words')]
    return len(loose) > MAX_DEPARTS_PER_PICTURE or any(d['item'] in VIEWPOINT_ITEMS and not (policy and d.get('by_policy')) for d in loose)


def tangent_v(lens_mm, aspect):
    """tan of half the vertical view (sensor fitted to the long side, as Blender's AUTO)."""
    tan_long = SENSOR_MM / (2 * lens_mm)
    return tan_long if aspect < 1 else tan_long / aspect


def elevation_from_horizon(horizon_v, lens_mm, aspect):
    """The camera's elevation (camera_frame_core.basis: + above the subject looking down) from where a level horizon
    crosses the frame: a camera pitched up by p sees it at 0.5 + tan(p) / (2 tan(half vertical view))."""
    pitch_up = math.degrees(math.atan((horizon_v - 0.5) * 2 * tangent_v(lens_mm, aspect)))
    return -pitch_up


def intent_of(frame, lens_mm=None):
    """What a frame target keeps when the camera keeps its picture's intent rather than its numbers (move.params.solve
    'intent'): size class and band, side of level, lens class, cut edges, lean, must-see, show - from the frames[] row
    alone, an older one included (camera_moves_core.intent_of). The host's door to it: frames apply writes each
    target's intent with the pose chosen for it (the thumbnail sheet's pick reads it back)."""
    return _core_intent_of(frame, lens_mm)


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
    # where the frame crosses the subject (A4, 2026-10-14): the solve places these when the whole box is out of reach
    segment = segment_of(whole, cut, r.get('subject_tilt_deg'))
    return {'height_share': round(whole[3] - whole[1], 4), 'width_share': round(whole[2] - whole[0], 4),
            'centre': [round((whole[0] + whole[2]) / 2, 4), round((whole[1] + whole[3]) / 2, 4)],
            **({'edges': edges} if edges else {}), 'cut_edges': sorted(cut), **({'segment': segment} if segment else {}),
            'side': r.get('side'),
            'view': {'elevation_deg': round(elevation, 2), 'azimuth_deg': float(r.get('azimuth_deg', azimuth_deg))},
            'lens_mm': lens, 'elevation_from': source,
            'context': {c['name']: c['box'] for c in r.get('context') or [] if c.get('box')},
            'tilt_deg': r.get('subject_tilt_deg'), 'shows': list(r.get('shows') or []), 'objects': list(r.get('objects') or []),
            'how': r.get('how'), 'opened': list(r.get('opened') or []),   # None: read before pictures were read for how
            'fore': r.get('fore'),
            'check': {'box': [round(v, 4) for v in box], 'cut_edges': sorted(r.get('cut_edges') or [])}}


def picture_layout(path, shot, notes=None):
    """{concept_id: {subject: {box, cut_edges}, context: [{name, ids, box}], unnamed: [names]}} - where each read
    picture has its subject and the things round it (the reading's visible subject box, its cut edges, its context
    boxes), for the intent camera (camera_moves_core.picture_terms) in Blender. A thing is named in the scene by the
    shot's context elements (context_elements: the world's words -> scene ids); one the scene names nothing for is
    listed unnamed and left out. {} for a shot with no frame pictures, or when the readings cannot be read (the
    reason appended to `notes`: the camera then keeps the intent without the layout, never fails on it)."""
    if ((shot.get('camera') or {}).get('move') or {}).get('type') != 'frame_to_frame':
        return {}
    try:
        rows = targets(path, shot['shot_id'])   # a picture changed since its reading (another sha) is left out there
        names = context_elements(path, shot)
    except (StudioError, KeyError, ValueError, TypeError, OSError) as error:
        if notes is not None:
            notes.append(f"picture layout skipped: {type(error).__name__}: {str(error)[:160]}")
        return {}
    out = {}
    for t in rows:
        context, unnamed = [], []
        for name, box in (t.get('context') or {}).items():
            (context.append({'name': name, 'ids': names[name], 'box': box}) if names.get(name) else unnamed.append(name))
        out[t['concept_id']] = {'subject': {'box': t['check']['box'], 'cut_edges': t['check']['cut_edges']},
                                'context': context, 'unnamed': unnamed}
    return out


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
                        **({'cheats': row['cheats']} if row.get('cheats') else {}),
                        **({'chosen': row['chosen']} if row.get('chosen') else {}),
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
        or next(iter((shot.get('screen') or {}).get('subject') or []), None) \
        or next(iter(x['subject_id'] for x in shot.get('subjects') or []), None) or _linked_subject(path, shot)
    if not subject:
        raise StudioError('INPUT_INVALID', f'{shot_id}: name the scene group the pictures frame (--target); the shot builds no subject '
                          'and its main element has no scene ids yet (world link once its stand-in is built)')
    parts = params.get('parts') if old.get('type') == 'frame_to_frame' else (old.get('focus') or {}).get('parts')
    details = [d['id'] for d in (shot.get('screen') or {}).get('details', [])]
    shown_at = {round(at_share(r['at']), 4): list(r['parts']) for r in show_of(shot)}   # what each moment of the show must show
    elsewhere = {p for parts in shown_at.values() for p in parts}
    end_parts = shown_at.get(1.0, [])
    # the last picture shows what the shot explains - but a part the show places at another moment is that moment's
    last = ([d for d in details if d not in elsewhere] + end_parts) if must_see is None else list(must_see)
    unknown = sorted(set(last) - set(details))
    if unknown:
        raise StudioError('INPUT_INVALID', f'{shot_id}: must-see {unknown} are not screen.details (they carry the min_px they must show at)')
    undeclared = sorted({i for t in wanted for i in t.get('shows') or []} - set(details))
    extra = []
    from .decisions import show_decided
    if undeclared and (shot.get('show') or show_decided(path, shot_id)):
        # the shot list decided what each moment shows (show-first, 2026-10-13): what else the picture shows - a fin, a
        # weld line at the edge of a whole-body view - is noted, not made a must-see the shot list never asked for
        extra, undeclared = undeclared, []
    if undeclared:   # what a picture shows is what the build must show: declared, with the size it shows at
        raise StudioError('FRAME_SHOWS_UNDECLARED', f'{shot_id}: the pictures show {undeclared}, which screen.details does not name',
                          recovery='add each to screen.details with min_px and why (what the picture shows it for), or correct the '
                                   'reading (frames mark --shows ...)')
    # each picture is drawn the way the shot list decided its moment shows (show_core): a picture that shows it another
    # way is drawn again, never followed (2026-10-13: the order is the shot list, then the picture, then the scene)
    show = show_of(shot)   # a shot made from an earlier shot list: its direction.reveal at the end moment
    differs, unread = [], []
    for t in wanted:
        row = held_at(show, t['at'])
        decided = row['how'] if row else 'exterior'
        if t['how'] is None:   # read before pictures were read for how: an outside moment needs nothing more
            if opens(decided):
                unread.append(f"{t['concept_id']} at {t['at']} (the shot list says {decided})")
        elif not same_view(t['how'], decided):
            differs.append(f"{t['concept_id']} at {t['at']} shows it {t['how']}, the shot list says {decided}")
    if unread:
        raise StudioError('FRAME_SHOW_UNREAD', f"{shot_id}: not yet read for how they show the subject: " + '; '.join(unread),
                          recovery='frames measure --force (or frames mark --how), then frames apply')
    if differs:
        raise StudioError('FRAME_SHOW_DIFFERS', f"{shot_id}: " + '; '.join(differs),
                          recovery='draw the picture again from studio concept brief (it says how), or correct the reading (frames mark --how)')
    assigned = {p for r in show for p in r['parts']}
    musts = []
    for n, t in enumerate(wanted):
        own = set((held_at(show, t['at']) or {}).get('parts') or [])   # this moment's row, or the one it still holds
        # what the picture shows, less what the shot list shows at another moment (A3-4, 2026-10-14: the start picture of
        # s06 drew the legs the list unfolds at the end; they became a must-see of the start, where they are folded). A
        # moment inside an opening still held from before shows what that opening shows (held_at, as the build holds it)
        shows = set(t.get('shows') or []) - set(extra) - (assigned - own)
        musts.append(sorted(shows | (set(last) if n == len(wanted) - 1 else set()) | set(shown_at.get(round(t['at'], 4), []))))
    warnings = [f'FRAME_SHOWS_EXTRA: the pictures also show {extra}, which the shot list does not ask this shot to show'] if extra else []
    warnings += _view_lines(path, shot_id, show, wanted, musts)
    stale = target_stale(path, shot, _store(path, shot_id), list(parts) if parts else [subject])
    if stale:   # the pictures' boxes are another thing's: the camera would size this one to them
        from .gates import severity_for
        if severity_for(path, shot).get('FRAME_TARGET_STALE') == 'error':
            raise StudioError('FRAME_TARGET_STALE', stale)
        warnings.append(f'FRAME_TARGET_STALE: {stale}')
    _reachable(path, shot, subject, wanted, musts)
    move = {'type': 'frame_to_frame', 'params': {'target': subject, 'path': path_kind, 'follow': float(follow), 'pace': pace,
                                                 **({'parts': list(parts)} if parts else {}),
                                                 # how the frames are solved stays what the shot set (FRAME_SOLVES)
                                                 **({'solve': (old.get('params') or {})['solve']} if (old.get('params') or {}).get('solve') else {})},
            'frames': [{'at': t['at'], 'height_share': t['height_share'], 'centre': t['centre'], 'view': t['view'], 'lens_mm': t['lens_mm'],
                        **({'edges': t['edges']} if t.get('edges') else {}),
                        # what the picture cuts, for the solve (camera_moves_core.frame_segment)
                        'cut_edges': t['cut_edges'], **({'width_share': t['width_share']} if t.get('width_share', 0) > 0 else {}),
                        **({'segment': t['segment']} if t.get('segment') else {}),
                        **({'side': t['side']} if t.get('side') else {}),
                        **({'tilt_deg': t['tilt_deg']} if t.get('tilt_deg') is not None else {}),
                        **({'must_see': seen} if (seen := musts[n]) else {}),
                        **({'show': row} if (row := moment_at(show, t['at'])) else {}),
                        # the declared cheats of this picture (frames check --apply; cheat_core.CHEATS), applied by the build
                        **({'cheats': [{k: v for k, v in c.items() if k in CHEAT_KEYS} for c in t['cheats']]} if t.get('cheats') else {}),
                        # the pose picked for this moment from its candidates sheet (frames pick): the intent solve tries it first
                        **({'chosen': t['chosen']} if t.get('chosen') else {}),
                        'picture': {'concept_id': t['concept_id'], 'sha256': t['sha256']}} for n, t in enumerate(wanted)],
            **{k: old[k] for k in ('style', 'timing', 'guards', 'clearance_m', 'motion_blur_shutter') if k in old}}
    if frame_solve(move['params']) == 'intent':   # what each moment keeps when solved by its intent (an exact shot unchanged)
        for fr in move['frames']:
            fr['intent'] = intent_of(fr)
    from .gates import severity_for
    for line in cheat_core.touch_lines(path, {**shot, 'camera': {**shot['camera'], 'move': move}}):
        if severity_for(path, shot).get(line['code'], 'error') == 'error':   # a cheat on what a line says is never written
            raise StudioError(line['code'], line['text'])
        warnings.append(f"{line['code']}: {line['text']}")
    updated = dict(shot)
    updated['camera'] = {**shot['camera'], 'movement': 'rig', 'keys': [], 'move': move}
    updated['camera'].pop('rig', None)
    _write_shot(path, shot_id, updated, shot['revision'])
    return {'shot_id': shot_id, 'move': move, 'shot': str(shot_path(path, shot_id)), 'warnings': warnings}


def _view_lines(path, shot_id, show, wanted, musts):
    """SHOW_VIEW_HIDES (explains.view_line): an outside moment's must-see part that its picture's view hides, judged on
    the subject's exposure survey before the build (A3-5, 2026-10-14). A warning: the survey sees the subject unturned."""
    from .explains import view_line
    out = []
    for t, seen in zip(wanted, musts):
        row = held_at(show, t['at'])
        if row is None or not opens(row['how']):
            out += [line for ref in seen if (line := view_line(path, f"{shot_id} t={t['at']}", ref, t['view']))]
    return out


def _reachable(path, shot, subject, wanted, musts):
    """MUST_SEE_UNREACHABLE (broken): a must-see part that even the closest the camera comes cannot show at its min_px,
    refused before the build. Its most on screen: the most its thinnest side can show at (explains.part_reach_m, an
    upper bound) times the most pixels per metre any point of the subject shows at (exposure_core.most_px_per_m, with
    perspective: the near side of the subject), for the picture's framing made CLOSER_MAX larger (the camera's own
    retry, camera_moves_core) through the widest lens the pose may go down to (camera_moves_core.FLOOR_LENSES: a moment
    no view solves goes down the whole ladder past a thin part, camera_moves._pose widen - so the bound is reachable). Upper
    bounds throughout, so a refusal is never a part the build could have shown - for a subject standing as modelled
    (or laid down: the bound then widens itself). Judged only where the subject and the part have a survey.
    Why (2026-10-14): a part 12 px at the picture's framing, 32 needed, failed only after a build."""
    from .blender_ops.camera_moves_core import CLOSER_MAX, FLOOR_LENSES
    from .blender_ops.exposure_core import most_px_per_m
    from .explains import part_reach_m, subject_box
    box = subject_box(path, subject)
    if box is None:
        return
    output = load_project(path)['output']
    need = {d['id']: d.get('min_px', 0) for d in (shot.get('screen') or {}).get('details', [])}
    short = []
    for t, seen in zip(wanted, musts):
        widest = min([t['lens_mm'], *FLOOR_LENSES])
        focal = output['height'] / 2 / tangent_v(widest, output['width'] / output['height'])
        per_m = most_px_per_m(box, t['view']['elevation_deg'], t['height_share'] * output['height'] * CLOSER_MAX,
                              t.get('width_share', 0.0) * output['width'] * CLOSER_MAX, focal)
        for ref in seen:
            reach = part_reach_m(path, ref)
            if reach is not None and reach * per_m < need.get(ref, 0):
                short.append(f"{ref} at t={t['at']}: at most {reach * per_m:.0f} px of {need[ref]} (its thinnest side {reach:.3f} m, at most "
                             f"{per_m:.0f} px/m on the subject's near side, {CLOSER_MAX}x closer than the picture, at {widest:g} mm)")
    if short:
        raise StudioError('MUST_SEE_UNREACHABLE', f"{shot['shot_id']}: " + '; '.join(short),
                          recovery='draw the moment closer on the part (a detail picture), give the part to a closer moment or shot, or lower '
                                   'its min_px in screen.details with why')


def compare(target, box, elevation_deg=None, must_see_px=None, context_boxes=None, tilt_deg=None, solve=None):
    """Problems of a built frame against its picture target: subject centre, size, cut edges, camera up/down; the
    must-see parts' thickness ({id: (px, min_px)}); the things around it against their size in the picture. What the
    picture's departures name (target['departs'], frames depart: a difference kept on purpose, with its reason) is not
    reported; a must-see part and an absent subject never depart. An intent shot (solve) is sized by the picture's size
    class, as its camera was solved (camera_moves_core.size_kept: its edges with SIZE_EDGE_MARGIN), not by HEIGHT_RATIO -
    one size rule from the solve to the build check (2026-10-16)."""
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
    if (solve or frame_solve({})) == 'intent':
        if goal_h > 0 and not size_kept(height, size_class(goal_h)[1], picture_h=goal_h):
            problems.append(('size', f"t={target['at']}: subject {height:.2f} of the frame tall ({size_class(height)[0]}), the picture "
                                     f"{goal_h:.2f} ({size_class(goal_h)[0]})"))
    elif goal_h > 0 and not HEIGHT_RATIO[0] <= height / goal_h <= HEIGHT_RATIO[1]:
        problems.append(('size', f"t={target['at']}: subject {height:.2f} of the frame tall, the picture {goal_h:.2f}"))
    cut = sorted(e for e, hit in zip(EDGES, (box[1] <= 0.002, box[3] >= 0.998, box[0] <= 0.002, box[2] >= 0.998)) if hit)
    if cut != want['cut_edges']:
        problems.append(('edges', f"t={target['at']}: subject runs past {cut or 'no edge'}, in the picture past {want['cut_edges'] or 'no edge'}"))
    # The angle is not judged by size here (2026-10-13): the camera keeps the picture's view unless it says why it moved
    # (moments: the move report's moved_because), and a view that no longer matches the reading is a stale target.
    if target.get('tilt_deg') is not None and tilt_deg is not None and abs(((tilt_deg - target['tilt_deg'] + 180) % 360) - 180) > TILT_TOL_DEG:
        problems.append(('tilt', f"t={target['at']}: the subject leans {tilt_deg:+.0f} deg on screen, in the picture {target['tilt_deg']:+.0f} deg"))
    departed = {d['item'] for d in target.get('departs') or []}
    return [text for kind, text in problems if kind not in departed or kind == 'must_see']


STALE_DEG = 0.5   # a camera target this far from its picture's reading was set from an earlier reading


def view_problems(want, picture, solved_row):
    """[(item, text)] about the camera's view at one picture moment: it moved from the picture (and why - the solver's
    own words), or its target was set from an earlier reading of the picture (FRAME_TARGET_STALE: frames apply again)."""
    out = []
    held, read = want.get('view') or {}, picture.get('view') or {}
    gap = max(abs(float(held.get(k, 0.0)) - float(read.get(k, 0.0))) for k in ('elevation_deg', 'azimuth_deg')) if held and read else 0.0
    if gap > STALE_DEG:
        out.append(('angle', f"FRAME_VIEW_STALE: t={want['at']}: the camera aims for {held.get('elevation_deg')}/{held.get('azimuth_deg')} deg, "
                             f"the picture now reads {read.get('elevation_deg')}/{read.get('azimuth_deg')} - run frames apply again"))
    offset = (solved_row or {}).get('offset_from_picture') or {}
    if any(offset.values()):
        out.append(('angle', f"t={want['at']}: the camera moved {offset} from the picture's view because "
                             f"{(solved_row or {}).get('moved_because') or 'its view did not solve'}"))
    for key, words in (('size_scale', 'came closer'), ('place_shift', 'moved the framing')):
        if (solved_row or {}).get(key):
            out.append(('size' if key == 'size_scale' else 'place', f"t={want['at']}: the camera {words} ({solved_row[key]}): {solved_row.get('because')}"))
    return out


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


MISREAD_RATIO = 2.0      # a subject box this many times its target's is another thing's (one boxed with what flew above it: 14x)
MOVING_SHARE = 0.05      # a subject that travels less than this share of its own length over the shot does not move
ATMOSPHERE_FRAME_MAX = 0.5   # an atmosphere volume over more of the frame than this is the picture (well-read builds 0-0.004)
ATMOSPHERE_FOCUS_MAX = 0.3   # ... or over more of the focus than this: camera_moves.BLOCKED_MAX, as for a solid in front


def _side(box, x):
    """-1 when the box stands wholly left of x, +1 wholly right, 0 when it spans it (a ground, a sea: no side)."""
    return -1 if box[2] < x else 1 if box[0] > x else 0


def side_misses(drawn_subject, drawn, built_subject, built):
    """FRAME_SIDE_MISS: [{name, drawn, built}] for the things (drawn / built: {name: box}) that stand on one side of the
    subject's centre in the picture and on the other in the build; the offsets are their centres' from the subject's,
    in shares of the frame width. A thing spanning the subject's centre has no side. Why (2026-10-14): a picture had the
    structure beside the subject on its left, the build on its right - the subject leant toward it, not away."""
    if not drawn_subject or not built_subject:
        return []
    dx, bx = (drawn_subject[0] + drawn_subject[2]) / 2, (built_subject[0] + built_subject[2]) / 2
    out = []
    for name in sorted(set(drawn) & set(built)):
        d, b = drawn[name], built[name]
        if d and b and _side(d, dx) * _side(b, bx) < 0:
            out.append({'name': name, 'drawn': round((d[0] + d[2]) / 2 - dx, 3), 'built': round((b[0] + b[2]) / 2 - bx, 3)})
    return out


def misread(box, cut, size, spans=None, own=None, fore=None):
    """FRAME_SUBJECT_MISREAD: why a picture's subject box (`box`, shares of the frame; `cut` the edges it runs past) is
    more than MISREAD_RATIO times its target, else None - judged two ways: uncut, its length for its width (in pixels of
    `size`) against the most the target can show from any side (`spans`: its own extents in metres, the longest over the
    shortest) - only when nothing stands in front of it (`fore`, the reading's foreground: a box of the part a wall leaves
    visible is slimmer than the thing); and against `own`, the box the same reading gives the target itself, on either axis.
    Why (2026-10-14): a falling part's pictures were read for words that also named what flew on above it; the box held
    both and the camera sized the part to it."""
    w, h = (box[2] - box[0]) * size[0], (box[3] - box[1]) * size[1]
    if spans and not cut and not fore and min(w, h) > 0:
        drawn, most = max(w, h) / min(w, h), max(spans) / max(min(spans), 1e-6)
        if drawn > MISREAD_RATIO * most:
            return (f'the subject box is {drawn:.1f} times as long as it is wide; the target, {max(spans):.1f} by {min(spans):.1f} m, '
                    f'shows at most {most:.1f} from any side')
    if own:
        ow, oh = (own[2] - own[0]) * size[0], (own[3] - own[1]) * size[1]
        ratio = max(w / max(ow, 1e-6), h / max(oh, 1e-6))
        if ratio > MISREAD_RATIO:
            return f'the subject box is {ratio:.1f} times the box the same picture gives the target itself ({[round(v, 3) for v in own]})'
    return None


FLAT_HEIGHT_DROP = 0.25    # the subject this share smaller than its picture lost the picture's size ...
FLAT_ELEVATION_DEG = 15.0  # ... the camera this much nearer level its height (a low angle flattened to the eye's) ...
FLAT_TILT_SHARE = 0.5      # ... this share of a lean the picture gives it gone (a lean within TILT_TOL_DEG is no lean) ...
FLAT_LENS_SHARE = 0.01     # ... or a lens this much longer: flatter perspective than drawn


def flattened(picture, box, elevation_deg=None, lean_deg=None, lens_mm=None):
    """FRAME_FLATTENED: why a built moment is flatter than its picture (picture: a targets() row; box: the subject as
    built, elevation the solved view's - the camera's direction from the subject, as the picture's view reads -, lean as
    built, the lens the solve took), [] when it keeps the punch. Why
    (2026-10-14): a picture that could not be shot was solved by turning toward safe, literal views - "실제를 고집하다
    영상이 노잼될 우려"; a fix or a cheat that does so is said next to CAMERA_FLAT."""
    out = []
    want = picture['check']['box']
    if box is not None and want[3] - want[1] > 0 and (box[3] - box[1]) / (want[3] - want[1]) < 1.0 - FLAT_HEIGHT_DROP:
        out.append(f"the subject {box[3] - box[1]:.2f} of the frame tall, the picture {want[3] - want[1]:.2f}")
    drawn = (picture.get('view') or {}).get('elevation_deg')
    if drawn is not None and elevation_deg is not None and abs(drawn) - abs(elevation_deg) > FLAT_ELEVATION_DEG:
        out.append(f"the camera {abs(elevation_deg):.0f} deg {'down' if elevation_deg > 0 else 'up'}, the picture {abs(drawn):.0f} deg "
                   f"{'down' if drawn > 0 else 'up'}")
    tilt = picture.get('tilt_deg')
    if tilt is not None and lean_deg is not None and abs(tilt) > TILT_TOL_DEG and abs(lean_deg) < (1.0 - FLAT_TILT_SHARE) * abs(tilt):
        out.append(f"the subject leans {lean_deg:+.0f} deg, the picture {tilt:+.0f} deg")
    if lens_mm is not None and picture.get('lens_mm') and lens_mm > picture['lens_mm'] * (1.0 + FLAT_LENS_SHARE):
        out.append(f"a {lens_mm:g} mm lens, the picture {picture['lens_mm']:g} mm")
    return out


def drift(solved_box, built_box):
    """MOVING_SUBJECT_OFF: how the built frame shows a moving subject away from where the camera was solved to frame it
    (both boxes clipped to the frame; CENTRE_TOL, HEIGHT_RATIO), else None. Like with like: both are the same points
    projected with nothing hidden, from the solved pose and through the camera as built (camera_moves.measure_built)."""
    if not solved_box or not built_box:
        return None
    clip = lambda b: [min(1.0, max(0.0, v)) for v in b]  # noqa: E731
    s, b = clip(solved_box), clip(built_box)
    off = math.dist(((s[0] + s[2]) / 2, (s[1] + s[3]) / 2), ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2))
    ratio = (b[3] - b[1]) / max(1e-6, s[3] - s[1])
    if off > CENTRE_TOL or not HEIGHT_RATIO[0] <= ratio <= HEIGHT_RATIO[1]:
        return (f'built at centre ({(b[0] + b[2]) / 2:.2f}, {(b[1] + b[3]) / 2:.2f}), {b[3] - b[1]:.2f} of the frame tall; solved at '
                f'({(s[0] + s[2]) / 2:.2f}, {(s[1] + s[3]) / 2:.2f}), {s[3] - s[1]:.2f} tall')
    return None


def moments(path, shot, frame_report, rig_report, move_report=None, version=None):
    """Each frame picture's moment in a built version: [{at, frame, concept_id, box, elevation_deg, problems, ...}] - the
    probe's box of what shows (occlusion counted) and the rig's pitch, compared with the picture's reading; the lean as
    built (camera_moves.measure_built: the subject's axis seen by the final camera), and what the A5 checks found:
    end_on (the axis's angle to the line of sight; the lean is then not compared), sides, misread, moving_off, atmosphere."""
    move = (shot.get('camera') or {}).get('move') or {}
    if not move.get('frames'):
        return []
    read = {t['concept_id']: t for t in targets(path, shot['shot_id'])}
    params = move.get('params') or {}
    ids = list(params.get('parts') or [params.get('target')])
    probed = {f['frame']: f for f in (frame_report or {}).get('frames', [])}
    pitch = {s['frame']: s.get('pitch_deg') for s in (rig_report or {}).get('samples', [])}
    leans = {r['frame']: r.get('tilt_deg') for r in (move_report or {}).get('frames') or []}   # a report from before measure_built
    solved = {round(r['at'], 4): r for r in (move_report or {}).get('frames') or [] if 'at' in r}
    travel = (move_report or {}).get('subject_travel_m') or 0.0
    things, ctx = layout_elements(path, shot), context_elements(path, shot)
    doubled = {n for n, cids in ctx.items() if any(set(info['ids']) == set(cids) for info in things.values())}
    from .blender_ops.ids_core import spellings
    framed = {s for i in ids if i for s in spellings(i)}
    policy = cheat_core.viewpoint_policy(path)
    out = []
    for want in move['frames']:
        picture = read.get((want.get('picture') or {}).get('concept_id'))
        frame = round(want['at'] * (shot['duration_frames'] - 1))
        if picture is None or frame not in probed:
            continue
        row = solved.get(round(want['at'], 4)) or {}
        seen = row.get('built') or {}
        box = _box_at(probed[frame], ids)
        elevation = -pitch[frame] if pitch.get(frame) is not None else None
        from .blender_ops.frame_probe_core import thickness_px
        size = (frame_report or {}).get('output_size') or [1080, 1920]
        need = {d['id']: d.get('min_px', 0) for d in (shot.get('screen') or {}).get('details', [])}
        must = {i: (thickness_px((probed[frame].get('shapes') or {}).get(f'key:{i}'), size), need.get(i, 0)) for i in want.get('must_see') or []}
        around = {name: _box_at(probed[frame], cids, fallback=False) for name, cids in ctx.items()}
        built = {eid: _box_at(probed[frame], info['ids'], fallback=False) for eid, info in things.items()}
        layout = compare_layout(picture.get('objects'), built, want['at']) if picture.get('objects') else None
        review = next((r for r in reversed(picture.get('reviews') or []) if version is None or r.get('version') == version), None)
        departed = {d['item'] for d in picture.get('departs') or []}
        lean = seen['tilt_deg'] if 'tilt_deg' in seen else leans.get(frame)
        end_on = seen.get('sight_deg') if picture.get('tilt_deg') is not None and seen.get('sight_deg') is not None \
            and seen['sight_deg'] < END_ON_DEG else None
        problems = compare(picture, box, elevation, must, around, None if end_on is not None else lean, frame_solve(params)) + \
            [text for kind, text in view_problems(want, picture, row or None) if kind not in departed]
        own = [eid for eid, info in things.items() if framed & {s for i in info['ids'] for s in spellings(i)}]
        drawn = {o['name']: o['box'] for o in picture.get('objects') or [] if o.get('box') and o['name'] in things and o['name'] not in own}
        # a thing read both as an object (its element id) and as context (its words) is compared once, as the object
        aside = {name: b for name, b in around.items() if name not in doubled}
        sides = side_misses(picture['check']['box'], {**drawn, **(picture.get('context') or {})}, box,
                            {**{k: v for k, v in built.items() if k not in own}, **aside})
        wrong = misread(picture['check']['box'], picture['check']['cut_edges'], size, row.get('spans_m'), fore=picture.get('fore'))
        out.append({'at': want['at'], 'frame': frame, 'concept_id': picture['concept_id'], 'box': box, 'elevation_deg': elevation,
                    'problems': problems, 'departs': picture.get('departs') or [],
                    'abandoned': abandoned(picture.get('departs'), policy), 'layout': layout, 'review': review,
                    'tilt_deg': lean, 'end_on': end_on, 'sides': sides,
                    # the reading's own box of the target is judged before the build (reading_checks); here its shape
                    'misread': wrong,
                    'moving_off': drift(seen.get('posed_box'), seen.get('box')) if travel > MOVING_SHARE * max(row.get('spans_m') or [0.0]) else None,
                    'atmosphere': seen.get('atmosphere'),
                    # the solved view (the same measure as the picture's: the camera's direction from the subject), not the pitch
                    # its size as the camera framed it (nothing hidden: something in front is not a flatter picture), and
                    # not against a misread box (that box is another thing's)
                    'flattened': flattened(picture, None if wrong else (seen.get('box') or box), (row.get('view') or [None])[0],
                                           None if end_on is not None else lean, row.get('lens_mm')),
                    'cheats': list(want.get('cheats') or [])})
    return out


def gate_lines(path, shot, frame_report, rig_report, move_report=None):
    """The A5 checks of a built version's pictured moments, judged before the version is kept: [{code, text}] -
    FRAME_SUBJECT_MISREAD (broken), FRAME_SIDE_MISS, SUBJECT_END_ON, MOVING_SUBJECT_OFF, FRAME_FLATTENED (B5),
    ATMOSPHERE_COVERS (taste)."""
    sid, out = shot['shot_id'], []
    for row in moments(path, shot, frame_report, rig_report, move_report):
        where = f"{sid} picture {row['concept_id']} t={row['at']}"
        if row['misread']:
            out.append({'code': 'FRAME_SUBJECT_MISREAD', 'text': f"{where}: {row['misread']} - the reader boxed more than the camera "
                        'frames: frames measure --subject "<the target in the picture\'s words>" (or frames mark --box), then frames apply'})
        for miss in row['sides']:
            out.append({'code': 'FRAME_SIDE_MISS', 'text': f"{where}: {miss['name']} stands {miss['drawn']:+.2f} of the frame from the "
                        f"subject in the picture, {miss['built']:+.2f} in the build - the other side: move it in the scene, or turn the camera round"})
        if row['end_on'] is not None:
            out.append({'code': 'SUBJECT_END_ON', 'text': f"{where}: the subject's long axis is {row['end_on']:.0f} deg from the line of "
                        f"sight (under {END_ON_DEG:.0f}): it shows its end, so its lean is not compared - pose it side-on at this moment, or draw the end"})
        if row['moving_off']:
            out.append({'code': 'MOVING_SUBJECT_OFF', 'text': f"{where}: the moving subject {row['moving_off']} - the camera and the "
                        'subject drift apart at the picture: check the rig follow and pacing, or the subject\'s motion'})
        if row['flattened']:
            out.append({'code': 'FRAME_FLATTENED', 'text': f"{where}: flatter than its picture - " + '; '.join(row['flattened'])
                        + ' - keep the picture\'s angle, size and lens (a cheat that keeps them first: frames check), else draw it again'})
        cover = row['atmosphere'] or {}
        if (cover.get('frame_share') or 0) > ATMOSPHERE_FRAME_MAX or (cover.get('focus_share') or 0) > ATMOSPHERE_FOCUS_MAX:
            out.append({'code': 'ATMOSPHERE_COVERS', 'text': f"{where}: atmosphere covers {cover.get('frame_share')} of the frame and "
                        f"{cover.get('focus_share')} of the focus (limits {ATMOSPHERE_FRAME_MAX}, {ATMOSPHERE_FOCUS_MAX}) - the camera "
                        'stands back from the volume, or the volume is made smaller at this moment'})
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
    p.add_argument('--how', choices=METHODS, help='how the picture shows the subject (exterior, section, xray, ...)')
    p.add_argument('--opened', nargs='*', help='part ids the picture shows only because it is opened or see-through')
    p.set_defaults(handler=lambda a: mark(a.project, a.shot, a.concept, a.box, a.whole,
                                          a.horizon if a.horizon in (None, 'none') else float(a.horizon), a.view_deg, a.lens_mm, a.azimuth_deg, a.note,
                                          read_json(a.reading) if a.reading else None, a.subject, a.tilt_deg, a.shows,
                                          read_json(a.objects) if a.objects else None, a.how, a.opened))
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
    p = commands.add_parser('uncheat', help='Take recorded cheats off a picture (then frames apply carries the move without them)')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--concept', required=True)
    p.add_argument('--kind', action='append', choices=sorted(cheat_core.CHEATS), help='only these cheat kinds (repeatable); default all')

    def _uncheat(a):
        from .frames_check import uncheat
        return uncheat(a.project, a.shot, a.concept, a.kind)
    p.set_defaults(handler=_uncheat)
    p = commands.add_parser('check', help='Can the pictures be shot: solve each moment on the authored scene, try the declared cheats '
                                          '(cheat_core.CHEATS) on the ones that fail, say which need redrawing (no save, no render)')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--apply', action='store_true', help='record the cheats that solve a moment with its picture (by the agent)')
    p.add_argument('--moment-s', type=float, default=420.0, help='seconds the cheat search may take per moment')
    p.add_argument('--shot-s', type=float, default=1200.0, help='seconds the whole check may take')
    p.add_argument('--solve', choices=['exact', 'intent'], help="solve the moments this way for this check (default: the shot's "
                                                                "move.params.solve); intent: verdicts never by the clock")

    def _check(a):
        from .frames_check import check
        return check(a.project, a.shot, a.apply, a.moment_s, a.shot_s, a.solve)
    p.set_defaults(handler=_check)
    p = commands.add_parser('candidates', help="The shot's intent poses to pick from: each moment solved by its picture's intent, "
                                               'its kept poses rendered (Workbench) beside the picture - concepts/<shot>/candidates/')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--keep', type=int, default=3, help='poses per moment (the engine\'s choice first)')

    def _candidates(a):
        from .frames_check import candidates
        return candidates(a.project, a.shot, a.keep)
    p.set_defaults(handler=_candidates)
    p = commands.add_parser('pick', help="Record the user's pick of a moment's candidate (frames candidates), in their own words")
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--moment', required=True, help='the moment: its at (0..1) or its picture (concept id)')
    p.add_argument('--candidate', type=int, required=True, help="the candidate's number on the sheet")
    p.add_argument('--user-words', required=True)

    def _pick(a):
        from .frames_check import pick
        return pick(a.project, a.shot, a.moment, a.candidate, a.user_words)
    p.set_defaults(handler=_pick)
    p = commands.add_parser('show')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.set_defaults(handler=lambda a: {'targets': targets(a.project, a.shot), 'store': _store(project_dir(a.project), a.shot)})
