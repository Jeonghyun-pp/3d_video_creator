"""Who makes each cut: a generative video model, Blender, or both (hybrid) - decided at the shot list, per cut.

Why (2026-10-20): the user chose generative first ("엄청 상세한 부품정도만 blender 로 하고, 생성모델을 많이 쓰는게 오히려
좋을수도"), then refused an either/or of cut kinds ("원리 컷/세상 컷 이렇게 이분법적으로 나누는것도 좀 그렇지않나"): the
same building is the world in one cut and the mechanism in the next. So what must be right in a cut is what its lines
say on screen (implied_keeps: a shape, a motion, a count - the user, 2026-10-20: "대사가 말하는것만"), and library/production_methods.json says what each way of making a cut can keep. The
method that keeps everything and leaves the most of the frame to a model is chosen (choose) - generative first, Blender
when nothing is left to generate. A new method is one more row of that table, not a new rule. The shot list may say
otherwise (`maker: {mode, why}`), never with a method that drops a keep (MAKER_KEEP_UNCOVERED).
"""
from __future__ import annotations

from .common import StudioError

MODES = ('generative', 'hybrid', 'blender')
KEEP_KINDS = ('shape', 'motion', 'count', 'label', 'identity')
SHARED_FUNCTIONS = ('subject', 'set', 'agent')  # world element functions whose look must stay one across generated cuts
METHODS = None   # library/production_methods.json, read once


def methods():
    global METHODS
    if METHODS is None:
        from .common import REPO, read_json
        METHODS = read_json(REPO / 'library' / 'production_methods.json')
    return METHODS


def _on_screen(cut):
    return set(cut['focus']) | {p for r in cut['show'] for p in r.get('parts') or []}


def implied_keeps(cut):
    """What the lines make a cut keep, whatever the plan wrote: a working thing a line explains (the subject, a part, a
    feature, a subject element), on screen, keeps its shape; a number said of a solid thing on screen, its count; the key
    change of a thing the lines speak of, its motion. The narration is true (10-14): what it explains is right where it is
    shown. A place, a crowd or the weather a line mentions is the model's to draw (a lake is any plausible lake)."""
    shown = _on_screen(cut)
    said = {t['id'] for t in cut['things']} | {p for t in cut['things'] for p in t['parts']}
    out = []
    working = [t for t in cut['things'] if t['kind'] in ('subject', 'part', 'feature') or cut['functions'].get(t['id']) == 'subject']
    held = [i for t in working for i in ([t['id']] if t['id'] in shown else sorted(set(t['parts']) & shown))]
    if held:
        out.append({'kind': 'shape', 'ids': sorted(set(held)), 'what': 'what its lines claim, on screen', 'implied': True})
    counted = [i for i in cut['numbered'] if i in shown and _keepable(cut, i)]   # a speed, a year or the weather is no count
    if counted:
        out.append({'kind': 'count', 'ids': counted, 'what': 'a number its lines give', 'implied': True})
    moving = sorted({p for r in cut['show'] if r.get('key') for p in (r.get('parts') or cut['focus']) if p in said}
                    | {p for c in cut.get('changes') or [] for p in c['parts']})   # what the shot list says changes (change_moves)
    if moving:
        out.append({'kind': 'motion', 'ids': moving, 'what': 'the key change its lines explain', 'implied': True})
    return out


def keeps_of(cut):
    """What the cut must keep: what its lines make true on screen (implied_keeps), one row per kind and id set. A list's
    own `keeps` are not read: the user, 2026-10-20, "대사가 말하는것만 이면 될것같은데" - planners asked to write them marked
    273 on 187 cuts (128 motions, 51 of water, smoke or lightning) and sent a third of the reel to Blender."""
    out, seen = [], set()
    for k in implied_keeps(cut):
        key = (k['kind'], tuple(k['ids']))
        if key not in seen:
            seen.add(key); out.append(k)
    return out


def _known(cut, ident):
    return ident in cut['kinds'] or ident in cut['functions']


def _keepable(cut, ident):
    """A solid thing the build places and the keep masks can cut out: a spec subject, part or feature, or a world
    element of a solid function (production_methods keepable_functions)."""
    if cut['kinds'].get(ident) in ('subject', 'part', 'feature'):
        return True
    return cut['functions'].get(ident) in methods()['keepable_functions']


