"""How the reel's pictures run from shot to shot, against the study set of reference reels (warnings, never a stop).

A reel can pass every shot's checks and still be one picture repeated: the same subject in the middle at one size,
the camera never resting, no wide shot that says where we are, every shot longer than the reference's. These are
read twice: on the shot list (planned roles, sizes, lengths, hand-overs) and on the built shots (frame reports, the
probe's id pictures, the camera). Why (2026-10-17): the rocket reel's 25 built shots showed one upright cylinder
mid-frame in 20, the camera moved in all of them, its median shot was 3.95 s; measured on the study set (local
measure 2026-10-17): median shot 1.8 s, a third to a half of the shots held still, 16 % filled the frame, the wide
shot came back through the reel.

A shot that departs on purpose says so in its pattern departures (the finding's code in lower case, with why: the
shot list's pattern.departs, the shot's screen.pattern_departs); a choice about the whole reel is a reel departure
(`studio reel depart`, the user's words or a delegated run's reason). Both are read for what they name only.
"""
from __future__ import annotations

import math
import statistics
from pathlib import Path

from .common import StudioError, now, read_json, write_json

# value, why. Local measure of the study set, 2026-10-17 (size is the focus box's height over the frame's).
REEL_FLOW = {
    'WIDE_HEIGHT_MAX': (0.5, "a focus at most half the frame tall leaves room for where it is: the study set's wide and medium "
                             "shots (height under 0.33 wide, under 0.66 medium) - a wide shot by this measure"),
    'ENV_SHARE_MIN': (0.15, 'what the subject stands in or on takes this much of the frame: the place shows even when the subject is large'),
    'ESTABLISH_GAP_SHARE': (0.4, 'the study set re-establishes through the reel (wide shots come back in its opening, middle and late '
                                 'parts); longer than this without a wide shot, the viewer loses where we are'),
    'SAME_SILHOUETTE_IOU': (0.7, "two neighbouring shots whose subject silhouettes, across the cut, overlap this much read as one picture"),
    'SAME_VIEW_DEG': (30.0, 'the cut needs at least this change of camera direction for the same size to read as a new shot (the 30-degree rule)'),
    'OVERFILL_SHARE_MAX': (0.25, 'shots filling the frame with their focus: the study set 16 %, the flat rocket reel 32 %'),
    'FILL_HEIGHT': (0.95, 'a focus this tall fills the frame (patterns.FULL_FRAME)'),
    'STATIC_SHARE_MIN': (0.2, 'shots with the camera at rest: the study set 33-53 %, the rocket reel none - a camera that never rests tires'),
    'PACE_MEDIAN_MAX_S': (3.0, 'median shot length: the study set 1.8 s (p75 3.0 s); past its p75 the reel drags'),
    'CLOSE_HEIGHT': (0.66, "a focus taller than this is a close shot (the study set's medium/close line)"),
    'CLOSE_RUN_MAX': (4, 'close shots in a row before a wider one says where they are (re-establishing after close-ups)'),
    'ROLE_RUN_MAX': (2, 'the same role in a row past this: the study set repeats a role between neighbours 21 % of the time, the rocket reel 44 %'),
    'STILL_MOVE': (0.02, 'a camera moving less than this share of its distance to what it looks at stays where it is'),
    'STILL_TURN_DEG': (1.0, 'a camera turning (pan or tilt) less than this over the shot looks the same way'),
    'STILL_LENS_LOG': (0.05, 'a lens changing less than this (natural log of the focal length ratio, about 5 %) holds the same view'),
    'SUPPORT_GREY': (12, "the probe's id pictures paint what the subject stands on in mid grey (frame_probe palette[0]); a pixel "
                         'within this of 127 on every channel is the place, black is nothing, the rest is the subject'),
}

