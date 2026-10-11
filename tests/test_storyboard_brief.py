"""storyboard brief (studio/storyboard_brief.py): each shot in words - its size, angle and place at the first and last
moment, the camera's move, the join from the shot before and where the shot list's plan was left (2026-10-11: "실제
어떤 화면 구도들로 영상이 이루어질지에 대한 브리핑이 더 상세해야"). Pure functions on recorded rows; no Blender."""
import unittest

from studio import storyboard_brief as sb


def row(frame, h, centre, view, position, aim, box=None, lens=24.0):
    return {'frame': frame, 'lens_mm': lens, 'view': view, 'position': position, 'aim': aim,
            'achieved': {'height_share': h, 'centre': centre, 'box': box or [centre[0] - 0.1, centre[1] - h / 2, centre[0] + 0.1, centre[1] + h / 2]}}


class WordsTest(unittest.TestCase):
    def test_size_classes_by_height_past_the_frame(self):
        self.assertEqual([sb.size_class(h) for h in (0.05, 0.15, 0.3, 0.6, 1.2, 2.5)],
                         ['원경', '전체', '전체', '꽉 참', '클로즈업', '익스트림 클로즈업'])
        self.assertIsNone(sb.size_class(None))

    def test_place_and_angle_words(self):
        self.assertEqual(sb.place_words([0.1, 0.1]), '왼쪽 위')
        self.assertEqual(sb.place_words([0.5, 0.5]), '가운데')
        self.assertEqual(sb.place_words([0.9, 0.5]), '오른쪽')
        self.assertEqual(sb.place_words([0.5, 1.4]), '가운데 아래')   # a centre off the frame reads as its edge
        self.assertEqual(sb.angle_words(-55), '올려다봄 55°')      # + looks down, - looks up (frame_targets)
        self.assertEqual(sb.angle_words(20), '내려다봄 20°')
        self.assertTrue(sb.angle_words(3).startswith('눈높이'))
        self.assertEqual(sb.cut_edges([-0.1, 0.2, 0.8, 1.3]), ['왼쪽', '아래'])

    def test_move_words_say_push_turn_rise_follow_and_pace(self):
        a = sb._moment(row(0, 0.3, [0.5, 0.5], [-25.0, 20.0], [20.0, 0.0, 0.0], [0.0, 0.0, 0.0]))
        b = sb._moment(row(89, 0.6, [0.5, 0.5], [-9.0, 38.0], [10.0, 0.0, 0.0], [0.0, 0.0, 0.0]))
        words = sb.move_words(a, b, {'path': 'arc', 'follow': 0.5, 'pacing': 'subject'})
        for part in ('둘레로', '다가감 (20→10 m)', '오른쪽으로 18° 돎', '카메라가 올라감', '50%를 따라감', '대상에 맞춤'):
            self.assertIn(part, words)
        still = sb.move_words(a, a, {'path': 'straight', 'follow': 0.0, 'move': {'timing': {'profile': 'ease_in_out'}}})
        self.assertIn('거리 유지', still)
        self.assertIn('제자리', still)
        self.assertIn('부드럽게', still)

    def test_any_move_falls_back_to_the_rig_samples(self):
        r = sb._rig_row({'frame': 0, 'camera': [10.0, 0.0, 5.0], 'lens_mm': 45, 'pitch_deg': -3.0, 'target_box': [0.3, -0.1, 0.7, 0.7]}, [0.0, 0.0, 5.0])
        m = sb._moment(r)
        self.assertEqual((m['view'][0], round(m['view'][1]), m['size'], m['cut']), (3.0, 0, '꽉 참', ['위']))


class JoinTest(unittest.TestCase):
    def card(self, sid, first_h, last_h, plan=None):
        mk = lambda h: sb._moment(row(0, h, [0.5, 0.5], [0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 0.0]))   # noqa: E731
        return {'shot_id': sid, 'first': mk(first_h), 'last': mk(last_h), 'plan': plan or {}, 'subject': ['booster']}

    def test_the_cut_reads_last_to_first_and_the_plan_is_checked(self):
        prev = self.card('s18', 0.4, 1.5)
        same = sb.join(prev, self.card('s19', 0.28, 0.4, {'scale_step': 'same', 'transition': 'cut on touchdown'}))
        self.assertEqual(same['step'], 'wider')
        self.assertIn('클로즈업 → 전체', same['words'])
        self.assertIn('cut on touchdown', same['words'])
        self.assertTrue(same['problems'] and same['problems'][0].startswith('BRIEF_PLAN_DIFFERS: s19 was planned same'))
        ok = sb.join(prev, self.card('s19', 0.28, 0.4, {'scale_step': 'wider'}))
        self.assertEqual(ok['problems'], [])
        self.assertEqual(sb.join(None, self.card('s01', 0.2, 0.4))['words'], None)
        part = {**self.card('s19', 0.28, 0.4, {'scale_step': 'same'}), 'subject': ['booster/leg_0']}
        other = sb.join(prev, part)   # a leg after the whole booster: no size step, no plan flag
        self.assertEqual((other['step'], other['problems']), (None, []))
        self.assertIn('대상이 바뀜: booster → leg_0', other['words'])


class PageTest(unittest.TestCase):
    def test_page_has_the_flow_table_a_card_per_shot_and_no_collected_photo(self):
        cards = []
        for i in (1, 2):
            first = sb._moment(row(0, 0.2, [0.5, 0.3], [-10.0, 0.0], [5.0, 0.0, 0.0], [0.0, 0.0, 0.0]))
            cards.append({'shot_id': f's0{i}', 'start_s': 0.0, 'end_s': 2.5, 'length_s': 2.5, 'narration': '줄 <b>', 'purpose': 'p',
                          'role': 'hook', 'subject': ['booster'], 'details': [], 'plan': {'camera': 'held'}, 'version': 'v0001',
                          'first': first, 'last': None, 'camera_move': None, 'issues': [], 'departs': [], 'status': 'proposed',
                          'approval': None, 'join': {'words': None, 'problems': []},
                          'image_urls': [f'img/s0{i}_0.jpg', None, None, None]})
        cards[0]['pictures'] = [{'concept_id': 'c02', 'at': 1.0, 'problems': [], 'departs': 3, 'abandoned': True}]
        cards[1]['pictures'] = [{'concept_id': 'c01', 'at': 0.0, 'problems': ['t=0.0: subject 0.2 tall, the picture 0.5'], 'departs': 0, 'abandoned': False}]
        html_text = sb.page(cards, 't')
        self.assertIn('그림 버림', html_text)   # a picture departed from past the limit (2026-10-11, s06)
        self.assertIn('차이 1', html_text)
        self.assertEqual(html_text.count("class='card'"), 2)
        self.assertIn('전체 흐름', html_text)
        self.assertIn('&lt;b&gt;', html_text)          # narration is escaped
        self.assertIn('아직 빌드 없음', html_text)       # a moment without a build says so
        self.assertNotIn('references/photos', html_text)


if __name__ == '__main__':
    unittest.main()
