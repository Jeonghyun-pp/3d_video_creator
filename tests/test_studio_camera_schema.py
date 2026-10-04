from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from studio.common import StudioError, read_json
from studio.project import validate_shot

ROOT = Path(__file__).resolve().parents[1]


def existing_shots():
    return sorted(p for p in (ROOT / 'projects').glob('*/*/shots/*/shot.json')) + sorted((ROOT / 'projects').glob('*/shots/*/shot.json'))


RIG = {'type': 'chase', 'subject': 'pursuer', 'offset_keys': [{'frame': 0, 'offset_m': [5, 8, 22]}]}


class CameraSchemaTest(unittest.TestCase):
    def base(self):
        shot = read_json(existing_shots()[0])
        shot = deepcopy(shot)
        shot['camera'] = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [], 'rig': deepcopy(RIG)}
        return shot

    def assertRejected(self, shot, code='INPUT_INVALID'):
        with self.assertRaises(StudioError) as ctx:
            validate_shot(shot)
        self.assertEqual(ctx.exception.code, code)

    def test_existing_shots_still_validate_and_are_untouched(self):
        paths = existing_shots()
        self.assertGreater(len(paths), 5)
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        for path in paths:
            shot = read_json(path)
            if 'rig' not in shot['camera']:
                validate_shot(shot)
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})

    def test_valid_rig(self):
        validate_shot(self.base())

    def test_rig_requires_rig_movement_and_no_keys(self):
        shot = self.base(); shot['camera']['movement'] = 'authored'; self.assertRejected(shot)
        shot = self.base(); shot['camera']['keys'] = [{'frame': 0, 'location': [0, 0, 1], 'target': [0, 1, 1]}]; self.assertRejected(shot)
        shot = self.base(); del shot['camera']['rig']; self.assertRejected(shot)

    def test_type_specific_requirements(self):
        shot = self.base(); del shot['camera']['rig']['subject']; self.assertRejected(shot)
        shot = self.base(); shot['camera']['rig'] = {'type': 'flythrough', 'speed_mps': 20}; self.assertRejected(shot)
        shot = self.base(); shot['camera']['rig'] = {'type': 'orbit', 'subject': 's'}; self.assertRejected(shot)
        shot = self.base(); shot['camera']['rig'] = {'type': 'procedural', 'subject': 's', 'script': '../escape.py'}; self.assertRejected(shot)
        shot = self.base(); shot['camera']['rig']['unknown'] = 1; self.assertRejected(shot)

    def test_rig_key_frames_inside_shot_and_ordered(self):
        shot = self.base(); shot['camera']['rig']['lens_keys'] = [{'frame': shot['duration_frames'], 'lens_mm': 20}]
        self.assertRejected(shot, 'TIMING_CONFLICT')
        shot = self.base(); shot['camera']['rig']['aim_keys'] = [{'frame': 5, 'blend': .2}, {'frame': 2, 'blend': .3}]
        self.assertRejected(shot)


if __name__ == '__main__':
    unittest.main()
