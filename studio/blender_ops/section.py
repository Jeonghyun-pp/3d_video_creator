"""Section staging: present an underground (or any boxed) structure as an architectural section cut.

stage(name, box, ...) dresses the structure's -Y face as the cut plane the camera looks at:
  soil      blocks around the box (sides, below) from the face back, up to the ground: the dark mass the section
            reads against (a structure floating in the void reads as a model, not a cut)
  poché     every face lying on the cut plane (slab ends, walls, base, soil) gets the poché material - the
            drafting convention that tells cut from seen
  interior  per-level ceiling area lights and LED strips across the cut, near the face (what the camera sees first);
            lights carry studio_keep_light so the photoreal look keeps them
The cut itself (ground in front of the face removed) is an ordinary reveal action with a cutter whose origin sits on
the face, so it can grow toward the camera; a camera move frames it (camera_moves_core 'section_push').
Measured 2026-10-05 (verify_b S1): light interior albedo, ~150 W ceiling lights per bay and poché 0.08-0.12 make
4-5 levels read at the aerial exposure (interior/aerial luminance 0.93-1.0).
"""
from __future__ import annotations

import bmesh
import bpy
from mathutils import Vector

ROLE = 'studio_scene_role'


def _linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _material(name, srgb, roughness=0.95, emission=None):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    lin = tuple(_linear(c) for c in srgb)
    bsdf.inputs['Base Color'].default_value = (*lin, 1.0)
    bsdf.inputs['Roughness'].default_value = roughness
    if emission:
        bsdf.inputs['Emission Color'].default_value = (*lin, 1.0)
        bsdf.inputs['Emission Strength'].default_value = emission
    mat.diffuse_color = (*lin, 1.0)
    return mat


