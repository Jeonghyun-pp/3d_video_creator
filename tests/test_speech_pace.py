"""How the user's reference speaks (studio/speech_pace.py): sentences counted with or without punctuation, a reel's
numbers from its transcript, a reel posted twice counted once. No transcription here (mlx-whisper is optional)."""
import unittest
from unittest import mock

from studio import speech_pace as sp


def speech(words, text=None):
    """[(start, end, word)] -> a transcript."""
    return {'text': text or ''.join(w for _, _, w in words), 'segments': [{'words': [{'start': a, 'end': b, 'word': w} for a, b, w in words]}]}


class SentencesTest(unittest.TestCase):
    def test_punctuated(self):
        self.assertEqual(sp.sentences('물이 내려갑니다. 그런데 어디로 갈까요? 따라가 보죠.'),
                         ['물이 내려갑니다.', '그런데 어디로 갈까요?', '따라가 보죠.'])

    def test_unpunctuated_by_korean_endings(self):
        # whisper left the marks out on some reels: the endings still close each sentence
        text = '부산시 홍보대사가 다쳤습니다 이 상어는 북항입니다 처음엔 무태상어로 알려졌죠 그런데 왜 다쳤을까요 원인은 아직 모릅니다'
        self.assertEqual(len(sp.sentences(text)), 5)

    def test_empty(self):
        self.assertEqual(sp.sentences('  '), [])


class ReelPaceTest(unittest.TestCase):
    def test_numbers_of_one_reel(self):
        words = [(0.0, 0.5, ' 물이'), (0.5, 1.4, ' 내려갑니다.'), (2.0, 2.6, ' 어디로'), (2.6, 3.4, ' 갈까요?')]
        pace = sp.reel_pace(speech(words), 4.0, [2.0, 2.0])
        self.assertEqual((pace['sentences'], pace['pauses'], pace['shots']), (2, 1, 2))   # one gap of 0.6 s
        self.assertAlmostEqual(pace['speech_share'], 2.8 / 4.0, places=3)
        self.assertEqual(pace['chars_per_sentence'], round(len('물이내려갑니다.어디로갈까요?') / 2, 1))


class LearnTest(unittest.TestCase):
    def test_a_reel_posted_twice_counts_once_and_the_profile_keeps_the_rest(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            measured = Path(tmp) / 'measured'
            for name, sha in (('A', 'a1'), ('B', 'b1'), ('C', 'c1')):
                (measured / name).mkdir(parents=True)
                (measured / name / 'grammar.json').write_text(json.dumps({'video_sha256': sha, 'media': {'duration': 10.0},
                                                                          'shots': [{'len_s': 5.0}, {'len_s': 5.0}]}))
            full = Path(tmp) / 'full.json'
            full.write_text(json.dumps({'clips': {'x': {'sha256': 'a1', 'group': 'g'}, 'y': {'sha256': 'b1', 'group': 'g'},
                                                  'z': {'sha256': 'c1', 'group': 'other'}}}))
            once = speech([(0.0, 9.0, ' 같은 영상입니다.')])
            stored = {}
            with mock.patch('studio.camera_taste._measured_dir', return_value=(full, measured)), \
                    mock.patch('studio.camera_taste._video_of', return_value=Path('v.mp4')), \
                    mock.patch.object(sp, 'transcribe', return_value=once), \
                    mock.patch('studio.camera_taste.load_profile', return_value={'name': 'ref', 'kinds': {}}), \
                    mock.patch.object(sp, 'write_json', side_effect=lambda path, data: stored.update(data)):
                result = sp.learn(['g'], '기준 계정 문장 세어봐')
            self.assertEqual(result['reels'], 1)
            self.assertEqual(stored['name'], 'ref')                     # the camera profile is kept
            self.assertEqual(stored['speech']['speech_share']['p50'], 0.9)


if __name__ == '__main__':
    unittest.main()
