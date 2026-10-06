from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[1]
ID_PATTERN = r'[a-z0-9][a-z0-9_-]*'


class StudioError(Exception):
    def __init__(self, code, message, recovery='', retryable=False, affected_ids=None):
        super().__init__(message)
        self.code, self.message, self.recovery = code, message, recovery
        self.retryable, self.affected_ids = retryable, affected_ids or []

    def as_dict(self):
        return {key: getattr(self, key) for key in ('code', 'message', 'retryable', 'affected_ids', 'recovery')}


def now():
    return datetime.now(timezone.utc).isoformat()


def check_id(value):
    if not isinstance(value, str) or not re.fullmatch(ID_PATTERN, value):
        raise StudioError('INPUT_INVALID', f'Invalid ID: {value!r}')
    return value


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise StudioError('INPUT_INVALID', f'Cannot read JSON {path}: {exc}') from exc


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def stable_hash(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


FONTS = REPO / 'library' / 'fonts'
DEFAULT_FONT = 'library/fonts/pretendard'   # OFL, tracked: the same glyphs on every machine (library/fonts/*/font.json)
SYSTEM_FONTS = ('/System/Library/Fonts/AppleSDGothicNeo.ttc', '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
FONT_SUFFIXES = ('.otf', '.ttf', '.ttc', '.otc')


def _font_location(value, project_dir):
    path = Path(value)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == 'library':   # repository fonts, shared by every project
        return safe_path(REPO, path)
    return safe_path(project_dir, path)


def font_faces(path):
    """Font files of a family: the file itself, or every font file of a family folder (sorted)."""
    path = Path(path)
    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.suffix.lower() in FONT_SUFFIXES)
    return [path] if path.is_file() else []


def font_file(style, project_dir):
    """The text font of a style: typography.font_path (a file or a family folder; 'library/...' resolves in the
    repository), else the bundled default, else a system Korean font. A family folder gives its Regular face."""
    typography = (style or {}).get('typography', {})
    configured = typography.get('font_path') or typography.get('font')
    candidates = ([_font_location(configured, project_dir)] if configured else []) + [REPO / DEFAULT_FONT] + [Path(p) for p in SYSTEM_FONTS]
    for path in candidates:
        faces = font_faces(path)
        if faces:
            regular = [f for f in faces if 'regular' in f.stem.lower()]
            return (regular or faces)[0]
    raise StudioError('FONT_UNAVAILABLE', 'No Korean font: set typography.font_path or restore library/fonts')


def safe_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise StudioError('INPUT_INVALID', f'Path escapes root: {relative}')
    return path


@contextmanager
def lock(path, blocking=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise StudioError('BUSY', f'Another operation holds {path}', retryable=True) from exc
        try:
            yield stream
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def blender_binary():
    candidate = os.environ.get('STUDIO_BLENDER') or shutil.which('blender') or '/Applications/Blender.app/Contents/MacOS/Blender'
    if not Path(candidate).is_file():
        raise StudioError('ENVIRONMENT_MISSING', 'Blender executable was not found', 'Set STUDIO_BLENDER to the installed executable.')
    return candidate


BLENDER_ENV = ('PATH', 'HOME', 'TMPDIR', 'TMP', 'TEMP', 'LANG', 'USER', 'LOGNAME', 'OCIO', 'SYSTEMROOT')
BLENDER_ENV_PREFIXES = ('LC_', 'BLENDER_SYSTEM_', 'BLENDER_USER_')


def blender_env():
    """The environment a Blender process gets: an allow-list, so API keys and tokens (FAL_KEY, OPENAI_API_KEY, cloud
    credentials, anything else) never reach code that runs inside Blender - author scripts included."""
    env = {k: v for k, v in os.environ.items() if k in BLENDER_ENV or k.startswith(BLENDER_ENV_PREFIXES)}
    env['PYTHONPATH'] = ''
    return env


def _is_blender(command):
    try:
        return Path(str(command[0])).resolve() == Path(blender_binary()).resolve()
    except StudioError:
        return False


def run_command(command, log=None, timeout=3600, env=None):
    if env is None and _is_blender(command):   # every Blender we start, whoever starts it
        env = blender_env()
    try:
        result = subprocess.run([str(x) for x in command], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        raise StudioError('TIMEOUT', f'Command timed out after {timeout}s: {command[0]}', retryable=True) from exc
    if log:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        Path(log).write_text(result.stdout, encoding='utf-8')
    if result.returncode:
        raise StudioError('COMMAND_FAILED', f'{command[0]} exited {result.returncode}: {result.stdout[-3000:]}', retryable=True)
    return result.stdout


BT709_CHAIN = 'scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv'


def h264_encoder_args(crf='18', preset=None):
    """h264_args without the filter, for commands that end their own filter_complex with BT709_CHAIN."""
    return [a for a in h264_args(crf, preset) if a not in ('-vf', BT709_CHAIN)]


def source_matrix(stream_color_space):
    """YUV matrix to decode a clip with: its tag, else BT.601 (what untagged pre-2026-10-04 renders used)."""
    return 'bt709' if stream_color_space == 'bt709' else 'bt601'


def h264_args(crf='18', preset=None):
    """Encoder args for sRGB/RGB sources: convert with the BT.709 matrix and tag it.

    ffmpeg's default RGB->YUV matrix is BT.601 and untagged; players then decode with
    BT.709 and shift colours (reds/greens). One helper keeps every encode consistent.
    """
    args = ['-vf', BT709_CHAIN, '-c:v', 'libx264', '-crf', str(crf)]
    if preset:
        args += ['-preset', preset]
    return args + ['-pix_fmt', 'yuv420p', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', '-color_range', 'tv']
