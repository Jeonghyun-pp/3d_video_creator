#!/usr/bin/env python3
"""Run the repository production skill through native Codex, explicitly on Astra.

From the sealed engine only (studio/seal.py, 2026-10-18): `python -I -B <sealed>/studio/_launch.py reel_agent ...` (print it
with `studio seal status`). Astra starts in a fresh empty folder with the checkout added as a writable folder (codex
-C <tmp> --add-dir <checkout>): the checkout's own .codex/config.toml - which the agent can edit, and whose MCP servers
codex starts outside the sandbox (measured) - is never read; the studio MCP server is given on the command line, started
from the sealed engine."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request',help='Natural-language scene/reel request')
    parser.add_argument('--project',help='Existing project relative to repository root')
    parser.add_argument('--dry-run',action='store_true',help='Print command without a model call')
    parser.add_argument('--approve-for-me',action='store_true',help='Use native automatic review for sandbox-boundary requests; does not bypass approval')
    parser.add_argument('--device',choices=['GPU','CPU'],default='GPU',help='Render device for studio render jobs (Metal GPU is ~11x faster outside the Codex sandbox)')
    parser.add_argument('--low-load',action='store_true',help='Keep the computer usable: CPU renders, one worker, start with one small frame')
    parser.add_argument('--effort',choices=['low','medium','high','xhigh'],default='medium',help="Astra's own reasoning effort (orchestration); subagents get theirs per task (references/blender_freedom.md)")
    parser.add_argument('--image',action='append',default=[],help='Image to attach to the request (reference photo, drawing); repeatable. Must be inside projects/')
    parser.add_argument('--log-dir',help='Write the run record here: events.jsonl (codex --json), stderr.log and run.json (start, end, exit code, thread id, arguments) - what scripts/astra_run_metrics.py and scripts/run_status.py read')
    parser.add_argument('--delegate',metavar='USER_WORDS',help="The user's own words handing this run's decisions to Astra: recorded on --project now, or on the project this run creates (project init); no decision-ladder sheets")
    args=parser.parse_args()
    engine=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(engine))
    from studio.seal import launch_argv, running_sealed
    if not running_sealed() and not args.dry_run:   # a dry run only prints the command, here with this engine
        from studio.common import StudioError
        try:how=' '.join(launch_argv('reel_agent','...'))
        except StudioError as error:how=f'{error.message} ({error.recovery})'
        sys.stderr.write('reel_agent runs from the sealed engine only (studio/seal.py): '+how+'\n')
        return 2
    root=Path(os.environ.get('STUDIO_DATA_ROOT') or engine).resolve()   # the checkout the run works in (its data and its files)
    device='CPU' if args.low_load else args.device
    codex=shutil.which('codex')
    if not codex:parser.error('codex not found; install and authenticate native Codex first')
    context=''
    if args.project:
        path=(root/args.project).resolve()
        if not path.is_relative_to(root/'projects'):parser.error('--project must be inside projects/')
        if not path.is_dir():parser.error('project does not exist')
        context=f'\nExisting project: {path}\n'
    from studio.taste_picks import prompt_block
    taste=prompt_block()
    # The plan first (2026-10-17): a story drawn as options, picked by the user; the user spends three moments - the request,
    # the story pick, the clay reel - and the rest is decided here and recorded (references/story.md)
    planning=("PLANNING - the user's time is three moments: their request; picking one of the story directions; watching the "
              "clay reel and naming shots to redo. 1) Facts first, then pitches (`story pitch-brief`: your own 5-8 lenses from the "
              "facts with `pitch-lenses`, 2-4 pitches per lens each in its own pass with `pitch-add`, `pitch-group`, then "
              "`pitch-judge-pack` for 3 blind judges and `pitch-judged`), then `story directions` (the best pitches of three groups) - "
              "write each from its pitch, not a safe average: first 4-6 free sentences, only then the story body; `story add`; judge "
              "them against the references (`story judge-pack`, `story judged`, a loser rewritten once), `story sheet`, and stop: show the page and ask for a number and a line "
              "of why (`story pick` in their words; a mix they ask for is `--body`). The story pick is never delegated, even in a "
              "delegated run: wait for the user's answer (their delegation words are refused). This run has no user at hand: no command takes "
              "the user's words here (USER_WORDS_UNATTENDED) - write what they are to decide in REPORT.md and stop. 2) The script's lines name their story beat; "
              "shots follow the beats; the shot list's flow warnings (ESTABLISH_MISSING, SAME_PICTURE, OVERFILL_SHARE, "
              "STATIC_SHARE_LOW, PACE_SLOW, CLOSE_RUN, ROLE_RUN) are read before pictures. 3) Each pictured moment is drawn as "
              "variants (`concept brief --variants 3`, one picture per variant line, `concept add --variant`), then `concept check` "
              "(one picture drawn twice: redraw it once with --redraw-of, then pick); in a delegated run "
              "pick with --agent-note by the shot's story beat, the flow warnings and the user's taste below. 4) After the builds, "
              "`reel clay` and stop: show review/clay/index.html; each shot they name is `reel redo start` with their words, "
              "options drawn, `reel redo build`, their pick `reel redo pick`. Rules are said by the checks that read your "
              "output (SKILL lists them); write freely first, then fit the format.\n"
              +(taste+'\n' if taste else ''))
    prompt=(f"Your workspace is {root} (writable): cd there first; every path below is relative to it. "
            "Use .agents/skills/reel-production/SKILL.md. You are the production orchestrator. "
            "Read project state, make the requested output, inspect representative renders, "
            "and finish with the requested artifacts and an evidence-based report. "
            "For a still-preview request, return stills; produce a playable artifact only when video is requested. "
            "Use available subagents for bounded independent tasks; keep one scene writer. "
            "Ask the user only for paid calls, approvals in their words, frozen-code edits, a large deviation on a real subject and "
            "anything outside the request; decide everything else yourself and record it (decide note --topic --choice --why). "
            "Build what shows concretely (SKILL #8): list the reference's detail in four tiers first, model every item or put it in "
            "the simplification table with its on-screen size; passing acceptance is the floor - while previews are free, fix the "
            "largest remaining difference. "
            "Preserve other projects. Technical tests alone don't establish visual quality.\n"
            f"Render device: STUDIO_RENDER_DEVICE={device} (prefix render commands with it; confirm renderer_actual.json). It applies to "
            "render jobs only: workbench previews, id passes and the build's frame probe always use the GPU briefly - that is not a "
            "conflict with a CPU limit, use them. "
            "Before authoring, run `route plan` and decide camera.energy per shot. Never call paid generation "
            "(generate clip, asset image3d) unless the user approved that shot and cost in this conversation and `route approve` "
            "recorded their own words; never write approvals yourself. Generated frames must contain no text. "
            "A hybrid take fills the whole frame from the look render; hold structure by declaring the parts the narration "
            "names (key_parts, screen.subject, labels) - the prompt states their measured order and auto_keep restores only "
            "what a take loses - never by keeping the whole subject as Blender pixels. "
            "Before the shot list, picture the whole minute (concept add-beat, one picture per beat, with surroundings and "
            "light) and settle the world (references/world.md): its kind, light, and each element's shape and look owner - "
            "the core subject in Blender, the rest clay stand-ins the model fills from those pictures; what places the composition "
            "is built even as a blockout - the world's shell (ground to the horizon, sky), the one or two set pieces at their "
            "real size (blockout + scene.stand_ins) and a person for scale where a new place opens (references/world.md 4b). "
            "After the shot list, draw each shot's first and last picture (concept add --frame start|end, a fitting beat picture "
            "reused with --from-beat; section shots on the booster in flight, never on a void), read them (frames measure, look at "
            "frames_check.png, correct with frames mark incl. --tilt-deg and --shows), frames apply, build, storyboard: the camera reproduces the pictures as drawn (lean by roll, what they show as must-see; at most 2 departures per picture, never the viewpoint, without the user's words; "
            "every thing a picture shows read as objects and compared, FRAME_LAYOUT_MISS; an eye records frames review beside the sheet; "
            "a picture the scene cannot be is painted over the built frame - concept add --derived-from, frames derive) - "
            "composition only, never shape; the camera moves while the subject moves, each view chosen near its picture so the subject is not hidden and the must-see parts show; list the flame stand-ins, sea and deck in guards.clearance_ids (references/frames.md). "
            "Otherwise choose each shot's picture - camera.move.focus height_share/centre and the move's angle, from its concept, "
            "the user's taste, its role's range and the reel's size flow, never one median for all - and let the build "
            "solve the camera; screen.subject is the whole explained structure, its thinnest part a screen.details min_px. "
            "Every named subject gets a subject spec before modelling (subject init/lint); you choose parts, relations and claims, "
            "numbers that make a claim come from sources, subject trace/fit or from-dxf, never typed guesses; a real subject is matched "
            "to licensed photos from two sides before its look (reference collect through studio_run when none was given, "
            "reference_fit_camera, photo_views; references/subject_fidelity.md 1b); appearance shape is "
            "free (spec ops, any bpy) and illustrative. Iterate with the workbench (workbench start/call/commit; exec explores, "
            "cannot be committed); each rebuild names one --diagnosis, regressions auto-revert, while fidelity fails stop after 3 "
            "non-improving builds and ask the user. Explore freely (variant_save, workbench compare) and commit one chosen "
            "variant with --why; declare deliberate shape changes in spec deviations instead of loosening checks. "
            "Every value of a shot is reachable by the user's words: use a word-op, else set/add/remove on its path "
            "(storyboard revise --ops, fill revise --ops, workbench set_shot_value); never say a value cannot be changed. "
            "Blender is yours within structural lines (references/blender_freedom.md): any bpy in an author script, new shapes "
            "or joint laws as contrib entries, looks as shot.render grade/compositor/engine_settings; read frame_probe images "
            "after every build. Blender cannot start in your sandbox: run every studio command that needs it (shot build/revise, "
            "storyboard, render, graphics, generate inputs/control, camera fit) through the MCP tool studio_run {args: [...]}, and "
            "iterate with the studio_workbench tools. When you spawn a subagent, pass model and reasoning_effort explicitly "
            "(agent type files are not applied): high for new shapes or motion laws (contrib), scene writing, failure diagnosis "
            "and visual review; medium for specs and workbench iteration; low for inventories and reading.\n"
            "Build toward a picture, judge by what shows, keep going while budget remains (SKILL Phase D/2/3): before 3D, draw "
            "each moment's variants with your built-in image tool (`concept brief --variants 3`; no text in them; never send collected "
            "reference photos), `concept add --variant`, `concept check` (CONCEPTS_ONE_IDEA), `concept sheet`, `concept pick` (the user's words; in a "
            "delegated run your --agent-note). Storyboard "
            "each shot with --hero (the frame that explains); per shot 2-3 camera takes (as intended, then two other feel recipes from "
            "library/camera_recipes.json - dig_in, pass_by, against, look_up, ride_along - set as the take's recipe; TAKES_ONE_FEEL, "
            "TAKE_RECIPE_DIFFERS; where the script says what is inside the shot list plans direction.reveal and a camera that enters - "
            "section_push/dive_through, never a slide between two outside pictures, REVEAL_UNPLANNED, REVEAL_NOT_SHOWN; a line is not a "
            "shot: neighbouring lines that continue are one camera with camera.move.then, cuts measured JOIN_REVERSED/JOIN_STOPPED; words fill about 90 % "
            "of the reel as in the user's reference - one whole thought per line (SCRIPT_CHOPPY), the script sized to brief length x speech share "
            "(SCRIPT_DENSE), the voice going on over an opening too, never cut short (OPENING_SHORT, line_at_s)) watched as clay clips with their dynamics and feel lines against the user's reference profile (storyboard variants, storyboard "
            "animatic), the user picks before a picture is painted over the camera; `storyboard board` for the whole reel; report it with the composition "
            "brief it writes (brief.html: each shot's first/last frame, size, angle, camera move, join, BRIEF_PLAN_DIFFERS). Declare what the narration "
            "explains in shot.screen.details with a visible thickness in pixels (DETAIL_NOT_SHOWN: being in the scene is not being shown; too thin "
            "after a cut face and a closer camera: display_scale {factor, reason} plus a label saying so - never silently thicker); give every structure a "
            "world past the frame (FRAME_MODEL_EDGE); reference_critique compares with the concept by default. Give each shot-list "
            "shot a role, look at `pattern show --role` pictures first and record what you carry and what you leave on purpose "
            "(each shot its own `screen.pattern_carried`/`pattern_departs`, delegated too; `screen.subject` names the focus they measure) - "
            "patterns are material to judge with, not targets; the user's taste (`pattern show` `taste`) wins where they disagree. Paint "
            "built parts with catalog kinds or a registered texture (`MATERIAL_NOT_CATALOG`); people and mood are the generative model's, "
            "structure stays Blender's. After each build, "
            "a high-effort reviewer answers references/visual_review.md; while `appearance.remaining` > 0 and it fails, change "
            "the one thing it names and rebuild - checks passing is not the end. A rough cut watched whole is the `cut` layer "
            "before finals and generation. Remote renders: `render submit --remote` through studio_run, batched in one go.\n"
            +planning+context+'\nUser request:\n'+args.request)
    images=[]
    for image in args.image:
        path=(root/image).resolve() if not Path(image).is_absolute() else Path(image).resolve()
        if not path.is_relative_to(root/'projects') or not path.is_file():parser.error(f'--image {image}: put the image inside projects/ (references stay local)')
        images.append(str(path))
    if images:
        prompt+=('\n\nAttached reference images (local only: never commit them or send them to a generation model; numbers read off a photo '
                 'are estimates - mark them so, and prefer sourced specifications): '+', '.join(str(Path(i).relative_to(root)) for i in images))
    import tempfile
    launch=Path(tempfile.mkdtemp(prefix='astra-run-'))   # where codex starts: no project config but the one given here
    (launch/'AGENTS.md').write_text(f'The workspace is {root}: cd there first. Its AGENTS.md is the entry point.\n')
    mcp=launch_argv('mcp',engine=engine)   # this engine's server: the sealed one (a dry run from a checkout prints its own)
    toml=lambda v: json.dumps(v,ensure_ascii=False)   # noqa: E731 - TOML takes JSON strings and arrays of them
    cmd=[codex,'exec','-C',str(launch),'--add-dir',str(root),'--skip-git-repo-check','-m','gpt-6-astra',
         '-c',f'model_reasoning_effort="{args.effort}"','-c','agents.enabled=true','-c','agents.max_concurrent_threads_per_session=3',
         '-c','agents.default_subagent_model="gpt-6.1-sol"','-c','agents.default_subagent_reasoning_effort="high"',
         '-c',f'mcp_servers.studio_workbench.command={toml(mcp[0])}','-c',f'mcp_servers.studio_workbench.args={toml(mcp[1:])}',
         '-c',f'mcp_servers.studio_workbench.cwd={toml(str(root))}','-c','mcp_servers.studio_workbench.tool_timeout_sec=3600',
         '-c','mcp_servers.studio_workbench.startup_timeout_sec=20','-c','mcp_servers.studio_workbench.default_tools_approval_mode="approve"',
         # codex starts an MCP server with only the variables named here (measured 2026-10-18: STUDIO_RENDER_DEVICE never reached it)
         '-c','mcp_servers.studio_workbench.env.STUDIO_UNATTENDED="1"','-c',f'mcp_servers.studio_workbench.env.STUDIO_RENDER_DEVICE={toml(device)}',
         '-c','mcp_servers.studio_workbench.enabled_tools='+toml(["workbench_start","workbench_call","workbench_compare","workbench_commit","workbench_stop","studio_run"]),
         '--json']
    for image in images:cmd.extend(['-i',image])
    # the sandbox always (the user-wide config may say danger-full-access); --approve-for-me only adds the automatic reviewer
    cmd.extend(['--sandbox','workspace-write',*(['--approve-for-me'] if args.approve_for_me else [])])
    cmd.append(prompt)
    env={**os.environ,'STUDIO_RENDER_DEVICE':device,'STUDIO_UNATTENDED':'1'}   # no user at hand: no command takes their words
    if args.delegate:
        from studio.generative.review import check_user_words
        words=check_user_words(args.delegate,'delegation')
        if args.project:
            if not args.dry_run:
                from studio.decisions import delegate
                delegate(root/args.project,words)
        else:   # the project does not exist yet: project init records it (studio/decisions.claim_pending_delegation)
            pending=root/'.studio'/'delegation.json'
            if not args.dry_run:
                pending.parent.mkdir(exist_ok=True);pending.write_text(json.dumps({'user_words':words},ensure_ascii=False))
        prompt_note=('\n\nThis run is delegated by the user ("'+words+'"): open no decision-ladder sheets; '
                     + ('the delegation is recorded on the project.' if args.project else 'project init records it on the project you create.'))
        cmd[-1]=cmd[-1]+prompt_note
    if args.dry_run:print(json.dumps({'model':'gpt-6-astra','env':{'STUDIO_RENDER_DEVICE':device,'STUDIO_UNATTENDED':'1'},'command':cmd},ensure_ascii=False,indent=2));return 0
    if not args.log_dir:
        return subprocess.run(cmd,cwd=launch,stdin=subprocess.DEVNULL,env=env).returncode
    import time
    log=Path(args.log_dir).resolve();log.mkdir(parents=True,exist_ok=True)
    record={'started_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'model':'gpt-6-astra','effort':args.effort,'project':args.project,
            'images':images,'delegate':args.delegate,'request':args.request}
    (log/'run.json').write_text(json.dumps(record,ensure_ascii=False,indent=1))
    with open(log/'events.jsonl','w') as out,open(log/'stderr.log','w') as err:
        code=subprocess.run(cmd,cwd=launch,stdin=subprocess.DEVNULL,env=env,stdout=out,stderr=err).returncode
    first=(log/'events.jsonl').read_text().split('\n',1)[0]
    try:thread=json.loads(first).get('thread_id')
    except ValueError:thread=None
    record.update({'finished_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'exit_code':code,'thread_id':thread})
    (log/'run.json').write_text(json.dumps(record,ensure_ascii=False,indent=1))
    return code

if __name__=='__main__':sys.exit(main())
