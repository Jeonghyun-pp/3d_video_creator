"""Views in the subject's own terms (studio/views.py): 2026-10-23, r3 - every cut of an 83 m hull lying along y was
framed from 25-50 deg, its stern quarter, by a director who could not tell, and every recipe default was there too."""
from pathlib import Path
import tempfile
import unittest

from studio import views
from studio.common import write_json
from studio.recipe_moves import book

HULL = ((0.0, 1.0), 1.0)   # length along y, its front (bow) at +y


class ViewsTest(unittest.TestCase):
    def test_an_azimuth_is_said_as_the_side_it_sees(self):
        self.assertEqual(views.words(30, HULL), 'from the back quarter, on its right')   # r3's 'whole boat' framing
        self.assertEqual(views.words(90, HULL), 'from the side, on its right')
        self.assertEqual(views.words(135, HULL), 'from the front quarter, on its right')
        self.assertEqual(views.words(180, HULL), 'from ahead')
        self.assertEqual(views.words(-90, HULL), 'from the side, on its left')
        self.assertEqual(views.words(10, ((0.0, 1.0), None)), 'from end-on')            # no front: how far off its length
        self.assertIsNone(views.words(10, None))

    def test_the_broad_side_follows_the_subjects_turn(self):
        self.assertEqual(views.side_azimuth(HULL), 90.0)
        with tempfile.TemporaryDirectory() as tmp:
            write_json(Path(tmp) / 'subjects/boat/spec.json', {'subject_id': 'boat', 'axes': {'length': 'y', 'width': 'x', 'height': 'z'},
                                                              'front': '+length'})
            turned = {'scene': {'instances': [{'subject': 'boat', 'rot_z_deg': 90}]}}
            frame = views.subject_frame(tmp, turned, 'boat/tank')
            self.assertAlmostEqual(frame[0][0], -1.0, places=6)                           # its length now along -x
            self.assertEqual(views.words(views.side_azimuth(frame), frame), 'from the side, on its right')
            self.assertIsNone(views.subject_frame(tmp, {}, 'nothing'))

    def test_recipes_default_to_the_side_not_a_world_number(self):
        for rid, r in book().items():
            if (r.get('make') or {}).get('choose', {}).get('azimuth_deg'):
                self.assertEqual(r['make']['choose']['azimuth_deg'][2], 'side', rid)


if __name__ == '__main__':
    unittest.main()
