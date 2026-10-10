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
from ..blender_ops.code_closure import module_closure
CODE = module_closure('keep_masks.py')   # every module the mask pass runs: its cache key follows any of them (a list went stale)
FEATHER_SIGMA_PX = 1.0   # about a 2 px soft edge at 1080 wide: no hard cut-out line, no halo of the Blender background


BACKGROUND = '@background'   # in screen.generate_only: where no object renders (sky, far plate)


def _parts(shot, parts):
    decals = [d['id'] for d in (shot.get('scene') or {}).get('decals', [])]   # words on surfaces: a model garbles letters, so they always stay
    parts = list(dict.fromkeys(list(parts or (shot.get('screen') or {}).get('keep') or []) + decals))
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


SEAM_RING_PX = 6          # at the 256-px-tall seam sample: the band just outside the kept parts both sides are compared on
SEAM_GAIN_MAX = 1.5       # beyond this contrast ratio (or its inverse) the take relit the scene; matching would repaint
SEAM_OFFSET_MAX = 40.0    # the kept Blender part, so it is said (SEAM_COLOUR_FAR) instead of applied (8-bit levels)
SEAM_HEIGHT = 256
SEAM_FRAMES = 5


def colour_transfer(take_stats, render_stats):
    """Per channel gain and offset that bring the render's colour around the seam to the take's (mean and spread
    matched). stats: [(mean, stddev)] x 3 in 8-bit levels. Returns {gain, offset, far}: far when the two looks differ
    more than a grade can honestly bridge."""
    gain, offset = [], []
    for (mt, st), (mr, sr) in zip(take_stats, render_stats):
        g = st / sr if sr > 1.0 else 1.0
        gain.append(round(g, 4)); offset.append(round(mt - g * mr, 2))
    far = any(g > SEAM_GAIN_MAX or g < 1 / SEAM_GAIN_MAX for g in gain) or any(abs(o) > SEAM_OFFSET_MAX for o in offset)
    return {'gain': gain, 'offset': offset, 'far': far}


def seam_match(take, render, pattern, frame_count, size):
    """Colour transfer for the kept parts: on a few frames, the ring just outside the masks in the take vs the same
    ring in the Blender look render (both show the surroundings the seam joins). Free: Pillow + ffmpeg frame grabs."""
    from PIL import Image, ImageChops, ImageFilter, ImageStat
    from ..qa_generative import _frame, sample_frames
    width = max(2, round(size[0] * SEAM_HEIGHT / size[1]))
    take_rows, render_rows = [], []
    for frame in sample_frames(frame_count, SEAM_FRAMES):
        mask = Image.open(pattern % frame).convert('L').resize((width, SEAM_HEIGHT)).point(lambda v: 255 if v > 127 else 0)
        ring = ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(2 * SEAM_RING_PX + 1)), mask)
        if not ring.getbbox():
            continue
        for video, rows in ((take, take_rows), (render, render_rows)):
            stat = ImageStat.Stat(_frame(video, frame, width, SEAM_HEIGHT), ring)
            rows.append(list(zip(stat.mean, stat.stddev)))
    if not take_rows:
        return {'gain': [1.0] * 3, 'offset': [0.0] * 3, 'far': False, 'applied': False, 'note': 'no seam on the sampled frames'}
    average = lambda rows: [(sum(r[c][0] for r in rows) / len(rows), sum(r[c][1] for r in rows) / len(rows)) for c in range(3)]  # noqa: E731
    result = colour_transfer(average(take_rows), average(render_rows))
    return {**result, 'applied': not result['far']}


