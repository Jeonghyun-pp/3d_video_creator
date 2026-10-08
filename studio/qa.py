"""Technical output checks and quarter-second sheets for separate visual review."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import subprocess

from .audio import run_media
from .common import StudioError, file_hash, read_json, safe_path, write_json
from .project import load_project
from .qa_motion import compare_motion, measure_motion, shot_motion
from .shot_qa import review


def audio_loudness(path: Path) -> dict:
    result = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(path), '-vn', '-af',
                             'loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-'],
                            capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise StudioError('QA_FAILED', result.stderr[-2000:])
    match = re.search(r'\{\s*"input_i".*?\}', result.stderr, re.S)
    if not match:
        raise StudioError('QA_FAILED', 'Could not measure encoded audio loudness')
    values = json.loads(match.group())
    def finite(value):
        number = float(value)
        return number if math.isfinite(number) else None
    return {'integrated_lufs': finite(values['input_i']), 'true_peak_dbtp': finite(values['input_tp']),
            'loudness_range_lu': finite(values['input_lra'])}


def collect_qa(project_dir: Path, candidate_id: str, reference: Path | None = None) -> dict:
    project_dir = Path(project_dir).resolve()
    project = load_project(project_dir)
    directory = safe_path(project_dir, 'final/' + candidate_id)
    manifest = read_json(directory / 'manifest.json')
    video = safe_path(project_dir, manifest['output_path'])
    if not video.is_file() or file_hash(video) != manifest['output_sha256']:
        raise StudioError('CANDIDATE_CHANGED', 'Candidate bytes no longer match immutable manifest')
    sheet_dir = directory / 'qa'
    evidence = review(video, sheet_dir, reference)
    media = evidence['candidate']
    decoded = json.loads(run_media(['ffprobe', '-v', 'error', '-count_frames', '-show_streams', '-of', 'json', str(video)]))
    video_stream = next(s for s in decoded['streams'] if s['codec_type'] == 'video')
    audio_streams = [s for s in decoded['streams'] if s['codec_type'] == 'audio']
    checks = []
    def check(name: str, passed: bool, actual, expected):
        checks.append({'name': name, 'passed': passed, 'actual': actual, 'expected': expected})
    check('resolution', (media['width'], media['height']) == (manifest['width'], manifest['height']),
          [media['width'], media['height']], [manifest['width'], manifest['height']])
    check('fps', abs(media['fps'] - 30) < .001 and abs(media['nominal_fps'] - 30) < .001, media['fps'], 30)
    count = int(video_stream.get('nb_read_frames') or video_stream.get('nb_frames') or 0)
    check('frame_count', count == manifest['frame_count'], count, manifest['frame_count'])
    check('duration', abs(media['duration'] - manifest['frame_count'] / 30) <= 1 / 30 + .005,
          media['duration'], manifest['frame_count'] / 30)
    check('video_codec', media['codec'] == 'h264', media['codec'], 'h264')
    check('pixel_format', media['pixel_format'] == 'yuv420p', media['pixel_format'], 'yuv420p')
    for field in ('color_space', 'color_primaries', 'color_transfer'):
        check(field, media.get(field) == 'bt709', media.get(field), 'bt709')
    check('color_range', media.get('color_range') == 'tv', media.get('color_range'), 'tv')
    check('audio_present', len(audio_streams) == 1, len(audio_streams), 1)
    if audio_streams:
        check('audio_codec', audio_streams[0]['codec_name'] == 'aac', audio_streams[0]['codec_name'], 'aac')
        check('audio_sample_rate', int(audio_streams[0]['sample_rate']) == 48000, audio_streams[0]['sample_rate'], 48000)
        audio_duration = float(audio_streams[0].get('duration') or media['container_duration'])
        check('audio_duration', abs(audio_duration - manifest['duration_seconds']) <= .1, audio_duration, manifest['duration_seconds'])
        loudness = audio_loudness(video)
        narration_present = manifest.get('narration_present', True)
        check('audible_audio', not narration_present or loudness['integrated_lufs'] is not None,
              loudness['integrated_lufs'], 'finite' if narration_present else 'No narration requested')
        check('no_audio_clipping', (not narration_present and loudness['true_peak_dbtp'] is None) or
              (loudness['true_peak_dbtp'] is not None and loudness['true_peak_dbtp'] < 0),
              loudness['true_peak_dbtp'], '<0 dBTP')
    else:
        loudness = None
    # Faststart is a container property; locate atoms without reading large mdat payloads.
    positions = {}
    with video.open('rb') as stream:
        size = video.stat().st_size
        while stream.tell() + 8 <= size:
            offset = stream.tell()
            header = stream.read(8)
            length, kind = int.from_bytes(header[:4], 'big'), header[4:]
            if length == 1:
                length = int.from_bytes(stream.read(8), 'big')
            elif length == 0:
                length = size - offset
            if length < 8:
                break
            positions.setdefault(kind.decode('ascii', errors='replace'), offset)
            stream.seek(offset + length)
    check('faststart', 'moov' in positions and 'mdat' in positions and positions['moov'] < positions['mdat'],
          positions, 'moov before mdat')
    overlay = manifest.get('overlays', {})
    safe = overlay.get('safe_rect_pixels')
    if safe:
        def outside(box, rect):
            return box['bbox'][0] < rect[0] or box['bbox'][1] < rect[1] or box['bbox'][2] > rect[2] or box['bbox'][3] > rect[3]
        rows = overlay.get('text_boxes', [])
        violations = [box for box in rows if box['kind'] != 'title' and outside(box, safe)]
        check('subtitle_safe_area', not violations, violations, 'all text inside configured safe rectangle')
        title_safe = overlay.get('title_safe_rect_pixels')
        if any(box['kind'] == 'title' for box in rows):   # titles have their own (critical text) rectangle
            rect = [math.floor(title_safe[0]), math.floor(title_safe[1]), math.ceil(title_safe[2]), math.ceil(title_safe[3])]
            violations = [box for box in rows if box['kind'] == 'title' and outside(box, rect)]
            check('title_safe_area', not violations, violations[:10], 'every title frame inside the title safe rectangle')
    warnings = list(manifest.get('warnings', [])) + evidence['warnings']
    if manifest.get('ai_generated_shots'):
        warnings.append(f"ai_generated: {manifest['ai_generated_shots']} contain generated video; disclosure required before delivery")
    if manifest['speech_status'] not in ('final', 'not_applicable'):
        warnings.append('voice_not_final: scratch candidate cannot be delivered as master')
    if evidence['candidate_findings']['black_intervals']:
        warnings.append('black_intervals: inspect evidence; this is not an aesthetic failure by itself')
    if evidence['candidate_findings']['freeze_intervals']:
        warnings.append('freeze_intervals: inspect motion; intentional holds may be valid')
    if loudness and loudness['integrated_lufs'] is not None and abs(loudness['integrated_lufs'] + 16) > 1:
        warnings.append('encoded loudness differs from -16 LUFS operating target by more than 1 LU')
    snapshot_path = directory / 'edit.snapshot.json'
    shots = [{'shot_id': row['shot_id'], 'start_frame': row['start_frame'], 'frame_count': row['frame_count'],
              'energy': row.get('shot_snapshot', {}).get('camera', {}).get('energy'), 'style': _shot_style(row.get('shot_snapshot', {}))}
             for row in read_json(snapshot_path).get('shots', [])] if snapshot_path.is_file() else []
    motion = {'shots': shot_motion(video, shots) if shots else [], 'reference': None, 'styles': _style_rows(video, shots)}
    if reference:
        motion['reference'] = compare_motion(video, reference)
    # Style misses are advisory (like energy warnings): they never change technical_pass.
    warnings += [f"motion_style: {row['shot_id']} outside {row['style']} on {', '.join(row['misses'])}" for row in motion['styles'] if row['misses']]
    snapshot_rows = read_json(snapshot_path).get('shots', []) if snapshot_path.is_file() else []
    looks = _look_rows(video, snapshot_rows, project_dir) + _look_rows(video, snapshot_rows, project_dir, 'composition')
    warnings += [w for row in looks for w in row.get('warnings', [])]   # advisory, like motion styles
    motion['look_styles'] = looks
    write_json(sheet_dir / 'motion.json', motion)
    warnings += [w for row in motion['shots'] for w in row['warnings']]
    passed = all(c['passed'] for c in checks)
    visual = {}
    if reference and shots:   # what differs from the reference at each shot's middle, ranked (critique.py); never a gate
        from .critique import critique_videos
        snap = {row['shot_id']: row.get('shot_snapshot') for row in snapshot_rows}
        spans = [(snap.get(s['shot_id']), s['start_frame'] / 30, (s['start_frame'] + s['frame_count']) / 30) for s in shots]
        visual = {'critique': critique_videos(reference, video, [(a + b) / 2 for _, a, b in spans], sheet_dir / 'critique', shots=spans)}
    report = {'schema_version': 1, 'project_id': project['project_id'], 'candidate_id': candidate_id, 'candidate_hash': manifest['output_sha256'],
              'technical_pass': passed, 'status': 'auto_pass' if passed else 'needs_work', 'reviewer_kind': 'agent',
              'technical_checks': checks, 'visual_checks': visual, 'visual_status': 'pending',
              'human_approved': False, 'speech_status': manifest['speech_status'], 'loudness': loudness,
              'contact_sheets': evidence['contact_sheets'], 'sample_count': evidence['sample_count'],
              'findings': evidence['candidate_findings'], 'warnings': warnings,
              'motion': {'shots': [{k: v for k, v in row.items() if k != 'windows_1s'} for row in motion['shots']],
                         'reference_ratio': motion['reference']['ratio'] if motion['reference'] else None},
              'interpretation': 'Technical checks do not establish visual, factual, licensing, or human approval.'}
    write_json(directory / 'qa.json', report)
    return {**report, 'artifacts': [str(directory / 'qa.json'), *report['contact_sheets']]}


def _look_rows(video, rows, project_dir, kind='look'):
    """Per-shot style checks for shots whose style or render names one: look (density, brightness, colour) and
    composition (horizon, sky - over render.composition_span_s of the shot when given). Advisory."""
    from . import composition_style, look_style
    module = look_style if kind == 'look' else composition_style
    style = read_json(project_dir / 'style.json') if (project_dir / 'style.json').is_file() else {}
    out = []
    for row in rows:
        shot = row.get('shot_snapshot', {})
        name = module.style_for(shot, style)
        if not name:
            continue
        start, end = row['start_frame'] / 30, (row['start_frame'] + row['frame_count']) / 30
        span = shot.get('render', {}).get('composition_span_s') if kind == 'composition' else None
        if span:
            start, end = start + span[0], min(end, start + span[1])
        try:
            result = module.check(name, video, start, end)
        except StudioError as error:
            out.append({'shot_id': row['shot_id'], 'style': name, 'kind': kind, 'error': error.code})
            continue
        out.append({'shot_id': row['shot_id'], 'style': name, 'kind': kind, 'misses': result['misses'],
                    'warnings': [f"{row['shot_id']}: {w}" for w in result['warnings']]})
    return out


def _shot_style(shot):
    camera = shot.get('camera', {})
    return (camera.get('move') or {}).get('style') or camera.get('motion_style')


def _style_rows(video, shots):
    """Per-shot motion style check for shots that declare a style (camera.move.style or camera.motion_style)."""
    from .motion_style import check
    rows = []
    for name in sorted({s['style'] for s in shots if s.get('style')}):
        mine = [s for s in shots if s.get('style') == name]
        try:
            result = check(name, video, mine)
        except StudioError as error:
            rows += [{'shot_id': s['shot_id'], 'style': name, 'misses': [], 'error': error.code} for s in mine]
            continue
        rows += [{'shot_id': r['shot_id'], 'style': name, 'misses': r['misses'], 'envelope': r['envelope']} for r in result['rows']]
    return rows


def register_commands(subparsers) -> None:
    command = subparsers.add_parser('qa', help='Collect technical checks and visual evidence')
    sub = command.add_subparsers(dest='qa_command', required=True)
    collect = sub.add_parser('collect')
    collect.add_argument('--project', type=Path, required=True)
    collect.add_argument('--candidate', required=True)
    collect.add_argument('--reference', type=Path)
    collect.set_defaults(handler=lambda args: {'operation': 'qa.collect', **collect_qa(args.project, args.candidate, args.reference)})
    motion = sub.add_parser('motion', help='Screen-motion (speed-feel) metric of a video, optionally against a reference')
    motion.add_argument('--video', type=Path, required=True)
    motion.add_argument('--reference', type=Path)
    motion.add_argument('--reference-start', type=int, default=0)
    motion.add_argument('--frames', type=int)
    motion.set_defaults(handler=lambda args: {'operation': 'qa.motion', 'motion': compare_motion(args.video, args.reference, args.reference_start, args.frames)
                                              if args.reference else measure_motion(args.video, count=args.frames)})
