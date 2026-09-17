#!/usr/bin/env python3
"""Configure an explicitly approved worker, run ONE synthetic smoke test, optionally A/B.

Uses existing Codex authentication or a user-specified loopback Chat Completions server.
No global configuration changes, no key copying, no deletion of previous results.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
SCRIPTS=ROOT/'skills/io-delegation/scripts'
sys.path.insert(0,str(HERE)); sys.path.insert(0,str(SCRIPTS))
from worker_runtime import codex_preflight, run_process
from io_delegate import load_config, endpoint
from codex_io_ab import engine_hash, config_hash, load_events, worker_usage, write_json, git


def new_folder(base):
    base=Path(base).resolve()
    if base.is_symlink(): raise ValueError('symlinked output base')
    base.mkdir(parents=True,exist_ok=True)
    folder=base/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    folder.mkdir()
    (folder/'.gitignore').write_text('*\n',encoding='utf-8')
    return folder


def smoke(config, destination):
    """Live model test, but only synthetic public-sized data, never the user's repo."""
    with tempfile.TemporaryDirectory(prefix='io-worker-smoke-') as temp:
        root=Path(temp); value=uuid.uuid4().hex[:10]
        (root/'probe.txt').write_text('WORKER_CHECK = "'+value+'"\n',encoding='utf-8')
        question='What is the literal value of WORKER_CHECK? Include the exact assignment as evidence.'
        cmd=[sys.executable,str(SCRIPTS/'io_delegate.py'),'bulk-read','--root',str(root),
             '--config',str(config),'--paths','probe.txt','--question',question]
        cfg=load_config(str(config))
        cp=run_process(cmd,timeout=cfg.get('timeout_seconds',120)+65,max_output=500_000)
        events,errors=load_events(root/'.io-delegation/worker-events.jsonl')
        stats=worker_usage(events,errors)
        try:
            result=json.loads(cp.stdout.decode('utf-8-sig'))
        except (ValueError,UnicodeError): result={}
        findings=result.get('findings',[])
        ok=(cp.returncode==0 and not cp.timed_out and result.get('status')=='ok' and
            stats['worker_calls']==1 and stats['worker_accepted']==1 and
            stats['worker_accounting_complete'] and
            any(value in f.get('evidence','') and value in f.get('fact','') for f in findings))
        receipt=dict(ok=ok,config_sha256=config_hash(config),engine_sha256=engine_hash(),
                     model=cfg.get('model'),synthetic_only=True,stats=stats,
                     error=None if ok else 'See smoke.stderr.txt and smoke.events.jsonl. A/B was NOT started.')
        write_json(destination/'worker-smoke.json',receipt)
        (destination/'smoke.stdout.txt').write_bytes(cp.stdout)
        (destination/'smoke.stderr.txt').write_bytes(cp.stderr)
        write_json(destination/'smoke.events.json',events)
        if (root/'.io-delegation/worker-events.jsonl').exists():
            shutil.copy2(root/'.io-delegation/worker-events.jsonl',destination/'smoke.events.jsonl')
        return receipt