def merge(take, render, pattern, out, frames, size, fps=30, invert=False, grade=None):
    """take outside the masks, render inside them, feathered: ffmpeg maskedmerge in planar RGB, then BT.709 like retime.
    invert: the take inside the masks, the render outside (generate_only). grade: {gain, offset} applied to the render
    first (seam_match), so a kept part sits in the take's colour."""
    width, height = size
    lut = ''
    if grade:
        lut = ',format=rgb24,lutrgb=' + ':'.join(f"{c}='clip(val*{g}+{o},0,255)'" for c, g, o in zip('rgb', grade['gain'], grade['offset']))
    graph = (f'[0:v]scale={width}:{height},format=gbrp[t];[1:v]scale={width}:{height}{lut},format=gbrp[r];'
             f'[2:v]scale={width}:{height},format=gray,{"negate," if invert else ""}gblur=sigma={FEATHER_SIGMA_PX},format=gbrp[m];'
             f'[t][r][m]maskedmerge,{BT709_CHAIN}[v]')
    from ..audio import run_media
    run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(take), '-i', str(render), '-framerate', str(fps),
               '-start_number', '0', '-i', pattern, '-filter_complex', graph, '-map', '[v]', '-frames:v', str(frames),
               *h264_encoder_args(), '-movflags', '+faststart', str(out)])


def keep_take(project, shot_id, take=None, parts=None, reasons=None):
    """Write clip_kept.mp4 for a generated take (default: every complete take of the shot) and record it in clip.json.
    parts: the part ids to keep (default shot.screen.keep); reasons: {part: why} recorded with them (auto_keep)."""
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
        seam = seam_match(manifest['clip_path'], render['clip_path'], masks['pattern'], shot['duration_frames'], masks['size']) \
            if mode == 'keep' else None   # generate_only keeps most of the frame Blender: the take is what must fit, not the render
        merge(manifest['clip_path'], render['clip_path'], masks['pattern'], out, shot['duration_frames'], masks['size'],
              invert=mode == 'generate_only', grade=seam if seam and seam['applied'] else None)
        manifest['kept'] = {'path': str(out), 'sha256': file_hash(out), 'parts': masks['parts'], 'masks': masks['fingerprint'], 'mode': mode,
                            **({'generate_only': (shot.get('screen') or {}).get('generate_only')} if mode == 'generate_only' else {}),
                            **({'reasons': {p: (reasons or {}).get(p, 'declared') for p in masks['parts']}} if mode == 'keep' else {}),
                            **({'seam': seam} if seam else {}), 'render': render['fingerprint'], 'created_at': now()}
        if seam and seam['far']:
            manifest.setdefault('warnings', []).append(
                f"SEAM_COLOUR_FAR: the take's colour around the kept parts is far from the Blender render's (gain {seam['gain']}, "
                f"offset {seam['offset']}); merged ungraded - the kept parts may read as pasted. A look closer to the take, or a "
                'take whose look follows the reference image, closes it')
        write_json(directory / 'clip.json', manifest)
        done.append({'take': directory.name, **manifest['kept']})
    return {'shot_id': shot_id, 'kept': done, 'masks': masks['fingerprint']}


def label_parts(shot):
    """Each label's anchored part (instance/part from its anchor path)."""
    return list(dict.fromkeys('/'.join(l['anchor'].split('/')[:2]) for l in shot.get('labels') or [] if l.get('anchor', '').count('/') >= 1))


def keep_reasons(shot, qa):
    """{part: why} that must be Blender pixels in a take: 'lost' (the take lost it), 'declared' (screen.keep), 'label'
    (a labelled part, unless the whole-frame gate measured anchors), 'decal'. Empty: the take stays 100 % generated."""
    reasons = {}
    for part in (qa.get('parts') or {}).get('lost') or []:
        reasons.setdefault(part, 'lost')
    for part in (shot.get('screen') or {}).get('keep') or []:
        reasons.setdefault(part, 'declared')
    if (qa.get('structure') or {}).get('passed') is not True:
        for part in label_parts(shot):
            reasons.setdefault(part, 'label')
    for decal in (shot.get('scene') or {}).get('decals', []):
        reasons.setdefault(decal['id'], 'decal')
    return reasons


