"""Run inside Blender, no renderer. A declared exaggeration (studio_display_scale) is judged against real size x factor -
a declaration, not an exemption - on the object or through an ancestor; a bad declaration is ignored with a warning;
layout rows and subject dimension deviations write the declaration the audit and the build read (2026-10-09)."""
from pathlib import Path
import sys
import bpy
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'studio/blender_ops'))
from look_scale import audit_scale

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


def bolt(name, x, factor=1.0):   # M20 hex head (30 mm across flats, 12.5 mm high) drawn at x factor
    bpy.ops.mesh.primitive_cylinder_add(vertices=6, radius=factor * 0.030 / 3 ** 0.5, depth=factor * 0.0125, location=(x, 0, 0))
    obj = bpy.context.object; obj.name = name; obj['studio_dim_role'] = 'bolt_hex_head'
    return obj


REASON = 'the head reads at phone size'
real = bolt('real', 0)
big = bolt('big', 1, 3.0)                                                        # x3, undeclared: flagged
shown = bolt('shown', 2, 3.0); shown['studio_display_scale'] = 3.0; shown['studio_display_reason'] = REASON
over = bolt('over', 3, 6.0); over['studio_display_scale'] = 3.0; over['studio_display_reason'] = REASON   # x6 declared x3
child = bolt('child', 4, 2.0)
holder = bpy.data.objects.new('holder', None); scene.collection.objects.link(holder)
holder['studio_id'] = 'holder'; holder['studio_display_scale'] = 2.0; holder['studio_display_reason'] = REASON
child.parent = holder                                                            # declared on the ancestor
bad = bolt('bad', 5, 3.0); bad['studio_display_scale'] = 9.0; bad['studio_display_reason'] = REASON      # above the bound
terse = bolt('terse', 6, 3.0); terse['studio_display_scale'] = 3.0; terse['studio_display_reason'] = 'big'  # no reason
bpy.context.view_layer.update()

report = audit_scale(scene)
ok = {}
for row in report['checks']:
    ok[row['object']] = ok.get(row['object'], True) and row['ok']
assert ok == {'real': True, 'big': False, 'shown': True, 'over': False, 'child': True, 'bad': False, 'terse': False}, ok
assert [d['object'] for d in report['display_scaled']] == ['child', 'over', 'shown'], report['display_scaled']
assert next(d for d in report['display_scaled'] if d['object'] == 'child')['declared_by'] == 'holder'
assert sum('is ignored' in w for w in report['warnings']) == 2, report['warnings']

# layout rows and subject deviations write the declaration
import layout
row = {'id': 'buffer', 'display_scale': {'factor': 3, 'reason': '30 mm buffer reads at phone size'}}
target = bpy.data.objects.new('buffer', None); scene.collection.objects.link(target)
layout._declare_display(target, row)
assert target['studio_display_scale'] == 3.0 and target['studio_display_reason'].startswith('30 mm')
sys.path.insert(0, str(REPO / 'studio/blender_ops'))
from modeling import assemble
root = bpy.data.objects.new('subj', None); scene.collection.objects.link(root)
parts = {p: bpy.data.objects.new(f'subj/{p}', None) for p in ('layer', 'slab')}
spec = {'dimensions': [{'id': 'layer_t', 'part_ids': ['layer']}, {'id': 'whole_h'}],
        'deviations': [{'id': 'd1', 'check': 'dimension:layer_t', 'factor': 3, 'reason': 'thin layer reads at phone size'},
                       {'id': 'd2', 'check': 'dimension:whole_h', 'factor': 1.2, 'reason': 'whole subject taller for the frame'},
                       {'id': 'd3', 'check': 'silhouette:front', 'min_iou': 0.5, 'reason': 'not a size departure at all'}]}
assemble._declare_display(spec, parts, root)
assert parts['layer']['studio_display_scale'] == 3.0 and root['studio_display_scale'] == 1.2
assert 'studio_display_scale' not in parts['slab'].keys()
spec['deviations'] = spec['deviations'][2:]                                      # deviations removed: nothing stale
assemble._declare_display(spec, parts, root)
assert all('studio_display_scale' not in o.keys() for o in (root, *parts.values()))
print('display_scale smoke OK')
