"""The reel's composition brief: what each shot shows on screen, how its camera moves, how it joins the shot before,
and where the build left the shot list's plan - one page the user reads before approving storyboards.

Why (2026-10-11, rocket_landing): the user, after 25 storyboard sheets, a 40 000 px board and long agent reports:
"실제 어떤 화면 구도들로 영상이 이루어질지에 대한 브리핑이 더 상세해야 할것같아". Nothing said in words what a
shot looks like, and s19 - planned "held, the same side view" - had become an arc into one leg without anyone saying
so. Everything here is read from data the build already wrote (camera_move_report, frame_report, the shot list,
the frame pictures, the latest sheet's frames); nothing is rendered.
"""
from __future__ import annotations

import html
import math
from pathlib import Path

from .blender_ops.camera_moves_core import SIZE_CLASSES
from .blender_ops.show_core import CONTINUOUS, handover_of
from .common import StudioError, read_json, write_json
from .project import load_project, load_shot, project_dir, shot_path

LEVEL_DEG = 5.0          # a view within this of horizontal reads as eye level (frame_targets.LEVEL_DEG)
TURN_DEG = 5.0           # a camera that turns less than this round the subject did not go round it
DIST_RATIO = 1.15        # nearer / farther than this ratio is a push in / pull back (patterns.REPEAT height_ratio)
TRAVEL_SHARE = 0.05      # the subject crossing less than this share of the frame stays put on screen
LAYOUT_CODES = ('FRAME_TARGET_MISS', 'FRAMING_REPEATED', 'FRAME_PATH_BLOCKED', 'DETAIL_NOT_SHOWN', 'MUST_SEE_SHORT', 'FOCUS_FRAMING',
                'FRAME_SUBJECT_SMALL', 'FRAME_UNSOLVED', 'SCALE_FLOW_FLAT')   # composition warnings the brief carries
LAYOUT_MIN_SHOWN = 0.7   # frame_targets.LAYOUT_MIN: below it the brief lists what differs thing by thing
STEP_WORDS = {'closer': '가까워짐', 'wider': '멀어짐', 'same': '같은 크기'}
PACING_WORDS = {'subject': '속도는 대상에 맞춤', 'ease_in_out': '부드럽게 출발해 멈춤', 'linear': '일정한 속도',
                'burst_settle': '빠르게 치고 들어가 멈춤'}


def size_class(height_share):
    if height_share is None:
        return None
    return next(name for top, name in SIZE_CLASSES if height_share < top)


def place_words(centre):
    """Where on screen, in a 3 x 3 grid: '왼쪽 위' ... '가운데'."""
    if not centre:
        return None
    x, y = (min(1.0, max(0.0, v)) for v in centre)
    col = '왼쪽' if x < 1 / 3 else '오른쪽' if x > 2 / 3 else '가운데'
    row = '위' if y < 1 / 3 else '아래' if y > 2 / 3 else ''
    return f'{col} {row}'.strip() if (col, row) != ('가운데', '') else '가운데'


def angle_words(elevation_deg):
    """+ looks down (camera above), - looks up."""
    if elevation_deg is None:
        return None
    if abs(elevation_deg) < LEVEL_DEG:
        return f'눈높이 ({elevation_deg:+.0f}°)'
    return f"{'내려다봄' if elevation_deg > 0 else '올려다봄'} {abs(elevation_deg):.0f}°"


def cut_edges(box):
    if not box:
        return []
    names = (('left', box[0] < 0), ('top', box[1] < 0), ('right', box[2] > 1), ('bottom', box[3] > 1))
    return [{'left': '왼쪽', 'top': '위', 'right': '오른쪽', 'bottom': '아래'}[n] for n, out in names if out]


def _dist(a, b):
    return math.dist(a, b) if a and b else None


def move_words(start, end, move_report):
    """The camera's move between the first and the last moment, in plain words."""
    if not start or not end:
        return None
    parts = []
    path = (move_report or {}).get('path')
    if path:
        parts.append('둘레로 돌며' if path == 'arc' else '곧장')
    elif ((move_report or {}).get('move') or {}).get('type'):
        parts.append(f"{move_report['move']['type']} 움직임")
    da, db = _dist(start.get('position'), start.get('aim')), _dist(end.get('position'), end.get('aim'))
    if da and db:
        ratio = da / db
        parts.append(f'다가감 ({da:.0f}→{db:.0f} m)' if ratio > DIST_RATIO else f'물러남 ({da:.0f}→{db:.0f} m)' if ratio < 1 / DIST_RATIO
                     else f'거리 유지 (약 {db:.0f} m)')
    if start.get('view') and end.get('view') and None not in (start['view'][1], end['view'][1]):
        turn = ((end['view'][1] - start['view'][1] + 180) % 360) - 180
        if abs(turn) >= TURN_DEG:
            parts.append(f"{'오른쪽' if turn > 0 else '왼쪽'}으로 {abs(turn):.0f}° 돎")
        rise = end['view'][0] - start['view'][0]
        if abs(rise) >= TURN_DEG:
            parts.append(f"카메라가 {'올라감' if rise > 0 else '내려감'} ({angle_words(start['view'][0])} → {angle_words(end['view'][0])})")
    follow = (move_report or {}).get('follow')
    if follow is not None:
        parts.append(f'대상 이동의 {round(follow * 100)}%를 따라감' if follow > 0 else '제자리(대상이 지나감)')
    pacing = (move_report or {}).get('pacing')
    pacing = pacing or (((move_report or {}).get('move') or {}).get('timing') or {}).get('profile')
    if pacing:
        parts.append(PACING_WORDS.get(pacing, f'속도 {pacing}'))
    return ', '.join(parts)


