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
                                  'arguments': {'project': 'x', 'session': 'wb00000000', 'tool': 'apply_shot', 'args': {}}}},
                                 {'jsonrpc': '2.0', 'id': 4, 'method': 'nope'})
        self.assertEqual([r['id'] for r in replies], [1, 2, 3, 4])
        self.assertEqual(replies[0]['result']['serverInfo']['name'], 'studio-workbench')
        self.assertEqual({t['name'] for t in replies[1]['result']['tools']}, {'workbench_start', 'workbench_call', 'workbench_compare', 'workbench_commit', 'workbench_stop', 'studio_run'})
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


class WorkbenchLinesTest(unittest.TestCase):
    def test_host_plumbing_is_not_callable_by_an_agent(self):
        from studio.common import StudioError
        from studio import workbench
        for tool in ('apply_shot', 'current_shot', 'generated_snapshot', 'replay_snapshot'):
            with self.assertRaises(StudioError):
                workbench_mcp.run_tool('workbench_call', {'project': 'x', 'session': 'wb00000000', 'tool': tool})
        with self.assertRaises(StudioError) as caught:   # refused before the session is even looked up
            workbench.call('no_such_project', 'wb00000000', 'apply_shot', {'pristine': '/etc/passwd'})
        self.assertIn('internal', caught.exception.message)

    def test_a_failed_exec_still_blocks_the_commit(self):
        from studio.workbench import replayable
        self.assertFalse(replayable([{'tool': 'exec', 'ok': False, 'non_replayable': True, 'args': {'code': 'x = 1; raise ValueError'}}]))
        self.assertTrue(replayable([{'tool': 'exec', 'ok': False, 'non_replayable': False}]))   # refused before it ran
        self.assertFalse(replayable([{'tool': 'set_transform', 'ok': True, 'non_replayable': True}]))
        self.assertTrue(replayable([{'tool': 'set_transform', 'ok': True}]))

    def test_exec_is_open_for_exploring_and_never_committed(self):
        from studio import workbench
        names = {t['name']: t for t in workbench_mcp.TOOLS}
        self.assertIn('allow_exec', names['workbench_start']['inputSchema']['properties'])
        self.assertNotIn('exec', workbench_mcp.HIDDEN)
        import inspect
        self.assertNotIn('user_words', inspect.signature(workbench.start).parameters)
        self.assertFalse(workbench.replayable([{'tool': 'exec', 'ok': True}]))   # the line: such a session never commits


class WorkbenchShotEditTest(unittest.TestCase):
    def test_tool_list_comes_from_the_tool_table(self):
        from studio import workbench, workbench_mcp
        kinds = workbench.tool_kinds()
        self.assertEqual((kinds['set_shot_value'], kinds['apply_shot'], kinds['current_shot'], kinds['set_camera_rig']), ('write', 'write', 'read', 'write'))
        description = next(t for t in workbench_mcp.TOOLS if t['name'] == 'workbench_call')['description']
        self.assertIn('set_shot_value', description); self.assertIn('set_camera_rig', description)
        self.assertIn('exec', description.split('.')[0]); self.assertNotIn('apply_shot', description)   # exec explores; host plumbing stays hidden

    def test_author_scripts_are_traced_through_workbench_commits(self):
        import tempfile
        from pathlib import Path
        from studio.common import write_json
        from studio.project import init_project, shot_path
        from studio.workbench import _authored_by_script
        with tempfile.TemporaryDirectory() as root:
            p = Path(init_project('wbt', {'request': 'x', 'shots': [{'shot_id': 's', 'frame_count': 10}]}, root)['project_path'])
            versions = shot_path(p, 's').parent / 'versions'
            # author_job.json is every version's input record (failed ones too); 'record' marks a workbench commit.
            # v0006 is built before 2026-10-07: no 'record' in its job, the commit marker is in changes.json.
            rows = {'v0001': ({'script_path': 'author.py', 'base_version': None, 'record': {}}, None),
                    'v0002': ({'script_path': 'patch.py', 'base_version': 'v0001', 'record': {'workbench_session': 'wb1'}}, None),
                    'v0003': ({'script_path': None, 'base_version': None, 'record': {}}, None),
                    'v0004': ({'script_path': 'patch.py', 'base_version': 'v0003', 'record': {'workbench_session': 'wb2'}}, None),
                    'v0005': ({'script_path': 'patch.py', 'base_version': None, 'record': {'workbench_session': 'wb3'}}, None),
                    'failed_x': ({'script_path': 'author.py', 'base_version': None, 'record': {}}, None),
                    'v0006': ({'script_path': 'patch.py', 'base_version': 'v0001'}, {'workbench_session': 'wb4'})}
            for version, (job, changes) in rows.items():
                (versions / version).mkdir(parents=True)
                write_json(versions / version / 'author_job.json', job)
                if changes is not None:
                    write_json(versions / version / 'changes.json', changes)
            self.assertEqual([_authored_by_script(p, 's', v) for v in rows], [True, True, False, False, False, True, True])


if __name__ == '__main__':
    unittest.main()