def covers(method, keep, cut):
    """Does the method keep it? Ids the cut knows nothing by are left out: an unknown id decides nothing."""
    ids = [i for i in keep['ids'] if _known(cut, i)]
    how = method['keeps'].get(keep['kind'], 'none')
    if how == 'all' or not ids:
        return True
    if how == 'kept_parts':
        return all(_keepable(cut, i) for i in ids)
    if how == 'shared_picture':   # a later generated cut drawn from the first one's picture (look_anchor): things that keep a look, in focus
        return keep['kind'] == 'identity' and all(i in cut['focus'] and cut['shareable'](i) for i in ids)
    return False


def generated_share(method, cut, keeps):
    """How much of the frame a model makes with this method: all of it (generative), none (blender), or the frame less
    the kept things (hybrid) - the focus's share of the frame by its size, times the share of the focus kept."""
    if method['mode'] == 'generative':
        return 1.0
    how = set(method['keeps'].values())
    if how == {'all'}:
        return 0.0
    kept = {i for k in keeps for i in k['ids'] if _known(cut, i)}
    if not kept:
        return 1.0
    focus = cut['focus'] or sorted(kept)
    # a focus thing kept whole counts whole (or kept through its parent); one with a part kept, half; a kept thing
    # outside the focus (a show part), half a focus thing - the frame left to the model shrinks with each
    held = sum(1.0 if f in kept or any(f.startswith(k + '/') for k in kept) else 0.5 if any(k.startswith(f + '/') for k in kept) else 0.0
               for f in focus)
    held += 0.5 * sum(1 for k in kept if k not in focus and not any(k.startswith(f + '/') or f.startswith(k + '/') for f in focus))
    return round(1.0 - methods()['focus_share'].get(cut['size'] or 'medium') * min(1.0, held / len(focus)), 3)


def choose(cut, keeps):
    """The method that keeps everything and leaves the most of the frame to a model - generative first; one whose model
    would repaint under generate_min of the frame is no better than Blender alone. -> (method, share)"""
    table = methods()
    best = None
    for m in table['methods']:
        if not m.get('available') or not all(covers(m, k, cut) for k in keeps):
            continue
        share = generated_share(m, cut, keeps)
        if 0 < share < table['generate_min']:
            continue
        if best is None or share > best[1]:
            best = (m, share)
    if best is None:
        raise StudioError('INPUT_INVALID', 'no available method keeps all of it (library/production_methods.json: one row must keep everything)')
    return best


def _numbered(facts, script, line_ids):
    """About ids of the claims the lines say whose text gives a number."""
    from .decisions import _digits
    claims = {c['claim_id']: c for c in (facts or {}).get('claims', [])}
    lines = {l['line_id']: l for l in (script or {}).get('lines', [])}
    return sorted({a for i in line_ids for c in lines.get(i, {}).get('claim_ids') or []
                   if _digits(claims.get(c, {}).get('text', '')) for a in claims.get(c, {}).get('about') or []})


def _cut(path, entry, facts, script, world):
    from pathlib import Path
    from .blender_ops.show_core import show_of
    from .explains import line_things
    frame = entry.get('frame') or {}
    focus = list(frame.get('focus') or [])
    from .explains import _specs, resolve
    specs = _specs(path)
    kinds = {}
    ids = set(focus) | {p for r in show_of(entry) for p in r.get('parts') or []}
    for f in sorted(ids):   # what each id on screen or kept is: a subject spec, one of its parts or features (explains.resolve)
        thing = resolve(path, f, None, specs)
        if thing is not None:
            kinds[f] = thing['kind']
    things = line_things(path, entry.get('line_ids') or [], facts, script, world) if facts and script else []
    for t in things:   # what the lines speak of is resolved already
        kinds.setdefault(t['id'], t['kind'])
        for part in t['parts']:
            kinds.setdefault(part, 'part')
    return {'things': things,
            'show': show_of(entry), 'focus': focus, 'size': frame.get('size'),
            'action': str((entry.get('direction') or {}).get('action') or ''), 'kinds': kinds,
            'changes': list((entry.get('direction') or {}).get('change') or []),
            'functions': {e['id']: e.get('function') for e in (world or {}).get('elements', [])},
            'numbered': _numbered(facts, script, entry.get('line_ids') or []),
            'shareable': lambda i: shareable(path, i, world)}


