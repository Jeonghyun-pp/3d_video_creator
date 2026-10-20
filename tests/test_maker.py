"""Who makes each cut (studio/maker.py, 2026-10-20): what must be right in it (keeps, written or claimed by its lines
on screen) against what each method keeps (library/production_methods.json) - the method that keeps it all and leaves
the most to a model. No cut kinds, never a world's per-video declaration."""
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


def cut(sid, things=(), show=(), focus=None, size='wide', action=None, written=None, elements=('lake',), keeps=None):
    entry = {'shot_id': sid, 'line_ids': ['l1'], 'elements': list(elements), 'duration_s': 2.0, 'show': list(show)}
    if keeps:
        entry['keeps'] = keeps
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

    def test_nothing_to_keep_is_the_models(self):
        for entry, things in (cut('s1', [LAKE], focus=['lake']), cut('s2', [], focus=['locks'], size='close'),
                              cut('s3', [], focus=['ship'], size='close', action='the ship rises'),
                              cut('s4', [], focus=['gate'], size='close', elements=('gate',))):   # a world's "exact" decides nothing
            made = self.propose(entry, things)
            self.assertEqual((made['mode'], made['keeps']), ('generative', []), entry['shot_id'])

    def test_what_the_lines_claim_on_screen_must_be_right(self):
        made = self.propose(*cut('s1', [PART], focus=['locks/valve'], size='medium'))
        self.assertEqual(made['keeps'][0]['kind'], 'shape')
        self.assertEqual(made['mode'], 'hybrid')   # the valve put back from Blender, the rest the model's
        self.assertEqual(self.propose(*cut('s2', [PART], focus=['lake']))['mode'], 'generative')   # claimed, not on screen
        key = [{'at': 'end', 'parts': ['locks/valve'], 'how': 'exterior', 'why': 'it opens', 'key': True}]
        moved = self.propose(*cut('s3', [PART], key, focus=['locks/valve', 'lake'], size='medium'))
        self.assertEqual({k['kind'] for k in moved['keeps']}, {'shape', 'motion'})
        self.assertEqual(moved['mode'], 'hybrid')

    def test_when_nothing_is_left_to_generate_it_is_blender(self):
        """The kept thing fills the frame (detail): a model would repaint only its margin - Blender alone."""
        made = self.propose(*cut('s1', [PART], focus=['locks/valve'], size='detail'))
        self.assertEqual(made['mode'], 'blender')
        made = self.propose(*cut('s2', [PART], focus=['locks/valve'], size='close'))
        self.assertEqual(made['mode'], 'hybrid')

    def test_a_thing_with_no_outline_cannot_be_put_back(self):
        """Water's motion kept: no mask cuts water out of a take - only Blender keeps it."""
        world = {'elements': WORLD['elements'] + [{'id': 'water', 'function': 'phenomenon', 'description': 'water'}]}
        entry, _ = cut('s1', [], focus=['water'], size='medium', keeps=[{'kind': 'motion', 'ids': ['water'], 'what': 'it falls 25 m'}])
        with mock.patch('studio.explains.line_things', return_value=[]):
            self.assertEqual(maker.propose('p', entry, {'claims': []}, {'lines': []}, world)['mode'], 'blender')

    def test_a_number_said_of_what_is_on_screen_is_kept(self):
        entry = cut('s1', [], focus=['ship'], size='wide')[0]
        facts = {'claims': [{'claim_id': 'c1', 'text': 'about 300 ships a day', 'about': ['ship'], 'source_ids': ['a']}]}
        script = {'lines': [{'line_id': 'l1', 'text': 'x', 'claim_ids': ['c1']}]}
        with mock.patch('studio.explains.line_things', return_value=[]):
            made = maker.propose('p', entry, facts, script, WORLD)
            self.assertEqual(([k['kind'] for k in made['keeps']], made['mode']), (['count'], 'hybrid'))
            facts['claims'][0]['text'] = 'ships queue for the lock'
            self.assertEqual(maker.propose('p', entry, facts, script, WORLD)['mode'], 'generative')

    def test_a_written_maker_is_kept_but_never_one_that_drops_a_keep(self):
        body = {'shots': [cut('s1', focus=['locks/valve'], size='medium', written={'mode': 'generative', 'why': 'the model paints it'})[0]]}
        with mock.patch('studio.explains.line_things', return_value=[PART]), mock.patch('studio.maker._built_thing', return_value=True):
            errors, _ = maker.lines('p', body, {'claims': []}, {'lines': []}, WORLD)
        self.assertTrue(errors and errors[0].startswith('MAKER_KEEP_UNCOVERED'), errors)
        body = {'shots': [cut('s1', focus=['lake'], written={'mode': 'blender', 'why': 'we want it exact here'})[0]]}
        with mock.patch('studio.explains.line_things', return_value=[LAKE]):
            errors, warnings = maker.lines('p', body, {'claims': []}, {'lines': []}, WORLD)
        self.assertEqual(errors, [])
        self.assertTrue(any(w.startswith('MAKER_DIFFERS') and 'we want it exact' in w for w in warnings), warnings)
        body = {'shots': [cut('s1', focus=['lake'], keeps=[{'kind': 'shape', 'ids': ['nowhere']}])[0]]}
        with mock.patch('studio.explains.line_things', return_value=[]), mock.patch('studio.maker._built_thing', return_value=False):
            _, warnings = maker.lines('p', body, {'claims': []}, {'lines': []}, WORLD)
        self.assertTrue(any(w.startswith('KEEP_UNKNOWN') for w in warnings), warnings)

    def test_review_2026_10_20_numbers_ids_and_written_makers(self):
        """A speed said of the sea is no count; a typo decides nothing; a written hybrid keeps what the cut must; identity
        by a shared picture only for a thing that keeps a look, in focus."""
        world = {'elements': WORLD['elements'] + [{'id': 'sea', 'function': 'phenomenon', 'description': 'sea layers'}]}
        facts = {'claims': [{'claim_id': 'c1', 'text': 'it sinks 9 m a minute', 'about': ['sea'], 'source_ids': ['a']}]}
        script = {'lines': [{'line_id': 'l1', 'text': 'x', 'claim_ids': ['c1']}]}
        with mock.patch('studio.explains.line_things', return_value=[]):
            self.assertEqual(maker.propose('p', cut('s1', [], focus=['sea'])[0], facts, script, world)['mode'], 'generative')
            typo = cut('s2', [], focus=['lake'], keeps=[{'kind': 'label', 'ids': ['nonexistent_thing']}])[0]
            self.assertEqual(maker.propose('p', typo, {'claims': []}, {'lines': []}, WORLD)['mode'], 'generative')
            ident = cut('s3', [], focus=['lake'], keeps=[{'kind': 'identity', 'ids': ['lake']}])[0]   # a place keeps no look of its own
            self.assertNotEqual(maker.propose('p', ident, {'claims': []}, {'lines': []}, WORLD)['mode'], 'generative')
            ship = cut('s4', [], focus=['ship'], keeps=[{'kind': 'identity', 'ids': ['ship']}])[0]
            self.assertEqual(maker.propose('p', ship, {'claims': []}, {'lines': []}, WORLD)['mode'], 'generative')
        written = cut('s5', [PART], focus=['locks/valve'], size='medium', written={'mode': 'hybrid', 'why': 'we want the model look here'})[0]
        with mock.patch('studio.explains.line_things', return_value=[PART]):
            made = maker.maker_of('p', written, {'claims': []}, {'lines': []}, WORLD)
        self.assertEqual((made['given'], made['keeps'][0]['ids']), (True, ['locks/valve']))
        uncovered = {'shots': [cut('s6', [], focus=['lake'], keeps=[{'kind': 'motion', 'ids': ['sea']}],
                                   written={'mode': 'hybrid', 'why': 'the model look for the sea'})[0]]}
        with mock.patch('studio.explains.line_things', return_value=[]):
            errors, _ = maker.lines('p', uncovered, {'claims': []}, {'lines': []}, world)
        self.assertTrue(errors and 'motion of sea' in errors[0], errors)

    def test_every_method_row_says_what_it_keeps_and_how_well_it_is_known(self):
        table = maker.methods()
        for m in table['methods']:
            self.assertEqual(set(m['keeps']), set(maker.KEEP_KINDS), m['method'])
            self.assertTrue(m['why'] and m['quality'])
        self.assertTrue(any(m['method'] == 'blender' and m['available'] for m in table['methods']))   # something always keeps all

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
        body = {'shots': [cut('s1', focus=['ship', 'lake'])[0], {**cut('s2', show=key, focus=['ship'], size='detail')[0], 'duration_s': 6.0},
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
