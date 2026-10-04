"""Blender-side renderer. Run only through video_pipeline.py."""

import json
import math
import os
from pathlib import Path
import sys

import bpy
from mathutils import Vector


def fail(message):
    raise RuntimeError(message)


def look_at(camera, target):
    direction = Vector(target) - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def bounds(points):
    if not points:
        fail("No mesh objects in visible collections")
    minimum = Vector(tuple(min(point[index] for point in points) for index in range(3)))
    maximum = Vector(tuple(max(point[index] for point in points) for index in range(3)))
    return (minimum + maximum) / 2, (maximum - minimum).length


def action_curves(action):
    if hasattr(action, "fcurves"):
        return action.fcurves
    return [curve for layer in action.layers for strip in layer.strips
            for bag in strip.channelbags for curve in bag.fcurves]


def preview_camera(scene, source, source_count, preview_count):
    """Sample an authored camera at the original shot times for preview frames."""
    data = source.data.copy()
    data.animation_data_clear()
    camera = bpy.data.objects.new("PipelinePreviewCamera", data)
    scene.collection.objects.link(camera)
    for frame in range(1, preview_count + 1):
        source_frame = 1 + (source_count - 1) * (frame - 1) / max(preview_count - 1, 1)
        whole = math.floor(source_frame)
        scene.frame_set(whole, subframe=source_frame - whole)
        evaluated = source.evaluated_get(bpy.context.evaluated_depsgraph_get())
        camera.matrix_world = evaluated.matrix_world.copy()
        camera.keyframe_insert(data_path="location", frame=frame)
        camera.keyframe_insert(data_path="rotation_euler", frame=frame)
        camera.keyframe_insert(data_path="scale", frame=frame)
        data.lens = evaluated.data.lens
        data.ortho_scale = evaluated.data.ortho_scale
        data.keyframe_insert(data_path="lens", frame=frame)
        data.keyframe_insert(data_path="ortho_scale", frame=frame)
    scene.camera = camera
    scene.frame_set(1)
    return camera


