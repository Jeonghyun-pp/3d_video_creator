"""How strictly a project judges taste: which gates may be warnings instead of errors.

Only the gates listed in SOFTENABLE can be softened. Everything else (money, approvals in the user's words, licences,
facts, real subjects, data integrity, broken output) is always an error: a new hard gate needs no entry here (default
deny). Each softenable gate says what it is, and that decides its default severity:

  broken  the picture or the scene is wrong in a way generation cannot repair (the subject covered, a level empty,
          objects at the wrong real size) - an error by default
  budget  a loop that has stopped paying (builds without improving) - an error by default, so the agent stops and asks
  taste   a craft preference or a threshold learned from one reference (frame size and edges, margins, fill limits,
          camera realism, detail) - a warning by default: reported with the build, never a discarded build

Why taste warns (2026-10-07, engine_cutaway2): all three failed builds of the run were taste gates (KEY_PART_SMALL on
parts plainly visible at 0.17 % of the frame, FRAME_EDGE_CUT); each discarded a build that had passed every mechanism
check, two of three shots ended with no version, and the two real defects of the run were seen by eye, not by a gate.

project.policy.strictness: 'explain-strict' (default: broken and budget gates are errors, taste warns), 'look-first'
(every softenable gate warns), 'all-strict' (every softenable gate is an error - the default before 2026-10-07);
project.policy.gates overrides single codes either way; shot.policy.strictness overrides the project for one shot.
"""
from __future__ import annotations

from .common import StudioError, read_json