# A shot departs a finding on purpose by naming its code (lower case) in its pattern departures; what that does - only
# to that shot, never to the reel's other shots. A reel departure (reel_departs.json) silences the code for the reel.
DEPARTS = {
    'ESTABLISH_MISSING': ('establish_missing', 'this shot opens a new place close on purpose (a mystery, a cold open): not reported'),
    'ESTABLISH_GAP': ('establish_gap', 'its seconds do not count toward a stretch without a wide shot (it says where we are another way)'),
    'SAME_PICTURE': ('same_picture', 'the cut into this shot repeats the picture on purpose (a match, a beat held): not reported'),
    'OVERFILL_SHARE': ('overfill_share', 'it fills the frame on purpose (a climax, a texture): not counted among the full ones'),
    'STATIC_SHARE_LOW': ('static_share_low', 'its camera moves on purpose (a follow, a reveal): it leaves the count of resting shots'),
    'PACE_SLOW': ('pace_slow', 'it runs long on purpose (a held reveal, a payoff): it leaves the median and the climax set'),
    'CLOSE_RUN': ('close_run', 'a close shot that belongs in the run on purpose: skipped, neither counted nor breaking it'),
    'ROLE_RUN': ('role_run', 'its role repeats on purpose: skipped, neither counted nor breaking the run'),
}
DEPART_ITEMS = {item: code for code, (item, _) in DEPARTS.items()}
WIDE_ROLES = ('establish', 'hook')
WIDE_STEPS = ('wider', 'first')
REEL_DEPARTS = 'reel_departs.json'


def v(name):
    return REEL_FLOW[name][0]


def _iou_masks(a, b):
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _view_angle(a, b):
    """Degrees between two camera directions given as [elevation, azimuth] around the subject."""
    def unit(view):
        el, az = math.radians(view[0]), math.radians(view[1])
        return (math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el))
    dot = sum(x * y for x, y in zip(unit(a), unit(b)))
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


def _wide(r):
    if r.get('height') is not None:
        return r['height'] <= v('WIDE_HEIGHT_MAX') or (r.get('env') or 0) >= v('ENV_SHARE_MIN')
    return r.get('role') in WIDE_ROLES or r.get('scale_step') in WIDE_STEPS


def _departs(r, code):
    return DEPARTS[code][0] in (r.get('departs') or ())


