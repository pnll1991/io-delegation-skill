#!/usr/bin/env python3
"""Expand machine-local dogfood variables then invoke the resumable runner."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import tempfile

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import run as orchestrator

ENV_RE=re.compile(r'\$\{([A-Z][A-Z0-9_]*)\}')


def expand(value,env=None):
    env=env or os.environ
    if isinstance(value,str):
        value=value.replace('{validation_root}',str(HERE))
        def repl(match):
            name=match.group(1)
            if name not in env or not env[name]: raise ValueError('missing environment variable: '+name)
            return env[name]
        return ENV_RE.sub(repl,value)
    if isinstance(value,list): return [expand(x,env) for x in value]
    if isinstance(value,dict): return {k:expand(v,env) for k,v in value.items()}
    return value


def inject_scope_flags(data):
    """Make post-run static validators see exactly the task's authorized corpus."""
    if not isinstance(data,dict): return data
    for task in data.get('tasks',[]):
        prefixes=list(task.get('allow_prefixes',[])); files=list(task.get('allow_files',[]))
        for command in task.get('validator',[]):
            if not isinstance(command,list) or not command: continue
            if not any(str(item).replace('\\','/').endswith('/validators.py') for item in command):
                continue
            if '--scope-prefix' in command or '--scope-file' in command:
                continue
            for prefix in prefixes: command.extend(['--scope-prefix',str(prefix)])
            for path in files: command.extend(['--scope-file',str(path)])
    return data


def prepare(value,env=None):
    return inject_scope_flags(expand(value,env))


def main(argv=None):
    argv=list(argv if argv is not None else sys.argv[1:])
    if not argv: raise ValueError('manifest required')
    manifest=Path(argv[0]).resolve(strict=True)
    data=prepare(json.loads(manifest.read_text(encoding='utf-8-sig')))
    with tempfile.TemporaryDirectory(prefix='io-v1-manifest-') as folder:
        resolved=Path(folder)/'manifest.json'
        resolved.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return orchestrator.main([str(resolved),*argv[1:]])

if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        print('real validation launcher: '+str(exc),file=sys.stderr); raise SystemExit(2)
