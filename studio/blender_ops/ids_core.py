"""Object ids, the pure part (no bpy): the one rule that turns a declared id into the id the build tags, and the order
in which a declared id is looked for among the built objects. The host (studio/layout.py, studio/blender.py) and
Blender (scene_index.resolve_group, frame_probe, fidelity, keep masks) both import it, so a shot's key_parts, screen
targets, keep list and subjects are resolved by the same rule wherever they are read.

Why a layout id: a declarative scene's instance row becomes a subject whose objects are tagged `<id>/<part>`, and its
repeated copies `<id>.0`, `<id>.1` (layout._expand) - so the built id cannot carry the '.' that numbers copies, and the
layout has always written '_' as '-' (b852cd0). Existing builds keep those ids; what changes is that every reader
applies the same rule instead of comparing the declared spelling.
"""
from __future__ import annotations

import difflib

FILL_PREFIX = 'fill-'   # fill_brief builds a project subject's copies as fill-<subject id>


def layout_id(ident):
    """The subject id a declarative scene instance row builds under."""
    return str(ident).lower().replace('_', '-').replace('.', '-')


def spellings(ident):
    """Every id a declared one may be built as, most literal first: as written; with the first segment (the subject)
    in layout form; as the fill copies of that subject. A declared id names one of them; the first that exists wins."""
    ident = str(ident)
    head, sep, rest = ident.partition('/')
    out = [ident, layout_id(head) + sep + rest, FILL_PREFIX + ident, FILL_PREFIX + layout_id(head) + sep + rest]
    return list(dict.fromkeys(out))


LINKS = {}   # world element id -> the scene ids that build it (world link); the build job sets it (build_scene.py)


def from_job(job):
    """The job, with its world links set for every reader in this Blender session (job['element_links']). Every entry
    script that resolves declared ids calls it on the job it reads (tests/test_element_links.py reads the scripts):
    2026-10-22, picture passes and candidates never set the links and a shot naming a linked part met SHOW_PART_MISSING."""
    global LINKS
    LINKS = dict(job.get('element_links') or {})
    return job


def linked(ident):
    """The scene ids a declared id names through a world link: an element's linked ids, or for '<element>/rest' each
    linked id with the rest; [] when it names no linked element. Why (2026-10-22, submarine): a shot names world
    elements (its show rows, details, keeps, the move's must-see); once an element is built as parts of a subject spec
    its objects carry the spec's ids, and every reader looked for the element's own id and found nothing."""
    ident = str(ident)
    head, sep, rest = ident.partition('/')
    # a link to one of the element's own parts takes no rest: 'grid' -> 'grid/dead' names the part, and 'grid/dead'
    # read through it again would be 'grid/dead/dead', an id nothing builds (2026-10-26: it grew without end)
    return [f'{i}{sep}{rest}' if sep else i for i in LINKS.get(head, []) if not (sep and i.startswith(head + '/'))]


def through_links(ident, find, _seen=frozenset()):
    """The roots a declared id names: what `find(id)` gives for it (the built objects under one id; [] when none), else
    what its world links name, all of them. Each id is followed once along a path: a link back to an id being resolved -
    an element linked to its own id (a kit built under the element's name), or a cycle - names nothing more. Why
    (2026-10-26, semiconductor_fab): `fab_building` linked to `fab_building` recursed without end when the cut had not
    built it, so the build died in Python instead of saying the place was not there; and an element linked to its own
    part (`wafer_circuit` -> `wafer_circuit/dead_chip`) grew that part's id without end."""
    seen = _seen | {str(ident)}
    found = find(ident)
    if found:
        return found
    return list(dict.fromkeys(r for i in linked(ident) if i not in seen for r in through_links(i, find, seen)))


def in_group(candidate, ident):
    """candidate (a built studio_id or layout id) belongs to the group a declared id names: ident.0, ident/part."""
    return candidate.startswith(ident + '.') or candidate.startswith(ident + '/')


def suggest(ident, known, n=3):
    """The built ids closest to a declared one that matched nothing (for the error hint)."""
    return difflib.get_close_matches(str(ident), sorted(set(known)), n=n, cutoff=0.5)
