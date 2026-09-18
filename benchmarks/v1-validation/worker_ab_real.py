#!/usr/bin/env python3
"""Expand machine-local variables then run the isolated semantic-worker A/B."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import sys
import tempfile

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import worker_ab

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

def main(argv=None):
    argv=list(argv if argv is not None else sys.argv[1:])
    if not argv: raise ValueError('manifest required')
    manifest=Path(argv[0]).resolve(strict=True)
    data=expand(json.loads(manifest.read_text(encoding='utf-8-sig')))
    with tempfile.TemporaryDirectory(prefix='io-worker-ab-manifest-') as folder:
        resolved=Path(folder)/'manifest.json'
        resolved.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return worker_ab.main([str(resolved),*argv[1:]])

if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        print('worker A/B launcher: '+str(exc),file=sys.stderr)
        raise SystemExit(2)
