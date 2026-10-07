"""Frame probe, the pure part (no bpy): which frames to look at, and what a frame's class pixels say about it.

The probe (frame_probe.py) renders an id pass through the real scene camera - clip planes, lens shift and all - and
counts pixels per class: key parts (one class each), subject, support (everything else that renders) and background.
It looks at the picture, not at how the scene was made, so it judges an authored script, a declared scene and a
workbench edit the same way.

Codes:
  FRAME_EMPTY            hard   a sampled frame shows (almost) nothing (a subject out of frame for a while is the
                                shot's choice - a reveal, a dive - and is judged by FRAME_SUBJECT_SMALL over the shot)
  FRAME_NEAR_CLIP_CUT    hard   the near clip plane removes a visible share of subject / key pixels
  KEY_PART_INVISIBLE     hard on explain shots, a warning on mood shots: a key part never shows in its window
  FRAME_EDGE_CUT         softenable: a key part touches the frame border in more than a third of its frames
  FRAME_SUBJECT_SMALL    softenable: the subject's median share of the frame is small
  KEY_PART_SMALL         softenable: a key part shows, but never at a readable size
Key parts the shot declares (shot.key_parts) are judged on all key-part codes; parts the probe infers (a rig's look
target, what an approved storyboard kept in frame) only on KEY_PART_INVISIBLE - they must show, their size is the shot's.
"""
from __future__ import annotations

import statistics

# Initial values, set from the robot_joint and jet shots; measure them on the corpus and record changes in
# docs/BUILD_REPORT.md (the thresholds are the rule's numbers, the codes above are the rule).
THRESHOLDS = {
    'empty_opaque_share': 0.01,        # FRAME_EMPTY: less than 1 % of the frame renders anything
    'near_clip_cut_share': 0.005,      # FRAME_NEAR_CLIP_CUT: the near plane removes >= 0.5 % of the frame's subject/key pixels
    'key_min_px': 20,                  # KEY_PART_INVISIBLE: fewer pixels than this (at the probe's 256 px height) is not seen
    'key_readable_share': 0.004,       # KEY_PART_SMALL: never above 0.4 % of the frame (about a 16 px square at 256 px)
    'edge_frames_share': 1 / 3,        # FRAME_EDGE_CUT: touching the border in more than a third of the window's frames
    'subject_small_share': 0.02,       # FRAME_SUBJECT_SMALL: median subject share under 2 % (the winch fixture, a readable product shot, is 2.9 %)
}
HARD = ('FRAME_EMPTY', 'FRAME_NEAR_CLIP_CUT')
SOFTENABLE = ('FRAME_EDGE_CUT', 'FRAME_SUBJECT_SMALL', 'KEY_PART_SMALL')
# Support share is measured (rows, notes) but not judged: a canyon filling a jet chase is a composition, a bench covering
# a gear is a mistake, and the share alone cannot tell them apart. A rule needs evidence first (docs/BUILD_REPORT.md).
SIDES = ('left', 'right', 'top', 'bottom')


def sample_count(count):
    return 8 if count <= 240 else 12


def pick_frames(count, required=()):
    """Evenly spaced frames over the shot plus the ones that matter (storyboard focus frames, key-part windows, move
    cues), deduplicated and sorted - deterministic for the same inputs."""
    if count <= 0:
        return []
    n = min(count, sample_count(count))
    even = {round(i * (count - 1) / max(1, n - 1)) for i in range(n)}
    return sorted(even | {int(f) for f in required if 0 <= int(f) < count})


def key_windows(key_parts, count):
    """{id: (from, to)} with defaults to the whole shot."""
    return {k['id']: (int(k.get('from_frame', 0)), int(k.get('to_frame', count - 1))) for k in key_parts}


