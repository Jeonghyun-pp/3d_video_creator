"""Hybrid control pass (runs inside Blender): depth + clay PNG sequences of the complete motion pass.

Job JSON (after `--`): {output_dir, frame_count, width, height, fps, passes: ['depth', 'clay'], labels}.
Writes <output_dir>/depth/frame_NNNNNN.png, clay/frame_NNNNNN.png, control_meta.json, anchors.json.

Depth: Emission(MapRange(Camera Data.View Distance, near->1, far->0, clamped)); near/far are FIXED for the
whole shot (computed once over every frame), so brightness never pumps when the camera moves. The value
is linearised before the Standard view transform, so PNG value = (far - d) / (far - near).
Clay: view-layer override with a matte grey Principled BSDF under a fixed camera-relative two-sun rig
(scene lights hidden, world visible to camera only). The look never leaks into the control, and the
direct-only noise-free lighting keeps canny edges stable at low samples.
"""
import json
import math
from pathlib import Path
import sys
import bpy
from mathutils import Euler, Vector

sys.path.insert(0, str(Path(__file__).parent))
from scene_tools import anchors_for_frame
from scene_roles import counts
from scene_geometry import instance_boxes
from bpy_extras.object_utils import world_to_camera_view

job = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text())
scene = bpy.context.scene
out = Path(job['output_dir'])
frames = range(job['frame_count'])
DEPTH_SAMPLES, CLAY_SAMPLES = 4, 8   # within the 1-8 contract; both passes are noise-free by construction
GRID = 3                              # bound-box lattice points per axis used for near/far


