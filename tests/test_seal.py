"""The sealed engine (studio/seal.py): outside the agent's sandbox only the user's approved copy of the code runs,
isolated from the checkout (python -I -B, its own pycache); code paths come from the engine, data from the checkout."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from studio import broker, freeze, seal
from studio.common import CODE, REPO, StudioError

CODE_HASH = re.compile(r"(file_hash|_sha|sha256)\(\s*REPO\s*/|relative_to\(REPO\)")   # a hash or a name of engine code taken under the data root
CODE_PATH = re.compile(r"REPO\)?\s*/\s*['\"](studio|schemas|scripts)\b")


class SealTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(seal, 'root', lambda: Path(self.tmp.name) / 'engine')
        patcher.start(); self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_code_comes_from_the_engine_and_data_from_the_checkout(self):
        self.assertEqual(CODE, REPO)   # one checkout, no launcher: the same folder
        # a path that runs or reads engine code; asking whether a folder lies in the checkout's own (is_relative_to) is a
        # question about the data root, and stays REPO
        found = [f'{p}:{n}' for p in [*CODE.joinpath('studio').rglob('*.py'), *CODE.joinpath('scripts').glob('*.py')]
                 for n, line in enumerate(p.read_text().splitlines(), 1)
                 for m in CODE_PATH.finditer(line) if not line[:m.start()].endswith('is_relative_to(')]
        self.assertEqual(found, [], 'engine code is found under CODE, never under the data root REPO')
        hashed = [f'{p}:{n}' for p in CODE.joinpath('studio').rglob('*.py') for n, line in enumerate(p.read_text().splitlines(), 1)
                  if CODE_HASH.search(line) and 'is_relative_to(REPO)' not in line and 'EXAMPLES' not in line]   # examples/ is data
        self.assertEqual(hashed, [], 'engine code is hashed and named under CODE (a path under REPO is data)')
        out = subprocess.run([sys.executable, '-c', 'from studio.common import CODE, REPO; print(CODE); print(REPO)'], cwd=CODE,
                             env={**os.environ, 'STUDIO_DATA_ROOT': self.tmp.name}, capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(out, [str(CODE), str(Path(self.tmp.name).resolve())])

    def test_code_hashes_read_the_engine_not_the_data_root(self):
        """Under a launcher the data root is the checkout and the code the sealed copy (2026-10-19: the edit's code hash
        made engine files relative to the checkout and failed; the render fingerprint hashed the checkout's files). With
        an empty data root every hash of engine code must still come out the same."""
        probe = ('from studio import edit, fidelity, freeze, jobs; import json; '
                 'print(json.dumps([edit._code_hash(), jobs.render_fingerprint({}, {}, [1]), fidelity.code_sha256(), freeze.code_groups()], '
                 'sort_keys=True))')
        here = subprocess.run([sys.executable, '-c', probe], cwd=CODE, capture_output=True, text=True, check=True).stdout
        apart = subprocess.run([sys.executable, '-c', probe], cwd=CODE, env={**os.environ, 'STUDIO_DATA_ROOT': self.tmp.name},
                               capture_output=True, text=True)
        self.assertEqual(apart.stdout, here, apart.stderr[-600:])

    def test_a_seal_is_a_hashed_copy_in_the_users_words_and_its_launcher_is_isolated(self):
        with self.assertRaises(StudioError):
            seal.launch_argv('studio', 'doctor')   # nothing sealed: nothing outside the sandbox may run
        made = seal.seal('봉인해')
        folder = Path(made['engine'])
        manifest = json.loads((folder / 'seal.json').read_text())
        self.assertEqual((manifest['user_words'], manifest['data_root']), ('봉인해', str(REPO.resolve())))
        self.assertEqual(seal.current(), folder)
        self.assertFalse(list(folder.rglob('__pycache__')))
        self.assertTrue((folder / 'studio' / '_launch.py').is_file() and (folder / 'scripts' / 'reel_agent.py').is_file())
        argv = seal.launch_argv('studio', 'doctor')
        self.assertEqual(argv[1:4], ['-I', '-B', '-X'])
        self.assertEqual(argv[4], f'pycache_prefix={folder / ".pyc"}')
        self.assertEqual(argv[5:], [str(folder / 'studio' / '_launch.py'), 'studio', 'doctor'])
        self.assertEqual(seal.status()['behind'], [])
        self.assertEqual(seal.groups_of(folder), freeze.code_groups())   # the sealed engine, run from its folder, lists its own code
        with self.assertRaises(StudioError):   # the checkout moved on since the user saw it: nothing is sealed
            seal.seal('봉인해', expect='0' * 16)
        self.assertEqual(seal.seal('봉인해', expect=seal.status()['checkout_digest'])['engine'], str(folder))
        with mock.patch.object(seal, 'engine_files', side_effect=lambda base=CODE: {**manifest['files'], 'studio/common.py': 'edited'}):
            self.assertEqual(seal.status()['behind'], ['studio/common.py'])
            self.assertTrue(seal.behind_warning().startswith('ENGINE_SEAL_BEHIND'))
        # the sealed engine runs from outside the checkout, isolated: its studio is its own, the data the checkout's
        done = subprocess.run([*argv[:-2], 'studio', 'seal', 'status'], cwd=tempfile.gettempdir(), capture_output=True, text=True, timeout=120)
        self.assertEqual(json.loads(done.stdout.splitlines()[-1])['ok'], True, done.stderr[-400:])
        probe = subprocess.run([sys.executable, '-I', '-B', '-c', 'import sys; print(sys.flags.isolated)'], capture_output=True, text=True)
        self.assertEqual(probe.stdout.strip(), '1')
        refused = subprocess.run([sys.executable, str(folder / 'studio' / '_launch.py'), 'studio', 'doctor'], capture_output=True, text=True)
        self.assertEqual(refused.returncode, 2)   # never without -I -B

    def test_freeze_record_seals_only_the_checkouts_own_baseline(self):
        with mock.patch.object(seal, 'seal', return_value={'engine': 'x'}) as sealed, \
                mock.patch.object(seal, 'groups_of', side_effect=lambda engine: freeze.code_groups()):   # the sealed engine's own list
            freeze.record('기준 기록', baseline=Path(self.tmp.name) / 'b.json')
            sealed.assert_not_called()
            with mock.patch.object(freeze, 'baseline_path', lambda: Path(self.tmp.name) / 'real.json'):
                out = freeze.record('기준 기록')
            sealed.assert_called_once_with('기준 기록', None)
        self.assertEqual(out['sealed'], {'engine': 'x'})

    def test_a_sealed_engine_is_checked_against_its_own_seal(self):
        """A later seal must not stop a run the older, also approved, engine is serving (2026-10-19)."""
        path = Path(self.tmp.name) / 'b.json'
        freeze.record('기준 기록', baseline=path)
        mine = freeze.code_groups()
        freeze.record_groups(path, mine, 'aaaaaaaaaaaaaaaa')
        newer = json.loads(json.dumps(mine))
        name = next(iter(newer['network_worker']))
        newer['network_worker'][name] = '0' * 64
        freeze.record_groups(path, newer, 'bbbbbbbbbbbbbbbb')   # the user sealed newer code while the old engine runs
        self.assertEqual(sorted(json.loads(path.read_text())['seals']), ['aaaaaaaaaaaaaaaa', 'bbbbbbbbbbbbbbbb'])
        with mock.patch.object(freeze, '_own_seal', return_value='aaaaaaaaaaaaaaaa'):
            self.assertEqual(freeze.require_code_frozen(path), [])
        for engine in (None, 'cccccccccccccccc'):   # the checkout, or an engine the baseline does not know: the latest seal
            with mock.patch.object(freeze, '_own_seal', return_value=engine), self.assertRaises(StudioError) as caught:
                freeze.require_code_frozen(path)
            self.assertEqual(caught.exception.code, 'FROZEN_CODE_CHANGED')
        freeze.record('기준 다시 기록', baseline=path)   # a new record keeps the seals
        self.assertEqual(sorted(json.loads(path.read_text())['seals']), ['aaaaaaaaaaaaaaaa', 'bbbbbbbbbbbbbbbb'])

    def test_the_broker_starts_commands_from_the_engine_it_runs(self):
        cmd = broker.command(['shot', 'build', '--project', 'p', '--shot', 's'])
        self.assertIn(str(CODE / 'studio' / '_launch.py'), cmd)
        self.assertEqual(cmd[cmd.index(str(CODE / 'studio' / '_launch.py')) - 5:cmd.index(str(CODE / 'studio' / '_launch.py')) - 2],
                         [sys.executable, '-I', '-B'])


if __name__ == '__main__':
    unittest.main()
