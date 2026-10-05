from pathlib import Path
import shutil
import tempfile
import unittest

from studio.blender import build_shot
from studio.common import StudioError, read_json, write_json
from studio.fidelity import judge_claim, require_fidelity, spec_sha256

ROOT = Path(__file__).resolve().parents[1]


class FreshnessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / 'p'
        shutil.copytree(ROOT / 'tests/fixtures/winch_spec', self.project,
                        ignore=shutil.ignore_patterns('renders', 'workbench', 'runs', 'failed_*', 'scene.blend'))
        self.shot = read_json(self.project / 'shots/winch/shot.json')
        self.spec = read_json(self.project / 'subjects/winch/spec.json')
        vdir = self.project / 'shots/winch/versions' / self.shot['scene_version']
        write_json(vdir / 'fidelity_report.json', {'passed': True, 'subjects': [{'subject_id': 'winch', 'passed': True, 'failures': [],
                                                                               'spec_sha256': spec_sha256(self.spec)}]})

    def tearDown(self):
        self.tmp.cleanup()

    def test_report_must_match_current_spec(self):
        self.assertTrue(require_fidelity(self.project, self.shot)['passed'])
        self.spec['builders'][0]['params']['segments'] = 64
        write_json(self.project / 'subjects/winch/spec.json', self.spec)
        with self.assertRaises(StudioError) as caught:
            require_fidelity(self.project, self.shot, purpose='look render')
        self.assertEqual(caught.exception.code, 'FIDELITY_STALE')

    def test_legacy_report_without_hash_is_stale(self):
        vdir = self.project / 'shots/winch/versions' / self.shot['scene_version']
        write_json(vdir / 'fidelity_report.json', {'passed': True, 'subjects': [{'subject_id': 'winch', 'passed': True, 'failures': []}]})
        with self.assertRaises(StudioError) as caught:
            require_fidelity(self.project, self.shot)
        self.assertEqual(caught.exception.code, 'FIDELITY_STALE')

    def test_lint_errors_block_the_build_before_blender(self):
        self.spec['features'][0]['part_ids'] = ['no_such_part']
        write_json(self.project / 'subjects/winch/spec.json', self.spec)
        with self.assertRaises(StudioError) as caught:
            build_shot(self.project, 'winch', ROOT / 'tests/fixtures/winch_spec_inputs/author_winch.py')
        self.assertEqual(caught.exception.code, 'SUBJECT_SPEC_INVALID')
        self.assertFalse(list((self.project / 'shots/winch/versions').glob('failed_*')))


class ClaimJudgementTest(unittest.TestCase):
    def test_each_claim_type(self):
        ok = lambda claim, measured: judge_claim(claim, measured)[0]  # noqa: E731
        self.assertTrue(ok({'type': 'contact', 'a': 'p', 'b': 's'}, {'distance_m': 0.0, 'penetration_m': 0.0002}))
        self.assertFalse(ok({'type': 'contact', 'a': 'p', 'b': 's'}, {'distance_m': 0.002, 'penetration_m': 0.0}))
        self.assertFalse(ok({'type': 'contact', 'a': 'p', 'b': 's'}, {'distance_m': 0.0, 'penetration_m': 0.004}))
        self.assertFalse(ok({'type': 'no_interference', 'a': 'p', 'b': 's'}, {'distance_m': 0.0, 'penetration_m': None}))
        self.assertTrue(ok({'type': 'clearance', 'a': 'p', 'b': 's', 'value_m': 0.05}, {'distance_m': 0.06}))
        self.assertFalse(ok({'type': 'clearance', 'a': 'p', 'b': 's', 'value_m': 0.05}, {'distance_m': 0.04}))
        through = {'protrude_m': [0.0, 0.03], 'centred_in_b': True, 'penetration_m': 0.0}
        self.assertTrue(ok({'type': 'through', 'a': 'b', 'b': 'p', 'axis': 'z'}, through))
        self.assertFalse(ok({'type': 'through', 'a': 'b', 'b': 'p', 'axis': 'z'}, {**through, 'protrude_m': [-0.004, 0.03]}))
        self.assertFalse(ok({'type': 'cover', 'a': 'r', 'b': 'c', 'value_m': 0.04}, {'inside': True, 'cover_m': 0.035}))
        self.assertFalse(ok({'type': 'cover', 'a': 'r', 'b': 'c', 'value_m': 0.04}, {'inside': False, 'cover_m': 0.05}))
        passed, _, _, note = judge_claim({'type': 'no_floating'}, {'parts': {'a': {'gap_m': 0.0, 'nearest': 'b'}, 'c': {'gap_m': 0.0024, 'nearest': 'b'}}})
        self.assertFalse(passed); self.assertIn('c 2.4 mm from b', note)
        self.assertFalse(ok({'type': 'contact', 'a': 'p', 'b': 'x'}, {'missing': ['x']}))


if __name__ == '__main__':
    unittest.main()