def lines(rows, story=None, silenced=()):
    """Warnings for a reel. rows (cut order): {shot_id, len_s, role?, scale_step?, handover?, story_beats?, departs?,
    and when built (measured): height (focus box height share, median), env (support share), fills (bool), still
    (bool | None), silhouette_start/_end (pixel sets), view_start/_end ([elevation, azimuth] | None)}. A shot not
    measured breaks every neighbour comparison it sits in. story: the story body, for where its climax is. silenced:
    codes the reel departs from (reel departures)."""
    out = []
    if not rows:
        return out
    say = lambda code, text: out.append(f'{code}: {text}') if code not in silenced else None   # noqa: E731
    measured = lambda r: r.get('height') is not None   # noqa: E731
    for a, b in zip(rows, rows[1:]):   # a new place opens wide
        if a.get('handover') == 'new_place' and not _wide(b) and not _departs(b, 'ESTABLISH_MISSING'):
            say('ESTABLISH_MISSING', f"{b['shot_id']} opens a new place (after {a['shot_id']}) "
                + (f"with its focus {b['height']:.2f} of the frame and little around it" if measured(b)
                   else f"as a {b.get('role') or 'shot'} ({b.get('scale_step') or 'no size step'})")
                + ' - open it wide enough to show where we are, or record the departure (establish_missing)')
    total = sum(r['len_s'] for r in rows) or 1.0
    gap, longest, since = 0.0, (0.0, None), rows[0]['shot_id']
    for r in rows:
        if _wide(r):
            gap, since = 0.0, r['shot_id']
        elif not _departs(r, 'ESTABLISH_GAP'):
            gap += r['len_s']
            if gap > longest[0]:
                longest = (gap, since)
    if longest[0] / total > v('ESTABLISH_GAP_SHARE'):
        say('ESTABLISH_GAP', f"{longest[0]:.1f} s ({longest[0] / total:.0%} of the reel) after {longest[1]} with no wide shot - "
            f"bring a wide one back (over {v('ESTABLISH_GAP_SHARE'):.0%} the viewer loses where we are)")
    built = [r for r in rows if measured(r)]
    for a, b in zip(rows, rows[1:]):   # neighbours across a cut, both measured (an unbuilt shot between breaks it)
        if not (measured(a) and measured(b)) or a.get('handover') in ('continue', 'match_action') or _departs(b, 'SAME_PICTURE'):
            continue
        if a.get('silhouette_end') is None or b.get('silhouette_start') is None or a.get('view_end') is None or b.get('view_start') is None:
            continue   # an unknown silhouette or direction is not the same one
        overlap, turn = _iou_masks(a['silhouette_end'], b['silhouette_start']), _view_angle(a['view_end'], b['view_start'])
        if overlap >= v('SAME_SILHOUETTE_IOU') and turn < v('SAME_VIEW_DEG'):
            say('SAME_PICTURE', f"{a['shot_id']} and {b['shot_id']} cut between one silhouette (overlap {overlap:.2f}) seen from one "
                f"direction ({turn:.0f} degrees apart) - change the size, the side or the angle by {v('SAME_VIEW_DEG'):.0f} degrees "
                'or more, show something else, or record the departure (same_picture)')
    if built:
        counted = [r for r in built if not _departs(r, 'OVERFILL_SHARE')]
        full = [r['shot_id'] for r in counted if r.get('fills') or r['height'] >= v('FILL_HEIGHT')]
        if counted and len(full) / len(counted) > v('OVERFILL_SHARE_MAX'):
            say('OVERFILL_SHARE', f"{len(full)} of {len(counted)} shots fill the frame with their focus ({', '.join(full)}) - "
                f"over {v('OVERFILL_SHARE_MAX'):.0%}; step back on some so the subject has a place")
        stills = [r for r in built if r.get('still') is not None and not _departs(r, 'STATIC_SHARE_LOW')]
        if stills and sum(r['still'] for r in stills) / len(stills) < v('STATIC_SHARE_MIN'):
            say('STATIC_SHARE_LOW', f"{sum(r['still'] for r in stills)} of {len(stills)} shots hold the camera still - under "
                f"{v('STATIC_SHARE_MIN'):.0%}; let some shots rest (the subject moves, the camera watches)")
    run = []
    for r in rows + [None]:
        if r is not None and measured(r) and _departs(r, 'CLOSE_RUN'):
            continue
        if r is not None and measured(r) and r['height'] > v('CLOSE_HEIGHT'):
            run.append(r['shot_id'])
            continue
        if len(run) > v('CLOSE_RUN_MAX'):
            say('CLOSE_RUN', f"{len(run)} close shots in a row ({', '.join(run)}) - a wider one between says where they are")
        run = []
    paced = [r for r in rows if not _departs(r, 'PACE_SLOW')]
    median = statistics.median(r['len_s'] for r in paced) if paced else 0.0
    if median > v('PACE_MEDIAN_MAX_S'):
        say('PACE_SLOW', f"the median shot is {median:.1f} s, over {v('PACE_MEDIAN_MAX_S')} s - split long shots into "
            'two angles or cut what the picture already says')
    if story and paced:
        peaks = {b['story_beat_id'] for b in story['beats'] if b['function'] == 'climax'}
        climax = [r for r in paced if peaks & set(r.get('story_beats') or [])]
        if climax and statistics.median(r['len_s'] for r in climax) > median:
            say('PACE_SLOW', f"the climax shots ({', '.join(r['shot_id'] for r in climax)}) run longer than the reel's median "
                f'{median:.1f} s - cut faster at the peak')
    run = []
    for r in rows + [None]:
        if r is not None and _departs(r, 'ROLE_RUN'):
            continue
        if r is not None and r.get('role') and run and r['role'] == run[-1]['role']:
            run.append(r)
            continue
        if len(run) > v('ROLE_RUN_MAX'):
            say('ROLE_RUN', f"{len(run)} {run[0]['role']} shots in a row ({', '.join(x['shot_id'] for x in run)}) - break them "
                'with a reveal, a comparison or a consequence')
        run = [r] if r is not None and r.get('role') else []
    return out


