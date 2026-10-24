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
        """A line explains the water's fall as the key change: no mask cuts water out of a take - only Blender keeps it."""
        world = {'elements': WORLD['elements'] + [{'id': 'water', 'function': 'phenomenon', 'description': 'water'}]}
        key = [{'at': 'end', 'parts': ['water'], 'how': 'exterior', 'why': 'it falls', 'key': True}]
        entry, _ = cut('s1', [], key, focus=['water'], size='medium')
        with mock.patch('studio.explains.line_things', return_value=[WATER]):
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
        body = {'shots': [cut('s1', focus=['lake'], keeps=[{'kind': 'shape', 'ids': ['lake']}])[0]]}   # written keeps decide nothing
        with mock.patch('studio.explains.line_things', return_value=[]):
            _, warnings = maker.lines('p', body, {'claims': []}, {'lines': []}, WORLD)
            self.assertEqual(maker.propose('p', body['shots'][0], {'claims': []}, {'lines': []}, WORLD)['mode'], 'generative')
        self.assertTrue(any(w.startswith('KEEPS_NOT_READ') for w in warnings), warnings)

    def test_review_2026_10_20_numbers_ids_and_written_makers(self):
        """A speed said of the sea is no count; a typo decides nothing; a written hybrid keeps what the cut must; identity
        by a shared picture only for a thing that keeps a look, in focus."""
        world = {'elements': WORLD['elements'] + [{'id': 'sea', 'function': 'phenomenon', 'description': 'sea layers'}]}
        facts = {'claims': [{'claim_id': 'c1', 'text': 'it sinks 9 m a minute', 'about': ['sea'], 'source_ids': ['a']}]}
        script = {'lines': [{'line_id': 'l1', 'text': 'x', 'claim_ids': ['c1']}]}
        with mock.patch('studio.explains.line_things', return_value=[]):
            self.assertEqual(maker.propose('p', cut('s1', [], focus=['sea'])[0], facts, script, world)['mode'], 'generative')
            generative = next(m for m in maker.methods()['methods'] if m['method'] == 'generative')
            lake = maker._cut('p', cut('s3', [], focus=['lake'])[0], {'claims': []}, {'lines': []}, WORLD)
            ship = maker._cut('p', cut('s4', [], focus=['ship'])[0], {'claims': []}, {'lines': []}, WORLD)
            self.assertFalse(maker.covers(generative, {'kind': 'identity', 'ids': ['lake']}, lake))   # a place keeps no look of its own
            self.assertTrue(maker.covers(generative, {'kind': 'identity', 'ids': ['ship']}, ship))
            self.assertTrue(maker.covers(generative, {'kind': 'label', 'ids': ['nonexistent_thing']}, lake))   # a typo decides nothing
        written = cut('s5', [PART], focus=['locks/valve'], size='medium', written={'mode': 'hybrid', 'why': 'we want the model look here'})[0]
        with mock.patch('studio.explains.line_things', return_value=[PART]):
            made = maker.maker_of('p', written, {'claims': []}, {'lines': []}, WORLD)
        self.assertEqual((made['given'], made['keeps'][0]['ids']), (True, ['locks/valve']))
        SEA = {'id': 'sea', 'kind': 'element', 'subject': None, 'parts': [], 'words': 'sea layers'}
        sinks = [{'at': 'end', 'parts': ['sea'], 'how': 'exterior', 'why': 'the layer drops', 'key': True}]
        uncovered = {'shots': [cut('s6', [], sinks, focus=['sea'], size='medium', written={'mode': 'hybrid', 'why': 'the model look for the sea'})[0]]}
        with mock.patch('studio.explains.line_things', return_value=[SEA]):
            errors, _ = maker.lines('p', uncovered, {'claims': []}, {'lines': []}, world)
        self.assertTrue(errors and 'motion of sea' in errors[0], errors)

    def test_every_method_row_says_what_it_keeps_and_how_well_it_is_known(self):
        table = maker.methods()
        for m in table['methods']:
            self.assertEqual(set(m['keeps']), set(maker.KEEP_KINDS), m['method'])
            self.assertTrue(m['why'] and m['quality'])
        self.assertTrue(any(m['method'] == 'blender' and m['available'] for m in table['methods']))   # something always keeps all

    def test_a_cut_with_no_screen_keeps_the_shot_list_a_draft(self):
        # 2026-10-24: a warning nothing read; writing the frame costs nothing and who makes the cut is read from it
        body = {'shots': [cut('s1')[0], cut('s2', focus=['nowhere'])[0]]}
        with mock.patch('studio.explains.line_things', return_value=[]), mock.patch('studio.maker._built_thing', return_value=False):
            errors, warnings = maker.lines('p', body, {'claims': []}, {'lines': []}, WORLD)
        self.assertEqual(sum(e.startswith('FRAME_UNDESCRIBED') for e in errors + warnings), 2, (errors, warnings))
        self.assertTrue(all('says nothing' in e for e in errors if e.startswith('FRAME_UNDESCRIBED')), errors)

    def test_a_built_cut_never_shows_a_part_only_a_model_makes(self):
        """E2E 2026-10-21: a generated water column in a hybrid cut's show stopped the build (KEY_PART_UNKNOWN)."""
        world = {'elements': WORLD['elements'] + [{'id': 'mist', 'function': 'phenomenon', 'description': 'mist',
                                                   'owner': {'shape': 'generative', 'look': 'generative'}}]}
        row = [{'at': 'end', 'parts': ['mist'], 'how': 'exterior', 'why': 'the mist rises', 'key': True}]
        built = cut('s1', show=row, focus=['mist'], written={'mode': 'blender', 'why': 'the user wants it built'})[0]
        made = cut('s2', show=row, focus=['mist'], written={'mode': 'generative', 'why': 'a model paints the mist'})[0]
        with mock.patch('studio.explains.line_things', return_value=[]):
            errors, warnings = maker.lines('p', {'shots': [built, made]}, {'claims': []}, {'lines': []}, world)
        self.assertEqual([e.split(':')[0] + ' ' + e.split(' ')[1] for e in errors], ['SHOW_PART_GENERATED s1'])
        row[0]['parts'] = ['ship']   # built by default (a clay stand-in), but nothing builds it yet: said
        with mock.patch('studio.explains.line_things', return_value=[]):
            errors, warnings = maker.lines('p', {'shots': [built]}, {'claims': []}, {'lines': []}, world)
        self.assertTrue(any(w.startswith('SHOW_PART_UNBUILT: s1') for w in warnings), warnings)

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
