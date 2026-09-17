#!/usr/bin/env python3
"""Create the deterministic four-file repository used for authenticated host parity."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

FILES={
 'alpha.txt':'TARGET_VALUE=17\nSAFE_FLAG=true\n',
 'beta.txt':'COMMON=red\n'+('B'*900)+'\n',
 'gamma.txt':'COMMON=green\n'+('G'*900)+'\n',
 'delta.txt':'COMMON=blue\n'+('D'*900)+'\n',
}
FIXED_DATE='2026-01-01T00:00:00Z'

def git(root,*args,env=None):
    cp=subprocess.run(['git','-c','core.autocrlf=false','-C',str(root),*args],capture_output=True,text=True,encoding='utf-8',errors='replace',env=env,timeout=30)
    if cp.returncode: raise ValueError((cp.stderr or cp.stdout)[-500:])
    return cp.stdout.strip()

def create(root):
    root=Path(root).resolve()
    if root.exists() and any(root.iterdir()): raise ValueError('fixture root must be new/empty')
    root.mkdir(parents=True,exist_ok=True)
    subprocess.run(['git','init'],cwd=root,check=True,capture_output=True)
    git(root,'config','core.autocrlf','false')
    for name,text in FILES.items(): (root/name).write_text(text,encoding='utf-8',newline='\n')
    git(root,'add','--',*FILES)
    env=os.environ.copy(); env.update({'GIT_AUTHOR_NAME':'io-validation','GIT_AUTHOR_EMAIL':'io@example.invalid','GIT_COMMITTER_NAME':'io-validation','GIT_COMMITTER_EMAIL':'io@example.invalid','GIT_AUTHOR_DATE':FIXED_DATE,'GIT_COMMITTER_DATE':FIXED_DATE})
    git(root,'commit','-m','host validation fixture',env=env)
    return {'root':str(root),'commit':git(root,'rev-parse','HEAD'),'files':sorted(FILES)}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('root'); a=p.parse_args(argv)
    print(json.dumps(create(a.root),ensure_ascii=False)); return 0

if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        print('host fixture: '+str(exc),file=sys.stderr); raise SystemExit(2)