def _keep_words(keeps):
    return '; '.join(f"{k['kind']} of {', '.join(k['ids'])}{' (' + k['what'] + ')' if k.get('what') else ''}" for k in keeps)


def propose(path, entry, facts, script, world):
    """{mode, method, keeps, why} for a shot list entry: what must be right in it and the method that keeps it all."""
    cut = _cut(path, entry, facts, script, world)
    keeps = keeps_of(cut)
    method, share = choose(cut, keeps)
    why = (f"keeps {_keep_words(keeps)} - {method['method']} keeps it, the model makes {share:.0%} of the frame"
           if keeps else 'nothing in it must be exact - the model makes all of it')
    return {'mode': method['mode'], 'method': method['method'], 'rule': method['method'], 'keeps': keeps, 'share': share, 'why': why}


def applies(entry, shot):
    """Does the shot list's maker decide this shot's route? When the list writes one (`maker`), or the shot has nothing a
    build made (no version) and its route was never the user's or another layer's choice beyond a plan. A built shot an
    earlier plan routed keeps its route: the table never takes a build away unasked (review 2026-10-20: re-approving the
    rocket's list would have turned its built hybrid shots generative); the sheet says what it would propose."""
    if entry.get('maker'):
        return True
    route = (shot or {}).get('route') or {}
    return not (shot or {}).get('scene_version') and route.get('rule_id') in (None, 'S_shotlist_maker', 'W_world_owners')


def maker_of(path, entry, facts, script, world):
    """The cut's maker: the shot list's own (maker), else the table's. -> {mode, why, rule?, given: bool}"""
    given = entry.get('maker')
    if given:   # what the cut must keep holds whoever makes it: a written hybrid puts it back too
        return {**given, 'keeps': keeps_of(_cut(path, entry, facts, script, world)), 'given': True}
    return {**propose(path, entry, facts, script, world), 'given': False}


def made_for(path, entry, facts, script, world, policy):
    """maker_of within the project's route policy: a cut the project or its features allow no generation is Blender's
    (routing.classify R1_no_generation) - what the build routes and what the sheet shows."""
    from .routing import classify
    made = maker_of(path, entry, facts, script, world)
    held = classify(entry.get('route_features') or [], policy)
    if made['mode'] != 'blender' and held[1] == 'R1_no_generation':
        made = {**made, 'mode': 'blender', 'why': f"{held[2]} (the table said {made['mode']}: {made['why']})"}
    return made


def leaves_to_words(entry, shot, mode):
    """Does the build leave what this cut only shows to words (world.described_for)? Where the table routes the cut and
    makes it hybrid; or an approved hybrid route planned with words (its screen.described, empty included). A cut built
    or routed before keeps every stand-in: the table never takes a build away unasked."""
    route = (shot or {}).get('route') or {}
    if route.get('status') != 'approved' and applies(entry, shot):
        return mode == 'hybrid'
    planned = route.get('status') == 'approved' or built_by_table(shot)
    return planned and route.get('mode') == 'hybrid' and 'described' in ((shot or {}).get('screen') or {})


def built_mode(path, entry, facts, script, world, policy=None):
    """The mode the build will give the cut, as materialize decides it: an approved route's, a kept route's (built or
    routed before: applies), else the table's within the project's policy (made_for, R1: no generation)."""
    from .project import load_shot, shot_path
    shot = load_shot(path, entry['shot_id']) if shot_path(path, entry['shot_id']).is_file() else None
    route = (shot or {}).get('route') or {}
    if route.get('mode') and (route.get('status') == 'approved' or not applies(entry, shot)):
        return route['mode']
    return made_for(path, entry, facts, script, world, policy or {'allow_generative': True, 'budget_usd': 0, 'turnaround_required': True})['mode']


def built_by_table(shot):
    """A shot built after the table routed it. It keeps its build as any built shot does (applies), but it was planned
    with the list: what it keeps and leaves to words keep following the list (E2E 2026-10-21: rewriting the list after
    builds re-routed built hybrid cuts by the world's owners and dropped what they leave to words)."""
    return bool((shot or {}).get('scene_version')) and ((shot or {}).get('route') or {}).get('rule_id') == 'S_shotlist_maker'


