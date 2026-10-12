"""Narration lines placed where the shot plan puts them (audio.place_segments): the gaps are silence, the words are never
cut or sped up. Why (2026-10-12, the user: "하나의 내용(문장)을 설명할때 컷이 좀더 길지 않나? ... 훅훅 넘어가는 느낌")."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from studio import audio
from studio.common import StudioError, file_hash, read_json, write_json
from studio.project import init_project

TEXT = '가나 다라'
# 가 0-0.5, 나 0.5-1.0, the joining space 1.0-1.5 (silence), 다 1.5-2.0, 라 2.0-2.5
ALIGNMENT = {'characters': list(TEXT), 'character_start_times_seconds': [0.0, 0.5, 1.0, 1.5, 2.0],
             'character_end_times_seconds': [0.5, 1.0, 1.5, 2.0, 2.5], 'source': 'test'}


class PlaceSegmentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(init_project('voice', {'request': 'voice', 'shots': [{'shot_id': 's01', 'frame_count': 300}]}, self.tmp.name)['project_path'])
        folder = self.project / 'shots/s01/audio/k'
        folder.mkdir(parents=True)
        self.wav = folder / 'voice.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=f=440:d=1', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=mono:d=0.5',
                        '-f', 'lavfi', '-i', 'sine=f=660:d=1', '-filter_complex',
                        '[0:a]aresample=48000,aformat=channel_layouts=mono[a];[2:a]aresample=48000,aformat=channel_layouts=mono[c];'
                        '[a][1:a][c]concat=n=3:v=0:a=1[o]', '-map', '[o]', '-c:a', 'pcm_s16le', str(self.wav)], check=True)
        write_json(folder / 'alignment.json', {'alignment': ALIGNMENT, 'normalized_alignment': ALIGNMENT})
        self.built = {'wav_path': str(self.wav.relative_to(self.project)), 'wav_sha256': file_hash(self.wav),
                      'alignment_path': str((folder / 'alignment.json').relative_to(self.project)), 'duration_seconds': 2.5, 'warnings': []}

    def tearDown(self):
        self.tmp.cleanup()

    def shot(self, at):
        return {'shot_id': 's01', 'narration': {'text': TEXT, 'segments': [{'text': '가나', 'at_s': at[0]}, {'text': '다라', 'at_s': at[1]}]}}

    def test_the_second_line_waits_in_silence(self):
        placed = audio.place_segments(self.project, self.shot([0.0, 4.0]), self.built)
        self.assertAlmostEqual(placed['duration_seconds'], 5.0, places=2)   # 4.0 s + the second line's 1.0 s
        moved = read_json(self.project / placed['alignment_path'])['alignment']
        self.assertAlmostEqual(moved['character_start_times_seconds'][3], 4.0, places=3)
        self.assertEqual(moved['character_start_times_seconds'][:2], [0.0, 0.5])
        starts = moved['character_start_times_seconds']
        self.assertEqual(starts, sorted(starts))   # subtitles can be timed from it
        quiet = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(self.project / placed['wav_path']), '-af', 'atrim=1.4:3.9,volumedetect',
                                '-f', 'null', '-'], capture_output=True, text=True).stderr
        self.assertIn('max_volume: -91', quiet)   # digital silence between the lines

    def test_a_line_asked_to_start_while_the_one_before_speaks(self):
        with self.assertRaises(StudioError) as caught:
            audio.place_segments(self.project, self.shot([0.0, 0.6]), self.built)
        self.assertEqual(caught.exception.code, 'TIMING_CONFLICT')

    def test_without_segments_nothing_moves(self):
        self.assertIs(audio.place_segments(self.project, {'narration': {'text': TEXT}}, self.built), self.built)


if __name__ == '__main__':
    unittest.main()
