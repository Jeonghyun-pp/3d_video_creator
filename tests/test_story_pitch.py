"""Story pitches (studio/story_pitch.py, 2026-10-20): the agent names lenses from the facts, writes pitches per lens,
groups the alike; blind judges rank the pitches; the directions are the best pitches of different groups - never a draw."""
from pathlib import Path
import tempfile
import unittest

from studio import story, story_pitch
from studio.common import StudioError, read_json, write_json
from studio.project import init_project

LENSES = [{'lens_id': f'L{k}', 'question': q} for k, q in enumerate(
    ['what number surprises most', 'who suffers when it stops', 'where do people meet it daily',
     'when did it nearly fail', 'how big is it beside a bus'])]


def pitches(lenses=5, each=3):
    return [{'pitch_id': f'p{k}{j}', 'lens': f'L{k}', 'opening': f'the lift {k}{j} stops between floors',
             'question': 'why did it stop there', 'stake': 'the rider late for work'} for k in range(lenses) for j in range(each)]


def groups(lenses=5, each=3):
    return {f'g{k}': {'label': f'lens {k}', 'pitch_ids': [f'p{k}{j}' for j in range(each)]} for k in range(lenses)}


class PitchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('lift', {'request': 'how a lift stops level', 'shots': [{'shot_id': 's1', 'frame_count': 30}]},
                                         self.tmp.name)['project_path'])

    def tearDown(self):
        self.tmp.cleanup()

    def facts(self):
        sources = read_json(self.project / 'sources.json')
        write_json(self.project / 'sources.json', {**sources, 'claims': [{'claim_id': 'c1', 'text': 'a lift brake holds 125 % of its load'}]})

    def test_the_brief_needs_facts_and_shows_lenses_as_examples(self):
        with self.assertRaises(StudioError):
            story_pitch.brief(self.project)
        self.facts()
        out = story_pitch.brief(self.project)
        text = Path(out['brief']).read_text()
        self.assertIn('a lift brake holds 125 %', text)
        self.assertIn('never copy them', text)   # the library's lenses are examples, never slots
        self.assertIn('its own pass', text)

    def test_lenses_differ_and_pitches_open_on_something_seen(self):
        self.facts()
        rnd = story_pitch.brief(self.project)['round']
        with self.assertRaises(StudioError):   # too few
            story_pitch.lenses(self.project, LENSES[:3], rnd)
        with self.assertRaises(StudioError) as said:   # one question asked twice
            story_pitch.lenses(self.project, LENSES[:4] + [{'lens_id': 'L9', 'question': 'what number surprises most'}], rnd)
        self.assertIn('LENS_REPEATED', str(said.exception))
        story_pitch.lenses(self.project, LENSES, rnd)
        with self.assertRaises(StudioError) as said:
            story_pitch.add(self.project, [{**pitches()[0], 'opening': 'have you ever wondered how a lift stops?'}], rnd)
        self.assertIn('HOOK_UNSEEN', str(said.exception))
        with self.assertRaises(StudioError):   # a lens not named
            story_pitch.add(self.project, [{**pitches()[0], 'lens': 'L7'}], rnd)

    def test_groups_cover_every_pitch_once_and_the_set_is_full(self):
        self.facts()
        rnd = story_pitch.brief(self.project)['round']
        story_pitch.lenses(self.project, LENSES, rnd)
        story_pitch.add(self.project, pitches(each=1), rnd)
        with self.assertRaises(StudioError):   # one per lens: too thin, too few
            story_pitch.group(self.project, groups(each=1), rnd)
        story_pitch.add(self.project, pitches(), rnd)
        bad = groups(); bad['g0']['pitch_ids'].append('p10')
        with self.assertRaises(StudioError):   # p10 in two groups
            story_pitch.group(self.project, bad, rnd)
        story_pitch.group(self.project, groups(), rnd)

    def test_the_tournament_meets_other_groups_and_the_directions_are_three_groups_best(self):
        self.facts()
        rnd = story_pitch.brief(self.project)['round']
        story_pitch.lenses(self.project, LENSES, rnd)
        story_pitch.add(self.project, pitches(), rnd)
        story_pitch.group(self.project, groups(), rnd)
        with self.assertRaises(StudioError) as said:   # no tournament yet: no directions, no draw instead
            story.directions(self.project)
        self.assertIn('STORY_PITCHES_UNJUDGED', str(said.exception))
        packed = story_pitch.judge_pack(self.project, rnd, seed=1)
        folder = self.project / 'story' / 'pitches' / rnd
        key = read_json(folder / 'judge_key.json')['pairs']
        self.assertFalse((folder / 'judge' / 'judge_key.json').exists())   # the key is never where the judges read
        group_of = {i: g for g, v in groups().items() for i in v['pitch_ids']}
        self.assertTrue(all(group_of[k['A']] != group_of[k['B']] for k in key.values()))
        meetings = {i: sum(i in (k['A'], k['B']) for k in key.values()) for i in group_of}
        self.assertTrue(all(m == story_pitch.MEETINGS for m in meetings.values()), meetings)
        self.assertEqual(packed['pairs'], len(key))
        strong = {'p20', 'p31', 'p02'}   # the judges always pick these, else side A
        verdicts = [{'judge': j, 'pair': p, 'why': 'flat',
                     'winner': 'A' if k['A'] in strong else 'B' if k['B'] in strong else 'A'} for p, k in key.items() for j in range(3)]
        story_pitch.judged(self.project, verdicts, rnd)
        scores = read_json(folder / 'scores.json')['scores']
        self.assertTrue(all(scores[i]['score'] >= 0.75 for i in strong))   # two strong ones may meet
        rnd2 = story.directions(self.project)
        chosen = [s['pitch'] for s in rnd2['slots']]
        self.assertEqual(len({p['group'] for p in chosen}), 3)
        tops = {}
        for i in sorted(scores, key=lambda i: (-scores[i]['score'], i)):
            tops.setdefault(group_of[i], i)
        self.assertEqual([p['pitch_id'] for p in chosen], sorted(tops.values(), key=lambda i: (-scores[i]['score'], i))[:3])
        self.assertIn(chosen[0]['opening'], rnd2['slots'][0]['brief'])
        with self.assertRaises(StudioError) as said:   # the body names another pitch
            from tests.test_story import written
            story.add(self.project, rnd2['round'], 1, written(rnd2['slots'][1]))
        self.assertIn('STORY_PITCH_DIFFERS', str(said.exception))

    def ready(self):
        self.facts()
        rnd = story_pitch.brief(self.project)['round']
        story_pitch.lenses(self.project, LENSES, rnd)
        story_pitch.add(self.project, pitches(), rnd)
        story_pitch.group(self.project, groups(), rnd)
        return rnd

    def test_every_pitch_meets_its_count_whatever_the_seed_and_a_group_over_half_is_refused(self):
        ids = [f'p{k}{j}' for k in range(3) for j in range(5)]
        group_of = {i: i[1] for i in ids}
        for seed in range(30):
            pairs, short = story_pitch.schedule(ids, group_of, seed=seed)
            self.assertEqual(short, [], seed)
            self.assertTrue(all(group_of[a] != group_of[b] for a, b in pairs))
        rnd = self.ready()
        big = {'g0': {'label': 'most', 'pitch_ids': [f'p{k}{j}' for k in range(3) for j in range(3)]},
               'g1': {'label': 'rest', 'pitch_ids': [f'p{k}{j}' for k in (3, 4) for j in range(3)]}}
        with self.assertRaises(StudioError):
            story_pitch.group(self.project, big, rnd)

    def test_verdicts_on_changed_pitches_or_groups_are_refused(self):
        rnd = self.ready()
        with self.assertRaises(StudioError):   # nothing packed yet
            story_pitch.judged(self.project, [], rnd)
        story_pitch.judge_pack(self.project, rnd)
        folder = self.project / 'story' / 'pitches' / rnd
        key = read_json(folder / 'judge_key.json')['pairs']
        verdicts = [{'judge': j, 'pair': p, 'winner': 'A', 'why': 'x'} for p in key for j in range(3)]
        regrouped = groups(); regrouped['g0']['label'] = 'renamed'
        story_pitch.group(self.project, regrouped, rnd)   # a new grouping: the pairs are gone with it
        self.assertFalse((folder / 'judge_key.json').exists())
        story_pitch.judge_pack(self.project, rnd)
        key = read_json(folder / 'judge_key.json')['pairs']
        verdicts = [{'judge': j, 'pair': p, 'winner': 'A', 'why': 'x'} for p in key for j in range(3)]
        pitched = read_json(folder / 'pitches.json'); pitched[0]['opening'] = 'something else happens on screen'
        write_json(folder / 'pitches.json', pitched)   # edited by hand behind the pack
        with self.assertRaises(StudioError) as said:
            story_pitch.judged(self.project, verdicts, rnd)
        self.assertIn('DECISION_STALE', str(said.exception.code if hasattr(said.exception, 'code') else said.exception))

    def test_a_new_round_goes_on_to_unused_pitches_and_an_unjudged_newer_round_is_not_skipped(self):
        rnd = self.ready()
        story_pitch.judge_pack(self.project, rnd)
        key = read_json(self.project / 'story' / 'pitches' / rnd / 'judge_key.json')['pairs']
        story_pitch.judged(self.project, [{'judge': j, 'pair': p, 'winner': 'A', 'why': 'x'} for p in key for j in range(3)], rnd)
        first = {s['pitch']['pitch_id'] for s in story.directions(self.project)['slots']}
        second = {s['pitch']['pitch_id'] for s in story.directions(self.project)['slots']}
        self.assertFalse(first & second)
        story_pitch.brief(self.project)   # a newer pitch round, not judged: its directions wait for it
        with self.assertRaises(StudioError) as said:
            story.directions(self.project)
        self.assertIn('p02 is not judged', str(said.exception))

    def test_pitches_changed_after_the_pack_are_stale(self):
        self.facts()
        rnd = story_pitch.brief(self.project)['round']
        story_pitch.lenses(self.project, LENSES, rnd)
        story_pitch.add(self.project, pitches(), rnd)
        story_pitch.group(self.project, groups(), rnd)
        story_pitch.judge_pack(self.project, rnd)
        self.assertFalse((self.project / 'story' / 'pitches' / rnd / 'scores.json').exists())
        story_pitch.add(self.project, [{**pitches()[0], 'pitch_id': 'p05'}], rnd)   # a new pitch: the grouping is of another set
        self.assertFalse((self.project / 'story' / 'pitches' / rnd / 'groups.json').exists())


if __name__ == '__main__':
    unittest.main()
