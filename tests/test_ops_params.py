"""Contrib mesh parts: the shading keys belong to the part, the rest to the entry (studio/blender._contrib_table)."""
import unittest
from pathlib import Path
from unittest import mock

from studio import blender
from studio.common import StudioError


def table_for(params):
    return {'contrib:slab@v001': {'dir': '/x', 'sha256': '0', 'kind': 'mesh', 'entry': 'slab', 'params': params}}


def spec(params):
    return {'subject_id': 's', 'builders': [{'part_id': 'p', 'builder': 'contrib:slab@v001', 'params': params}]}


class ContribShadingKeysTest(unittest.TestCase):
    def run_table(self, manifest_params, part_params):
        with mock.patch('studio.contrib.resolve', return_value=table_for(manifest_params)), \
                mock.patch('studio.contrib.refs_in', return_value={'contrib:slab@v001'}):
            return blender._contrib_table(Path('/p'), {}, {'s': spec(part_params)}, None)

    def test_shading_keys_pass_unknown_keys_refused(self):
        self.assertIn('contrib:slab@v001', self.run_table({'size': 0.1}, {'size': 0.2, 'smooth': True, 'sharp_angle_deg': 20}))
        with self.assertRaisesRegex(StudioError, 'does not read'):
            self.run_table({'size': 0.1}, {'sise': 0.2})

    def test_an_entry_may_not_claim_a_shading_key(self):
        with self.assertRaisesRegex(StudioError, 'shading keys'):
            self.run_table({'size': 0.1, 'smooth': True}, {})


if __name__ == '__main__':
    unittest.main()
