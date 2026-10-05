"""Real FFmpeg integration and edge-case checks for narration/edit/QA."""
from pathlib import Path
import io
import base64
import math
from copy import deepcopy
import json
import urllib.error
from PIL import Image
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from studio.audio import _eleven_response, build_audio, make_cues, run_media, seconds_to_frame
from studio.common import StudioError, file_hash, read_json, write_json
from studio.edit import build_edit, make_overlays, pinned_font, wrap_text
from studio.project import init_project, shot_path
from studio.qa import collect_qa


class TimingTest(unittest.TestCase):
    def test_repeated_words_use_explicit_normalized_ranges(self):
        text = '문 문 문'
        alignment = {'characters': list(text), 'character_start_times_seconds': [i * .1 for i in range(5)],
                     'character_end_times_seconds': [(i + 1) * .1 for i in range(5)]}
        cues, warnings = make_cues(text, alignment, .5, 30,
                                  [{'cue_id': 'last', 'display_text': '마지막 문', 'character_range': [4, 5]}])
        self.assertEqual(cues[0]['start_frame'], 12)
        self.assertEqual(cues[0]['spoken_text'], '문')
        self.assertFalse(warnings)
        self.assertEqual(seconds_to_frame(.25, 30), 8)

    def test_bad_alignment_falls_back_without_word_sync_claim(self):
        cues, warnings = make_cues('문장', {'characters': ['문'], 'character_start_times_seconds': [float('nan')],
                                          'character_end_times_seconds': [.1]}, 1.0, 30)
        self.assertEqual(cues[0]['alignment_source'], 'sentence')
        self.assertEqual(cues[0]['character_range'], None)
        self.assertTrue(warnings)

    def test_font_measurement_rejects_overflow(self):
        font, _ = pinned_font({}, Path('/tmp'), 30)
        with self.assertRaises(StudioError):
            wrap_text('긴 문장 ' * 30, font, 100)
        lines = wrap_text('안쪽 연결부를 살펴봅니다.', font, 200)
        self.assertLessEqual(len(lines), 2)
        self.assertTrue(all(font.getlength(line) <= 200 for line in lines))



