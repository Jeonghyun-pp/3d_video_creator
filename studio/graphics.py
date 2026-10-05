"""graphics render: a shot's explainer graphics (shot.graphics) as a transparent RGBA frame sequence.

The graphics are built into the scene version (blender_ops/graphics.py) but hidden from the beauty render; this
renders only them (blender_ops/render_graphics.py, EEVEE, meshes as holdouts) into
shots/<id>/graphics/<fingerprint>/frame_NNNNNN.png. The edit lays this layer over the shot, under labels and
captions. The fingerprint covers the scene, the script, the graphics spec and the size, so a re-render happens
only when one of them changes; a complete earlier render is reused.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

from .common import REPO, StudioError, blender_binary, file_hash, now, read_json, run_command, safe_path, stable_hash, write_json
from .project import load_project, load_shot, project_dir, shot_path

SCRIPT = REPO / 'studio' / 'blender_ops' / 'render_graphics.py'
TEXT_SIZE = 0.040    # of frame width (captions use 0.039)
TEXT_OFFSET = 0.035  # of frame width, across the line
DEFAULT_HEIGHT = 1280


def _size(project, height):
    out = project['output']
    return max(2, round(out['width'] * height / out['height'] / 2) * 2), height // 2 * 2


def latest_graphics(path, shot):
    """The newest complete graphics render of the shot's current scene version and graphics spec, or None."""
    rows = []
    for file in (shot_path(path, shot['shot_id']).parent / 'graphics').glob('*/graphics.json'):
        data = read_json(file)
        if data.get('scene_version') == shot['scene_version'] and data.get('spec_hash') == stable_hash(shot.get('graphics') or []):
            rows.append(data)
    return max(rows, key=lambda d: d.get('created_at', '')) if rows else None


def _stamp(directory, texts, style, project_dir, width):
    """Draw dimension values onto the rendered layer frames."""
    if not texts:
        return
    from PIL import Image, ImageDraw
    from .edit import pinned_font
    font, _ = pinned_font(style, project_dir, round(width * TEXT_SIZE))
    for frame, rows in texts.items():
        file = directory / f'frame_{int(frame):06d}.png'
        image = Image.open(file).convert('RGBA')
        w, h = image.size
        draw = ImageDraw.Draw(image)
        for row in rows:
            du, dv = row['du'] * w, row['dv'] * h
            length = max((du * du + dv * dv) ** 0.5, 1e-6)
            nx, ny = -dv / length, du / length          # across the line, on the right-hand side of a->b
            if nx < 0:                                   # keep the value on the right of the line on screen
                nx, ny = -nx, -ny
            x, y = row['u'] * w + nx * w * TEXT_OFFSET, row['v'] * h + ny * w * TEXT_OFFSET
            colour = tuple(round(255 * c) for c in row['color_srgb']) + (255,)
            draw.text((x, y), row['text'], font=font, fill=colour, anchor='lm',
                      stroke_width=max(1, round(font.size * 0.12)), stroke_fill=(20, 20, 24, 230))
        image.save(file)


def render_graphics(project, shot_id, height=DEFAULT_HEIGHT):
    path = project_dir(project)
    project_data = load_project(path)
    shot = load_shot(path, shot_id)
    if not shot.get('graphics'):
        raise StudioError('INPUT_INVALID', f'{shot_id} has no graphics')
    if not shot.get('scene_version'):
        raise StudioError('INPUT_INVALID', 'Build the shot first: graphics are built into the scene version')
    scene = safe_path(shot_path(path, shot_id).parent / 'versions', f"{shot['scene_version']}/scene.blend")
    width, height = _size(project_data, height)
    frames = shot['duration_frames']
    fps = project_data['output']['fps']
    style = read_json(path / 'style.json') if (path / 'style.json').exists() else {}
    fingerprint = stable_hash({'scene': file_hash(scene), 'script': file_hash(SCRIPT), 'stamp': file_hash(Path(__file__)),
                               'font': (style.get('typography') or {}), 'spec': shot['graphics'],
                               'size': [width, height], 'frames': frames, 'fps': fps})[:24]
    directory = shot_path(path, shot_id).parent / 'graphics' / fingerprint
    if (directory / 'graphics.json').is_file() and len(list(directory.glob('frame_*.png'))) == frames:
        return {**read_json(directory / 'graphics.json'), 'status': 'reused'}
    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.graphics-', dir=directory.parent))
    try:
        job = staging / 'job.json'
        write_json(job, {'output_dir': str(staging), 'frames': list(range(frames)), 'width': width, 'height': height, 'fps': fps})
        run_command([blender_binary(), '--background', '--factory-startup', '--disable-autoexec', scene, '--python-exit-code', '1',
                     '--python', SCRIPT, '--', job], staging / 'graphics.log', timeout=7200)
        rendered = len(list(staging.glob('frame_*.png')))
        _stamp(staging, read_json(staging / 'graphics_render.json').get('texts') or {}, style, path, width)
        if rendered != frames:
            raise StudioError('MISSING_FRAMES', f'graphics rendered {rendered}/{frames} frames')
        data = {'schema_version': 1, 'shot_id': shot_id, 'scene_version': shot['scene_version'], 'fingerprint': fingerprint,
                'spec_hash': stable_hash(shot['graphics']), 'width': width, 'height': height, 'frames': frames,
                'frames_dir': str(directory), 'created_at': now(), **read_json(staging / 'graphics_render.json')}
        write_json(staging / 'graphics.json', data)
        if directory.exists():
            shutil.rmtree(directory)
        staging.rename(directory)
    except BaseException:
        if staging.exists():
            staging.rename(staging.with_name('failed_' + staging.name.lstrip('.')))
        raise
    return {**data, 'status': 'complete'}


def register_commands(subparsers):
    parser = subparsers.add_parser('graphics', help='Explainer graphics layer (arrows, dimensions, outlines) rendered on its own')
    commands = parser.add_subparsers(dest='graphics_command', required=True)
    p = commands.add_parser('render')
    p.add_argument('--project', required=True); p.add_argument('--shot', required=True)
    p.add_argument('--height', type=int, default=DEFAULT_HEIGHT)
    p.set_defaults(handler=lambda a: render_graphics(a.project, a.shot, a.height))