def plan_rows(shots, lines_by_id=None):
    """Rows from a shot list body (decisions): planned role, size step, length, hand-over, story beats."""
    from .story_core import beats_of
    line_beat = {i: l.get('story_beat_id') for i, l in (lines_by_id or {}).items()}
    return [{'shot_id': s['shot_id'], 'len_s': s['duration_s'], 'role': s.get('role'), 'scale_step': (s.get('direction') or {}).get('scale_step'),
             'handover': (s.get('handover') or {}).get('kind'), 'story_beats': s.get('story_beat_ids') or beats_of(s['line_ids'], line_beat),
             'departs': [d['item'] for d in (s.get('pattern') or {}).get('departs', [])]} for s in shots]


def _direction(position, aim):
    d = [b - a for a, b in zip(position, aim)]
    n = math.sqrt(sum(x * x for x in d)) or 1.0
    return [x / n for x in d], n


def _still(rig, move, dynamics):
    """Does the camera rest over the shot? The camera's own measure when it has one (camera_dynamics: axis 'still');
    else its travel against its distance to what it looks at, how far it turns (pan and tilt, from the move's poses
    and the rig's pitch) and how much its lens changes. None when neither says where it looks (unknown, not moving)."""
    axis = ((dynamics or {}).get('camera') or {}).get('axis')
    if axis:
        return axis == 'still'
    poses = [f for f in (move or {}).get('frames') or [] if f.get('position') and f.get('aim')]
    samples = [s for s in (rig or {}).get('samples') or [] if s.get('camera')]
    if not poses:
        return None
    first, reach = _direction(poses[0]['position'], poses[0]['aim'])
    places = [s['camera'] for s in samples] or [f['position'] for f in poses]
    moved = max(math.dist(p, places[0]) for p in places) / max(reach, 1e-6)
    turned = max(math.degrees(math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(first, _direction(f['position'], f['aim'])[0]))))))
                 for f in poses)
    pitches = [s['pitch_deg'] for s in samples if s.get('pitch_deg') is not None]
    if pitches:
        turned = max(turned, max(pitches) - min(pitches))
    lenses = [x for x in [f.get('lens_mm') for f in poses] + [s.get('lens_mm') for s in samples] if x]
    zoom = abs(math.log(max(lenses) / min(lenses))) if lenses else 0.0
    return moved < v('STILL_MOVE') and turned < v('STILL_TURN_DEG') and zoom < v('STILL_LENS_LOG')


