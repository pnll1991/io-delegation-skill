#!/usr/bin/env python3
"""Fail-closed held-out evidence report for the deterministic activation gate."""
from __future__ import annotations
import argparse, json, math, statistics
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import paired, record

def _get(row,*path):
    cur=row
    for key in path:
        if not isinstance(cur,dict): return None
        cur=cur.get(key)
    return cur

def _pct(left,right):
    if not isinstance(left,(int,float)) or isinstance(left,bool) or left==0: return None
    if not isinstance(right,(int,float)) or isinstance(right,bool): return None
    return (right/left-1)*100

def _median(values):
    vals=[x for x in values if isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)]
    return statistics.median(vals) if vals else None

def evaluate(rows,left='gate-always',right='gate-auto',required_pairs=15,min_valid_pairs=12,
             max_token_overhead_pct=5.0,max_wall_overhead_pct=10.0):
    pairs,incomplete=paired.pair_rows(rows,left,right)
    valid=[]; token_pct=[]; wall_pct=[]
    always_success=auto_success=0
    false_enables=[]; leaked_context=[]; unexpected_calls=[]; quality_regressions=[]
    for key,always,auto in pairs:
        always_success += always.get('success') is True
        auto_success += auto.get('success') is True
        if always.get('success') is True and auto.get('success') is not True:
            quality_regressions.append(auto['run_id'])
        decision=_get(auto,'activation','decision')
        if decision!='bypass': false_enables.append(auto['run_id'])
        if any((_get(auto,'context',field) or 0)>0 for field in ('source_bytes','selected_bytes','result_bytes')):
            leaked_context.append(auto['run_id'])
        if _get(auto,'router','called') is True or (_get(auto,'worker','calls') or 0)>0:
            unexpected_calls.append(auto['run_id'])
        if always.get('success') is True and auto.get('success') is True:
            valid.append(key)
            token_pct.append(_pct(_get(always,'principal','raw_tokens'),_get(auto,'principal','raw_tokens')))
            wall_pct.append(_pct(_get(always,'timing','wall_ms'),_get(auto,'timing','wall_ms')))
    token_median=_median(token_pct); wall_median=_median(wall_pct)
    gates={
        'complete_pairs': len(pairs)>=required_pairs and not incomplete,
        'functional_quality': auto_success>=always_success and not quality_regressions,
        'valid_efficiency_sample': len(valid)>=min_valid_pairs,
        'all_small_controls_bypass': not false_enables,
        'bypass_has_zero_gateway_context': not leaked_context,
        'bypass_has_zero_router_worker_calls': not unexpected_calls,
        'principal_token_overhead': token_median is not None and token_median<=max_token_overhead_pct,
        'wall_time_overhead': wall_median is not None and wall_median<=max_wall_overhead_pct,
    }
    return {
        'schema':'io-context-activation-evidence/v1',
        'left_arm':left,'right_arm':right,
        'pairs':len(pairs),'incomplete_pairs':len(incomplete),'valid_pairs':len(valid),
        'left_successes':always_success,'right_successes':auto_success,
        'false_enable_run_ids':false_enables,
        'context_leak_run_ids':leaked_context,
        'unexpected_call_run_ids':unexpected_calls,
        'quality_regression_run_ids':quality_regressions,
        'principal_token_delta_pct':paired.distribution(token_pct),
        'wall_time_delta_pct':paired.distribution(wall_pct),
        'thresholds':{
            'required_pairs':required_pairs,'min_valid_pairs':min_valid_pairs,
            'max_token_overhead_pct':max_token_overhead_pct,
            'max_wall_overhead_pct':max_wall_overhead_pct,
        },
        'gates':gates,'pass':all(gates.values()),
    }

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('records',type=Path)
    p.add_argument('--left',default='gate-always'); p.add_argument('--right',default='gate-auto')
    p.add_argument('--required-pairs',type=int,default=15); p.add_argument('--min-valid-pairs',type=int,default=12)
    p.add_argument('--max-token-overhead-pct',type=float,default=5.0)
    p.add_argument('--max-wall-overhead-pct',type=float,default=10.0)
    p.add_argument('--output',type=Path)
    a=p.parse_args(argv)
    if a.required_pairs<1 or a.min_valid_pairs<1: p.error('pair thresholds must be positive')
    rows=record.load(a.records)
    result=evaluate(rows,a.left,a.right,a.required_pairs,a.min_valid_pairs,
                    a.max_token_overhead_pct,a.max_wall_overhead_pct)
    text=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if a.output:
        a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(text,encoding='utf-8')
    else: print(text,end='')
    return 0 if result['pass'] else 3

if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError) as exc:
        print('activation evidence: '+str(exc),file=sys.stderr); raise SystemExit(2)
