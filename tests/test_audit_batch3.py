"""2026-10-12 audit, third batch: names stay inside their folders, malformed input is a StudioError (or a coded
ValueError in Blender code) instead of a crash, state survives a refusal, caches are keyed by what made them, and the
judgments cover the cases they missed."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from studio.common import StudioError, json_arg, read_json, video_ranges, write_json

ROOT = Path(__file__).resolve().parents[1]


class NamesStayInsideTest(unittest.TestCase):
    def test_versions_and_beat_pictures_are_ids(self):
        from studio import characters, concept, sounds
        for load in (sounds.load, characters.load):
            with self.assertRaises(StudioError):
                load('sfx_confirm@../../x')
        with tempfile.TemporaryDirectory() as tmp:
            for ref in ('../../x/y', 'b1', 'b1/../p'):
                with self.assertRaises(StudioError):
                    concept._beat_source(Path(tmp), ref)

    def test_newest_version_is_by_number(self):
        from studio.blender_ops.version_order import newest
        self.assertEqual(newest(['v9', 'v10', 'v0002']), 'v10')
        self.assertEqual(newest([Path('blender-4.9.json'), Path('blender-4.10.json')]).name, 'blender-4.10.json')


class MalformedInputTest(unittest.TestCase):
    def test_one_range_per_video(self):
        self.assertEqual(video_ranges(['a', 'b'], None), [None, None])
        self.assertEqual(video_ranges(['a', 'b'], ['1,2', None]), [[1.0, 2.0], None])
        for bad in (['1,2'], ['2,1', None], ['x', None]):
            with self.assertRaises(StudioError):
                video_ranges(['a', 'b'], bad)

    def test_camera_keys_and_vertical_views(self):
        from studio.blender_ops.camera_frame_core import basis, screen_tilt_deg
        from studio.blender_ops.camera_moves_core import eased_progress
        with self.assertRaisesRegex(ValueError, 'FRAME_KEYS_COINCIDE'):
            eased_progress([0, 10, 10], 20)
        down, near = basis(90, 30), basis(89.99, 30)
        self.assertTrue(all(abs(a - b) < 1e-3 for a, b in zip(down[1], near[1])))   # the limit, not a division by zero
        self.assertIsNotNone(screen_tilt_deg((0, 0, 5), (0, 0, 0), (0, 1, 0)))

    def test_fill_layouts_and_levels(self):
        from studio.blender_ops.env_fill_core import layout_problems, level_layout, level_problems
        self.assertEqual(layout_problems({'item_id': 'k', 'layout': 'cluster', 'pitch_m': 2}), ['k: layout cluster needs count'])
        self.assertEqual(layout_problems({'item_id': 'r', 'layout': 'grid', 'pitch_m': 2}), [])
        with self.assertRaisesRegex(ValueError, 'FILL'):
            level_layout((0, 0, 10, 10), {'item_id': 'k', 'layout': 'cluster', 'pitch_m': 2})
        self.assertTrue(level_problems({'level_id': 'L1', 'z': 0, 'rects': []}))
        self.assertTrue(level_problems({'level_id': 'L1', 'z': 0, 'rects': [[5, 0, 1, 1]]}))
        self.assertEqual(level_problems({'level_id': 'L1', 'z': 0, 'rects': [[0, 0, 4, 9]], 'obstacles': [[1, 1, 0.3]]}), [])

    def test_layout_rows_and_edits(self):
        from studio import layout
        row = {'id': 'bay', 'repeat': {'counts': [1, 1, 3], 'pitch_m': [0, 0, 4]}, 'level_by_z': ['L1', 'L2']}
        with self.assertRaisesRegex(StudioError, 'level_by_z'):
            layout._expand([row], {})
        with self.assertRaisesRegex(StudioError, 'sets/x.json is not a valid scene'):   # a set row without its id: no KeyError
            layout._checked_scene({'primitives': [{'shape': 'box'}]}, 'sets/x.json')
        spec = read_json(ROOT / 'tests/fixtures/winch_spec/subjects/winch/spec.json')
        for edit in ({'op': 'set', 'path': '/identity'}, {'op': 'drop_parts'}):
            with self.assertRaisesRegex(StudioError, 'needs'):
                layout._edit(spec, [edit])

    def test_a_part_id_names_one_builder(self):
        from studio.subjects import lint_spec
        spec = read_json(ROOT / 'tests/fixtures/winch_spec/subjects/winch/spec.json')
        spec['builders'].append(dict(spec['builders'][0]))
        self.assertTrue(any('used by 2 builders' in e for e in lint_spec(spec)['errors']))

    def test_json_arguments_are_text_or_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(Path(tmp) / 'ops.json', [{'op': 'set'}])
            self.assertEqual(json_arg(str(Path(tmp) / 'ops.json')), [{'op': 'set'}])
        self.assertEqual(json_arg('[{"op": "set"}]'), [{'op': 'set'}])
        self.assertEqual(json_arg({'a': 1}), {'a': 1})
        with self.assertRaises(StudioError):
            json_arg('[{"op": ')


class StateTest(unittest.TestCase):
    def test_a_job_waiting_for_the_broker_is_active(self):
        from studio.jobs import ACTIVE_STATES
        self.assertIn('awaiting_dispatch', ACTIVE_STATES)

    def test_a_refused_delegation_keeps_the_users_words(self):
        from studio import decisions
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / 'delegation.json'
            write_json(pending, {'user_words': 'ok'})
            with mock.patch.object(decisions, 'PENDING_DELEGATION', pending), \
                    mock.patch.object(decisions, 'delegate', side_effect=StudioError('BUSY', 'held')):
                with self.assertRaises(StudioError):
                    decisions.claim_pending_delegation(tmp)
            self.assertTrue(pending.is_file())

    def test_only_failed_image_slots_get_a_new_attempt(self):
        from studio.generative.images import attempt_dest
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / 'k_0' / 'state.json', {'state': 'remote_failed'})
            write_json(root / 'k_1' / 'response.json', {'files': []})
            self.assertEqual(attempt_dest(root, 'k_0').name, 'k_0')                   # not asked: refused as before
            self.assertEqual(attempt_dest(root, 'k_0', True).name, 'k_0_r2')          # asked: a new attempt
            write_json(root / 'k_0_r2' / 'state.json', {'state': 'submitted'})
            self.assertEqual(attempt_dest(root, 'k_0', True).name, 'k_0_r2')          # in flight: never a third
            self.assertEqual(attempt_dest(root, 'k_1', True).name, 'k_1')             # made: reused, free


class CacheTest(unittest.TestCase):
    def test_grammar_caches_are_keyed_by_their_inputs(self):
        from studio.screen_grammar import _fresh, _stamp
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'motion.json'
            target.write_text('{}')
            self.assertFalse(_fresh(target, {'video': 'a'}))          # unkeyed, not vouched for: made again
            target.write_text('{}')
            self.assertTrue(_fresh(target, {'video': 'a'}, legacy_ok=True))
            self.assertTrue(_fresh(target, {'video': 'a'}))
            self.assertFalse(_fresh(target, {'video': 'b'}))          # another video: removed
            self.assertFalse(target.exists())
            target.write_text('{}'); _stamp(target, {'video': 'b'})
            self.assertTrue(_fresh(target, {'video': 'b'}))

    def test_the_edit_key_covers_the_chart_code(self):
        from studio.freeze import package_closure
        names = {p.name for p in package_closure(ROOT / 'studio/edit.py')}
        self.assertTrue({'edit.py', 'charts.py', 'titles.py'} <= names, sorted(names))


class JudgmentTest(unittest.TestCase):
    def test_subtitles_cover_line_breaks_and_long_runs(self):
        from studio.audio import CUE_CHARS, cue_chunks, make_cues
        text = '첫 문장입니다.\n둘째 줄은 조금 더 길게 써서 한 줄을 넘기도록 만든 문장입니다.\n' + 'x' * 70
        chunks = cue_chunks(text, 0, len(text))
        self.assertEqual(''.join(text[a:b] for a, b, _ in chunks), text)
        self.assertTrue(all(b - a <= CUE_CHARS + 1 or text[a:b].strip() != text[a:b] for a, b, _ in chunks))
        alignment = {'characters': list(text), 'character_start_times_seconds': [i * 0.1 for i in range(len(text))],
                     'character_end_times_seconds': [i * 0.1 + 0.1 for i in range(len(text))]}
        cues, warnings = make_cues(text, alignment, len(text) * 0.1, 30)
        self.assertGreater(len(cues), 3)
        self.assertTrue(any('cue_cut_inside_a_word' in w for w in warnings))
        import re
        for same in ('로켓이 똑바로 섭니다. 그런데 이 로켓은, 공중에서 멈출 수가 없습니다.',   # where the old rule covered the text,
                     'The first stage lands upright on a drone ship after a burn that slows it to zero.'):   # its chunks stay
            old = [(m.start(), m.end()) for m in re.finditer(r'.{1,28}(?:\s+|[.!?。]|$)', same)]
            self.assertEqual([(a, b) for a, b, _ in cue_chunks(same, 0, len(same))], old)

    def test_previews_follow_the_long_side(self):
        from studio.jobs import render_settings
        shot = {'render': {}}
        for size, want in (((1080, 1920), (360, 640)), ((1920, 1080), (640, 360)), ((1080, 1080), (640, 640))):
            project = {'output': {'width': size[0], 'height': size[1], 'fps': 30}}
            got = render_settings(project, shot, 'layout', [1])
            self.assertEqual((got['width'], got['height']) if 'width' in got else tuple(got['resolution']), want)


if __name__ == '__main__':
    unittest.main()
