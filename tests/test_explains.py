"""What is said must be seen (studio/explains.py): what a line explains comes from its claims' `about`; what is inside
its subject comes from the exposure survey; the ways to open come from the show table, never a name in the rule."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from studio import decisions, explains
from studio.blender_ops import exposure_core, show_core
from studio.common import file_hash, read_json, write_json
from studio.project import init_project

SPEC = {'subject_id': 'arm', 'identity': 'a robot arm joint',
        'builders': [{'part_id': 'casing'}, {'part_id': 'gear_0'}, {'part_id': 'gear_1'}, {'part_id': 'handle'}],
        'features': [{'id': 'feat.gears', 'description': 'planetary gears inside the casing', 'part_ids': ['gear_0', 'gear_1']},
                     {'id': 'feat.grip', 'description': 'the handle and a gear', 'part_ids': ['handle', 'gear_0']}]}
WORLD = {'elements': [{'id': 'engineer', 'description': 'an engineer at a bench'}]}


def write_spec(project, spec=SPEC):
    write_json(Path(project, 'subjects', spec['subject_id'], 'spec.json'), spec)


def write_exposure(project, shares, subject='arm', rows=None, version=exposure_core.SCHEMA_VERSION):
    """A survey record as `studio subject exposure` writes it: shares {part: best_share} (the same share from every
    direction), or whole part rows (per_direction, exposable_share, enclosed_by, box) in `rows`."""
    spec = Path(project, 'subjects', subject, 'spec.json')
    parts = {p: {'best_share': s, 'best_dir': [0, 0, 1], 'hidden_by': ['casing'] if s < 0.5 else [], 'per_direction': [s] * 64,
                 'exposable_share': s, 'enclosed_by': {}, 'box': [[0, 0, 0], [0.1, 0.1, 0.1]]} for p, s in shares.items()}
    parts.update(rows or {})
    write_json(spec.parent / explains.EXPOSURE, {'schema_version': version, 'spec_sha256': file_hash(spec), 'threshold': 0.5,
                                                 'directions': 64, 'samples': 300, 'parts': parts})


DIRECTION_STOPS = ('HOOK_WEAK', 'KEY_CHANGE_UNPLANNED', 'KEY_CHANGE_UNSHOWN', 'DIRECTION_UNRESOLVED')


def ladder_only(project):
    """These tests are about the ladder's mechanics, not direction: the direction rules (tests/test_direction_rules.py)
    warn here instead of stopping (project.policy.gates)."""
    from studio.common import read_json, write_json
    data = read_json(Path(project) / 'project.json')
    data.setdefault('policy', {}).setdefault('gates', {}).update({c: 'warn' for c in DIRECTION_STOPS})
    write_json(Path(project) / 'project.json', data)


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
        # before the world: only names under a written spec can be judged (ghost/x may still be modelled)
        self.assertEqual(explains.unknown(self.project, ['arm/nothing', 'ghost/x', 'engineer']), ['arm/nothing'])
        self.assertEqual(explains.unknown(self.project, ['engineer', 'ghost', 'ghost/x'], WORLD, bare=True), ['ghost', 'ghost/x'])

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

    def test_a_survey_from_before_directions_were_kept_is_stale(self):
        # A3-3 (2026-10-14): a v1 survey kept only each part's best side; the view checks need every direction's share
        write_exposure(self.project, {'casing': 1.0, 'gear_0': 0.1, 'gear_1': 0.0, 'handle': 0.9}, version=1)
        self.assertEqual(explains.need(self.project, self.thing('arm/feat.gears'))['problem'], 'EXPOSURE_STALE')
        self.assertEqual(explains.exposure(self.project, 'arm')[1], 'EXPOSURE_STALE')

    def test_a_feature_is_inside_only_when_all_its_parts_are(self):
        write_exposure(self.project, {'casing': 1.0, 'gear_0': 0.1, 'gear_1': 0.0, 'handle': 0.9})
        gears = explains.need(self.project, self.thing('arm/feat.gears'))
        self.assertEqual((gears['inside'], gears['hidden_by']), (True, ['casing']))
        self.assertFalse(explains.need(self.project, self.thing('arm/feat.grip'))['inside'])   # the handle shows it from outside
        self.assertIsNone(explains.need(self.project, self.thing('engineer'))['inside'])        # nothing to measure
        self.assertFalse(explains.need(self.project, self.thing('arm'))['inside'])

    def test_a_stale_survey_under_a_must_see_part_is_said_at_the_build(self):
        from studio.blender import _exposables, exposure_lines
        shot = {'shot_id': 's', 'camera': {'move': {'frames': [{'at': 0.0, 'must_see': ['arm/gear_0']}, {'at': 1.0, 'must_see': ['arm']}]}}}
        spec_paths = {'arm': 'staged/arm.spec.json'}
        write_exposure(self.project, {'casing': 1.0, 'gear_0': 0.1, 'gear_1': 0.0, 'handle': 0.9})
        self.assertEqual(exposure_lines(self.project, shot, spec_paths), [])                      # fresh: used, nothing said
        write_spec(self.project, {**SPEC, 'identity': 'edited'})
        self.assertEqual(_exposables(self.project, spec_paths), {})                               # stale: left out ...
        lines = exposure_lines(self.project, shot, spec_paths)                                    # ... and said
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('EXPOSURE_STALE: s:') and "['arm/gear_0']" in lines[0] and 'spec changed' in lines[0], lines)
        self.assertEqual(exposure_lines(self.project, {**shot, 'camera': {}}, spec_paths), [])   # no must-see part: nothing to say
        survey = Path(self.project, 'subjects', 'arm', explains.EXPOSURE)
        record = read_json(survey)
        write_spec(self.project, SPEC)
        write_json(survey, {**record, 'schema_version': 1})                                       # an older schema: said as such
        self.assertIn('schema 1', exposure_lines(self.project, shot, spec_paths)[0])
        survey.unlink()                                                                           # none at all: said too
        lines = exposure_lines(self.project, shot, spec_paths)
        self.assertTrue(lines[0].startswith('EXPOSURE_MISSING: s:') and 'no exposure survey' in lines[0], lines)

    def test_an_untagged_coat_is_said_once_per_subject_and_never_offered_for_a_shown_part(self):
        from studio.blender import exposure_lines
        shot = {'shot_id': 's', 'camera': {'move': {'frames': [{'at': 0.0, 'must_see': ['arm/gear_0', 'arm/gear_1']}]}}}
        write_exposure(self.project, {'casing': 1.0, 'gear_0': 0.1, 'gear_1': 0.0, 'handle': 0.9})
        survey = Path(self.project, 'subjects', 'arm', explains.EXPOSURE)
        record = read_json(survey)
        record['parts']['gear_0']['coated_by'] = {'handle': 0.05, 'gear_1': 0.04, 'casing': 0.01}   # gear_1 is shown: a part
        record['parts']['gear_1']['coated_by'] = {'handle': 0.03}
        write_json(survey, record)
        lines = exposure_lines(self.project, shot, {'arm': 'staged/arm.spec.json'})
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith('COAT_UNTAGGED: s: arm:'), lines)
        self.assertIn('handle lies on gear_0', lines[0])
        self.assertIn('handle lies on gear_1', lines[0])
        self.assertNotIn('gear_1 lies', lines[0])    # a part the shot must show is not offered as a coat
        self.assertNotIn('casing', lines[0])         # a stray hit (under COAT_MIN_SHARE) is not said

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


CRATE = {'subject_id': 'crate', 'identity': 'nine cans in a carton', 'builders': [{'part_id': 'carton'}] + [{'part_id': f'can_{n}'} for n in range(9)]}
THERMOS = {'subject_id': 'thermos', 'identity': 'a thermos with a straw', 'builders': [{'part_id': p} for p in ('shell', 'glass', 'straw')]}


def crate_rows():
    """3 x 3 cans: the middle one (can_4) shows only from above; can_1 stands in the front row (the -y side) and its
    lower part is inside the carton, so 0.6 of it shows from any side at all and 0.5 from its own side."""
    dirs = exposure_core.directions()
    middle = {'per_direction': [0.4 if d[2] > 0.8 else 0.0 for d in dirs], 'exposable_share': 0.4}
    front = {'per_direction': [0.5 if d[1] < -0.3 else 0.1 for d in dirs], 'exposable_share': 0.6}
    return {'can_4': {**middle, 'best_share': 0.4, 'best_dir': [0, 0, 1], 'hidden_by': ['can_1'], 'enclosed_by': {'can_1': 0.3}, 'box': [[0, 0, 0], [0.07, 0.07, 0.12]]},
            'can_1': {**front, 'best_share': 0.5, 'best_dir': [0, -1, 0], 'hidden_by': ['carton'], 'enclosed_by': {'carton': 0.3}, 'box': [[0, -0.08, 0], [0.07, -0.01, 0.12]]}}


class ViewTest(unittest.TestCase):
    """A3-5 (2026-10-14): a part the picture's own view cannot show from outside is found while it is still words -
    judged against what of the part any side shows at all (its exposable share), from the direction nearest the view."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('views', {'request': 'cans', 'shots': [{'shot_id': 's1', 'frame_count': 30}]}, self.tmp.name)['project_path'])
        ladder_only(self.project)
        write_spec(self.project, CRATE)
        write_exposure(self.project, {'carton': 1.0, **{f'can_{n}': 1.0 for n in range(9)}}, subject='crate', rows=crate_rows())
        write_spec(self.project, THERMOS)
        write_exposure(self.project, {'shell': 1.0}, subject='thermos',
                       rows={'glass': {'best_share': 0.05, 'best_dir': [0, 0, 1], 'hidden_by': ['shell'], 'per_direction': [0.05] * 64,
                                       'exposable_share': 0.05, 'enclosed_by': {'shell': 0.95}, 'box': [[0, 0, 0], [0.06, 0.06, 0.2]]},
                             'straw': {'best_share': 0.1, 'best_dir': [0, 0, 1], 'hidden_by': ['glass'], 'per_direction': [0.0] * 64,
                                       'exposable_share': 0.1, 'enclosed_by': {'glass': 0.85, 'shell': 0.05}, 'box': [[0, 0, 0], [0.01, 0.01, 0.25]]}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_middle_can_from_the_side_hides_and_says_where_it_shows(self):
        side = {'elevation_deg': 0.0, 'azimuth_deg': 0.0}
        hides = explains.view_hides(self.project, 'crate/can_4', side)
        self.assertIsNotNone(hides)
        self.assertGreater(hides['best']['elevation_deg'], 45.0)              # from above
        line = explains.view_line(self.project, 's1 t=1.0', 'crate/can_4', side)
        self.assertTrue(line.startswith('SHOW_VIEW_HIDES'))
        self.assertIn(explains.open_words(), line)                             # or opened, by any opening row
        self.assertIsNone(explains.view_hides(self.project, 'crate/can_4', {'elevation_deg': 70.0, 'azimuth_deg': 0.0}))

    def test_an_outer_can_passes_against_what_of_it_shows_at_all(self):
        # its lower part sits in the carton from every side: 0.5 seen of 0.6 that can be seen is not 'hidden'
        self.assertIsNone(explains.view_hides(self.project, 'crate/can_1', {'elevation_deg': 0.0, 'azimuth_deg': 0.0}))
        self.assertIsNotNone(explains.view_hides(self.project, 'crate/can_1', {'elevation_deg': 0.0, 'azimuth_deg': 180.0}))

    def test_not_judged_without_a_survey_or_a_part(self):
        self.assertIsNone(explains.view_hides(self.project, 'crate', {'elevation_deg': 0.0, 'azimuth_deg': 0.0}))
        self.assertIsNone(explains.view_hides(self.project, 'pump/impeller', {'elevation_deg': 0.0, 'azimuth_deg': 0.0}))

    def shot_list(self, show):
        return {'shots': [{'shot_id': 's1', 'purpose': 'p', 'line_ids': [], 'duration_s': 3, 'show': show}]}

    def test_the_shot_list_names_a_side_view_that_hides_its_part(self):
        from studio.project import load_shot, shot_path
        shot = load_shot(self.project, 's1')
        frame = lambda at, elevation: {'at': at, 'height_share': 0.5, 'centre': [0.5, 0.5], 'lens_mm': 35.0,  # noqa: E731
                                       'view': {'elevation_deg': elevation, 'azimuth_deg': 0.0}}
        shot['camera'] = {**shot['camera'], 'movement': 'rig', 'keys': [],
                          'move': {'type': 'frame_to_frame', 'params': {'target': 'crate'}, 'frames': [frame(0.0, 60.0), frame(1.0, 0.0)]}}
        write_json(shot_path(self.project, 's1'), shot)
        row = {'parts': ['crate/can_4'], 'how': 'exterior', 'why': 'the middle can'}
        lines = decisions.view_lines(self.project, self.shot_list([{**row, 'at': 'start'}, {**row, 'at': 'end'}]))
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('SHOW_VIEW_HIDES: s1 t=1.0'))     # the start's picture looks from above
        self.assertEqual(decisions.view_lines(self.project, self.shot_list([{**row, 'at': 'end', 'how': 'cutaway'}])), [])

    def test_two_must_see_parts_one_inside_the_other_are_named(self):
        nested = decisions.view_lines(self.project, self.shot_list([{'at': 'end', 'parts': ['thermos/straw', 'thermos/glass'], 'how': 'xray',
                                                                      'why': 'the straw in the glass'}]))
        self.assertEqual(len(nested), 1)
        self.assertTrue(nested[0].startswith('MUST_SEE_NESTED: s1'))
        self.assertIn('thermos/glass', nested[0])
        self.assertEqual(decisions.view_lines(self.project, self.shot_list([{'at': 'end', 'parts': ['thermos/straw', 'thermos/shell'],
                                                                            'how': 'xray', 'why': 'w'}])), [])   # 0.05: not around it


class ReachTest(unittest.TestCase):
    """MUST_SEE_UNREACHABLE's bounds (exposure_core): never smaller than what a picture can really show."""

    def test_a_part_never_shows_thicker_than_its_two_short_sides(self):
        self.assertAlmostEqual(exposure_core.reach_m(([0, 0, 0], [0.01, 1.0, 0.01])), 0.01 * 2 ** 0.5)   # a rod
        self.assertAlmostEqual(exposure_core.reach_m(([0, 0, 0], [0.5, 0.002, 0.1])), (0.1 ** 2 + 0.002 ** 2) ** 0.5)   # a plate face on

    def test_the_pictures_scale_allows_any_turn_perspective_and_a_subject_laid_down(self):
        focal = 1000.0
        mast = ([-0.5, -0.5, 0.0], [0.5, 0.5, 20.0])                      # 20 m tall, drawn 400 px tall and 20 px wide
        far = focal * 20.0 / 400                                           # its far side at least 50 m away
        self.assertAlmostEqual(exposure_core.most_px_per_m(mast, 0.0, 400, 20, focal), focal / (far - 2 ** 0.5))   # near side
        self.assertGreater(exposure_core.most_px_per_m(mast, 0.0, 400, 20, focal), 400 / 20.0)   # more than its middle shows
        # the scene lays it down: the picture draws its 20 m length 400 px across (20 px/m in its middle) - only its
        # shortest side and its whole diagonal are sure, and the bound stays above what it shows
        self.assertGreater(exposure_core.most_px_per_m(mast, 0.0, 20, 400, focal), 400 / 20.0)
        # drawn so large that the camera may stand inside the subject's reach: no bound
        self.assertEqual(exposure_core.most_px_per_m(mast, 0.0, 1900, 100, 100.0), float('inf'))


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
        ladder_only(self.project)
        write_spec(self.project)

    def tearDown(self):
        self.tmp.cleanup()

    def lint(self, claims):
        return decisions.lint(self.project, 'facts', {'sources': [{'source_id': 's', 'title': 't'}], 'claims': claims})['errors']

    def test_every_claim_says_what_it_is_about(self):
        claim = {'claim_id': 'c', 'text': 'gears multiply torque', 'source_ids': ['s']}
        self.assertIn('CLAIM_ABOUT_MISSING', ' '.join(self.lint([claim, {**claim, 'claim_id': 'd', 'about': []}])))
        self.assertEqual(self.lint([claim]), [])            # facts from before about: warned, not refused
        self.assertIn('ABOUT_UNSET', ' '.join(decisions.lint(self.project, 'facts', {'sources': [{'source_id': 's', 'title': 't'}],
                                                                                  'claims': [claim]})['warnings']))
        self.assertEqual(self.lint([{**claim, 'about': []}]), [])
        self.assertEqual(self.lint([{**claim, 'about': ['pump/impeller']}]), [])   # no pump spec yet: the world lint asks
        self.assertEqual(self.lint([{**claim, 'about': ['arm/feat.gears', 'engineer']}]), [])   # engineer: for the world to know
        self.assertIn('ABOUT_UNKNOWN', ' '.join(self.lint([{**claim, 'about': ['arm/feat.nothing']}])))


from tests.test_story import STORY

BRIEF = {'topic': 'how a robot arm joint turns', 'audience': 'curious adults', 'length_s': 10, 'key_message': 'gears trade speed for force',
         'subject_mode': 'schematic'}


class BeatPicturesTest(unittest.TestCase):
    """A beat's pictures are read for what its lines explain; the world lint holds them to it."""

    def setUp(self):
        from PIL import Image
        from studio.concept import add_beat
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('beats', {'request': 'arm', 'shots': [{'shot_id': 'draft', 'frame_count': 30}]}, self.tmp.name)['project_path'])
        ladder_only(self.project)
        write_spec(self.project)
        write_exposure(self.project, {'casing': 1.0, 'gear_0': 0.1, 'gear_1': 0.0, 'handle': 0.9})
        for layer, body in (('brief', BRIEF),
                            ('facts', {'sources': [{'source_id': 's', 'title': 't'}],
                                       'claims': [{'claim_id': 'g', 'text': 'gears multiply torque', 'source_ids': ['s'], 'about': ['arm/feat.gears']},
                                                  {'claim_id': 'e', 'text': 'an engineer tunes it', 'source_ids': ['s'], 'about': ['engineer']}]}),
                            ('story', STORY),
                            ('script', {'lines': [{'line_id': 'l1', 'text': '관절 속 기어가 힘을 키웁니다.', 'claim_ids': ['g', 'e'], 'story_beat_id': 'peak'}]})):
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
        def fake(images, prompt, schema, target, force=False):   # codex_json's signature (force: read again)
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
        reference_use.record_job(self.project, 'job_1', ['arm/feat.gears'])
        reference_use.record_result(self.project, 'job_1', {'collected': [{'file': 'references/photos/p0.jpg'}, {'file': 'references/photos/p1.jpg'}]})
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
        facts['claims'][0]['about'] = ['arm/feat.renamed']                 # a spec renamed under approved facts: never silent
        self.assertIn('ABOUT_UNKNOWN', errors(show=closed))
        facts['claims'][0]['about'] = ['arm/feat.gears']
        self.assertNotIn('SHOW_INSIDE_CLOSED', errors(show=closed))

    def test_a_line_said_across_a_cut_is_shown_by_one_of_its_shots(self):
        """2026-10-20: a line running into the next shot explains over both - one of them showing it is enough, and a
        miss is said once, for the line's first shot."""
        from studio.decisions import envelope, explain_lines
        facts, script = envelope(self.project, 'facts')['body'], envelope(self.project, 'script')['body']
        opened = [{'at': 'start', 'parts': ['arm/gear_0'], 'how': 'xray', 'why': 'the gears turn'}]
        body = {'shots': [{'shot_id': 's1', 'line_ids': ['l1'], 'duration_s': 2, 'elements': ['engineer']},
                          {'shot_id': 's2', 'line_ids': ['l1'], 'duration_s': 2, 'elements': ['engineer'], 'show': opened}]}
        self.assertEqual(explain_lines(self.project, body, facts, script, self.world), [])
        body['shots'][1].pop('show')
        found = [e for e in explain_lines(self.project, body, facts, script, self.world) if e.startswith('SHOT_EXPLAINS_UNSHOWN')]
        self.assertEqual(len(found), 1, found)
        self.assertIn('s1', found[0])

    def test_the_shot_brief_says_what_its_lines_explain(self):
        from studio.concept import explained_lines
        decisions.propose(self.project, 'shotlist', {'shots': [{'shot_id': 'draft', 'purpose': 'the joint', 'line_ids': ['l1'],
                                                                       'duration_s': 10, 'route_features': ['exact_motion']}]})
        said = ' '.join(explained_lines(self.project, 'draft'))
        self.assertIn('arm/feat.gears', said)
        self.assertIn('sits inside casing', said)

    def test_a_subject_is_drawn_from_its_own_spec(self):
        write_spec(self.project, {**SPEC, 'dimensions': [{'id': 'dim.height', 'value_m': 0.8, 'measure': 'z'}]})
        thing = explains.resolve(self.project, 'arm', WORLD)
        self.assertEqual(explains.look_words(self.project, thing), 'planetary gears inside the casing; the handle and a gear; height 0.8 m')

    def test_a_world_edit_elsewhere_keeps_the_readings_and_a_hand_correction_stays(self):
        from studio import beat_pictures
        self.read(['arm/gear_0', 'engineer'], ['arm/gear_0'], 'cutaway')
        self.world['elements'].append({'id': 'bench', 'function': 'set', 'description': 'a workbench',
                                       'owner': {'shape': 'blender', 'look': 'generative'}, 'why': 'where the arm stands'})
        self.assertEqual(self.errors(), '')
        beat_pictures.mark(self.project, 'b1', self.picture['path'], how='section', note='the cut is a section, not a window')
        with self.answer(['arm'], [], 'exterior'):
            beat_pictures.read(self.project, 'b1', force=True)                 # never read over the person's correction
        from studio.common import read_json
        kept = read_json(beat_pictures.reading_path(self.project, self.picture['path']))
        self.assertEqual((kept['read']['how'], kept['read']['opened']), ('section', ['arm/gear_0']))

    def test_a_picture_that_opens_something_else_shows_the_inside_closed(self):
        self.read(['arm/gear_0', 'engineer'], [], 'cutaway')
        self.assertIn('BEAT_INSIDE_CLOSED', self.errors())

    def test_the_users_words_start_the_count_again(self):
        from PIL import Image
        from studio import beat_pictures
        from studio.common import StudioError
        from studio.concept import add_beat
        for n in range(beat_pictures.MAX_DRAWS):
            image = Path(self.tmp.name) / f'again{n}.png'
            Image.new('RGB', (90, 160), (0, 10 * n, 0)).save(image)
            path = add_beat(self.project, 'b1', image, 'try', for_things=['arm/feat.gears'])['path']
            with self.answer(['arm/feat.gears'], [], 'exterior'):
                beat_pictures.read(self.project, 'b1', [path])
        with self.assertRaises(StudioError):
            beat_pictures.brief(self.project, 'b1')
        brief = beat_pictures.brief(self.project, 'b1', user_words='엑스레이로 기어만 밝게')
        self.assertIn('엑스레이로 기어만 밝게', brief['prompt'])
        with self.assertRaises(StudioError):
            add_beat(self.project, 'b1', image, 'try', for_things=['arm/feat.gearz'])   # a typo never counts silently

    def test_a_feature_naming_an_unbuilt_part_is_the_specs_error(self):
        spec = dict(SPEC, features=[{**SPEC['features'][0], 'part_ids': ['gear_0', 'gear_9']}, SPEC['features'][1]])
        write_spec(self.project, spec)
        write_exposure(self.project, {'casing': 1.0, 'gear_0': 0.1, 'gear_1': 0.0, 'handle': 0.9})
        self.read(['arm/gear_0', 'engineer'], ['arm/gear_0'], 'cutaway')
        self.assertIn('SPEC_PART_UNBUILT', self.errors())

    def test_shots_build_the_subjects_their_elements_name_and_read_what_they_show(self):
        from studio.frame_targets import subject_words
        from studio.project import load_shot
        self.read(['arm/gear_0', 'engineer'], ['arm/gear_0'], 'cutaway')
        decisions.approve(self.project, 'world', '이 세계로 가요', decisions.envelope(self.project, 'world')['sheet']['rev'])
        shots = {'shots': [{'shot_id': 'gears', 'purpose': 'the gears inside', 'line_ids': ['l1'], 'duration_s': 5, 'elements': ['arm', 'engineer'],
                            'show': [{'at': 'end', 'parts': ['arm/gear_0'], 'how': 'cutaway', 'why': 'the gears inside'}]},
                           {'shot_id': 'bench', 'purpose': 'the engineer', 'line_ids': [], 'duration_s': 2, 'elements': ['engineer']}]}
        decisions.approve(self.project, 'shotlist', '이 샷들로 가요', decisions.propose(self.project, 'shotlist', shots)['rev'])
        gears, bench = load_shot(self.project, 'gears'), load_shot(self.project, 'bench')
        self.assertEqual((gears['subjects'], bench['subjects']), ([{'subject_id': 'arm'}], []))
        self.assertEqual((gears['scene']['instances'], bench.get('scene')), ([{'id': 'arm', 'subject': 'arm', 'at': [0, 0, 0]}], None))
        self.assertEqual(subject_words(self.project, bench), 'an engineer at a bench')   # builds nothing: what it shows

    def test_no_survey_is_named_with_its_command(self):
        (self.project / 'subjects' / 'arm' / explains.EXPOSURE).unlink()
        self.read(['arm/feat.gears', 'engineer'], [], 'exterior')
        self.assertIn('EXPOSURE_MISSING', self.errors())
        self.assertIn('studio subject exposure', self.errors())


if __name__ == '__main__':
    unittest.main()
