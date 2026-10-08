"""Scene roles: one table that every "all meshes" rule consults, instead of per-rule exceptions.

Many passes sweep every mesh - look target bounds, previs clay, perfection, control near/far and render,
camera visibility / pass-through / flow ray casts, reveal overlap cuts. An earth shell around an underground
set, a fog volume, a hidden reveal cutter or a lane marking each needs different treatment in each of them.
An object declares what it is with `obj['studio_scene_role']` (not `studio_role`, which already names asset
parts and clay colour categories); each rule asks `counts(obj, rule)`. A new kind of
object is one new row here, not a new exception in nine places.

Untagged objects: hide_render -> 'helper' (the cutter, path and aim helpers are hidden), else 'object'; an object whose
hide_render is keyed (it appears or leaves during the shot) is an 'object' whatever the current frame shows.
"""
from __future__ import annotations

ROLES = {
    #                   bounds  clay   depth_range  control  raycast  reveal_overlap  perfection
    'object':            (True,  True,  True,        True,    True,    True,           True),
    'environment_shell': (True,  True,  False,       True,    True,    False,          False),  # earth / sky box around a set
    # shell 'bounds' stays True (measured 2026-10-05, samsung_photoreal s02): dropping the shell from the look's
    # bounds moved the practicals into the set and the log-average meter then blew the interior out
    # (EV 5.5-6.0 vs 3.35, visibly worse). Revisit together with interior metering, not alone.
    'atmosphere':        (False, False, False,       False,   False,   False,          False),  # fog / beam volumes
    'helper':            (False, False, False,       False,   False,   False,          False),  # cutters, paths, aim empties
    'clutter':           (True,  True,  True,        True,    True,    True,           False),  # markings, window bands
    'light_fixture':     (True,  True,  True,        True,    True,    False,          False),  # emissive panels / strips
    'scatter':           (True,  True,  True,        True,    True,    False,          False),  # GN host of scattered instances
    'scatter_source':    (False, False, False,       False,   False,   False,          False),  # instanced originals (excluded)
    'graphic':           (False, False, False,       False,   False,   False,          False),  # explainer graphics: own layer
    'decal':             (False, False, False,       False,   False,   False,          False),  # words on a surface (a sign, a tag):
    # lit with the scene, but never in the control passes a model reads, and kept from Blender in a generated take
    'simulated':         (True,  True,  True,        True,    True,    False,          False),  # baked rigid-body / sim results
}
TAG = 'studio_scene_role'
RULES = ('bounds', 'clay', 'depth_range', 'control', 'raycast', 'reveal_overlap', 'perfection')
# bounds          the look's subject bounds (lamp placement, metering target, scale audit)
# clay            receives the previs clay material (else hidden in previs)
# depth_range     counts toward the control pass's fixed near/far
# control         rendered in control passes (depth, clay, lines)
# raycast         blocks camera rays (visibility, pass-through, screen flow)
# reveal_overlap  may be cut by a reveal's also_cut_overlapping
# perfection      bevel / weighted normals / snapping


def role(obj):
    declared = obj.get(TAG) if hasattr(obj, 'get') else None
    if declared:
        if declared not in ROLES:
            raise ValueError(f"unknown {TAG} {declared!r} on {obj.name} (known: {sorted(ROLES)})")
        return declared
    return 'helper' if getattr(obj, 'hide_render', False) and not _keyed_visibility(obj) else 'object'


def _keyed_visibility(obj):
    """hide_render is animated: the object appears or disappears during the shot (an inspector walking in at frame 15),
    so being hidden on the current frame does not make it a helper."""
    action = getattr(getattr(obj, 'animation_data', None), 'action', None)
    if action is None:
        return False
    curves = getattr(action, 'fcurves', None)
    if curves is None:   # layered actions (Blender 4.4+): curves live in the channelbags of the action's layers
        curves = [c for layer in getattr(action, 'layers', []) for strip in layer.strips for bag in getattr(strip, 'channelbags', []) for c in bag.fcurves]
    return any(c.data_path == 'hide_render' for c in curves)


def counts(obj, rule):
    """Does `obj` take part in `rule` (one of RULES)?"""
    source = getattr(obj, 'original', None) or obj
    return ROLES[role(source)][RULES.index(rule)]


def first_blocking_hit(scene, depsgraph, origin, direction, distance, rule='raycast', max_steps=16):
    """scene.ray_cast that steps over objects not counting for `rule` (hidden helpers, volumes).
    Returns (hit, location, normal, index, object, matrix) like ray_cast; object is the original."""
    left, start = distance, origin.copy()
    for _ in range(max_steps):
        hit, location, normal, index, obj, matrix = scene.ray_cast(depsgraph, start, direction, distance=left)
        if not hit:
            return False, None, None, None, None, None
        source = getattr(obj, 'original', None) or obj
        # A collection instance is reported as its source object, which lives in an excluded collection and is
        # never seen except as an instance: count it as the scatter it belongs to.
        if counts(source, rule) or (role(source) == 'scatter_source' and ROLES['scatter'][RULES.index(rule)]):
            return True, location, normal, index, source, matrix
        step = (location - start).length + 1e-4
        start, left = location + direction * 1e-4, left - step
        if left <= 0:
            break
    return False, None, None, None, None, None
