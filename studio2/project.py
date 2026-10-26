"""A v2 project is a v1 project (project.json, decisions/, shots/<id>/shot.json), so v1's readers, voice and edit work on
it unchanged. `project init` is v1's init with the cutaway grammar's reference library chosen; `project copy-story` starts
a v2 project from another project's settled story - brief, facts, story and script, as they were decided - so v2 plans
only what is shown (the cuts layer)."""
from __future__ import annotations

import shutil
from pathlib import Path

from studio.common import REPO, StudioError, check_id, now, read_json, write_json
from studio.project import init_project, load_project, project_dir, validate_schema

STORY_LAYERS = ('brief', 'facts', 'story', 'script')   # what a copied story keeps: what is said, never what is shown
STORY_FILES = ('sources.json', 'story.json')            # what those layers projected into (facts, the story pick)


def grammar_library():
    from .grammar import load
    return load()['library']


def caption_choice():
    """style.captions for a v2 reel: the subtitle letters at the reference's median ink height (the cutaway grammar's
    subtitle_text field), with the evidence said - a choice the cuts layer shows against the reference's middle half
    (CAPTION_SIZE_OFF), never a floor: its reels differ. None when the reference was not measured."""
    from .grammar import read
    try:
        got = read('subtitle_text')
    except Exception:   # noqa: BLE001 - no measure of the reference: v1's caption size stands
        return None
    p25, median, p75 = (got.get(f'all.subtitle_text_height.{k}', {}).get('measured') for k in ('p25', 'median', 'p75'))
    if median is None:
        return None
    strength = got['all.subtitle_text_height.median'].get('strength')
    return {'letter_height': round(median, 4),
            'evidence': f"the reference's subtitle letters: median {median:.3f} of the frame height (p25 {p25:.3f}, p75 {p75:.3f}; "
                        f"{strength} - its reels differ), read by studio2 grammar measure-labels"}


def init(identifier, brief, root=None):
    """v1's project init, reading the cutaway grammar's reference (pattern_library) unless the brief names another, and
    its captions at the reference's subtitle letter height (caption_choice) unless the style chose one."""
    out = init_project(identifier, brief, root)
    path = Path(out['project_path'])
    data = load_project(path)
    if not data.get('pattern_library'):
        data['pattern_library'] = grammar_library()
        validate_schema(data, 'project')
        write_json(path / 'project.json', data)
    style_file = path / 'style.json'
    style = read_json(style_file) if style_file.is_file() else {}
    choice = caption_choice()
    if choice and not (style.get('captions') or {}).get('letter_height'):
        style['captions'] = {**(style.get('captions') or {}), **choice}
        write_json(style_file, style)
    return out


def copy_story(identifier, source, root=None):
    """A new project at <root>/<id> holding `source`'s story as it was settled: its brief and output, the four layers'
    records (decided against each other, so they stay decided), the files they projected into, and the delegation they
    were decided under (decisions/copied.json says where they came from). What it shows is not copied: v2 plans that anew."""
    from studio.decisions import envelope, state
    check_id(identifier)
    src = project_dir(source)
    path = Path(root or REPO / 'projects').resolve() / identifier
    if path.exists():
        raise StudioError('REVISION_CONFLICT', f'Project already exists: {path}')
    unsettled = {layer: state(src, layer) for layer in STORY_LAYERS if state(src, layer)['state'] not in ('decided', 'approved')}
    if unsettled:
        raise StudioError('DECISION_UNAPPROVED', f'copy-story: {src.name} has unsettled layers {unsettled}',
                          recovery='copy from a project whose brief, facts, story and script are settled')
    data = load_project(src)
    brief = {**data['brief'], 'output': data['output'], 'style_id': data['style_id'], 'audio': data['audio'],
             'route_policy': data.get('route_policy', {})}
    out = init(identifier, brief, path.parent)
    project = load_project(path)
    project['pattern_library'] = data.get('pattern_library') or project['pattern_library']
    if data.get('delegation'):
        project['delegation'] = dict(data['delegation'])   # where it came from: decisions/copied.json
    validate_schema(project, 'project')
    write_json(path / 'project.json', project)
    for name in STORY_FILES:
        if (src / name).is_file():
            shutil.copy2(src / name, path / name)
    (path / 'decisions').mkdir(exist_ok=True)
    for layer in STORY_LAYERS:
        if envelope(src, layer) is not None:
            shutil.copy2(src / 'decisions' / f'{layer}.json', path / 'decisions' / f'{layer}.json')
    write_json(path / 'decisions' / 'copied.json', {'schema_version': 1, 'from': str(src), 'layers': list(STORY_LAYERS), 'at': now()})
    states = {layer: state(path, layer)['state'] for layer in STORY_LAYERS}
    return {**out, 'copied_from': str(src), 'layers': states, 'artifacts': [str(path / 'project.json')]}


def status(project):
    """Each layer v2 stands on, as v1 sees it, and the cuts layer."""
    from studio.decisions import state
    path = project_dir(project)
    layers = {layer: state(path, layer) for layer in (*STORY_LAYERS, 'cuts')}
    return {'project': str(path), 'layers': layers,
            'next': next((f"{k}: {v['reason']}" for k, v in layers.items() if v['state'] not in ('decided', 'approved')), 'all layers settled')}


def register_commands(subparsers):
    parser = subparsers.add_parser('project', help='v2 projects (v1 projects with a cuts layer)')
    commands = parser.add_subparsers(dest='project_command', required=True)
    p = commands.add_parser('init', help="v1's project init, reading the cutaway grammar's reference")
    p.add_argument('--id', required=True); p.add_argument('--brief', required=True); p.add_argument('--root')
    p.set_defaults(handler=lambda a: init(a.id, a.brief, a.root))
    p = commands.add_parser('copy-story', help="a new project from another's settled brief, facts, story and script")
    p.add_argument('--id', required=True); p.add_argument('--from', dest='source', required=True); p.add_argument('--root')
    p.set_defaults(handler=lambda a: copy_story(a.id, a.source, a.root))
    p = commands.add_parser('status'); p.add_argument('--project', required=True)
    p.set_defaults(handler=lambda a: status(a.project))
    return commands
