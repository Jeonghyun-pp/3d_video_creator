"""project init keeps every shot field a brief carries (not a list of the ones known today) and refuses keys that are
neither shot fields nor read by init - nothing the user wrote disappears silently (2026-10-07: key_parts dropped)."""
import tempfile
import unittest

from studio.common import StudioError
from studio.project import init_project, load_shot


class ProjectInitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_shot_field_in_the_brief_is_kept(self):
        shot = {'shot_id': 's01', 'frame_count': 60, 'key_parts': [{'id': 'engine/head_cover', 'min_px': 40}],
                'titles': [], 'narration': {'text': '엔진이 돈다'}, 'route_features': ['exact_geometry']}
        path = init_project('p', {'request': 'engine', 'shots': [shot]}, self.tmp.name)['project_path']
        saved = load_shot(path, 's01')
        self.assertEqual(saved['key_parts'], shot['key_parts'])
        self.assertEqual(saved['titles'], [])
        self.assertEqual(saved['narration']['text'], '엔진이 돈다')     # brief value over the default
        self.assertEqual(saved['narration']['cues'], [])               # a default the brief did not mention stays
        self.assertEqual(saved['duration_frames'], 60)
        self.assertNotIn('route_features', saved)                       # read by init, not a shot field

    def test_a_key_that_is_not_a_shot_field_is_refused(self):
        with self.assertRaises(StudioError) as caught:
            init_project('q', {'request': 'engine', 'shots': [{'shot_id': 's01', 'keyparts': []}]}, self.tmp.name)
        self.assertIn("['keyparts']", caught.exception.message)
        self.assertIn('key_parts', caught.exception.message)   # the error lists what is read


if __name__ == '__main__':
    unittest.main()
