"""Run the 3D-first pictures (studio/picture3d.py) over a project's shots unattended: shot by shot, resumable, every
result appended to <project>/runs/picture3d_<date>.jsonl, the review sheet at the end. Nothing is picked, approved or
paid for: the pictures are the agent's proposals, and the paid call code (studio.generative: fal_client, clip) is never
loaded on this path - the run fails if it was (frames apply writes the shot through routing, which pays nothing).

Shots: the ones named, else every shot of the project (project.json shots, in its order) whose frames check
(concepts/<shot>/frames_check.json) found a moment the picture and the 3D disagree on (a redraw, a moment moved off its
intent, or failed apart from the camera). Each finished shot is written through `frames apply` (its move.frames pinned
to the painted pictures' poses): the run changes the shots' camera frames, not only their pictures.
Usage: python scripts/picture3d_overnight.py --project <dir> [--shots s09 s12 ...]
"""
from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DISAGREE = ('redraw', 'moved', 'other', 'stopped')   # frames check statuses where the picture and the 3D disagree


def shots_to_run(path):
    from studio.project import load_project
    out = []
    for row in load_project(path).get('shots') or []:
        shot_id = row['shot_id'] if isinstance(row, dict) else row
        check = path / 'concepts' / shot_id / 'frames_check.json'
        moments = (json.loads(check.read_text()).get('moments') or []) if check.is_file() else []
        if any(m.get('status') in DISAGREE for m in moments):
            out.append(shot_id)
    return out


def main(argv=None):
    from studio.common import StudioError
    from studio.picture3d import run, sheet
    from studio.project import project_dir
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True)
    parser.add_argument('--shots', nargs='*')
    a = parser.parse_args(argv)
    path = project_dir(a.project)
    shots = a.shots or shots_to_run(path)
    log = path / 'runs' / f'picture3d_{datetime.date.today().isoformat()}.jsonl'
    log.parent.mkdir(parents=True, exist_ok=True)
    for shot_id in shots:
        try:
            result = run(a.project, [shot_id])['shots'][shot_id]
        except StudioError as exc:
            result = {'error': exc.as_dict()}
        with log.open('a', encoding='utf-8') as out:
            out.write(json.dumps({'shot_id': shot_id, 'at': datetime.datetime.now().isoformat(timespec='seconds'), **result},
                                 ensure_ascii=False, default=str) + '\n')
        print(shot_id, 'error' if result.get('error') else 'done', flush=True)
    made = sheet(a.project, shots)
    paid = sorted(m for m in sys.modules if m.startswith(('studio.generative.clip', 'studio.generative.fal_client')))
    print(json.dumps({'shots': shots, 'log': str(log), 'sheet': made['sheet'], 'paid_modules_loaded': paid}, ensure_ascii=False))
    return 1 if paid else 0


if __name__ == '__main__':
    raise SystemExit(main())
