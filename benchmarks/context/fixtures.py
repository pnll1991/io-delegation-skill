#!/usr/bin/env python3
"""Known-answer, synthetic holdouts. Never changes a real website or calls a model."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys

EXPECTED={
 'html':dict(title='Catalog & tools',h1='One two',canonical='https://example.invalid/tools',description='Local example'),
 'json':dict(limit=0,enabled=False,optional=None),
 'policy':dict(timeout_retry=True,credentials_retry=False,retry_limit=3)}


def check(root,case,regression=False,base_commit=None):
    root=Path(root);out=f'benchmark-output/{case}.json'
    if regression:
        cp=subprocess.run(['git','status','--porcelain=v1','--untracked-files=all'],cwd=root,capture_output=True,text=True,check=True)
        clean = cp.stdout.splitlines()==['?? '+out]
        if base_commit:
            head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
            delta=subprocess.run(['git','diff','--quiet',base_commit,'--','data'],cwd=root)
            clean = clean and head==base_commit and delta.returncode==0
        return clean
    try:return json.loads((root/out).read_text(encoding='utf-8-sig'))==EXPECTED[case]
    except (OSError,ValueError,KeyError):return False


def create(destination,model,config=None):
    base=Path(destination).resolve()
    if base.exists() and any(base.iterdir()):raise ValueError('Destination must be new')
    base.mkdir(parents=True,exist_ok=True);repo=base/'fixture-repo';repo.mkdir();(repo/'data').mkdir()
    (repo/'data/page.html').write_text('<title>Catalog &amp; tools</title><meta name="description" content="Local example"><link rel="canonical" href="https://example.invalid/tools"><h1>One<br>two</h1>'+('<div>irrelevant filler</div>'*8000),encoding='utf-8')
    (repo/'data/values.json').write_text('{"service":{"limit":0,"enabled":false,"optional":null}}',encoding='utf-8')
    (repo/'data/rules.txt').write_text('Retry limit: 3. Retry temporary network failures only. Never retry validation errors.\n',encoding='utf-8')
    (repo/'data/classification.txt').write_text('A network timeout is temporary. Invalid credentials are a validation error.\n',encoding='utf-8')
    for args in (['init'],['add','.'],['-c','user.name=Context Fixture','-c','user.email=fixture@example.invalid','commit','--no-gpg-sign','-m','Synthetic predeclared tasks']):
        subprocess.run(['git',*args],cwd=repo,capture_output=True,check=True)
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    prompts={
      'html':'Extract title, first h1, canonical and description from data/page.html into benchmark-output/html.json with those exact keys. Decode entities once; BR is a space. Static source, not rendered DOM.',
      'json':'Read data/values.json. Create benchmark-output/json.json containing exactly limit, enabled and optional from service, preserving zero, false and null.',
      'policy':'Using data/rules.txt and data/classification.txt, create benchmark-output/policy.json with exactly timeout_retry (boolean), credentials_retry (boolean) and retry_limit (integer). Apply the restrictions, not just positive statements.'}
    tasks=[]
    for case,prompt in prompts.items():
        cmd=['{python}',str(Path(__file__).resolve()),'check','--root','{root}','--case',case,'--base-commit',sha]
        tasks.append(dict(id=case,prompt=prompt,allow_prefixes=['data'],acceptance=[cmd],regression=[cmd+['--regression']],outputs=[f'benchmark-output/{case}.json'],timeout=300))
    manifest=dict(version=1,repo=str(repo),base_commit=sha,model=model,reasoning_effort='medium',repetitions=1,
        arms=['baseline','deterministic']+(['semantic-optional'] if config else []),tasks=tasks,seed=20260916,
        experiment='synthetic holdout; one repetition is exploratory',adoption_target=dict(minimum_reduction=0.20,no_quality_drop=True))
    if config:manifest['worker_config']=str(Path(config).resolve(strict=True))
    (base/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    checks=[]
    (repo/'benchmark-output').mkdir()
    for case in EXPECTED:
        path=repo/f'benchmark-output/{case}.json';path.write_text(json.dumps(EXPECTED[case]));good=check(repo,case) and check(repo,case,True)
        path.write_text('{}');bad=not check(repo,case);path.unlink();checks.append(dict(case=case,positive=good,negative=bad))
    (repo/'benchmark-output').rmdir()
    (base/'validator-selftests.json').write_text(json.dumps(checks,indent=2)+'\n')
    if not all(x['positive'] and x['negative'] for x in checks):raise ValueError('Validator selftest failed')
    return base/'manifest.json'


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='cmd',required=True)
    a=sub.add_parser('create');a.add_argument('--output',type=Path,required=True);a.add_argument('--model',required=True);a.add_argument('--config',type=Path)
    b=sub.add_parser('check');b.add_argument('--root',type=Path,required=True);b.add_argument('--case',choices=list(EXPECTED),required=True);b.add_argument('--regression',action='store_true');b.add_argument('--base-commit')
    args=p.parse_args()
    if args.cmd=='create':print(create(args.output,args.model,args.config));return 0
    ok=check(args.root,args.case,args.regression,args.base_commit);print(json.dumps(dict(ok=ok,case=args.case)));return 0 if ok else 1


if __name__=='__main__':raise SystemExit(main())
