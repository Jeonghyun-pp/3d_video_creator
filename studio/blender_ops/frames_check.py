"""frames check, the Blender side (host: studio/frames_check.py): open a shot's authored checkpoint, run the chain up to
the camera (generate.prepare -> the declared cheats -> the support check -> the openings and their cuts) and solve
every frame target as the build would (camera_moves.solve_frame) - nothing saved, nothing rendered.

Each moment is first solved as it stands, by its picture's intent (camera_moves._solve_intent): as_intended (it keeps
its size class, side of level and lens class, nothing said), moved (what it gave up is listed - core.kept_intent), or
failed. A failed or moved moment is searched on the probe (search_intent) with the cheats of cheat_table.CHEATS: each
allowed cheat applied in this session and the moment probed again; of those the probes pass, the cheapest few are
solved in full, then pairs; for a moved moment a cheat counts only when it brings the intent back. The cheats on what
the shot's lines speak of (job['locked'], by ids and by every object a moved thing carries) are tried last, only to
tell "only a locked cheat solves it" from "nothing does" - FRAME_NEEDS_REDRAW is said only after the whole search.
The chosen cheats are then applied together, as a build applies them (keyed, held over the shot), and every moment is
solved again: a moment may give up nothing more with every cheat applied than it gave up alone (verify).

What a cheat may be offered to comes from the moment itself: from where the picture's camera would stand
(camera_moves_core.frame_seed), what stands in front of the framed subject and of each part it must show (by owner:
camera_frame._owner) - the subject's own parts beside it (see_neighbours), a part the moment shows that stands in front
of another it shows (see_shown), at a moment that explains parts what wraps round a part (see_enclosure) and, dearest,
those taken apart for the moment (explode_apart), other things (context_*) but never what the subject stands on (a
support: moving it takes the ground from under it), the atmosphere volumes nearest the camera, the subject itself
(subject_turn). A cheat that opens a subject is offered only within cheat_table.refused's bounds.
Time: job['check'] moment_s / shot_s only stop a run ('stopped', never a verdict). frames_check.json is written after
every moment.
Run: blender -b <authored.blend> --python frames_check.py -- <job.json>
"""
from __future__ import annotations

import copy
import itertools
import json
import math
from pathlib import Path
import sys
import time

import bpy
from mathutils import Vector

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))
import camera_frame  # noqa: E402
import camera_moves  # noqa: E402
import camera_moves_core as core  # noqa: E402
import cheat_scene  # noqa: E402
from cheat_table import CHEATS, drop_locked, held_by, in_rounds, ordered, other_way, refused, targets_of, touches_locked  # noqa: E402
from scene_index import resolve_ref  # noqa: E402
from scene_roles import counts  # noqa: E402

MAX_PAIRS = 6        # pairs of the cheapest candidate of two rows, cheapest pairs first
INTENT_FULL = 2      # intent search: of the cheats the probes pass, this many (cheapest, best scored) are solved in full
MAX_LOCKED = 6       # cheats on locked things, tried only to say whether one would have solved it
MAX_OWNERS = 2       # things in front offered a context or atmosphere cheat, most in front first
SEED_SAMPLES = 240   # points per framed ref / must-see part looked at from the picture's camera
WRAP_POINTS = 12     # of them, the points each thing in front is tested for wrapping round the part (an enclosure)


def _code(error):
    return getattr(error, 'report', {}).get('code') if getattr(error, 'report', None) else camera_moves._code(str(error), 'FRAME_UNSOLVED')


def _solve(job, move, fr, ctx):
    """(row, None) or (None, {code, message, report}). The row says how far the subject's long axis is from the line
    of sight (sight_deg: under 45 it shows its end and its lean means nothing, as frame_targets.END_ON_DEG)."""
    try:
        key, row, warnings = camera_moves.solve_frame(job, move, fr, {**ctx, 'cache': {}})
        sight = None
        if key.get('axis') is not None:
            look = Vector(key['aim']) - Vector(key['eye'])
            if look.length > 1e-9:
                sight = round(math.degrees(math.acos(min(1.0, abs(look.normalized().dot(Vector(key['axis']).normalized()))))), 2)
        return {**_summary(row), 'sight_deg': sight, 'warnings': warnings}, None
    except Exception as error:   # noqa: BLE001 - any failure of the solve is this moment's result, with its code
        return None, {'code': _code(error) if isinstance(error, ValueError) else f'FRAME_CHECK_ERROR ({type(error).__name__})',
                      'message': str(error)[:900], 'report': getattr(error, 'report', None)}