def required_frames(key_parts, count, extra=()):
    """Every key part's window midpoint and ends, plus extra frames (focus, cues)."""
    out = set(extra)
    for lo, hi in key_windows(key_parts, count).values():
        out |= {lo, hi, (lo + hi) // 2}
    return out


def metrics(counts, borders, total_px):
    """One frame's row from its class pixel counts.

    counts: {'subject': n, 'support': n, 'background': n, 'key': {id: n}, 'concealed': {id: n}}  (key and concealed
            pixels are not in 'subject'; both are part of the subject, so the shares add them back)
    borders: {side: {'subject': bool, 'key': [ids]}} - which classes touch each border
    """
    keys, concealed = counts.get('key', {}), counts.get('concealed', {})
    subject = counts.get('subject', 0) + sum(keys.values()) + sum(concealed.values())
    opaque = subject + counts.get('support', 0)
    share = lambda n: round(n / total_px, 6) if total_px else 0.0  # noqa: E731
    return {'opaque_share': share(opaque), 'subject_share': share(subject), 'support_share': share(counts.get('support', 0)),
            'key_px': dict(keys), 'key_share': {k: share(v) for k, v in keys.items()}, 'concealed_px': dict(concealed),
            'subject_edges': [s for s in SIDES if borders.get(s, {}).get('subject')],
            'key_edges': {k: [s for s in SIDES if k in borders.get(s, {}).get('key', [])] for k in keys}}


def judge(rows, key_parts, role, has_subject, count, exempt_frames=(), concealed_parts=()):
    """(failures, notes): failures are {'code', ...} dicts (the caller splits them by gate severity); notes are
    measurements kept with no verdict. role: 'explain' | 'mood' | None (the shot's route role). concealed_parts: parts
    that must not show in their window (an intact exterior hides its valve train) - any role, more than max_px is broken."""
    t = THRESHOLDS
    failures = []
    judged = [r for r in rows if r['frame'] not in set(exempt_frames)]   # whip windows: a deliberate blur of nothing
    empty = [r['frame'] for r in judged if r['opaque_share'] < t['empty_opaque_share']]
    if empty:
        failures.append({'code': 'FRAME_EMPTY', 'frames': empty[:20], 'hint': 'the camera sees nothing here'})
    cut = [(r['frame'], r['near_cut_share']) for r in rows if (r.get('near_cut_share') or 0) >= t['near_clip_cut_share']]
    if cut:
        failures.append({'code': 'FRAME_NEAR_CLIP_CUT', 'frames': [f for f, _ in cut][:20], 'max_cut_share': max(s for _, s in cut),
                         'hint': 'the near clip plane slices the subject: move the camera back or lower camera clip_start'})
    windows = key_windows(key_parts, count)
    for part in key_parts:
        lo, hi = windows[part['id']]
        inside = [r for r in rows if lo <= r['frame'] <= hi]
        if not inside:
            continue
        min_px = max(t['key_min_px'], int(part.get('min_px', 0)))
        best = max(r['key_px'].get(part['id'], 0) for r in inside)
        if best < min_px:
            code = 'KEY_PART_INVISIBLE' if role == 'explain' else 'KEY_PART_INVISIBLE_MOOD'
            failures.append({'code': code, 'part': part['id'], 'frames': [lo, hi], 'max_px': best, 'min_px': min_px,
                             'hint': 'the key part is hidden or off frame for its whole window'})
            continue
        if part.get('source', 'declared') != 'declared':   # implied keys (rig target, storyboard focus) must show; size is the shot's call
            continue
        if max(r['key_share'].get(part['id'], 0) for r in inside) < t['key_readable_share']:
            failures.append({'code': 'KEY_PART_SMALL', 'part': part['id'], 'frames': [lo, hi], 'hint': 'move closer or longer lens'})
        edged = [r['frame'] for r in inside if r['key_edges'].get(part['id'])]
        if len(edged) > t['edge_frames_share'] * len(inside):
            failures.append({'code': 'FRAME_EDGE_CUT', 'part': part['id'], 'frames': edged[:20], 'hint': 'the frame border cuts the key part'})
    hidden_windows = key_windows(concealed_parts, count)
    for part in concealed_parts:   # SKILL rule #6: broken output, not taste - never softened
        lo, hi = hidden_windows[part['id']]
        allowed = int(part.get('max_px', 0))
        shown = [(r['frame'], r.get('concealed_px', {}).get(part['id'], 0)) for r in rows if lo <= r['frame'] <= hi]
        shown = [(f, n) for f, n in shown if n > allowed]
        if shown:
            failures.append({'code': 'CONCEALED_PART_VISIBLE', 'part': part['id'], 'frames': [f for f, _ in shown][:20],
                             'max_px': max(n for _, n in shown), 'allowed_px': allowed,
                             'hint': 'a part the shot declares hidden shows through: close the shell (gap, missing cover, cut) or end the window earlier'})
    if has_subject and judged:
        median = statistics.median(r['subject_share'] for r in judged)
        if median < t['subject_small_share']:
            failures.append({'code': 'FRAME_SUBJECT_SMALL', 'median_share': round(median, 4), 'hint': 'the subject is small in the frame'})
    notes = {'subject_share_median': round(statistics.median(r['subject_share'] for r in rows), 4) if rows else None,
             'support_share_median': round(statistics.median(r['support_share'] for r in rows), 4) if rows else None,
             'frames': [r['frame'] for r in rows]}
    return failures, notes


def is_warning_by_role(failure):
    """KEY_PART_INVISIBLE (and KEY_PART_UNDER_UI, marked by_role) on a mood shot is a warning whatever the project policy:
    the shot explains nothing exact."""
    return failure['code'] == 'KEY_PART_INVISIBLE_MOOD' or bool(failure.get('by_role'))
