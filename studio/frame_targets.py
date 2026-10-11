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

from .common import StudioError, now, read_json, stable_hash, write_json
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
                            'context', 'note'],
               'properties': {'index': {'type': 'integer'}, 'subject_box': box, 'whole_box': box,
                              'cut_edges': {'type': 'array', 'items': {'enum': list(EDGES)}},
                              'horizon_v': {'type': ['number', 'null']}, 'view_height': {'enum': list(VIEW_HEIGHTS)},
                              'view_deg': {'type': 'number'}, 'side': {'enum': list(SIDES)}, 'lens': {'enum': list(LENS_MM)},
                              'fore': {'type': ['string', 'null']},
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
- context: for each of these things around the subject - {context} - its name exactly as given and its box
  [x0, y0, x1, y1] as it shows (null if it is not in the picture).
- note: one sentence on what the composition does (where the eye goes, what moves).
Return every image, in order."""
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
    todo = [p for p in pictures if force or (store['pictures'].get(p['concept_id']) or {}).get('sha256') != p['sha256']]
    if todo:
        key = stable_hash({'subject': words, 'context': sorted(around), 'pictures': [p['sha256'] for p in todo]})[:16]
        out = _folder(path, shot_id) / 'frames_labels' / f'{key}.json'
        prompt = PROMPT.format(subject=words, context='; '.join(f'"{n}"' for n in around) or 'none')
        answer = read_json(out) if out.is_file() else codex_json([path / p['path'] for p in todo], prompt, schema(), out)
        readings = {r['index']: r for r in answer['answer']['pictures']}
        for i, picture in enumerate(todo):
            if i not in readings:
                raise StudioError('LABELLER_FAILED', f"{shot_id}: the reader skipped picture {picture['concept_id']}", retryable=True)
            reading = {k: v for k, v in readings[i].items() if k != 'index'}
            old = store['pictures'].get(picture['concept_id']) or {}
            store['pictures'][picture['concept_id']] = {
                'sha256': picture['sha256'], 'frame': picture['frame'], 'read': reading, 'read_at': now(), 'labels': str(out.relative_to(path)),
                **({'marked': old['marked']} if old.get('marked') and old.get('sha256') == picture['sha256'] else {})}
    store['subject'], store['context'] = words, sorted(around)
    write_json(_store_path(path, shot_id), store)
    check = check_picture(path, shot_id)
    return {'shot_id': shot_id, 'subject': words, 'read': [p['concept_id'] for p in todo], 'targets': targets(path, shot_id),
            'check': str(check), 'artifacts': [str(_store_path(path, shot_id)), str(check)]}


def mark(project, shot_id, concept_id, box=None, whole=None, horizon=None, view_deg=None, lens_mm=None, azimuth_deg=None, note=None):
    """Correct a reading by hand (a person or the agent looking at the check picture): what is given replaces the
    reading, the rest stays; `view_deg` is signed (+ looks down, - looks up)."""
    path = project_dir(project)
    store = _store(path, shot_id)
    row = store['pictures'].get(concept_id)
    if row is None:
        raise StudioError('INPUT_INVALID', f'{shot_id}: no reading of {concept_id}; frames measure first')
    given = {'subject_box': box, 'whole_box': whole, 'horizon_v': horizon, 'view_deg_signed': view_deg, 'lens_mm': lens_mm,
             'azimuth_deg': azimuth_deg}
    for key in ('subject_box', 'whole_box'):
        if given[key] is not None and (len(given[key]) != 4 or given[key][2] <= given[key][0] or given[key][3] <= given[key][1]):
            raise StudioError('INPUT_INVALID', f'{key}: [x0, y0, x1, y1] with x1 > x0 and y1 > y0')
    changed = {k: v for k, v in given.items() if v is not None}
    if horizon == 'none':
        changed['horizon_v'] = None
    if not changed:
        raise StudioError('INPUT_INVALID', 'frames mark: give at least one of --box --whole --horizon --view-deg --lens-mm --azimuth-deg')
    row['marked'] = {**(row.get('marked') or {}), **changed, 'at': now(), **({'note': note} if note else {})}
    write_json(_store_path(path, shot_id), store)
    check = check_picture(path, shot_id)
    return {'shot_id': shot_id, 'concept_id': concept_id, 'marked': row['marked'], 'targets': targets(path, shot_id), 'check': str(check)}


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
    return {'height_share': round(min(1.2, max(0.05, whole[3] - whole[1])), 4),
            'centre': [round((whole[0] + whole[2]) / 2, 4), round((whole[1] + whole[3]) / 2, 4)],
            **({'edges': edges} if edges else {}),
            'view': {'elevation_deg': round(elevation, 2), 'azimuth_deg': float(r.get('azimuth_deg', azimuth_deg))},
            'lens_mm': lens, 'elevation_from': source,
            'context': {c['name']: c['box'] for c in r.get('context') or [] if c.get('box')},
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
                        'sha256': picture['sha256'], **target})
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
    move = {'type': 'frame_to_frame', 'params': {'target': subject, 'path': path_kind, 'follow': float(follow), 'pace': pace,
                                                 **({'parts': list(parts)} if parts else {})},
            'frames': [{'at': t['at'], 'height_share': t['height_share'], 'centre': t['centre'], 'view': t['view'], 'lens_mm': t['lens_mm'],
                        **({'edges': t['edges']} if t.get('edges') else {}),
                        **({'must_see': last} if n == len(wanted) - 1 and last else {}),
                        'picture': {'concept_id': t['concept_id'], 'sha256': t['sha256']}} for n, t in enumerate(wanted)],
            **{k: old[k] for k in ('style', 'timing', 'guards', 'clearance_m', 'motion_blur_shutter') if k in old}}
    updated = dict(shot)
    updated['camera'] = {**shot['camera'], 'movement': 'rig', 'keys': [], 'move': move}
    updated['camera'].pop('rig', None)
    _write_shot(path, shot_id, updated, shot['revision'])
    return {'shot_id': shot_id, 'move': move, 'shot': str(shot_path(path, shot_id))}


def compare(target, box, elevation_deg=None, must_see_px=None, context_boxes=None):
    """Problems of a built frame against its picture target: subject centre, size, cut edges, camera up/down; the
    must-see parts' thickness ({id: (px, min_px)}); the things around it against their size in the picture."""
    problems = []
    for ident, (px, need) in sorted((must_see_px or {}).items()):
        if px < need:
            problems.append(f"t={target['at']}: must-see {ident} shows {px:.0f} px thick, it needs {need} px")
    for name, built in sorted((context_boxes or {}).items()):
        drawn = (target.get('context') or {}).get(name)
        if not drawn or not built:
            continue
        ratio = (built[3] - built[1]) / max(1e-6, drawn[3] - drawn[1])
        if not CONTEXT_RATIO[0] <= ratio <= CONTEXT_RATIO[1]:
            problems.append(f"t={target['at']}: {name} {built[3] - built[1]:.2f} of the frame tall, the picture {drawn[3] - drawn[1]:.2f} - "
                            'make the subject smaller at this moment (the camera stands back), then raise the camera')
    want = target['check']
    if box is None:
        return [f"t={target['at']}: the subject is not on screen; the picture has it at {want['box']}"]
    centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    goal = ((want['box'][0] + want['box'][2]) / 2, (want['box'][1] + want['box'][3]) / 2)
    off = math.dist(centre, goal)
    if off > CENTRE_TOL:
        problems.append(f"t={target['at']}: subject centre at ({centre[0]:.2f}, {centre[1]:.2f}), the picture ({goal[0]:.2f}, {goal[1]:.2f})")
    height, goal_h = box[3] - box[1], want['box'][3] - want['box'][1]
    if goal_h > 0 and not HEIGHT_RATIO[0] <= height / goal_h <= HEIGHT_RATIO[1]:
        problems.append(f"t={target['at']}: subject {height:.2f} of the frame tall, the picture {goal_h:.2f}")
    cut = sorted(e for e, hit in zip(EDGES, (box[1] <= 0.002, box[3] >= 0.998, box[0] <= 0.002, box[2] >= 0.998)) if hit)
    if cut != want['cut_edges']:
        problems.append(f"t={target['at']}: subject runs past {cut or 'no edge'}, in the picture past {want['cut_edges'] or 'no edge'}")
    wanted = target['view']['elevation_deg']
    if elevation_deg is not None and abs(wanted) >= LEVEL_DEG and (abs(elevation_deg) < LEVEL_DEG or (elevation_deg > 0) != (wanted > 0)):
        look = lambda e: 'level' if abs(e) < LEVEL_DEG else ('down' if e > 0 else 'up')  # noqa: E731
        problems.append(f"t={target['at']}: the camera looks {look(elevation_deg)} ({elevation_deg:.0f} deg), the picture {look(wanted)} ({wanted:.0f} deg)")
    return problems


def _box_at(frame_row, ids, fallback=True):
    """The union box of `ids` (their own probe classes, else the subject class) on one probed frame."""
    shapes = frame_row.get('shapes') or {}
    boxes = [shapes[f'key:{i}']['bbox'] for i in ids if (shapes.get(f'key:{i}') or {}).get('bbox')]
    if not boxes and fallback and (shapes.get('subject') or {}).get('bbox'):
        boxes = [shapes['subject']['bbox']]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def moments(path, shot, frame_report, rig_report):
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
        out.append({'at': want['at'], 'frame': frame, 'concept_id': picture['concept_id'], 'box': box, 'elevation_deg': elevation,
                    'problems': compare(picture, box, elevation, must, around)})
    return out


def build_lines(path, shot, frame_report, rig_report):
    """FRAME_TARGET_MISS lines (taste) for a built version."""
    return [f"FRAME_TARGET_MISS: {shot['shot_id']} {problem} (picture {row['concept_id']}; frames mark corrects a misread picture, "
            'frames apply rewrites the move)' for row in moments(path, shot, frame_report, rig_report) for problem in row['problems']]


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
    p.set_defaults(handler=lambda a: mark(a.project, a.shot, a.concept, a.box, a.whole,
                                          a.horizon if a.horizon in (None, 'none') else float(a.horizon), a.view_deg, a.lens_mm, a.azimuth_deg, a.note))
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
    p = commands.add_parser('show')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.set_defaults(handler=lambda a: {'targets': targets(a.project, a.shot), 'store': _store(project_dir(a.project), a.shot)})

