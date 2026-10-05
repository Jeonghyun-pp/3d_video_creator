"""Anchor lookups without scanning every object on every frame.

scene_tools.anchor_for (frozen: part of the render fingerprint) scans bpy.data.objects and parses every
studio_anchors JSON per call; per-frame samplers call it frames x anchors times. AnchorIndex builds the
same lookup once and resolves exactly like anchor_for: first object in bpy.data.objects order whose
studio_id or name matches, else the first scene object declaring the id in studio_anchors, else '<id>/center'
(bbox centre of a mesh, origin otherwise). Positions are read live, so it stays valid across frame_set;
rebuild it after creating or renaming objects.
"""
from __future__ import annotations

import json

import bpy
from mathutils import Vector


class AnchorIndex:
    def __init__(self):
        self.by_id = {}
        for obj in bpy.data.objects:
            for key in (obj.get('studio_id'), obj.name):
                if key is not None:
                    self.by_id.setdefault(key, obj)
        self.declared = {}
        for obj in bpy.context.scene.objects:
            raw = obj.get('studio_anchors')
            anchors = json.loads(raw) if isinstance(raw, str) else {}
            for key, local in anchors.items():
                self.declared.setdefault(key, (obj, Vector(local)))

    def resolve(self, identifier):
        """(object, world point) or (None, None), as scene_tools.anchor_for at the current frame."""
        obj = self.by_id.get(identifier)
        if obj is not None:
            return obj, obj.matrix_world.translation.copy()
        if identifier in self.declared:
            obj, local = self.declared[identifier]
            return obj, obj.matrix_world @ local
        if identifier.endswith('/center'):
            obj = self.by_id.get(identifier[:-7])
            if obj is not None:
                center = sum((Vector(c) for c in obj.bound_box), Vector()) / 8 if obj.type == 'MESH' else Vector()
                return obj, obj.matrix_world @ center
        return None, None


def sample_many(identifiers, count):
    """{identifier: (owner, [point per frame])} in one pass over frames 1..count."""
    scene = bpy.context.scene
    index = AnchorIndex()
    out = {i: (None, []) for i in identifiers}
    for f in range(count):
        scene.frame_set(f + 1)
        for identifier in identifiers:
            obj, point = index.resolve(identifier)
            if obj is None:
                raise ValueError(f'CAMERA_RIG: anchor not found: {identifier}')
            out[identifier] = (obj, out[identifier][1] + [tuple(point)])
    return out
