"""Build the seven-second DDP-style space-frame flythrough in Blender.

Run: blender -b -t 4 --python scripts/create_truss_flythrough.py -- PROJECT_DIR [--neutral|--tuned]
The output .blend contains an animated camera and can be rendered again without
the generator.  This is a visual reconstruction, not a surveyed DDP model.
"""

import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


OUT = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
TUNED_LOOK = "--tuned" in sys.argv
NEUTRAL_LOOK = "--neutral" in sys.argv or TUNED_LOOK
FRAMES = 210
FPS = 30
XS = (-4.0, -2.0, 0.0, 2.0, 4.0, 6.0)
ZS = (-2.8, 0.0, 2.8, 5.6)
YS = tuple(-12.0 + n * 3.2 for n in range(20))


def material(name, color, metallic=0.0, roughness=0.5, emission=0.0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*color, 1)
    shader.inputs["Metallic"].default_value = metallic
    shader.inputs["Roughness"].default_value = roughness
    if emission:
        shader.inputs["Emission Color"].default_value = (*color, 1)
        shader.inputs["Emission Strength"].default_value = emission
    return mat


def rods(name, segments, radius, mat, sides=12):
    """One mesh for many pipes; a few hundred Blender objects render slower."""
    verts, faces = [], []
    for a, b in segments:
        a, b = Vector(a), Vector(b)
        direction = (b - a).normalized()
        side = direction.cross(Vector((0, 0, 1)))
        if side.length < 0.01:
            side = direction.cross(Vector((0, 1, 0)))
        side.normalize()
        other = direction.cross(side).normalized()
        base = len(verts)
        for point in (a, b):
            for k in range(sides):
                angle = 2 * math.pi * k / sides
                verts.append(point + radius * (side * math.cos(angle) + other * math.sin(angle)))
        for k in range(sides):
            following = (k + 1) % sides
            faces.append((base + k, base + following, base + sides + following, base + sides + k))
        faces.extend((tuple(base + k for k in reversed(range(sides))),
                      tuple(base + sides + k for k in range(sides))))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    mesh.materials.append(mat)
    for poly in mesh.polygons:
        poly.use_smooth = len(poly.vertices) == 4
    return obj


def area(name, location, target, power, size, color):
    data = bpy.data.lights.new(name, "AREA")
    data.energy = power
    data.shape = "RECTANGLE"
    data.size = size[0]
    data.size_y = size[1]
    data.color = color
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def camera_pose(t):
    """Smooth forward acceleration, human-operated lateral drift, slight look-up."""
    y = -10.0 + 47.5 * (0.72 * t + 0.28 * t * t)
    x = 0.52 + 0.72 * t + 0.15 * math.sin(t * 5.2)
    z = 0.94 + 0.23 * math.sin(t * 4.8 + 0.4)
    position = Vector((x, y, z))
    target = Vector((0.85 + 2.2 * t + 0.35 * math.sin(t * 4.2 + 0.6), y + 7.6,
                     1.85 + 0.38 * math.sin(t * 4.4)))
    return position, (target - position).to_track_quat("-Z", "Y").to_euler()


