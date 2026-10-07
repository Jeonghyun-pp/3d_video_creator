"""generate inputs: fill route.generative.inputs from the shot's role, so what a model sees is a rule, not a hand edit.

  explain  previs = the control clay of the current scene version, control = its depth. The structure gate compares
           against the same clay, so the model is judged on exactly what it was shown.
  mood     previs = the look render of the current scene version (review/final full sequence): lit windows, lamps
           and colour reach the model, which the clay cannot show (control passes override every material with
           grey, emission 0). control = depth when asked (Wan uses it; reference models ignore it).
Reference images and first frames already on the route are kept. Paths are project-relative with their sha256, so
the request fingerprint (generate review) changes whenever an input does and an old approval stops applying.

The light the look designed: an explain shot sends grey clay, so its key and fill never reach the model. When the shot
has a look render and the model takes reference images (routing.REFERENCE_MODELS), its middle frame goes along as one
(`look_keyframe`). It is the studio's own render - never a reference photo - and is saved beside that render's frames
(renders/<fingerprint>/look_key_*.png, so the reference clearance sees a frame of this project), and a refill replaces
it while reference images the user added stay.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from ..common import StudioError, file_hash, read_json
from ..project import load_shot, project_dir, shot_path

INPUTS_FOR = {   # role -> {input kind: source}; fill_inputs and the W9/W10 lint read the same table
    'explain': {'previs': 'control_clay', 'control': 'control_depth'},
    'mood': {'previs': 'look_render', 'control': 'control_depth'},
}
LOOK_RENDER_PROFILES = ('final', 'review')
FLAT_LOOKS = (None, 'flat_stylized', 'previs_clay')


def latest_complete_render(path, shot, profiles=LOOK_RENDER_PROFILES):
    """Newest complete full-sequence render of the shot's current scene version with a clip, or None."""
    rows = []
    for manifest in (shot_path(path, shot['shot_id']).parent / 'renders').glob('*/render.json'):
        data = read_json(manifest)
        if data.get('status') != 'complete' or data.get('scene_version') != shot.get('scene_version') or data.get('profile') not in profiles:
            continue
        clip = data.get('clip_path')
        if not data.get('full_sequence') or not clip or not Path(clip).is_file():
            continue
        if data.get('clip_sha256') and file_hash(Path(clip)) != data['clip_sha256']:
            continue
        rows.append((profiles.index(data['profile']) * -1, data.get('created_at', ''), data))
    return max(rows, key=lambda r: (r[0], r[1]))[2] if rows else None


def sources_for(path, shot):
    """{source name: (absolute path, origin)} available for the current scene version."""
    from .control import latest_control
    out = {}
    control = latest_control(path, shot)
    if control:
        for kind in ('clay', 'depth'):
            file = control['files'].get(kind, {}).get('path')
            if file and Path(file).is_file():
                out[f'control_{kind}'] = (Path(file), f"control {control['fingerprint'][:12]}")
    render = latest_complete_render(path, shot)
    if render:
        out['look_render'] = (Path(render['clip_path']), f"render {render['fingerprint'][:12]} ({render['profile']})")
    return out


def wanted(path, shot, with_depth=None):
    """[(kind, source name)] the role asks for. Depth goes to models that use a control video (Wan) or when asked."""
    from .policy import role_of
    route = shot['route']
    table = INPUTS_FOR[role_of(route)]
    use_depth = with_depth if with_depth is not None else route['generative']['model'] == 'wan-2.2-vace'
    return [(kind, src) for kind, src in table.items() if kind != 'control' or use_depth]


KEYFRAME_PREFIX = 'look_key_'   # look keyframes the studio derives: rebuilt on every fill


def look_keyframe(path, shot, render):
    """The middle frame of the shot's look render as a PNG under the shot's generation_inputs/ (cached by render)."""
    from ..audio import run_media
    middle = shot['duration_frames'] // 2
    out = Path(render['clip_path']).parent / f'{KEYFRAME_PREFIX}{middle:06d}.png'
    if not out.is_file():
        run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(render['clip_path']), '-vf', f'select=eq(n\\,{middle})',
                   '-frames:v', '1', str(out)])
    return out


