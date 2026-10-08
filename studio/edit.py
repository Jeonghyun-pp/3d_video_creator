"""Frame-exact FFmpeg edits with Pillow captions and projected callouts."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil

from PIL import Image, ImageDraw, ImageFont, __version__ as PILLOW_VERSION

from .audio import build_audio, make_cues, run_media
from .common import BT709_CHAIN, REPO, StudioError, font_file, file_hash, h264_args, h264_encoder_args, lock, read_json, safe_path, source_matrix, stable_hash, write_json
from . import titles
from .project import load_project, project_content_hash, shot_path
from .shot_qa import probe

EDIT_VERSION = 2  # 2: BT.709 encode/tag, RGB overlay compositing, generated clips


def pinned_font(style: dict, project_dir: Path, size: int) -> tuple[ImageFont.FreeTypeFont, Path]:
    path = font_file(style, project_dir)
    return ImageFont.truetype(str(path), max(10, size)), path


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, max_lines: int = 2) -> list[str]:
    """Measure glyphs, split at spaces where possible, reject text that cannot fit."""
    lines = []
    line = ''
    for char in text:
        if char == '\n':
            lines.append(line.rstrip())
            line = ''
        elif font.getlength(line + char) <= max_width:
            line += char
        else:
            split = line.rfind(' ')
            if split > 0:
                lines.append(line[:split])
                line = line[split + 1:] + char
            else:
                if not line:
                    raise StudioError('TEXT_OVERFLOW', 'A character exceeds the text box')
                lines.append(line.rstrip())
                line = char
    if line.strip():
        lines.append(line.rstrip())
    if len(lines) > max_lines:
        raise StudioError('TEXT_OVERFLOW', f'Text needs {len(lines)} lines (maximum {max_lines}): {text}')
    return lines


def _safe_rect(style: dict, width: int, height: int) -> tuple[int, int, int, int]:
    value = style.get('safe_rect_normalized', [.07, .10, .86, .78])
    if isinstance(value, dict):
        value = [value.get('x_min', .07), value.get('y_min', .10), value.get('x_max', .86), value.get('y_max', .78)]
    if len(value) != 4 or not 0 <= value[0] < value[2] <= 1 or not 0 <= value[1] < value[3] <= 1:
        raise StudioError('INVALID_STYLE', 'Invalid normalized safe rectangle')
    return tuple(round(v * (width if i % 2 == 0 else height)) for i, v in enumerate(value))


CAPTION_DEFAULTS = {   # style.captions: the subtitle's look; these values are the look every earlier edit had
    'size_frac': .039, 'color_srgb': [247 / 255, 251 / 255, 1.0], 'outline_srgb': [0.0, 0.0, 0.0], 'outline_frac': 0.0,
    'panel': 'rounded', 'panel_srgb': [8 / 255, 15 / 255, 24 / 255], 'panel_alpha': 224 / 255}


def _rgba(srgb, alpha=1.0):
    return tuple(round(max(0.0, min(1.0, v)) * 255) for v in (*srgb, alpha))


def caption_style(style: dict) -> dict:
    extra = set(style.get('captions') or {}) - set(CAPTION_DEFAULTS)
    if extra:
        raise StudioError('INVALID_STYLE', f'captions: unknown keys {sorted(extra)} (reads {sorted(CAPTION_DEFAULTS)})')
    return {**CAPTION_DEFAULTS, **(style.get('captions') or {})}


def _caption_layer(text: str, size: tuple[int, int], font, safe: tuple[int, int, int, int], look: dict | None = None) -> tuple[Image.Image, list[int]]:
    """One subtitle: wrapped text bottom-aligned in the safe rect, on a rounded panel or bare with an outline
    (style.captions). Returns the layer and the box it occupies (the panel, or the text block without one)."""
    look = look or CAPTION_DEFAULTS
    width, height = size
    left, top, right, bottom = safe
    padding = max(6, round(width * .015))
    stroke = round(width * look['outline_frac'])
    lines = wrap_text(text, font, right - left - 2 * padding - 2 * stroke)
    line_height = round(font.size * 1.28)
    panel_height = line_height * len(lines) + padding * 2
    if bottom - panel_height < top:
        raise StudioError('TEXT_OVERFLOW', 'Caption exceeds the safe rectangle')
    layer = Image.new('RGBA', size)
    draw = ImageDraw.Draw(layer)
    panel = [left, bottom - panel_height, right, bottom]
    if look['panel'] == 'rounded':
        draw.rounded_rectangle(panel, radius=padding, fill=_rgba(look['panel_srgb'], look['panel_alpha']))
    for i, line in enumerate(lines):
        x = left + (right - left - font.getlength(line)) / 2
        y = bottom - panel_height + padding + i * line_height
        draw.text((x, y), line, font=font, fill=_rgba(look['color_srgb']), anchor='lt', stroke_width=stroke,
                  stroke_fill=_rgba(look['outline_srgb']))
        bbox = draw.textbbox((x, y), line, font=font, anchor='lt', stroke_width=stroke)
        if bbox[0] < left or bbox[1] < top or bbox[2] > right or bbox[3] > bottom:
            raise StudioError('TEXT_OVERFLOW', 'Measured subtitle glyph box exceeds safe rectangle')
    return layer, panel


def _anchors(path: Path | None) -> dict:
    if path is None:
        return {}
    value = read_json(path)
    rows = value.get('anchors', value.get('records', value.get('frames', []))) if isinstance(value, dict) else value
    result = {}
    for row in rows:
        if 'anchors' in row:  # One record may contain all anchors for a frame.
            for anchor in row['anchors']:
                result[(row['frame'], anchor.get('anchor_id', anchor.get('id')))] = anchor
        else:
            result[(row['frame'], row.get('anchor_id', row.get('anchor', row.get('label_id'))))] = row
    return result


def make_overlays(directory: Path, shots: list[dict], width: int, height: int, style: dict,
                  project_dir: Path) -> dict:
    frame_dir = directory / 'overlays'
    frame_dir.mkdir(parents=True, exist_ok=True)
    caption_look = caption_style(style)
    font, font_path = pinned_font(style, project_dir, round(width * caption_look['size_frac']))
    label_font, _ = pinned_font(style, project_dir, round(width * .029))
    safe = _safe_rect(style, width, height)
    accent_value = style.get('palette_srgb', {}).get('accent', [0.4, 0.88, 0.94])
    accent = tuple(round(max(0, min(1, value)) * 255) for value in accent_value) + (255,) if isinstance(accent_value, list) else accent_value
    scratch = any(entry['audio']['speech_status'] == 'scratch' for entry in shots)
    caption_cache = {}
    raster_cache = {}
    boxes = []
    label_boxes_seen = set()
    warnings = []
    cursor = 0
    title_safe = titles.safe_rect(style, width, height)
    for entry in shots:
        shot = entry['shot']
        masters = {t['title_id']: titles.Master(t, font_path, width) for t in shot.get('titles', [])}
        anchors = _anchors(entry.get('anchors_path'))
        if shot.get('labels') and not anchors:
            warnings.append(f"{shot['shot_id']}: labels hidden; projected anchors unavailable")
        for frame in range(entry['frame_count']):
            active_cues = [c for c in entry['audio']['cues'] if c['start_frame'] <= frame < c['end_frame']]
            if len(active_cues) > 1:
                raise StudioError('TIMING_CONFLICT', 'Subtitle cue intervals overlap')
            text = active_cues[0]['display_text'] if active_cues else ''
            if text and text not in caption_cache:
                caption_cache[text] = _caption_layer(text, (width, height), font, safe, caption_look)
                boxes.append({'kind': 'subtitle', 'shot_id': shot['shot_id'], 'text': text, 'bbox': caption_cache[text][1]})
            active_labels = [label for label in shot.get('labels', []) if label['start_frame'] <= frame < label['end_frame']]
            if len(active_labels) > 2:
                raise StudioError('LABEL_LIMIT', 'At most two simultaneous labels are supported')
            resolved = []
            for label in active_labels:
                row = anchors.get((frame, label['anchor'])) or anchors.get((frame, label['label_id']))
                if not row:
                    continue
                u, v = row.get('u'), row.get('v')
                if not isinstance(u, (int, float)) or not isinstance(v, (int, float)):
                    continue
                if not row.get('visible', True) or row.get('depth', 1) <= 0 or not 0 <= u <= 1 or not 0 <= v <= 1:
                    continue
                occluded = row.get('occluded', False)
                if occluded and label.get('occlusion_policy', 'hide') == 'hide':
                    continue
                resolved.append((label, round(u * width), round(v * height), occluded))
            graphic = Path(entry['graphics_dir']) / f'frame_{frame:06d}.png' if entry.get('graphics_dir') else None
            title_states = [t for t in (titles.state(title, frame) for title in shot.get('titles', [])) if t]
            signature = stable_hash({'text': text, 'labels': resolved, 'graphic': file_hash(graphic) if graphic else None,
                                     **({'titles': [shot['shot_id'], title_states]} if title_states else {})})
            output = frame_dir / f'{cursor + frame + 1:06d}.png'
            if signature in raster_cache:
                if not output.exists():
                    os.link(raster_cache[signature], output)
                continue
            # explainer graphics (own render layer) under titles, captions and labels
            image = Image.open(graphic).convert('RGBA').resize((width, height), Image.LANCZOS) if graphic else Image.new('RGBA', (width, height))
            if title_states:
                boxes += titles.render_titles(shot['titles'], frame, masters, width, height, title_safe, image, shot['shot_id'])
            if text:
                image.alpha_composite(caption_cache[text][0])
            draw = ImageDraw.Draw(image)
            if scratch:
                badge = 'SCRATCH VOICE'
                badge_font, _ = pinned_font(style, project_dir, round(width * .018))
                badge_y = safe[1] - badge_font.size * 2
                draw.text((safe[0], max(3, badge_y)), badge, font=badge_font, fill=(230, 234, 240, 175), anchor='lt')
            occupied_slots = set()
            for label, x, y, occluded in resolved:
                slot = label.get('slot', 'upper_left')
                on_anchor = label.get('placement', 'slot') == 'anchor'   # a tag on the thing itself: the box follows its anchor
                if not on_anchor:
                    if slot in occupied_slots:
                        raise StudioError('LABEL_OVERLAP', 'Simultaneous labels must use distinct slots')
                    occupied_slots.add(slot)
                slot_style = style.get('label_slots', {}).get(slot, {})
                if not isinstance(slot_style, dict) or any(not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1 for value in slot_style.values()):
                    raise StudioError('INVALID_STYLE', 'Label slot coordinates must be finite normalized numbers')
                if set(slot_style) - {'x', 'y', 'width'}:
                    raise StudioError('INVALID_STYLE', 'Label slots accept x, y and width')
                label_width = min(round(width * slot_style.get('width', .34)), safe[2] - safe[0])
                if label_width < 1:
                    raise StudioError('INVALID_STYLE', 'Label slot width must be positive')
                padding = max(5, round(width * .012))
                lines = wrap_text(label['text'], label_font, label_width - 2 * padding)
                box_height = round(label_font.size * 1.3) * len(lines) + padding * 2
                if on_anchor:   # as wide as its text, centred on the projected anchor
                    label_width = min(label_width, round(max(label_font.getlength(line) for line in lines)) + 2 * padding)
                    lx, ly = x - label_width // 2, y - box_height // 2
                else:
                    lx = safe[0] if 'left' in slot else safe[2] - label_width
                    ly = safe[1] + (round(height * .18) if 'lower' in slot else 0)
                    if 'x' in slot_style:
                        lx = round(width * slot_style['x'])
                    if 'y' in slot_style:
                        ly = round(height * slot_style['y'])
                bbox = [lx, ly, lx + label_width, ly + box_height]
                if label.get('leader', not on_anchor):
                    endpoint = (lx + label_width if 'left' in slot else lx, ly + box_height // 2)
                    if occluded:
                        distance = math.hypot(endpoint[0] - x, endpoint[1] - y)
                        for step in range(0, max(1, round(distance)), 12):
                            a, b = step / max(1, distance), min(step + 6, distance) / max(1, distance)
                            draw.line([(x + (endpoint[0]-x)*a, y + (endpoint[1]-y)*a),
                                       (x + (endpoint[0]-x)*b, y + (endpoint[1]-y)*b)], fill=accent, width=max(1, width // 360))
                    else:
                        draw.line([(x, y), endpoint], fill=accent, width=max(1, width // 360))
                    draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=accent)
                fill = _rgba(label['fill_srgb']) if label.get('fill_srgb') else (8, 20, 31, 225)
                draw.rounded_rectangle(bbox, radius=padding, fill=fill, outline=fill if label.get('fill_srgb') else accent)
                for i, line in enumerate(lines):
                    draw.text((lx + padding, ly + padding + i * round(label_font.size * 1.3)), line,
                              font=label_font, fill=_rgba(label['text_srgb']) if label.get('text_srgb') else 'white', anchor='lt')
                if bbox[0] < safe[0] or bbox[1] < safe[1] or bbox[2] > safe[2] or bbox[3] > safe[3]:
                    raise StudioError('TEXT_OVERFLOW', 'Label exceeds safe rectangle')
                if text and bbox[3] > caption_cache[text][1][1]:
                    raise StudioError('LABEL_OVERLAP', 'Label overlaps subtitle panel')
                box_key = (shot['shot_id'], label['label_id'], label['text'], tuple(bbox))
                if box_key not in label_boxes_seen:
                    label_boxes_seen.add(box_key)
                    boxes.append({'kind': 'label', 'shot_id': shot['shot_id'], 'label_id': label['label_id'],
                                  'text': label['text'], 'bbox': bbox})
            image.save(output)
            raster_cache[signature] = output
        cursor += entry['frame_count']
    expected = [frame_dir / f'{i:06d}.png' for i in range(1, cursor + 1)]
    if any(not p.is_file() for p in expected) or len(list(frame_dir.glob('*.png'))) != cursor:
        raise StudioError('MISSING_FRAMES', 'Overlay sequence is incomplete')
    return {'frame_count': cursor, 'frames_dir': str(frame_dir), 'font_path': str(font_path),
            'font_sha256': file_hash(font_path), 'safe_rect_pixels': list(safe), 'text_boxes': boxes,
            'title_safe_rect_pixels': [round(v, 2) for v in title_safe],
            'warnings': warnings, 'unique_rasters': len(raster_cache),
            'sequence_hash': stable_hash([file_hash(p) for p in expected])}


def latest_generated(project_dir: Path, shot: dict, frame_count: int) -> dict | None:
    """Selected usable generated clip of a generative/hybrid shot (several takes need an explicit selection).
    Takes the shot's role does not allow (policy.judge) are never chosen; {'rejected': reasons} when every take
    is unusable, None when there is no take."""
    from .generative.policy import judge, policy_for, project_policy
    spec = shot['route']['generative']
    policy = policy_for(shot, project_policy(project_dir))   # overlays re-judged now: a later label voids a pick
    selection = spec.get('selection') or {}
    picked = lambda key: selection.get('take') == key and bool(selection.get('user_words'))  # noqa: E731
    clips, rejected = [], []
    for manifest_path in sorted((shot_path(project_dir, shot['shot_id']).parent / 'generated').glob('*/clip.json')):
        manifest = read_json(manifest_path)
        if manifest.get('status') == 'complete' and manifest.get('frame_count') == frame_count:
            verdict = judge(manifest, policy, picked(manifest_path.parent.name))
            if verdict['usable']:
                clips.append((manifest_path.parent.name, manifest_path, manifest))
            else:
                rejected.append(f"{manifest_path.parent.name}: {'; '.join(verdict['reasons'])}")
    if not clips:
        return {'rejected': rejected} if rejected else None
    if len(clips) > 1 and not spec.get('selected_take'):
        raise StudioError('GENERATION_SELECTION_REQUIRED', f"{shot['shot_id']}: {len(clips)} generated takes; run generate select")
    key = spec.get('selected_take') or clips[0][0]
    chosen = next((c for c in clips if c[0] == key), None)
    if chosen is None:
        raise StudioError('GENERATION_SELECTION_REQUIRED', f"{shot['shot_id']}: selected take {key} not found or not usable for a {policy['role']} shot")
    clip = Path(chosen[2]['clip_path'])
    if file_hash(clip) != chosen[2]['clip_sha256']:
        raise StudioError('CACHE_CORRUPT', f"Generated clip hash mismatch: {shot['shot_id']}")
    kept = chosen[2].get('kept')   # the take with the shot's kept parts put back from Blender (generative/keep.py)
    only = (shot.get('screen') or {}).get('generate_only')
    fits = (kept.get('mode') == 'generate_only' and kept.get('generate_only') == only) if (kept and only) else \
        bool(kept) and kept.get('mode', 'keep') == 'keep' and set((shot.get('screen') or {}).get('keep') or []) <= set(kept['parts'])
    if kept and fits:   # it keeps at least what the shot asks (or generates exactly the regions it hands the model)
        if file_hash(Path(kept['path'])) != kept['sha256']:
            raise StudioError('CACHE_CORRUPT', f"Kept clip hash mismatch: {shot['shot_id']}")
        clip = Path(kept['path'])
    return {'manifest': chosen[2], 'manifest_path': chosen[1], 'clip': clip, 'generated': True,
            'warnings': [f"{shot['shot_id']}: {w}" for w in judge(chosen[2], policy, picked(chosen[0]))['warnings']]}


def latest_render(project_dir: Path, shot: dict, frame_count: int, profile: str) -> dict:
    mode = (shot.get('route') or {}).get('mode', 'blender')
    if mode != 'blender':
        generated = latest_generated(project_dir, shot, frame_count)
        if generated and 'rejected' not in generated:
            return generated
        rejected = (generated or {}).get('rejected')
        if mode == 'generative':
            if rejected:
                raise StudioError('GENERATION_NOT_USABLE', f"{shot['shot_id']}: no generated take is usable: {rejected}",
                                  recovery='Regenerate, change the route, or set the shot role deliberately')
            raise StudioError('RENDER_REQUIRED', f"No generated clip for generative shot {shot['shot_id']}")
    candidates = []
    for manifest_path in (shot_path(project_dir, shot['shot_id']).parent / 'renders').glob('*/render.json'):
        manifest = read_json(manifest_path)
        if manifest.get('status') not in ('complete', 'completed') or manifest.get('scene_version') != shot.get('scene_version'):
            continue
        if manifest.get('frame_count') != frame_count:
            continue
        clip = Path(manifest.get('clip_path') or manifest_path.parent / 'clip.mp4')
        if not clip.is_absolute():
            clip = safe_path(project_dir, str(clip))
        if not clip.is_file():
            continue
        if manifest.get('clip_sha256') and file_hash(clip) != manifest['clip_sha256']:
            raise StudioError('CACHE_CORRUPT', f"Rendered clip hash mismatch: {shot['shot_id']}")
        candidates.append((manifest.get('profile') == ('final' if profile == 'candidate' else 'preview'), manifest_path.stat().st_mtime_ns,
                           manifest_path, manifest, clip))
    if not candidates:
        raise StudioError('RENDER_REQUIRED', f"No complete render of current scene for {shot['shot_id']}")
    _, _, path, render, clip = max(candidates, key=lambda item: item[:2])
    warnings = []
    if mode == 'hybrid':
        warnings.append(f"{shot['shot_id']}: no usable generated take ({'; '.join(rejected)}); using the Blender motion pass (reject_route)"
                        if rejected else f"{shot['shot_id']}: hybrid shot has no generated clip yet; using the Blender motion pass")
    return {'manifest': render, 'manifest_path': path, 'clip': clip, 'generated': False, 'warnings': warnings}


def _srt(cues: list[dict], fps: int) -> str:
    def stamp(frame):
        ms = round(frame / fps * 1000)
        return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'
    return '\n'.join(f"{i + 1}\n{stamp(c['start_frame'])} --> {stamp(c['end_frame'])}\n{c['display_text']}\n" for i, c in enumerate(cues))


MUSIC_DEFAULTS = {'gain_db': -14.0, 'duck_ratio': 10.0, 'fade_in_s': 0.6, 'fade_out_s': 1.2, 'start_s': 0.0}
SFX_GAIN_DB = -4.0


def sound_events(shots: list[dict], fps: int) -> list[dict]:
    """Every shot's sound effects (shot.sfx) at their time in the reel: a frame of the shot, or a narration cue's start,
    plus offset_frames."""
    events, cursor = [], 0
    for entry in shots:
        shot = entry['shot']
        cues = {c['cue_id']: c for c in entry['audio'].get('cues', [])}
        for row in shot.get('sfx', []):
            if 'cue' in row:
                if row['cue'] not in cues:
                    raise StudioError('TIMING_CONFLICT', f"{shot['shot_id']}: sfx {row['id']} names cue {row['cue']}, which the narration does not have "
                                      f"(cues: {sorted(cues)})")
                frame = cues[row['cue']]['start_frame']
            else:
                frame = row.get('frame', 0)
            frame = max(0, min(entry['frame_count'] - 1, frame + row.get('offset_frames', 0)))
            events.append({'id': f"{shot['shot_id']}/{row['id']}", 'sound': row['sound'], 'seconds': round((cursor + frame) / fps, 4),
                           'gain_db': row.get('gain_db', SFX_GAIN_DB)})
        cursor += entry['frame_count']
    return events


def sound_mix(voice: Path, music: dict | None, events: list[dict], total_seconds: float, destination: Path) -> dict:
    """Voice + a music bed ducked under it (sidechain compression keyed by the voice) + effects at their times, before
    loudness normalisation. Returns what went in (sound ids, hashes) for the manifest."""
    from .sounds import load
    inputs, graph, labels, used = ['-i', str(voice)], ['[0:a]aformat=channel_layouts=stereo,asplit=2[v][vkey]'], ['[v]'], []
    if music:
        settings = {**MUSIC_DEFAULTS, **music}
        manifest, path = load(settings['sound'])
        inputs += ['-stream_loop', '-1', '-i', str(path)]
        fade_out = max(0.0, total_seconds - settings['fade_out_s'])
        graph.append(f"[1:a]atrim=start={settings['start_s']}:duration={total_seconds},asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo,"
                     f"afade=t=in:d={settings['fade_in_s']},afade=t=out:st={fade_out}:d={settings['fade_out_s']},volume={settings['gain_db']}dB[mus]")
        graph.append(f"[mus][vkey]sidechaincompress=threshold=0.008:ratio={settings['duck_ratio']}:knee=2:attack=10:release=400[duck]")
        labels.append('[duck]')
        used.append({'role': 'music', 'sound': settings['sound'], 'sha256': manifest['sha256'], 'licence': manifest['source']['license_id']})
    else:
        graph.append('[vkey]anullsink')
    for event in events:
        manifest, path = load(event['sound'])
        index = len([a for a in inputs if a == '-i'])
        inputs += ['-i', str(path)]
        ms = int(round(event['seconds'] * 1000))
        graph.append(f"[{index}:a]aformat=channel_layouts=stereo,adelay={ms}|{ms},volume={event['gain_db']}dB[s{index}]")
        labels.append(f'[s{index}]')
        used.append({'role': 'sfx', 'id': event['id'], 'sound': event['sound'], 'at_s': event['seconds'], 'sha256': manifest['sha256'],
                     'licence': manifest['source']['license_id']})
    graph.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=first,alimiter=limit=0.89[out]")
    run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *inputs, '-filter_complex', ';'.join(graph), '-map', '[out]',
               '-t', f'{total_seconds:.4f}', '-ar', '48000', '-c:a', 'pcm_s16le', str(destination)])
    return {'sounds': used}


def _loudnorm(source: Path, destination: Path) -> dict:
    # FFmpeg analysis reports to stderr; keep stdout reserved for CLI JSON.
    import subprocess
    result = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(source), '-af',
                             'loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-'],
                            capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise StudioError('MEDIA_TOOL_FAILED', result.stderr[-2000:])
    match = re.search(r'\{\s*"input_i".*?\}', result.stderr, re.S)
    if not match:
        raise StudioError('LOUDNESS_FAILED', 'No loudnorm analysis result')
    measured = json.loads(match.group())
    if any(not math.isfinite(float(measured[key])) for key in ['input_i', 'input_tp', 'input_lra', 'input_thresh', 'target_offset']):
        raise StudioError('LOUDNESS_FAILED', 'Narration is silent or loudness cannot be measured')
    options = ':'.join(f'{name}={measured[key]}' for name, key in [('measured_I', 'input_i'), ('measured_TP', 'input_tp'),
                                                                ('measured_LRA', 'input_lra'), ('measured_thresh', 'input_thresh'),
                                                                ('offset', 'target_offset')])
    run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source), '-af',
               f'loudnorm=I=-16:TP=-1.5:LRA=11:{options}:linear=true', '-ar', '48000', '-c:a', 'pcm_s16le', str(destination)])
    return {'target_lufs': -16, 'target_true_peak_dbtp': -1.5, 'analysis': measured}


def build_edit(project_dir: Path, profile: str = 'rough') -> dict:
    with lock(Path(project_dir).resolve() / '.edit.lock'):
        result = _build_edit(project_dir, profile)
        root = Path(project_dir).resolve()
        return {**result, 'artifacts': [str(safe_path(root, result['output_path'])),
                str(root / 'final' / result['candidate_id'] / 'manifest.json'),
                str(root / 'final' / result['candidate_id'] / 'edit.snapshot.json')]}


def _code_hash(module) -> str:
    """The module's source plus the studio modules it imports directly (titles draws with camera_rig_core's curves)."""
    import inspect
    import sys as _sys
    files = {Path(module.__file__)}
    for value in vars(module).values():
        owner = value if inspect.ismodule(value) else _sys.modules.get(getattr(value, '__module__', None) or '')
        if owner is not None and owner.__name__.startswith('studio.') and getattr(owner, '__file__', None):
            files.add(Path(owner.__file__))
    return stable_hash({str(f.relative_to(REPO)): file_hash(f) for f in sorted(files)})


def _build_edit(project_dir: Path, profile: str) -> dict:
    project_dir = Path(project_dir).resolve()
    project = load_project(project_dir)
    fps = project['output']['fps']
    if fps != 30:
        raise StudioError('OUTPUT_SPEC', 'Version 1 edits require 30fps')
    style_path = Path(REPO) / 'library' / 'styles' / f"{project['style_id']}.json"
    style = read_json(project_dir / 'style.json') if (project_dir / 'style.json').is_file() else (read_json(style_path) if style_path.is_file() else {})
    shots = []
    snapshot_shots = []
    global_cues = []
    for timing in project['shots']:
        shot = read_json(shot_path(project_dir, timing['shot_id']))
        render = latest_render(project_dir, shot, timing['frame_count'], profile)
        voice = build_audio(project_dir, shot['shot_id'], 'final' if project['audio'].get('speech_status') == 'final' else 'scratch')
        shot = read_json(shot_path(project_dir, shot['shot_id']))
        if any(action.get('time_binding') for action in shot.get('actions', [])):
            from .timing import resolve_actions
            _, timing_changes = resolve_actions(shot, voice['cues'])
            if timing_changes:
                raise StudioError('TIMING_CONFLICT', f"{shot['shot_id']}: speech-bound actions differ from current rendered motion",
                                  recovery='Run timing resolve, then render the new scene version before editing')
        if voice['minimum_frame_count'] > timing['frame_count']:
            raise StudioError('TIMING_CONFLICT', f"{shot['shot_id']}: speech requires {voice['minimum_frame_count']} frames; shot has {timing['frame_count']}",
                              recovery='Shorten narration or extend the shot; audio is never cut')
        clip_info = probe(render['clip'])
        streams = json.loads(run_media(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_frames',
                                      '-show_entries', 'stream=nb_read_frames', '-of', 'json', str(render['clip'])]))['streams']
        if not streams or int(streams[0].get('nb_read_frames', 0)) != timing['frame_count']:
            raise StudioError('MISSING_FRAMES', f"{shot['shot_id']}: base clip frame count differs from timeline")
        if profile == 'candidate' and (clip_info['width'] != project['output']['width'] or clip_info['height'] != project['output']['height']):
            raise StudioError('FINAL_RENDER_REQUIRED', f"{shot['shot_id']}: candidate requires native output resolution; lower-resolution sources cannot be upscaled")
        if abs(clip_info['duration'] - timing['frame_count'] / fps) > 1 / fps + .005 or abs(clip_info['fps'] - fps) > .001:
            raise StudioError('RENDER_INVALID', f"{shot['shot_id']}: clip timing differs from project")
        candidates = [render['manifest_path'].parent / 'anchors.json']
        if shot.get('scene_version'):   # generative shots have no 3D version to anchor to
            candidates.append(shot_path(project_dir, shot['shot_id']).parent / 'versions' / shot['scene_version'] / 'anchors.json')
        anchors_path = next((p for p in candidates if p.is_file()), None)
        if shot.get('labels') and render.get('generated'):
            # Generated pixels do not follow 3D anchors; only measured 2D anchors may carry labels.
            anchors_2d = render['manifest_path'].parent / 'anchors_2d.json'
            if not anchors_2d.is_file():
                raise StudioError('LABELS_UNSUPPORTED', f"{shot['shot_id']}: labels on a generated clip need anchors_2d.json from structure QA",
                                  recovery='Remove labels, or run hybrid structure QA (anchor error <= 1 % width) to produce 2D anchors')
            anchors_path = anchors_2d
        elif shot.get('labels'):
            scene_path = shot_path(project_dir, shot['shot_id']).parent / 'versions' / shot['scene_version'] / 'scene.blend'
            if scene_path.is_file():
                from .blender import export_anchors
                anchors_path = Path(export_anchors(project_dir, shot['shot_id'])['anchors_path'])
            elif anchors_path is None:
                raise StudioError('SCENE_INVALID', 'Labels require a scene snapshot or supplied projected anchors')
        graphics_dir = None
        if shot.get('graphics'):
            if render.get('generated') and not (render['manifest_path'].parent / 'anchors_2d.json').is_file():
                raise StudioError('GRAPHICS_UNSUPPORTED', f"{shot['shot_id']}: graphics on a generated clip need anchors_2d.json from structure QA",
                                  recovery='Remove the graphics, or pass hybrid structure QA first')
            from .graphics import latest_graphics
            layer = latest_graphics(project_dir, shot)
            if layer is None:
                raise StudioError('GRAPHICS_NOT_RENDERED', f"{shot['shot_id']}: graphics layer missing for {shot['scene_version']}",
                                  recovery=f"Run graphics render --project {project_dir} --shot {shot['shot_id']}")
            graphics_dir = layer['frames_dir']
        graphics_layer = stable_hash(layer) if graphics_dir else None
        entry = {'shot': shot, 'audio': voice, 'clip': render['clip'], 'frame_count': timing['frame_count'], 'anchors_path': anchors_path,
                 'graphics_dir': graphics_dir,
                 'generated': render.get('generated', False), 'warnings': render.get('warnings', []),
                 'matrix': source_matrix(clip_info.get('color_space'))}
        shots.append(entry)
        snapshot_shots.append({'shot_id': shot['shot_id'], 'start_frame': timing['start_frame'], 'frame_count': timing['frame_count'],
                               'clip_path': str(render['clip'].relative_to(project_dir)), 'clip_sha256': file_hash(render['clip']),
                               'shot_snapshot': shot, 'audio': voice, 'anchors_sha256': file_hash(anchors_path) if anchors_path else None,
                               **({'graphics_layer_sha256': graphics_layer} if graphics_layer else {})})
        global_cues.extend({**cue, 'start_frame': cue['start_frame'] + timing['start_frame'],
                            'end_frame': cue['end_frame'] + timing['start_frame']} for cue in voice['cues'])
    width, height = project['output']['width'], project['output']['height']
    if profile == 'rough':
        scale = min(1, 540 / width)
        width, height = round(width * scale / 2) * 2, round(height * scale / 2) * 2
    _, font_path = pinned_font(style, project_dir, round(width * .039))
    total_frames = sum(s['frame_count'] for s in shots)
    from . import facts
    fact_problems = facts.check(project_dir, [s['shot'] for s in shots])
    if profile == 'candidate':
        from .decisions import require
        require(project_dir, 'candidate')
        if fact_problems:
            facts.require(project_dir, [s['shot'] for s in shots])   # raises with the first problem
    snapshot = {'schema_version': 1, 'edit_version': EDIT_VERSION, 'edit_script_sha256': file_hash(Path(__file__)),
                'sources_sha256': facts.sources_sha256(project_dir),
                **({'titles_sha256': _code_hash(titles)} if any(s['shot'].get('titles') for s in shots) else {}),
                'pillow_version': PILLOW_VERSION, 'profile': profile, 'project_revision': project['revision'],
                'project_content_hash': project_content_hash(project),
                'output': {'width': width, 'height': height, 'fps': fps, 'frame_count': total_frames},
                'shots': snapshot_shots, 'style_snapshot': style, 'font_sha256': file_hash(font_path),
                'ffmpeg_version': run_media(['ffmpeg', '-version']).splitlines()[0], 'encoder': stable_hash(h264_args())}
    named = {row['sound'] for s in shots for row in s['shot'].get('sfx', [])} | ({project['audio']['music']['sound']} if (project.get('audio') or {}).get('music') else set())
    if named:   # a library sound replaced by a new version is a different edit
        from .sounds import load as load_sound
        snapshot['sounds'] = {name: load_sound(name)[0]['sha256'] for name in sorted(named)}
    # Runtime cache-hit flags must never invalidate content-derived edits.
    for row in snapshot_shots:
        row['audio'] = {key: value for key, value in row['audio'].items() if key not in ('cache_hit', 'artifacts')}
    key = stable_hash(snapshot)
    directory = project_dir / 'edit' / key
    candidate_id = f'{profile}_{key[:16]}'
    final_dir = project_dir / 'final' / candidate_id
    manifest_path = final_dir / 'manifest.json'
    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        output = safe_path(project_dir, manifest['output_path'])
        if not output.is_file() or file_hash(output) != manifest['output_sha256']:
            raise StudioError('CACHE_CORRUPT', 'Immutable candidate hash mismatch')
        return {**manifest, 'cache_hit': True}
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / 'edit.snapshot.json', snapshot)
    for entry, frozen in zip(shots, snapshot_shots):
        if entry['anchors_path']:
            anchor_copy = directory / (entry['shot']['shot_id'] + '.anchors.snapshot.json')
            shutil.copy2(entry['anchors_path'], anchor_copy)
            if file_hash(anchor_copy) != frozen['anchors_sha256']:
                raise StudioError('INPUT_CHANGED', 'Projected anchors changed while taking edit snapshot')
            entry['anchors_path'] = anchor_copy
    overlays = make_overlays(directory, shots, width, height, style, project_dir)
    write_json(directory / 'overlay.json', overlays)
    segments = []
    voice_segments = []
    for i, entry in enumerate(shots):
        segment = directory / f'video_{i:03d}.mp4'
        if not segment.is_file():
            run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(entry['clip']), '-an', '-vf',
                       f"scale={width}:{height}:force_original_aspect_ratio=decrease:in_color_matrix={entry['matrix']}:in_range=tv:out_color_matrix=bt709:out_range=tv,"
                       f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,{BT709_CHAIN}",
                       '-frames:v', str(entry['frame_count']), *h264_encoder_args(preset='fast'), str(segment)])
        segments.append(segment)
        voice_segment = directory / f'voice_{i:03d}.wav'
        run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(safe_path(project_dir, entry['audio']['wav_path'])),
                   '-af', f"apad=whole_len={entry['frame_count'] * 48000 // fps}", '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', str(voice_segment)])
        voice_segments.append(voice_segment)
    # Paths in concat manifests are relative generated names, never user-provided strings.
    concat = directory / 'video.concat.txt'
    concat.write_text(''.join(f"file '{p.name}'\n" for p in segments), encoding='utf-8')
    joined = directory / 'joined.mp4'
    run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '1', '-i', str(concat), '-c', 'copy', str(joined)])
    audio_concat = directory / 'audio.concat.txt'
    audio_concat.write_text(''.join(f"file '{p.name}'\n" for p in voice_segments), encoding='utf-8')
    voice_track = directory / 'voice_track.wav'
    run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '1', '-i', str(audio_concat), '-c', 'copy', str(voice_track)])
    mixed = directory / 'mix.wav'
    narration_present = any(s['audio'].get('narration_present', True) for s in shots)
    music = (project.get('audio') or {}).get('music')
    events = sound_events(shots, fps)
    sound_record = None
    if music or events:   # a music bed under the voice and effects on their events, then the same loudness target
        bed = directory / 'sound_mix.wav'
        sound_record = sound_mix(voice_track, music, events, sum(s['frame_count'] for s in shots) / fps, bed)
        voice_track = bed
    if narration_present or sound_record:
        loudness = _loudnorm(voice_track, mixed)
    else:
        shutil.copy2(voice_track, mixed)
        loudness = {'status': 'not_applicable', 'reason': 'No narration requested'}
    final_voice = all(s['audio']['speech_status'] in ('final', 'not_applicable') for s in shots)
    filename = 'candidate.mp4' if final_voice else 'scratch_candidate.mp4'
    final_dir.mkdir(parents=True, exist_ok=True)
    output = final_dir / filename
    temporary = directory / 'candidate.tmp.mp4'
    run_media(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(joined),
               '-framerate', '30', '-start_number', '1', '-i', str(directory / 'overlays' / '%06d.png'), '-i', str(mixed),
               # Composite in RGB, then convert to BT.709 YUV exactly once.
               '-filter_complex', f'[0:v]scale=in_color_matrix=bt709:in_range=tv,format=gbrp[base];[1:v]format=gbrap[text];'
                                  f'[base][text]overlay=0:0:shortest=1:format=gbrp,{BT709_CHAIN}[v]', '-map', '[v]', '-map', '2:a:0',
               '-frames:v', str(total_frames), *h264_encoder_args(preset='fast'),
               '-r', '30', '-fps_mode', 'cfr', '-c:a', 'aac', '-ar', '48000', '-b:a', '192k', '-movflags', '+faststart', str(temporary)])
    info = probe(temporary)
    if info['width'] != width or info['height'] != height or abs(info['duration'] - total_frames / fps) > .04 or info['audio_streams'] != 1:
        raise StudioError('OUTPUT_SPEC', 'Encoded candidate specifications do not match edit')
    for entry, frozen in zip(shots, snapshot_shots):
        if file_hash(entry['clip']) != frozen['clip_sha256'] or file_hash(safe_path(project_dir, entry['audio']['wav_path'])) != entry['audio']['wav_sha256']:
            raise StudioError('INPUT_CHANGED', 'A source changed during editing; candidate not published')
    temporary.replace(output)
    shutil.copy2(directory / 'edit.snapshot.json', final_dir / 'edit.snapshot.json')
    (final_dir / 'narration.srt').write_text(_srt(global_cues, fps), encoding='utf-8')
    sources = project_dir / 'sources.json'
    credits = ''.join(f"\n- {row['role']} {row['sound']}: {row['licence']}" for row in (sound_record or {}).get('sounds', []))
    (final_dir / 'sources.md').write_text('# Sources\n\n' + (json.dumps(read_json(sources), ensure_ascii=False, indent=2) if sources.is_file() else 'Schematic visualization; no factual claims supplied.')
                                          + ('\n\n## Sounds (library/sounds)' + credits if credits else '') + '\n', encoding='utf-8')
    manifest = {'schema_version': 1, 'candidate_id': candidate_id, 'project_id': project['project_id'], 'profile': profile,
                'edit_hash': key, 'output_path': str(output.relative_to(project_dir)), 'output_sha256': file_hash(output),
                'speech_status': ('final' if narration_present else 'not_applicable') if final_voice else 'scratch',
                'narration_present': narration_present, 'delivery_status': 'review_required' if final_voice else 'needs_voice',
                'width': width, 'height': height, 'fps': fps, 'frame_count': total_frames, 'duration_seconds': total_frames / fps,
                'audio_present': True, 'overlays': overlays, 'loudness': loudness, 'sounds': (sound_record or {}).get('sounds', []),
                'warnings': overlays['warnings'] + [w for s in shots for w in s['audio']['warnings']] + [w for s in shots for w in s['warnings']]
                            + [f"{p['code']}: {p.get('shot_id', '')} {p.get('sentence', p.get('claim_id', ''))}" for p in fact_problems],
                'ai_generated_shots': [s['shot']['shot_id'] for s in shots if s['generated']],
                'technical_qa_status': 'pending', 'visual_qa_status': 'pending', 'human_approved': False}
    write_json(manifest_path, manifest)
    return {**manifest, 'cache_hit': False}


def register_commands(subparsers) -> None:
    command = subparsers.add_parser('edit', help='Assemble narration and graphics')
    sub = command.add_subparsers(dest='edit_command', required=True)
    build = sub.add_parser('build')
    build.add_argument('--project', type=Path, required=True)
    build.add_argument('--profile', choices=['rough', 'candidate'], default='rough')
    build.set_defaults(handler=lambda args: {'operation': 'edit.build', 'status': 'complete', **build_edit(args.project, args.profile)})
