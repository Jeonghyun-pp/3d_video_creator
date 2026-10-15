"""Declared cheats: what a picture may change in the scene or the camera so it can be shot, and what it may never touch.
Pure (no bpy), imported on both sides: studio/cheat_core.py (what is locked, from the facts) re-exports it,
blender_ops/cheat_scene.py applies its rows and blender_ops/frames_check.py searches them.

Why (2026-10-14): a reel is for viewers, not a paper - the user: "이건 논문이 아니라 사람들한테 보여줄 영상인만큼 실제를
고집하다 영상이 노잼될 우려". What the narration claims stays true (the facts, part counts, what happens); everything
visual may cheat the way film does - a lens, an angle, a neighbour made see-through, a smaller plume, a stand-in moved
or scaled, the subject turned on its own axis - as long as it is declared. A picture that cannot be shot is first given
a cheat that keeps its punch; only when every cheat would touch what a line says is it drawn again.

What is locked is computed from the facts, never listed by hand (locked): what the shot's lines speak of (their claims'
`about`, studio/explains.line_things: elements with their scene ids, subjects, parts, features with their parts), the
structure of every subject the shot builds from a spec (its parts are never moved or scaled one by one), and the pose of
whatever a line speaks of as a whole. A state (legs out, fins open) has no cheat at all: a picture with the wrong state
is drawn again.

One table, CHEATS: a new cheat is one more row. Each row says what it touches, its limits, whether it may touch what is
locked, its cost (the search tries the cheapest first), the values the search tries (`steps`) and which Blender
mechanism applies it (`applies`: lens | view | see_through | scale | move | turn - cheat_scene.change / retarget).
"""
from __future__ import annotations

import math

try:   # the host imports this as studio.blender_ops.cheat_table, Blender as a top-level module beside its siblings
    from .camera_moves_core import VIEW_STEPS
    from .ids_core import in_group, spellings
    from .show_core import SHOW_METHODS
except ImportError:
    from camera_moves_core import VIEW_STEPS
    from ids_core import in_group, spellings
    from show_core import SHOW_METHODS

# on_locked: True - it changes no fact (the camera, a see-through skin), so it may touch what a line speaks of;
#            False - never on a locked thing; 'unclaimed_pose' - only when no line speaks of the thing as a whole.
# targets: what the search offers it to (frames_check.candidates): camera | siblings (the framed subject's own parts in
#          front of what a moment must show) | atmosphere (scene_roles atmosphere volumes) | context (things that are
#          neither the framed subject nor what it stands on) | subject (the framed subject as a whole).
CHEATS = {
    'lens': {'cost': 1, 'applies': 'lens', 'targets': 'camera', 'on_locked': True, 'limits': {'mm': [14.0, 50.0]},
             'steps': [14.0, 18.0, 24.0, 35.0, 50.0],
             'touches': 'the lens: how wide, so how strong the perspective'},
    'view': {'cost': 2, 'applies': 'view', 'targets': 'camera', 'on_locked': True,
             'limits': {'azimuth_deg': max(abs(v) for v in VIEW_STEPS['azimuth_deg']),
                        'elevation_deg': max(abs(v) for v in VIEW_STEPS['elevation_deg'])},
             'steps': None,   # camera_moves_core.candidate_views within the limits, nearest first
             'touches': "the angle: the camera off the picture's view"},
    'see_neighbours': {'cost': 3, 'applies': 'see_through', 'targets': 'siblings', 'on_locked': True,
                       'limits': {'alpha': [0.05, 1.0]}, 'steps': [SHOW_METHODS['transparency']['alpha']],
                       'touches': "the subject's own parts in front of a part the moment shows, see-through like the transparency row"},
    'atmosphere_scale': {'cost': 3, 'applies': 'scale', 'targets': 'atmosphere', 'on_locked': False,
                         'limits': {'factor': [0.3, 1.0]}, 'steps': [0.6, 0.3],
                         'touches': 'a flame, smoke or fog volume, smaller, about where it meets its source'},
    'context_move': {'cost': 4, 'applies': 'move', 'targets': 'context', 'on_locked': False,
                     'limits': {'size_share': 1.0, 'turn_deg': 180.0},
                     'steps': [{'size_share': 0.5}, {'size_share': 1.0}, {'size_share': -1.0}, {'turn_deg': 90.0}, {'turn_deg': 180.0}],
                     'touches': 'a stand-in, a person or a background thing moved (at most its own size) or turned about its upright'},
    'context_scale': {'cost': 5, 'applies': 'scale', 'targets': 'context', 'on_locked': False,
                      'limits': {'factor': [0.5, 2.0]}, 'steps': [0.5],
                      'touches': 'a stand-in, a person or a background thing made smaller or larger, standing where it stood'},
    'subject_turn': {'cost': 5, 'applies': 'turn', 'targets': 'subject', 'on_locked': 'unclaimed_pose',
                     'limits': {'deg': 45.0}, 'steps': [22.5, -22.5, 45.0, -45.0],
                     'touches': 'the whole subject turned about its own long axis (nothing of it moves against the rest)'},
}
KINDS = tuple(CHEATS)
MOVES = ('scale', 'move', 'turn')   # the mechanisms that change where a thing is or how large (see_through changes neither)


