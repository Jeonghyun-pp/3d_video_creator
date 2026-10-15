"""frames check, the Blender side (host: studio/frames_check.py): open a shot's authored checkpoint, run the chain up to
the camera (generate.prepare -> the declared cheats -> the support check -> the openings and their cuts) and solve
every frame target as the build would (camera_moves.solve_frame) - nothing saved, nothing rendered.

Each moment is first solved as it stands: as drawn (the picture's own view and lens, no warning), moved (the solve's
own view search or lens ladder went elsewhere, or it warned: the lean not reached, the camera in a volume), or failed.
A failed or moved moment is tried again in the same session with the cheats of cheat_table.CHEATS - every candidate
of every row, in rounds (cheat_table.in_rounds: each kind's first, cheapest kind first, then each kind's second ...), then pairs of the cheapest of each row; for a moved moment a cheat counts only when it
brings the picture back (its view, its lens or wider, no warning: never settle toward a duller view). A camera cheat
counts only for what it did: a lens cheat when the solve kept that lens, a view cheat when it stayed at that view (else
the cheat is re-aimed at what the solve reached and tried again). The cheats on what the shot's lines speak of
(job['locked'], by ids and by every object a moved thing carries) are tried last, only to tell "only a locked cheat
solves it" from "nothing does" - FRAME_NEEDS_REDRAW is said only after the whole search; a search the time cut short
is undecided. The chosen cheats are then applied together, as a build applies them (keyed, held over the shot), and
every moment is solved again: a cheat one moment needs may undo another (verify).

What a cheat may be offered to comes from the moment itself: from where the picture's camera would stand
(camera_moves_core.frame_seed), what stands in front of the framed subject and of each part it must show (by owner:
camera_frame._owner) - the subject's own parts beside it (see_neighbours; one that wraps round it is an enclosure, the
shot list's opening, never a cheat), other things (context_*) but never what the subject stands on (a support: moving
it takes the ground from under it), the atmosphere volumes nearest the camera, the subject itself (subject_turn).
Time: job['check'] moment_s / shot_s; a try is skipped when less time is left than the moment's own solve took, a
moment past the shot's time is not solved (undecided). frames_check.json is written after every moment.
A move solved by its pictures' intent (params.solve 'intent'; job['check'] solve sets it for the check) is searched on
the probe (search_intent): each cheat scored by re-probing the moment with it, the best few solved in full; the verdict
is the search's, as_intended / moved by what the moment kept of its intent (camera_moves_core.kept_intent), and the
time only stops a run (stopped).
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
from cheat_table import CHEATS, in_rounds, ordered, touches_locked  # noqa: E402
from scene_index import resolve_ref  # noqa: E402
from scene_roles import counts  # noqa: E402

MAX_PAIRS = 6        # pairs of the cheapest candidate of two rows, cheapest pairs first
INTENT_FULL = 2      # intent search: of the cheats the probes pass, this many (cheapest, best scored) are solved in full
MAX_LOCKED = 6       # cheats on locked things, tried only to say whether one would have solved it
MAX_VIEWS = 4        # view cheat steps (nearest first) when the solve's own view ladder was cut short
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
            'mode', 'engaged', 'position', 'aim', 'size_scale', 'place_shift', 'moved_because', 'cheated', 'spans_m', 'azimuth_from',
            'picture_tilt_deg', 'lens_rejected', 'lenses_at_picture', 'departed', 'intent')
    out = {k: row[k] for k in keep if k in row}
    refused = [{'view': r['view'], 'lens': r.get('lens'), 'owners': r['owners']} for r in row.get('rejected') or [] if r.get('owners')]
    if refused:   # the views refused and what stood in front at each (an intent solve): what a cheat may clear
        out['refused'] = refused
    return out


def _owner_objects(owner):
    return [o for o in camera_frame.named_objects(owner)] if owner else []


def _in_front(job, move, fr, ctx):
    """{'context': {owner: share}, 'siblings': {owner: share}, 'eye': [x, y, z]} - what stands in front of the framed
    subject and of each must-see part, seen from where the picture's camera would stand."""
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
        own = {o.name for o in camera_frame.named_objects(part)}
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
    enclosing = sorted(o for o, (n, wraps) in wrapping.items() if n and wraps >= 0.5 * n)
    for owner in list(siblings):   # what is in front of a must-see part and not the subject's own is context
        if owner.split('/')[0] not in subjects or owner in shown:
            share = siblings.pop(owner)
            if owner.split('/')[0] not in subjects:
                context[owner] = max(context.get(owner, 0.0), share)
    for owner in enclosing:
        siblings.pop(owner, None)
    scene.frame_set(1)
    return {'context': context, 'siblings': siblings, 'enclosing': enclosing, 'eye': list(eye), 'box': box}


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
            views = core.candidate_views((fr['view']['elevation_deg'], fr['view'].get('azimuth_deg', 0.0)))
            if len([r for r in report.get('rejected') or [] if not r.get('widen')]) >= len(views):
                continue   # the solve already went round every view within the limits
            steps = [v['offset'] for v in views if any(v['offset'].values())
                     and all(abs(v['offset'][k]) <= row['limits'][k] for k in row['limits'])]
            out += [{'kind': kind, 'target': None, 'value': {k: float(v) for k, v in step.items()}} for step in steps[:MAX_VIEWS]]
        elif row['targets'] == 'siblings':
            front = sorted((set(seen['siblings']) | {b for b in blocking if '/' in b and b.split('/')[0] == str(target).split('/')[0]})
                           - set(seen['enclosing']))
            if front:
                out += [{'kind': kind, 'target': front, 'value': value} for value in row['steps']]
        elif row['targets'] == 'atmosphere':
            for owner in _atmosphere(seen['eye'], MAX_OWNERS):
                out += [{'kind': kind, 'target': owner, 'value': value} for value in row['steps']]
        elif row['targets'] == 'context':
            things = {**{b: 1.0 for b in blocking if '/' not in b or b.split('/')[0] != str(target).split('/')[0]}, **seen['context']}
            things = [o for o in sorted(things, key=lambda o: -things[o]) if not _is_support(o)
                      and not any(o2.name in ctx['mine'] for o2 in _owner_objects(o))][:MAX_OWNERS]
            for owner in things:
                for value in row['steps']:
                    out.append({'kind': kind, 'target': owner, 'value': _context_value(row, value, owner, seen) if row['applies'] == 'move' else value})
        elif row['targets'] == 'subject':
            out += [{'kind': kind, 'target': target, 'value': value} for value in row['steps']]
    return [c for c in out if c['value'] is not None]


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
    found = generate._sinks(job, job.get('_sink_allowance'), only=moved)
    if found and found['gate_failures']:
        return {'code': 'SUBJECT_SINKS', 'message': found['gate_failures'][0]['why']}
    return None


