"""What is said must be seen (studio/explains.py): what a line explains comes from its claims' `about`; what is inside
its subject comes from the exposure survey; the ways to open come from the show table, never a name in the rule."""
from pathlib import Path
import tempfile
import unittest

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


if __name__ == '__main__':
    unittest.main()
