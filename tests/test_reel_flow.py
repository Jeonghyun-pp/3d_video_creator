"""The reel's pictures shot to shot (studio/reel_flow.py): read on the shot list and on the built shots' reports."""
from pathlib import Path
import tempfile
import unittest

from studio import reel_flow
from studio.common import write_json


def mask(x0, x1, y0=0, y1=10, w=10):
    return {y * w + x for y in range(y0, y1) for x in range(x0, x1)}


def row(i, height=0.4, still=False, length=2.0, sil=None, view=(10.0, 0.0), **extra):
    sil = sil if sil is not None else mask(4, 6)
    return {'shot_id': i, 'len_s': length, 'height': height, 'env': 0.0, 'fills': False, 'still': still,
            'silhouette_start': sil, 'silhouette_end': sil, 'view_start': list(view), 'view_end': list(view), **extra}


def codes(found):
    return sorted({line.split(':')[0] for line in found})


class FlowTest(unittest.TestCase):
    def test_a_varied_reel_is_quiet(self):
        rows = [row('a', 0.3, still=True, sil=mask(0, 3)), row('b', 0.8, sil=mask(1, 9), view=(10, 90)),
                row('c', 0.45, still=True, sil=mask(6, 9)), row('d', 0.9, sil=mask(2, 8, 0, 5), view=(-40, 200))]
        self.assertEqual(reel_flow.lines(rows), [])

    def test_one_picture_repeated_is_said(self):
        rows = [row(i, 0.97, length=4.0) for i in 'abcdef']
        found = codes(reel_flow.lines(rows))
        self.assertEqual(found, ['CLOSE_RUN', 'ESTABLISH_GAP', 'OVERFILL_SHARE', 'PACE_SLOW', 'SAME_PICTURE', 'STATIC_SHARE_LOW'])

    def test_a_cut_that_runs_on_or_departs_on_purpose_is_not_the_same_picture(self):
        a, b, c = row('a', 0.6), row('b', 0.6), row('c', 0.3, still=True, sil=mask(0, 2))
        self.assertIn('SAME_PICTURE', codes(reel_flow.lines([a, b, c])))
        self.assertNotIn('SAME_PICTURE', codes(reel_flow.lines([{**a, 'handover': 'continue'}, b, c])))
        self.assertNotIn('SAME_PICTURE', codes(reel_flow.lines([a, {**b, 'departs': ['same_picture']}, c])))
        # an item that names another finding silences nothing here (departures are read for what they name)
        self.assertIn('SAME_PICTURE', codes(reel_flow.lines([a, {**b, 'departs': ['close_run']}, c])))
        self.assertNotIn('SAME_PICTURE', codes(reel_flow.lines([a, {**b, 'view_start': [10.0, 50.0]}, c])))   # turned 50 degrees
        self.assertNotIn('SAME_PICTURE', codes(reel_flow.lines([a, {**b, 'view_start': None}, c])))           # unknown is not same

    def test_an_unbuilt_shot_breaks_the_cut_beside_it(self):
        gap = {'shot_id': 'g', 'len_s': 2.0}
        self.assertNotIn('SAME_PICTURE', codes(reel_flow.lines([row('a', 0.6), gap, row('b', 0.6), row('c', 0.3, still=True, sil=mask(0, 2))])))

    def test_every_finding_can_be_departed_by_a_shot_or_the_reel(self):
        rows = [row(i, 0.97, length=4.0, view=(10.0, 40.0 * n)) for n, i in enumerate('abcdef')]
        self.assertIn('OVERFILL_SHARE', codes(reel_flow.lines(rows)))
        departed = [{**r, 'departs': ['overfill_share', 'static_share_low', 'pace_slow', 'close_run']} for r in rows]
        self.assertEqual(codes(reel_flow.lines(departed)), ['ESTABLISH_GAP'])
        self.assertEqual(codes(reel_flow.lines(rows, silenced={'OVERFILL_SHARE', 'STATIC_SHARE_LOW', 'PACE_SLOW', 'CLOSE_RUN'})),
                         ['ESTABLISH_GAP'])
        self.assertEqual(set(reel_flow.DEPART_ITEMS.values()), set(reel_flow.DEPARTS))

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

    def test_stillness_is_travel_against_distance_turn_and_zoom(self):
        pose = lambda pos, aim, lens=35.0: {'position': pos, 'aim': aim, 'lens_mm': lens, 'view': [0, 0]}   # noqa: E731
        held = {'frames': [pose([0, -10, 1], [0, 0, 1]), pose([0, -10.1, 1], [0, 0, 1])]}
        self.assertTrue(reel_flow._still(None, held, None))
        panned = {'frames': [pose([0, -10, 1], [0, 0, 1]), pose([0, -10, 1], [3, 0, 1])]}
        self.assertFalse(reel_flow._still(None, panned, None))   # only a pan: it moves
        zoomed = {'frames': [pose([0, -10, 1], [0, 0, 1]), pose([0, -10, 1], [0, 0, 1], 70.0)]}
        self.assertFalse(reel_flow._still(None, zoomed, None))
        self.assertIsNone(reel_flow._still({'samples': [{'camera': [0, 0, 0], 'pitch_deg': 0}]}, None, None))   # no direction: unknown
        self.assertTrue(reel_flow._still(None, panned, {'camera': {'axis': 'still'}}))   # the camera's own measure first

    def test_a_built_shot_is_read_from_its_reports(self):
        from PIL import Image
        from studio.project import init_project, load_project, load_shot, shot_path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(init_project('flow', {'request': 'x', 'shots': [{'shot_id': 's1', 'frame_count': 30}]}, tmp)['project_path'])
            shot = load_shot(path, 's1'); shot['scene_version'] = 'v0001'; write_json(shot_path(path, 's1'), shot)
            folder = path / 'shots' / 's1' / 'versions' / 'v0001'
            (folder / 'frame_probe').mkdir(parents=True)
            full = {'subject': {'bbox': [0.0, 0.0, 1.0, 1.0]}}
            write_json(folder / 'frame_report.json', {'frames': [{'frame': f, 'shapes': full} for f in range(0, 30, 5)],
                                                      'summary': {'support_share_median': 0.0, 'key_parts': ['detail', 'room'],
                                                                  'key_sources': {'detail': 'details', 'room': 'context'}}})
            pose = {'position': [0, -5, 1], 'aim': [0, 0, 1], 'lens_mm': 35.0, 'view': [5.0, 0.0]}
            write_json(folder / 'camera_move_report.json', {'frames': [{**pose, 'at': 0.0}, {**pose, 'at': 1.0}]})
            picture = Image.new('RGB', (4, 2), (0, 0, 0))
            picture.putpixel((0, 0), (127, 127, 127)); picture.putpixel((1, 0), (0, 0, 127)); picture.putpixel((2, 1), (255, 0, 0))
            picture.putpixel((3, 0), (0, 0, 255))   # the room: a context element with a class of its own, not the focus
            picture.save(folder / 'frame_probe' / 'frame_0000_id.png'); picture.save(folder / 'frame_probe' / 'frame_0029_id.png')
            got = reel_flow.built_row(path, load_project(path)['shots'][0], 30)
            self.assertEqual((got['height'], got['fills'], got['still'], got['view_start']), (1.0, True, True, [5.0, 0.0]))
            self.assertEqual(got['silhouette_start'], {1, 6})   # the subject and its detail; not the room, the support, nothing


if __name__ == '__main__':
    unittest.main()
