"""Per-part structure and light direction of a generated take (studio/qa_generative.py parts / light_direction): a part the
take dropped is named while a kept one passes between the bounds; the light fitted on a Lambert picture through the
normal pass comes back within 3 degrees."""
import math
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from studio import qa_generative as qa

W, H, FRAMES = 180, 320, 6


def encode(directory, name, frames):
    for i, image in enumerate(frames):
        image.save(directory / f'{name}_{i:03d}.png')
    out = directory / f'{name}.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', '30', '-i', str(directory / f'{name}_%03d.png'), '-pix_fmt', 'yuv420p',
                    '-crf', '18', str(out)], check=True)
    return str(out)


def boxes(drop_right=False, colour=(200, 200, 200)):
    image = Image.new('RGB', (W, H), (40, 40, 40))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 60, 70, 140), fill=colour, outline=(255, 255, 255), width=2)
    draw.line((20, 100, 70, 100), fill=(90, 90, 90), width=2)
    if not drop_right:
        draw.rectangle((110, 180, 160, 260), fill=colour, outline=(255, 255, 255), width=2)
        draw.line((135, 180, 135, 260), fill=(90, 90, 90), width=2)
    return image


def mask(box):
    image = Image.new('L', (W, H), 0)
    ImageDraw.Draw(image).rectangle(box, fill=255)
    return image


class PartsTest(unittest.TestCase):
    def test_a_dropped_part_is_named_and_a_kept_one_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            clay = encode(tmp, 'clay', [boxes() for _ in range(FRAMES)])
            take = encode(tmp, 'take', [boxes(drop_right=True, colour=(180, 120, 60)) for _ in range(FRAMES)])   # restyled, right box gone
            for name, box in (('left', (20, 60, 70, 140)), ('right', (110, 180, 160, 260))):
                for i in range(FRAMES):
                    mask(box).save(tmp / f'{name}_{i:06d}.png')
            result = qa.parts(clay, take, {'left': str(tmp / 'left_%06d.png'), 'right': str(tmp / 'right_%06d.png')}, width=W)
            self.assertFalse(result['left']['lost'], result['left'])
            self.assertGreater(result['left']['ratio'], 0.6, result['left'])
            self.assertTrue(result['right']['lost'], result['right'])


class LightTest(unittest.TestCase):
    def sphere(self, light, clamp=False):
        """A camera-space normal pass of a sphere and its Lambert picture under a light from `light`."""
        normals, picture = Image.new('RGB', (W, H)), Image.new('L', (W, H))
        cx, cy, r = W / 2, H / 2, 70
        for y in range(H):
            for x in range(W):
                dx, dy = (x + 0.5 - cx) / r, (cy - y - 0.5) / r
                if dx * dx + dy * dy >= 1:
                    continue
                n = (dx, dy, math.sqrt(1 - dx * dx - dy * dy))   # camera space: x right, y up, z toward the camera
                normals.putpixel((x, y), tuple(round((c * 0.5 + 0.5) * 255) for c in n))
                shade = sum(a * b for a, b in zip(n, light))
                picture.putpixel((x, y), round(255 * (0.45 + 0.4 * (max(0.0, shade) if clamp else shade))))
        return normals, picture.convert('RGB')

    def test_the_light_comes_back_from_the_picture(self):
        light = (-0.5, 0.6, 0.62)
        norm = math.sqrt(sum(c * c for c in light)); light = tuple(c / norm for c in light)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            normals, picture = self.sphere(light)
            normal_clip = encode(tmp, 'normal', [normals] * FRAMES)
            got = qa.light_direction(normal_clip, encode(tmp, 'lit', [picture] * FRAMES), width=W)
            angle = math.degrees(math.acos(sum(a * b for a, b in zip(got['direction'], light))))
            self.assertLess(angle, 3.0, got)
            mirrored = (-light[0], light[1], light[2])   # the take lit from the other side
            _, other = self.sphere(mirrored)
            change = qa.light_change(normal_clip, encode(tmp, 'ref', [picture] * FRAMES), encode(tmp, 'other', [other] * FRAMES))
            expected = math.degrees(math.acos(sum(a * b for a, b in zip(light, mirrored))))
            self.assertLess(abs(change['angle_deg'] - expected), 4.0, change)


if __name__ == '__main__':
    unittest.main()
