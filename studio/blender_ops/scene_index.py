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
    from ids_core import linked   # a world element named by the shot: what its links build, all of it
    return list(dict.fromkeys(r for i in linked(identifier) for r in resolve_group(i, index)))


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


APART_WINDOW = 'studio_apart_window'   # [from, until) scene frames an object is taken apart (cheat_scene explode_apart)


def apart(obj, frame=None):
    """The object (or what carries it) is taken apart at this scene frame (default: the current one)."""
    frame = bpy.context.scene.frame_current if frame is None else frame
    o = obj
    while o is not None:
        held = o.get(APART_WINDOW)
        if held is not None and any(held[i] <= frame < held[i + 1] for i in range(0, len(held) - 1, 2)):
            return True
        o = o.parent
    return False


def world_box(objects, roots=(), set_aside=True):
    """World axis-aligned box of the meshes among `objects` (their data bounds, as camera moves always measured); with
    no mesh, the display cubes of the empties among `roots`. None when nothing has extent. A part taken apart at this
    frame (apart) is set aside for the moment, not part of what is framed - unless nothing else is left; set_aside=False
    keeps it (what a camera follows moves with the subject, not with a part taken apart and back)."""
    meshes = [o for o in objects if o.type == 'MESH']
    meshes = ([o for o in meshes if not apart(o)] or meshes) if set_aside else meshes
    corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
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
    if not roots:   # a world element named by the shot: the roots its links build (ids_core.linked)
        from ids_core import linked
        roots = list(dict.fromkeys(r for i in linked(identifier) for r in resolve_group(i, index)))
    if not roots:
        return None
    objects = list(dict.fromkeys(o for r in roots for o in (r, *r.children_recursive)))
    box = world_box(objects, roots)
    whole = world_box(objects, roots, set_aside=False)   # the point a camera follows: never jumps when a part is set aside
    if len(roots) == 1:
        point = roots[0].matrix_world.translation.copy()
    elif whole is not None:
        point = (Vector(whole[0]) + Vector(whole[1])) / 2
    else:
        point = sum((r.matrix_world.translation for r in roots), Vector()) / len(roots)
    return {'roots': roots, 'objects': objects, 'box': box, 'point': point}


def scattered(identifier, index=None):
    """The members of a declared group that stand apart (frame_probe_core.stand_apart), else []: the group's roots when
    it has several (`<id>.n` copies, `<id>/piece` objects), or the members under one root object - its own mesh, if it
    has one, among them. A frame that shows one of them shows the group (frame_probe_core.best_piece). Why (2026-10-20,
    canal): twelve valves along a 970 m lock were measured as one part; no frame could hold enough of it to read."""
    from frame_probe_core import stand_apart
    from ids_core import in_group
    roots = resolve_group(identifier, index)
    if len(roots) == 1:
        root = roots[0]
        named = lambda o: any(in_group(str(v), identifier) for v in (o.get('studio_id'), o.get('studio_layout_id'), o.name)  # noqa: E731
                              if v is not None)
        members = [c for c in root.children if named(c)]
        trees = [[m, *m.children_recursive] for m in members]
        if root.type == 'MESH' and len(root.data.polygons):
            members, trees = [root, *members], [[root], *trees]
    else:
        members, trees = roots, [[r, *r.children_recursive] for r in roots]
    boxes = [world_box(tree, [m]) for m, tree in zip(members, trees)]
    if len(members) < 2 or any(b is None for b in boxes):
        return []
    return members if stand_apart(boxes) else []


def known_ids():
    """Every id a declaration could have meant (for ids_core.suggest)."""
    out = set()
    for obj in bpy.data.objects:
        out.update(str(v) for v in (obj.get('studio_id'), obj.get('studio_layout_id')) if v is not None)
    return out
