"""Where a caption is cut, into pages (one subtitle on screen at a time) and into lines (a page's rows): only between
words (Korean eojeol, the space-separated unit), never after a number or a determiner (a number stays with its counter
or unit: "한 톨", "25장", "300 mm"; "이 칩"), preferring a cut after a stop or comma, then after a clause ending, then
after a subject or object particle, then another particle, and the pieces as even as the cuts allow. The fewest pieces that fit are made; among the ways to cut
into that many, the cheapest by BREAK_COST plus the unevenness is taken.

Why (2026-10-27, the fab v2 preview): pages were cut at the last space within 28 characters and lines at the last space
that fit, so "먼지 한 톨이" was split "먼지 한 / 톨이" across two subtitles - a number torn from its counter and a phrase read
in halves. The page cutter in studio/audio.py (cue_chunks) is frozen code; its cues are kept for timing and sound
bindings, and the edit re-pages them for display (display_cues) from the same alignment.

The word classes below are Korean grammar (closed classes: particles, clause endings, number words, determiners), not
cases of one reel: a sentence not seen before is cut by the same costs. A word in none of them (a noun, a modifier
like "떨어진") is a costly place to cut, never a forbidden one.
"""
from __future__ import annotations

import math
import re

STOPS = ',.!?…:;·)」』"\''
# a cut after a word ending in one of these (longest first): the word ends a clause (connective or final ending)
CLAUSE_ENDINGS = ('지만', '는데', '은데', '도록', '려고', '면서', '니까', '므로', '어서', '아서', '고', '며', '면', '서', '자', '니', '데',
                  '게', '다', '요', '죠', '까', '네')
# ... or a subject, topic or object particle (the phrase that names who or what is whole)
SUBJECT_PARTICLES = ('께서', '은', '는', '이', '가', '을', '를', '도', '만')
# ... or another particle (a place, a means, a partner: it often leans on the words after it - "칩으로 가득 찬"); the
# genitive 의 binds its noun to the next one and is no place to cut (a plain word)
PARTICLES = ('에서', '에게', '까지', '부터', '처럼', '보다', '마다', '조차', '으로', '한테', '에', '께', '로', '와', '과', '랑')
# a word that is a number or a determiner modifies the word after it: never cut after it
NATIVE_NUMBERS = {'한', '두', '세', '석', '서', '네', '넉', '너', '다섯', '여섯', '일곱', '여덟', '아홉', '열', '스무', '몇', '수십', '수백', '수천',
                  '수만', '수억', '수십만', '수백만', '한두', '두세', '서너', '대여섯', '여러'}
DETERMINERS = {'그', '이', '저', '각', '모든', '어느', '무슨', '새', '온', '첫', '매', '약', '총', '단', '제'}
SINO_DIGITS = re.compile(r'[일이삼사오육칠팔구십백천만억]+')
NUMBER = re.compile(r'[+\-−]?[\d.,]*\d[\d.,]*%?')

BREAK_COST = {'stop': 0.0, 'clause': 1.0, 'subject': 1.5, 'particle': 2.5, 'word': 6.0, 'bound': 100.0}
EVEN_WEIGHT = 3.0    # the unevenness term: EVEN_WEIGHT x the sum of each piece's squared share off the mean


def kind_after(word):
    """How a cut after `word` reads: stop | clause | subject | particle | word | bound (a number or determiner: never)."""
    w = word.strip()
    if not w:
        return 'word'
    if w[-1] in STOPS:
        return 'stop'
    if w in NATIVE_NUMBERS or w in DETERMINERS or NUMBER.fullmatch(w) or SINO_DIGITS.fullmatch(w):
        return 'bound'
    if w.endswith(CLAUSE_ENDINGS):
        return 'clause'
    if w.endswith('의'):
        return 'word'
    if w.endswith(SUBJECT_PARTICLES):
        return 'subject'
    if w.endswith(PARTICLES):
        return 'particle'
    return 'word'


def words_of(text):
    """[(start, end)] of the words of text (runs without spaces)."""
    return [m.span() for m in re.finditer(r'\S+', text)]