def _moment(row):
    """A camera report frame row -> what the brief says about that moment."""
    achieved = row.get('achieved') or {}
    view = row.get('view')
    h = achieved.get('height_share')
    return {'frame': row.get('frame'), 'at': row.get('at'), 'height_share': h, 'size': size_class(h),
            'place': place_words(achieved.get('centre')), 'cut': cut_edges(achieved.get('box')),
            'angle': angle_words(view[0] if view else None), 'view': view if view and view[0] is not None else None, 'lens_mm': row.get('lens_mm'),
            'position': row.get('position'), 'aim': row.get('aim'), 'must_see_px': row.get('must_see_px') or {},
            'offset_from_picture': row.get('offset_from_picture')}


def _rig_row(sample, aim=None):
    """A camera rig sample in the camera report's frame-row shape (pitch: + looks up; view elevation: + looks down)."""
    box = sample.get('target_box') or sample.get('subject_box')
    cam = sample.get('camera')
    azimuth = math.degrees(math.atan2(cam[1] - aim[1], cam[0] - aim[0])) if cam and aim else None
    return {'frame': sample.get('frame'), 'lens_mm': sample.get('lens_mm'), 'position': cam, 'aim': aim,
            'view': [-sample['pitch_deg'] if sample.get('pitch_deg') is not None else None, azimuth],
            'achieved': {'height_share': round(box[3] - box[1], 4), 'centre': [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2], 'box': box} if box else {}}


def _move_ids(shot):
    """What the move frames: its subject (or parts) and every moment's own subject (camera_moves_core.frame_target)."""
    from .blender_ops.camera_moves_core import frame_target
    move = (shot.get('camera') or {}).get('move') or {}
    params = move.get('params') or {}
    ids = list(params.get('parts') or ([params['target']] if params.get('target') else []))
    return list(dict.fromkeys(ids + [frame_target(fr, params)[0] for fr in move.get('frames') or [] if fr.get('target')]))


def _plan(path, shot_id):
    file = project_dir(path) / 'decisions' / 'shotlist.json'
    if not file.is_file():
        return {}
    body = read_json(file).get('body') or {}
    return next((s for s in body.get('shots', []) if s.get('shot_id') == shot_id), {})


def _images(path, shot, version_moments):
    """(first picture, first built frame, last picture, last built frame) as project-relative paths, None when absent.
    Collected reference photos never appear: pictures come from concept frames, frames from the shot's own sheet."""
    from .concept import frames as frame_pictures
    from .storyboard import envelope
    root = project_dir(path)
    pictures = frame_pictures(root, shot['shot_id'])
    first_pic = pictures[0]['path'] if pictures else None
    last_pic = pictures[-1]['path'] if len(pictures) > 1 else None
    sheet = ((envelope(root, shot['shot_id']) or {}).get('sheet') or {}).get('path')
    folder = (root / sheet).parent if sheet else None

    def built(frame):
        if folder is None or frame is None:
            return None
        file = folder / f'frame_{int(frame):06d}.png'
        return str(file.relative_to(root)) if file.is_file() else None
    first, last = (version_moments[0], version_moments[-1]) if version_moments else (None, None)
    return [first_pic, built(first['frame'] if first else None), last_pic, built(last['frame'] if last else None)]


