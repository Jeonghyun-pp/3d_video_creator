"""Blender-only scene inspection and deterministic shot operations."""
from __future__ import annotations

import json
import math
from pathlib import Path
import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view


def curves(action):
    if not action:
        return []
    if hasattr(action, 'fcurves'):
        return list(action.fcurves)
    result = []
    for layer in action.layers:
        for strip in layer.strips:
            for bag in getattr(strip, 'channelbags', []):
                result.extend(bag.fcurves)
    return result


def interpolation(obj, mode='ease_in_out'):
    action = obj.animation_data.action if obj.animation_data else None
    for curve in curves(action):
        for key in curve.keyframe_points:
            key.interpolation = {'linear': 'LINEAR', 'constant': 'CONSTANT'}.get(mode, 'BEZIER')
            if key.interpolation == 'BEZIER':
                key.handle_left_type = key.handle_right_type = 'AUTO_CLAMPED'



def clear_transform_keys(obj):
    action = obj.animation_data.action if obj.animation_data else None
    if not action:
        return
    collections = [action.fcurves] if hasattr(action, 'fcurves') else [bag.fcurves for layer in action.layers for strip in layer.strips for bag in getattr(strip, 'channelbags', [])]
    for collection in collections:
        for curve in list(collection):
            if curve.data_path in ('location', 'rotation_euler'):
                collection.remove(curve)


def object_by_id(identifier):
    return next((o for o in bpy.data.objects if o.get('studio_id') == identifier or o.name == identifier), None)


def targets(action):
    found = []
    for target in action['targets']:
        instance, part = target['instance_id'], target['part_id']
        matches = [o for o in bpy.context.scene.objects if
                   o.get('studio_id') == f'{instance}/{part}' or
                   (o.get('studio_instance_id') == instance and o.get('studio_part_id') == part)]
        if not matches:
            matches = [o for o in bpy.context.scene.objects if o.name == part]
        if not matches:
            raise ValueError(f'No target objects for {instance}/{part}')
        for obj in matches:
            children = [child for child in obj.children if child.get('studio_part_id')]
            found.extend(children if children else [obj])
    unique = {o.name: o for o in found}
    # Parent motion already moves all descendants; never translate both.
    result = []
    for obj in unique.values():
        parent = obj.parent
        while parent and parent.name not in unique:
            parent = parent.parent
        if parent is None:
            result.append(obj)
    return result


def apply_camera(shot):
    settings = shot['camera']
    scene = bpy.context.scene
    camera = scene.camera
    if not camera:
        data = bpy.data.cameras.new('StudioCamera')
        camera = bpy.data.objects.new('StudioCamera', data)
        scene.collection.objects.link(camera)
        scene.camera = camera
    camera.data.type = 'ORTHO' if settings['projection'] == 'orthographic' else 'PERSP'
    if settings['keys']:
        camera.animation_data_clear()
        camera.data.animation_data_clear()
        previous = None
        for key in settings['keys']:
            camera.location = key['location']
            direction = Vector(key['target']) - camera.location
            if direction.length < 1e-8:
                raise ValueError('Camera location equals target')
            rotation = direction.to_track_quat('-Z', 'Y').to_euler('XYZ', previous) if previous else direction.to_track_quat('-Z', 'Y').to_euler('XYZ')
            camera.rotation_euler = rotation
            previous = rotation.copy()
            frame = key['frame'] + 1
            camera.keyframe_insert('location', frame=frame)
            camera.keyframe_insert('rotation_euler', frame=frame)
            camera.data.lens = key.get('lens_mm', camera.data.lens)
            camera.data.ortho_scale = key.get('ortho_scale', camera.data.ortho_scale)
            camera.data.keyframe_insert('lens', frame=frame)
            camera.data.keyframe_insert('ortho_scale', frame=frame)
        interpolation(camera)
        interpolation(camera.data)


