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
from context_query import QueryEngine
import decision_router as jev_router
from context_cache import ResultCache
from context_sources import SourceScope, encoded, exact_keys
from worker_mcp import relative_name, PROTOCOLS, MAX_RPC_BYTES, reject_links
import io_delegate as delegate

SERVER = 'io_context'
VERSION = '0.6.0'
MAX_RESULT_BYTES = 24_000


def private_external(path, root):
    raw = Path(path).absolute()
    if not raw.is_dir():
        raise ValueError('Audit directory must exist')
    info = raw.lstat()
    if raw.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Audit directory itself cannot be a symlink or reparse point')
    path = raw.resolve(strict=True)
    root = Path(root).resolve(strict=True)
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
    def __init__(self, root, audit_root, prefixes=(), files=(), config=None, router_config=None,
                 orchestrator_config=None, cache=True, host=None, model_preset=None):
        self.scope = SourceScope(root, prefixes, files)
        self.audit = private_external(audit_root, self.scope.root)
        self.config = config
        self.cache = ResultCache(self.audit, enabled=cache)
        self.engine = SemanticEngine(self.scope, self.audit, config, cache=cache) if config is not None else None
        self.query = QueryEngine(self.scope, self.audit, config, router_config,
                                 orchestrator_config=orchestrator_config, cache=cache,
                                 host=host, model_preset=model_preset)

    def tool_names(self):
        return ('search', 'extract', 'query')

    def call(self, name, arguments):
        op_id, started = uuid.uuid4().hex, time.monotonic()
        append_event(self.audit, dict(schema='io-context/v1', operation_id=op_id,
                                    event='operation_started', operation=name if name in self.tool_names() else 'invalid'))
        sources = []
        metrics = dict(route='local', model_calls=0, cache='disabled')
        try:
            if name not in self.tool_names():
                raise ValueError('Tool not enabled')
            if name == 'query':
                result, metrics = self.query.run(arguments)
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
    paths = dict(type='array', minItems=1, maxItems=128, uniqueItems=True,
                 items=dict(type='string', description='Relative permitted path or scoped glob. Never use . or output paths; if the task names data/page.html, use that exact path.'))
    def shape(kind, properties, required):
        return dict(type='object', properties=dict(kind=dict(type='string', enum=[kind]), **properties),
                    required=['kind', *required], additionalProperties=False)
    projection = dict(oneOf=[
        shape('html', dict(fields=dict(type='array', minItems=1, maxItems=4, uniqueItems=True,
                                      items=dict(type='string', enum=list(ops.HTML_FIELDS)))), ['fields']),
        shape('json', dict(pointers=dict(type='array', minItems=1, maxItems=16, uniqueItems=True, items=dict(type='string'))), ['pointers']),
        shape('lines', dict(start=dict(type='integer', minimum=1), end=dict(type='integer', minimum=1)), ['start','end']),
        shape('span', dict(start=dict(type='integer', minimum=0), end=dict(type='integer', minimum=1)), ['start','end'])])
    result = [dict(name='search', description='Local literal search or permitted file listing. If the task already names a source file, do not search to rediscover it; call extract directly. Never use . or output paths. Bounded excerpts; no model calls.',
        inputSchema=dict(type='object', properties=dict(paths=paths, needle=dict(type='string'),
            max_matches=dict(type='integer', minimum=1, maximum=100), window=dict(type='integer', minimum=0, maximum=256)),
            required=['paths'], additionalProperties=False),
        annotations=dict(readOnlyHint=True, destructiveHint=False, openWorldHint=False)),
        dict(name='extract', description='Local exact extraction. For static HTML fields use only title, h1, canonical, description. For JSON use pointers; lines/spans require start/end. If the task names a file, use that exact path. Batch up to 8 projections. No model calls.',
        inputSchema=dict(type='object', properties=dict(paths=paths, projection=projection,
                             projections=dict(type='array', minItems=1, maxItems=8, items=projection)),
                         required=['paths'], additionalProperties=False),
        annotations=dict(readOnlyHint=True, destructiveHint=False, openWorldHint=False))]

    selector = dict(oneOf=[
        shape('lines', dict(start=dict(type='integer',minimum=1), end=dict(type='integer',minimum=1)), ['start','end']),
        shape('span', dict(start=dict(type='integer',minimum=0), end=dict(type='integer',minimum=1)), ['start','end']),
        shape('literal', dict(needle=dict(type='string',minLength=1,maxLength=500), window=dict(type='integer',minimum=0,maximum=1024), max_regions=dict(type='integer',minimum=1,maximum=12)), ['needle']),
        shape('python_symbol', dict(name=dict(type='string',minLength=1,maxLength=160)), ['name'])])
    if service.query:
        result.append(dict(name='query',
            description='Smart bounded context query. Uses local rules plus optional Jev routing/compute scoring; approved host-aware model workers are selected by local policy, validated against literal evidence, and bounded by user ceilings/escalation limits. Jev sees only task text and aggregate metadata.',
            inputSchema=dict(type='object', properties=dict(
                selections=dict(type='array',minItems=1,maxItems=12,items=dict(type='object',
                    properties=dict(path=dict(type='string'),select=selector),required=['path','select'],additionalProperties=False)),
                question=dict(type='string',minLength=1,maxLength=4000),
                questions=dict(type='array',minItems=1,maxItems=4,items=dict(type='string',minLength=1,maxLength=800)),
                operation=dict(type='string',enum=['unknown','exploration','factual','generation','debugging','architecture','security','editing']),
                search_results=dict(type='integer',minimum=0), known_symbols=dict(type='integer',minimum=0)),
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


def codex_arguments(root, audit_root, prefixes=(), files=(), config=None, router_config=None,
                    orchestrator_config=None, model_preset=None):
    args = ['-I', str(Path(__file__).resolve()), '--root', str(Path(root).resolve()),
            '--audit-root', str(Path(audit_root).resolve()), '--host', 'codex']
    if config:
        args += ['--config', str(Path(config).resolve())]
    if router_config:
        args += ['--router-config', str(Path(router_config).resolve())]
    if orchestrator_config:
        args += ['--orchestrator-config', str(Path(orchestrator_config).resolve())]
    if model_preset:
        args += ['--model-preset', str(model_preset)]
    for p in prefixes: args += ['--allow-prefix', relative_name(p)]
    for p in files: args += ['--allow-file', relative_name(p)]
    if not prefixes and not files:
        raise ValueError('Explicit allowlist required')
    settings = dict(command=sys.executable, args=args, required=True, enabled=True,
                    startup_timeout_sec=20, tool_timeout_sec=185,
                    enabled_tools=['search', 'extract', 'query'])
    env_vars = ['CODEX_HOME']
    if config:
        cfg = delegate.load_config(str(config))
        settings['tool_timeout_sec'] = cfg.get('timeout_seconds', 120)+65
        if cfg.get('api_key_env'): env_vars.append(cfg['api_key_env'])
    if router_config:
        rcfg = jev_router.load_config(str(router_config))
        env_vars.append(rcfg['api_key_env'])
        settings['tool_timeout_sec'] = max(settings['tool_timeout_sec'], int(rcfg['timeout_seconds'])+30)
    if orchestrator_config:
        import context_orchestrator as compute
        ocfg = compute.load_config(str(orchestrator_config))
        env_vars.append(ocfg['api_key_env'])
        settings['tool_timeout_sec'] = max(settings['tool_timeout_sec'], int(ocfg['timeout_seconds'])+30)
    settings['env_vars'] = list(dict.fromkeys(env_vars))
    return [x for k, v in settings.items() for x in ('-c', f'mcp_servers.{SERVER}.{k}='+json.dumps(v, ensure_ascii=True))]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True); p.add_argument('--audit-root', type=Path, required=True)
    p.add_argument('--allow-prefix', action='append', default=[]); p.add_argument('--allow-file', action='append', default=[])
    p.add_argument('--config', type=Path)
    p.add_argument('--router-config', type=Path)
    p.add_argument('--orchestrator-config', type=Path)
    p.add_argument('--host', choices=['codex','cursor','claude-code'])
    p.add_argument('--model-preset', choices=['cost','balanced','quality'])
    p.add_argument('--no-cache', action='store_true', help='Disable local result reuse, not provider caching')
    a = p.parse_args()
    return serve(ContextService(a.root, a.audit_root, a.allow_prefix, a.allow_file, a.config,
                                a.router_config, a.orchestrator_config, cache=not a.no_cache,
                                host=a.host, model_preset=a.model_preset))


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (OSError, ValueError, delegate.DelegateError):
        print('Context MCP stopped; review approved paths/configuration. No permission changes attempted.', file=sys.stderr)
        raise SystemExit(2)