def shot_card(path, shot_id, timing=None):
    """Everything the brief says about one shot (a dict; no rendering)."""
    root = project_dir(path)
    shot = load_shot(root, shot_id)
    plan = _plan(root, shot_id)
    fps = (timing or {}).get('fps') or 30
    start = (timing or {}).get('start_frame') or 0
    count = shot['duration_frames']
    card = {'shot_id': shot_id, 'start_s': round(start / fps, 2), 'end_s': round((start + count) / fps, 2),
            'length_s': round(count / fps, 2), 'narration': (shot.get('narration') or {}).get('text'),
            'purpose': plan.get('purpose') or shot.get('goal'), 'role': (shot.get('screen') or {}).get('role') or plan.get('role'),
            'subject': (shot.get('screen') or {}).get('subject') or _move_ids(shot),
            'details': [{'id': d['id'], 'why': d.get('why'), 'min_px': d.get('min_px')} for d in (shot.get('screen') or {}).get('details', [])],
            'plan': plan.get('direction') or {}, 'version': shot.get('scene_version'), 'built': False,
            'handover': handover_of(plan) if plan else shot.get('handover'), 'screen_dx': None,
            'first': None, 'last': None, 'camera_move': None, 'screen_travel': None, 'issues': [], 'departs': [], 'cheats': [], 'images': [None] * 4}
    from .decisions import reference_chars_per_s, spoken_seconds
    spoken = round(spoken_seconds([card['narration'] or ''], reference_chars_per_s(root)), 1)   # the time to look: what the words leave of the shot
    card['pace'] = {'spoken_s': spoken, 'look_s': round(card['length_s'] - spoken, 1),
                    'placed': [seg['at_s'] for seg in (shot.get('narration') or {}).get('segments') or []]}
    version = shot_path(root, shot_id).parent / 'versions' / str(shot.get('scene_version'))
    move = read_json(version / 'camera_move_report.json') if (version / 'camera_move_report.json').is_file() else None
    probe = read_json(version / 'frame_report.json') if (version / 'frame_report.json').is_file() else None
    rows = sorted((move or {}).get('frames') or [], key=lambda r: r.get('frame', 0))
    if not rows:   # any other move: the rig's first and last samples (camera, pitch, the target's box)
        rig = read_json(version / 'camera_rig_report.json') if (version / 'camera_rig_report.json').is_file() else {}
        aim = ((move or {}).get('focus') or {}).get('aim')
        rows = [_rig_row(sample, aim) for sample in (rig.get('samples') or [])[:1] + (rig.get('samples') or [])[-1:]]
    moments = [_moment(r) for r in rows]
    if moments:
        card.update({'built': True, 'first': moments[0], 'last': moments[-1], 'camera_move': move_words(moments[0], moments[-1], move)})
    if probe:
        from .frame_targets import _box_at
        ids = card['subject']
        probed = sorted(probe.get('frames', []), key=lambda f: f['frame'])
        boxes = [b for b in (_box_at(f, ids) for f in (probed[0], probed[-1])) if b] if probed else []
        if len(boxes) == 2:
            (a, b) = [((x0 + x1) / 2, (y0 + y1) / 2) for x0, y0, x1, y1 in boxes]
            travel = math.dist(a, b)
            card['screen_travel'] = round(travel, 3)
            card['screen_dx'] = round(b[0] - a[0], 3) if abs(b[0] - a[0]) >= TRAVEL_SHARE else 0.0
            card['screen_travel_words'] = '화면 안에서 거의 그대로' if travel < TRAVEL_SHARE else \
                f"화면을 {'오른쪽' if b[0] > a[0] else '왼쪽'}{'·아래' if b[1] - a[1] > TRAVEL_SHARE else '·위' if a[1] - b[1] > TRAVEL_SHARE else ''}로 가로지름 ({round(travel * 100)}%)"
        card['built'] = True
    for source in (move or {}, probe or {}):
        for warning in source.get('warnings') or []:
            text = warning if isinstance(warning, str) else (warning.get('message') or str(warning))
            if any(text.startswith(code) for code in LAYOUT_CODES) and text not in card['issues']:
                card['issues'].append(text)
    from .frame_targets import _store, moments as picture_moments
    rig = read_json(version / 'camera_rig_report.json') if (version / 'camera_rig_report.json').is_file() else {}
    from .frame_targets import layout_elements, targets as picture_targets
    drawn = {t['concept_id']: t.get('objects') or [] for t in picture_targets(root, shot_id)}
    probed = {f['frame']: f for f in (probe or {}).get('frames', [])}
    from .frame_targets import _box_at
    things = layout_elements(root, shot)
    card['pictures'] = [{'concept_id': m['concept_id'], 'at': m['at'], 'frame': m['frame'], 'problems': m['problems'],
                         'departs': len(m['departs']), 'abandoned': m['abandoned'],
                         'layout': m.get('layout'), 'review': m.get('review'), 'flattened': m.get('flattened') or [],
                         'drawn': [o for o in drawn.get(m['concept_id'], []) if o.get('box')],
                         'built': [{'name': eid, 'box': b} for eid, info in things.items()
                                   for b in [_box_at(probed.get(m['frame']) or {}, info['ids'], fallback=False)] if b]}
                        for m in picture_moments(root, shot, probe or {}, rig, move or {}, shot.get('scene_version'))] if probe else []
    for m in card['pictures']:
        if m['layout'] and m['layout']['score'] is not None and m['layout']['score'] < LAYOUT_MIN_SHOWN:
            card['issues'] += [f"FRAME_LAYOUT_MISS: {q}" for q in m['layout']['problems'][:4]]
        if m['review'] and not all(m['review'][k] for k in ('objects', 'extent', 'state', 'viewpoint')):
            card['issues'].insert(0, f"FRAME_REVIEW_DIFFERS ({m['review']['by']}): {m['review']['why']}")
    from .layout import behind
    lag = behind(root, shot) if card['built'] else None
    if lag:   # what the board shows is an older build than the shot
        card['issues'].insert(0, f"SCENE_BEHIND_SHOT ({lag['from']}): {lag['why']}")
    for m in card['pictures']:   # flatter than drawn (frame_targets.flattened): said beside the camera's own CAMERA_FLAT
        if m['flattened']:
            card['issues'].append(f"FRAME_FLATTENED: picture {m['concept_id']} - " + '; '.join(m['flattened']))
    for m in card['pictures']:
        if m['abandoned']:
            card['issues'].insert(0, f"FRAME_PICTURE_ABANDONED: picture {m['concept_id']} kept different in {m['departs']} ways "
                                     "without the user's words - the build no longer follows it")
    for concept_id, row in sorted((_store(root, shot_id).get('pictures') or {}).items()):
        card['departs'] += [{'picture': concept_id, 'item': d['item'], 'why': d['why'], **({'by_policy': d['by_policy']} if d.get('by_policy') else {})}
                            for d in row.get('departs') or []]
        # the declared cheats (cheat_core.CHEATS): what the scene or the camera changes so the picture can be shot
        card['cheats'] += [{'picture': concept_id, 'kind': c['kind'], 'target': c.get('target'), 'value': c.get('value'), 'why': c.get('why'),
                            'by': c.get('by')} for c in row.get('cheats') or []]
    applied = read_json(version / 'cheat_report.json') if (version / 'cheat_report.json').is_file() else {}
    card['cheats_applied'] = len(applied.get('cheats') or [])
    from .storyboard import envelope
    env = envelope(root, shot_id) or {}
    card['status'] = env.get('status') or 'no sheet'
    card['approval'] = (env.get('approval') or {}).get('user_words')
    card['images'] = _images(root, shot, moments)
    dynamics = read_json(version / 'camera_dynamics.json') if (version / 'camera_dynamics.json').is_file() else None
    card['dynamics'] = (dynamics or {}).get('judged')
    card['camera_measured'] = (dynamics or {}).get('camera')
    card['animatic'] = str((version / 'animatic.mp4').relative_to(root)) if (version / 'animatic.mp4').is_file() else None
    card['synth'] = str((version / 'grammar_synth.json').relative_to(root)) if (version / 'grammar_synth.json').is_file() else None
    reveal = card['plan'].get('reveal')
    inside = ((card['dynamics'] or {}).get('feel') or {}).get('inside')
    if reveal and reveal != 'none' and inside is not None and inside < REVEAL_SHOWN:
        card['issues'].insert(0, f"REVEAL_NOT_SHOWN: the shot list plans {reveal} here and at most {round(inside * 100)} % of the picture "
                                 "shows the subject's inside - open it where the camera looks (a section the camera pushes into)")
    card['issues'] += [l for l in ((card['dynamics'] or {}).get('lines') or [])]
    card['takes'] = [{'id': t['id'], 'label': t.get('label'), 'why': t.get('why'), 'recipe': t.get('recipe'), 'version': t['version'], 'animatic': t.get('animatic'),
                      'dynamics': t.get('dynamics')} for t in ((env.get('variants') or {}).get('takes') or [])] \
        if env.get('status') == 'variants' else []
    from .concept import frames as frame_pictures
    now = sorted((c['concept_id'], c['sha256']) for c in frame_pictures(root, shot_id))
    built = sorted((p['concept_id'], p['sha256']) for p in ((env.get('sheet') or {}).get('frame_pictures') or []))
    card['stale'] = bool(now) and now != built   # the pictures changed after the sheet's build: the frames shown are older
    card['intent'] = {c['concept_id']: (c.get('derived_from') or {}).get('supersedes') for c in frame_pictures(root, shot_id) if c.get('derived_from')}
    if card['stale']:
        card['issues'].insert(0, 'BRIEF_STALE: the shot has pictures its latest sheet was not built with - the frames shown are from an older build')
    return card


