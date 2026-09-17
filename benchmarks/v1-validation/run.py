#!/usr/bin/env python3
"""Schema-only, preflight and resumable orchestration for V1 validation experiments."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import experiment
import record


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def planned_rows(manifest, selected_tasks=None, selected_arms=None, repetitions=None, seed=None):
    rows = experiment.plan(manifest, selected_tasks, selected_arms, repetitions, seed)
    return [
        {
            'ordinal': index,
            'task': task['id'],
            'arm': arm['name'],
            'repetition': repetition,
            'run_id': experiment.safe_run_id(f"{index:04d}-{task['id']}-{arm['name']}-r{repetition}"),
        }
        for index, (task, arm, repetition) in enumerate(rows, 1)
    ], rows


def initialize_output(output, manifest, plan_rows):
    if output.exists() and any(output.iterdir()):
        raise ValueError('output directory is not empty; use --resume only for the exact same experiment')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'runs').mkdir(exist_ok=True)
    snapshot = {'manifest': manifest, 'manifest_sha256': digest(manifest)}
    write_json(output / 'manifest.snapshot.json', snapshot)
    write_json(output / 'plan.json', plan_rows)


def verify_resume(output, manifest, plan_rows):
    if not output.is_dir():
        raise ValueError('--resume requires an existing output directory')
    snapshot_path = output / 'manifest.snapshot.json'
    plan_path = output / 'plan.json'
    if not snapshot_path.is_file() or not plan_path.is_file():
        raise ValueError('resume output lacks manifest snapshot or plan')
    snapshot = read_json(snapshot_path)
    expected_hash = snapshot.get('manifest_sha256')
    if expected_hash != digest(manifest) or snapshot.get('manifest') != manifest:
        raise ValueError('manifest differs from the original experiment; refuse resume')
    if read_json(plan_path) != plan_rows:
        raise ValueError('run plan differs from the original experiment; refuse resume')


def archive_interrupted(output, run_id):
    source = output / 'runs' / run_id
    if not source.exists():
        return None
    folder = output / 'interrupted'
    folder.mkdir(exist_ok=True)
    target = folder / f'{run_id}-{int(time.time())}'
    suffix = 0
    while target.exists():
        suffix += 1
        target = folder / f'{run_id}-{int(time.time())}-{suffix}'
    shutil.move(str(source), str(target))
    return target


def execute(manifest, repos, router, worker, output, plan_rows, plan_objects, *, resume=False, keep_raw=False):
    records_path = output / 'runs.jsonl'
    existing = record.load(records_path) if records_path.is_file() else []
    completed = {row['run_id']: row for row in existing}
    all_rows = list(existing)

    for plan_row, (task, arm, repetition) in zip(plan_rows, plan_objects):
        run_id = plan_row['run_id']
        if run_id in completed:
            continue
        if resume:
            archive_interrupted(output, run_id)
        value = experiment.run_one(
            manifest, repos, router, worker, task, arm, repetition,
            plan_row['ordinal'], output, keep_raw=keep_raw,
        )
        if value['run_id'] != run_id:
            raise ValueError('runner produced a run id different from the immutable plan')
        all_rows.append(value)
        completed[run_id] = value
        write_json(output / 'summary.json', record.summarize(all_rows))
    return all_rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--output', type=Path, default=Path('v1-validation-results'))
    parser.add_argument('--schema-only', action='store_true', help='Validate manifest shape only; no git, Codex, network or writes')
    parser.add_argument('--preflight', action='store_true', help='Resolve repos/configs and plan without model calls or output writes')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--task', action='append')
    parser.add_argument('--arm', action='append')
    parser.add_argument('--repetitions', type=int)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--keep-raw', action='store_true')
    args = parser.parse_args(argv)

    manifest = experiment.validate_manifest(read_json(args.manifest))
    plan_rows, plan_objects = planned_rows(manifest, args.task, args.arm, args.repetitions, args.seed)
    if args.schema_only:
        print(json.dumps({'ok': True, 'model_calls': 0, 'filesystem_writes': 0, 'runs': len(plan_rows)}))
        return 0

    repos, router, worker = experiment.preflight(manifest)
    if args.preflight:
        print(json.dumps({
            'ok': True,
            'model_calls': 0,
            'filesystem_writes': 0,
            'runs': len(plan_rows),
            'repositories': {key: value['commit'] for key, value in repos.items()},
        }, ensure_ascii=False))
        return 0

    output = args.output.resolve()
    if args.resume:
        verify_resume(output, manifest, plan_rows)
    else:
        initialize_output(output, manifest, plan_rows)
    rows = execute(
        manifest, repos, router, worker, output, plan_rows, plan_objects,
        resume=args.resume, keep_raw=args.keep_raw,
    )
    summary = record.summarize(rows)
    write_json(output / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'validation run: {exc}', file=sys.stderr)
        raise SystemExit(2)
