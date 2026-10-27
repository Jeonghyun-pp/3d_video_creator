"""Caption pages and lines are cut between Korean words where the phrase allows (studio/caption_breaks.py): never after a
number or a determiner, after a stop or comma first, then a clause ending, then a particle, the pieces even."""
from pathlib import Path
import unittest

from studio.caption_breaks import display_cues, kind_after, lines, pages
from studio.edit import pinned_font, wrap_text

WIDTH = 28   # studio/audio.CUE_CHARS


def cut(text, width=WIDTH):
    return [text[a:b].strip() for a, b in pages(text, 0, len(text), width)]


class PagesTest(unittest.TestCase):
    def assert_whole(self, text, got):
        self.assertEqual(' '.join(got).split(), text.split())   # every word kept, in order, none cut inside

    def test_a_number_stays_with_its_counter_or_unit(self):
        for text, together in (('그런데 상자 문이 열리자, 소매에서 떨어진 먼지 한 톨이 다가옵니다.', '한 톨이'),
                               ('웨이퍼 25장이 질소로 채운 상자 안에서 300 mm 간격으로 서 있습니다.', '300 mm'),
                               ('300 mm 웨이퍼 25장을 실은 이 상자는 평균 12주, 최대 1,400단계를 돕니다.', '평균 12주,'),
                               ('하지만 놓친 한 톨이 회로에 앉으면, 칩 하나가 통째로 죽습니다.', '한 톨이')):
            got = cut(text)
            self.assert_whole(text, got)
            self.assertTrue(any(together in page for page in got), (text, got))
            self.assertTrue(all(len(page) <= WIDTH for page in got), got)

    def test_the_cut_falls_at_the_phrase_boundary(self):
        cases = {
            '그런데 상자 문이 열리자, 소매에서 떨어진 먼지 한 톨이 다가옵니다.': ['그런데 상자 문이 열리자,', '소매에서 떨어진 먼지 한 톨이 다가옵니다.'],
            '손을 뻗는 순간, 천장 레일이 웨이퍼 상자를 먼저 낚아채 갑니다.': ['손을 뻗는 순간,', '천장 레일이 웨이퍼 상자를 먼저 낚아채 갑니다.'],
            '석 달 뒤, 같은 상자가 칩으로 가득 찬 웨이퍼를 싣고 돌아옵니다.': ['석 달 뒤, 같은 상자가', '칩으로 가득 찬 웨이퍼를 싣고 돌아옵니다.'],
            '그래서 공장은 사람 손을 웨이퍼에서 아예 떼어 놓았습니다.': ['그래서 공장은 사람 손을 웨이퍼에서', '아예 떼어 놓았습니다.'],
            '공기가 쉬지 않고 떨어지는 이 방에서, 먼지를 가장 많이 내는 건 사람입니다.': ['공기가 쉬지 않고 떨어지는 이 방에서,', '먼지를 가장 많이 내는 건 사람입니다.'],
        }
        for text, want in cases.items():
            self.assertEqual(cut(text), want)

    def test_a_short_sentence_is_one_page_and_a_long_one_is_cut_evenly(self):
        self.assertEqual(cut('칩 하나가 통째로 죽습니다.'), ['칩 하나가 통째로 죽습니다.'])
        text = '반도체 공장은 먼지를 막으려고 천장에서 공기를 쏟아붓고, 바닥 아래로 그 공기를 빨아들이며, 사람 손을 웨이퍼에서 떼어 놓습니다.'
        got = cut(text)
        self.assert_whole(text, got)
        self.assertEqual(len(got), 3)
        self.assertLessEqual(max(map(len, got)) - min(map(len, got)), 8, got)   # even pages
        self.assertTrue(all(kind_after(page.split()[-1]) != 'bound' for page in got), got)

    def test_a_word_longer_than_a_page_is_the_only_cut_inside_a_word(self):
        text = '가' * 40 + ' 끝.'
        got = cut(text)
        self.assertEqual(''.join(got).replace(' ', ''), text.replace(' ', ''))
        self.assertTrue(all(len(page) <= WIDTH for page in got))

    def test_the_ranges_cover_the_span_whole(self):
        text = '  닿기 직전, 쏟아지는 공기가 그 먼지를 바닥 아래로 끌어내립니다. '
        got = pages(text, 0, len(text), WIDTH)
        self.assertEqual(got[0][0], 0)
        self.assertEqual(got[-1][1], len(text))
        self.assertTrue(all(a[1] == b[0] for a, b in zip(got, got[1:])))

    def test_word_kinds(self):
        for word, kind in (('열리자,', 'stop'), ('한', 'bound'), ('25', 'bound'), ('1,400', 'bound'), ('그', 'bound'), ('이', 'bound'),
                           ('앉으면', 'clause'), ('상자가', 'subject'), ('칩으로', 'particle'), ('공장의', 'word'), ('떨어진', 'word')):
            self.assertEqual(kind_after(word), kind, word)


