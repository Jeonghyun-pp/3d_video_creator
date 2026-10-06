"""Host code imports Blender-side pure modules through the package and never puts studio/blender_ops on sys.path:
that directory holds top-level fidelity.py, graphics.py, assets.py ... which would shadow same-named imports."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class ImportHygieneTest(unittest.TestCase):
    def test_cli_import_leaves_sys_path_alone(self):
        code = ("import sys; before = list(sys.path); import studio.__main__, studio.titles, studio.camera_fit, studio.edit, studio.qa, studio.mechanisms, studio.storyboard; "
                "assert not any(p.endswith('blender_ops') for p in sys.path), sys.path")
        subprocess.run([sys.executable, '-c', code], cwd=REPO, check=True)

    def test_cli_runs_from_another_directory_beside_a_foreign_scripts_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'scripts').mkdir(); (Path(tmp) / 'scripts' / '__init__.py').write_text('')
            env = {**os.environ, 'PYTHONPATH': os.pathsep.join([tmp, str(REPO)])}
            result = subprocess.run([sys.executable, '-m', 'studio', 'doctor'], cwd=tmp, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout[-400:] + result.stderr[-400:])


if __name__ == '__main__':
    unittest.main()
