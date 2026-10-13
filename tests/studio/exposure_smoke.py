"""Run inside Blender (headless): the exposure survey calls inside only what no outside side shows.

A closed box shell with a block in it, a fin on it and a coat over its lid: the block is inside; the fin, the shell and the
lid under its coat are not. The same shell open at the bottom: its block shows from below, so nothing has to be opened to
show it. A part wrapped by its own child part is inside: the child is another part.
"""
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import exposure_core  # noqa: E402
from exposure_survey import measure  # noqa: E402


def tagged(obj, subject, part, parent):
    obj['studio_id'] = f'{subject}/{part}'
    obj['studio_part_id'] = part
    obj['studio_subject_id'] = subject
    obj.name = f'{subject}/{part}'
    obj.parent = parent
    return obj


def cube(location, scale):
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.active_object
    obj.scale = scale
    return obj


def subject(name, offset, bottom):
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    root['studio_id'] = root['studio_subject_id'] = name
    x = offset
    walls = [((x + 1, 0, 0), (0.05, 1, 1)), ((x - 1, 0, 0), (0.05, 1, 1)), ((x, 1, 0), (1, 0.05, 1)), ((x, -1, 0), (1, 0.05, 1)),
             ((x, 0, 1), (1, 1, 0.05))] + ([((x, 0, -1), (1, 1, 0.05))] if bottom else [])
    for n, (loc, scale) in enumerate(walls):
        tagged(cube(loc, scale), name, f'wall_{n}', root)
    tagged(cube((x, 0, 0), (0.3, 0.3, 0.3)), name, 'block', root)
    tagged(cube((x + 1.4, 0, 0), (0.3, 0.05, 0.3)), name, 'fin', root)
    tagged(cube((x, 0, 1.052), (1.0, 1.0, 0.0005)), name, 'coat', root)   # soot on the lid, 1.5 mm out: it hides nothing
    return root


bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
closed = subject('closed', 0.0, bottom=True)
opened = subject('opened', 20.0, bottom=False)
parts = ['block', 'fin', 'coat'] + [f'wall_{n}' for n in range(6)]
result = measure('closed', closed, parts, directions=32, samples=120)
threshold = result['threshold']
rows = result['parts']
inside = {p for p, r in rows.items() if exposure_core.inside(r['best_share'], threshold)}
assert inside == {'block'}, rows
assert rows['wall_4']['best_share'] > 0.8, rows['wall_4']   # under the coat
assert rows['block']['hidden_by'] and rows['block']['hidden_by'][0].startswith('wall_'), rows['block']
open_rows = measure('opened', opened, ['block', 'fin'], directions=32, samples=120)['parts']
assert not exposure_core.inside(open_rows['block']['best_share'], threshold), open_rows   # seen from below
assert open_rows['block']['best_dir'][2] < 0, open_rows['block']
nested = bpy.data.objects.new('nested', None)
bpy.context.scene.collection.objects.link(nested)
nested['studio_id'] = nested['studio_subject_id'] = 'nested'
core = tagged(cube((40, 0, 0), (0.3, 0.3, 0.3)), 'nested', 'core', nested)
cover = tagged(cube((40, 0, 0), (0.6, 0.6, 0.6)), 'nested', 'cover', core)   # a child part wrapped round its parent
cover.matrix_parent_inverse = core.matrix_world.inverted()
nested_rows = measure('nested', nested, ['core', 'cover'], directions=32, samples=120)['parts']
assert exposure_core.inside(nested_rows['core']['best_share'], threshold), nested_rows   # its own child hides it
assert nested_rows['core']['hidden_by'] == ['cover'], nested_rows['core']
print('exposure smoke ok', {p: r['best_share'] for p, r in rows.items()}, open_rows['block'])
