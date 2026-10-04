"""Per-shot narration, immutable audio cache, and conservative subtitle timing."""
from __future__ import annotations

import argparse
import base64
import binascii
from decimal import Decimal, ROUND_HALF_UP
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from .common import StudioError, file_hash, lock, read_json, safe_path, stable_hash, write_json
from .project import load_project, shot_path


def run_media(args: list[str], *, timeout: int = 600) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StudioError('MEDIA_TOOL_FAILED', str(exc)) from exc
    if result.returncode:
        raise StudioError('MEDIA_TOOL_FAILED', result.stderr[-2500:])
    return result.stdout


def probe_audio(path: Path) -> dict:
    data = json.loads(run_media(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))
    streams = [s for s in data.get('streams', []) if s.get('codec_type') == 'audio']
    if len(streams) != 1:
        raise StudioError('INVALID_AUDIO', 'Expected exactly one audio stream')
    duration = float(streams[0].get('duration') or data['format']['duration'])
    if not math.isfinite(duration) or duration <= 0:
        raise StudioError('INVALID_AUDIO', 'Audio duration must be finite and positive')
    return {'duration_seconds': duration, 'sample_rate': int(streams[0]['sample_rate']),
            'channels': streams[0]['channels'], 'codec': streams[0]['codec_name']}


def seconds_to_frame(seconds: float, fps: int) -> int:
    return int((Decimal(str(seconds)) * fps).to_integral_value(rounding=ROUND_HALF_UP))


def make_cues(text: str, alignment: dict | None, duration: float, fps: int,
              requested: list[dict] | None = None) -> tuple[list[dict], list[str]]:
    """Ranges always index alignment text; original display text stays separate."""
    warning = []
    try:
        chars = alignment['characters']
        starts = alignment['character_start_times_seconds']
        ends = alignment['character_end_times_seconds']
        if not chars or not len(chars) == len(starts) == len(ends):
            raise ValueError('array lengths')
        if any(not isinstance(c, str) or len(c) != 1 for c in chars):
            raise ValueError('alignment requires individual characters')
        for i, (start, end) in enumerate(zip(starts, ends)):
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start or end > duration + .1:
                raise ValueError('invalid timestamp')
            if i and (start < starts[i - 1] or end < ends[i - 1]):
                raise ValueError('non-monotonic timestamp')
        normalized = ''.join(chars)
        if requested:
            ranges = requested
        elif normalized == text:
            # ponytail: whitespace/punctuation chunks, explicit mappings for rewritten display text.
            ranges = [{'cue_id': f'cue_{i + 1:03d}', 'character_range': [m.start(), m.end()],
                       'display_text': m.group().strip()} for i, m in enumerate(re.finditer(r'.{1,28}(?:\s+|[.!?。]|$)', normalized))]
            if not ranges or ''.join(normalized[r['character_range'][0]:r['character_range'][1]] for r in ranges) != normalized:
                ranges = [{'cue_id': 'cue_001', 'character_range': [0, len(chars)], 'display_text': text}]
        else:
            ranges = [{'cue_id': 'cue_001', 'character_range': [0, len(chars)], 'display_text': text}]
            warning.append('normalized_text_differs: whole utterance mapped explicitly')
        cues = []
        ids = set()
        for row in ranges:
            start, end = row['character_range']
            cue_id = row['cue_id']
            if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(chars) or cue_id in ids:
                raise ValueError('invalid cue range or duplicate id')
            ids.add(cue_id)
            cues.append({'cue_id': cue_id, 'spoken_text': normalized[start:end],
                         'display_text': row.get('display_text', normalized[start:end]), 'character_range': [start, end],
                         'start_seconds': starts[start], 'end_seconds': ends[end - 1],
                         'start_frame': seconds_to_frame(starts[start], fps),
                         'end_frame': max(seconds_to_frame(starts[start], fps) + 1, seconds_to_frame(ends[end - 1], fps)),
                         'alignment_source': 'tts'})
        return cues, warning
    except (TypeError, KeyError, ValueError, IndexError):
        if alignment:
            warning.append('invalid_alignment: sentence timing only')
        display_text, cue_id = text, 'cue_001'
        if requested:
            row = requested[0]
            if len(requested) == 1 and (row.get('spoken_text') == text or row.get('character_range') == [0, len(text)]):
                display_text, cue_id = row.get('display_text', text), row.get('cue_id', cue_id)
            else:
                warning.append('sentence_only_mapping: only a whole-utterance display override is supported without alignment')
        return [{'cue_id': cue_id, 'spoken_text': text, 'display_text': display_text,
                 'character_range': None, 'start_seconds': 0.0, 'end_seconds': duration,
                 'start_frame': 0, 'end_frame': max(1, math.ceil(duration * fps)),
                 'alignment_source': 'sentence'}], warning


