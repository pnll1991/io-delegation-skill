#!/usr/bin/env python3
"""Cross-host parity matrix from common Context Gateway validation records."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import record

DEFAULT_HOSTS=('codex','claude','cursor')


def parse_source(value):
    if '=' not in value: raise ValueError('--records must be HOST=PATH')
    host,path=value.split('=',1)
    host=host.strip(); path=Path(path).expanduser().resolve(strict=True)
    if not host: raise ValueError('host name required')
    return host,path


def _get(row,*path):
    current=row
    for key in path:
        if not isinstance(current,dict): return None
        current=current.get(key)
    return current


def _ratio(row):
    source=_get(row,'context','source_bytes'); selected=_get(row,'context','selected_bytes')
    if type(source) is not int or source<=0 or type(selected) is not int: return None
    return selected/source


def load_sources(sources):
    result={}
    for host,path in sources:
        if host in result: raise ValueError('duplicate host source: '+host)
        rows=record.load(path)
        bad=[row['run_id'] for row in rows if row.get('host')!=host]
        if bad: raise ValueError(f'{host} source contains rows for another host')
        result[host]=rows
    return result


def summarize(by_host,required_hosts=DEFAULT_HOSTS):
    tasks=sorted({row['task_id'] for rows in by_host.values() for row in rows})
    matrix=[]; deviations=[]
    for task_id in tasks:
        entries={}
        for host in required_hosts:
            matches=[row for row in by_host.get(host,[]) if row['task_id']==task_id]
            if len(matches)>1: raise ValueError(f'multiple {host} rows for {task_id}; aggregate repetitions first')
            if not matches:
                entries[host]=None
                continue
            row=matches[0]
            entries[host]={
                'run_id':row['run_id'],
                'success':row.get('success'),
                'validator':_get(row,'validator','status'),
                'route':_get(row,'route','effective'),
                'router_called':_get(row,'router','called'),
                'worker_calls':_get(row,'worker','calls'),
                'principal_tokens':_get(row,'principal','raw_tokens'),
                'wall_ms':_get(row,'timing','wall_ms'),
                'selected_source_ratio':_ratio(row),
                'errors':row.get('errors',[]),
            }
        present=[value for value in entries.values() if value is not None]
        task_deviations=[]
        missing=[host for host,value in entries.items() if value is None]
        if missing: task_deviations.append({'kind':'missing_host','hosts':missing})
        if present:
            for key in ('success','validator','route'):
                values={json.dumps(value.get(key),sort_keys=True) for value in present}
                if len(values)>1: task_deviations.append({'kind':key+'_mismatch'})
            failed=[host for host,value in entries.items() if value is not None and value.get('success') is not True]
            if failed: task_deviations.append({'kind':'host_failure','hosts':failed})
        matrix.append({'task_id':task_id,'hosts':entries,'deviations':task_deviations})
        deviations.extend({'task_id':task_id,**item} for item in task_deviations)
    return {
        'schema':'io-context-host-parity/v1',
        'required_hosts':list(required_hosts),
        'tasks':len(tasks),
        'matrix':matrix,
        'deviations':deviations,
        'critical_deviations':sum(item['kind'] in ('missing_host','host_failure','validator_mismatch','route_mismatch') for item in deviations),
    }


def markdown(result):
    hosts=result['required_hosts']
    lines=['# Context Gateway host parity','',
           '| Task | '+ ' | '.join(hosts) +' | Deviations |',
           '|---|' + '|'.join(['---']*len(hosts)) + '|---|']
    for row in result['matrix']:
        cells=[]
        for host in hosts:
            value=row['hosts'][host]
            if value is None: cells.append('missing')
            else: cells.append(f"{'pass' if value['success'] else 'fail'} / {value['route'] or 'unknown'} / worker={value['worker_calls']}")
        dev=', '.join(item['kind'] for item in row['deviations']) or 'none'
        lines.append('| '+row['task_id']+' | '+' | '.join(cells)+' | '+dev+' |')
    lines+=['',f"Critical deviations: {result['critical_deviations']}"]
    return '\n'.join(lines)+'\n'


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--records',action='append',required=True,help='HOST=PATH')
    p.add_argument('--host',action='append',dest='hosts')
    p.add_argument('--output-json',type=Path)
    p.add_argument('--output-md',type=Path)
    a=p.parse_args(argv)
    sources=[parse_source(value) for value in a.records]
    result=summarize(load_sources(sources),tuple(a.hosts or DEFAULT_HOSTS))
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if a.output_json:
        a.output_json.parent.mkdir(parents=True,exist_ok=True); a.output_json.write_text(text,encoding='utf-8')
    else: print(text,end='')
    if a.output_md:
        a.output_md.parent.mkdir(parents=True,exist_ok=True); a.output_md.write_text(markdown(result),encoding='utf-8')
    return 0 if result['critical_deviations']==0 else 3

if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError) as exc:
        print('host parity: '+str(exc),file=sys.stderr); raise SystemExit(2)