def split(units, size, fits, most=None):
    """The cheapest cut of `units` (a list of words) into the fewest pieces that each fit: [(i, j)] index ranges. A
    cut after a number or determiner is taken only when no count of pieces up to `most` avoids it (more rows or pages
    before a number torn from its counter).
    size(i, j): a piece's size; fits(i, j): whether it fits. None when a single unit does not fit (the caller cuts
    that unit itself)."""
    n = len(units)
    if n == 0:
        return []
    if any(not fits(i, i + 1) for i in range(n)):
        return None
    total = size(0, n)
    fallback = None
    for k in range(1, (min(most, n) if most else n) + 1):
        mean = total / k
        # best[j][p]: the least cost of cutting units[:j] into p pieces
        best = [[math.inf] * (k + 1) for _ in range(n + 1)]
        back = [[None] * (k + 1) for _ in range(n + 1)]
        best[0][0] = 0.0
        for j in range(1, n + 1):
            for p in range(1, k + 1):
                for i in range(j - 1, -1, -1):
                    if not fits(i, j):
                        break
                    if best[i][p - 1] == math.inf:
                        continue
                    cut = BREAK_COST[kind_after(units[j - 1])] if j < n else 0.0
                    cost = best[i][p - 1] + cut + EVEN_WEIGHT * ((size(i, j) - mean) / max(mean, 1e-9)) ** 2
                    if cost < best[j][p]:
                        best[j][p], back[j][p] = cost, i
        if best[n][k] < math.inf:
            out, j = [], n
            for p in range(k, 0, -1):
                i = back[j][p]
                out.append((i, j))
                j = i
            if best[n][k] < BREAK_COST['bound']:
                return out[::-1]
            fallback = fallback or out[::-1]
    return fallback


def pages(text, start, end, width):
    """[(a, b)] covering text[start:end] whole, in order: subtitles of at most `width` characters (spaces counted, as
    studio/audio.cue_chunks counts), cut between words by the costs above; a word longer than `width` is cut inside
    (the only cut inside a word). Each range keeps the spaces after its last word."""
    spans = [(start + a, start + b) for a, b in words_of(text[start:end])]
    if not spans:
        return [(start, end)]
    pieces = []   # words, a too-long one cut into width-long pieces
    for a, b in spans:
        pieces += [(x, min(x + width, b)) for x in range(a, b, width)] if b - a > width else [(a, b)]
    units = [text[a:b] for a, b in pieces]
    size = lambda i, j: pieces[j - 1][1] - pieces[i][0]
    got = split(units, size, lambda i, j: size(i, j) <= width)
    out = []
    for n, (i, j) in enumerate(got):
        a = start if n == 0 else pieces[i][0]
        b = pieces[got[n + 1][0]][0] if n + 1 < len(got) else end
        out.append((a, b))
    return out


def lines(text, length, max_width, most=None):
    """The rows of one paragraph (no newline in it) whose rows each measure at most max_width by length(str): the fewest
    rows, cut between words by the costs above; a word wider than max_width is cut inside, at the last character that
    fits. `most`: the rows the caller allows (a cut after a number is taken before a row too many)."""
    units = text.split()
    if not units:
        return []
    pieces = []
    for u in units:
        if length(u) <= max_width:
            pieces.append(u)
            continue
        part = ''
        for ch in u:
            if part and length(part + ch) > max_width:
                pieces.append(part)
                part = ''
            if not part and length(ch) > max_width:
                return None
            part += ch
        pieces.append(part)
    row = lambda i, j: ' '.join(pieces[i:j])
    got = split(pieces, lambda i, j: length(row(i, j)), lambda i, j: length(row(i, j)) <= max_width, most)
    return None if got is None else [row(i, j) for i, j in got]


def display_cues(voice, alignment, spans, fps, width):
    """The voice's cues re-paged for display: each span (a sentence within a line; the stretches a subtitle may not
    cross) cut by `pages`, timed from the alignment the voice was cut from. The voice's own cues are returned unchanged
    when they were not made by the automatic cutter (an author's display mapping, a text the alignment does not match,
    no character alignment)."""
    from .audio import seconds_to_frame
    cues = voice.get('cues') or []
    chars = (alignment or {}).get('characters')
    if not cues or not chars or any(c.get('character_range') is None or c.get('display_text') != c.get('spoken_text', '').strip() for c in cues):
        return cues
    text = ''.join(chars)
    if [c['spoken_text'] for c in cues] != [text[c['character_range'][0]:c['character_range'][1]] for c in cues] or \
            ''.join(c['spoken_text'] for c in cues) != text:
        return cues
    starts, ends = alignment['character_start_times_seconds'], alignment['character_end_times_seconds']
    out = []
    for a0, b0 in spans:
        for a, b in pages(text, a0, b0, width):
            if not text[a:b].strip():
                continue
            out.append({**cues[0], 'cue_id': f'page_{len(out) + 1:03d}', 'spoken_text': text[a:b], 'display_text': text[a:b].strip(),
                        'character_range': [a, b], 'start_seconds': starts[a], 'end_seconds': ends[b - 1],
                        'start_frame': seconds_to_frame(starts[a], fps),
                        'end_frame': max(seconds_to_frame(starts[a], fps) + 1, seconds_to_frame(ends[b - 1], fps))})
    return out if ''.join(c['spoken_text'] for c in out).strip() == text.strip() else cues
