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
    'revolve': ('profile', 'axis', 'angle_deg', 'segments', 'closed_profile', 'cap_start', 'cap_end') + SMOOTHING,
    'sweep': ('profile', 'path', 'closed', 'segments', 'twist_deg', 'cap_start', 'cap_end') + SMOOTHING,
    'box': ('size', 'bevel_m', 'bevel_segments') + SMOOTHING,
    'profile': ('profile', 'length', 'axis', 'centered', 'start', 'fillet_segments') + SMOOTHING,
    'wall': ('length', 'height', 'thickness', 'openings') + SHARP,
    'toothed_ring': ('module', 'teeth', 'external', 'length', 'wall_m', 'phase_deg', 'addendum', 'dedendum', 'thickness', 'flank_deg') + SHARP,
    'array': ('item', 'pattern', 'count', 'counts', 'axis', 'axes', 'center', 'start_deg', 'points', 'pitch_m', 'start_m', 'orient'),
    'mirror': ('source', 'axis'),
    'asset': ('manifest', 'instance_id'),
}
ITEM_BUILDERS = ('group',)   # an array item may also be a group of nested items: {builder: group, params: {items: [...]}}
GROUP_PARAMS = ('items',)


def unknown_params(builder):
    """[(pointer, key)] for every params key in a spec builder (array items included) that no builder reads."""
    out = []

    def walk(entry, pointer):
        kind, params = entry.get('builder'), entry.get('params') or {}
        known = GROUP_PARAMS if kind in ITEM_BUILDERS else BUILDER_PARAMS.get(kind)
        if known is None or not isinstance(params, dict):
            return
        out.extend((f'{pointer}/params/{key}', key) for key in params if key not in known)
        if kind == 'array' and isinstance(params.get('item'), dict):
            walk(params['item'], f'{pointer}/params/item')
        if kind in ITEM_BUILDERS:
            for i, sub in enumerate(params.get('items') or []):
                if isinstance(sub, dict):
                    walk(sub, f'{pointer}/params/items/{i}')

    walk(builder, '')
    return out
