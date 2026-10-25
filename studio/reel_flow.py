"""How the reel's pictures run from shot to shot, against the study set of reference reels (warnings, never a stop).

A reel can pass every shot's checks and still be one picture repeated: the same subject in the middle at one size,
the camera never resting, no wide shot that says where we are, every shot longer than the reference's. These are
read twice: on the shot list (planned roles, sizes, lengths, hand-overs) and on the built shots (frame reports, the
probe's id pictures, the camera). Why (2026-10-17): the rocket reel's 25 built shots showed one upright cylinder
mid-frame in 20, the camera moved in all of them, its median shot was 3.95 s; measured on the study set (local
measure 2026-10-17): median shot 1.8 s, a third to a half of the shots held still, 16 % filled the frame, the wide
shot came back through the reel.

A shot that departs on purpose says so in its pattern departures (the finding's code in lower case, with why: the
shot list's pattern.departs, the shot's screen.pattern_departs); a choice about the whole reel is a reel departure
(`studio reel depart`, the user's words or a delegated run's reason). Both are read for what they name only.
"""
from __future__ import annotations

import math
import statistics
from pathlib import Path

from .common import StudioError, now, read_json, write_json

# value, why. Local measure of the study set, 2026-10-17 (size is the focus box's height over the frame's).
REEL_FLOW = {
    'WIDE_HEIGHT_MAX': (0.5, "a focus at most half the frame tall leaves room for where it is: the study set's wide and medium "
                             "shots (height under 0.33 wide, under 0.66 medium) - a wide shot by this measure"),
    'ENV_SHARE_MIN': (0.15, 'what the subject stands in or on takes this much of the frame: the place shows even when the subject is large'),
    'ESTABLISH_GAP_SHARE': (0.4, 'the study set re-establishes through the reel (wide shots come back in its opening, middle and late '
                                 'parts); longer than this without a wide shot, the viewer loses where we are'),
    'SAME_SILHOUETTE_IOU': (0.7, "two neighbouring shots whose subject silhouettes, across the cut, overlap this much read as one picture"),
    'SAME_VIEW_DEG': (30.0, 'the cut needs at least this change of camera direction for the same size to read as a new shot (the 30-degree rule)'),
    'SAME_FRAME_AGREE': (0.75, "two shots' whole frames (nothing / what things stand on / everything else, on a coarse grid) agreeing on "
                               'this share of what either shows read as one picture whatever their focus: measured 2026-10-20 - the canal '
                               'clay\'s look-alike high diagonals agreed 0.81-0.94, the bike reel\'s distinct shots 0.45-0.70; its '
                               'last three (the rider and the bike from one side, t11-t13) 0.76-0.86, read as one picture by eye too'),
    'SAME_DIST_RATIO': (2.0, 'two cameras looking the same way read as one picture only from about one distance: past this ratio one is '
                             'the other\'s close-up or wide (the canal look-alikes stood 1.1-1.8 apart)'),
    'REPEAT_WINDOW': (4, 'shots apart (this many or fewer) whose pictures read as one count toward a repeat: the canal clay cut back '
                         'to one high diagonal every two or three shots (2026-10-20)'),
    'REPEAT_GROUP_MIN': (3, 'that many shots of one picture within the window repeat it (rule 3, PICTURE_REPEATS): a pair apart is a '
                            'return, three is a habit (2026-10-20: the canal clay returned five times, the bike reel ended on three)'),
    'OVERFILL_SHARE_MAX': (0.25, 'shots filling the frame with their focus: the study set 16 %, the flat rocket reel 32 %'),
    'FILL_HEIGHT': (0.95, 'a focus this tall fills the frame (patterns.FULL_FRAME)'),
    'STATIC_SHARE_MIN': (0.3, 'shots with the camera at rest, the climax left out (it moves on purpose): the study set 33-53 %, the rocket '
                              'reel none - a camera that never rests tires (the user, 2026-10-19: rule 6 at 30 %)'),
    'HEIGHT_FLIP_DEG': (60.0, 'the camera height across a cut: a jump of this many degrees of elevation (from far below to far above) '
                              'reads as a jolt unless it is meant (rule 6)'),
    'UNDERFILL_HEIGHT': (0.2, 'a focus this small in the frame is lost unless what is around it gives its size (rule 5: a small '
                              'subject needs a reference; the study set fills 0.12-0.21 of shots with a small wide subject)'),
    'FLAT_SHARE': (0.125, "cuts at or past the reference's long-cut length (its p75: a quarter of its cuts, by definition) under "
                          'half that share: the reel never holds a picture long enough to see it open or change (PACE_FLAT, rule 7; '
                          '2026-10-26: the fab clay ran 2 of 14 cuts past 4 s where the cutaway reference runs 29 %)'),
    'CLOSE_HEIGHT': (0.66, "a focus taller than this is a close shot (the study set's medium/close line)"),
    'CLOSE_RUN_MAX': (4, 'close shots in a row before a wider one says where they are (re-establishing after close-ups)'),
    'CLOSE_MEDIAN_SHARE': (1.0, 'the last shot runs at least the median: the study set ends on its longest kind of shot '
                                '(last shot 3.3 s, 1.37 x its median) - a reel that cuts away fast at the end feels cut off'),
    'CLIMAX_CLOSE_RANK': (0.75, 'the climax shots are among the closest of the reel: their largest focus at or above this share of the '
                                "reel's shots (rule 9, the user's taste: the key moment close and dynamic)"),
    'HOOK_WINDOW_S': (3.0, 'the hook: within its first seconds the reel cuts at least once and shows something happen (rule 1; the '
                           'study set: about two shots start in the first 3 s, the first shot a hook in 49 of 54, still in 6)'),
    'KEY_SIZE_MIN': (0.15, "at its moment the key change's parts together span at least this share of the frame height (their box's "
                           "long side, a width counted in heights): measured 2026-10-19 on the bike reel - parts read by eye at 0.18 "
                           "(a lever) to 0.49 (a cassette), unread at 0.11 and under (pulleys 0.07-0.11, a lever in a wide 0.03)"),
    'KEY_HIDDEN_MAX': (0.5, 'at its moment no more than this share of the key change is hidden in front (rule 12): the bike reel read '
                            '0.26-0.33 hidden (spokes across a cassette), not 0.55-0.62 (pulleys behind a frame and a see-through '
                            'figure); a hand on the lever it presses, 0.49, still read'),
    'PART_HIDDEN_MAX': (0.4, 'a shown part hidden in front by more than this share over its window (median) is said (a warning): '
                             'between what read and what did not on the bike reel'),
    'CONTRAST_STEP': (10.0, 'a step across a part\'s outline differs when the colours a few pixels inside and outside it differ by '
                            'this much (CIE76, about a just-clear difference)'),
    'CONTRAST_OFFSET': (3 / 540, 'how far inside and outside the outline the colours are read, as a share of the frame height (past '
                                 'the blend of an anti-aliased edge and the id pass\'s coarser grid)'),
    'CONTRAST_MIN': (0.66, 'a key part stands apart when this share of its outline steps differ (rule 13): measured 2026-10-19 on the '
                           'bike reel\'s final renders - apart by eye at 0.73-0.89 (a blue shirt, black pulleys, a dark stand), not '
                           'at 0.33-0.59 (a white crank on a white frame, a silver chain on a silver cassette)'),
    'THREAD_OFF_SHARE_MAX': (0.25, "shots that show none of the story's thread (story.thread carrier_ids) past this share: the viewer "
                                   'loses what they were holding (rule 14). The study set, measured 2026-10-19: 0-0.59 of shots off '
                                   'their thread (median 0.15; 7 of 10 at most 0.19); the bike plan 0.31'),
    'THREAD_OFF_RUN_MAX': (3, 'shots in a row without the thread past this: the study set runs 0-9 (median 1.5; 7 of 10 at most 3) - '
                              'a face or a place is a breath, a longer stretch is another story (rule 14)'),
    'ROLE_RUN_MAX': (2, 'the same role in a row past this: the study set repeats a role between neighbours 21 % of the time, the rocket reel 44 %'),
    'STILL_MOVE': (0.02, 'a camera moving less than this share of its distance to what it looks at stays where it is'),
    'STILL_TURN_DEG': (1.0, 'a camera turning (pan or tilt) less than this over the shot looks the same way'),
    'STILL_LENS_LOG': (0.05, 'a lens changing less than this (natural log of the focal length ratio, about 5 %) holds the same view'),
    'SUPPORT_GREY': (12, "the probe's id pictures paint what the subject stands on in mid grey (frame_probe palette[0]); a pixel "
                         'within this of 127 on every channel is the place, black is nothing, the rest is the subject'),
}

# The numbers the rhythm rules read from the project's reference - its pattern library (project.json pattern_library,
# patterns.library_name): name -> ('section.item.stat', why). Why (2026-10-26, the user: "컷 자체가 좀 짧은느낌인데"):
# 3.0 s and 1.5 s fixed here were the pooled study set's pace (seven accounts, 1.7 s median), not the reference the user
# chose (2.45 s median, 29 % of its cuts 4 s or longer). A reading not in the library, or weak there, silences the rule
# that reads it and `reel rules` says 'no evidence in the reference'. A new reading is a row here; a new reference needs
# no code. 'reel.' reads one number a reel (patterns.REEL_NUMERIC).
RHYTHM = {
    'LONG_CUT_S': ('all.len_s.p75', "a cut this long is among the reference's long quarter: a median cut past it drags (PACE_SLOW); "
                                    'a reel with under FLAT_SHARE of its cuts this long never holds (PACE_FLAT); a cut past it '
                                    'answers for its quiet (SHOT_HOLDS_UNSPOKEN) - rule 7'),
    'MEDIAN_CUT_S': ('all.len_s.median', "the reference's median cut: a long cut holding longer than this with nothing said or "
                                         'changing drags (SHOT_HOLDS_UNSPOKEN); the climax cuts short only where the reference\'s '
                                         'climax cuts run under it (rule 7, 9)'),
    'CLIMAX_LEN_S': ('climax.len_s.median', "the reference's climax cuts: 'cut faster at the peak' (PACE_SLOW, CLIMAX_ONE_SHOT) holds "
                                            'only when they run under its median cut (rule 9)'),
    'CHARS_PER_S': ('reel.chars_per_s.median', 'how fast the reference speaks: a line takes its characters over this (decisions.'
                                               'reference_chars_per_s; the scratch voice CHARS_PER_S when not measured)'),
    'REEL_CUT_S': ('reel.mean_len_s.median', "the reference's mean cut a reel: a reel's length over it is about how many cuts it "
                                             'has (BEATS_CROWD says it)'),
    'REEL_QUICK_CUT_S': ('reel.mean_len_s.p25', "the mean cut of the reference's quicker quarter of reels: more cuts than a reel's "
                                                'length over it crowd the reel (BEATS_CROWD)'),
    'HOOK_CUT_S': ('hook.len_s.p75', "the reference's opening cuts (its hook shots): the first cut comes by this (HOOK_WEAK, rule 1); "
                                     'the cutaway reference opens on 2-8 s cuts that move from the start, the pooled set cuts by 3 s'),
    'HOOK_HEIGHT_MIN': ('hook.height_share.p25', "the reference's hook focus height (p25): a built opening at least this tall - a "
                                                 'travel opening where it lands (HOOK_WEAK, rule 1)'),
    'SHOT_MIN_S': ('all.len_s.p10', "no cut is shorter than the reference's 10th percentile (decisions.length_lines), never under "
                                    "decisions.FLASH_S"),
}


