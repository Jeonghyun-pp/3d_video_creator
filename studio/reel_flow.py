"""How the reel's pictures run from shot to shot, against the reference reels (warnings, never a stop).

A reel can pass every shot's checks and still be one picture repeated: the same subject in the middle at one size,
the camera never resting, no wide shot that says where we are, every shot longer than the reference's. These are
read twice: on the shot list (planned roles, sizes, lengths, hand-overs) and on the built shots (frame reports, the
camera rig). Why (2026-10-17): the rocket reel's 25 built shots showed one upright cylinder mid-frame in 20, the
camera moved in all of them, its median shot was 3.95 s; measured on the user's 84 reference reels (3,240 shots,
local measure 2026-10-17): median shot 1.8 s, a third to a half of the shots held still, 16 % filled the frame, the
wide shot came back through the reel. A shot that departs on purpose says so in its pattern departures.
"""
from __future__ import annotations

import math
import statistics
from pathlib import Path

from .common import read_json

# value, why. Local reference measure 2026-10-17 (84 reels; size is the focus box's height over the frame's).
REEL_FLOW = {
    'WIDE_HEIGHT_MAX': (0.5, "a focus at most half the frame tall leaves room for where it is: the reference's wide and medium "
                             "shots (height under 0.33 wide, under 0.66 medium) - a wide shot by this measure"),
    'ENV_SHARE_MIN': (0.15, 'what the subject stands in or on takes this much of the frame: the place shows even when the subject is large'),
    'ESTABLISH_GAP_SHARE': (0.4, 'the reference re-establishes through the reel (wide shots at about 0.02, 0.12, 0.26, 0.51 and 0.83 '
                                 'of one clear example); longer than this without a wide shot, the viewer loses where we are'),
    'SAME_BOX_IOU': (0.7, "two neighbouring shots whose focus boxes overlap this much read as one picture"),
    'SAME_VIEW_DEG': (30.0, 'the cut needs at least this change of camera angle for the same size to read as a new shot (the 30-degree rule)'),
    'OVERFILL_SHARE_MAX': (0.25, 'shots filling the frame with their focus: the reference 16 %, the flat rocket reel 32 %'),
    'FILL_HEIGHT': (0.95, 'a focus this tall fills the frame (patterns.FULL_FRAME)'),
    'STATIC_SHARE_MIN': (0.2, 'shots with the camera at rest: the reference 33-53 %, the rocket reel none - a camera that never rests tires'),
    'PACE_MEDIAN_MAX_S': (3.0, 'median shot length: the reference 1.8 s (p75 3.0 s); past its p75 the reel drags'),
    'CLOSE_HEIGHT': (0.66, "a focus taller than this is a close shot (the reference's medium/close line)"),
    'CLOSE_RUN_MAX': (4, 'close shots in a row before a wider one says where they are (re-establishing after close-ups)'),
    'ROLE_RUN_MAX': (2, 'the same role in a row past this: the reference repeats a role between neighbours 21 % of the time, the rocket reel 44 %'),
    'STILL_MOVE': (0.02, 'a camera whose position moves under this share of its distance to the subject, and turns under STILL_TURN_DEG, rests'),
    'STILL_TURN_DEG': (1.0, 'see STILL_MOVE'),
}
WIDE_ROLES = ('establish', 'hook')
WIDE_STEPS = ('wider', 'first')
DEPARTS = {'ESTABLISH_MISSING': 'scale_step', 'SAME_PICTURE': 'height_share', 'CLOSE_RUN': 'height_share'}   # the pattern item a
# shot departs on purpose to silence its finding (screen.pattern_departs / the shot list's pattern.departs)


def v(name):
    return REEL_FLOW[name][0]


def _iou(a, b):
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = w * h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _wide(r):
    if r.get('height') is not None:
        return r['height'] <= v('WIDE_HEIGHT_MAX') or (r.get('env') or 0) >= v('ENV_SHARE_MIN')
    return r.get('role') in WIDE_ROLES or r.get('scale_step') in WIDE_STEPS


