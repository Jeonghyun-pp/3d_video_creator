"""Blender side of subject fidelity: measure the built subject in its own frame, no judgement here.

Writes raw geometry facts (bbox along the spec axes, per-part sizes, feature counts, projected
silhouette triangles per orthographic view, assembly-claim distances, max on-screen size of each
feature along the shot camera). The host (studio/fidelity.py) compares them with the spec and the
reference silhouettes. ``geometry_only`` skips the camera pass (fitting loops, workbench).
"""
from __future__ import annotations

import json

import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view

VIEW_AXES = {'side': ('length', 'height'), 'top': ('length', 'width'), 'front': ('width', 'height')}
AXIS_INDEX = {'x': 0, 'y': 1, 'z': 2}


def _parts(subject_id):
    found = {}
    for obj in bpy.context.scene.objects:
        if obj.get('studio_subject_id') == subject_id and obj.type == 'MESH':
            found.setdefault(obj.get('studio_part_id', obj.name), []).append(obj)
    return found


def _copies(objs):
    """Countable units of a part: an array copy (an assembly 'group' of several meshes counts once), else a mesh."""
    keys = set()
    for obj in objs:
        unit = obj
        while unit is not None and 'studio_array_index' not in unit:
            unit = unit.parent
        keys.add((unit or obj).name)
    return len(keys)


def _local_points(objs, root_inverse):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = []
    for obj in objs:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            matrix = root_inverse @ obj.matrix_world
            points += [matrix @ v.co for v in mesh.vertices]
        finally:
            evaluated.to_mesh_clear()
    return points


def _triangles(objs, root_inverse, u_axis, v_axis):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    tris = []
    for obj in objs:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            matrix = root_inverse @ obj.matrix_world
            coords = [matrix @ v.co for v in mesh.vertices]
            for tri in mesh.loop_triangles:
                tris.append([[round(coords[i][u_axis], 5), round(coords[i][v_axis], 5)] for i in tri.vertices])
        finally:
            evaluated.to_mesh_clear()
    return tris


def _solid(objs, root_inverse):
    """Triangles [[x, y, z] x 3] of the evaluated meshes in the subject's root frame."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    tris = []
    for obj in objs:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            matrix = root_inverse @ obj.matrix_world
            coords = [[round(c, 5) for c in matrix @ v.co] for v in mesh.vertices]
            tris.extend([coords[i] for i in tri.vertices] for tri in mesh.loop_triangles)
        finally:
            evaluated.to_mesh_clear()
    return tris


def measure_subject(spec, shot=None, output_size=None, frame=1, geometry_only=False, views=None):
    scene = bpy.context.scene
    scene.frame_set(frame)
    bpy.context.view_layer.update()  # dimensions are stale until the depsgraph is evaluated
    subject_id = spec['subject_id']
    root = next((o for o in scene.objects if o.get('studio_id') == subject_id and not o.get('studio_part_id')), None)
    from mathutils import Matrix
    root_inverse = root.matrix_world.inverted() if root else Matrix.Identity(4)
    parts = _parts(subject_id)
    axes = {**{'length': 'y', 'width': 'x', 'height': 'z'}, **spec.get('axes', {})}
    every = [o for objs in parts.values() for o in objs]
    def extent(points, axis):
        i = AXIS_INDEX[axes.get(axis, axis)]
        values = [p[i] for p in points]
        return (max(values) - min(values)) if values else 0.0
    whole = _local_points(every, root_inverse)
    result = {'subject_id': subject_id, 'root_found': root is not None, 'parts': {}, 'whole': {}, 'silhouettes': {}, 'screen_px': {}}
    for axis in ('length', 'width', 'height', 'x', 'y', 'z'):
        result['whole'][axis] = round(extent(whole, axis), 5)
    for part_id, objs in sorted(parts.items()):
        points = _local_points(objs, root_inverse)
        result['parts'][part_id] = {'objects': len(objs), 'copies': _copies(objs), 'features': sorted({f for o in objs for f in json.loads(o.get('studio_features', '[]'))}),
                                    **{axis: round(extent(points, axis), 5) for axis in ('length', 'width', 'height', 'x', 'y', 'z')},
                                    'bounds': {a: [round(min(p[i] for p in points), 5), round(max(p[i] for p in points), 5)] if points else None
                                               for a, i in AXIS_INDEX.items()}}
    for silhouette in spec.get('silhouettes', []):
        if views is not None and silhouette['view'] not in views:
            continue
        u, v = VIEW_AXES[silhouette['view']]
        kept = [o for part, objs in parts.items() if part not in silhouette.get('exclude_parts', []) for o in objs]
        result['silhouettes'][silhouette['view']] = _triangles(kept, root_inverse, AXIS_INDEX[axes[u]], AXIS_INDEX[axes[v]])
    if spec.get('photo_views'):   # the host projects these with each reference view's camera (studio/fidelity.py photo checks)
        result['solid'] = {part_id: _solid(objs, root_inverse) for part_id, objs in sorted(parts.items())}
    if spec.get('assembly_claims'):
        from geom_checks import measure_claim, part_groups
        groups = part_groups(parts, root_inverse)
        result['assembly'] = []
        for i, claim in enumerate(spec['assembly_claims']):
            missing = [p for p in (claim.get('a'), claim.get('b')) if p and p not in groups]
            result['assembly'].append({'index': i, 'type': claim['type'], 'missing': missing} if missing
                                      else {'index': i, **measure_claim(claim, groups)})
    # Largest on-screen size of each feature along the shot (every 5th frame), at the project output size.
    if scene.camera and not geometry_only and shot is not None:
        width, height = output_size or (scene.render.resolution_x, scene.render.resolution_y)
        saved = (scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage)
        scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = width, height, 100
        for feature in spec['features']:
            objs = [o for part in feature['part_ids'] for o in parts.get(part, [])]
            best = 0.0
            for f in range(1, shot['duration_frames'] + 1, 5):
                scene.frame_set(f)
                xs, ys = [], []
                for obj in objs:
                    for corner in obj.bound_box:
                        ndc = world_to_camera_view(scene, scene.camera, obj.matrix_world @ Vector(corner))
                        if ndc.z > 0:
                            xs.append(ndc.x * width); ys.append(ndc.y * height)
                if xs:
                    best = max(best, max(max(xs) - min(xs), max(ys) - min(ys)))
            result['screen_px'][feature['id']] = round(best, 1)
        scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = saved
        scene.frame_set(frame)
    return result