def apply_actions(shot):
    scene = bpy.context.scene
    scene.frame_set(1)
    # Rebuild only channels and objects owned by semantic actions; deleted actions must disappear too.
    for obj in list(scene.objects):
        if obj.get('studio_flow_action'):
            bpy.data.objects.remove(obj, do_unlink=True)
            continue
        if obj.get('studio_managed_transform'):
            clear_transform_keys(obj)
            obj.location = obj.get('studio_rest_location', obj.location)
            obj.rotation_euler = obj.get('studio_rest_rotation', obj.rotation_euler)
            obj['studio_managed_transform'] = False
        for modifier in list(obj.modifiers):
            if modifier.name.startswith('StudioCutaway_'):
                obj.modifiers.remove(modifier)
        for slot in obj.material_slots:
            if slot.material and slot.material.get('studio_original_material'):
                original = bpy.data.materials.get(slot.material['studio_original_material'])
                if original:
                    slot.material = original
    baseline = {}
    for obj in scene.objects:
        if 'studio_rest_location' not in obj:
            obj['studio_rest_location'] = list(obj.location)
            obj['studio_rest_rotation'] = list(obj.rotation_euler)
        baseline[obj.name] = (Vector(obj['studio_rest_location']), Vector(obj['studio_rest_rotation']))
    displaced = {}
    highlight_initialized = set()
    for action in shot['actions']:
        objects = targets(action)
        params, kind = action['params'], action['type']
        start, end = action['start_frame'], action['end_frame']
        if kind in ('explode', 'peel', 'assemble'):
            order = params.get('order', 'asset_order')
            if isinstance(order, list):
                ranks = {p: i for i, p in enumerate(order)}
                objects.sort(key=lambda o: (ranks.get(o.get('studio_part_id', o.name), len(ranks)), o.name))
            elif order in ('left_to_right', 'right_to_left'):
                scene.frame_set(1)
                objects.sort(key=lambda o: (world_to_camera_view(scene, scene.camera, o.matrix_world.translation).x, o.name), reverse=order == 'right_to_left')
            else:
                objects.sort(key=lambda o: o.get('studio_part_id', o.name))
            stagger = params.get('stagger_frames', 0)
            length = end - start - (len(objects) - 1) * stagger
            if length < 2:
                raise ValueError('TIMING_CONFLICT: stagger leaves fewer than two frames per part')
            poses = {}
            for index, obj in enumerate(objects):
                if not obj.get('studio_managed_transform'):
                    clear_transform_keys(obj)
                    obj['studio_managed_transform'] = True
                rest_loc, rest_rot = baseline[obj.name]
                if kind == 'assemble':
                    source = displaced.get(params['source_action_id'], {})
                    if obj.name not in source:
                        raise ValueError('Assemble target has no displaced source action')
                    initial, initial_rot = source[obj.name]
                    final, final_rot = rest_loc, rest_rot
                else:
                    direction = Vector(params.get('axis', [0, 0, 1]) if params.get('direction_source') == 'axis' else obj.get('studio_explode_vector', [0, 0, 0]))
                    if direction.length < 1e-8:
                        raise ValueError(f'Asset target {obj.name} has no nonzero studio_explode_vector')
                    direction.normalize()
                    distance = params['distance_m'] * (index + 1 if kind == 'explode' else 1)
                    initial, initial_rot = rest_loc, rest_rot
                    delta = direction * distance
                    asset_root = object_by_id(obj.get('studio_asset_root_id', ''))
                    if asset_root:
                        world_delta = asset_root.matrix_world.to_quaternion() @ delta
                        delta = obj.parent.matrix_world.inverted().to_3x3() @ world_delta if obj.parent else world_delta
                    final = rest_loc + delta
                    final_rot = Vector(rest_rot) + Vector(params.get('rotation_radians', [0, 0, 0]))
                first, last = start + index * stagger + 1, start + index * stagger + length
                obj.location, obj.rotation_euler = initial, initial_rot
                obj.keyframe_insert('location', frame=first)
                obj.keyframe_insert('rotation_euler', frame=first)
                obj.location, obj.rotation_euler = final, final_rot
                obj.keyframe_insert('location', frame=last)
                obj.keyframe_insert('rotation_euler', frame=last)
                interpolation(obj, action['easing'])
                poses[obj.name] = (Vector(final).copy(), Vector(final_rot).copy())
            displaced[action['action_id']] = poses
        elif kind == 'cutaway':
            cutter = object_by_id(params['cutter_object_id'])
            material = bpy.data.materials.get(params['cap_material_id'])
            if not cutter or cutter.type != 'MESH' or not material:
                raise ValueError('Cutaway requires mesh cutter and existing cap material')
            cutter.hide_render = True
            cutter.data.materials.clear(); cutter.data.materials.append(material)
            for obj in objects:
                if obj.type != 'MESH':
                    raise ValueError('Cutaway target must be a closed mesh')
                import bmesh
                mesh = bmesh.new(); mesh.from_mesh(obj.data)
                closed = all(edge.is_manifold for edge in mesh.edges)
                mesh.free()
                if not closed:
                    raise ValueError(f'Cutaway target {obj.name} is not manifold')
                if material.name not in [m.name for m in obj.data.materials if m]:
                    obj.data.materials.append(material)
                modifier_name = 'StudioCutaway_' + action['action_id']
                modifier = obj.modifiers.get(modifier_name) or obj.modifiers.new(modifier_name, 'BOOLEAN')
                modifier.operation = 'DIFFERENCE'; modifier.solver = 'EXACT'; modifier.object = cutter
                if hasattr(modifier, 'material_mode'):
                    modifier.material_mode = 'TRANSFER'
            for key in params.get('cutter_keys', []):
                cutter.location = key['location']; cutter.keyframe_insert('location', frame=key['frame'] + 1)
                if 'rotation_euler' in key:
                    cutter.rotation_euler = key['rotation_euler']; cutter.keyframe_insert('rotation_euler', frame=key['frame'] + 1)
            interpolation(cutter, action['easing'])
        elif kind == 'flow':
            path = object_by_id(params['path_object_id'])
            if not path or path.type != 'CURVE':
                raise ValueError('Flow path must be an existing curve')
            if len(path.data.splines) != 1:
                raise ValueError('Flow requires exactly one continuous spline')
            scale = path.matrix_world.to_scale()
            if max(scale)-min(scale) > 1e-5:
                raise ValueError('Flow requires uniform world scale; apply scale before authoring the path')
            length = path.data.splines[0].calc_length(resolution=64) * abs(scale.x)
            if length <= 0 or params['marker_count'] < 1 or params['speed_mps'] <= 0:
                raise ValueError('Flow path length, count and speed must be positive')
            prefix = f"flow_{action['action_id']}_"
            for old_marker in list(scene.objects):
                if old_marker.get('studio_flow_action') == action['action_id']:
                    bpy.data.objects.remove(old_marker, do_unlink=True)
            for i in range(params['marker_count']):
                bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8, radius=params.get('marker_radius_m', .06))
                marker = bpy.context.object; marker.name = f"flow_{action['action_id']}_{i:03}"; marker['studio_flow_action'] = action['action_id']
                constraint = marker.constraints.new('FOLLOW_PATH'); constraint.target = path; constraint.use_fixed_location = True
                span = params['speed_mps'] * (end-start-1) / scene.render.fps / length
                origin = i / params['marker_count']
                for local in range(start, end):
                    offset = origin + span * (local-start) / max(1, end-start-1)
                    offset = offset % 1 if params.get('loop') else min(1, offset)
                    constraint.offset_factor = 1-offset if params.get('reverse') else offset
                    constraint.keyframe_insert('offset_factor', frame=local+1)
                marker.hide_render = True; marker.keyframe_insert('hide_render', frame=max(1,start))
                marker.hide_render = False; marker.keyframe_insert('hide_render', frame=start+1)
                marker.hide_render = True; marker.keyframe_insert('hide_render', frame=end+1)
                interpolation(marker, 'linear')
        elif kind == 'highlight':
            for obj in objects:
                for slot_index, slot in enumerate(obj.material_slots):
                    original = slot.material
                    if original and original.get('studio_original_material'):
                        original = bpy.data.materials.get(original['studio_original_material'], original)
                    if not original:
                        continue
                    material_name = f"StudioHighlight_{obj.name}_{slot_index}"
                    material = bpy.data.materials.get(material_name) or original.copy()
                    material.name = material_name; material['studio_original_material'] = original.name; slot.material = material
                    material.use_nodes = True
                    node = next((n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
                    if not node:
                        raise ValueError('Highlight requires a Principled BSDF material')
                    color = node.inputs['Emission Color']; strength = node.inputs['Emission Strength']
                    original_node = next((n for n in original.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
                    oldcolor = tuple(original_node.inputs['Emission Color'].default_value) if original_node else (0,0,0,1)
                    oldstrength = original_node.inputs['Emission Strength'].default_value if original_node else 0
                    states = [(max(1,start),False),(start+1,True),(end,True)]
                    if params.get('restore', True):
                        states.append((end+1,False))
                    if material.name not in highlight_initialized:
                        material.node_tree.animation_data_clear()
                        highlight_initialized.add(material.name)
                    for frame, enabled in states:
                        color.default_value = (*params['color_srgb'],1) if enabled else oldcolor
                        strength.default_value = params['strength'] if enabled else oldstrength
                        color.keyframe_insert('default_value', frame=frame); strength.keyframe_insert('default_value', frame=frame)
                    interpolation(material.node_tree, 'constant')
    scene.frame_set(1)


def inspect():
    scene = bpy.context.scene
    objects = []
    for obj in scene.objects:
        if 'studio_id' not in obj:
            obj['studio_id'] = obj.name
        data = {'name': obj.name, 'studio_id': obj['studio_id'], 'type': obj.type,
                'parent': obj.parent.get('studio_id', obj.parent.name) if obj.parent else None,
                'location': list(obj.location), 'dimensions': list(obj.dimensions), 'scale': list(obj.scale),
                'materials': [s.material.name if s.material else None for s in obj.material_slots]}
        if obj.type == 'MESH':
            data.update({'vertices': len(obj.data.vertices), 'polygons': len(obj.data.polygons)})
        if obj.get('studio_part_id'):
            data['part_id'] = obj['studio_part_id']
        objects.append(data)
    missing, external = [], []
    for img in bpy.data.images:
        if img.source == 'FILE' and not img.packed_file and img.filepath:
            path = str(Path(bpy.path.abspath(img.filepath)).resolve())
            external.append(path)
            if not Path(path).is_file():
                missing.append(path)
    return {'schema_version': 1, 'blender_version': bpy.app.version_string, 'object_count': len(objects), 'objects': objects,
            'camera': scene.camera.name if scene.camera else None, 'frame_start': scene.frame_start, 'frame_end': scene.frame_end,
            'fps': scene.render.fps, 'engine': scene.render.engine, 'external_files': sorted(set(external)), 'missing_files': missing,
            'warnings': ['No camera'] if not scene.camera else []}


def anchor_for(identifier):
    obj = object_by_id(identifier)
    if obj:
        return obj, obj.matrix_world.translation.copy()
    # Semantic anchors can be stored as local coordinates on a part root.
    for obj in bpy.context.scene.objects:
        raw = obj.get('studio_anchors')
        anchors = json.loads(raw) if isinstance(raw, str) else {}
        if identifier in anchors:
            return obj, obj.matrix_world @ Vector(anchors[identifier])
    if identifier.endswith('/center'):
        obj = object_by_id(identifier[:-7])
        if obj:
            center = sum((Vector(corner) for corner in obj.bound_box), Vector()) / 8 if obj.type == 'MESH' else Vector()
            return obj, obj.matrix_world @ center
    return None, None


def anchors_for_frame(labels, frame):
    scene = bpy.context.scene
    result = []
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for label in labels:
        if not label['start_frame'] <= frame < label['end_frame']:
            continue
        obj, point = anchor_for(label['anchor'])
        if not obj:
            result.append({'frame': frame, 'label_id': label['label_id'], 'anchor_id': label['anchor'], 'u': 0, 'v': 0, 'depth': -1, 'visible': False, 'occluded': None})
            continue
        ndc = world_to_camera_view(scene, scene.camera, point)
        visible = ndc.z > 0 and 0 <= ndc.x <= 1 and 0 <= ndc.y <= 1
        occluded = None
        if visible:
            origin = scene.camera.matrix_world.translation
            ray = point-origin
            distance = ray.length
            hit, hitpoint, _, _, hitobj, _ = scene.ray_cast(depsgraph, origin, ray.normalized(), distance=max(0, distance-.005))
            occluded = bool(hit and hitobj != obj and (hitpoint-origin).length < distance-.005)
        result.append({'frame': frame, 'label_id': label['label_id'], 'anchor_id': label['anchor'], 'u': ndc.x, 'v': 1-ndc.y, 'depth': ndc.z, 'visible': visible, 'occluded': occluded})
    return result
