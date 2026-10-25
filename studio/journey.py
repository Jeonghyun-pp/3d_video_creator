"""The reel's camera journey: the places the camera goes through, in order, as the director plans them in the shot list
(journey: [{place, scale, why, passable?}]) and each cut's part in it (direction.journey: {from, to, role, why} - place
numbers, and travel | show | rest). The director chooses the journey, the roles and why; one place said with why is a
journey too (nothing forces the camera to fly). The engine holds only what follows from what is written:

- JOURNEY_UNSAID: the shot list has no journey, or a cut does not say its part in it (writing it costs nothing);
- JOURNEY_PLACE_UNKNOWN: a cut names a place number the journey does not have;
- JOURNEY_PLACE_UNBUILT: a travel cut passes a place the 3D does not build (a world element with a blockout, scene ids or
  a Blender shape) - no camera flies through a place that is not there;
- JOURNEY_JUMP (reel_flow, a flow finding answered with why): a cut that jumps past places without handing over to a
  new place.

`passable` names the slabs of a place the camera may pass through (a roof, a ceiling's filters): only those get a cut
that opens as the camera reaches them (recipe fly_in, reveal.py); any other crossing stays CAMERA_PASS_THROUGH.

Why (2026-10-25, the user, after a stadium fly-in example: "어떻게 하면 그런 연출을 하고, 그 연출을 구현할수 있을지"):
each cut's camera was chosen alone and nothing planned how the reel moves through its world.
"""
from __future__ import annotations

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


def built(place, world):
    """Whether the 3D builds a journey place: a world element whose shape is made in Blender - a blockout, scene ids that
    build it, or a Blender shape. A place named outside the world, or one only a picture makes, is not there to fly
    through."""
    element = next((e for e in (world or {}).get('elements') or [] if e.get('id') == place), None)
    if element is None:
        return False
    return bool(element.get('blockout') or element.get('ids') or (element.get('owner') or {}).get('shape') == 'blender')


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
    return errors, []
