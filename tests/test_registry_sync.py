"""Registries and the schemas that admit their names cannot drift apart: what the schema accepts is exactly what the
code builds, and every params key a builder reads is declared (so spec lint can refuse the ones nothing reads)."""
import ast
import json
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / 'studio' / 'blender_ops'
SCHEMAS = ROOT / 'schemas' / 'studio-v1'


def schema(name):
    return json.loads((SCHEMAS / f'{name}.schema.json').read_text())


def dict_keys(path, name):
    """Keys of a module-level `NAME = {...}` literal, read without importing the module (it may need bpy)."""
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return {k.value for k in node.value.keys}
    raise AssertionError(f'{name} not found in {path}')


def params_reads(path):
    keys = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id == 'params' and isinstance(n.slice, ast.Constant):
            keys.add(n.slice.value)
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'get' and isinstance(n.func.value, ast.Name)
                and n.func.value.id == 'params' and n.args and isinstance(n.args[0], ast.Constant)):
            keys.add(n.args[0].value)
        if isinstance(n, ast.Compare) and isinstance(n.left, ast.Constant) and any(isinstance(c, ast.Name) and c.id == 'params' for c in n.comparators):
            keys.add(n.left.value)   # 'count' in params
    return keys


class RegistrySyncTest(unittest.TestCase):
    def test_builders_schema_code_and_param_table_agree(self):
        from studio.blender_ops.builder_params import BUILDER_PARAMS
        enum = set(schema('subject')['properties']['builders']['items']['properties']['builder']['enum'])
        geometry = dict_keys(OPS / 'modeling' / 'assemble.py', 'GEOMETRY')
        self.assertEqual(enum, set(BUILDER_PARAMS))
        self.assertEqual(geometry | {'array', 'mirror', 'asset'}, set(BUILDER_PARAMS))

    def test_every_params_read_is_declared(self):
        from studio.blender_ops.builder_params import BUILDER_PARAMS, GROUP_PARAMS
        modules = {'loft': ['loft'], 'wing': ['wing'], 'revolve': ['revolve'], 'sweep': ['sweep'], 'primitives': ['box'],
                   'profile': ['profile', 'toothed_ring'], 'wall': ['wall'],
                   'assemble': list(BUILDER_PARAMS)}   # assemble reads every geometry part (sharp angle, profile kind for the summary)
        for module, builders in modules.items():
            declared = set(GROUP_PARAMS).union(*(BUILDER_PARAMS[b] for b in builders))
            undeclared = params_reads(OPS / 'modeling' / f'{module}.py') - declared
            self.assertFalse(undeclared, f'{module}.py reads {sorted(undeclared)}: declare them in blender_ops/builder_params.py')

    def test_unknown_params_are_found_in_nested_items(self):
        from studio.blender_ops.builder_params import unknown_params
        entry = {'part_id': 'a', 'builder': 'array', 'params': {'count': 3, 'item': {'builder': 'group', 'params': {'items': [
            {'builder': 'box', 'params': {'size': [1, 1, 1], 'colour': 'red'}}]}}, 'cuont': 2}}
        self.assertEqual(sorted(k for _, k in unknown_params(entry)), ['colour', 'cuont'])

    def test_spec_lint_refuses_a_param_no_builder_reads(self):
        from studio.subjects import lint_spec
        from studio.mechanisms import planetary_spec
        spec = planetary_spec('pg', 0.001, 12, 24, 60, 3)
        self.assertFalse([e for e in lint_spec(spec)['errors'] if 'does not read' in e or 'mechanism' in e])
        spec['builders'][0]['params']['teeth_count'] = 3
        self.assertTrue(any('does not read' in e and 'teeth_count' in e for e in lint_spec(spec)['errors']))
        spec['couplings'][0]['sun'] = 'no_such_joint'
        self.assertTrue(any(e.startswith('mechanism:') for e in lint_spec(spec)['errors']))

    def test_couplings_schema_rows_and_solver_agree(self):
        from studio.blender_ops.kinematics_core import COUPLINGS, solve
        props = set(schema('subject')['properties']['couplings']['items']['properties']) - {'id', 'kind'}
        used = set().union(*(set(row['fields']) | set(row.get('notes', ())) for row in COUPLINGS.values()))
        self.assertEqual(props, used, 'every coupling field in the schema is read by a coupling row, and the reverse')
        samples = {'gear': {'driver': 'a', 'driven': 'b', 'teeth': [10, 20]}, 'internal_gear': {'driver': 'a', 'driven': 'b', 'teeth': [10, 40]},
                   'belt': {'driver': 'a', 'driven': 'b', 'ratio': 0.5}, 'rack': {'driver': 'a', 'driven': 'b', 'radius_m': 0.01},
                   'planetary': {'sun': 'a', 'carrier': 'b', 'planets': ['p'], 'teeth': {'sun': 12, 'planet': 24, 'ring': 60}},
                   'harmonic': {'driver': 'a', 'driven': 'b', 'teeth': {'flex': 100, 'circular': 102}, 'deform_part': 'f', 'deflection_m': 0.0005}}
        self.assertEqual(set(samples), set(COUPLINGS))
        for kind, c in samples.items():
            values = solve([{'id': 'c', 'kind': kind, **c}], {'a': 90.0})
            self.assertTrue(all(math.isfinite(v) for v in values.values()) and 'b' in values, f'solve ignores {kind}')

    def test_moves_kits_and_shaders_match_their_schemas(self):
        from studio.blender_ops.camera_moves_core import MOVES
        from studio.layout import KITS
        shot = schema('shot')
        self.assertEqual(set(shot['properties']['camera']['properties']['move']['properties']['type']['enum']), set(MOVES))
        self.assertEqual(set(shot['$defs']['scene']['properties']['kits']['items']['properties']['kit']['enum']), set(KITS))
        shader_enum = set(schema('subject')['properties']['materials']['items']['properties']['shader']['properties']['kind']['enum'])
        self.assertEqual(shader_enum, dict_keys(OPS / 'env_materials.py', 'SHADERS'))


if __name__ == '__main__':
    unittest.main()
