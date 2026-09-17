#!/usr/bin/env python3
"""Authenticated Codex / Claude Code / Cursor host validation using observed MCP audit data."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRIPTS = ROOT / 'skills' / 'io-delegation' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))
import context_telemetry
import io_delegate
import record
import validators as static_validators
from worker_runtime import run_process

PROJECT_ID_RE = re.compile(r'^[0-9a-f]{12,32}$')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def build_command(host, project, prompt, model=None, mcp_config=None):
    project = str(Path(project).resolve())
    if host == 'claude':
        exe = shutil.which('claude') or 'claude'
        if not mcp_config:
            raise ValueError('Claude host validation requires an exclusive MCP config')
        command = [
            exe, '-p', prompt,
            '--output-format', 'json',
            '--max-turns', '8',
            '--no-session-persistence',
            '--permission-prompts', 'none',
            '--tools', '',
            '--strict-mcp-config', '--mcp-config', str(mcp_config),
            '--allowedTools',
            'mcp__io_context__search,mcp__io_context__extract,mcp__io_context__query',
        ]
        if model:
            command += ['--model', model]
        return command
    if host == 'cursor':
        exe = shutil.which('agent') or 'agent'
        command = [exe, '-p', prompt, '--output-format', 'json', '--mode', 'ask', '--workspace', project]
        if model:
            command += ['--model', model]
        return command
    if host == 'codex':
        exe = shutil.which('codex') or 'codex'
        command = [
            exe, 'exec', '--json', '--ephemeral', '--ignore-user-config',
            '--skip-git-repo-check', '--sandbox', 'read-only', '-C', project,
            '-c', 'approval_policy="never"', '-c', 'web_search="disabled"',
            '-c', 'features.shell_tool=false', '-c', 'features.multi_agent=false',
            '-c', 'features.memories=false', '-c', 'features.unified_exec=false',
            '-c', 'features.skill_mcp_dependency_install=false',
            *managed_codex_mcp_args(project), '-',
        ]
        if model:
            command[command.index('-c')] = '-c'
            command[2:2] = ['-m', model]
        return command
    raise ValueError('host must be codex, claude or cursor')


def mcp_check_command(host):
    if host == 'claude':
        return [shutil.which('claude') or 'claude', 'mcp', 'get', 'io_context']
    if host == 'cursor':
        return [shutil.which('agent') or 'agent', 'mcp', 'list-tools', 'io_context']
    if host == 'codex':
        return [shutil.which('codex') or 'codex', 'mcp', 'list', '--json']
    raise ValueError('host must be codex, claude or cursor')


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
            if (('input_tokens' in item and 'output_tokens' in item) or
                ('inputTokens' in item and 'outputTokens' in item)):
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
    def integer(*names):
        for name in names:
            value=item.get(name)
            if type(value) is int and value >= 0: return value
        return None
    result = {
        'input_tokens': integer('input_tokens','inputTokens'),
        'output_tokens': integer('output_tokens','outputTokens'),
        'cached_input_tokens': integer('cached_input_tokens','cacheReadTokens'),
        'cache_write_input_tokens': integer('cache_write_input_tokens','cacheWriteTokens'),
        'reasoning_output_tokens': integer('reasoning_output_tokens','reasoningOutputTokens'),
    }
    if result['input_tokens'] is None or result['output_tokens'] is None:
        return None
    return result



def _parsed_output_values(raw):
    if len(raw) > 8_000_000:
        return []
    text=raw.decode('utf-8-sig',errors='replace').strip()
    values=[]
    if not text: return values
    try: values.append(json.loads(text))
    except ValueError:
        for line in text.splitlines():
            try: values.append(json.loads(line))
            except ValueError: continue
    return values


def _answer_candidates(value):
    result=[]
    def add(item):
        if isinstance(item,dict): result.append(item)
        elif isinstance(item,str):
            try:
                parsed=json.loads(io_delegate.unwrap(item.lstrip('\ufeff').strip()))
            except (ValueError,TypeError): return
            if isinstance(parsed,dict): result.append(parsed)
    def walk(item):
        if isinstance(item,dict):
            for key in ('result','final','answer','text','content','message'):
                if key in item: add(item[key])
            for child in item.values(): walk(child)
        elif isinstance(item,list):
            for child in item: walk(child)
    walk(value); return result


def answer_validator(raw,expected):
    expected=static_validators.normalize(expected)
    candidates=[]
    for value in _parsed_output_values(raw): candidates.extend(_answer_candidates(value))
    matched=any(static_validators.normalize(item)==expected for item in candidates)
    return matched,[{'ok':matched,'kind':'assistant_json','candidate_count':len(candidates)}]

def project_state(project):
    project = Path(project).resolve(strict=True)
    marker = read_json(project / '.io-delegation' / 'project.json')
    project_id = marker.get('project_id') if isinstance(marker, dict) else None
    if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
        raise ValueError('invalid I/O Delegation project marker')
    home = Path(os.environ.get('IO_DELEGATION_HOME', str(Path.home()/'.io-delegation'))).expanduser()
    state = read_json(home / 'projects' / f'{project_id}.json')
    if not isinstance(state, dict) or state.get('project_id') != project_id:
        raise ValueError('I/O Delegation project state missing or invalid')
    return state


def project_audit_root(project):
    state = project_state(project)
    audit = Path(state.get('audit_root', '')).expanduser().resolve(strict=True)
    if not audit.is_dir():
        raise ValueError('I/O Delegation audit directory missing')
    return audit


def audit_snapshot(audit):
    context_path = Path(audit) / 'context-events.jsonl'
    rows, malformed = context_telemetry.events(context_path)
    worker = context_telemetry.workers(Path(audit) / '.io-delegation' / 'worker-events.jsonl')
    return {'context_count': len(rows), 'context_malformed': malformed, 'worker': worker}


def _sum_if_complete(rows, key, predicate=lambda row: True):
    selected = [row for row in rows if predicate(row)]
    if not selected:
        return None
    values = [row.get(key) for row in selected]
    if any(type(value) is not int or value < 0 for value in values):
        return None
    return sum(values)


def observed_context(audit, before):
    rows, malformed = context_telemetry.events(Path(audit) / 'context-events.jsonl')
    new = rows[before['context_count']:]
    completed = [row for row in new if row.get('schema') == 'io-context/v1' and row.get('event') == 'operation_completed']
    queries = [row for row in completed if row.get('operation') == 'query']
    if queries:
        route = queries[-1].get('route')
    elif any(row.get('operation') == 'extract' for row in completed):
        route = 'targeted_read'
    elif any(row.get('operation') == 'search' for row in completed):
        route = 'deterministic'
    elif not completed:
        route = 'principal'
    else:
        route = 'deterministic'
    router_rows = [row for row in queries if int(row.get('router_calls', 0) or 0) > 0]
    router_called = bool(router_rows)
    router_usage = None
    if router_called:
        input_tokens = _sum_if_complete(router_rows, 'router_input_tokens')
        output_tokens = _sum_if_complete(router_rows, 'router_output_tokens')
        if input_tokens is not None and output_tokens is not None:
            router_usage = {
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'cached_input_tokens': None,
                'reasoning_output_tokens': None,
            }
    return {
        'route': route,
        'operations': [row.get('operation') for row in completed],
        'source_bytes': sum(int(row.get('source_bytes', 0) or 0) for row in completed),
        'selected_bytes': sum(int(row.get('selected_bytes', 0) or 0) for row in completed),
        'result_bytes': sum(int(row.get('result_bytes', 0) or 0) for row in completed),
        'router_called': router_called,
        'router_usage': router_usage,
        'router_latency_ms': _sum_if_complete(router_rows, 'router_elapsed_ms') if router_called else None,
        'router_route': queries[-1].get('router_route') if queries else None,
        'router_confidence': queries[-1].get('router_confidence') if queries else None,
        'malformed_delta': max(0, malformed - before['context_malformed']),
    }


def _nonnegative_delta(after, before, key):
    a = after.get(key); b = before.get(key)
    if type(a) is not int or type(b) is not int or a < b:
        return None
    return a - b


def observed_worker(audit, before):
    after = context_telemetry.workers(Path(audit) / '.io-delegation' / 'worker-events.jsonl')
    prior = before['worker']
    calls = _nonnegative_delta(after, prior, 'calls')
    accepted = _nonnegative_delta(after, prior, 'accepted')
    failures = _nonnegative_delta(after, prior, 'failures')
    usage = None
    if calls and after.get('accounting_complete') and prior.get('accounting_complete'):
        values = {}
        for key in ('input_tokens','output_tokens','cached_input_tokens','cache_write_input_tokens','reasoning_output_tokens'):
            a = (after.get('usage') or {}).get(key); b = (prior.get('usage') or {}).get(key)
            values[key] = a-b if type(a) is int and type(b) is int and a >= b else None
        if values['input_tokens'] is not None and values['output_tokens'] is not None:
            usage = {
                'input_tokens': values['input_tokens'],
                'output_tokens': values['output_tokens'],
                'cached_input_tokens': values['cached_input_tokens'],
                'reasoning_output_tokens': values['reasoning_output_tokens'],
            }
    raw_tokens = None
    a = after.get('raw_tokens'); b = prior.get('raw_tokens')
    if type(a) is int and type(b) is int and a >= b:
        raw_tokens = a-b
    return {
        'calls': calls if calls is not None else 0,
        'accepted': accepted if accepted is not None else 0,
        'rejected': failures if failures is not None else 0,
        'usage': usage,
        'raw_tokens': raw_tokens,
        'accounting_complete': bool(after.get('accounting_complete') and prior.get('accounting_complete')),
    }



def managed_codex_mcp_args(project):
    home=Path(os.environ.get('IO_DELEGATION_HOME',str(Path.home()/'.io-delegation'))).expanduser()
    bootstrap=(home/'runtime/io-delegation/scripts/context_bootstrap.py').resolve(strict=True)
    state=project_state(project)
    envs=['CODEX_HOME','IO_DELEGATION_HOME']
    for name in state.get('credential_env_names',[]):
        if isinstance(name,str) and name and name not in envs: envs.append(name)
    settings={
        'command':str(Path(sys.executable).resolve()),
        'args':[str(bootstrap),'--project',str(Path(project).resolve())],
        'enabled':True,'required':True,'startup_timeout_sec':20,'tool_timeout_sec':185,
        'enabled_tools':['search','extract','query'],'env_vars':envs,
    }
    return [item for key,value in settings.items()
            for item in ('-c',f'mcp_servers.io_context.{key}='+json.dumps(value,ensure_ascii=True))]


def managed_claude_mcp_config(path):
    home=Path(os.environ.get('IO_DELEGATION_HOME',str(Path.home()/'.io-delegation'))).expanduser()
    bootstrap=(home/'runtime/io-delegation/scripts/context_bootstrap.py').resolve(strict=True)
    definition={'mcpServers':{'io_context':{'type':'stdio','command':str(Path(sys.executable).resolve()),'args':[str(bootstrap)]}}}
    Path(path).write_text(json.dumps(definition,separators=(',',':'))+'\n',encoding='utf-8')
    return Path(path)

def managed_cursor_project_config(project):
    project=Path(project).resolve(strict=True); folder=project/'.cursor'
    mcp_path=folder/'mcp.json'; cli_path=folder/'cli.json'
    if mcp_path.exists() or cli_path.exists():
        raise ValueError('Cursor validation refuses to overwrite existing project config')
    home=Path(os.environ.get('IO_DELEGATION_HOME',str(Path.home()/'.io-delegation'))).expanduser()
    bootstrap=(home/'runtime/io-delegation/scripts/context_bootstrap.py').resolve(strict=True)
    folder.mkdir(parents=True,exist_ok=True)
    mcp={'mcpServers':{'io_context':{'type':'stdio','command':str(Path(sys.executable).resolve()),'args':[str(bootstrap)]}}}
    cli={'version':1,'permissions':{'allow':['Mcp(io_context:*)'],'deny':['Shell(*)','Read(**)','Write(**)','WebFetch(*)']}}
    mcp_path.write_text(json.dumps(mcp,separators=(',',':'))+'\n',encoding='utf-8')
    cli_path.write_text(json.dumps(cli,separators=(',',':'))+'\n',encoding='utf-8')
    return (mcp_path,cli_path)

def cleanup_cursor_project_config(paths):
    if not paths: return
    folder=paths[0].parent
    for path in paths: path.unlink(missing_ok=True)
    try: folder.rmdir()
    except OSError: pass

def preflight(host, project, io_command='io-delegation'):
    executable = {'claude':'claude','cursor':'agent','codex':'codex'}[host]
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
    return project, project_audit_root(project)


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
        expected=case.get('expected_json')
        commands=case.get('validator')
        if expected is None and (not isinstance(commands,list) or not commands):
            raise ValueError('case validator or expected_json required')
        if expected is not None and not isinstance(expected,dict):
            raise ValueError('expected_json must be an object')
        if case.get('expected_route') not in ('deterministic','targeted_read','bulk_read','principal'):
            raise ValueError('case expected_route required')
    return data


def run_suite(host, project, audit, suite, output, model=None):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('output directory must be new/empty')
    output.mkdir(parents=True, exist_ok=True)
    records = output / 'runs.jsonl'
    results = []
    for index, case in enumerate(suite['cases'], 1):
        run_id = f'{host}-{index:02d}-{case["id"]}-r1'
        prompt = case['prompt']
        mcp_config=None
        temporary=None
        if host == 'claude':
            temporary=tempfile.TemporaryDirectory(prefix='io-host-mcp-')
            mcp_config=managed_claude_mcp_config(Path(temporary.name)/'mcp.json')
        command = build_command(host, project, prompt, model=model, mcp_config=mcp_config)
        before = audit_snapshot(audit)
        started_epoch = time.time()
        started = time.monotonic()
        cp = run_process(command, cwd=project, timeout=int(case.get('timeout_seconds', 600)),
                         payload=prompt.encode('utf-8') if host == 'codex' else b'',
                         max_output=8_000_000)
        wall_ms = round((time.monotonic() - started) * 1000)
        if case.get('expected_json') is not None:
            ok, checks = answer_validator(cp.stdout, case['expected_json'])
        else:
            ok, checks = validator(project, case['validator'], int(case.get('validator_timeout_seconds', 120)))
        usage = parse_usage(cp.stdout)
        raw_tokens = None
        if usage and usage['input_tokens'] is not None and usage['output_tokens'] is not None:
            raw_tokens = usage['input_tokens'] + usage['output_tokens']
        context = observed_context(audit, before)
        worker = observed_worker(audit, before)
        expected = case['expected_route']
        route_ok = context['route'] == expected
        success = cp.returncode == 0 and not cp.timed_out and not cp.oversized and ok and route_ok and context['malformed_delta'] == 0
        errors = []
        if cp.returncode: errors.append('host_nonzero_exit')
        if cp.timed_out: errors.append('host_timeout')
        if cp.oversized: errors.append('host_output_limit')
        if not ok: errors.append('validator_failed')
        if not route_ok: errors.append('route_mismatch')
        if context['malformed_delta']: errors.append('audit_malformed')
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
            'route': {
                'effective': context['route'],
                'expected': expected,
                'model_route': context['router_route'],
                'confidence': context['router_confidence'],
                'fallback': context['router_route'] in ('error','current_rules'),
            },
            'principal': {'model': model, 'usage': usage, 'raw_tokens': raw_tokens},
            'router': {
                'called': context['router_called'],
                'usage': context['router_usage'],
                'latency_ms': context['router_latency_ms'],
            },
            'worker': worker,
            'context': {
                'source_bytes': context['source_bytes'],
                'selected_bytes': context['selected_bytes'],
                'result_bytes': context['result_bytes'],
            },
            'timing': {'wall_ms': wall_ms},
            'validator': {'status': 'pass' if ok else 'fail', 'checks': len(checks)},
            'rework': 0,
            'errors': errors,
            'notes': 'observed_operations=' + ','.join(str(x) for x in context['operations']),
            'tags': ['authenticated-host-validation','observed-mcp-audit'],
        }
        record.append(records, value)
        results.append(value)
        if temporary is not None:
            temporary.cleanup()
    summary = record.summarize(results)
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', choices=['codex', 'claude', 'cursor'], required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--suite', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model')
    parser.add_argument('--io-command', default='io-delegation')
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args(argv)
    suite = validate_suite(read_json(args.suite))
    cursor_paths=None
    try:
        if args.host == 'cursor':
            cursor_paths=managed_cursor_project_config(args.project)
        project, audit = preflight(args.host, args.project, args.io_command)
        if args.preflight:
            print(json.dumps({'ok': True, 'model_calls': 0, 'cases': len(suite['cases']), 'audit': str(audit)}))
            return 0
        print(json.dumps(run_suite(args.host, project, audit, suite, args.output, model=args.model), ensure_ascii=False, indent=2))
        return 0
    finally:
        cleanup_cursor_project_config(cursor_paths)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'host validation: {exc}', file=sys.stderr)
        raise SystemExit(2)
