"""How strictly a project judges taste: which gates may be warnings instead of errors.

Only the gates listed in SOFTENABLE can be softened. Everything else (money, approvals in the user's words, licences,
facts, real subjects, data integrity, broken output) is always an error: a new hard gate needs no entry here (default
deny). Each softenable gate says what it is, and that decides its default severity:

  broken  the picture or the scene is wrong in a way generation cannot repair (the subject covered, a level empty,
          objects at the wrong real size) - an error by default
  budget  a loop that has stopped paying (builds without improving) - an error by default, so the agent stops and asks
  taste   a craft preference or a threshold learned from one reference (frame size and edges, margins, fill limits,
          camera realism, detail) - a warning by default: reported with the build, never a discarded build

Why taste warns (2026-10-07, engine_cutaway2): all three failed builds of the run were taste gates (KEY_PART_SMALL on
parts plainly visible at 0.17 % of the frame, FRAME_EDGE_CUT); each discarded a build that had passed every mechanism
check, two of three shots ended with no version, and the two real defects of the run were seen by eye, not by a gate.

project.policy.strictness: 'explain-strict' (default: broken and budget gates are errors, taste warns), 'look-first'
(every softenable gate warns), 'all-strict' (every softenable gate is an error - the default before 2026-10-07);
project.policy.gates overrides single codes either way; shot.policy.strictness overrides the project for one shot.
"""
from __future__ import annotations

from .common import StudioError, read_json

STRICTNESS = ('explain-strict', 'look-first', 'all-strict')
DEFAULT = 'explain-strict'
KINDS = ('broken', 'budget', 'taste')
ERROR_KINDS = {'explain-strict': ('broken', 'budget'), 'look-first': (), 'all-strict': KINDS}
SOFTENABLE = {   # code -> (kind, what it judges)
    'FILL_LEVEL_EMPTY': ('broken', 'a seen level carries nothing the brief says it explains or identifies'),
    'FILL_SUBJECT_HIDDEN': ('broken', 'the subject is mostly covered by nearer identity or ambient copies'),
    'FILL_OFF_BRIEF': ('taste', 'fill placed that the brief does not list'),
    'fill_identity_kinds': ('taste', 'more than two identity kinds on a level'),
    'fill_ambient_density': ('taste', 'ambient life above 4 per 100 m2'),
    'subject_margin': ('taste', 'the rig subject crosses the frame margin'),
    'look_target_hidden': ('taste', 'the look target is hidden longer than the rig allows'),
    'framing': ('taste', 'the horizon leaves the height the move holds (learned from one reference)'),
    'GRAPHIC_ILLEGIBLE': ('taste', 'a screen arrow is short, thin or near the flight line (thresholds from one shot)'),
    'REPAIR_BUDGET_EXHAUSTED': ('budget', 'three builds without improving subject fidelity'),
    'fidelity_illustrative': ('taste', 'dimension, proportion or silhouette misses on a schematic or fictional subject'),
    'subject_trace_illustrative': ('taste', 'request words not traced in a schematic or fictional subject spec'),
    'FRAME_EDGE_CUT': ('taste', 'a key part touches the frame border in more than a third of its frames (frame probe)'),
    'FRAME_SUBJECT_SMALL': ('taste', 'the subject is small in the frame (frame probe, initial threshold)'),
    'KEY_PART_SMALL': ('taste', 'a key part shows but never at a readable size (frame probe)'),
    'look_scale': ('broken', 'objects classified by name or category sit outside their real dimensions (look scale audit)'),
    'look_camera_dof': ('taste', 'the subject is softer than the depth-of-field limit even stopped down'),
    'look_camera_shake': ('taste', 'camera shake moves label anchors more than the jitter limit'),
    'look_camera_two_point': ('taste', 'the two-point correction cannot hold the verticals within tolerance'),
    'detail_placeholder': ('taste', 'a visible part is a coarse primitive (fidelity detail check, SKILL #8)'),
}


def severity_map(project, shot=None):
    """{code: 'error' | 'warn'} for every softenable code; codes not in the map are errors."""
    policy = (project or {}).get('policy') or {}
    strictness = ((shot or {}).get('policy') or {}).get('strictness') or policy.get('strictness') or DEFAULT
    if strictness not in STRICTNESS:
        raise StudioError('INPUT_INVALID', f'Unknown strictness {strictness!r} (known: {STRICTNESS})')
    severity = {code: 'error' if kind in ERROR_KINDS[strictness] else 'warn' for code, (kind, _) in SOFTENABLE.items()}
    overrides = policy.get('gates') or {}
    unknown = sorted(set(overrides) - set(SOFTENABLE))
    if unknown:
        raise StudioError('INPUT_INVALID', f'policy.gates can only soften {sorted(SOFTENABLE)}; {unknown} are always errors')
    severity.update(overrides)
    return severity


def severity_for(path, shot=None):
    """The project's map, or the default when there is no project file (bare specs, tests)."""
    from .project import project_dir
    file = project_dir(path) / 'project.json' if path is not None else None
    return severity_map(read_json(file) if file is not None and file.is_file() else {}, shot)


def is_error(code, severity):
    return severity.get(code, 'error') == 'error'
