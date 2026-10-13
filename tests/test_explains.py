"""What is said must be seen (studio/explains.py): what a line explains comes from its claims' `about`; what is inside
its subject comes from the exposure survey; the ways to open come from the show table, never a name in the rule."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from studio import decisions, explains
from studio.blender_ops import exposure_core, show_core
from studio.common import file_hash, write_json
from studio.project import init_project

SPEC = {'subject_id': 'arm', 'identity': 'a robot arm joint',
        'builders': [{'part_id': 'casing'}, {'part_id': 'gear_0'}, {'part_id': 'gear_1'}, {'part_id': 'handle'}],
        'features': [{'id': 'feat.gears', 'description': 'planetary gears inside the casing', 'part_ids': ['gear_0', 'gear_1']},
                     {'id': 'feat.grip', 'description': 'the handle and a gear', 'part_ids': ['handle', 'gear_0']}]}
WORLD = {'elements': [{'id': 'engineer', 'description': 'an engineer at a bench'}]}


def write_spec(project, spec=SPEC):
    write_json(Path(project, 'subjects', spec['subject_id'], 'spec.json'), spec)


def write_exposure(project, shares, subject='arm'):
    spec = Path(project, 'subjects', subject, 'spec.json')
    write_json(spec.parent / explains.EXPOSURE, {'spec_sha256': file_hash(spec), 'threshold': 0.5, 'directions': 64, 'samples': 300,
                                                 'parts': {p: {'best_share': s, 'best_dir': [0, 0, 1], 'hidden_by': ['casing'] if s < 0.5 else []}
                                                           for p, s in shares.items()}})


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        write_spec(self.project)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ids_resolve_to_parts_features_subjects_and_elements(self):
        kinds = {t['id']: (t['kind'], t['parts']) for t in explains.things(self.project, ['arm/feat.gears', 'arm/handle', 'arm', 'engineer'], WORLD)}
        self.assertEqual(kinds['arm/feat.gears'], ('feature', ['arm/gear_0', 'arm/gear_1']))
        self.assertEqual(kinds['arm/handle'], ('part', ['arm/handle']))
        self.assertEqual(kinds['arm'][0], 'subject')
        self.assertEqual(kinds['engineer'][0], 'element')

    def test_unknown_waits_for_the_world_for_bare_names(self):
        self.assertEqual(explains.unknown(self.project, ['arm/nothing', 'ghost/x', 'engineer']), ['arm/nothing', 'ghost/x'])
        self.assertEqual(explains.unknown(self.project, ['engineer', 'ghost'], WORLD, bare=True), ['ghost'])

    def test_a_line_explains_the_union_of_its_claims_about(self):
        facts = {'claims': [{'claim_id': 'a', 'about': ['arm/feat.gears']}, {'claim_id': 'b', 'about': ['engineer', 'arm/feat.gears']},
                            {'claim_id': 'old'}]}   # approved before about existed: explains nothing checkable
        script = {'lines': [{'line_id': 'l1', 'claim_ids': ['a', 'b']}, {'line_id': 'l2', 'claim_ids': ['old']}, {'line_id': 'l3', 'illustrative': 'x'}]}
        self.assertEqual(explains.line_about(facts, script, ['l1', 'l2', 'l3']), ['arm/feat.gears', 'engineer'])
        self.assertEqual([t['id'] for t in explains.line_things(self.project, ['l1'], facts, script, WORLD)], ['arm/feat.gears', 'engineer'])


class InsideTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        write_spec(self.project)

    def tearDown(self):
        self.tmp.cleanup()

    def thing(self, about):
        return explains.resolve(self.project, about, WORLD)

    def test_no_survey_or_an_edited_spec_is_named(self):
        self.assertEqual(explains.need(self.project, self.thing('arm/feat.gears'))['problem'], 'EXPOSURE_MISSING')
        write_exposure(self.project, {'casing': 1.0, 'gear_0': 0.1, 'gear_1': 0.0, 'handle': 0.9})
        write_spec(self.project, {**SPEC, 'identity': 'edited'})
        self.assertEqual(explains.need(self.project, self.thing('arm/feat.gears'))['problem'], 'EXPOSURE_STALE')

    def test_a_feature_is_inside_only_when_all_its_parts_are(self):
        write_exposure(self.project, {'casing': 1.0, 'gear_0': 0.1, 'gear_1': 0.0, 'handle': 0.9})
        gears = explains.need(self.project, self.thing('arm/feat.gears'))
        self.assertEqual((gears['inside'], gears['hidden_by']), (True, ['casing']))
        self.assertFalse(explains.need(self.project, self.thing('arm/feat.grip'))['inside'])   # the handle shows it from outside
        self.assertIsNone(explains.need(self.project, self.thing('engineer'))['inside'])        # nothing to measure
        self.assertFalse(explains.need(self.project, self.thing('arm'))['inside'])

    def test_the_threshold_is_the_one_recorded_with_the_survey(self):
        self.assertTrue(exposure_core.inside(0.49, 0.5))
        self.assertFalse(exposure_core.inside(0.5, 0.5))
        self.assertFalse(exposure_core.inside(0.49, 0.6))

    def test_directions_cover_the_sphere(self):
        dirs = exposure_core.directions()
        self.assertEqual(len(dirs), exposure_core.DIRECTIONS)
        self.assertTrue(all(abs(sum(c * c for c in d) - 1) < 1e-4 for d in dirs))
        self.assertLess(abs(sum(d[2] for d in dirs)), 1e-3)   # as many below as above
        self.assertTrue(any(d[0] > 0.9 for d in dirs) and any(d[0] < -0.9 for d in dirs))


class OpeningWordsTest(unittest.TestCase):
    def test_a_new_opening_row_joins_without_a_code_change(self):
        row = {'opens': 'cut', 'extent': 'parts', 'open_s': 1.0, 'draw': 'peeled'}
        original = dict(show_core.SHOW_METHODS)
        try:
            show_core.SHOW_METHODS['peel'] = row
            show_core.METHODS = tuple(show_core.SHOW_METHODS)
            self.assertIn('peel', explains.open_words())
            self.assertNotIn('exterior', explains.open_words())
        finally:
            show_core.SHOW_METHODS.clear(); show_core.SHOW_METHODS.update(original)
            show_core.METHODS = tuple(show_core.SHOW_METHODS)


class FactsLintTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('explains', {'request': 'arm', 'shots': [{'shot_id': 'draft', 'frame_count': 30}]}, self.tmp.name)['project_path'])
        write_spec(self.project)

    def tearDown(self):
        self.tmp.cleanup()

    def lint(self, claims):
        return decisions.lint(self.project, 'facts', {'sources': [{'source_id': 's', 'title': 't'}], 'claims': claims})['errors']

    def test_every_claim_says_what_it_is_about(self):
        claim = {'claim_id': 'c', 'text': 'gears multiply torque', 'source_ids': ['s']}
        self.assertIn('CLAIM_ABOUT_MISSING', ' '.join(self.lint([claim])))
        self.assertEqual(self.lint([{**claim, 'about': []}]), [])
        self.assertEqual(self.lint([{**claim, 'about': ['arm/feat.gears', 'engineer']}]), [])   # engineer: for the world to know
        self.assertIn('ABOUT_UNKNOWN', ' '.join(self.lint([{**claim, 'about': ['arm/feat.nothing']}])))


BRIEF = {'topic': 'how a robot arm joint turns', 'audience': 'curious adults', 'length_s': 10, 'key_message': 'gears trade speed for force',
         'subject_mode': 'schematic'}


class BeatPicturesTest(unittest.TestCase):
    """A beat's pictures are read for what its lines explain; the world lint holds them to it."""

    def setUp(self):
        from PIL import Image
        from studio.concept import add_beat
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('beats', {'request': 'arm', 'shots': [{'shot_id': 'draft', 'frame_count': 30}]}, self.tmp.name)['project_path'])
        write_spec(self.project)
        write_exposure(self.project, {'casing': 1.0, 'gear_0': 0.1, 'gear_1': 0.0, 'handle': 0.9})
        for layer, body in (('brief', BRIEF),
                            ('facts', {'sources': [{'source_id': 's', 'title': 't'}],
                                       'claims': [{'claim_id': 'g', 'text': 'gears multiply torque', 'source_ids': ['s'], 'about': ['arm/feat.gears']},
                                                  {'claim_id': 'e', 'text': 'an engineer tunes it', 'source_ids': ['s'], 'about': ['engineer']}]}),
                            ('script', {'lines': [{'line_id': 'l1', 'text': '관절 속 기어가 힘을 키웁니다.', 'claim_ids': ['g', 'e']}]})):
            rev = decisions.propose(self.project, layer, body)['rev']
            decisions.approve(self.project, layer, '좋아요 이대로 가요', rev)
        image = Path(self.tmp.name) / 'beat.png'
        Image.new('RGB', (90, 160), (120, 120, 120)).save(image)
        self.picture = add_beat(self.project, 'b1', image, 'the arm, closed', for_things=['arm/feat.gears'])['picture']
        self.world = {'kind': 'none', 'beats': [{'beat_id': 'b1', 'line_ids': ['l1'], 'pictures': [self.picture]}],
                      'elements': [{'id': 'engineer', 'function': 'agent', 'description': 'an engineer at a bench',
                                    'owner': {'shape': 'blender', 'look': 'generative'}, 'why': 'the line names who tunes it'},
                                   {'id': 'arm', 'function': 'subject', 'description': 'the arm', 'requirements': ['exact'],
                                    'owner': {'shape': 'blender', 'look': 'blender'}, 'why': 'its gears are the claim'}]}
        decisions.propose(self.project, 'world', self.world)

    def tearDown(self):
        self.tmp.cleanup()

    def errors(self):
        return ' '.join(decisions.lint(self.project, 'world', self.world)['errors'])

    def answer(self, shows, opened, how):
        def fake(images, prompt, schema, target):
            self.prompt = prompt
            write_json(target, {'answer': {'pictures': [{'index': 0, 'shows': shows, 'opened': opened, 'how': how, 'note': 'n'}]}})
            from studio.common import read_json
            return read_json(target)
        return mock.patch('studio.screen_grammar.codex_json', side_effect=fake)

    def read(self, shows, opened, how):
        from studio import beat_pictures
        with self.answer(shows, opened, how):
            return beat_pictures.read(self.project, 'b1', force=True)

    def test_an_unread_picture_is_named(self):
        self.assertIn('BEAT_PICTURE_UNREAD', self.errors())

    def test_unseen_and_closed_are_errors_and_opened_passes(self):
        self.read(['arm', 'engineer'], [], 'exterior')
        self.assertIn('arm/feat.gears', self.prompt)          # the reader is asked about what the line explains
        self.assertIn('BEAT_EXPLAINS_UNSEEN: b1 explains arm/feat.gears', self.errors())
        self.read(['arm/feat.gears', 'engineer'], [], 'exterior')
        self.assertIn('BEAT_INSIDE_CLOSED: b1 explains arm/feat.gears', self.errors())
        self.assertIn(explains.open_words(), self.errors())   # the ways to open come from the table
        self.read(['arm/gear_0', 'engineer'], ['arm/gear_0'], 'cutaway')
        self.assertEqual(self.errors(), '')

    def test_an_element_the_line_names_must_be_seen_too(self):
        self.read(['arm/feat.gears'], ['arm/feat.gears'], 'section')
        self.assertIn('BEAT_EXPLAINS_UNSEEN: b1 explains engineer', self.errors())

    def test_a_reading_is_bound_to_the_list_it_was_read_against(self):
        self.read(['arm/gear_0', 'engineer'], ['arm/gear_0'], 'cutaway')
        spec = dict(SPEC, features=[{**SPEC['features'][0], 'description': 'sun and planet gears'}, SPEC['features'][1]])
        write_spec(self.project, spec)
        write_exposure(self.project, {'casing': 1.0, 'gear_0': 0.1, 'gear_1': 0.0, 'handle': 0.9})
        self.assertIn('BEAT_PICTURE_UNREAD', self.errors())   # the gears described anew: read again

    def test_a_hand_correction_keeps_its_note(self):
        from studio import beat_pictures
        self.read(['arm', 'engineer'], [], 'exterior')
        marked = beat_pictures.mark(self.project, 'b1', self.picture['path'], shows=['arm/gear_1', 'engineer'], opened=['arm/gear_1'],
                                    how='section', note='the cut gears are drawn small at the left')
        self.assertTrue(beat_pictures.met(marked['judged']['things'][0]))
        self.assertEqual(self.errors(), '')
        with self.assertRaises(Exception):
            beat_pictures.mark(self.project, 'b1', self.picture['path'], shows=['ghost'], note='a thing nobody named')

    def test_the_brief_asks_for_what_is_missing_and_why(self):
        from studio import beat_pictures
        from studio.blender_ops.show_core import SHOW_METHODS, opening_methods
        self.read(['arm', 'engineer'], [], 'exterior')
        brief = beat_pictures.brief(self.project, 'b1')
        self.assertEqual(brief['things'], ['arm/feat.gears'])                 # the engineer is already seen
        self.assertIn('sits inside casing', brief['prompt'])
        self.assertTrue(all(SHOW_METHODS[h]['draw'] in brief['prompt'] for h in opening_methods()))
        self.assertNotIn(SHOW_METHODS['exterior']['draw'], brief['prompt'])
        self.assertIn('--for arm/feat.gears', ' '.join(brief['next']))
        self.read(['arm/gear_0', 'engineer'], ['arm/gear_0'], 'cutaway')
        self.assertTrue(beat_pictures.brief(self.project, 'b1')['done'])

    def test_redraws_stop_at_the_limit(self):
        from PIL import Image
        from studio import beat_pictures
        from studio.common import StudioError
        from studio.concept import add_beat
        drawn = []
        for n in range(beat_pictures.MAX_DRAWS):
            image = Path(self.tmp.name) / f'try{n}.png'
            Image.new('RGB', (90, 160), (10 * n, 0, 0)).save(image)
            drawn.append(add_beat(self.project, 'b1', image, 'try', for_things=['arm/feat.gears'])['path'])
            with self.answer(['arm/feat.gears'], [], 'exterior'):          # drawn, but closed
                beat_pictures.read(self.project, 'b1', [drawn[-1]])
            if n < beat_pictures.MAX_DRAWS - 1:
                self.assertIn(drawn[-1], beat_pictures.brief(self.project, 'b1')['prompt'])   # the miss is named
        with self.assertRaises(StudioError) as caught:
            beat_pictures.brief(self.project, 'b1')
        self.assertEqual(caught.exception.code, 'DRAW_LIMIT')

    def test_photos_about_a_thing_are_attached_only_with_the_users_words(self):
        from studio import beat_pictures, reference_use
        self.read(['arm', 'engineer'], [], 'exterior')
        brief = beat_pictures.brief(self.project, 'b1')
        self.assertEqual(brief['attach'], [])
        self.assertIn('--about arm/feat.gears', ' '.join(brief['next']))         # no photo yet: collect one
        photos = self.project / 'references' / 'photos'
        photos.mkdir(parents=True, exist_ok=True)
        rows = []
        for n, (query, title) in enumerate((('robot arm gearbox', 'File:a.jpg'), ('robot arm gearbox', 'File:b.jpg'), (None, 'File:c.jpg'))):
            (photos / f'p{n}.jpg').write_bytes(b'x')
            rows.append({'file': f'references/photos/p{n}.jpg', 'title': title, 'query': query, 'subject_id': 'arm', 'licence': 'CC BY 4.0'})
        write_json(photos / 'index.json', {'schema_version': 1, 'photos': rows})
        reference_use.record_collect(self.project, ['arm/feat.gears'], 'robot arm gearbox')
        reference_use.about(self.project, 'references/photos/p2.jpg', ['arm/gear_1'])
        reference_use.exclude(self.project, 'references/photos/p1.jpg', 'a maker logo on the casing')
        brief = beat_pictures.brief(self.project, 'b1')
        self.assertEqual((brief['attach'], brief['compare_only']), ([], ['references/photos/p0.jpg', 'references/photos/p2.jpg']))
        self.assertIn('attach-consent', ' '.join(brief['next']))
        reference_use.consent(self.project, '첨부도 가능')
        brief = beat_pictures.brief(self.project, 'b1')
        self.assertEqual([a['path'] for a in brief['attach']], ['references/photos/p0.jpg', 'references/photos/p2.jpg'])
        self.assertIn('never a logo', brief['prompt'])

    def test_the_shot_list_shows_what_its_lines_explain_opened_when_inside(self):
        from studio.decisions import envelope, explain_lines
        facts, script = envelope(self.project, 'facts')['body'], envelope(self.project, 'script')['body']

        def errors(**shot):
            body = {'shots': [{'shot_id': 's1', 'line_ids': ['l1'], 'duration_s': 3, 'elements': ['engineer'], **shot}]}
            return ' '.join(explain_lines(self.project, body, facts, script, self.world))
        self.assertIn('SHOT_EXPLAINS_UNSHOWN: s1', errors())
        closed = [{'at': 'start', 'parts': ['arm/gear_0'], 'how': 'exterior', 'why': 'the gears turn'}]
        self.assertIn('SHOW_INSIDE_CLOSED: s1', errors(show=closed))
        self.assertIn(explains.open_words(), errors(show=closed))
        self.assertEqual(errors(show=[{**closed[0], 'how': 'xray'}]), '')
        self.assertIn('SHOT_EXPLAINS_UNSHOWN: s1\'s lines explain engineer', errors(show=[{**closed[0], 'how': 'xray'}], elements=['arm']))
        (self.project / 'subjects' / 'arm' / explains.EXPOSURE).unlink()
        self.assertIn('EXPOSURE_MISSING', errors(show=closed))           # not measured: seen is all it can ask
        self.assertNotIn('SHOW_INSIDE_CLOSED', errors(show=closed))

    def test_the_shot_brief_says_what_its_lines_explain(self):
        from studio.concept import explained_lines
        decisions.propose(self.project, 'shotlist', {'shots': [{'shot_id': 'draft', 'purpose': 'the joint', 'line_ids': ['l1'],
                                                                       'duration_s': 10, 'route_features': ['exact_motion']}]})
        said = ' '.join(explained_lines(self.project, 'draft'))
        self.assertIn('arm/feat.gears', said)
        self.assertIn('sits inside casing', said)

    def test_no_survey_is_named_with_its_command(self):
        (self.project / 'subjects' / 'arm' / explains.EXPOSURE).unlink()
        self.read(['arm/feat.gears', 'engineer'], [], 'exterior')
        self.assertIn('EXPOSURE_MISSING', self.errors())
        self.assertIn('studio subject exposure', self.errors())


if __name__ == '__main__':
    unittest.main()
