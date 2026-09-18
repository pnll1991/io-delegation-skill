#!/usr/bin/env python3
"""Machine-local MCP bootstrap that resolves the current configured project."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import sys

PROTOCOLS = ('2024-11-05', '2025-03-26', '2025-06-18')
ID_RE = re.compile(r'^[0-9a-f]{12,32}$')
MARKER = Path('.io-delegation/project.json')


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')


def user_root():
    return Path(os.environ.get('IO_DELEGATION_HOME', str(Path.home()/'.io-delegation'))).expanduser().resolve()


def find_project(start):
    here = Path(start).resolve(strict=True)
    if here.is_file():
        here = here.parent
    for root in (here, *here.parents):
        marker = root/MARKER
        if marker.is_file() and not marker.is_symlink():
            return root, marker
    return None, None


def marker_id(path):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    project_id = data.get('project_id') if isinstance(data, dict) else None
    if not isinstance(project_id, str) or not ID_RE.fullmatch(project_id):
        raise ValueError('Invalid I/O Delegation project marker')
    return project_id


def load_state(root, marker):
    project_id = marker_id(marker)
    path = user_root()/'projects'/f'{project_id}.json'
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('project_id') != project_id:
        raise ValueError('Invalid I/O Delegation project state')
    data = dict(data)
    data['project'] = str(root)
    return data


def inactive_serve(inp=None, out=None):
    inp = inp or sys.stdin.buffer
    out = out or sys.stdout.buffer
    initialized = False
    while True:
        raw = inp.readline(1_000_001)
        if not raw:
            return 0
        try:
            msg = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0':
                raise ValueError()
            if 'id' not in msg:
                continue
            rid = msg['id']; method = msg.get('method')
            if method == 'initialize':
                initialized = True
                version = msg.get('params', {}).get('protocolVersion')
                result = {'protocolVersion': version if version in PROTOCOLS else PROTOCOLS[-1],
                          'capabilities': {'tools': {'listChanged': False}},
                          'serverInfo': {'name': 'io_context', 'version': '1.1'}}
            elif initialized and method == 'tools/list':
                result = {'tools': []}
            elif initialized and method in ('ping', 'resources/list', 'prompts/list'):
                result = {} if method == 'ping' else {method.split('/')[0]: []}
            else:
                out.write(encoded({'jsonrpc':'2.0','id':rid,
                                   'error':{'code':-32601,'message':'I/O Delegation is inactive for this project/task'}})); out.flush(); continue
            out.write(encoded({'jsonrpc':'2.0','id':rid,'result':result})); out.flush()
        except (ValueError, TypeError, UnicodeError):
            continue


def configured_serve(root, state):
    scripts = Path(__file__).resolve().parent
    sys.path.insert(0, str(scripts))
    from context_activation import decide as activation_decide
    # Existing V1.1 states predate activation_mode. Keep their behavior until setup refreshes them.
    if 'activation_mode' not in state:
        state = dict(state)
        state['activation_mode'] = 'always'
    activation = activation_decide(root, state)
    if activation.get('decision') == 'bypass':
        return inactive_serve()
    from context_mcp import ContextService, serve
    worker = state.get('worker_config')
    router = state.get('router_config')
    orchestrator = state.get('orchestrator_config')
    service = ContextService(root, Path(state['audit_root']),
                             prefixes=state.get('allow_prefixes', ()),
                             files=state.get('allow_files', ()),
                             config=Path(worker) if worker else None,
                             router_config=Path(router) if router else None,
                             orchestrator_config=Path(orchestrator) if orchestrator else None)
    return serve(service)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', help='Explicit workspace root; otherwise resolve from MCP cwd')
    a = p.parse_args(argv)
    try:
        start = Path(a.project).expanduser() if a.project else Path.cwd()
        root, marker = find_project(start)
        if root is None:
            return inactive_serve()
        state = load_state(root, marker)
        return configured_serve(root, state)
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return inactive_serve()


if __name__ == '__main__':
    raise SystemExit(main())
