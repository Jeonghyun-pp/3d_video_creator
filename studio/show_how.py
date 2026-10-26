"""The decision chain a shot list carries, from the reel down to each cut's motion:

  the reel's point (point.says)  ->  the scene that proves it (point.proof: rows of the shot list)
  ->  the beat's picture (story beat picture_idea)  ->  each cut's proof on screen (direction.show_how: point, proof rows)
  ->  the showing way (show_how.way, library/showing_ways.json)  ->  place and scale (direction.journey)
  ->  the motion (direction.move)

Each step follows from the one above it; a step that leaves the one above says why (a departure, read with the picture
by studio/excuses.py). The engine holds only what follows from what is written - a field left out, a row named that the
list does not have, two fields that contradict each other. Whether the chain is sound is read by a model from the words
(studio/plan_read.py, a cheap first filter) and judged on the picture by a viewer who does not know the plan
(studio/reel_see.py, the real test).

Why (2026-10-26, the user, after the fab reel's first cut flew in where the beat's picture was a box snatched from a
hand): "명확히 이 씬에서는 뭘 어떻게 보여줄거고 그러기 위해서는 어떤 움직임으로 어떤 장면을 보여주는것이 효과적이다. 라는
결정을 할수 있어야" - and the agent decides it, not the person directing the agent.
"""
from __future__ import annotations

import re

from .common import REPO, read_json


def _row(shot, ref):
    """The row of a shot a reference names: a direction.change id, a show row by its moment (show@0.35, show@end), or
    `frame` - what the cut frames (frame.focus), for a cut that proves by what it holds in view (a glance)."""
    if ref == 'frame':
        return shot.get('frame')
    if ref.startswith('show@'):
        from .blender_ops.show_core import at_share, show_of
        at = ref[len('show@'):]
        try:
            share = at_share(at if at in ('start', 'end') else float(at))
        except ValueError:
            return None
        return next((r for r in show_of(shot) if abs(at_share(r['at']) - share) < 1e-6), None)
    return next((c for c in (shot.get('direction') or {}).get('change') or [] if c.get('id') == ref), None)


def point_lines(body, silenced=()):
    """(errors, warnings) of the reel's point (shot list `point`):
    POINT_UNDECIDED (an error: writing it costs nothing) - the list says no point: the one sentence the reel proves, the
      scene that proves it and the rows that make that scene, and what the first cut does and why;
    POINT_UNSHOWN (a flow finding, answered with why: studio reel depart) - a row the proof names is not a change or a
      show moment of that cut: the proof is not on screen as anything the build makes."""
    point = body.get('point')
    if not point:
        return ["POINT_UNDECIDED: the shot list says no point - point: {says: the one thing the reel proves (from the story's "
                "stake), proof: {what: the scene that proves it - what is seen and what changes, rows: [shot_id/change_id, "
                "shot_id/show@<at> or shot_id/frame] that make it}, opening: {does: what the first cut does, why}}"], []
    if 'POINT_UNSHOWN' in silenced:
        return [], []
    shots = {s['shot_id']: s for s in body.get('shots') or []}
    missing = [ref for ref in point['proof']['rows'] if _row(shots.get(ref.split('/', 1)[0]) or {}, ref.split('/', 1)[1]) is None]
    if not missing:
        return [], []
    return [], [f"POINT_UNSHOWN: the proof ({point['proof']['what']}) names {', '.join(missing)}, which no cut makes - give the cut "
                "that carries it the change that proves it (direction.change) or the moment that shows it (show), name those, or "
                "say why the reel proves its point another way (studio reel depart --code POINT_UNSHOWN)"]


# --- each cut's show_how --------------------------------------------------------------------------------------------

TABLE = REPO / 'library' / 'showing_ways.json'


def ways():
    """The showing ways (library/showing_ways.json): a new way is a row, read here and nowhere else."""
    return read_json(TABLE)['ways']


