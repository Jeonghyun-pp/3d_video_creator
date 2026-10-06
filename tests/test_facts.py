"""Narration facts (studio/facts.py): each sentence rests on a sourced claim or is marked illustrative, without numbers."""
from pathlib import Path
import tempfile
import unittest

from studio import facts
from studio.common import StudioError, read_json, write_json
from studio.project import init_project, shot_path


class FactsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('facts_test', {'request': 'facts', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, self.tmp.name)['project_path'])

    def tearDown(self):
        self.tmp.cleanup()

    def say(self, text, links=None, sources=None, claims=None):
        shot = read_json(shot_path(self.project, 's'))
        shot['narration']['text'] = text
        if links is None:
            shot['narration'].pop('sentence_claims', None)
        else:
            shot['narration']['sentence_claims'] = links
        write_json(shot_path(self.project, 's'), shot)
        write_json(self.project / 'sources.json', {'schema_version': 1, 'sources': sources or [], 'claims': claims or []})
        return [p['code'] for p in facts.check(self.project)]

    def test_sentences_split_on_korean_and_latin_punctuation(self):
        self.assertEqual(facts.sentences('기둥은 무엇을 받칠까요? 지붕입니다. 끝'), ['기둥은 무엇을 받칠까요?', '지붕입니다.', '끝'])

    def test_every_sentence_needs_a_claim_or_an_illustrative_mark(self):
        source = [{'source_id': 'ks', 'title': 'KS D 3504', 'citation': 'KS D 3504:2021'}]
        claim = [{'claim_id': 'rebar', 'text': 'deformed bars', 'source_ids': ['ks']}]
        self.assertEqual(self.say('철근은 이형철근입니다. 그림으로 보죠.'), ['FACTS_UNSOURCED', 'FACTS_UNSOURCED'])
        self.assertEqual(self.say('철근은 이형철근입니다. 그림으로 보죠.', [{'claim_ids': ['rebar']}], source, claim), ['FACTS_MISALIGNED'])
        self.assertEqual(self.say('철근은 이형철근입니다. 그림으로 보죠.', [{'claim_ids': ['rebar']}, {'illustrative': 'framing line'}], source, claim), [])
        self.assertEqual(self.say('기둥 80개 중 50개입니다.', [{'illustrative': 'example only'}]), ['FACTS_ILLUSTRATIVE_NUMBER'])
        self.assertEqual(self.say('철근은 이형철근입니다.', [{'claim_ids': ['nope']}], source, claim), ['FACTS_UNKNOWN_CLAIM'])
        self.assertEqual(self.say('철근은 이형철근입니다.', [{'claim_ids': ['rebar']}], [], claim), ['FACTS_SOURCE_MISSING'])
        with self.assertRaises(StudioError) as caught:
            facts.require(self.project)
        self.assertEqual(caught.exception.code, 'FACTS_SOURCE_MISSING')

    def test_the_samsung_example_makes_no_unsourced_claims(self):
        from studio.project import from_example
        target = Path(self.tmp.name) / 'samsung'
        from_example('samsung_cutaway', target)
        self.assertEqual(facts.check(target), [])


if __name__ == '__main__':
    unittest.main()