def _summary(row):
    keep = ('at', 'frame', 'lens_mm', 'view', 'picture_view', 'offset_from_picture', 'must_see_px', 'blocked_share', 'tilt_deg',
            'mode', 'engaged', 'position', 'aim', 'moved_because', 'cheated', 'spans_m', 'azimuth_from',
            'picture_tilt_deg', 'lens_rejected', 'lenses_at_picture', 'departed', 'departures', 'intent', 'roll_deg', 'placement',
            'subject_at', 'subject_pose')
    out = {k: row[k] for k in keep if k in row}
    refused = [{'view': r['view'], 'lens': r.get('lens'), 'owners': r['owners']} for r in row.get('rejected') or [] if r.get('owners')]
    if refused:   # the views refused and what stood in front at each (an intent solve): what a cheat may clear
        out['refused'] = refused
    return out


def _owner_objects(owner):
    return camera_frame.own_objects(owner) if owner else []   # its coats too: an owner is a part with what lies on it


def _in_front(job, move, fr, ctx):
    """{'context': {owner: share}, 'siblings': {owner: share}, 'shown': {owner: share}, 'enclosing': [owners], 'eye': [x, y,
    z]} - what stands in front of the framed subject and of each must-see part, seen from where the picture's camera
    would stand: the subject's own parts (siblings), of them those the moment shows itself (shown) and those that wrap
    round a must-see part (enclosing), other things (context)."""
    frame = round(fr['at'] * (job['shot']['duration_frames'] - 1))
    refs, target = ctx['refs'], ctx['target']
    geo = camera_moves.resolve({'target': target, **({'parts': refs} if ctx['p'].get('parts') else {})}, frame=frame + 1)
    box = core._ref(geo, ctx['framed'], 'box')
    lens = fr.get('lens_mm') or move.get('lens_mm', 24.0)
    eye = Vector(core.frame_seed(box, fr, lens, ctx['aspect'])[0])
    scene = bpy.context.scene
    scene.frame_set(frame + 1)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    subjects = {str(r).split('/')[0] for r in [target, *refs, *(fr.get('must_see') or [])]}
    context, siblings = {}, {}

    def look(points, mine, into):
        for p in points:
            for obj in camera_frame.blockers(scene, depsgraph, eye, Vector(p), mine):
                owner = camera_frame._owner(obj)
                into[owner] = into.get(owner, 0) + 1.0 / max(1, len(points))
    try:
        look(camera_frame.sample(refs, depsgraph, count=SEED_SAMPLES), ctx['mine'], context)
    except ValueError:
        pass
    shown = set(fr.get('must_see') or []) | set((fr.get('show') or {}).get('parts') or [])
    wrapping = {}   # owner -> [points it is in front of, of them those it wraps]
    for part in fr.get('must_see') or []:
        own = {o.name for o in camera_frame.own_objects(part)}
        try:
            points = camera_frame.sample([part], depsgraph, count=SEED_SAMPLES)
        except ValueError:
            continue
        look(points, own, siblings)
        # what wraps round the part (camera_frame._wraps, as diagnose_part judges a shell) is an enclosure: opening it
        # is the shot list's show (section, x-ray ...), decided before the picture - never a cheat's
        for p in points[::max(1, len(points) // WRAP_POINTS)]:
            target = Vector(p)
            far = 2.0 * (target - eye).length
            for owner in {camera_frame._owner(o) for o in camera_frame.blockers(scene, depsgraph, eye, target, own)}:
                row = wrapping.setdefault(owner, [0, 0])
                row[0] += 1
                row[1] += camera_frame._wraps(scene, depsgraph, target, owner, far, own)
    enclosing = sorted(o for o, (n, wraps) in wrapping.items() if n and wraps >= 0.5 * n and o.split('/')[0] in subjects
                       and held_by(o, shown) is None)
    shown_front = {}
    for owner in list(siblings):   # what is in front of a must-see part and not the subject's own is context
        held = held_by(owner, shown)   # a shown part, or a piece of one
        if owner.split('/')[0] not in subjects or held:
            share = siblings.pop(owner)
            if owner.split('/')[0] not in subjects:
                context[owner] = max(context.get(owner, 0.0), share)
            else:   # a part the moment shows itself, in front of another it shows: the whole shown part is faded, so the
                shown_front[held] = max(shown_front.get(held, 0.0), share)   # build measures it as one see-through part
    for owner in enclosing:
        siblings.pop(owner, None)
    scene.frame_set(1)
    return {'context': context, 'siblings': siblings, 'shown': shown_front, 'enclosing': enclosing, 'eye': list(eye), 'box': box}


def _is_support(owner):
    return any(o.type == 'MESH' and counts(o, 'floor') for o in _owner_objects(owner))


def _atmosphere(eye, limit):
    """The atmosphere volumes (by owner) nearest the picture's camera, nearest first."""
    rows = {}
    for obj in camera_frame.atmosphere_objects():
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        lo = [min(c[i] for c in corners) for i in range(3)]
        hi = [max(c[i] for c in corners) for i in range(3)]
        gap = math.dist(eye, [min(max(eye[i], lo[i]), hi[i]) for i in range(3)])
        owner = camera_frame._owner(obj)
        rows[owner] = min(rows.get(owner, math.inf), gap)
    return sorted(rows, key=lambda o: rows[o])[:limit]


def candidates(job, move, fr, ctx, failure, solved=None):
    """Every cheat worth a try for a failed moment (or a moved one: `solved`, its row), cheapest first:
    [{kind, target, value}]. A lens cheat is offered only at the lenses the solve did not try: those its failure report
    tried: the lenses refused at the picture's own view (lenses_at_picture: of the failure, or of the moved row - not
    the lenses of the view it moved to), the solved row's own when it solved there, and always the picture's lens."""
    out = []
    seen = _in_front(job, move, fr, ctx)
    target = ctx['target']
    frames = move.get('frames') or [fr]
    shown_ids = {str(r) for f in frames for r in (f.get('must_see') or []) + list((f.get('show') or {}).get('parts') or [])}
    is_shown = lambda o: held_by(o, shown_ids) is not None  # noqa: E731 - a shown part or a piece of one
    report = (failure or {}).get('report') or {}
    blocking = list(report.get('blocking') or [])
    first = fr.get('lens_mm') or move.get('lens_mm', 24.0)
    tried = {first, *(report.get('lenses_at_picture') or (solved or {}).get('lenses_at_picture') or [])}
    if solved and solved.get('lens_mm') and not any((solved.get('offset_from_picture') or {}).values()):
        tried.add(solved['lens_mm'])   # solved at the picture's view: its lens is no cheat
    for kind in ordered():
        row = CHEATS[kind]
        if row['targets'] == 'camera' and row['applies'] == 'lens':
            # wider first (the picture's punch), then longer
            lenses = sorted((mm for mm in row['steps'] if mm not in tried), key=lambda mm: (mm > first, abs(math.log(mm / first))))
            out += [{'kind': kind, 'target': None, 'value': mm} for mm in lenses]
        elif row['targets'] == 'camera' and row['applies'] == 'view':
            continue   # the intent search already goes round the subject within its height band: no view cheat
        elif row['targets'] == 'siblings':
            front = sorted((set(seen['siblings']) | {b for b in blocking if '/' in b and b.split('/')[0] == str(target).split('/')[0]})
                           - set(seen['enclosing']))
            if front:
                out += [{'kind': kind, 'target': front, 'value': value} for value in row['steps']]
        elif row['targets'] == 'shown' and seen['shown']:
            out += [{'kind': kind, 'target': sorted(seen['shown']), 'value': value} for value in row['steps']]
        elif row['targets'] == 'enclosure':
            wraps = sorted(set(seen['enclosing']) | {b for d in ((report.get('diagnosis') or {}).get('parts') or {}).values()
                                                     if d.get('enclosed') for b in d.get('blocked_by') or []
                                                     if '/' in b and b.split('/')[0] == str(target).split('/')[0]})
            wraps = [w for w in wraps if not is_shown(w)]
            if wraps:   # with the shown parts that stand in front of another shown part: faded too (show_core.SHOWN_THROUGH)
                out += [{'kind': kind, 'target': sorted(set(wraps) | set(seen['shown'])), 'value': value} for value in row['steps']]
        elif row['targets'] == 'apart':
            # what stands in front of or round the part, one taken apart at a time (the fewest moved), then all of them
            front = sorted(o for o in set(seen['siblings']) | set(seen['enclosing']) if not is_shown(o))
            groups = [[f] for f in front] + ([front] if len(front) > 1 else [])
            out += [{'kind': kind, 'target': g, 'value': value} for value in row['steps'] for g in groups]
        elif row['targets'] == 'atmosphere':
            for owner in _atmosphere(seen['eye'], MAX_OWNERS):
                out += [{'kind': kind, 'target': owner, 'value': value} for value in row['steps']]
        elif row['targets'] == 'context':
            things = {**{b: 1.0 for b in blocking if '/' not in b or b.split('/')[0] != str(target).split('/')[0]}, **seen['context']}
            # what it stands on is never moved or scaled, but may be made see-through (a cutaway of the ground); what a
            # moment shows is never faded by a context cheat
            fades = row['applies'] == 'see_through'
            things = [o for o in sorted(things, key=lambda o: -things[o]) if (not _is_support(o) if not fades else not is_shown(o))
                      and not any(o2.name in ctx['mine'] for o2 in _owner_objects(o))][:MAX_OWNERS]
            for owner in things:
                for value in row['steps']:
                    out.append({'kind': kind, 'target': owner, 'value': _context_value(row, value, owner, seen) if row['applies'] == 'move' else value})
        elif row['targets'] == 'subject':
            out += [{'kind': kind, 'target': target, 'value': value} for value in row['steps']]
    lock = job.get('locked') or {}
    return [c for c in out if c['value'] is not None and not refused(c, fr, frames, lock, subject=target)]


def _context_value(row, step, owner, seen):
    """A move step for a thing in front: sideways across the line of sight (to the side it already leans to, a negative
    share the other way), or a turn about its upright."""
    found = resolve_ref(owner)
    if not found or not found['box']:
        return None
    lo, hi = found['box']
    size = max(hi[i] - lo[i] for i in range(3))
    if step.get('turn_deg'):
        return {'move_m': [0.0, 0.0, 0.0], 'turn_deg': float(step['turn_deg']), 'size_m': round(size, 4)}
    eye, box = Vector(seen['eye']), seen['box']
    focus = Vector([(box[0][i] + box[1][i]) / 2 for i in range(3)])
    sight = focus - eye
    side = Vector((-sight.y, sight.x, 0.0))
    if side.length < 1e-6:
        side = Vector((1.0, 0.0, 0.0))
    side.normalize()
    middle = Vector([(lo[i] + hi[i]) / 2 for i in range(3)])
    lean = 1.0 if (middle - eye).dot(side) >= 0 else -1.0
    move = side * (lean * step['size_share'] * size)
    return {'move_m': [round(v, 4) for v in move], 'turn_deg': 0.0, 'size_m': round(size, 4)}


def _allowance(job, cheats):
    """How deep what `cheats` move stood in a support before them (generate.sink_allowance), kept per object for the
    session: the scene a trial starts from is the same every time (recorded cheats applied, the trial's undone)."""
    import generate
    held = job.setdefault('_sink_allowance', {})
    new = [c for c in cheats if CHEATS[c['kind']]['applies'] in cheat_scene.MOVES
           and not cheat_scene.moved_by([c]) <= set(held)]
    if new:
        held.update({k: v for k, v in generate.sink_allowance(job, new).items() if k not in held})
    return held


def _sunk(job, cheats):
    """SUBJECT_SINKS when a cheat that moves, scales or turns a thing puts it into its support deeper than it stood
    before: the failure, else None (generate._sinks on what these cheats move alone - a subject that sank before them
    is the scene's, said in pre_camera, never a trial's)."""
    moved = cheat_scene.moved_by(cheats)
    if not moved:
        return None
    import generate
    # a part taken apart is judged among its whole subject (generate._sinks among): its support is the subject's
    among = {o.name for c in cheats if CHEATS[c['kind']]['applies'] == 'explode' for t in targets_of(c)
             for o in ((resolve_ref(str(t).split('/')[0]) or {}).get('objects') or [])}
    found = generate._sinks(job, job.get('_sink_allowance'), only=moved, among=among)
    if found and found['gate_failures']:
        return {'code': 'SUBJECT_SINKS', 'message': found['gate_failures'][0]['why']}
    return None


def _attempt(job, move, fr, ctx, cheats, enforce=True, seeds=None, turned=False):
    """Solve the moment again with the cheats applied in this session; the scene is put back afterwards. A cheat that
    sinks a thing into its support is refused before the solve (SUBJECT_SINKS) - a part taken apart into it is tried
    once the other way (cheat_table.other_way), and the attempt carries the way that was tried last."""
    trial_fr = copy.deepcopy(fr)
    undo, started = [], time.monotonic()
    try:
        _allowance(job, cheats)
        for cheat in cheats:
            undo.append(cheat_scene.trial(job, trial_fr, cheat, enforce=enforce))
        sunk = _sunk(job, cheats)
        row, failure = (None, sunk) if sunk else _solve(job, move, trial_fr, {**ctx, **({'seeds': seeds} if seeds else {})})
    except ValueError as error:   # the cheat itself could not be applied (out of range, conflict, missing, locked)
        row, failure = None, {'code': camera_moves._code(str(error), 'CHEAT_FAILED'), 'message': str(error)[:400]}
    finally:
        for step in reversed(undo):
            step()
    if row is None and failure['code'] == 'SUBJECT_SINKS' and not turned and other_way(cheats):
        return _attempt(job, move, fr, ctx, other_way(cheats), enforce, seeds, turned=True)
    return {'cheats': cheats, 'ok': row is not None, 'row': row, 'code': None if row else failure['code'],
            'message': None if row else failure['message'][:300], 'seconds': round(time.monotonic() - started, 2)}


class Stopped(Exception):
    """The run's time (moment_s, shot_s) is spent: an intent search stops - said 'stopped', never a verdict."""


def _probe_attempt(job, move, fr, ctx, cheats, enforce=True, views=None):
    """An intent moment probed again with the cheats applied in this session (camera_moves._solve_intent probe_only:
    the placed views, refused and probed, no full measure); the scene is put back afterwards. {cheats, ok, score, best,
    passed, code, seconds}: ok when some placed view passes the probe with them."""
    trial_fr = copy.deepcopy(fr)
    undo, started = [], time.monotonic()
    best, passed, code, passing = None, 0, None, []
    try:   # no sinking check here: it reads every frame of the shot; the full attempt (_attempt) makes it
        for cheat in cheats:
            undo.append(cheat_scene.trial(job, trial_fr, cheat, enforce=enforce))
        _key, found, _w = camera_moves.solve_frame(job, move, trial_fr, {**ctx, 'cache': {}, 'probe_only': True,
                                                                         **({'probe_views': views} if views is not None else {})})
        best, passed, passing = found.get('best'), found.get('passed', 0), found.get('passing') or []
        code = None if best else 'FRAME_UNSOLVED'
    except ValueError as error:
        code = camera_moves._code(str(error), 'CHEAT_FAILED')
    finally:
        for step in reversed(undo):
            step()
    return {'cheats': cheats, 'ok': best is not None, 'score': best['score'] if best else None, 'best': best, 'passed': passed,
            'passing': passing, 'code': code, 'seconds': round(time.monotonic() - started, 2)}


def _split(job, move, fr, ctx, failure, solved):
    """(allowed, barred) cheats for a moment: candidates() split by what is locked (touches_locked, tree_lock)."""
    lock = job.get('locked') or {}
    allowed, barred = [], []
    for c in candidates(job, move, fr, ctx, failure, solved):
        why = touches_locked(c, lock)
        if not why and CHEATS[c['kind']]['applies'] in cheat_scene.MOVES:
            why = cheat_scene.tree_lock(c['kind'], cheat_scene._roots(c['target']), lock)
        (barred if why else allowed).append({**c, **({'why_locked': why} if why else {})})
    return allowed, barred


def search_intent(job, move, fr, ctx, failure, abort_at, goal=None, solved=None):
    """The cheats for an intent moment, scored on the probe first: every allowed cheat applied in this session and the
    moment probed again (_probe_attempt: the placed views refused and probed, no full measure); of those the probes pass,
    the INTENT_FULL cheapest (cheat cost, then score) solved in full, as the build would; then pairs of each kind's
    cheapest, likewise; then the locked ones, only to tell 'only a locked cheat solves it' from 'nothing does'. The
    intent search itself goes round the subject and down the wider lenses, so a view cheat is no cheat here and a lens
    cheat only a longer lens than the picture's. The verdict is the whole search's - cheat, or redraw once every cheat
    and pair was probed - never the clock's: past abort_at the run stops (Stopped).
    -> {status: cheat | redraw, solved_by, attempts, probes, locked_solves, complete}."""
    goal = goal or (lambda row: True)
    allowed, barred = _split(job, move, fr, ctx, failure, solved)
    first = fr.get('lens_mm') or move.get('lens_mm', 24.0)
    covered = [c for c in allowed if CHEATS[c['kind']]['applies'] == 'lens' and float(c['value']) <= first]
    allowed = [c for c in allowed if c not in covered]
    attempts, probes = [], []
    # the views the moment was refused at as it stands, and what stood in front at each: a cheat on a thing (a
    # neighbour made see-through, a thing moved or scaled) is probed only where that thing stood in front - elsewhere it
    # changes nothing; a cheat on the camera, the air or the subject itself is probed everywhere
    refused = ((failure or {}).get('report') or {}).get('rejected') or (solved or {}).get('refused') or []

    def views_for(cs):
        if not refused:   # nothing known of what stood where (a hidden part's report, a solve that moved): every view
            return None
        out = set()
        for c in cs:   # a part in front at some views (a neighbour, a shown part) is probed there; a wrap or a part taken
            if CHEATS[c['kind']]['targets'] not in ('context', 'siblings', 'shown'):   # apart may hide it from every side
                return None
            things = c['target'] if isinstance(c['target'], list) else [c['target']]
            out |= {tuple(r['view']) for r in refused for o in r.get('owners') or [] for t in things
                    if o == t or o.startswith(f'{t}/') or t.startswith(f'{o}/')}
        return out

    def clean(cs):
        return [{k: v for k, v in c.items() if k != 'why_locked'} for c in cs]

    def probe(cs, enforce=True):
        if time.monotonic() > abort_at:
            raise Stopped()
        views = views_for(cs)
        if views is not None and not views:   # it stood in front of no view the moment was refused at
            got = {'cheats': clean(cs), 'ok': False, 'score': None, 'code': 'NOT_IN_FRONT', 'seconds': 0.0, 'passing': []}
        else:
            got = _probe_attempt(job, move, fr, ctx, clean(cs), enforce, views)
        probes.append({k: got[k] for k in ('cheats', 'ok', 'score', 'code', 'seconds')})
        return got

    def full(cs, enforce=True, seeds=None):
        if time.monotonic() > abort_at:
            raise Stopped()
        # seeded with the poses the cheat's probe passed: judged first, the whole intent search only when none holds
        got = _attempt(job, move, fr, ctx, clean(cs), enforce, seeds)
        attempts.append(got)
        if got['ok'] and not goal(got['row']):
            got['ok'], got['short_of_goal'] = False, core.kept_intent(got['row'])
        return got if got['ok'] else None

    def best_of(scored):   # the cheapest, then the one that touches fewer things, then the best scored
        ok = sorted((p for p in scored if p['ok']), key=lambda p: (sum(CHEATS[c['kind']]['cost'] for c in p['cheats']),
                                                                    sum(len(targets_of(c)) for c in p['cheats']), p['score']))
        for p in ok[:INTENT_FULL]:
            found = full(p['cheats'], seeds=p.get('passing'))
            if found:
                return found
        return None
    found = best_of([probe([c]) for c in in_rounds(allowed)])
    if not found:
        cheapest = {}
        for c in allowed:
            cheapest.setdefault(c['kind'], c)
        pairs = sorted(itertools.combinations(cheapest.values(), 2),
                       key=lambda p: CHEATS[p[0]['kind']]['cost'] + CHEATS[p[1]['kind']]['cost'])[:MAX_PAIRS]
        found = best_of([probe(list(pair)) for pair in pairs])
    out = {'attempts': attempts, 'probes': probes, 'complete': True,
           **({'covered_by_intent': [c['value'] for c in covered]} if covered else {})}
    if found:
        return {**out, 'status': 'cheat', 'solved_by': found}
    locked_solves, unfinished = [], False
    try:   # only to say why: the redraw verdict stands whether or not this ends
        for c in barred[:MAX_LOCKED]:
            tried = probe([c], enforce=False)
            if tried['ok'] and full([c], enforce=False, seeds=tried.get('passing')):
                locked_solves.append({k: c[k] for k in ('kind', 'target', 'value', 'why_locked')})
                break
    except Stopped:
        unfinished = True
    return {**out, 'status': 'redraw', 'locked_solves': locked_solves, **({'locked_unfinished': True} if unfinished else {})}


def _drop(fr, part):
    """The moment without `part` among what it must show: its must-see, its show's parts and its intent's copies."""
    fr['must_see'] = [m for m in fr.get('must_see') or [] if m != part]
    if fr.get('show'):
        fr['show']['parts'] = [m for m in fr['show'].get('parts') or [] if m != part]
    if fr.get('intent'):
        fr['intent']['must_see'] = list(fr['must_see'])
        if (fr['intent'].get('show') or {}).get('parts') is not None:
            fr['intent']['show']['parts'] = [m for m in fr['intent']['show']['parts'] if m != part]


def drop_options(job, move, fr, ctx, failure, abort_at=None):
    """Option B of a blocked moment: each must-see part it could not show (its failure's parts, else every one) dropped
    from this moment alone (_drop, in memory - the shot is not written) and the moment solved again: {part, status
    as_intended | moved | fail | locked | unchecked, must_see_px of the rest, dropped_px (how much of the part still shows
    at that pose)}. A part locked (cheat_table.drop_locked: the lines explain it and no other moment shows it) is not
    solved - it may not be proposed. One part at a time: a moment that needs two dropped is said by each failing alone,
    never by a combination. Past `abort_at` the rest are unchecked."""
    report = (failure or {}).get('report') or {}
    must = list(fr.get('must_see') or [])
    named = list(report.get('parts') or []) + list(((report.get('diagnosis') or {}).get('parts') or {}))
    blocked = [p for p in dict.fromkeys(named) if p in must] or must
    frames = move.get('frames') or [fr]
    size = job.get('output_size') or (1080, 1920)
    keys = [k['id'] for k in (job.get('probe') or {}).get('key_parts') or [] if k.get('id')]
    out = []
    for part in blocked:
        trial = copy.deepcopy(fr)
        _drop(trial, part)
        locked = drop_locked(part, fr, frames, job.get('locked'))
        option = {'part': part, 'must_see': trial['must_see'], **({'locked': locked} if locked else {})}
        if locked:
            out.append({**option, 'status': 'locked', 'seconds': 0.0})
            continue
        if abort_at is not None and time.monotonic() > abort_at:
            out.append({**option, 'status': 'unchecked', 'message': "the check's time was spent first", 'seconds': 0.0})
            continue
        started = time.monotonic()
        row, failed = _solve(job, move, trial, {**ctx, 'abort_at': abort_at})
        if row is None:
            option.update(status='unchecked' if failed['code'] == 'FRAME_CHECK_STOPPED' else 'fail',
                          code=failed['code'], message=failed['message'][:300])
        else:
            lost = core.kept_intent(row)
            frame = round(fr['at'] * (job['shot']['duration_frames'] - 1))
            dropped = camera_frame.parts_px([part], row['position'], row['aim'], row['lens_mm'], size, frame + 1,
                                            roll_deg=row.get('roll_deg') or 0.0, subjects=[ctx['target']], keys=keys)
            bpy.context.scene.frame_set(1)
            option.update(status='moved' if lost else 'as_intended', lost=lost, row=row, dropped_px=dropped)
        option['seconds'] = round(time.monotonic() - started, 1)
        out.append(option)
    return out


def proposals(job, move, fr, ctx, failure, abort_at=None):
    """What the user may choose for a moment no cheat solves (frames proposals): {a: the free-angle poses a picture
    redrawn from there would be shot at (camera_moves.wide_search - proposed, never taken), b: the must-see parts dropped
    one at a time (drop_options)}. The moment stays redraw until the user picks. Bounded by the moment's time
    (`abort_at`): the intent search that already failed is not run again (ctx wide_only), and what the time leaves
    unchecked is said, never decided."""
    started = time.monotonic()
    _row, again = _solve(job, move, fr, {**ctx, 'wide': True, 'wide_only': True, 'abort_at': abort_at})
    wide = ((again or {}).get('report') or {}).get('wide') or {}
    return {'a': wide.get('poses') or [], 'a_search': {k: v for k, v in wide.items() if k != 'poses'},
            'b': drop_options(job, move, fr, ctx, failure, abort_at), 'seconds': round(time.monotonic() - started, 1)}


def _moment(job, move, fr, ctx, shot_deadline, check):
    """A moment solved as it stands - as_intended when it keeps its picture's
    intent (core.kept_intent: no departure, no warning), moved with what it lost - and searched on the probe
    (search_intent) when it failed or moved. Time only stops the run: 'stopped', never a verdict."""
    t0 = time.monotonic()
    out = {'at': fr['at'], 'concept_id': (fr.get('picture') or {}).get('concept_id'), 'recorded': fr.get('cheats') or []}
    abort_at = min(shot_deadline, t0 + float(check.get('moment_s', 420)))
    if t0 > shot_deadline:
        out.update(status='stopped', as_is={'ok': None}, why="the run's time (shot_s) was spent before this moment")
        return out
    row, failure = _solve(job, move, fr, ctx)
    out['as_is'] = {'ok': row is not None, 'seconds': round(time.monotonic() - t0, 1)}
    try:
        if row is not None:
            out['row'] = row
            lost = core.kept_intent(row)
            if not lost:
                out['status'] = 'cheat_recorded' if out['recorded'] else 'as_intended'
                return out
            out.update(status='moved', moved=lost)
            try:   # it solved: a search the run's time stops leaves it moved, said
                found = search_intent(job, move, fr, ctx, None, abort_at, goal=lambda r: not core.kept_intent(r), solved=row)
            except Stopped:
                out['search'] = {'stopped': True}
                return out
            out['search'] = {k: v for k, v in found.items() if k != 'status'}
            if found['status'] == 'cheat':
                out.update(status='cheat', solved_by=found['solved_by'])
            return out
        out['as_is'].update(code=failure['code'], message=failure['message'])
        out.update(search_intent(job, move, fr, ctx, failure, abort_at))
        if out['status'] == 'redraw':   # no cheat solves it: what a redrawn picture (A) or a part dropped (B) would do
            out['proposals'] = proposals(job, move, fr, ctx, failure, abort_at)
    except Stopped:
        out.update(status='stopped', why=f"the run's time (moment_s {check.get('moment_s')}, shot_s {check.get('shot_s')}) was spent "
                                         'before the search ended - nothing is decided')
    return out


def verify(job, move, ctx, moments, shot_deadline):
    """Apply the cheats chosen for every moment together, as a build does (cheat_scene.apply: keyed, held over the
    shot), and solve every moment again: {at: {ok, code, row}}. A cheat chosen at one moment may undo another."""
    chosen = {m['at']: m['solved_by']['cheats'] for m in moments if m.get('status') == 'cheat'}
    if not chosen:
        return None
    if time.monotonic() > shot_deadline:
        return {'skipped': "the shot's time ran out before the chosen cheats were checked together"}
    trial = copy.deepcopy(job)
    frames = trial['shot']['camera']['move']['frames']
    for fr in frames:
        fr['cheats'] = list(chosen.get(fr['at']) or [])   # recorded ones are in the scene already
    try:
        _allowance(job, [c for cheats in chosen.values() for c in cheats])
        cheat_scene.apply(trial)
    except ValueError as error:
        return {'error': str(error)[:400]}
    sunk = _sunk({**trial, '_sink_allowance': job.get('_sink_allowance')}, [c for cheats in chosen.values() for c in cheats])
    if sunk:   # the cheats keyed together put a thing into its support
        return {'error': f"{sunk['code']}: {sunk['message'][:380]}"}
    out = {}
    own = {m['at']: _own_row(m) or {} for m in moments}
    for fr in sorted(frames, key=lambda f: f['at']):
        row, failure = _solve(trial, trial['shot']['camera']['move'], fr, ctx)
        lost = core.kept_intent(row) if row else None
        # a moment gives up nothing more with every cheat applied than it gave up alone (as it stood, moved, or
        # by its own cheat - a failed moment solved by turning round still turns round, 2026-10-16: it read 'undecided'),
        # judged per kind and amount (camera_moves_core.lost_more)
        more = core.lost_more(row, own.get(fr['at'], {})) if row is not None else []
        ok = row is not None and not more
        out[str(fr['at'])] = {'ok': ok, 'code': None if row else failure['code'], 'lost': lost, 'row': row,
                              **({'lost_more': more} if more else {})}
    return out


def _own_row(moment):
    """The row a moment solved with on its own: its cheat's (else, when the cheat kept none, its own as it stood or
    moved), else its own."""
    if moment.get('status') == 'cheat':
        return (moment.get('solved_by') or {}).get('row') or moment.get('row')
    return moment.get('row')


def _complete(moments):
    """Every moment came to its verdict: a stopped one did not, nor did a moved one whose search was stopped."""
    return not any(m.get('status') == 'stopped' or (m.get('search') or {}).get('stopped') for m in moments)


def run(job, write=None):
    output = Path(job['output_dir'])
    check = job.get('check') or {}
    started = time.monotonic()
    shot_deadline = started + float(check.get('shot_s', 1200))
    out = {'shot_id': job['shot_id'], 'scene': bpy.data.filepath, 'pre_camera': [], 'moments': [], 'complete': False}
    write = write or (lambda result: None)
    import generate
    try:
        generate.prepare(job, output)
        move = job['shot']['camera'].get('move') or {}
        if move.get('type') != 'frame_to_frame':
            raise ValueError(f"FRAME_CHECK_NO_PICTURES: {job['shot_id']} has no frame_to_frame move (frames apply first)")
        if any(f.get('cheats') for f in move.get('frames') or []):
            job['_sink_allowance'] = generate.sink_allowance(job)   # how deep what they move stood before them
            out['applied'] = cheat_scene.apply(job)   # what is recorded already: checked as it will be built
        sinks = generate._sinks(job, job.get('_sink_allowance'))
        if sinks and sinks['gate_failures']:
            out['pre_camera'].append({'code': 'SUBJECT_SINKS', 'text': sinks['gate_failures'][0]['why']})
        import reveal
        import show_scene
        rows = show_scene.apply(job)
        show_scene.media(job)   # the world's media see-through, as the build has them (generate.py)
        if rows:
            reveal.apply_camera_bound(job['shot'], {})
            cuts = show_scene.check_cuts(job, rows)
            out['pre_camera'] += [{'code': 'SHOW_CUT_MISSES', 'text': f['why']} for f in cuts['gate_failures']]
        ctx = camera_moves.frame_context(job, move)
    except Exception as error:   # noqa: BLE001 - the shot fails before the camera: said, with its code
        out['error'] = {'code': camera_moves._code(str(error), 'FRAME_CHECK_FAILED'), 'message': str(error)[:900]}
        out['seconds'] = round(time.monotonic() - started, 1)
        write(out)
        return out
    for fr in sorted(move['frames'], key=lambda f: f['at']):
        t0 = time.monotonic()
        try:
            moment = _moment(job, move, fr, ctx, shot_deadline, check)
        except Exception as error:   # noqa: BLE001 - one moment's failure is its own result
            moment = {'at': fr['at'], 'concept_id': (fr.get('picture') or {}).get('concept_id'), 'status': 'error', 'as_is': {'ok': None},
                      'error': f'{type(error).__name__}: {str(error)[:600]}'}
        moment['seconds'] = round(time.monotonic() - t0, 1)
        out['moments'].append(moment)
        out['seconds'] = round(time.monotonic() - started, 1)
        write(out)
    try:
        out['verify'] = verify(job, move, ctx, out['moments'], shot_deadline)
    except Exception as error:   # noqa: BLE001
        out['verify'] = {'error': f'{type(error).__name__}: {str(error)[:600]}'}
    out['complete'] = _complete(out['moments'])
    out['seconds'] = round(time.monotonic() - started, 1)
    write(out)
    return out


if __name__ == '__main__' and '--' in sys.argv:
    job_path = Path(sys.argv[sys.argv.index('--') + 1])
    JOB = json.loads(job_path.read_text())
    import ids_core   # world element -> the scene ids that build it (ids_core.linked), as in the build
    ids_core.LINKS = dict(JOB.get('element_links') or {})
    target = Path(JOB['output_dir']) / 'frames_check.json'

    def save(result):
        part = target.with_suffix('.part')
        part.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str))
        part.replace(target)
    run(JOB, save)
