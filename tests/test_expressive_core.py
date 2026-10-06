"""Expressive settings as data (blender_ops/expressive_core.py): the schema takes them, nothing unread passes, every
value is reachable by the edit grammar, and the table of what the renderer overrides equals what it actually assigns."""
import ast
import copy
import unittest
from pathlib import Path

from studio.blender_ops import expressive_core as core

ROOT = Path(__file__).resolve().parents[1]
RENDER = {'grade': {'view_transform': 'Standard', 'exposure_offset_ev': 0.5, 'curve': [[0, 0], [0.5, 0.6], [1, 1]]},
          'compositor': {'ops': [{'op': 'glare', 'type': 'Bloom', 'threshold': 0.8, 'strength': 0.6}, {'op': 'soften', 'factor': 0.3}]},
          'engine_settings': {'cycles': {'max_bounces': 8, 'blur_glossy': 0.5}, 'render': {'use_motion_blur': True}},
          'addons': ['node_wrangler']}


def _assigned(path):
    """'owner.attr' for every attribute the renderer assigns (scene.render.x = ..., c.samples = ..., _set_enum(c, 'x', ...))."""
    def chain(n):
        parts = []
        while isinstance(n, ast.Attribute):
            parts.append(n.attr); n = n.value
        if isinstance(n, ast.Name):
            parts.append(n.id)
        return list(reversed(parts))
    alias = {'c': 'cycles'}
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        targets = node.targets if isinstance(node, ast.Assign) else []
        for t in targets:
            for e in (t.elts if isinstance(t, ast.Tuple) else [t]):
                c = chain(e)
                if len(c) >= 2 and (c[0] == 'scene' or c[0] in alias):
                    owner = alias.get(c[0], c[1] if c[0] == 'scene' else c[0])
                    if owner in ('render', 'cycles', 'eevee'):
                        out.add(f'{owner}.{c[-1]}')
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_set_enum' and isinstance(node.args[0], ast.Name):
            owner = alias.get(node.args[0].id)
            if owner:
                out.add(f'{owner}.{node.args[1].value}')
    return out


class ExpressiveCoreTest(unittest.TestCase):
    def test_declared_values_validate_and_unread_ones_do_not(self):
        self.assertEqual(core.unread(RENDER), [])
        bad = copy.deepcopy(RENDER)
        bad['grade']['contrast_boost'] = 1
        bad['compositor']['ops'].append({'op': 'glare', 'tresh': 1})
        bad['compositor']['ops'].append({'op': 'grain'})
        bad['engine_settings']['cycles']['samples'] = 64
        bad['engine_settings']['eevee'] = {'taa_render_samples': 16}
        rows = core.unread(bad)
        self.assertEqual(len(rows), 5, rows)
        self.assertTrue(any('samples' in r and 'render.samples' in r for r in rows))

    def test_renderer_owns_equals_what_the_renderer_assigns(self):
        assigned = _assigned(ROOT / 'studio/blender_ops/render_frames.py') | _assigned(ROOT / 'studio/blender_ops/render_profile.py')
        table = {f'{owner}.{key}' for owner, keys in core.RENDERER_OWNS.items() for key in keys}
        self.assertEqual(assigned, table)

    def test_engine_settings_never_overlap_what_the_renderer_owns(self):
        for owner, keys in core.ENGINE_SETTINGS.items():
            self.assertFalse(keys & set(core.RENDERER_OWNS.get(owner, {})), owner)

    def test_schema_and_shot_validation_take_them(self):
        from studio.common import read_json
        from studio.project import validate_shot
        shot = read_json(ROOT / 'tests/fixtures/jet_canyon_rig/shots/chase/shot.json')
        shot['render'] = {**shot['render'], **RENDER}
        validate_shot(shot)
        shot['render']['engine_settings']['cycles']['samples'] = 64
        from studio.common import StudioError
        with self.assertRaises(StudioError):
            validate_shot(shot)

    def test_every_value_is_reachable_by_path(self):
        from studio.storyboard import apply_ops
        from studio.common import read_json
        shot = read_json(ROOT / 'tests/fixtures/jet_canyon_rig/shots/chase/shot.json')
        shot['render'] = {**shot['render'], **copy.deepcopy(RENDER)}
        change, _ = apply_ops(shot, [{'op': 'set', 'path': '/render/engine_settings/cycles/max_bounces', 'value': 12},
                                  {'op': 'set', 'path': '/render/grade/exposure_offset_ev', 'delta': 0.25},
                                  {'op': 'set', 'path': '/render/compositor/ops/0/strength', 'factor': 0.5},
                                  {'op': 'add', 'path': '/render/compositor/ops/-', 'value': {'op': 'sharpen', 'factor': 0.2}}])
        edited = {**shot, **change}
        self.assertEqual(edited['render']['engine_settings']['cycles']['max_bounces'], 12)
        self.assertAlmostEqual(edited['render']['grade']['exposure_offset_ev'], 0.75)
        self.assertAlmostEqual(edited['render']['compositor']['ops'][0]['strength'], 0.3)
        self.assertEqual(edited['render']['compositor']['ops'][-1]['op'], 'sharpen')


if __name__ == '__main__':
    unittest.main()
