#!/usr/bin/env python3
"""Reproducible Context Gateway dogfood and causal A/B runner.

The activation gate runs before Codex. A bypass arm does not attach the skill or MCP,
so small-task overhead can be measured honestly. Jev and worker toggles are explicit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRIPTS = ROOT / 'skills' / 'io-delegation' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))

import context_activation
import context_mcp
import context_telemetry
import decision_router
import io_delegate
import record as run_record
from worker_runtime import run_process

SENSITIVE = re.compile(r'(^|/)(\.env($|\.)|\.ssh/|\.aws/|\.gnupg/|id_rsa|id_ed25519|credentials|secrets?\.)', re.I)
FAMILIES = {
    'large-understanding', 'multi-file-factual', 'cross-file-behavior',
    'principal', 'small-control', 'targeted', 'worker-eligible',
}
VALIDATOR_TAIL_BYTES = 4_000


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def git(repo, *args, timeout=120):
    repo = Path(repo).resolve()
    command = ['git', '-c', f'safe.directory={repo}', '-c', 'core.quotepath=false', '-C', str(repo), *args]
    cp = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    if cp.returncode:
        raise RuntimeError('git failed: ' + (cp.stderr or cp.stdout)[-1200:])
    return cp.stdout


def format_value(value, context):
    text = str(value)
    for key, item in context.items():
        text = text.replace('{' + key + '}', str(item))
    return text


def _validator_tail(raw):
    if not raw:
        return ''
    if isinstance(raw, str):
        text = raw
        raw_bytes = raw.encode('utf-8', errors='replace')
    else:
        raw_bytes = bytes(raw)
        text = raw_bytes.decode('utf-8', errors='replace')
    # Keep only a bounded diagnostic tail. Secrets are removed before persistence.
    tail = text[-VALIDATOR_TAIL_BYTES:]
    return run_record.redact(tail)


def _validator_result(cp):
    row = {'ok': cp.returncode == 0, 'exit_code': cp.returncode}
    if cp.returncode:
        row['stdout_sha256'] = hashlib.sha256(cp.stdout or b'').hexdigest()
        row['stderr_sha256'] = hashlib.sha256(cp.stderr or b'').hexdigest()
        stdout_tail = _validator_tail(cp.stdout)
        stderr_tail = _validator_tail(cp.stderr)
        if stdout_tail:
            row['stdout_tail'] = stdout_tail
        if stderr_tail:
            row['stderr_tail'] = stderr_tail
    return row


def run_checks(worktree, commands, context, timeout=180):
    rows = []
    for entry in commands:
        if isinstance(entry, str):
            text = format_value(entry, context)
            argv = ['cmd.exe', '/d', '/c', text] if os.name == 'nt' else ['/bin/sh', '-c', text]
        elif isinstance(entry, list) and entry:
            argv = [format_value(item, context) for item in entry]
        else:
            raise ValueError('validator commands must be strings or non-empty argv arrays')
        try:
            cp = subprocess.run(argv, cwd=worktree, capture_output=True, timeout=timeout)
            rows.append(_validator_result(cp))
        except subprocess.TimeoutExpired as exc:
            row = {'ok': False, 'timeout': True}
            stdout_tail = _validator_tail(exc.output)
            stderr_tail = _validator_tail(exc.stderr)
            if stdout_tail:
                row['stdout_tail'] = stdout_tail
            if stderr_tail:
                row['stderr_tail'] = stderr_tail
            rows.append(row)
        except OSError as exc:
            rows.append({'ok': False, 'error': type(exc).__name__})
    return bool(rows) and all(row['ok'] for row in rows), rows


def validate_manifest(data, path=None):
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('manifest version 1 required')
    repos = data.get('repositories')
    tasks = data.get('tasks')
    arms = data.get('arms')
    if not isinstance(repos, dict) or not repos:
        raise ValueError('repositories object required')
    if not isinstance(tasks, list) or not tasks:
        raise ValueError('tasks array required')
    if not isinstance(arms, list) or not arms:
        raise ValueError('arms array required')
    task_ids = [task.get('id') for task in tasks]
    arm_names = [arm.get('name') for arm in arms]
    if len(task_ids) != len(set(task_ids)) or any(not isinstance(x, str) or not x for x in task_ids):
        raise ValueError('task ids must be unique non-empty strings')
    if len(arm_names) != len(set(arm_names)) or any(not isinstance(x, str) or not x for x in arm_names):
        raise ValueError('arm names must be unique non-empty strings')
    repetitions = data.get('repetitions', 1)
    if type(repetitions) is not int or repetitions < 1:
        raise ValueError('repetitions must be >= 1')
    for repo_id, repo in repos.items():
        if not isinstance(repo_id, str) or not isinstance(repo, dict):
            raise ValueError('invalid repository entry')
        if not isinstance(repo.get('path'), str) or not isinstance(repo.get('commit'), str):
            raise ValueError('repository path and commit required')
    for task in tasks:
        if task.get('repo') not in repos:
            raise ValueError(f"unknown repo for task {task.get('id')}")
        if task.get('family') not in FAMILIES:
            raise ValueError(f"unknown family for task {task.get('id')}")
        if not isinstance(task.get('prompt'), str) or not task['prompt'].strip():
            raise ValueError('task prompt required')
        if not isinstance(task.get('validator'), list) or not task['validator']:
            raise ValueError('each task needs validator commands')
        scope = list(task.get('allow_files', [])) + list(task.get('allow_prefixes', []))
        if not scope:
            raise ValueError('each task needs explicit allowed scope')
        if any(SENSITIVE.search(str(item).replace('\\', '/')) for item in scope):
            raise ValueError('sensitive path in task scope')
        if task.get('expected_route') is not None and task['expected_route'] not in (
            'deterministic', 'targeted_read', 'bulk_read', 'principal', 'bypass'
        ):
            raise ValueError('invalid expected_route')
    for arm in arms:
        for flag in ('gateway', 'jev', 'worker'):
            if arm.get(flag, False) not in (True, False):
                raise ValueError(f'{flag} must be boolean')
        if arm.get('activation', 'auto') not in ('auto', 'always', 'off'):
            raise ValueError('invalid arm activation')
        if arm.get('jev') and not arm.get('gateway'):
            raise ValueError('Jev requires gateway')
        if arm.get('worker') and not arm.get('gateway'):
            raise ValueError('worker requires gateway')
    return data


def resolve_repositories(manifest):
    resolved = {}
    for repo_id, row in manifest['repositories'].items():
        path = Path(row['path']).expanduser().resolve(strict=True)
        commit = git(path, 'rev-parse', row['commit'] + '^{commit}').strip()
        resolved[repo_id] = {'path': path, 'commit': commit}
    return resolved


def config_path(value, label):
    if not value:
        return None
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file() or path.is_symlink():
        raise ValueError(label + ' must be a regular non-symlink file')
    return path


def preflight(manifest):
    validate_manifest(manifest)
    if not shutil.which('git') or not shutil.which('codex'):
        raise ValueError('git and codex are required')
    repos = resolve_repositories(manifest)
    router = config_path(manifest.get('router_config'), 'router_config')
    worker = config_path(manifest.get('worker_config'), 'worker_config')
    if any(arm.get('jev') for arm in manifest['arms']):
        if not router:
            raise ValueError('Jev arm requires router_config')
        decision_router.load_config(str(router))
    if any(arm.get('worker') for arm in manifest['arms']):
        if not worker:
            raise ValueError('worker arm requires worker_config')
        io_delegate.load_config(str(worker))
    return repos, router, worker


def install_skill(worktree):
    destination = worktree / '.agents' / 'skills' / 'io-delegation'
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(ROOT / 'skills' / 'io-delegation', destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))


def activation_state(task, arm):
    return {
        'allow_files': task.get('allow_files', []),
        'allow_prefixes': task.get('allow_prefixes', []),
        'activation_mode': arm.get('activation', 'auto'),
        'activation_limits': arm.get('activation_limits', {}),
    }


def router_config_for_arm(arm, router):
    return router if arm.get('jev') else None


def worker_config_for_arm(arm, worker):
    return worker if arm.get('worker') else None


def safe_run_id(value):
    if not re.fullmatch(r'[A-Za-z0-9._-]+', value):
        raise ValueError('unsafe run id')
    return value


def read_query_metrics(audit):
    path = Path(audit) / 'context-events.jsonl'
    rows, malformed = context_telemetry.events(path)
    completed = [
        row for row in rows
        if row.get('schema') == 'io-context/v1'
        and row.get('event') == 'operation_completed'
    ]
    queries = [row for row in completed if row.get('operation') == 'query']
    last = queries[-1] if queries else None
    return {
        'malformed': malformed,
        'route': last.get('route') if last else None,
        'router_called': bool(sum(int(row.get('router_calls', 0) or 0) for row in queries)),
        'router_route': last.get('router_route') if last else None,
        'router_confidence': last.get('router_confidence') if last else None,
        'router_input_tokens': sum(
            int(row.get('router_input_tokens', 0) or 0) for row in queries
            if row.get('router_input_tokens') is not None
        ) if any(row.get('router_input_tokens') is not None for row in queries) else None,
        'router_output_tokens': sum(
            int(row.get('router_output_tokens', 0) or 0) for row in queries
            if row.get('router_output_tokens') is not None
        ) if any(row.get('router_output_tokens') is not None for row in queries) else None,
        'router_elapsed_ms': sum(
            int(row.get('router_elapsed_ms', 0) or 0) for row in queries
            if row.get('router_elapsed_ms') is not None
        ) if any(row.get('router_elapsed_ms') is not None for row in queries) else None,
        'query_calls': len(queries),
    }


def usage_object(summary):
    if summary is None:
        return None
    keys = ('input_tokens', 'output_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'reasoning_output_tokens')
    if not all(key in summary for key in ('input_tokens', 'output_tokens')):
        return None
    return {key: summary.get(key) for key in keys}


def run_conditions(principal, activation):
    return {
        'principal_process': 'cold',
        'context_cache': 'cold' if activation.get('decision') == 'enable' else 'disabled',
        'provider_cache': 'reported' if principal.get('cached_input_tokens') is not None else 'unreported',
        'worktree': 'fresh',
    }


def run_one(manifest, repos, router, worker, task, arm, repetition, ordinal, out_root, keep_raw=False):
    repo = repos[task['repo']]
    run_id = safe_run_id(f"{ordinal:04d}-{task['id']}-{arm['name']}-r{repetition}")
    run_dir = out_root / 'runs' / run_id
    if run_dir.exists():
        raise ValueError('run directory already exists: ' + run_id)
    run_dir.mkdir(parents=True)
    audit = run_dir / 'audit'
    audit.mkdir()
    worktree = run_dir / 'worktree'
    git(repo['path'], 'worktree', 'add', '--detach', str(worktree), repo['commit'])
    started_epoch = time.time()
    started = time.monotonic()
    activation = None
    validator_ok = False
    validator_rows = []
    errors = []
    raw_path = run_dir / 'codex.jsonl'
    stderr_path = run_dir / 'codex.stderr.txt'
    try:
        state = activation_state(task, arm)
        if arm.get('gateway'):
            activation = context_activation.decide(worktree, state, task.get('task_hint', task['prompt']))
        else:
            activation = {'decision': 'bypass', 'reason': 'control_arm', 'signals': {}, 'principal_intent': False}

        command = [
            shutil.which('codex'), 'exec', '--json', '-C', str(worktree),
            '-s', arm.get('sandbox', manifest.get('sandbox', 'workspace-write')),
        ]
        model = arm.get('model', manifest.get('model'))
        if model:
            command += ['-m', model]
        reasoning = arm.get('reasoning_effort', manifest.get('reasoning_effort'))
        if reasoning:
            command += ['-c', 'model_reasoning_effort=' + json.dumps(reasoning)]
        for override in manifest.get('config_overrides', []) + arm.get('config_overrides', []):
            command += ['-c', str(override)]

        prompt_parts = []
        if arm.get('prompt_prefix'):
            prompt_parts.append(str(arm['prompt_prefix']))
        if activation['decision'] == 'enable':
            install_skill(worktree)
            command += context_mcp.codex_arguments(
                worktree, audit,
                prefixes=task.get('allow_prefixes', []),
                files=task.get('allow_files', []),
                config=worker_config_for_arm(arm, worker),
                router_config=router_config_for_arm(arm, router),
            )
            prompt_parts.append(
                'Context Gateway is enabled for this run. Use io_context only when it helps. '
                'Keep security, architecture, debugging and editing reasoning in the principal agent.'
            )
        else:
            prompt_parts.append(
                'Context Gateway is intentionally bypassed for this run. '
                'Do not invoke external workers or native subagents.'
            )
        prompt_parts.append(task['prompt'])
        prompt = '\n\n'.join(prompt_parts)
        command += ['-']
        write_json(run_dir / 'command.json', command)
        write_json(run_dir / 'activation.json', activation)
        (run_dir / 'prompt.txt').write_text(run_record.redact(prompt), encoding='utf-8')

        timeout = int(task.get('timeout_seconds', manifest.get('timeout_seconds', 600)))
        cp = run_process(command, cwd=worktree, payload=prompt.encode('utf-8'), timeout=timeout, max_output=24_000_000)
        wall_ms = round((time.monotonic() - started) * 1000)
        raw_path.write_bytes(cp.stdout)
        if keep_raw:
            stderr_path.write_bytes(cp.stderr)

        context = {
            'worktree': str(worktree),
            'repo': str(repo['path']),
            'python': sys.executable,
            'run_dir': str(run_dir),
        }
        validator_ok, validator_rows = run_checks(
            worktree, task['validator'], context,
            timeout=int(task.get('validator_timeout_seconds', 180)),
        )

        principal = context_telemetry.trajectory(raw_path)
        worker_summary = context_telemetry.workers(audit / '.io-delegation' / 'worker-events.jsonl')
        operations = context_telemetry.operations(audit / 'context-events.jsonl')
        query = read_query_metrics(audit)
        route = query['route'] or ('bypass' if activation['decision'] == 'bypass' else None)
        expected = task.get('expected_route')
        fallback = query['router_route'] in ('error', 'current_rules')
        success = bool(cp.returncode == 0 and not cp.timed_out and not cp.oversized and validator_ok)
        if expected and expected != route:
            errors.append('route_mismatch')
        if cp.timed_out:
            errors.append('principal_timeout')
        if cp.oversized:
            errors.append('principal_output_oversized')
        if cp.returncode:
            errors.append('principal_nonzero_exit')
        if not validator_ok:
            errors.append('validator_failed')

        router_usage = None
        if query['router_called']:
            router_usage = {
                'input_tokens': query['router_input_tokens'],
                'output_tokens': query['router_output_tokens'],
                'cached_input_tokens': None,
                'cache_write_input_tokens': None,
                'reasoning_output_tokens': None,
            }
        worker_usage = worker_summary.get('usage') if worker_summary.get('calls') else None
        if worker_usage is not None:
            worker_usage = {
                'input_tokens': worker_usage.get('input_tokens'),
                'output_tokens': worker_usage.get('output_tokens'),
                'cached_input_tokens': worker_usage.get('cached_input_tokens'),
                'cache_write_input_tokens': worker_usage.get('cache_write_input_tokens'),
                'reasoning_output_tokens': worker_usage.get('reasoning_output_tokens'),
            }

        result_record = {
            'schema': run_record.SCHEMA,
            'run_id': run_id,
            'task_id': task['id'],
            'family': task['family'],
            'repo': task['repo'],
            'commit': repo['commit'],
            'arm': arm['name'],
            'host': 'codex',
            'started_at': started_epoch,
            'ended_at': time.time(),
            'success': success,
            'activation': activation,
            'route': {
                'effective': route,
                'expected': expected,
                'model_route': query['router_route'],
                'confidence': query['router_confidence'],
                'fallback': bool(fallback),
            },
            'principal': {
                'model': model,
                'reasoning_effort': reasoning,
                'usage': usage_object(principal),
                'raw_tokens': principal.get('raw_tokens'),
                'usage_complete': principal.get('usage_complete'),
            },
            'router': {
                'called': query['router_called'],
                'model': 'jev-latest' if query['router_called'] else None,
                'usage': router_usage,
                'latency_ms': query['router_elapsed_ms'],
            },
            'worker': {
                'calls': worker_summary.get('calls', 0),
                'accepted': worker_summary.get('accepted', 0),
                'rejected': worker_summary.get('failures', 0),
                'usage': worker_usage,
                'raw_tokens': worker_summary.get('raw_tokens'),
                'accounting_complete': worker_summary.get('accounting_complete'),
            },
            'context': {
                'source_bytes': operations.get('source_bytes'),
                'selected_bytes': operations.get('selected_bytes'),
                'result_bytes': operations.get('result_bytes'),
            },
            'timing': {'wall_ms': wall_ms},
            'validator': {
                'status': 'pass' if validator_ok else 'fail',
                'checks': len(validator_rows),
            },
            'conditions': run_conditions(principal, activation),
            'rework': 0,
            'errors': errors,
            'notes': '',
            'tags': list(task.get('tags', [])),
        }
        run_record.append(out_root / 'runs.jsonl', result_record)
        write_json(run_dir / 'record.json', run_record.sanitized(result_record))
        write_json(run_dir / 'validator.json', validator_rows)
        return result_record
    finally:
        if raw_path.exists() and not keep_raw:
            raw_path.unlink()
        if stderr_path.exists() and not keep_raw:
            stderr_path.unlink()
        try:
            git(repo['path'], 'worktree', 'remove', '--force', str(worktree))
        except Exception:
            pass


def plan(manifest, selected_tasks=None, selected_arms=None, repetitions=None, seed=None):
    tasks = [task for task in manifest['tasks'] if not selected_tasks or task['id'] in selected_tasks]
    arms = [arm for arm in manifest['arms'] if not selected_arms or arm['name'] in selected_arms]
    reps = repetitions or int(manifest.get('repetitions', 1))
    rows = [(task, arm, rep) for task in tasks for arm in arms for rep in range(1, reps + 1)]
    random.Random(seed if seed is not None else int(manifest.get('seed', 20260917))).shuffle(rows)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--output', type=Path, default=Path('v1-validation-results'))
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--task', action='append')
    parser.add_argument('--arm', action='append')
    parser.add_argument('--repetitions', type=int)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--keep-raw', action='store_true')
    args = parser.parse_args(argv)

    manifest = validate_manifest(read_json(args.manifest))
    repos, router, worker = preflight(manifest)
    runs = plan(manifest, args.task, args.arm, args.repetitions, args.seed)
    if args.preflight:
        print(json.dumps({
            'ok': True,
            'model_calls': 0,
            'runs': len(runs),
            'repositories': {key: value['commit'] for key, value in repos.items()},
        }, ensure_ascii=False))
        return 0

    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('output directory must be new/empty')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'runs').mkdir()
    write_json(output / 'manifest.snapshot.json', manifest)
    write_json(output / 'plan.json', [
        {'task': task['id'], 'arm': arm['name'], 'repetition': rep}
        for task, arm, rep in runs
    ])

    completed = []
    for ordinal, (task, arm, repetition) in enumerate(runs, 1):
        completed.append(run_one(
            manifest, repos, router, worker, task, arm, repetition,
            ordinal, output, keep_raw=args.keep_raw,
        ))
        summary = run_record.summarize(completed)
        write_json(output / 'summary.json', summary)
    print(json.dumps(run_record.summarize(completed), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'validation experiment: {exc}', file=sys.stderr)
        raise SystemExit(2)
