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
