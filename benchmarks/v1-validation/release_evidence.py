#!/usr/bin/env python3
"""Fail-closed V1 release evidence table from completed validation artifacts."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import paired
import record

LARGE_FAMILIES={'large-understanding','multi-file-factual'}


def read_json(path):
    value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(value,dict): raise ValueError('evidence artifact must contain an object')
    return value


def _get(row,*path):
    current=row
    for key in path:
        if not isinstance(current,dict): return None
        current=current.get(key)
    return current


def _median(values):
    vals=[x for x in values if isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)]
    return statistics.median(vals) if vals else None


def success_count(rows,arm):
    selected=[row for row in rows if row['arm']==arm]
    return sum(row.get('success') is True for row in selected),len(selected)


def pair_successes(report):
    left=right=0
    for family in report.get('families',{}).values():
        left+=int(family.get('left_successes',0)); right+=int(family.get('right_successes',0))
    return left,right


def dogfood_gates(rows,left,right,small_overhead_pct,context_ratio):
    report=paired.summarize(rows,left,right)
    pairs,_incomplete=paired.pair_rows(rows,left,right)
    left_ok,right_ok=pair_successes(report)
    regressions=[rhs['run_id'] for _key,lhs,rhs in pairs
                 if lhs.get('success') is True and rhs.get('success') is not True]
    quality=(right_ok>=left_ok and not regressions
             and report['pair_count']>=20 and report['incomplete_pair_count']==0)
    small=report.get('families',{}).get('small-control',{})
    small_median=_get(small,'principal_token_delta_pct','median')
    small_gate=small.get('valid_pairs',0)>=3 and small_median is not None and small_median<=small_overhead_pct
    gateway=[row for row in rows if row['arm']==right and row['family'] in LARGE_FAMILIES and row.get('success') is True]
    ratios=[]
    for row in gateway:
        source=_get(row,'context','source_bytes'); selected=_get(row,'context','selected_bytes')
        if type(source) is int and source>0 and type(selected) is int:
            ratios.append(selected/source)
    ratio_median=_median(ratios)
    context_gate=len(ratios)>=3 and ratio_median is not None and ratio_median<=context_ratio
    principal=[row for row in rows if row['arm']==right and row['family']=='principal']
    unsafe=[row['run_id'] for row in principal if (_get(row,'worker','calls') or 0)>0 or _get(row,'route','effective')=='bulk_read']
    principal_gate=len(principal)>=3 and not unsafe
    return report,{
        'functional_quality':{'pass':quality,'left_successes':left_ok,'right_successes':right_ok,
                              'pairs':report['pair_count'],'regression_run_ids':regressions},
        'small_task_overhead':{'pass':small_gate,'median_pct':small_median,'max_allowed_pct':small_overhead_pct,'valid_pairs':small.get('valid_pairs',0)},
        'large_context_reduction':{'pass':context_gate,'median_selected_source_ratio':ratio_median,'max_ratio':context_ratio,'measured_runs':len(ratios)},
        'principal_safety':{'pass':principal_gate,'runs':len(principal),'unsafe_run_ids':unsafe},
    }


def causal_gate(rows,left,right,min_pairs,component):
    report=paired.summarize(rows,left,right)
    pairs,_incomplete=paired.pair_rows(rows,left,right)
    left_ok,right_ok=pair_successes(report)
    regressions=[rhs['run_id'] for _key,lhs,rhs in pairs
                 if lhs.get('success') is True and rhs.get('success') is not True]
    warnings=list(report.get('warnings',[]))
    called=0
    for family in report.get('families',{}).values():
        called+=int(family.get('jev_called_pairs' if component=='jev' else 'worker_called_pairs',0))
    never_text='never called Jev' if component=='jev' else 'never called the worker'
    relevant_warning=any(never_text in warning for warning in warnings)
    passed=(report['pair_count']>=min_pairs and report['incomplete_pair_count']==0 and
            right_ok>=left_ok and not regressions and called>0 and not relevant_warning)
    return report,{'pass':passed,'pairs':report['pair_count'],'minimum_pairs':min_pairs,
                   'left_successes':left_ok,'right_successes':right_ok,'component_called_pairs':called,
                   'regression_run_ids':regressions,'warnings':warnings}


def activation_gate(value):
    false_enables=value.get('false_enable_run_ids',[]) if isinstance(value,dict) else []
    unexpected=value.get('unexpected_call_run_ids',[]) if isinstance(value,dict) else []
    context_leaks=value.get('context_leak_run_ids',[]) if isinstance(value,dict) else []
    schema=value.get('schema') if isinstance(value,dict) else None
    passed=(schema=='io-context-activation-evidence/v1' and value.get('pass') is True)
    return {
        'pass':passed,'schema':schema,'pairs':value.get('pairs') if isinstance(value,dict) else None,
        'false_enables':len(false_enables) if isinstance(false_enables,list) else None,
        'unexpected_calls':len(unexpected) if isinstance(unexpected,list) else None,
        'context_leaks':len(context_leaks) if isinstance(context_leaks,list) else None,
    }


def evaluate(args):
    dogfood=record.load(args.dogfood_records)
    jev=record.load(args.jev_records)
    worker=record.load(args.worker_records)
    activation=read_json(args.activation)
    parity=read_json(args.parity)
    security=read_json(args.security)
    dogfood_report,dogfood_g=dogfood_gates(dogfood,args.dogfood_left,args.dogfood_right,args.small_overhead_pct,args.context_ratio)
    jev_report,jev_g=causal_gate(jev,args.jev_left,args.jev_right,args.min_jev_pairs,'jev')
    worker_report,worker_g=causal_gate(worker,args.worker_left,args.worker_right,args.min_worker_pairs,'worker')
    activation_g=activation_gate(activation)
    parity_g={'pass':parity.get('critical_deviations')==0 and parity.get('lifecycle_complete') is True,
              'critical_deviations':parity.get('critical_deviations'),
              'lifecycle_complete':parity.get('lifecycle_complete')}
    security_g={'pass':security.get('clean') is True,'secret_hits':len(security.get('secret_hits',[])),
                'forbidden_event_fields':len(security.get('forbidden_event_fields',[])),
                'malformed_jsonl':len(security.get('malformed_jsonl',[]))}
    gates={**dogfood_g,'activation_gate':activation_g,'jev_causal_sample':jev_g,'worker_causal_sample':worker_g,
           'host_parity':parity_g,'security_audit':security_g}
    ready=all(item.get('pass') is True for item in gates.values())
    return {'schema':'io-context-release-evidence/v1','ready':ready,'gates':gates,
            'artifacts':{'dogfood_paired':dogfood_report,'activation_schema':activation.get('schema'),
                         'jev_paired':jev_report,'worker_paired':worker_report,
                         'parity_schema':parity.get('schema'),'security_schema':security.get('schema')}}


def markdown(result):
    lines=['# Context Gateway V1 release evidence','', '| Gate | Pass | Evidence |','|---|---|---|']
    for name,item in result['gates'].items():
        evidence=', '.join(f'{k}={v}' for k,v in item.items() if k!='pass')
        lines.append(f"| {name} | {'yes' if item['pass'] else 'no'} | {evidence} |")
    lines+=['',f"Release-ready: {'yes' if result['ready'] else 'no'}"]
    return '\n'.join(lines)+'\n'


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dogfood-records',type=Path,required=True); p.add_argument('--activation',type=Path,required=True)
    p.add_argument('--jev-records',type=Path,required=True); p.add_argument('--worker-records',type=Path,required=True)
    p.add_argument('--parity',type=Path,required=True); p.add_argument('--security',type=Path,required=True)
    p.add_argument('--dogfood-left',default='control'); p.add_argument('--dogfood-right',default='gateway-smart')
    p.add_argument('--jev-left',default='gateway-local'); p.add_argument('--jev-right',default='gateway-jev')
    p.add_argument('--worker-left',default='direct-selected'); p.add_argument('--worker-right',default='semantic-worker')
    p.add_argument('--small-overhead-pct',type=float,default=5.0); p.add_argument('--context-ratio',type=float,default=.5)
    p.add_argument('--min-jev-pairs',type=int,default=20); p.add_argument('--min-worker-pairs',type=int,default=5)
    p.add_argument('--output-json',type=Path); p.add_argument('--output-md',type=Path)
    a=p.parse_args(argv)
    if a.min_jev_pairs<1 or a.min_worker_pairs<1 or a.context_ratio<0 or a.context_ratio>1: p.error('invalid gate threshold')
    result=evaluate(a); text=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if a.output_json:
        a.output_json.parent.mkdir(parents=True,exist_ok=True); a.output_json.write_text(text,encoding='utf-8')
    else: print(text,end='')
    if a.output_md:
        a.output_md.parent.mkdir(parents=True,exist_ok=True); a.output_md.write_text(markdown(result),encoding='utf-8')
    return 0 if result['ready'] else 3

if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError) as exc:
        print('release evidence: '+str(exc),file=sys.stderr); raise SystemExit(2)