def _attempt(job, move, fr, ctx, cheats, enforce=True, seeds=None):
    """Solve the moment again with the cheats applied in this session; the scene is put back afterwards. A cheat that
    sinks a thing into its support is refused before the solve (SUBJECT_SINKS)."""
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


def picture_lens(fr):
    return (fr.get('_cheated') or {}).get('lens_from') or fr.get('lens_mm')


def drawn(row, fr):
    """Why a solved row is not the picture as drawn ([] when it is): the solve's own view step, a lens other than the
    picture's, a warning (a lean not reached, the camera in a volume)."""
    out = []
    if any((row.get('offset_from_picture') or {}).values()):
        out.append(f"the solve turned {row['offset_from_picture']} from the picture's view: {str(row.get('moved_because') or '')[:160]}")
    want = picture_lens(fr)
    if want and row.get('lens_mm') and abs(row['lens_mm'] - want) > 1e-6:
        out.append(f"{row['lens_mm']:g} mm, the picture {want:g} mm")
    out += [w for w in row.get('warnings') or []]
    return out


def _credit(cheats, row):
    """The camera cheats the solve did not keep ([] when every one did): a lens cheat whose lens the ladder left, a view
    cheat the view search turned off (or a lying subject's lean replaced)."""
    out = []
    for c in cheats:
        how = CHEATS[c['kind']]['applies']
        if how == 'lens' and abs((row.get('lens_mm') or 0) - float(c['value'])) > 1e-6:
            out.append((c, {'kind': c['kind'], 'target': None, 'value': float(row['lens_mm'])}))
        elif how == 'view':
            offset = row.get('offset_from_picture') or {}
            if any(offset.values()) or ('azimuth_deg' in c['value'] and row.get('azimuth_from') == 'tilt'):
                total = {k: round(float(c['value'].get(k, 0.0)) + float(offset.get(k, 0.0)), 3) for k in ('azimuth_deg', 'elevation_deg')}
                out.append((c, {'kind': c['kind'], 'target': None, 'value': total}))
    return out


def _within(cheat):
    from cheat_table import check_value
    return check_value(cheat['kind'], cheat['value']) is None


