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


def in_group(candidate, ident):
    """candidate (a built studio_id or layout id) belongs to the group a declared id names: ident.0, ident/part."""
    return candidate.startswith(ident + '.') or candidate.startswith(ident + '/')


def suggest(ident, known, n=3):
    """The built ids closest to a declared one that matched nothing (for the error hint)."""
    return difflib.get_close_matches(str(ident), sorted(set(known)), n=n, cutoff=0.5)
