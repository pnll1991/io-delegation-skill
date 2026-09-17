#!/usr/bin/env python3
"""Calibrate Jev confidence acceptance on one labeled case per route, confirm on held-out cases."""
from __future__ import annotations

import argparse
from collections import defaultdict
import importlib.util
import json
from pathlib import Path
import statistics
import sys

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
spec=importlib.util.spec_from_file_location('router_bench',HERE/'run.py')
bench=importlib.util.module_from_spec(spec); spec.loader.exec_module(bench)
router=bench.router


def split_cases(cases):
    grouped=defaultdict(list)
    for case in cases: grouped[case['expected']].append(case)
    calibration=[]; holdout=[]
    for route in sorted(router.ROUTES):
        rows=sorted(grouped.get(route,[]),key=lambda x:x['id'])
        if len(rows)<2: raise ValueError(f'need at least two labeled cases for {route}')
        calibration.append(rows[0]); holdout.append(rows[1])
    return calibration,holdout


def score(rows,threshold):
    accepted=[row for row in rows if row['confidence']>=threshold]
    errors=[row for row in accepted if row['model_route']!=row['expected']]
    correct=len(accepted)-len(errors)
    return {
        'threshold':threshold,'cases':len(rows),'accepted':len(accepted),'fallbacks':len(rows)-len(accepted),
        'coverage':len(accepted)/len(rows) if rows else 0.0,
        'accepted_accuracy':correct/len(accepted) if accepted else None,
        'accepted_errors':len(errors),'error_ids':[row['id'] for row in errors],
    }


def choose_threshold(rows):
    candidates=[round(x/100,2) for x in range(50,100)]
    scored=[score(rows,value) for value in candidates]
    zero=[row for row in scored if row['accepted_errors']==0 and row['accepted']>0]
    if not zero: raise ValueError('no confidence threshold yields an error-free accepted calibration set')
    zero.sort(key=lambda row:(-row['coverage'],row['threshold']))
    return zero[0],scored


def usage_summary(rows):
    input_tokens=[row['usage'].get('input_tokens') for row in rows if isinstance(row.get('usage'),dict)]
    output_tokens=[row['usage'].get('output_tokens') for row in rows if isinstance(row.get('usage'),dict)]
    latencies=[row.get('elapsed_ms') for row in rows if isinstance(row.get('elapsed_ms'),(int,float))]
    known_in=[x for x in input_tokens if type(x) is int]
    known_out=[x for x in output_tokens if type(x) is int]
    return {
        'input_tokens':sum(known_in) if len(known_in)==len(input_tokens) and input_tokens else None,
        'output_tokens':sum(known_out) if len(known_out)==len(output_tokens) and output_tokens else None,
        'median_latency_ms':statistics.median(latencies) if latencies else None,
    }


def execute(root,cfg,cases,repetitions,dry_run):
    rows=[]
    for repetition in range(1,repetitions+1):
        for case in cases:
            row=bench.run_case(root,cfg,case,dry_run)
            row['repetition']=repetition; rows.append(row)
    return rows


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',default=str(REPO)); p.add_argument('--config',required=True)
    p.add_argument('--cases',default=str(HERE/'cases.json')); p.add_argument('--repetitions',type=int,default=3)
    p.add_argument('--output'); p.add_argument('--dry-run',action='store_true')
    a=p.parse_args(argv)
    try:
        if a.repetitions<1: raise ValueError('repetitions must be positive')
        root=Path(a.root).resolve(strict=True); cfg=router.load_config(a.config); cases=bench.load_cases(Path(a.cases))
        calibration_cases,holdout_cases=split_cases(cases)
        if a.dry_run:
            report={'mode':'dry_run','model_calls':0,'calibration_ids':[x['id'] for x in calibration_cases],'holdout_ids':[x['id'] for x in holdout_cases]}
        else:
            calibration=execute(root,cfg,calibration_cases,a.repetitions,False)
            chosen,grid=choose_threshold(calibration)
            holdout=execute(root,cfg,holdout_cases,a.repetitions,False)
            report={
                'mode':'live','repetitions':a.repetitions,'chosen_threshold':chosen['threshold'],
                'calibration':chosen,'holdout':score(holdout,chosen['threshold']),
                'calibration_usage':usage_summary(calibration),'holdout_usage':usage_summary(holdout),
                'calibration_ids':[x['id'] for x in calibration_cases],'holdout_ids':[x['id'] for x in holdout_cases],
                'grid':grid,
            }
        text=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        if a.output:
            target=Path(a.output); target.parent.mkdir(parents=True,exist_ok=True); target.write_text(text,encoding='utf-8')
        else: print(text,end='')
        return 0
    except (OSError,ValueError,router.RouterError) as exc:
        print(json.dumps({'component':'typesafe-router-calibration','status':'error','error':str(exc)}),file=sys.stderr); return 2

if __name__=='__main__': raise SystemExit(main())