def silhouette(image):
    """The subject's pixels in a probe id picture (frame_probe/frame_NNNN_id.png): neither black (nothing) nor the
    support grey (the place) - a set of pixel indices, at the picture's size."""
    from PIL import Image
    tol = v('SUPPORT_GREY')
    with Image.open(image) as src:
        raw = src.convert('RGB').tobytes()
    out = set()
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        if r == g == b == 0 or (abs(r - 127) <= tol and abs(g - 127) <= tol and abs(b - 127) <= tol):
            continue
        out.add(i // 3)
    return out


def _ends(folder):
    """The probe's first and last id pictures of a version (None when it kept none)."""
    pictures = sorted((folder / 'frame_probe').glob('frame_*_id.png'))
    return (pictures[0], pictures[-1]) if pictures else (None, None)


def built_row(project_path, entry, fps):
    """One built shot's row (unmeasured when it has no version): its focus box over its frames, environment, stillness,
    its subject silhouette and camera direction at its first and last frame."""
    from .patterns import _union, focus_of
    from .project import load_shot
    shot = load_shot(project_path, entry['shot_id'])
    row = {'shot_id': entry['shot_id'], 'len_s': entry['frame_count'] / fps, 'role': (shot.get('screen') or {}).get('role'),
           'scale_step': (shot.get('direction') or {}).get('scale_step'), 'handover': (shot.get('handover') or {}).get('kind'),
           'story_beats': shot.get('story_beat_ids') or [], 'departs': [d['item'] for d in (shot.get('screen') or {}).get('pattern_departs', [])]}
    version = shot.get('scene_version')
    folder = Path(project_path) / 'shots' / entry['shot_id'] / 'versions' / str(version)
    if not version or not (folder / 'frame_report.json').is_file():
        return row
    report = read_json(folder / 'frame_report.json')
    ids, _ = focus_of(shot)
    classes = [f'key:{i}' for i in ids] if ids else ['subject']
    boxes = [b for b in (_union([f['shapes'][c]['bbox'] for c in classes if ((f.get('shapes') or {}).get(c) or {}).get('bbox')])
                         for f in report.get('frames', [])) if b]
    if not boxes:
        return row
    box = [statistics.median(b[k] for b in boxes) for k in range(4)]
    settled = boxes[int(len(boxes) * 0.6):] or boxes
    read = lambda name: read_json(folder / name) if (folder / name).is_file() else None   # noqa: E731
    rig, move, dynamics = read('camera_rig_report.json'), read('camera_move_report.json'), read('camera_dynamics.json')
    views = [f['view'] for f in (move or {}).get('frames') or [] if f.get('view')]
    first, last = _ends(folder)
    row.update({'height': box[3] - box[1], 'env': (report.get('summary') or {}).get('support_share_median') or 0.0,
                'fills': sum(b[0] <= 0.002 and b[1] <= 0.002 and b[2] >= 0.998 and b[3] >= 0.998 for b in settled) * 2 >= len(settled),
                'still': _still(rig, move, dynamics), 'view_start': views[0] if views else None, 'view_end': views[-1] if views else None,
                'silhouette_start': silhouette(first) if first else None, 'silhouette_end': silhouette(last) if last else None})
    return row


def reel_departs(path):
    from .project import project_dir
    file = project_dir(path) / REEL_DEPARTS
    return read_json(file).get('departs', []) if file.is_file() else []


def silenced(path):
    return {d['code'] for d in reel_departs(path)}


def depart(project, code, why, user_words=None, agent_note=None, remove=False):
    """A choice about the whole reel that a flow finding reads as on purpose (a reel of close textures, a reel in one
    long take): the user's words, or a delegated run's reason. remove takes it back."""
    from .project import load_project, project_dir
    path = project_dir(project)
    if code not in DEPARTS:
        raise StudioError('INPUT_INVALID', f'reel depart: {code} is not a flow finding ({sorted(DEPARTS)})')
    rows = [d for d in reel_departs(path) if d['code'] != code]
    if not remove:
        if not why or len(why.strip()) < 12:
            raise StudioError('INPUT_INVALID', 'reel depart needs why (a sentence)')
        if user_words:
            from .generative.review import check_user_words
            who = {'by': 'user', 'user_words': check_user_words(user_words, 'reel depart')}
        elif agent_note and load_project(path).get('delegation'):
            who = {'by': 'agent', 'agent_note': agent_note}
        else:
            raise StudioError('INPUT_INVALID', "reel depart needs the user's words; only a delegated run departs on its own reason (--agent-note)")
        rows.append({'code': code, 'why': why.strip(), **who, 'at': now()})
    write_json(path / REEL_DEPARTS, {'schema_version': 1, 'departs': rows})
    return {'departs': rows, 'artifacts': [str(path / REEL_DEPARTS)]}


def built(project):
    """The reel flow of the latest built versions, in cut order: {rows, warnings, errors} (errors: findings the
    project's gate policy makes errors - all-strict, policy.gates)."""
    from .gates import severity_for, sort_lines
    from .project import load_project, project_dir
    from .story import body_of, load as load_story
    path = project_dir(project)
    data = load_project(path)
    rows = [built_row(path, e, data['output']['fps']) for e in data['shots']]
    unbuilt = [r['shot_id'] for r in rows if r.get('height') is None]
    out = lines(rows, body_of(load_story(path)), silenced(path))
    if unbuilt and len(unbuilt) < len(rows):
        out.append(f"REEL_FLOW_PARTIAL: {', '.join(unbuilt)} not built or not measured - the picture checks skip them and the cuts beside them")
    errors, warnings = sort_lines(out, severity_for(path))
    shown = [{k: val for k, val in r.items() if not k.startswith('silhouette')} for r in rows]
    return {'rows': shown, 'warnings': warnings, 'errors': errors, 'departs': reel_departs(path)}
