from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from studio.common import StudioError, read_json
from studio.project import validate_shot

ROOT = Path(__file__).resolve().parents[1]


def existing_shots():
    """Local working projects first; on a fresh clone (projects/ is not tracked) the tracked examples and fixtures."""
    local = sorted(p for p in (ROOT / 'projects').glob('*/*/shots/*/shot.json')) + sorted((ROOT / 'projects').glob('*/shots/*/shot.json'))
    return local or sorted((ROOT / 'examples').glob('*/project/shots/*/shot.json')) + sorted((ROOT / 'tests/fixtures').glob('*/shots/*/shot.json'))


RIG = {'type': 'chase', 'subject': 'pursuer', 'offset_keys': [{'frame': 0, 'offset_m': [5, 8, 22]}]}


class CameraSchemaTest(unittest.TestCase):
    def base(self):
        shot = read_json(next(p for p in existing_shots() if not read_json(p).get('actions')))
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

    def test_move_is_exclusive_with_rig_and_needs_rig_movement(self):
        move = {'type': 'dive_through', 'params': {'opening': 'road.opening', 'below': 'concourse'}, 'style': 'archcutaway'}
        shot = self.base(); del shot['camera']['rig']; shot['camera']['move'] = move; validate_shot(shot)
        shot = self.base(); shot['camera']['move'] = move; self.assertRejected(shot)
        shot = self.base(); del shot['camera']['rig']; shot['camera']['move'] = move; shot['camera']['movement'] = 'authored'; self.assertRejected(shot)
        shot = self.base(); del shot['camera']['rig']; shot['camera']['move'] = {**move, 'type': 'barrel_roll'}; self.assertRejected(shot)
        shot = self.base(); del shot['camera']['rig']; shot['camera']['move'] = {**move, 'timing': {'profile': 'wobble'}}; self.assertRejected(shot)

    def test_flythrough_takes_speed_or_timing_and_keys_may_ease(self):
        fly = {'type': 'flythrough', 'path': 'route'}
        shot = self.base(); shot['camera']['rig'] = fly; self.assertRejected(shot)
        shot = self.base(); shot['camera']['rig'] = {**fly, 'speed_mps': 20}; validate_shot(shot)
        shot = self.base(); shot['camera']['rig'] = {**fly, 'timing': {'profile': 'burst_settle', 'burst_share': 0.6}}; validate_shot(shot)
        shot = self.base(); shot['camera']['rig']['lens_keys'] = [{'frame': 0, 'lens_mm': 20}, {'frame': 10, 'lens_mm': 28, 'ease': 'linear'}]
        validate_shot(shot)

    def test_several_camera_bound_actions_and_shared_colliders(self):
        shot = self.base(); del shot['camera']['rig']
        shot['camera']['move'] = {'type': 'dive_through', 'params': {'opening': 'road.opening', 'below': 'kiosk'}}
        bind = {'start_cue_id': 'cam-wp0', 'end_cue_id': 'cam-mouth', 'start_offset_frames': 0, 'end_offset_frames': 0}
        sim = lambda i: {'action_id': f'sim{i}', 'type': 'simulate', 'targets': [{'instance_id': 'road', 'part_id': 'road'}], 'start_frame': 0,
                         'end_frame': 1, 'easing': 'linear', 'time_binding': bind,
                         'params': {'kind': 'dust', 'region': [[0, 0, 0], [1, 1, 1]], 'count': 10}}
        shot['actions'] = [sim(1), sim(2)]
        validate_shot(shot)   # two cue-bound actions, one shared collider
        shot['actions'] = [sim(1), sim(1)]
        self.assertRejected(shot)   # duplicate ids still refused


if __name__ == '__main__':
    unittest.main()
