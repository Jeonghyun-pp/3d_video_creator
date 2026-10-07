"""What a scene-only shot (archcut3 s01: a street and a section, no subject object) needs from the engine: declared ids
resolved by one rule, its subject named in shot.screen, the photo camera solved without a subject, the shot's author
script kept across fresh builds, and the edit's captions and labels drawn the way the style asks."""
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from studio.blender import probe_inputs
from studio.blender_ops import ids_core
from studio.common import StudioError, write_json
from studio.photo_match import add_view, compare_images, fit_camera, planar, project_points


class IdsTest(unittest.TestCase):
    def test_layout_id_is_the_one_rule(self):
        self.assertEqual(ids_core.layout_id('concrete_hall'), 'concrete-hall')
        self.assertEqual(ids_core.layout_id('rc.0'), 'rc-0')

    def test_spellings_cover_layout_form_and_fill_copies(self):
        self.assertEqual(ids_core.spellings('concrete_hall/columns_l'),
                         ['concrete_hall/columns_l', 'concrete-hall/columns_l', 'fill-concrete_hall/columns_l', 'fill-concrete-hall/columns_l'])
        self.assertIn('fill-station_column/concrete', ids_core.spellings('station_column/concrete'))   # archcut3 s01 keep

    def test_groups_and_suggestions(self):
        self.assertTrue(ids_core.in_group('st.slab.0', 'st.slab'))
        self.assertTrue(ids_core.in_group('hall/floor', 'hall'))
        self.assertFalse(ids_core.in_group('st.slabs', 'st.slab'))
        self.assertEqual(ids_core.suggest('st.slb', ['st.slab.0', 'st.wall_l', 'camera'])[0], 'st.slab.0')


def _shot(**extra):
    return {'shot_id': 's01', 'camera': {}, 'duration_frames': 30, **extra}


class ProbeInputsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_screen_subject_and_keep_reach_the_probe(self):
        got = probe_inputs(self.path, _shot(screen={'subject': ['st.slab', 'station_column'], 'keep': ['st.wall_l']}), 30)
        self.assertEqual(got['subjects'], ['st.slab', 'station_column'])
        self.assertEqual(got['declared_subjects'], ['st.slab', 'station_column'])   # these must exist at build time
        self.assertEqual(got['keep'], ['st.wall_l'])

    def test_camera_subjects_are_not_required(self):
        got = probe_inputs(self.path, _shot(camera={'move': {'params': {'target': 'tower'}}}), 30)
        self.assertEqual((got['subjects'], got['declared_subjects'], got['keep']), (['tower'], [], []))


CAM = {'target': [0, 0, 0], 'distance_m': 40.0, 'azimuth_deg': 20.0, 'elevation_deg': 15.0, 'lens_mm': 35.0, 'width': 300, 'height': 200}
WORLD = [[-6, 0, 0], [6, 0, 0], [6, 0, 8], [-6, 0, 8], [-6, -8, 0], [6, -8, 0], [0, -4, 12]]   # two facades and a roof point


