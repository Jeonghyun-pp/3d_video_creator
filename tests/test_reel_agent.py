import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('codex'), 'codex CLI not installed')
class ReelAgentTest(unittest.TestCase):
    def run_dry(self, *args):
        out = subprocess.run([sys.executable, 'scripts/reel_agent.py', 'make a test', '--dry-run', *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        return json.loads(out)

    def test_default_gpu_and_route_rules_in_prompt(self):
        data = self.run_dry()
        self.assertEqual(data['env']['STUDIO_RENDER_DEVICE'], 'GPU')
        prompt = data['command'][-1]
        self.assertIn('route plan', prompt); self.assertIn('route approve', prompt); self.assertIn('no text', prompt)

    def test_low_load_uses_cpu(self):
        self.assertEqual(self.run_dry('--low-load')['env']['STUDIO_RENDER_DEVICE'], 'CPU')


if __name__ == '__main__':
    unittest.main()