def readings(library):
    """{name: {reads, value, n | why}} - each RHYTHM number from a library (None with why when it is not there or weak)."""
    from .patterns import reading
    return {name: {**reading(library, reads), 'why_read': why} for name, (reads, why) in RHYTHM.items()}


def read_reference(project=None):
    """readings() of the project's library (patterns.library_name); every number None when there is no library to read."""
    from .patterns import library_name, load
    try:
        name = library_name(project)
        return {name_: {**r, 'library': name} for name_, r in readings(load(name)).items()}
    except (StudioError, OSError, ValueError) as error:
        return {name: {'reads': reads, 'value': None, 'why': f'no evidence in the reference (no library: {error})', 'why_read': why}
                for name, (reads, why) in RHYTHM.items()}


def ref(reference, name):
    return ((reference or {}).get(name) or {}).get('value')


# A shot departs a finding on purpose by naming its code (lower case) in its pattern departures; what that does - only
# to that shot, never to the reel's other shots. A reel departure (reel_departs.json) silences the code for the reel.
DEPARTS = {
    'ESTABLISH_MISSING': ('establish_missing', 'this shot opens a new place close on purpose (a mystery, a cold open): not reported'),
    'ESTABLISH_GAP': ('establish_gap', 'its seconds do not count toward a stretch without a wide shot (it says where we are another way)'),
    'SAME_PICTURE': ('same_picture', 'the cut into this shot repeats the picture on purpose (a match, a beat held): not reported'),
    'PICTURE_REPEATS': ('picture_repeats', 'it returns to a picture on purpose (a refrain, a before and after): left out of the repeats'),
    'OVERFILL_SHARE': ('overfill_share', 'it fills the frame on purpose (a climax, a texture): not counted among the full ones'),
    'STATIC_SHARE_LOW': ('static_share_low', 'its camera moves on purpose (a follow, a reveal): it leaves the count of resting shots'),
    'PACE_SLOW': ('pace_slow', 'it runs long on purpose (a held reveal, a payoff): it leaves the median and the climax set'),
    'PACE_FLAT': ('pace_flat', 'it is short on purpose (a beat, a flash): it leaves the count of long cuts'),
    'BEATS_CROWD': ('beats_crowd', 'it is a flash on purpose (a sting, a title): it leaves the count of cuts'),
    'CLOSE_RUN': ('close_run', 'a close shot that belongs in the run on purpose: skipped, neither counted nor breaking it'),
    'ROLE_RUN': ('role_run', 'its role repeats on purpose: skipped, neither counted nor breaking the run'),
    'HEIGHT_FLIP': ('height_flip', 'the cut into this shot jumps the camera height on purpose (a reversal, a reveal from above): not reported'),
    'UNDERFILL_NO_SCALE': ('underfill_no_scale', 'its subject is small on purpose (lost in a vast place): not reported'),
    'SHOT_HOLDS_UNSPOKEN': ('shot_holds_unspoken', 'it holds without words on purpose (a pause after a punchline, a long look): not reported'),
    'CLOSE_SHORT': ('close_short', 'the reel ends short on purpose (a cut to black, a sting): not reported'),
    'CLIMAX_ONE_SHOT': ('climax_one_shot', 'the climax is one held shot on purpose (one take of the event): not reported'),
    'CLIMAX_NOT_CLOSEST': ('climax_not_closest', 'the climax is wide on purpose (the whole system at once): not reported'),
    'PART_OCCLUDED': ('part_occluded', 'a shown part is partly hidden on purpose (seen through a gap, a peek): not reported'),
    'PART_LOW_CONTRAST': ('part_low_contrast', 'a part matches what is around it on purpose (camouflage is the point): not reported'),
    'THREAD_DEVICE_UNUSED': ('thread_device_unused', 'the reel carries its thread another way (a device no shot field makes, '
                                                     'said in the why): not reported'),
    'CUT_UNTOLD': ('cut_untold', 'a cut that tells no beat on purpose (a breath, a title): not reported'),
    'THREAD_OFF_SCREEN': ('thread_off_screen', 'it leaves the thread on purpose (a face, a place, a cut-away): neither counted nor '
                                               'breaking a run'),
    # the reel seen from one side, moving with one feel: answered for the whole reel (reel depart), its reason read with
    # every cut's picture (studio/excuses.py) - 2026-10-24: warnings only reel clay said, which nothing asked to answer
    'VIEW_MONOTONE': ('view_monotone', 'the reel keeps to one side of its subject on purpose (a profile is the point): not reported'),
    'RECIPE_MONOTONE': ('recipe_monotone', 'the reel moves with one feel on purpose (one long calm look): not reported'),
    # the journey (studio/journey.py): a cut that jumps past places of the journey without handing over to a new place
    # how the camera crosses a built cut (grammar_core.join, both versions measured): answered as the reel's sameness is
    'JOIN_REVERSED': ('join_reversed', "the camera's motion turns back at the cut into this shot on purpose (a reversal): not reported"),
    'HANDOVER_BROKEN': ('handover_broken', 'the cut into this shot breaks the motion the shot before hands over on purpose: not reported'),
    'JOURNEY_JUMP': ('journey_jump', 'the cut into this shot jumps past places of the journey on purpose (a cold open, a '
                                     'flash forward): not reported'),
}
# Blocking rules (the user, 2026-10-19: rules 1, 11, 12 stop - what the reel explains must be seen): no shot or reel
# departs from these; a project's gate policy (look-first) is the only way to make them warnings (studio/gates.py).
BLOCKING = ('HOOK_WEAK', 'KEY_CHANGE_UNPLANNED', 'KEY_CHANGE_UNSHOWN')
DEPART_ITEMS = {item: code for code, (item, _) in DEPARTS.items()}
# The 14 direction rules (the user, 2026-10-19: "처음 생성 시 잘 연출하는 원칙"), as the planner reads them before writing a
# shot list (studio reel rules) and as each sheet reports them: number, rule, the findings that check it, severity.
RULES = (
    (1, "Something happens within 3 s; the first cut comes by the reference's opening cut (reference: HOOK_CUT_S); a hook, close "
        '(a travel opening lands within 3 s and is sized where it lands)', ('HOOK_WEAK',), 'stop'),
    (2, 'A new place opens wide; wide shots come back through the reel', ('ESTABLISH_MISSING', 'ESTABLISH_GAP'), 'warn'),
    (3, 'Never the same picture twice in a row, nor the same picture again and again', ('SAME_PICTURE', 'PICTURE_REPEATS'), 'warn'),
    (4, 'The same size across a cut turns the camera 30 degrees or more', ('SAME_PICTURE',), 'warn'),
    (5, 'Few shots fill the frame; a small subject has something that gives its size', ('OVERFILL_SHARE', 'UNDERFILL_NO_SCALE'), 'warn'),
    (6, 'The camera rests in 30 % of the shots (the climax aside); no jolt in camera height', ('STATIC_SHARE_LOW', 'HEIGHT_FLIP'), 'warn'),
    (7, "Cuts run at the reference's pace (reference: its median and long-cut length): the median cut no longer than its long "
        'cuts, a quarter of the cuts held long enough to see the change, none held quiet; the ending holds',
     ('PACE_SLOW', 'PACE_FLAT', 'SHOT_HOLDS_UNSPOKEN', 'CLOSE_SHORT'), 'warn'),
    (8, 'After four close shots, a wider one says where we are', ('CLOSE_RUN',), 'warn'),
    (9, "The climax is the closest; it is cut into short shots where the reference's climax cuts run short (reference: CLIMAX_LEN_S)",
     ('CLIMAX_ONE_SHOT', 'CLIMAX_NOT_CLOSEST', 'PACE_SLOW'), 'warn'),
    (10, 'Roles vary: no three of a kind in a row', ('ROLE_RUN',), 'warn'),
    (11, 'The key change is planned (a show row with key: true) and shown close', ('KEY_CHANGE_UNPLANNED', 'KEY_CHANGE_UNSHOWN'), 'stop'),
    (12, 'Nothing stands in front of what is shown; see-through is the last resort', ('KEY_CHANGE_UNSHOWN', 'PART_OCCLUDED'),
     'stop (key change) / warn'),
    (13, 'What is explained stands apart from its surroundings in colour or light', ('PART_LOW_CONTRAST',), 'warn'),
    (14, "The story's thread stays on screen; it passes to another carrier only where a shot hands it over (handover.thread_to)",
     ('THREAD_OFF_SCREEN', 'THREAD_DEVICE_UNUSED'), 'warn'),
    (15, 'Every cut has a job in the story: it tells a beat (asks, answers, shows what follows, makes the stake felt), never decoration',
     ('CUT_UNTOLD',), 'warn'),
)