def warped(point):
    """Small bay-to-bay irregularity avoids a perfect infinite grid."""
    x, y, z = point
    return (x + 0.37 * math.sin(y * 0.27 + z * 0.75),
            y + 0.12 * math.sin(x * 1.3 + z),
            z + 0.28 * math.sin(y * 0.24 + x * 0.8))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.context.collection.name = "truss"
    steel = material("Pale satin structural steel", (0.42, 0.44, 0.46) if TUNED_LOOK else
                     (0.47, 0.48, 0.49) if NEUTRAL_LOOK else (0.53, 0.59, 0.62),
                     0.48 if TUNED_LOOK else 0.42 if NEUTRAL_LOOK else 0.63,
                     0.46 if TUNED_LOOK else 0.43 if NEUTRAL_LOOK else 0.32)
    fittings = material("Brighter ball-joint housings", (0.51, 0.53, 0.55) if TUNED_LOOK else
                        (0.55, 0.56, 0.57) if NEUTRAL_LOOK else (0.64, 0.69, 0.71),
                        0.52 if NEUTRAL_LOOK else 0.76, 0.43 if TUNED_LOOK else 0.39 if NEUTRAL_LOOK else 0.27)
    fine = material("Secondary steel rods", (0.30, 0.32, 0.34) if TUNED_LOOK else
                    (0.34, 0.35, 0.36) if NEUTRAL_LOOK else (0.32, 0.38, 0.42),
                    0.43 if NEUTRAL_LOOK else 0.58, 0.55 if TUNED_LOOK else 0.51 if NEUTRAL_LOOK else 0.43)
    red = material("Engineering red guide wires", (0.42, 0.015, 0.04), 0.15, 0.35, 0.15)
    dark = material("Deep blue-black ground", (0.045, 0.050, 0.055) if TUNED_LOOK else
                    (0.068, 0.071, 0.075) if NEUTRAL_LOOK else (0.013, 0.020, 0.028),
                    0.08, 0.85)

    principal, secondary, red_lines = [], [], []
    for iy, y in enumerate(YS):
        for x in XS:
            for z in ZS:
                if x != XS[-1]:
                    principal.append(((x, y, z), (x + 2.0, y, z)))
                if z != ZS[-1]:
                    principal.append(((x, y, z), (x, y, z + 2.8)))
                if iy < len(YS) - 1:
                    principal.append(((x, y, z), (x, YS[iy + 1], z)))
        # Vary the diagonal direction on each transverse plane. The central
        # cell at x=0..2, z=0..2.8 remains clear for the moving camera.
        for xi in (0, 1, 3, 4):
            for zi in range(len(ZS) - 1):
                a, b = XS[xi:xi + 2]
                low, high = ZS[zi:zi + 2]
                secondary.append(((a, y, low if (iy + zi) % 2 else high),
                                  (b, y, high if (iy + zi) % 2 else low)))
        if iy < len(YS) - 1:
            next_y = YS[iy + 1]
            for x in (-4.0, 0.0, 2.0, 6.0):
                for zi in range(len(ZS) - 1):
                    a, b = ZS[zi:zi + 2]
                    secondary.append(((x, y, a if iy % 2 else b),
                                      (x, next_y, b if iy % 2 else a)))
            for z in (ZS[0], ZS[1], ZS[2], ZS[-1]):
                for xi in range(len(XS) - 1):
                    a, b = XS[xi:xi + 2]
                    secondary.append(((a if iy % 2 else b, y, z),
                                      (b if iy % 2 else a, next_y, z)))
            if iy % 3 == 0:
                red_lines.extend((((-4.0, y, 4.85), (-4.0, next_y, 3.0)),
                                  ((6.0, y, 2.2), (6.0, next_y, 4.0))))
    solid_segments = [(Vector(warped(a)), Vector(warped(b))) for a, b in principal + secondary]
    closest = float("inf")
    for frame in range(1, FRAMES + 1):
        point, _ = camera_pose((frame - 1) / (FRAMES - 1))
        for start, end in solid_segments:
            direction = end - start
            fraction = max(0.0, min(1.0, (point - start).dot(direction) / direction.length_squared))
            closest = min(closest, (point - start - fraction * direction).length)
    if closest < 0.30:
        raise RuntimeError(f"Camera intersects a steel member: nearest centerline {closest:.3f} m")
    rods("Primary structural tubes", [(warped(a), warped(b)) for a, b in principal],
         0.075 if TUNED_LOOK else 0.105, steel, 16)
    rods("Secondary diagonal braces", [(warped(a), warped(b)) for a, b in secondary],
         0.04 if TUNED_LOOK else 0.057, fine)
    rods("Sparse red load-path guides", [(warped(a), warped(b)) for a, b in red_lines], 0.012, red, 8)

    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8,
                                         radius=0.15 if TUNED_LOOK else 0.205)
    prototype = bpy.context.object
    prototype.name = "Joint prototype"
    prototype.data.materials.append(fittings)
    bpy.ops.object.shade_smooth()
    for iy, y in enumerate(YS):
        for x in XS:
            for z in ZS:
                if iy == 0 and x == XS[0] and z == ZS[0]:
                    node = prototype
                else:
                    node = bpy.data.objects.new("Ball joint", prototype.data)
                    bpy.context.collection.objects.link(node)
                node.location = warped((x, y, z))

    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 18, -3.25))
    ground = bpy.context.object
    ground.name = "Shadow ground"
    ground.dimensions = (18, 85, 0.12)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    ground.data.materials.append(dark)

    for n, y in enumerate((-8, 0, 9, 18, 27, 36, 45)):
        area(f"Long softbox left {n}", (-6.5, y, 5.8), (0, y + 2, 0),
             800 if TUNED_LOOK else 1050 if NEUTRAL_LOOK else 1150, (4, 6),
             (0.90, 0.94, 1.0) if TUNED_LOOK else
             (0.96, 0.96, 0.96) if NEUTRAL_LOOK else (0.75, 0.86, 1.0))
        area(f"Edge light right {n}", (6.2, y + 2.0, 3.9), (0, y + 1, 1),
             550 if TUNED_LOOK else 720 if NEUTRAL_LOOK else 780, (2, 4),
             (0.95, 0.97, 1.0) if TUNED_LOOK else
             (0.98, 0.98, 0.98) if NEUTRAL_LOOK else (0.89, 0.94, 1.0))
    world = bpy.context.scene.world
    world.color = (0.015, 0.022, 0.03)
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (
        (0.038, 0.043, 0.050, 1) if TUNED_LOOK else
        (0.058, 0.060, 0.063, 1) if NEUTRAL_LOOK else (0.018, 0.026, 0.035, 1))
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = (
        0.65 if TUNED_LOOK else 0.72 if NEUTRAL_LOOK else 0.55)

    camera_data = bpy.data.cameras.new("Camera 35.25-42.25")
    camera = bpy.data.objects.new("Camera 35.25-42.25", camera_data)
    bpy.context.collection.objects.link(camera)
    bpy.context.scene.camera = camera
    camera_data.type = "PERSP"
    camera_data.lens = 27
    camera_data.clip_end = 180
    for frame in range(1, FRAMES + 1):
        camera.location, camera.rotation_euler = camera_pose((frame - 1) / (FRAMES - 1))
        camera.keyframe_insert(data_path="location", frame=frame)
        camera.keyframe_insert(data_path="rotation_euler", frame=frame)

    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = FRAMES
    scene.render.fps = FPS
    scene.render.resolution_x = 720
    scene.render.resolution_y = 1280
    scene.render.resolution_percentage = 100
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 20
    scene.cycles.use_denoising = True
    render_device = "CPU"
    try:
        preferences = bpy.context.preferences.addons["cycles"].preferences
        preferences.compute_device_type = "METAL"
        preferences.get_devices()
        metal_devices = [device for device in preferences.devices if device.type == "METAL"]
        if metal_devices:
            for device in preferences.devices:
                device.use = device.type == "METAL"
            scene.cycles.device = "GPU"
            render_device = "METAL"
    except (KeyError, RuntimeError, TypeError):
        scene.cycles.device = "CPU"
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "AgX"
    scene.render.filepath = str(OUT / "frames" / "frame_")
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "truss_flythrough.blend"))
    (OUT / "shot_manifest.json").write_text(json.dumps({
        "reference_time_s": [35.25, 42.25], "fps": FPS, "frames": FRAMES,
        "resolution": [720, 1280], "camera": camera.name,
        "scene": "procedural space-frame visual reconstruction; not surveyed DDP geometry",
        "primary_tubes": len(principal), "secondary_braces": len(secondary),
        "ball_joints": len(XS) * len(ZS) * len(YS),
        "minimum_camera_to_steel_centerline_m": round(closest, 3),
        "render_device_at_creation": render_device,
        "look": "tuned" if TUNED_LOOK else "neutral" if NEUTRAL_LOOK else "original",
    }, indent=2) + "\n")
    print(f"Saved {OUT / 'truss_flythrough.blend'} with {len(principal)} tubes and {len(secondary)} braces")


if __name__ == "__main__":
    main()
