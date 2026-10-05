from pathlib import Path
import shutil
import tempfile
import unittest

from studio import exemplars
from studio.common import StudioError, read_json, write_json
from studio.fidelity import spec_sha256

ROOT = Path(__file__).resolve().parents[1]


class ExemplarTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / 'p'
        self.library = Path(self.tmp.name) / 'library'
        shutil.copytree(ROOT / 'tests/fixtures/winch_spec', self.project,
                        ignore=shutil.ignore_patterns('renders', 'workbench', 'runs', 'failed_*', 'scene.blend'))
        self.shot = read_json(self.project / 'shots/winch/shot.json')
        self.spec = read_json(self.project / 'subjects/winch/spec.json')
        self.report_path = self.project / 'shots/winch/versions' / self.shot['scene_version'] / 'fidelity_report.json'

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, passed, sha=None):
        write_json(self.report_path, {'passed': passed, 'subjects': [{'subject_id': 'winch', 'passed': passed, 'failures': [],
                                                                    'summary': {'failures_n': 0}, 'spec_sha256': sha or spec_sha256(self.spec)}]})

    def test_promote_requires_passing_current_spec(self):
        self.report(False)
        with self.assertRaises(StudioError):
            exemplars.promote(self.project, 'winch', self.library)
        self.report(True, sha='0' * 64)  # passed, but for another spec
        with self.assertRaises(StudioError):
            exemplars.promote(self.project, 'winch', self.library)
        self.report(True)
        entry = exemplars.promote(self.project, 'winch', self.library)['exemplar']
        self.assertEqual((entry['version'], entry['exemplar_id']), (1, 'winch'))
        self.assertIn('revolve', entry['builders']); self.assertIn('sweep', entry['builders'])
        self.assertEqual(exemplars.promote(self.project, 'winch', self.library)['exemplar']['version'], 2)
        self.assertEqual(len(read_json(self.library / 'index.json')['exemplars']), 1)

    def test_search_and_init(self):
        self.report(True)
        exemplars.promote(self.project, 'winch', self.library)
        hits = exemplars.search('winch drum hand wheel', library=self.library)['results']
        self.assertEqual(hits[0]['exemplar_id'], 'winch')
        self.assertEqual(exemplars.search('fighter aircraft', library=self.library)['results'], [])
        made = exemplars.init_from_exemplar(self.project, 'hoist', 'winch', 'mine hoist drum', '광산 권양기 드럼', self.library)
        spec = read_json(made['spec_path'])
        self.assertEqual((spec['subject_id'], spec['request']), ('hoist', '광산 권양기 드럼'))
        self.assertEqual(spec['request_trace'][0]['items'], [])  # the new request must be decomposed again


if __name__ == '__main__':
    unittest.main()