def key_reach():
    """How far a cut's key_at may sit from its key show row and still be the same moment: half the widest gap between
    the frames a watcher's strip shows (screen_grammar.JUDGE_SHARES) - closer than that, both land on one judged frame."""
    from .screen_grammar import JUDGE_SHARES
    return max(b - a for a, b in zip(JUDGE_SHARES, JUDGE_SHARES[1:])) / 2


# words too common to say what a motion reveals: a reveals line and a part's own words sharing only these share nothing
COMMON = frozenset('the and with its from into onto over under then that this their there where while one all out off '
                   'for are was its his her who what when how which each than more most very just only also'.split())


def _words(text):
    return {w[:-1] if len(w) > 3 and w.endswith('s') else w for w in re.findall(r'[a-z]{3,}|[가-힣]{2,}', str(text).lower())} - COMMON


def _departs(shot, code):
    return any(d.get('item') == code.lower() for d in (shot.get('pattern') or {}).get('departs') or [])


def _parts(shot):
    from .blender_ops.show_core import show_of
    from .change_moves import changes_of
    return [p for r in show_of(shot) for p in r.get('parts') or []] + [p for c in changes_of(shot) for p in c.get('parts') or []]


def _mismatches(shot, body, words_of, about):
    """What two fields of one cut say against each other (INTENT_MISMATCH), in words."""
    from .blender_ops.show_core import at_share, show_of
    from .change_moves import changes_of
    out = []
    d = shot.get('direction') or {}
    move = d.get('move') or {}
    keys = [r for r in show_of(shot) if r.get('key')]
    if keys and move.get('key_at') is not None:
        key_parts = [p for r in keys for p in r.get('parts') or []]
        windows = [c['at'] for c in changes_of(shot) if c.get('at') and any(
            p == k or p.startswith(k + '/') or k.startswith(p + '/') for p in c.get('parts') or [] for k in key_parts)]
        near = any(abs(at_share(r['at']) - move['key_at']) <= key_reach() + 1e-9 for r in keys)
        if not near and not any(a <= move['key_at'] <= b for a, b in windows):
            out.append(f"its key_at {move['key_at']:g} is not at its key moment ({', '.join(str(r['at']) for r in keys)}) nor inside a "
                       'change of the key part')
    parts = _parts(shot)
    if move.get('reveals') and parts:
        said = _words(move['reveals'])
        named = set().union(*(_words(p.replace('_', ' ').replace('/', ' ')) | _words(words_of(p)) for p in parts))
        if not said & named:
            out.append(f"what its motion reveals ({move['reveals']!r}) names none of what it shows or changes ({', '.join(dict.fromkeys(parts))})")
    part = d.get('journey') or {}
    places = body.get('journey') or []
    if part.get('role') == 'travel' and 0 <= part.get('to', -1) < len(places):
        goal = places[part['to']]['place']
        seen = set((shot.get('frame') or {}).get('focus') or []) | set(parts) | set(about(shot))
        if not any(i == goal or i.startswith(goal + '/') for i in seen):
            out.append(f"it travels to {goal}, which is neither its focus, a part it shows nor what its lines speak of")
    return out