def rules(project=None):
    """The direction rules with the numbers behind them, for the planner before a shot list is written: each rule, its
    findings and severity, the thresholds, and what shots of each role do in the study set (the pattern library:
    length, focus height, share of resting cameras) - targets to aim at, the checks only the net under them."""
    targets = {}
    try:
        from .patterns import library_name, load
        library = load(library_name(project))
        for role, part in library['sections'].items():
            items = part['items']
            row = {k: items[k]['median'] for k in ('len_s', 'height_share') if k in items and 'median' in items[k]}
            axis = (items.get('camera_axis') or {}).get('values') or {}
            if 'still' in axis:
                row['still_share'] = axis['still'].get('share')
            targets[role] = row
    except (StudioError, KeyError, OSError):
        targets = {}
    taste = []
    try:
        from .patterns import taste_entries
        taste = [{k: t.get(k) for k in ('sections', 'item', 'prefer', 'why')} for t in taste_entries()]
    except (StudioError, KeyError, OSError):
        pass
    thread = None
    if project is not None:   # the story's thread and the devices its kind shows it with (library/story_axes.json thread)
        try:
            from .decisions import envelope
            from .story import body_of, load as load_story
            from .project import project_dir
            from .story_core import load_axes
            root = project_dir(project)
            story = body_of(load_story(root))   # the settled story (story.json), never a draft
            held = (story or {}).get('thread')
            if held:
                kind = ((load_axes().get('thread') or {}).get('values') or {}).get(held['kind']) or {}
                thread = {**held, 'orders_by': kind.get('orders_by'), 'devices': kind.get('devices') or []}
        except (StudioError, KeyError, OSError):
            thread = None
    return {'rules': [{'n': n, 'rule': text, 'findings': list(codes), 'severity': severity} for n, text, codes, severity in RULES],
            **({'thread': thread} if thread else {}),
            'reference': read_reference(project),   # the numbers the rhythm rules read from the project's library, or why not
            'thresholds': {k: {'value': val, 'why': why} for k, (val, why) in REEL_FLOW.items()},
            'role_targets': targets, 'taste': taste,
            'answer': "an open warning is fixed or departed with why (shot list pattern.departs {item, why}; built shot "
                      "screen.pattern_departs; studio reel depart); a stop is fixed - DIRECTION_UNRESOLVED keeps the shot list "
                      "a draft and refuses look and final renders"}


def rules_table(findings, departs=()):
    """The sheet's direction table: each rule open (its findings named), departed (a shot or the reel says why) or met."""
    codes = [line.split(':', 1)[0] for line in findings]
    departed = {DEPART_ITEMS.get(d, d) for d in departs}
    out = ['| # | rule | severity | state |', '|---|---|---|---|']
    for n, text, checks, severity in RULES:
        open_ = [c for c in checks if c in codes]
        state = ('**open**: ' + ', '.join(open_)) if open_ else ('departed: ' + ', '.join(c for c in checks if c in departed)) \
            if any(c in departed for c in checks) else 'met'
        out.append(f'| {n} | {text} | {severity} | {state} |')
    return out

WIDE_ROLES = ('establish', 'hook')
WIDE_STEPS = ('wider', 'first')
REEL_DEPARTS = 'reel_departs.json'


def v(name):
    return REEL_FLOW[name][0]


def _iou_masks(a, b):
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _view_angle(a, b):
    """Degrees between two camera directions given as [elevation, azimuth] around the subject."""
    def unit(view):
        el, az = math.radians(view[0]), math.radians(view[1])
        return (math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el))
    dot = sum(x * y for x, y in zip(unit(a), unit(b)))
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


def _looks(move, shot):
    """Where the camera looks from at the shot's first and last frame, in the world: ({dir: unit eye to aim, dist},
    same at the end), from the move's solved poses, else the shot's own camera keys (a camera written by hand, an
    authored rig's keys); (None, None) when neither says. Why (2026-10-20): the canal clay's cameras were keys, no move
    report gave a direction, and the picture checks skipped every cut."""
    poses = [(f['position'], f['aim']) for f in (move or {}).get('frames') or [] if f.get('position') and f.get('aim')]
    if not poses:
        keys = sorted([k for k in ((shot or {}).get('camera') or {}).get('keys') or [] if k.get('location') and k.get('target')],
                      key=lambda k: k.get('frame', 0))
        poses = [(k['location'], k['target']) for k in keys]
    if not poses:
        return None, None
    look = lambda pose: dict(zip(('dir', 'dist'), _direction(*pose)))   # noqa: E731
    return look(poses[0]), look(poses[-1])


def _look_view(look):
    """[elevation, azimuth] (degrees) of a world look: how high the camera stands over what it looks at, and from where -
    azimuth 0 with the camera on -Y looking +Y, as camera_frame_core.basis reads a view."""
    d = look['dir']
    return [math.degrees(math.asin(max(-1.0, min(1.0, -d[2])))), math.degrees(math.atan2(-d[0], d[1]))]


COARSE = (54, 96)   # the grid two whole frames are compared on (frame_classes): coarse enough that a camera nudged a little
                    # still lands each thing in the same cells


def frame_classes(image):
    """A probe id picture on the COARSE grid as nothing (0) / what things stand on (1, the support grey) / anything else (2)."""
    from PIL import Image
    tol = v('SUPPORT_GREY')
    with Image.open(image) as src:
        raw = src.convert('RGB').resize(COARSE, Image.NEAREST).tobytes()
    return [0 if r == g == b == 0 else 1 if max(abs(r - 127), abs(g - 127), abs(b - 127)) <= tol else 2
            for r, g, b in zip(raw[0::3], raw[1::3], raw[2::3])]


def _agree(a, b):
    """The share of the cells either frame shows something in where both show the same kind."""
    cells = [i for i in range(min(len(a), len(b))) if a[i] or b[i]]
    return sum(a[i] == b[i] for i in cells) / len(cells) if cells else 0.0


def same_picture(a, b, a_end='end', b_end='start'):
    """Do these two built rows' frames (a's `a_end`, b's `b_end`) read as one picture? {same, turn, overlap, agree, ratio}
    or None when either lacks a direction or a picture. One direction (within SAME_VIEW_DEG) and either one focus
    silhouette (SAME_SILHOUETTE_IOU) or one whole frame from about one distance (SAME_FRAME_AGREE, SAME_DIST_RATIO) -
    the second because five look-alike shots of the canal clay framed different parts of one view (2026-10-20)."""
    la, lb = a.get(f'look_{a_end}'), b.get(f'look_{b_end}')
    va, vb = a.get(f'view_{a_end}'), b.get(f'view_{b_end}')
    if la and lb:
        turn = math.degrees(math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(la['dir'], lb['dir']))))))
        ratio = max(la['dist'], lb['dist']) / max(min(la['dist'], lb['dist']), 1e-9)
    elif va and vb:
        turn, ratio = _view_angle(va, vb), None
    else:
        return None
    sa, sb = a.get(f'silhouette_{a_end}'), b.get(f'silhouette_{b_end}')
    fa, fb = a.get(f'silhouette_frame_{a_end}'), b.get(f'silhouette_frame_{b_end}')
    if sa is None and fa is None or sb is None and fb is None:
        return None
    overlap = _iou_masks(sa, sb) if sa is not None and sb is not None else 0.0
    agree = _agree(fa, fb) if fa is not None and fb is not None else 0.0
    alike = overlap >= v('SAME_SILHOUETTE_IOU') or (agree >= v('SAME_FRAME_AGREE') and ratio is not None and ratio <= v('SAME_DIST_RATIO'))
    return {'same': turn < v('SAME_VIEW_DEG') and alike, 'turn': turn, 'overlap': overlap, 'agree': agree, 'ratio': ratio}


def _wide(r):
    if r.get('height') is not None:
        return r['height'] <= v('WIDE_HEIGHT_MAX') or (r.get('env') or 0) >= v('ENV_SHARE_MIN')
    return r.get('role') in WIDE_ROLES or r.get('scale_step') in WIDE_STEPS


def _departs(r, code):
    return DEPARTS[code][0] in (r.get('departs') or ())




def hook_lines(rows, reference=None):
    """HOOK_WEAK (blocking, rule 1): the reel cuts within the reference's opening cut (HOOK_CUT_S, its hook shots' p75), its
    first shot is a hook, and something happens within HOOK_WINDOW_S - a change the build makes (direction.change, made
    and read on screen: CHANGE_UNMADE and CHANGE_UNSHOWN stop a build that does not) or the key change itself; built, its
    focus is at least the reference's hook height (HOOK_HEIGHT_MIN). A travel opening (direction.journey role travel)
    lands within HOOK_WINDOW_S - planned, settle_at of its length; built, the camera's arrive_s - and is sized where it
    lands. Why (2026-10-19, the bike reel): one still wide shot for 2.4 s with nothing happening opened a reel about a
    chain jumping sprockets. 2026-10-26 (plan R3): the window came from the pooled study set's hooks; the cutaway
    reference the user chose opens on long cuts (2.3, 7.7, 6.0, 7.2 s) that move from their first frame. A pattern's
    change_kind no longer counts (2026-10-24 audit: a word in the shot list passed it)."""
    first = rows[0]
    window, cut_by, floor = v('HOOK_WINDOW_S'), ref(reference, 'HOOK_CUT_S'), ref(reference, 'HOOK_HEIGHT_MIN')
    wrong = []
    if cut_by is not None and (len(rows) < 2 or first['len_s'] >= cut_by):
        wrong.append(f"no cut in the first {cut_by:.0f} s (the first shot runs {first['len_s']:.1f} s; the reference's opening cuts "
                     f'run up to {cut_by:.1f} s, their p75)')
    if first.get('role') and first['role'] != 'hook':
        wrong.append(f"the first shot is a {first['role']}, not a hook")
    starts = [sum(x['len_s'] for x in rows[:n]) for n in range(len(rows))]
    happens = [any(start + t < window for t in (r.get('key_times') or []) + (r.get('change_times') or []))
               for r, start in zip(rows, starts) if start < window]
    if not any(happens):
        wrong.append('nothing happens in it (a direction.change the build makes in the first seconds, or the key change shown)')
    travels = ((first.get('journey') or {}).get('role')) == 'travel'
    if travels:   # a fly-in: where it lands is the opening picture
        built_arrival = first.get('arrive_s')
        lands = built_arrival if built_arrival is not None else (first['settle_at'] * first['len_s'] if first.get('settle_at') is not None else None)
        if lands is not None and lands >= window:
            wrong.append(f"it travels and lands at {lands:.1f} s, past the first {window:.0f} s (settle sooner, travel faster or shorter)")
        size, where = first.get('height_landed'), ' where it lands'
    else:
        size, where = first.get('height'), ''
    if size is not None and floor and size < floor:
        wrong.append(f"its focus is {size:.2f} of the frame tall{where}, under the reference's hook ({floor:.2f}, p25)")
    if not wrong:
        return []
    return [f"HOOK_WEAK: {'; '.join(wrong)} - open on the result, the key change or the question, close, with something happening "
            f"inside {window:.0f} s (rule 1; not departable). What happens is made in the 3D: the opening shot's direction.change (a "
            'part that moves, turns, fills, flows - the build makes it and reads it on screen), or a show row with key: true in the '
            'first seconds']


