"""Independent work at the same time (studio/pool.py): a bounded pool whose size is read from the machine (cores, free
memory) or set (STUDIO_WORKERS); results come back in the order asked, one item's failure never stops the others; and
the lock files threads of one command share are taken in turn, while another process still finds them held."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from studio import pool
from studio.common import StudioError, lock


class PoolTest(unittest.TestCase):
    def test_results_in_the_order_asked_and_at_the_same_time(self):
        def slow(n):
            time.sleep(0.2 if n == 0 else 0.0)
            return n * 10
        started = time.monotonic()
        with patch.dict(os.environ, {pool.ENV: '4'}):
            got = pool.each([0, 1, 2, 3], slow)
        self.assertEqual([r.value for r in got], [0, 10, 20, 30])
        self.assertLess(time.monotonic() - started, 0.5)

    def test_one_failure_does_not_stop_the_others(self):
        def step(n):
            if n == 1:
                raise StudioError('X', 'no')
            return n
        with patch.dict(os.environ, {pool.ENV: '2'}):
            got = pool.each([0, 1, 2], step)
        self.assertEqual([r.value for r in got], [0, None, 2])
        self.assertEqual(got[1].error.code, 'X')

    def test_never_more_at_once_than_the_limit(self):
        now, most, guard = [0], [0], threading.Lock()

        def step(n):
            with guard:
                now[0] += 1; most[0] = max(most[0], now[0])
            time.sleep(0.05)
            with guard:
                now[0] -= 1
        with patch.dict(os.environ, {pool.ENV: '2'}):
            pool.each(range(6), step)
        self.assertEqual(most[0], 2)

    def test_the_limit_is_read_from_the_machine_or_set(self):
        with patch.dict(os.environ, {pool.ENV: ''}), patch('os.cpu_count', return_value=10), \
                patch('studio.pool.free_memory_gb', return_value=64.0):
            self.assertEqual(pool.limit(), pool.MOST)   # cores and memory to spare: the measured best
        with patch.dict(os.environ, {pool.ENV: ''}), patch('os.cpu_count', return_value=10), \
                patch('studio.pool.free_memory_gb', return_value=pool.GB_PER_WORKER * 2 + 0.5):
            self.assertEqual(pool.limit(), 2)           # memory bounds it
        with patch.dict(os.environ, {pool.ENV: ''}), patch('os.cpu_count', return_value=2), \
                patch('studio.pool.free_memory_gb', return_value=64.0):
            self.assertEqual(pool.limit(), 1)           # half the cores: Blender threads itself
        with patch.dict(os.environ, {pool.ENV: ''}), patch('studio.pool.free_memory_gb', return_value=None):
            self.assertGreaterEqual(pool.limit(), 1)    # memory unknown: the cores alone
        with patch.dict(os.environ, {pool.ENV: '3'}):
            self.assertEqual(pool.limit(), 3)           # the setting wins
        with patch.dict(os.environ, {pool.ENV: 'many'}), patch('studio.pool.free_memory_gb', return_value=0.1):
            self.assertEqual(pool.limit(), 1)           # an unreadable setting falls back to the machine

    def test_free_memory_is_read_on_this_machine(self):
        got = pool.free_memory_gb()
        self.assertTrue(got is None or got > 0)


class LockTurnsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / '.project.lock'

    def tearDown(self):
        self.tmp.cleanup()

    def test_threads_of_one_process_take_turns(self):
        order = []

        def hold(name):
            with lock(self.file, blocking=False):
                order.append(name + '+'); time.sleep(0.1); order.append(name + '-')
        threads = [threading.Thread(target=hold, args=(n,)) for n in 'ab']
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(order), 4)
        self.assertEqual(order[0][0], order[1][0])   # one held it whole, then the other: no BUSY, no overlap

    def test_the_same_thread_taking_it_again_finds_it_held(self):
        with lock(self.file, blocking=False):
            with self.assertRaises(StudioError) as caught:
                with lock(self.file, blocking=False):
                    pass
        self.assertEqual(caught.exception.code, 'BUSY')
        with lock(self.file, blocking=False, reentrant=True):
            with lock(self.file, blocking=False, reentrant=True):
                pass

    def test_another_process_holding_it_is_still_busy(self):
        code = ('import fcntl,sys,time; s=open(sys.argv[1],"a"); fcntl.flock(s, fcntl.LOCK_EX); print("held", flush=True); '
                'time.sleep(3)')
        child = subprocess.Popen([sys.executable, '-c', code, str(self.file)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'held')
            with self.assertRaises(StudioError) as caught:
                with lock(self.file, blocking=False):
                    pass
            self.assertEqual(caught.exception.code, 'BUSY')
        finally:
            child.kill(); child.wait(); child.stdout.close()


if __name__ == '__main__':
    unittest.main()