def fill_inputs(project, shot_id, with_depth=None):
    from ..routing import _write_shot
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    route = shot.get('route') or {}
    if route.get('mode') not in ('hybrid', 'generative'):
        raise StudioError('INPUT_INVALID', f'{shot_id} is not a generative or hybrid shot')
    if not shot.get('scene_version'):
        raise StudioError('INPUT_INVALID', f'{shot_id} has no built scene version; pure text-to-video shots take no Blender inputs')
    available = sources_for(path, shot)
    picked, missing = [], []
    for kind, source in wanted(path, shot, with_depth):
        if source not in available:
            missing.append(source)
            continue
        file, origin = available[source]
        if not file.resolve().is_relative_to(path.resolve()):
            raise StudioError('INPUT_INVALID', f'{shot_id}: {source} {file} lies outside the project; inputs are this project\'s own outputs')
        picked.append({'kind': kind, 'path': str(file.resolve().relative_to(path.resolve())), 'sha256': file_hash(file), 'origin': origin})
    if missing:
        how = {'control_clay': 'generate control --kinds depth,clay', 'control_depth': 'generate control --kinds depth,clay',
               'look_render': 'render submit --profile review (with the shot look preset, not previs_clay)'}
        raise StudioError('INPUT_MISSING', f'{shot_id}: missing {sorted(set(missing))}',
                          recovery='; '.join(sorted({how[m] for m in missing})))
    from ..routing import REFERENCE_MODELS
    from .policy import role_of
    render = latest_complete_render(path, shot)
    if role_of(route) == 'explain' and route['generative']['model'] in REFERENCE_MODELS and render is not None:
        key = look_keyframe(path, shot, render)
        picked.append({'kind': 'reference_image', 'path': str(key.resolve().relative_to(path.resolve())), 'sha256': file_hash(key),
                       'origin': f"look render {render['fingerprint'][:12]} middle frame"})
    updated = deepcopy(shot)
    derived = lambda i: Path(i['path']).name.startswith(KEYFRAME_PREFIX) and 'renders' in Path(i['path']).parts  # noqa: E731
    kept = [i for i in route['generative'].get('inputs', []) if i['kind'] in ('reference_image', 'first_frame') and not derived(i)]
    updated['route']['generative']['inputs'] = [{k: v for k, v in i.items() if k != 'origin'} for i in picked] + kept
    if updated != shot:
        _write_shot(path, shot_id, updated, shot['revision'])
    return {'status': 'filled', 'shot_id': shot_id, 'inputs': picked + kept, 'role': route.get('role')}


def input_warnings(path, shot):
    """W9: a mood shot that would show the model grey clay (or a flat look). W10: inputs older than the current version."""
    from .policy import role_of
    route = shot['route']
    out = []
    inputs = route['generative'].get('inputs', [])
    previs = next((i for i in inputs if i['kind'] == 'previs'), None)
    if role_of(route) == 'mood' and shot.get('scene_version'):
        if previs and ('/control/' in previs['path'] or previs['path'].endswith('clay.mp4')):
            out.append({'code': 'W9_mood_input_clay', 'shot_id': shot['shot_id'],
                        'message': 'mood shot sends grey clay: lit windows, lamps and colour never reach the model; run generate inputs'})
        if shot['render'].get('look_preset') in FLAT_LOOKS:
            out.append({'code': 'W9_mood_input_clay', 'shot_id': shot['shot_id'],
                        'message': f"mood shot look preset {shot['render'].get('look_preset')!r}: its render carries no lighting for the model"})
    if shot.get('scene_version') and inputs:
        current = {str(f.resolve()) for f, _ in sources_for(path, shot).values()}
        stale = [i['path'] for i in inputs if i['kind'] in ('previs', 'control') and str((path / i['path']).resolve()) not in current]
        if stale:
            out.append({'code': 'W10_inputs_stale', 'shot_id': shot['shot_id'],
                        'message': f'inputs not from the current version {shot["scene_version"]}: {stale}; run generate inputs'})
    return out


def register_inputs(commands):
    p = commands.add_parser('inputs', help="Fill the shot's generation inputs from its role (explain: control clay+depth, mood: look render)")
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--with-depth', action='store_true', default=None, help='also send the depth control (default: only for Wan)')
    p.set_defaults(handler=lambda a: fill_inputs(a.project, a.shot, a.with_depth))
