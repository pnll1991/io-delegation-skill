#!/usr/bin/env python3
"""Scoped context preparation over MCP STDIO. Local tools need no model or login."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context_ops as ops
from context_engine import SemanticEngine
from context_cache import ResultCache
from context_sources import SourceScope, encoded, exact_keys
from worker_mcp import relative_name, PROTOCOLS, MAX_RPC_BYTES, reject_links
import io_delegate as delegate

SERVER = 'io_context'
VERSION = '0.4.0'
MAX_RESULT_BYTES = 24_000


def private_external(path, root):
    path = Path(path).absolute()
    if not path.is_dir():
        raise ValueError('Audit directory must exist')
    for component in (path, *path.parents):
        info = component.lstat()
        if component.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Private directory cannot contain symlinks or reparse points')
    path = path.resolve(strict=True)
    if path == root or root in path.parents:
        raise ValueError('Audit directory must be outside the project')
    return path


def append_event(folder, row):
    path = folder/'context-events.jsonl'
    if path.is_symlink():
        raise ValueError('Symlinked journal')
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    fd = os.open(path, flags, 0o600)
    try:
        raw = encoded(row)+b'\n'
        if os.write(fd, raw) != len(raw):
            raise OSError('Incomplete journal write')
        os.fsync(fd)
    finally:
        os.close(fd)


class ContextService:
    def __init__(self, root, audit_root, prefixes=(), files=(), config=None, cache=True):
        self.scope = SourceScope(root, prefixes, files)
        self.audit = private_external(audit_root, self.scope.root)
        self.config = config
        self.cache = ResultCache(self.audit, enabled=cache)
        self.engine = SemanticEngine(self.scope, self.audit, config, cache=cache) if config is not None else None

    def tool_names(self):
        return ('search', 'extract', 'semantic_query') if self.engine else ('search', 'extract')

    def call(self, name, arguments):
        op_id, started = uuid.uuid4().hex, time.monotonic()
        append_event(self.audit, dict(schema='io-context/v1', operation_id=op_id,
                                    event='operation_started', operation=name if name in self.tool_names() else 'invalid'))
        sources = []
        metrics = dict(route='local', model_calls=0, cache='disabled')
        try:
            if name not in self.tool_names():
                raise ValueError('Tool not enabled')
            if name == 'semantic_query':
                result, metrics = self.engine.run(arguments)
            else:
                if name == 'search':
                    exact_keys(arguments, ('paths',), ('needle', 'max_matches', 'window'))
                else:
                    exact_keys(arguments, ('paths',), ('projection', 'projections'))
                    if ('projection' in arguments) == ('projections' in arguments):
                        raise ValueError('Specify projection OR projections, not both')
                sources = self.scope.load(arguments['paths'])
                key = self.cache.key(self.scope, sources, name, arguments)
                result = self.cache.get(key)
                hit = result is not None
                if not hit:
                    if name == 'search':
                        result = ops.search(sources, arguments.get('needle'), arguments.get('max_matches', 20), arguments.get('window', 96))
                    else:
                        result = (ops.extract(sources, arguments['projection']) if 'projection' in arguments
                                  else ops.extract_many(sources, arguments['projections']))
                self.scope.unchanged(sources)
                if not hit and len(encoded(result)) <= MAX_RESULT_BYTES:
                    self.cache.put(key, result)
                metrics['cache'] = 'hit' if hit else ('miss' if self.cache.enabled else 'disabled')
                result['cache'] = metrics['cache']
                metrics['source_bytes'] = sum(x['bytes'] for x in sources)
            result['audit_id'] = op_id
            if len(encoded(result)) > MAX_RESULT_BYTES:
                result = dict(status='budget_exceeded', reason='Result too large; narrow fields, paths or ranges',
                              model_calls=metrics['model_calls'], audit_id=op_id)
        except (OSError, ValueError, TypeError, KeyError, delegate.DelegateError) as exc:
            message = str(exc) if type(exc) is ValueError else type(exc).__name__
            result = dict(status='error', reason=message, model_calls=metrics['model_calls'], audit_id=op_id)
        append_event(self.audit, dict(schema='io-context/v1', operation_id=op_id,
             event='operation_completed', operation=name if name in self.tool_names() else 'invalid',
             status=result['status'], result_bytes=len(encoded(result)),
             elapsed_ms=round(1000*(time.monotonic()-started)), **metrics))
        return dict(content=[dict(type='text', text=encoded(result).decode('utf-8'))],
                    isError=result['status'] in ('error', 'budget_exceeded'))


def tools(service):
    paths = dict(type='array', minItems=1, maxItems=128, items=dict(type='string'))
    projection = dict(type='object', properties={
        'kind': dict(type='string', enum=['html', 'json', 'lines', 'span']),
        'fields': dict(type='array', items=dict(type='string')),
        'pointers': dict(type='array', items=dict(type='string')),
        'start': dict(type='integer'), 'end': dict(type='integer')},
        required=['kind'], additionalProperties=False)
    result = [dict(name='search', description='Local literal search or file listing (omit needle) in permitted paths/globs. Bounded excerpts; no model calls.',
        inputSchema=dict(type='object', properties=dict(paths=paths, needle=dict(type='string'),
            max_matches=dict(type='integer', minimum=1, maximum=100), window=dict(type='integer', minimum=0, maximum=256)),
            required=['paths'], additionalProperties=False),
        annotations=dict(readOnlyHint=True, destructiveHint=False, openWorldHint=False)),
        dict(name='extract', description='Local static HTML fields, JSON pointers or explicit lines/spans across permitted files. Batch with projections (up to 8), or one projection. No model; missing/partial scope is explicit.',
        inputSchema=dict(type='object', properties=dict(paths=paths, projection=projection,
                             projections=dict(type='array', minItems=1, maxItems=8, items=projection)),
                         required=['paths'], additionalProperties=False),
        annotations=dict(readOnlyHint=True, destructiveHint=False, openWorldHint=False))]

    if service.engine:
        selector = dict(type='object', properties=dict(
            kind=dict(type='string', enum=['lines','span','literal','python_symbol']),
            start=dict(type='integer'), end=dict(type='integer'), name=dict(type='string'),
            needle=dict(type='string'), window=dict(type='integer',minimum=0,maximum=1024),
            max_regions=dict(type='integer',minimum=1,maximum=12)), required=['kind'], additionalProperties=False)
        result.append(dict(name='semantic_query',
            description='Optional interpretation over explicit fragments only. Prefer local extract for exact fields. One question OR up to four related questions; no whole-file fallback. Returns literal evidence and partial coverage.',
            inputSchema=dict(type='object', properties=dict(
                selections=dict(type='array',minItems=1,maxItems=12,items=dict(type='object',
                    properties=dict(path=dict(type='string'),select=selector),required=['path','select'],additionalProperties=False)),
                question=dict(type='string',minLength=1,maxLength=4000),
                questions=dict(type='array',minItems=1,maxItems=4,items=dict(type='string',minLength=1,maxLength=800))),
                required=['selections'],additionalProperties=False),
            annotations=dict(readOnlyHint=True,destructiveHint=False,idempotentHint=False,openWorldHint=True)))
    return result


def serve(service, inp=None, out=None):
    inp = inp or sys.stdin.buffer; out = out or sys.stdout.buffer
    initialized = False
    while True:
        raw = inp.readline(MAX_RPC_BYTES+1)
        if not raw:
            return 0
        if len(raw) > MAX_RPC_BYTES:
            return 2
        rid = None
        try:
            msg = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0':
                raise ValueError()
            if 'id' not in msg:
                continue
            rid = msg['id']
            if type(rid) not in (int, str):
                raise ValueError()
            method, params = msg.get('method'), msg.get('params', {})
            if not isinstance(params, dict):
                raise ValueError()
            if method == 'initialize':
                version = params.get('protocolVersion')
                initialized = True
                result = dict(protocolVersion=version if version in PROTOCOLS else PROTOCOLS[-1],
                    capabilities=dict(tools=dict(listChanged=False)), serverInfo=dict(name=SERVER, version=VERSION))
            elif not initialized:
                raise ValueError()
            elif method == 'ping': result = {}
            elif method == 'tools/list': result = dict(tools=tools(service))
            elif method == 'resources/list': result = dict(resources=[])
            elif method == 'prompts/list': result = dict(prompts=[])
            elif method == 'tools/call' and params.get('name') in service.tool_names():
                result = service.call(params['name'], params.get('arguments'))
            else:
                out.write(encoded(dict(jsonrpc='2.0', id=rid, error=dict(code=-32601, message='Method or tool not found')))+b'\n'); out.flush(); continue
            reply = dict(jsonrpc='2.0', id=rid, result=result)
        except (ValueError, UnicodeError, TypeError):
            reply = dict(jsonrpc='2.0', id=rid, error=dict(code=-32602, message='Invalid request or parameters'))
        out.write(encoded(reply)+b'\n'); out.flush()


def codex_arguments(root, audit_root, prefixes=(), files=(), config=None):
    args = ['-I', str(Path(__file__).resolve()), '--root', str(Path(root).resolve()),
            '--audit-root', str(Path(audit_root).resolve())]
    if config:
        args += ['--config', str(Path(config).resolve())]
    for p in prefixes: args += ['--allow-prefix', relative_name(p)]
    for p in files: args += ['--allow-file', relative_name(p)]
    if not prefixes and not files:
        raise ValueError('Explicit allowlist required')
    settings = dict(command=sys.executable, args=args, required=True, enabled=True,
                    startup_timeout_sec=20, tool_timeout_sec=185,
                    enabled_tools=['search', 'extract']+(['semantic_query'] if config else []))
    if config:
        cfg = delegate.load_config(str(config))
        settings['tool_timeout_sec'] = cfg.get('timeout_seconds', 120)+65
        settings['env_vars'] = ['CODEX_HOME']+([cfg['api_key_env']] if cfg.get('api_key_env') else [])
    return [x for k, v in settings.items() for x in ('-c', f'mcp_servers.{SERVER}.{k}='+json.dumps(v, ensure_ascii=True))]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True); p.add_argument('--audit-root', type=Path, required=True)
    p.add_argument('--allow-prefix', action='append', default=[]); p.add_argument('--allow-file', action='append', default=[])
    p.add_argument('--config', type=Path)
    p.add_argument('--no-cache', action='store_true', help='Disable local result reuse, not provider caching')
    a = p.parse_args()
    return serve(ContextService(a.root, a.audit_root, a.allow_prefix, a.allow_file, a.config, cache=not a.no_cache))


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (OSError, ValueError, delegate.DelegateError):
        print('Context MCP stopped; review approved paths/configuration. No permission changes attempted.', file=sys.stderr)
        raise SystemExit(2)
