"""The v2 launcher's instruction (scripts/reel_agent.py --v2): what a run is for, how its work is judged and where it
stops - never a picture. Every picture, opening, scale step, camera and length is the agent's to decide, from the story
and the reference (the user, 2026-10-26: a brief gives goals and criteria, never creative answers; a better choice the
user has to suggest is a hole in the process)."""
from __future__ import annotations

SKILL = '.agents/skills/reel-production-v2/SKILL.md'

GOALS = (
    "Make the reel the request asks for as a v2 reel (python -m studio2): every cut a picture planned as a cutaway - what it "
    "opens, what flows, what is named, its scale step - drawn by the free image tool, moved and marked locally, cut with the "
    "scratch voice into a free preview, and put to the user on the approval sheet before anything is paid.",
    "A viewer who does not know the plan learns each cut's point from its picture, and the reel's point from the reel.",
    "The reel looks like it belongs to the reference series, cut by cut and as a whole.",
)
CRITERIA = (
    "The checks that read your output are the floor (SKILL lists them): lint of the cuts layer, the picture checks, the "
    "located marks' judge, the reel flow on the cuts (hook, key change, pace, the same view read from the pictures), the "
    "style reader against the reference's own cuts, reel see and reel judge on the preview. Fix what they find in the plan "
    "or the pictures - never the check, never with a camera or label tweak on pictures that failed.",
    "Cut lengths and the rhythm come from the project's reference (studio2 cuts rules), lines running across cuts where a "
    "picture is shorter than its sentence; label and caption letters at the reference's measured height.",
    "A thing that belongs to the scene is painted in the pictures; only a speck-sized thing is drawn over them.",
    "Every finding is answered: fixed, or departed from with why. Your own choices are recorded (decide note); spending, "
    "the user's taste and the user's words are not yours.",
)
STOPS = (
    "Stop at the approval sheet (studio2 sheet build): show review/sheet/index.html - each cut's pictures, line, motion, "
    "free preview, paid model and cost, and the total - and write in REPORT.md what the user is to decide. Open the pages "
    "and clips yourself before you call them ready.",
    "This run has no user at hand: no command takes the user's words here (USER_WORDS_UNATTENDED) - never write their words "
    "yourself; sheet approve, video review and freeze words are theirs, recorded outside this run.",
    "Stop and report at once if anything would cost money (a quota or a pay prompt from the image tool, a paid call) or "
    "would need frozen code changed.",
)


def instruction(request, project=None):
    """The run's whole instruction: the skill, the goals, the criteria, the stop points, then the request."""
    head = (f"Use {SKILL} (read it first, and the reference modules its index names for the phase you are in). You are the "
            "production orchestrator of a v2 reel; decide every picture yourself, from the story and the reference.")
    parts = [head, 'GOALS:'] + [f'- {g}' for g in GOALS] + ['CRITERIA:'] + [f'- {c}' for c in CRITERIA] + ['STOP POINTS:'] + [f'- {s}' for s in STOPS]
    if project:
        parts.append(f'Existing project: {project}')
    parts += ['', 'User request:', request]
    return '\n'.join(parts)
