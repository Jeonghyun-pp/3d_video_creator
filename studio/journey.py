"""The reel's camera journey: the places the camera goes through, in order, as the director plans them in the shot list
(journey: [{place, scale, why, passable?}]) and each cut's part in it (direction.journey: {from, to, role, why} - place
numbers, and travel | show | rest). The director chooses the journey, the roles and why; one place said with why is a
journey too (nothing forces the camera to fly). The engine holds only what follows from what is written:

- JOURNEY_UNSAID: the shot list has no journey, or a cut does not say its part in it (writing it costs nothing);
- JOURNEY_PLACE_UNKNOWN: a cut names a place number the journey does not have;
- JOURNEY_PLACE_UNBUILT: a travel cut passes a place the 3D does not build (a world element with a blockout, scene ids or
  a Blender shape) - no camera flies through a place that is not there;
- JOURNEY_JUMP (reel_flow, a flow finding answered with why): a cut that jumps past places without handing over to a
  new place;
- JOURNEY_SCALE_JUMP: a step of more than one scale level crossed by one camera or by a plain cut. One metric scene
  holds one scale: such a step is crossed only by a dive - the cut pushes into the next place (its part ends there) and
  hands over scale_through to the cut that starts at it (scale_through_core: the engine shapes both ends and measures
  the join, SCALE_JOIN_OFF);
- SCALE_THROUGH_PLACE: a dive whose places do not line up (the next cut starts elsewhere, or no cut follows);
- SCALE_THROUGH_OUTWARD: a dive out to a larger scale - the engine dives inward only.

A scale level is a factor of ten in the true size a place stands at: the named scales (outside, building, room, object,
inside) are about one apart, and where the world builds a place at a size (a blockout's size_m) the factors of ten
between the two sizes count too - the larger of the two readings. So a scale finer than 'inside' (a chip's wiring, a
transistor) needs no new name: its size says how far it is (the N+1 scale is a size, not a word).

`passable` names the slabs of a place the camera may pass through (a roof, a ceiling's filters): only those get a cut
that opens as the camera reaches them (recipe fly_in, reveal.py); any other crossing stays CAMERA_PASS_THROUGH.

Why (2026-10-25, the user, after a stadium fly-in example: "어떻게 하면 그런 연출을 하고, 그 연출을 구현할수 있을지"):
each cut's camera was chosen alone and nothing planned how the reel moves through its world.
"""
from __future__ import annotations

import math

SCALES = ('outside', 'building', 'room', 'object', 'inside')
ROLES = ('travel', 'show', 'rest')


def of(shot):
    """A cut's part in the journey (direction.journey), or None."""
    return (shot.get('direction') or {}).get('journey')


def passes(part):
    """The place numbers a cut goes through, in its order (from to to, both included)."""
    a, b = int(part['from']), int(part['to'])
    step = 1 if b >= a else -1
    return list(range(a, b + step, step))


def passable(body, index):
    """The slabs the camera may pass through at journey place `index` (its `passable`)."""
    places = body.get('journey') or []
    return list((places[index] if 0 <= index < len(places) else {}).get('passable') or [])


def size_of(place, world):
    """The true size a journey place stands at (its world element's blockout: the largest side, metres), or None."""
    element = next((e for e in (world or {}).get('elements') or [] if e.get('id') == place.get('place')), None)
    size = ((element or {}).get('blockout') or {}).get('size_m')
    return max(size) if size else None


def scale_gap(a, b, world=None):
    """How many scale levels lie between two journey places: the named scales' distance, and the factors of ten
    between their true sizes where both are built at one (rounded), the larger of the two."""
    named = abs(SCALES.index(b['scale']) - SCALES.index(a['scale']))
    sa, sb = size_of(a, world), size_of(b, world)
    decades = int(abs(math.log10(sa / sb)) + 0.5) if sa and sb else 0
    return max(named, decades)


def inward(a, b, world=None):
    """Whether going from place a to place b goes to a smaller scale: by true size where both have one, else by name."""
    sa, sb = size_of(a, world), size_of(b, world)
    if sa and sb:
        return sb < sa
    return SCALES.index(b['scale']) >= SCALES.index(a['scale'])


def built(place, world):
    """Whether the 3D builds a journey place: a world element whose shape is made in Blender - a blockout, scene ids that
    build it, or a Blender shape. A place named outside the world, or one only a picture makes, is not there to fly
    through."""
    element = next((e for e in (world or {}).get('elements') or [] if e.get('id') == place), None)
    if element is None:
        return False
    return bool(element.get('blockout') or element.get('ids') or (element.get('owner') or {}).get('shape') == 'blender')


