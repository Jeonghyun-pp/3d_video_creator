"""Openings read along the shot (studio/blender_ops/show_core.faces_away)."""
import unittest

from studio.blender_ops.show_core import faces_away


class FacesAwayTest(unittest.TestCase):
    """2026-10-23, r3 s13: a slide swept round a hull cut open toward one side; the tank read edge on most of the shot."""

    def report(self, readable):
        def row(f, ok):
            box = [0.4, 0.4, 0.6, 0.5] if ok else [0.5, 0.45, 0.505, 0.455]
            return {'frame': f, 'shapes': {'key:boat/tank': {'px': 100, 'bbox': box}}}
        return {'frames': [row(f, ok) for f, ok in zip(range(0, 70, 10), readable)]}

    SHOW = {'openings': [{'how': 'cutaway', 'parts': ['boat/tank'], 'open': [0, 0], 'close': None}]}

    def test_an_opening_the_camera_mostly_looks_past_is_said(self):
        lines = faces_away(self.report([True, False, False, False, False, True, False]), self.SHOW, (1080, 1920))
        self.assertEqual([l['code'] for l in lines], ['OPENING_FACES_AWAY'])
        self.assertEqual(faces_away(self.report([True] * 5 + [False] * 2), self.SHOW, (1080, 1920)), [])
        self.assertEqual(faces_away(self.report([False] * 7), self.SHOW, (1080, 1920)), [])   # never read: another line's
        self.assertEqual(faces_away(self.report([True] * 7), {'openings': [{**self.SHOW['openings'][0], 'how': 'exterior'}]},
                                    (1080, 1920)), [])


class OneMomentTest(unittest.TestCase):
    """2026-10-26, semiconductor_fab s10: a cutaway and two outside rows at `start` were read as three moments in turn; the
    outside one after the cutaway shut it and the box never opened."""

    SHOW = [{'at': 'start', 'parts': ['box/wafers'], 'how': 'cutaway', 'why': 'w'},
            {'at': 'start', 'parts': ['speck'], 'how': 'exterior', 'why': 'w'},
            {'at': 'start', 'parts': ['tool'], 'how': 'exterior', 'why': 'w'},
            {'at': 0.6, 'parts': ['speck'], 'how': 'exterior', 'why': 'w'}]

    def test_rows_at_one_at_are_one_moment_led_by_its_opening(self):
        from studio.blender_ops.show_core import held_at, moment_at, moment_parts, moment_rows, openings
        rows = moment_rows(self.SHOW)
        self.assertEqual([(r['at'], r['how'], r['parts']) for r in rows], [('start', 'cutaway', ['box/wafers']), (0.6, 'exterior', ['speck'])])
        self.assertEqual([(o['first'], o['closes']) for o in openings(rows)], [(0, 1)])   # open over the start, shut at 0.6
        self.assertEqual(held_at(self.SHOW, 0.3)['how'], 'cutaway')
        self.assertEqual(moment_at(self.SHOW, 0.0)['how'], 'cutaway')
        self.assertEqual(moment_parts(self.SHOW, 0.3), ['box/wafers', 'speck', 'tool'])   # seen beside it, never cut

    def test_the_build_keeps_what_the_moment_also_shows(self):
        from studio.blender_ops.camera_moves_core import show_moments
        shot = {'shot_id': 's10', 'show': self.SHOW, 'camera': {'move': {'type': 'slide', 'params': {'elevation_deg': 20, 'azimuth_deg': 30}}}}
        moments = show_moments(shot)
        self.assertEqual(len(moments), 2)
        self.assertEqual(moments[0]['must_see'], ['box/wafers', 'speck', 'tool'])

    def test_two_ways_of_opening_at_one_moment_are_named(self):
        from studio.blender_ops.show_core import split_moments
        show = [*self.SHOW, {'at': 'start', 'parts': ['box/door'], 'how': 'section', 'why': 'w'}]
        self.assertEqual(split_moments(show), [('start', ['cutaway', 'section'])])
        self.assertEqual(split_moments(self.SHOW), [])


if __name__ == '__main__':
    unittest.main()
