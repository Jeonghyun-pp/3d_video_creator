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


if __name__ == '__main__':
    unittest.main()
