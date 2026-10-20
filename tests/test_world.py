"""The world layer (studio/world.py): the minute seen as pictures first, the world's kind, place and light, and who
makes each element's shape and look - from requirements, never from the topic."""
from pathlib import Path
import tempfile
import unittest

from studio import decisions, world
from studio.common import StudioError
from studio.project import init_project, load_shot

BRIEF = {'topic': 'why a footstep upstairs is heard downstairs', 'audience': 'apartment residents', 'length_s': 8,
         'key_message': 'the thump travels through the structure', 'subject_mode': 'schematic'}
FACTS = {'sources': [{'source_id': 'kcs', 'title': 'floor impact sound code', 'citation': 'KCS 41'}],
         'claims': [{'claim_id': 'layers', 'text': 'a floating floor rests on a resilient layer', 'source_ids': ['kcs'], 'about': []}]}
SCRIPT = {'lines': [{'line_id': 'l1', 'text': '위층 아이가 뛰면 쿵 소리가 납니다.', 'illustrative': 'the event'},
                    {'line_id': 'l2', 'text': '바닥은 완충재 위에 떠 있습니다.', 'claim_ids': ['layers']}]}


def element(eid, function, reqs=(), shape='blender', look='generative', ids=()):
    return {'id': eid, 'function': function, 'description': eid.replace('_', ' '), 'requirements': list(reqs),
            'owner': {'shape': shape, 'look': look}, 'why': 'what this shot needs of it', **({'ids': list(ids)} if ids else {})}


class OwnerRulesTest(unittest.TestCase):
    def test_requirements_force_owners_and_the_rest_is_clay_filled(self):
        self.assertEqual(world.suggested_owner({'requirements': ['labelled']}, 'continuous'), {'shape': 'blender', 'look': 'blender'})
        self.assertEqual(world.suggested_owner({'requirements': ['motion_bound']}, 'continuous'), {'shape': 'blender', 'look': 'generative'})
        self.assertEqual(world.suggested_owner({'requirements': []}, 'continuous'), {'shape': 'blender', 'look': 'generative'})
        self.assertEqual(world.suggested_owner({'requirements': ['far_background']}, 'continuous'), {'shape': 'generative', 'look': 'generative'})
        self.assertEqual(world.suggested_owner({'requirements': ['far_background', 'recurring']}, 'independent')['shape'], 'blender')

    def test_conflicts_name_the_requirement(self):
        bad = element('layers', 'subject', ['labelled'], look='generative')
        self.assertIn('labelled', world.owner_conflicts(bad, 'continuous')[0])
        moving = element('child', 'agent', ['motion_bound'], shape='generative')
        self.assertTrue(world.owner_conflicts(moving, 'continuous'))
        self.assertEqual(world.owner_conflicts(element('sky', 'context', ['far_background'], 'generative'), 'continuous'), [])


class ShotPlanTest(unittest.TestCase):
    WORLD = {'kind': 'continuous', 'elements': [
        element('layers', 'subject', ['labelled'], 'blender', 'blender', ids=['floor']),
        element('room', 'set', ids=['room']), element('child', 'agent', ['motion_bound'], ids=['child']),
        element('estate', 'context', ['far_background'], 'generative', 'generative'),
        element('afternoon', 'light', look='generative'), element('kitchen', 'set')]}

    def test_regions_mode_and_role_follow_the_owners(self):
        plan = world.shot_plan(self.WORLD, ['layers', 'room', 'child', 'estate', 'afternoon'])
        self.assertEqual((plan['mode'], plan['role']), ('hybrid', 'explain'))
        self.assertEqual(plan['generate_only'], ['room', 'child', '@background'])
        mood = world.shot_plan(self.WORLD, ['room', 'child'])                 # clay stand-ins only: still Blender's camera
        self.assertEqual((mood['mode'], mood['role']), ('hybrid', 'mood'))
        self.assertEqual(world.shot_plan(self.WORLD, ['estate'])['mode'], 'generative')
        self.assertEqual(world.shot_plan(self.WORLD, ['layers'])['mode'], 'blender')
        self.assertEqual(world.shot_plan(self.WORLD, ['kitchen'])['unbuilt'], ['kitchen'])   # no stand-in linked yet

    def test_the_world_light_fills_what_the_shot_leaves(self):
        lit = {'kind': 'continuous', 'time_light': {'temperature_k': 4800, 'white_balance_k': 5600, 'sun': {'elevation_deg': 20}}}
        shot = {'render': {'look_preset': 'photoreal_exterior', 'lighting': {'sun': {'temperature_k': 6000}}}}
        out = world.lit(shot, lit)
        self.assertEqual(out['render']['lighting']['sun'], {'temperature_k': 6000, 'elevation_deg': 20})   # the shot's own wins
        self.assertEqual(out['render']['grade']['white_balance_k'], 5600)
        inside = world.lit({'render': {'look_preset': 'photoreal_interior'}}, lit)
        self.assertEqual(inside['render']['lighting'], {'practicals': {'temperature_k': 4800}})             # no sun in that preset
        self.assertEqual(shot['render']['lighting']['sun'], {'temperature_k': 6000})                          # never written back


