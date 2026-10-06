"""Run inside Blender: every action reader runs on recording params, and what it read must equal its row of
studio/blender_ops/action_params.py (both ways: a key read but not in the table would be refused by validation and
unreachable by words; a key in the table nobody reads would be a silent no-op). Readers: scene_tools.apply_actions,
reveal.apply, simulate.apply (both kinds), kinematics.apply_drives (rpm and keys).
"""
from pathlib import Path
import json
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'studio/blender_ops'))
import action_params as ap  # noqa: E402
import kinematics  # noqa: E402
import reveal  # noqa: E402
import simulate  # noqa: E402
from modeling import build_subject  # noqa: E402
from scene_tools import apply_actions  # noqa: E402

READ = {}


class Recording(dict):
    """A params dict that notes every key read (get, [], in) under its label (type, list key, ...)."""
    def __init__(self, label, data):
        super().__init__({k: self._wrap(label, k, v) for k, v in data.items()})
        self.label = label

    @staticmethod
    def _wrap(label, key, value):
        if isinstance(value, list) and value and isinstance(value[0], dict) and ap.ITEMS.get((*label, key)) is not None:
            return [Recording((*label, key), item) for item in value]
        return value

    def _note(self, key):
        READ.setdefault(self.label, set()).add(key)

    def get(self, key, default=None):
        self._note(key); return super().get(key, default)

    def __getitem__(self, key):
        self._note(key); return super().__getitem__(key)

    def __contains__(self, key):
        self._note(key); return super().__contains__(key)


def recorded(action):
    return {**action, 'params': Recording((action['type'],), action['params'])}


bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene; scene.render.fps = 30; scene.frame_end = 60


def cube(name, loc, size=1.0):
    bpy.ops.mesh.primitive_cube_add(size=size, location=loc)
    obj = bpy.context.object; obj.name = name
    obj['studio_id'] = 'test/' + name; obj['studio_instance_id'] = 'test'; obj['studio_part_id'] = name; obj['studio_explode_vector'] = [0, 0, 1]
    return obj


for name, loc in (('panel', (0, 0, 0)), ('stack', (2, 0, 0)), ('shell', (4, 0, 0)), ('lamp', (6, 0, 0)), ('ground', (10, 0, 0)), ('floor', (14, 0, 0))):
    cube(name, loc)
cube('cutter', (4, .4, 0)); cube('road_cutter', (10, 0, 0), 0.5)
cap = bpy.data.materials.new('cap'); cap.use_nodes = True
bpy.data.objects['lamp'].data.materials.append(cap)
curve = bpy.data.curves.new('path', 'CURVE'); curve.dimensions = '3D'
spline = curve.splines.new('POLY'); spline.points.add(1); spline.points[0].co = (0, 2, 0, 1); spline.points[1].co = (2, 2, 0, 1)
scene.collection.objects.link(bpy.data.objects.new('path', curve))


def action(name, kind, part, params, start=0, end=30, instance='test'):
    return {'action_id': name, 'type': kind, 'targets': [{'instance_id': instance, 'part_id': part}],
            'start_frame': start, 'end_frame': end, 'easing': 'linear', 'params': params}


# scene_tools: every optional key given, so each read is reachable in one pass
tools = [action('peel', 'peel', 'panel', {'direction_source': 'asset', 'distance_m': 1, 'order': 'asset_order', 'stagger_frames': 1,
                                          'rotation_radians': [0, 0, 0.1]}, 0, 10),
         action('back', 'assemble', 'panel', {'source_action_id': 'peel', 'order': 'asset_order', 'stagger_frames': 0}, 10, 20),
         action('blow', 'explode', 'stack', {'direction_source': 'axis', 'axis': [0, 0, 1], 'distance_m': 2, 'order': 'asset_order',
                                            'stagger_frames': 0, 'rotation_radians': [0, 0, 0]}, 0, 10),
         action('cut', 'cutaway', 'shell', {'cutter_object_id': 'test/cutter', 'cap_material_id': 'cap',
                                            'cutter_keys': [{'frame': 0, 'location': [4, .4, 0], 'rotation_euler': [0, 0, 0]},
                                                            {'frame': 20, 'location': [4, .6, 0]}]}),
         action('flow', 'flow', 'panel', {'path_object_id': 'path', 'speed_mps': 1, 'marker_count': 3, 'marker_radius_m': 0.05, 'loop': True,
                                          'reverse': True}),
         action('glow', 'highlight', 'lamp', {'color_srgb': [1, .2, .1], 'strength': 2, 'restore': False}, 0, 10)]
