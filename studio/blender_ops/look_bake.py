"""Opt-in texture bake (look passes.bake): heavy procedural materials on static, distant objects become three
packed images (colour, roughness, tangent normal) sampled by one plain Principled material.

Procedural catalog materials (look_materials, tagged `studio_lod`) cost every sample of every frame; on an object
the camera never comes near, a bake at a fixed footprint is visually the same and cheaper. Eligibility is a rule,
not a list: the object's role counts for 'perfection' (static real geometry), it does not move or simulate, no
boolean cuts it (a reveal changes its surface over time), every material is procedural and opaque dielectric
(metal, glass and emission do not survive a diffuse-colour bake), and its closest approach to the camera over the
shot is at least `min_distance_m`.

LOD: look_materials fades detail by the camera's View Distance; measured on 5.2, a bake evaluates View Distance
from the scene camera's current position, so each baked copy pins it to the object's closest approach instead
(the detail the shot can show, independent of the frame the bake ran on).

Measured 2026-10-05, samsung_photoreal s02 (min_distance_m 15, 1024 px, 40 largest of 349 eligible objects):
picture unchanged (CIE76 dE median 0.0, p95 1.07) but Cycles frame time only 13.45 -> 12.68 s (-6 %), for +606 s
of build. Below the -30 % bar: the s02 cost is lighting and volumes, not material evaluation. Keep it opt-in for
scenes whose profile shows shader time dominating (many layered procedural materials in view), not by default.
Each eligible object needs four bakes (~15 s at 1024 px on CPU), so cap `max_objects`.

Revertible like the other look passes: original materials are stored on the object and restored by revert_bake().
Config (passes.bake): {min_distance_m: 25, resolution_px: 1024, samples: 16, max_objects: 40}.
"""
from __future__ import annotations

import hashlib
import json

import bpy
from mathutils import Vector

from mesh_data import unique_data
from scene_roles import counts

PROP_REST = 'studio_look_rest_materials'
PROP_FROM = 'studio_baked_from'
UV = 'StudioBake'
PREFIX = 'StudioBaked_'
DEFAULTS = {'min_distance_m': 25.0, 'resolution_px': 1024, 'samples': 16, 'max_objects': 40, 'margin_px': 8}
OPAQUE = {'Transmission Weight': 0.0, 'Emission Strength': 0.0, 'Coat Weight': 0.0}  # max allowed (unlinked)
MAPS = (('color', 'Base Color', 'sRGB'), ('metallic', 'Metallic', 'Non-Color'), ('roughness', 'Roughness', 'Non-Color'))


def _closest_approach(obj, cameras):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector([min(c[k] for c in corners) for k in range(3)])
    hi = Vector([max(c[k] for c in corners) for k in range(3)])
    return min((Vector([min(max(p[k], lo[k]), hi[k]) for k in range(3)]) - p).length for p in cameras)


