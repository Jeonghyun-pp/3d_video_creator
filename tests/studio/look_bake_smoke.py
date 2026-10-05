"""Run inside Blender. Opt-in texture bake: what is baked, what is refused, LOD pinned, exact values, revert.

Checks: a distant static object with a procedural (studio_lod) material is baked into packed colour / metallic /
roughness / normal images on one plain material; a near object, a reveal-cut object and an emissive material are
skipped with their reason; the baked colour equals the material's Base Color input (emission-routed bake: exact,
unlit); the View Distance LOD is pinned to the closest approach (the baked roughness is the value at that
distance, not at the camera's current frame); revert restores the procedural material and drops the bake data.
"""
from pathlib import Path
import json
import sys
import bpy
from mathutils import Vector
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'studio/blender_ops'))
from look_bake import apply_bake, revert_bake, UV, PREFIX

bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 20


def procedural(name, color, emission=0.0):
    """A catalog-like material: roughness = View Distance / 100 (an LOD that depends on the camera)."""
    m = bpy.data.materials.new(name); m.use_nodes = True; m['studio_lod'] = '{}'
    nt = m.node_tree; bsdf = nt.nodes['Principled BSDF']
    bsdf.inputs['Base Color'].default_value = (*color, 1.0)
    bsdf.inputs['Emission Strength'].default_value = emission
    cam = nt.nodes.new('ShaderNodeCameraData'); div = nt.nodes.new('ShaderNodeMath'); div.operation = 'DIVIDE'
    div.inputs[1].default_value = 100.0
    nt.links.new(cam.outputs['View Distance'], div.inputs[0]); nt.links.new(div.outputs[0], bsdf.inputs['Roughness'])
    return m


def plane(name, loc, material):
    bpy.ops.mesh.primitive_plane_add(size=2, location=loc); o = bpy.context.object; o.name = name
    o.data.materials.append(material)
    return o


far = plane('far', (0, 40, 0), procedural('far_mat', (0.2, 0.5, 0.8)))
near = plane('near', (0, 3, 0), procedural('near_mat', (0.8, 0.2, 0.2)))
lamp = plane('lamp', (6, 40, 0), procedural('lamp_mat', (1, 1, 1), emission=5.0))
cut = plane('cut', (-6, 40, 0), procedural('cut_mat', (0.5, 0.5, 0.5)))
cut.modifiers.new('StudioReveal_x', 'BOOLEAN')
cam = bpy.data.objects.new('cam', bpy.data.cameras.new('cam')); scene.collection.objects.link(cam); scene.camera = cam
cam.location = (0, 0, 2); cam.keyframe_insert('location', frame=1)
cam.location = (0, 10, 2); cam.keyframe_insert('location', frame=20)  # closest approach to 'far' ~ 29 m
scene.frame_set(20)  # the bake must not use the camera's current position (30.0 vs 10.0 m away)

report = apply_bake(scene, {'min_distance_m': 20, 'resolution_px': 64, 'samples': 1})
checks = []
assert report['baked'] == ['far'], report
assert report['skipped'] == {'near': 1, 'cut': 1, 'material': 1, 'role': 1}, report['skipped']  # role: the camera
checks += ['distant_static_procedural_baked', 'near_cut_emissive_skipped']

mat = far.material_slots[0].material
images = {n.image.name.rsplit('.', 1)[-1]: n.image for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE'}
assert mat.name == PREFIX + 'far' and sorted(images) == ['color', 'metallic', 'normal', 'roughness'], (mat.name, sorted(images))
assert all(i.packed_file is not None for i in images.values()) and UV in far.data.uv_layers
checks.append('four_packed_maps_one_material')


def centre(image):
    w, h = image.size
    i = (h // 2 * w + w // 2) * 4
    return list(image.pixels[i:i + 3])


def to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


color = [to_linear(c) for c in centre(images['color'])]
assert all(abs(a - b) < 0.02 for a, b in zip(color, (0.2, 0.5, 0.8))), color
checks.append('colour_exact_unlit')
rough = centre(images['roughness'])[0]
closest = (Vector((0, 39, 0)) - Vector((0, 10, 2))).length  # camera path end -> nearest bound-box point: 29.07 m
assert abs(rough - closest / 100) < 0.01, (rough, closest)   # not 0.30 (the camera at the current frame)
assert scene.frame_current == 20 and scene.render.engine != 'CYCLES', 'render settings and frame restored'
checks.append('lod_pinned_to_closest_approach')

restored = revert_bake(scene)
assert restored['restored'] == ['far'] and far.material_slots[0].material.name == 'far_mat' and UV not in far.data.uv_layers
assert not [m for m in bpy.data.materials if m.name.startswith(PREFIX)] and not [i for i in bpy.data.images if i.name.startswith(PREFIX)]
checks.append('revert_restores_procedural')
print('STUDIO_LOOK_BAKE_SMOKE ' + json.dumps({'ok': True, 'checks': checks, 'roughness_at_closest': round(rough, 3)}))
