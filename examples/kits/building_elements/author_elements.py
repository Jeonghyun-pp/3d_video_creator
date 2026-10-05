"""Building elements built only from the version's spec snapshots, laid out apart; floor, sun, camera."""
import json
from pathlib import Path
import bpy
from modeling import build_subject
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
places = json.loads((Path(STUDIO_JOB['project_dir']) / 'places.json').read_text())
for subject_id, path in sorted(STUDIO_JOB['subject_spec_paths'].items()):
    build_subject(json.loads(Path(path).read_text()), root_location=tuple(places[subject_id]))
bpy.ops.mesh.primitive_plane_add(size=120, location=(0, 10, -0.01)); floor = bpy.context.object; floor.name = 'floor'; floor['studio_id'] = 'floor'
floor['studio_dim_role'] = 'none'
bpy.ops.object.light_add(type='SUN', location=(0, 0, 30)); sun = bpy.context.object; sun.data.energy = 3; sun.rotation_euler = (0.6, 0.2, 0.8)
bpy.ops.object.camera_add(); scene.camera = bpy.context.object
