#!/usr/bin/env python3
"""Offline preflight for real dogfood manifests, including gold computation at pinned commits."""
from __future__ import annotations

from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import experiment
import run_real
import validators

MIN_FAMILIES={'large-understanding':5,'multi-file-factual':5,'cross-file-behavior':4,'principal':3,'small-control':3}


def validator_namespace(command,root):
    args=list(command)
    def one(flag,default=None):
        return args[args.index(flag)+1] if flag in args else default
    def many(flag):
        return [args[i+1] for i,x in enumerate(args[:-1]) if x==flag]
    mode=one('--mode')
    if not mode: raise ValueError('validator command missing --mode')
    return argparse.Namespace(root=str(root),output=one('--output'),mode=mode,literal=one('--literal'),ext=many('--ext'),field=many('--field'),prefix=one('--prefix'))


def run(manifest):
    experiment.validate_manifest(manifest)
    repos,router,worker=experiment.preflight(manifest)
    counts=Counter(task['family'] for task in manifest['tasks'])
    for family,minimum in MIN_FAMILIES.items():
        if counts[family] < minimum: raise ValueError(f'{family} needs at least {minimum} tasks')
    if len(manifest['tasks']) < 20 or len(repos) < 3: raise ValueError('dogfood requires 20+ tasks across 3+ repositories')
    outputs=set(); golds={}
    with tempfile.TemporaryDirectory(prefix='io-v1-preflight-') as folder:
        roots={}
        try:
            for repo_id,row in repos.items():
                target=Path(folder)/repo_id
                experiment.git(row['path'],'worktree','add','--detach',str(target),row['commit'])
                roots[repo_id]=target
            for task in manifest['tasks']:
                command=task['validator'][0]
                output=command[command.index('--output')+1] if '--output' in command else None
                if not output or output in outputs: raise ValueError('task outputs must be unique')
                outputs.add(output)
                ns=validator_namespace(command,roots[task['repo']])
                gold=validators.expected(ns)
                digest=hashlib.sha256(json.dumps(validators.normalize(gold),ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                golds[task['id']]={'sha256':digest,'mode':ns.mode}
        finally:
            for repo_id,target in roots.items():
                try: experiment.git(repos[repo_id]['path'],'worktree','remove','--force',str(target))
                except Exception: pass
    return {'ok':True,'model_calls':0,'tasks':len(manifest['tasks']),'repositories':len(repos),'families':dict(counts),'golds':golds,'router_configured':bool(router),'worker_configured':bool(worker)}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('manifest',type=Path); a=p.parse_args(argv)
    data=run_real.expand(json.loads(a.manifest.read_text(encoding='utf-8-sig')))
    print(json.dumps(run(data),ensure_ascii=False,indent=2)); return 0

if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError,RuntimeError) as exc:
        print('real preflight: '+str(exc),file=sys.stderr); raise SystemExit(2)
