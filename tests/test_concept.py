"""Concept frames without Blender: provenance, the pick's authority (user words, or the agent's note only when delegated),
staleness, and the cut's hero frame critiqued against the concept in qa (2026-10-09)."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from studio import concept
from studio.common import StudioError
from studio.decisions import delegate
from studio.project import init_project
from studio.qa import _concept_critique


class ConceptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.p = Path(init_project('c', {'request': 'concept test', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, self.root)['project_path'])
        self.image = self.root / 'gen.png'
        Image.new('RGB', (90, 160), (30, 60, 120)).save(self.image)

    def tearDown(self):
        self.tmp.cleanup()

    def test_not_required_until_ladder_or_delegation(self):
        self.assertIsNone(concept.require(self.p, 's'))
        delegate(self.p, '이번 실행은 에이전트에게 맡긴다', [])
        with self.assertRaises(StudioError) as caught:
            concept.require(self.p, 's')
        self.assertEqual(caught.exception.code, 'CONCEPT_UNPICKED')

    def test_the_agent_picks_only_in_a_delegated_run(self):
        concept.add(self.p, 's', self.image, prompt='x')
        with self.assertRaises(StudioError):
            concept.pick(self.p, 's', 'c01', agent_note='looks right')          # not delegated: the user's words only
        picked = concept.pick(self.p, 's', 'c01', user_words='이 그림으로 가자')
        self.assertEqual(picked['decision']['by'], 'user')

    def test_sheet_with_multiple_shots_keeps_numeric_row_spacing_after_captioning(self):
        project = Path(init_project('sheet', {'request': 'sheet test', 'shots': [
            {'shot_id': 's01', 'frame_count': 30}, {'shot_id': 's02', 'frame_count': 30}]}, self.root)['project_path'])
        delegate(project, '이번 실행은 에이전트에게 맡긴다', [])
        for shot in ('s01', 's02'):
            concept.add(project, shot, self.image, prompt=f'picture for {shot}')
        concept.pick(project, 's01', 'c01', agent_note='first shot concept')
        result = concept.sheet(project)
        with Image.open(result['sheet']) as sheet:
            self.assertEqual(sheet.height, 12 + 2 * (concept.THUMB[1] + 28 + 12))
        self.assertEqual(result['shots']['s01']['picked'], 'c01')
        self.assertIsNone(result['shots']['s02']['picked'])
        text = Path(result['sheet']).with_suffix('.md').read_text()
        self.assertIn('## s01: picked c01', text)
        self.assertIn('## s02: not picked', text)

    def test_a_moments_brief_says_its_own_row_and_names_parts_by_what_they_are(self):
        """E2E 2026-10-21: the start brief named the stone by the end row's why, and the start was drawn as the end."""
        from studio.common import write_json
        from studio.project import load_shot, shot_path
        write_json(self.p / 'world.json', {'elements': [{'id': 'stone', 'description': 'a fist-sized grey stone'}]})
        shot = load_shot(self.p, 's')
        shot['show'] = [{'at': 'start', 'parts': ['stone'], 'how': 'exterior', 'why': 'the stone enters at the top'},
                        {'at': 'end', 'parts': ['stone'], 'how': 'exterior', 'why': 'the stone has dropped below the keel'}]
        shot['screen'] = {'details': [{'id': 'stone', 'min_px': 32, 'why': 'the stone has dropped below the keel'}]}
        write_json(shot_path(self.p, 's'), shot)
        start = concept.brief(self.p, 's', 'start')['prompt']
        self.assertIn('stone (a fist-sized grey stone) - because the stone enters at the top', start)
        self.assertNotIn('dropped below the keel', start)
        shot['show'] = [{'at': 0.4, 'parts': ['stone'], 'how': 'cutaway', 'why': 'the stone opened', 'key': True}]
        write_json(shot_path(self.p, 's'), shot)   # before its first row a shot is seen from outside, and the brief says so
        self.assertIn('comes before the shot shows it cutaway (at 0.40)', concept.brief(self.p, 's', 'start')['prompt'])
        write_json(self.p / 'world.json', {'place': 'Open sea; no sea floor in sight.', 'elements': []})
        shot['frame'] = {'focus': ['stone'], 'size': 'wide'}
        write_json(shot_path(self.p, 's'), shot)   # the list's framing and the place's words hold over attached pictures
        prompt = concept.brief(self.p, 's', 'start')['prompt']
        self.assertIn('Frame: a wide shot: the subject small, the place around it large, stone in focus', prompt)
        self.assertIn('The place: Open sea; no sea floor in sight. Where an attached picture shows otherwise', prompt)
        self.assertIn('no arrows', prompt)

    def test_variants_of_a_moment_differ_and_a_users_pick_among_them_is_taste(self):
        from unittest import mock
        from studio import patterns, taste_picks
        for module in (patterns, taste_picks):
            patcher = mock.patch.object(module, 'TASTE', self.root / 'taste.json')
            patcher.start(); self.addCleanup(patcher.stop)
        made = concept.brief(self.p, 's', 'start', variants=3)
        drawn = [v['axes'] for v in made['variants']]
        self.assertEqual(len({tuple(sorted(a.items())) for a in drawn}), 3)
        self.assertTrue(all(v['line'] in v['prompt'] and v['prompt'].endswith('no arrows or diagram marks in the picture.') for v in made['variants']))
        for axes in drawn:
            concept.add(self.p, 's', self.image, prompt='x', frame='start', extra={'variant': {'axes': axes}})
        self.assertTrue(concept.concepts_one_idea(self.p, 's')[0].startswith('CONCEPTS_UNREAD'))   # the sheet: not read yet
        looks = {'c01': ('wide', 'eye'), 'c02': ('close', 'low'), 'c03': ('medium', 'high')}
        reads = []
        def reader(path, shot_id, pictures):
            reads.append([p['concept_id'] for p in pictures])
            return {p['concept_id']: {'subject_box': [0, 0, 1, {'wide': 0.3, 'medium': 0.7, 'close': 0.95}[looks[p['concept_id']][0]]],
                                      'view_height': looks[p['concept_id']][1], 'side': 'left'} for p in pictures}
        self.assertEqual(concept.check(self.p, 's', reader)['warnings'], [])   # read: three different pictures
        looks['c04'] = looks['c01']
        concept.add(self.p, 's', self.image, prompt='x', frame='start', extra={'variant': {'axes': drawn[0]}})
        found = concept.check(self.p, 's', reader)['warnings']
        self.assertTrue(found[0].startswith('CONCEPTS_ONE_IDEA') and '--redraw-of c04' in found[0])
        self.assertEqual(reads[-1], ['c04'])   # only the new picture is read again
        self.assertEqual(concept.frames(self.p, 's'), [])   # variants are candidates until one is picked
        concept.pick(self.p, 's', 'c02', user_words='두 번째 각도가 시원하네요')
        self.assertEqual([c['concept_id'] for c in concept.frames(self.p, 's')], ['c02'])
        row = taste_picks.picks('concept')[0]
        self.assertEqual((row['chosen'], len(row['rejected'])), (drawn[1], 3))
        again = concept.brief(self.p, 's', 'start', variants=3)['variants']
        self.assertNotEqual(again[2]['axes'], made['variants'][2]['axes'])   # the explore one goes where this moment has not been
        with self.assertRaises(StudioError):
            concept.variant_axes('size=huge')

    def test_one_redraw_a_set_then_pick(self):
        made = concept.brief(self.p, 's', 'end', variants=3)
        for v in made['variants']:
            concept.add(self.p, 's', self.image, prompt='x', frame='end', extra={'variant': {'axes': v['axes']}})
        axes = made['variants'][2]['axes']
        concept.add(self.p, 's', self.image, prompt='x', frame='end', extra={'variant': {'axes': axes}}, redraw_of='c03')
        current = [c['concept_id'] for c in concept.current_variants(self.p, 's', 1.0)]
        self.assertEqual(current, ['c01', 'c02', 'c04'])   # the redrawn one leaves the set
        with self.assertRaises(StudioError):   # one redraw a set
            concept.add(self.p, 's', self.image, prompt='x', frame='end', extra={'variant': {'axes': axes}}, redraw_of='c02')
        again = concept.brief(self.p, 's', 'end', variants=3)   # a new draw is a new set
        concept.add(self.p, 's', self.image, prompt='x', frame='end', extra={'variant': {'axes': again['variants'][0]['axes']}})
        self.assertEqual([c['concept_id'] for c in concept.current_variants(self.p, 's', 1.0)], ['c05'])

    def test_a_pick_is_per_moment_and_the_latest_decision_wins(self):
        concept.add(self.p, 's', self.image, prompt='old start', frame='start')                                   # c01
        concept.add(self.p, 's', self.image, prompt='end', frame='end')                                           # c02
        concept.add(self.p, 's', self.image, prompt='v', frame='start', extra={'variant': {'axes': {'size': 'wide'}}})   # c03
        concept.pick(self.p, 's', 'c03', user_words='넓은 쪽이 좋아요')
        self.assertEqual([c['concept_id'] for c in concept.frames(self.p, 's')], ['c03', 'c02'])
        concept.pick(self.p, 's', 'c01', user_words='역시 처음 그림이 낫네요')   # an older picture picked after the variant
        self.assertEqual([c['concept_id'] for c in concept.frames(self.p, 's')], ['c01', 'c02'])
        concept.pick(self.p, 's', 'c02', user_words='끝 그림도 이걸로 할게요')   # the end pick keeps the start pick
        record = concept.picked(self.p, 's')
        self.assertEqual((record['concept_id'], sorted(record['moments'])), ('c02', ['0.0000', '1.0000']))
        self.assertEqual([c['concept_id'] for c in concept.frames(self.p, 's')], ['c01', 'c02'])
        concept.add(self.p, 's', self.image, prompt='painted over 3d', frame='start')   # c04: a newer picture supersedes
        self.assertEqual([c['concept_id'] for c in concept.frames(self.p, 's')], ['c04', 'c02'])

    def test_examples_show_the_edges_too_and_rotate_per_project(self):
        from studio import patterns
        items = {'len_share': {'median': 0.1, 'p10': 0.05, 'p25': 0.08, 'p75': 0.12, 'p90': 0.2},
                 'height_share': {'median': 0.6, 'p10': 0.3, 'p25': 0.5, 'p75': 0.7, 'p90': 0.9}}
        rows = [{'group': f'g{i}', 'note': 'n', 'strip': f's{i}', 'len_share': 0.1, 'height_share': h}
                for i, h in enumerate([0.6, 0.62, 0.58, 0.2, 0.95, 0.61, 0.25])]
        picked = patterns.exemplars(rows, items)
        self.assertEqual(sorted(e['kind'] for e in picked), ['edge'] * 3 + ['typical'] * 3)
        shown = [[e['strip'] for e in patterns.rotate(picked, name)] for name in ('a', 'b', 'c', 'd')]
        self.assertTrue(all(len(s) == patterns.SHOWN_EXAMPLES for s in shown) and len({tuple(s) for s in shown}) > 1)
        self.assertTrue(all(any(e.get('kind') == 'edge' for e in patterns.rotate(picked, n)) for n in 'abcd'))

    def test_example_kind_survives_the_local_file_and_show(self):
        """The edge/typical kind is written to the local examples file and read back by show, so every project's
        rotation keeps one example from the edge of the range."""
        from unittest import mock
        from studio import patterns
        library = patterns.LIBRARY / 'screen_grammar_v1.json'
        if not library.is_file():
            self.skipTest('no pattern library on this machine')
        strips = []
        for i in range(4):
            strip = self.root / f'strip{i}.jpg'
            strip.write_bytes(b'x'); strips.append(str(strip))
        examples = self.root / 'exemplars.json'
        rows = [{'strip': s, 'note': 'n', 'len_share': 0.1, 'height_share': 0.6, 'kind': 'typical'} for s in strips[:3]]
        rows.append({'strip': strips[3], 'note': 'n', 'len_share': 0.4, 'height_share': 0.98, 'kind': 'edge'})
        examples.write_text(json.dumps({'hook': rows}))
        local = self.root / 'local'
        local.mkdir()
        (local / 'screen_grammar_v1.local.json').write_text(json.dumps({'exemplars': str(examples)}))
        with mock.patch.object(patterns, 'LOCAL', local):
            for project in ('a', 'b', 'c'):
                shown = patterns.show('hook', name='screen_grammar_v1', project=project)['examples']
                self.assertTrue(any(e.get('kind') == 'edge' for e in shown), project)

    def test_qa_critiques_the_hero_frame_against_the_concept(self):
        delegate(self.p, '이번 실행은 에이전트에게 맡긴다', [])
        concept.add(self.p, 's', self.image)
        concept.pick(self.p, 's', 'c01', agent_note='closest to the brief')
        video = self.root / 'cut.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=0xC07020:s=90x160:r=30', '-frames:v', '30', str(video)], check=True)
        rows = _concept_critique(self.p, video, [{'shot_id': 's', 'start_frame': 0, 'frame_count': 30}], self.root / 'out')
        self.assertEqual((rows[0]['shot_id'], rows[0]['concept_id'], rows[0]['frame']), ('s', 'c01', 15))
        self.assertGreater(rows[0]['score'], 0)                                   # orange cut against a blue concept
        self.assertTrue(rows[0]['top'])
        # 2026-10-18: once the shot has frame pictures (a redo, a moment pick), they are what it is built to - the
        # single picked concept is not what qa, the workbench critique or the grade compare with any more
        for frame, colour in (('start', (200, 40, 40)), ('end', (40, 200, 40))):
            Image.new('RGB', (90, 160), colour).save(self.root / f'{frame}.png')
            concept.add(self.p, 's', self.root / f'{frame}.png', prompt=frame, frame=frame)
        chosen, local = concept.target(self.p, 's')
        self.assertEqual((chosen['concept_id'], local), ('c03', 29))            # nearest moment to the middle, at its frame
        self.assertEqual(concept.target(self.p, 's', 2)[0]['concept_id'], 'c02')
        rows = _concept_critique(self.p, video, [{'shot_id': 's', 'start_frame': 0, 'frame_count': 30}], self.root / 'out')
        self.assertEqual((rows[0]['concept_id'], rows[0]['frame']), ('c03', 29))


    def test_appearance_builds_are_counted_after_the_first(self):
        from studio import repair
        from studio.common import read_json, write_json
        project = read_json(self.p / 'project.json'); project['limits']['appearance_iterations_per_shot'] = 2
        write_json(self.p / 'project.json', project)
        self.assertEqual(repair.appearance(self.p, 's', 'v0001'), {'used': 0, 'budget': 2, 'remaining': 2})   # construction
        repair.appearance(self.p, 's', 'v0002')
        self.assertEqual(repair.appearance(self.p, 's', 'v0002')['used'], 1)                                # same version: once
        self.assertEqual(repair.appearance(self.p, 's', 'v0003'), {'used': 2, 'budget': 2, 'remaining': 0})

if __name__ == '__main__':
    unittest.main()
