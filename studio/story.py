"""The story contract (story.json): where the reel hooks, turns and peaks, settled before the script.

On the decision ladder the story is a layer (decisions.py: brief -> facts -> story -> script); its approval writes
story.json. A delegated run has no ladder: the story is still the user's pick (story pick, their words), written here.
The tools read story.json: the script's lines name its beats, the shots follow them, the reel flow checks read where
the climax is (studio/reel_flow.py).
"""
from __future__ import annotations

from .common import now, read_json, write_json
from .project import load_project, project_dir

STORY_FILE = 'story.json'


def load(path):
    file = project_dir(path) / STORY_FILE
    return read_json(file) if file.is_file() else None


def body_of(record):
    return {k: record[k] for k in ('logline', 'direction', 'beats') if k in record} if record else None


def materialize(path, body, decided_by='user', user_words=None, picked=None):
    """story.json: the body and who settled it (the ladder's approval, or a pick in the user's words)."""
    path = project_dir(path)
    record = {'schema_version': 1, **body, 'decided_by': decided_by, **({'user_words': user_words} if user_words else {}),
              **({'picked': picked} if picked else {}), 'written_at': now()}
    write_json(path / STORY_FILE, record)
    return [STORY_FILE]


def missing(path):
    """STORY_MISSING (a warning): a project with shots and no story - made before the story existed, or skipped."""
    path = project_dir(path)
    if load(path) is not None or not load_project(path).get('shots'):
        return []
    return ["STORY_MISSING: no story.json - the shots follow the sentences with nothing saying where the reel hooks, turns and "
            "peaks (studio story directions; on the ladder the story layer)"]


def check(project):
    """The story's lint as the ladder runs it, for a project whatever its route (delegated or not)."""
    from .decisions import validate_body
    from .story_core import lint_story, load_axes
    path = project_dir(project)
    record = load(path)
    if record is None:
        return {'story': None, 'errors': [], 'warnings': missing(path)}
    body = body_of(record)
    validate_body('story', body)
    errors, warnings = lint_story(body, load_project(path)['output'].get('target_seconds'), load_axes())
    return {'story': str(path / STORY_FILE), 'errors': errors, 'warnings': warnings}


def register_commands(subparsers):
    parser = subparsers.add_parser('story', help='The story before the script: directions drawn on axes, picked by the user')
    commands = parser.add_subparsers(dest='story_command', required=True)
    p = commands.add_parser('check', help="story.json against the story rules (studio/story_core.STORY_RULES)")
    p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: check(a.project))
    return commands
