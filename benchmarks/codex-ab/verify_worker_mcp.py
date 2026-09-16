#!/usr/bin/env python3
"""Test the REAL Codex -> MCP -> worker boundary before another project benchmark.

Reuses the approved config and existing direct smoke receipt from a manifest.
Default: one small principal turn and one intended worker call, synthetic input
only. No benchmark runs automatically. --offline checks the MCP handshake only.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from codex_io_ab import (read_json, write_json, load_config, config_hash, engine_hash,
                        bridge_hash, usage_from_jsonl, transport_diagnostics, mcp_scope)
from worker_mcp import codex_mcp_arguments, SERVER, json_bytes
from worker_runtime import run_process, codex_stream


def check_config(manifest):
    workers = [s for s in manifest['strategies'] if s.get('worker_mode', 'disabled') != 'disabled']
    if len(workers) != 1:
        raise ValueError('Select a manifest with exactly one approved worker strategy')
    strategy = workers[0]
    config = Path(strategy['worker_config']).resolve(strict=True)
    cfg = load_config(str(config))
    direct = read_json(strategy['worker_receipt'])
    if (direct.get('ok') is not True or direct.get('config_sha256') != config_hash(config)
        or direct.get('engine_sha256') != engine_hash()):
        raise ValueError('Existing direct smoke receipt is missing or stale; no model was called')
    for task in manifest['tasks']:
        mcp_scope(task, strategy)
    return strategy, config, cfg


def server_command(overrides):
    settings = dict(x.split('=', 1) for x in overrides[1::2])
    return [json.loads(settings[f'mcp_servers.{SERVER}.command']),
            *json.loads(settings[f'mcp_servers.{SERVER}.args'])]


def handshake(overrides):
    messages = [
        {'jsonrpc':'2.0','id':1,'method':'initialize','params':{
            'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'io-preflight','version':'1'}}},
        {'jsonrpc':'2.0','method':'notifications/initialized'},
        {'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}}]
    cp = run_process(server_command(overrides), payload=b''.join(json_bytes(m)+b'\n' for m in messages),
                     timeout=20, max_output=100_000)
    try:
        replies = [json.loads(line) for line in cp.stdout.splitlines()]
        listed = next(r['result']['tools'] for r in replies if r.get('id') == 2)
        ok = cp.returncode == 0 and not cp.timed_out and not cp.oversized and [t['name'] for t in listed] == ['bulk_read']
    except (ValueError, KeyError, StopIteration, TypeError):
        ok = False
    return ok, cp


def probe_result(cp, folder, config, nonce):
    audit_journal = folder/'worker-audit/.io-delegation/worker-events.jsonl'
    if audit_journal.is_file():
        shutil.copy2(audit_journal, folder/'worker-events.jsonl')
    u = usage_from_jsonl(folder/'codex.jsonl', folder/'worker-events.jsonl')
    diagnostic = transport_diagnostics(folder/'codex.jsonl')
    ok = (cp.returncode == 0 and not cp.timed_out and not cp.oversized and
          u['principal_usage_complete'] and u['worker_accounting_complete'] and
          u['worker_calls'] == 1 and u['worker_accepted'] == 1 and
          diagnostic['worker_mcp_tool_calls'] == 1 and nonce in cp.stdout.decode('utf-8', errors='replace'))
    return dict(ok=ok, transport='mcp', synthetic_only=True,
                config_sha256=config_hash(config), engine_sha256=engine_hash(),
                bridge_sha256=bridge_hash(), stats=u, diagnostic=diagnostic,
                error=None if ok else 'Boundary probe failed; no A/B started. Inspect this folder, not another full run.')


def next_manifest(original, folder):
    updated = copy.deepcopy(original)
    updated['repetitions'] = 1
    updated['experiment'] = 'MCP boundary verified; forced integration path, not an economic optimum'
    for strategy in updated['strategies']:
        if strategy.get('worker_mode', 'disabled') != 'disabled':
            strategy['worker_transport'] = 'mcp'
            strategy['worker_boundary_receipt'] = str(folder/'boundary-receipt.json')
            strategy['name'] = 'skill-worker-mcp-'+strategy['worker_mode']
    destination = folder/'manifest.mcp.json'
    write_json(destination, updated)
    return destination


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('manifest', type=Path)
    ap.add_argument('--offline', action='store_true', help='Handshake only; ZERO model calls, no live receipt')
    ap.add_argument('--run', action='store_true', help='Only after a passing boundary probe, run one A/B repetition')
    args = ap.parse_args(argv)
    if args.offline and args.run:
        raise ValueError('--offline cannot start a benchmark')
    source = args.manifest.resolve(strict=True)
    manifest = read_json(source)
    strategy, config, cfg = check_config(manifest)
    executable = shutil.which('codex.exe') or shutil.which('codex')
    if not args.offline and not executable:
        raise ValueError('Codex CLI is not available; no model was called')
    folder = source.parent/('mcp-check-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    folder.mkdir()
    (folder/'.gitignore').write_text('*\n', encoding='utf-8')
    audit = folder/'worker-audit'; audit.mkdir()
    with tempfile.TemporaryDirectory(prefix='io-mcp-boundary-') as temporary:
        root = Path(temporary)
        nonce = uuid.uuid4().hex[:16]
        (root/'probe.txt').write_text('WORKER_CHECK = "'+nonce+'"\n', encoding='utf-8')
        overrides = codex_mcp_arguments(root, config, audit, files=['probe.txt'])
        good, pre = handshake(overrides)
        write_json(folder/'handshake.json', {'ok':good,'model_calls':0})
        (folder/'handshake.stderr.txt').write_bytes(pre.stderr)
        if not good:
            print('MCP initialization failed before any model call. Artifacts: '+str(folder)); return 2
        if args.offline:
            print(json.dumps({'handshake_ok':True,'model_calls':0,'live_boundary_verified':False,'artifacts':str(folder)})); return 0
        print('Testing Codex -> MCP -> worker: one principal turn and one intended worker call. Synthetic data only.', flush=True)
        prompt = ('Use the io_delegation MCP bulk_read tool exactly ONCE with paths ["probe.txt"] and question '
                  '"What is the literal value of WORKER_CHECK? Include the exact assignment as evidence and the value in fact." '
                  'Do not read files yourself. Do not use shell, Python, memory, other MCP servers or subagents. '
                  'If the tool is unavailable or fails, stop immediately without retrying. Otherwise answer only with the value.')
        command = [executable,'exec','--json','--skip-git-repo-check','-C',str(root),'-s','workspace-write',
                   '-m', strategy.get('model', cfg.get('model', 'gpt-5.6-luna')),
                   '-c','model_reasoning_effort="medium"','-c','features.shell_tool=false',
                   '-c','features.unified_exec=false','-c','features.multi_agent=false','-c','features.memories=false',
                   *overrides,'-']
        write_json(folder/'command.json', command)
        cp = run_process(command, cwd=root, payload=prompt.encode('utf-8'),
                         timeout=cfg.get('timeout_seconds',120)+120, max_output=2_000_000)
        (folder/'codex.jsonl').write_bytes(cp.stdout)
        (folder/'codex.stderr.txt').write_bytes(cp.stderr)
        receipt = probe_result(cp, folder, config, nonce)
        write_json(folder/'boundary-receipt.json', receipt)
    print(json.dumps(receipt, indent=2), flush=True)
    print('Artifacts: '+str(folder), flush=True)
    if not receipt['ok']:
        return 2
    prepared = next_manifest(manifest, folder)
    print('Boundary verified. Prepared comparison (not started):\npython "'+str(HERE/'codex_io_ab.py')+
          '" "'+str(prepared)+'" --output "'+str(folder/'ab')+'"', flush=True)
    if args.run:
        from codex_io_ab import main as benchmark
        return benchmark([str(prepared),'--output',str(folder/'ab')])
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print('Stopped: '+str(exc), file=sys.stderr)
        raise SystemExit(2)
