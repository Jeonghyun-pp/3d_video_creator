"""Model readers (screen_grammar.codex_json) run at most pool.readers() at a time on the machine - threads of one command
and other processes alike (2026-10-26: five codex readers at once failed and stalled; two at once ran)."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from studio import pool, screen_grammar
from studio.common import StudioError


class ReaderSlotsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_readers_default_to_two_and_the_setting_overrides(self):
        with patch.dict(os.environ, {pool.READERS_ENV: ''}):
            self.assertEqual(pool.readers(), 2)
        with patch.dict(os.environ, {pool.READERS_ENV: '1'}):
            self.assertEqual(pool.readers(), 1)

    def test_a_third_taker_waits_for_a_free_slot(self):
        inside, release, order = threading.Event(), threading.Event(), []

        def hold(n):
            with pool.slot('t', 2, folder=self.dir):
                order.append(f'in{n}')
                if len(order) == 2:
                    inside.set()
                release.wait(5)
        threads = [threading.Thread(target=hold, args=(n,)) for n in range(2)]
        for t in threads:
            t.start()
        self.assertTrue(inside.wait(5))
        with self.assertRaises(StudioError) as error:   # both slots held: the third finds none within its wait
            with pool.slot('t', 2, folder=self.dir, wait_s=0.3, poll_s=0.05):
                pass
        self.assertEqual(error.exception.code, 'BUSY')
        release.set()
        for t in threads:
            t.join()
        with pool.slot('t', 2, folder=self.dir, wait_s=1):   # freed: taken at once
            pass

    def test_codex_json_reads_inside_a_reader_slot(self):
        taken = []
        real = pool.slot

        def spy(name, count, **kw):
            taken.append((name, count))
            return real(name, count, folder=self.dir)

        def codex(cmd, **kw):
            self.assertEqual(taken, [('codex_read', 2)])   # the slot is held while the model runs
            Path(cmd[cmd.index('-o') + 1]).write_text('{"a": 1}')
            return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        with patch.dict(os.environ, {pool.READERS_ENV: ''}), patch('studio.outside_model.must_ask', return_value=False), \
                patch('studio.pool.slot', side_effect=spy), patch('studio.screen_grammar.subprocess.run', side_effect=codex):
            screen_grammar.codex_json([], 'q', {'type': 'object'}, self.dir / 'answer.json')
        self.assertEqual(taken, [('codex_read', 2)])

    def test_a_stalled_read_is_asked_again_once_then_fails_retryably(self):
        """2026-10-27: three locate reads in a row stalled at the 900 s limit (a trivial read answered in 7 s) and each
        crashed the whole locate as an INTERNAL_ERROR 'tool bug', its other readings lost."""
        import subprocess
        calls = []

        def stall_then(answer_on):
            def run(cmd, **kw):
                calls.append(kw['timeout'])
                if len(calls) < answer_on:
                    raise subprocess.TimeoutExpired(cmd, kw['timeout'])
                Path(cmd[cmd.index('-o') + 1]).write_text('{"a": 1}')
                return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
            return run
        with patch('studio.outside_model.must_ask', return_value=False), \
                patch('studio.screen_grammar.subprocess.run', side_effect=stall_then(2)):
            got = screen_grammar.codex_json([], 'q', {'type': 'object'}, self.dir / 'a1.json')
        self.assertEqual((got['answer'], len(calls)), ({'a': 1}, 2))
        calls.clear()
        with patch('studio.outside_model.must_ask', return_value=False), \
                patch('studio.screen_grammar.subprocess.run', side_effect=stall_then(99)), self.assertRaises(StudioError) as caught:
            screen_grammar.codex_json([], 'q', {'type': 'object'}, self.dir / 'a2.json')
        self.assertEqual((caught.exception.code, caught.exception.retryable, len(calls)), ('LABELLER_STALLED', True, 2))


if __name__ == '__main__':
    unittest.main()