class ContextVoidTest(unittest.TestCase):
    def test_an_empty_frame_in_a_world_with_a_place_is_said_unless_the_background_is_generated(self):
        report = {'frames': [{'frame': 0, 'opaque_share': 0.2}, {'frame': 50, 'opaque_share': 0.9}]}
        shot = {'screen': {}}
        lines = world.context_void(report, shot, {'kind': 'continuous'}, 0.44)
        self.assertTrue(lines[0].startswith('CONTEXT_VOID') and '80 %' in lines[0])
        self.assertEqual(world.context_void(report, {'screen': {'generate_only': ['@background']}}, {'kind': 'continuous'}, 0.44), [])
        self.assertEqual(world.context_void(report, shot, {'kind': 'none'}, 0.44), [])          # an explanation on a stage
        self.assertEqual(world.context_void({'frames': [{'frame': 0, 'opaque_share': 0.7}]}, shot, {'kind': 'independent'}, 0.44), [])


class WorldLayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('world', {'request': 'floor noise', 'shots': [{'shot_id': 'draft', 'frame_count': 30}]}, self.tmp.name)['project_path'])
        from tests.test_story import ladder_only
        ladder_only(self.project)   # the world layer's mechanics, not direction (tests/test_direction_rules.py)
        from tests.test_story import STORY, told
        for layer, body in (('brief', BRIEF), ('facts', FACTS), ('story', STORY), ('script', told(SCRIPT))):
            decisions.approve(self.project, layer, '좋아요 이대로', decisions.propose(self.project, layer, body)['rev'])

    def tearDown(self):
        self.tmp.cleanup()

    def picture(self, beat):
        from PIL import Image
        from studio.concept import add_beat
        image = Path(self.tmp.name) / f'{beat}.png'
        Image.new('RGB', (90, 160), (200, 160, 120)).save(image)
        return add_beat(self.project, beat, image, f'{beat} of the minute')['picture']

    def body(self, **change):
        body = {'kind': 'continuous', 'place': 'a Seoul apartment estate', 'time_light': {'time_of_day': 'afternoon', 'temperature_k': 4800},
                'beats': [{'beat_id': 'b1', 'line_ids': ['l1'], 'pictures': [self.picture('b1')]},
                          {'beat_id': 'b2', 'line_ids': ['l2'], 'pictures': [self.picture('b2')]}],
                'elements': [element('layers', 'subject', ['labelled'], 'blender', 'blender'), element('room', 'set'),
                             element('estate', 'context', ['far_background'], 'generative', 'generative')]}
        body['reference'] = body['beats'][0]['pictures'][0]
        body.update(change)
        return body

    def test_missing_context_reference_and_lines_block_approval(self):
        bare = self.body(elements=[element('layers', 'subject', ['labelled'], 'blender', 'blender')])
        bare.pop('reference')
        bare['beats'] = bare['beats'][:1]
        errors = decisions.propose(self.project, 'world', bare)['errors']
        self.assertTrue(any(e.startswith('CONTEXT_MISSING') for e in errors))
        self.assertTrue(any(e.startswith('WORLD_REFERENCE_MISSING') for e in errors))
        self.assertIn('script line l2 is in no beat', errors)
        none = self.body(kind='none', elements=[element('layers', 'subject', ['labelled'], 'blender', 'blender')])
        self.assertEqual(decisions.propose(self.project, 'world', none)['errors'], [])   # an explanation needs no place

    def test_the_shot_list_reads_the_world_and_shots_get_regions_and_routes(self):
        rev = decisions.propose(self.project, 'world', self.body())
        self.assertIn('| estate', Path(rev['sheet']).read_text())
        decisions.approve(self.project, 'world', '이 세계로 가요', rev['rev'])
        self.assertTrue((self.project / 'world.json').is_file())
        shots = {'shots': [{'shot_id': 'event', 'purpose': 'the child jumps', 'line_ids': ['l1'], 'duration_s': 3, 'elements': ['room', 'estate']},
                           {'shot_id': 'section', 'purpose': 'the floating floor', 'line_ids': ['l2'], 'duration_s': 5,
                            'elements': ['layers', 'room'], 'beat_ids': ['b2'],
                            # the cut is about the layers (a show row names them): their exact shape keeps it hybrid (maker)
                            'show': [{'at': 'end', 'parts': ['layers'], 'how': 'exterior', 'why': 'the layers of the floor'}]}]}
        bad = decisions.propose(self.project, 'shotlist', {'shots': [{**shots['shots'][0], 'elements': ['nothing']}, shots['shots'][1]]})
        self.assertIn('event: unknown world element nothing', bad['errors'])
        decisions.approve(self.project, 'shotlist', '좋아요 이 샷들로 가요', decisions.propose(self.project, 'shotlist', shots)['rev'])
        # a cut the model makes (maker: generative) keeps no regions: it has no Blender pass to keep from (set aside, said)
        self.assertNotIn('generate_only', load_shot(self.project, 'event')['screen'])
        linked = world.link(self.project, 'room', ['room'])                                           # its stand-in, built later
        self.assertEqual(linked['shots'], ['section'])   # the generated cut keeps no regions to resync
        event, section = load_shot(self.project, 'event'), load_shot(self.project, 'section')
        # who makes a cut is the shot list's (studio/maker.py, 2026-10-20): the jump claims no part - the model makes it
        self.assertEqual((event['route']['mode'], event['route']['role'], event['route']['rule_id']), ('generative', 'mood', 'S_shotlist_maker'))
        self.assertNotIn('generate_only', event['screen'])
        self.assertEqual((section['route']['mode'], section['route']['role'], section['screen']['generate_only']), ('hybrid', 'explain', ['room']))
        self.assertEqual(section['route']['generative']['model'], 'seedance-2.5')
        from studio.concept import picked, require
        beat = picked(self.project, 'section')                # no pick of its own: the approved beat picture
        self.assertEqual((beat['concept_id'], beat['decision']['by']), ('beat:b2', 'world'))
        self.assertIsNone(picked(self.project, 'event'))      # a shot naming no beat has none
        self.assertEqual(require(self.project, 'section')['concept_id'], 'beat:b2')

    def test_a_shot_the_plan_now_generates_lets_go_of_an_old_build(self):
        from studio.common import write_json
        from studio.project import shot_path
        decisions.approve(self.project, 'world', '이 세계로 가요', decisions.propose(self.project, 'world', self.body())['rev'])
        old = {'shots': [{'shot_id': 'event', 'purpose': 'the floating floor', 'line_ids': ['l1', 'l2'], 'duration_s': 5, 'elements': ['layers', 'room']}]}
        decisions.approve(self.project, 'shotlist', '좋아요 이 샷들로', decisions.propose(self.project, 'shotlist', old)['rev'])
        built = load_shot(self.project, 'event'); built['scene_version'] = 'v0003'
        write_json(shot_path(self.project, 'event'), built)     # an earlier plan's build of the same id
        new = {'shots': [{'shot_id': 'event', 'purpose': 'the estate at dusk', 'line_ids': ['l1', 'l2'], 'duration_s': 5, 'elements': ['estate']}]}
        self.assertFalse(any(w.startswith('SHOT_ID_REUSED') for w in decisions.lint(self.project, 'shotlist', new)['warnings']))   # same words
        other = {'shots': [{**new['shots'][0], 'line_ids': ['l2']}, {'shot_id': 'more', 'purpose': 'the jump', 'line_ids': ['l1'], 'duration_s': 3,
                                                                       'elements': ['room']}]}
        self.assertTrue(any(w.startswith('SHOT_ID_REUSED: event') for w in decisions.lint(self.project, 'shotlist', other)['warnings']))
        result = decisions.approve(self.project, 'shotlist', '새 샷으로 가요', decisions.propose(self.project, 'shotlist', new)['rev'])
        event = load_shot(self.project, 'event')
        self.assertEqual((event['route']['mode'], event['scene_version']), ('generative', None))
        self.assertEqual(result['materialized']['retired_versions'], [{'shot_id': 'event', 'scene_version': 'v0003'}])

    def test_a_ladder_project_settles_its_world_on_the_ladder_even_when_delegated(self):
        from studio.decisions import delegate
        delegate(self.project, '알아서 다 해줘')
        with self.assertRaises(StudioError):   # a world beside the ladder's record is what the readers missed (2026-10-18)
            world.write(self.project, self.body(), 'one estate, afternoon, from the beat pictures')


