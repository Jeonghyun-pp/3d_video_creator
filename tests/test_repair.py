from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from studio import repair
from studio.common import StudioError, file_hash, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]


class RepairPolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / 'p'
        source = ROOT / 'projects/harness_validation/winch_spec'
        shutil.copytree(source, self.project, ignore=shutil.ignore_patterns('shots', 'renders', 'workbench', 'runs'))
        self.shot_dir = self.project / 'shots/winch'
        shot = read_json(source / 'shots/winch/shot.json')
        shot.update({'scene_version': None, 'revision': 1})
        write_json(self.shot_dir / 'shot.json', shot)
        self.shot = shot

    def tearDown(self):
        self.tmp.cleanup()

    def version(self, name, passed, iou, failures=0, deviations=None):
        d = self.shot_dir / 'versions' / name
        d.mkdir(parents=True)
        write_json(d / 'subjects/winch.spec.json', {'subject_id': 'winch', **({'deviations': deviations} if deviations else {})})
        (d / 'scene.blend').write_bytes(name.encode())
        write_json(d / 'dependencies.json', {'scene_sha256': file_hash(d / 'scene.blend')})
        write_json(d / 'shot.snapshot.json', {**self.shot, 'scene_version': name, 'revision': 2})
        write_json(d / 'fidelity_report.json', {'passed': passed, 'subjects': [{'subject_id': 'winch', 'passed': passed, 'failures': ['x'] * failures,
                                                 'summary': {'failures_n': failures, 'mean_silhouette_iou': iou}}]})
        shot = read_json(self.shot_dir / 'shot.json'); shot['scene_version'] = name; shot['revision'] += 1
        write_json(self.shot_dir / 'shot.json', shot)
        return name

    def current(self):
        return read_json(self.shot_dir / 'shot.json')['scene_version']

    def test_regression_reverts_and_budget_stops(self):
        first = repair.record(self.project, 'winch', self.version('v0001', False, 0.80, 2), diagnosis='wing too short')
        self.assertEqual(first['outcome'], 'new_best')
        better = repair.record(self.project, 'winch', self.version('v0002', False, 0.85, 1), base='v0001', diagnosis='tail too wide')
        self.assertEqual(better['outcome'], 'new_best')
        worse = repair.record(self.project, 'winch', self.version('v0003', False, 0.70, 3), base='v0002', diagnosis='nose')
        self.assertEqual((worse['outcome'], worse['reverted_to']), ('regressed', 'v0002'))
        self.assertEqual(self.current(), 'v0002')
        repair.record(self.project, 'winch', self.version('v0004', False, 0.85, 1), base='v0002')  # equal while failing: counts
        repair.record(self.project, 'winch', None, base='v0002', error='LOOK_QA_FAILED')         # failed build: counts
        with self.assertRaises(StudioError) as caught:
            repair.check_budget(self.project, 'winch')
        self.assertEqual(caught.exception.code, 'REPAIR_BUDGET_EXHAUSTED')
        self.assertEqual(read_json(repair.ledger_path(self.project, 'winch'))['attempts'][2]['diagnosis'], 'nose')
        with self.assertRaises(StudioError):
            repair.reset(self.project, 'winch', 'ok')
        repair.reset(self.project, 'winch', 'user: try splitting the canopy into two parts')
        repair.check_budget(self.project, 'winch')

    def test_passing_shot_equal_builds_do_not_spend_budget(self):
        repair.record(self.project, 'winch', self.version('v0001', True, 0.95))
        for i in range(2, 7):
            decision = repair.record(self.project, 'winch', self.version(f'v000{i}', True, 0.95), base='v0001')
            self.assertEqual(decision['outcome'], 'equal')
        repair.check_budget(self.project, 'winch')
        self.assertEqual(self.current(), 'v0006')
        decision = repair.record(self.project, 'winch', self.version('v0007', False, 0.90, 1))
        self.assertEqual(decision['reverted_to'], 'v0001')

    def test_declared_intent_starts_a_new_baseline(self):
        repair.record(self.project, 'winch', self.version('v0001', True, 0.99))
        styled = [{'id': 'tall', 'check': 'dimension:dim.h', 'factor': 1.2, 'reason': 'raised so the window reads'}]
        decision = repair.record(self.project, 'winch', self.version('v0002', True, 0.80, deviations=styled))
        self.assertEqual(decision['outcome'], 'intent_changed'); self.assertNotIn('reverted_to', decision)
        self.assertEqual(self.current(), 'v0002')
        worse = repair.record(self.project, 'winch', self.version('v0003', True, 0.70, deviations=styled))  # same intent, lower
        self.assertEqual(worse['reverted_to'], 'v0002')

    def test_build_shot_runs_the_policy(self):
        from studio import blender
        def fake_build(path, shot_id, *args):
            return {'scene_version': self.version('v0009', False, 0.5, 4), 'warnings': []}
        repair.record(self.project, 'winch', self.version('v0008', True, 0.9))
        with mock.patch.object(blender, '_build_shot', side_effect=fake_build):
            result = blender.build_shot(self.project, 'winch', 'x.py', base='v0008', diagnosis='try')
        self.assertEqual(result['repair']['reverted_to'], 'v0008')
        self.assertTrue(any('REPAIR_REVERTED' in w for w in result['warnings']))


if __name__ == '__main__':
    unittest.main()
