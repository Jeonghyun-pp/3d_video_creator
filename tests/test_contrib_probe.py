"""contrib_probe (SKILL #8): a manifest param that changes nothing in the entry's output is found - in an isolated
interpreter, on the hash-checked bytes - refused for a draft, warned for a promoted version, cached by hash."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from studio import blender, contrib_probe
from studio.blender_ops.contrib_loader import code_sha
from studio.common import StudioError

SLAB = '''def slab(size=0.1, rib=0.01, ribs=3, hollow=False):
    h = size / 2
    v = [(x, y, z) for x in (-h, h) for y in (-h, h) for z in (-h, h)]
    f = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    if hollow:
        v = [(x * 0.9, y, z) for x, y, z in v]
    return v, f
'''
LAW = '''def lift(x, lift=0.008, duration_deg=240.0):
    import math
    half = duration_deg / 2
    return lift * max(0.0, math.cos(math.pi / 2 * min(1.0, abs(x % 360 - 180) / half)))
'''


def entry(root, name, source, manifest):
    folder = Path(root) / name
    folder.mkdir()
    (folder / 'impl.py').write_text(source)
    (folder / 'manifest.json').write_text(json.dumps(manifest))
    return {'dir': str(folder), 'sha256': code_sha(folder), 'kind': manifest['kind'], 'entry': manifest['entry'], 'params': manifest['params']}


class ContribProbeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(contrib_probe, 'CACHE', Path(self.tmp.name) / 'cache.json')
        patcher.start(); self.addCleanup(patcher.stop)
        self.slab = entry(self.tmp.name, 'slab', SLAB, {'kind': 'mesh', 'name': 'slab', 'entry': 'slab', 'words': ['slab'],
                                                        'params': {'size': 0.1, 'rib': 0.01, 'ribs': 3, 'hollow': False}})
        self.law = entry(self.tmp.name, 'lift', LAW, {'kind': 'coupling', 'name': 'lift', 'entry': 'lift', 'words': ['cam'],
                                                      'params': {'lift': 0.008, 'duration_deg': 240.0}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_finds_ignored_params_of_any_kind(self):
        self.assertEqual(contrib_probe.probe(self.slab)['unused'], ['rib', 'ribs'])   # size and hollow shape it
        self.assertEqual(contrib_probe.probe(self.law)['unused'], [])

    def test_changed_bytes_are_never_run_and_results_are_cached(self):
        self.assertIn('changed', contrib_probe.probe({**self.slab, 'sha256': '0' * 64})['error'])
        contrib_probe.probe(self.slab)
        with mock.patch('subprocess.run', side_effect=AssertionError('the cache should answer')):
            self.assertEqual(contrib_probe.probe(self.slab)['unused'], ['rib', 'ribs'])

    def test_build_refuses_a_draft_and_warns_for_a_promoted_version(self):
        spec = {'subject_id': 's', 'builders': [{'part_id': 'p', 'builder': 'contrib:slab@draft', 'params': {}}]}
        for draft in (True, False):
            table = {'contrib:slab@draft': {**self.slab, 'draft': draft}}
            with mock.patch('studio.contrib.resolve', return_value=table), mock.patch('studio.contrib.refs_in', return_value=set(table)):
                warnings = []
                if draft:
                    with self.assertRaises(StudioError) as caught:
                        blender._contrib_table(Path('/p'), {}, {'s': spec}, None, warnings)
                    self.assertEqual(caught.exception.code, 'CONTRIB_PARAM_UNUSED')
                else:
                    blender._contrib_table(Path('/p'), {}, {'s': spec}, None, warnings)
                    self.assertTrue(warnings and warnings[0].startswith('CONTRIB_PARAM_UNUSED') and "'rib'" in warnings[0])


if __name__ == '__main__':
    unittest.main()
