"""shot.scene.backdrop: the place around an exact Blender subject - a relation decided first, and a still image behind.

relation (docs/BACKDROP_STAGING_PLAN.md): support (built by layout.py: the bench or floor the subject stands on), view (the
camera move's elevation; lint warns on a mismatch), light (here: a key and a fill as practical lights - studio_keep_light,
so the look keeps them - on the relation's sides and colour temperatures, sized from the subject; and, with an image,
a dome of the backdrop only reflections see, so the metal carries the place). The image itself: A camera-parented card past the farthest geometry, sized to cover the
frame at the widest lens the shot uses; emission only (lights and exposure ignore it), seen by camera rays only (the
metal in front does not reflect a flat card), scene role 'atmosphere' (no bounds, clay, depth, control or raycast: it
is mood, not structure). The blur is applied to the pixels here (box blur, separable), so every renderer sees it.
"""
from pathlib import Path

import math

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


def _kelvin_rgb(kelvin):
    """Approximate blackbody colour (Tanner Helland), linear-ish 0..1 - enough for a lamp tint."""
    t = kelvin / 100.0
    r = 1.0 if t <= 66 else min(1.0, max(0.0, 329.698727446 * ((t - 60) ** -0.1332047592) / 255))
    g = min(1.0, max(0.0, (99.4708025861 * math.log(t) - 161.1195681661) / 255)) if t <= 66 else min(1.0, max(0.0, 288.1221695283 * ((t - 60) ** -0.0755148492) / 255))
    b = 1.0 if t >= 66 else (0.0 if t <= 19 else min(1.0, max(0.0, (138.5177312231 * math.log(t - 10) - 305.0447927307) / 255)))
    return (r, g, b)


def _subject_box(scene):
    corners = [o.matrix_world @ Vector(c) for o in scene.objects if o.type == 'MESH' and o.get('studio_subject_id') for c in o.bound_box]
    if not corners:
        return None
    lo = Vector([min(c[i] for c in corners) for i in range(3)]); hi = Vector([max(c[i] for c in corners) for i in range(3)])
    return (lo + hi) / 2, max((hi - lo).length / 2, 1e-3)


KEY_IRRADIANCE, FILL_IRRADIANCE = 6.0, 2.0   # ratio 3:1; the look meters the exposure, so only the ratio and colour matter


def _lights(light, camera, center, radius):
    """Key and fill as area lamps around the subject: the key on the relation's side of the camera, high and a little
    behind the subject; the fill low on the other side. Power from irradiance (P = E pi d^2), so scale does not matter."""
    scene = bpy.context.scene
    scene.frame_set(1)
    forward = (center - camera.matrix_world.translation).normalized()
    right = forward.cross(Vector((0, 0, 1))).normalized()
    side = -1.0 if light.get('key_side', 'left') == 'left' else 1.0
    made = []
    for name, sign, up, back, kelvin, irradiance in (('Key', side, 0.9, 0.4, light.get('key_kelvin', 5600), KEY_IRRADIANCE),
                                                      ('Fill', -side, 0.3, -0.4, light.get('fill_kelvin', 4000), FILL_IRRADIANCE)):
        direction = (right * sign + Vector((0, 0, up)) + forward * back).normalized()
        distance = radius * 6
        data = bpy.data.lights.new(f'{NAME}{name}', 'AREA')
        data.shape, data.size = 'DISK', radius * 2
        data.energy = irradiance * math.pi * distance ** 2
        data.color = _kelvin_rgb(kelvin)
        lamp = bpy.data.objects.new(f'{NAME}{name}', data)
        scene.collection.objects.link(lamp)
        lamp.location = center + direction * distance
        lamp.rotation_euler = (center - lamp.location).to_track_quat('-Z', 'Y').to_euler()
        lamp['studio_keep_light'] = True       # look_lighting keeps author practicals
        lamp['studio_id'] = f'{NAME}{name}'
        made.append(lamp.name)
    return made


def _reflection_dome(image, center, radius, strength):
    """A cylinder of the backdrop around the subject that only glossy and diffuse rays see: the place reflected in the
    metal and tinting the shadows, never seen directly."""
    scene = bpy.context.scene
    r, h, n = radius * 10, radius * 12, 48
    verts = [(center.x + r * math.cos(2 * math.pi * i / n), center.y + r * math.sin(2 * math.pi * i / n), center.z + z) for z in (-h / 4, h * 3 / 4) for i in range(n)]
    faces = [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    mesh = bpy.data.meshes.new(f'{NAME}Dome')
    mesh.from_pydata(verts, [], faces)
    uv = mesh.uv_layers.new()   # u runs 0..2 around (the image and its mirror: MIRROR extension closes the seam), v bottom..top
    for k, poly in enumerate(mesh.polygons):
        for li, (column, row) in zip(poly.loop_indices, ((k, 0), (k + 1, 0), (k + 1, 1), (k, 1))):
            uv.data[li].uv = (2 * column / n, row)
    mat = bpy.data.materials.new(f'{NAME}Dome')
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    tex = nodes.new('ShaderNodeTexImage'); tex.image = image; tex.extension = 'MIRROR'
    emit = nodes.new('ShaderNodeEmission'); emit.inputs['Strength'].default_value = strength
    out = nodes.new('ShaderNodeOutputMaterial')
    links.new(tex.outputs['Color'], emit.inputs['Color']); links.new(emit.outputs['Emission'], out.inputs['Surface'])
    mesh.materials.append(mat)
    dome = bpy.data.objects.new(f'{NAME}Dome', mesh)
    scene.collection.objects.link(dome)
    dome[ROLE] = 'atmosphere'
    dome['studio_id'] = f'{NAME}Dome'
    dome.visible_camera = False
    dome.visible_shadow = False
    dome.visible_transmission = False
    return dome.name


def build(job):
    spec = ((job['shot'].get('scene') or {}).get('backdrop'))
    if not spec:
        return None
    scene, camera = bpy.context.scene, bpy.context.scene.camera
    relation = spec.get('relation') or {}
    report = {}
    box = _subject_box(scene)
    if relation.get('light') and box:
        report['lights'] = _lights(relation['light'], camera, *box)
    if not spec.get('image'):
        return report
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
    if relation.get('light', {}).get('reflections', True) and relation and box:
        report['reflections'] = _reflection_dome(image, *box, float(spec.get('strength', 1.0)) * 0.6)
    return {**report, 'image': spec['image'], 'distance_m': round(d, 4), 'size_m': [round(2 * half_w, 4), round(2 * half_h, 4)], 'blur_px': spec.get('blur_px', 12)}
