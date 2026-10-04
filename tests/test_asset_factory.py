"""Host contracts for the CAD asset factory; the CAD worker is mocked except in test_m20_real_gates."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from studio.asset_factory import factory
from studio.asset_factory.spec import load_tables, resolve_spec, spec_hash
from studio.assets import fetch_asset
from studio.common import REPO, StudioError

EXAMPLE = REPO / 'examples/factory/m20_bolt_set.json'
VERIFICATION = 'Values transcribed for prototyping; a human must check against the standard text before publication.'
# Documented choice: M20 x 80 bolt (ISO 4014 nominal length l = 80) + head height k = 12.5 -> 92.5 mm along the bolt axis.
BOLT_AXIS_MM, BOLT_AXIS_TOL_MM = 92.5, 0.5


def spec():
    return json.loads(EXAMPLE.read_text())


def fake_worker(gates_pass=True, missing_gate=None):
    """subprocess.run stand-in that writes what cad_worker.py would, with chosen gate results."""
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        job = json.loads(Path(command[3]).read_text())
        out = Path(command[4])
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{job['spec']['asset_id']}.glb").write_bytes(b'glTF-fake')
        gates = {name: {'passed': True} for name in factory.REQUIRED_GATES}
        gates['bbox'].update({'limit_mm': 0.5, 'parts': [{'part_id': 'bolt', 'max_error_mm': 0.0}]})
        gates['table']['checks'] = []
        gates['interference'].update({'limit_mm3': 1e-3, 'max_overlap_mm3': 0.0 if gates_pass else 12.5})
        gates['interference']['passed'] = gates_pass
        if missing_gate:
            del gates[missing_gate]
        report = {'spec_sha256': job['spec_sha256'], 'glb': f"{job['spec']['asset_id']}.glb", 'versions': {}, 'gates': gates,
                  'parts': [{'part_id': p, 'node_name': p, 'explode_vector': [0, 0, 1], 'anchors_mm': {'top': [0, 0, 1]}}
                            for p in ('bolt', 'washer_head', 'washer_nut', 'nut')]}
        (out / 'factory.json').write_text(json.dumps(report))
        return subprocess.CompletedProcess(command, 0 if gates_pass and not missing_gate else 1, '', '')
    return run, calls


class FactoryContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / 'library'

    def tearDown(self):
        self.temp.cleanup()

    def generate(self, **kwargs):
        return factory.generate_asset(spec(), library_root=self.library, python=sys.executable, **kwargs)

    def test_tables_cite_sources_and_cover_required_rows(self):
        tables = load_tables()
        required = {'ISO 4014': {'M12', 'M16', 'M20', 'M24'}, 'ISO 4032': {'M12', 'M16', 'M20', 'M24'},
                    'ISO 7089': {'M12', 'M16', 'M20', 'M24'}, 'EN 10365': {'HEB 200', 'HEB 300', 'IPE 300'},
                    'KS D 3504': {'D10', 'D13', 'D16', 'D22'}}
        for standard, rows in required.items():
            self.assertEqual(tables[standard]['_verification'], VERIFICATION)
            self.assertLessEqual(rows, set(tables[standard]['rows']))
            self.assertTrue(all(row['source'].strip() for row in tables[standard]['rows'].values()))

    def test_unknown_designation_rejected_before_worker(self):
        bad = {**spec(), 'designation': 'M99'}
        with self.assertRaises(StudioError) as error:
            resolve_spec(bad)
        self.assertEqual(error.exception.code, 'SPEC_UNKNOWN_DESIGNATION')
        with patch('studio.asset_factory.factory.subprocess.run', side_effect=AssertionError('worker must not run')), \
                self.assertRaises(StudioError) as error:
            factory.generate_asset(bad, library_root=self.library, python=sys.executable)
        self.assertEqual(error.exception.code, 'SPEC_UNKNOWN_DESIGNATION')
        with self.assertRaises(StudioError) as error:
            resolve_spec({'asset_id': 'beam', 'kind': 'hbeam', 'designation': 'HEB 999', 'length_mm': 1000})
        self.assertEqual(error.exception.code, 'SPEC_UNKNOWN_DESIGNATION')

    def test_unknown_fields_and_bad_lengths_rejected(self):
        for bad in ({**spec(), 'colour': 'red'}, {**spec(), 'length_mm': 81}, {**spec(), 'grip_mm': 60}):
            with self.assertRaises(StudioError) as error:
                resolve_spec(bad)
            self.assertEqual(error.exception.code, 'INPUT_INVALID')

    def test_spec_hash_is_stable_and_spec_sensitive(self):
        self.assertEqual(spec_hash(spec()), spec_hash(json.loads(json.dumps(spec()))))
        self.assertNotEqual(spec_hash(spec()), spec_hash({**spec(), 'grip_mm': 30}))

    def test_failed_gate_writes_nothing_to_library(self):
        for kwargs in ({'gates_pass': False}, {'missing_gate': 'table'}):
            run, calls = fake_worker(**kwargs)
            with patch('studio.asset_factory.factory.subprocess.run', side_effect=run), self.assertRaises(StudioError) as error:
                self.generate()
            self.assertEqual(error.exception.code, 'FACTORY_GATE_FAILED')
            self.assertEqual(len(calls), 1)
            self.assertFalse(self.library.exists() and any(self.library.rglob('*')), list(self.library.rglob('*')) if self.library.exists() else [])

    def test_same_spec_twice_runs_worker_once(self):
        run, calls = fake_worker()
        with patch('studio.asset_factory.factory.subprocess.run', side_effect=run):
            first = self.generate()
            second = self.generate()
        self.assertEqual(len(calls), 1)
        self.assertEqual((first['reused'], second['reused']), (False, True))
        self.assertEqual(first['manifest_path'], second['manifest_path'])
        source = second['source']
        self.assertEqual((source['license_id'], source['use_status'], source['provider']), ('original-generated', 'cleared', 'factory'))
        self.assertEqual(source['factory_spec_sha256'], spec_hash(spec()))
        self.assertEqual(source['standards'], ['ISO 4014', 'ISO 4032', 'ISO 7089'])
        self.assertEqual(sorted(f['relative_path'] for f in second['files']), ['factory.json', 'm20_bolt_set.glb'])
        # A changed spec is a new immutable version, never an overwrite.
        with patch('studio.asset_factory.factory.subprocess.run', side_effect=run):
            changed = factory.generate_asset({**spec(), 'grip_mm': 30}, library_root=self.library, python=sys.executable)
        self.assertEqual((changed['version'], len(calls)), ('v0002', 2))

    def test_mapping_missing_node_is_incomplete(self):
        run, _ = fake_worker()
        rows = [{'name': name, 'type': 'MESH', 'object_id': f'm20_bolt_set:object_{i:04d}'}
                for i, name in enumerate(('bolt', 'washer_head', 'washer_nut'), 1)]  # no 'nut'
        prepared = []

        def prepare(manifest, mapping=None, blender=None):
            prepared.append(mapping)
            return {'status': 'needs_mapping', 'inventory': {'objects': rows}, 'asset': {}, 'artifacts': []}
        with patch('studio.asset_factory.factory.subprocess.run', side_effect=run), \
                patch('studio.asset_factory.factory.assets.prepare_asset', side_effect=prepare), self.assertRaises(StudioError) as error:
            self.generate(prepare=True)
        self.assertEqual(error.exception.code, 'FACTORY_MAPPING_INCOMPLETE')
        self.assertIn('nut', error.exception.affected_ids)
        self.assertEqual(prepared, [None])  # never reached the pinning (mapped) prepare
        report = {'parts': [{'part_id': 'bolt', 'node_name': 'bolt', 'explode_vector': [0, 0, 1], 'anchors_mm': {'tip': [0, 0, -80]}}]}
        extra = [{'name': 'bolt', 'type': 'MESH', 'object_id': 'a:object_0001'}, {'name': 'stray', 'type': 'EMPTY', 'object_id': 'a:object_0002'}]
        with self.assertRaises(StudioError) as error:
            factory.build_mapping(report, {'objects': extra})
        self.assertEqual(error.exception.affected_ids, ['stray'])
        mapping = factory.build_mapping(report, {'objects': extra[:1]})
        self.assertEqual(mapping['parts'][0]['object_ids'], ['a:object_0001'])
        self.assertEqual(mapping['anchors'][0]['point_local_m'], [0.0, 0.0, -0.08])

    def test_factory_provider_candidate_json_rejected(self):
        glb = Path(self.temp.name) / 'part.glb'
        glb.write_bytes(b'glTF-fake')
        candidate = Path(self.temp.name) / 'candidate.json'
        candidate.write_text(json.dumps({'asset_id': 'forged', 'provider': 'factory', 'path': str(glb),
                                         'source': {'license_id': 'original-generated', 'use_status': 'cleared'}}))
        with self.assertRaises(StudioError) as error:
            fetch_asset(str(candidate), self.library / 'assets')
        self.assertEqual(error.exception.code, 'INPUT_INVALID')
        self.assertFalse((self.library / 'assets/forged/v0001/asset.json').exists())

    @unittest.skipUnless(factory.doctor_check()['available'], 'CAD interpreter (.venvs/cad or STUDIO_CAD_PYTHON) unavailable')
    def test_m20_real_gates(self):
        resolved = resolve_spec(spec())
        with tempfile.TemporaryDirectory() as staging:
            report, _, code = factory.run_worker({**resolved, 'spec_sha256': spec_hash(spec())}, staging)
        self.assertEqual(code, 0)
        factory.check_gates(report, spec_hash(spec()))
        self.assertEqual([p['part_id'] for p in report['parts']], ['bolt', 'washer_head', 'washer_nut', 'nut'])
        bolt = report['parts'][0]['bbox_mm']['size']
        self.assertAlmostEqual(bolt[2], BOLT_AXIS_MM, delta=BOLT_AXIS_TOL_MM)
        self.assertTrue(29.16 <= bolt[1] <= 30.0, bolt)  # across flats s (ISO 4014 M20, grade B min .. max)
        self.assertLessEqual(report['gates']['interference']['max_overlap_mm3'], 1e-3)
        self.assertLess(max(p['max_error_mm'] for p in report['gates']['bbox']['parts']), 0.5)


if __name__ == '__main__':
    unittest.main()