apply_actions({'duration_frames': 60, 'actions': [recorded(a) for a in tools]})

opening = action('open', 'reveal', 'ground', {'cutter_object_id': 'test/road_cutter', 'cap_material_id': 'cap', 'also_cut_overlapping': True,
                                              'cutter_keys': [{'t': 0, 'location': [10, 0, 0], 'rotation_euler': [0, 0, 0], 'scale': [0.1, 0.1, 1]},
                                                              {'t': 1, 'scale': [1, 1, 1]}]}, 0, 20)
reveal.apply({'duration_frames': 60, 'actions': [recorded(opening)]})

sims = [action('debris', 'simulate', 'floor', {'kind': 'rigid_debris', 'region': [[13.6, -0.4, 2], [14.4, 0.4, 3]], 'count': 4, 'seed': 1,
                                                'size_range': [0.1, 0.2], 'color_srgb': [0.5, 0.5, 0.5]}, 0, 20),
        action('dust', 'simulate', 'floor', {'kind': 'dust', 'region': [[13, -1, 0.6], [15, 1, 2]], 'count': 10, 'seed': 2, 'color_srgb': [0.6, 0.6, 0.6],
                                              'ceiling_z': 1.5, 'drift_mps': 0.4, 'grain_m': 0.02}, 0, 20)]
simulate.apply({'duration_frames': 60, 'actions': [recorded(a) for a in sims]})

SPEC = {'schema_version': 1, 'subject_id': 'mech', 'identity': 'two blocks on a belt', 'subject_mode': 'schematic',
        'builders': [{'part_id': 'a', 'builder': 'box', 'params': {'size': [0.2, 0.2, 0.2]}, 'transform': {'location': [20, 0, 0]}},
                     {'part_id': 'b', 'builder': 'box', 'params': {'size': [0.2, 0.2, 0.2]}, 'transform': {'location': [21, 0, 0]}}],
        'joints': [{'id': 'j_a', 'type': 'revolute', 'parent': 'root', 'child': 'a', 'origin': [20, 0, 0], 'axis': [0, 0, 1]},
                   {'id': 'j_b', 'type': 'revolute', 'parent': 'root', 'child': 'b', 'origin': [21, 0, 0], 'axis': [0, 0, 1]}],
        'couplings': [{'id': 'belt', 'kind': 'belt', 'driver': 'j_a', 'driven': 'j_b', 'ratio': 0.5}]}
build_subject(SPEC)
drives = [action('spin_rpm', 'drive', 'a', {'drives': [{'joint': 'j_a', 'subject': 'mech', 'rpm': 30}]}, 0, 20, 'mech'),
          action('spin_keys', 'drive', 'a', {'drives': [{'joint': 'j_a', 'profile': 'ease', 'keys': [{'t': 0, 'value': 0}, {'t': 1, 'value': 90}]}]}, 30, 50, 'mech')]
for row in drives:
    kinematics.apply_drives({'duration_frames': 60, 'actions': [recorded(row)]}, 30)

expected = {}
for kind in ap.ACTION_PARAMS:
    row = set(ap.ACTION_PARAMS[kind]) | (set().union(*ap.SIMULATE_KINDS.values()) if kind == 'simulate' else set())
    expected[(kind,)] = row
for key, row in ap.ITEMS.items():
    expected[key] = set(row)
problems = {}
for label, keys in expected.items():
    got = READ.get(label, set())
    if got != keys:
        problems['/'.join(label)] = {'read_not_in_table': sorted(got - keys), 'in_table_never_read': sorted(keys - got)}
assert not problems, json.dumps(problems, indent=1)
print('STUDIO_ACTION_PARAMS_SMOKE ' + json.dumps({'ok': True, 'rows_checked': len(expected), 'labels_read': len(READ)}))