def _eleven_response(text: str, voice: str, settings: dict, destination: Path,
                     previous_text: str | None, next_text: str | None) -> dict:
    cached = destination / 'response.json'
    if cached.is_file():
        return read_json(cached)
    state = destination / 'request.state.json'
    if state.is_file():
        raise StudioError('AUDIO_REQUEST_UNKNOWN', 'Previous paid request has no durable response; inspect before retrying',
                          recovery='Do not automatically repeat an ambiguous paid POST')
    body = {'text': text, 'model_id': 'eleven_multilingual_v2', 'voice_settings': settings}
    if previous_text:
        body['previous_text'] = previous_text
    if next_text:
        body['next_text'] = next_text
    url = 'https://api.elevenlabs.io/v1/text-to-speech/' + urllib.parse.quote(voice, safe='') + '/with-timestamps?output_format=mp3_44100_128'
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                    headers={'xi-api-key': os.environ['ELEVENLABS_API_KEY'], 'Content-Type': 'application/json'})
    write_json(state, {'status': 'request_started', 'provider': 'elevenlabs'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                result = json.loads(response.read())
            write_json(cached, result)  # Persist response before decoding/resampling; never pay again for conversion failures.
            write_json(state, {'status': 'response_saved'})
            return result
        except urllib.error.HTTPError as exc:
            if (exc.code == 429 or 500 <= exc.code < 600) and attempt < 2:
                try:
                    delay = min(30, float(exc.headers.get('Retry-After', 2 ** attempt)))
                except ValueError:
                    delay = 2 ** attempt
                time.sleep(max(0, delay))
                continue
            write_json(state, {'status': 'http_error', 'http_status': exc.code})
            raise StudioError('AUDIO_PROVIDER_FAILED', f'ElevenLabs HTTP {exc.code}') from exc
        except (OSError, ValueError) as exc:
            write_json(state, {'status': 'unknown'})
            raise StudioError('AUDIO_REQUEST_UNKNOWN', 'Paid request response uncertain; cached state preserved') from exc


def _select_audio_manifest(project_dir: Path, shot_id: str, text: str, manifest_path: Path, speech_status: str) -> None:
    relative = str(manifest_path.relative_to(project_dir))
    with lock(project_dir / '.project.lock'):
        current = read_json(shot_path(project_dir, shot_id))
        if current['narration']['text'].strip() == text and (
            current['narration'].get('audio_manifest') != relative or current['narration'].get('speech_status') != speech_status
        ):
            current['narration']['audio_manifest'] = relative
            current['narration']['speech_status'] = speech_status
            current['revision'] += 1
            write_json(shot_path(project_dir, shot_id), current)


def build_audio(project_dir: Path, shot_id: str, mode: str = 'scratch', input_wav: Path | None = None,
                allow_paid: bool = False) -> dict:
    with lock(shot_path(Path(project_dir).resolve(), shot_id).parent / '.audio.lock'):
        result = _build_audio(project_dir, shot_id, mode, input_wav, allow_paid)
        root = Path(project_dir).resolve()
        return {**result, 'project_id': load_project(root)['project_id'],
                'artifacts': [str(safe_path(root, result['wav_path'])), str(safe_path(root, result['alignment_path']))]}


def _build_audio(project_dir: Path, shot_id: str, mode: str, input_wav: Path | None, allow_paid: bool) -> dict:
    project_dir = Path(project_dir).resolve()
    project = load_project(project_dir)
    shot = read_json(shot_path(project_dir, shot_id))
    text = shot['narration']['text'].strip()
    config = project['audio']
    if config['provider'] == 'none' and not input_wav:
        request = {'schema_version': 1, 'provider': 'none', 'frame_count': shot['duration_frames'], 'fps': project['output']['fps']}
        key = stable_hash(request)
        destination = shot_path(project_dir, shot_id).parent / 'audio' / key
        manifest_path = destination / 'audio.json'
        if manifest_path.is_file():
            cached = read_json(manifest_path)
            wav = safe_path(project_dir, cached['wav_path'])
            if not wav.is_file() or file_hash(wav) != cached['wav_sha256']:
                raise StudioError('CACHE_CORRUPT', 'Silent audio cache hash mismatch')
            return {**cached, 'cache_hit': True}
        destination.mkdir(parents=True, exist_ok=True)
        write_json(destination / 'request.snapshot.json', request)
        wav = destination / 'voice.wav'
        duration = shot['duration_frames'] / project['output']['fps']
        run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                   'anullsrc=r=48000:cl=mono', '-t', str(duration), '-c:a', 'pcm_s16le', str(wav)])
        write_json(destination / 'alignment.json', {'alignment': None, 'normalized_alignment': None})
        manifest = {'schema_version': 1, 'request_key': key, 'shot_id': shot_id, 'provider': 'none', 'voice_id': None,
                    'speech_status': 'not_applicable', 'narration_present': False, 'narration_text': '',
                    'wav_path': str(wav.relative_to(project_dir)), 'wav_sha256': file_hash(wav),
                    'alignment_path': str((destination / 'alignment.json').relative_to(project_dir)),
                    **probe_audio(wav), 'cues': [], 'warnings': [], 'minimum_frame_count': shot['duration_frames']}
        write_json(manifest_path, manifest)
        return {**manifest, 'cache_hit': False}
    if not text:
        raise StudioError('INVALID_AUDIO', 'Narration text is empty; set audio.provider=none for a silent scene')
    prior_path = shot['narration'].get('audio_manifest')
    prior = read_json(safe_path(project_dir, prior_path)) if prior_path else None
    configured_source = shot['narration'].get('audio_path')
    source = input_wav or (safe_path(project_dir, configured_source) if configured_source else None)
    if source and not Path(source).is_file():
        raise StudioError('INVALID_AUDIO', f'Imported audio missing: {source}')
    source_hash = file_hash(Path(source)) if source else None

    def cached_audio(path, warnings=None):
        cached = read_json(path)
        wav = safe_path(project_dir, cached['wav_path'])
        if not wav.is_file() or file_hash(wav) != cached['wav_sha256']:
            raise StudioError('CACHE_CORRUPT', 'Audio cache hash mismatch', recovery='Quarantine the corrupt cache before rebuilding')
        alignment_data = read_json(safe_path(project_dir, cached['alignment_path']))
        alignment = alignment_data.get('normalized_alignment') or alignment_data.get('alignment')
        cues, cue_warnings = make_cues(text, alignment, cached['duration_seconds'], project['output']['fps'], shot['narration'].get('cues'))
        _select_audio_manifest(project_dir, shot_id, text, path, cached['speech_status'])
        return {**cached, 'cues': cues, 'warnings': (warnings or []) + cue_warnings,
                'minimum_frame_count': math.ceil((cached['duration_seconds'] + .15) * project['output']['fps']), 'cache_hit': True}

    # An explicitly selected recording is independent of TTS voice/settings.
    if prior and not input_wav and prior.get('provider') == 'import' and prior.get('narration_text') == text:
        original = read_json(safe_path(project_dir, prior_path).parent / 'request.snapshot.json')
        if source is None or original.get('source_hash') == source_hash:
            return cached_audio(safe_path(project_dir, prior_path))

    settings = config.get('settings', {'stability': .5, 'similarity_boost': .75})
    configured_voice = config.get('voice_id') if config['provider'] == 'elevenlabs' else None
    voice = os.environ.get('ELEVENLABS_VOICE_ID') or configured_voice
    warnings = []
    final_request = {'schema_version': 1, 'text': text, 'provider': 'elevenlabs', 'voice_id': voice,
                     'model_id': 'eleven_multilingual_v2', 'settings': settings,
                     'previous_text': config.get('previous_text'), 'next_text': config.get('next_text'), 'source_hash': None}
    wants_final = mode == 'final' or (prior and prior.get('provider') == 'elevenlabs')
    if not source and wants_final and voice:
        if config.get('model_id') not in (None, 'eleven_multilingual_v2'):
            raise StudioError('INPUT_INVALID', 'Only eleven_multilingual_v2 is supported for timestamped narration')
        final_path = shot_path(project_dir, shot_id).parent / 'audio' / stable_hash(final_request) / 'audio.json'
        if final_path.is_file():
            return cached_audio(final_path)
    provider, speech_status = ('import', 'final') if source else ('say', 'scratch')
    if not source and wants_final:
        if mode == 'final' and allow_paid and os.environ.get('ELEVENLABS_API_KEY') and voice:
            provider, speech_status = 'elevenlabs', 'final'
        else:
            warnings.append('voice_not_final: requested voice/settings/context have no matching cache; paid authorization or credentials missing')
    request = final_request if provider == 'elevenlabs' else {
        'schema_version': 1, 'text': text, 'provider': provider,
        'voice_id': 'Yuna' if provider == 'say' else None, 'model_id': None,
        'settings': {}, 'previous_text': None, 'next_text': None, 'source_hash': source_hash}
    voice = request['voice_id']
    key = stable_hash(request)
    destination = shot_path(project_dir, shot_id).parent / 'audio' / key
    manifest_path = destination / 'audio.json'
    if manifest_path.is_file():
        return cached_audio(manifest_path, warnings)
    destination.mkdir(parents=True, exist_ok=True)
    write_json(destination / 'request.snapshot.json', request)
    alignment_data = {'alignment': None, 'normalized_alignment': None}
    if provider == 'say':
        if not shutil.which('say'):
            raise StudioError('VOICE_UNAVAILABLE', 'macOS say with Korean Yuna voice is required')
        text_file = destination / 'narration.txt'
        text_file.write_text(text, encoding='utf-8')
        source = destination / 'scratch.aiff'
        run_media(['say', '-v', 'Yuna', '-f', str(text_file), '-o', str(source)])
    elif provider == 'elevenlabs':
        response = _eleven_response(text, voice, settings, destination, request['previous_text'], request['next_text'])
        source = destination / 'source.mp3'
        try:
            source.write_bytes(base64.b64decode(response['audio_base64'], validate=True))
        except (KeyError, ValueError, binascii.Error) as exc:
            raise StudioError('AUDIO_PROVIDER_FAILED', 'Invalid provider audio; durable response preserved') from exc
        alignment_data = {name: response.get(name) for name in alignment_data}
    elif shot['narration'].get('alignment_path'):
        alignment_data = read_json(safe_path(project_dir, shot['narration']['alignment_path']))
        if 'characters' in alignment_data:
            alignment_data = {'alignment': alignment_data, 'normalized_alignment': alignment_data}
    source_duration = probe_audio(Path(source))['duration_seconds']
    wav = destination / 'voice.wav'
    run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source), '-vn',
               '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', str(wav)])
    info = probe_audio(wav)
    if abs(info['duration_seconds'] - source_duration) > .05:
        raise StudioError('AUDIO_TIMING_CHANGED', 'Resampling changed audio length unexpectedly')
    write_json(destination / 'alignment.json', alignment_data)
    alignment = alignment_data.get('normalized_alignment') or alignment_data.get('alignment')
    cues, cue_warnings = make_cues(text, alignment, info['duration_seconds'], project['output']['fps'], shot['narration'].get('cues'))
    manifest = {'schema_version': 1, 'request_key': key, 'shot_id': shot_id, 'provider': provider,
                'voice_id': voice, 'speech_status': speech_status, 'narration_present': True, 'narration_text': text,
                'wav_path': str(wav.relative_to(project_dir)), 'wav_sha256': file_hash(wav),
                'alignment_path': str((destination / 'alignment.json').relative_to(project_dir)),
                **info, 'cues': cues, 'warnings': warnings + cue_warnings,
                'minimum_frame_count': math.ceil((info['duration_seconds'] + .15) * project['output']['fps'])}
    write_json(manifest_path, manifest)
    _select_audio_manifest(project_dir, shot_id, text, manifest_path, speech_status)
    return {**manifest, 'cache_hit': False}


def register_commands(subparsers) -> None:
    command = subparsers.add_parser('audio', help='Build cached per-shot narration')
    sub = command.add_subparsers(dest='audio_command', required=True)
    build = sub.add_parser('build')
    build.add_argument('--project', type=Path, required=True)
    build.add_argument('--shot', required=True)
    build.add_argument('--mode', choices=['scratch', 'final'], default='scratch')
    build.add_argument('--input-wav', type=Path)
    build.add_argument('--allow-paid', action='store_true')
    build.set_defaults(handler=lambda args: {'operation': 'audio.build', 'status': 'complete',
                       **build_audio(args.project, args.shot, args.mode, args.input_wav, args.allow_paid)})