def key_change_lines(rows, story):
    """KEY_CHANGE_UNPLANNED (blocking, rule 11): a story with a turn or a climax marks, in a shot telling one of them, the
    moment of the key change (a show row with key: true). KEY_CHANGE_UNSHOWN (blocking): built, at that moment its
    parts span KEY_SIZE_MIN of the frame height and at most KEY_HIDDEN_MAX of them is hidden in front. Why (2026-10-19,
    the bike reel): what the reel explains - the chain moving to the next sprocket - had no shot that showed it close;
    the pulley that guides it was 68 of 36,864 probe pixels."""
    out = []
    turns = {b['story_beat_id'] for b in (story or {}).get('beats', []) if b['function'] in ('turn', 'climax')}
    telling = [r for r in rows if turns & set(r.get('story_beats') or [])]
    if turns and telling and not any(r.get('key_show') for r in rows):
        out.append(f"KEY_CHANGE_UNPLANNED: the story turns or peaks in {', '.join(r['shot_id'] for r in telling)}, and no shot marks the "
                   "moment of the key change - give one of them a show row with key: true (the parts that change, at the moment they "
                   "do), close and unhidden (rule 11; not departable)")
    for r in rows:
        for m in r.get('key_moments') or []:
            what = f"{r['shot_id']} at {m['at']} ({', '.join(m['parts'])})"
            if m.get('size') is None or m.get('hidden') is None:
                out.append(f"KEY_CHANGE_UNSHOWN: {what} is not measured - build the shot again so the probe reads the key change "
                           "(its size and what stands in front of it)")
                continue
            wrong = []
            if m['size'] < v('KEY_SIZE_MIN'):
                wrong.append(f"{m['size']:.2f} of the frame height across (under {v('KEY_SIZE_MIN'):.2f})")
            if m['hidden'] > v('KEY_HIDDEN_MAX'):
                wrong.append(f"{m['hidden']:.0%} hidden in front (over {v('KEY_HIDDEN_MAX'):.0%})")
            if wrong:
                out.append(f"KEY_CHANGE_UNSHOWN: {what} is {' and '.join(wrong)} - bring the camera closer or to a side where nothing "
                           "stands in front, or move what hides it; see-through is the last resort (rule 11, 12; not departable)")
    return out


def lines(rows, story=None, silenced=(), reference=None):
    """Findings for a reel. rows (cut order): {shot_id, len_s, role?, scale_step?, handover?, story_beats?, departs?,
    spoken_s?, opens?, change_kind?, key_show?, and when built (measured): height (focus box height share, median), env
    (support share), fills (bool), still (bool | None), silhouette_start/_end (pixel sets), view_start/_end ([elevation,
    azimuth] | None), key_moments [{at, parts, size, hidden}], hidden {part: share}, contrast {part: share of its
    outline that stands apart}}. A shot not measured breaks every neighbour comparison it sits in. story: the story body, for where its
    hook, turn and climax are. silenced: codes the reel departs from (reel departures; never a BLOCKING code). reference:
    the numbers read from the project's library (reference(project); the default library's when not given)."""
    out = []
    if not rows:
        return out
    reference = reference if reference is not None else read_reference()
    long_cut, median_cut, climax_cut = (ref(reference, k) for k in ('LONG_CUT_S', 'MEDIAN_CUT_S', 'CLIMAX_LEN_S'))
    peak_fast = climax_cut is not None and median_cut is not None and climax_cut < median_cut   # rule 9 has evidence
    say = lambda code, text: out.append(f'{code}: {text}') if code not in silenced else None   # noqa: E731
    measured = lambda r: r.get('height') is not None   # noqa: E731
    peaks = {b['story_beat_id'] for b in (story or {}).get('beats', []) if b['function'] == 'climax'}
    climactic = lambda r: bool(peaks & set(r.get('story_beats') or []))   # noqa: E731
    if (story or {}).get('beats'):   # a reel told as a story (the story layer): its hook and its key change are rules
        out += hook_lines(rows, reference) + key_change_lines(rows, story)
    def new_place(a, b):   # handed over to a new place, or the journey starts the cut somewhere the cut before did not end
        ja, jb = a.get('journey'), b.get('journey')
        return a.get('handover') == 'new_place' or bool(ja and jb and jb['from'] != ja['to'])
    for a, b in zip(rows, rows[1:]):   # a new place opens wide
        if new_place(a, b) and not _wide(b) and not _departs(b, 'ESTABLISH_MISSING'):
            say('ESTABLISH_MISSING', f"{b['shot_id']} opens a new place (after {a['shot_id']}) "
                + (f"with its focus {b['height']:.2f} of the frame and little around it" if measured(b)
                   else f"as a {b.get('role') or 'shot'} ({b.get('scale_step') or 'no size step'})")
                + ' - open it wide enough to show where we are, or record the departure (establish_missing)')
    for a, b in zip(rows, rows[1:]):   # the journey runs place to place; past a place only into a new one
        ja, jb = a.get('journey'), b.get('journey')
        if ja and jb and abs(jb['from'] - ja['to']) > 1 and a.get('handover') != 'new_place' and not _departs(b, 'JOURNEY_JUMP'):
            say('JOURNEY_JUMP', f"{a['shot_id']} ends at journey place {ja['to']} and {b['shot_id']} starts at {jb['from']}, past the "
                'places between - travel through them, hand over to a new place (handover new_place), or record the departure '
                '(journey_jump)')
    from .blender_ops.show_core import CONTINUOUS
    for a, b in zip(rows, rows[1:]):   # how the camera crosses each built cut (join_in: grammar_core.join of both versions)
        kind = b.get('join_in')
        if kind == 'reverse' and not _departs(b, 'JOIN_REVERSED'):
            say('JOIN_REVERSED', f"{a['shot_id']} -> {b['shot_id']}: the camera's motion snaps back the other way at the cut - carry the "
                'direction on, settle before the cut, or make the two one camera (camera.move.then), or record the departure (join_reversed)')
        if a.get('handover') in CONTINUOUS and kind in ('reverse', 'stop', 'turn') and not _departs(b, 'HANDOVER_BROKEN'):
            say('HANDOVER_BROKEN', f"{a['shot_id']} hands over '{a['handover']}' and the camera {kind}s at the cut into {b['shot_id']} - let "
                f"{b['shot_id']} take the motion on, say contrast / new_place in the shot list, or record the departure (handover_broken)")
    total = sum(r['len_s'] for r in rows) or 1.0
    gap, longest, since = 0.0, (0.0, None), rows[0]['shot_id']
    for r in rows:
        if _wide(r):
            gap, since = 0.0, r['shot_id']
        elif not _departs(r, 'ESTABLISH_GAP'):
            gap += r['len_s']
            if gap > longest[0]:
                longest = (gap, since)
    if longest[0] / total > v('ESTABLISH_GAP_SHARE'):
        say('ESTABLISH_GAP', f"{longest[0]:.1f} s ({longest[0] / total:.0%} of the reel) after {longest[1]} with no wide shot - "
            f"bring a wide one back (over {v('ESTABLISH_GAP_SHARE'):.0%} the viewer loses where we are)")
    built = [r for r in rows if measured(r)]
    for a, b in zip(rows, rows[1:]):   # neighbours across a cut, both measured (an unbuilt shot between breaks it)
        if not (measured(a) and measured(b)) or a.get('handover') in ('continue', 'match_action'):
            continue
        if not _departs(b, 'HEIGHT_FLIP') and a.get('view_end') and b.get('view_start') \
                and abs(b['view_start'][0] - a['view_end'][0]) >= v('HEIGHT_FLIP_DEG'):
            say('HEIGHT_FLIP', f"{a['shot_id']} to {b['shot_id']} jumps the camera from {a['view_end'][0]:.0f} to {b['view_start'][0]:.0f} "
                f"degrees of elevation - step the height over two cuts, or record the departure (height_flip)")
        if _departs(b, 'SAME_PICTURE'):
            continue
        found = same_picture(a, b)
        if found and found['same']:
            say('SAME_PICTURE', f"{a['shot_id']} and {b['shot_id']} cut between one picture (focus overlap {found['overlap']:.2f}, whole "
                f"frame {found['agree']:.2f}) seen from one direction ({found['turn']:.0f} degrees apart) - change the size, the side "
                f"or the angle by {v('SAME_VIEW_DEG'):.0f} degrees or more, show something else, or record the departure (same_picture)")
    kept = [f"{r['shot_id']} ({r['off_picture']})" for r in rows if r.get('off_picture')]
    if kept:   # said on every sheet: a camera of hand keys beside the shot's pictures (frame_targets.off_picture)
        say('CAMERA_OFF_PICTURE_KEPT', f"shot from hand keys, not their pictures: {'; '.join(kept)}")
    # one picture again and again (rule 3, 2026-10-20): shots within REPEAT_WINDOW of each other whose pictures read as one
    # (either end against either end), joined into groups; a group of REPEAT_GROUP_MIN repeats it
    measured_rows = [r for r in rows if measured(r) and not _departs(r, 'PICTURE_REPEATS')]
    group = {r['shot_id']: r['shot_id'] for r in measured_rows}

    def top(i):
        while group[i] != i:
            i = group[i]
        return i
    for n, a in enumerate(measured_rows):
        for b in measured_rows[n + 1:n + 1 + int(v('REPEAT_WINDOW'))]:
            if any((found := same_picture(a, b, x, y)) and found['same'] for x in ('start', 'end') for y in ('start', 'end')):
                group[top(b['shot_id'])] = top(a['shot_id'])
    groups = {}
    for r in measured_rows:
        groups.setdefault(top(r['shot_id']), []).append(r['shot_id'])
    for shots in groups.values():
        if len(shots) >= v('REPEAT_GROUP_MIN'):
            say('PICTURE_REPEATS', f"{', '.join(shots)} return to one picture (one direction, one distance, one frame) - give some of "
                'them another size, side or height, or record the departure on the ones meant to return (picture_repeats)')

    if built:
        counted = [r for r in built if not _departs(r, 'OVERFILL_SHARE')]
        full = [r['shot_id'] for r in counted if r.get('fills') or r['height'] >= v('FILL_HEIGHT')]
        if counted and len(full) / len(counted) > v('OVERFILL_SHARE_MAX'):
            say('OVERFILL_SHARE', f"{len(full)} of {len(counted)} shots fill the frame with their focus ({', '.join(full)}) - "
                f"over {v('OVERFILL_SHARE_MAX'):.0%}; step back on some so the subject has a place")
        small = [r['shot_id'] for r in built if r['height'] < v('UNDERFILL_HEIGHT') and (r.get('env') or 0) < v('ENV_SHARE_MIN')
                 and not _departs(r, 'UNDERFILL_NO_SCALE')]
        if small:
            say('UNDERFILL_NO_SCALE', f"{', '.join(small)}: the focus is under {v('UNDERFILL_HEIGHT'):.0%} of the frame tall with little "
                'around it to give its size - come closer, or frame it with what it stands in or a thing of known size')
        stills = [r for r in built if r.get('still') is not None and not climactic(r) and not _departs(r, 'STATIC_SHARE_LOW')]
        if stills and sum(r['still'] for r in stills) / len(stills) < v('STATIC_SHARE_MIN'):
            say('STATIC_SHARE_LOW', f"{sum(r['still'] for r in stills)} of {len(stills)} shots (the climax left out) hold the camera "
                f"still - under {v('STATIC_SHARE_MIN'):.0%}; let some shots rest (the subject moves, the camera watches)")
        for r in built:
            keyed = {p for m in r.get('key_moments') or [] for p in m['parts']}
            hidden = {p: h for p, h in (r.get('hidden') or {}).items() if p not in keyed and h > v('PART_HIDDEN_MAX')}
            if hidden and not _departs(r, 'PART_OCCLUDED'):
                say('PART_OCCLUDED', f"{r['shot_id']} shows {', '.join(f'{p} {h:.0%} hidden' for p, h in sorted(hidden.items()))} in front "
                    f"(over {v('PART_HIDDEN_MAX'):.0%}) - move the camera or what is in front; see-through is the last resort (rule 12)")
            flat = {p: c for p, c in (r.get('contrast') or {}).items() if c < v('CONTRAST_MIN')}
            if flat and not _departs(r, 'PART_LOW_CONTRAST'):
                say('PART_LOW_CONTRAST', f"{r['shot_id']}: {', '.join(f'{p} {c:.0%}' for p, c in sorted(flat.items()))} of the outline stands "
                    f"apart from what is around it (under {v('CONTRAST_MIN'):.0%}) - give the part a colour or finish of its own, or light "
                    'it apart (rule 13)')
    run = []
    for r in rows + [None]:
        if r is not None and measured(r) and _departs(r, 'CLOSE_RUN'):
            continue
        if r is not None and measured(r) and r['height'] > v('CLOSE_HEIGHT'):
            run.append(r['shot_id'])
            continue
        if len(run) > v('CLOSE_RUN_MAX'):
            say('CLOSE_RUN', f"{len(run)} close shots in a row ({', '.join(run)}) - a wider one between says where they are")
        run = []
    # a long cut its words and changes fill end to end is not slow (a 4-7 s explanation that keeps moving): it leaves the
    # median as a departed one does (2026-10-25, plan M4)
    filled = lambda r: (r.get('spoken_s') is not None and long_cut is not None and median_cut is not None   # noqa: E731
                        and r['len_s'] > long_cut and unfilled_s(r) <= median_cut)
    paced = [r for r in rows if not _departs(r, 'PACE_SLOW') and not filled(r)]
    median = statistics.median(r['len_s'] for r in paced) if paced else 0.0
    if long_cut is not None and median > long_cut:
        say('PACE_SLOW', f"the median shot is {median:.1f} s, over the reference's long cut {long_cut:.1f} s (its p75) - split long "
            'shots into two angles or cut what the picture already says')
    if peaks and paced and peak_fast:
        climax = [r for r in paced if climactic(r)]
        if climax and statistics.median(r['len_s'] for r in climax) > median:
            say('PACE_SLOW', f"the climax shots ({', '.join(r['shot_id'] for r in climax)}) run longer than the reel's median "
                f"{median:.1f} s - cut faster at the peak (the reference's climax cuts run {climax_cut:.1f} s)")
    counted = [r for r in rows if not _departs(r, 'PACE_FLAT')]
    if long_cut is not None and len(rows) > 1 and counted:
        held = [r for r in counted if r['len_s'] >= long_cut - 1e-9]
        if len(held) / len(counted) < v('FLAT_SHARE'):
            say('PACE_FLAT', f"{len(held)} of {len(counted)} cuts run {long_cut:.1f} s or longer (the reference's long cut, its p75: a "
                'quarter of its cuts) - this reel never holds a picture long enough to see it open or change; give the cut whose change '
                'is the point the time to show it (filled by the change, not a still tail), or record the departure (pace_flat)')
    reel_cut, quick_cut = ref(reference, 'REEL_CUT_S'), ref(reference, 'REEL_QUICK_CUT_S')
    cuts = [r for r in rows if not _departs(r, 'BEATS_CROWD')]
    if reel_cut and quick_cut and len(cuts) > total / quick_cut:
        say('BEATS_CROWD', f"{len(cuts)} cuts in {total:.0f} s - the reference cuts a reel this long about {total / reel_cut:.0f} times "
            f"(its mean cut {reel_cut:.1f} s), its quicker reels {total / quick_cut:.0f} (mean {quick_cut:.1f} s): join beats into longer "
            'cuts that show their change, or make the reel longer (beats_crowd)')
    for r in rows:
        if long_cut is None or median_cut is None or r.get('spoken_s') is None or r['len_s'] <= long_cut \
                or _departs(r, 'SHOT_HOLDS_UNSPOKEN'):
            continue
        quiet = unfilled_s(r)   # held with nothing said and nothing changing on screen
        if quiet > median_cut:
            say('SHOT_HOLDS_UNSPOKEN', f"{r['shot_id']} runs {r['len_s']:.1f} s and its words take about {r['spoken_s']:.1f} s - "
                f"{quiet:.1f} s held with nothing said or changing (over the reference's median cut {median_cut:.1f} s): fill it with "
                'the change, cut it shorter or split it into two pictures (rule 7)')
    if len(rows) > 1 and rows[-1]['len_s'] < v('CLOSE_MEDIAN_SHARE') * median and not _departs(rows[-1], 'CLOSE_SHORT'):
        say('CLOSE_SHORT', f"the last shot {rows[-1]['shot_id']} runs {rows[-1]['len_s']:.1f} s, under the reel's median {median:.1f} s - "
            'let the ending hold (the study set ends on its longest shots)')
    tellers = [r for r in rows if climactic(r)]
    if len(tellers) == 1 and peak_fast and not _departs(tellers[0], 'CLIMAX_ONE_SHOT'):
        say('CLIMAX_ONE_SHOT', f"the climax is one shot ({tellers[0]['shot_id']}) - cut it into short shots from more than one side "
            '(rule 9; a held climax departs climax_one_shot)')
    heights = sorted(r['height'] for r in built)
    peak = [r for r in tellers if measured(r) and not _departs(r, 'CLIMAX_NOT_CLOSEST')]
    if peak and len(heights) > 2:
        rank = heights[min(len(heights) - 1, int(v('CLIMAX_CLOSE_RANK') * len(heights)))]
        if max(r['height'] for r in peak) < rank:
            say('CLIMAX_NOT_CLOSEST', f"the climax ({', '.join(r['shot_id'] for r in peak)}) is not among the closest shots of the reel "
                f"(its focus {max(r['height'] for r in peak):.2f} of the frame, the reel's top quarter from {rank:.2f}) - "
                'come closest at the peak (rule 9)')
    run = []
    for r in rows + [None]:
        if r is not None and _departs(r, 'ROLE_RUN'):
            continue
        if r is not None and r.get('role') and run and r['role'] == run[-1]['role']:
            run.append(r)
            continue
        if len(run) > v('ROLE_RUN_MAX'):
            say('ROLE_RUN', f"{len(run)} {run[0]['role']} shots in a row ({', '.join(x['shot_id'] for x in run)}) - break them "
                'with a reveal, a comparison or a consequence')
        run = [r] if r is not None and r.get('role') else []
    out += [f'THREAD_OFF_SCREEN: {t}' for t in thread_lines(rows, story) if 'THREAD_OFF_SCREEN' not in silenced]
    for code, text in untold_lines(rows, story):
        say(code, text)
    return out


