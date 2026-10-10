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
         'claims': [{'claim_id': 'layers', 'text': 'a floating floor rests on a resilient layer', 'source_ids': ['kcs']}]}
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
        for layer, body in (('brief', BRIEF), ('facts', FACTS), ('script', SCRIPT)):
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
                            'elements': ['layers', 'room'], 'beat_ids': ['b2']}]}
        bad = decisions.propose(self.project, 'shotlist', {'shots': [{**shots['shots'][0], 'elements': ['nothing']}, shots['shots'][1]]})
        self.assertIn('event: unknown world element nothing', bad['errors'])
        decisions.approve(self.project, 'shotlist', '좋아요 이 샷들로 가요', decisions.propose(self.project, 'shotlist', shots)['rev'])
        self.assertEqual(load_shot(self.project, 'event')['screen']['generate_only'], ['@background'])   # room not built yet
        linked = world.link(self.project, 'room', ['room'])                                           # its stand-in, built later
        self.assertEqual(linked['shots'], ['event', 'section'])
        event, section = load_shot(self.project, 'event'), load_shot(self.project, 'section')
        self.assertEqual((event['route']['mode'], event['route']['role']), ('hybrid', 'mood'))
        self.assertEqual(event['screen']['generate_only'], ['room', '@background'])
        self.assertEqual((section['route']['mode'], section['route']['role'], section['screen']['generate_only']), ('hybrid', 'explain', ['room']))
        self.assertEqual(section['route']['generative']['model'], 'seedance-2.5')
        from studio.concept import picked, require
        beat = picked(self.project, 'section')                # no pick of its own: the approved beat picture
        self.assertEqual((beat['concept_id'], beat['decision']['by']), ('beat:b2', 'world'))
        self.assertIsNone(picked(self.project, 'event'))      # a shot naming no beat has none
        self.assertEqual(require(self.project, 'section')['concept_id'], 'beat:b2')

    def test_a_delegated_run_writes_its_world_with_a_reason(self):
        with self.assertRaises(StudioError):
            world.write(self.project, self.body(), 'agent choice')                   # not delegated
        from studio.decisions import delegate
        delegate(self.project, '알아서 다 해줘')
        out = world.write(self.project, self.body(), 'one estate, afternoon, from the beat pictures')
        self.assertEqual(world.load(self.project)['decided_by'], 'agent')
        self.assertTrue(Path(out['world']).is_file())


if __name__ == '__main__':
    unittest.main()
