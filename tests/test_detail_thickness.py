"""A must-see part's thickness is one conservative rule for the camera and for the build's check (2026-10-17: an engine
bell read 34 px by the camera and 30 by the id pass under a 32 px minimum, so the build refused what the camera had
accepted; a column measure alone then read a face-on ring and a rail split by a post several times too thick).
frame_probe_core.thickness_px = the least of the column median extent, area over long side and short side, on the id
pass's grid; the camera rasterises its visible points on that same grid. Shapes, not the reel (N+1): a face-on ring, a
rail split by a post, a block with a thin arm, a wedge, a thin rail. Runs on the host (no numpy); the numpy twin of
the column measure is checked where numpy is (Blender's Python)."""
import importlib.util
from pathlib import Path
import sys
import unittest

OPS = Path(__file__).resolve().parents[1] / 'studio/blender_ops'
sys.path.insert(0, str(OPS))
import frame_probe_core as probe_core  # noqa: E402

SIZE = (1080, 1920)
GRID = probe_core.grid_size(SIZE)          # (144, 256): one cell is 7.5 output px
CELL = SIZE[1] / GRID[1]


def cells_where(test):
    """Cells of the id-pass grid whose centre (in output px) passes test(x, y)."""
    gw, gh = GRID
    return {(c, r) for c in range(gw) for r in range(gh) if test((c + 0.5) * SIZE[0] / gw, (r + 0.5) * SIZE[1] / gh)}


def points_of(cells, per=4):
    """Screen points (shares) spread inside the cells, `per` to a cell: what the camera's visible points rasterise as."""
    gw, gh = GRID
    out = []
    for c, r in cells:
        for k in range(per):
            out.append(((c + (k + 0.5) / per) / gw, (r + ((k * 3) % per + 0.5) / per) / gh, 1.0))
    return out


def both(cells):
    """(build, camera) thickness of a mask: the id pass's shape, and the camera's points rasterised on the same grid."""
    build = probe_core.thickness_px(probe_core.cells_shape(cells, GRID), SIZE)
    camera = probe_core.thickness_px(probe_core.cells_shape(probe_core.raster_cells(points_of(cells), GRID), GRID), SIZE)
    return build, camera


class ConservativeTest(unittest.TestCase):
    def test_a_face_on_ring_reads_its_band_not_its_diameter(self):
        cx, cy = 540, 960
        ring = cells_where(lambda x, y: 150 ** 2 <= (x - cx) ** 2 + (y - cy) ** 2 <= 180 ** 2)   # a 30 px band, 360 across
        build, camera = both(ring)
        self.assertEqual(build, camera)
        self.assertLess(build, 90)            # the column extent alone read ~ the diameter (~330)

    def test_a_rail_split_by_a_post_reads_the_rail(self):
        rail = cells_where(lambda x, y: 945 <= y <= 975 and 100 <= x <= 980 and not 500 <= x <= 580)   # 30 px, a gap
        post = cells_where(lambda x, y: 500 <= x <= 580 and 200 <= y <= 1700)
        build, camera = both(rail)
        self.assertEqual(build, camera)
        self.assertLess(build, 40)
        self.assertFalse(rail & post)

    def test_a_block_with_a_thin_arm_reads_its_thinnest_rule(self):
        block = cells_where(lambda x, y: 100 <= x <= 400 and 800 <= y <= 1100)       # 300 x 300
        arm = cells_where(lambda x, y: 400 < x <= 1000 and 940 <= y <= 970)         # 600 x 30 beside it
        build, camera = both(block | arm)
        self.assertEqual(build, camera)
        self.assertLess(build, 100)           # the arm's columns outnumber the block's: conservative, said in the report

    def test_a_wedge_reads_its_median_column(self):
        wedge = cells_where(lambda x, y: 100 <= x <= 900 and 960 - (x - 100) / 10 <= y <= 960 + (x - 100) / 10)   # 0..160 px
        build, camera = both(wedge)
        self.assertEqual(build, camera)
        self.assertTrue(60 <= build <= 100, build)

    def test_a_thin_rail_reads_its_thickness(self):
        rail = cells_where(lambda x, y: 945 <= y <= 975 and 100 <= x <= 980)
        build, camera = both(rail)
        self.assertEqual(build, camera)
        self.assertLessEqual(abs(build - 30), CELL, build)

    def test_rows_from_before_read_area_over_long_side_and_short_side(self):
        shape = {'bbox': [0.4, 0.4, 0.6, 0.6], 'share': 0.02}
        w, h = 0.2 * SIZE[0], 0.2 * SIZE[1]
        self.assertAlmostEqual(probe_core.thickness_px(shape, SIZE), min(0.02 * SIZE[0] * SIZE[1] / max(w, h), min(w, h)))

    def test_points_off_the_frame_are_left_out(self):
        fill = [(0.5 + dx / (GRID[0] * 4), 0.5 + dy / (GRID[1] * 4), 1) for dx in (0.5, 1.5, 2.5, 3.5) for dy in (0.5, 1.5, 2.5, 3.5)]
        self.assertEqual(probe_core.raster_cells([(-0.1, 0.5, 1), (1.2, 0.5, 1), *fill], GRID), {(72, 128)})

    def test_a_rod_grazing_cells_reads_as_a_render_reads_it_not_thicker(self):
        # a 32 px rod whose edges fall mid-cell: points touch 6 cells across, its pixel centres cover ~4
        top, bottom = 940.0, 972.0
        rod = [(x / SIZE[0], (top + (bottom - top) * ((k * 0.618) % 1.0)) / SIZE[1], 1.0)
               for k, x in enumerate(range(100, 980, 1)) for _ in range(1)] + \
              [(x / SIZE[0], (top + (bottom - top) * ((k * 0.382) % 1.0)) / SIZE[1], 1.0) for k, x in enumerate(range(100, 980, 1))]
        rendered = cells_where(lambda x, y: top <= y <= bottom and 100 <= x <= 980)
        camera = probe_core.thickness_px(probe_core.cells_shape(probe_core.raster_cells(rod, GRID), GRID), SIZE)
        build = probe_core.thickness_px(probe_core.cells_shape(rendered, GRID), SIZE)
        self.assertLessEqual(camera - build, CELL / 2 + 1e-6, (camera, build))


@unittest.skipUnless(importlib.util.find_spec('numpy') is not None, 'numpy: run under Blender\'s Python (see the module docstring)')
class NumpyTwinTest(unittest.TestCase):
    def test_the_id_pass_column_measure_is_the_cells_one(self):
        import numpy as np
        ring = cells_where(lambda x, y: 150 ** 2 <= (x - 540) ** 2 + (y - 960) ** 2 <= 180 ** 2)
        mask = np.zeros((GRID[1], GRID[0]), dtype=bool)
        for c, r in ring:
            mask[r, c] = True
        self.assertAlmostEqual(probe_core.column_thickness(mask), probe_core.cells_shape(ring, GRID)['col_v'])


if __name__ == '__main__':
    unittest.main()
