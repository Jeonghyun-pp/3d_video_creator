"""Run inside Blender, no renderer/GPU required. Builds every catalog material from the library."""
from pathlib import Path
import json
import shutil
import sys
import tempfile

import bpy

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'studio/blender_ops'))
import look_materials as lm

LIB = REPO / 'library'
bpy.ops.wm.read_factory_settings(use_empty=True)
cat = lm.catalog()

# 1. one material per catalog entry whose texture asset is in the library
built, skipped = {}, []
for i, key in enumerate(sorted(cat['kinds'])):
    ts = cat['kinds'][key]['texset']
    asset = cat['texsets'][ts]['asset_id'] if ts else None
    if asset and not (LIB / 'assets' / asset).is_dir():
        skipped.append(key)
        continue
    bpy.ops.mesh.primitive_cube_add(size=0.3, location=(i, 0, 0))
    obj = bpy.context.object
    mat = lm.make_material(key, key, library_root=LIB)
    lm.assign(obj, mat)
    assert obj.material_slots[0].material == mat and mat.name == 'StudioMat_' + key, mat.name
    built[key] = mat
assert not skipped, skipped
assert len(built) == len(cat['kinds']), sorted(built)  # every catalog kind builds (no fixed list: a new kind is covered)
for key, mat in built.items():  # kinds that declare emission emit; the others stay unlit surfaces
    strength = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED').inputs['Emission Strength'].default_value
    assert (strength > 0) == (cat['kinds'][key].get('emission', 0) > 0), (key, strength)

# 2. every image packed, no external or absolute paths
external = [i.filepath for i in bpy.data.images if i.source == 'FILE' and not i.packed_file]
assert external == [], external
assert all(i.filepath_raw.startswith('//') for i in bpy.data.images if i.source == 'FILE'), [i.filepath_raw for i in bpy.data.images]
assert all(i.size[0] > 0 for i in bpy.data.images if i.source == 'FILE')

# 3. colour spaces: base_color sRGB, data maps Non-Color
spaces = {i['studio_map_role']: set() for i in bpy.data.images if 'studio_map_role' in i}
for i in bpy.data.images:
    if 'studio_map_role' in i:
        spaces[i['studio_map_role']].add(i.colorspace_settings.name)
assert spaces['base_color'] == {'sRGB'}, spaces
assert all(v == {'Non-Color'} for k, v in spaces.items() if k != 'base_color'), spaces
assert set(spaces) == {'base_color', 'roughness', 'displacement'}, spaces
n_images = len([i for i in bpy.data.images if i.source == 'FILE'])
used = {kind['texset'] for kind in cat['kinds'].values() if kind.get('texset')}
assert n_images == len(used) * 3, (n_images, len(used))   # every texture set the kinds use x its 3 maps, shared across rebuilds

# 4. object-local coordinates only (swim-safe) and distance LOD present
for key, mat in built.items():
    nodes = mat.node_tree.nodes
    for link in mat.node_tree.links:
        if link.from_node.type == 'TEX_COORD':
            assert link.from_socket.name == 'Object', (key, link.from_socket.name)
        assert not (link.from_node.type == 'NEW_GEOMETRY' and link.from_socket.name == 'Position'), key
    assert any(n.type == 'CAMERA' for n in nodes), key
    assert json.loads(mat['studio_textures']) or not cat['kinds'][key]['texset'], key

# 5. deterministic rebuild in place (same name, same graph)
def signature(mat):
    return sorted((n.bl_idname, tuple(round(float(x), 6) for s in n.inputs if hasattr(s, 'default_value')
                   for x in (s.default_value if hasattr(s.default_value, '__len__') else [s.default_value])
                   if isinstance(x, (int, float)))) for n in mat.node_tree.nodes)
sig = signature(built['galvanized_steel'])
again = lm.make_material('galvanized_steel', 'galvanized_steel', library_root=LIB)
assert again == built['galvanized_steel'] and signature(again) == sig

# 6. wear / scale_m / overrides
clean = lm.make_material('wood_clean', 'wood', library_root=LIB, scale_m=1.6, wear=0.0, overrides={'base_color': [0.5, 0.4, 0.3]})
p = json.loads(clean['studio_params'])
assert p['edge_wear'] == p['dust'] == p['grime'] == 0 and p['tile_m'] == 1.6
for bad in (dict(scale_m=1.0, catalog_key='glass'), dict(overrides={'nope': 1}, catalog_key='glass')):
    try:
        lm.make_material('bad', library_root=LIB, **bad)
        raise AssertionError(bad)
    except ValueError:
        pass

# 7. tampered library copy and uncleared manifest are rejected
tamper = {}
with tempfile.TemporaryDirectory() as tmp:
    src = LIB / 'assets' / 'ambientcg_metal009'
    shutil.copytree(src, Path(tmp) / 'assets' / 'ambientcg_metal009')
    vdir = Path(tmp) / 'assets' / 'ambientcg_metal009' / 'v0001'
    color = vdir / 'original' / 'Metal009_1K-JPG_Color.jpg'
    data = bytearray(color.read_bytes()); data[len(data) // 2] ^= 0x01; color.write_bytes(bytes(data))
    for role in ('base_color', 'roughness'):
        try:
            lm.texture('ambientcg_metal009', role, library_root=tmp)
            tamper[role] = 'accepted'
        except ValueError as exc:
            tamper[role] = 'LOOK_QA_FAILED' if str(exc).startswith('LOOK_QA_FAILED: texture provenance') else str(exc)
    manifest = json.loads((vdir / 'asset.json').read_text())
    manifest['source']['use_status'] = 'pending_review'
    (vdir / 'asset.json').write_text(json.dumps(manifest))
    try:
        lm.texture('ambientcg_metal009', 'roughness', library_root=tmp)
        tamper['uncleared'] = 'accepted'
    except ValueError as exc:
        tamper['uncleared'] = 'LOOK_QA_FAILED' if 'use_status' in str(exc) else str(exc)
assert tamper == {'base_color': 'LOOK_QA_FAILED', 'roughness': 'accepted', 'uncleared': 'LOOK_QA_FAILED'}, tamper
assert not [i for i in bpy.data.images if i.filepath_raw.startswith(tmp) or not i.packed_file and i.source == 'FILE']

print('STUDIO_LOOK_MATERIALS_SMOKE ' + json.dumps({
    'ok': True, 'built': sorted(built), 'skipped_missing_assets': skipped, 'images_packed': n_images,
    'colorspaces': {k: sorted(v) for k, v in sorted(spaces.items())}, 'tamper': tamper,
    'checks': ['catalog_builds', 'all_packed_relative_paths', 'colorspaces', 'object_local_coords',
               'distance_lod', 'deterministic_rebuild', 'wear_scale_overrides', 'sha256_tamper', 'use_status']}, sort_keys=True))
