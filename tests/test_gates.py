"""Gate strictness (studio/gates.py): taste gates warn by default, broken and budget gates block; nothing unlisted can warn."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from studio.common import StudioError, read_json, write_json
from studio.gates import SOFTENABLE, is_error, severity_map
from studio.project import init_project

from tests.test_subject_spec import SPEC


class GateSeverityTest(unittest.TestCase):
    def test_default_blocks_broken_and_budget_and_warns_on_taste(self):
        from studio.gates import KINDS, SOFTENABLE as table
        self.assertTrue(all(kind in KINDS and reason for kind, reason in table.values()))   # every softenable gate says what it is
        default = severity_map({})
        self.assertEqual({c for c, v in default.items() if v == 'error'}, {c for c, (k, _) in table.items() if k in ('broken', 'budget')})
        self.assertEqual(default['KEY_PART_SMALL'], 'warn'); self.assertEqual(default['look_scale'], 'error')
        soft = severity_map({'policy': {'strictness': 'look-first'}})
        self.assertEqual(set(soft), set(SOFTENABLE)); self.assertTrue(all(v == 'warn' for v in soft.values()))
        self.assertTrue(all(v == 'error' for v in severity_map({'policy': {'strictness': 'all-strict'}}).values()))
        self.assertTrue(is_error('BUDGET_EXCEEDED', soft) and is_error('TITLE_OUT_OF_SAFE', soft))   # never listed: always errors
        self.assertEqual(severity_map({'policy': {'strictness': 'look-first'}}, {'policy': {'strictness': 'all-strict'}})['framing'], 'error')
        self.assertEqual(severity_map({'policy': {'gates': {'framing': 'error'}}})['framing'], 'error')   # a project can tighten one
        with self.assertRaises(StudioError):
            severity_map({'policy': {'gates': {'FIDELITY_FAILED': 'warn'}}})               # a hard gate cannot be softened

    def test_engine_run_failures_rejudged_are_warnings(self):
        """The three builds engine_cutaway2 discarded (2026-10-07) failed on taste gates only: under the default they
        build with warnings. Codes copied from their frame_report.json gate_failures."""
        discarded = {'s01 failed_yux_78rq': ['KEY_PART_SMALL'], 's02 failed_k76i254z': ['KEY_PART_SMALL', 'KEY_PART_SMALL'],
                     's03 failed_hdoai5c1': ['FRAME_EDGE_CUT', 'KEY_PART_SMALL', 'KEY_PART_SMALL']}
        default = severity_map({})
        self.assertFalse([c for codes in discarded.values() for c in codes if is_error(c, default)])

    def project(self, strictness):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        path = Path(init_project('gates', {'request': 'gates', 'shots': [{'shot_id': 's', 'frame_count': 30}]}, tmp.name)['project_path'])
        project = read_json(path / 'project.json'); project['policy'] = {'strictness': strictness}; write_json(path / 'project.json', project)
        return path

    def test_fill_lint_identity_kinds_warn_under_look_first(self):
        from studio.fill import lint
        brief = {'status': 'proposed', 'topic': 't', 'request_trace': [], 'levels': [{'level_id': 'B1', 'items': [
            {'item_id': f'i{k}', 'role': 'identity', 'element': e, 'layout': 'along_edge', 'count': 2, 'why': 'cue', 'source': 'agent'}
            for k, e in enumerate(('bus', 'taxi', 'car'))]}]}
        self.assertTrue(any('identity kinds' in e for e in lint(brief, self.project('all-strict'))['errors']))
        relaxed = lint(brief, self.project('look-first'))
        self.assertFalse(any('identity kinds' in e for e in relaxed['errors']))
        self.assertTrue(any('identity kinds' in w for w in relaxed['warnings']))

    def test_shape_misses_warn_only_for_schematic_subjects(self):
        from studio.fidelity import build_report
        geometry = {'whole': {'length': 8.8, 'width': 11.2}, 'parts': {'fuselage': {'objects': 1, 'features': []},
                    'radiator': {'objects': 1, 'features': ['feat.radiator']}, 'blade': {'objects': 4, 'features': ['feat.prop']}},
                    'silhouettes': {}, 'screen_px': {}}
        relaxed = self.project('look-first')
        self.assertIn('dimension dim.length', ' '.join(build_report(SPEC, geometry, relaxed)['failures']))     # real: stays an error
        schematic = deepcopy(SPEC); schematic['subject_mode'] = 'schematic'
        report = build_report(schematic, geometry, relaxed)
        self.assertTrue(report['passed']); self.assertIn('dimension dim.length', ' '.join(report['advisories']))
        self.assertFalse(build_report(schematic, geometry, self.project('all-strict'))['passed'])



class GateSchemaTest(unittest.TestCase):
    def test_policy_gates_schema_names_exactly_the_softenable_gates(self):
        from studio.common import REPO, read_json
        from studio.gates import SOFTENABLE
        schema = read_json(REPO / 'schemas/studio-v1/project.schema.json')
        self.assertEqual(set(schema['properties']['policy']['properties']['gates']['propertyNames']['enum']), set(SOFTENABLE))

if __name__ == '__main__':
    unittest.main()
