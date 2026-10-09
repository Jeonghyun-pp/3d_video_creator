"""The neutral settings of an id pass (flat per-object colour that must come out exactly as painted) - one table for the
frame probe (frame_probe.py) and the workbench preview (workbench_tools.py).

A photoreal look leaves exposure, white balance, a tone curve, an AgX transform and a compositor on the scene; any of
them shifts the painted colours and the palette no longer matches (2026-10-07: EV +2.85 clipped the preview's id colours
and parts read as hidden). ``apply`` sets every entry and returns what it replaced, ``restore`` puts it back, so a
setting added to the table is also restored - no second list to keep in step.
"""
ID_VIEW = (
    ('render.engine', 'BLENDER_WORKBENCH'),
    ('render.film_transparent', True),
    ('render.use_compositing', False),
    ('render.use_sequencer', False),
    ('render.use_motion_blur', False),
    ('render.dither_intensity', 0.0),
    ('render.image_settings.file_format', 'PNG'),
    ('render.image_settings.color_mode', 'RGBA'),
    ('display.render_aa', 'OFF'),
    ('display.shading.light', 'FLAT'),
    ('display.shading.color_type', 'OBJECT'),
    ('display.shading.show_object_outline', False),
    ('display.shading.show_cavity', False),
    ('display.shading.show_shadows', False),
    ('display.shading.show_xray', False),
    ('display.shading.show_specular_highlight', False),
    ('display.shading.use_dof', False),
    ('display.shading.show_backface_culling', False),
    ('view_settings.view_transform', 'Standard'),
    ('view_settings.look', 'None'),
    ('view_settings.exposure', 0.0),
    ('view_settings.gamma', 1.0),
    ('view_settings.use_curve_mapping', False),
    ('view_settings.use_white_balance', False),
)


def _owner(scene, path):
    *parents, name = path.split('.')
    owner = scene
    for part in parents:
        owner = getattr(owner, part)
    return owner, name


def apply(scene):
    """Set the id-pass settings on ``scene``; returns {path: previous value} for ``restore``. Every value is read before
    any is set (changing the view transform resets the look). Settings this Blender does not have are skipped."""
    present = [(path, value, *_owner(scene, path)) for path, value in ID_VIEW]
    present = [(path, value, owner, name) for path, value, owner, name in present if hasattr(owner, name)]
    saved = {path: getattr(owner, name) for path, _, owner, name in present}
    for _, value, owner, name in present:
        setattr(owner, name, value)
    return saved


def restore(scene, saved):
    # table order: a setting whose valid values depend on another comes after it (a look is only valid once its view
    # transform is back - restored the other way round, 'AgX - Base Contrast' is refused under Standard)
    for path, value in saved.items():
        owner, name = _owner(scene, path)
        setattr(owner, name, value)


VISIBILITY_PATHS = ('hide_render', 'hide_viewport')


def _curves(action):
    """F-curves of an action, legacy or layered (Blender 4.4+: in the channelbags of its layers' strips)."""
    if getattr(action, 'fcurves', None) is not None:
        return action.fcurves, [action.fcurves]
    bags = [bag for layer in getattr(action, 'layers', []) for strip in layer.strips for bag in getattr(strip, 'channelbags', [])]
    return [c for bag in bags for c in bag.fcurves], [bag.fcurves for bag in bags]


def hide_always(obj):
    """Out of an id pass on every frame: hidden, its keyed visibility removed (re-evaluated on each frame_set it would
    bring the object back) - everything else it animates kept: a reveal cutter is a hidden helper that still has to move.
    Black should anything still draw it. Works on a copy of the action (actions are shared). Why (floor_noise,
    2026-10-09): wave lines tagged atmosphere and keyed visible came back white in the people masks."""
    obj.hide_render = True
    obj.color = (0.0, 0.0, 0.0, 1.0)
    data = obj.animation_data
    if not data:
        return
    for driver in list(data.drivers):
        if driver.data_path in VISIBILITY_PATHS:
            data.drivers.remove(driver)
    actions = [('action', data, data.action)] + [('action', strip, strip.action) for track in data.nla_tracks for strip in track.strips]
    for attr, owner, action in actions:
        if action is None:
            continue
        curves, collections = _curves(action)
        if not any(c.data_path in VISIBILITY_PATHS for c in curves):
            continue
        slot = getattr(owner, 'action_slot', None)
        copy = action.copy()
        setattr(owner, attr, copy)
        if slot is not None and getattr(copy, 'slots', None):   # layered actions: the copy's slot of the same name
            owner.action_slot = next((s for s in copy.slots if s.identifier == slot.identifier), copy.slots[0])
        for collection in _curves(copy)[1]:
            for curve in [c for c in collection if c.data_path in VISIBILITY_PATHS]:
                collection.remove(curve)
    obj.hide_render = True
