"""The edit's sound: the music bed drops under the voice (sidechain), effects land at their cue, and the effects'
library sounds are named in the result."""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from studio import edit


def tone(path, freq, seconds, volume=0.5):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'sine=f={freq}:d={seconds}:sample_rate=48000', '-af', f'volume={volume}',
                    str(path)], check=True)


def band_rms(path, freq, start, end):
    out = subprocess.run(['ffmpeg', '-v', 'info', '-ss', str(start), '-t', str(end - start), '-i', str(path), '-af',
                          f'bandpass=f={freq}:width_type=h:w=60,astats=metadata=0:reset=0', '-f', 'null', '-'], capture_output=True, text=True).stderr
    return float(re.findall(r'RMS level dB: (-?[\d.]+|-inf)', out)[-1].replace('-inf', '-200'))


class SoundMixTest(unittest.TestCase):
    def test_music_ducks_under_the_voice_and_effects_land(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            # voice: silent 0-1.5 s, a 1 kHz "voice" 1.5-3 s, silent 3-4.5 s
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=mono', '-t', '1.5', str(tmp / 'a.wav')], check=True)
            tone(tmp / 'b.wav', 1000, 1.5, 0.6)
            (tmp / 'list.txt').write_text("file 'a.wav'\nfile 'b.wav'\nfile 'a.wav'\n")
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(tmp / 'list.txt'), '-ar', '48000', '-ac', '1', str(tmp / 'voice.wav')], check=True)
            tone(tmp / 'music.wav', 220, 6.0, 0.5)
            tone(tmp / 'click.wav', 3000, 0.15, 0.8)
            sounds = {'bed': ({'sha256': 'm', 'source': {'license_id': 'CC0-1.0'}}, tmp / 'music.wav'),
                      'click': ({'sha256': 'c', 'source': {'license_id': 'CC0-1.0'}}, tmp / 'click.wav')}
            with mock.patch('studio.sounds.load', side_effect=lambda name: sounds[name]):
                out = edit.sound_mix(tmp / 'voice.wav', {'sound': 'bed', 'gain_db': -6, 'fade_in_s': 0, 'fade_out_s': 0},
                                     [{'id': 's01/tag', 'sound': 'click', 'seconds': 4.0, 'gain_db': 0}], 4.5, tmp / 'mix.wav')
            free, under = band_rms(tmp / 'mix.wav', 220, 0.3, 1.3), band_rms(tmp / 'mix.wav', 220, 1.9, 2.9)
            self.assertGreater(free - under, 6, (free, under))                                # the bed ducks while the voice speaks
            self.assertGreater(band_rms(tmp / 'mix.wav', 3000, 3.95, 4.2), band_rms(tmp / 'mix.wav', 3000, 3.3, 3.8) + 20)   # the click at 4 s
            self.assertEqual([s['role'] for s in out['sounds']], ['music', 'sfx'])

    def test_effects_at_cues(self):
        shots = [{'shot': {'shot_id': 's01', 'sfx': [{'id': 'open', 'sound': 'whoosh', 'frame': 10}]}, 'audio': {'cues': []}, 'frame_count': 90},
                 {'shot': {'shot_id': 's02', 'sfx': [{'id': 'tag', 'sound': 'pop', 'cue': 'cue_002', 'offset_frames': -3}]},
                  'audio': {'cues': [{'cue_id': 'cue_002', 'start_frame': 33}]}, 'frame_count': 60}]
        events = edit.sound_events(shots, 30)
        self.assertEqual([e['seconds'] for e in events], [round(10 / 30, 4), round((90 + 30) / 30, 4)])
        shots[1]['shot']['sfx'][0]['cue'] = 'cue_009'
        with self.assertRaises(Exception):
            edit.sound_events(shots, 30)


if __name__ == '__main__':
    unittest.main()
