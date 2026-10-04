import re
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / '.agents/skills/reel-production'
BEFORE_PHASE_C = [  # original craft rules that must survive verbatim in references
    'For fine disassembly, compare the provider\'s native .blend with glTF before splitting geometry.',
    'Primitive cube UV faces may be rotated and occupy only a quarter atlas.',
    'World Position can cause textures to slide across moving geometry',
]


class SkillContractTest(unittest.TestCase):
    def setUp(self):
        self.text = (SKILL / 'SKILL.md').read_text()

    def test_severity_structure(self):
        self.assertEqual(self.text.count('<HARD-GATE>'), 2)
        self.assertEqual(self.text.count('<CRITICAL>'), 1)
        forbidden = self.text.count('Forbidden:') + sum(p.read_text().count('Forbidden:') for p in (SKILL / 'references').glob('*.md'))
        self.assertGreaterEqual(forbidden, 2)

    def test_referenced_modules_exist(self):
        for name in set(re.findall(r'references/([a-z_]+\.md)', self.text)):
            self.assertTrue((SKILL / 'references' / name).is_file(), name)

    def test_named_cli_commands_exist(self):
        commands = {('route', 'plan'), ('route', 'approve'), ('route', 'lint'), ('generate', 'clip'), ('generate', 'select'),
                    ('asset', 'approve'), ('asset', 'image3d'), ('qa', 'motion'), ('qa', 'collect'), ('project', 'validate'), ('asset', 'generate'), ('generate', 'control'),
                    ('generate', 'prompt'), ('subject', 'init'), ('subject', 'lint'), ('subject', 'trace'), ('subject', 'fit'), ('subject', 'from-dxf'),
                    ('subject', 'promote'), ('subject', 'exemplars'), ('shot', 'select'), ('workbench', 'start'), ('workbench', 'call'),
                    ('workbench', 'commit'), ('workbench', 'stop'), ('workbench', 'compare'), ('workbench', 'variants'), ('repair', 'status'), ('repair', 'reset'), ('api', 'search'), ('api', 'show')}
        for group, sub in commands:
            with self.subTest(command=f'{group} {sub}'):
                result = subprocess.run([sys.executable, '-m', 'studio', group, sub, '--help'], cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr[-300:])

    def test_craft_rules_moved_verbatim(self):
        craft = (SKILL / 'references/blender_craft.md').read_text()
        for sentence in BEFORE_PHASE_C:
            self.assertIn(sentence, craft)


if __name__ == '__main__':
    unittest.main()
