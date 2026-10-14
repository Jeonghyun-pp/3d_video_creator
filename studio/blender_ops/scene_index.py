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
    """{identifier: (roots, [point per frame])} in one pass over frames 1..count (resolve_ref: a group's point is its box
    centre, its roots every copy)."""
    scene = bpy.context.scene
    index = AnchorIndex()
    out = {i: ([], []) for i in identifiers}
    for f in range(count):
        scene.frame_set(f + 1)
        for identifier in identifiers:
            found = resolve_ref(identifier, index)
            if found is None:
                raise ValueError(f'CAMERA_RIG: anchor not found: {identifier}')
            out[identifier] = (found['roots'], out[identifier][1] + [tuple(found['point'])])
    return out


def resolve_group(identifier, index=None):
    """The root objects a declared id names (subject, key part, concealed part, screen subject or keep): the first of
    ids_core.spellings that exists, as one object (AnchorIndex: studio_id or name, a declared anchor's owner,
    '<id>/center') or else as a group - every object whose studio_id, studio_layout_id or name is <id>.n or <id>/part,
    reduced to the group's roots. [] when nothing is built under any spelling."""
    from ids_core import spellings
    index = index or AnchorIndex()
    for name in spellings(identifier):
        obj = index.resolve(name)[0]
        if obj is not None:
            return [obj]
        group = _group_roots(name)
        if group:
            return group
    return []


def _group_roots(name):
    """Every object tagged <name>.n or <name>/part (studio_id, studio_layout_id or name), reduced to those with no member
    above them (a member under a non-member under a member is not a second root)."""
    from ids_core import in_group
    group = [o for o in bpy.data.objects
             if any(in_group(str(v), name) for v in (o.get('studio_id'), o.get('studio_layout_id'), o.name) if v is not None)]
    members = set(group)

    def nested(o):
        o = o.parent
        while o is not None:
            if o in members:
                return True
            o = o.parent
        return False
    return [o for o in group if not nested(o)]


def world_box(objects, roots=()):
    """World axis-aligned box of the meshes among `objects` (their data bounds, as camera moves always measured); with
    no mesh, the display cubes of the empties among `roots`. None when nothing has extent."""
    corners = [o.matrix_world @ Vector(c) for o in objects if o.type == 'MESH' for c in o.bound_box]
    if not corners:
        corners = [r.matrix_world @ Vector((x * r.empty_display_size, y * r.empty_display_size, z * r.empty_display_size))
                   for r in roots if r.type == 'EMPTY' for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    if not corners:
        return None
    return (tuple(min(c[i] for c in corners) for i in range(3)), tuple(max(c[i] for c in corners) for i in range(3)))


def resolve_ref(identifier, index=None):
    """What a declared id names in the built scene, read by every camera and show path (2026-10-14: the camera path
    compared ids exactly, so a group - `engineers` built as `engineers.00..` - was "not in the scene" while the frame
    probe found it). None when nothing is built under it, else {roots, objects, box, point}:
      an object (AnchorIndex: studio_id or name)      roots [it], objects it and its descendants, its origin as the point;
      a declared anchor or '<id>/center'             a point on its owner: roots [owner], no objects, no box;
      the roots of a group (`<id>.n` copies, `<id>/part`)  their union: every root's tree, the box centre as the point
        (one root reads as an object).
    Tried in resolve_group's order over ids_core.spellings (as written, layout form, fill copies): the first spelling
    that names anything decides, with that spelling's own kind (an anchor spelling stays a point).
    box: world_box over the objects. Positions are read live (valid across frame_set)."""
    from ids_core import spellings
    if not identifier:
        return None
    index = index or AnchorIndex()
    roots = []
    for name in spellings(identifier):
        obj, point = index.resolve(name)
        if obj is not None and index.by_id.get(name) is not obj:   # a declared anchor, or '<id>/center'
            return {'roots': [obj], 'objects': [], 'box': None, 'point': point}
        roots = [obj] if obj is not None else _group_roots(name)
        if roots:
            break
    if not roots:
        return None
    objects = list(dict.fromkeys(o for r in roots for o in (r, *r.children_recursive)))
    box = world_box(objects, roots)
    if len(roots) == 1:
        point = roots[0].matrix_world.translation.copy()
    elif box is not None:
        point = (Vector(box[0]) + Vector(box[1])) / 2
    else:
        point = sum((r.matrix_world.translation for r in roots), Vector()) / len(roots)
    return {'roots': roots, 'objects': objects, 'box': box, 'point': point}


def known_ids():
    """Every id a declaration could have meant (for ids_core.suggest)."""
    out = set()
    for obj in bpy.data.objects:
        out.update(str(v) for v in (obj.get('studio_id'), obj.get('studio_layout_id')) if v is not None)
    return out
