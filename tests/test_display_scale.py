"""A part drawn larger or smaller than life must say so on screen: a label anchored to it, inside it or on its group
(2026-10-09, declared exaggeration)."""
import unittest

from studio.blender import display_undisclosed
from studio.gates import SOFTENABLE
from studio.common import REPO, read_json


def row(ident):
    return {'id': ident, 'factor': 3.0, 'reason': '30 mm buffer reads at phone size'}


class DisplayScaleDisclosureTest(unittest.TestCase):
    def test_a_label_on_it_inside_it_or_on_its_group_discloses(self):
        for anchor, ident in (('buffer', 'buffer'), ('floor/buffer', 'floor/buffer'), ('floor/buffer/top', 'floor/buffer'),
                              ('slab', 'slab.2'), ('concrete_hall/buffer', 'concrete-hall/buffer')):
            self.assertEqual(display_undisclosed([row(ident)], [{'label_id': 'a', 'anchor': anchor}]), [], (anchor, ident))

    def test_no_label_or_a_label_elsewhere_does_not(self):
        self.assertEqual(display_undisclosed([row('buffer')], None), [row('buffer')])
        self.assertEqual(display_undisclosed([row('buffer')], [{'label_id': 'a', 'anchor': 'buffer_strip'}]), [row('buffer')])

    def test_the_gate_is_softenable_and_in_the_project_schema(self):
        self.assertEqual(SOFTENABLE['DISPLAY_SCALE_UNDISCLOSED'][0], 'broken')
        schema = read_json(REPO / 'schemas/studio-v1/project.schema.json')
        self.assertIn('DISPLAY_SCALE_UNDISCLOSED', str(schema))


class LabelSlotTest(unittest.TestCase):
    """The validator applies the edit's rule (found writing the display-scale smoke, 2026-10-09: a label without the
    optional `slot` crashed validation with KeyError)."""
    def label(self, ident, **extra):
        return {'label_id': ident, 'text': ident, 'anchor': 'a', 'start_frame': 0, 'end_frame': 30, 'occlusion_policy': 'show', **extra}

    def test_slot_defaults_and_anchor_tags_take_no_slot(self):
        from studio.common import StudioError
        from studio.project import default_shot, validate_shot
        shot = default_shot('s', 30, {'request': 'x'})
        shot['labels'] = [self.label('one')]
        validate_shot(shot)
        shot['labels'] = [self.label('tag1', placement='anchor'), self.label('tag2', placement='anchor')]
        validate_shot(shot)
        shot['labels'] = [self.label('one'), self.label('two', slot='upper_left')]
        with self.assertRaises(StudioError):
            validate_shot(shot)



class PlainMaterialTest(unittest.TestCase):
    def test_plain_materials_on_built_parts_under_a_photoreal_look(self):
        from studio.blender import plain_materials
        inventory = {'objects': [{'name': 'floor/eps', 'studio_id': 'floor/eps', 'part_id': 'eps', 'materials': ['black_EPS_beads']},
                                 {'name': 'floor/slab', 'studio_id': 'floor/slab', 'part_id': 'slab', 'materials': ['StudioMat_slab']},
                                 {'name': 'child', 'studio_id': 'child', 'materials': ['skin']}]}   # a character: not a built part
        shot = {'render': {'look_preset': 'photoreal_interior'}}
        lines = plain_materials(shot, inventory)
        self.assertTrue(lines[0].startswith('MATERIAL_NOT_CATALOG') and 'black_EPS_beads' in lines[0] and 'skin' not in lines[0])
        self.assertIn('aerated_concrete', lines[0])
        self.assertEqual(plain_materials({'render': {'look_preset': 'technical_clean'}}, inventory), [])
        clay = {'render': {'look_preset': 'photoreal_interior'}, 'screen': {'generate_only': ['floor', '@background']}}
        self.assertEqual(plain_materials(clay, inventory), [])   # a clay stand-in the model fills needs no material


if __name__ == '__main__':
    unittest.main()
