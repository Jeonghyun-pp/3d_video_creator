"""How strictly a project judges taste: which gates may be warnings instead of errors.

Only the gates listed in SOFTENABLE can be softened - taste learned from one reference and craft rules for shots that
explain nothing exact. Everything else (money, approvals in the user's words, licences, facts, real subjects, data
integrity, broken output) is always an error: a new hard gate needs no entry here (default deny).

project.policy.strictness: 'explain-strict' (default; today's behaviour) or 'look-first' (every softenable gate warns);
project.policy.gates overrides single codes; shot.policy.strictness overrides the project for one shot.
"""
from __future__ import annotations

from .common import StudioError, read_json

STRICTNESS = ('explain-strict', 'look-first')
DEFAULT = 'explain-strict'
SOFTENABLE = {
    'FILL_LEVEL_EMPTY': 'a seen level carries nothing the brief says it explains or identifies',
    'FILL_SUBJECT_HIDDEN': 'the subject is mostly covered by nearer identity or ambient copies',
    'FILL_OFF_BRIEF': 'fill placed that the brief does not list',
    'fill_identity_kinds': 'more than two identity kinds on a level',
    'fill_ambient_density': 'ambient life above 4 per 100 m2',
    'subject_margin': 'the rig subject crosses the frame margin',
    'look_target_hidden': 'the look target is hidden longer than the rig allows',
    'framing': 'the horizon leaves the height the move holds (learned from one reference)',
    'GRAPHIC_ILLEGIBLE': 'a screen arrow is short, thin or near the flight line (thresholds from one shot)',
    'REPAIR_BUDGET_EXHAUSTED': 'three builds without improving subject fidelity',
    'fidelity_illustrative': 'dimension, proportion or silhouette misses on a schematic or fictional subject',
    'subject_trace_illustrative': 'request words not traced in a schematic or fictional subject spec',
    'FRAME_EDGE_CUT': 'a key part touches the frame border in more than a third of its frames (frame probe)',
    'FRAME_SUBJECT_SMALL': 'the subject is small in the frame (frame probe, initial threshold)',
    'KEY_PART_SMALL': 'a key part shows but never at a readable size (frame probe)',
}


def severity_map(project, shot=None):
    """{code: 'error' | 'warn'} for every softenable code; codes not in the map are errors."""
    policy = (project or {}).get('policy') or {}
    strictness = ((shot or {}).get('policy') or {}).get('strictness') or policy.get('strictness') or DEFAULT
    if strictness not in STRICTNESS:
        raise StudioError('INPUT_INVALID', f'Unknown strictness {strictness!r} (known: {STRICTNESS})')
    severity = {code: 'warn' if strictness == 'look-first' else 'error' for code in SOFTENABLE}
    overrides = policy.get('gates') or {}
    unknown = sorted(set(overrides) - set(SOFTENABLE))
    if unknown:
        raise StudioError('INPUT_INVALID', f'policy.gates can only soften {sorted(SOFTENABLE)}; {unknown} are always errors')
    severity.update(overrides)
    return severity


def severity_for(path, shot=None):
    """The project's map, or the strict default when there is no project file (bare specs, tests)."""
    from .project import project_dir
    file = project_dir(path) / 'project.json' if path is not None else None
    return severity_map(read_json(file) if file is not None and file.is_file() else {}, shot)


def is_error(code, severity):
    return severity.get(code, 'error') == 'error'
