#!/usr/bin/env python3
"""Run the same authenticated fixture on Codex, Claude and Cursor, then build parity evidence."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import host_real, parity, record

HOSTS=('codex','claude','cursor')

def read_json(path):
    value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(value,dict): raise ValueError('expected JSON object')
    return value

def parse_models(values):
    result={}
    for value in values or []:
        if '=' not in value: raise ValueError('--model must be HOST=MODEL')
        host,model=value.split('=',1); host=host.strip(); model=model.strip()
        if host not in HOSTS or not model or host in result: raise ValueError('invalid/duplicate host model')
        result[host]=model
    return result

def aggregate_security(output):
    secret_hits=[]; forbidden=[]; malformed=[]; missing=[]; reports={}
    for host in HOSTS:
        path=Path(output)/host/'security.json'
        if not path.is_file():
            missing.append(host); continue
        value=read_json(path); reports[host]=value.get('schema')
        secret_hits.extend({'host':host,**item} for item in value.get('secret_hits',[]) if isinstance(item,dict))
        forbidden.extend({'host':host,**item} for item in value.get('forbidden_event_fields',[]) if isinstance(item,dict))
        malformed.extend({'host':host,**item} for item in value.get('malformed_jsonl',[]) if isinstance(item,dict))
        if value.get('clean') is not True: missing.append(host+':unclean')
    return {
        'schema':'io-context-security-audit/v1',
        'host_reports':reports,'missing_host_reports':missing,
        'secret_hits':secret_hits,'forbidden_event_fields':forbidden,'malformed_jsonl':malformed,
        'clean':not missing and not secret_hits and not forbidden and not malformed,
    }

def build_parity(output):
    by_host={}; lifecycles={}
    for host in HOSTS:
        records=Path(output)/host/'runs.jsonl'
        lifecycle=Path(output)/host/'lifecycle.json'
        by_host[host]=record.load(records) if records.is_file() else []
        if lifecycle.is_file(): lifecycles[host]=read_json(lifecycle)
    return parity.summarize(by_host,HOSTS,lifecycles)

def execute(worker,output,models=None):
    output=Path(output).resolve()
    if output.exists() and any(output.iterdir()): raise ValueError('output directory must be new/empty')
    output.mkdir(parents=True,exist_ok=True)
    failures={}
    for host in HOSTS:
        try:
            host_real.execute(host,worker,output/host,(models or {}).get(host))
        except Exception as exc:
            failures[host]=type(exc).__name__
    parity_result=build_parity(output)
    parity_result['execution_failures']=failures
    security=aggregate_security(output)
    (output/'parity.json').write_text(json.dumps(parity_result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (output/'parity.md').write_text(parity.markdown(parity_result),encoding='utf-8')
    (output/'security.json').write_text(json.dumps(security,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    summary={'schema':'io-context-host-parity-run/v1','failures':failures,
             'critical_deviations':parity_result['critical_deviations'],
             'lifecycle_complete':parity_result['lifecycle_complete'],'security_clean':security['clean'],
             'pass':not failures and parity_result['critical_deviations']==0
                    and parity_result['lifecycle_complete'] and security['clean']}
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    return summary

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worker-config',required=True); p.add_argument('--output',required=True)
    p.add_argument('--model',action='append',default=[],help='HOST=MODEL')
    a=p.parse_args(argv)
    result=execute(a.worker_config,a.output,parse_models(a.model))
    print(json.dumps(result,indent=2))
    return 0 if result['pass'] else 3

if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError,RuntimeError) as exc:
        print('host parity real: '+str(exc),file=sys.stderr); raise SystemExit(2)