REVEAL_SHOWN = 0.1   # a planned reveal shows the inside on at least a tenth of the picture somewhere in the shot


def measured_join(root, previous, card):
    """How the camera crosses the cut from `previous` into `card`, from both versions' scene measurements
    (grammar_core.join); None when either was not measured (pattern dynamics measure)."""
    from .grammar_core import join as crossing
    if not previous or not previous.get('synth') or not card.get('synth'):
        return None
    a, b = read_json(Path(root) / previous['synth']), read_json(Path(root) / card['synth'])
    return crossing(a['pairs'], b['pairs'], b['fps'])


def join(previous, card, root=None):
    """How a cut from `previous` (its last moment) into `card` (its first) reads, and whether the shot list's
    scale_step for this shot holds (BRIEF_PLAN_DIFFERS)."""
    from .grammar_core import scale_step
    if not previous:
        return {'words': None, 'step': None, 'problems': [], 'motion': None}
    a, b = previous.get('last') or {}, card.get('first') or {}   # a moment not measured: no sizes, the motion still compares
    same_subject = sorted(previous.get('subject') or []) == sorted(card.get('subject') or [])
    # sizes compare only on one subject: a leg at 0.3 after a whole booster at 1.5 is not "wider"
    step = (scale_step(a.get('height_share'), b.get('height_share')) or {}).get('step') \
        if same_subject and a.get('height_share') and b.get('height_share') else None
    words = [f"{a.get('size') or '?'} → {b.get('size') or '?'}" + (f" ({STEP_WORDS.get(step, step)})" if step else '')
             + ('' if same_subject else f" (대상이 바뀜: {subject_label(previous.get('subject'))} → {subject_label(card.get('subject'))})")] \
        if a or b else []
    if a.get('view') and b.get('view'):
        words.append(f"각도 {a['view'][0]:+.0f}° → {b['view'][0]:+.0f}°")
    if a.get('place') and b.get('place') and a['place'] != b['place']:
        words.append(f"위치 {a['place']} → {b['place']}")
    handover = previous.get('handover') or {}   # how the shot before said it hands over (2026-10-13: this read the incoming card's own)
    if handover:
        words.append(f"연결 계획: {handover['kind']}" + (f" - {handover['why']}" if handover.get('why') else ''))
    from .grammar_core import JOIN_WORDS
    motion = measured_join(root, previous, card) if root else None
    words.append(f"카메라 움직임: {JOIN_WORDS[motion['kind']]}" if motion and motion['kind'] else '카메라 움직임: 미측정')
    planned = (card['plan'].get('scale_step') or '').strip().lower()
    problems = []
    if planned in STEP_WORDS and step and planned != step:
        problems.append(f"BRIEF_PLAN_DIFFERS: {card['shot_id']} was planned {planned} from {previous['shot_id']} and is {step} "
                        f"({a.get('height_share')} -> {b.get('height_share')} frame heights) - change the shot list or the shot")
    kind = (motion or {}).get('kind')
    if kind == 'reverse':
        problems.append(f"JOIN_REVERSED: {previous['shot_id']} -> {card['shot_id']}: the camera's motion snaps back the other way at the cut "
                        "- carry the direction on, settle before the cut, or make the two one camera (camera.move.then)")
    elif kind == 'stop':
        problems.append(f"JOIN_STOPPED: {previous['shot_id']} -> {card['shot_id']}: moving into the cut, still after it - let "
                        f"{previous['shot_id']} settle first or start {card['shot_id']} moving")
    if handover.get('kind') in CONTINUOUS and kind in ('reverse', 'stop', 'turn'):
        problems.append(f"HANDOVER_BROKEN: {previous['shot_id']} hands over '{handover['kind']}' but the camera {JOIN_WORDS[kind]} at the cut - "
                        f"let {card['shot_id']} take the motion on, or say contrast / new_place in the shot list")
    return {'words': ', '.join(words), 'step': step, 'problems': problems, 'motion': kind}


