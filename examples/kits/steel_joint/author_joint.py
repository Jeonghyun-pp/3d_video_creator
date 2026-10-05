"""Column base and CAD wall built only from the version's spec snapshots; floor, sun, camera."""
import json
from pathlib import Path
import bpy
from modeling import build_subject
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
for subject_id, location in (('column_base', (0, 0, 0.3)), ('wall_a', (2.0, 2.0, 0))):
    build_subject(json.loads(Path(STUDIO_JOB['subject_spec_paths'][subject_id]).read_text()), root_location=location)
bpy.ops.mesh.primitive_plane_add(size=30); floor = bpy.context.object; floor.name = 'floor'; floor['studio_id'] = 'floor'; floor['studio_dim_role'] = 'none'
bpy.ops.object.light_add(type='SUN', location=(0, 0, 10)); sun = bpy.context.object; sun.data.energy = 3; sun.rotation_euler = (0.6, 0.2, 0.8)
bpy.ops.object.camera_add(); scene.camera = bpy.context.object
