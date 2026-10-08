"""A genre is learned from a folder of reels under one name, each learner over all of them, and says when it has fewer
than the target number of references."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from studio import style_library


class GenreTest(unittest.TestCase):
    def test_learn_genre_from_a_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'refs').mkdir()
            for i, src in enumerate(('testsrc2=s=180x320:r=30:d=3', 'testsrc=s=180x320:r=30:d=3')):   # two moving test patterns
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', src, '-pix_fmt', 'yuv420p',
                                str(tmp / 'refs' / f'r{i}.mp4')], check=True)
            (tmp / 'refs' / 'notes.txt').write_text('not a video')
            styles = {name: tmp / name for name in ('motion', 'look', 'composition')}
            with mock.patch('studio.motion_style.STYLES', styles['motion']), mock.patch("studio.look_style.STYLES", styles["look"]), \
                    mock.patch('studio.composition_style.STYLES', styles['composition']), mock.patch.object(style_library, 'REPO', tmp):
                out = style_library.learn_genre('test_genre', tmp / 'refs')
            self.assertEqual(out['references'], 2)
            self.assertFalse(out['enough'])
            self.assertTrue(any(w.startswith('GENRE_FEW_REFERENCES') for w in out['warnings']))
            self.assertTrue((tmp / 'library/genres/test_genre.json').is_file())
            for kind in ('motion', 'look', 'composition'):
                self.assertTrue(Path(out['learned'][kind]['path']).is_file())


if __name__ == '__main__':
    unittest.main()