def cut_lines(body, story=None, lines=None, words_of=None, about=None, silenced=()):
    """(errors, warnings) of each cut's show_how (direction.show_how: point, proof, way, picture):
    SHOW_HOW_UNDECIDED (an error: writing it costs nothing) - a cut says no show_how, or its proof names no row of its own
      (a direction.change id, or show@<at> of a show moment);
    WAY_UNKNOWN (an error, like an unknown recipe) - its way, or the way it did not pick, is no row of showing_ways.json,
      or both are one way;
    flow findings, answered with why (a pattern departure of the cut, read with its picture; studio reel depart):
    MOVE_NOT_OF_WAY - its motion's recipe is not one its way delivers (the way's recipes);
    PICTURE_DROPPED - a story beat has a picture (picture_idea) and no cut telling the beat keeps it (picture: "beat") or
      says why it leaves it (picture: {departs: why});
    INTENT_MISMATCH - two of its fields contradict each other: its key_at is not at a key show row (nor inside a change
      of the key part), what its motion reveals names nothing it shows or changes, or a travel cut arrives somewhere that
      is neither its focus, a part it shows nor what its lines speak of.
    words_of(part id) -> the part's own words (a reveals line may name a part by them); about(shot) -> the ids its lines
    explain."""
    words_of = words_of or (lambda _id: '')
    about = about or (lambda _shot: ())
    table = {w['id']: w for w in ways()}
    errors, warnings = [], []
    for s in body.get('shots') or []:
        how = (s.get('direction') or {}).get('show_how')
        sid = s['shot_id']
        if not how:
            errors.append(f"SHOW_HOW_UNDECIDED: {sid} says no direction.show_how - point (what its viewer knows when it ends), proof "
                          f"(its rows that show it: change ids, show@<at>, frame), way ({', '.join(table)}) with one not picked and why, "
                          "picture (\"beat\" when it keeps its beat's picture, {departs: why} when it leaves it)")
            continue
        missing = [ref for ref in how['proof'] if _row(s, ref) is None]
        if missing:
            errors.append(f"SHOW_HOW_UNDECIDED: {sid}'s proof names {', '.join(missing)}, which is no change id, show moment (show@<at>) "
                          "nor its frame - the proof is a row the build makes")
        way, rejected = how['way']['id'], how['way']['rejected']['id']
        unknown = [w for w in (way, rejected) if w not in table]
        if unknown or way == rejected:
            errors.append(f"WAY_UNKNOWN: {sid} names {', '.join(unknown) or way + ' twice'} - a way is a row of library/showing_ways.json "
                          f"({', '.join(table)}); a new way is a new row")
            continue
        recipe = ((s.get('direction') or {}).get('move') or {}).get('recipe')
        if recipe and recipe not in table[way]['recipes'] and not _departs(s, 'MOVE_NOT_OF_WAY') and 'MOVE_NOT_OF_WAY' not in silenced:
            warnings.append(f"MOVE_NOT_OF_WAY: {sid} shows by {way} and moves as {recipe}, which {way} does not deliver "
                            f"({', '.join(table[way]['recipes'])}) - move as its way does, take the way its motion delivers, or say "
                            "why (pattern departs move_not_of_way)")
        if not _departs(s, 'INTENT_MISMATCH') and 'INTENT_MISMATCH' not in silenced:
            warnings += [f"INTENT_MISMATCH: {sid}: {m} - make the fields say one plan, or say why (pattern departs intent_mismatch)"
                         for m in _mismatches(s, body, words_of, about)]
    warnings += _picture_lines(body, story, lines, silenced)
    return errors, warnings


def _picture_lines(body, story, lines, silenced):
    from .story_core import beats_of
    if not story or 'PICTURE_DROPPED' in silenced:
        return []
    line_beat = {i: l.get('story_beat_id') for i, l in (lines or {}).items()}
    out = []
    for beat in story.get('beats') or []:
        if not beat.get('picture_idea'):
            continue
        cuts = [s for s in body.get('shots') or []
                if beat['story_beat_id'] in (s.get('story_beat_ids') or beats_of(s.get('line_ids') or [], line_beat))]
        said = [((s.get('direction') or {}).get('show_how') or {}).get('picture') for s in cuts]
        if cuts and not any(p == 'beat' or isinstance(p, dict) for p in said) and not any(_departs(s, 'PICTURE_DROPPED') for s in cuts):
            out.append(f"PICTURE_DROPPED: beat {beat['story_beat_id']}'s picture ({beat['picture_idea']}) is kept by none of its cuts "
                       f"({', '.join(s['shot_id'] for s in cuts)}) and none says why it leaves it - keep it (show_how.picture \"beat\") "
                       "or say what the cut shows instead and why (show_how.picture {departs: why})")
    return out


def purpose_words(shot):
    """What a cut is for, as a watcher is told it: its goal (the shot list's purpose, stored on the shot as goal) and what
    its viewer must know when it ends (show_how.point) - its id when neither is written."""
    point = ((shot.get('direction') or {}).get('show_how') or {}).get('point')
    said = [x for x in (shot.get('goal'), f'the viewer must come away knowing: {point}' if point else None) if x]
    return '; '.join(said) or shot['shot_id']
