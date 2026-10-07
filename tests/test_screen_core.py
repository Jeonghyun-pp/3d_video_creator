"""screen_core (studio/blender_ops/screen_core.py): declared screen targets against the probe's shapes and the projected
motion - only what a shot declares is judged, a miss is taste, a key part under the platform UI is broken."""
import math
import unittest

from studio.blender_ops import screen_core as core
from studio.gates import SOFTENABLE


def shape(x0, y0, x1, y1, outside=0, px=100):
    return {'px': px, 'share': round((x1 - x0) * (y1 - y0), 6), 'bbox': [x0, y0, x1, y1],
            'centroid': [(x0 + x1) / 2, (y0 + y1) / 2], 'outside_ui_px': outside}


def rows(subject_boxes, key_boxes=None):
    out = []
    for i, box in enumerate(subject_boxes):
        shapes = {'subject': shape(*box), 'all': shape(*box)}
        if key_boxes:
            shapes['key:valve'] = key_boxes[i]
        out.append({'frame': i * 10, 'shapes': shapes})
    return out


MOTION = {'stride': 10, 'frames': [0, 10, 20], 'centers': {'subject': [[0.2, 0.5], [0.3, 0.5], [0.4, 0.5]]}}


class ScreenCoreTest(unittest.TestCase):
    def test_metrics_read_the_shape(self):
        s = shape(0.1, 0.2, 0.5, 0.8)
        m = {k: fn(s) for k, (fn, _) in core.METRICS.items() if fn}
        self.assertAlmostEqual(m['center_x'], 0.3); self.assertAlmostEqual(m['center_y'], 0.5)
        self.assertAlmostEqual(m['width_share'], 0.4); self.assertAlmostEqual(m['height_share'], 0.6)
        self.assertAlmostEqual(m['edge_margin'], 0.1)
        self.assertAlmostEqual(m['thirds_distance'], math.dist((0.3, 0.5), (1 / 3, 1 / 3)))
        self.assertEqual(set(core.METRICS), {'center_x', 'center_y', 'width_share', 'height_share', 'area_share', 'edge_margin',
                                             'thirds_distance', 'ui_overlap', 'speed'})

    def test_only_declared_targets_are_judged_and_a_miss_is_taste(self):
        r = rows([(0.1, 0.2, 0.5, 0.8)] * 3)
        failures, summary = core.judge(r, MOTION, None, [], 30, 9 / 16)
        self.assertEqual(failures, []); self.assertEqual(summary['targets'], [])
        screen = {'targets': [{'id': 'left_third', 'metric': 'center_x', 'of': 'subject', 'value': 1 / 3, 'tol': 0.05},
                              {'id': 'tall', 'metric': 'height_share', 'of': 'subject', 'value': 0.4, 'tol': 0.1}]}
        failures, summary = core.judge(r, MOTION, screen, [], 30, 9 / 16)
        rows_by_id = {t['id']: t for t in summary['targets']}
        self.assertGreater(rows_by_id['left_third']['score'], 0.6)            # 0.3 against 1/3 ±0.05
        self.assertEqual([f['target'] for f in failures], ['tall'])           # 0.6 against 0.4 ±0.1
        self.assertEqual(SOFTENABLE['SCREEN_TARGET_MISSED'][0], 'taste')

    def test_speed_is_p95_of_the_projected_centre_in_frame_widths(self):
        steps = core.speeds(MOTION, 'subject', 9 / 16)
        self.assertEqual([round(v, 4) for v in steps], [0.01, 0.01])
        moving_y = {'stride': 1, 'frames': [0, 1], 'centers': {'subject': [[0.5, 0.2], [0.5, 0.3]]}}
        self.assertAlmostEqual(core.speeds(moving_y, 'subject', 9 / 16)[0], 0.1 * 16 / 9)   # a tall frame: y steps are longer in widths
        failures, summary = core.judge(rows([(0.1, 0.2, 0.5, 0.8)] * 3), MOTION, {'max_speed': 0.006}, [], 30, 9 / 16)
        self.assertEqual([f['code'] for f in failures], ['SCREEN_SPEED_HIGH'])
        self.assertAlmostEqual(summary['subject_speed_p95'], 0.01)

    def test_a_key_part_under_the_ui_everywhere_is_broken(self):
        under = shape(0.4, 0.8, 0.5, 0.9, outside=80)       # 80 % of its pixels below the 65 % line
        clear = shape(0.4, 0.4, 0.5, 0.5, outside=0)
        part = [{'id': 'valve'}]
        failures, _ = core.judge(rows([(0.1, 0.2, 0.5, 0.8)] * 2, [under, under]), {}, None, part, 20, 9 / 16)
        self.assertEqual([f['code'] for f in failures], ['KEY_PART_UNDER_UI'])
        self.assertEqual(SOFTENABLE['KEY_PART_UNDER_UI'][0], 'broken')
        failures, _ = core.judge(rows([(0.1, 0.2, 0.5, 0.8)] * 2, [under, clear]), {}, None, part, 20, 9 / 16)
        self.assertEqual(failures, [])                                         # it shows clear of the UI once: readable
        failures, _ = core.judge(rows([(0.1, 0.2, 0.5, 0.8)] * 2, [under, under]), {}, None, [{'id': 'valve', 'source': 'rig'}], 20, 9 / 16)
        self.assertEqual(failures, [])                                         # implied keys: where they sit is the shot's call

    def test_a_target_on_a_part_that_never_shows_is_missed(self):
        screen = {'targets': [{'id': 'v', 'metric': 'center_y', 'of': 'valve', 'value': 0.5, 'tol': 0.1}]}
        failures, summary = core.judge(rows([(0.1, 0.2, 0.5, 0.8)] * 2, [{'px': 0}, {'px': 0}]), {}, screen, [], 20, 9 / 16)
        self.assertEqual(failures[0]['code'], 'SCREEN_TARGET_MISSED'); self.assertEqual(summary['targets'][0]['score'], 0.0)




