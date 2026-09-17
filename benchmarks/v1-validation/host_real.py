#!/usr/bin/env python3
"""End-to-end authenticated host lifecycle: fixture -> setup -> validate -> remove."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(HERE))
import host_fixture

HOST_AGENT={'claude':'claude-code','cursor':'cursor'}
FILES=tuple(sorted(host_fixture.FILES))

def gateway_command(*args):
    return [sys.executable,str(ROOT/'io_gateway.py'),*map(str,args)]

def setup_command(host,project,worker):
    cmd=gateway_command('setup','--project',project,'--agent',HOST_AGENT[host],
                        '--worker-config',worker,'--jev','off','--guard','off',
                        '--activation','always','--no-doctor')
    for name in FILES: cmd += ['--allow-file',name]
    return cmd

def remove_command(project):
    return gateway_command('remove','--project',project,'--purge-data')

def run_checked(command,*,cwd=None,timeout=120):
    cp=subprocess.run(command,cwd=cwd,capture_output=True,text=True,
                      encoding='utf-8',errors='replace',timeout=timeout)
    if cp.returncode:
        raise ValueError((cp.stderr or cp.stdout)[-800:])
    return cp

def host_executable(host):
    if host=='claude': return shutil.which('claude') or shutil.which('claude.cmd')
    found=shutil.which('agent') or shutil.which('agent.cmd')
    if found: return found
    if os.name=='nt':
        candidate=Path(os.environ.get('LOCALAPPDATA',''))/'cursor-agent/agent.cmd'
        if candidate.is_file(): return str(candidate)
    return None

def host_validate_command(host,project,suite,output,model=None):
    cmd=[sys.executable,str(HERE/'host_validate.py'),'--host',host,'--project',str(project),
         '--suite',str(suite),'--output',str(output),'--io-command',str(ROOT/('io-delegation.cmd' if os.name=='nt' else 'io-delegation'))]
    if model: cmd += ['--model',model]
    return cmd

def execute(host,worker,output,model=None):
    if host not in HOST_AGENT: raise ValueError('unsupported host')
    worker=Path(worker).expanduser().resolve(strict=True)
    if not worker.is_file() or worker.is_symlink(): raise ValueError('worker config must be regular file')
    if not host_executable(host): raise ValueError(host+' CLI not installed')
    output=Path(output).resolve()
    if output.exists() and any(output.iterdir()): raise ValueError('output directory must be new/empty')
    suite=HERE/'host_suite.fixture.json'; started=time.time(); lifecycle={}
    error=None
    with tempfile.TemporaryDirectory(prefix='io-host-real-') as folder:
        project=Path(folder)/'fixture'; fixture=host_fixture.create(project)
        try:
            setup=run_checked(setup_command(host,project,worker),timeout=120)
            lifecycle['setup']='pass'
            command=host_validate_command(host,project,suite,output,model)
            run_checked(command,cwd=project,timeout=3000)
            lifecycle['validation']='pass'
        except Exception as exc:
            error=exc; lifecycle['validation']='fail'
        finally:
            try:
                run_checked(remove_command(project),timeout=120); lifecycle['remove']='pass'
            except Exception as cleanup:
                lifecycle['remove']='fail'
                if error is None: error=cleanup
    output.mkdir(parents=True,exist_ok=True)
    evidence={'host':host,'fixture_commit':fixture['commit'],'started_at':started,
              'ended_at':time.time(),'lifecycle':lifecycle,'error':type(error).__name__ if error else None}
    (output/'lifecycle.json').write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')
    if error: raise error
    return evidence

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host',choices=sorted(HOST_AGENT),required=True)
    p.add_argument('--worker-config',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--model')
    args=p.parse_args(argv)
    print(json.dumps(execute(args.host,args.worker_config,args.output,args.model),indent=2))
    return 0

if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        print('host real: '+str(exc),file=sys.stderr); raise SystemExit(2)
