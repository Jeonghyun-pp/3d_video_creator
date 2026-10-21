"""Host smoke (macOS `say`, ffmpeg): the scratch voice speaks a shot's narration line by line and places each line where
the shot plan puts it (narration.segments, audio.place_segments). Two lines, the second asked at 3.0 s: its first subtitle
starts there and the time between is silence. Why (2026-10-12, the user: "훅훅 넘어가는 느낌").
Run: .venv/bin/python tests/studio/audio_segments_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.audio import build_audio  # noqa: E402
from studio.common import read_json, write_json  # noqa: E402
from studio.project import init_project, shot_path  # noqa: E402
from tests.decisions_fixture import settle   # builds stand on settled decisions (studio/decisions.require)

LINES = ['1단은 로켓 값의 대부분이라,', '버리지 않고 다시 쓰려면 세워서 내려야 합니다.']
SECOND_AT = 3.0

with tempfile.TemporaryDirectory(prefix='audio-segments-') as tmp:
    project = Path(init_project('voice', {'request': 'voice', 'shots': [{'shot_id': 's01', 'frame_count': 240}]}, tmp)['project_path'])
    settle(project)
    file = shot_path(project, 's01')
    shot = read_json(file)
    shot['narration'] = {**shot['narration'], 'text': ' '.join(LINES),
                         'segments': [{'text': LINES[0], 'at_s': 0.0}, {'text': LINES[1], 'at_s': SECOND_AT}]}
    write_json(file, shot)
    voice = build_audio(project, 's01', 'scratch')
    alignment = read_json(project / voice['alignment_path'])['alignment']
    second = len(LINES[0]) + 1
    start = alignment['character_start_times_seconds'][second]
    assert abs(start - SECOND_AT) < 0.01, start
    first_end = alignment['character_end_times_seconds'][len(LINES[0]) - 1]
    assert first_end < SECOND_AT - 0.3, first_end   # the first line is done well before: a real pause
    quiet = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(project / voice['wav_path']), '-af',
                            f'atrim={first_end + 0.2:.2f}:{SECOND_AT - 0.1:.2f},volumedetect', '-f', 'null', '-'],
                           capture_output=True, text=True).stderr
    level = float(quiet.split('max_volume:')[1].split('dB')[0])
    assert level < -50, level
    cue_starts = [c['start_seconds'] for c in voice['cues']]
    assert any(abs(t - SECOND_AT) < 0.01 for t in cue_starts), cue_starts   # a subtitle starts with the second line
    print('AUDIO SEGMENTS SMOKE OK ' + json.dumps({'first_line_ends': round(first_end, 2), 'second_starts': start,
                                                   'gap_max_db': level, 'duration_s': voice['duration_seconds'], 'cues': cue_starts}))
