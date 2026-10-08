"""Genre styles: motion, look and composition learned together from a folder of reference reels of one genre.

Why (2026-10-08): every learned style came from one reel (overfit_risk on all four), so a second genre would inherit
the cutaway reel's horizon, rhythm and palette. Each learner already takes many videos; this runs the three over the
same folder under one genre name and records which files (by hash, never copied: references stay local) made it.
Ten or more reels per genre is the target; fewer is recorded as such.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .common import REPO, StudioError, check_id, now, write_json

VIDEO_SUFFIXES = ('.mp4', '.mov', '.m4v', '.webm', '.mkv')
TARGET_REFERENCES = 10


def learn_genre(genre, folder):
    genre = check_id(genre)
    videos = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in VIDEO_SUFFIXES)
    if not videos:
        raise StudioError('INPUT_INVALID', f'no reference videos ({VIDEO_SUFFIXES}) in {folder}')
    from .composition_style import learn as learn_composition
    from .look_style import learn as learn_look
    from .motion_style import learn as learn_motion
    results = {'motion': learn_motion(genre, [str(v) for v in videos]), 'look': learn_look(genre, [str(v) for v in videos]),
               'composition': learn_composition(genre, [str(v) for v in videos])}
    record = {'schema_version': 1, 'genre': genre, 'created_at': now(), 'references': len(videos),
              'sources': [{'name': v.name, 'sha256': hashlib.sha256(v.read_bytes()).hexdigest()} for v in videos],
              'styles': {'motion_style': genre, 'look_style': genre, 'composition_style': genre},
              'enough': len(videos) >= TARGET_REFERENCES}
    write_json(REPO / 'library' / 'genres' / f'{genre}.json', record)
    warnings = [] if record['enough'] else [f'GENRE_FEW_REFERENCES: {len(videos)} reels (target {TARGET_REFERENCES}): its ranges are narrow']
    return {**record, 'warnings': warnings + [w for r in results.values() for w in r.get('warnings', [])],
            'learned': {k: {'style': v['style'], 'path': v['path'], **({'shots': v['shots']} if 'shots' in v else {})} for k, v in results.items()}}


def register_commands(subparsers):
    parser = subparsers.add_parser('style', help='Genre styles: motion, look and composition learned from a folder of reference reels')
    commands = parser.add_subparsers(dest='style_command', required=True)
    p = commands.add_parser('learn-genre')
    p.add_argument('--genre', required=True); p.add_argument('--folder', required=True, help='local reference reels of one genre (never copied)')
    p.set_defaults(handler=lambda a: learn_genre(a.genre, a.folder))
