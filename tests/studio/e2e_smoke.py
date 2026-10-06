"""End to end on a fresh copy of the tracked example (one small Workbench render, no paid call): from-example -> build
the Blender shot -> layout render through the job worker -> the generated mood shot (the user's approval on its
review sheet, then a fake fal response) -> rough edit with both shots -> technical QA.
Run: .venv/bin/python tests/studio/e2e_smoke.py
"""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from studio.blender import build_shot
from studio.edit import build_edit
from studio.generative import clip as clipmod
from studio.generative.review import build_review
from studio.jobs import job_status, submit_render
from studio.project import from_example
from studio.qa import collect_qa
from studio.routing import approve


def fake_paid(endpoint, arguments, dest, **kwargs):   # what fal would return: a 4 s clip
    Path(dest).mkdir(parents=True, exist_ok=True)
    video = Path(dest) / 'out.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=s=1080x1920:r=24:d=4', '-pix_fmt', 'yuv420p', str(video)], check=True)
    return {'request_id': 'e2e', 'estimated_usd': 0.8, 'files': [{'path': str(video)}]}


checks = []
with tempfile.TemporaryDirectory(prefix='e2e-smoke-') as root:
    project = Path(root) / 'e2e'
    made = from_example('e2e_minimal', project)
    assert made['missing_inputs'] == [] and list(project.glob('runs/*/run.json')), made
    checks.append('example_copied_ready')

    built = build_shot(project, 'a', ROOT / 'examples/e2e_minimal/author_e2e.py')
    assert (project / 'shots/a/versions' / built['scene_version'] / 'authored.blend').is_file()
    checks.append('blender_shot_built')

    job = submit_render(project, 'a', built['scene_version'], 'layout')
    deadline = time.monotonic() + 600
    while (status := job_status(project, job['job_id']))['status'] in ('queued', 'running') and time.monotonic() < deadline:
        time.sleep(1)
    assert status['status'] == 'complete', status
    rendered = json.loads((Path(status['output_dir']) / 'render.json').read_text())
    assert rendered['renderer_actual']['engine'] == 'BLENDER_WORKBENCH' and rendered['full_sequence'], rendered['renderer_actual']
    checks.append('layout_render_workbench')

    review = build_review(project, ['g'])
    approve(project, 'g', '분위기 컷 생성해도 좋아', None, review['review_id'])     # the user's words on the sheet they saw
    with patch.object(clipmod, 'paid_call', side_effect=fake_paid):
        generated = clipmod.generate_clip(project, 'g', allow_paid=True, max_usd=1)
    assert generated['ai_generated'] and generated['profile'] == 'final', generated
    checks.append('generated_mood_shot')

    edit = build_edit(project, 'rough')
    assert (project / edit['output_path']).is_file() and edit['ai_generated_shots'] == ['g'], edit
    checks.append('rough_edit_both_shots')

    qa = collect_qa(project, edit['candidate_id'])
    assert qa['candidate_hash'], qa
    checks.append('qa_collected')

print('STUDIO_E2E_SMOKE ' + json.dumps({'ok': True, 'checks': checks, 'candidate': edit['candidate_id'],
                                        'frames': rendered['frame_count'], 'technical_pass': qa.get('technical_pass')}))
