"""What an author read of its shot (blender_ops/read_record): every read a path, compared between two shots. A
lamp-post author, not the scene the rule came from (N+1)."""
import copy
import unittest

from studio.blender_ops import read_record as rr


class ReadRecordTest(unittest.TestCase):
    def shot(self):
        return {'duration_frames': 48, 'camera': {'move': {'frames': [{'at': 0.0, 'tilt_deg': -12.0}, {'at': 1.0}]}},
                'subjects': [{'subject_id': 'post'}]}

    def test_reads_are_paths_and_only_those_compare(self):
        reads = set()
        shot = rr.recorded(self.shot(), reads)
        lean = [f.get('tilt_deg') for f in shot['camera']['move']['frames']]   # the author leans its post by the pictures
        self.assertEqual(lean, [-12.0, None])
        rows = rr.as_json(reads)
        self.assertIn(['value', ['camera', 'move', 'frames', 0, 'tilt_deg']], rows)
        self.assertIn(['value', ['camera', 'move', 'frames', 1, 'tilt_deg']], rows)   # looked for and missing: a read too
        self.assertIn(['len', ['camera', 'move', 'frames']], rows)
        now = self.shot()
        now['duration_frames'] = 60                                                   # not read: no change
        self.assertEqual(rr.changed(rows, self.shot(), now), [])
        now['camera']['move']['frames'][0]['tilt_deg'] = -20.0
        now['camera']['move']['frames'][1]['tilt_deg'] = 5.0
        self.assertEqual(rr.changed(rows, self.shot(), now), ['camera/move/frames/0/tilt_deg', 'camera/move/frames/1/tilt_deg'])
        now = self.shot()
        now['camera']['move']['frames'].append({'at': 0.5})                           # one more picture: the list read
        self.assertEqual(rr.changed(rows, self.shot(), now), ['camera/move/frames'])

    def test_a_copy_reads_the_whole_and_the_copy_is_plain(self):
        reads = set()
        shot = rr.recorded(self.shot(), reads)
        whole = copy.deepcopy(shot['camera'])
        self.assertIs(type(whole), dict)
        self.assertIs(type(whole['move']['frames']), list)
        self.assertEqual(rr.as_json(reads), [['value', ['camera']]])
        self.assertEqual(dict(shot['subjects'][0]), {'subject_id': 'post'})
        self.assertIn(['keys', ['subjects', 0]], rr.as_json(reads))


if __name__ == '__main__':
    unittest.main()