def auto_keep(project, shot_id, take):
    """After a take: put back from Blender pixels only what must be exact - the parts the take lost (qa.parts), the
    declared screen.keep, the labelled parts when the whole-frame gate gave no measured anchors, and decals - and
    nothing when that is empty (the take stays 100 % generated). A shot declaring generate_only keeps its own rule.
    Labels then ride on their kept parts' 3D anchors (exact by construction): anchors_2d.json, anchors_source 'kept'."""
    from .policy import judge, policy_for, project_policy
    path = project_dir(project)
    shot = load_shot(path, shot_id)
    directory = shot_path(path, shot_id).parent / 'generated' / take
    manifest = read_json(directory / 'clip.json')
    screen = shot.get('screen') or {}
    qa = manifest.get('qa') or {}
    if screen.get('generate_only'):
        keep_take(path, shot_id, take)
    else:
        reasons = keep_reasons(shot, qa)
        labels = label_parts(shot) if (qa.get('structure') or {}).get('passed') is not True else []
        if not reasons:
            return {'shot_id': shot_id, 'take': take, 'kept': None}
        keep_take(path, shot_id, take, list(reasons), reasons)
        manifest = read_json(directory / 'clip.json')
        if labels and set(labels) <= set(manifest['kept']['parts']):
            from .control import latest_control
            from ..qa_generative import _anchors_2d
            control = latest_control(path, shot)
            if control and control.get('anchors_path') and Path(control['anchors_path']).is_file():
                rows = read_json(control['anchors_path']).get('frames') or []
                write_json(directory / 'anchors_2d.json', {'schema_version': 1, 'source': 'kept', 'frames': _anchors_2d(rows, {})})
                manifest['anchors_source'] = 'kept'
    manifest = read_json(directory / 'clip.json') if 'anchors_source' not in manifest else manifest
    manifest['policy'] = judge(manifest, policy_for(shot, project_policy(path)))
    write_json(directory / 'clip.json', manifest)
    return {'shot_id': shot_id, 'take': take, 'kept': manifest.get('kept'), 'policy': manifest['policy']}


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


LAYOUT_HEIGHT = 512   # thin layers (a 40 mm screed at room scale) stay a few pixels tall here, at 128 they vanish


def _median(values):
    values = sorted(values)
    return values[len(values) // 2] if values else 0


def part_layout(project, shot_id, parts, frame=None):
    """{part id: {bbox, centre, run_v, run_h} or None when unseen} on one frame (default the middle) of the current
    version - occluders included, one Workbench pass, all in 0..1 of the frame (top-left origin). run_v is the median
    height of the part over the columns it covers, run_h the median width over its rows: a layer's thickness on screen
    even when perspective tilts it (its box would count the tilt). What clip.structure_clause tells a model, measured."""
    from PIL import Image
    shot = load_shot(project_dir(project), shot_id)
    frame = shot['duration_frames'] // 2 if frame is None else frame
    masks = build_keep_masks(project, shot_id, list(parts), frames=[frame], split=True, height=LAYOUT_HEIGHT, allow_unknown=True)
    out = {}
    for part in parts:
        image = Image.open(masks['patterns'][part] % frame).convert('L')
        w, h = image.size
        data = image.tobytes()
        cols, rows, sx, sy, n = [0] * w, [0] * h, 0, 0, 0
        for y in range(h):
            line = data[y * w:(y + 1) * w]
            for x, v in enumerate(line):
                if v > 127:
                    cols[x] += 1; rows[y] += 1; sx += x; sy += y; n += 1
        if n / (w * h) < VISIBLE_SHARE:
            out[part] = None
            continue
        xs = [x for x, c in enumerate(cols) if c]
        ys = [y for y, c in enumerate(rows) if c]
        out[part] = {'bbox': [round(xs[0] / w, 4), round(ys[0] / h, 4), round((xs[-1] + 1) / w, 4), round((ys[-1] + 1) / h, 4)],
                     'centre': [round((sx / n + 0.5) / w, 4), round((sy / n + 0.5) / h, 4)],
                     'run_v': round(_median([c for c in cols if c]) / h, 4), 'run_h': round(_median([r for r in rows if r]) / w, 4),
                     'aspect': round(w / h, 4)}
    return out