class OffLadderTest(unittest.TestCase):
    """A project off the ladder keeps every settled layer where the ladder keeps it (decisions.write): decided in the
    user's words or by a delegated agent, read by the same tools. Why (2026-10-18): a delegated run's world went to
    world.json only, the beat reader read decisions/world.json and stopped at the first beat."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('world', {'request': 'floor noise', 'shots': [{'shot_id': 'draft', 'frame_count': 30}]}, self.tmp.name)['project_path'])
        from tests.test_story import ladder_only
        ladder_only(self.project)   # the world layer's mechanics, not direction (tests/test_direction_rules.py)
        from tests.test_story import STORY, told
        for layer, body in (('brief', BRIEF), ('facts', FACTS), ('story', STORY), ('script', told(SCRIPT))):
            out = decisions.write(self.project, layer, body, user_words='좋아요 이대로', _picked=layer == 'story')   # story: as story pick writes it
            self.assertEqual(out['state']['state'], 'decided', out)

    tearDown, picture, body = WorldLayerTest.tearDown, WorldLayerTest.picture, WorldLayerTest.body

    def test_a_delegated_world_is_read_where_every_layer_is(self):
        with self.assertRaises(StudioError):
            world.write(self.project, self.body(), 'agent choice')                   # not delegated
        from studio.decisions import delegate
        delegate(self.project, '알아서 다 해줘')
        out = world.write(self.project, self.body(), 'one estate, afternoon, from the beat pictures')
        self.assertEqual(out['state']['state'], 'decided', out)
        self.assertEqual(world.load(self.project)['decided_by'], 'agent')
        self.assertEqual(world.load(self.project)['agent_note'], 'one estate, afternoon, from the beat pictures')
        from studio.beat_pictures import _beat, _layers
        self.assertEqual(_beat(_layers(self.project)['world'], 'b1')['beat_id'], 'b1')   # the reader finds the beat
        self.assertEqual(decisions.settled(self.project, 'world')['place'], 'a Seoul apartment estate')
        self.assertFalse(decisions.adopted(self.project))
        self.assertIsNone(decisions.ladder_evidence(self.project))   # off-ladder records are no sign of a ladder
        decisions.require(self.project, 'build')                     # no gate off the ladder
        env = decisions.envelope(self.project, 'world')
        self.assertEqual((env['decided']['by'], env['decided']['delegation']), ('agent', '알아서 다 해줘'))
        self.assertIsNone(env['approval'])

    def test_a_delegated_run_reads_its_beat_pictures_by_asking_where_no_model_starts(self):
        # the run that found this (2026-10-18): Astra's sandbox could not start the reader; the studio asks, a fresh
        # agent answers, the read goes on
        import os
        from unittest import mock
        from studio import beat_pictures, outside_model
        from studio.blender_ops.show_core import METHODS
        from studio.decisions import delegate
        delegate(self.project, '알아서 다 해줘')
        world.write(self.project, self.body(), 'one estate, afternoon, from the beat pictures')
        with mock.patch.dict(os.environ, {'CODEX_SANDBOX_NETWORK_DISABLED': '1'}), mock.patch('subprocess.run') as spawn:
            with self.assertRaises(StudioError) as caught:
                beat_pictures.read(self.project, 'b1')
            self.assertEqual(caught.exception.code, 'OUTSIDE_MODEL_ASKED')
            request = caught.exception.affected_ids[0]
            outside_model.answer(request, {'pictures': [{'index': 0, 'shows': [], 'opened': [], 'how': METHODS[0], 'note': 'the estate'}]},
                                 'gpt-6-astra', 'a fresh subagent')
            out = beat_pictures.read(self.project, 'b1')
            spawn.assert_not_called()
        self.assertEqual(len(out['read']), 1)

    def test_lint_errors_keep_a_draft_and_a_changed_parent_stales_it(self):
        from studio.decisions import delegate
        delegate(self.project, '알아서 다 해줘')
        bare = self.body(elements=[element('layers', 'subject', ['labelled'], 'blender', 'blender')])
        bare.pop('reference')
        out = decisions.write(self.project, 'world', bare, agent_note='first beat only, the rest after its read')
        self.assertEqual(out['state']['state'], 'proposed')
        self.assertIsNone(decisions.settled(self.project, 'world'))
        self.assertEqual(decisions.envelope(self.project, 'world')['body']['beats'][0]['beat_id'], 'b1')   # readable as a draft
        self.assertEqual(out['materialized'], [])
        decisions.write(self.project, 'world', self.body(), agent_note='every beat read')
        self.assertEqual(decisions.state(self.project, 'world')['state'], 'decided')
        from tests.test_story import told
        changed = told({'lines': [{**SCRIPT['lines'][0], 'text': '위층에서 뛰면 쿵 소리가 아래로 납니다.'}, *SCRIPT['lines'][1:]]})
        decisions.write(self.project, 'script', changed, agent_note='the first line said more plainly')
        self.assertEqual(decisions.state(self.project, 'world')['state'], 'stale')
        decisions.write(self.project, 'world', self.body(), agent_note='every beat read, against the new script')
        self.assertEqual(decisions.state(self.project, 'world')['state'], 'decided')
        # a grandparent changed: the script goes stale and the world with it, though the script's body did not change
        decisions.write(self.project, 'facts', {**FACTS, 'sources': [*FACTS['sources'], {'source_id': 'kcs2', 'title': 'floor code 2', 'citation': 'KCS 42'}]},
                        agent_note='a second source for the same rule')
        self.assertEqual(decisions.state(self.project, 'script')['state'], 'stale')
        self.assertEqual(decisions.state(self.project, 'world')['state'], 'stale')
        self.assertIsNone(decisions.settled(self.project, 'world'))
        # and a world written against a stale script waits as a draft
        self.assertEqual(decisions.write(self.project, 'world', self.body(), agent_note='again')['state']['state'], 'proposed')

    def test_who_may_write_what(self):
        from tests.test_story import STORY
        with self.assertRaises(StudioError):   # an agent writes only under a delegation
            decisions.write(self.project, 'brief', BRIEF, agent_note='my own brief')
        from studio.decisions import delegate
        delegate(self.project, '알아서 다 해줘')
        with self.assertRaises(StudioError):   # the story is the user's pick, never the agent's
            decisions.write(self.project, 'story', STORY, agent_note='the calm one')
        for words in ('알아서 다 해줘', '좋아요 이대로'):   # a delegated run has no user to quote: not even the delegation's words
            with self.assertRaises(StudioError):
                decisions.write(self.project, 'story', STORY, user_words=words)
            with self.assertRaises(StudioError):
                decisions.write(self.project, 'brief', BRIEF, user_words=words)
        with self.assertRaises(StudioError):   # one of the two, not both, not neither
            decisions.write(self.project, 'brief', BRIEF)
        with self.assertRaises(StudioError):   # an off-ladder record is not a sheet to approve
            decisions.approve(self.project, 'brief', '좋아요 이대로', decisions.envelope(self.project, 'brief')['sheet']['rev'])
        decisions.write(self.project, 'world', self.body(), agent_note='every beat read')
        decisions.require(self.project, 'build')
        decisions.adopt(self.project)
        with self.assertRaises(StudioError):   # on the ladder: propose and approve
            decisions.write(self.project, 'brief', BRIEF, agent_note='mine')
        # and what was decided off it counts for nothing there until the user approves it (review 2026-10-18)
        self.assertEqual(decisions.state(self.project, 'world')['state'], 'proposed')
        self.assertIsNone(decisions.settled(self.project, 'world'))
        with self.assertRaises(StudioError):
            decisions.require(self.project, 'build')


if __name__ == '__main__':
    unittest.main()