def lines(path, body, facts, script, world, policy=None):
    """(errors, warnings) of the shot list's makers: MAKER_KEEP_UNCOVERED (error, never softened) - a written maker whose
    methods cannot keep what the cut must keep; MAKER_DIFFERS (said) - a written maker other than the table's, with the
    why it gives; KEEPS_NOT_READ - a list writes keeps, which decide nothing; FRAME_UNDESCRIBED (an error when it has no frame) - a cut that says nothing of its
    screen (what it keeps is then read from its lines alone)."""
    errors, warnings = [], []
    known = {e['id'] for e in (world or {}).get('elements', [])}
    for entry in body.get('shots') or []:
        frame = entry.get('frame') or {}
        unknown = [f for f in frame.get('focus') or [] if f not in known and not _built_thing(path, f)]
        if not frame:   # an error since 2026-10-24: writing it costs nothing, and who makes the cut is read from it
            errors.append(f"FRAME_UNDESCRIBED: {entry['shot_id']} says nothing of its screen (frame: {{focus, size}}) - who makes the cut is read from it")
        elif unknown:   # a focus not built yet may be one the world adds: said
            warnings.append(f"FRAME_UNDESCRIBED: {entry['shot_id']} holds {unknown} in focus, which no element, subject or part is - who makes "
                            'the cut is read from it')
        if entry.get('keeps'):
            warnings.append(f"KEEPS_NOT_READ: {entry['shot_id']} writes keeps - what must be right is what its lines say on screen "
                            '(maker.implied_keeps); to make something exact, say it in a line the cut shows')
        made = maker_of(path, entry, facts, script, world)
        if built_mode(path, entry, facts, script, world, policy) != 'generative':   # a moment's parts are measured in the build: the build must make them (E2E
            # 2026-10-21: a generated water column in a hybrid cut's show stopped its build, KEY_PART_UNKNOWN)
            from .blender_ops.show_core import show_of
            elements = {e['id']: e for e in (world or {}).get('elements', [])}
            shown = sorted({p for row in show_of(entry) or () for p in row['parts'] if p in elements})
            model_made = [p for p in shown if (elements[p].get('owner') or {}).get('shape') == 'generative']
            if model_made:
                errors.append(f"SHOW_PART_GENERATED: {entry['shot_id']} (built) shows {model_made} in its show rows, whose shape "
                              "the model makes - the build cannot measure it: leave it to the cut's elements (the model draws it in words), "
                              "or show it in a cut a model makes")
            unbuilt = [p for p in shown if p not in model_made and not scene_ids(path, p, world)]
            if unbuilt:
                warnings.append(f"SHOW_PART_UNBUILT: {entry['shot_id']} shows {unbuilt}, which nothing builds yet (no spec, blockout or "
                                'link) - model it or give it a blockout before the build')
        if made['mode'] == 'hybrid':
            unbuilt = sorted({i for k in made.get('keeps') or [] for i in k['ids'] if i in known and not scene_ids(path, i, world)})
            if unbuilt:
                warnings.append(f"KEEP_UNBUILT: {entry['shot_id']} keeps {unbuilt}, which nothing builds yet (no spec, blockout or link) - "
                                'model it or give it a blockout before the build, or it cannot be put back from Blender')
        given = entry.get('maker')
        if not given:
            continue
        table = propose(path, entry, facts, script, world)
        cut = _cut(path, entry, facts, script, world)
        able = [m for m in methods()['methods'] if m['mode'] == given['mode'] and m.get('available')
                and all(covers(m, k, cut) for k in table['keeps'])]
        if not able:
            dropped = [k for k in table['keeps'] if not any(covers(m, k, cut) for m in methods()['methods'] if m['mode'] == given['mode'] and m.get('available'))]
            errors.append(f"MAKER_KEEP_UNCOVERED: {entry['shot_id']} is written {given['mode']}, which cannot keep {_keep_words(dropped)} - "
                          f"make it {table['mode']}, or show those things in a cut a model may make")
        elif given['mode'] != table['mode']:
            warnings.append(f"MAKER_DIFFERS: {entry['shot_id']} is {given['mode']} where the table says {table['mode']} "
                            f"({table['why']}); why: {given.get('why')}")
    return errors, warnings


