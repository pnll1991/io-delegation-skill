#!/usr/bin/env python3
"""Verify local MCP or the authenticated Codex boundary with synthetic data only.

Default calls zero models. --live permits one principal turn and, with --config,
one intended worker call. Does not launch an A/B, change global config or copy keys.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
SCRIPTS=Path(__file__).resolve().parents[2]/'skills/io-delegation/scripts'
sys.path.insert(0,str(SCRIPTS))
from context_mcp import codex_arguments,SERVER
from context_cache import implementation_hash
from context_sources import encoded
from context_telemetry import combined
from worker_runtime import run_process


def handshake(args):
    settings={args[i+1].split('=',1)[0]:json.loads(args[i+1].split('=',1)[1]) for i in range(0,len(args),2)}
    command=[settings[f'mcp_servers.{SERVER}.command'],*settings[f'mcp_servers.{SERVER}.args']]
    request=[dict(jsonrpc='2.0',id=1,method='initialize',params=dict(protocolVersion='2025-06-18',capabilities={},clientInfo=dict(name='context-check',version='1'))),
             dict(jsonrpc='2.0',method='notifications/initialized'),dict(jsonrpc='2.0',id=2,method='tools/list',params={})]
    cp=run_process(command,payload=b''.join(encoded(x)+b'\n' for x in request),timeout=20)
    try:
        responses=[json.loads(x) for x in cp.stdout.splitlines()]
        names=[x['name'] for x in next(x['result']['tools'] for x in responses if x.get('id')==2)]
        ok=cp.returncode==0 and not cp.timed_out and not cp.oversized and names in (['search','extract'],['search','extract','semantic_query'])
    except (ValueError,KeyError,StopIteration,TypeError):ok=False;names=[]
    return dict(ok=ok,model_calls=0,tools=names),cp


def verify(output,model='gpt-5.6-luna',config=None,live=False):
    output=Path(output).resolve()
    if output.exists() and any(output.iterdir()):raise ValueError('Use a new output directory')
    output.mkdir(parents=True,exist_ok=True);(output/'.gitignore').write_text('*\n')
    audit=output/'audit';audit.mkdir()
    with tempfile.TemporaryDirectory(prefix='context-boundary-') as temp:
        root=Path(temp);nonce=uuid.uuid4().hex[:16]
        (root/'probe.json').write_bytes(encoded(dict(value=nonce))+b'\n')
        (root/'probe.txt').write_text('VALUE = '+nonce+'\n',encoding='utf-8')
        args=codex_arguments(root,audit,files=['probe.json','probe.txt'],config=config)
        result,cp=handshake(args);(output/'handshake.json').write_bytes(encoded(result)+b'\n')
        (output/'handshake.stderr.txt').write_bytes(cp.stderr)
        if not result['ok'] or not live:
            result.update(live_boundary_verified=False,synthetic_only=True)
            return result
        exe=shutil.which('codex.exe') or shutil.which('codex')
        if not exe:raise ValueError('Codex CLI unavailable; no model called')
        prompt=('Call io_context.extract exactly once with paths ["probe.json"] and projection {"kind":"json","pointers":["/value"]}. '
                'Answer only the extracted value. Do not use shell, memory or other tools; stop on tool failure.')
        if config:
            prompt=('Call io_context.semantic_query exactly once with selections [{"path":"probe.txt","select":{"kind":"lines","start":1,"end":1}}] '
                    'and question "What is the value of VALUE? Cite the assignment literally and include the value in fact." '
                    'Answer only its value. Do not read files, use shell, memory or other tools. Stop on tool failure, no retry.')
        cmd=[exe,'exec','--json','--ephemeral','--ignore-user-config','--disable','apps','--disable','plugins','--skip-git-repo-check','-C',str(root),'-s','workspace-write','-m',model,
             '-c','model_reasoning_effort="medium"','-c','web_search="disabled"','-c','features.shell_tool=false','-c','features.unified_exec=false',
             '-c','features.multi_agent=false','-c','features.memories=false','-c','skills.include_instructions=false']
        if sys.platform == 'win32': cmd += ['-c','windows.sandbox="elevated"']
        cmd += [*args,'-']
        (output/'command.json').write_bytes(encoded(cmd)+b'\n')
        cp=run_process(cmd,cwd=root,payload=prompt.encode(),timeout=300,max_output=2_000_000)
        (output/'codex.jsonl').write_bytes(cp.stdout);(output/'codex.stderr.txt').write_bytes(cp.stderr)
        usage=combined(output/'codex.jsonl',audit)
        passed=(cp.returncode==0 and not cp.timed_out and not cp.oversized and usage['system_accounting_complete'] and
                usage['main']['command_items']==0 and usage['main']['mcp_items']==1 and nonce in cp.stdout.decode('utf-8',errors='replace') and
                (usage['worker']['calls']==1 and usage['worker']['accepted']==1 if config else usage['worker']['calls']==0))
        result=dict(ok=passed,live_boundary_verified=passed,synthetic_only=True,context_code=implementation_hash(),
                    configuration_sha256=hashlib.sha256(Path(config).read_bytes()).hexdigest() if config else None,
                    stats=usage,model=model,no_benchmark_started=True)
        (output/'receipt.json').write_bytes(encoded(result)+b'\n')
        return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--model',default='gpt-5.6-luna')
    p.add_argument('--config',type=Path);p.add_argument('--live',action='store_true')
    a=p.parse_args(argv);result=verify(a.output,a.model,a.config,a.live);print(json.dumps(result,indent=2))
    return 0 if result['ok'] else 2


if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,KeyError) as exc:
        print('Verification stopped: '+type(exc).__name__,file=sys.stderr);raise SystemExit(2)
