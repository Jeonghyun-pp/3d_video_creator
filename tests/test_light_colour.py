"""Lamp colour against the white balance (blender_ops/light_colour.py), and transient transfer retries (remote_gpu)."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'studio/blender_ops'))
import light_colour as lc


class LightColourTest(unittest.TestCase):
    def test_cct_of_familiar_colours(self):
        self.assertAlmostEqual(lc.rgb_to_cct((1, 1, 1)), 6500, delta=300)          # sRGB white is D65
        self.assertGreater(lc.rgb_to_cct((0.82, 0.89, 1.0)), 7000)                 # the cool section fill (~7400 K)
        self.assertLess(lc.rgb_to_cct((1.0, 0.72, 0.43)), 4500)                    # a warm practical (~4300 K)
        self.assertIsNone(lc.rgb_to_cct((0, 0, 0)))

    def test_a_lamp_far_from_the_balance_is_reported_with_its_tint(self):
        warnings = lc.casts([('fill', 7400), ('practical', 4300), ('near', 3700)], 4000)   # the cutaway's lamps
        self.assertEqual(len(warnings), 1)
        self.assertIn('fill', warnings[0]); self.assertIn('blue', warnings[0])
        self.assertIn('orange', lc.casts([('lamp', 2200)], 6500)[0])


class SyncRetryTest(unittest.TestCase):
    def test_a_dropped_link_is_retried_and_a_real_error_is_not(self):
        from studio import remote_gpu
        pod = {'publicIp': '1.2.3.4', 'portMappings': {'22': 2222}}
        calls = []
        def run(results):
            def fake(*args, **kwargs):
                calls.append(1)
                return subprocess.CompletedProcess(args, results.pop(0), '', 'unexpected end of file')
            return fake
        with mock.patch.object(remote_gpu, 'endpoint', return_value=('1.2.3.4', 2222)), mock.patch.object(remote_gpu.time, 'sleep'):
            with mock.patch.object(remote_gpu.subprocess, 'run', side_effect=run([255, 0])):
                remote_gpu.rsync(pod, ['a'], '/w')
            self.assertEqual(len(calls), 2)
            calls.clear()
            with mock.patch.object(remote_gpu.subprocess, 'run', side_effect=run([2])), self.assertRaises(remote_gpu.StudioError) as caught:
                remote_gpu.rsync(pod, ['a'], '/w')
            self.assertEqual((len(calls), caught.exception.retryable), (1, False))
            calls.clear()
            with mock.patch.object(remote_gpu.subprocess, 'run', side_effect=run([255, 255, 255, 255])), self.assertRaises(remote_gpu.StudioError) as caught:
                remote_gpu.rsync(pod, ['a'], '/w')
            self.assertEqual((len(calls), caught.exception.retryable), (4, True))


if __name__ == '__main__':
    unittest.main()