def visible_meshes():
    return [o for o in scene.objects if o.type in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT'} and not o.hide_render]


def near_far():
    """Min/max camera distance over all frames of bound-box lattice points inside the camera frustum."""
    near, far = math.inf, 0.0
    steps = [i / (GRID - 1) for i in range(GRID)]
    for frame in frames:
        scene.frame_set(frame + 1)
        cam = scene.camera
        origin = cam.matrix_world.translation
        start, end = cam.data.clip_start, cam.data.clip_end
        for obj in visible_meshes():
            if obj.hide_render or not counts(obj, 'depth_range'):   # keyframed visibility; shells and volumes
                continue
            box = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
            lo = Vector((min(p.x for p in box), min(p.y for p in box), min(p.z for p in box)))
            hi = Vector((max(p.x for p in box), max(p.y for p in box), max(p.z for p in box)))
            for a in steps:
                for b in steps:
                    for c in steps:
                        point = Vector((lo.x + (hi.x - lo.x) * a, lo.y + (hi.y - lo.y) * b, lo.z + (hi.z - lo.z) * c))
                        ndc = world_to_camera_view(scene, cam, point)
                        if start <= ndc.z <= end and -0.05 <= ndc.x <= 1.05 and -0.05 <= ndc.y <= 1.05:
                            distance = (point - origin).length
                            near, far = min(near, distance), max(far, distance)
        # Geometry Nodes instances: their host's bound_box is empty, so measure the instances themselves
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for _host, lo, hi in instance_boxes(depsgraph, keep=lambda host: counts(host, 'depth_range')):
            for a in steps:
                for b in steps:
                    for c in steps:
                        point = Vector((lo[0] + (hi[0] - lo[0]) * a, lo[1] + (hi[1] - lo[1]) * b, lo[2] + (hi[2] - lo[2]) * c))
                        ndc = world_to_camera_view(scene, cam, point)
                        if start <= ndc.z <= end and -0.05 <= ndc.x <= 1.05 and -0.05 <= ndc.y <= 1.05:
                            distance = (point - origin).length
                            near, far = min(near, distance), max(far, distance)
    if not math.isfinite(near):
        near, far = scene.camera.data.clip_start, scene.camera.data.clip_end
    return near, max(far, near + 1e-3)


def base_settings(samples):
    r = scene.render
    r.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = samples
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.use_denoising = False
    scene.cycles.seed = 0
    scene.cycles.use_animated_seed = False
    scene.cycles.max_bounces = 0
    r.resolution_x, r.resolution_y, r.resolution_percentage = job['width'], job['height'], 100
    r.fps, r.fps_base = job['fps'], 1
    r.use_motion_blur = False
    r.film_transparent = False
    r.use_compositing = False
    r.use_sequencer = False
    r.use_persistent_data = True
    if hasattr(scene, 'compositing_node_group'):
        scene.compositing_node_group = None
    elif hasattr(scene, 'use_nodes'):
        scene.use_nodes = False
    vs = scene.view_settings
    scene.display_settings.display_device = 'sRGB'
    vs.view_transform = 'Standard'
    vs.look = 'None'
    vs.exposure, vs.gamma = 0.0, 1.0
    vs.use_curve_mapping = False
    for obj in scene.objects:
        if obj.type == 'CAMERA':
            obj.data.dof.use_dof = False


def world(color, camera_only=False):
    w = bpy.data.worlds.new('StudioControlWorld')
    w.use_nodes = True
    nodes, links = w.node_tree.nodes, w.node_tree.links
    background = nodes.get('Background')
    background.inputs['Color'].default_value = (*color, 1)
    background.inputs['Strength'].default_value = 1.0
    if camera_only:   # visible backdrop, but never lights the clay (no world sampling noise)
        path, black, mix = nodes.new('ShaderNodeLightPath'), nodes.new('ShaderNodeBackground'), nodes.new('ShaderNodeMixShader')
        black.inputs['Strength'].default_value = 0.0
        output = nodes.get('World Output')
        links.new(path.outputs['Is Camera Ray'], mix.inputs['Fac'])
        links.new(black.outputs['Background'], mix.inputs[1])
        links.new(background.outputs['Background'], mix.inputs[2])
        links.new(mix.outputs['Shader'], output.inputs['Surface'])
    scene.world = w


def depth_material(near, far):
    m = bpy.data.materials.new('StudioControlDepth')
    m.use_nodes = True
    nodes, links = m.node_tree.nodes, m.node_tree.links
    nodes.clear()
    camera = nodes.new('ShaderNodeCameraData')
    remap = nodes.new('ShaderNodeMapRange')
    remap.clamp = True
    remap.inputs['From Min'].default_value, remap.inputs['From Max'].default_value = near, far
    remap.inputs['To Min'].default_value, remap.inputs['To Max'].default_value = 1.0, 0.0
    # Inverse sRGB OETF ((x+.055)/1.055)^2.4 so the Standard view writes the linear fraction.
    add, div, power = (nodes.new('ShaderNodeMath') for _ in range(3))
    add.operation, div.operation, power.operation = 'ADD', 'DIVIDE', 'POWER'
    add.inputs[1].default_value, div.inputs[1].default_value, power.inputs[1].default_value = 0.055, 1.055, 2.4
    emission, output = nodes.new('ShaderNodeEmission'), nodes.new('ShaderNodeOutputMaterial')
    emission.inputs['Strength'].default_value = 1.0
    links.new(camera.outputs['View Distance'], remap.inputs['Value'])
    links.new(remap.outputs['Result'], add.inputs[0])
    links.new(add.outputs['Value'], div.inputs[0])
    links.new(div.outputs['Value'], power.inputs[0])
    links.new(power.outputs['Value'], emission.inputs['Color'])
    links.new(emission.outputs['Emission'], output.inputs['Surface'])
    return m


def clay_material():
    m = bpy.data.materials.new('StudioControlClay')
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = (0.5, 0.5, 0.5, 1)
    bsdf.inputs['Roughness'].default_value = 1.0
    bsdf.inputs['Metallic'].default_value = 0.0
    for name in ('Specular IOR Level', 'Coat Weight', 'Sheen Weight', 'Emission Strength', 'Transmission Weight'):
        if name in bsdf.inputs:
            bsdf.inputs[name].default_value = 0.0
    return m


def hide_non_control():
    """Objects scene_roles keeps out of control passes (volumes, helpers) render nothing here."""
    for obj in scene.objects:
        if obj.type in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'VOLUME', 'GREASEPENCIL'} and not counts(obj, 'control'):
            obj.hide_render = True
            if obj.animation_data:
                obj.animation_data.action = None