def create_manifest(repo, folder, config, main_model, repetitions, mode):
    sha=git(repo,'rev-parse','HEAD^{commit}').strip()
    base=dict(name='baseline',model=main_model,sandbox='workspace-write',worker_mode='disabled',
              config_overrides=['model_reasoning_effort="medium"'])
    worker=dict(base,name='skill-worker-'+mode,worker_mode=mode,worker_config=str(config),
                worker_receipt=str(folder/'worker-smoke.json'),
                prompt_prefix='Use the io-delegation skill. Read targeted originals to verify worker evidence.',
                setup_commands=[['{python}',str(ROOT/'install.py'),'--agent','codex','--project','{worktree}']])
    task=dict(id='kuatrometric-html-inventory',base_commit=sha,
        user_prompt='Create only benchmark-output/html-inventory.json. One object per herramientas/**/index.html with exactly path, title and h1. path uses relative forward slashes; extract the title and first H1, decode entities once, collapse whitespace and replace BR with a space. Array order is not significant. Do not modify existing files. Verify accuracy.',
        acceptance=[['{python}',str(HERE/'validate_html_inventory.py'),'--root','.']],
        regression=[['{python}',str(HERE/'validate_html_inventory.py'),'--root','.','--regression']],
        capture_paths=['benchmark-output/html-inventory.json'],max_wall_seconds=900)
    m=dict(repo=str(repo),base_commit=sha,repetitions=repetitions,max_wall_seconds=900,
           forbid_base_paths=['.agents/skills/io-delegation'],strategies=[base,worker],tasks=[task],
           experiment='integration-path, forced delegation; NOT an economic optimum' if mode=='required' else 'optional worker routing')
    write_json(folder/'manifest.worker.json',m)
    return folder/'manifest.worker.json'


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,required=True)
    ap.add_argument('--adapter',choices=['codex-cli','chat-completions'],default='codex-cli')
    ap.add_argument('--worker-model',default='gpt-5.6-luna')
    ap.add_argument('--main-model',default='gpt-5.6-luna')
    ap.add_argument('--endpoint',default='http://127.0.0.1:1234/v1/chat/completions')
    ap.add_argument('--config',type=Path,help='Already reviewed adapter config instead of constructing one')
    ap.add_argument('--approve-worker',action='store_true',help='Explicitly authorize this adapter and one synthetic model call')
    ap.add_argument('--run',action='store_true',help='After smoke passes, run baseline + worker A/B (uses model quota)')
    ap.add_argument('--worker-mode',choices=['required','optional'],default='required')
    ap.add_argument('--repetitions',type=int,default=1)
    ap.add_argument('--output-base',type=Path,default=HERE/'local-runs')
    args=ap.parse_args(argv)
    if not args.approve_worker: raise ValueError('Review the adapter and add --approve-worker. No model was called.')
    repo=args.repo.resolve(strict=True)
    if not (repo/'herramientas').is_dir(): raise ValueError('The Kuatrometric herramientas directory was not found')
    git(repo,'rev-parse','HEAD^{commit}')
    if args.repetitions not in range(1,11): raise ValueError('repetitions must be 1..10')
    folder=new_folder(args.output_base)
    if args.config:
        cfg=load_config(str(args.config.resolve(strict=True)))
    elif args.adapter=='codex-cli':
        exe=shutil.which('codex.exe') or shutil.which('codex')
        if not exe: raise ValueError('Codex CLI is not on PATH')
        if os.name=='nt' and Path(exe).suffix.lower()!='.exe': raise ValueError('Use the installed standalone codex.exe')
        codex_preflight(exe)
        cfg=dict(approved=True,adapter='codex-cli',model=args.worker_model,executable=exe,
                 reasoning_effort='low',timeout_seconds=120,max_calls_per_workspace=4)
    else:
        cfg=dict(approved=True,adapter='chat-completions',url=args.endpoint,model=args.worker_model,
                 timeout_seconds=120,reader_max_tokens=1600,writer_max_tokens=4096,temperature=0.2,
                 max_calls_per_workspace=4)
        endpoint(cfg)  # Reject non-loopback hosts without an explicitly reviewed --config.
    config=folder/'worker.local.json'; write_json(config,cfg); load_config(str(config))
    print('Testing ONE real worker call using synthetic input. No project code in this smoke test.',flush=True)
    receipt=smoke(config,folder)
    print(json.dumps(receipt,indent=2),flush=True)
    print('Artifacts: '+str(folder),flush=True)
    if not receipt['ok']: return 2
    manifest=create_manifest(repo,folder,config,args.main_model,args.repetitions,args.worker_mode)
    if args.run:
        from codex_io_ab import main as benchmark
        return benchmark([str(manifest),'--output',str(folder/'ab')])
    print('Smoke passed. Run the prepared comparison with:\n'+
          'python "'+str(HERE/'codex_io_ab.py')+'" "'+str(manifest)+'" --output "'+str(folder/'ab')+'"')
    return 0


if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print('Stopped before further model calls: '+str(exc),file=sys.stderr); raise SystemExit(2)
