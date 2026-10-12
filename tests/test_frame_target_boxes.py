"""Full-frame element bounds include separately classified pieces of the same element."""
import unittest

from studio.frame_targets import _box_at


class FrameTargetBoxTest(unittest.TestCase):
    def test_parent_and_child_boxes_are_unioned(self):
        row = {'shapes': {'key:booster': {'bbox': [0.4, 0.1, 0.6, 0.8]},
                          'key:booster/landing_assembly': {'bbox': [0.2, 0.7, 0.8, 0.95]},
                          'subject': {'bbox': [0, 0, 1, 1]}}}
        self.assertEqual(_box_at(row, ['booster']), [0.2, 0.1, 0.8, 0.95])

    def test_descendants_without_a_parent_class_still_form_the_element(self):
        row = {'shapes': {'key:booster/engine/nozzle': {'bbox': [0.4, 0.5, 0.6, 0.8]},
                          'key:booster.0': {'bbox': [0.3, 0.2, 0.5, 0.6]},
                          'key:booster/hidden': {'bbox': None},
                          'subject': {'bbox': [0, 0, 1, 1]}}}
        self.assertEqual(_box_at(row, ['booster'], fallback=False), [0.3, 0.2, 0.6, 0.8])

    def test_similarly_prefixed_siblings_are_excluded(self):
        row = {'shapes': {'key:booster/leg': {'bbox': [0.4, 0.5, 0.6, 0.8]},
                          'key:boosters/leg': {'bbox': [0, 0, 1, 1]},
                          'key:booster_aux/leg': {'bbox': [0, 0, 1, 1]}}}
        self.assertEqual(_box_at(row, ['booster']), [0.4, 0.5, 0.6, 0.8])
        row['shapes']['key:booster/leg_extra'] = {'bbox': [0, 0, 1, 1]}
        self.assertEqual(_box_at(row, ['booster/leg']), [0.4, 0.5, 0.6, 0.8])

    def test_subject_fallback_is_used_only_when_enabled_and_no_element_shows(self):
        row = {'shapes': {'key:booster/leg': {'bbox': None},
                          'subject': {'bbox': [0.1, 0.2, 0.9, 0.8]}}}
        self.assertIsNone(_box_at(row, ['booster'], fallback=False))
        self.assertEqual(_box_at(row, ['booster']), [0.1, 0.2, 0.9, 0.8])
        self.assertIsNone(_box_at({'shapes': {}}, ['booster']))


if __name__ == '__main__':
    unittest.main()