def untold_lines(rows, story=None):
    """[(code, text)] - CUT_UNTOLD (rule 15): a planned cut that tells no story beat (no story_beat_ids, no line of a
    beat). Why (2026-10-20): ten topics planned blind lost as 'a tour of the mechanism'; the study set's reels give every
    cut a job, whatever it shows - the share of mechanism cuts (0-0.94 there) never told good from bad. Planned and built
    rows alike (a built row's beats come from its lines)."""
    if not (story or {}).get('beats'):
        return []
    loose = [r['shot_id'] for r in rows if 'story_beats' in r and not r.get('story_beats') and not _departs(r, 'CUT_UNTOLD')]
    return [('CUT_UNTOLD', f"cuts {', '.join(loose)} tell no story beat (story_beat_ids, or lines of a beat) - give each a job: "
             "what the last beat caused, what raises the next question, what makes the stake felt")] if loose else []


def _carries(shown, carrier, whole=True):
    """A shown id carries the thread: it is the carrier or a part of it, or (whole: a shown part, which is in view entire)
    the thing the carrier is a part of - the whole bike shown shows its chain; a world element names no such view."""
    return shown == carrier or shown.startswith(f'{carrier}/') or (whole and carrier.startswith(f'{shown}/'))


def thread_seen(r, carriers):
    """True / False / None (unknown) - does the shot show the thread? Planned: one of its show parts or world elements
    carries it. Built (r['visible'], {part: True/False/None} measured on the frame report): a shown part that carries it is
    seen; one measured not seen, or no shown part that carries it, is not; unmeasured is unknown, never a pass."""
    if 'visible' in r:
        if r['visible'] is None:
            return None
        found = [seen for part, seen in r['visible'].items() if any(_carries(part, c) for c in carriers)]
        if any(found):
            return True
        if found and all(f is None for f in found):
            return None
        if not found and any(_carries(e, c, whole=False) for e in r.get('elements') or [] for c in carriers):
            return None   # a world element carries it - not measured on the frames
        return False
    return any(_carries(p, c) for p in r.get('parts') or [] for c in carriers) or \
        any(_carries(e, c, whole=False) for e in r.get('elements') or [] for c in carriers)


