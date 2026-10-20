"""Who makes each cut (studio/maker.py): the key change close on a claimed part stays Blender; how the thing works
(a claimed working part, an opening, a working part in focus) is hybrid; the world around it is generated. Read from
the shot list's screen and the lines, never from a world's per-video declaration."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from studio import maker

WORLD = {'elements': [{'id': 'locks', 'function': 'subject', 'description': 'the lock'},
                      {'id': 'lake', 'function': 'context', 'description': 'a lake'},
                      {'id': 'gate', 'function': 'set', 'description': 'a gate', 'requirements': ['exact']},
                      {'id': 'ship', 'function': 'set', 'description': 'a ship'}]}
PART = {'id': 'locks/valve', 'kind': 'part', 'subject': 'locks', 'parts': ['locks/valve'], 'words': 'valve'}
LAKE = {'id': 'lake', 'kind': 'element', 'subject': None, 'parts': [], 'words': 'a lake'}
WATER = {'id': 'water', 'kind': 'element', 'subject': None, 'parts': [], 'words': 'water'}


def cut(sid, things=(), show=(), focus=None, size='wide', action=None, written=None, elements=('lake',)):
    entry = {'shot_id': sid, 'line_ids': ['l1'], 'elements': list(elements), 'duration_s': 2.0, 'show': list(show)}
    if focus:
        entry['frame'] = {'focus': list(focus), 'size': size}
    if action:
        entry['direction'] = {'action': action}
    if written:
        entry['maker'] = written
    return entry, list(things)


class MakerTest(unittest.TestCase):
    def propose(self, entry, things, path='p'):
        with mock.patch('studio.explains.line_things', return_value=things):
            return maker.propose(path, entry, {'claims': []}, {'lines': []}, WORLD)

    def test_the_table_reads_the_screen_and_the_lines(self):
        """M1-M3 (2026-10-20): Blender alone only for the key change close on a claimed part; how it works is hybrid; the
        world around it generative, raised to hybrid where a line gives a number for what is on screen."""
        self.assertEqual(self.propose(*cut('s1', [LAKE], focus=['lake']))['mode'], 'generative')   # a place
        key = [{'at': 'end', 'parts': ['locks/valve'], 'how': 'exterior', 'why': 'it opens', 'key': True}]
        self.assertEqual(self.propose(*cut('s2', [PART], key, focus=['locks/valve'], size='close'))['rule'], 'key_close')
        # the same key change seen medium, or on a part no line claims: how it works, not checked part by part
        self.assertEqual(self.propose(*cut('s3', [PART], key, focus=['locks/valve'], size='medium'))['mode'], 'hybrid')
        self.assertEqual(self.propose(*cut('s4', [LAKE], key, focus=['locks/valve'], size='close'))['rule'], 'mechanism')
        # a claimed working part on screen; a subject element of the world counts (no spec at plan time)
        self.assertEqual(self.propose(*cut('s5', [PART], focus=['locks/valve'], size='wide'))['rule'], 'claimed')
        LOCKS = {'id': 'locks', 'kind': 'element', 'subject': None, 'parts': [], 'words': 'the lock'}
        self.assertEqual(self.propose(*cut('s6', [LOCKS], focus=['locks'], size='medium'))['rule'], 'claimed')
        self.assertEqual(self.propose(*cut('s6b', [LOCKS], focus=['locks'], size='wide'))['mode'], 'generative')   # the world
        # claimed but off screen: nothing on screen is the fact (SHOT_EXPLAINS_UNSHOWN says the rest)
        self.assertEqual(self.propose(*cut('s7', [PART], focus=['lake']))['mode'], 'generative')
        # an opening, or a subject element in focus: how it works
        opened = [{'at': 'end', 'parts': ['locks/culvert'], 'how': 'section', 'why': 'inside'}]
        self.assertEqual(self.propose(*cut('s8', [LAKE], opened, focus=['lake']))['rule'], 'mechanism')
        self.assertEqual(self.propose(*cut('s9', [], focus=['locks'], size='medium'))['rule'], 'mechanism')
        self.assertEqual(self.propose(*cut('s9b', [], focus=['locks'], size='wide'))['mode'], 'generative')   # the lock in its valley
        # a set piece moving, a ship waiting, a gate the world calls exact: the world, the model's
        SHIP = {'id': 'ship', 'kind': 'element', 'subject': None, 'parts': [], 'words': 'a ship'}
        self.assertEqual(self.propose(*cut('s10', [SHIP], focus=['ship'], size='close', action='the ship rises'))['mode'], 'generative')
        self.assertEqual(self.propose(*cut('s11', [], focus=['gate'], size='close', elements=('gate',)))['mode'], 'generative')
        self.assertEqual([r[0] for r in maker.MAKER_RULES][-1], 'default')

    def test_a_number_said_of_what_is_on_screen_is_a_fact(self):
        entry = cut('s1', [], focus=['ship'], size='wide')[0]
        facts = {'claims': [{'claim_id': 'c1', 'text': 'about 300 ships a day', 'about': ['ship'], 'source_ids': ['a']}]}
        script = {'lines': [{'line_id': 'l1', 'text': 'x', 'claim_ids': ['c1']}]}
        with mock.patch('studio.explains.line_things', return_value=[]):
            self.assertEqual(maker.propose('p', entry, facts, script, WORLD)['rule'], 'world_fact')
            facts['claims'][0]['text'] = 'ships queue for the lock'
            self.assertEqual(maker.propose('p', entry, facts, script, WORLD)['mode'], 'generative')

    def test_the_cut_kind_is_read_from_the_screen(self):
        cases = [(cut('s1', [], focus=['lake'])[0], 'world'), (cut('s2', [], focus=['locks'], size='medium')[0], 'mechanism'),
                 (cut('s3', [], [{'at': 'end', 'parts': [], 'how': 'xray', 'why': 'in'}], focus=['lake'])[0], 'mechanism')]
        for entry, kind in cases:
            with mock.patch('studio.explains.line_things', return_value=[]):
                self.assertEqual(maker.kind_of('p', entry, {'claims': []}, {'lines': []}, WORLD), kind, entry['shot_id'])
        # what fills the frame decides: wide is the place; a rider beside the lock is the world
        with mock.patch('studio.explains.line_things', return_value=[]):
            self.assertEqual(maker.kind_of('p', cut('s5', [], focus=['locks'], size='wide')[0], {}, {}, WORLD), 'world')
            self.assertEqual(maker.kind_of('p', cut('s6', [], focus=['locks', 'lake', 'ship'], size='medium')[0], {}, {}, WORLD), 'world')
        written = cut('s4', [], focus=['lake'])[0]
        written['frame']['kind'] = 'mechanism'
        with mock.patch('studio.explains.line_things', return_value=[]):
            _errors, warnings = maker.lines('p', {'shots': [written]}, {'claims': []}, {'lines': []}, WORLD)
        self.assertTrue(any(w.startswith('CUT_KIND_DIFFERS') for w in warnings), warnings)

    def test_a_subject_spec_in_focus_is_how_it_works(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'subjects' / 'bike').mkdir(parents=True)
            (Path(root) / 'subjects' / 'bike' / 'spec.json').write_text('{}')
            self.assertEqual(self.propose(*cut('s1', [], focus=['bike'], size='close'), path=root)['mode'], 'hybrid')

    def test_a_written_maker_is_kept_but_never_generative_over_a_fact(self):
        body = {'shots': [cut('s1', focus=['locks/valve'], written={'mode': 'generative', 'why': 'the model paints it'})[0]]}
        with mock.patch('studio.explains.line_things', return_value=[PART]), mock.patch('studio.maker._built_thing', return_value=True):
            errors, warnings = maker.lines('p', body, {'claims': []}, {'lines': []}, WORLD)
        self.assertTrue(errors and errors[0].startswith('MAKER_FACT_GENERATED'), errors)
        body = {'shots': [cut('s1', focus=['lake'], written={'mode': 'blender', 'why': 'we want it exact here'})[0]]}
        with mock.patch('studio.explains.line_things', return_value=[LAKE]):
            errors, warnings = maker.lines('p', body, {'claims': []}, {'lines': []}, WORLD)
        self.assertEqual(errors, [])
        self.assertTrue(any(w.startswith('MAKER_DIFFERS') and 'we want it exact' in w for w in warnings), warnings)

    def test_a_cut_with_no_screen_is_said(self):
        body = {'shots': [cut('s1')[0], cut('s2', focus=['nowhere'])[0]]}
        with mock.patch('studio.explains.line_things', return_value=[]), mock.patch('studio.maker._built_thing', return_value=False):
            _errors, warnings = maker.lines('p', body, {'claims': []}, {'lines': []}, WORLD)
        self.assertEqual(sum(w.startswith('FRAME_UNDESCRIBED') for w in warnings), 2, warnings)

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

    def test_the_sheet_says_each_cut_the_share_and_a_shared_look(self):
        key = [{'at': 'end', 'parts': ['ship'], 'how': 'exterior', 'why': 'it rises', 'key': True}]
        SHIP = {'id': 'ship', 'kind': 'element', 'subject': None, 'parts': [], 'words': 'a ship'}
        body = {'shots': [cut('s1', focus=['ship', 'lake'])[0], {**cut('s2', show=key, focus=['ship'], size='close')[0], 'duration_s': 6.0},
                          cut('s3', focus=['lake'])[0]]}
        with mock.patch('studio.explains.line_things', side_effect=[[LAKE], [SHIP], [LAKE]] * 3):
            rows, share = maker.table('p', body, {'claims': []}, {'lines': []}, WORLD)
        self.assertEqual([(r['shot_id'], r['mode']) for r in rows], [('s1', 'generative'), ('s2', 'blender'), ('s3', 'generative')])
        self.assertEqual(share, {'generative': 0.4, 'hybrid': 0.0, 'blender': 0.6})
        # the ship recurs only in a Blender cut: no shared look; the lake is context, never shared
        self.assertEqual([r.get('shared_reference') for r in rows], [None, None, None])
        body['shots'][1].pop('show')   # the ship now only seen: generated, its look shared
        with mock.patch('studio.explains.line_things', return_value=[LAKE]):
            rows, _share = maker.table('p', body, {'claims': []}, {'lines': []}, WORLD)
        # the first cut sets the ship's look; only the later one takes it (from s1's start picture, at drawing time)
        self.assertEqual([r.get('shared_reference') for r in rows], [None, {'from': 's1', 'ids': ['ship']}, None])


if __name__ == '__main__':
    unittest.main()