def ordered(kinds=None):
    """The kinds, cheapest first (the table's order breaks ties)."""
    names = list(kinds or KINDS)
    return sorted(names, key=lambda k: (CHEATS[k]['cost'], KINDS.index(k)))


def check_value(kind, value):
    """Why `value` is outside the row's limits, else None (the mechanism decides how a value reads)."""
    row = CHEATS.get(kind)
    if row is None:
        return f'unknown cheat {kind!r} (CHEATS: {list(KINDS)})'
    limits, how = row['limits'], row['applies']
    number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)  # noqa: E731
    if how in ('lens', 'scale', 'see_through'):
        lo, hi = next(iter(limits.values()))
        return None if number(value) and lo <= value <= hi else f'{kind} {value!r}: a number within [{lo}, {hi}]'
    if how == 'turn':
        return None if number(value) and abs(value) <= limits['deg'] else f"{kind} {value!r}: at most {limits['deg']} deg either way"
    if how == 'view':
        if not isinstance(value, dict) or not value or set(value) - set(limits) or not all(number(v) for v in value.values()):
            return f'{kind} {value!r}: {{azimuth_deg, elevation_deg}} offsets from the picture'
        over = [k for k, v in value.items() if abs(v) > limits[k] + 1e-9]
        return f'{kind} {value}: beyond {[(k, limits[k]) for k in over]}' if over else None
    if how == 'move':
        if not isinstance(value, dict) or not number(value.get('size_m', None)) or value['size_m'] <= 0:
            return f'{kind} {value!r}: {{move_m: [x, y, z], turn_deg, size_m: the thing\'s own size}}'
        move = value.get('move_m') or [0.0, 0.0, 0.0]
        if len(move) != 3 or not all(number(v) for v in move) or not number(value.get('turn_deg', 0.0)):
            return f'{kind} {value!r}: move_m is [x, y, z] metres, turn_deg a number'
        if math.hypot(*move) > limits['size_share'] * value['size_m'] + 1e-6:
            return f"{kind}: moves {math.hypot(*move):.2f} m, more than {limits['size_share']} x its size {value['size_m']:.2f} m"
        if abs(value.get('turn_deg', 0.0)) > limits['turn_deg'] + 1e-9:
            return f"{kind}: turns {value['turn_deg']} deg, more than {limits['turn_deg']}"
        return None
    return f'{kind}: no rule reads values applied by {how!r}'


def targets_of(cheat):
    target = cheat.get('target')
    return [t for t in (target if isinstance(target, list) else [target]) if t]


def _same(a, b):
    """Two ids name the same thing or one holds the other (a group and its copy, a subject and its part)."""
    return any(x == y or in_group(x, y) or in_group(y, x) for x in spellings(a) for y in spellings(b))


def part_of(target, subject):
    """The subject a target is one part of (`<subject>/<part>`, or a copy's `<subject>.n/<part>`), never the subject
    itself or a whole copy of it (`<subject>.n`, its fill copies): a whole may move, its parts keep their places."""
    import re
    return any(x.startswith(y + '/') or re.match(re.escape(y) + r'\.[^/]+/', x) for x in spellings(target) for y in spellings(subject))


def touches_locked(cheat, lock):
    """Why `cheat` touches what is locked (`lock`: cheat_core.locked), else None. A cheat that may touch locked things
    (its row's on_locked True) never does. Every other one is refused on one part of a subject built from a spec (its
    structure: the parts and their places); a scene cheat (on_locked False) also on any id a line speaks of, a pose
    cheat (on_locked 'unclaimed_pose') on what a line speaks of as a whole. Ids only: what a moved thing holds is judged
    in the scene too (cheat_scene.tree_lock)."""
    row = CHEATS[cheat['kind']]
    if row['on_locked'] is True:
        return None
    for target in targets_of(cheat):
        built = [s for s in lock.get('structure') or [] if part_of(target, s)]
        if built:
            return f"{cheat['kind']} changes {target}, one part of {built[0]}, whose parts and their places its spec fixes"
        said = [i for i in lock.get('pose' if row['on_locked'] == 'unclaimed_pose' else 'ids') or [] if _same(target, i)]
        if said and row['on_locked'] == 'unclaimed_pose':
            return f"{cheat['kind']} turns {target}, whose pose the shot's lines speak of ({said[0]})"
        if said:
            return f"{cheat['kind']} changes {target}, which the shot's lines speak of ({said[0]})"
    return None


def held_locked(kind, names, lock):
    """Why moving a thing whose tree holds objects named `names` (each object's ids: studio id, subject/part, subject,
    Blender name) touches what is locked, else None: a scene cheat on an unclaimed group that carries a claimed thing
    moves that thing too."""
    row = CHEATS[kind]
    if row['on_locked'] is True:
        return None
    key = 'pose' if row['on_locked'] == 'unclaimed_pose' else 'ids'
    for ident in names:
        said = [i for i in lock.get(key) or [] if _same(ident, i)]
        if said:
            return f"{kind} would move {ident}, which the shot's lines speak of ({said[0]})"
    return None
