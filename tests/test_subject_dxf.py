from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from studio.asset_factory.factory import cad_python
from studio.common import read_json, write_json
from studio.fidelity import datum_mask, mask_iou, silhouette_reference

ROOT = Path(__file__).resolve().parents[1]
MAKE_DXF = '''
import sys, ezdxf
doc = ezdxf.new(); doc.header['$INSUNITS'] = 4  # mm
msp = doc.modelspace()
msp.add_lwpolyline([(0, 0), (6000, 0), (6000, 3000), (0, 3000)], close=True, dxfattribs={'layer': 'WALL'})
msp.add_lwpolyline([(1000, 0), (2000, 0), (2000, 2100), (1000, 2100)], close=True, dxfattribs={'layer': 'WALL'})
msp.add_lwpolyline([(3000, 1000), (5000, 1000), (5000, 2200), (3000, 2200)], close=True, dxfattribs={'layer': 'WALL'})
msp.add_line((0, 0), (10, 10), dxfattribs={'layer': 'WALL'})
doc.saveas(sys.argv[1])
'''


@unittest.skipUnless(cad_python().is_file(), 'CAD interpreter (.venvs/cad) not installed')
class DxfTest(unittest.TestCase):
    def test_wall_from_dxf_is_exact(self):
        from studio.subject_dxf import from_dxf
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / 'p'
            shutil.copytree(ROOT / 'tests/fixtures/winch_spec', project, ignore=shutil.ignore_patterns('shots', 'renders', 'workbench', 'runs'))
            dxf = Path(tmp) / 'wall plan.dxf'
            subprocess.run([str(cad_python()), '-I', '-c', MAKE_DXF, str(dxf)], check=True)
            result = from_dxf(project, 'winch', dxf, 'WALL', view='front', license='own drawing', apply=True)
            self.assertEqual(result['units_scale_m'], 0.001)
            self.assertEqual(result['holes'], 2); self.assertEqual(result['skipped_open_entities'], 1)
            self.assertEqual(result['wall'], {'length': 6.0, 'height': 3.0, 'openings': [{'x': 1.0, 'z': 0.0, 'w': 1.0, 'h': 2.1},
                                                                                        {'x': 3.0, 'z': 1.0, 'w': 2.0, 'h': 1.2}]})
            spec = read_json(project / 'subjects/winch/spec.json')
            silhouette = next(s for s in spec['silhouettes'] if s['view'] == 'front')
            self.assertTrue(any(s['kind'] == 'cad' for s in spec['sources']))
            # the exact model of that wall, drawn through the datum, must cover the reference almost perfectly
            reference = silhouette_reference(silhouette, project)
            tris = [[[0, 0], [6, 0], [6, 3]], [[0, 0], [6, 3], [0, 3]]]
            model = datum_mask(tris, reference.size, silhouette['datum'], silhouette['px_per_m'])
            holes = datum_mask([[[1, 0], [2, 0], [2, 2.1]], [[1, 0], [2, 2.1], [1, 2.1]], [[3, 1], [5, 1], [5, 2.2]], [[3, 1], [5, 2.2], [3, 2.2]]],
                               reference.size, silhouette['datum'], silhouette['px_per_m'])
            from PIL import ImageChops
            self.assertGreater(mask_iou(ImageChops.subtract(model, holes), reference), 0.995)


if __name__ == '__main__':
    unittest.main()