class LinesTest(unittest.TestCase):
    def test_lines_are_cut_between_words_and_even(self):
        font, _ = pinned_font({}, Path('/tmp'), 40)
        text = '소매에서 떨어진 먼지 한 톨이 다가옵니다.'
        width = font.getlength(text) * 0.7
        got = wrap_text(text, font, width)
        self.assertEqual(len(got), 2)
        self.assertTrue(all('한 톨이' in row or '한' not in row.split() for row in got), got)
        self.assertNotEqual(got[0].split()[-1], '한')
        self.assertTrue(all(font.getlength(row) <= width for row in got))
        self.assertLess(abs(font.getlength(got[0]) - font.getlength(got[1])), width * 0.6)

    def test_a_line_that_fits_stays_one_line_and_a_newline_is_kept(self):
        font, _ = pinned_font({}, Path('/tmp'), 30)
        self.assertEqual(wrap_text('웨이퍼 상자', font, 1000), ['웨이퍼 상자'])
        self.assertEqual(wrap_text('웨이퍼\n상자', font, 1000), ['웨이퍼', '상자'])

    def test_a_number_never_ends_a_line(self):
        length = len
        for text in ('웨이퍼 25 장이 서 있습니다', '먼지 한 톨이 다가옵니다', '두께 300 mm 웨이퍼'):
            for width in range(8, 16):
                got = lines(text, length, width, most=3)
                if got:
                    self.assertTrue(all(kind_after(row.split()[-1]) != 'bound' for row in got[:-1]), (text, width, got))


class DisplayCuesTest(unittest.TestCase):
    def test_the_voice_cues_are_re_paged_on_their_own_alignment(self):
        from studio.audio import make_cues, sentence_spans
        text = '그런데 상자 문이 열리자, 소매에서 떨어진 먼지 한 톨이 다가옵니다.'
        alignment = {'characters': list(text), 'character_start_times_seconds': [i * .1 for i in range(len(text))],
                     'character_end_times_seconds': [i * .1 + .1 for i in range(len(text))], 'source': 'test'}
        cues, _ = make_cues(text, alignment, len(text) * .1 + .5, 30)
        self.assertEqual([c['display_text'] for c in cues][-1], '톨이 다가옵니다.')   # the frozen cutter, as it was
        shown = display_cues({'cues': cues}, alignment, sentence_spans(text), 30, WIDTH)
        self.assertEqual([c['display_text'] for c in shown], ['그런데 상자 문이 열리자,', '소매에서 떨어진 먼지 한 톨이 다가옵니다.'])
        self.assertEqual(shown[0]['start_frame'], 0)
        self.assertEqual(shown[1]['start_frame'], round(text.index('소매') * .1 * 30))
        self.assertTrue(all(a['end_frame'] <= b['start_frame'] for a, b in zip(shown, shown[1:])))

    def test_an_authors_mapping_is_kept(self):
        text = '먼지 한 톨이 다가옵니다.'
        alignment = {'characters': list(text), 'character_start_times_seconds': [0.0] * len(text),
                     'character_end_times_seconds': [0.1] * len(text)}
        mapped = [{'cue_id': 'cue_001', 'spoken_text': text, 'display_text': '먼지가 다가옵니다', 'character_range': [0, len(text)],
                   'start_frame': 0, 'end_frame': 3}]
        self.assertIs(display_cues({'cues': mapped}, alignment, [(0, len(text))], 30, WIDTH), mapped)


if __name__ == '__main__':
    unittest.main()