def _camera_path(scene):
    if scene.camera is None:
        return []
    start, end, current = scene.frame_start, scene.frame_end, scene.frame_current
    step = max(1, (end - start) // 48)
    out = []
    for f in list(range(start, end + 1, step)) + [end]:
        scene.frame_set(f)
        out.append(scene.camera.matrix_world.translation.copy())
    scene.frame_set(current)
    return out


def _surface_bsdf(mat):
    """The Principled BSDF wired straight into the active material output, or None (mixed shaders)."""
    out = mat.node_tree.get_output_node('CYCLES') if mat.use_nodes else None
    link = out.inputs['Surface'].links[0] if out and out.inputs['Surface'].is_linked else None
    return link.from_node if link and link.from_node.type == 'BSDF_PRINCIPLED' else None


def _bakeable_material(mat):
    """Procedural (catalog), one Principled surface, opaque and non-emissive: colour, metallic, roughness and
    normal then carry everything it shows."""
    if mat is None or not mat.use_nodes or 'studio_lod' not in mat:
        return False
    bsdf = _surface_bsdf(mat)
    if bsdf is None:
        return False
    for name, limit in OPAQUE.items():
        sock = bsdf.inputs.get(name)
        if sock is not None and (sock.is_linked or float(sock.default_value) > limit):
            return False
    alpha = bsdf.inputs.get('Alpha')
    return alpha is None or (not alpha.is_linked and alpha.default_value >= 1.0)


def _why_not(obj, cameras, cfg):
    from look_perfection import _is_moving
    if obj.type != 'MESH' or obj.hide_render or not counts(obj, 'perfection'):
        return 'role'
    if _is_moving(obj):
        return 'moving'
    if any(m.type == 'BOOLEAN' for m in obj.modifiers):
        return 'cut'
    if not obj.material_slots or not all(_bakeable_material(s.material) for s in obj.material_slots):
        return 'material'
    if not cameras or _closest_approach(obj, cameras) < cfg['min_distance_m']:
        return 'near'
    return None


def _uv(obj):
    unique_data(obj)
    mesh = obj.data
    keep_render = next((uv.name for uv in mesh.uv_layers if uv.active_render), None)
    layer = mesh.uv_layers.get(UV) or mesh.uv_layers.new(name=UV)
    mesh.uv_layers.active = layer
    view_layer = bpy.context.view_layer
    view_layer.objects.active = obj
    with bpy.context.temp_override(active_object=obj, object=obj, edit_object=obj,
                                   selected_objects=[obj], selected_editable_objects=[obj]):
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.uv.smart_project(island_margin=0.004)
        bpy.ops.object.mode_set(mode='OBJECT')
    if keep_render and keep_render in mesh.uv_layers:
        mesh.uv_layers[keep_render].active_render = True  # catalog textures keep their own mapping
    return layer


def _pinned_copy(mat, distance):
    copy = mat.copy()
    nt = copy.node_tree
    for node in [n for n in nt.nodes if n.type == 'CAMERA']:
        out = node.outputs['View Distance']
        if not out.links:
            continue
        value = nt.nodes.new('ShaderNodeValue')
        value.outputs[0].default_value = distance
        for link in list(out.links):
            nt.links.new(value.outputs[0], link.to_socket)
    return copy


def _emit(mat, socket):
    """Show one Principled input as unlit emission (EMIT bake reads it exactly)."""
    nt = mat.node_tree
    bsdf = _surface_bsdf(mat) or nt.nodes.get(PREFIX + 'bsdf')
    bsdf.name = PREFIX + 'bsdf'
    emit = nt.nodes.get(PREFIX + 'emit') or nt.nodes.new('ShaderNodeEmission')
    emit.name = PREFIX + 'emit'
    for link in list(emit.inputs['Color'].links):
        nt.links.remove(link)
    src = bsdf.inputs[socket]
    if src.is_linked:
        nt.links.new(src.links[0].from_socket, emit.inputs['Color'])
    else:
        v = src.default_value
        emit.inputs['Color'].default_value = tuple(v) if hasattr(v, '__len__') else (v, v, v, 1.0)
    nt.links.new(emit.outputs[0], nt.get_output_node('CYCLES').inputs['Surface'])


def _target(mat, image):
    nt = mat.node_tree
    node = nt.nodes.get(PREFIX + 'target') or nt.nodes.new('ShaderNodeTexImage')
    node.name = PREFIX + 'target'
    node.image = image
    nt.nodes.active = node


def _baked_material(name, images):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes['Principled BSDF']
    uv = nt.nodes.new('ShaderNodeUVMap'); uv.uv_map = UV
    tex = {}
    for kind, image in images.items():
        node = nt.nodes.new('ShaderNodeTexImage'); node.image = image; node.interpolation = 'Linear'
        nt.links.new(uv.outputs['UV'], node.inputs['Vector'])
        tex[kind] = node
    nt.links.new(tex['color'].outputs['Color'], bsdf.inputs['Base Color'])
    nt.links.new(tex['roughness'].outputs['Color'], bsdf.inputs['Roughness'])
    nt.links.new(tex['metallic'].outputs['Color'], bsdf.inputs['Metallic'])
    nmap = nt.nodes.new('ShaderNodeNormalMap'); nmap.space = 'TANGENT'; nmap.uv_map = UV
    nt.links.new(tex['normal'].outputs['Color'], nmap.inputs['Color'])
    nt.links.new(nmap.outputs['Normal'], bsdf.inputs['Normal'])
    mat.diffuse_color = tuple(images['color'].pixels[:4]) if len(images['color'].pixels) else mat.diffuse_color
    return mat


def _bake_object(scene, obj, distance, cfg):
    originals = [s.material for s in obj.material_slots]
    _uv(obj)
    res = int(cfg['resolution_px'])
    images = {}
    for kind, colorspace in [(k, c) for k, _, c in MAPS] + [('normal', 'Non-Color')]:
        image = bpy.data.images.new(f'{PREFIX}{obj.name}.{kind}', res, res, alpha=False)
        image.colorspace_settings.name = colorspace
        images[kind] = image
    copies = [_pinned_copy(m, distance) for m in originals]
    for slot, copy in zip(obj.material_slots, copies):
        slot.material = copy
    override = dict(active_object=obj, object=obj, selected_objects=[obj], selected_editable_objects=[obj])

    def bake(kind, bake_type, **extra):
        for copy in copies:
            _target(copy, images[kind])
        with bpy.context.temp_override(**override):
            bpy.ops.object.bake(type=bake_type, margin=int(cfg['margin_px']), use_clear=True, **extra)

    bake('normal', 'NORMAL', normal_space='TANGENT')
    # the Principled inputs themselves (unlit), routed through an emission: exact values, metal included
    for kind, socket, _ in MAPS:
        for copy in copies:
            _emit(copy, socket)
        bake(kind, 'EMIT')
    for image in images.values():
        image.pack()
    baked = _baked_material(PREFIX + obj.name, images)
    for slot in obj.material_slots:
        slot.material = baked
    for copy in copies:
        bpy.data.materials.remove(copy)
    obj[PROP_REST] = json.dumps([m.name for m in originals])
    obj[PROP_FROM] = hashlib.sha256(json.dumps({'materials': [m.name for m in originals], 'distance': round(distance, 3),
                                                'resolution': res}, sort_keys=True).encode()).hexdigest()[:16]


def apply_bake(scene, cfg):
    cfg = {**DEFAULTS, **(cfg or {})}
    cameras = _camera_path(scene)
    skipped, candidates = {}, []
    for obj in sorted(scene.objects, key=lambda o: o.name):
        reason = _why_not(obj, cameras, cfg)
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
        else:
            candidates.append((obj, _closest_approach(obj, cameras)))
    candidates.sort(key=lambda c: (-len(c[0].data.polygons), c[0].name))
    chosen = candidates[:int(cfg['max_objects'])]
    if not chosen:
        return {'baked': [], 'skipped': skipped, 'config': cfg}
    render = scene.render
    saved = (render.engine, scene.cycles.samples, scene.cycles.device, scene.frame_current)
    render.engine, scene.cycles.samples = 'CYCLES', int(cfg['samples'])
    try:
        for obj, distance in chosen:
            _bake_object(scene, obj, distance, cfg)
    finally:
        render.engine, scene.cycles.samples, scene.cycles.device = saved[:3]
        scene.frame_set(saved[3])
    return {'baked': [o.name for o, _ in chosen], 'not_chosen_over_max': len(candidates) - len(chosen),
            'skipped': skipped, 'config': cfg}


def revert_bake(scene):
    """Restore the procedural materials and drop the bake UV layer, images and materials. Idempotent."""
    restored = []
    for obj in sorted(scene.objects, key=lambda o: o.name):
        if PROP_REST not in obj:
            continue
        names = json.loads(obj[PROP_REST])
        for slot, name in zip(obj.material_slots, names):
            slot.material = bpy.data.materials.get(name)
        if obj.type == 'MESH' and UV in obj.data.uv_layers:
            obj.data.uv_layers.remove(obj.data.uv_layers[UV])
        del obj[PROP_REST]
        if PROP_FROM in obj:
            del obj[PROP_FROM]
        restored.append(obj.name)
    for mat in [m for m in bpy.data.materials if m.name.startswith(PREFIX) and m.users == 0]:
        bpy.data.materials.remove(mat)
    for image in [i for i in bpy.data.images if i.name.startswith(PREFIX) and i.users == 0]:
        bpy.data.images.remove(image)
    return {'restored': restored}
