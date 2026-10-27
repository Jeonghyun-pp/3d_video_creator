"""The paid video's prompt (studio2/prompts.video, video_negative, video_lint): a structure, not a list of cases - the
camera's one named move with where it ends and a hold, then the one thing that moves, then what must hold said as a
positive sentence, then "Single continuous shot."; what to avoid goes as nouns in negative_prompt. The check refuses a
prompt with more actions than its clip holds, a percentage for a camera distance, a "no/never/nothing" sentence or two
camera moves (research of 2026-10-27: Vertex video best practices, Runway Gen-4, Gigwise camera library)."""
from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from studio import decisions
from tests import studio2_fixture as fx


def codes(findings):
    return [f.split(':', 1)[0] for f in findings]


def moving_cut(**over):
    """A cut of another subject: a crane lifts a container off a ship's deck."""
    cut = fx.cut('c1', ['l01'], change={'kind': 'move', 'what': 'the crane lifts the container off the deck', 'at': [0.1, 0.9],
                                        'element': {'what': 'the red container', 'path': 'straight up from the deck to just under the crane boom',
                                                    'drawn': 'picture'}},
                 frames={'first': {'shows': 'the container on the deck, the crane spreader locked on its corners'},
                         'last': {'shows': 'the container high under the boom, the deck empty'}})
    cut['cutaway']['camera'] = {'move': 'still'}
    cut['video'] = {'holds': ['The container stays locked to the crane spreader at all four corners; spreader and container rise together '
                              'as one piece.'], 'avoid': ['swinging container', 'detached container']}
    cut.update(over)
    return cut


class VideoPromptTest(unittest.TestCase):
    def test_camera_then_action_then_holds_then_one_continuous_shot(self):
        from studio2 import prompts
        body = fx.body()
        text = prompts.video(moving_cut(), body)
        sentences = [s.strip() for s in text.split('. ') if s.strip()]
        self.assertTrue(sentences[0].startswith('The locked-off camera remains perfectly still'), text)
        self.assertLess(text.index('red container moves'), text.index('stays locked to the crane spreader'))
        self.assertTrue(text.endswith('Single continuous shot.'), text)
        self.assertNotIn(body['look'][:20], text)          # motion only: the picture already holds the look
        self.assertEqual(prompts.video_lint(text, 4.0), [], text)

    def test_a_push_names_its_move_its_end_and_a_hold_never_a_percentage(self):
        from studio2 import prompts
        cut = fx.cut('c1', ['l01'])                         # a push by 0.06
        text = prompts.video(cut, fx.body())
        self.assertIn('dolly in', text.lower())
        self.assertIn('ending on', text)
        self.assertIn('holds', text)
        self.assertNotIn('%', text)
        cut['cutaway']['camera']['ends'] = 'a close-up of the floor holes'
        self.assertIn('ending on a close-up of the floor holes', prompts.video(cut, fx.body()))

    def test_what_to_avoid_goes_to_the_negative_prompt_as_nouns(self):
        from studio2 import prompts
        negative = prompts.video_negative(moving_cut(), fx.body())
        for noun in ('text', 'camera cut', 'morphing', 'swinging container', 'camera movement'):   # base, the cut's, the still row's
            self.assertIn(noun, negative)
        self.assertEqual(prompts.negative_lint(negative), [])
        self.assertIn('VIDEO_NEGATIVE_NOUNS', codes(prompts.negative_lint('text, the container must not swing at all')))

    def test_the_check_refuses_more_actions_than_the_clip_holds(self):
        from studio2 import prompts
        two = ('The locked-off camera remains perfectly still. The parcel slides off the belt into the bin, then the bin rolls '
               'away to the dock. Single continuous shot.')
        self.assertIn('VIDEO_PROMPT_ACTIONS', codes(prompts.video_lint(two, 4.0)))
        self.assertEqual(prompts.video_lint(two, 8.0), [])               # an 8 s clip holds two
        again = two.replace('then the bin rolls away to the dock', 'and the next parcel does the same again')
        self.assertIn('VIDEO_PROMPT_ACTIONS', codes(prompts.video_lint(again, 4.0)))

    def test_the_check_refuses_a_percentage_a_negation_and_two_camera_moves(self):
        from studio2 import prompts
        end = ' Single continuous shot.'
        self.assertIn('VIDEO_PROMPT_PERCENT', codes(prompts.video_lint('Slow dolly in toward the valve, about 10% closer.' + end, 4.0)))
        for said in ('Nothing else moves.', 'No text appears.', "The pipes don't bend.", 'The roof never shakes.'):
            got = prompts.video_lint('The locked-off camera remains perfectly still. ' + said + end, 4.0)
            self.assertIn('VIDEO_PROMPT_NEGATION', codes(got), said)
        both = 'Slow dolly in toward the engine while the camera pans left across the hangar.' + end
        self.assertIn('VIDEO_PROMPT_CAMERA', codes(prompts.video_lint(both, 4.0)))
        self.assertIn('VIDEO_PROMPT_ORDER', codes(prompts.video_lint('The engine turns over.', 4.0)))
        for finding in prompts.video_lint('Slow dolly in, 10% closer. Nothing moves.', 4.0):
            self.assertIn(' - ', finding)                                    # every finding carries how to fix it

    def test_a_ride_is_first_person_travel_along_its_way(self):
        from studio2 import prompts
        cut = fx.cut('c1', ['l01'])
        cut['cutaway'].update(view='rider', camera={'move': 'travel', 'amount': 0.5,
                                                   'along': 'straight down the narrow capillary toward where it opens into a wider vessel'})
        text = prompts.video(cut, fx.body())
        self.assertTrue(text.startswith('First-person view'), text)
        self.assertIn('straight down the narrow capillary', text)
        self.assertIn('steady high speed', text)
        self.assertIn('at the frame edges with slight motion blur', text)
        self.assertEqual(prompts.video_lint(text, 2.0), [], text)

    def test_a_new_camera_row_brings_its_own_words(self):
        # N+1: a camera move nobody wrote code for is one row of the grammar, its words included
        from studio2 import grammar, prompts
        table = grammar.load()
        table['fields']['camera.move']['values']['orbit'] = {
            'means': 'the camera circles the subject', 'video': {'says': 'The camera orbits slowly around {toward}, ending on {ends}, and holds there.',
                                                               'ends': 'its far side', 'terms': ['orbits'], 'avoid': ['wobble']}}
        cut = fx.cut('c1', ['l01'])
        cut['cutaway']['camera'] = {'move': 'orbit', 'amount': 0.1}
        with mock.patch('studio2.grammar.load', return_value=table):
            text = prompts.video(cut, fx.body())
            self.assertIn('The camera orbits slowly around', text)
            self.assertIn('wobble', prompts.video_negative(cut, fx.body()))
            self.assertEqual(prompts.video_lint(text, 4.0), [], text)


class PlanRefusesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = fx.project(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def errors(self, body):
        decisions.validate_body('cuts', body)
        return decisions.lint(self.path, 'cuts', body)['errors']

    def test_a_cut_whose_paid_prompt_breaks_the_rules_is_refused_at_the_plan(self):
        body = fx.body()
        body['cuts'][0] = moving_cut()
        self.assertFalse([e for e in self.errors(body) if e.startswith('VIDEO_PROMPT')], self.errors(body))
        chained = deepcopy(body)
        chained['cuts'][0]['change']['element']['path'] = 'up off the deck, then across to the quay and down onto a truck'
        self.assertTrue(any(e.startswith('VIDEO_PROMPT_ACTIONS: c1') for e in self.errors(chained)), self.errors(chained))
        denied = deepcopy(body)
        denied['cuts'][0]['video']['holds'] = ['Nothing else on the ship moves.']
        self.assertTrue(any(e.startswith('VIDEO_PROMPT_NEGATION: c1') for e in self.errors(denied)), self.errors(denied))

    def test_a_hold_names_its_pair_from_the_cuts_parts_and_a_contact_kind_row(self):
        """A hold may name what it holds (held, holder: the cut's parts) and how (kind: a row of library/video_checks.json
        contact_kinds), so the paid clip's contact is measured from the plan, never guessed from part names; the prompt
        still gets the sentence."""
        from studio2 import prompts
        body = fx.body()
        cut = moving_cut()
        cut['cutaway']['parts'] = [{'id': 'container', 'words': 'the red container'}, {'id': 'spreader', 'words': 'the crane spreader'}]
        cut['cutaway']['labels'] = [{'part': 'container', 'word': '컨테이너'}]
        cut['video']['holds'] = [{'says': cut['video']['holds'][0], 'held': 'container', 'holder': 'spreader', 'kind': 'carried'}]
        body['cuts'][0] = cut
        self.assertIn('spreader and container rise together', prompts.video(cut, body))
        self.assertFalse([e for e in self.errors(body) if e.startswith('VIDEO_HOLD')], self.errors(body))
        for wrong in ({'held': 'deck'}, {'holder': 'container'}, {'kind': 'welded_by_magic'}, {'at': [0.8, 0.2]}):
            bad = deepcopy(body)
            bad['cuts'][0]['video']['holds'][0].update(wrong)
            self.assertTrue(any(e.startswith('VIDEO_HOLD_PAIR: c1') for e in self.errors(bad)), (wrong, self.errors(bad)))

    def test_the_camera_a_prompt_names_is_read_back_from_its_words(self):
        """A clip made before its request was kept (2026-10-27, fab c01-c04) has only the words it was sent: the camera
        is read back by the grammar rows' own terms, and a percentage beside a move whose amount is the picture's growth
        is that amount. Two moves or none read back as unknown, never guessed."""
        from studio2 import prompts
        self.assertEqual(prompts.camera_said('Photoreal. The camera pushes in slowly and steadily toward the box, about 8% closer. It ends.'),
                         {'move': 'push', 'amount': 0.08})
        self.assertEqual(prompts.camera_said('The camera holds still. The box rises.'), {'move': 'still'})
        self.assertEqual(prompts.camera_said('The box rises into the hoist.'), {})
        self.assertEqual(prompts.camera_said('The camera pushes in, then pulls back.'), {})

    def test_a_ride_plans_its_way_and_travels_beyond_the_references_growth(self):
        body = fx.body()
        ride = body['cuts'][0]
        ride['show_how']['way'] = {'id': 'ride', 'rejected': {'id': 'follow', 'why': 'watched from outside the trip is counted, not felt'}}
        ride['cutaway'].update(view='rider', camera={'move': 'travel', 'amount': 0.8})
        report = decisions.lint(self.path, 'cuts', body)
        self.assertTrue(any(e.startswith('CAMERA_UNDECIDED: c1') and 'along' in e for e in report['errors']), report['errors'])
        ride['cutaway']['camera']['along'] = 'straight down the vent shaft toward the light at its end'
        report = decisions.lint(self.path, 'cuts', body)
        self.assertFalse([e for e in report['errors'] if 'c1' in e and ('CAMERA' in e or 'WAY' in e or 'VALUE' in e)], report['errors'])
        self.assertFalse([w for w in report['warnings'] if w.startswith('CAMERA_AMOUNT_OFF: c1')])   # a ride is not a push's growth


class FreeRideTest(unittest.TestCase):
    def test_the_free_preview_rides_at_a_steady_speed_and_streaks_its_edges(self):
        from PIL import Image
        from studio2 import motion
        travel, push = {'move': 'travel', 'amount': 0.6}, {'move': 'push', 'amount': 0.6}
        steps = [motion.camera_zoom(travel, t, 2.0) for t in (0.0, 0.5, 1.0, 1.5, 2.0)]
        self.assertAlmostEqual(steps[1] - steps[0], steps[3] - steps[2], places=6)   # steady, not eased: a ride does not settle
        self.assertAlmostEqual(steps[-1], 1.6)
        self.assertLess(motion.camera_zoom(push, 0.5, 2.0) - 1, steps[1] - 1)          # a push eases in
        stripes = Image.new('RGB', (90, 160))
        for x in range(0, 90, 6):
            stripes.paste((255, 255, 255), (x, 0, x + 3, 160))
        streaked = motion.streak(stripes, 1.3, 0.5, 0.5, 0.6)
        def spread(im, box):
            from statistics import pstdev
            return pstdev(im.crop(box).convert('L').getdata())
        self.assertLess(spread(streaked, (0, 70, 10, 90)), spread(stripes, (0, 70, 10, 90)))     # the edges smear
        self.assertEqual(streaked.getpixel((45, 80)), motion.zoom(stripes, 1.3, 0.5, 0.5).getpixel((45, 80)))   # the way ahead stays sharp


    def test_a_ride_smears_the_picture_never_its_labels(self):
        # fab c04 (2026-10-27): the label near the frame's edge came out streaked with the walls passing behind it
        from PIL import Image
        from studio2 import motion
        stripes = Image.new('RGB', (90, 160))
        for x in range(0, 90, 6):
            stripes.paste((255, 255, 255), (x, 0, x + 3, 160))
        marks = motion.Marks(90, 160)
        marks.s.rectangle((2, 70, 12, 90), fill=(255, 0, 0, 255))    # a mark at the edge, where the smear is strongest
        got = motion.ride_frame(stripes, marks, 1.0, 0.5, 0.5)
        self.assertEqual(got.getpixel((6, 80)), (255, 0, 0))           # the mark stays as drawn
        self.assertNotEqual(got.getpixel((6, 20)), motion.zoom(stripes, 1.0, 0.5, 0.5).getpixel((6, 20)))   # the picture smears


    def test_a_rides_labels_follow_their_parts_at_their_own_size_inside_the_frame(self):
        # fab c04b (2026-10-27): its label grew with the ride's 1.6x and ran off the frame's left edge
        from studio2 import motion
        font = motion.font_for(30)
        plan = {'labels': [('최대 1,400단계', (400.0, 1400.0), (300.0, 1300.0), 0.1, 1.0)], 'flows': [([(540.0, 960.0), (540.0, 1200.0)], None, (0.0, 1.0))],
                'element': None, 'font': font, 'safe': (60.0, 100.0, 1020.0, 1700.0), 'fixed': []}
        got = motion.follow_zoom(plan, 1.6, 0.5, 0.5, 1080, 1920)
        (word, anchor, spot, _s, _p), = got['labels']
        self.assertAlmostEqual(anchor[0], 540 + (400 - 540) * 1.6, places=3)       # the part moves with the picture
        self.assertAlmostEqual(anchor[1], 960 + (1400 - 960) * 1.6, places=3)
        self.assertEqual(got['flows'][0][0][1], (540.0, 960 + 240 * 1.6))
        probe = motion.ImageDraw.Draw(motion.Image.new('L', (8, 8)))
        x0, y0, x1, y1 = probe.textbbox(spot, word, font=font, anchor='mm')
        self.assertTrue(60 <= x0 and x1 <= 1020 and 100 <= y0 and y1 <= 1700, (x0, y0, x1, y1))   # its letters stay inside
        self.assertIs(motion.follow_zoom(plan, 1.0, 0.5, 0.5, 1080, 1920)['labels'][0][2], plan['labels'][0][2])


class ClipArgumentsTest(unittest.TestCase):
    """What leaves for the provider: our prompt as written (its rewrite switch off) and the negative nouns."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        (self.path / 'first.png').write_bytes(b'i')

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, **spec):
        from studio.generative.clip import build_arguments
        base = {'model': 'veo-3.1', 'operation': 'image_to_video', 'duration_seconds': 4, 'inputs': [{'kind': 'first_frame', 'path': 'first.png'}]}
        return build_arguments(self.path, {'output': {'width': 1080, 'height': 1920}}, {**base, **spec}, 'p', uri=lambda f: Path(f).name)

    def test_the_provider_never_rewrites_our_prompt(self):
        from studio.generative.clip import PROMPT_REWRITE_OFF
        self.assertIs(self.args()['auto_fix'], False)
        self.assertIs(self.args(operation='text_to_video', inputs=[])['auto_fix'], False)   # fal veo3.1 t2v: auto_fix defaults to true
        self.assertEqual(PROMPT_REWRITE_OFF['wan-2.2-vace'], {'enable_prompt_expansion': False})

    def test_the_negative_nouns_go_in_the_models_own_field(self):
        self.assertEqual(self.args(negative_prompt='text, camera cut')['negative_prompt'], 'text, camera cut')
        self.assertNotIn('negative_prompt', self.args())
        from studio.common import StudioError
        (self.path / 'v.mp4').write_bytes(b'v')
        with self.assertRaises(StudioError) as caught:   # a model with no such field never drops it silently
            self.args(model='kling-o1-edit', operation='video_to_video', negative_prompt='text', inputs=[{'kind': 'previs', 'path': 'v.mp4'}])
        self.assertEqual(caught.exception.code, 'INPUT_INVALID')


if __name__ == '__main__':
    unittest.main()
