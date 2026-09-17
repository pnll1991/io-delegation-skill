#!/usr/bin/env python3
"""Authenticated Claude Code / Cursor host validation using the common run schema."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import record


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def build_command(host, project, prompt, model=None):
    project = str(Path(project).resolve())
    if host == 'claude':
        exe = shutil.which('claude') or 'claude'
        command = [
            exe, '-p', prompt,
            '--output-format', 'json',
            '--max-turns', '8',
            '--allowedTools',
            'mcp__io_context__search,mcp__io_context__extract,mcp__io_context__query',
        ]
        if model:
            command += ['--model', model]
        return command
    if host == 'cursor':
        exe = shutil.which('agent') or 'agent'
        command = [exe, '-p', prompt, '--output-format', 'json', '--workspace', project]
        if model:
            command += ['--model', model]
        return command
    raise ValueError('host must be claude or cursor')


def mcp_check_command(host):
    if host == 'claude':
        return [shutil.which('claude') or 'claude', 'mcp', 'get', 'io_context']
    if host == 'cursor':
        return [shutil.which('agent') or 'agent', 'mcp', 'list-tools', 'io_context']
    raise ValueError('host must be claude or cursor')


def format_value(value, context):
    text = str(value)
    for key, item in context.items():
        text = text.replace('{' + key + '}', str(item))
    return text


def validator(project, commands, timeout=120):
    rows = []
    context = {'project': str(project), 'python': sys.executable}
    for entry in commands:
        if isinstance(entry, str):
            text = format_value(entry, context)
            argv = ['cmd.exe', '/d', '/c', text] if os.name == 'nt' else ['/bin/sh', '-c', text]
        else:
            argv = [format_value(item, context) for item in entry]
        try:
            cp = subprocess.run(argv, cwd=project, capture_output=True, timeout=timeout)
            rows.append({'ok': cp.returncode == 0, 'exit_code': cp.returncode})
        except subprocess.TimeoutExpired:
            rows.append({'ok': False, 'timeout': True})
        except OSError as exc:
            rows.append({'ok': False, 'error': type(exc).__name__})
    return bool(rows) and all(row['ok'] for row in rows), rows


def parse_usage(raw):
    try:
        value = json.loads(raw.decode('utf-8-sig'))
    except (ValueError, UnicodeError):
        return None
    candidates = []
    def walk(item):
        if isinstance(item, dict):
            if 'input_tokens' in item and 'output_tokens' in item:
                candidates.append(item)
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
    walk(value)
    if not candidates:
        return None
    item = candidates[-1]
    def integer(name):
        value = item.get(name)
        return value if type(value) is int and value >= 0 else None
    result = {
        'input_tokens': integer('input_tokens'),
        'output_tokens': integer('output_tokens'),
        'cached_input_tokens': integer('cached_input_tokens'),
        'reasoning_output_tokens': integer('reasoning_output_tokens'),
    }
    if result['input_tokens'] is None or result['output_tokens'] is None:
        return None
    return result


def preflight(host, project, io_command='io-delegation'):
    executable = 'claude' if host == 'claude' else 'agent'
    if not shutil.which(executable):
        raise ValueError(f'{executable} CLI not installed')
    project = Path(project).resolve(strict=True)
    doctor = subprocess.run(
        [io_command, 'doctor', '--project', str(project), '--json'],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
    )
    if doctor.returncode not in (0,):
        raise ValueError('io-delegation doctor failed')
    mcp = subprocess.run(
        mcp_check_command(host), cwd=project,
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
    )
    if mcp.returncode:
        raise ValueError('host does not report io_context MCP as available')
    return project


def validate_suite(data):
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('suite version 1 required')
    cases = data.get('cases')
    if not isinstance(cases, list) or len(cases) < 4:
        raise ValueError('at least four host cases required')
    ids = [case.get('id') for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate case ids')
    for case in cases:
        if not isinstance(case.get('prompt'), str) or not case['prompt']:
            raise ValueError('case prompt required')
        if not isinstance(case.get('validator'), list) or not case['validator']:
            raise ValueError('case validator required')
    return data


def run_suite(host, project, suite, output, model=None):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('output directory must be new/empty')
    output.mkdir(parents=True, exist_ok=True)
    records = output / 'runs.jsonl'
    results = []
    for index, case in enumerate(suite['cases'], 1):
        run_id = f'{host}-{index:02d}-{case["id"]}-r1'
        prompt = case['prompt']
        command = build_command(host, project, prompt, model=model)
        started_epoch = time.time()
        started = time.monotonic()
        cp = subprocess.run(command, cwd=project, capture_output=True, timeout=int(case.get('timeout_seconds', 600)))
        wall_ms = round((time.monotonic() - started) * 1000)
        ok, checks = validator(project, case['validator'], int(case.get('validator_timeout_seconds', 120)))
        usage = parse_usage(cp.stdout)
        raw_tokens = None
        if usage and usage['input_tokens'] is not None and usage['output_tokens'] is not None:
            raw_tokens = usage['input_tokens'] + usage['output_tokens']
        success = cp.returncode == 0 and ok
        value = {
            'schema': record.SCHEMA,
            'run_id': run_id,
            'task_id': case['id'],
            'family': case.get('family', 'targeted'),
            'repo': suite.get('repo_id', Path(project).name),
            'commit': suite.get('commit', 'host-current'),
            'arm': 'gateway-host',
            'host': host,
            'started_at': started_epoch,
            'ended_at': time.time(),
            'success': success,
            'activation': None,
            'route': {'effective': case.get('expected_route'), 'confidence': None, 'fallback': False},
            'principal': {'model': model, 'usage': usage, 'raw_tokens': raw_tokens},
            'router': {'called': None, 'usage': None, 'latency_ms': None},
            'worker': {'calls': 0, 'accepted': 0, 'rejected': 0, 'usage': None, 'raw_tokens': None},
            'context': {'source_bytes': None, 'selected_bytes': None, 'result_bytes': None},
            'timing': {'wall_ms': wall_ms},
            'validator': {'status': 'pass' if ok else 'fail', 'checks': len(checks)},
            'rework': 0,
            'errors': [] if success else ['host_or_validator_failure'],
            'notes': '',
            'tags': ['authenticated-host-validation'],
        }
        record.append(records, value)
        results.append(value)
    summary = record.summarize(results)
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', choices=['claude', 'cursor'], required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--suite', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model')
    parser.add_argument('--io-command', default='io-delegation')
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args(argv)
    suite = validate_suite(read_json(args.suite))
    project = preflight(args.host, args.project, args.io_command)
    if args.preflight:
        print(json.dumps({'ok': True, 'model_calls': 0, 'cases': len(suite['cases'])}))
        return 0
    print(json.dumps(run_suite(args.host, project, suite, args.output, model=args.model), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'host validation: {exc}', file=sys.stderr)
        raise SystemExit(2)
