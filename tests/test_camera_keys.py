"""Camera keys by context (studio/blender_ops/camera_keys.py) agree with the code: camera_rig_core.bake is run per rig
type and timing profile on recording dicts, the Blender side (camera_rig.py) is scanned for the rig keys it reads, and
shot validation refuses keys the context ignores."""
import ast
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'studio/blender_ops'))
import camera_keys as ck  # noqa: E402
import camera_rig_core as core  # noqa: E402


class Recording(dict):
    def __init__(self, data, log):
        super().__init__(data); self.log = log

    def get(self, key, default=None):
        self.log.add(key); return super().get(key, default)

    def __getitem__(self, key):
        self.log.add(key); return super().__getitem__(key)

    def __contains__(self, key):
        self.log.add(key); return super().__contains__(key)

    def items(self):   # iterating a dict reads every key in it
        self.log.update(self.keys()); return super().items()


FULL_TIMING = {'profile': 'burst_settle', 'burst_frac': 0.2, 'burst_share': 0.6, 'hold_frac': 0.2, 'drift': 0.02, 'head_frac': 0.3,
               'head_share': 0.1, 'scope': 'all', 'distance_m': 5.0, 'dwell': [{'u': 0.5, 'frac': 0.1}]}
EVERYTHING = {'aim_keys': [{'frame': 0, 'blend': 0.5}], 'lens_keys': [{'frame': 0, 'lens_mm': 35}], 'roll': {'follow_bank': 0.1, 'max_deg': 5},
              'smoothing': {'position_s': 0.1}, 'screen_anchor': {'x': 0.5, 'y': 0.5, 'weight': 0.5}, 'shake': {'seed': 1, 'amp_deg': 0.1, 'freq_hz': 1},
              'pitch_limit_deg': 80, 'framing': {'horizon_v': 0.4}}


def bake_reads(kind, timing=None, extra=None):
    rig_log, timing_log = set(), set()
    rig = {'type': kind, **EVERYTHING, **(extra or {})}
    if timing:
        rig['timing'] = Recording(dict(timing), timing_log)
    n = 12
    line = [(float(i), 0.0, 0.0) for i in range(n)]
    core.bake(Recording(rig, rig_log), 24, n, subject=None if kind == 'flythrough' else line, path=[(0, 0, 0), (20, 0, 0)],
              view=(36.0, 'AUTO', 1080, 1920), target=line)
    return rig_log, timing_log


def blender_side_keys():
    tree = ast.parse((ROOT / 'studio/blender_ops/camera_rig.py').read_text())
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == 'rig' and isinstance(node.slice, ast.Constant):
            keys.add(node.slice.value)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get' and isinstance(node.func.value, ast.Name)
                and node.func.value.id == 'rig' and node.args and isinstance(node.args[0], ast.Constant)):
            keys.add(node.args[0].value)
    return keys


class CameraKeysTest(unittest.TestCase):
    def test_rig_rows_match_what_is_read(self):
        extras = {'orbit': {'subject': 's', 'orbit': {'radius_m': 3, 'height_m': 1, 'start_deg': 0, 'deg_per_s': 10}, 'sweep_deg': 90},
                  'flythrough': {'path': 'p', 'speed_mps': 2, 'start_offset_m': 0, 'look_ahead_m': 2, 'offset_keys': [{'frame': 0, 'offset_m': [0, 0, 0]}]},
                  'chase': {'subject': 's', 'offset_keys': [{'frame': 0, 'offset_m': [0, 1, 4]}]},
                  'follow': {'subject': 's', 'offset_keys': [{'frame': 0, 'offset_m': [0, 1, 4]}]},
                  'procedural': {'subject': 's', 'script': 'x.py', 'offset_keys': [{'frame': 0, 'offset_m': [0, 1, 4]}]}}
        blender = blender_side_keys()
        all_rows = ck.RIG_ALWAYS | set().union(*ck.RIG_BY_TYPE.values())
        union = set(blender)
        for kind, extra in extras.items():
            read = bake_reads(kind, None, extra)[0] | bake_reads(kind, FULL_TIMING, extra)[0]
            row = ck.RIG_ALWAYS | ck.RIG_BY_TYPE[kind]
            self.assertLessEqual(read, row, f'{kind} reads keys outside its row')
            self.assertLessEqual(ck.RIG_BY_TYPE[kind], read | blender, f'{kind} row has keys nothing reads')
            union |= read
        self.assertEqual(union, all_rows)
        self.assertLessEqual(blender, all_rows)

    def test_conditional_reads(self):
        fly = {'path': 'p', 'speed_mps': 2}
        self.assertIn('speed_mps', bake_reads('flythrough', None, fly)[0])
        self.assertNotIn('speed_mps', bake_reads('flythrough', FULL_TIMING, fly)[0])
        orbit = {'subject': 's', 'orbit': {'radius_m': 3, 'height_m': 1, 'start_deg': 0, 'deg_per_s': 10}, 'sweep_deg': 90}
        self.assertNotIn('sweep_deg', bake_reads('orbit', None, orbit)[0])          # without timing deg_per_s sets the pace
        self.assertIn('sweep_deg', bake_reads('orbit', FULL_TIMING, orbit)[0])
        for profile, keys in ck.TIMING_BY_PROFILE.items():
            timing = {k: v for k, v in FULL_TIMING.items() if k in ck.TIMING_ALWAYS | keys}
            timing.update(profile=profile, **({'points': [[0, 0], [1, 1]]} if profile == 'points' else {}))
            read = bake_reads('flythrough', timing, fly)[1]
            self.assertEqual(read, ck.TIMING_ALWAYS | keys, profile)

    def test_validation_refuses_ignored_keys(self):
        sys.path.insert(0, str(ROOT))
        from studio.common import StudioError
        from studio.project import default_shot, validate_shot
        shot = default_shot('s', 30, {'request': 'x'})
        shot['camera'].update(movement='rig', move={'type': 'turntable', 'params': {'target': 'r'}, 'whip_in_deg': 30})
        with self.assertRaises(StudioError) as caught:
            validate_shot(shot)
        self.assertIn('whip_in_deg (an orbit has no path)', caught.exception.message)
        shot['camera']['move'] = {'type': 'slide', 'params': {'target': 'r'}, 'timing': {'profile': 'linear', 'burst_frac': 0.2}}
        with self.assertRaises(StudioError) as caught:
            validate_shot(shot)
        self.assertIn('timing/burst_frac', caught.exception.message)


if __name__ == '__main__':
    unittest.main()