def scene_ids(path, ident, world):
    """The scene ids that build a kept thing: a spec subject, part or feature is its own id; a world element, the ids it is
    linked to or its blockout's (world.element_ids); [] while nothing builds it."""
    from .world import element_ids
    element = next((e for e in (world or {}).get('elements', []) if e['id'] == ident), None)
    if element is not None and element_ids(element):
        return element_ids(element)
    return [ident] if _built_thing(path, ident) else []   # a spec subject, part or feature (an element built from its spec too)


def _built_thing(path, ident):
    """A subject spec, or a part or feature of one, by id."""
    from .explains import resolve
    return resolve(path, ident) is not None


def table(path, body, facts, script, world, policy=None):
    """Sheet rows: [{shot_id, mode, why, given}] and the share each maker takes of the reel's seconds. What a hybrid
    cut leaves to words is the build's own answer (leaves_to_words, world.described_for on world.json, the world the
    build reads): `words`, or `words_kept` for a cut that keeps its stand-ins (built or routed before)."""
    from .project import load_shot, shot_path
    from .world import described_for, load as load_world
    policy = policy or {'allow_generative': True, 'budget_usd': 0, 'turnaround_required': True}   # (the project's, from the sheet)
    built = load_world(path) or world
    rows = []
    for e in body.get('shots') or []:
        row = {'shot_id': e['shot_id'], 'seconds': float(e.get('duration_s') or 0), **made_for(path, e, facts, script, built, policy)}
        shot = load_shot(path, e['shot_id']) if shot_path(path, e['shot_id']).is_file() else None
        if shot is not None and not applies(e, shot) and (shot.get('route') or {}).get('mode'):
            row['kept'] = shot['route']['mode']   # a built shot keeps its route; the table's proposal is only said
        if (row.get('kept') or row['mode']) == 'hybrid' and built:
            words = described_for(e, shot, built, facts, script, path)[0]
            if words:
                row['words' if leaves_to_words(e, shot, row['mode']) else 'words_kept'] = words
        rows.append(row)
    modes = {r['shot_id']: r.get('kept') or r['mode'] for r in rows}
    for r in rows:
        anchor = look_anchor(path, body, r['shot_id'], modes, built)
        if anchor:
            r['shared_reference'] = {'from': anchor[0], 'ids': anchor[1]}
    total = sum(r['seconds'] for r in rows) or 1.0
    share = {m: round(sum(r['seconds'] for r in rows if r['mode'] == m) / total, 2) for m in MODES}
    return rows, share


def shareable(path, ident, world):
    """Does a thing keep one look across generated cuts: a world element that is a subject, set piece or person, or a
    subject spec, part or feature? (Not a place, weather or an unknown id.)"""
    element = next((e for e in (world or {}).get('elements', []) if e['id'] == ident), None)
    if element is not None:
        return element.get('function') in SHARED_FUNCTIONS
    from .explains import resolve
    return resolve(path, ident) is not None


def look_anchor(path, body, shot_id, modes, world):
    """(anchor shot id, [ids]) - the first earlier generated cut holding in focus a shareable thing this generated cut
    holds in focus too - else None. That cut's start picture sets the thing's look: this cut's start picture is drawn
    with it attached (concept.brief), and so its video starts from the same look. Why (the user, 2026-10-20, "응"): a
    thing that recurs stays generated, its look held by a shared picture; Veo's image to video reads only the first
    frame, so the look is shared where the picture is drawn, not sent to the video model."""
    order = [e['shot_id'] for e in body.get('shots') or []]
    entries = {e['shot_id']: e for e in body.get('shots') or []}
    if shot_id not in entries or modes.get(shot_id) != 'generative':
        return None
    mine = [f for f in (entries[shot_id].get('frame') or {}).get('focus') or [] if shareable(path, f, world)]
    for other in order[:order.index(shot_id)]:
        if modes.get(other) != 'generative':
            continue
        same = [f for f in mine if f in ((entries[other].get('frame') or {}).get('focus') or [])]
        if same:
            return other, same
    return None
