from copy import deepcopy
from pathlib import Path
import math
import tempfile
import unittest

from PIL import Image, ImageDraw

from studio.common import StudioError
from studio.fidelity import datum_mask, mask_iou, model_to_px, px_to_model, silhouette_reference
from studio.subject_fit import dimension_penalty, free_parameters, nelder_mead
from studio.subject_trace import register_by_rule, trace_loft, trace_planform
from studio.subjects import lint_spec, resolve_pointer, set_pointer

PPM = 50.0
DATUM = {'px': [500.0, 200.0], 'model': [0.0, 0.0], 'u_dir': '-x', 'v_dir': '+y'}  # nose toward image left, like the P-51D drawing


def spec_with(builders, silhouettes=None, dimensions=None):
    return {'schema_version': 1, 'subject_id': 'rig', 'identity': 'test body', 'subject_mode': 'fictional', 'request': 'body',
            'request_trace': [{'phrase': 'body', 'items': ['body']}], 'sources': [{'id': 'draw', 'kind': 'drawing', 'license': 'CC0'}],
            'dimensions': dimensions or [], 'features': [{'id': 'f', 'description': 'body', 'part_ids': ['body'], 'verify': 'presence'}],
            'builders': builders, 'silhouettes': silhouettes or []}


def ellipse_rows(u0, u1, half):
    """(u, half-width) of an ellipse-ended body, for drawing."""
    return [(u0 + (u1 - u0) * k / 200, half * math.sqrt(max(0.0, 1 - ((2 * k / 200) - 1) ** 2))) for k in range(201)]


class NelderMeadTest(unittest.TestCase):
    def test_quadratic_minimum_and_bounds(self):
        x, fx, evals = nelder_mead(lambda p: (p[0] - 0.3) ** 2 + (p[1] - 0.7) ** 2, [0.9, 0.1], 300)
        self.assertLess(abs(x[0] - 0.3), 1e-3); self.assertLess(abs(x[1] - 0.7), 1e-3); self.assertLessEqual(evals, 300)
        x, _, _ = nelder_mead(lambda p: -p[0], [0.5], 100)  # optimum outside the box -> clipped to the bound
        self.assertAlmostEqual(x[0], 1.0, places=6)


class PointerAndLintTest(unittest.TestCase):
    def test_pointer_roundtrip(self):
        doc = {'a': [{'b': 1}, {'c': {'d/e': 2}}]}
        self.assertEqual(resolve_pointer(doc, '/a/1/c/d~1e'), 2)
        set_pointer(doc, '/a/0/b', 5); self.assertEqual(doc['a'][0]['b'], 5)

    def test_free_lint(self):
        builder = {'part_id': 'body', 'builder': 'box', 'params': {'size': [1, 2, 3]}, 'free': [{'pointer': '/params/size/1', 'min': 1, 'max': 4}]}
        self.assertEqual(lint_spec(spec_with([builder]))['errors'], [])
        self.assertEqual(free_parameters(spec_with([builder])), [('/builders/0/params/size/1', 1.0, 4.0)])
        bad = deepcopy(builder); bad['free'][0]['pointer'] = '/params/size/7'
        self.assertTrue(any('does not resolve' in e for e in lint_spec(spec_with([bad]))['errors']))
        bad = deepcopy(builder); bad['free'][0]['min'] = 3
        self.assertTrue(any('min <=' in e for e in lint_spec(spec_with([bad]))['errors']))

    def test_dimension_penalty_only_for_sourced(self):
        dims = [{'id': 'len', 'value_m': 10, 'tol_pct': 2, 'source_id': 'draw', 'measure': 'length'},
                {'id': 'wid', 'value_m': 1, 'tol_pct': 1, 'source_id': 'assumed', 'measure': 'width'}]
        spec = spec_with([{'part_id': 'body', 'builder': 'box', 'params': {}}], dimensions=dims)
        self.assertEqual(dimension_penalty(spec, {'whole': {'length': 10.1, 'width': 3}, 'parts': {}})[0], 0.0)
        penalty, broken = dimension_penalty(spec, {'whole': {'length': 11, 'width': 3}, 'parts': {}})
        self.assertEqual(broken, ['len']); self.assertAlmostEqual(penalty, 0.08)


class TraceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.project = Path(self.tmp.name)
        image = Image.new('L', (900, 400), 255); draw = ImageDraw.Draw(image)
        rows = ellipse_rows(-8.0, 0.0, 0.6)  # body from u=-8 (tail) to u=0 (nose), half-width 0.6
        outline = [model_to_px(DATUM, PPM, u, h) for u, h in rows] + [model_to_px(DATUM, PPM, u, -h) for u, h in reversed(rows)]
        draw.polygon(outline, fill=0)
        # a straight-tapered wing: LE u=-2.5 at root, sweep makes it -3.0 at v=3; chord 2.0 -> 1.0
        wing = [(-2.5, 0), (-3.0, 3), (-4.0, 3), (-4.5, 0), (-4.0, -3), (-3.0, -3)]
        draw.polygon([model_to_px(DATUM, PPM, u, v) for u, v in wing], fill=0)
        image.save(self.project / 'top.png')
        self.silhouette = {'view': 'top', 'image': 'top.png', 'source_id': 'draw', 'px_per_m': PPM, 'datum': DATUM}

    def tearDown(self):
        self.tmp.cleanup()

    def test_datum_roundtrip_and_iou(self):
        u, v = px_to_model(DATUM, PPM, *model_to_px(DATUM, PPM, -3.2, 1.1))
        self.assertAlmostEqual(u, -3.2); self.assertAlmostEqual(v, 1.1)
        ref = silhouette_reference(self.silhouette, self.project)
        tris = [[[-8, -0.6], [0, -0.6], [0, 0.6]], [[-8, -0.6], [0, 0.6], [-8, 0.6]]]
        self.assertGreater(mask_iou(datum_mask(tris, ref.size, DATUM, PPM), ref), 0.3)

    def test_geometry_beyond_the_drawing_counts(self):
        from PIL import Image
        from studio.fidelity import datum_iou
        datum = {'px': [10.0, 110.0], 'model': [0.0, 0.0], 'u_dir': '+x', 'v_dir': '-y'}
        reference = datum_mask([[[0, 0], [4, 0], [4, 2]], [[0, 0], [4, 2], [0, 2]]], (220, 120), datum, 50)  # 4 x 2 m, fills the frame
        taller = [[[0, 0], [4, 0], [4, 3]], [[0, 0], [4, 3], [0, 3]]]                                          # 4 x 3 m model
        self.assertAlmostEqual(datum_iou(taller, reference, datum, 50)[0], 2 / 3, delta=0.02)
        self.assertGreater(mask_iou(datum_mask(taller, reference.size, datum, 50), reference), 0.85)  # what clipping used to report (0.91)

    def test_loft_stations_and_occlusion(self):
        stations = [{'s': s, 'section': {'type': 'ellipse', 'a': 0.3, 'b': 0.3}} for s in (-0.4, -1.5, -3.5, -6.0, -7.6)]
        spec = spec_with([{'part_id': 'body', 'builder': 'loft', 'params': {'stations': stations}},
                          {'part_id': 'wing', 'builder': 'box', 'params': {}}], [self.silhouette])
        geometry = {'parts': {'body': {'bounds': {'x': [-0.3, 0.3], 'y': [-8, 0], 'z': [-0.3, 0.3]}},
                              'wing': {'bounds': {'x': [-3, 3], 'y': [-4.5, -2.5], 'z': [-0.1, 0.1]}}}}
        trace = trace_loft(spec, 'body', 'top', geometry, self.project)
        rows = {r['s']: r for r in trace['rows']}
        self.assertEqual(rows[-3.5]['status'], 'occluded'); self.assertEqual(rows[-3.5]['by'], ['wing'])
        for s in (-1.5, -6.0):
            expected = 0.6 * math.sqrt(1 - ((s + 4) / 4) ** 2)
            self.assertLess(abs(rows[s]['traced'] - expected), 1.5 / PPM, (s, rows[s]['traced'], expected))
            self.assertLess(abs(rows[s]['centre_traced']), 1.5 / PPM)

    def test_planform(self):
        spec = spec_with([{'part_id': 'body', 'builder': 'box', 'params': {}},
                          {'part_id': 'wing', 'builder': 'wing', 'params': {'span': 5.0, 'root_chord': 1.5, 'tip_chord': 1.5, 'sweep_deg': 0},
                           'transform': {'location': [0, -3.0, 0]}}], [self.silhouette])
        geometry = {'parts': {'wing': {'bounds': {'x': [-2.5, 2.5], 'y': [-4.2, -2.8], 'z': [0, 0.1]}}}}
        trace = trace_planform(spec, 'wing', 'top', geometry, self.project)
        patch = trace['patch']
        self.assertLess(abs(patch['/builders/1/params/root_chord'] - 2.0), 0.05)
        self.assertLess(abs(patch['/builders/1/params/tip_chord'] - 1.0), 0.05)
        self.assertLess(abs(patch['/builders/1/params/span'] - 6.0), 0.06)
        qc_sweep = math.degrees(math.atan2((-2.5 - 0.5) - (-3.0 - 0.25), 3))  # qc root -3.0, qc tip -3.25
        self.assertLess(abs(patch['/builders/1/params/sweep_deg'] - qc_sweep), 0.5)
        self.assertLess(abs(patch['/builders/1/transform/location'][1] - (-3.0)), 0.04)

    def test_register_by_rule_ignores_model_errors(self):
        silhouette = dict(self.silhouette); silhouette.pop('datum')
        silhouette['register'] = {'u': {'anchor': 'body/-y', 'image': '+x'}, 'v': {'symmetric': True}}
        spec = spec_with([{'part_id': 'body', 'builder': 'box', 'params': {}}], [silhouette])
        geometry = {'parts': {'body': {'bounds': {'x': [-0.4, 0.4], 'y': [-8.0, 0.0], 'z': [0, 1]}}}}
        registered = register_by_rule(spec, silhouette, geometry, [], self.project)
        datum = registered['datum']
        self.assertEqual((datum['u_dir'], datum['v_dir']), ('-x', '+y'))
        u, v = px_to_model(datum, PPM, *model_to_px(DATUM, PPM, -8.0, 0.0))  # true tail tip maps back to u=-8
        self.assertLess(abs(u + 8.0), 1.5 / PPM); self.assertLess(abs(v), 1.5 / PPM)
        silhouette['register']['u'] = {'anchor': 'body/-x', 'image': '+x'}
        with self.assertRaises(StudioError):
            register_by_rule(spec, silhouette, geometry, [], self.project)


if __name__ == '__main__':
    unittest.main()