def clay_lights():
    for obj in scene.objects:
        if obj.type == 'LIGHT':
            obj.hide_render = True
            if obj.animation_data:   # keyed visibility must not switch scene lights back on
                obj.animation_data.action = None
    for name, energy, rotation in (('StudioControlKey', 3.0, (math.radians(-35), math.radians(-40), 0)),
                                   ('StudioControlFill', 0.8, (math.radians(25), math.radians(50), 0))):
        light = bpy.data.objects.new(name, bpy.data.lights.new(name, 'SUN'))
        light.data.energy, light.data.angle = energy, 0.0
        # Shadowless: clay shades by surface orientation only. With shadows, slabs and ceilings blacked out
        # every interior (a station section's control clay measured 2 % edge coverage), handing the model nothing.
        light.data.use_shadow = False
        scene.collection.objects.link(light)
        light.parent = scene.camera
        light.matrix_parent_inverse.identity()
        light.location = (0, 0, 0)
        light.rotation_euler = Euler(rotation)


EXTRA = ('normal', 'lines', 'id')   # geometric signals that ride along with the clay render (no extra render)
LINE_PX_AT_640 = 1.0                # Freestyle line width, scaled with the frame height


def extra_passes(wanted):
    """Normal, object-index and Freestyle line passes on the clay render, written as float EXR per frame by a
    compositor File Output node (the combined image - the clay - is untouched: lines are a render pass only)."""
    layer = bpy.context.view_layer
    layer.use_pass_normal = 'normal' in wanted
    layer.use_pass_object_index = 'id' in wanted
    if 'id' in wanted:  # deterministic object indices: sorted by studio id / name
        meshes = sorted((o for o in scene.objects if o.type == 'MESH'), key=lambda o: (str(o.get('studio_id') or ''), o.name))
        for i, obj in enumerate(meshes):
            obj.pass_index = i + 1
    if 'lines' in wanted:
        scene.render.use_freestyle = True
        layer.use_freestyle = True
        layer.freestyle_settings.as_render_pass = True
        scene.render.line_thickness_mode = 'ABSOLUTE'
        scene.render.line_thickness = LINE_PX_AT_640 * job['height'] / 640
        sets = layer.freestyle_settings.linesets
        lineset = sets[0] if len(sets) else sets.new('StudioControlLines')
        lineset.select_silhouette = lineset.select_border = lineset.select_crease = True
    tree = bpy.data.node_groups.new('StudioControlPasses', 'CompositorNodeTree')
    scene.compositing_node_group = tree
    scene.render.use_compositing = True
    layers = tree.nodes.new('CompositorNodeRLayers')
    node = tree.nodes.new('CompositorNodeOutputFile')
    node.directory = str(out / '_exr') + '/'
    node.format.media_type = 'IMAGE'
    node.format.file_format, node.format.color_depth = 'OPEN_EXR', '32'
    for kind, socket, kind_type in (('normal', 'Normal', 'VECTOR'), ('id', 'Object Index', 'FLOAT'), ('lines', 'Freestyle', 'RGBA')):
        if kind in wanted:
            item = node.file_output_items.new(kind_type, kind)
            tree.links.new(layers.outputs[socket], node.inputs[item.name])
    return node


def _load(path):
    import numpy as np
    image = bpy.data.images.load(str(path))
    image.colorspace_settings.name = 'Non-Color'
    w, h = image.size
    px = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(px)
    bpy.data.images.remove(image)
    return px.reshape(h, w, 4)


def _save_png(rgb, path):
    import numpy as np
    h, w, _ = rgb.shape
    image = bpy.data.images.new('StudioControlOut', w, h, alpha=False)
    image.colorspace_settings.name = 'Non-Color'
    image.pixels.foreach_set(np.ascontiguousarray(np.dstack([rgb, np.ones((h, w))]), dtype=np.float32).ravel())
    image.filepath_raw, image.file_format = str(path), 'PNG'
    image.save()
    bpy.data.images.remove(image)


