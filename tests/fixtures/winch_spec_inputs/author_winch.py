"""Winch built only from subjects/winch/spec.json; studio light and camera rig from shot.json."""
import json
from pathlib import Path
import bpy
from modeling import build_subject
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
spec = json.loads(Path(STUDIO_JOB['subject_spec_paths']['winch']).read_text())  # the version's snapshot
build_subject(spec, root_location=(0, 0, 0.3))
bpy.ops.mesh.primitive_plane_add(size=6); floor = bpy.context.object; floor.name = 'floor'; floor['studio_id'] = 'floor'
bpy.ops.object.light_add(type='AREA', location=(1.5, -2, 3)); key = bpy.context.object; key.data.energy = 400; key.data.size = 2
key.rotation_euler = (0.7, 0.3, 0.6)
bpy.ops.object.camera_add(); scene.camera = bpy.context.object
