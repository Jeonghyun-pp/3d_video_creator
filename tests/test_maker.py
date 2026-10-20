"""Who makes each cut (studio/maker.py): what a viewer takes as fact - a claimed part, a motion that explains, the
subject's structure in focus - stays Blender (or hybrid); places, moods, scale, people and results are generated. Read
from the shot list's screen and the lines, never from a world's per-video declaration."""
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
        self.assertEqual(self.propose(*cut('s1', [LAKE], focus=['lake']))['mode'], 'generative')   # a place
        self.assertEqual(self.propose(*cut('s2', [PART]))['rule'], 'part_said')
        opened = [{'at': 'end', 'parts': ['locks/culvert'], 'how': 'section', 'why': 'inside'}]
        self.assertEqual(self.propose(*cut('s3', [LAKE], opened))['rule'], 'explained_motion')
        key = [{'at': 'end', 'parts': ['ship'], 'how': 'exterior', 'why': 'it rises', 'key': True}]
        self.assertEqual(self.propose(*cut('s4', [], key))['mode'], 'blender')
        # an action on what the lines speak of, held in focus: the motion explains (a set piece, not a place or weather)
        SHIP = {'id': 'ship', 'kind': 'element', 'subject': None, 'parts': [], 'words': 'a ship'}
        self.assertEqual(self.propose(*cut('s5', [SHIP], focus=['ship'], size='close', action='the ship rises'))['rule'], 'said_motion')
        self.assertEqual(self.propose(*cut('s5b', [LAKE], focus=['lake'], size='wide', action='the lake glitters'))['mode'], 'generative')
        # the same action on a thing no line speaks of: the model's
        self.assertEqual(self.propose(*cut('s6', [LAKE], focus=['ship'], size='medium', action='the ship waits'))['mode'], 'generative')
        # the subject element in focus close: its structure reads; wide, it is a place
        self.assertEqual(self.propose(*cut('s7', [], focus=['locks'], size='medium'))['rule'], 'structure_in_focus')
        self.assertEqual(self.propose(*cut('s8', [], focus=['locks'], size='wide'))['mode'], 'generative')
        # a world's "exact" requirement alone decides nothing (2026-10-20: the canal's lock)
        self.assertEqual(self.propose(*cut('s9', [], focus=['gate'], size='close', elements=('gate',)))['mode'], 'generative')
        self.assertEqual([r[0] for r in maker.MAKER_RULES][-1], 'default')

    def test_a_subject_spec_in_focus_reads_as_structure(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'subjects' / 'bike').mkdir(parents=True)
            (Path(root) / 'subjects' / 'bike' / 'spec.json').write_text('{}')
            self.assertEqual(self.propose(*cut('s1', [], focus=['bike'], size='close'), path=root)['rule'], 'structure_in_focus')

    def test_a_written_maker_is_kept_but_never_generative_over_a_fact(self):
        body = {'shots': [cut('s1', focus=['lake'], written={'mode': 'generative', 'why': 'the model paints it'})[0]]}
        with mock.patch('studio.explains.line_things', return_value=[PART]):
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

    def test_a_part_in_focus_close_reads_as_structure(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'subjects' / 'lock').mkdir(parents=True)
            (Path(root) / 'subjects' / 'lock' / 'spec.json').write_text('{"builders": [{"part_id": "gate"}], "features": []}')
            self.assertEqual(self.propose(*cut('s1', [], focus=['lock/gate'], size='detail'), path=root)['rule'], 'structure_in_focus')

    def test_the_sheet_says_each_cut_the_share_and_a_shared_look(self):
        body = {'shots': [cut('s1', focus=['ship', 'lake'])[0], {**cut('s2', focus=['ship'])[0], 'duration_s': 6.0},
                          cut('s3', focus=['lake'])[0]]}
        with mock.patch('studio.explains.line_things', side_effect=[[LAKE], [PART], [LAKE]]):
            rows, share = maker.table('p', body, {'claims': []}, {'lines': []}, WORLD)
        self.assertEqual([(r['shot_id'], r['mode']) for r in rows], [('s1', 'generative'), ('s2', 'blender'), ('s3', 'generative')])
        self.assertEqual(share, {'generative': 0.4, 'hybrid': 0.0, 'blender': 0.6})
        # the ship recurs only in a Blender cut: no shared look; the lake is context, never shared
        self.assertEqual([r.get('shared_reference') for r in rows], [None, None, None])
        with mock.patch('studio.explains.line_things', side_effect=[[LAKE], [LAKE], [LAKE]]):
            rows, _share = maker.table('p', body, {'claims': []}, {'lines': []}, WORLD)
        # the first cut sets the ship's look; only the later one takes it (from s1's start picture, at drawing time)
        self.assertEqual([r.get('shared_reference') for r in rows], [None, {'from': 's1', 'ids': ['ship']}, None])


if __name__ == '__main__':
    unittest.main()
