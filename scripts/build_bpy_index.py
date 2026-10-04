"""Dump the running Blender's Python API (RNA types, operators' parameters, bmesh.ops, mathutils) to JSON.

blender -b --factory-startup --python scripts/build_bpy_index.py -- library/api/blender-<version>.json
The index is what `python -m studio api search/show` answers from, so agents look up the installed
version's real names (e.g. 5.x NodesModifier inputs) instead of recalling an older API.
"""
import inspect
import json
from pathlib import Path
import sys

import bmesh
import bpy
import mathutils

ENUM_LIMIT = 60


def prop(p):
    row = {'id': p.identifier, 'type': p.type, 'ro': p.is_readonly}
    if p.description:
        row['desc'] = p.description
    if p.type == 'ENUM':
        items = [i.identifier for i in p.enum_items] or [i.identifier for i in getattr(p, 'enum_items_static', [])]
        row['items'] = items[:ENUM_LIMIT]
        if len(items) > ENUM_LIMIT:
            row['items_total'] = len(items)  # e.g. icon lists; look these up live with workbench api_lookup
    if p.type in ('POINTER', 'COLLECTION') and p.fixed_type:
        row['of'] = p.fixed_type.identifier
    if getattr(p, 'is_array', False) and getattr(p, 'array_length', 0):
        row['len'] = p.array_length
    return row


types = {}
for name in dir(bpy.types):
    cls = getattr(bpy.types, name)
    rna = getattr(cls, 'bl_rna', None)
    if rna is None:
        continue
    types[name] = {'base': rna.base.identifier if rna.base else None, 'desc': rna.description,
                   'props': [prop(p) for p in rna.properties if p.identifier != 'rna_type'],
                   'funcs': [{'id': f.identifier, 'desc': f.description, 'params': [prop(p) for p in f.parameters]} for f in rna.functions]}
operators = {}
for module_name in dir(bpy.ops):
    module = getattr(bpy.ops, module_name)
    for op_name in dir(module):
        try:
            rna = getattr(module, op_name).get_rna_type()
        except Exception:
            continue
        operators[f'{module_name}.{op_name}'] = {'desc': rna.description, 'params': [prop(p) for p in rna.properties if p.identifier != 'rna_type']}
bmesh_ops = {n: (getattr(bmesh.ops, n).__doc__ or '').strip()[:1500] for n in dir(bmesh.ops) if not n.startswith('_')}
math_types = {}
for name in ('Vector', 'Matrix', 'Quaternion', 'Euler', 'Color'):
    cls = getattr(mathutils, name)
    math_types[name] = {m: (getattr(cls, m).__doc__ or '').strip()[:600] for m in dir(cls) if not m.startswith('_')}
for sub in ('bvhtree', 'geometry', 'kdtree', 'interpolate', 'noise'):
    mod = getattr(mathutils, sub, None)
    if mod is not None:
        math_types[f'mathutils.{sub}'] = {m: (getattr(mod, m).__doc__ or '').strip()[:600] for m in dir(mod) if not m.startswith('_')}
out = Path(sys.argv[sys.argv.index('--') + 1])
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({'blender_version': bpy.app.version_string, 'types': types, 'operators': operators,
                           'bmesh_ops': bmesh_ops, 'mathutils': math_types}, separators=(',', ':')))
print('BPY_INDEX', out, len(types), len(operators), len(bmesh_ops))
