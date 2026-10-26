"""A v2 test project: a v1 project whose brief, facts, story and script are settled by the test (decisions_fixture), a
delegation a test recorded (so an agent note may settle the cuts), and a two-cut cuts body a test may change."""
from copy import deepcopy
import json
from pathlib import Path

from studio.common import read_json, write_json
from studio.project import init_project
from tests.decisions_fixture import settle

LINES = [{'line_id': 'l01', 'text': '공기는 천장에서 바닥으로 쉬지 않고 떨어집니다.', 'illustrative': 'a test line, no claim'},
         {'line_id': 'l02', 'text': '먼지 한 톨이 칩 하나를 죽입니다.', 'illustrative': 'a test line, no claim'}]
TEST_NOTE = 'a test plans these cuts'


def project(root, identifier='v2t', lines=None):
    path = Path(init_project(identifier, {'request': 'v2 test reel', 'shots': [{'shot_id': 'placeholder', 'frame_count': 30}]}, root)['project_path'])
    settle(path, ['script'], {'script': {'lines': deepcopy(lines or LINES)}})
    data = read_json(path / 'project.json')
    data['delegation'] = {'user_words': '테스트: 알아서 해 주세요', 'scope': [], 'recorded_at': '2026-10-26T00:00:00+00:00'}
    write_json(path / 'project.json', data)
    return path


def cut(cid, lines, scale='room', step='first', **over):
    row = {'cut_id': cid, 'line_ids': list(lines), 'duration_s': 4.0, 'role': 'explain',
           'purpose': 'a test cut shows air falling through a room',
           'show_how': {'point': 'air falls from the ceiling through the floor', 'proof': ['flow/0'],
                        'way': {'id': 'cut_open', 'rejected': {'id': 'glance', 'why': 'a glance does not show the path of the air'}}},
           'place': 'bay', 'scale': scale,
           'cutaway': {'opens': 'section_cut', 'what': 'a vertical section of one bay, filling the frame',
                       'parts': [{'id': 'filters', 'words': 'the ceiling filter layer'}, {'id': 'floor', 'words': 'the perforated floor'}],
                       'flow': [{'what': 'filtered air', 'colour': 'blue', 'path': 'from the ceiling filters down through the floor holes',
                                 'along': ['filters', 'floor']}],
                       'labels': [{'part': 'filters', 'word': '필터'}], 'scale_step': step, 'view': 'level',
                       'camera': {'move': 'push', 'amount': 0.06}},
           'change': {'kind': 'none'},
           'frames': {'first': {'shows': 'the bay cut open, the air space above the floor clear'}}}
    for key, value in over.items():
        row[key] = value
    return row


def body(**over):
    out = {'look': 'Photoreal, natural light, like a museum cutaway model photographed, no text anywhere', 'label_height': 0.037,
           'places': [{'id': 'bay', 'words': 'one bay of a semiconductor cleanroom'}],
           'point': {'says': 'the room itself keeps the dust off the wafers', 'proof': {'what': 'the air falls through the floor', 'rows': ['c1/flow/0']}},
           'cuts': [cut('c1', ['l01']),
                    cut('c2', ['l02'], scale='part', step='closer',
                        change={'kind': 'state', 'what': 'the centre chip goes dark', 'at': [0.4, 0.6], 'parts': ['floor']},
                        frames={'first': {'shows': 'the chips lit'}, 'last': {'shows': 'the chips lit, the centre one dark'}})]}
    out.update(over)
    return out


def write_body(path, data, name='cuts.json'):
    file = Path(path) / name
    file.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return file
