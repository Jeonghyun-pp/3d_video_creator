"""Declared cheats, the host side: what is locked in a shot, computed from its facts (locked), the gate on the cheats
a move carries (touch_lines: CHEAT_TOUCHES_LOCKED), the project's viewpoint policy and the stored entry. The table
itself - CHEATS, its limits and the locked rule - is pure and shared with Blender: studio/blender_ops/cheat_table.py.

Why (2026-10-14): a reel is for viewers, not a paper ("실제를 고집하다 영상이 노잼될 우려"): what the narration claims
stays true, the pictures may cheat as long as it is declared; what is locked is computed from the facts, never listed
by hand per shot.
"""
from __future__ import annotations

from .blender_ops.cheat_table import CHEATS, _same, check_value, ordered, refused, targets_of, touches_locked  # noqa: F401


def locked(path, shot):
    """What no cheat that changes the scene may touch in this shot, from the facts: {lines, things: [{id, kind, ids}],
    ids: [every id a line speaks of, with an element's scene ids and a feature's parts], structure: [subjects the shot
    builds from specs], pose: [ids a line speaks of as a whole - their pose is said too], state: [parts whose state a
    line speaks of - a part or feature, or a part an element a line speaks of is built as], held_start: the shot runs on
    from the one before (its handover is continue / match_action), so its first moment is seen as that shot left it,
    held_end: the next shot runs on from this one, seconds: the shot's length}. A shot's lines are not timed against its
    moments: what they say holds over every moment."""
    from .decisions import envelope
    from .explains import line_things
    from .world import load as load_world
    body = {layer: (envelope(path, layer) or {}).get('body') or {} for layer in ('facts', 'script', 'shotlist')}
    entry = next((s for s in body['shotlist'].get('shots') or [] if s.get('shot_id') == shot['shot_id']), None)
    line_ids = list((entry or {}).get('line_ids') or [])
    world = load_world(path) or (envelope(path, 'world') or {}).get('body') or {}
    elements = {e['id']: e for e in world.get('elements', [])}
    things = []
    for thing in line_things(path, line_ids, body['facts'], body['script'], world) if line_ids else []:
        ids = [thing['id'], *thing['parts']]
        if thing['kind'] == 'element':
            ids += list((elements.get(thing['id']) or {}).get('ids') or [])
        things.append({'id': thing['id'], 'kind': thing['kind'], 'ids': list(dict.fromkeys(ids))})
    structure = sorted({s['subject_id'] for s in shot.get('subjects') or [] if s.get('subject_id')})
    from .blender_ops.show_core import CONTINUOUS, handover_of
    from .concept import _neighbour
    from .project import load_shot, shot_path
    from .project import load_project
    previous, following = _neighbour(path, shot['shot_id'], -1), _neighbour(path, shot['shot_id'], 1)
    held_start = bool(previous and shot_path(path, previous).is_file()
                      and (handover_of(load_shot(path, previous)) or {}).get('kind') in CONTINUOUS)
    held_end = bool(following and (handover_of(shot) or {}).get('kind') in CONTINUOUS)
    fps = ((load_project(path).get('output') or {}).get('fps')) or 30
    return {'lines': line_ids, 'things': things,
            'ids': sorted({i for t in things for i in t['ids']}),
            'structure': structure,
            'pose': sorted({i for t in things if t['kind'] in ('subject', 'element') for i in t['ids']}),
            # what explode_apart never takes apart (cheat_table: on_locked 'unclaimed_state'); the sheet says it too. An
            # element a line speaks of counts by the parts it is built as (`subject/part` among its scene ids): a line
            # about "the landing legs" said through an element keeps them in place (review 2026-10-17)
            'state': sorted({p for t in things if t['kind'] in ('part', 'feature', 'element') for p in t['ids'] if '/' in p}),
            'held_start': held_start, 'held_end': held_end,
            'seconds': round((shot.get('duration_frames') or 0) / fps, 3)}


def touch_lines(path, shot):
    """CHEAT_TOUCHES_LOCKED / CHEAT_INVALID (always errors, never softened: the facts stay true): [{code, text}] for the cheats the shot's move carries (camera.move.
    frames[].cheats, frames apply) - a value beyond its row's limits, or a scene cheat on what is locked. [] without cheats."""
    frames = ((shot.get('camera') or {}).get('move') or {}).get('frames') or []
    rows = [(fr, c) for fr in frames for c in fr.get('cheats') or []]
    if not rows:
        return []
    lock, out = locked(path, shot), []
    for fr, cheat in rows:
        at = fr['at']
        bad = check_value(cheat.get('kind'), cheat.get('value'))
        if not bad and CHEATS[cheat['kind']]['targets'] != 'camera' and not targets_of(cheat):
            bad = f"{cheat['kind']} names no target: a scene cheat changes a thing, named by its scene id"
        if not bad:
            bad = refused(cheat, fr, frames, lock, subject=(((shot.get('camera') or {}).get('move') or {}).get('params') or {}).get('target'))
        if bad:
            out.append({'code': 'CHEAT_INVALID', 'text': f"{shot['shot_id']} t={at}: {bad}"})
            continue
        why = touches_locked(cheat, lock)
        if why:
            out.append({'code': 'CHEAT_TOUCHES_LOCKED', 'text': f"{shot['shot_id']} t={at}: {why} - the narration's facts stay as "
                        'they are: take the cheat off (frames uncheat, then frames apply), or draw the picture again (frames check says how)'})
    return out


def viewpoint_policy(path):
    """project.policy.viewpoint_cheat ({by, user_words}): the user left the camera's lens and angle to the agent, so a
    view or lens departure needs no words per shot (still shown, still counted). None when the project has none."""
    from .common import read_json
    from .project import project_dir
    file = project_dir(path) / 'project.json'
    return ((read_json(file) if file.is_file() else {}).get('policy') or {}).get('viewpoint_cheat')


def set_viewpoint_policy(project, user_words):
    """Record project.policy.viewpoint_cheat: the user left the lens and the angle to the agent, in their own words
    (generative.review.check_user_words), with when it was recorded. Only the user's words set it."""
    from .common import lock, now, write_json
    from .generative.review import check_user_words
    from .project import load_project, project_dir, validate_schema
    path = project_dir(project)
    words = check_user_words(user_words, 'viewpoint policy')
    with lock(path / '.project.lock'):
        data = load_project(path)
        data.setdefault('policy', {})['viewpoint_cheat'] = {'by': 'agent', 'user_words': words, 'at': now()}
        data['revision'] += 1
        validate_schema(data, 'project')
        write_json(path / 'project.json', data)
    return {'project': str(path), 'viewpoint_cheat': data['policy']['viewpoint_cheat']}


def entry(kind, target, value, why, by, at, policy=None):
    """A cheat as stored with a picture (frames.json pictures.<cid>.cheats) and carried to move.frames[].cheats."""
    bad = check_value(kind, value)
    if bad:
        from .common import StudioError
        raise StudioError('CHEAT_INVALID', bad)
    return {'kind': kind, 'target': target, 'value': value, 'why': why, 'by': by, 'at': at,
            **({'under': policy} if policy else {})}
