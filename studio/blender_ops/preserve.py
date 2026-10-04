"""Explicit revision invariants, evaluated inside Blender before a snapshot is saved.

geometry: existing mesh local vertices/edges/faces/shape keys; additions and object
transforms are allowed. materials: existing object material assignments and shader
state. camera: active camera transform, data, constraints and animation. An object
ID protects that object and its existing descendants, including transforms.
These checks protect editable scene data, not pixel equivalence.
"""
from __future__ import annotations

import hashlib
import json
import bpy
from scene_tools import curves, object_by_id


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def identifier(obj):
    return obj.get('studio_id', obj.name)


def scalar(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bpy.types.ID):
        return {'id_type': type(value).__name__, 'id': identifier(value) if isinstance(value, bpy.types.Object) else value.name}
    try:
        return [scalar(item) for item in value]
    except TypeError:
        return str(value)


def properties(value, exclude=()):
    ignored = {'rna_type', 'name', 'name_full', 'id_data', 'original', 'session_uid', 'is_evaluated', 'users', 'tag', 'use_fake_user', 'is_embedded_data', 'library', 'override_library', 'preview', *exclude}
    result = {}
    for prop in value.bl_rna.properties:
        if prop.identifier in ignored or prop.type == 'COLLECTION' or prop.is_readonly:
            continue
        try:
            result[prop.identifier] = scalar(getattr(value, prop.identifier))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            continue
    return result


def animation(value):
    data = value.animation_data
    if not data:
        return None
    result = {'curves': [], 'drivers': [], 'nla': []}
    for curve in curves(data.action):
        result['curves'].append({'path': curve.data_path, 'index': curve.array_index,
                                 'extrapolation': curve.extrapolation,
                                 'keys': [{'co': list(k.co), 'left': list(k.handle_left), 'right': list(k.handle_right),
                                           'interpolation': k.interpolation, 'easing': k.easing} for k in curve.keyframe_points]})
    result['curves'].sort(key=lambda item: (item['path'], item['index']))
    for driver in data.drivers:
        result['drivers'].append({'path': driver.data_path, 'index': driver.array_index, 'type': driver.driver.type,
                                  'expression': driver.driver.expression, 'variables': [
                                      {'name': var.name, 'type': var.type, 'targets': [properties(t) for t in var.targets]}
                                      for var in driver.driver.variables]})
    for track in data.nla_tracks:
        result['nla'].append({'mute': track.mute, 'solo': track.is_solo,
                              'strips': [{'properties': properties(strip), 'action': strip.action.name if strip.action else None}
                                         for strip in track.strips]})
    return result


def geometry(obj):
    if obj.type != 'MESH':
        return None
    mesh = obj.data
    result = {'vertices': [list(v.co) for v in mesh.vertices], 'edges': [list(e.vertices) for e in mesh.edges],
              'faces': [list(p.vertices) for p in mesh.polygons]}
    if mesh.shape_keys:
        result['shape_keys'] = {key.name: [list(point.co) for point in key.data] for key in mesh.shape_keys.key_blocks}
    return digest(result)


def tree_state(tree, seen=None):
    seen = set() if seen is None else set(seen)
    if tree.name in seen:
        return {'recursive_group': tree.name}
    seen.add(tree.name)
    result = {'nodes': [], 'links': [], 'animation': animation(tree)}
    for node in tree.nodes:
        item = {'name': node.name, 'type': node.bl_idname,
                'properties': properties(node, ('location', 'width', 'height', 'dimensions', 'select', 'show_options', 'show_preview', 'show_texture', 'hide', 'label', 'parent', 'color', 'use_custom_color'))}
        for direction in ('inputs', 'outputs'):
            item[direction] = [{'id': socket.identifier, 'default': scalar(socket.default_value) if hasattr(socket, 'default_value') else None} for socket in getattr(node, direction)]
        if getattr(node, 'image', None):
            image = node.image
            item['image'] = {'path': image.filepath, 'source': image.source, 'colorspace': image.colorspace_settings.name,
                             'packed_sha256': hashlib.sha256(bytes(image.packed_file.data)).hexdigest() if image.packed_file else None}
        if getattr(node, 'color_ramp', None):
            item['ramp'] = {'interpolation': node.color_ramp.interpolation, 'elements': [(e.position, list(e.color)) for e in node.color_ramp.elements]}
        if node.type == 'GROUP' and node.node_tree:
            item['group'] = tree_state(node.node_tree, seen)
        result['nodes'].append(item)
    result['nodes'].sort(key=lambda item: item['name'])
    result['links'] = sorted((link.from_node.name, link.from_socket.identifier, link.to_node.name, link.to_socket.identifier) for link in tree.links)
    if hasattr(tree, 'interface'):
        result['interface'] = [properties(item) for item in tree.interface.items_tree]
    return result