STRICTNESS = ('explain-strict', 'look-first', 'all-strict')
DEFAULT = 'explain-strict'
KINDS = ('broken', 'budget', 'taste')
ERROR_KINDS = {'explain-strict': ('broken', 'budget'), 'look-first': (), 'all-strict': KINDS}
SOFTENABLE = {   # code -> (kind, what it judges)
    'FILL_LEVEL_EMPTY': ('broken', 'a seen level carries nothing the brief says it explains or identifies'),
    'FILL_SUBJECT_HIDDEN': ('broken', 'the subject is mostly covered by nearer identity or ambient copies'),
    'CONTEXT_VOID': ('taste', 'a world with a place shows mostly empty frame and hands no background to the model (study reels p90)'),
    'WORLD_LIGHT_CAST': ('broken', 'the world declares its light and a kept lamp is more than 100 mired from the white balance: what it '
                                   'lights renders tinted while the generated world around it does not (the 2026-10-10 floor section read blue in a warm room)'),
    'FILL_OFF_BRIEF': ('taste', 'fill placed that the brief does not list'),
    'fill_identity_kinds': ('taste', 'more than two identity kinds on a level'),
    'fill_ambient_density': ('taste', 'ambient life above 4 per 100 m2'),
    'subject_margin': ('taste', 'the rig subject crosses the frame margin'),
    'look_target_hidden': ('taste', 'the look target is hidden longer than the rig allows'),
    'framing': ('taste', 'the horizon leaves the height the move holds (learned from one reference)'),
    'GRAPHIC_ILLEGIBLE': ('taste', 'a screen arrow is short, thin or near the flight line (thresholds from one shot)'),
    'REPAIR_BUDGET_EXHAUSTED': ('budget', 'three builds without improving subject fidelity'),
    'fidelity_illustrative': ('taste', 'dimension, proportion or silhouette misses on a schematic or fictional subject'),
    'subject_trace_illustrative': ('taste', 'request words not traced in a schematic or fictional subject spec'),
    'FRAME_EDGE_CUT': ('taste', 'a key part touches the frame border in more than a third of its frames (frame probe)'),
    'FRAME_SUBJECT_SMALL': ('taste', 'the subject is small in the frame (frame probe, initial threshold)'),
    'KEY_PART_SMALL': ('taste', 'a key part shows but never at a readable size (frame probe)'),
    'look_scale': ('broken', 'objects classified by name or category sit outside their real dimensions (look scale audit)'),
    'look_camera_dof': ('taste', 'the subject is softer than the depth-of-field limit even stopped down'),
    'look_camera_shake': ('taste', 'camera shake moves label anchors more than the jitter limit'),
    'look_camera_two_point': ('taste', 'the two-point correction cannot hold the verticals within tolerance'),
    'detail_placeholder': ('taste', 'a visible part is a coarse primitive (fidelity detail check, SKILL #8)'),
    'KEY_PART_UNDER_UI': ('broken', 'the platform UI covers a declared key part wherever it shows (frame probe, screen_core)'),
    'SCREEN_TARGET_MISSED': ('taste', 'a measured screen value is outside the tolerance the shot declares (shot.screen.targets)'),
    'SUBJECT_UNDECLARED': ('taste', 'no object is the shot\'s subject, so subject share and subject targets measure nothing (frame probe)'),
    'SCREEN_SPEED_HIGH': ('taste', 'the subject crosses the frame faster than shot.screen.max_speed'),
    'PATTERN_UNREAD': ('taste', 'a shot with a role records nothing it took from or left in its role\'s patterns (screen.pattern_carried / pattern_departs)'),
    'PATTERN_RECORD_REPEATED': ('taste', 'one pattern record pasted on half the shots or more'),
    'MATERIAL_NOT_CATALOG': ('taste', 'a visible subject or key part under a photoreal look is painted with a plain material, not a catalog kind'),
    'SCALE_FLOW_FLAT': ('taste', 'three or more shots in a row at one focus size while study reels mostly change size between shots (studio/patterns.py scale_flow)'),
    'FOCUS_FRAMING': ('taste', 'when the shot settles its focus is cut at one edge, fills the whole frame or has a part off screen (studio/patterns.py framing)'),
    'SCREEN_PATTERN_OFF': ('taste', 'a strong pattern item of the shot\'s role is outside its usual range, with no declared departure (studio/patterns.py)'),
    'DETAIL_NOT_SHOWN': ('broken', 'a detail the shot explains (shot.screen.details) never shows at its declared size (frame probe)'),
    'DISPLAY_SCALE_UNDISCLOSED': ('broken', 'a part declared larger or smaller than life (display_scale, a dimension deviation factor) has no label anchored to it'),
    'FRAME_TARGET_MISS': ('taste', 'a moment the shot has a frame picture of comes out another composition: subject elsewhere, another size, other edges cut, or the camera looking the other way (studio/frame_targets.py)'),
    'FRAME_PATH_BLOCKED': ('taste', 'between two frame pictures the focus hides behind something else for a stretch of the move (studio/blender_ops/camera_moves.py)'),
    'FRAME_PICTURE_ABANDONED': ('taste', 'a frame picture kept different in more than two ways, or in its viewpoint, without the user\'s words: it is no longer the target (studio/frame_targets.py)'),
    'CAMERA_FLAT': ("taste", "the camera moves less than most of the user's reference shots of its kind, or the reel holds far more often (studio/camera_taste.py)"),
    'CAMERA_HARSH': ("taste", "the camera moves or turns faster than nearly all of the user's reference shots - comfort first (studio/camera_taste.py)"),
    'TAKES_ONE_FEEL': ('taste', 'the camera takes of a shot all measure as the same recipe (library/camera_recipes.json): one feel at several values, not a choice'),
    'TAKE_RECIPE_DIFFERS': ('taste', 'a take built for one recipe measures as another (or none): the camera does not do what the take says'),
    'JOIN_REVERSED': ('taste', "at a cut the camera's motion snaps back the other way (studio/grammar_core.join, storyboard brief)"),
    'JOIN_STOPPED': ('taste', 'at a cut the camera moves into it and is still after it - the jolt CUT_MOTION_JUMP finds in a render, found before one'),
    'JOINS_ROUGH': ('taste', "more of the reel's cuts change the moving camera's direction than the user's reference's (studio/camera_taste.join_lines)"),
    'REVEAL_UNPLANNED': ('taste', "the user's reference shows insides and the shot list plans no shot that does, nor says why (studio/decisions.reveal_lines)"),
    'REVEAL_NOT_SHOWN': ('taste', "a shot planned to show an inside (direction.reveal) never shows the subject's inside on screen (storyboard brief)"),
    'SCRIPT_DENSE': ('taste', "the words fill more of the reel than its speech share leaves (decisions.SPEECH_SHARE, project audio.speech_share): no time to look"),
    'SCRIPT_CHOPPY': ('taste', "the script's lines are shorter than the reference's short sentences: one thought said in pieces, each with its own picture (decisions.choppy_lines)"),
    'PACING_DENSE': ('taste', 'a shot that opens something (direction.reveal) whose words leave less than REVEAL_SHOW_S with nothing said (decisions.pacing_lines)'),
    'RECIPE_MONOTONE': ('taste', "one camera recipe makes up most of the reel, well above the user's reference's largest share (studio/camera_taste.py)"),
    'FRAME_LAYOUT_MISS': ('taste', 'a pictured moment shows other things than its picture, or the same things elsewhere or cut differently (studio/frame_targets.compare_layout)'),
    'FRAME_REVIEW_DIFFERS': ('taste', 'an eye compared a picture with its built moment and saw other things, extent, state or viewpoint (frames review)'),
    'FRAME_ROLL_LIMITED': ('taste', "a picture's lean is matched only partly: rolling to it fully would turn the picture faster than the comfort limit (studio/blender_ops/camera_moves_core.limit_roll_rate)"),
    'FRAME_INTENT_DEPARTED': ('taste', "a frame target kept by its picture's intent gave part of it up: "
                                       'turned past INTENT_TURN_MAX round the subject, a wider lens class than the picture\'s, or '
                                       'another size class (studio/blender_ops/camera_moves._solve_intent)'),
    'FRAME_TILT_UNREACHED': ('taste', 'a frame picture leans the subject further than rolling the camera can match (upright), or than any side shows it (lying: the lean sets the azimuth): pose the subject nearer it at that moment (studio/blender_ops/camera_moves.py)'),
    'FRAMING_REPEATED': ('taste', 'two shots cut together on the same composition (subject place, size and camera angle) without a match cut asked for (studio/patterns.py framing_repeats)'),
    'SUBJECT_SINKS': ('broken', 'a subject the shot moves, or a thing a cheat moved, scaled or turned (deeper than it stood before the cheat), '
                                'reaches more than 2 cm into what it stands on (scene_roles support: a deck, a pad, a table) on some frame - '
                                'feet in the deck read as a crash, not a landing (studio/blender_ops/camera_frame.sinks)'),
    'SHOW_CUT_MISSES': ('broken', "an opening's cut leaves a part it opens hidden from the picture's view: more than HIDDEN_SHARE of it still "
                                  'behind something, measured on the opened scene before the camera is solved (studio/blender_ops/show_scene.check_cuts)'),
    'FRAME_MODEL_EDGE': ('taste', 'the model ends inside the frame in a void on three or more sides - a doll\'s house (frame probe)'),
    # A5 (2026-10-14): what a frame picture's reading and its built moment are checked for besides place and size
    'CAMERA_IN_ATMOSPHERE': ('broken', 'a pictured pose inside a fog, smoke or beam volume (scene_roles atmosphere; a cloud of '
                                       'specks by the region it fills) or within ATMOSPHERE_NEAR_M of it, unless the volume holds the '
                                       'subject too (its air): the frame is the volume - the pose is refused like one under the '
                                       'support (studio/blender_ops/camera_moves.py)'),
    'ATMOSPHERE_COVERS': ('taste', 'at a pictured moment an atmosphere volume covers more than half the frame, or more than BLOCKED_MAX of '
                                   'the focus shows only through it; or between the pictures the camera passes through one - volumes '
                                   'still block no ray (studio/frame_targets.py, camera_moves.py)'),
    'FRAME_READING_STALE': ('broken', "the frame pictures were read for other subject words than the shot's now: the camera "
                                      'targets were read off another thing (studio/frame_targets.reading_checks)'),
    'FRAME_READING_PARTIAL': ('taste', 'things of the shot were linked after its pictures were read: the reading has no box for '
                                       'them, so their size and side are never compared (studio/frame_targets.reading_checks)'),
    'FRAME_READING_UNRECORDED': ('taste', 'the frame pictures were read before the store recorded what the reader was asked: '
                                          'things it placed no box for go uncompared, linked late or never drawn - the '
                                          "store's age, not the shot's (studio/frame_targets.reading_checks)"),
    'FRAME_TARGET_STALE': ('broken', 'the move frames another thing than the pictures were read for (move.target / parts against '
                                     "the reading's subject elements), before the build and at frames apply (studio/frame_targets.py)"),
    'FRAME_SUBJECT_MISREAD': ('broken', "a picture's subject box is more than MISREAD_RATIO times the target - the box the same "
                                        'reading gives the target itself (before the build), or, uncut with nothing in front, as long '
                                        'for its width as the framed thing can look from any side: the reader boxed more than what the '
                                        'camera frames (studio/frame_targets.py)'),
    'FRAME_SIDE_MISS': ('taste', 'a thing around the subject stands left of it in the picture and right of it in the build, or the '
                                 'other way round (studio/frame_targets.py)'),
    'SUBJECT_END_ON': ('taste', "at a picture with a lean, the built subject's long axis is within END_ON_DEG of the line of sight: "
                                'its lean on screen means nothing, so it is not compared (studio/frame_targets.py)'),
    'MOVING_SUBJECT_OFF': ('taste', 'at a pictured moment a subject travelling more than MOVING_SHARE of its length is not where the '
                                    'solved pose frames it (its points, nothing hidden, through the built camera against the pose; '
                                    'centre or height beyond the picture tolerances): the rig drifts off (studio/frame_targets.py)'),
    # B (2026-10-14): a declared cheat may cost the picture its punch (studio/cheat_core.py). A cheat on what the narration
    # says (CHEAT_TOUCHES_LOCKED) or beyond its limits (CHEAT_INVALID) is never listed here: the facts stay true, always an error
    'FRAME_FLATTENED': ('taste', 'a pictured moment came out flatter than its picture: the subject over a quarter smaller, the camera '
                                 'more than FLAT_ELEVATION_DEG nearer level, the lean more than half gone, or a longer lens - the '
                                 "picture's punch lost to a fix or a cheat (studio/frame_targets.flattened)"),
}


def severity_map(project, shot=None):
    """{code: 'error' | 'warn'} for every softenable code; codes not in the map are errors."""
    policy = (project or {}).get('policy') or {}
    strictness = ((shot or {}).get('policy') or {}).get('strictness') or policy.get('strictness') or DEFAULT
    if strictness not in STRICTNESS:
        raise StudioError('INPUT_INVALID', f'Unknown strictness {strictness!r} (known: {STRICTNESS})')
    severity = {code: 'error' if kind in ERROR_KINDS[strictness] else 'warn' for code, (kind, _) in SOFTENABLE.items()}
    overrides = policy.get('gates') or {}
    unknown = sorted(set(overrides) - set(SOFTENABLE))
    if unknown:
        raise StudioError('INPUT_INVALID', f'policy.gates can only soften {sorted(SOFTENABLE)}; {unknown} are always errors')
    severity.update(overrides)
    return severity


def severity_for(path, shot=None):
    """The project's map, or the default when there is no project file (bare specs, tests)."""
    from .project import project_dir
    file = project_dir(path) / 'project.json' if path is not None else None
    return severity_map(read_json(file) if file is not None and file.is_file() else {}, shot)


def is_error(code, severity):
    return severity.get(code, 'error') == 'error'
