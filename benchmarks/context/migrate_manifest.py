#!/usr/bin/env python3
"""Prepare A/B/C from an existing approved benchmark. No model or global writes."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from evaluate import prepare,write


def migrate(source,output):
    source=Path(source).resolve(strict=True);output=Path(output).resolve()
    if output.exists():raise ValueError('Never overwrite an existing manifest')
    old=json.loads(source.read_text(encoding='utf-8-sig'))
    workers=[s for s in old['strategies'] if s.get('worker_mode','disabled')!='disabled']
    if len(workers)!=1:raise ValueError('Exactly one previously approved worker configuration required')
    worker=workers[0];models={s.get('model') for s in old['strategies']}
    if len(models)!=1 or None in models:raise ValueError('Pin the same principal model for all arms')
    efforts=set()
    for strategy in old['strategies']:
        declared=[x.split('=',1)[1] for x in strategy.get('config_overrides',[]) if x.startswith('model_reasoning_effort=')]
        if len(declared)!=1:raise ValueError('Explicit reasoning effort required in the source manifest')
        effort=json.loads(declared[0])
        if effort not in ('low','medium','high'):raise ValueError('Unsupported effort')
        efforts.add(effort)
    if len(efforts)!=1:raise ValueError('Source strategies use different reasoning effort')
    tasks=[];bases=set()
    for t in old['tasks']:
        base=t.get('base_commit',old.get('base_commit'))
        if not base:raise ValueError('Pinned source commit required')
        bases.add(base)
        prefixes=worker.get('worker_allow_prefixes',t.get('worker_allow_prefixes',[]))
        files=worker.get('worker_allow_files',t.get('worker_allow_files',[]))
        if not prefixes and not files and t['id']=='kuatrometric-html-inventory':prefixes=['herramientas']
        tasks.append(dict(id=t['id'],prompt=t['user_prompt'],allow_prefixes=prefixes,allow_files=files,
             acceptance=t['acceptance'],regression=t['regression'],outputs=t.get('capture_paths',[]),
             timeout=min(t.get('max_wall_seconds',300),900)))
    if len(bases)!=1:raise ValueError('Split experiments with different source commits')
    new=dict(version=1,repo=old['repo'],base_commit=next(iter(bases)),model=next(iter(models)),
             reasoning_effort=next(iter(efforts)),repetitions=1,seed=20260916,arms=['baseline','deterministic','semantic-optional'],
             worker_config=worker['worker_config'],tasks=tasks,origin=str(source),
             live_validation='New context service needs verify_context.py --live before rollout',
             adoption_target=dict(minimum_reduction=0.20,no_quality_drop=True))
    _,sha,_=prepare(new);new['base_commit']=sha
    output.parent.mkdir(parents=True,exist_ok=True);write(output,new)
    return new


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();m=migrate(a.source,a.output)
    print(json.dumps(dict(ok=True,model_calls=0,manifest=str(a.output),arms=m['arms'],original_unchanged=True)))
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,KeyError,TypeError) as exc:
        print('Migration stopped: '+str(exc),file=sys.stderr);raise SystemExit(2)
