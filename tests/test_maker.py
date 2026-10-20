"""Who makes each cut (studio/maker.py): generative first; Blender where a line claims a part, or the cut shows the key
change or an opening; hybrid where an element's shape must hold or the lines speak of the subject whole."""
import unittest
from unittest import mock

from studio import maker

WORLD = {'elements': [{'id': 'locks', 'function': 'subject', 'description': 'the lock'},
                      {'id': 'lake', 'function': 'context', 'description': 'a lake'},
                      {'id': 'gate', 'function': 'set', 'description': 'a gate', 'requirements': ['exact']}]}
PART = {'id': 'locks/valve', 'kind': 'part', 'subject': 'locks', 'parts': ['locks/valve'], 'words': 'valve'}
LAKE = {'id': 'lake', 'kind': 'element', 'subject': None, 'parts': [], 'words': 'a lake'}
WHOLE = {'id': 'locks', 'kind': 'subject', 'subject': 'locks', 'parts': [], 'words': 'the lock'}


def cut(sid, things=(), show=(), elements=('lake',), written=None):
    entry = {'shot_id': sid, 'line_ids': ['l1'], 'elements': list(elements), 'duration_s': 2.0, 'show': list(show)}
    if written:
        entry['maker'] = written
    return entry, list(things)


class MakerTest(unittest.TestCase):
    def propose(self, entry, things):
        with mock.patch('studio.explains.line_things', return_value=things):
            return maker.propose('p', entry, {'claims': []}, {'lines': []}, WORLD)

    def test_the_table_reads_the_cut(self):
        self.assertEqual(self.propose(*cut('s1', [LAKE]))['mode'], 'generative')   # a place: the model's
        self.assertEqual(self.propose(*cut('s2', [PART]))['rule'], 'part_said')
        opened = [{'at': 'end', 'parts': ['locks/culvert'], 'how': 'section', 'why': 'inside'}]
        self.assertEqual(self.propose(*cut('s3', [LAKE], opened))['mode'], 'blender')
        key = [{'at': 'end', 'parts': ['ship'], 'how': 'exterior', 'why': 'it rises', 'key': True}]
        self.assertEqual(self.propose(*cut('s4', [], key))['rule'], 'key_or_opened')
        gate = [{'at': 'end', 'parts': ['gate'], 'how': 'exterior', 'why': 'the gate'}]
        self.assertEqual(self.propose(*cut('s5', [], gate, elements=('gate', 'lake')))['mode'], 'hybrid')   # an exact shape it shows
        self.assertEqual(self.propose(*cut('s5b', [LAKE], elements=('gate', 'lake')))['mode'], 'generative')   # in the back of a wide
        self.assertEqual(self.propose(*cut('s6', [WHOLE]))['rule'], 'subject_said')
        self.assertEqual(self.propose(*cut('s7', [{**LAKE, 'id': 'locks', 'kind': 'element'}], elements=('locks',)))['mode'], 'hybrid')
        self.assertEqual([r[0] for r in maker.MAKER_RULES][-1], 'default')

    def test_a_written_maker_is_kept_but_never_generative_over_a_fact(self):
        body = {'shots': [cut('s1', written={'mode': 'generative', 'why': 'the model paints it'})[0]]}
        with mock.patch('studio.explains.line_things', return_value=[PART]):
            errors, warnings = maker.lines('p', body, {'claims': []}, {'lines': []}, WORLD)
        self.assertTrue(errors and errors[0].startswith('MAKER_FACT_GENERATED'), errors)
        body = {'shots': [cut('s1', written={'mode': 'blender', 'why': 'we want it exact here'})[0]]}
        with mock.patch('studio.explains.line_things', return_value=[LAKE]):
            errors, warnings = maker.lines('p', body, {'claims': []}, {'lines': []}, WORLD)
        self.assertEqual(errors, [])
        self.assertTrue(warnings and warnings[0].startswith('MAKER_DIFFERS') and 'we want it exact' in warnings[0], warnings)

    def test_a_built_shot_keeps_its_route_unless_the_list_writes_a_maker(self):
        """Review 2026-10-20: re-approving an older list (the rocket's built hybrid shots) must not take builds away."""
        entry = cut('s1')[0]
        self.assertTrue(maker.applies(entry, None))                                        # a new shot
        self.assertTrue(maker.applies(entry, {'route': {'rule_id': 'W_world_owners'}}))    # planned, nothing built
        built = {'scene_version': 'v0003', 'route': {'rule_id': 'W_world_owners', 'mode': 'hybrid'}}
        self.assertFalse(maker.applies(entry, built))
        self.assertFalse(maker.applies(entry, {'scene_version': 'v0001'}))                 # built before routes
        self.assertFalse(maker.applies(entry, {'route': {'rule_id': 'R3_structural'}}))    # the feature rules decided it
        self.assertTrue(maker.applies({**entry, 'maker': {'mode': 'generative', 'why': 'the user wants it painted'}}, built))

    def test_the_sheet_says_each_cut_and_the_share(self):
        body = {'shots': [cut('s1')[0], {**cut('s2')[0], 'duration_s': 6.0}]}
        with mock.patch('studio.explains.line_things', side_effect=[[LAKE], [PART]]):
            rows, share = maker.table('p', body, {'claims': []}, {'lines': []}, WORLD)
        self.assertEqual([(r['shot_id'], r['mode']) for r in rows], [('s1', 'generative'), ('s2', 'blender')])
        self.assertEqual(share, {'generative': 0.25, 'hybrid': 0.0, 'blender': 0.75})


if __name__ == '__main__':
    unittest.main()
