#!/usr/bin/env python3
"""Bounded local security scan for Context Gateway managed artifacts and validation records."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import record

MAX_FILE_BYTES=4_000_000
MAX_TOTAL_BYTES=128_000_000
FORBIDDEN_EVENT_KEYS={
    'content','source_text','raw_source','file_contents','contents',
    'api_key','password','secret','credential','credentials',
}
SKIP_DIRS={'.git','node_modules','.venv','venv','__pycache__'}


def iter_files(roots):
    seen=set()
    for raw in roots:
        root=Path(raw).expanduser().resolve(strict=True)
        candidates=[root] if root.is_file() else root.rglob('*')
        for path in candidates:
            try:
                if path.is_symlink() or not path.is_file(): continue
                if any(part in SKIP_DIRS for part in path.parts): continue
                resolved=path.resolve(strict=True)
            except OSError:
                continue
            if resolved in seen: continue
            seen.add(resolved); yield resolved


def secret_values(names):
    values={}
    for name in names:
        value=os.environ.get(name)
        if value and len(value)>=6: values[name]=value
    return values


def forbidden_keys(value,path=''):
    findings=[]
    if isinstance(value,dict):
        for key,item in value.items():
            low=str(key).lower()
            here=f'{path}.{key}' if path else str(key)
            if low in FORBIDDEN_EVENT_KEYS: findings.append(here)
            findings.extend(forbidden_keys(item,here))
    elif isinstance(value,list):
        for index,item in enumerate(value):
            findings.extend(forbidden_keys(item,f'{path}[{index}]'))
    return findings


def scan_jsonl(path):
    findings=[]; malformed=0
    name=path.name.lower()
    inspect_keys=name in ('context-events.jsonl','worker-events.jsonl')
    validate_records=name=='runs.jsonl'
    try:
        lines=path.read_text(encoding='utf-8-sig',errors='strict').splitlines()
    except (OSError,UnicodeError): return findings,malformed
    if validate_records:
        try: record.load(path)
        except ValueError: malformed+=1
    if inspect_keys:
        for number,line in enumerate(lines,1):
            if not line.strip(): continue
            try: value=json.loads(line)
            except ValueError:
                malformed+=1; continue
            for key in forbidden_keys(value): findings.append({'line':number,'key':key})
    return findings,malformed


def audit(roots,env_names=(),max_total=MAX_TOTAL_BYTES):
    secrets=secret_values(env_names)
    secret_hits=[]; forbidden=[]; malformed=[]; scanned=bytes_scanned=skipped_large=0
    for path in iter_files(roots):
        try: size=path.stat().st_size
        except OSError: continue
        if size>MAX_FILE_BYTES:
            skipped_large+=1; continue
        if bytes_scanned+size>max_total: raise ValueError('security audit total byte budget exceeded')
        try: raw=path.read_bytes()
        except OSError: continue
        scanned+=1; bytes_scanned+=len(raw)
        for name,value in secrets.items():
            if value.encode('utf-8',errors='ignore') in raw:
                secret_hits.append({'env':name,'path':str(path)})
        if path.suffix.lower()=='.jsonl':
            key_hits,bad=scan_jsonl(path)
            forbidden.extend({'path':str(path),**item} for item in key_hits)
            if bad: malformed.append({'path':str(path),'records':bad})
    return {
        'schema':'io-context-security-audit/v1',
        'files_scanned':scanned,'bytes_scanned':bytes_scanned,'large_files_skipped':skipped_large,
        'secret_envs_checked':sorted(secrets),
        'secret_hits':secret_hits,'forbidden_event_fields':forbidden,'malformed_jsonl':malformed,
        'clean':not secret_hits and not forbidden and not malformed,
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',action='append',required=True)
    p.add_argument('--secret-env',action='append',default=[])
    p.add_argument('--max-total-bytes',type=int,default=MAX_TOTAL_BYTES)
    p.add_argument('--output',type=Path)
    a=p.parse_args(argv)
    if a.max_total_bytes<1: p.error('--max-total-bytes must be positive')
    result=audit(a.root,a.secret_env,a.max_total_bytes)
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if a.output:
        a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(text,encoding='utf-8')
    else: print(text,end='')
    return 0 if result['clean'] else 3

if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError) as exc:
        print('security audit: '+str(exc),file=sys.stderr); raise SystemExit(2)
