"""Composite completion and child cleanup, using local 30 fps video and fake readers only (no RIFE or paid calls)."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image

from studio import decisions
from studio.common import StudioError, file_hash, read_json, write_json
from studio2 import video
from tests import studio2_fixture as fx
from tests.test_studio2_pictures import marks_ok, place_reader


class CompositeCompletionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = fx.project(Path(self.tmp.name))
        decisions.write(self.path, 'cuts', fx.body(), agent_note=fx.TEST_NOTE)
        project = read_json(self.path / 'project.json')
        project['output'].update(width=216, height=384)
        write_json(self.path / 'project.json', project)
        self.source = self.path / 'source.mp4'
        # Longer than the six frames requested: the decoder must stop without blocking on its output pipe.
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=gray:s=216x384:r=30',
                        '-frames:v', '12', '-pix_fmt', 'yuv420p', str(self.source)], check=True)
        self.row = {'fingerprint': 'fixture', 'frame_count': 6, 'clip': str(self.source), 'clip_sha256': file_hash(self.source)}
        self.folder = video.folder_of(self.path, 'c1', 'fixture')
        need = mock.patch.object(video, '_need', return_value=self.row)
        need.start()
        self.addCleanup(need.stop)
        # Location is independent of decoder fault injection; all frames describe the same test pattern.
        def grab(clip, seconds, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (216, 384), 'gray').save(target)
            return target
        grabbing = mock.patch.object(video, '_grab', side_effect=grab)
        grabbing.start()
        self.addCleanup(grabbing.stop)
        self.children = []
        self.popen = subprocess.Popen

    def composite(self):
        return video.composite(self.path, 'c1', reader=place_reader, checker=marks_ok)

    def spawn(self, decoder_frames=None, decoder_exit=0, encoder_exit=None, broken_pipe=False, spawn_error=False):
        def popen(command, **kwargs):
            if kwargs.get('stdout') == subprocess.PIPE:
                if decoder_frames is not None:
                    code = f'import sys; sys.stdout.buffer.write(bytes(216*384*3*{decoder_frames})); sys.exit({decoder_exit})'
                    command = [sys.executable, '-c', code]
            elif kwargs.get('stdin') == subprocess.PIPE:
                if spawn_error:
                    raise OSError('test encoder could not start')
                if encoder_exit is not None:
                    # Leave a partial artifact to verify failed output never becomes a completed cache entry.
                    Path(command[-1]).write_bytes(b'partial')
                    code = f'import sys; sys.stdin.buffer.read(); sys.exit({encoder_exit})'
                    if broken_pipe:
                        code = f'import os, sys; os.close(0); sys.exit({encoder_exit})'
                    command = [sys.executable, '-c', code]
            child = self.popen(command, **kwargs)
            self.children.append(child)
            return child
        return mock.patch.object(video.subprocess, 'Popen', side_effect=popen)

    def assert_failed_cleanly(self):
        self.assertFalse((self.folder / 'composite.json').exists())
        target = self.path / 'shots' / 'c1' / 'composite'
        self.assertEqual(list(target.rglob('clip.json')), [])
        self.assertEqual(list(target.rglob('*.mp4')), [])
        for child in self.children:
            self.assertIsNotNone(child.returncode)
            for pipe in (child.stdin, child.stdout):
                if pipe is not None:
                    self.assertTrue(pipe.closed)
            # wait() already collected the child, rather than merely observing that it exited.
            with self.assertRaises(ChildProcessError):
                os.waitpid(child.pid, os.WNOHANG)

    def test_complete_clip_has_actual_frames_and_is_reused(self):
        with self.spawn():
            result = self.composite()
        self.assertEqual((result['status'], result['frame_count']), ('complete', 6))
        clip = self.path / result['clip_path']
        self.assertEqual(video._frame_count(clip), 6)
        self.assertEqual(result['clip_sha256'], file_hash(clip))
        with mock.patch.object(video.subprocess, 'Popen', side_effect=AssertionError('cached: no encoder')):
            self.assertTrue(self.composite()['cached'])
        self.assertTrue(all(c.returncode == 0 for c in self.children))

    def test_short_eof_is_not_complete_and_retry_can_succeed(self):
        with self.spawn(decoder_frames=5), self.assertRaises(StudioError) as caught:
            self.composite()
        self.assertEqual(caught.exception.code, 'RENDER_FAILED')
        self.assertIn('5/6 frames', caught.exception.message)
        self.assert_failed_cleanly()
        result = self.composite()
        self.assertFalse(result['cached'])
        self.assertEqual(video._frame_count(self.path / result['clip_path']), 6)

    def test_decoder_failure_after_all_frames_is_not_complete(self):
        with self.spawn(decoder_frames=6, decoder_exit=7), self.assertRaises(StudioError) as caught:
            self.composite()
        self.assertEqual(caught.exception.code, 'RENDER_FAILED')
        self.assertIn('decoder exit 7', caught.exception.message)
        self.assert_failed_cleanly()

    def test_encoder_failure_after_all_frames_is_not_complete(self):
        with self.spawn(encoder_exit=8), self.assertRaises(StudioError) as caught:
            self.composite()
        self.assertEqual(caught.exception.code, 'RENDER_FAILED')
        self.assertIn('encoder exit 8', caught.exception.message)
        self.assert_failed_cleanly()

    def test_encoder_broken_pipe_reaps_both_children(self):
        with self.spawn(encoder_exit=9, broken_pipe=True), self.assertRaises(StudioError) as caught:
            self.composite()
        self.assertEqual(caught.exception.code, 'RENDER_FAILED')
        self.assert_failed_cleanly()

    def test_drawing_error_reaps_both_children(self):
        with self.spawn(), mock.patch('studio2.motion.draw', side_effect=ValueError('bad overlay')), self.assertRaisesRegex(ValueError, 'bad overlay'):
            self.composite()
        self.assert_failed_cleanly()

    def test_encoder_spawn_error_reaps_decoder(self):
        with self.spawn(spawn_error=True), self.assertRaises(StudioError) as caught:
            self.composite()
        self.assertEqual(caught.exception.code, 'RENDER_FAILED')
        self.assert_failed_cleanly()


if __name__ == '__main__':
    unittest.main()
