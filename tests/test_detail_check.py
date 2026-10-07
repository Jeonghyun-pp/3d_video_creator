"""fidelity detail check (SKILL #8): a visible coarse primitive fails unless its builder says why the real part is plain;
a fine mesh passes; the policy can only move the failure to a warning."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from studio.fidelity import DETAIL_PX_PER_FACE, build_report


def spec(plain=None):
    head = {'part_id': 'head', 'builder': 'box', 'params': {'size': [1, 1, 1]}}
    if plain:
        head['plain'] = plain
    return {'subject_id': 'e', 'identity': 'engine', 'subject_mode': 'schematic', 'dimensions': [], 'features': [],
            'builders': [head, {'part_id': 'block', 'builder': 'casting', 'params': {}}]}


GEOMETRY = {'parts': {}, 'silhouettes': {}, 'detail': {
    'head': {'object': 'e/head', 'directions': 6, 'screen_px': 600.0, 'px_per_face': round(600 / 6 ** 0.5, 2)},
    'block': {'object': 'e/block', 'directions': 13000, 'screen_px': 800.0, 'px_per_face': round(800 / 13000 ** 0.5, 2)}}}


class DetailCheckTest(unittest.TestCase):
    def report(self, plain=None, gates=None):
        with tempfile.TemporaryDirectory() as tmp:
            if gates:
                (Path(tmp) / 'project.json').write_text(json.dumps({'policy': {'gates': gates}}))
            return build_report(spec(plain), GEOMETRY, tmp)

    def test_a_coarse_visible_part_fails_even_on_a_schematic_subject(self):
        report = self.report()
        rows = {c['id']: c for c in report['checks'] if c['kind'] == 'detail'}
        self.assertEqual(set(rows), {'head'})   # the fine casting makes no row
        self.assertFalse(rows['head']['passed'])
        self.assertIn('SKILL #8', rows['head']['note'])
        self.assertFalse(report['passed'])
        self.assertGreater(rows['head']['measured'], DETAIL_PX_PER_FACE)

    def test_plain_reason_passes_and_is_reported(self):
        row = next(c for c in self.report(plain='a cast iron slab, flat on every side')['checks'] if c['kind'] == 'detail')
        self.assertTrue(row['passed'])
        self.assertIn('cast iron slab', row['note'])

    def test_policy_moves_it_to_a_warning_only_when_asked(self):
        report = self.report(gates={'detail_placeholder': 'warn'})
        self.assertTrue(report['passed'])
        self.assertTrue(any(a.startswith('detail head') for a in report['advisories']))


if __name__ == '__main__':
    unittest.main()
