"""Coats: a thin layer laid on a part (soot, paint, a decal shell a millimetre out) that is drawn as its own object yet
is the part it lies on, wherever the engine looks at that part (2026-10-17: a rocket body's three soot shells, each its
own spec part, were painted over the body in the build's id pass - 106 px of a 180 px body - and named as standing in
front of it in every failure message).

A coat says what it lies on in one custom property, studio_coat_of = '<subject>/<part>' (camera_frame._owner's form):
written from a spec part's `coats` field (modeling/assemble) or by an author script with mark(). Read by the id pass
(frame_probe._classes: a coat takes the class of the part it coats), the ray walks (camera_frame: a ray reaching a coat
of its target reaches the target) and so the blocker lists (a coat is never in front of what it coats). A layer that
lies on a part like a coat without saying so is found by the exposure survey (coated_by) and said (COAT_UNTAGGED)."""
from __future__ import annotations

import bpy

COAT = 'studio_coat_of'


def mark(obj, of):
    """Make obj (and everything under it) a coat of the part `of` ('<subject>/<part>')."""
    for o in [obj, *obj.children_recursive]:
        o[COAT] = str(of)


def coat_of(obj):
    """The part ('<subject>/<part>') an object coats - its own or its nearest marked parent's - or None."""
    o = obj
    while o is not None:
        if o.get(COAT):
            return str(o[COAT])
        o = o.parent
    return None


def coats(owners):
    """The objects that coat any of these parts (owners: '<subject>/<part>' strings)."""
    owners = set(owners)
    if not owners:
        return []
    return [o for o in bpy.data.objects if o.get(COAT) and str(o[COAT]) in owners]
