"""Pictures from the image tool: Codex's image generation (the subscription - free and local), one picture per call.

One runner for every picture v2 makes (a cut's frames, a place probe, a calibration image): v1's painter
(studio.picture3d.paint_into - one painting at a time on the machine, the image the call's --json events name, the
outside-model ask where no model may start) with what v2 needs around it:
- the prompt goes before the inputs (`codex exec "<prompt>" -i a.png -i b.png`: -i takes every argument after it as an
  image - found in stage 1, 2026-10-26; paint_into builds the command that way);
- each picture is kept with what made it, beside it (<name>.json): the prompt, the inputs and their hashes, every try
  with its seconds and how it ended; the same request with the same inputs is not made twice;
- a failed call is tried again (RETRIES); what its words say decides first (STOPS): a quota or a pay prompt stops at
  once and is never retried - spending or waiting is the user's to decide - and a refusal is tried again only REFUSALS
  times, then stops;
- each call is a `model` row under the command that asked (studio timings).
"""
from __future__ import annotations

from pathlib import Path
import re
import time

from studio.common import StudioError, file_hash, now, read_json, stable_hash, write_json

RETRIES = 2      # a failure that is neither a stop nor a refusal is tried this many more times
REFUSALS = 2     # refusals before the request stops (a third would be asking the same thing again)
# What a failed call's words mean, read in order: the first row whose pattern matches the call's tail decides. A new sign
# is a row. stop: never tried again (the user decides); a refusal row is tried up to REFUSALS times.
STOPS = (
    {'sign': 'quota', 'code': 'IMAGE_QUOTA', 'stop': True,
     'pattern': r"usage limit|rate[ -]?limit|quota|too many requests|\b429\b|limit (has been )?(reached|exceeded)|hit your .*limit",
     'means': 'the image tool\'s allowance is used up for now'},
    {'sign': 'payment', 'code': 'IMAGE_PAYMENT', 'stop': True,
     'pattern': r"upgrade (your|to)|purchase|billing|(buy|add) (more )?credits|payment|pay to continue|subscribe to",
     'means': 'the image tool asks for payment'},
    {'sign': 'refused', 'code': 'IMAGE_REFUSED', 'stop': False,
     'pattern': r"\bI (can't|cannot|won't|am unable to|'m unable to|'m not able to) (help|create|generate|make|draw)|content polic|"
                r"violates? .*polic|not able to (create|generate) (that|this)",
     'means': 'the image tool refused the request'},
)


def sign_of(tail):
    """The STOPS row a failed call's words match, or None."""
    for row in STOPS:
        if re.search(row['pattern'], tail or '', flags=re.IGNORECASE):
            return row
    return None


def _inputs(inputs):
    return [{'path': str(p), 'sha256': file_hash(p)} for p in (Path(i) for i in inputs)]


def record_path(target):
    return Path(target).with_suffix('.json')


def cached(target, prompt, inputs):
    """The record of `target` when it was made from exactly this prompt and these inputs and is unchanged since."""
    target, record = Path(target), record_path(target)
    if not (target.is_file() and record.is_file()):
        return None
    row = read_json(record)
    if row.get('ok') and row.get('prompt_sha256') == stable_hash(prompt) and row.get('inputs') == _inputs(inputs) \
            and row.get('image_sha256') == file_hash(target):
        return row
    return None


def make(target, prompt, inputs=(), runner=None, retries=RETRIES, force=False, label=None):
    """One picture at `target` from `prompt` and the `inputs` (a place reference, the picture an edit starts from).
    runner(cmd, cwd, timeout) stands in for codex in tests. -> the record (<target>.json) with cached: bool."""
    from studio.picture3d import paint_into
    from studio.timings import timed
    target = Path(target)
    inputs = [Path(i) for i in inputs]
    missing = [str(i) for i in inputs if not i.is_file()]
    if missing:
        raise StudioError('INPUT_INVALID', f'{target.name}: inputs not found: {missing}')
    if not force:
        done = cached(target, prompt, inputs)
        if done is not None:
            return {**done, 'cached': True}
    target.parent.mkdir(parents=True, exist_ok=True)
    row = {'schema_version': 1, 'target': target.name, 'prompt': prompt, 'prompt_sha256': stable_hash(prompt), 'inputs': _inputs(inputs),
           'tool': 'codex image generation (subscription)', 'attempts': [], 'ok': False}
    refusals = 0
    for attempt in range(1, retries + 2):
        started = time.monotonic()
        with timed('model', f'image {label or target.parent.name + "/" + target.name}'):
            made, how, tail = paint_into(target, inputs, prompt, runner, label or target.name)
        seconds = round(time.monotonic() - started, 2)
        if made is not None:
            row['attempts'].append({'n': attempt, 'seconds': seconds, 'ok': True, 'found_by': how, 'at': now()})
            row.update({'ok': True, 'image_sha256': file_hash(target), 'made_at': now(), 'seconds': seconds})
            write_json(record_path(target), row)
            return {**row, 'cached': False}
        sign = sign_of(tail)
        row['attempts'].append({'n': attempt, 'seconds': seconds, 'ok': False, 'sign': sign['sign'] if sign else None,
                                'tail': (tail or '')[-600:], 'at': now()})
        write_json(record_path(target), row)
        if sign and sign['stop']:
            raise StudioError(sign['code'], f"{target.name}: {sign['means']} - stopped, not tried again: {(tail or '')[-300:]}",
                              recovery='a quota or a payment is the user\'s to decide: report it and wait for their words')
        if sign:
            refusals += 1
            if refusals >= REFUSALS:
                raise StudioError(sign['code'], f"{target.name}: refused {refusals} times - stopped: {(tail or '')[-300:]}",
                                  recovery='rewrite what the request asks (not the same words again), or report it')
    raise StudioError('IMAGE_FAILED', f"{target.name}: no picture after {retries + 1} tries: {(row['attempts'][-1].get('tail') or '')[-300:]}",
                      retryable=True, recovery=f'see {record_path(target)}; try again later')


def register_commands(subparsers):
    parser = subparsers.add_parser('images', help='Pictures from the image tool (codex, free): one runner, each kept with what made it')
    commands = parser.add_subparsers(dest='images_command', required=True)
    p = commands.add_parser('make', help='one picture from a prompt (and inputs: a place reference, the picture an edit starts from)')
    p.add_argument('--project', help='the project the call is timed under (studio timings)')
    p.add_argument('--out', required=True); p.add_argument('--prompt', required=True, help='the prompt, or a .txt file holding it')
    p.add_argument('--input', action='append', default=[]); p.add_argument('--force', action='store_true')

    def handler(a):
        prompt = Path(a.prompt).read_text(encoding='utf-8').strip() if a.prompt.endswith('.txt') and Path(a.prompt).is_file() else a.prompt
        got = make(a.out, prompt, a.input, force=a.force)
        return {**got, 'artifacts': [a.out, str(record_path(a.out))]}
    p.set_defaults(handler=handler)
    return commands
