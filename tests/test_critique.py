"""Reference critique: a difference a person sees ranks first, a whole-frame shift is said once as a grade, what remains
per region after it is still said, and each comes with the shot value that would close it."""
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from studio.critique import class_regions, critique, size_rows

W, H = 180, 320


def frame(sky=(90, 110, 150), ground=(70, 70, 70), box=None, box_colour=(200, 60, 40)):
    image = Image.new('RGB', (W, H), ground)
    ImageDraw.Draw(image).rectangle((0, 0, W, H * 0.4), fill=sky)
    for y in range(0, H, 16):   # some structure, the same in both
        ImageDraw.Draw(image).line((0, y, W, y), fill=(40, 40, 40))
    if box:
        ImageDraw.Draw(image).rectangle(box, fill=box_colour)
    return image


class CritiqueTest(unittest.TestCase):
    def test_the_same_picture_has_nothing_to_say(self):
        self.assertEqual(critique(frame(), frame())['differences'], [])

    def test_a_blue_cast_is_one_whole_frame_grade(self):
        ref = frame(sky=(150, 140, 120), ground=(120, 115, 100))
        ours = frame(sky=(110, 130, 170), ground=(90, 105, 135))           # archcut3 s02: the hall came out blue
        out = critique(ref, ours, shot={'render': {'grade': {'white_balance_k': 5500}}})
        first = out['differences'][0]
        self.assertEqual((first['region'], first['metric']), ('whole frame', 'b'))
        self.assertEqual(first['proposal']['path'], '/render/grade/white_balance_k')
        self.assertGreater(first['proposal']['value'], 5500)                   # bluer than the reference: warmer
        self.assertEqual(sum(1 for d in out['differences'] if d['metric'] == 'b' and d['region'] == 'whole frame'), 1)

    def test_one_bright_region_proposes_a_light_target(self):
        ref, ours = frame(), frame()
        ImageDraw.Draw(ours).rectangle((120, 0, W, 64), fill=(235, 235, 235))   # archcut3 s03: a ceiling too bright
        out = critique(ref, ours)
        first = out['differences'][0]
        self.assertEqual((first['region'], first['metric']), ('top-right', 'L'))
        self.assertEqual(first['proposal']['path'], '/screen/light/regions')
        self.assertLess(first['proposal']['value']['luminance'], 60)

    def test_a_region_still_differs_after_the_whole_frame_shift(self):
        ref = frame()
        ours = frame(sky=(130, 150, 190), ground=(110, 110, 110))
        ImageDraw.Draw(ours).rectangle((0, 256, W, H), fill=(230, 170, 175))    # archcut3 s01: the pink ground
        out = critique(ref, ours)
        regions = [d['region'] for d in out['differences'][:5]]
        self.assertIn('whole frame', regions)
        self.assertTrue(any(r.startswith('bottom') for r in regions), regions)

    def test_class_regions_and_part_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ids = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(ids).rectangle((60, 200, 100, 300), fill=(255, 0, 0, 255))
            ids.save(Path(tmp) / 'id.png')
            regions = class_regions(Path(tmp) / 'id.png', {'#ff0000': 'inspector'}, (W, H))
            self.assertEqual(regions['class:inspector'].getbbox(), (60, 200, 101, 301))
            ref, ours = frame(box=(60, 200, 100, 300)), frame(box=(60, 200, 100, 300), box_colour=(60, 200, 40))
            out = critique(ref, ours, regions=regions, out_dir=tmp)
            self.assertEqual(out['differences'][0]['region'], 'class:inspector')
            self.assertTrue(Path(out['sheet']).is_file())
        rows = size_rows({'inspector': [0, 0, 10, 188]}, {'inspector': [0, 0, 10, 160]}, (100, 1000), (100, 1000))
        self.assertEqual(rows[0]['proposal']['value']['value'], 0.188)          # archcut3 s03: 0.160 H vs 0.188 H


if __name__ == '__main__':
    unittest.main()