def _split(job, move, fr, ctx, failure, solved, keep_view):
    """(allowed, barred) cheats for a moment: candidates() split by what is locked (touches_locked, tree_lock)."""
    lock = job.get('locked') or {}
    allowed, barred = [], []
    for c in candidates(job, move, fr, ctx, failure, solved):
        if keep_view and CHEATS[c['kind']]['applies'] == 'view':
            continue
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
    allowed, barred = _split(job, move, fr, ctx, failure, solved, keep_view=True)
    first = fr.get('lens_mm') or move.get('lens_mm', 24.0)
    covered = [c for c in allowed if CHEATS[c['kind']]['applies'] == 'lens' and float(c['value']) <= first]
    allowed = [c for c in allowed if c not in covered]
    attempts, probes = [], []
    # the views the moment was refused at as it stands, and what stood in front at each: a cheat on a thing (a
    # neighbour made see-through, a thing moved or scaled) is probed only where that thing stood in front - elsewhere it
    # changes nothing; a cheat on the camera, the air or the subject itself is probed everywhere
    refused = ((failure or {}).get('report') or {}).get('rejected') or (solved or {}).get('refused') or []

    def views_for(cs):
        out = set()
        for c in cs:
            if CHEATS[c['kind']]['targets'] not in ('context', 'siblings'):
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

    def best_of(scored):
        ok = sorted((p for p in scored if p['ok']), key=lambda p: (sum(CHEATS[c['kind']]['cost'] for c in p['cheats']), p['score']))
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
    locked_solves = []
    for c in barred[:MAX_LOCKED]:
        tried = probe([c], enforce=False)
        if tried['ok'] and full([c], enforce=False, seeds=tried.get('passing')):
            locked_solves.append({k: c[k] for k in ('kind', 'target', 'value', 'why_locked')})
            break
    return {**out, 'status': 'redraw', 'locked_solves': locked_solves}


def search(job, move, fr, ctx, failure, deadline, goal=None, estimate=1.0, keep_view=False, solved=None):
    """The cheats for a moment: {status: cheat | redraw | undecided | moved, solved_by, attempts, locked_solves, untried}.
    goal(row) -> True when a solved row is what the search looks for (any solve for a failed moment; the picture as
    drawn for a moved one). A try starts only while more than `estimate` seconds (the moment's own solve) are left.
    keep_view: the picture's view is what is sought, so no view cheat is offered (it would only move off it)."""
    goal = goal or (lambda row: True)
    allowed, barred = _split(job, move, fr, ctx, failure, solved, keep_view)
    attempts, cut, failing = [], [False], [0.0]   # failing: how long the last failing try took

    def run(cheats, enforce=True, aimed=False):
        # a try starts only while it can end: a failing solve (the second, widened sweep included) takes longer than
        # the as-is solve that found the moment solvable
        if time.monotonic() + max(estimate, failing[0]) > deadline:
            cut[0] = True
            return None
        got = _attempt(job, move, fr, ctx, [{k: v for k, v in c.items() if k != 'why_locked'} for c in cheats], enforce)
        attempts.append(got)
        if not got['ok']:
            failing[0] = got['seconds']
        if got['ok']:   # a camera cheat counts for what it did; else re-aimed at what the solve reached, once
            missed = _credit(got['cheats'], got['row'])
            if missed:
                fixed = [next((new for old, new in missed if old is c), c) for c in got['cheats']]
                got['ok'], got['uncredited'] = False, [old for old, _ in missed]
                if not aimed and all(_within(c) for c in fixed) and fixed != got['cheats']:
                    return run(fixed, enforce, aimed=True)
            elif not goal(got['row']):
                got['ok'], got['short_of_goal'] = False, drawn(got['row'], fr)
        return got if got['ok'] else None

    found = None
    for c in in_rounds(allowed):
        found = run([c])
        if found or cut[0]:
            break
    pairs = []
    if not found and not cut[0]:
        cheapest = {}
        for c in allowed:
            cheapest.setdefault(c['kind'], c)
        pairs = sorted(itertools.combinations(cheapest.values(), 2), key=lambda p: CHEATS[p[0]['kind']]['cost'] + CHEATS[p[1]['kind']]['cost'])[:MAX_PAIRS]
        for pair in pairs:
            found = run(list(pair))
            if found or cut[0]:
                break
    if found:
        return {'status': 'cheat', 'solved_by': found, 'attempts': attempts}
    locked_solves = []
    if not cut[0]:
        for c in barred[:MAX_LOCKED]:
            got = run([c], enforce=False)
            if got:
                locked_solves.append({k: c[k] for k in ('kind', 'target', 'value', 'why_locked')})
                break
            if cut[0]:
                break
    tried = {json.dumps(a['cheats'], sort_keys=True) for a in attempts}
    untried = [c for c in allowed if json.dumps([{k: v for k, v in c.items() if k != 'why_locked'}], sort_keys=True) not in tried]
    full = not cut[0] and not untried
    status = 'redraw' if locked_solves or full else 'undecided'
    return {'status': status, 'attempts': attempts, 'locked_solves': locked_solves, 'cut_short': cut[0],
            'untried': {'allowed': len(untried), 'pairs': len(pairs), 'barred': len(barred)}}


