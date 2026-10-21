"""3D-first pictures end to end (studio/picture3d.run): a lighthouse on a headland, two pictured moments drawn from text
(placeholders here). The engine's candidates pick each moment's pose; the passes render clay, depth, lines and the id
row there; the "painting" is the clay moved 6 % and scaled 1.03 (the image tool mocked: what the pilot measured it
does); registration lays it back; the registered painting becomes the moment's picture, read from the 3D itself, its
pose pinned by the agent; frames apply carries the new pictures; the build takes the pinned poses. Nothing is picked.
Run: .venv/bin/python tests/studio/picture3d_smoke.py"""
from pathlib import Path
import sys
import tempfile
import time

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio import concept, picture3d  # noqa: E402
from studio.common import read_json, write_json  # noqa: E402
from studio.project import init_project, load_shot, shot_path  # noqa: E402
from tests.decisions_fixture import settle   # builds stand on settled decisions (studio/decisions.require)

GROUND = {'id': 'headland', 'shape': 'box', 'size': [60.0, 60.0, 0.2], 'at': [0.0, 0.0, -0.1], 'material': 'm'}
TOWER = {'id': 'lighthouse', 'shape': 'box', 'size': [1.6, 1.6, 9.0], 'at': [0.0, 0.0, 4.5], 'material': 'm'}
HUT = {'id': 'hut', 'shape': 'box', 'size': [2.0, 2.0, 1.6], 'at': [3.0, 1.0, 0.8], 'material': 'm'}


def frame(at, azimuth):
    return {'at': at, 'height_share': 0.6, 'centre': [0.5, 0.5], 'view': {'elevation_deg': 8.0, 'azimuth_deg': azimuth}, 'lens_mm': 35.0,
            'cut_edges': []}


def fake_paint(project, shot_id, at, attempt, finding=None):
    """The image tool, as the pilot measured it: the clay handed back moved and rescaled."""
    folder = picture3d._moment_dir(Path(project), shot_id, at)
    clay = Image.open(folder / 'clay.png').convert('RGB')
    w, h = clay.size
    big = clay.resize((round(w * 1.03), round(h * 1.03)))
    canvas = Image.new('RGB', (w, h), (40, 60, 90))
    canvas.paste(big, (round(0.06 * w) - (big.width - w) // 2, -(big.height - h) // 2))
    canvas.save(folder / f'paint_{attempt}.png')
    return {'painting': str(folder / f'paint_{attempt}.png')}


with tempfile.TemporaryDirectory(prefix='picture3d-smoke-') as root:
    p = Path(init_project('picture3d', {'request': 'picture3d smoke', 'shots': [{'shot_id': 's', 'frame_count': 48}]}, root)['project_path'])
    settle(p)
    row = read_json(shot_path(p, 's'))
    row['scene'] = {'world': {'kind': 'blockout', 'color': [0.2, 0.2, 0.25], 'strength': 0.6, 'samples': 16},
                    'materials': {'m': {'color': [0.6, 0.6, 0.6]}}, 'primitives': [GROUND, TOWER, HUT]}
    row['camera'] = {'projection': 'perspective', 'movement': 'rig', 'target_anchor': None, 'keys': [],
                     'move': {'type': 'frame_to_frame', 'params': {'target': 'lighthouse', 'follow': 0.0},
                              'frames': [frame(0.0, 0.0), frame(1.0, 25.0)]}}
    row['screen'] = {'subject': ['lighthouse']}
    write_json(shot_path(p, 's'), row)
    drawn = Path(root) / 'drawn.png'
    Image.new('RGB', (90, 160), (200, 120, 60)).save(drawn)        # the pictures drawn from text, before the 3D
    concept.add(p, 's', drawn, 'start from text', frame='start')
    concept.add(p, 's', drawn, 'end from text', frame='end')
    started = time.monotonic()
    out = picture3d.run(p, ['s'], steps={'paint': fake_paint, 'register': picture3d.register, 'read': picture3d.read, 'record': picture3d.record})
    seconds = round(time.monotonic() - started, 1)
    done = out['shots']['s']
    assert not done.get('error'), done
    assert done.get('applied_at'), done
    for at in ('0.00', '1.00'):
        folder = p / 'concepts/s/picture3d' / at
        for name in ('clay.png', 'depth.png', 'lines.png', 'region.png', 'id.json', 'state.json', 'pose.json', 'registered_1.png'):
            assert (folder / name).is_file(), (at, name, sorted(f.name for f in folder.iterdir()))
        fit = read_json(folder / 'register.json')['paint_1.png']
        assert not fit['over_limit'] and 0.03 <= fit['worst_share'] <= 0.09 and fit['unpainted_share'] <= 0.08 and fit['subject_f1'] > 0.5, fit
        made = done['moments'][at]
        assert made['source'] == 'paint_over_3d', made
        record = read_json(p / 'concepts/s' / f"{made['concept_id']}.json")
        assert record['proposal']['by'] == 'agent' and record['derived_from']['supersedes'] in ('c01', 'c02'), record
    shot = load_shot(p, 's')
    frames = shot['camera']['move']['frames']
    assert [f['picture']['concept_id'] for f in frames] == [done['moments'][a]['concept_id'] for a in ('0.00', '1.00')], frames
    assert all(f['chosen']['by'] == 'agent' for f in frames), [f.get('chosen') for f in frames]
    assert not (p / 'decisions' / 'concept').exists()                # nothing picked
    assert (p / 'concepts' / 'picture3d_sheet.html').is_file()
    from studio.blender import build_shot
    version = p / 'shots/s/versions' / build_shot(p, 's', None)['scene_version']
    built = read_json(version / 'camera_move_report.json')['frames']
    for f, b in zip(frames, built):
        assert b['view'] == f['chosen']['view'] and b['lens_mm'] == f['chosen']['lens_mm'], (b['view'], f['chosen'])
    print('picture3d smoke OK', seconds, 's', [read_json(p / 'concepts/s/picture3d' / a / 'register.json')['paint_1.png']['worst_share']
                                                for a in ('0.00', '1.00')])