def _soil(name, srgb):
    """Earth with horizontal strata and grain (noise on world z), so the cut reads as ground, not a dark wall."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
    coord = nt.nodes.new('ShaderNodeTexCoord')
    mapping = nt.nodes.new('ShaderNodeMapping'); mapping.inputs['Scale'].default_value = (0.15, 0.15, 1.6)   # layers: stretched in plan
    noise = nt.nodes.new('ShaderNodeTexNoise'); noise.inputs['Scale'].default_value = 1.2; noise.inputs['Detail'].default_value = 8.0
    ramp = nt.nodes.new('ShaderNodeValToRGB')
    lin = tuple(_linear(c) for c in srgb)
    ramp.color_ramp.elements[0].color = (*(c * 0.55 for c in lin), 1.0)
    ramp.color_ramp.elements[1].color = (*(min(1.0, c * 1.35) for c in lin), 1.0)
    nt.links.new(coord.outputs['Object'], mapping.inputs['Vector'])
    nt.links.new(mapping.outputs['Vector'], noise.inputs['Vector'])
    nt.links.new(noise.outputs['Fac'], ramp.inputs['Fac'])
    nt.links.new(ramp.outputs['Color'], bsdf.inputs['Base Color'])
    bsdf.inputs['Roughness'].default_value = 0.97
    mat.diffuse_color = (*lin, 1.0)
    return mat


def _block(name, lo, hi, material, role=None):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(hi) - Vector(lo), verts=bm.verts)
    bmesh.ops.translate(bm, vec=(Vector(lo) + Vector(hi)) / 2, verts=bm.verts)
    bm.to_mesh(mesh); bm.free()
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj['studio_id'] = name
    obj['studio_dim_role'] = 'none'
    if role:
        obj[ROLE] = role
    return obj


def poche(objects, face_y, material, tol=0.02):
    """Assign `material` to every polygon lying on the plane y = face_y and facing -Y (the cut faces)."""
    count = 0
    for obj in objects:
        if obj.type != 'MESH' or not obj.data.polygons:
            continue
        world = obj.matrix_world
        normal_m = world.to_3x3().inverted().transposed()
        hits = [p for p in obj.data.polygons if abs((world @ p.center).y - face_y) < tol and (normal_m @ p.normal).normalized().y < -0.9]
        if not hits:
            continue
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        if not obj.data.materials:   # slot 0 keeps the faces that are not cut
            obj.data.materials.append(None)
        if material.name not in [m.name for m in obj.data.materials if m]:
            obj.data.materials.append(material)
        index = [m.name if m else None for m in obj.data.materials].index(material.name)
        for p in hits:
            p.material_index = index
        count += len(hits)
    return count


def stage(name, box, *, ground_z=0.0, ground_thickness_m=0.4, soil_m=70.0, soil_below_m=40.0, lit_depth_m=60.0,
          ceilings=(), bay_m=9.0, light_w=90.0, light_size=(4.0, 1.2), strip_rows=(0.2, 0.4, 0.6, 0.8), strip_emission=3.0,
          poche_srgb=(0.1, 0.1, 0.105), soil_srgb=(0.46, 0.36, 0.27), kelvin=4500):
    """box: ((x0, y0, z0), (x1, y1, z1)) of the structure; its y0 face is the cut. ceilings: z of each level's
    ceiling (lights hang just below). Returns a report."""
    (x0, y0, z0), (x1, y1, z1) = box
    top = ground_z - ground_thickness_m
    poche_mat = _material(f'{name}.poche', poche_srgb)
    soil_mat = _soil(f'{name}.soil', soil_srgb)
    soil = [_block(f'{name}.soil.l', (x0 - soil_m, y0, z0 - soil_below_m), (x0, y1, top), soil_mat, 'environment_shell'),
            _block(f'{name}.soil.r', (x1, y0, z0 - soil_below_m), (x1 + soil_m, y1, top), soil_mat, 'environment_shell'),
            _block(f'{name}.soil.b', (x0, y0, z0 - soil_below_m), (x1, y1, z0), soil_mat, 'environment_shell')]
    inside = [o for o in bpy.context.scene.objects if o.type == 'MESH' and not o.hide_render and o not in soil and _overlaps(o, box)]
    cut_faces = poche(inside, y0, poche_mat)   # the structure's cut; the soil keeps its own (lighter, strata) face
    from env_materials import kelvin_rgb
    colour = kelvin_rgb(kelvin)
    data = bpy.data.lights.new(f'{name}.ceiling', 'AREA')
    data.shape, data.size, data.size_y = 'RECTANGLE', light_size[0], light_size[1]
    data.energy, data.color = light_w, colour
    lights, strips = [], []
    strip_mat = _material(f'{name}.led', tuple(min(1.0, c) for c in colour), 0.4, emission=strip_emission)
    width = x1 - x0
    for level, ceiling in enumerate(ceilings):
        y = y0 + bay_m / 2
        while y < min(y1, y0 + lit_depth_m):
            for x in (x0 + width * 0.25, x0 + width * 0.75):
                light = bpy.data.objects.new(f'{name}.light{level}.{len(lights)}', data)
                light.location = (x, y, ceiling - 0.3)
                light['studio_keep_light'] = True
                light['studio_id'] = light.name
                bpy.context.scene.collection.objects.link(light)
                lights.append(light)
            y += bay_m
        for k, share in enumerate(strip_rows):   # linear LED rows along the structure (they lead the eye into the cut)
            x = x0 + width * share
            strips.append(_block(f'{name}.led{level}.{k}', (x - 0.08, y0 + 0.5, ceiling - 0.1), (x + 0.08, min(y1, y0 + lit_depth_m), ceiling - 0.04),
                                 strip_mat, 'light_fixture'))
    return {'name': name, 'soil': [o.name for o in soil], 'poche_faces': cut_faces, 'lights': len(lights), 'light_w': light_w,
            'strips': len(strips), 'face_y': y0}


def _overlaps(obj, box):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = [min(c[i] for c in corners) for i in range(3)]
    hi = [max(c[i] for c in corners) for i in range(3)]
    return all(lo[i] <= box[1][i] + 0.01 and hi[i] >= box[0][i] - 0.01 for i in range(3))


def front_cutter(name, face_y, *, reach_m, half_w_m, z_lo, z_hi):
    """Cutter for the ground in front of the face: a box from face_y back reach_m, origin on the face, so a reveal
    scaling it in y grows the cut from the section toward the camera."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(2 * half_w_m, reach_m, z_hi - z_lo), verts=bm.verts)
    bmesh.ops.translate(bm, vec=(0, -reach_m / 2 - 0.01, (z_lo + z_hi) / 2), verts=bm.verts)   # stops 1 cm short of the face: never coplanar with it
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = (0.0, face_y, 0.0)
    bpy.context.scene.collection.objects.link(obj)
    obj['studio_id'] = name
    obj.hide_render = True
    return obj
