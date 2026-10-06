import io
import json
import unittest

from studio import workbench_mcp
from studio.workbench import effective_ops


class WorkbenchMcpTest(unittest.TestCase):
    def roundtrip(self, *messages):
        out = io.StringIO()
        workbench_mcp.serve(io.StringIO(''.join(json.dumps(m) + '\n' for m in messages)), out)
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_initialize_list_and_hidden_exec(self):
        replies = self.roundtrip({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}},
                                 {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                                 {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                                 {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'workbench_call',
                                  'arguments': {'project': 'x', 'session': 'wb00000000', 'tool': 'exec', 'args': {'code': 'pass'}}}},
                                 {'jsonrpc': '2.0', 'id': 4, 'method': 'nope'})
        self.assertEqual([r['id'] for r in replies], [1, 2, 3, 4])
        self.assertEqual(replies[0]['result']['serverInfo']['name'], 'studio-workbench')
        self.assertEqual({t['name'] for t in replies[1]['result']['tools']}, {'workbench_start', 'workbench_call', 'workbench_compare', 'workbench_commit', 'workbench_stop'})
        self.assertTrue(replies[2]['result']['isError'])
        self.assertIn('not available over MCP', replies[2]['result']['content'][0]['text'])
        self.assertEqual(replies[3]['error']['code'], -32601)

    def test_malformed_requests_never_stop_the_server(self):
        out = io.StringIO()
        lines = ['[]', '"x"', json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': ['not', 'an', 'object']}),
                 json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 5, 'arguments': []}}),
                 json.dumps({'jsonrpc': '2.0', 'id': 3, 'method': 'ping'})]
        workbench_mcp.serve(io.StringIO('\n'.join(lines) + '\n'), out)
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(len(replies), 5)
        self.assertEqual([r['error']['code'] for r in replies[:2]], [-32600, -32600])
        self.assertTrue(replies[2]['result']['isError']); self.assertTrue(replies[3]['result']['isError'])
        self.assertEqual(replies[4], {'jsonrpc': '2.0', 'id': 3, 'result': {}})


class EffectiveOpsTest(unittest.TestCase):
    def test_restore_rewinds_and_reads_drop(self):
        ops = [{'tool': 'build_subject', 'args': {'subject_id': 's'}, 'kind': 'write', 'ok': True},
               {'tool': 'measure', 'args': {}, 'kind': 'read', 'ok': True},
               {'tool': 'checkpoint', 'args': {'name': 'a'}, 'kind': 'control', 'ok': True},
               {'tool': 'set_spec_param', 'args': {'subject_id': 's', 'pointer': '/x', 'value': 1}, 'kind': 'write', 'ok': True},
               {'tool': 'restore', 'args': {'name': 'a'}, 'kind': 'control', 'ok': True},
               {'tool': 'set_transform', 'args': {'id': 'bad'}, 'kind': 'write', 'ok': False},
               {'tool': 'set_transform', 'args': {'id': 'Area'}, 'kind': 'write', 'ok': True}]
        self.assertEqual([o['tool'] for o in effective_ops(ops)], ['build_subject', 'set_transform'])

    def test_compare_hops_replay_the_current_state(self):
        w = lambda tool, **args: {'tool': tool, 'args': args, 'kind': 'write', 'ok': True}  # noqa: E731
        c = lambda tool, **args: {'tool': tool, 'args': args, 'kind': 'control', 'ok': True}  # noqa: E731
        ops = [w('set_camera_keys', keys='low'), c('variant_save', name='low'),
               w('set_camera_keys', keys='side'), c('variant_save', name='side'),
               c('variant_restore', name='low'),                       # back to the low chase
               w('set_transform', id='Area'),                          # current state = low + light
               c('checkpoint', name='__compare_return'),               # what workbench compare does:
               c('variant_restore', name='side'), c('variant_restore', name='low'),
               c('restore', name='__compare_return')]
        self.assertEqual([(o['tool'], o['args'].get('keys', o['args'].get('id'))) for o in effective_ops(ops)],
                         [('set_camera_keys', 'low'), ('set_transform', 'Area')])


if __name__ == '__main__':
    unittest.main()