def thread_lines(rows, story):
    """THREAD_OFF_SCREEN (rule 14): the story's thread (story.thread carrier_ids) missing from more than
    THREAD_OFF_SHARE_MAX of the shots or from more than THREAD_OFF_RUN_MAX in a row. A shot that hands the thread over
    (handover.thread_to) makes the shots after it hold the new carriers; one departing (thread_off_screen) is not counted
    and does not break a run; an unknown one breaks it and is not counted. Why (2026-10-19): the study set keeps what the
    story follows on screen; the bike reel lost its chain in five shots of thirteen and read as a parade of parts."""
    carriers = list(((story or {}).get('thread') or {}).get('carrier_ids') or [])
    if not carriers:
        return []
    off, counted, run, worst, held = [], 0, [], [], list(carriers)
    for r in rows:
        seen = None if _departs(r, 'THREAD_OFF_SCREEN') else thread_seen(r, carriers)
        if _departs(r, 'THREAD_OFF_SCREEN'):
            pass
        elif seen is None:
            run = []
        else:
            counted += 1
            if seen:
                run = []
            else:
                off.append(r['shot_id']); run.append(r['shot_id'])
                worst = max(worst, run, key=len)[:]
        if r.get('thread_to'):
            carriers = list(r['thread_to'])
            held += [c for c in carriers if c not in held]
    out = []
    if len(worst) > v('THREAD_OFF_RUN_MAX') or (counted and len(off) / counted > v('THREAD_OFF_SHARE_MAX')):
        out.append(f"{len(off)} of {counted} shots ({', '.join(off)}) show none of the thread "
                   f"({', '.join(held)}), {len(worst)} in a row"
                   f"{' (' + ', '.join(worst) + ')' if len(worst) > 1 else ''} - keep what the story follows in the picture "
                   f"(over {v('THREAD_OFF_SHARE_MAX'):.0%}, or {v('THREAD_OFF_RUN_MAX') + 1} in a row, the viewer lets go); "
                   'hand it over where it passes to another thing (handover.thread_to), or record the departure (thread_off_screen)')
    return out


def _change_kind(carried):
    return next((r.get('value') for r in carried or [] if r.get('item') == 'change_kind'), None)


def _key_times(show, length_s):
    """Seconds into the shot of its key-change moments."""
    from .blender_ops.show_core import at_share
    return [round(at_share(r['at']) * length_s, 3) for r in show if r.get('key')]


def _change_times(shot, length_s):
    """When each change the shot makes starts, in seconds (direction.change `at`, a share of the shot)."""
    from .change_moves import changes_of
    return [round(c['at'][0] * length_s, 3) for c in changes_of(shot) if c.get('at')]


def _change_spans(shot, length_s):
    """[start_s, end_s] of each change the shot makes (direction.change `at`)."""
    from .change_moves import changes_of
    return [[round(c['at'][0] * length_s, 3), round(c['at'][1] * length_s, 3)] for c in changes_of(shot) if c.get('at')]


def unfilled_s(r):
    """Seconds of a cut neither its words (from its start, spoken_s) nor a change on screen (change_spans) fill."""
    spans = sorted([[max(a, r.get('spoken_s') or 0.0), b] for a, b in r.get('change_spans') or [] if b > (r.get('spoken_s') or 0.0)])
    covered, edge = 0.0, r.get('spoken_s') or 0.0
    for a, b in spans:
        a = max(a, edge)
        if b > a:
            covered, edge = covered + b - a, b
    return max(0.0, r['len_s'] - (r.get('spoken_s') or 0.0) - covered)


def _plan_row(s, lines_by_id, spoken):
    from .blender_ops.show_core import show_of
    from .decisions import opens_inside
    from .story_core import beats_of
    line_beat = {i: l.get('story_beat_id') for i, l in lines_by_id.items()}
    row = {'shot_id': s['shot_id'], 'len_s': s['duration_s'], 'role': s.get('role'), 'scale_step': (s.get('direction') or {}).get('scale_step'),
           'handover': (s.get('handover') or {}).get('kind'), 'story_beats': s.get('story_beat_ids') or beats_of(s['line_ids'], line_beat),
           'departs': [d['item'] for d in (s.get('pattern') or {}).get('departs', [])],
           'spoken_s': spoken.get(s['shot_id']) if lines_by_id else None, 'opens': opens_inside(s),
           'key_show': any(r.get('key') for r in show_of(s)), 'key_times': _key_times(show_of(s), s['duration_s']),
           'parts': sorted({p for r in show_of(s) for p in r['parts']}), 'elements': list(s.get('elements') or []),
           'thread_to': list((s.get('handover') or {}).get('thread_to') or []), 'change_times': _change_times(s, s['duration_s']),
           'journey': (s.get('direction') or {}).get('journey'), 'change_spans': _change_spans(s, s['duration_s']),
           'settle_at': ((s.get('direction') or {}).get('move') or {}).get('settle_at')}
    kind = _change_kind((s.get('pattern') or {}).get('carried'))
    if kind:
        row['change_kind'] = kind
    return row


def unresolved(findings):
    """DIRECTION_UNRESOLVED (broken by default: studio/gates.py) when direction findings a shot or the reel can depart
    from are left - each is fixed, or answered with why (the shot's pattern departs, the reel's reel depart). The
    blocking findings stand on their own. Why (2026-10-19): these were warnings the bike run settled its shot list and
    built its clay past - five on the shot list, six on the clay - and nothing asked for an answer."""
    left = [line.split(':', 1)[0] for line in findings if line.split(':', 1)[0] in DEPARTS]
    if not left:
        return []
    codes = ', '.join(f'{c} x{left.count(c)}' if left.count(c) > 1 else c for c in dict.fromkeys(left))
    return [f"DIRECTION_UNRESOLVED: {len(left)} direction findings are open ({codes}) - fix each, or say why: the shot's "
            "pattern departs {item: the code in lower case, why: a sentence}, or the reel's (studio reel depart --code CODE --why ...)"]


def plan_rows(shots, lines_by_id=None, chars_per_s=None):
    """Rows from a shot list body (decisions): planned role, size step, length, hand-over, story beats, how long its
    words take (at chars_per_s, the reference's pace), whether it opens something, what it says happens (pattern
    change_kind) and whether it marks the key change."""
    from .decisions import CHARS_PER_S, speech_occupancy
    lines_by_id = lines_by_id or {}
    spoken = speech_occupancy(shots, {i: l.get('text', '') for i, l in lines_by_id.items()},   # lines said across cuts included
                              chars_per_s or CHARS_PER_S)
    return [_plan_row(s, lines_by_id, spoken) for s in shots]


def _direction(position, aim):
    d = [b - a for a, b in zip(position, aim)]
    n = math.sqrt(sum(x * x for x in d)) or 1.0
    return [x / n for x in d], n


def _still(rig, move, dynamics):
    """Does the camera rest over the shot? The camera's own measure when it has one (camera_dynamics: axis 'still');
    else its travel against its distance to what it looks at, how far it turns (pan and tilt, from the move's poses
    and the rig's pitch) and how much its lens changes. None when neither says where it looks (unknown, not moving)."""
    axis = ((dynamics or {}).get('camera') or {}).get('axis')
    if axis:
        return axis == 'still'
    poses = [f for f in (move or {}).get('frames') or [] if f.get('position') and f.get('aim')]
    samples = [s for s in (rig or {}).get('samples') or [] if s.get('camera')]
    if not poses:
        return None
    first, reach = _direction(poses[0]['position'], poses[0]['aim'])
    places = [s['camera'] for s in samples] or [f['position'] for f in poses]
    moved = max(math.dist(p, places[0]) for p in places) / max(reach, 1e-6)
    turned = max(math.degrees(math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(first, _direction(f['position'], f['aim'])[0]))))))
                 for f in poses)
    pitches = [s['pitch_deg'] for s in samples if s.get('pitch_deg') is not None]
    if pitches:
        turned = max(turned, max(pitches) - min(pitches))
    lenses = [x for x in [f.get('lens_mm') for f in poses] + [s.get('lens_mm') for s in samples] if x]
    zoom = abs(math.log(max(lenses) / min(lenses))) if lenses else 0.0
    return moved < v('STILL_MOVE') and turned < v('STILL_TURN_DEG') and zoom < v('STILL_LENS_LOG')


def _class_colours(summary):
    """{class: sRGB 0-255} of a probe report's id pictures (frame_probe_core.palette, in the probe's class order)."""
    from .blender_ops.frame_probe_core import palette
    names = ['support', 'subject', *[f'key:{k}' for k in summary.get('key_parts', [])], *[f'hide:{c}' for c in summary.get('concealed_parts', [])]]
    return {n: tuple(round(c * 255) for c in srgb) for n, (_, srgb) in zip(names, palette())}


