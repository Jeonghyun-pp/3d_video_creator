"""What each spec builder reads from its params - pure Python (no bpy).

One table for spec lint (studio/subjects.py: a key no builder reads is refused, so a typo cannot be silently ignored)
and the docs. The readers are the GEOMETRY builders in modeling/*.py plus the array / mirror / asset branches of
modeling/assemble.py. tests/test_registry_sync.py parses those files and requires every `params[...]` / `params.get(...)`
read to be declared here, and the builder names to equal the subject schema's enum - so the table cannot drift from the code.
"""
from __future__ import annotations

SMOOTHING = ('smooth', 'sharp_angle_deg')   # primitives.mesh_object, for builders that pass their params through
SHARP = ('sharp_angle_deg',)                 # assemble stores it on every geometry part (studio_sharp_angle_deg)

BUILDER_PARAMS = {
    'loft': ('stations', 'axis', 'segments', 'cap_start', 'cap_end') + SMOOTHING,
    'wing': ('span', 'root_chord', 'tip_chord', 'sweep_deg', 'dihedral_deg', 'airfoil', 'tip_airfoil', 'incidence_deg', 'washout_deg',
             'twist_deg', 'mirror', 'sections', 'chord_points', 'span_axis', 'chord_axis', 'thickness_axis', 'tip', 'elliptic') + SMOOTHING,
    'revolve': ('profile', 'axis', 'angle_deg', 'segments', 'closed_profile', 'cap_start', 'cap_end', 'fillet_m', 'fillet_segments') + SMOOTHING,
    'sweep': ('profile', 'path', 'closed', 'segments', 'twist_deg', 'cap_start', 'cap_end', 'path_smooth', 'path_samples', 'scale') + SMOOTHING,
    'box': ('size', 'bevel_m', 'bevel_segments') + SMOOTHING,
    'profile': ('profile', 'length', 'axis', 'centered', 'start', 'fillet_segments') + SMOOTHING,
    'wall': ('length', 'height', 'thickness', 'openings') + SHARP,
    'toothed_ring': ('module', 'teeth', 'external', 'length', 'wall_m', 'phase_deg', 'addendum', 'dedendum', 'thickness', 'flank_deg') + SHARP,
    'subd': ('verts', 'faces', 'creases', 'levels') + SMOOTHING,
    'casting': ('members', 'subtract', 'voxel_m', 'fillet_m', 'round_m', 'offset_m', 'adaptivity', 'cuts') + SMOOTHING,
    'array': ('item', 'pattern', 'count', 'counts', 'axis', 'axes', 'center', 'start_deg', 'points', 'pitch_m', 'start_m', 'orient'),
    'mirror': ('source', 'axis'),
    'asset': ('manifest', 'instance_id'),
}
ITEM_LISTS = {'casting': ('members', 'subtract', 'cuts')}   # params holding lists of items, checked like array items
ITEM_BUILDERS = ('group',)   # an array item may also be a group of nested items: {builder: group, params: {items: [...]}}
GROUP_PARAMS = ('items',)
# What each shape op (an entry of a geometry builder's `ops`, modeling/ops.py) reads besides `op`.
OP_PARAMS = {
    'bevel': ('width_m', 'segments', 'angle_deg', 'profile'),
    'boolean': ('with', 'mode', 'solver'),
    'subdivide': ('levels', 'crease_angle_deg'),
    'solidify': ('thickness_m', 'offset'),
    'remesh_voxel': ('voxel_m', 'adaptivity'),
    'displace': ('strength_m', 'scale_m', 'seed'),
    'weld': ('dist_m',),
    'shade': ('smooth', 'sharp_angle_deg'),
}


def walk_entries(builder, pointer=''):
    """Yield (pointer, entry) for a spec builder and every geometry entry nested in it: array items, group items,
    item lists (casting members / subtract / cuts) and boolean operands - one walk for every check of nested entries."""
    yield pointer, builder
    kind, params = builder.get('builder'), builder.get('params') or {}
    if not isinstance(params, dict):
        return
    nested = []
    if kind == 'array' and isinstance(params.get('item'), dict):
        nested.append((f'{pointer}/params/item', params['item']))
    for key in ITEM_LISTS.get(kind, ()) + (('items',) if kind in ITEM_BUILDERS else ()):
        nested += [(f'{pointer}/params/{key}/{i}', sub) for i, sub in enumerate(params.get(key) or []) if isinstance(sub, dict)]
    for i, op in enumerate(builder.get('ops') or []):
        if isinstance(op, dict) and op.get('op') == 'boolean' and isinstance(op.get('with'), dict):
            nested.append((f'{pointer}/ops/{i}/with', op['with']))
    for here, entry in nested:
        yield from walk_entries(entry, here)


def unknown_params(builder):
    """[(pointer, key, reader, known)] for every key in a spec builder that nothing reads: params of the builder and of
    every nested entry (walk_entries), the keys of each shape op, and op names no op has."""
    out = []
    for pointer, entry in walk_entries(builder):
        kind, params = entry.get('builder'), entry.get('params') or {}
        known = GROUP_PARAMS if kind in ITEM_BUILDERS else BUILDER_PARAMS.get(kind)
        if known is not None and isinstance(params, dict):
            out.extend((f'{pointer}/params/{key}', key, kind, known) for key in params if key not in known)
        for i, op in enumerate(entry.get('ops') or []):
            if not isinstance(op, dict):
                continue
            name, here = op.get('op'), f'{pointer}/ops/{i}'
            if name not in OP_PARAMS:
                out.append((f'{here}/op', name, 'ops', tuple(OP_PARAMS)))
                continue
            out.extend((f'{here}/{key}', key, f'op {name}', OP_PARAMS[name]) for key in op if key != 'op' and key not in OP_PARAMS[name])
    return out


def cage_problems(verts, faces):
    """Why a subdivision cage is not a closed, consistently oriented surface (empty = fine) - pure, for spec lint."""
    problems = []
    n = len(verts)
    if any(len(v) != 3 for v in verts):
        problems.append('every vert is [x, y, z]')
    directed = {}
    for k, face in enumerate(faces):
        if len(face) < 3 or len(set(face)) != len(face) or not all(isinstance(i, int) and 0 <= i < n for i in face):
            problems.append(f'face {k} needs >= 3 distinct vert indices in 0..{n - 1}')
            continue
        for a, b in zip(face, list(face[1:]) + [face[0]]):
            directed[(a, b)] = directed.get((a, b), 0) + 1
    undirected = {}
    for (a, b), count in directed.items():
        undirected[frozenset((a, b))] = undirected.get(frozenset((a, b)), 0) + count
    open_edges = sorted(tuple(sorted(e)) for e, c in undirected.items() if c != 2)
    if open_edges:
        problems.append(f'not closed: edges {open_edges[:6]} are not shared by exactly two faces')
    else:   # closed: an edge used twice in the same direction means its two faces face opposite ways
        flipped = sorted(edge for edge, count in directed.items() if count > 1)
        if flipped:
            problems.append(f'faces disagree on orientation at edges {flipped[:6]} (each edge must run once each way)')
    return problems
