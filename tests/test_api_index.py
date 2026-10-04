import unittest

from studio.api_index import API_DIR, search, show
from studio.common import StudioError


@unittest.skipUnless(list(API_DIR.glob('blender-*.json')), 'API index not built (scripts/build_bpy_index.py)')
class ApiIndexTest(unittest.TestCase):
    def test_finds_current_names(self):
        info = show('bpy.types.NodesModifier.properties')
        self.assertEqual(info['properties'][0]['of'], 'NodesModifierProperties')
        self.assertTrue(show('bpy.types.Mesh.set_sharp_from_angle')['functions'])
        paths = [r['path'] for r in search('sharp from angle')['results']]
        self.assertIn('bpy.types.Mesh.set_sharp_from_angle', paths)

    def test_unknown_member_lists_real_ones(self):
        with self.assertRaises(StudioError) as caught:
            show('bpy.types.Mesh.use_auto_smooth')  # removed in 4.1; recalled by older training data
        self.assertIn('set_sharp_from_angle', caught.exception.recovery)


if __name__ == '__main__':
    unittest.main()
