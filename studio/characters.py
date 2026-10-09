"""Library characters: rigged people (and later animals) a declarative scene places by id - registered once with their
licence and measured actions, resolved for a build (shot.scene.characters; Blender side blender_ops/characters.py).

A character is library data like an exemplar: library/characters/<id>/<version>/ holds the rig file (FBX: no code
runs when it loads, unlike a .blend), its textures, its licence and character.json - the native height, the forward
axis and, per action, the frames and (for locomotion) the ground speed of the planted foot. Registration measures
those in Blender; only cleared licences (CC0 / CC-BY with attribution) are registered.
"""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

from .common import REPO, StudioError, blender_binary, check_id, now, read_json, run_command, write_json

ROOT = REPO / 'library' / 'characters'
LICENCES = ('CC0-1.0', 'CC-BY-4.0')
LOCOMOTION = ('walk', 'run')


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def register(character_id, fbx, licence, source_url, actions, textures=(), licence_file=None, author=None):
    """Copy the rig and its textures into the library, measure it in Blender, write character.json. actions:
    {key: action name in the file}; keys walk/run are locomotion (their ground speed is measured)."""
    character_id = check_id(character_id)
    if licence not in LICENCES:
        raise StudioError('INPUT_INVALID', f'licence {licence!r}: only {LICENCES} are registered (the clip is published)')
    fbx = Path(fbx).resolve()
    if fbx.suffix.lower() != '.fbx' or not fbx.is_file():
        raise StudioError('INPUT_INVALID', 'a character is registered from an .fbx (no code runs when it loads)')
    versions = sorted((ROOT / character_id).glob('v*')) if (ROOT / character_id).is_dir() else []
    version = f'v{len(versions) + 1:04d}'
    dest = ROOT / character_id / version
    dest.mkdir(parents=True)
    files = []
    for src in [fbx, *[Path(t).resolve() for t in textures], *([Path(licence_file).resolve()] if licence_file else [])]:
        target = dest / src.name.replace(' ', '_')
        shutil.copy2(src, target)
        files.append({'path': target.name, 'sha256': _sha(target), 'bytes': target.stat().st_size})
    with tempfile.TemporaryDirectory(prefix='character-') as tmp:
        job, out = Path(tmp) / 'job.json', Path(tmp) / 'out.json'
        write_json(job, {'fbx': str(dest / files[0]['path']), 'actions': actions, 'locomotion': [k for k in actions if k in LOCOMOTION]})
        run_command([blender_binary(), '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
                     '--python', REPO / 'studio/blender_ops/characters.py', '--', job, out], Path(tmp) / 'measure.log', timeout=600)
        measured = read_json(out)
    manifest = {'schema_version': 1, 'character_id': character_id, 'version': version, 'fbx': files[0]['path'],
                'textures': [f['path'] for f in files[1:1 + len(textures)]], 'files': files,
                'source': {'url': source_url, 'license_id': licence, 'author': author, 'retrieved_at': now(), 'use_status': 'cleared'},
                **measured}
    write_json(dest / 'character.json', manifest)
    return {'character_id': character_id, 'version': version, 'manifest': str(dest / 'character.json'), **measured}


def load(asset):
    """The manifest and folder of a character id (latest version) or id@version; files checked against their sha256."""
    name, _, version = str(asset).partition('@')
    folder = ROOT / check_id(name)
    versions = sorted(folder.glob('v*')) if folder.is_dir() else []
    if not versions:
        known = sorted(p.name for p in ROOT.iterdir()) if ROOT.is_dir() else []
        raise StudioError('INPUT_INVALID', f'no character {name!r} in the library (has {known})')
    chosen = folder / version if version else versions[-1]
    manifest = read_json(chosen / 'character.json')
    for row in manifest['files']:
        if _sha(chosen / row['path']) != row['sha256']:
            raise StudioError('INPUT_INVALID', f"character {name}: {row['path']} does not match its sha256")
    return manifest, chosen


def resolve(rows):
    """shot.scene.characters rows -> what the Blender build places (manifest, folder, defaults), or raises."""
    out = []
    for row in rows or []:
        manifest, folder = load(row['asset'])
        if row['action'] not in manifest['actions']:
            raise StudioError('INPUT_INVALID', f"character {row['id']}: action {row['action']!r} is not one of {sorted(manifest['actions'])}")
        moving = 'path' in row
        if moving and 'ground_speed' not in manifest['actions'][row['action']]:
            raise StudioError('INPUT_INVALID', f"character {row['id']}: {row['action']} is not a locomotion action; a path needs walk or run")
        if moving == ('at' in row):
            raise StudioError('INPUT_INVALID', f"character {row['id']}: give a path (walking) or at (standing), not both")
        if moving and row.get('playback', 'loop') != 'loop':
            raise StudioError('INPUT_INVALID', f"character {row['id']}: a walk along a path loops; playback once/hold is for a character standing at a point")
        texture = row.get('texture') or (manifest['textures'][0] if manifest.get('textures') else None)
        if texture and texture not in manifest.get('textures', []):
            raise StudioError('INPUT_INVALID', f"character {row['id']}: texture {texture!r} is not one of {manifest.get('textures')}")
        out.append({'height_m': 1.75, 'speed_mps': 1.35 if row['action'] == 'walk' else 3.5, **row, 'texture': texture,
                    'manifest': manifest, 'asset_dir': str(folder)})
    return out


def register_commands(subparsers):
    parser = subparsers.add_parser('character', help='Library characters: rigged people a scene places by id')
    commands = parser.add_subparsers(dest='character_command', required=True)
    p = commands.add_parser('register', help='copy a rigged FBX with its licence into the library and measure its actions')
    p.add_argument('--id', required=True); p.add_argument('--fbx', required=True); p.add_argument('--licence', required=True, choices=LICENCES)
    p.add_argument('--source-url', required=True); p.add_argument('--author')
    p.add_argument('--action', action='append', required=True, help='key=name in the file, e.g. walk=Walk (walk/run are measured as locomotion)')
    p.add_argument('--texture', action='append', default=[]); p.add_argument('--licence-file')
    p.set_defaults(handler=lambda a: register(a.id, a.fbx, a.licence, a.source_url, dict(x.split('=', 1) for x in a.action), a.texture,
                                              a.licence_file, a.author))
    show = commands.add_parser('show'); show.add_argument('--id', required=True)
    show.set_defaults(handler=lambda a: {'manifest': load(a.id)[0]})
