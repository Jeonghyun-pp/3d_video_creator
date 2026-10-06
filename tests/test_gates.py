"""Gate strictness (studio/gates.py): only listed taste gates can warn; everything else stays an error."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from studio.common import StudioError, read_json, write_json
from studio.gates import SOFTENABLE, is_error, severity_map
from studio.project import init_project

from tests.test_subject_spec import SPEC


class GateSeverityTest(unittest.TestCase):
    def test_default_is_strict_and_look_first_softens_only_the_list(self):
        self.assertTrue(all(v == 'error' for v in severity_map({}).values()))
        soft = severity_map({'policy': {'strictness': 'look-first'}})
        self.assertEqual(set(soft), set(SOFTENABLE)); self.assertTrue(all(v == 'warn' for v in soft.values()))
        self.assertTrue(is_error('BUDGET_EXCEEDED', soft) and is_error('TITLE_OUT_OF_SAFE', soft))   # never listed: always errors
        self.assertEqual(severity_map({'policy': {'strictness': 'look-first'}}, {'policy': {'strictness': 'explain-strict'}})['framing'], 'error')
        self.assertEqual(severity_map({'policy': {'gates': {'framing': 'warn'}}})['framing'], 'warn')
        with self.assertRaises(StudioError):
            severity_map({'policy': {'gates': {'FIDELITY_FAILED': 'warn'}}})               # a hard gate cannot be softened

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
        self.assertTrue(any('identity kinds' in e for e in lint(brief, self.project('explain-strict'))['errors']))
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
        self.assertFalse(build_report(schematic, geometry, self.project('explain-strict'))['passed'])



class GateSchemaTest(unittest.TestCase):
    def test_policy_gates_schema_names_exactly_the_softenable_gates(self):
        from studio.common import REPO, read_json
        from studio.gates import SOFTENABLE
        schema = read_json(REPO / 'schemas/studio-v1/project.schema.json')
        self.assertEqual(set(schema['properties']['policy']['properties']['gates']['propertyNames']['enum']), set(SOFTENABLE))

if __name__ == '__main__':
    unittest.main()
