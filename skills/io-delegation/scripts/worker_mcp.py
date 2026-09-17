#!/usr/bin/env python3
"""Read-only, scoped MCP bridge to an ALREADY approved I/O worker.

Codex starts this STDIO process, not its sandboxed shell. No ACL changes, no
credential copying, no general command tool, and no provider chosen by the model.
Only explicit, permitted source paths reach the worker. Logs stay outside the
agent worktree. MCP STDIO: one UTF-8 JSON-RPC object per line, stdout protocol only.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import io_delegate as delegate
from worker_runtime import Journal, TransportError, normalize_usage, usage_complete

SERVER = 'io_delegation'
PROTOCOLS = ('2024-11-05', '2025-03-26', '2025-06-18')
MAX_RPC_BYTES = 64_000


def json_bytes(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('utf-8')


def relative_name(name):
    if (not isinstance(name, str) or not name or len(name) > 1024 or
        any(c in name for c in '\\:\x00\r\n') or name.startswith(('/', '-', '~')) or
        any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('Paths must be canonical relative paths with forward slashes')
    return PurePosixPath(name).as_posix()


def reject_links(path, root):
    raw_root = Path(root).absolute()
    raw_path = Path(path).absolute()
    canonical_root = raw_root.resolve(strict=True)
    canonical_path = raw_path.resolve(strict=True)
    try:
        canonical_path.relative_to(canonical_root)
    except ValueError:
        raise ValueError('Path resolves outside approved root') from None
    # Prefer the lexical path when root/path share the same spelling so an
    # in-scope symlink cannot be hidden by resolve(). OS aliases above the
    # approved root (macOS /var, Windows 8.3 names) fall back to canonical form.
    try:
        relative = raw_path.relative_to(raw_root)
        current = raw_root
    except ValueError:
        relative = canonical_path.relative_to(canonical_root)
        current = canonical_root
    for part in relative.parts:
        current = current / part
        info = current.lstat()
        if current.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Symlinks and reparse points are not accepted')


class WorkerService:
    def __init__(self, root, config, audit_root, prefixes=(), files=()):
        self.root = Path(root).resolve(strict=True)
        self.config_path = Path(config).resolve(strict=True)
        self.audit_root = Path(audit_root).resolve(strict=True)
        if not self.root.is_dir() or not self.audit_root.is_dir():
            raise ValueError('Root and audit root must be existing directories')
        for path in (self.audit_root, self.config_path):
            if path == self.root or self.root in path.parents:
                raise ValueError('Approved config and audit must be outside the agent worktree')
        self.prefixes = tuple(relative_name(p) for p in prefixes)
        self.files = frozenset(relative_name(p) for p in files)
        if not self.prefixes and not self.files:
            raise ValueError('An explicit source allowlist is required')
        self.cfg = delegate.load_config(str(self.config_path))
        self.config_hash = hashlib.sha256(self.config_path.read_bytes()).hexdigest()
        self.max_calls = min(self.cfg.get('max_calls_per_workspace', 4), 4)
        self.calls = 0

    def bulk_read(self, arguments):
        journal = Journal(self.audit_root)
        dispatched = responded = False
        last_usage = normalize_usage(None)

        def record(event, **values):
            nonlocal dispatched, responded, last_usage
            dispatched = dispatched or event == 'worker_dispatched'
            if event == 'worker_response':
                responded = True
                last_usage = normalize_usage(values.get('usage'))
            return journal.emit(event, mode='bulk-read', transport='mcp', **values)

        try:
            record('worker_attempt')
            journal.acquire()
            self.calls += 1
            if self.calls > 8 or journal.dispatched_count() >= self.max_calls:
                raise ValueError('Worker call budget exhausted; stop rather than retry')
            if hashlib.sha256(self.config_path.read_bytes()).hexdigest() != self.config_hash:
                raise ValueError('Approved configuration changed; restart after review')
            if not isinstance(arguments, dict) or set(arguments) != {'paths', 'question'}:
                raise ValueError('Exactly paths and question are required; no commands or configuration')
            paths = arguments['paths']
            if not isinstance(paths, list) or not 1 <= len(paths) <= delegate.MAX_FILES:
                raise ValueError('Use 1..12 paths per call; prefer 1..4 and split larger requests')
            names = [relative_name(p) for p in paths]
            if len(names) != len(set(names)):
                raise ValueError('Duplicate paths are not accepted')
            for name in names:
                if name not in self.files and not any(name.startswith(p+'/') for p in self.prefixes):
                    raise ValueError('Source path is outside the approved allowlist')
                reject_links(self.root/name, self.root)
            question = arguments['question']
            if not isinstance(question, str) or not question.strip() or len(question.encode('utf-8')) > 16000:
                raise ValueError('Question must be nonempty and at most 16000 UTF-8 bytes')
            sources = delegate.snapshots(self.root, names)
            job = delegate.build_job('bulk-read', question, sources)
            output, metrics = delegate.invoke(job, self.cfg, self.root, record)
            if not responded:
                u = normalize_usage(metrics.get('usage'))
                record('worker_response', usage=u, usage_complete=usage_complete(u))
            delegate.unchanged(self.root, sources)
            result = delegate.validate_summary(output.lstrip('\ufeff'), sources)
            accepted = result['status'] == 'ok' and bool(result['findings'])
            record('worker_completed', accepted=accepted, status=result['status'], usage=last_usage)
            return {'content': [{'type': 'text', 'text': json_bytes(result).decode()}],
                    'isError': not accepted}
        except (delegate.DelegateError, TransportError, OSError, ValueError, TypeError, KeyError) as exc:
            # Never include external exception bodies, credentials, or worker prompts.
            message = str(exc) if type(exc) is ValueError or isinstance(exc, delegate.DelegateError) else type(exc).__name__
            record('worker_error', code=type(exc).__name__, dispatched=dispatched,
                   usage=last_usage, usage_complete=usage_complete(last_usage) if dispatched else True)
            return {'content': [{'type': 'text', 'text': json_bytes({
                'status': 'error', 'error': message, 'worker_dispatched': dispatched,
                'action': 'Stop on runtime/permission failure. Do not run Python in the shell or change sandbox permissions.'
            }).decode()}], 'isError': True}
        finally:
            journal.close()


def tools():
    return [{'name': 'bulk_read', 'description':
        'Extract factual evidence from explicitly approved project files using the configured worker. '
        'Send only paths and a narrow question, not file contents. Prefer 1-4 files; maximum 12 files '
        'and 12 findings per call. Do not use shell/Python to invoke the worker.',
        'inputSchema': {'type': 'object', 'properties': {
            'paths': {'type': 'array', 'minItems': 1, 'maxItems': 12, 'items': {'type': 'string'}},
            'question': {'type': 'string', 'minLength': 1, 'maxLength': 16000}},
            'required': ['paths', 'question'], 'additionalProperties': False},
        'annotations': {'readOnlyHint': True, 'destructiveHint': False,
                        'idempotentHint': False, 'openWorldHint': True}}]


def serve(service, inp=None, out=None):
    inp = inp or sys.stdin.buffer
    out = out or sys.stdout.buffer
    initialized = False
    while True:
        raw = inp.readline(MAX_RPC_BYTES+1)
        if not raw:
            return 0
        if len(raw) > MAX_RPC_BYTES:
            return 2  # Bound input, rather than accumulating an unbounded JSON message.
        rid = None
        try:
            msg = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0':
                raise ValueError('Invalid JSON-RPC message')
            if 'id' not in msg:  # Notifications (including initialized/cancelled) have no reply.
                continue
            rid = msg['id']
            if type(rid) not in (int, str):
                raise ValueError('Invalid request ID')
            method, params = msg.get('method'), msg.get('params', {})
            if not isinstance(params, dict):
                raise ValueError('Invalid parameters')
            if method == 'initialize':
                version = params.get('protocolVersion')
                initialized = True
                result = {'protocolVersion': version if version in PROTOCOLS else PROTOCOLS[-1],
                          'capabilities': {'tools': {'listChanged': False}},
                          'serverInfo': {'name': SERVER, 'version': '0.3.1'}}
            elif not initialized:
                raise ValueError('Initialize first')
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': tools()}
            elif method == 'tools/call' and params.get('name') == 'bulk_read':
                result = service.bulk_read(params.get('arguments'))
            elif method == 'resources/list':
                result = {'resources': []}
            elif method == 'prompts/list':
                result = {'prompts': []}
            else:
                out.write(json_bytes({'jsonrpc': '2.0', 'id': rid, 'error': {
                    'code': -32601, 'message': 'Method or tool not found'}})+b'\n'); out.flush()
                continue
            reply = {'jsonrpc': '2.0', 'id': rid, 'result': result}
        except (ValueError, UnicodeError, TypeError):
            reply = {'jsonrpc': '2.0', 'id': rid, 'error': {'code': -32602, 'message': 'Invalid request or parameters'}}
        out.write(json_bytes(reply)+b'\n')
        out.flush()


def codex_mcp_arguments(root, config, audit_root, prefixes=(), files=()):
    """Invocation-only settings: no global config changes or approval bypass."""
    cfg = delegate.load_config(str(config))
    args = ['-I', str(Path(__file__).resolve()), '--root', str(Path(root).resolve()),
            '--config', str(Path(config).resolve()), '--audit-root', str(Path(audit_root).resolve())]
    for prefix in prefixes:
        args += ['--allow-prefix', relative_name(prefix)]
    for file in files:
        args += ['--allow-file', relative_name(file)]
    if not prefixes and not files:
        raise ValueError('MCP worker needs an explicit source allowlist')
    settings = {'command': sys.executable, 'args': args, 'enabled': True, 'required': True,
                'enabled_tools': ['bulk_read'], 'startup_timeout_sec': 20,
                'tool_timeout_sec': cfg.get('timeout_seconds',120)+65}
    # Forward names, never credential values. Existing Codex login remains in place.
    env_vars = ['CODEX_HOME']
    if cfg.get('api_key_env'):
        env_vars.append(cfg['api_key_env'])
    settings['env_vars'] = env_vars
    result = []
    for key, value in settings.items():
        result += ['-c', f'mcp_servers.{SERVER}.{key}='+json.dumps(value, ensure_ascii=True)]
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--config', type=Path, required=True)
    ap.add_argument('--audit-root', type=Path, required=True)
    ap.add_argument('--allow-prefix', action='append', default=[])
    ap.add_argument('--allow-file', action='append', default=[])
    args = ap.parse_args()
    service = WorkerService(args.root, args.config, args.audit_root, args.allow_prefix, args.allow_file)
    return serve(service)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, delegate.DelegateError):
        print('Worker MCP stopped. Check approved config, paths, permissions and the audit journal; in-flight usage may be unknown.', file=sys.stderr)
        raise SystemExit(2)