def pingpong_lines(made):
    """SCREEN_DIRECTION_PINGPONG: the same subject, handed over continue / match_action, crossing the screen one way and
    then the other at two cuts in a row (2026-10-13, the user: a change of direction is no problem in itself - a new
    subject, a place shown, an intent - only going back and forth tires the eye)."""
    out, flips = [], []
    for a, b in zip(made, made[1:]):
        if abs((a.get('end_s') or 0) - (b.get('start_s') or 0)) > 1e-6:   # not cut together (a brief of some shots only)
            flips = []
            continue
        same = sorted(a.get('subject') or []) == sorted(b.get('subject') or [])
        flipped = same and (a.get('handover') or {}).get('kind') in CONTINUOUS and a.get('screen_dx') and b.get('screen_dx') \
            and (a['screen_dx'] > 0) != (b['screen_dx'] > 0)
        flips = flips + [(a['shot_id'], b['shot_id'])] if flipped else []
        if len(flips) == 2:
            shots = [flips[0][0], flips[0][1], flips[1][1]]
            out.append(f"SCREEN_DIRECTION_PINGPONG: {' -> '.join(shots)} cross the screen one way, then back, then again with "
                       'the same subject running on - keep one direction, or make one of the cuts a contrast or a new place')
    return out


def cards(path, shots=None):
    root = project_dir(path)
    project = load_project(root)
    fps = project['output']['fps']
    out, previous = [], None
    for entry in project['shots']:
        if shots and entry['shot_id'] not in shots:
            previous = None
            continue
        card = shot_card(root, entry['shot_id'], {'fps': fps, 'start_frame': entry.get('start_frame')})
        card['join'] = join(previous, card, root)
        card['issues'] = card['join']['problems'] + card['issues']
        out.append(card)
        previous = card
    return out