def silhouette(image, colours=None):
    """The focus's pixels in a probe id picture (frame_probe/frame_NNNN_id.png) - a set of pixel indices, at the
    picture's size. colours: the focus classes' sRGB (focus_classes); without them, everything that is neither black
    (nothing) nor the support grey. Why (2026-10-19): the bike reel's workshop, a context element with a class of its
    own, counted as subject and two shots of different sizes read as one picture (overlap 0.98)."""
    from PIL import Image
    tol = v('SUPPORT_GREY')
    with Image.open(image) as src:
        raw = src.convert('RGB').tobytes()
    out = set()
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        if colours is not None:
            if any(abs(r - c[0]) <= tol and abs(g - c[1]) <= tol and abs(b - c[2]) <= tol for c in colours):
                out.add(i // 3)
            continue
        if r == g == b == 0 or (abs(r - 127) <= tol and abs(g - 127) <= tol and abs(b - 127) <= tol):
            continue
        out.add(i // 3)
    return out


def focus_classes(shot, summary):
    """The probe classes a shot is about: its declared focus (patterns.focus_of) when it has one, else the subject class
    and every key part that is not context - what a picture draws around it (frame_targets: workshop, stand, people)
    has a class of its own so its size is measured, but is not what the shot is about."""
    from .patterns import focus_of
    ids, _ = focus_of(shot)
    keys = summary.get('key_parts', [])
    if ids:   # a declared focus with the parts inside it that have classes of their own (bike, bike/chain)
        return [f'key:{k}' for k in keys if any(k == i or k.startswith(f'{i}/') for i in ids)] or [f'key:{i}' for i in ids]
    sources = summary.get('key_sources') or {}
    return ['subject', *[f'key:{k}' for k in summary.get('key_parts', []) if sources.get(k, 'declared') != 'context']]


def _ends(folder):
    """The probe's first and last id pictures of a version (None when it kept none)."""
    pictures = sorted((folder / 'frame_probe').glob('frame_*_id.png'))
    return (pictures[0], pictures[-1]) if pictures else (None, None)


def _sources(project_path, shot, summary, fps):
    """{key part: where it came from} - the report's own (frame_probe, 2026-10-19 on), else what the build would give
    the probe now (blender.probe_inputs) for a report written before."""
    if summary.get('key_sources'):
        return summary['key_sources']
    try:
        from .blender import probe_inputs
        return {k['id']: k.get('source', 'declared') for k in probe_inputs(project_path, shot, fps)['key_parts']}
    except (StudioError, KeyError, OSError, ValueError):
        return {}


def _moment_frame(at, count):
    from .blender_ops.show_core import at_share
    return round(at_share(at) * max(0, count - 1))


def _nearest(frames, frame):
    return min(frames, key=lambda f: abs(f['frame'] - frame)) if frames else None


def _hidden(frame, parts):
    """The share of parts hidden in front at a probed frame (pixel-weighted over the parts), None when not measured
    (a report from before the probe read occlusion, or a part it was not asked to read)."""
    found = [(frame.get('occlusion') or {}).get(p) for p in parts]
    if any(f is None for f in found):
        return None
    alone = sum(f['alone'] for f in found)
    return round(1.0 - sum(f['alone'] * (1.0 - f['hidden']) for f in found) / alone, 4) if alone else None


def _size(boxes, aspect):
    """The long side of the boxes' joint box in frame heights (a width counts as width x aspect)."""
    x0, y0, x1, y1 = min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)
    return round(max(y1 - y0, (x1 - x0) * aspect), 4)


def key_moments(shot, report):
    """[{at, parts, frame, size, hidden}] of the shot's key-change show rows at their probed frame nearest the moment:
    the parts' joint box long side (in frame heights) and the share hidden in front (frame_probe occlusion)."""
    from .blender_ops.show_core import show_of
    frames = report.get('frames') or []
    w, h = (report.get('summary') or {}).get('size_px') or (1080, 1920)
    out = []
    measured = set((report.get('summary') or {}).get('key_parts', []))
    for row in show_of(shot):
        if not row.get('key'):
            continue
        near = _nearest(frames, _moment_frame(row['at'], shot['duration_frames']))
        known = near is not None and all(p in measured for p in row['parts'])   # each part a class of the probe's
        boxes = [b for b in (((near or {}).get('shapes') or {}).get(f'key:{k}', {}).get('bbox') for k in measured
                             if any(_within(k, p) for p in row['parts'])) if b] if known else []
        out.append({'at': row['at'], 'parts': list(row['parts']), 'frame': (near or {}).get('frame'),
                    'size': (_size(boxes, w / h) if boxes else 0.0) if known else None,
                    'hidden': _hidden(near, row['parts']) if known else None})
    return out


def _within(key, part):
    """A probe class belongs to a part when it is the part or one of its own (bike/chain within bike)."""
    return key == part or key.startswith(f'{part}/')


def part_hidden(shot, report):
    """{part: median share hidden in front over the probed frames of its window} for every part a show row names: the
    frames where a row naming it is the one held (show_core.held_at - rows of one moment hold together), else every
    frame that measured it. Why (2026-10-25, semiconductor_fab s14): the stack a closing shot shows only once the pod's
    door has opened (a row at 0.9) was judged over the whole shot, the pod still shut for most of it - 85 % hidden
    where it showed 3-15 % hidden while shown (story.md: the median over its window)."""
    from .blender_ops.show_core import at_share, held_at, show_of
    rows = show_of(shot)
    last = max(1, int(shot.get('duration_frames') or 1) - 1)

    def held(frame):   # the parts of every row held at that frame (rows at one moment hold together)
        row = held_at(rows, frame / last)
        return {p for r in rows if row is not None and abs(at_share(r['at']) - at_share(row['at'])) < 1e-9 for p in r['parts']}
    out = {}
    for part in sorted({p for row in rows for p in row['parts']}):
        measured = [f for f in report.get('frames') or [] if part in (f.get('occlusion') or {})]
        window = [f for f in measured if part in held(f['frame'])] or measured
        if window:
            out[part] = round(statistics.median(f['occlusion'][part]['hidden'] for f in window), 4)
    return out


def part_visible(shot, report, hidden):
    """{part: True / False / None} for every part a show row names: in the picture (its probe class, or one of its own, has
    pixels) in at least half the probed frames and hidden in front no more than PART_HIDDEN_MAX; None when the probe did not
    class it (summary.key_parts) or no frame measured it - unread is unknown, never unseen."""
    from .blender_ops.show_core import show_of
    frames = report.get('frames') or []
    measured = set((report.get('summary') or {}).get('key_parts', []))   # the probe's classes: a part not among them is unread
    out = {}
    for part in sorted({p for row in show_of(shot) for p in row['parts']}):
        classes = [k for k in measured if _within(k, part)]
        read = [any(((f.get('shapes') or {}).get(f'key:{k}') or {}).get('px', 0) > 0 for k in classes)
                for f in frames if f.get('shapes')] if classes else []
        if not read:
            out[part] = None
            continue
        out[part] = sum(read) * 2 >= len(read) and (hidden.get(part) is None or hidden[part] <= v('PART_HIDDEN_MAX'))
    return out


def _coloured_render(folder, version):
    """The frames folder of the latest coloured render (look, review, final) of this version, else None: a part's
    contrast is a fact of its materials and light, which the clay does not have."""
    renders = []
    for meta in folder.parent.parent.glob('renders/*/render.json'):
        data = read_json(meta)
        if data.get('scene_version') == version and data.get('profile') in ('look', 'review', 'final') and (meta.parent / 'frames').is_dir():
            renders.append((meta.stat().st_mtime, meta.parent / 'frames'))
    return max(renders)[1] if renders else None


CONTRAST_HEIGHT_PX = 540   # the coloured frame and the id picture are compared at this height (the id pass is 256)


def outline_apart(lab, inside):
    """The share of a region's outline steps where the colour a few pixels inside differs from the colour a few pixels
    outside by CONTRAST_STEP or more (CIE76); None when it has no outline to read. lab: a PIL Lab image; inside: the
    region's pixels as a set of (x, y)."""
    import math
    width, height = lab.size
    px, off = lab.load(), max(1, round(v('CONTRAST_OFFSET') * height))
    steps = []
    for x, y in inside:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if (x + dx, y + dy) in inside:
                continue
            a, b = (x - dx * (off - 1), y - dy * (off - 1)), (x + dx * off, y + dy * off)
            if a not in inside or b in inside or not (0 <= b[0] < width and 0 <= b[1] < height):
                continue
            pa, pb = px[a], px[b]   # PIL Lab: L 0-255, a/b offset by 128
            steps.append(math.dist((pa[0] * 100 / 255, pa[1], pa[2]), (pb[0] * 100 / 255, pb[1], pb[2])))
    return round(sum(d >= v('CONTRAST_STEP') for d in steps) / len(steps), 4) if steps else None


def part_contrast(shot, report, folder, version):
    """{key-change part: share of its outline that stands apart} on the coloured render's frame at the probed frame of
    the moment (rule 13; the part's pixels from the probe's id picture there). Why (2026-10-19): the bike reel's
    explained parts were white on white and silver on silver - every one measured 0.33-0.59, a blue shirt 0.87."""
    from PIL import Image
    from .blender_ops.show_core import show_of
    from .critique import _lab
    frames_dir = _coloured_render(folder, version)
    if frames_dir is None:
        return {}
    colours = _class_colours(report.get('summary') or {})
    tol = v('SUPPORT_GREY')
    out = {}
    for row in show_of(shot):
        if not row.get('key'):
            continue
        near = _nearest(report.get('frames') or [], _moment_frame(row['at'], shot['duration_frames']))
        picture = frames_dir / f"frame_{near['frame']:06d}.png" if near else None
        ids = folder / 'frame_probe' / f"frame_{near['frame']:04d}_id.png" if near else None
        if picture is None or not picture.is_file() or not ids.is_file():
            continue
        with Image.open(ids) as src:
            size = (round(CONTRAST_HEIGHT_PX * src.size[0] / src.size[1]), CONTRAST_HEIGHT_PX)
            id_image = src.convert('RGB').resize(size, Image.NEAREST)
        with Image.open(picture) as src:
            lab = _lab(src.convert('RGB').resize(size, Image.BILINEAR))
        pixels = id_image.load()
        for part in row['parts']:
            own = [c for k, c in colours.items() if k.startswith('key:') and _within(k[4:], part)]   # the part and its own parts
            if not own or part in out:
                continue
            inside = {(x, y) for x in range(size[0]) for y in range(size[1])
                      if any(all(abs(pixels[x, y][i] - c[i]) <= tol for i in range(3)) for c in own)}
            apart = outline_apart(lab, inside) if len(inside) >= 20 else None
            if apart is not None:
                out[part] = apart
    return out


def built_row(project_path, entry, fps, chars_per_s=None):
    """One built shot's row (unmeasured when it has no version): its focus box over its frames, environment, stillness,
    its focus silhouette and camera direction at its first and last frame, how long its words take, what it says
    happens, its key-change moments (size, hidden share), how much of each shown part is hidden, and - on a coloured
    render of the version - its key parts' contrast."""
    from .blender_ops.show_core import show_of
    from .decisions import CHARS_PER_S, opens_inside, spoken_seconds
    from .patterns import _union
    from .project import load_shot
    shot = load_shot(project_path, entry['shot_id'])
    said = ((shot.get('narration') or {}).get('text') or '').strip()
    row = {'shot_id': entry['shot_id'], 'len_s': entry['frame_count'] / fps, 'role': (shot.get('screen') or {}).get('role'),
           'scale_step': (shot.get('direction') or {}).get('scale_step'), 'handover': (shot.get('handover') or {}).get('kind'),
           'story_beats': shot.get('story_beat_ids') or [], 'departs': [d['item'] for d in (shot.get('screen') or {}).get('pattern_departs', [])],
           'spoken_s': spoken_seconds([said], chars_per_s or CHARS_PER_S) if said else None, 'continues_into': (shot.get('narration') or {}).get('continues_into') or [],
           'opens': opens_inside(shot), 'key_show': any(r.get('key') for r in show_of(shot)),
           'key_times': _key_times(show_of(shot), entry['frame_count'] / fps), 'visible': None,
           'elements': list(shot.get('elements') or []), 'thread_to': list((shot.get('handover') or {}).get('thread_to') or []),
           'off_picture': ((shot.get('camera') or {}).get('off_picture_why') if not (shot.get('camera') or {}).get('move') else None),
           'change_times': _change_times(shot, entry['frame_count'] / fps), 'journey': (shot.get('direction') or {}).get('journey'),
           'change_spans': _change_spans(shot, entry['frame_count'] / fps),
           'settle_at': ((shot.get('direction') or {}).get('move') or {}).get('settle_at')}
    kind = _change_kind((shot.get('screen') or {}).get('pattern_carried'))
    if kind:
        row['change_kind'] = kind
    version = shot.get('scene_version')
    folder = Path(project_path) / 'shots' / entry['shot_id'] / 'versions' / str(version)
    if not version or not (folder / 'frame_report.json').is_file():
        if row['key_show']:
            row['key_moments'] = [{'at': r['at'], 'parts': list(r['parts']), 'size': None, 'hidden': None} for r in show_of(shot) if r.get('key')]
        return row
    report = read_json(folder / 'frame_report.json')
    summary = report.get('summary') or {}
    snapshot = read_json(folder / 'shot.snapshot.json') if (folder / 'shot.snapshot.json').is_file() else shot
    classes = focus_classes(snapshot, {**summary, 'key_sources': _sources(project_path, snapshot, summary, fps)})
    framed = [(f.get('frame'), b) for f, b in ((f, _union([f['shapes'][c]['bbox'] for c in classes if ((f.get('shapes') or {}).get(c) or {}).get('bbox')]))
                                                for f in report.get('frames', [])) if b]
    boxes = [b for _, b in framed]
    row.update({'key_moments': key_moments(shot, report), 'hidden': part_hidden(shot, report),
                'contrast': part_contrast(shot, report, folder, version)})
    row['visible'] = part_visible(shot, report, row['hidden'])
    if not boxes:
        return row
    box = [statistics.median(b[k] for b in boxes) for k in range(4)]
    settled = boxes[int(len(boxes) * 0.6):] or boxes
    read = lambda name: read_json(folder / name) if (folder / name).is_file() else None   # noqa: E731
    rig, move, dynamics = read('camera_rig_report.json'), read('camera_move_report.json'), read('camera_dynamics.json')
    arrive = ((dynamics or {}).get('follow') or {}).get('arrive_s')   # when the camera comes to rest (camera_follow)
    if arrive is not None:   # the picture it lands on: the focus over the frames from there (a fly-in's opening is where it lands)
        landed = [b for frame, b in framed if frame is not None and frame / fps >= arrive] or boxes[-1:]
        row.update({'arrive_s': arrive, 'height_landed': statistics.median(b[3] - b[1] for b in landed)})
    views = [f['view'] for f in (move or {}).get('frames') or [] if f.get('view')]
    look_start, look_end = _looks(move, snapshot)
    if not views and look_start:   # a camera of keys: its height and side from the keys, in the world
        views = [_look_view(look_start), _look_view(look_end)]
    first, last = _ends(folder)
    colours = _class_colours(summary)
    focus = [colours[c] for c in classes if c in colours]
    row.update({'height': box[3] - box[1], 'env': summary.get('support_share_median') or 0.0,
                'fills': sum(b[0] <= 0.002 and b[1] <= 0.002 and b[2] >= 0.998 and b[3] >= 0.998 for b in settled) * 2 >= len(settled),
                'still': _still(rig, move, dynamics), 'view_start': views[0] if views else None, 'view_end': views[-1] if views else None,
                'silhouette_start': silhouette(first, focus) if first else None, 'silhouette_end': silhouette(last, focus) if last else None,
                'silhouette_frame_start': frame_classes(first) if first else None, 'silhouette_frame_end': frame_classes(last) if last else None,
                'look_start': look_start, 'look_end': look_end})
    return row


def _spoken_across(rows):
    """Each built row's spoken_s as the seconds of it the voice speaks over: a shot's own words from its start (a shot
    whose last line runs on, narration.continues_into, lends what overruns to the shots it runs into)."""
    spill = {}
    for r in rows:
        own = (r.get('spoken_s') or 0.0)
        over = max(0.0, own - r['len_s']) if r.get('continues_into') else 0.0
        if r.get('spoken_s') is not None or r['shot_id'] in spill:
            r['spoken_s'] = min(r['len_s'], own + spill.get(r['shot_id'], 0.0))
        for into in r.get('continues_into') or []:
            spill[into] = spill.get(into, 0.0) + over
            over = max(0.0, over - next((x['len_s'] for x in rows if x['shot_id'] == into), 0.0))


def reel_departs(path):
    from .project import project_dir
    file = project_dir(path) / REEL_DEPARTS
    return read_json(file).get('departs', []) if file.is_file() else []


def silenced(path):
    return {d['code'] for d in reel_departs(path)}


def depart(project, code, why, user_words=None, agent_note=None, remove=False):
    """A choice about the whole reel that a flow finding reads as on purpose (a reel of close textures, a reel in one
    long take): the user's words, or a delegated run's reason. remove takes it back."""
    from .project import load_project, project_dir
    path = project_dir(project)
    if code not in DEPARTS:
        raise StudioError('INPUT_INVALID', f'reel depart: {code} is not a flow finding ({sorted(DEPARTS)})')
    rows = [d for d in reel_departs(path) if d['code'] != code]
    if not remove:
        if not why or len(why.strip()) < 12:
            raise StudioError('INPUT_INVALID', 'reel depart needs why (a sentence)')
        if user_words:
            from .generative.review import check_user_words
            who = {'by': 'user', 'user_words': check_user_words(user_words, 'reel depart')}
        elif agent_note and load_project(path).get('delegation'):
            who = {'by': 'agent', 'agent_note': agent_note}
        else:
            raise StudioError('INPUT_INVALID', "reel depart needs the user's words; only a delegated run departs on its own reason (--agent-note)")
        rows.append({'code': code, 'why': why.strip(), **who, 'at': now()})
    write_json(path / REEL_DEPARTS, {'schema_version': 1, 'departs': rows})
    return {'departs': rows, 'artifacts': [str(path / REEL_DEPARTS)]}


def _values(node, path):
    """Every value at a dotted path, lists read through (show.how: each show row's how)."""
    found = [node]
    for key in path.split('.'):
        found = [x for v in found for x in (v if isinstance(v, list) else [v])]
        found = [v.get(key) for v in found if isinstance(v, dict) and v.get(key) is not None]
    return [x for v in found for x in (v if isinstance(v, list) else [v])]


def uses_device(shot, rule):
    """Does a shot use a thread device, by the rule of the field that makes it (library/story_axes.json thread
    device_shots: path and in, min or any)?"""
    values = _values(shot, rule['path'])
    if 'in' in rule:
        return any(v in rule['in'] for v in values)
    if 'min' in rule:
        return any(isinstance(v, (int, float)) and v >= rule['min'] for v in values)
    return bool(values)


def device_lines(story, shots, silenced=frozenset()):
    """THREAD_DEVICE_UNUSED (taste): the story's thread kind has devices a shot field makes, and no built shot uses one -
    the thread is said, but nothing on screen carries it the way its kind is shown (2026-10-20, canal: water was the
    thread, its way through the culverts the explanation, and no shot drew it moving)."""
    from .story_core import load_axes
    kind = ((story or {}).get('thread') or {}).get('kind')
    if not kind or not shots or 'THREAD_DEVICE_UNUSED' in silenced:
        return []
    devices = (((load_axes().get('thread') or {}).get('values') or {}).get(kind) or {}).get('device_shots') or {}
    if not devices or any(uses_device(s, rule) for s in shots for rule in devices.values()):
        return []
    return [f"THREAD_DEVICE_UNUSED: the {kind} thread is carried by none of its devices in any built shot - "
            f"{'; '.join(devices)} ({', '.join(r['path'] for r in devices.values())}), or say why (studio reel depart)"]


def _sameness_lines(path, shots):
    """VIEW_MONOTONE (the side each built cut sees its subject from) and RECIPE_MONOTONE (the feel each built version
    measures as) over the reel."""
    from .camera_taste import PROFILE, load_profile, recipe_lines
    from .views import reel_lines as view_lines
    felt = []
    for s in shots:
        measured = Path(path) / 'shots' / s['shot_id'] / 'versions' / str(s.get('scene_version')) / 'camera_dynamics.json'
        recipe = ((read_json(measured).get('judged') or {}).get('recipe') if measured.is_file() else None)
        if recipe:
            felt.append(recipe)
    return view_lines(path) + (recipe_lines(load_profile() if PROFILE.is_file() else {}, felt) if len(felt) >= 4 else [])


def _joins(path, rows):
    """Each built row's join_in: how the camera crosses the cut into it (grammar_core.join of the two current versions'
    scene measurements, grammar_synth.json), when both are measured."""
    from .grammar_core import join
    from .project import load_shot
    synth = []
    for r in rows:
        version = load_shot(path, r['shot_id']).get('scene_version')
        file = Path(path) / 'shots' / r['shot_id'] / 'versions' / str(version) / 'grammar_synth.json'
        synth.append(read_json(file) if version and file.is_file() else None)
    for (a, sa), (b, sb) in zip(zip(rows, synth), list(zip(rows, synth))[1:]):
        if sa and sb:
            b['join_in'] = join(sa['pairs'], sb['pairs'], sb['fps'])['kind']


def built(project):
    """The reel flow of the latest built versions, in cut order: {rows, warnings, errors} (errors: findings the
    project's gate policy makes errors - all-strict, policy.gates)."""
    from .gates import severity_for, sort_lines
    from .project import load_project, project_dir
    from .story import body_of, load as load_story
    path = project_dir(project)
    data = load_project(path)
    from .decisions import reference_chars_per_s
    pace = reference_chars_per_s(path)
    rows = [built_row(path, e, data['output']['fps'], pace) for e in data['shots']]
    _spoken_across(rows)
    _joins(path, rows)
    unbuilt = [r['shot_id'] for r in rows if r.get('height') is None]
    story = body_of(load_story(path))
    out = lines(rows, story, silenced(path), read_reference(path))
    from .project import load_shot
    shots = [load_shot(path, e['shot_id']) for e in data['shots']]
    out += device_lines(story, [s for s in shots if s.get('scene_version')], silenced(path))
    out += [line for line in _sameness_lines(path, shots) if line.split(':', 1)[0] not in silenced(path)]
    if (story or {}).get('beats'):   # a reel told as a story answers its findings (the shot list's rule, reel_flow.unresolved)
        out += unresolved(out)
    from .project import load_shot as _load
    made_by_model = [s for s in unbuilt if (_load(path, s).get('route') or {}).get('mode') == 'generative']
    unbuilt = [s for s in unbuilt if s not in made_by_model]
    if made_by_model:   # a cut a model makes (studio/maker.py) has no build to measure: a person reviews its motion instead
        out.append(f"REEL_FLOW_GENERATED: {', '.join(made_by_model)} are made by a video model - not measured here; their motion is "
                   'checked by a person (video-review) - the picture checks skip them and the cuts beside them')
    if unbuilt and len(unbuilt) + len(made_by_model) < len(rows):
        out.append(f"REEL_FLOW_PARTIAL: {', '.join(unbuilt)} not built or not measured - the picture checks skip them and the cuts beside them")
    elif unbuilt:   # nothing measured is said and refused, never a clean read (2026-10-21: with every shot unmeasured nothing was said)
        out.append(f"REEL_FLOW_UNMEASURED: no shot of the reel is built and measured ({', '.join(unbuilt)}) - the reel flow checked nothing")
    errors, warnings = sort_lines(out, severity_for(path))
    shown = [{k: val for k, val in r.items() if not k.startswith('silhouette')} for r in rows]
    return {'rows': shown, 'warnings': warnings, 'errors': errors, 'departs': reel_departs(path)}
