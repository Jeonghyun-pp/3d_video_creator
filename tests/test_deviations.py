from copy import deepcopy
from pathlib import Path
import unittest

from studio.fidelity import build_report
from studio.subject_fit import dimension_penalty
from studio.subjects import lint_spec

ROOT = Path(__file__).resolve().parents[1]
SPEC = {'schema_version': 1, 'subject_id': 'joint', 'identity': 'bolted bracket', 'subject_mode': 'schematic', 'request': 'bracket',
        'request_trace': [{'phrase': 'bracket', 'items': ['plate']}], 'sources': [{'id': 'd', 'kind': 'measurement', 'license': 'own'}],
        'dimensions': [{'id': 'dim.bolt', 'value_m': 0.02, 'tol_pct': 2, 'source_id': 'd', 'measure': 'x', 'part_ids': ['bolt']}],
        'features': [{'id': 'feat.bolt', 'description': 'bolt', 'part_ids': ['bolt'], 'verify': 'presence'}],
        'builders': [{'part_id': 'plate', 'builder': 'box', 'params': {}}, {'part_id': 'bolt', 'builder': 'box', 'params': {}}],
        'assembly_claims': [{'id': 'floats', 'type': 'no_floating'}]}
REASON = 'bolt is 8 px tall on a 1080 px portrait frame and cannot be read'


def geometry(bolt_x, gap):
    return {'whole': {}, 'parts': {'plate': {'objects': 1, 'features': []}, 'bolt': {'objects': 1, 'features': ['feat.bolt'], 'x': bolt_x}},
            'silhouettes': {}, 'screen_px': {}, 'assembly': [{'index': 0, 'type': 'no_floating', 'parts': {'bolt': {'gap_m': gap, 'nearest': 'plate'},
                                                                                                    'plate': {'gap_m': gap, 'nearest': 'bolt'}}}]}


class DeviationTest(unittest.TestCase):
    def with_deviations(self, *deviations, mode='schematic'):
        spec = deepcopy(SPEC); spec['subject_mode'] = mode; spec['deviations'] = list(deviations)
        return spec

    def test_factor_is_judged_not_waved_through(self):
        spec = self.with_deviations({'id': 'big_bolt', 'check': 'dimension:dim.bolt', 'factor': 1.5, 'reason': REASON})
        report = build_report(spec, geometry(0.03, 0.0), ROOT)
        row = next(c for c in report['checks'] if c['id'] == 'dim.bolt')
        self.assertTrue(report['passed'])
        self.assertEqual(row['deviation']['original_expected'], '0.02 m ±2%')
        self.assertFalse(row['deviation']['original_passed'])
        self.assertTrue(report['summary']['stylized'])
        self.assertEqual(report['unused_deviations'], [])
        self.assertFalse(build_report(spec, geometry(0.02, 0.0), ROOT)['passed'])  # declared 1.5x but built 1x: fails
        self.assertEqual(dimension_penalty(spec, geometry(0.03, 0.0))[1], [])
        self.assertEqual(dimension_penalty(spec, geometry(0.02, 0.0))[1], ['dim.bolt'])

    def test_waive_keeps_measurement_and_flags_unused(self):
        spec = self.with_deviations({'id': 'exploded', 'check': 'assembly:floats', 'waive': True, 'reason': 'exploded view: parts float apart on purpose'})
        report = build_report(spec, geometry(0.02, 0.2), ROOT)
        row = next(c for c in report['checks'] if c['kind'] == 'assembly')
        self.assertTrue(report['passed']); self.assertEqual(row['expected'], 'waived')
        self.assertIn('bolt', row['measured'])
        self.assertFalse(build_report(SPEC, geometry(0.02, 0.2), ROOT)['passed'])
        self.assertEqual(build_report(spec, geometry(0.02, 0.0), ROOT)['unused_deviations'], ['exploded'])

    def test_lint(self):
        errors = lambda *d, mode='schematic': lint_spec(self.with_deviations(*d, mode=mode))['errors']  # noqa: E731
        ok = {'id': 'a', 'check': 'dimension:dim.bolt', 'factor': 1.5, 'reason': REASON}
        self.assertEqual(errors(ok), [])
        self.assertTrue(any('no dimension check' in e for e in errors({**ok, 'check': 'dimension:dim.nut'})))
        self.assertTrue(any('takes exactly factor' in e for e in errors({'id': 'a', 'check': 'dimension:dim.bolt', 'waive': True, 'reason': REASON})))
        self.assertTrue(any('already has a deviation' in e for e in errors(ok, {**ok, 'id': 'b'})))
        self.assertTrue(any('user_evidence' in e for e in errors(ok, mode='specific_real')))
        self.assertFalse(any('user_evidence' in e for e in errors({**ok, 'factor': 1.2}, mode='specific_real')))
        self.assertFalse(any('user_evidence' in e for e in errors({**ok, 'user_evidence': '볼트 크게 보이게 해줘'}, mode='specific_real')))
        spec = self.with_deviations({**ok, 'reason': 'short'})
        with self.assertRaises(Exception):
            lint_spec(spec)


if __name__ == '__main__':
    unittest.main()
