#!/usr/bin/env python3
"""Offline real-repository activation-gate check; no Codex, Jev or worker calls."""
from __future__ import annotations
import argparse, json, tempfile
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
SCRIPTS=ROOT/'skills/io-delegation/scripts'
sys.path.insert(0,str(SCRIPTS)); sys.path.insert(0,str(HERE))

import context_activation
import experiment
import run_real

BROAD={'large-understanding','multi-file-factual','cross-file-behavior'}

def expected_decision(family):
    if family=='small-control': return 'bypass'
    if family in BROAD: return 'enable'
    return None

def summarize(rows):
    judged=[r for r in rows if r['expected'] is not None]
    mismatches=[r['task_id'] for r in judged if r['decision']!=r['expected']]
    small=[r for r in rows if r['family']=='small-control']
    broad=[r for r in rows if r['family'] in BROAD]
    principal=[r for r in rows if r['family']=='principal']
    return {
        'schema':'io-context-activation-preflight/v1',
        'tasks':len(rows),'judged_tasks':len(judged),
        'small_controls':len(small),
        'small_bypassed':sum(r['decision']=='bypass' for r in small),
        'broad_tasks':len(broad),
        'broad_enabled':sum(r['decision']=='enable' for r in broad),
        'principal_tasks':len(principal),
        'principal_intent_detected':sum(bool(r['principal_intent']) for r in principal),
        'mismatches':mismatches,
        'pass':not mismatches,
    }

def evaluate(manifest, env=None):
    data=run_real.prepare(json.loads(Path(manifest).read_text(encoding='utf-8-sig')),env)
    experiment.validate_manifest(data)
    repos=experiment.resolve_repositories(data)
    rows=[]; worktrees={}
    with tempfile.TemporaryDirectory(prefix='io-activation-preflight-') as folder:
        base=Path(folder)
        try:
            for name,repo in repos.items():
                wt=base/name
                experiment.git(repo['path'],'worktree','add','--detach',str(wt),repo['commit'])
                worktrees[name]=wt
            for task in data['tasks']:
                state=experiment.activation_state(task,{'activation':'auto'})
                result=context_activation.decide(
                    worktrees[task['repo']],state,task.get('task_hint',task['prompt']))
                rows.append({
                    'task_id':task['id'],'family':task['family'],
                    'decision':result['decision'],'reason':result['reason'],
                    'expected':expected_decision(task['family']),
                    'principal_intent':bool(result.get('principal_intent')),
                    'signals':result.get('signals',{}),
                })
        finally:
            for name,wt in worktrees.items():
                try: experiment.git(repos[name]['path'],'worktree','remove','--force',str(wt))
                except Exception: pass
    return {'summary':summarize(rows),'results':rows}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest',type=Path); p.add_argument('--output',type=Path)
    a=p.parse_args(argv)
    result=evaluate(a.manifest)
    text=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if a.output:
        a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(text,encoding='utf-8')
    else: print(text,end='')
    return 0 if result['summary']['pass'] else 3

if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError,RuntimeError) as exc:
        print('activation preflight: '+str(exc),file=sys.stderr); raise SystemExit(2)