def _moment_intent(job, move, fr, ctx, shot_deadline, check):
    """An intent moment (move.params.solve 'intent'): solved as it stands - as_intended when it keeps its picture's
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
            found = search_intent(job, move, fr, ctx, None, abort_at, goal=lambda r: not core.kept_intent(r), solved=row)
            out['search'] = {k: v for k, v in found.items() if k != 'status'}
            if found['status'] == 'cheat':
                out.update(status='cheat', solved_by=found['solved_by'])
            return out
        out['as_is'].update(code=failure['code'], message=failure['message'])
        out.update(search_intent(job, move, fr, ctx, failure, abort_at))
    except Stopped:
        out.update(status='stopped', why=f"the run's time (moment_s {check.get('moment_s')}, shot_s {check.get('shot_s')}) was spent "
                                         'before the search ended - nothing is decided')
    return out


def _moment(job, move, fr, ctx, shot_deadline, check):
    if ((move.get('params') or {}).get('solve')) == 'intent':
        return _moment_intent(job, move, fr, ctx, shot_deadline, check)
    t0 = time.monotonic()
    out = {'at': fr['at'], 'concept_id': (fr.get('picture') or {}).get('concept_id'), 'recorded': fr.get('cheats') or []}
    if t0 > shot_deadline:
        out.update(status='undecided', as_is={'ok': None}, why="the shot's time ran out before this moment")
        return out
    row, failure = _solve(job, move, fr, ctx)
    estimate = max(1.0, time.monotonic() - t0)
    out['as_is'] = {'ok': row is not None, 'seconds': round(estimate, 1)}
    deadline = min(shot_deadline, time.monotonic() + float(check.get('moment_s', 420)))
    if row is not None:
        off = drawn(row, fr)
        out['row'] = row
        if not off:
            out['status'] = 'cheat_recorded' if out['recorded'] else 'as_drawn'
            return out
        out.update(status='moved', moved=off)
        lens = picture_lens(fr)
        found = search(job, move, fr, ctx, None, deadline, goal=lambda r: not drawn(r, fr) or
                       (not any((r.get('offset_from_picture') or {}).values()) and r.get('lens_mm', 0) <= (lens or 0) + 1e-6
                        and not r.get('warnings')), estimate=estimate, keep_view=True, solved=row)
        out['search'] = {k: v for k, v in found.items() if k != 'status'}
        if found['status'] == 'cheat':
            out.update(status='cheat', solved_by=found['solved_by'])
        return out
    out['as_is'].update(code=failure['code'], message=failure['message'])
    out.update(search(job, move, fr, ctx, failure, deadline, estimate=estimate))
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
    for fr in sorted(frames, key=lambda f: f['at']):
        row, failure = _solve(trial, trial['shot']['camera']['move'], fr, ctx)
        intent = ((move.get('params') or {}).get('solve')) == 'intent'
        out[str(fr['at'])] = {'ok': row is not None, 'code': None if row else failure['code'],
                              'drawn': (core.kept_intent(row) if intent else drawn(row, fr)) if row else None, 'row': row}
    return out


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
        if check.get('solve'):   # --solve: the check solves as asked, the shot's own move unchanged on disk
            move.setdefault('params', {})['solve'] = check['solve']
        out['solve'] = (move.get('params') or {}).get('solve', 'exact')
        if any(f.get('cheats') for f in move.get('frames') or []):
            job['_sink_allowance'] = generate.sink_allowance(job)   # how deep what they move stood before them
            out['applied'] = cheat_scene.apply(job)   # what is recorded already: checked as it will be built
        sinks = generate._sinks(job, job.get('_sink_allowance'))
        if sinks and sinks['gate_failures']:
            out['pre_camera'].append({'code': 'SUBJECT_SINKS', 'text': sinks['gate_failures'][0]['why']})
        import reveal
        import show_scene
        rows = show_scene.apply(job)
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
    out['complete'] = True
    out['seconds'] = round(time.monotonic() - started, 1)
    write(out)
    return out


if __name__ == '__main__' and '--' in sys.argv:
    job_path = Path(sys.argv[sys.argv.index('--') + 1])
    JOB = json.loads(job_path.read_text())
    target = Path(JOB['output_dir']) / 'frames_check.json'

    def save(result):
        part = target.with_suffix('.part')
        part.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str))
        part.replace(target)
    run(JOB, save)