class UiRectTest(unittest.TestCase):
    def test_the_feed_ui_covers_vertical_outputs_only_unless_the_style_says(self):
        from studio.blender import ui_rect
        from studio.titles import TITLE_SAFE
        self.assertEqual(ui_rect({}, (1080, 1920)), list(TITLE_SAFE))
        self.assertEqual(ui_rect({}, (1400, 934)), [0.0, 0.0, 1.0, 1.0])   # engine_cutaway2's 3:2 still: no feed UI over it
        self.assertEqual(ui_rect({'title_safe_rect_normalized': [0.1, 0.1, 0.9, 0.9]}, (1400, 934)), [0.1, 0.1, 0.9, 0.9])




class LightTargetTest(unittest.TestCase):
    def test_light_targets_against_the_applied_rig(self):
        rig = [{'name': 'key', 'azimuth_deg': -60, 'elevation_deg': 20, 'stops_vs_key': 0.0}, {'name': 'fill', 'azimuth_deg': 40, 'elevation_deg': 15, 'stops_vs_key': -1.0}]
        failures, rows = core.judge_light({'key_azimuth_deg': -55, 'key_elevation_deg': 20, 'stops': {'fill': -2}}, rig)
        self.assertEqual([f['target'] for f in failures], ['light.fill_stops'])        # 1 stop under, asked 2 (±0.5)
        self.assertEqual(rows[0]['deviation'], -5)
        failures, _ = core.judge_light({'key_azimuth_deg': 175}, [{'name': 'key', 'azimuth_deg': -175, 'elevation_deg': 0, 'stops_vs_key': 0}])
        self.assertEqual(failures, [])                                                  # 10 deg apart across the wrap
        failures, _ = core.judge_light({'stops': {'rim': -1}}, rig)
        self.assertIn('no such light', failures[0]['hint'])


if __name__ == '__main__':
    unittest.main()
