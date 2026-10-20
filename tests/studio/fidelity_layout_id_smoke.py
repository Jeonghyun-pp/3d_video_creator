"""Run inside Blender (headless): a spec subject built as a scene instance row is measured under the id it is built as.

A declarative scene builds `canopy_tree_1` under its layout id `canopy-tree-1` (ids_core.layout_id); fidelity looked
for the spec's id as written, found no root and no part, and failed every such subject (2026-10-20, canopy trees).
"""
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import fidelity  # noqa: E402

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
root = bpy.data.objects.new('canopy-tree-1', None)
bpy.context.scene.collection.objects.link(root)
root['studio_id'] = root['studio_subject_id'] = 'canopy-tree-1'
bpy.ops.mesh.primitive_cylinder_add(radius=0.2, depth=4.0, location=(0, 0, 2))
trunk = bpy.context.active_object
trunk.name = trunk['studio_id'] = 'canopy-tree-1/trunk'
trunk['studio_subject_id'], trunk['studio_part_id'] = 'canopy-tree-1', 'trunk'
trunk.parent = root
spec = {'subject_id': 'canopy_tree_1', 'features': [], 'builders': [{'part_id': 'trunk'}]}
measured = fidelity.measure_subject(spec, geometry_only=True)
assert measured['subject_id'] == 'canopy_tree_1' and measured['root_found'], measured
assert measured['parts'].get('trunk', {}).get('objects') == 1 and abs(measured['whole']['height'] - 4.0) < 1e-3, measured
print('STUDIO_FIDELITY_LAYOUT_ID_SMOKE', {'ok': True})
