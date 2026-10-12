"""How the user's reference speaks: its reels transcribed on this machine and measured - sentences, characters a sentence,
seconds a sentence, characters a second, the share of the reel with words - into the local reference profile
(.studio/camera_dynamics.json, 'speech'), which the script checks read (decisions.speech_share, decisions.choppy_lines).

Why (2026-10-12, rocket_landing): the user felt the reel rush ("하나의 내용(문장)을 설명할때 컷이 좀더 길지 않나? ... 훅훅
넘어가는 느낌") and asked that the reference's sentences be counted. Guessed from the picture alone, the answer was wrong
(more silence); counted, the reference talks 91 % of the time at 7.4 characters a second, in sentences of 23 characters -
ours were 17.6. `pattern pacing learn` keeps that measurement reproducible.

The transcription runs locally (mlx-whisper, an optional dependency: docs/SETUP.md) on a model already on disk
(HF_HUB_OFFLINE): the reels never leave the machine, and the transcripts stay beside their measurements (local only).
"""
from __future__ import annotations

import os
import re
import statistics
from pathlib import Path

from .common import StudioError, now, read_json, write_json

MODEL = 'mlx-community/whisper-small-mlx'
PAUSE_S = 0.5            # a gap between words at least this long is a pause
PUNCTUATION = re.compile(r'(?<=[.?!])\s+')
# a Korean sentence ends in one of these when the transcript carries no punctuation (whisper leaves it out on some reels)
ENDINGS = re.compile(r'(니다|어요|아요|에요|예요|해요|죠|까요|네요|거든요|데요|고요|세요|군요|래요|다|요)(?:[.?!]+|\s|$)')
PUNCTUATED = 1 / 80      # fewer sentence marks than one per this many characters: the transcript is unpunctuated


def sentences(text):
    """The sentences of a transcript: by punctuation, or by Korean sentence endings when it has too little of it."""
    text = text.strip()
    if not text:
        return []
    marks = len(re.findall(r'[.?!]', text))
    if marks >= PUNCTUATED * len(text.replace(' ', '')):
        return [s for s in PUNCTUATION.split(text) if s.strip()]
    out, start = [], 0
    for m in ENDINGS.finditer(text):
        out.append(text[start:m.end()].strip())
        start = m.end()
    if text[start:].strip():
        out.append(text[start:].strip())
    return [s for s in out if s]


def reel_pace(speech, duration_s, shot_lengths):
    """One reel's numbers from its transcript ({text, segments: [{words: [{start, end, word}]}]})."""
    words = [w for s in speech['segments'] for w in s.get('words', [])]
    text = ''.join(w['word'] for w in words).strip() or speech.get('text', '').strip()
    found = sentences(text)
    chars = len(text.replace(' ', ''))
    spoken = sum(w['end'] - w['start'] for w in words)
    gaps = [b['start'] - a['end'] for a, b in zip(words, words[1:])]
    n = max(1, len(found))
    return {'duration_s': round(duration_s, 2), 'sentences': len(found), 'chars': chars,
            'chars_per_sentence': round(chars / n, 1), 's_per_sentence': round(duration_s / n, 2),
            'chars_per_s': round(chars / duration_s, 2) if duration_s else None,
            'speech_share': round(spoken / duration_s, 3) if duration_s else None,
            'pauses': sum(1 for g in gaps if g >= PAUSE_S), 'shots': len(shot_lengths),
            'shot_median_s': round(statistics.median(shot_lengths), 2) if shot_lengths else None}


def transcribe(video, target):
    """A local transcript with word times -> target (kept: a reel is transcribed once)."""
    if target.is_file():
        return read_json(target)
    os.environ.setdefault('HF_HUB_OFFLINE', '1')   # the model on disk only: nothing is fetched, nothing is sent
    try:
        import mlx_whisper
    except ImportError as error:
        raise StudioError('INPUT_INVALID', 'pattern pacing learn transcribes with mlx-whisper, an optional dependency',
                          recovery='pip install mlx-whisper in the studio environment (docs/SETUP.md); the model '
                                   f'{MODEL} must already be on disk') from error
    result = mlx_whisper.transcribe(str(video), path_or_hf_repo=MODEL, word_timestamps=True)
    speech = {'model': MODEL, 'text': result['text'],
              'segments': [{'start': s['start'], 'end': s['end'], 'text': s['text'],
                            'words': [{'start': w['start'], 'end': w['end'], 'word': w['word']} for w in s.get('words', [])]}
                           for s in result['segments']]}
    write_json(target, speech)
    return speech


def _quantiles(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    pick = lambda q: values[min(len(values) - 1, int(q * (len(values) - 1) + 0.5))]  # noqa: E731
    return {f'p{round(q * 100)}': round(pick(q), 3) for q in (0.1, 0.25, 0.5, 0.75, 0.9)} | {'n': len(values)}


def learn(groups, user_words):
    """The chosen group(s)' reels transcribed and measured -> the reference profile's 'speech'. A reel posted twice (the same
    transcript) counts once."""
    from .camera_taste import PROFILE, _measured_dir, _video_of, load_profile
    from .generative.review import check_user_words
    words = check_user_words(user_words, 'pattern pacing learn')
    full, measured = _measured_dir()
    by_sha = {c['sha256']: c['group'] for c in read_json(full)['clips'].values()}
    rows, seen = [], set()
    for grammar in sorted(measured.glob('*/grammar.json')):
        data = read_json(grammar)
        if by_sha.get(data.get('video_sha256')) not in groups:
            continue
        speech = transcribe(_video_of(grammar.parent, data['video_sha256']), grammar.parent / 'speech.json')
        key = speech['text'].strip()[:80]
        if key in seen:
            continue
        seen.add(key)
        rows.append(reel_pace(speech, data['media']['duration'], [s['len_s'] for s in data['shots']]))
    if not rows:
        raise StudioError('INPUT_INVALID', f'no reels of {groups} in the local study set')
    profile = load_profile()
    profile['speech'] = {'reels': len(rows), 'user_words': words, 'learned_at': now(), 'model': MODEL,
                         **{k: _quantiles([r[k] for r in rows]) for k in ('duration_s', 'sentences', 'chars_per_sentence', 's_per_sentence',
                                                                          'chars_per_s', 'speech_share', 'shot_median_s')}}
    write_json(PROFILE, profile)
    return {'reels': len(rows), 'speech': {k: (v or {}).get('p50') if isinstance(v, dict) else v for k, v in profile['speech'].items()
                                           if k not in ('user_words',)}}


def register_commands(commands):
    """`pattern pacing ...` (registered by patterns.register_commands)."""
    p = commands.add_parser('pacing', help="How the user's reference speaks (transcribed locally): learn, show")
    sub = p.add_subparsers(dest='pacing_command', required=True)
    q = sub.add_parser('learn', help='transcribe and measure the reels of the given group(s) into the reference profile')
    q.add_argument('--groups', required=True); q.add_argument('--user-words', required=True)
    q.set_defaults(handler=lambda a: learn([g.strip() for g in a.groups.split(',') if g.strip()], a.user_words))
    q = sub.add_parser('show')
    q.set_defaults(handler=lambda a: _show())


def _show():
    from .camera_taste import load_profile
    speech = load_profile().get('speech')
    if not speech:
        raise StudioError('INPUT_INVALID', 'no speech measurement: pattern pacing learn --groups ... --user-words "..."')
    return {k: v for k, v in speech.items() if k != 'user_words'}
