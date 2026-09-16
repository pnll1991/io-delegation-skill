#!/usr/bin/env python3
"""A=current Codex; B=deterministic MCP; C=same MCP+optional approved semantics.

Default/preflight calls NO models. --live explicitly authorizes inference.
Validators and manifests are operator-controlled trusted code. Never expose their
contents to the model, erase previous results, force a worker call, or infer money.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

SCRIPTS=Path(__file__).resolve().parents[2]/'skills/io-delegation/scripts'
sys.path.insert(0,str(SCRIPTS))
from context_cache import implementation_hash
from context_engine import safe_config
from context_mcp import ContextService, codex_arguments
from context_sources import SourceScope, encoded
from context_telemetry import combined
from worker_runtime import run_process

ARMS=('baseline','deterministic','semantic-optional')


def write(path,value):Path(path).write_bytes(encoded(value)+b'\n')


def git(root,*args):
    cp=subprocess.run(['git',*map(str,args)],cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
    if cp.returncode:raise ValueError('Git operation failed: '+cp.stderr[-1200:])
    return cp.stdout.strip()


def checks(root,commands,ctx):
    result=[]
    for command in commands:
        if not isinstance(command,list) or not command or any(not isinstance(a,str) for a in command):
            raise ValueError('Checks must be explicit argv arrays, not shell strings')
        args=[]
        for arg in command:
            for k,v in ctx.items():arg=arg.replace('{'+k+'}',str(v))
            args.append(arg)
        try:
            cp=run_process(args,cwd=root,timeout=60,max_output=100_000)
            result.append(dict(ok=cp.returncode==0 and not cp.timed_out and not cp.oversized,
                exit_code=cp.returncode,stdout=cp.stdout.decode('utf-8',errors='replace'),
                stderr=cp.stderr.decode('utf-8',errors='replace')))
        except OSError as exc:result.append(dict(ok=False,error=type(exc).__name__))
    return bool(result) and all(r['ok'] for r in result),result


def prepare(manifest):
    if manifest.get('version')!=1:raise ValueError('Unsupported experiment version')
    root=Path(manifest['repo']).resolve(strict=True)
    if not shutil.which('git'):raise ValueError('Git required')
    sha=git(root,'rev-parse',str(manifest['base_commit'])+'^{commit}')
    reps=manifest.get('repetitions',1)
    if type(reps) is not int or not 1<=reps<=10:raise ValueError('repetitions must be 1..10')
    arms=manifest.get('arms',list(ARMS))
    if not arms or len(set(arms))!=len(arms) or set(arms)-set(ARMS):raise ValueError('Invalid arms')
    if not manifest.get('model'):raise ValueError('Pin the principal model explicitly')
    if 'semantic-optional' in arms:
        if not manifest.get('worker_config'):raise ValueError('C needs an approved worker config')
        safe_config(manifest['worker_config'],root)
    tasks=manifest.get('tasks',[])
    if not tasks or len(tasks)>10:raise ValueError('Use 1..10 predeclared tasks')
    seen=set()
    for t in tasks:
        name=t.get('id','')
        if not name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in name) or name in seen:
            raise ValueError('Unique safe task ids required')
        seen.add(name)
        if not t.get('prompt') or not t.get('acceptance') or not t.get('regression'):raise ValueError('Prompt and both validators required')
        SourceScope(root,t.get('allow_prefixes',[]),t.get('allow_files',[]))
        for field in ('acceptance','regression'):
            for cmd in t[field]:
                if not isinstance(cmd,list) or not cmd or any(not isinstance(x,str) for x in cmd):raise ValueError('argv validator arrays required')
        for out in t.get('outputs',[]):
            from worker_mcp import relative_name
            relative_name(out)
    return root,sha,arms


def validator_hashes(tasks):
    hashes = {}
    for task in tasks:
        for kind in ('acceptance','regression'):
            for command in task[kind]:
                for value in command:
                    path = Path(value)
                    if path.is_absolute() and path.is_file():
                        if path.stat().st_size > 8_000_000:raise ValueError('Validator dependency too large to freeze')
                        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def comparison(rows,control,candidate,minimum_reduction=0.20):
    left=[r for r in rows if r['arm']==control];right=[r for r in rows if r['arm']==candidate]
    result=dict(control=control,candidate=candidate,target_reduction=minimum_reduction,
                decision='pending',universal_savings_claim=False)
    if not left or not right:return result
    from collections import Counter
    if Counter(r['task'] for r in left)!=Counter(r['task'] for r in right):
        result['reason']='unbalanced task/repetition coverage';return result
    a=summarize(left)[0];b=summarize(right)[0]
    if not a['accounting_complete'] or not b['accounting_complete']:
        result['reason']='unknown usage';return result
    if b['success_rate']<a['success_rate']:
        result.update(decision='reject',reason='quality decreased');return result
    if not a['system_token_CPTS'] or b['system_token_CPTS'] is None:
        result['reason']='no successful-task comparison';return result
    reduction=1-b['system_token_CPTS']/a['system_token_CPTS']
    result.update(observed_reduction=reduction,decision='sample_threshold_met' if reduction>=minimum_reduction else 'not_demonstrated',
                  reason='exploratory sample; confirm with independent tasks and repetitions before adoption')
    return result


def summarize(rows):
    result=[]
    for arm in ARMS:
        part=[r for r in rows if r['arm']==arm]
        if not part:continue
        successes=sum(r['task_success'] for r in part)
        known=all(r['system_accounting_complete'] and r['system_raw_tokens'] is not None for r in part)
        values=[r['system_raw_tokens'] for r in part if r['system_raw_tokens'] is not None]
        total=sum(values) if known else None
        result.append(dict(arm=arm,runs=len(part),successes=successes,success_rate=successes/len(part),
            system_tokens_all_attempts=total,system_token_CPTS=total/successes if total is not None and successes else None,
            mean_tokens=statistics.mean(values) if known else None,median_tokens=statistics.median(values) if known else None,
            sample_stdev=statistics.stdev(values) if known and len(values)>1 else None,
            minimum=min(values) if known else None,maximum=max(values) if known else None,
            accounting_complete=known,worker_calls=sum(r['worker_calls'] for r in part),
            cache_hits=sum(r['cache_hits'] for r in part),seconds=sum(r['seconds'] for r in part)))
    return result


def report(out,rows):
    write(out/'runs.json',rows);summary=summarize(rows);write(out/'summary.json',summary)
    # Task-level comparisons are distinct from a mixture of workloads.
    per_task={task:summarize([r for r in rows if r['task']==task]) for task in sorted({r['task'] for r in rows})}
    write(out/'by-task.json',per_task)
    write(out/'gates.json',[comparison(rows,'baseline','deterministic'),comparison(rows,'deterministic','semantic-optional')])
    lines=['# A/B/C context preparation - reported tokens, not currency','',
       '| Arm | Success | Tokens/success including failed attempts | Worker calls | Local cache hits |',
       '|---|---:|---:|---:|---:|']
    for r in summary:
        n=f"{r['system_token_CPTS']:,.0f}" if r['system_token_CPTS'] is not None else 'unknown'
        lines.append(f"| {r['arm']} | {r['successes']}/{r['runs']} | {n} | {r['worker_calls']} | {r['cache_hits']} |")
    lines+=['','No model savings are inferred from bytes, mocks, cache hits or tool-call counts.',
       'Read by-task.json for distributions. One run is exploratory; no universal conclusions.',
       'Acceptance failures stay in the cost numerator. Unknown usage is not zero.',
       'C may use zero workers. Its benefit over B cannot then be attributed to inference by an auxiliary.',
       'Current-user configuration; native subagent costs and internal inference count may be unavailable.']
    (out/'summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def run(manifest,out,live=False,executor=None):
    root,sha,arms=prepare(manifest);out=Path(out).resolve()
    if out==root or root in out.parents:raise ValueError('Results must be outside project')
    if out.exists() and any(out.iterdir()):raise ValueError('Never overwrite results; select a new directory')
    out.mkdir(parents=True,exist_ok=True);(out/'.gitignore').write_text('*\n',encoding='utf-8')
    frozen=validator_hashes(manifest['tasks'])
    pinned=dict(manifest,base_commit=sha);write(out/'manifest.snapshot.json',pinned)
    write(out/'validator-hashes.json',frozen)
    write(out/'environment.json',dict(python=sys.version,platform=sys.platform,context_code=implementation_hash(),
        configuration='isolated benchmark via --ignore-user-config; managed defaults may still apply',synthetic_driver=executor is not None))
    if not live:
        # Execute real local tools/initialization only; not an authenticated boundary receipt.
        for i,t in enumerate(manifest['tasks']):
            audit=out/f'preflight-{i}';audit.mkdir()
            for arm in arms:
                if arm=='baseline':continue
                ContextService(root,audit,t.get('allow_prefixes',[]),t.get('allow_files',[]),
                               manifest.get('worker_config') if arm=='semantic-optional' else None)
        write(out/'preflight.json',dict(ok=True,model_calls=0,live_boundary_verified=False))
        return []
    codex=manifest.get('codex_command') or [shutil.which('codex.exe') or shutil.which('codex')]
    if not isinstance(codex,list) or not codex or not all(isinstance(x,str) and x for x in codex):raise ValueError('Codex executable unavailable')
    execute=executor or run_process
    plan=[(t,arm,rep) for t in manifest['tasks'] for arm in arms for rep in range(manifest.get('repetitions',1))]
    random.Random(manifest.get('seed',20260916)).shuffle(plan)
    write(out/'plan.json',[dict(task=t['id'],arm=a,repetition=r+1) for t,a,r in plan])
    rows=[]
    with tempfile.TemporaryDirectory(prefix='context-abc-') as temp:
        for index,(task,arm,rep) in enumerate(plan,1):
            if validator_hashes(manifest['tasks']) != frozen:raise ValueError('Validator changed; stopped before another inference')
            rid=f'{index:03d}-{task["id"]}-{arm}-r{rep+1}';rd=out/rid;rd.mkdir();wt=Path(temp)/rid
            git(root,'worktree','add','--detach',wt,sha)
            try:
                audit=rd/'audit';audit.mkdir()
                prompt=task['prompt']+'\nPreserve unrelated files; do not use native subagents or change permissions.'
                cmd=codex+['exec','--json','--ephemeral','--ignore-user-config','--disable','apps','--disable','plugins','-C',str(wt),'-s','workspace-write','-m',manifest['model'],
                    '-c','model_reasoning_effort='+json.dumps(manifest.get('reasoning_effort','medium')),
                    '-c','web_search="disabled"','-c','features.multi_agent=false','-c','features.memories=false',
                    '-c','include_skill_instructions=false']
                if sys.platform == 'win32': cmd += ['-c','windows.sandbox="elevated"']
                if arm!='baseline':
                    config=manifest.get('worker_config') if arm=='semantic-optional' else None
                    cmd+=codex_arguments(wt,audit,task.get('allow_prefixes',[]),task.get('allow_files',[]),config)
                    scope_hint='prefixes='+','.join(task.get('allow_prefixes',[]))+'; files='+','.join(task.get('allow_files',[]))
                    prompt+='\nMCP io_context approved source scope: '+scope_hint+'. If the task names an exact source path, call extract directly with that path; do not search to rediscover it and do not use . or output paths. Use search only when localization is actually needed. For exact HTML/JSON fields prefer one extract call, not one call per field.'
                    if config:prompt+=' semantic_query is optional over explicit selected fragments only; do not call it for routine deterministic extraction.'
                else:prompt+='\nUse local tools; no external auxiliary workers.'
                write(rd/'command.json',cmd+['-']);(rd/'prompt.txt').write_text(prompt,encoding='utf-8')
                started=time.monotonic()
                try:cp=execute(cmd+['-'],cwd=wt,payload=prompt.encode('utf-8'),timeout=min(task.get('timeout',300),900),max_output=16_000_000)
                except OSError as exc:
                    write(rd/'launch-error.json',dict(error=type(exc).__name__,model_usage='unknown'))
                    raise ValueError('Launch failed; inspect preserved artifacts before retrying') from exc
                elapsed=time.monotonic()-started
                (rd/'codex.jsonl').write_bytes(cp.stdout);(rd/'codex.stderr.txt').write_bytes(cp.stderr)
                if validator_hashes(manifest['tasks']) != frozen:raise ValueError('Validator changed; preserve logs and do not rescore silently')
                ctx=dict(root=wt,python=sys.executable,manifest_dir=out)
                aok,ar=checks(wt,task['acceptance'],ctx);rok,rr=checks(wt,task['regression'],ctx)
                write(rd/'acceptance.json',ar);write(rd/'regression.json',rr)
                (rd/'git-status.txt').write_text(git(wt,'status','--porcelain=v1','--untracked-files=all'),encoding='utf-8')
                (rd/'patch.diff').write_text(git(wt,'diff',sha,'--binary'),encoding='utf-8')
                for i,relative in enumerate(task.get('outputs',[])):
                    source=wt/relative
                    from worker_mcp import reject_links
                    if source.is_file():
                        reject_links(source,wt)
                        if source.stat().st_size<=2_000_000:shutil.copy2(source,rd/f'captured-{i}-{source.name}')
                counters=combined(rd/'codex.jsonl',audit)
                write(rd/'accounting.json',counters)
                main=counters['main'];worker=counters['worker'];ops=counters['operations']
                success=cp.returncode==0 and not cp.timed_out and not cp.oversized and aok and rok
                row=dict(task=task['id'],arm=arm,repetition=rep+1,task_success=success,
                    acceptance_ok=aok,regression_ok=rok,exit_code=cp.returncode,seconds=elapsed,
                    system_accounting_complete=counters['system_accounting_complete'],
                    system_raw_tokens=counters['system_raw_tokens'],main_raw_tokens=main['raw_tokens'],
                    worker_raw_tokens=worker['raw_tokens'],worker_calls=worker['calls'],worker_accepted=worker['accepted'],
                    cache_hits=ops['cache_hits'],mcp_items=main['mcp_items'],model_requests=main['model_requests'])
                rows.append(row);report(out,rows)
                print(f'[{index}/{len(plan)}] {task["id"]} {arm} success={success} total={row["system_raw_tokens"]} workers={worker["calls"]}',flush=True)
                if not counters['system_accounting_complete'] or cp.timed_out or cp.oversized:
                    write(out/'stopped.json',dict(reason='Incomplete accounting/transport; no further model calls'))
                    break
            finally:git(root,'worktree','remove','--force',wt)
    return rows


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('manifest',type=Path);p.add_argument('--output',type=Path,required=True)
    mode=p.add_mutually_exclusive_group();mode.add_argument('--live',action='store_true');mode.add_argument('--preflight',action='store_true')
    a=p.parse_args(argv);m=json.loads(a.manifest.read_text(encoding='utf-8-sig'))
    rows=run(m,a.output,live=a.live)
    print(json.dumps(dict(live=a.live,runs=len(rows),output=str(a.output)),ensure_ascii=True))
    return 2 if a.live and (not rows or not all(r['system_accounting_complete'] for r in rows)) else 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
        print('Stopped: '+str(exc),file=sys.stderr);raise SystemExit(2)
