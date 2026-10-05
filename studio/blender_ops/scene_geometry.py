"""Evaluated scene geometry, instances included - one place that sees what the renderer sees.

A Geometry Nodes host that outputs instances has a zero bound_box and an empty to_mesh() (measured on 5.2,
tests/studio/blender_facts_smoke.py), so code that walks objects and their meshes misses every scattered tree,
person or rock. depsgraph.object_instances lists real meshes and instances alike, each with its world matrix;
ray casts report an instance as its host object with the instance matrix. Everything that needs geometry
(clearance BVHs, depth ranges, lit bounds, clip planes) asks here, filtered by the host object.
"""
from __future__ import annotations

import numpy as np


def records(depsgraph, keep=None):
    """[(host, world_vertices (n,3), triangles (m,3), is_instance)] for every evaluated mesh piece.
    host is the original object the piece belongs to (the GN host for instances); keep(host) filters."""
    out = []
    for inst in depsgraph.object_instances:
        obj = inst.object
        if obj.type != 'MESH':
            continue
        host = (inst.parent.original if inst.is_instance and inst.parent is not None else obj.original)
        if keep is not None and not keep(host):
            continue
        mesh = obj.data
        n = len(mesh.vertices)
        if not n:
            continue
        co = np.empty(n * 3, dtype=np.float64)
        mesh.vertices.foreach_get('co', co)
        matrix = np.array(inst.matrix_world)
        world = co.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
        tris = mesh.loop_triangles
        tri = np.empty(len(tris) * 3, dtype=np.int64)
        tris.foreach_get('vertices', tri)
        out.append((host, world, tri.reshape(-1, 3), inst.is_instance))
    return out


def merged(depsgraph, keep=None):
    """(vertices (N,3), triangles (M,3)) of every kept piece in world space, or (None, None)."""
    rows = [r for r in records(depsgraph, keep) if len(r[2])]
    if not rows:
        return None, None
    offsets = np.cumsum([0] + [len(r[1]) for r in rows[:-1]])
    return np.vstack([r[1] for r in rows]), np.vstack([r[2] + o for r, o in zip(rows, offsets)])


def instance_boxes(depsgraph, keep=None):
    """[(host, lo (3,), hi (3,))] world boxes of GN instances only (real objects keep using bound_box)."""
    return [(host, world.min(axis=0), world.max(axis=0)) for host, world, _tri, is_instance in records(depsgraph, keep) if is_instance]


def is_time_dependent(obj):
    """Geometry that can change between frames without keyframes: rigid bodies, baked or live simulation
    zones and particle systems, or anything tagged simulated."""
    if getattr(obj, 'rigid_body', None) is not None or obj.get('studio_scene_role') == 'simulated':
        return True
    for modifier in getattr(obj, 'modifiers', ()):
        if modifier.type == 'PARTICLE_SYSTEM':
            return True
        if modifier.type == 'NODES' and len(getattr(modifier, 'bakes', ())):  # a simulation zone has a bake slot
            return True
    return False