def passes_of(body, part):
    """The places a cut's journey part passes, in order, each with its scale and passable slabs - what the build writes
    on the shot (direction.journey.passes) for recipe fly_in to fly through. [] for a part naming a place out of range."""
    places = body.get('journey') or []
    if not part or not all(0 <= n < len(places) for n in (part['from'], part['to'])):
        return []
    return [{'place': places[n]['place'], 'scale': places[n]['scale'], **({'passable': list(places[n]['passable'])}
             if places[n].get('passable') else {})} for n in passes(part)]


def as_listed(direction):
    """A shot's direction as the shot list wrote it: without the passes the build wrote from the journey."""
    if not (direction or {}).get('journey') or 'passes' not in direction['journey']:
        return direction
    return {**direction, 'journey': {k: v for k, v in direction['journey'].items() if k != 'passes'}}


def lines(body, world=None):
    """(errors, warnings) of a shot list's journey (see module)."""
    errors = []
    places = body.get('journey') or []
    if not places:
        errors.append('JOURNEY_UNSAID: the shot list says no journey - the places the camera goes through in order, each with its '
                      'scale (outside, building, room, object, inside) and why; one place with why when the story stays there')
    for s in body.get('shots') or []:
        part = of(s)
        if not part:
            errors.append(f"JOURNEY_UNSAID: {s['shot_id']} says no direction.journey - where it goes from and to (place numbers), "
                          'its role (travel, show, rest) and why')
            continue
        unknown = [n for n in (part['from'], part['to']) if not 0 <= n < len(places)]
        if places and unknown:
            errors.append(f"JOURNEY_PLACE_UNKNOWN: {s['shot_id']} names place {unknown[0]}, the journey has {len(places)} "
                          f"(0..{len(places) - 1})")
            continue
        if part['role'] == 'travel' and places:
            missing = [places[n]['place'] for n in passes(part) if not built(places[n]['place'], world)]
            if missing:
                errors.append(f"JOURNEY_PLACE_UNBUILT: {s['shot_id']} travels through {', '.join(missing)}, which the 3D does not build - "
                              'give it a world element with a blockout (or scene ids), or travel another way')
    return errors + scale_lines(body, world), []


DIVE = 'scale_through'


def scale_lines(body, world=None):
    """JOURNEY_SCALE_JUMP / SCALE_THROUGH_PLACE / SCALE_THROUGH_OUTWARD (see module): each step of more than one scale
    level is crossed by a dive, and each dive hands its place to the cut that starts there."""
    places = body.get('journey') or []
    shots = body.get('shots') or []
    known = lambda part: bool(part) and all(0 <= part[k] < len(places) for k in ('from', 'to'))   # noqa: E731
    out = []
    for i, s in enumerate(shots):
        part = of(s)
        if not known(part):
            continue
        dives = (s.get('handover') or {}).get('kind') == DIVE
        nxt = shots[i + 1] if i + 1 < len(shots) else None
        after = of(nxt) if nxt else None
        steps = list(zip(passes(part), passes(part)[1:]))
        for n, (a, b) in enumerate(steps):   # inside one camera: only its last step may be a big one, crossed by its dive
            gap = scale_gap(places[a], places[b], world)
            if gap > 1 and not (dives and n == len(steps) - 1):
                out.append(f"JOURNEY_SCALE_JUMP: {s['shot_id']} flies from {places[a]['place']} to {places[b]['place']} in one camera, "
                           f"{gap} scale levels apart - one scene holds one scale: end the cut pushing into {places[b]['place']} and "
                           'hand over scale_through to a cut that starts there')
        if dives:
            if not known(after) or after['from'] != part['to']:
                where = f"{nxt['shot_id']} starts at place {after['from']}" if known(after) else 'no cut follows it'
                out.append(f"SCALE_THROUGH_PLACE: {s['shot_id']} dives into place {part['to']} ({places[part['to']]['place']}) and "
                           f"{where} - the cut after a dive starts at the place the dive pushes into")
            elif steps and not inward(places[steps[-1][0]], places[steps[-1][1]], world):
                out.append(f"SCALE_THROUGH_OUTWARD: {s['shot_id']} dives from {places[steps[-1][0]]['place']} out to the larger "
                           f"{places[steps[-1][1]]['place']} - the engine dives inward only: show the larger place with its own camera "
                           '(a new place)')
            continue
        if known(after) and after['from'] != part['to']:   # a plain cut to another place
            gap = scale_gap(places[part['to']], places[after['from']], world)
            if gap > 1:
                out.append(f"JOURNEY_SCALE_JUMP: {s['shot_id']} cuts from {places[part['to']]['place']} to "
                           f"{places[after['from']]['place']} in {nxt['shot_id']}, {gap} scale levels apart, with no dive - push into "
                           f"{places[after['from']]['place']} and hand over scale_through, or pass a place between")
    return out
