"""Author for the minimal end-to-end example: one plate on a floor, a light and a camera (shot a only)."""
import bpy

scene = bpy.context.scene
bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0)); floor = bpy.context.object; floor.name = 'floor'; floor['studio_id'] = 'floor'
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1)); block = bpy.context.object; block.name = 'block'; block['studio_id'] = 'block'
bpy.ops.object.light_add(type='SUN', location=(4, -4, 10))
bpy.ops.object.camera_add(location=(7, -7, 5)); camera = bpy.context.object; scene.camera = camera
camera.rotation_euler = (1.1, 0, 0.785)
scene['studio_authored_animation'] = True
