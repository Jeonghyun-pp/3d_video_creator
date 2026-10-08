"""generate keep-masks / keep: the parts an explanation depends on stay Blender pixels in a generated take.

A video-to-video model may restyle every pixel, and a thin or exact part is what it changes first (research 2026-10-07:
structure holds under depth/edge control, part identity and thin parts do not). The shot names those parts in
`shot.screen.keep`; keep-masks renders where they show on every frame (blender_ops/keep_masks.py, the frame probe's
classes, occluders included); keep merges the Blender look render over the take through the masks, feathered by
FEATHER_SIGMA_PX, into clip_kept.mp4 beside the take. The take itself and its QA are untouched: the structure gate judges
what the model made, the edit uses the kept clip (edit.latest_generated). Free: no paid call, one Workbench pass.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

from ..common import BT709_CHAIN, REPO, StudioError, blender_binary, file_hash, h264_encoder_args, now, read_json, run_command, safe_path, stable_hash, write_json
from ..project import load_project, load_shot, project_dir, shot_path

OPS = REPO / 'studio' / 'blender_ops'
CODE = ('keep_masks.py', 'frame_probe.py', 'frame_probe_core.py', 'screen_core.py', 'id_view.py', 'scene_index.py', 'scene_roles.py')
FEATHER_SIGMA_PX = 1.0   # about a 2 px soft edge at 1080 wide: no hard cut-out line, no halo of the Blender background


BACKGROUND = '@background'   # in screen.generate_only: where no object renders (sky, far plate)


def _parts(shot, parts):
    parts = list(parts or (shot.get('screen') or {}).get('keep') or [])
    if not parts:
        raise StudioError('INPUT_INVALID', f"{shot['shot_id']}: nothing to keep - declare shot.screen.keep (part ids) or pass --parts")
    return parts


def plan(shot, parts=None):
    """(mode, mask parts, background): 'keep' - the listed parts stay Blender pixels (screen.keep, or --parts);
    'generate_only' - only the regions screen.generate_only lists come from the take (people, the sky: what a model
    restyles well) and everything else stays Blender, the inverse of the same masks. Whole-frame restyles failed
    every measured structure test (2026-10-04 A/B 6/6, G8 2/2), so a shot can hand the model only what it does well."""
    only = (shot.get('screen') or {}).get('generate_only')
    if only and not parts:
        return 'generate_only', [p for p in only if p != BACKGROUND], BACKGROUND in only
    return 'keep', _parts(shot, parts), False


def build_keep_masks(project, shot_id, parts=None, frames=None, split=False, height=None, allow_unknown=False, background=False):
    """Masks of the kept parts for the shot's current scene version at the output size (cached by inputs). frames: only
    those frames; split: one mask per part from one pass (pattern per part in 'patterns'); height: a smaller pass."""
    path = project_dir(project)
    meta = load_project(path)
    shot = load_shot(path, shot_id)
    parts = list(parts) if (parts or background) else _parts(shot, parts)
    version = shot.get('scene_version')
    scene = safe_path(shot_path(path, shot_id).parent, f'versions/{version}/scene.blend') if version else None
    if scene is None or not scene.is_file():
        raise StudioError('INPUT_INVALID', f'{shot_id}: no built scene version', recovery='Build the shot first (shot build).')
    count = shot['duration_frames']
    out_w, out_h = meta['output']['width'], meta['output']['height']
    height = height or out_h
    width = max(2, round(out_w * height / out_h))
    frames = sorted(set(frames)) if frames else None
    fingerprint = stable_hash({'scene': file_hash(scene), 'parts': parts, 'size': [width, height], 'frames': frames or count,
                               'split': split, 'allow_unknown': allow_unknown, 'background': background,
                               'code': {name: file_hash(OPS / name) for name in CODE}})[:24]
    root = shot_path(path, shot_id).parent / 'keep_masks'
    directory = root / fingerprint
    if (directory / 'keep.json').is_file():
        return {'status': 'reused', **read_json(directory / 'keep.json')}
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.building-', dir=root))
    try:
        write_json(staging / 'job.json', {'output_dir': str(staging), 'frame_count': count, 'width': width, 'height': height, 'parts': parts,
                                          **({'frames': frames} if frames else {}), 'split': split, 'allow_unknown': allow_unknown,
                                          'background': background})
        run_command([blender_binary(), '--background', '--factory-startup', '--disable-autoexec', str(scene), '--python-exit-code', '1',
                     '--python', str(OPS / 'keep_masks.py'), '--', str(staging / 'job.json')], staging / 'keep.log', timeout=3600)
        want = len(frames) if frames else count
        rendered = len(list((staging / '0' if split else staging).glob('frame_*.png')))
        if rendered != want:
            raise StudioError('SCENE_INVALID', f'keep masks rendered {rendered}/{want} frames')
        blender = read_json(staging / 'keep_meta.json')
        data = {'schema_version': 1, 'fingerprint': fingerprint, 'shot_id': shot_id, 'scene_version': version, 'parts': parts,
                'frames': frames or count, 'size': [width, height], 'split': split, 'seconds': blender['seconds'], 'created_at': now()}
        if split:
            data['patterns'] = {(part if isinstance(part, str) else str(i)): str(directory / str(i) / 'frame_%06d.png') for i, part in enumerate(parts)}
        else:
            data['pattern'] = str(directory / 'frame_%06d.png')
        write_json(staging / 'keep.json', data)
        if directory.exists():
            shutil.rmtree(directory)
        staging.rename(directory)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {'status': 'built', **data}


def merge(take, render, pattern, out, frames, size, fps=30, invert=False):
    """take outside the masks, render inside them, feathered: ffmpeg maskedmerge in planar RGB, then BT.709 like retime.
    invert: the take inside the masks, the render outside (generate_only)."""
    width, height = size
    graph = (f'[0:v]scale={width}:{height},format=gbrp[t];[1:v]scale={width}:{height},format=gbrp[r];'
             f'[2:v]scale={width}:{height},format=gray,{"negate," if invert else ""}gblur=sigma={FEATHER_SIGMA_PX},format=gbrp[m];'
             f'[t][r][m]maskedmerge,{BT709_CHAIN}[v]')
    from ..audio import run_media
    run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(take), '-i', str(render), '-framerate', str(fps),
               '-start_number', '0', '-i', pattern, '-filter_complex', graph, '-map', '[v]', '-frames:v', str(frames),
               *h264_encoder_args(), '-movflags', '+faststart', str(out)])


def keep_take(project, shot_id, take=None, parts=None):
    """Write clip_kept.mp4 for a generated take (default: every complete take of the shot) and record it in clip.json.
    parts: the part ids to keep (default shot.screen.keep)."""
    from .inputs import latest_complete_render
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    mode, parts, background = plan(shot, parts)
    render = latest_complete_render(path, shot)
    if render is None:
        raise StudioError('INPUT_MISSING', f"{shot_id}: no complete look render of {shot.get('scene_version')} to keep parts from",
                          recovery='render submit --profile review (the shot look), then generate keep')
    masks = build_keep_masks(path, shot_id, parts, background=background)
    root = shot_path(path, shot_id).parent / 'generated'
    takes = [root / take] if take else sorted(p.parent for p in root.glob('*/clip.json'))
    done = []
    for directory in takes:
        manifest = read_json(directory / 'clip.json')
        if manifest.get('status') != 'complete' or manifest.get('frame_count') != shot['duration_frames']:
            continue
        out = directory / 'clip_kept.mp4'
        merge(manifest['clip_path'], render['clip_path'], masks['pattern'], out, shot['duration_frames'], masks['size'], invert=mode == 'generate_only')
        manifest['kept'] = {'path': str(out), 'sha256': file_hash(out), 'parts': masks['parts'], 'masks': masks['fingerprint'], 'mode': mode,
                            **({'generate_only': (shot.get('screen') or {}).get('generate_only')} if mode == 'generate_only' else {}),
                            'render': render['fingerprint'], 'created_at': now()}
        write_json(directory / 'clip.json', manifest)
        done.append({'take': directory.name, **manifest['kept']})
    return {'shot_id': shot_id, 'kept': done, 'masks': masks['fingerprint']}


def register_keep(commands):
    p = commands.add_parser('keep-masks', help='Masks of shot.screen.keep parts on every frame (free, one Workbench pass)')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--parts', help='comma-separated part ids')
    p.set_defaults(handler=lambda a: build_keep_masks(a.project, a.shot, a.parts.split(',') if a.parts else None))
    p = commands.add_parser('keep', help='Put the kept parts back from the Blender look render into generated takes (clip_kept.mp4)')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True); p.add_argument('--take')
    p.add_argument('--parts', help='comma-separated part ids (default shot.screen.keep)')
    p.set_defaults(handler=lambda a: keep_take(a.project, a.shot, a.take, a.parts.split(',') if a.parts else None))


VISIBLE_SHARE = 0.002   # a feature showing less than this share of the frame on every sampled frame is off screen
VISIBILITY_FRAMES = 5
VISIBILITY_HEIGHT = 128


def visible_features(project, shot_id, features):
    """{feature id: largest share of the frame its parts cover} over a few frames of the current version - occluders
    included (keep masks, one group per feature). features: {feature id: [part ids]}. Parts the version did not build
    count as unseen. Used to describe to a model only what the camera shows."""
    from PIL import Image
    from ..qa_generative import sample_frames
    shot = load_shot(project_dir(project), shot_id)
    ids = list(features)
    masks = build_keep_masks(project, shot_id, [features[i] for i in ids], frames=sample_frames(shot['duration_frames'], VISIBILITY_FRAMES),
                             split=True, height=VISIBILITY_HEIGHT, allow_unknown=True)
    out = {}
    for n, feature in enumerate(ids):
        pattern = masks['patterns'][str(n)]
        shares = []
        for frame in masks['frames']:
            image = Image.open(pattern % frame).convert('L')
            shares.append(sum(1 for v in image.tobytes() if v > 127) / (image.width * image.height))
        out[feature] = round(max(shares), 5) if shares else 0.0
    return out
