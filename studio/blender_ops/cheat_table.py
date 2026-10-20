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
mechanism applies it (`applies`: lens | view | see_through | scale | move | turn | explode - cheat_scene.change /
retarget) and its `words`, how the sheet says it to the user.

Opening what hides a part (2026-10-17): a moment that explains a part may show it the way a cutaway drawing does - what
wraps it or stands in front made see-through first (see_shown, see_enclosure), taken apart along the subject's long axis
for that moment last (explode_apart, the dearest) - the user: "카메라와 가리는것 사이 물체들을 반투명하거나 분리해서
보여주는건 별로인가? 어차피 부품 설명하는 화면이면 해체해도 되는거 아니야?". Never the whole subject, never what a line
says the state or place of, never what another moment shows (refused).
"""
from __future__ import annotations

import math

try:   # the host imports this as studio.blender_ops.cheat_table, Blender as a top-level module beside its siblings
    from .camera_moves_core import VIEW_STEPS
    from .ids_core import in_group, spellings
    from .show_core import SHOW_METHODS, SHOWN_THROUGH
except ImportError:
    from camera_moves_core import VIEW_STEPS
    from ids_core import in_group, spellings
    from show_core import SHOW_METHODS, SHOWN_THROUGH

# How see-through a skin must be for what stands behind it to count as seen (the probe and the build read through every
# skin): the shown part's own fade (show_core.SHOWN_THROUGH), the most solid see-through any row draws - a skin more solid
# than this (alpha 0.05 is 95 % opaque) hides what is behind it to a viewer, so no cheat may draw one. Review 2026-10-17.
SEEN_THROUGH_MIN = SHOWN_THROUGH['alpha']
# A part taken apart moves there from the moment before and back by the moment after: each way takes at least this many
# seconds, or the cut shows it jump (a part flung its own size in a few frames) - viewing comfort over the picture
# (memory: comfort over reference match). Review 2026-10-17.
APART_MIN_S = 0.5

# on_locked: True - it changes no fact (the camera, a see-through skin), so it may touch what a line speaks of;
#            False - never on a locked thing; 'unclaimed_pose' - only when no line speaks of the thing as a whole;
#            'unclaimed_state' - only on parts no line speaks of (their state or place is said: legs out, the tank above).
# targets: what the search offers it to (frames_check.candidates): camera | siblings (the framed subject's own parts in
#          front of what a moment must show) | atmosphere (scene_roles atmosphere volumes) | context (things that are
#          neither the framed subject nor what it stands on) | subject (the framed subject as a whole) | shown (a part
#          the moment shows in front of another it shows) | enclosure (the subject's own parts that wrap what the moment
#          must show) | apart (the subject's own parts in front of or round it, none the shot shows).
# part_shot: offered only at a moment that explains parts (part_shot()).
CHEATS = {
    'lens': {'cost': 1, 'applies': 'lens', 'targets': 'camera', 'on_locked': True, 'limits': {'mm': [14.0, 50.0]},
             'steps': [14.0, 18.0, 24.0, 35.0, 50.0],
             'touches': 'the lens: how wide, so how strong the perspective', 'words': '렌즈 바꾸기 (원근감)'},
    'view': {'cost': 2, 'applies': 'view', 'targets': 'camera', 'on_locked': True,
             'limits': {'azimuth_deg': max(abs(v) for v in VIEW_STEPS['azimuth_deg']),
                        'elevation_deg': max(abs(v) for v in VIEW_STEPS['elevation_deg'])},
             'steps': None,   # camera_moves_core.candidate_views within the limits, nearest first
             'touches': "the angle: the camera off the picture's view", 'words': '각도 바꾸기 (그림의 시점에서 벗어남)'},
    'see_neighbours': {'cost': 3, 'applies': 'see_through', 'targets': 'siblings', 'on_locked': True,
                       'limits': {'alpha': [SEEN_THROUGH_MIN, 1.0]}, 'steps': [SHOW_METHODS['transparency']['alpha']],
                       'touches': "the subject's own parts in front of a part the moment shows, see-through like the transparency row",
                       'words': '앞을 가리는 옆 부품을 반투명하게'},
    'see_shown': {'cost': 3, 'applies': 'see_through', 'targets': 'shown', 'on_locked': True,
                  'limits': {'alpha': [SEEN_THROUGH_MIN, 1.0]}, 'steps': [SHOWN_THROUGH['alpha']],
                  'touches': 'a part the moment shows that stands in front of another part it shows, see-through (show_core.SHOWN_THROUGH)',
                  'words': '함께 보여줄 부품이 다른 부품을 가려서, 앞의 것을 반투명하게'},
    'see_enclosure': {'cost': 3, 'applies': 'see_through', 'targets': 'enclosure', 'on_locked': True, 'part_shot': True,
                      'limits': {'alpha': [SEEN_THROUGH_MIN, 1.0]}, 'steps': [SHOW_METHODS['transparency']['alpha']],
                      'touches': "the subject's own parts that wrap a part the moment explains, see-through like the transparency row",
                      'words': '부품을 감싼 겉면을 반투명하게 (부품을 설명하는 순간)'},
    'atmosphere_scale': {'cost': 3, 'applies': 'scale', 'targets': 'atmosphere', 'on_locked': False,
                         'limits': {'factor': [0.3, 1.0]}, 'steps': [0.6, 0.3],
                         'touches': 'a flame, smoke or fog volume, smaller, about where it meets its source', 'words': '불꽃·연기·안개를 작게'},
    'context_move': {'cost': 4, 'applies': 'move', 'targets': 'context', 'on_locked': False,
                     'limits': {'size_share': 1.0, 'turn_deg': 180.0},
                     'steps': [{'size_share': 0.5}, {'size_share': 1.0}, {'size_share': -1.0}, {'turn_deg': 90.0}, {'turn_deg': 180.0}],
                     'touches': 'a stand-in, a person or a background thing moved (at most its own size) or turned about its upright',
                     'words': '주변 물체를 옮기거나 돌리기 (제 크기 이내)'},
    'context_scale': {'cost': 5, 'applies': 'scale', 'targets': 'context', 'on_locked': False,
                      'limits': {'factor': [0.5, 2.0]}, 'steps': [0.5],
                      'touches': 'a stand-in, a person or a background thing made smaller or larger, standing where it stood',
                      'words': '주변 물체를 작게 또는 크게'},
    # 2026-10-20 (canal): the banks, the hills and a ship stood between the camera and the lock in 1238 refused views; the
    # see-through rows reached only the subject's own parts. A thing round the subject made see-through changes no fact
    # (as see_neighbours), so it may touch what a line speaks of; what a moment shows is never faded by it. After moving it
    # (context_move) in the order: what hides a thing is moved before it is faded (SKILL rule 12's order); what cannot be
    # moved - the ground it stands in - is faded.
    'see_context': {'cost': 5, 'applies': 'see_through', 'targets': 'context', 'on_locked': True,
                    'limits': {'alpha': [SEEN_THROUGH_MIN, 1.0]}, 'steps': [SHOW_METHODS['transparency']['alpha']],
                    'touches': 'a thing round the subject (the ground it stands in, a neighbour, a passer-by) in front of what the '
                               'moment shows, see-through like the transparency row',
                    'words': '앞을 가리는 주변 물체(땅, 이웃, 지나가는 것)를 반투명하게'},
    'subject_turn': {'cost': 5, 'applies': 'turn', 'targets': 'subject', 'on_locked': 'unclaimed_pose',
                     'limits': {'deg': 45.0}, 'steps': [22.5, -22.5, 45.0, -45.0],
                     'touches': 'the whole subject turned about its own long axis (nothing of it moves against the rest)',
                     'words': '대상 전체를 제 긴 축으로 돌리기'},
    'explode_apart': {'cost': 6, 'applies': 'explode', 'targets': 'apart', 'on_locked': 'unclaimed_state', 'part_shot': True,
                      'limits': {'length_share': [0.3, 1.5]}, 'steps': [0.6, 1.2],
                      'touches': "the subject's own parts in front of or round a part the moment explains, moved apart along the "
                                 "subject's long axis (a share of their own size) at that moment and back at the moments beside it",
                      'words': '가리는 부품을 긴 축을 따라 잠깐 떼어 냈다가 되돌리기 (부품을 설명하는 순간)'},
}
# A see-through or taken-apart cheat never takes more than this share of the framed subject's objects (the camera's
# target): past it the subject itself is gone, not opened - and the framed subject is never a target of one. Why: the
# plan's risk "화면이 복잡해짐" (2026-10-17). A neighbour faded whole is no part of the subject: not bounded by it.
WHOLE_SUBJECT_SHARE = 0.6
OPENS = ('see_through', 'explode')   # the mechanisms that open a subject (what WHOLE_SUBJECT_SHARE and the refusals bound)
KINDS = tuple(CHEATS)
MOVES = ('scale', 'move', 'turn', 'explode')   # the mechanisms that change where a thing is or how large (see_through changes neither)


def ordered(kinds=None):
    """The kinds, cheapest first (the table's order breaks ties)."""
    names = list(kinds or KINDS)
    return sorted(names, key=lambda k: (CHEATS[k]['cost'], KINDS.index(k)))


def in_rounds(cheats):
    """Cheats (each {kind, ...}, cheapest first) in rounds: every kind's first candidate, cheapest kind first, then every
    kind's second, and so on. A search under a time limit tries each mechanism before the next candidate of any: a
    failing try costs as much as a whole solve, so two longer lenses could spend a moment's time before the parts in
    front were ever made see-through (2026-10-15: four moments undecided that way). Within a round the cost order
    stands, so of two kinds that solve in the same round the cheaper is found first."""
    seen, ranked = {}, []
    for i, c in enumerate(cheats):
        n = seen[c['kind']] = seen.get(c['kind'], -1) + 1
        ranked.append((n, CHEATS[c['kind']]['cost'], i, c))
    return [c for *_key, c in sorted(ranked, key=lambda r: r[:3])]


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
    if how == 'explode':   # the sign is the way (apart_sign); the size is the share
        lo, hi = next(iter(limits.values()))
        return None if number(value) and lo <= abs(value) <= hi else f'{kind} {value!r}: a number within [{lo}, {hi}] either way'
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


def held_by(owner, shown):
    """The shown id `owner` is, or is a piece of, else None. A whole subject shown does not make each of its parts shown:
    a part in front of a must-see part stays a sibling to open (see_enclosure, explode_apart), not a shown part to fade
    with the whole (review 2026-10-17)."""
    return next((s for s in sorted(shown) if owner == s or (part_of(owner, s) and part_of(s, str(s).split('/')[0]))), None)


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
        if row['on_locked'] == 'unclaimed_state':   # takes a built subject's parts apart for a moment, back after it
            said = [i for i in lock.get('state') or [] if _same(target, i)]
            if said:
                return f"{cheat['kind']} takes {target} apart, whose state or place the shot's lines speak of ({said[0]})"
            continue
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
    key = {'unclaimed_pose': 'pose', 'unclaimed_state': 'state'}.get(row['on_locked'], 'ids')
    for ident in names:
        if key == 'state':   # a claimed part, or a piece of one - not every object of its subject (each answers to it too)
            said = [i for i in lock.get(key) or [] if any(x == y for x in spellings(ident) for y in spellings(i)) or part_of(ident, i)]
        else:
            said = [i for i in lock.get(key) or [] if _same(ident, i)]
        if said:
            return f"{kind} would move {ident}, which the shot's lines speak of ({said[0]})"
    return None


def part_shot(fr, lock=None):
    """The moment explains parts: it opens its subject (its show is not the outside), or what it must show is a part of
    something (`<subject>/<part>`), or the shot's lines speak of a part. A moment that shows a thing whole, as it is, is
    never opened by a cheat (see_enclosure, explode_apart: part_shot)."""
    from_show = (fr.get('show') or {}).get('how') not in (None, 'exterior')
    parts = any('/' in str(ref) for ref in (fr.get('must_see') or []) + list((fr.get('show') or {}).get('parts') or []))
    return from_show or parts or bool((lock or {}).get('state'))


def refused(cheat, fr, frames, lock=None, subject=None):
    """Why a cheat that opens a subject (OPENS) may not be offered or kept at the moment `fr` of a shot whose moments are
    `frames`, else None: a part_shot row at a moment that explains no part; the framed subject (`subject`, the camera's
    target) as its target; a taking
    apart that moves what any moment shows, or at the shot's first moment when the shot runs on from the one before
    (lock['held_start']: the cut would show it jump apart). Ids only; the share of a subject's objects is the scene's
    (cheat_scene: WHOLE_SUBJECT_SHARE)."""
    row = CHEATS[cheat['kind']]
    if row['applies'] not in OPENS:
        return None
    if row.get('part_shot') and not part_shot(fr, lock):
        return f"{cheat['kind']} opens a subject only at a moment that explains its parts (this one shows it as it is)"
    targets = targets_of(cheat)
    whole = [t for t in targets if subject and any(x == y for x in spellings(t) for y in spellings(subject))]
    if whole:
        return f"{cheat['kind']} on {whole[0]}, the framed subject itself: a subject is opened, never taken away"
    if row['applies'] == 'explode':
        shown = {str(r) for f in frames for r in (f.get('must_see') or []) + list((f.get('show') or {}).get('parts') or [])}
        # what it takes apart is, or carries, a part a moment shows (the subject shown whole is not carried by its part)
        carried = [t for t in targets for s in shown if any(x == y for x in spellings(t) for y in spellings(s)) or part_of(s, t)]
        if carried:
            return f"{cheat['kind']} would take {carried[0]} apart, which a moment of the shot shows"
        if (lock or {}).get('held_start') and fr['at'] <= min(f['at'] for f in frames):
            return f"{cheat['kind']} at the first moment of a shot that runs on from the one before: the cut would show it jump apart"
        if (lock or {}).get('held_end') and fr['at'] >= max(f['at'] for f in frames):
            return f"{cheat['kind']} at the last moment of a shot the next one runs on from: the cut would show it snap back"
        seconds = (lock or {}).get('seconds')
        if seconds:
            ats = sorted({f['at'] for f in frames})
            i = ats.index(fr['at']) if fr['at'] in ats else None
            gaps = [abs(ats[j] - fr['at']) * seconds for j in (i - 1, i + 1) if i is not None and 0 <= j < len(ats)]
            short = [g for g in gaps if g < APART_MIN_S]
            if short:
                return (f"{cheat['kind']} with a moment {min(short):.2f} s beside it: taking it apart and back would take less "
                        f"than {APART_MIN_S} s each way, a jump")
    return None


def apart_sign(lean, value):
    """Which way along the subject's long axis a part is taken apart: away from what the moment must show (`lean`, the
    part's centre past the must-see middle along the axis) for a positive value, the other way for a negative one -
    frames check tries the other way when the first takes the part into its support (SUBJECT_SINKS). +1.0 or -1.0."""
    return (1.0 if lean >= 0 else -1.0) * (1.0 if value >= 0 else -1.0)


def other_way(cheats):
    """The same cheats with every explode_apart taken apart the other way (apart_sign), or None when there is none."""
    if not any(CHEATS.get(c.get('kind'), {}).get('applies') == 'explode' for c in cheats):
        return None
    return [{**c, 'value': -c['value']} if CHEATS.get(c.get('kind'), {}).get('applies') == 'explode' else c for c in cheats]


DROP_LOCKED_WORDS = '내레이션이 설명하는 부품 — B 불가'   # the proposals sheet's words for a part no moment may drop (SHOT_EXPLAINS_UNSHOWN)


def drop_locked(part, fr, frames, lock=None):
    """Why a must-see part may not be dropped from one moment (option B of a blocked moment: frames proposals), else
    None: a line of the shot explains a thing it belongs to (lock things: a part, a feature's parts, a whole subject, an element built
    of it) and no other moment shows any part of that thing - dropping it here leaves the lines explaining something the
    shot never shows (decisions.explain_lines: SHOT_EXPLAINS_UNSHOWN). Another moment showing it keeps B open."""
    elsewhere = {str(p) for f in frames if f is not fr and f.get('at') != fr.get('at')
                 for p in list(f.get('must_see') or []) + list((f.get('show') or {}).get('parts') or [])}
    for thing in (lock or {}).get('things') or []:
        ids = [str(i) for i in thing.get('ids') or []]   # a whole subject too: part_of reads its parts (2026-10-17 review)
        if not any(_same(part, i) or part_of(part, i) for i in ids):
            continue
        if not any(_same(p, i) or part_of(p, i) for p in elsewhere for i in ids):
            return f"SHOT_EXPLAINS_UNSHOWN: the lines explain {thing['id']} and no other moment shows it ({DROP_LOCKED_WORDS})"
    return None