class OverlayAndProviderTest(unittest.TestCase):
    def test_projected_label_follows_motion_and_hides_invalid_anchors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            anchor_path = root / 'anchors.json'
            rows = [{'frame': 0, 'label_id': 'joint', 'u': .6, 'v': .4, 'depth': 2, 'visible': True, 'occluded': False},
                    {'frame': 1, 'label_id': 'joint', 'u': .8, 'v': .4, 'depth': 2, 'visible': True, 'occluded': False},
                    {'frame': 2, 'label_id': 'joint', 'u': .8, 'v': .4, 'depth': 2, 'visible': True, 'occluded': True},
                    {'frame': 3, 'label_id': 'joint', 'u': 1.1, 'v': .4, 'depth': 2, 'visible': False, 'occluded': False},
                    {'frame': 4, 'label_id': 'joint', 'u': .8, 'v': .4, 'depth': -1, 'visible': True, 'occluded': False}]
            write_json(anchor_path, {'frames': rows})
            shot = {'shot_id': 'shot_01', 'labels': [{'label_id': 'joint', 'anchor': 'object/joint', 'text': '연결부',
                     'start_frame': 0, 'end_frame': 5, 'slot': 'upper_left', 'occlusion_policy': 'hide'}]}
            style = {'palette_srgb': {'accent': [1, 0, 0]}}
            output = make_overlays(root / 'edit', [{'shot': shot, 'audio': {'speech_status': 'final', 'cues': []},
                                    'frame_count': 5, 'anchors_path': anchor_path}], 180, 320, style, root)
            with Image.open(root / 'edit/overlays/000001.png') as first:
                self.assertEqual(first.getpixel((108, 128)), (255, 0, 0, 255))
            with Image.open(root / 'edit/overlays/000002.png') as second:
                self.assertEqual(second.getpixel((144, 128)), (255, 0, 0, 255))
                self.assertEqual(second.getpixel((108, 128))[3], 0)
            for frame in [3, 4, 5]:
                with Image.open(root / f'edit/overlays/{frame:06d}.png') as hidden:
                    self.assertIsNone(hidden.getbbox())
            self.assertEqual(output['unique_rasters'], 3)
            self.assertTrue(any(box['kind'] == 'label' for box in output['text_boxes']))
            style['label_slots'] = {'upper_left': {'x': .07, 'y': .28, 'width': .30}}
            shifted = make_overlays(root / 'shifted', [{'shot': shot, 'audio': {'speech_status': 'final', 'cues': []},
                                    'frame_count': 5, 'anchors_path': anchor_path}], 180, 320, style, root)
            self.assertEqual(shifted['text_boxes'][0]['bbox'][1], round(320 * .28))
            style['label_slots']['upper_left']['y'] = .76
            with self.assertRaises(StudioError):
                make_overlays(root / 'overflow', [{'shot': shot, 'audio': {'speech_status': 'final', 'cues': []},
                               'frame_count': 5, 'anchors_path': anchor_path}], 180, 320, style, root)

    def test_paid_response_cached_before_processing_and_no_secret_saved(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', {'ELEVENLABS_API_KEY': 'secret-key'}):
            root = Path(temp)
            payload = {'audio_base64': 'AAAA', 'normalized_alignment': None, 'alignment': None}
            with patch('studio.audio.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())) as call:
                self.assertEqual(_eleven_response('한국어', 'voice-id', {}, root, None, None), payload)
                self.assertEqual(_eleven_response('한국어', 'voice-id', {}, root, None, None), payload)
                self.assertEqual(call.call_count, 1)
                body = json.loads(call.call_args.args[0].data)
                self.assertEqual(body['model_id'], 'eleven_multilingual_v2')
                self.assertNotIn('language_code', body)
            self.assertNotIn('secret-key', (root / 'response.json').read_text())
            self.assertNotIn('secret-key', (root / 'request.state.json').read_text())

    def test_ambiguous_paid_post_cannot_be_automatically_repeated(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', {'ELEVENLABS_API_KEY': 'secret-key'}):
            root = Path(temp)
            with patch('studio.audio.urllib.request.urlopen', side_effect=urllib.error.URLError('disconnected')) as call:
                for _ in range(2):
                    with self.assertRaises(StudioError) as caught:
                        _eleven_response('한국어', 'voice-id', {}, root, None, None)
                    self.assertEqual(caught.exception.code, 'AUDIO_REQUEST_UNKNOWN')
                self.assertEqual(call.call_count, 1)

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class MediaIntegrationTest(unittest.TestCase):
    def fixture(self, root: Path, frames=90):
        result = init_project('media_test', {'request': 'Schematic test', 'output': {'width': 180, 'height': 320, 'target_seconds': frames/30},
                                              'shots': [{'shot_id': 'shot_01', 'frame_count': frames,
                                                         'narration': {'text': '안쪽 구조를 살펴봅니다.'}}]}, root=root)
        project = Path(result['project_path'])
        shot = read_json(shot_path(project, 'shot_01'))
        shot['scene_version'] = 'v0001'
        write_json(shot_path(project, 'shot_01'), shot)
        directory = project / 'shots/shot_01/renders/test_render'
        directory.mkdir(parents=True)
        clip = directory / 'clip.mp4'
        run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                   f'testsrc2=s=180x320:r=30:d={frames/30}', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(clip)])
        write_json(directory / 'render.json', {'status': 'complete', 'profile': 'preview', 'scene_version': 'v0001',
                                              'frame_count': frames, 'fps': 30, 'width': 180, 'height': 320, 'clip_path': str(clip)})
        wav = root / 'recording.wav'
        run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                   'sine=frequency=400:sample_rate=48000:duration=1', '-c:a', 'pcm_s16le', str(wav)])
        return project, wav, clip

    def test_import_edit_qa_cache_and_label_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            project, wav, clip = self.fixture(Path(temp))
            audio = build_audio(project, 'shot_01', input_wav=wav)
            self.assertEqual(audio['speech_status'], 'final')
            self.assertEqual(audio['sample_rate'], 48000)
            self.assertTrue(build_audio(project, 'shot_01')['cache_hit'])
            edited = build_edit(project, 'candidate')
            self.assertEqual(edited['speech_status'], 'final')
            self.assertEqual(edited['frame_count'], 90)
            self.assertTrue(build_edit(project, 'candidate')['cache_hit'])
            qa = collect_qa(project, edited['candidate_id'])
            self.assertTrue(qa['technical_pass'], qa['technical_checks'])
            self.assertFalse(qa['human_approved'])
            self.assertEqual(qa['sample_count'], 12)
            self.assertTrue(Path(qa['contact_sheets'][0]).is_file())
            clip_hash = file_hash(clip)
            shot = read_json(shot_path(project, 'shot_01'))
            shot['labels'] = [{'label_id': 'label_main', 'text': '안쪽 연결부', 'anchor': 'object/part/center',
                               'start_frame': 0, 'end_frame': 90, 'slot': 'upper_left', 'occlusion_policy': 'hide'}]
            write_json(shot_path(project, 'shot_01'), shot)
            write_json(clip.parent / 'anchors.json', {'anchors': [{'frame': i, 'label_id': 'label_main', 'u': .7, 'v': .4,
                                                                  'depth': 2, 'visible': True, 'occluded': False} for i in range(90)]})
            revised = build_edit(project, 'candidate')
            self.assertNotEqual(revised['edit_hash'], edited['edit_hash'])
            self.assertEqual(file_hash(clip), clip_hash)
            self.assertEqual(file_hash(project / edited['output_path']), edited['output_sha256'])
            self.assertEqual(revised['overlays']['frame_count'], 90)
            self.assertFalse(revised['warnings'])

    def test_graphics_layer_composited_under_captions_and_required(self):
        from studio.common import stable_hash
        with tempfile.TemporaryDirectory() as temp:
            project, wav, clip = self.fixture(Path(temp))
            build_audio(project, 'shot_01', input_wav=wav)
            shot = read_json(shot_path(project, 'shot_01'))
            shot['graphics'] = [{'graphic_id': 'arrow', 'kind': 'arrow', 'anchors': [[0, 0, 1], [0, 0, 0]], 'start_frame': 45}]
            write_json(shot_path(project, 'shot_01'), shot)
            with self.assertRaises(StudioError) as missing:
                build_edit(project, 'candidate')
            self.assertEqual(missing.exception.code, 'GRAPHICS_NOT_RENDERED')
            layer = project / 'shots/shot_01/graphics/fake'
            layer.mkdir(parents=True)
            for frame in range(90):  # a layer like render_graphics writes: transparent, a solid square from frame 45
                image = Image.new('RGBA', (180, 320))
                if frame >= 45:
                    image.paste((255, 0, 0, 255), (60, 120, 120, 180))
                image.save(layer / f'frame_{frame:06d}.png')
            write_json(layer / 'graphics.json', {'scene_version': 'v0001', 'spec_hash': stable_hash(shot['graphics']),
                                                 'frames_dir': str(layer), 'created_at': '2026-10-05T00:00:00Z'})
            edited = build_edit(project, 'candidate')

            def centre(frame):
                raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(project / edited['output_path']), '-vf',
                                      f'select=eq(n\\,{frame}),crop=10:10:85:145', '-frames:v', '1', '-f', 'rawvideo',
                                      '-pix_fmt', 'rgb24', '-'], capture_output=True, check=True).stdout
                return [sum(raw[i::3]) / (len(raw) // 3) for i in range(3)]
            before, after = centre(20), centre(70)
            self.assertGreater(after[0], 200)
            self.assertLess(after[1], 60)
            self.assertFalse(before[0] > 200 and before[1] < 60, before)  # the test pattern underneath, not the square

    def test_explicit_no_narration_builds_honest_silent_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            project, _, _ = self.fixture(Path(temp), frames=45)
            data = read_json(project / 'project.json')
            data['audio']['provider'] = 'none'
            write_json(project / 'project.json', data)
            shot = read_json(shot_path(project, 'shot_01'))
            shot['narration']['text'] = ''
            write_json(shot_path(project, 'shot_01'), shot)
            audio = build_audio(project, 'shot_01')
            self.assertEqual(audio['speech_status'], 'not_applicable')
            self.assertEqual(audio['cues'], [])
            self.assertFalse(audio['narration_present'])
            edited = build_edit(project, 'candidate')
            self.assertEqual(edited['speech_status'], 'not_applicable')
            self.assertFalse(edited['narration_present'])
            self.assertTrue(edited['output_path'].endswith('candidate.mp4'))
            qa = collect_qa(project, edited['candidate_id'])
            self.assertTrue(qa['technical_pass'], qa['technical_checks'])
            self.assertIsNone(qa['loudness']['integrated_lufs'])
            self.assertFalse(any('voice_not_final' in warning for warning in qa['warnings']))

    def test_actual_cli_subtitle_only_revision_preserves_scene_render_and_voice(self):
        with tempfile.TemporaryDirectory() as temp:
            project, wav, clip = self.fixture(Path(temp))
            def cli(*args):
                result = subprocess.run([sys.executable, '-m', 'studio', *args], capture_output=True, text=True,
                                        cwd=Path(__file__).resolve().parents[1], timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return json.loads(result.stdout)
            cli('audio', 'build', '--project', str(project), '--shot', 'shot_01', '--input-wav', str(wav))
            original = cli('edit', 'build', '--project', str(project), '--profile', 'candidate')
            shot = read_json(shot_path(project, 'shot_01'))
            scene_version, clip_hash = shot['scene_version'], file_hash(clip)
            voice_hash = read_json(project / shot['narration']['audio_manifest'])['wav_sha256']
            change = {'base_revision': shot['revision'], 'scope': 'edit', 'targets': ['shot_01'],
                      'change': {'narration': {'cues': [{'cue_id': 'cue_001', 'spoken_text': shot['narration']['text'],
                                  'display_text': '연결 구조를 확인합니다.', 'character_range': [0, len(shot['narration']['text'])]}]}},
                      'preserve': ['scene_version', 'camera', 'actions']}
            change_path = Path(temp) / 'subtitle-change.json'
            write_json(change_path, change)
            result = cli('shot', 'revise', '--project', str(project), '--shot', 'shot_01', '--change', str(change_path))
            self.assertFalse(result['render_invalidated'])
            revised = cli('edit', 'build', '--project', str(project), '--profile', 'candidate')
            self.assertEqual(read_json(shot_path(project, 'shot_01'))['scene_version'], scene_version)
            self.assertEqual(file_hash(clip), clip_hash)
            self.assertEqual(read_json(project / shot['narration']['audio_manifest'])['wav_sha256'], voice_hash)
            self.assertNotEqual(original['output_sha256'], revised['output_sha256'])
            self.assertIn('연결 구조를 확인합니다.', (project / 'final' / revised['candidate_id'] / 'narration.srt').read_text())
            self.assertEqual(file_hash(project / original['output_path']), original['output_sha256'])

    @unittest.skipUnless(shutil.which('say'), 'macOS say required for honest scratch fallback')
    def test_final_cache_checks_voice_settings_context_and_current_fps_without_paid_call(self):
        with tempfile.TemporaryDirectory() as temp:
            project, wav, _ = self.fixture(Path(temp))
            data = read_json(project / 'project.json')
            data['audio'].update({'provider': 'elevenlabs', 'voice_id': 'configured_voice',
                                  'speech_status': 'final', 'settings': {'stability': .5},
                                  'previous_text': '앞 문장', 'next_text': '뒤 문장'})
            write_json(project / 'project.json', data)
            mp3 = Path(temp) / 'provider.mp3'
            run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(wav), str(mp3)])
            response = {'audio_base64': base64.b64encode(mp3.read_bytes()).decode(), 'alignment': None, 'normalized_alignment': None}
            with patch.dict('os.environ', {'ELEVENLABS_API_KEY': 'fake-key', 'ELEVENLABS_VOICE_ID': ''}), \
                 patch('studio.audio._eleven_response', return_value=response) as provider:
                original = build_audio(project, 'shot_01', mode='final', allow_paid=True)
                self.assertEqual(original['voice_id'], 'configured_voice')
                self.assertEqual(provider.call_args.args[1], 'configured_voice')
                self.assertEqual(provider.call_args.args[-2:], ('앞 문장', '뒤 문장'))
                self.assertEqual(provider.call_count, 1)
            with patch.dict('os.environ', {'ELEVENLABS_API_KEY': '', 'ELEVENLABS_VOICE_ID': ''}), \
                 patch('studio.audio._eleven_response', side_effect=AssertionError('Paid provider must not be called')):
                same = build_audio(project, 'shot_01', mode='final')
                self.assertTrue(same['cache_hit'])
                self.assertEqual(same['speech_status'], 'final')
                changed_fps = deepcopy(data)
                changed_fps['output']['fps'] = 24
                write_json(project / 'project.json', changed_fps)
                cached = build_audio(project, 'shot_01', mode='final')
                self.assertEqual(cached['minimum_frame_count'], math.ceil((original['duration_seconds'] + .15) * 24))
                for patch_value in [{'voice_id': 'different_voice'}, {'voice_id': None}, {'settings': {'stability': .2}},
                                    {'previous_text': '다른 앞 문장'}, {'next_text': '다른 뒤 문장'}]:
                    changed = deepcopy(data)
                    changed['audio'].update(patch_value)
                    write_json(project / 'project.json', changed)
                    scratch = build_audio(project, 'shot_01', mode='final')
                    self.assertEqual(scratch['speech_status'], 'scratch', patch_value)
                    self.assertEqual(scratch['provider'], 'say')
                    self.assertNotEqual(scratch['request_key'], original['request_key'])
                    self.assertTrue(any('voice_not_final' in warning for warning in scratch['warnings']))
                    write_json(project / 'project.json', data)
                    restored = build_audio(project, 'shot_01', mode='final')
                    self.assertEqual(restored['request_key'], original['request_key'])
                    self.assertEqual(restored['speech_status'], 'final')
            with patch.dict('os.environ', {'ELEVENLABS_API_KEY': 'fake-key', 'ELEVENLABS_VOICE_ID': 'environment_voice'}), \
                 patch('studio.audio._eleven_response', return_value=response) as provider:
                override = build_audio(project, 'shot_01', mode='final', allow_paid=True)
                self.assertEqual(override['voice_id'], 'environment_voice')
                self.assertEqual(provider.call_args.args[1], 'environment_voice')
                self.assertNotEqual(override['request_key'], original['request_key'])

    def test_selected_recording_stays_pinned_when_tts_settings_change(self):
        with tempfile.TemporaryDirectory() as temp:
            project, wav, _ = self.fixture(Path(temp))
            original = build_audio(project, 'shot_01', input_wav=wav)
            data = read_json(project / 'project.json')
            data['audio'].update({'provider': 'elevenlabs', 'voice_id': 'another_voice', 'settings': {'stability': .1},
                                  'previous_text': '새 문맥', 'next_text': None})
            write_json(project / 'project.json', data)
            with patch('studio.audio._eleven_response', side_effect=AssertionError('Recording must not trigger TTS')):
                pinned = build_audio(project, 'shot_01', mode='final', allow_paid=True)
            self.assertEqual(pinned['provider'], 'import')
            self.assertEqual(pinned['request_key'], original['request_key'])
            self.assertEqual(pinned['wav_sha256'], original['wav_sha256'])

    def test_speech_overrun_is_rejected_without_trim(self):
        with tempfile.TemporaryDirectory() as temp:
            project, wav, _ = self.fixture(Path(temp), frames=30)
            audio = build_audio(project, 'shot_01', input_wav=wav)
            original_hash = audio['wav_sha256']
            with self.assertRaises(StudioError) as caught:
                build_edit(project)
            self.assertEqual(caught.exception.code, 'TIMING_CONFLICT')
            self.assertEqual(file_hash(project / audio['wav_path']), original_hash)

    @unittest.skipUnless(shutil.which('say'), 'macOS say required')
    def test_real_scratch_voice_never_marks_final(self):
        with tempfile.TemporaryDirectory() as temp:
            project, _, _ = self.fixture(Path(temp), frames=180)
            with patch.dict('os.environ', {'ELEVENLABS_API_KEY': '', 'ELEVENLABS_VOICE_ID': ''}):
                audio = build_audio(project, 'shot_01', mode='final')
            self.assertEqual(audio['provider'], 'say')
            self.assertEqual(audio['speech_status'], 'scratch')
            self.assertTrue(audio['duration_seconds'] > .5)
            self.assertTrue(any('voice_not_final' in warning for warning in audio['warnings']))
            edited = build_edit(project, 'rough')
            self.assertEqual(edited['delivery_status'], 'needs_voice')
            self.assertTrue(edited['output_path'].endswith('scratch_candidate.mp4'))


if __name__ == '__main__':
    unittest.main()
