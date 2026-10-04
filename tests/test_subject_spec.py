from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

from studio.common import StudioError
from studio.fidelity import aligned_iou, best_orientation_iou, build_report, iou, model_mask, model_mask_scaled, reference_mask
from studio.subjects import lint_spec

ROOT = Path(__file__).resolve().parents[1]
SPEC = {'schema_version': 1, 'subject_id': 'fighter', 'identity': 'North American P-51D Mustang', 'subject_mode': 'specific_real',
        'request': 'realistic WWII P-51 fighter',
        'request_trace': [{'phrase': 'realistic', 'items': ['dim.length', 'feat.radiator']}, {'phrase': 'WWII P-51 fighter', 'items': ['dim.span', 'fuselage']}],
        'sources': [{'id': 'a', 'kind': 'dimension', 'license': 'facts'}, {'id': 'b', 'kind': 'dimension', 'license': 'facts'}],
        'dimensions': [{'id': 'dim.length', 'value_m': 9.83, 'tol_pct': 2, 'source_id': 'a', 'measure': 'length'},
                       {'id': 'dim.span', 'value_m': 11.28, 'tol_pct': 2, 'source_id': 'b', 'measure': 'width'}],
        'proportions': [{'id': 'prop.span_length', 'numerator': 'dim.span', 'denominator': 'dim.length', 'value': 1.1475, 'tol_pct': 3, 'source_id': 'a'}],
        'features': [{'id': 'feat.radiator', 'description': 'belly radiator scoop', 'part_ids': ['radiator'], 'verify': 'presence'},
                     {'id': 'feat.prop', 'description': 'four-blade propeller', 'part_ids': ['blade'], 'verify': 'count', 'count': 4}],
        'builders': [{'part_id': 'fuselage', 'builder': 'loft', 'params': {}}, {'part_id': 'radiator', 'builder': 'loft', 'params': {}},
                     {'part_id': 'blade', 'builder': 'wing', 'params': {}}]}