def convert_extras(wanted):
    """EXR passes -> 8-bit PNG frames: normals in camera space (RGB = xyz/2 + 0.5, the normal-map convention),
    lines white on black, ids as a fixed palette (index -> colour from sha256, stable across shots)."""
    import hashlib
    import numpy as np
    palette = {}
    for kind in wanted:
        (out / kind).mkdir(parents=True, exist_ok=True)
    for frame in frames:
        scene.frame_set(frame + 1)
        if 'normal' in wanted:
            n = _load(out / '_exr' / f'f{frame:06d}_normal.exr')[..., :3]
            to_camera = np.array(scene.camera.matrix_world.to_3x3().inverted())
            _save_png(np.clip(n @ to_camera.T * 0.5 + 0.5, 0, 1) * (np.linalg.norm(n, axis=2, keepdims=True) > 0.5), out / 'normal' / f'frame_{frame:06d}.png')
        if 'lines' in wanted:
            a = _load(out / '_exr' / f'f{frame:06d}_lines.exr')[..., 3:4]
            _save_png(np.repeat(np.clip(a, 0, 1), 3, axis=2), out / 'lines' / f'frame_{frame:06d}.png')
        if 'id' in wanted:
            idx = np.rint(_load(out / '_exr' / f'f{frame:06d}_id.exr')[..., 0]).astype(np.int64)
            for value in np.unique(idx):
                if value not in palette:
                    digest = hashlib.sha256(f'id:{value}'.encode()).digest()
                    palette[value] = (0.0, 0.0, 0.0) if value == 0 else tuple(0.15 + 0.85 * b / 255 for b in digest[:3])
            lut = np.zeros((idx.max() + 1, 3))
            for value, colour in palette.items():
                if value <= idx.max():
                    lut[value] = colour
            _save_png(lut[idx], out / 'id' / f'frame_{frame:06d}.png')
    import shutil
    shutil.rmtree(out / '_exr', ignore_errors=True)


def render(kind, samples, depth, extras=()):
    base_settings(samples)
    target = out / kind
    target.mkdir(parents=True, exist_ok=True)
    settings = scene.render.image_settings
    settings.file_format, settings.color_mode, settings.color_depth = 'PNG', 'BW', depth
    node = extra_passes(extras) if extras else None
    for frame in frames:
        scene.frame_set(frame + 1)
        if node is not None:
            node.file_name = f'f{frame:06d}_'
        scene.render.filepath = str(target / f'frame_{frame:06d}.png')
        bpy.ops.render.render(write_still=True)
    if extras:
        convert_extras(extras)


if not scene.camera:
    raise ValueError('Scene has no active camera')
rows = []
for frame in frames:
    scene.frame_set(frame + 1)
    rows.extend(anchors_for_frame(job['labels'], frame))
(out / 'anchors.json').write_text(json.dumps({'schema_version': 1, 'frames': rows}, ensure_ascii=False))
near, far = near_far()
hide_non_control()  # after anchors (labels may sit on anything) and near/far
layer = bpy.context.view_layer
if 'depth' in job['passes']:
    world((0, 0, 0))
    layer.material_override = depth_material(near, far)
    render('depth', DEPTH_SAMPLES, '16')
extras = [k for k in EXTRA if k in job['passes']]
if 'clay' in job['passes'] or extras:
    world((0.02, 0.02, 0.02), camera_only=True)
    layer.material_override = clay_material()
    clay_lights()
    render('clay', CLAY_SAMPLES, '8', extras)
(out / 'control_meta.json').write_text(json.dumps({'near': near, 'far': far, 'frames': job['frame_count'], 'passes': job['passes'],
                                                   'width': job['width'], 'height': job['height'], 'blender_version': bpy.app.version_string,
                                                   'depth_encoding': 'linear (far-d)/(far-near), white=near, 16-bit grey PNG'}, indent=2))
