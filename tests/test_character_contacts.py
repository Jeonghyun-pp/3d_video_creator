"""Character contacts and clearance, host side (2026-10-19: a mechanic went through the bicycle he worked on): the shot
declares what a character's hands and feet are on and how it stands; a body through a solid thing is a broken build.
The posing and the measuring are Blender's: tests/studio/character_clearance_smoke.py."""
import unittest

from studio import characters, gates
from studio.common import StudioError

ROW = {'id': 'mechanic', 'asset': 'quaternius_human@v0002', 'action': 'idle', 'at': [0.3, 0.6, 0], 'facing_deg': -90}
CONTACTS = [{'limb': 'right_hand', 'target': 'bike/shifter', 'why': 'presses the lever'},
            {'limb': 'left_hand', 'target': 'bike/crank', 'local': [0.0, 0.0, -0.17], 'why': 'turns the pedal round'}]


class ContactsTest(unittest.TestCase):
    def test_a_standing_character_takes_contacts_and_a_stance(self):
        row = characters.resolve([{**ROW, 'contacts': CONTACTS, 'stance': 'crouch'}])[0]
        self.assertEqual([c['limb'] for c in row['contacts']], ['right_hand', 'left_hand'])
        self.assertEqual(row['stance'], 'crouch')

    def test_a_walker_and_a_limb_twice_are_refused(self):
        walker = {**ROW, 'action': 'walk', 'path': [[0, 0, 0], [1, 0, 0]], 'contacts': CONTACTS}
        walker.pop('at')
        with self.assertRaises(StudioError):
            characters.resolve([walker])
        with self.assertRaises(StudioError):
            characters.resolve([{**ROW, 'contacts': CONTACTS + [{'limb': 'right_hand', 'target': [0, 0, 1], 'why': 'again'}]}])

    def test_the_schema_takes_contacts_and_refuses_an_unknown_limb(self):
        from studio.common import CODE, read_json
        from jsonschema import Draft202012Validator
        schema = read_json(CODE / 'schemas/studio-v1/shot.schema.json')
        item = {'$ref': '#/$defs/scene/properties/characters/items', '$defs': schema['$defs']}
        Draft202012Validator(item).validate({**ROW, 'contacts': CONTACTS, 'stance': 'crouch'})
        bad = {**ROW, 'contacts': [{'limb': 'tail', 'target': 'bike', 'why': 'no'}]}
        self.assertTrue(list(Draft202012Validator(item).iter_errors(bad)))

    def test_a_body_through_a_thing_is_broken(self):
        self.assertEqual(gates.SOFTENABLE['CHARACTER_THROUGH_OBJECT'][0], 'broken')
        self.assertEqual(gates.severity_map({})['CHARACTER_THROUGH_OBJECT'], 'error')


if __name__ == '__main__':
    unittest.main()
