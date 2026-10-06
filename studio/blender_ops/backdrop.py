"""shot.scene.backdrop: a still image behind everything, fixed to the camera and blurred - a generated (or any) picture
of the place around an exact Blender subject. A camera-parented card past the farthest geometry, sized to cover the
frame at the widest lens the shot uses; emission only (lights and exposure ignore it), seen by camera rays only (the
metal in front does not reflect a flat card), scene role 'atmosphere' (no bounds, clay, depth, control or raycast: it
is mood, not structure). The blur is applied to the pixels here (box blur, separable), so every renderer sees it.
"""
from pathlib import Path

import bpy
from mathutils import Vector
import numpy as np

ROLE = 'studio_scene_role'
NAME = 'StudioBackdrop'


def _blur(image, radius):
    if radius < 1:
        return
    w, h = image.size
    px = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(px)
    a = px.reshape(h, w, 4)
    r = int(radius)
    for axis in (0, 1):
        cs = np.cumsum(np.pad(a, [(r + 1, r) if i == axis else (0, 0) for i in range(3)], mode='edge'), axis=axis)
        a = (np.take(cs, range(2 * r + 1, cs.shape[axis]), axis=axis) - np.take(cs, range(0, cs.shape[axis] - 2 * r - 1), axis=axis)) / (2 * r + 1)
    image.pixels.foreach_set(a.astype(np.float32).ravel())
    image.pack()


def build(job):
    spec = ((job['shot'].get('scene') or {}).get('backdrop'))
    if not spec:
        return None
    scene, camera = bpy.context.scene, bpy.context.scene.camera
    path = Path(job['project_dir']) / spec['image']
    if not path.is_file():
        raise ValueError(f"BACKDROP: image not found: {spec['image']}")
    old = bpy.data.objects.get(NAME)
    if old is not None:
        bpy.data.objects.remove(old)
    image = bpy.data.images.load(str(path), check_existing=False)
    image.name = NAME
    _blur(image, spec.get('blur_px', 12))
    # far enough: past every mesh the camera can see at any frame (sampled)
    meshes = [o for o in scene.objects if o.type == 'MESH' and not o.hide_render and o.get(ROLE) != 'atmosphere']
    frames = range(1, scene.frame_end + 1, max(1, scene.frame_end // 12))
    far, lenses = 0.0, []
    for f in frames:
        scene.frame_set(f)
        eye = camera.matrix_world.translation
        lenses.append(camera.data.lens)
        for o in meshes:
            for c in o.bound_box:
                far = max(far, (o.matrix_world @ Vector(c) - eye).length)
    scene.frame_set(1)
    d = spec.get('distance_m') or max(1.0, 1.5 * far)
    sensor = camera.data.sensor_width if camera.data.sensor_fit != 'VERTICAL' else camera.data.sensor_height
    aspect = scene.render.resolution_x / scene.render.resolution_y
    long_half = d * sensor / (2 * min(lenses)) * 1.08
    half_w, half_h = (long_half, long_half / aspect) if aspect >= 1 else (long_half * aspect, long_half)
    img_aspect = image.size[0] / max(1, image.size[1])
    if img_aspect > half_w / half_h:      # cover the frame: grow the short side of the card to the image's aspect
        half_w = half_h * img_aspect
    else:
        half_h = half_w / img_aspect
    mesh = bpy.data.meshes.new(NAME)
    mesh.from_pydata([(-half_w, -half_h, 0), (half_w, -half_h, 0), (half_w, half_h, 0), (-half_w, half_h, 0)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new()
    for loop, co in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop.uv = co
    mat = bpy.data.materials.new(NAME)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    tex = nodes.new('ShaderNodeTexImage'); tex.image = image
    emit = nodes.new('ShaderNodeEmission'); emit.inputs['Strength'].default_value = float(spec.get('strength', 1.0))
    out = nodes.new('ShaderNodeOutputMaterial')
    links.new(tex.outputs['Color'], emit.inputs['Color']); links.new(emit.outputs['Emission'], out.inputs['Surface'])
    mesh.materials.append(mat)
    card = bpy.data.objects.new(NAME, mesh)
    scene.collection.objects.link(card)
    card.parent = camera
    card.matrix_parent_inverse.identity()
    card.location = (0, 0, -d)
    card[ROLE] = 'atmosphere'
    card['studio_id'] = NAME
    for ray in ('visible_diffuse', 'visible_glossy', 'visible_transmission', 'visible_volume_scatter', 'visible_shadow'):
        setattr(card, ray, False)
    if camera.data.clip_end < d * 1.05:
        camera.data.clip_end = d * 1.05
    return {'image': spec['image'], 'distance_m': round(d, 4), 'size_m': [round(2 * half_w, 4), round(2 * half_h, 4)], 'blur_px': spec.get('blur_px', 12)}
