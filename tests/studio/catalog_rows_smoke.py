"""Run inside Blender (headless): a subject spec's catalog material rows reach its meshes (2026-10-08, archcut3: every
catalog_key row was skipped and its parts rendered Blender's default grey, so the author script assigned materials by
hand). Photoreal look: the catalog material; any other look: the row's flat colour; the row's scene_role either way.
Run: blender --background --factory-startup --python tests/studio/catalog_rows_smoke.py
"""
from pathlib import Path
import json
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'studio/blender_ops'))
from modeling import build_subject  # noqa: E402
from modeling.assemble import MATERIALS, configure_materials  # noqa: E402


def spec(subject_id):
    return {'subject_id': subject_id, 'identity': 'column', 'builders': [
        {'part_id': 'shaft', 'builder': 'box', 'params': {'size': [0.8, 0.8, 4.0]}},
        {'part_id': 'cap', 'builder': 'box', 'params': {'size': [1.0, 1.0, 0.2]}, 'transform': {'location': [0, 0, 2.1]}}],
        'materials': [{'part_ids': ['shaft'], 'catalog_key': 'concrete', 'color_srgb': [0.7, 0.68, 0.64], 'roughness': 0.9},
                      {'part_ids': ['cap'], 'catalog_key': 'concrete', 'catalog_overrides': {'roughness': 0.6}, 'scene_role': 'clutter'}]}


def slots(root):
    return {o.get('studio_part_id'): [s.material.name for s in o.material_slots if s.material]
            for o in [root, *root.children_recursive] if o.type == 'MESH'}


bpy.ops.wm.read_factory_settings(use_empty=True)
checks = []
configure_materials(ROOT / 'library', 'photoreal_interior')
built = build_subject(spec('col-a'))
got = slots(built['root'])
assert got['shaft'] == ['StudioMat_col-a/material.0'] and got['cap'] == ['StudioMat_col-a/material.1'], got
assert bpy.data.materials['StudioMat_col-a/material.0'].get('studio_catalog_key') == 'concrete'
assert built['parts']['cap'].get('studio_scene_role') == 'clutter' or any(o.get('studio_scene_role') == 'clutter' for o in built['parts']['cap'].children_recursive)
checks.append('photoreal_look_gets_the_catalog_material_and_the_scene_role')

configure_materials(ROOT / 'library', 'flat_stylized')
got = slots(build_subject(spec('col-b'))['root'])
shaft = bpy.data.materials[got['shaft'][0]]
assert not shaft.name.startswith('StudioMat_') and abs(shaft.diffuse_color[0] - 0.448) < 0.01, (shaft.name, tuple(shaft.diffuse_color))   # sRGB 0.7 -> linear
assert got['cap'], got   # a catalog row with no colour still gets the flat grey, never an empty slot
checks.append('other_looks_get_the_flat_colour_never_an_empty_slot')

MATERIALS.update(library_root=None, photoreal=False)   # a bare modelling run (no build job): flat colour too
assert all(slots(build_subject(spec('col-c'))['root']).values())
checks.append('unconfigured_run_falls_back_to_the_flat_colour')
print('STUDIO_CATALOG_ROWS_SMOKE ' + json.dumps({'ok': True, 'checks': checks}))