_shader_cache = {}


def shader(material):
    if material is None:
        return None
    if material.as_pointer() in _shader_cache:
        return _shader_cache[material.as_pointer()]
    result = {'properties': properties(material, ('node_tree',)), 'animation': animation(material),
              'tree': tree_state(material.node_tree) if material.node_tree else None}
    _shader_cache[material.as_pointer()] = digest(result)
    return _shader_cache[material.as_pointer()]


def materials(obj):
    return [shader(slot.material) for slot in obj.material_slots]


def transform(obj):
    return {'matrix_basis': [list(row) for row in obj.matrix_basis],
            'parent': identifier(obj.parent) if obj.parent else None,
            'matrix_parent_inverse': [list(row) for row in obj.matrix_parent_inverse],
            'constraints': [properties(constraint) for constraint in obj.constraints], 'animation': animation(obj)}


def object_state(obj):
    data = {'type': obj.type, 'geometry': geometry(obj), 'transform': transform(obj), 'materials': materials(obj),
            'children': sorted(identifier(child) for child in obj.children),
            'visibility': {'hide_render': obj.hide_render, 'hide_viewport': obj.hide_viewport},
            'modifiers': [properties(modifier) for modifier in obj.modifiers]}
    if obj.type == 'CAMERA':
        data['camera'] = camera_state(obj)
    return digest(data)


def camera_state(obj):
    if not obj or obj.type != 'CAMERA':
        raise ValueError('PRESERVE_VIOLATION: active camera missing')
    return {'id': identifier(obj), 'transform': transform(obj), 'data': properties(obj.data),
            'dof': properties(obj.data.dof), 'animation': animation(obj.data)}


def find_objects(token):
    obj = object_by_id(token)
    if obj:
        return [obj]
    if '/' in token:
        instance, part = token.split('/', 1)
        return [obj for obj in bpy.context.scene.objects if obj.get('studio_instance_id') == instance and obj.get('studio_part_id') == part]
    return []


def capture(tokens, existing=None):
    bpy.context.scene.frame_set(1)
    _shader_cache.clear()
    result = {}
    for token in tokens:
        if token == 'geometry':
            ids = existing[token].keys() if existing else [identifier(o) for o in bpy.context.scene.objects if o.type == 'MESH']
            result[token] = {oid: geometry(obj) if (obj := object_by_id(oid)) else '__MISSING__' for oid in ids}
        elif token == 'materials':
            ids = existing[token].keys() if existing else [identifier(o) for o in bpy.context.scene.objects]
            result[token] = {oid: materials(obj) if (obj := object_by_id(oid)) else '__MISSING__' for oid in ids}
        elif token == 'camera':
            result[token] = digest(camera_state(bpy.context.scene.camera))
        else:
            if existing:
                ids = existing[token].keys()
            else:
                roots = find_objects(token)
                if not roots:
                    raise ValueError(f'PRESERVE_VIOLATION: required object ID does not exist: {token}')
                ids = sorted({identifier(obj) for root in roots for obj in [root, *root.children_recursive]})
            result[token] = {oid: object_state(obj) if (obj := object_by_id(oid)) else '__MISSING__' for oid in ids}
    return result


def compare(before, after):
    issues = []
    for token, baseline in before.items():
        if isinstance(baseline, dict):
            for oid, value in baseline.items():
                if after[token].get(oid) != value:
                    issues.append({'constraint': token, 'object_id': oid, 'reason': 'Preserved scene data changed or object was deleted'})
        elif baseline != after[token]:
            issues.append({'constraint': token, 'reason': 'Preserved camera changed'})
    return {'ok': not issues, 'constraints': list(before), 'before_sha256': digest(before), 'after_sha256': digest(after), 'issues': issues}