class SubjectSpecTest(unittest.TestCase):
    def errors(self, spec):
        return lint_spec(spec)['errors']

    def test_valid_spec(self):
        self.assertEqual(self.errors(deepcopy(SPEC)), [])

    def test_request_must_be_fully_traced(self):
        spec = deepcopy(SPEC); spec['request'] = 'realistic WWII P-51 fighter in a canyon'
        self.assertTrue(any('not traced' in e and 'canyon' in e for e in self.errors(spec)))
        spec = deepcopy(SPEC); spec['request_trace'][0]['items'] = []
        self.assertTrue(any('maps to nothing' in e for e in self.errors(spec)))
        spec = deepcopy(SPEC); spec['request_trace'].append({'phrase': 'jet engine', 'items': ['fuselage']})
        self.assertTrue(any('not in the request' in e for e in self.errors(spec)))

    def test_real_subject_needs_sourced_dimensions(self):
        spec = deepcopy(SPEC); spec['dimensions'][0]['source_id'] = 'assumed'
        self.assertTrue(any('assumed' in e for e in self.errors(spec)))
        spec = deepcopy(SPEC); spec['dimensions'][1]['source_id'] = 'a'
        self.assertTrue(any('2 independent sources' in e for e in self.errors(spec)))
        spec = deepcopy(SPEC); spec['subject_mode'] = 'fictional'; spec['dimensions'][0]['source_id'] = 'assumed'
        self.assertFalse(any('assumed' in e for e in self.errors(spec)))

    def test_verify_rules_and_references(self):
        spec = deepcopy(SPEC); del spec['features'][1]['count']
        self.assertTrue(any('needs count' in e for e in self.errors(spec)))
        spec = deepcopy(SPEC); spec['features'][0]['part_ids'] = ['wheel']
        self.assertTrue(any('unknown part' in e for e in self.errors(spec)))
        spec = deepcopy(SPEC); spec['features'][0]['verify'] = 'silhouette'
        self.assertTrue(any('no silhouettes' in e for e in self.errors(spec)))
        spec = deepcopy(SPEC); spec['sources'].append({'id': 'img', 'kind': 'drawing', 'license': 'unknown'})
        self.assertTrue(any('licence' in e for e in self.errors(spec)))
        spec = deepcopy(SPEC); del spec['features'][0]['verify']
        with self.assertRaises(StudioError):
            lint_spec(spec)

    def test_relation_and_claim_lint(self):
        spec = deepcopy(SPEC)
        spec['relations'] = [{'type': 'attach', 'a': 'radiator/-z', 'b': 'fuselage/-z'}]
        spec['assembly_claims'] = [{'type': 'contact', 'a': 'radiator', 'b': 'fuselage'}, {'type': 'no_floating'}]
        self.assertEqual(self.errors(spec), [])
        bad = deepcopy(spec); bad['relations'][0]['b'] = 'fuselage/nose_tip'
        self.assertTrue(any('unknown anchor' in e for e in self.errors(bad)))
        bad['builders'][0]['anchors'] = {'nose_tip': [0, 3, 0]}
        self.assertEqual(self.errors(bad), [])
        bad = deepcopy(spec); bad['relations'].append({'type': 'attach', 'a': 'fuselage', 'b': 'radiator'})
        self.assertTrue(any('cycle' in e for e in self.errors(bad)))
        bad = deepcopy(spec); bad['relations'] = [{'type': 'through', 'a': 'radiator', 'b': 'fuselage'}]
        self.assertTrue(any('needs axis' in e for e in self.errors(bad)))
        bad = deepcopy(spec); bad['assembly_claims'] = [{'type': 'clearance', 'a': 'radiator', 'b': 'fuselage'}, {'type': 'no_floating', 'b': 'blade'}]
        errors = self.errors(bad)
        self.assertTrue(any('needs value_m' in e for e in errors) and any('takes no b' in e for e in errors))
        bad = deepcopy(spec); bad['features'][0]['verify'] = 'assembly'; bad['assembly_claims'] = [{'type': 'contact', 'a': 'blade', 'b': 'fuselage'}]
        self.assertTrue(any('no assembly claim names its parts' in e for e in self.errors(bad)))
        bad = deepcopy(spec); bad['builders'][0]['dim_role'] = 'spaceship'
        self.assertTrue(any('dim_role' in e for e in self.errors(bad)))

    def test_report_checks_dimensions_counts_and_proportions(self):
        geometry = {'whole': {'length': 9.85, 'width': 11.2}, 'parts': {'fuselage': {'objects': 1, 'features': []},
                    'radiator': {'objects': 1, 'features': ['feat.radiator']}, 'blade': {'objects': 3, 'features': ['feat.prop']}},
                    'silhouettes': {}, 'screen_px': {}}
        report = build_report(SPEC, geometry, ROOT)
        self.assertFalse(report['passed'])
        self.assertEqual([f.split(':')[0] for f in report['failures']], ['feature feat.prop'])
        geometry['whole']['length'] = 8.8
        self.assertIn('dimension dim.length', ' '.join(build_report(SPEC, geometry, ROOT)['failures']))

    def test_silhouette_iou(self):
        with tempfile.TemporaryDirectory() as temporary:
            plane = Image.new('L', (400, 200), 255)
            draw = ImageDraw.Draw(plane); draw.polygon([(20, 100), (300, 60), (380, 100), (300, 140)], fill=0)
            path = Path(temporary) / 'ref.png'; plane.save(path)
            reference = reference_mask(path)
            tris = [[[0.0, 0.5], [2.8, 0.9], [3.6, 0.5]], [[0.0, 0.5], [3.6, 0.5], [2.8, 0.1]]]
            model = model_mask(tris)
            score, _ = best_orientation_iou(model, reference)
            self.assertGreater(score, 0.9)
            mirrored = model_mask([[[3.6 - x, y] for x, y in t] for t in tris])
            self.assertGreater(best_orientation_iou(mirrored, reference)[0], 0.9)
            fat = model_mask([[[0.0, 0.5], [2.8, 1.3], [3.6, 0.5]], [[0.0, 0.5], [3.6, 0.5], [2.8, -0.3]]])
            self.assertLess(iou(fat, reference), 0.8)

    def test_true_scale_alignment_ignores_partial_erasure(self):
        tris = [[[0.0, 0.0], [4.0, 0.0], [4.0, 1.0]], [[0.0, 0.0], [4.0, 1.0], [0.0, 1.0]]]  # 4 m x 1 m plate
        model = model_mask_scaled(tris, 50)
        reference = Image.new('L', (200, 50), 255)
        cut = reference.crop((20, 0, 200, 50))  # first 0.4 m erased in the drawing: bbox normalisation would stretch it
        self.assertGreater(aligned_iou(model, cut), 0.85)
        wrong = model_mask_scaled([[[0.0, 0.0], [4.0, 0.0], [4.0, 1.6]], [[0.0, 0.0], [4.0, 1.6], [0.0, 1.6]]], 50)
        self.assertLess(aligned_iou(wrong, reference), 0.7)

    def test_cli_lint(self):
        result = subprocess.run([sys.executable, '-m', 'studio', 'subject', 'lint', '--help'], cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
