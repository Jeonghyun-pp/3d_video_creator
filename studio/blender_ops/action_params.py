"""What each action type reads from its params, with defaults - pure Python (no bpy).

One table for the edit path (studio/shot_edit.py: a key may be added only if it is read here), shot validation
(studio/project.py: a key nothing reads is refused) and the docs. The readers are scene_tools.apply_actions (explode,
peel, assemble, cutaway, flow, highlight), reveal.apply, simulate.apply and kinematics.apply_drives; a Blender smoke
(tests/studio/action_params_smoke.py) runs each of them on recording params and requires what they read to equal
these rows, so the table cannot drift from the code. REQUIRED: must be given; DERIVED: taken from the scene when unset.
"""
from __future__ import annotations

REQUIRED, DERIVED = 'required', 'derived'

ACTION_PARAMS = {
    'explode': {'direction_source': REQUIRED, 'distance_m': REQUIRED, 'axis': [0, 0, 1], 'order': 'asset_order', 'stagger_frames': 0,
                'rotation_radians': [0, 0, 0]},
    # peel: direction_source is always 'asset' (schema), so the axis branch never runs
    'peel': {'direction_source': REQUIRED, 'distance_m': REQUIRED, 'order': 'asset_order', 'stagger_frames': 0, 'rotation_radians': [0, 0, 0]},
    'assemble': {'source_action_id': REQUIRED, 'order': 'asset_order', 'stagger_frames': 0},
    'cutaway': {'cutter_object_id': REQUIRED, 'cap_material_id': REQUIRED, 'cutter_keys': []},
    'flow': {'path_object_id': REQUIRED, 'speed_mps': REQUIRED, 'marker_count': REQUIRED, 'marker_radius_m': 0.06, 'loop': False, 'reverse': False},
    'highlight': {'color_srgb': REQUIRED, 'strength': REQUIRED, 'restore': True},
    'reveal': {'cutter_object_id': REQUIRED, 'cap_material_id': REQUIRED, 'cutter_keys': REQUIRED, 'also_cut_overlapping': False},
    'simulate': {'kind': REQUIRED, 'region': REQUIRED, 'count': REQUIRED, 'seed': 0, 'color_srgb': DERIVED},
    'drive': {'drives': REQUIRED},
}
# simulate reads more by kind
SIMULATE_KINDS = {
    'rigid_debris': {'size_range': [0.2, 0.6]},
    'dust': {'ceiling_z': DERIVED, 'drift_mps': 0.6, 'grain_m': 0.03},
}
# items of list-valued params, by (type, list key, ...)
ITEMS = {
    ('reveal', 'cutter_keys'): {'t': REQUIRED, 'location': DERIVED, 'rotation_euler': DERIVED, 'scale': DERIVED},
    ('cutaway', 'cutter_keys'): {'frame': REQUIRED, 'location': REQUIRED, 'rotation_euler': DERIVED},
    ('drive', 'drives'): {'joint': REQUIRED, 'subject': DERIVED, 'rpm': DERIVED, 'keys': DERIVED, 'profile': 'linear'},
    ('drive', 'drives', 'keys'): {'t': REQUIRED, 'value': REQUIRED},
}


def reads(action, sub=()):
    """{key: default} read from the params of `action` (sub=()) or from the items of a list param (sub=('drives',)).
    None when nothing is declared there (an unknown type, or a list whose items are values, not objects)."""
    kind = action.get('type')
    if kind not in ACTION_PARAMS:
        return None
    if sub:
        return ITEMS.get((kind, *sub))
    row = dict(ACTION_PARAMS[kind])
    if kind == 'simulate':
        sim_kind = (action.get('params') or {}).get('kind')
        if sim_kind in SIMULATE_KINDS:
            row.update(SIMULATE_KINDS[sim_kind])
        else:   # kind not chosen yet: any kind's keys may be written, validation narrows them once it is
            for extra in SIMULATE_KINDS.values():
                row.update(extra)
    return row


def unread(action):
    """Paths under params that nothing reads (a typo, or a key of another type/kind)."""
    out = []

    def walk(value, sub, where):
        row = reads(action, sub)
        if row is None or not isinstance(value, dict):
            return
        for key, item in value.items():
            if key not in row:
                out.append(f'{where}/{key}')
            elif isinstance(item, list) and reads(action, (*sub, key)) is not None:
                for i, entry in enumerate(item):
                    walk(entry, (*sub, key), f'{where}/{key}/{i}')
    walk(action.get('params') or {}, (), 'params')
    return out