# ---- the page ---------------------------------------------------------------------------------------------------------

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1f23;--soft:#5d636e;--line:#e3e4e8;--warn:#b4401c;--ok:#2f7a3d;--chip:#eef0f4}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121417;--card:#1b1e23;--ink:#e9eaee;--soft:#a3a8b3;--line:#2c3038;--warn:#ff8a65;--ok:#7ccf8a;--chip:#262a31;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#121417;--card:#1b1e23;--ink:#e9eaee;--soft:#a3a8b3;--line:#2c3038;--warn:#ff8a65;--ok:#7ccf8a;--chip:#262a31;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Noto Sans KR",sans-serif}
main{max-width:1100px;margin:0 auto;padding-block:24px 64px;padding-inline:16px}.card,.strip figure,dd{min-width:0}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 12px}
.sub{color:var(--soft);margin:0 0 20px}table{width:100%;border-collapse:collapse;font-size:13px}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{color:var(--soft);font-weight:600}.wrap{overflow-x:auto}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:16px 0}
.head{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline}.head b{font-size:18px}.chip{background:var(--chip);border-radius:999px;padding:2px 10px;font-size:12px;color:var(--soft)}
.narr{font-size:16px;margin:8px 0 12px}.strip{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.strip figure{margin:0}.strip img{width:100%;aspect-ratio:9/16;object-fit:cover;border-radius:6px;background:var(--chip)}
.strip figcaption{font-size:11px;color:var(--soft);margin-top:2px}.miss{display:flex;align-items:center;justify-content:center;aspect-ratio:9/16;border-radius:6px;background:var(--chip);color:var(--soft);font-size:12px}
dl{display:grid;grid-template-columns:140px 1fr;gap:4px 12px;margin:14px 0 0}dt{color:var(--soft)}dd{margin:0}.warn{color:var(--warn)}.ok{color:var(--ok)}
@media (max-width:640px){.strip{grid-template-columns:repeat(2,1fr)}dl{grid-template-columns:1fr}dt{margin-top:6px}}
"""


def _e(value):
    return html.escape(str(value)) if value not in (None, '') else '—'


def subject_label(ids):
    """What the size is measured on, short: 'booster' or 'leg_0·leg_foot_0'."""
    return '·'.join(i.rsplit('/', 1)[-1] for i in ids) if ids else None


def _moment_line(m, ids=None):
    if not m:
        return '아직 빌드 없음'
    label = subject_label(ids)
    bits = [f"{m['size']} ({m['height_share']:.2f}{', ' + label if label else ''})" if m.get('height_share') is not None else None, m.get('angle'),
            f"{m['lens_mm']:.0f} mm" if m.get('lens_mm') else None, f"화면 {m['place']}" if m.get('place') else None,
            f"잘림: {'·'.join(m['cut'])}" if m.get('cut') else None]
    return ', '.join(b for b in bits if b)


def _video(url):
    return f"<video src='{html.escape(url)}' controls muted loop playsinline preload='metadata' style='width:100%;max-width:200px;display:block;margin-top:6px;border-radius:6px'></video>" if url else ''


RHYTHM_WORDS = {'burst_settle': '들어가 멈춤', 'build': '가속해 드러냄', 'even': '고른 속도', 'hold': '정지'}


def feel_words(judged):
    """A camera's feel in the brief's words: its recipe, then the values that make it (camera_taste.judge 'feel')."""
    if not judged:
        return None
    from .camera_taste import recipes
    names = {r['id']: r['name'] for r in recipes()}
    feel = judged.get('feel') or {}
    head = names.get(judged.get('recipe')) or ('섞임: ' + '/'.join(names.get(a, a) for a in judged.get('also') or []) if judged.get('also') else '이름 없는 느낌')
    parts = [head]
    if feel.get('streak') is not None:
        parts.append(f"앞 스침 화면 {round(feel['streak'] * 100)}%")
    if feel.get('heading_on') is not None:
        parts.append(f"부품 쪽으로 {round(feel['heading_on'] * 100)}%")
    if feel.get('counter') is not None:
        parts.append('대상과 반대로' if feel['counter'] < -0.3 else '대상과 같은 쪽으로' if feel['counter'] > 0.3 else '대상과 엇갈려')
    if feel.get('inside') is not None and feel['inside'] >= REVEAL_SHOWN:
        parts.append(f"속 보임 화면 {round(feel['inside'] * 100)}%" + (' · 안으로 들어감' if (feel.get('inside_gain') or 0) >= 0.2 else ''))
    if feel.get('pinned') is not None and feel['pinned'] <= 0.3:
        parts.append('대상 고정, 세상 흐름')
    if feel.get('rhythm'):
        parts.append(RHYTHM_WORDS.get(feel['rhythm'], feel['rhythm']))
    if feel.get('height_m') is not None:
        parts.append(f"높이 {feel['height_m']:.1f}m")
    if feel.get('lens_mm'):
        parts.append(f"{feel['lens_mm']:.0f}mm")
    if feel.get('pitch_deg') is not None and abs(feel['pitch_deg']) >= 8:
        parts.append(f"{'올려다봄' if feel['pitch_deg'] > 0 else '내려다봄'} {abs(feel['pitch_deg']):.0f}°")
    parts.append(f"회전 {feel.get('swirl', 0.0):.1f}°/s")
    return ' · '.join(parts)


def _pace_words(card):
    pace = card.get('pace') or {}
    if not card.get('narration'):
        return '말 없음'
    if 'spoken_s' not in pace:   # a card made before pacing was measured
        return '—'
    words = f"말 약 {pace['spoken_s']:.1f}초 · 말 없이 보여 줌 {pace['look_s']:.1f}초"
    if pace.get('placed'):
        words += ' · 줄 시작 ' + ', '.join(f'{t:.1f}초' for t in pace['placed'])
    return _e(words)


def _takes(card):
    """The camera candidates of a takes sheet, side by side: clip, idea, where it sits against the reference."""
    if not card.get('takes'):
        return ''
    cells = ''.join(f"<div><b>{_e(t['id'])}</b> {_e(t.get('label'))}{_video(t.get('animatic_url'))}"
                    f"<div><b>{_e(feel_words(t.get('dynamics')))}</b></div>"
                    f"<div class='sub'>{_e((t.get('dynamics') or {}).get('words'))}</div>"
                    f"{''.join('<div class=warn>' + _e(l) + '</div>' for l in (t.get('dynamics') or {}).get('lines') or [])}</div>"
                    for t in card['takes'])
    return f"<dt>카메라 후보</dt><dd><div class='strip'>{cells}</div></dd>"


def match_words(card):
    """How the built moments follow the pictures, short: match, n differences, or the picture dropped (HTML)."""
    pictures = card.get('pictures') or []
    if not pictures:
        return '—'
    if card.get('stale'):
        return "<span class='warn'>새 그림 미빌드</span>"
    if any(m['abandoned'] for m in pictures):
        return "<span class='warn'>그림 버림</span>"
    words = []
    n = sum(len(m['problems']) for m in pictures)
    words.append("대상 <span class='ok'>일치</span>" if n == 0 else f"대상 <span class='warn'>차이 {n}</span>")
    scores = [m['layout']['score'] for m in pictures if m.get('layout') and m['layout']['score'] is not None]
    if scores:
        low = min(scores)
        words.append(f"배치 <span class='{'ok' if low >= LAYOUT_MIN_SHOWN else 'warn'}'>{low:.2f}</span>")
    reviews = [m['review'] for m in pictures if m.get('review')]
    if reviews:
        same = all(all(r[k] for k in ('objects', 'extent', 'state', 'viewpoint')) for r in reviews)
        words.append(f"눈 <span class='{'ok' if same else 'warn'}'>{'같음' if same else '다름'}</span>")
    else:
        words.append("눈 <span class='warn'>미검토</span>")
    return ' · '.join(words)


def _join_mix(joins):
    """How the reel's cuts carry the camera's motion against the reference's cuts (shares of cuts)."""
    from .grammar_core import JOIN_WORDS
    if not joins:
        return ''
    theirs = joins.get('theirs')
    rows = ''.join(f"<tr><td>{_e(word)}</td><td>{round(joins['ours'].get(kind, 0) * 100)}%</td>"
                   f"<td>{'—' if theirs is None else str(round(theirs.get(kind, 0) * 100)) + '%'}</td></tr>" for kind, word in JOIN_WORDS.items())
    return (f"<h2>컷 연결</h2><div class='wrap'><table><tr><th>컷에서 카메라 움직임</th><th>우리 릴</th><th>기준 계정</th></tr>{rows}</table></div>"
            "<p class='sub'>앞 샷 마지막 0.3초와 다음 샷 첫 0.3초의 화면 움직임 방향을 비교.</p>")


def _mix(mix):
    """The reel's recipes against the reference's (shares of shots), one row per recipe; then its cuts."""
    if not mix:
        return ''
    if not mix.get('ours'):
        return _join_mix(mix.get('joins'))
    from .camera_taste import recipes
    rows = ''.join(f"<tr><td>{_e(r['name'])}</td><td>{round(mix['ours'].get(r['id'], 0) * 100)}%</td>"
                   f"<td>{'—' if mix['theirs'] is None else str(round(mix['theirs'].get(r['id'], 0) * 100)) + '%'}</td><td class='sub'>{_e(r['means'])}</td></tr>"
                   for r in recipes())
    note = (f"기준 계정은 픽셀로만 재서 일부 느낌(맞서기·우러러보기·따라붙기·뚫고 들어가기)은 잴 수 없습니다. 이름 붙지 않은 샷 {round(mix['unnamed'] * 100)}%."
            if mix.get('unnamed') is not None else '')
    return (f"<h2>느낌 분포</h2><div class='wrap'><table><tr><th>레시피</th><th>우리 릴</th><th>기준 계정</th><th>뜻</th></tr>{rows}</table></div>"
            f"<p class='sub'>{_e(note)}</p>" + _join_mix(mix.get('joins')))


def page(cards_, title, mix=None):
    rows = []
    for c in cards_:
        size = f"{(c['first'] or {}).get('size') or '—'} → {(c['last'] or {}).get('size') or '—'}"
        angle = f"{(c['first'] or {}).get('angle') or '—'} → {(c['last'] or {}).get('angle') or '—'}"
        flag = ' <span class="warn">●</span>' if c['issues'] else ''
        rows.append(f"<tr><td><a href='#{c['shot_id']}'>{c['shot_id']}</a>{flag}</td><td>{c['start_s']:.1f}–{c['end_s']:.1f}s</td>"
                    f"<td>{match_words(c)}</td>"
                    f"<td>{_e(subject_label(c['subject']))}</td><td>{_e(size)}</td><td>{_e(angle)}</td><td>{_e(c.get('camera_move'))}</td><td>{_e(c['narration'])}</td></tr>")
    body = [f"<h1>{_e(title)}</h1><p class='sub'>샷 {len(cards_)}개 · 첫 장면과 끝 장면, 카메라, 앞 샷과의 연결, 계획과 다른 점. "
            "크기는 설명 대상의 높이를 화면 높이로 나눈 값(1보다 크면 화면 밖으로 잘림).</p>",
            "<h2>전체 흐름</h2><div class='wrap'><table><tr><th>샷</th><th>시간</th><th>그림 일치</th><th>대상</th><th>크기 첫→끝</th><th>높이각 첫→끝</th><th>카메라</th><th>내레이션</th></tr>",
            *rows, '</table></div>', _mix(mix), '<h2>샷별 카드</h2>']
    for c in cards_:
        labels = ('첫 그림', '첫 장면(실제)', '끝 그림', '끝 장면(실제)')
        figs = ''.join(f"<figure><img src='{html.escape(src)}' alt='{c['shot_id']} {label}' loading='lazy'><figcaption>{label}</figcaption></figure>"
                       if src else f"<figure><div class='miss'>{label} 없음</div><figcaption>{label}</figcaption></figure>"
                       for src, label in zip(c.get('image_urls') or [None] * 4, labels))
        plan = c['plan']
        plan_words = '; '.join(f"{k}: {v}" for k, v in (('카메라', plan.get('camera')), ('동작', plan.get('action')), ('크기', plan.get('scale_step'))) if v)
        details = '; '.join(f"{d['id']} ({d.get('why') or ''})" for d in c['details'])
        departs = ''.join(f"<div>{_e(d['picture'])} {_e(d['item'])}: {_e(d['why'])}{' (위임 정책)' if d.get('by_policy') else ''}</div>"
                          for d in c['departs'])
        cheats = ''.join(f"<div>{_e(x['picture'])} {_e(x['kind'])} {_e(x['target'])} {_e(x['value'])}: {_e(x['why'])} ({_e(x['by'])})</div>"
                         for x in c.get('cheats') or [])
        issues = ''.join(f"<div class='warn'>{_e(i)}</div>" for i in c['issues'])
        status = f"<span class='ok'>승인: {_e(c['approval'])}</span>" if c.get('approval') else _e(c.get('status'))
        body.append(
            f"<section class='card' id='{c['shot_id']}'><div class='head'><b>{c['shot_id']}</b><span>{c['start_s']:.1f}–{c['end_s']:.1f}s ({c['length_s']:.1f}s)</span>"
            f"<span class='chip'>{_e(c['role'])}</span><span class='chip'>{_e(c['version'])}</span><span class='chip'>{status}</span></div>"
            f"<div class='narr'>“{_e(c['narration'])}”</div><div class='strip'>{figs}</div><dl>"
            f"<dt>보여 줄 것</dt><dd>{_e(c['purpose'])}{('<br>' + _e(details)) if details else ''}</dd>"
            f"<dt>말과 화면</dt><dd>{_pace_words(c)}</dd>"
            f"<dt>첫 장면</dt><dd>{_e(_moment_line(c['first'], c['subject']))}</dd><dt>끝 장면</dt><dd>{_e(_moment_line(c['last'], c['subject']))}</dd>"
            f"<dt>카메라 움직임</dt><dd>{_e(c.get('camera_move'))}{_video(c.get('animatic_url'))}"
            f"{('<div>느낌: <b>' + _e(feel_words(c.get('dynamics'))) + '</b></div><div>역동성: ' + _e((c.get('dynamics') or {}).get('words')) + '</div>') if c.get('dynamics') else ''}</dd>"
            f"{_takes(c)}<dt>대상 움직임</dt><dd>{_e(c.get('screen_travel_words'))}"
            f"{('<br>계획: ' + _e(plan.get('action'))) if plan.get('action') else ''}</dd>"
            f"<dt>앞 샷과 연결</dt><dd>{_e(c['join'].get('words'))}</dd><dt>샷리스트 계획</dt><dd>{_e(plan_words)}</dd>"
            f"<dt>그림과 비교</dt><dd>{match_words(c)}{''.join('<div>' + _e(q) + '</div>' for m in c.get('pictures') or [] for q in m['problems'] + ((m.get('layout') or {}).get('problems') or []))}"
            f"{''.join('<div>눈 검토 (' + _e(m['review']['by']) + '): ' + _e(m['review']['why']) + '</div>' for m in c.get('pictures') or [] if m.get('review'))}"
            f"{''.join('<div>' + _e(k) + '는 원래 그림 ' + _e(v) + '를 모델 위에 다시 그린 것</div>' for k, v in (c.get('intent') or {}).items() if v)}</dd>"
            f"<dt>그림과 다르게 간 점</dt><dd>{departs or '—'}</dd><dt>치팅</dt><dd>{cheats or '—'}</dd><dt>남은 문제</dt><dd>{issues or '<span class=ok>없음</span>'}</dd></dl></section>")
    return ("<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{_e(title)}</title><style>{CSS}</style></head><body><main>{''.join(body)}</main></body></html>")


PALETTE = ((255, 196, 0), (0, 200, 255), (255, 90, 90), (120, 230, 120), (220, 120, 255), (255, 150, 60))


def _boxes(image, things):
    """Outline each thing's box on a thumbnail, its name above it (colours follow the names, so a thing has one colour
    on the picture and on the frame)."""
    from PIL import ImageDraw
    draw = ImageDraw.Draw(image)
    w, h = image.size
    for thing in things:
        x0, y0, x1, y1 = [max(0.0, min(1.0, v)) for v in thing['box']]
        colour = PALETTE[sum(map(ord, thing['name'])) % len(PALETTE)]
        draw.rectangle((x0 * w, y0 * h, x1 * w, y1 * h), outline=colour, width=2)
        draw.text((x0 * w + 3, y0 * h + 2), thing['name'][:22], fill=colour)


def brief(project, shots=None):
    """Write decisions/sheets/brief/bNN/{brief.html, brief.md, brief.json, img/} and return their paths and the
    BRIEF_PLAN_DIFFERS lines."""
    from PIL import Image
    root = project_dir(project)
    made = cards(root, shots)
    if not made:
        raise StudioError('INPUT_INVALID', 'no shots to brief')
    base = root / 'decisions' / 'sheets' / 'brief'
    rev = f"b{len(list(base.glob('b*'))) + 1:02d}"
    out = base / rev
    (out / 'img').mkdir(parents=True, exist_ok=True)
    for c in made:
        urls = []
        for i, rel in enumerate(c['images']):
            if not rel or 'references/' in rel:   # pictures and our own frames only; collected photos stay out
                urls.append(None)
                continue
            name = f"img/{c['shot_id']}_{i}.jpg"
            with Image.open(root / rel) as src:
                thumb = src.convert('RGB')
                thumb.thumbnail((360, 640))
                pictures = c.get('pictures') or []
                moment = pictures[0 if i < 2 else -1] if pictures else None
                if moment:   # the things, boxed: drawn on the picture, built on the frame - one colour per thing
                    _boxes(thumb, moment['drawn'] if i % 2 == 0 else moment['built'])
                thumb.save(out / name, quality=82)
            urls.append(name)
        c['image_urls'] = urls
        (out / 'vid').mkdir(exist_ok=True)
        def clip(rel, name):
            if not rel or not (root / rel).is_file():
                return None
            import shutil
            shutil.copyfile(root / rel, out / 'vid' / name)
            return f'vid/{name}'
        c['animatic_url'] = clip(c.get('animatic'), f"{c['shot_id']}.mp4")
        for t in c.get('takes') or []:
            t['animatic_url'] = clip(t.get('animatic'), f"{c['shot_id']}_{t['id']}.mp4")
    title = f"{load_project(root).get('project_id') or root.name} 구도 브리핑"
    from .camera_taste import PROFILE, load_profile, recipe_mix
    profile = load_profile() if PROFILE.is_file() else {}
    felt = [(c.get('dynamics') or {}).get('recipe') for c in made if c.get('dynamics')]
    mix = {'ours': recipe_mix(felt), 'theirs': ((profile.get('recipes') or {}).get('shares')) if profile.get('recipes') else None,
           'unnamed': (profile.get('recipes') or {}).get('unnamed_share')} if felt else None
    crossings = [c['join'].get('motion') for c in made if c['join'].get('motion')]
    if crossings:
        mix = {**(mix or {}), 'joins': {'ours': recipe_mix(crossings), 'theirs': (profile.get('joins') or {}).get('shares')}}
    (out / 'brief.html').write_text(page(made, title, mix), encoding='utf-8')
    write_json(out / 'brief.json', {'schema_version': 1, 'cards': made})
    differs = [p for c in made for p in c['join']['problems']] + pingpong_lines(made)
    total = sum(c['length_s'] for c in made)
    spoken = sum((c.get('pace') or {}).get('spoken_s') or 0 for c in made)
    from .decisions import speech_share
    if total and spoken / total > speech_share(root) + 0.05:
        differs.append(f"SCRIPT_DENSE: words fill {round(100 * spoken / total)} % of the reel ({spoken:.0f} of {total:.0f} s), the plan leaves "
                       f"{round(100 * speech_share(root))} % - lengthen shots where the picture explains, or say less")
    from .reel_flow import built as reel_flow   # the pictures shot to shot against the reference (studio/reel_flow.py)
    differs += reel_flow(root)['warnings']
    from .camera_taste import PROFILE, join_lines, load_profile, recipe_lines, reel_lines
    if PROFILE.is_file():
        profile = load_profile()
        differs += reel_lines(profile, [{'camera': c['camera_measured']} for c in made if c.get('camera_measured')])
        differs += recipe_lines(profile, [(c.get('dynamics') or {}).get('recipe') for c in made if c.get('dynamics')])
        differs += join_lines(profile, [c['join'].get('motion') for c in made if c['join'].get('motion')])
    lines = [f'# {title}', '', f'Open brief.html ({len(made)} shots).', ''] + [
        f"- {c['shot_id']} {c['start_s']:.1f}-{c['end_s']:.1f}s: {_moment_line(c['first'], c['subject'])} -> {_moment_line(c['last'], c['subject'])}; {c.get('camera_move') or ''}"
        for c in made] + ([''] + differs if differs else [])
    (out / 'brief.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return {'rev': rev, 'html': str(out / 'brief.html'), 'brief': str(out / 'brief.md'), 'warnings': differs,
            'artifacts': [str(out / 'brief.html'), str(out / 'brief.md'), str(out / 'brief.json')]}
