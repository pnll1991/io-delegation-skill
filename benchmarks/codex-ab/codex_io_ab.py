#!/usr/bin/env python3
"""Reproducible Codex A/B: quality, raw tokens, workers and optional explicit pricing.

No implicit price ratios, no missing-usage-as-zero, no automatic deletion of results.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import time

SCRIPTS = Path(__file__).resolve().parents[2] / 'skills/io-delegation/scripts'
sys.path.insert(0, str(SCRIPTS))
from worker_runtime import USAGE_KEYS, codex_stream, normalize_usage, run_process, usage_complete
from io_delegate import load_config


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def git(repo, *args):
    cp = subprocess.run(['git', '-c', 'core.quotepath=false', *args], cwd=repo,
                        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
    if cp.returncode:
        raise RuntimeError('git failed: ' + cp.stderr[-1500:])
    return cp.stdout


def engine_hash():
    h = hashlib.sha256()
    for name in ('io_delegate.py', 'worker_runtime.py'):
        h.update((SCRIPTS/name).read_bytes())
    return h.hexdigest()


def config_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_events(path):
    events, malformed = [], 0
    if not Path(path).is_file():
        return events, malformed
    with Path(path).open(encoding='utf-8-sig', errors='replace') as f:
        for line in f:
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                if not isinstance(e, dict):
                    raise ValueError()
                events.append(e)
            except ValueError:
                malformed += 1
    return events, malformed


def worker_usage(events, malformed=0):
    grouped, seen = {}, set()
    for e in events:
        if e.get('schema') != 'io-worker/v2' or not isinstance(e.get('call_id'), str):
            malformed += 1
            continue
        identity = (e['call_id'], e.get('seq'))
        if identity in seen:
            continue
        seen.add(identity)
        grouped.setdefault(e['call_id'], []).append(e)
    result = dict(worker_attempts=len(grouped), worker_calls=0, worker_responses=0,
                  worker_accepted=0, worker_failures=0, worker_usage_unknown_calls=0,
                  worker_input_tokens=0, worker_output_tokens=0, worker_cached_input_tokens=0,
                  worker_cache_write_input_tokens=0, worker_reasoning_output_tokens=0,
                  worker_accounting_complete=malformed == 0, worker_records_invalid=malformed)
    for es in grouped.values():
        names = [e.get('event') for e in es]
        dispatched = 'worker_dispatched' in names
        result['worker_calls'] += dispatched
        responses = [e for e in es if e.get('event') == 'worker_response']
        result['worker_responses'] += bool(responses)
        accepted = any(e.get('event') == 'worker_completed' and e.get('accepted') is True for e in es)
        result['worker_accepted'] += accepted
        result['worker_failures'] += not accepted
        if 'worker_attempt' not in names or len(set(names)) != len(names):
            result['worker_accounting_complete'] = False
        if 'worker_error' not in names and 'worker_completed' not in names:
            result['worker_accounting_complete'] = False
        if dispatched:
            u = normalize_usage(responses[-1].get('usage') if responses else None)
            if not usage_complete(u):
                result['worker_usage_unknown_calls'] += 1
                result['worker_accounting_complete'] = False
            else:
                result['worker_input_tokens'] += u['input_tokens']
                result['worker_output_tokens'] += u['output_tokens']
            for k in ('cached_input_tokens','cache_write_input_tokens','reasoning_output_tokens'):
                key='worker_'+k
                result[key] = result[key]+u[k] if result[key] is not None and u[k] is not None else None
    return result


def usage_from_jsonl(path: Path, journal_path=None):
    raw = path.read_bytes() if path.exists() else b''
    u, completed, _, _ = codex_stream(raw)
    events, malformed = load_events(path)
    commands = set(); mcp = set(); agents = set(); tools = set(); legacy = []; inline = []
    for e in events:
        item = e.get('item') or {}
        if not isinstance(item, dict) or e.get('type') != 'item.completed':
            continue
        typ, iid = item.get('type'), item.get('id')
        iid = iid or str(len(tools))
        if typ == 'command_execution':
            commands.add(iid)
            for line in (item.get('aggregated_output') or '').splitlines():
                try: w = json.loads(line)
                except ValueError: continue
                if not isinstance(w, dict): continue
                if w.get('schema') == 'io-worker/v2': inline.append(w)
                elif w.get('event') == 'worker_response': legacy.append(w)
        if typ == 'mcp_tool_call': mcp.add(iid)
        if typ == 'collab_tool_call': agents.add(iid)
        if typ not in (None, 'agent_message', 'reasoning', 'todo_list'): tools.add(iid)
    journal, journal_errors = load_events(journal_path) if journal_path else ([], 0)
    # Prefer durable records; merge by call_id+seq to avoid counting stderr twice.
    wu = worker_usage(journal+inline, journal_errors)
    if legacy or agents:
        # Legacy terminal-only counters and native subagent events cannot prove full billing.
        wu['worker_accounting_complete'] = False
    u.update(wu, principal_usage_complete=completed and malformed == 0,
             command_items=len(commands), tool_items=len(tools), mcp_items=len(mcp),
             subagent_items=len(agents), legacy_worker_responses=len(legacy))
    return u


def price(usage, rates):
    """USD only with explicit, complete model rates; never guess plan consumption."""
    if not rates or not usage_complete(usage): return None
    keys = ('input_per_million','cached_input_per_million','cache_write_per_million','output_per_million')
    if any(type(rates.get(k)) not in (int,float) or not math.isfinite(rates[k]) or rates[k]<0 for k in keys):
        return None
    cached, writes = usage.get('cached_input_tokens'), usage.get('cache_write_input_tokens')
    if cached is None or writes is None or cached+writes > usage['input_tokens']: return None
    if writes and rates.get('cache_write_accounting') != 'exclusive_subset_of_input': return None
    return ((usage['input_tokens']-cached-writes)*rates['input_per_million'] +
            cached*rates['cached_input_per_million'] + writes*rates['cache_write_per_million'] +
            usage['output_tokens']*rates['output_per_million'])/1_000_000


def format_value(value, ctx):
    # Literal replacement only: Python/JSON braces in commands must stay intact.
    for k,v in ctx.items(): value = str(value).replace('{'+k+'}', str(v))
    return value


def check_commands(worktree, commands, timeout, ctx):
    rows = []
    for entry in commands:
        if isinstance(entry, str):
            command = format_value(entry,ctx)
            argv = ['cmd.exe','/d','/c',command] if os.name == 'nt' else ['/bin/sh','-c',command]
        else:
            argv = [format_value(x,ctx) for x in entry]
            command = argv
        try:
            cp = run_process(argv,cwd=worktree,timeout=timeout)
            ok = cp.returncode == 0 and not cp.timed_out and not cp.oversized
            rows.append(dict(command=command,ok=ok,exit_code=cp.returncode,
                             timed_out=cp.timed_out,
                             stdout_tail=cp.stdout.decode('utf-8',errors='replace')[-6000:],
                             stderr_tail=cp.stderr.decode('utf-8',errors='replace')[-6000:]))
        except OSError as exc:
            rows.append(dict(command=command,ok=False,error=type(exc).__name__))
    return bool(rows) and all(x['ok'] for x in rows), rows


def prepare(manifest, manifest_path, strategies, tasks):
    repo = Path(manifest['repo']).expanduser().resolve(strict=True)
    if not shutil.which('git') or not shutil.which('codex'):
        raise ValueError('git and codex must be installed')
    if not strategies or not tasks: raise ValueError('empty task/strategy selection')
    if int(manifest.get('repetitions',1)) < 1: raise ValueError('invalid repetitions')
    pinned = {t['id']:git(repo,'rev-parse',str(t.get('base_commit',manifest.get('base_commit','HEAD')))+'^{commit}').strip() for t in tasks}
    for t in tasks:
        if not t.get('user_prompt') or not t.get('acceptance') or not t.get('regression'):
            raise ValueError('Each task needs a prompt, acceptance and regression checks')
    for s in strategies:
        if 'worker' in s['name'] and 'worker_mode' not in s:
            raise ValueError('Worker strategy must explicitly set worker_mode and worker_config')
        if s.get('worker_mode','disabled') not in ('disabled','optional','required'):
            raise ValueError('invalid worker_mode')
        if s.get('worker_mode','disabled') != 'disabled':
            path = Path(s.get('worker_config',''))
            if not path.is_file(): raise ValueError('Worker strategy without an approved worker_config')
            cfg = load_config(str(path))
            receipt = read_json(s['worker_receipt'])
            if (receipt.get('ok') is not True or receipt.get('config_sha256') != config_hash(path)
                or receipt.get('engine_sha256') != engine_hash()):
                raise ValueError('Worker smoke receipt missing, failed or stale; run setup_worker.py first')
            if cfg['adapter']=='codex-cli' and not shutil.which(cfg.get('executable','codex')):
                raise ValueError('Worker Codex executable not found')
    # Environment is recorded, not silently erased. This benchmark measures current-user Codex.
    return repo,pinned


def aggregate(rows):
    result=[]
    for name in sorted({r['strategy'] for r in rows}):
        rs=[r for r in rows if r['strategy']==name]; n=sum(r['valid_success'] for r in rs)
        complete=all(r['principal_usage_complete'] and r['worker_accounting_complete'] for r in rs)
        principal=sum(r['raw_tokens'] or 0 for r in rs) if all(r['raw_tokens'] is not None for r in rs) else None
        system=sum(r['system_raw_tokens'] or 0 for r in rs) if complete else None
        result.append(dict(strategy=name,runs=len(rs),valid_successes=n,valid_success_rate=n/len(rs),
          comparison_valid=all(r['comparison_valid'] for r in rs),
          principal_token_CPTS=principal/n if n and principal is not None else None,
          system_token_CPTS=system/n if n and system is not None else None,
          system_accounting_complete=complete,
          principal_CPTS_usd=sum(r['principal_cost_usd'] for r in rs)/n
              if n and all(r['principal_cost_usd'] is not None for r in rs) else None,
          system_CPTS_usd=sum(r['system_cost_usd'] for r in rs)/n
              if n and all(r.get('system_cost_usd') is not None for r in rs) else None,
          worker_attempts=sum(r['worker_attempts'] for r in rs),worker_calls=sum(r['worker_calls'] for r in rs),
          worker_accepted=sum(r['worker_accepted'] for r in rs),worker_failures=sum(r['worker_failures'] for r in rs),
          wall_seconds=sum(r['wall_seconds'] for r in rs)))
    return result


def save_results(out, rows):
    write_json(out/'runs.json',rows); a=aggregate(rows); write_json(out/'aggregate.json',a)
    if rows:
        with (out/'runs.csv').open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    lines=['# Codex worker A/B - raw reported tokens, NOT currency','',
           '| Strategy | Success | Main tokens/success | System tokens/success | Worker attempts/calls/accepted | Comparable |',
           '|---|---:|---:|---:|---:|---|']
    f=lambda x:'unknown' if x is None else f'{x:,.0f}'
    for r in a:
        lines.append(f"| {r['strategy']} | {r['valid_successes']}/{r['runs']} | {f(r['principal_token_CPTS'])} | {f(r['system_token_CPTS'])} | {r['worker_attempts']}/{r['worker_calls']}/{r['worker_accepted']} | {r['comparison_valid']} |")
    lines+=['','Cached input is a subset of input. Reasoning is a subset of output. Neither is added twice.',
            'Unknown/failed usage is NOT zero. Retries and rejected responses count.',
            'No dollar or subscription-quota saving is claimed without verified prices.',
            'Hook installation alone is not proof of execution. Native subagent usage is not fully attributed.',
            'Results measure this task and current-user configuration; they are not a clean-room or universal benchmark.']
    (out/'summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def safe_capture(worktree, relative, dest):
    rel=Path(relative)
    if rel.is_absolute() or '..' in rel.parts: raise ValueError('unsafe capture path')
    src=worktree/rel
    if src.is_symlink(): raise ValueError('symlinked capture')
    src.resolve().relative_to(worktree.resolve())
    if src.is_file() and src.stat().st_size <= 8_000_000:
        shutil.copy2(src,dest)
        return True
    return False


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('manifest',type=Path); ap.add_argument('--output',type=Path,default=Path('codex-io-ab-results'))
    ap.add_argument('--seed',type=int,default=20260914); ap.add_argument('--strategy',action='append'); ap.add_argument('--task',action='append')
    ap.add_argument('--preflight',action='store_true',help='Validate inputs without model calls')
    args=ap.parse_args(argv); mp=args.manifest.resolve(); m=read_json(mp)
    strategies=[s for s in m['strategies'] if not args.strategy or s['name'] in args.strategy]
    tasks=[t for t in m['tasks'] if not args.task or t['id'] in args.task]
    repo,pinned=prepare(m,mp,strategies,tasks)
    if args.preflight:
        print(json.dumps({'ok':True,'model_calls':0,'pinned_commits':pinned})); return 0
    out=args.output.resolve()
    if out.exists() and any(out.iterdir()): raise ValueError('Output directory is not empty; use a NEW path')
    out.mkdir(parents=True,exist_ok=True); (out/'runs').mkdir(exist_ok=True)
    write_json(out/'manifest.snapshot.json',m)
    write_json(out/'environment.json',dict(python=sys.version,platform=sys.platform,pinned_commits=pinned,
        codex_version=run_process([shutil.which('codex'),'--version'],timeout=15).stdout.decode(errors='replace').strip(),
        configuration='current user; not isolated from global instructions',engine_sha256=engine_hash()))
    plan=[(t,s,r) for t in tasks for s in strategies for r in range(1,int(m.get('repetitions',1))+1)]
    random.Random(args.seed).shuffle(plan); rows=[]
    with tempfile.TemporaryDirectory(prefix='codex-io-ab-') as temp:
        for n,(task,strategy,rep) in enumerate(plan,1):
            rid=f'{n:03d}-{task["id"]}-{strategy["name"]}-r{rep}'
            # IDs from local manifests are trusted but must not escape the output directory.
            if '/' in rid or '\\' in rid or '..' in rid: raise ValueError('unsafe run id')
            rd=out/'runs'/rid; rd.mkdir(); wt=Path(temp)/rid
            git(repo,'worktree','add','--detach',str(wt),pinned[task['id']])
            try:
                for rel in m.get('forbid_base_paths',[]):
                    if (wt/rel).exists(): raise ValueError('Contaminated base path: '+rel)
                # Make only telemetry invisible to status; do not touch user/global ignores.
                telemetry=wt/'.io-delegation'; telemetry.mkdir(exist_ok=True)
                if telemetry.is_symlink(): raise ValueError('symlinked telemetry directory')
                (telemetry/'.gitignore').write_text('*\n',encoding='utf-8')
                ctx=dict(worktree=str(wt),repo=str(repo),python=sys.executable,manifest_dir=str(mp.parent),
                         io_delegation_root=str(Path(__file__).resolve().parents[2]))
                ctx.update(m.get('variables',{}))
                commands=strategy.get('setup_commands',[])
                if commands:
                    ok,rr=check_commands(wt,commands,180,ctx); write_json(rd/'setup.json',rr)
                    if not ok: raise ValueError('Strategy setup failed; see setup.json')
                mode=strategy.get('worker_mode','disabled')
                worker_cfg=None
                if mode!='disabled':
                    worker_cfg=load_config(strategy['worker_config'])
                    # Deliberately keep approved config outside the commit, under ignored runtime.
                    shutil.copy2(strategy['worker_config'],telemetry/'worker.local.json')
                git(wt,'add','-A')
                git(wt,'-c','user.name=Codex Benchmark','-c','user.email=bench@example.invalid',
                    'commit','--allow-empty','--no-gpg-sign','-m','benchmark setup')
                setup_sha=git(wt,'rev-parse','HEAD').strip()
                prompt='\n\n'.join(x for x in (strategy.get('prompt_prefix',''),task['user_prompt']) if x)
                if mode!='disabled':
                    prompt += ('\nAn approved worker is configured at .io-delegation/worker.local.json. '
                               'Use .agents/skills/io-delegation/scripts/io_delegate.py with --config for factual extraction. '
                               'Use this verified Python executable: '+sys.executable+'. '
                               'No recursive delegation. Preserve existing files and verify evidence.')
                    if mode=='required':
                        prompt += '\nThis is a delegated-path test: make at least ONE successful bulk-read call, with literal evidence, before producing the answer.'
                else:
                    prompt += '\nDo not invoke external workers or native subagents in this control arm.'
                cmd=[shutil.which('codex'),'exec','--json','-C',str(wt),'-s',strategy.get('sandbox','workspace-write')]
                if strategy.get('model'): cmd+=['-m',strategy['model']]
                for ov in strategy.get('config_overrides',[]): cmd+=['-c',str(ov)]
                cmd+=['-']; write_json(rd/'command.json',cmd); (rd/'prompt.txt').write_text(prompt,encoding='utf-8')
                started=time.monotonic(); cp=run_process(cmd,cwd=wt,payload=prompt.encode('utf-8'),timeout=task.get('max_wall_seconds',m.get('max_wall_seconds',900)),max_output=16_000_000)
                wall=time.monotonic()-started
                (rd/'codex.jsonl').write_bytes(cp.stdout); (rd/'codex.stderr.txt').write_bytes(cp.stderr)
                journal=telemetry/'worker-events.jsonl'
                if journal.exists(): shutil.copy2(journal,rd/'worker-events.jsonl')
                u=usage_from_jsonl(rd/'codex.jsonl',rd/'worker-events.jsonl')
                aok,ar=check_commands(wt,task['acceptance'],900,ctx)
                rok,rr=check_commands(wt,task['regression'],900,ctx)
                write_json(rd/'acceptance.json',ar); write_json(rd/'regression.json',rr)
                (rd/'git-status.txt').write_text(git(wt,'status','--porcelain=v1','--untracked-files=all'),encoding='utf-8')
                (rd/'patch.diff').write_text(git(wt,'diff',setup_sha,'--binary'),encoding='utf-8')
                for i,rel in enumerate(task.get('capture_paths',[])):
                    safe_capture(wt,rel,rd/f'captured-{i}-{Path(rel).name}')
                hook_path=wt/'.io-delegation-hooks/events.jsonl'
                hook_events,_=load_events(hook_path)
                if hook_path.exists(): shutil.copy2(hook_path,rd/'hook-events.jsonl')
                hooks_observed=any(e.get('host_event') is True for e in hook_events)
                needs_hooks=strategy.get('requires_hooks',False) or 'hooks' in strategy['name']
                contract_ok=(mode!='required' or u['worker_accepted']>0)
                valid=cp.returncode==0 and not cp.timed_out and not cp.oversized and aok and rok and contract_ok
                raw=u['input_tokens']+u['output_tokens'] if u['principal_usage_complete'] else None
                sysraw=raw+u['worker_input_tokens']+u['worker_output_tokens'] if raw is not None and u['worker_accounting_complete'] else None
                main_usd=price(u,m.get('pricing',{}).get(strategy.get('model')))
                wu={k:u.get('worker_'+k) for k in USAGE_KEYS}
                worker_usd=price(wu,m.get('pricing',{}).get((worker_cfg or {}).get('model'))) if u['worker_calls'] else 0
                system_usd=main_usd+worker_usd if main_usd is not None and worker_usd is not None and u['worker_accounting_complete'] else None
                row=dict(task=task['id'],strategy=strategy['name'],repetition=rep,base_commit=pinned[task['id']],
                    valid_success=valid,comparison_valid=valid and u['principal_usage_complete'] and u['worker_accounting_complete'] and (not needs_hooks or hooks_observed),
                    worker_mode=mode,worker_contract_ok=contract_ok,hooks_observed=hooks_observed,
                    codex_exit_code=cp.returncode,acceptance_ok=aok,regression_ok=rok,wall_seconds=wall,
                    raw_tokens=raw,system_raw_tokens=sysraw,principal_cost_usd=main_usd,worker_cost_usd=worker_usd,system_cost_usd=system_usd,**u)
                rows.append(row); save_results(out,rows)
                print(f'[{n}/{len(plan)}] {strategy["name"]} success={valid} input={u["input_tokens"]} worker_attempts={u["worker_attempts"]} worker_calls={u["worker_calls"]} worker_accepted={u["worker_accepted"]}',flush=True)
            finally:
                # Captured reports remain outside the temporary worktree.
                git(repo,'worktree','remove','--force',str(wt))
    print(json.dumps(aggregate(rows),indent=2)); return 0


if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print('Benchmark stopped: '+str(exc),file=sys.stderr); raise SystemExit(2)