def lines(rows, story=None):
    """Warnings for a reel. rows (cut order): {shot_id, len_s, role?, scale_step?, handover?, story_beats?, departs?,
    and when built: height (focus box height share, median), box (median focus box 0..1), env (support share),
    fills (bool), still (bool), view_deg}. story: the story body, for where its climax is."""
    out = []
    if not rows:
        return out
    built = all(r.get('height') is not None for r in rows)
    departs = lambda r, code: DEPARTS.get(code) in (r.get('departs') or ())  # noqa: E731
    for a, b in zip(rows, rows[1:]):   # a new place opens wide
        if a.get('handover') == 'new_place' and not _wide(b) and not departs(b, 'ESTABLISH_MISSING'):
            out.append(f"ESTABLISH_MISSING: {b['shot_id']} opens a new place (after {a['shot_id']}) "
                       + (f"with its focus {b['height']:.2f} of the frame and little around it" if b.get('height') is not None
                          else f"as a {b.get('role') or 'shot'} ({b.get('scale_step') or 'no size step'})")
                       + ' - open it wide enough to show where we are, or record the departure')
    total = sum(r['len_s'] for r in rows) or 1.0
    gap, longest, since = 0.0, (0.0, None), rows[0]['shot_id']
    for r in rows:
        if _wide(r):
            gap, since = 0.0, r['shot_id']
        else:
            gap += r['len_s']
            if gap > longest[0]:
                longest = (gap, since)
    if longest[0] / total > v('ESTABLISH_GAP_SHARE'):
        out.append(f"ESTABLISH_GAP: {longest[0]:.1f} s ({longest[0] / total:.0%} of the reel) after {longest[1]} with no wide shot - "
                   f"bring a wide one back (over {v('ESTABLISH_GAP_SHARE'):.0%} the viewer loses where we are)")
    if built:
        for a, b in zip(rows, rows[1:]):
            if a.get('handover') in ('continue', 'match_action') or departs(b, 'SAME_PICTURE'):
                continue
            if a.get('box') and b.get('box') and _iou(a['box'], b['box']) >= v('SAME_BOX_IOU') and (
                    a.get('view_deg') is None or b.get('view_deg') is None or abs(a['view_deg'] - b['view_deg']) < v('SAME_VIEW_DEG')):
                out.append(f"SAME_PICTURE: {a['shot_id']} and {b['shot_id']} frame their focus in one place at one size "
                           f"(overlap {_iou(a['box'], b['box']):.2f}) from about one angle - change the size, the side or the "
                           f"angle by {v('SAME_VIEW_DEG'):.0f} degrees or more, or show something else")
        full = [r['shot_id'] for r in rows if r.get('fills') or r['height'] >= v('FILL_HEIGHT')]
        if len(full) / len(rows) > v('OVERFILL_SHARE_MAX'):
            out.append(f"OVERFILL_SHARE: {len(full)} of {len(rows)} shots fill the frame with their focus ({', '.join(full)}) - "
                       f"over {v('OVERFILL_SHARE_MAX'):.0%}; step back on some so the subject has a place")
        stills = [r for r in rows if r.get('still') is not None]
        if stills and sum(r['still'] for r in stills) / len(stills) < v('STATIC_SHARE_MIN'):
            out.append(f"STATIC_SHARE_LOW: {sum(r['still'] for r in stills)} of {len(stills)} shots hold the camera still - under "
                       f"{v('STATIC_SHARE_MIN'):.0%}; let some shots rest (the subject moves, the camera watches)")
        run = []
        for r in rows + [None]:
            if r is not None and r['height'] > v('CLOSE_HEIGHT') and not departs(r, 'CLOSE_RUN'):
                run.append(r['shot_id'])
                continue
            if len(run) > v('CLOSE_RUN_MAX'):
                out.append(f"CLOSE_RUN: {len(run)} close shots in a row ({', '.join(run)}) - a wider one between says where they are")
            run = []
    median = statistics.median(r['len_s'] for r in rows)
    if median > v('PACE_MEDIAN_MAX_S'):
        out.append(f"PACE_SLOW: the median shot is {median:.1f} s, over {v('PACE_MEDIAN_MAX_S')} s - split long shots into "
                   'two angles or cut what the picture already says')
    if story:
        peaks = {b['story_beat_id'] for b in story['beats'] if b['function'] == 'climax'}
        climax = [r for r in rows if peaks & set(r.get('story_beats') or [])]
        if climax and statistics.median(r['len_s'] for r in climax) > median:
            out.append(f"PACE_SLOW: the climax shots ({', '.join(r['shot_id'] for r in climax)}) run longer than the reel's median "
                       f'{median:.1f} s - cut faster at the peak')
    run = []
    for r in rows + [None]:
        if r is not None and r.get('role') and run and r['role'] == run[-1]['role']:
            run.append(r)
            continue
        if len(run) > v('ROLE_RUN_MAX'):
            out.append(f"ROLE_RUN: {len(run)} {run[0]['role']} shots in a row ({', '.join(x['shot_id'] for x in run)}) - break them "
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


def _still(rig, dynamics):
    axis = ((dynamics or {}).get('camera') or {}).get('axis')
    if axis:
        return axis == 'still'
    samples = (rig or {}).get('samples') or []
    if len(samples) < 2:
        return None
    first = samples[0]
    box = first.get('target_box') or []
    span = math.dist(box[:2], box[2:4]) if len(box) == 4 else 0.0   # the target's size, in the rig's units
    moved = max(math.dist(s['camera'], first['camera']) for s in samples)
    turned = max(max(abs(s.get('pitch_deg', 0) - first.get('pitch_deg', 0)), abs(s.get('roll_deg', 0) - first.get('roll_deg', 0)),
                     abs(s.get('lens_mm', 0) - first.get('lens_mm', 0))) for s in samples)
    reach = max(span, 1e-6)
    return moved / reach < v('STILL_MOVE') and turned < v('STILL_TURN_DEG')


def built_row(project_path, entry, fps):
    """One built shot's row (None when it has no version): its focus box over its frames, environment, stillness."""
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
    rig = read_json(folder / 'camera_rig_report.json') if (folder / 'camera_rig_report.json').is_file() else None
    dynamics = read_json(folder / 'camera_dynamics.json') if (folder / 'camera_dynamics.json').is_file() else None
    pitches = [s['pitch_deg'] for s in (rig or {}).get('samples', []) if s.get('pitch_deg') is not None]
    row.update({'box': box, 'height': box[3] - box[1], 'env': (report.get('summary') or {}).get('support_share_median') or 0.0,
                'fills': sum(b[0] <= 0.002 and b[1] <= 0.002 and b[2] >= 0.998 and b[3] >= 0.998 for b in settled) * 2 >= len(settled),
                'still': _still(rig, dynamics), 'view_deg': statistics.median(pitches) if pitches else None})
    return row


def built(project):
    """The reel flow of the latest built versions, in cut order: {rows, warnings}."""
    from .project import load_project, project_dir
    from .story import body_of, load as load_story
    path = project_dir(project)
    data = load_project(path)
    rows = [built_row(path, e, data['output']['fps']) for e in data['shots']]
    measured = [r for r in rows if r.get('height') is not None]
    unbuilt = [r['shot_id'] for r in rows if r.get('height') is None]
    out = lines(measured or rows, body_of(load_story(path)))   # once any shot is measured, the measured ones are read
    if unbuilt and measured:
        out.append(f"REEL_FLOW_PARTIAL: {', '.join(unbuilt)} not built or not measured - the picture checks read the other shots only")
    return {'rows': rows, 'warnings': out}
