"""The decision ladder (studio/decisions.py): brief -> facts -> script -> shotlist -> look, each approved on the sheet
the user saw, bound to its parents, projected into the contracts, and gating the expensive steps."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from studio import decisions
from studio.common import StudioError, read_json, write_json
from studio.facts import check as facts_check
from studio.project import shot_path, init_project, load_project, load_shot
from tests.decisions_fixture import MOVE, STILL

BRIEF = {'topic': 'how a robot arm joint turns', 'audience': 'curious adults', 'length_s': 10, 'key_message': 'gears trade speed for force',
         'subject_mode': 'schematic'}
FACTS = {'sources': [{'source_id': 'iso', 'title': 'ISO 6336', 'citation': 'ISO 6336-1:2019'}],
         'claims': [{'claim_id': 'ratio', 'text': 'a planetary stage multiplies torque', 'source_ids': ['iso'], 'about': []}]}
STORY = {'logline': 'a small joint lifts a heavy arm', 'beats': [
    {'story_beat_id': 'open', 'function': 'hook', 'what_happens': 'the arm lifts a weight', 'tension': 0.4, 'length_share': 0.3,
     'raises': 'how does a small joint lift it?', 'on_screen': {'event': 'the arm lifts a weight', 'kind': 'payoff_first'}},
    {'story_beat_id': 'inside', 'function': 'climax', 'what_happens': 'the gears inside multiply the force', 'tension': 0.9, 'length_share': 0.7,
     'link': 'so', 'answers': ['open']}], 'thread': {'kind': 'moving', 'carrier': 'the force through the joint'}}
SCRIPT = {'lines': [{'line_id': 'l1', 'text': '로봇 팔 관절 안에는 기어가 있습니다.', 'illustrative': 'opening framing', 'story_beat_id': 'open'},
                    {'line_id': 'l2', 'text': '유성 기어는 힘을 키웁니다.', 'claim_ids': ['ratio'], 'story_beat_id': 'inside'}]}
SHOTS = {'shots': [{'shot_id': 'hero', 'purpose': 'the arm at work', 'line_ids': ['l1'], 'duration_s': 4, 'route_features': ['simple_hard_surface'],
                    'direction': {'move': MOVE, 'still': STILL}, 'frame': {'focus': ['arm'], 'size': 'medium'}},
                   {'shot_id': 'gears', 'purpose': 'inside the joint', 'line_ids': ['l2'], 'duration_s': 6, 'route_features': ['exact_motion'],
                    'direction': {'move': MOVE, 'still': STILL}, 'frame': {'focus': ['joint'], 'size': 'close'}}]}


DIRECTION_STOPS = ('HOOK_WEAK', 'KEY_CHANGE_UNPLANNED', 'KEY_CHANGE_UNSHOWN', 'DIRECTION_UNRESOLVED')


def ladder_only(project):
    """These tests are about the ladder's mechanics, not direction: the direction rules (tests/test_direction_rules.py)
    warn here instead of stopping (project.policy.gates)."""
    from studio.common import read_json, write_json
    data = read_json(Path(project) / 'project.json')
    data.setdefault('policy', {}).setdefault('gates', {}).update({c: 'warn' for c in DIRECTION_STOPS})
    write_json(Path(project) / 'project.json', data)
    from tests.decisions_fixture import allow_project_policy   # softening a blocking gate is the user's call: a test's words
    allow_project_policy(project)


class LadderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('ladder', {'request': 'robot arm', 'shots': [{'shot_id': 'draft', 'frame_count': 30}]}, self.tmp.name)['project_path'])
        ladder_only(self.project)

    def tearDown(self):
        self.tmp.cleanup()

    def settle_world(self):
        """An explanation with no world (kind none): one picture per beat, the minute seen first (studio/world.py)."""
        from PIL import Image
        from studio.concept import add_beat
        image = Path(self.tmp.name) / 'beat.png'
        Image.new('RGB', (90, 160), (120, 120, 120)).save(image)
        picture = add_beat(self.project, 'b1', image, 'the joint, cut open')['picture']
        world = {'kind': 'none', 'beats': [{'beat_id': 'b1', 'line_ids': ['l1', 'l2'], 'pictures': [picture]}],
                 'elements': [{'id': 'joint', 'function': 'subject', 'description': 'the gear joint', 'requirements': ['exact'],
                               'owner': {'shape': 'blender', 'look': 'blender'}, 'why': 'its gear ratio is a sourced claim'}]}
        return self.settle('world', world)

    def settle(self, layer, body, words='좋아요 이대로 가요'):
        rev = decisions.propose(self.project, layer, body)['rev']
        return decisions.approve(self.project, layer, words, rev)

    def test_a_project_without_the_ladder_is_gated_until_its_layers_are_settled(self):
        """2026-10-21: with no ladder.json every gate was open - a run that skipped facts, script and world still built,
        rendered and delivered. Closed by default: each layer the operation stands on must exist and be fresh."""
        with self.assertRaises(StudioError) as caught:
            decisions.require(self.project, 'build')
        self.assertEqual(caught.exception.code, 'DECISION_MISSING')
        self.assertFalse(decisions.adopted(self.project))
        from tests.decisions_fixture import settle
        settle(self.project, ['world', 'shotlist'])
        decisions.require(self.project, 'build')
        world = decisions.envelope(self.project, 'world')
        world['body']['changed'] = True   # a hand edit after it was decided
        __import__('studio.common', fromlist=['write_json']).write_json(Path(self.project) / 'decisions/world.json', world)
        with self.assertRaises(StudioError) as caught:
            decisions.require(self.project, 'build')
        self.assertEqual(caught.exception.code, 'DECISION_STALE')

    def test_approve_only_the_latest_sheet_in_the_users_words(self):
        first = decisions.propose(self.project, 'brief', BRIEF)
        self.assertTrue(decisions.adopted(self.project) and Path(first['sheet']).is_file())
        with self.assertRaises(StudioError) as caught:
            decisions.require(self.project, 'build')
        self.assertEqual(caught.exception.code, 'DECISION_UNAPPROVED')
        second = decisions.revise(self.project, 'brief', '30초 말고 10초로 해줘', ops=[{'op': 'set', 'path': '/length_s', 'value': 10}])
        with self.assertRaises(StudioError) as caught:
            decisions.approve(self.project, 'brief', '좋아요 진행해요', first['rev'])        # an older sheet
        self.assertEqual(caught.exception.code, 'DECISION_STALE')
        with self.assertRaises(StudioError):
            decisions.approve(self.project, 'brief', 'User: approved', second['rev'])       # an agent's wrapper
        decisions.approve(self.project, 'brief', '좋아요 진행해요', second['rev'])
        project = load_project(self.project)
        self.assertEqual((project['brief']['key_message'], project['output']['target_seconds']), (BRIEF['key_message'], 10))
        with self.assertRaises(StudioError):                                               # a path outside the contract
            decisions.revise(self.project, 'brief', '청중 바꿔줘', ops=[{'op': 'set', 'path': '/budget', 'value': 5}])

    def test_the_ladder_projects_shots_and_gates_until_every_layer_is_settled(self):
        self.settle('brief', BRIEF); self.settle('facts', FACTS); self.settle('story', STORY); self.settle('script', SCRIPT); self.settle_world()
        result = self.settle('shotlist', SHOTS)
        self.assertEqual(result['materialized']['shots'], ['hero', 'gears'])
        self.assertEqual(result['materialized']['removed_from_timeline'], ['draft'])
        gears = load_shot(self.project, 'gears')
        self.assertEqual((gears['duration_frames'], gears['narration']['sentence_claims']), (180, [{'claim_ids': ['ratio']}]))
        self.assertEqual(facts_check(self.project), [])
        decisions.require(self.project, 'build')
        with self.assertRaises(StudioError) as caught:
            decisions.require(self.project, 'render_look')                                  # the look is not settled
        self.assertEqual(caught.exception.code, 'DECISION_UNAPPROVED')
        self.settle('look', {'preset': 'photoreal_product'})
        self.assertEqual(load_shot(self.project, 'hero')['render']['look_preset'], 'photoreal_product')
        decisions.require(self.project, 'render_look')

        decisions.revise(self.project, 'facts', '출처 하나 더 넣어줘',                         # a parent changes ...
                         ops=[{'op': 'add', 'path': '/sources/-', 'value': {'source_id': 'kss', 'title': 'KS B ISO 6336'}}])
        state = decisions.state(self.project, 'shotlist')
        self.assertEqual(state['state'], 'stale'); self.assertIn('script', state['reason'])  # ... every layer below is stale
        with self.assertRaises(StudioError) as caught:
            decisions.require(self.project, 'build')
        self.assertEqual(caught.exception.code, 'DECISION_STALE')

    def test_a_shot_list_the_shots_cannot_take_is_not_approved(self):
        """2026-10-12: an approval was recorded, then its projection stopped at a shot the schema refused - some shots
        rewritten, the timeline not. Every shot is built and checked before the approval is written."""
        from unittest import mock
        self.settle('brief', BRIEF); self.settle('facts', FACTS); self.settle('story', STORY); self.settle('script', SCRIPT); self.settle_world()
        rev = decisions.propose(self.project, 'shotlist', SHOTS)['rev']
        real = decisions.validate_shot
        def refuse_gears(shot):
            if shot['shot_id'] == 'gears':
                raise StudioError('INPUT_INVALID', "['direction']: Additional properties are not allowed")
            return real(shot)
        with mock.patch.object(decisions, 'validate_shot', refuse_gears), self.assertRaises(StudioError):
            decisions.approve(self.project, 'shotlist', '좋아요 이대로 가요', rev)
        self.assertNotEqual(decisions.state(self.project, 'shotlist')['state'], 'approved')
        self.assertFalse(shot_path(self.project, 'hero').is_file())                         # nothing projected either
        self.assertEqual([s['shot_id'] for s in load_project(self.project)['shots']], ['draft'])
        decisions.approve(self.project, 'shotlist', '좋아요 이대로 가요', rev)                  # the same sheet, once it can take it

    def test_an_opening_is_not_cut_short_and_the_voice_goes_on(self):
        """2026-10-20, measured on the study set: openings run about their reel's median shot (p25 0.71) with the voice
        going on - no silence is asked (the user's call of 2026-10-20: decide it by the study set)."""
        shots = [{'shot_id': x, 'line_ids': [], 'duration_s': 2.5} for x in 'abc'] + \
                [{'shot_id': 'd', 'line_ids': [], 'duration_s': 1.5, 'direction': {'reveal': 'section_cut'}}]
        found = decisions.pacing_lines(shots)
        self.assertEqual(len(found), 1); self.assertTrue(found[0].startswith('OPENING_SHORT: d '))
        shots[3]['duration_s'] = 1.8   # 0.72 of the 2.5 s median: as the study set's short openings
        self.assertEqual(decisions.pacing_lines(shots), [])

    def test_a_script_said_in_pieces(self):
        pieces = ['1단은 로켓 값의 대부분이라,', '세워서 내려야 합니다.', '위는 산화제, 아래는 연료.', '마지막엔 가운데 엔진 하나.']
        whole = ['1단은 로켓 값의 대부분이라서 버리지 않고 다시 쓰려면 세워서 내려야 합니다.', '속을 열어 보면 위에는 산화제가, 아래에는 연료가 들어 있습니다.',
                 '마지막 순간에는 가운데 엔진 하나만 다시 켜서 속도를 줄입니다.']
        with mock.patch.object(decisions, '_reference_speech', return_value={}):
            self.assertTrue(decisions.choppy_lines(pieces)[0].startswith('SCRIPT_CHOPPY'))
            self.assertEqual(decisions.choppy_lines(whole), [])
        with mock.patch.object(decisions, '_reference_speech', return_value={'chars_per_sentence': {'p10': 10}}):
            self.assertEqual(decisions.choppy_lines(pieces), [])   # a learned reference sets its own floor

    def test_line_starts_reach_the_shot(self):
        self.settle('brief', BRIEF); self.settle('facts', FACTS); self.settle('story', STORY); self.settle('script', SCRIPT); self.settle_world()
        placed = {'shots': [dict(SHOTS['shots'][0], line_ids=['l1', 'l2'], duration_s=10, line_at_s=[0.0, 5.5])]}
        self.settle('shotlist', placed)
        segments = load_shot(self.project, 'hero')['narration']['segments']
        self.assertEqual([s['at_s'] for s in segments], [0.0, 5.5])
        bad = {'shots': [dict(SHOTS['shots'][0], line_ids=['l1', 'l2'], duration_s=10, line_at_s=[5.5, 0.0])]}
        rev = decisions.revise(self.project, 'shotlist', '줄 순서 바꿔', body=bad)['rev']
        with self.assertRaises(StudioError):
            decisions.approve(self.project, 'shotlist', '좋아요 이대로 가요', rev)

    def test_sentence_links_follow_the_sentences_of_the_joined_narration(self):
        """2026-10-12 audit: a line without a final mark runs into the next when a shot joins them; one link per line's
        own sentences left the counts unequal (FACTS_MISALIGNED at candidate/deliver)."""
        script = {'lines': [{'line_id': 'l1', 'text': '로봇 팔 관절 안에는 기어가 있고', 'illustrative': 'opening framing', 'story_beat_id': 'open'},
                            {'line_id': 'l2', 'text': '유성 기어는 힘을 키웁니다.', 'claim_ids': ['ratio'], 'story_beat_id': 'inside'}]}
        self.settle('brief', BRIEF); self.settle('facts', FACTS); self.settle('story', STORY); self.settle('script', script); self.settle_world()
        self.settle('shotlist', {'shots': [dict(SHOTS['shots'][0], line_ids=['l1', 'l2'], duration_s=10)]})
        hero = load_shot(self.project, 'hero')
        from studio.facts import sentences
        self.assertEqual(len(hero['narration']['sentence_claims']), len(sentences(hero['narration']['text'])))
        self.assertEqual(hero['narration']['sentence_claims'], [{'claim_ids': ['ratio']}])   # the sentence rests on l2's claim
        self.assertEqual([p for p in facts_check(self.project) if p.get('shot_id') == 'hero'], [])

    def test_an_approval_is_not_recorded_while_another_holds_the_project(self):
        import fcntl
        self.settle('brief', BRIEF); self.settle('facts', FACTS); self.settle('story', STORY); self.settle('script', SCRIPT); self.settle_world()
        rev = decisions.propose(self.project, 'shotlist', SHOTS)['rev']
        with open(self.project / '.project.lock', 'a') as other:   # another process's hold (a separate open file)
            fcntl.flock(other, fcntl.LOCK_EX)
            with self.assertRaises(StudioError) as caught:
                decisions.approve(self.project, 'shotlist', '좋아요 이대로 가요', rev)
            fcntl.flock(other, fcntl.LOCK_UN)
        self.assertEqual(caught.exception.code, 'BUSY')
        self.assertNotEqual(decisions.state(self.project, 'shotlist')['state'], 'approved')
        decisions.approve(self.project, 'shotlist', '좋아요 이대로 가요', rev)

    def test_drift_sees_a_changed_length(self):
        self.settle('brief', BRIEF); self.settle('facts', FACTS); self.settle('story', STORY); self.settle('script', SCRIPT); self.settle_world(); self.settle('shotlist', SHOTS)
        self.assertEqual(decisions.drift(self.project, 'shotlist'), [])
        project = load_project(self.project)
        project['shots'][0]['frame_count'] += 30
        write_json(self.project / 'project.json', project)
        self.assertTrue(decisions.drift(self.project, 'shotlist'))

    def test_a_shot_with_no_lines_and_no_line_starts(self):
        body = {'shots': [dict(SHOTS['shots'][0], line_ids=[], line_at_s=[])]}
        decisions.lint(self.project, 'shotlist', body)   # no IndexError
        # 2026-10-12 re-audit: an empty list for a shot that has lines is still an error, not 'nothing given'
        self.settle('brief', BRIEF); self.settle('facts', FACTS); self.settle('story', STORY); self.settle('script', SCRIPT); self.settle_world()
        empty = {'shots': [dict(SHOTS['shots'][0], line_ids=['l1', 'l2'], duration_s=10, line_at_s=[])]}
        self.assertTrue(any('line_at_s' in e for e in decisions.lint(self.project, 'shotlist', empty)['errors']))

    def test_an_empty_direction_is_no_direction(self):
        """2026-10-12 re-audit: materialize leaves out an empty direction; drift compared {} with nothing and never settled."""
        self.settle('brief', BRIEF); self.settle('facts', FACTS); self.settle('story', STORY); self.settle('script', SCRIPT); self.settle_world()
        self.settle('shotlist', {'shots': [dict(s, direction={'move': MOVE, 'still': STILL}) for s in SHOTS['shots']]})
        self.assertEqual(decisions.drift(self.project, 'shotlist'), [])

    def test_finals_and_generation_wait_for_the_rough_cut(self):
        """The third checkpoint (2026-10-09): the rough cut, watched whole, binds the scene versions finals render."""
        self.settle('brief', BRIEF); self.settle('facts', FACTS); self.settle('story', STORY); self.settle('script', SCRIPT); self.settle_world(); self.settle('shotlist', SHOTS)
        self.settle('look', {'preset': 'photoreal_product'})
        decisions.require(self.project, 'render_look')
        decisions.require(self.project, 'generate')                                         # a generative shot's take is its rough
        for operation in ('render_final', 'generate_hybrid'):
            with self.assertRaises(StudioError) as caught:
                decisions.require(self.project, operation)
            self.assertEqual(caught.exception.code, 'DECISION_UNAPPROVED')
        versions = {}
        for shot_id in ('hero', 'gears'):
            shot = load_shot(self.project, shot_id); shot['scene_version'] = 'v0001'; write_json(shot_path(self.project, shot_id), shot)
            versions[shot_id] = 'v0001'
        missing = decisions.propose(self.project, 'cut', {'candidate_id': 'rough_x', 'versions': versions})
        self.assertTrue(any('no rough cut' in e for e in missing['errors']))
        folder = self.project / 'final' / 'rough_x'; folder.mkdir(parents=True)
        write_json(folder / 'manifest.json', {'candidate_id': 'rough_x'})
        # the shape edit build writes: each row keeps the shot's snapshot, its version inside
        write_json(folder / 'edit.snapshot.json', {'shots': [{'shot_id': k, 'shot_snapshot': {'scene_version': v}} for k, v in versions.items()]})
        wrong = decisions.propose(self.project, 'cut', {'candidate_id': 'rough_x', 'versions': {'hero': 'v0009', 'gears': 'v0001'}})
        self.assertTrue(any('the cut has v0001' in e for e in wrong['errors']))
        rev = decisions.propose(self.project, 'cut', {'candidate_id': 'rough_x'})['rev']     # versions read from the cut itself
        self.assertEqual(decisions.envelope(self.project, 'cut')['body']['versions'], versions)
        decisions.approve(self.project, 'cut', '이 러프로 갑시다', rev)
        decisions.require(self.project, 'render_final')
        shot = load_shot(self.project, 'gears'); shot['scene_version'] = 'v0002'; write_json(shot_path(self.project, 'gears'), shot)
        with self.assertRaises(StudioError) as caught:                                  # the scene moved on after the cut was seen
            decisions.require(self.project, 'render_final')
        self.assertEqual(caught.exception.code, 'DECISION_DRIFT')

    def test_a_ladder_keeps_the_rules_it_was_adopted_with(self):
        """A project on the ladder before the cut layer existed does not gain it mid-run (concept frames: every project, 2026-10-21)."""
        self.settle('brief', BRIEF); self.settle('facts', FACTS); self.settle('story', STORY); self.settle('script', SCRIPT); self.settle_world(); self.settle('shotlist', SHOTS)
        self.settle('look', {'preset': 'photoreal_product'})
        ladder = self.project / 'decisions' / 'ladder.json'
        old = read_json(ladder); old['layers'] = ['brief', 'facts', 'script', 'shotlist', 'look']; old.pop('requires')
        write_json(ladder, old)
        decisions.require(self.project, 'render_final')                                   # no cut asked of an older ladder

    def test_a_shots_role_and_departures_reach_the_shot_and_older_role_names_still_revise(self):
        """The shot list's role is the shot's job on screen: it chooses the pattern section (patterns.py)."""
        self.settle('brief', BRIEF); self.settle('facts', FACTS); self.settle('story', STORY); self.settle('script', SCRIPT); self.settle_world()
        shots = {'shots': [{**SHOTS['shots'][0], 'role': 'hook', 'pattern': {
                     'carried': [{'item': 'height_share', 'value': 0.6, 'why': 'arm large in a square frame'}],
                     'departs': [{'item': 'camera_speed', 'why': 'a still opening on purpose'}]},
                            'direction': {'camera': 'low over the floor, pushing in', 'action': 'the arm swings up into frame', 'scale_step': 'first',
                                          'move': MOVE, 'still': STILL}},
                           {**SHOTS['shots'][1], 'role': 'detail'}]}
        rev = decisions.propose(self.project, 'shotlist', shots)
        self.assertIn('[hook]', Path(rev['sheet']).read_text())
        self.assertIn('takes height_share = 0.6', Path(rev['sheet']).read_text())
        self.assertFalse([w for w in rev['warnings'] if w.startswith('PATTERN_UNREAD: hero')])
        self.assertTrue([w for w in rev['warnings'] if w.startswith('PATTERN_UNREAD: gears')])   # this ladder asks for it
        revised = decisions.revise(self.project, 'shotlist', '두번째 샷 6초 유지', ops=[{'op': 'set', 'path': '/shots/1/duration_s', 'value': 6}])
        decisions.approve(self.project, 'shotlist', '좋아요 이대로', revised['rev'])   # 'detail' (an older name) revised fine
        hero, gears = load_shot(self.project, 'hero'), load_shot(self.project, 'gears')
        self.assertEqual((hero['screen']['role'], gears['screen']['role']), ('hook', 'explain'))
        self.assertEqual(hero['screen']['pattern_departs'][0]['item'], 'camera_speed')
        self.assertEqual(hero['direction']['camera'], 'low over the floor, pushing in')   # what the build is made from
        self.assertEqual(gears['direction'], {'move': MOVE, 'still': STILL})   # its motion, and no camera words it was not given
        revised = decisions.revise(self.project, 'shotlist', '첫 샷 기록 지워', ops=[{'op': 'set', 'path': '/shots/0/pattern', 'value': {}}])
        decisions.approve(self.project, 'shotlist', '좋아요 이대로', revised['rev'])   # what a later list takes out leaves the shot
        self.assertFalse({'pattern_carried', 'pattern_departs'} & set(load_shot(self.project, 'hero')['screen']))

    def test_one_record_pasted_on_every_shot_is_flagged(self):
        self.settle('brief', BRIEF); self.settle('facts', FACTS); self.settle('story', STORY); self.settle('script', SCRIPT); self.settle_world()
        same = {'carried': [{'item': 'depth_layers', 'value': 3, 'why': 'foreground, event and room kept apart'}]}
        rev = decisions.propose(self.project, 'shotlist', {'shots': [{**s, 'role': 'explain', 'pattern': same} for s in SHOTS['shots']]})
        self.assertTrue([w for w in rev['warnings'] if w.startswith('PATTERN_RECORD_REPEATED')])
        other = {'carried': [{'item': 'camera_move', 'value': 'static', 'why': 'held while the gears explain'}]}
        rev = decisions.revise(self.project, 'shotlist', '두번째 샷 기록 따로', ops=[{'op': 'set', 'path': '/shots/1/pattern', 'value': other}])
        self.assertFalse([w for w in rev['warnings'] if w.startswith('PATTERN_RECORD_REPEATED')])

    def test_an_older_ladder_is_not_asked_for_pattern_records(self):
        self.settle('brief', BRIEF); self.settle('facts', FACTS); self.settle('story', STORY); self.settle('script', SCRIPT); self.settle_world()
        ladder = self.project / 'decisions' / 'ladder.json'
        old = read_json(ladder); old['requires'] = ['concept_frames']; write_json(ladder, old)
        rev = decisions.propose(self.project, 'shotlist', {'shots': [{**s, 'role': 'explain'} for s in SHOTS['shots']]})
        self.assertFalse([w for w in rev['warnings'] if w.startswith('PATTERN_')])

    def test_a_fill_brief_is_bound_to_the_shot_list_it_fills(self):
        from studio.fill import approve as fill_approve, propose as fill_propose, require_approved
        self.settle('brief', BRIEF); self.settle('facts', FACTS); self.settle('story', STORY); self.settle('script', SCRIPT); self.settle_world(); self.settle('shotlist', SHOTS)
        brief = {'status': 'proposed', 'topic': 'gears', 'request_trace': [], 'levels': [{'level_id': 'L1', 'items': [
            {'item_id': 'g', 'role': 'subject', 'element': 'winch', 'layout': 'cluster', 'count': 1, 'why': 'the gears', 'source': 'agent'}]}]}
        draft = self.project / 'fill.json'; write_json(draft, brief)
        fill_propose(self.project, 'gears', draft); fill_approve(self.project, 'gears', '좋아요 이대로')
        require_approved(load_shot(self.project, 'gears'), self.project)
        decisions.revise(self.project, 'shotlist', '두 번째 컷을 7초로', ops=[{'op': 'set', 'path': '/shots/1/duration_s', 'value': 7}])
        with self.assertRaises(StudioError) as caught:
            require_approved(load_shot(self.project, 'gears'), self.project)
        self.assertEqual(caught.exception.code, 'FILL_BRIEF_STALE')

    def test_a_hand_edit_after_approval_is_drift(self):
        self.settle('brief', BRIEF); self.settle('facts', FACTS)
        project = read_json(self.project / 'project.json'); project['brief']['key_message'] = 'something else'
        write_json(self.project / 'project.json', project)
        self.assertEqual(decisions.drift(self.project, 'brief'), ['project.brief.key_message'])

    def test_deleting_the_ladder_does_not_turn_the_gates_off(self):
        import shutil
        decisions.propose(self.project, 'brief', BRIEF)
        self.assertIn('decision_ladder', load_project(self.project))
        (self.project / 'decisions' / 'ladder.json').unlink()
        with self.assertRaises(StudioError) as caught:
            decisions.require(self.project, 'build')
        self.assertEqual(caught.exception.code, 'DECISION_DRIFT')
        shutil.rmtree(self.project / 'decisions')     # the whole folder: project.json still remembers
        with self.assertRaises(StudioError) as caught:
            decisions.require(self.project, 'render')
        self.assertEqual(caught.exception.code, 'DECISION_DRIFT')
        self.assertIn('ladder_missing', decisions.status(self.project))

    def test_lint_refuses_numbers_said_without_a_source(self):
        self.settle('brief', {**BRIEF, 'subject_mode': 'specific_real'})
        report = decisions.propose(self.project, 'facts', {**FACTS, 'claims': [{'claim_id': 'r', 'text': 'ratio 4.5', 'source_ids': ['iso'], 'about': []}]})
        self.assertTrue(any('2 independent sources' in e for e in report['errors']))
        self.settle('facts', FACTS); self.settle('story', STORY)
        report = decisions.propose(self.project, 'script', {'lines': [{'line_id': 'l1', 'text': '기어비는 4.5입니다.', 'illustrative': 'x y z', 'story_beat_id': 'open'}]})
        self.assertTrue(any('number said aloud' in e for e in report['errors']))
        with self.assertRaises(StudioError) as caught:
            decisions.approve(self.project, 'script', '좋아요 진행', report['rev'])
        self.assertEqual(caught.exception.code, 'DECISION_INVALID')

    def test_adopt_drafts_every_layer_from_an_existing_project(self):
        from studio.project import from_example
        target = Path(self.tmp.name) / 'samsung'
        from_example('samsung_cutaway', target)
        adopted = decisions.adopt(target)
        self.assertEqual(adopted['adopted'], ['brief', 'facts', 'story', 'script', 'shotlist'])
        self.assertEqual({r['layer']: r['state'] for r in decisions.status(target)['layers']}['shotlist'], 'proposed')



class DecisionEditPathTest(unittest.TestCase):
    def test_bad_path_is_a_studio_error(self):
        from studio.common import StudioError
        from studio.decisions import apply_ops
        with self.assertRaises(StudioError) as caught:
            apply_ops({'topic': 'x'}, [{'op': 'set', 'path': '/beats/0/text', 'value': 'y'}])
        self.assertEqual(caught.exception.code, 'INPUT_INVALID')


if __name__ == '__main__':
    unittest.main()
