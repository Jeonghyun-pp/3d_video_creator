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


def _row(shot, ref):
    """The row of a shot a reference names: a direction.change id, or a show row by its moment (show@0.35, show@end)."""
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
                "stake), proof: {what: the scene that proves it - what is seen and what changes, rows: [shot_id/change_id or "
                "shot_id/show@<at>] that make it}, opening: {does: what the first cut does, why}}"], []
    if 'POINT_UNSHOWN' in silenced:
        return [], []
    shots = {s['shot_id']: s for s in body.get('shots') or []}
    missing = [ref for ref in point['proof']['rows'] if _row(shots.get(ref.split('/', 1)[0]) or {}, ref.split('/', 1)[1]) is None]
    if not missing:
        return [], []
    return [], [f"POINT_UNSHOWN: the proof ({point['proof']['what']}) names {', '.join(missing)}, which no cut makes - give the cut "
                "that carries it the change that proves it (direction.change) or the moment that shows it (show), name those, or "
                "say why the reel proves its point another way (studio reel depart --code POINT_UNSHOWN)"]
