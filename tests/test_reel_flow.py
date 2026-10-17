"""The reel's pictures shot to shot (studio/reel_flow.py): read on the shot list and on the built shots' reports."""
from pathlib import Path
import tempfile
import unittest

from studio import reel_flow
from studio.common import write_json


def row(i, height=0.4, box=None, still=False, length=2.0, **extra):
    box = box or [0.4, 0.5 - height / 2, 0.6, 0.5 + height / 2]
    return {'shot_id': i, 'len_s': length, 'height': height, 'box': box, 'env': 0.0, 'fills': False, 'still': still, 'view_deg': 10.0, **extra}


def codes(found):
    return sorted({line.split(':')[0] for line in found})


class FlowTest(unittest.TestCase):
    def test_a_varied_reel_is_quiet(self):
        rows = [row('a', 0.3, still=True), row('b', 0.8, box=[0.1, 0.1, 0.5, 0.9]), row('c', 0.45, still=True), row('d', 0.9, box=[0.5, 0.05, 0.9, 0.95])]
        self.assertEqual(reel_flow.lines(rows), [])

    def test_one_picture_repeated_is_said(self):
        rows = [row(i, 0.97, box=[0.3, 0.01, 0.7, 0.98], length=4.0) for i in 'abcdef']
        found = codes(reel_flow.lines(rows))
        self.assertEqual(found, ['CLOSE_RUN', 'ESTABLISH_GAP', 'OVERFILL_SHARE', 'PACE_SLOW', 'SAME_PICTURE', 'STATIC_SHARE_LOW'])

    def test_a_cut_that_runs_on_or_departs_on_purpose_is_not_the_same_picture(self):
        a, b = row('a', 0.6), row('b', 0.6)
        self.assertIn('SAME_PICTURE', codes(reel_flow.lines([a, b, row('c', 0.3, still=True)])))
        self.assertNotIn('SAME_PICTURE', codes(reel_flow.lines([{**a, 'handover': 'continue'}, b, row('c', 0.3, still=True)])))
        self.assertNotIn('SAME_PICTURE', codes(reel_flow.lines([a, {**b, 'departs': ['height_share']}, row('c', 0.3, still=True)])))
        self.assertNotIn('SAME_PICTURE', codes(reel_flow.lines([a, {**b, 'view_deg': 50.0}, row('c', 0.3, still=True)])))

    def test_a_new_place_opens_wide_and_the_climax_cuts_fast(self):
        story = {'beats': [{'story_beat_id': 'h', 'function': 'hook'}, {'story_beat_id': 'p', 'function': 'climax'}]}
        planned = reel_flow.plan_rows([
            {'shot_id': 'a', 'line_ids': ['l1'], 'duration_s': 2, 'role': 'hook', 'handover': {'kind': 'new_place', 'why': 'x'}},
            {'shot_id': 'b', 'line_ids': ['l2'], 'duration_s': 5, 'role': 'explain', 'direction': {'scale_step': 'closer'}},
            {'shot_id': 'c', 'line_ids': [], 'duration_s': 1.5, 'role': 'close', 'direction': {'scale_step': 'wider'}}],
            {'l1': {'story_beat_id': 'h'}, 'l2': {'story_beat_id': 'p'}})
        found = reel_flow.lines(planned, story)
        self.assertIn('ESTABLISH_MISSING', codes(found))
        self.assertTrue(any('climax shots (b)' in f for f in found))
        planned[1]['scale_step'] = 'wider'
        self.assertNotIn('ESTABLISH_MISSING', codes(reel_flow.lines(planned, story)))

    def test_a_built_shot_is_read_from_its_reports(self):
        from studio.project import init_project, load_project, load_shot, shot_path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(init_project('flow', {'request': 'x', 'shots': [{'shot_id': 's1', 'frame_count': 30}]}, tmp)['project_path'])
            shot = load_shot(path, 's1'); shot['scene_version'] = 'v0001'; write_json(shot_path(path, 's1'), shot)
            folder = path / 'shots' / 's1' / 'versions' / 'v0001'
            folder.mkdir(parents=True)
            full = {'subject': {'bbox': [0.0, 0.0, 1.0, 1.0]}}
            write_json(folder / 'frame_report.json', {'frames': [{'frame': f, 'shapes': full} for f in range(0, 30, 5)],
                                                      'summary': {'support_share_median': 0.0}})
            sample = {'camera': [0, -5, 1], 'pitch_deg': 5.0, 'roll_deg': 0.0, 'lens_mm': 35.0, 'target_box': [0, 0, 1, 2]}
            write_json(folder / 'camera_rig_report.json', {'samples': [{**sample, 'frame': 0}, {**sample, 'frame': 29}]})
            got = reel_flow.built_row(path, load_project(path)['shots'][0], 30)
            self.assertEqual((got['height'], got['fills'], got['still'], got['view_deg']), (1.0, True, True, 5.0))


if __name__ == '__main__':
    unittest.main()
