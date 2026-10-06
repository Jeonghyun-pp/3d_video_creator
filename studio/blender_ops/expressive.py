"""Apply a shot's declared expressive settings (expressive_core: grade, compositor, engine settings, add-ons).

Runs in generate.py right after the look and its gate check, so the look stays as frozen code and this step composes
with it: an exposure offset is added to the metered exposure, compositor ops are spliced in before the look's output
(its ops and per-segment exposure stay), engine settings are set by RNA name. A shot that declares none of these is
not touched at all. Everything applied is written to expressive_report.json with the values it replaced.
"""
from __future__ import annotations

import addon_utils
import bpy

import expressive_core as core

GROUP = 'StudioExpressive'


def _fail(message):
    raise ValueError('EXPRESSIVE_APPLY_FAILED: ' + message)


def _grade(scene, grade, report):
    view = scene.view_settings
    for key in ('view_transform', 'look'):
        if key in grade:
            before = getattr(view, key)
            try:
                setattr(view, key, grade[key])
            except TypeError as error:
                _fail(f'grade.{key} {grade[key]!r}: {error}')
            report['applied'].append({'path': f'grade.{key}', 'before': before, 'after': grade[key]})
    if 'exposure' in grade:
        report['warnings'].append(f"grade.exposure {grade['exposure']} replaces the metered exposure {round(view.exposure, 3)}")
        report['applied'].append({'path': 'grade.exposure', 'before': view.exposure, 'after': grade['exposure']})
        view.exposure = float(grade['exposure'])
    if 'exposure_offset_ev' in grade:
        before = view.exposure
        view.exposure = before + float(grade['exposure_offset_ev'])
        report['applied'].append({'path': 'grade.exposure_offset_ev', 'before': before, 'after': view.exposure})
    if 'gamma' in grade:
        report['applied'].append({'path': 'grade.gamma', 'before': view.gamma, 'after': grade['gamma']})
        view.gamma = float(grade['gamma'])
    if 'white_balance_k' in grade:
        view.use_white_balance = True
        report['applied'].append({'path': 'grade.white_balance_k', 'before': view.white_balance_temperature, 'after': grade['white_balance_k']})
        view.white_balance_temperature = float(grade['white_balance_k'])
    if grade.get('curve'):
        points = sorted((float(x), float(y)) for x, y in grade['curve'])
        if len(points) < 2 or any(not (0 <= v <= 1) for p in points for v in p):
            _fail('grade.curve needs >= 2 points [x, y] in 0..1')
        view.use_curve_mapping = True
        curve = view.curve_mapping.curves[3]   # the combined curve (R, G, B, C)
        while len(curve.points) > 2:
            curve.points.remove(curve.points[-1])
        curve.points[0].location, curve.points[1].location = points[0], points[-1]
        for x, y in points[1:-1]:
            curve.points.new(x, y)
        view.curve_mapping.update()
        report['applied'].append({'path': 'grade.curve', 'after': points})


def _output_link(tree):
    outputs = [n for n in tree.nodes if n.bl_idname == 'NodeGroupOutput']
    if not outputs or not outputs[0].inputs or not outputs[0].inputs[0].links:
        return None, None
    link = outputs[0].inputs[0].links[0]
    return link.from_socket, outputs[0].inputs[0]


def _compositor(scene, compositor, report):
    ops = compositor.get('ops') or []
    if not ops:
        return
    from look_camera import LABELS_FLAG   # anchored labels: the look itself drops position-changing ops for them
    moving = [o['op'] for o in ops if o['op'] in core.POSITION_CHANGING]
    if moving and scene.get(LABELS_FLAG):
        _fail(f'{moving} move pixels, and this shot has labels anchored to points in the scene; they would leave their anchors')
    tree = scene.compositing_node_group
    if tree is None:   # no look compositor: RenderLayers -> (ops) -> output
        tree = bpy.data.node_groups.new(GROUP, 'CompositorNodeTree')
        tree.interface.new_socket('Image', in_out='OUTPUT', socket_type='NodeSocketColor')
        layers = tree.nodes.new('CompositorNodeRLayers'); layers.scene = scene
        out = tree.nodes.new('NodeGroupOutput')
        tree.links.new(layers.outputs['Image'], out.inputs[0])
        scene.compositing_node_group = tree
    source, target = _output_link(tree)
    if source is None:
        _fail('the compositor has no image going to its output')
    image = source
    for i, op in enumerate(ops):
        node_type, params = core.COMPOSITOR_OPS[op['op']]
        node = tree.nodes.new(node_type)
        node.name = f"{GROUP}_{i}_{op['op']}"
        if op['op'] in core.FILTER_TYPE:
            node.inputs['Type'].default_value = core.FILTER_TYPE[op['op']]
        for key, value in op.items():
            if key == 'op':
                continue
            socket = params[key]
            name, kind = socket if isinstance(socket, tuple) else (socket, None)
            inputs = [s for s in node.inputs if s.name == name and (kind is None or s.type == kind)]
            if not inputs:
                _fail(f"compositor op {op['op']}: no input {name!r} on {node_type}")
            try:
                inputs[0].default_value = (*value, 1.0)[:4] if kind == 'RGBA' and len(value) == 3 else value
            except (TypeError, ValueError) as error:
                _fail(f"compositor op {op['op']}.{key} = {value!r}: {error}")
        tree.links.new(image, node.inputs['Image'])
        image = node.outputs['Image']
    tree.links.new(image, target)
    scene.render.use_compositing = True
    report['applied'].append({'path': 'compositor.ops', 'after': [o['op'] for o in ops], 'group': tree.name})


def _engine(scene, settings, report):
    for owner, values in settings.items():
        target = {'cycles': scene.cycles, 'eevee': scene.eevee, 'render': scene.render}[owner]
        for key, value in values.items():
            before = getattr(target, key)
            try:
                setattr(target, key, value)
            except (TypeError, AttributeError, ValueError) as error:
                _fail(f'engine_settings.{owner}.{key} = {value!r}: {error}')
            report['applied'].append({'path': f'engine_settings.{owner}.{key}', 'before': before if isinstance(before, (int, float, str, bool)) else str(before),
                                      'after': value})


def bundled(name):
    """True when the add-on ships with Blender (system scripts or the system extensions repository)."""
    roots = []
    for kind in ('SCRIPTS', 'EXTENSIONS'):
        try:
            roots.append(bpy.utils.system_resource(kind))
        except (TypeError, ValueError):
            pass
    for module in addon_utils.modules():
        if module.__name__ == name:
            return any(root and str(module.__file__).startswith(root) for root in roots)
    return False


def enable_addons(names, report=None):
    for name in names or []:
        if not bundled(name):
            raise ValueError(f'ADDON_NOT_BUNDLED: {name} is not an add-on shipped with Blender (downloaded add-on code never runs in a build)')
        addon_utils.enable(name, default_set=False)
        if report is not None:
            report['addons'].append(name)


def apply(scene, render):
    """Apply shot.render's expressive settings; return the report (None when the shot declares none)."""
    if not core.declared(render):
        return None
    report = {'schema_version': 1, 'applied': [], 'warnings': [], 'addons': []}
    enable_addons(render.get('addons'), report)
    _grade(scene, render.get('grade') or {}, report)
    _compositor(scene, render.get('compositor') or {}, report)
    _engine(scene, render.get('engine_settings') or {}, report)
    return report