def main():
    if "--" not in sys.argv:
        fail("Missing arguments")
    args = sys.argv[sys.argv.index("--") + 1:]
    if len(args) != 6:
        fail("Expected scene.json shot_id output_dir width height fps")
    spec_path, shot_id, output_dir, width, height, fps = args
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    shot = next((item for item in spec["scenes"] if item["id"] == shot_id), None)
    if shot is None:
        fail(f"Unknown shot: {shot_id}")
    collections = {collection.name: collection for collection in bpy.data.collections}
    missing = set(shot["visible_objects"]) - collections.keys()
    if missing:
        fail(f"Missing Blender collections: {sorted(missing)}")
    object_ids = {item["id"] for item in spec["objects"]}
    for name in object_ids:
        if name in collections:
            collections[name].hide_render = name not in shot["visible_objects"]
    animation = shot["animation"]
    kind = animation["type"]
    ordered = [item["id"] for item in spec["objects"] if item["id"] in shot["visible_objects"]]
    points = []
    for index, name in enumerate(ordered):
        shift = Vector((0, 0, 0))
        if kind in ("exploded_view", "assemble"):
            shift["xyz".index(animation.get("axis", "z"))] = (len(ordered) - 1 - index) * float(animation.get("distance", 2.5))
        elif kind == "cutaway_reveal" and name == "street":
            shift.z = 5
        elif kind == "flow" and name == "train":
            shift.x = 2
        for obj in collections[name].all_objects:
            if obj.type != "MESH":
                continue
            for corner in obj.bound_box:
                point = obj.matrix_world @ Vector(corner)
                points.append(point)
                if shift.length:
                    points.append(point + shift)
                    if kind == "flow" and name == "train":
                        points.append(point - shift)
    scene = bpy.context.scene
    scene.render.resolution_x = int(width)
    scene.render.resolution_y = int(height)
    scene.render.resolution_percentage = 100
    scene.render.fps = int(fps)
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.filepath = str(Path(output_dir) / "######")
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "AgX"
    requested_engine = os.environ.get("STUDIO_RENDER_ENGINE")
    if requested_engine:
        scene.render.engine = requested_engine
    elif shot["camera"]["movement"] != "scene_camera":
        try:
            scene.render.engine = "BLENDER_EEVEE_NEXT"
        except TypeError:
            scene.render.engine = "BLENDER_EEVEE"
    if scene.render.engine == "CYCLES" and sys.platform == "darwin":
        try:
            preferences = bpy.context.preferences.addons["cycles"].preferences
            preferences.compute_device_type = "METAL"
            preferences.get_devices()
            scene.cycles.device = "GPU"
            print("Cycles device: Metal GPU")
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            scene.cycles.device = "CPU"
            print(f"Cycles Metal unavailable, using CPU: {error}")
    if scene.render.engine == "BLENDER_WORKBENCH":
        scene.display.shading.color_type = "MATERIAL"
        scene.display.shading.light = "STUDIO"
    scene.render.image_settings.color_depth = "8"
    count = round(float(shot["duration"]) * int(fps))
    scene.frame_start = 1
    scene.frame_end = count
    center, span = bounds(points)
    span = max(span, 5)
    camera_spec = shot["camera"]
    movement = camera_spec["movement"]
    authored_camera = None
    if movement == "scene_camera":
        camera = bpy.data.objects.get(camera_spec["camera_name"])
        if camera is None or camera.type != "CAMERA":
            fail(f"Missing Blender camera: {camera_spec['camera_name']}")
        authored_camera = camera
        scene.camera = camera
        if int(fps) != int(spec["fps"]):
            camera = preview_camera(scene, camera, round(float(shot["duration"]) * int(spec["fps"])), count)
    else:
        camera_data = bpy.data.cameras.new("PipelineCamera")
        camera = bpy.data.objects.new("PipelineCamera", camera_data)
        scene.collection.objects.link(camera)
        scene.camera = camera
        camera_data.type = "ORTHO" if camera_spec["type"] == "orthographic" else "PERSP"
        camera_data.ortho_scale = span * 1.2
        camera_data.lens = 40
    if movement == "scene_camera":
        # The named camera's authored pose, lens, animation and constraints belong to the .blend.
        pass
    else:
        camera_data = camera.data
        base = Vector((span * .9, -span * 1.25, span * .85))
        camera.location = center + base
        look_at(camera, center)
        axes = camera.rotation_euler.to_matrix()
        right = axes.col[0]
        up = axes.col[1]
        projected_width = max((point - center).dot(right) for point in points) - min((point - center).dot(right) for point in points)
        projected_height = max((point - center).dot(up) for point in points) - min((point - center).dot(up) for point in points)
        required_scale = max(projected_height, projected_width * int(height) / int(width)) * 1.18
        camera_data.ortho_scale = required_scale
        if movement == "keyframes":
            keys = camera_spec["keyframes"]
            times = [key["t"] for key in keys]
            if times[0] != 0 or times[-1] != 1 or any(b <= a for a, b in zip(times, times[1:])):
                fail("Camera keyframe t values must increase strictly from 0 to 1")
            for key in keys:
                frame = 1 + (count - 1) * key["t"]
                camera.location = Vector(key["location"])
                if (Vector(key["target"]) - camera.location).length < 1e-6:
                    fail("Camera keyframe target must differ from location")
                look_at(camera, key["target"])
                camera.keyframe_insert(data_path="location", frame=frame)
                camera.keyframe_insert(data_path="rotation_euler", frame=frame)
                if camera_data.type == "PERSP":
                    camera_data.lens = key.get("lens_mm", camera_data.lens)
                    camera_data.keyframe_insert(data_path="lens", frame=frame)
                else:
                    camera_data.ortho_scale = key.get("ortho_scale", camera_data.ortho_scale)
                    camera_data.keyframe_insert(data_path="ortho_scale", frame=frame)
        else:
            for frame, factor in ((1, 0.0), (count, 1.0)):
                if movement == "slow_dolly_in":
                    offset = base * (1.12 - .25 * factor)
                    camera_data.ortho_scale = required_scale * (1.15 - .10 * factor)
                elif movement == "slow_dolly_out":
                    offset = base * (.88 + .25 * factor)
                    camera_data.ortho_scale = required_scale * (1.05 + .10 * factor)
                elif movement == "orbit":
                    angle = math.radians(-18 + 36 * factor)
                    offset = Vector((base.x * math.cos(angle) - base.y * math.sin(angle),
                                     base.x * math.sin(angle) + base.y * math.cos(angle), base.z))
                elif movement == "tilt_down":
                    offset = Vector((base.x, base.y, base.z * (.68 + .32 * factor)))
                else:
                    offset = base
                camera.location = center + offset
                look_at(camera, center)
                camera.keyframe_insert(data_path="location", frame=frame)
                camera.keyframe_insert(data_path="rotation_euler", frame=frame)
                if camera_data.type == "ORTHO":
                    camera_data.keyframe_insert(data_path="ortho_scale", frame=frame)
    if kind in ("exploded_view", "assemble", "cutaway_reveal"):
        distance = float(animation.get("distance", 2.5))
        axis = "xyz".index(animation.get("axis", "z"))
        for index, name in enumerate(ordered):
            if kind == "cutaway_reveal" and name != "street":
                continue
            shift = (len(ordered) - 1 - index) * distance if kind != "cutaway_reveal" else distance * 2
            for obj in collections[name].all_objects:
                original = obj.location.copy()
                for frame, displaced in ((1, kind == "assemble"), (count, kind != "assemble")):
                    obj.location = original.copy()
                    if displaced:
                        obj.location[axis] += shift
                    obj.keyframe_insert(data_path="location", frame=frame)
                obj.location = original
    elif kind == "flow" and "train" in collections:
        for obj in collections["train"].all_objects:
            original = obj.location.copy()
            for frame, offset in ((1, -2), (count, 2)):
                obj.location = original + Vector((offset, 0, 0))
                obj.keyframe_insert(data_path="location", frame=frame)
            obj.location = original
    camera_actions = set()
    if camera.animation_data and camera.animation_data.action:
        camera_actions.add(camera.animation_data.action)
    if camera.data.animation_data and camera.data.animation_data.action:
        camera_actions.add(camera.data.animation_data.action)
    if authored_camera is not None:
        for animated in (authored_camera, authored_camera.data):
            if animated.animation_data and animated.animation_data.action:
                camera_actions.add(animated.animation_data.action)
    for action in bpy.data.actions:
        for curve in action_curves(action):
            for point in curve.keyframe_points:
                if action not in camera_actions:
                    point.interpolation = "LINEAR"
                elif movement != "scene_camera":
                    point.interpolation = camera_spec.get("interpolation", "LINEAR")
    bpy.ops.render.render(animation=True)


main()
