"""A model read's first ask is cut off at its kind's limit, taken from the answered reads on record (library/read_limits.json),
and the read asked again with the full limit. Why (2026-10-26/27): every stall on record was a locate read hanging to the
900 s limit (nine, 15 min each) while no locate read since the reader cap took more than 64 s to answer."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from studio import read_limits, screen_grammar
from studio.common import REPO


def row(kind, seconds, ok=True, at='2026-10-27T10:00:00+00:00', target='read_1.json'):
    return {'id': 'x', 'parent': 'p', 'at': at, 'kind': 'model', 'command': f'model {kind}/{target}', 'seconds': seconds, 'ok': ok}


RULE = {'since': '2026-10-26T15:48:41+00:00', 'margin': 2, 'floor_s': 60, 'min_n': 3, 'full_s': 900}


class ReadLimitsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_kind_of_a_read_is_the_folder_its_answer_goes_to_without_its_picture_and_try(self):
        self.assertEqual(read_limits.kind_of('/p/shots/c01/frames/ab/locate/read_last_2.json'), 'locate')
        self.assertEqual(read_limits.kind_of('/p/shots/c01/frames/ab/checks/first_1_separate/locate.json'), 'separate')
        self.assertEqual(read_limits.kind_of('/p/runs/plan_read/read_1d42.json'), 'plan_read')

    def test_a_limit_is_the_margin_over_the_slowest_answered_read_of_its_kind_since_the_rule_began(self):
        rows = [row('locate', s) for s in (10, 20, 64.3)] + [row('locate', 900, ok=False)] \
            + [row('locate', 400, at='2026-10-26T13:20:00+00:00')] \
            + [row('checks', s) for s in (5, 6, 7)] + [row('judge', 80)] \
            + [{**row('x', 50), 'command': 'image c01/first_1.png'}]   # a picture is no read
        table = read_limits.calibrate(rows, RULE)
        self.assertEqual(table['kinds']['locate']['limit_s'], 129)   # 2 x 64.3; the 900 s stall and the read before the cap are not answers since
        self.assertEqual((table['kinds']['locate']['n'], table['kinds']['locate']['max_s'], table['kinds']['locate']['stalls']), (3, 64.3, 1))
        self.assertEqual(table['kinds']['checks']['limit_s'], 60)    # never below the floor
        self.assertNotIn('judge', table['kinds'])                    # too few answered reads to know its slowest: the full limit holds
        self.assertEqual(table['rule'], RULE)

    def test_a_read_started_before_the_rule_began_is_not_evidence(self):
        late_end = row('locate', 600, at='2026-10-26T15:55:00+00:00')   # ended after, started before the cap
        table = read_limits.calibrate([late_end] + [row('locate', s) for s in (10, 11, 12)], RULE)
        self.assertEqual(table['kinds']['locate']['max_s'], 12)

    def test_a_kind_without_a_row_gets_the_full_limit(self):
        table = {'rule': RULE, 'kinds': {'locate': {'limit_s': 129}}}
        self.assertEqual(read_limits.first_limit('/x/locate/read_1.json', 900, table), 129)
        self.assertEqual(read_limits.first_limit('/x/see/blind_1.json', 900, table), 900)
        self.assertEqual(read_limits.first_limit('/x/locate/read_1.json', 100, table), 100)   # never above the full limit

    def test_the_asks_before_the_last_stop_at_the_kinds_limit_and_the_last_has_the_full_limit(self):
        timeouts = []

        def run(cmd, **kw):
            timeouts.append(kw['timeout'])
            if len(timeouts) < screen_grammar.READ_ASKS:
                raise subprocess.TimeoutExpired(cmd, kw['timeout'])
            Path(cmd[cmd.index('-o') + 1]).write_text('{"a": 1}')
            return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        table = {'rule': RULE, 'kinds': {'locate': {'limit_s': 129}}}
        with patch('studio.outside_model.must_ask', return_value=False), patch('studio.read_limits.table', return_value=table), \
                patch('studio.screen_grammar.subprocess.run', side_effect=run):
            got = screen_grammar.codex_json([], 'q', {'type': 'object'}, self.dir / 'locate' / 'read_1.json')
        self.assertEqual((got['answer'], timeouts), ({'a': 1}, [129] * (screen_grammar.READ_ASKS - 1) + [screen_grammar.READ_TIMEOUT_S]))

    def test_the_library_table_holds_its_evidence_and_never_cuts_an_answered_read_on_record(self):
        table = json.loads((REPO / 'library' / 'read_limits.json').read_text())
        self.assertTrue(table['why'] and table['rule']['since'] and table['calibration']['at'])
        for kind, got in table['kinds'].items():
            self.assertGreaterEqual(got['n'], table['rule']['min_n'], kind)
            self.assertGreaterEqual(got['limit_s'], max(table['rule']['margin'] * got['max_s'], table['rule']['floor_s']) - 1, kind)
            self.assertLessEqual(got['limit_s'], table['rule']['full_s'], kind)


if __name__ == '__main__':
    unittest.main()
