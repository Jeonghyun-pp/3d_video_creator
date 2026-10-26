"""The preview reel: every cut's clip (studio2/motion.py) shown by v1's reel clay (review/clay: each clip captioned, the same
without words for the blind viewer, clay.json) and cut by v1's edit with v1's scratch voice and captions (a rough
candidate) - then watched by v1's readers: `reel see` (the viewer who does not know the plan) and `reel judge` (the
reel's watcher). What they find goes back to the cut plan (cuts write) or the pictures (frames make --reason); the agent
fixes it.
"""
from __future__ import annotations

from studio.common import StudioError
from studio.project import load_project, load_shot, project_dir


def preview(project):
    from studio import decisions
    from studio.edit import build_edit, latest_composite
    from studio.reel import clay
    path = project_dir(project)
    decisions.require(path, 'cut_preview')
    missing = [e['shot_id'] for e in load_project(path)['shots']
               if latest_composite(path, load_shot(path, e['shot_id']), e['frame_count']) is None]
    if missing:
        raise StudioError('RENDER_REQUIRED', f"no clip made for {', '.join(missing)}",
                          recovery='; '.join(f'studio2 motion render --cut {c}' for c in missing))
    shown = clay(path)
    cut = build_edit(path, 'rough')
    return {'reel': str(path / cut['output_path']), 'candidate_id': cut['candidate_id'], 'clay_page': shown['page'],
            'clay_reel': shown['reel'], 'warnings': shown.get('warnings', []) + cut.get('warnings', []),
            'next': ['studio2 reel see --project P', 'studio2 reel judge --project P'],
            'artifacts': [str(path / cut['output_path']), shown['page']]}


def see(project, force=False):
    from studio.reel_see import see as v1_see
    return v1_see(project, force=force)


def judge(project, force=False, user_words=None):
    from studio.reel_judge import judge as v1_judge
    return v1_judge(project, force=force, user_words=user_words)


def register_commands(subparsers):
    parser = subparsers.add_parser('reel', help='The preview reel (v1 clay, edit, voice and captions) and its readers')
    commands = parser.add_subparsers(dest='reel_command', required=True)
    p = commands.add_parser('preview', help="every cut's clip shown (v1 reel clay) and cut with v1's scratch voice and captions")
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: preview(a.project))
    p = commands.add_parser('see', help="v1's blind viewer on the preview (reel_see)")
    p.add_argument('--project', required=True); p.add_argument('--force', action='store_true')
    p.set_defaults(handler=lambda a: see(a.project, a.force))
    p = commands.add_parser('judge', help="v1's reel watcher on the preview (reel_judge)")
    p.add_argument('--project', required=True); p.add_argument('--force', action='store_true'); p.add_argument('--user-words')
    p.set_defaults(handler=lambda a: judge(a.project, a.force, a.user_words))
    return commands