class SceneViewTest(unittest.TestCase):
    """archcut3 s01: the section box is a helper, not a subject - the camera must still solve from world points."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        (self.project / 'references/r').mkdir(parents=True)
        Image.new('RGB', (300, 200), 'grey').save(self.project / 'references/r/street.png')

    def tearDown(self):
        self.tmp.cleanup()

    def _call(self, calls):
        def call(tool, args):
            calls.append(tool)
            raise AssertionError(f'a scene view needs no {tool} call')
        return call

    def test_fit_without_a_subject_solves_in_the_world(self):
        px = [[u * 300, v * 200] for u, v in project_points(CAM, WORLD)]
        add_view(self.project, 'r/street', 'references/r/street.png', 'local_only', None,
                 points=[{'xyz': p, 'px': q} for p, q in zip(WORLD, px)])
        calls = []
        out = fit_camera(self.project, self._call(calls), 'r/street')
        self.assertEqual(calls, [])                                    # no anchors, no silhouette renders
        self.assertLess(out['residual_px'], 0.5)
        self.assertIn('skipped', out['refine'])
        self.assertNotIn('warnings', out)

    def test_anchor_points_need_a_subject(self):
        add_view(self.project, 'r/street', 'references/r/street.png', 'local_only', None, points=[{'anchor': 'a', 'px': [1, 2]}])
        with self.assertRaises(StudioError) as error:
            fit_camera(self.project, self._call([]), 'r/street')
        self.assertIn('xyz', error.exception.message)

    def test_planar_points_are_said(self):
        self.assertTrue(planar([[x, 60, z] for x in (-5, 0, 5) for z in (0, 3)]))   # archcut3's six points on y = 60
        self.assertFalse(planar(WORLD))

    def test_compare_a_scene_view_over_the_whole_frame(self):
        ids = self.project / 'ids.png'
        Image.new('RGBA', (300, 200), (0, 0, 0, 0)).save(ids)            # nothing is a subject: an empty silhouette
        record = {'id': 'street', 'image': 'references/r/street.png', 'licence': 'local_only', 'subject_id': None, 'mask': {}, 'points': [], 'parts': {}}
        out = compare_images(self.project, record, ids, self.project / 'references/r/street.png', {}, self.project / 'out')
        self.assertTrue(out['scene_view'])
        self.assertIsNone(out['iou'])
        self.assertTrue(out['edges'])                                   # the structure comparison still runs


FONT = Path(__file__).resolve().parents[1] / 'library/fonts/pretendard/Pretendard-Bold.otf'


class CaptionAndTagTest(unittest.TestCase):
    """archcut3: the reference's subtitles are big white type with a black outline, and its tag is a red box on a column."""
    def test_default_caption_is_the_old_panel(self):
        from PIL import ImageFont
        from studio.edit import CAPTION_DEFAULTS, _caption_layer
        font = ImageFont.truetype(str(FONT), 28)
        layer, panel = _caption_layer('기둥 철근', (720, 1280), font, (50, 128, 670, 998), CAPTION_DEFAULTS)
        self.assertEqual(layer.getpixel((panel[0] + 20, panel[1] + 4)), (8, 15, 24, 224))   # the rounded panel, as before

    def test_bare_outlined_caption(self):
        from PIL import ImageFont
        from studio.edit import CAPTION_DEFAULTS, _caption_layer, caption_style
        look = caption_style({'captions': {'panel': 'none', 'outline_frac': 0.004, 'size_frac': 0.06}})
        font = ImageFont.truetype(str(FONT), round(720 * look['size_frac']))
        layer, panel = _caption_layer('기둥 철근', (720, 1280), font, (50, 128, 670, 998), look)
        self.assertEqual(layer.getpixel((panel[0] + 4, panel[1] + 4))[3], 0)                # no panel
        colours = {layer.getpixel((x, y))[:3] for x in range(panel[0], panel[2], 3) for y in range(panel[1], panel[3], 3) if layer.getpixel((x, y))[3] == 255}
        self.assertIn((0, 0, 0), colours)                                                    # the outline
        with self.assertRaises(StudioError):
            caption_style({'captions': {'colour': [1, 1, 1]}})
        self.assertEqual(caption_style({}), CAPTION_DEFAULTS)

    def test_a_tag_sits_on_its_anchor(self):
        from studio.edit import make_overlays
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_json(root / 'anchors.json', {'frames': [{'frame': f, 'label_id': 'tag', 'u': .3 + .1 * f, 'v': .4, 'depth': 2, 'visible': True} for f in range(2)]})
            shot = {'shot_id': 's03', 'labels': [{'label_id': 'tag', 'anchor': 'column/tag', 'text': '현장 점검', 'start_frame': 0, 'end_frame': 2,
                                                   'placement': 'anchor', 'fill_srgb': [0.86, 0.1, 0.08], 'text_srgb': [1, 1, 1], 'occlusion_policy': 'hide'}]}
            out = make_overlays(root / 'edit', [{'shot': shot, 'audio': {'speech_status': 'final', 'cues': []}, 'frame_count': 2,
                                                 'anchors_path': root / 'anchors.json'}], 360, 640, {'typography': {'font_path': str(FONT.parent)}}, root)
            boxes = [b['bbox'] for b in out['text_boxes'] if b['kind'] == 'label']
            for f, box in enumerate(boxes):
                centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
                self.assertLess(abs(centre[0] - (0.3 + 0.1 * f) * 360), 2)                   # follows the anchor
                self.assertLess(abs(centre[1] - 0.4 * 640), 2)
            with Image.open(root / 'edit/overlays/000001.png') as first:
                self.assertEqual(first.getpixel((boxes[0][0] + 3, (boxes[0][1] + boxes[0][3]) // 2))[:3], (219, 26, 20))   # red fill


if __name__ == '__main__':
    unittest.main()
