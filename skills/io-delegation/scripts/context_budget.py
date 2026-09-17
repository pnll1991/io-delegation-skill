"""Hard byte/call bounds and an honest between-call reported-token budget.

Unknown dispatched usage freezes further inference. CLI hidden prompts/output
cannot be capped exactly here: max_task_tokens is a post-response circuit breaker,
not a guarantee that the last call cannot cross it. Cached results need no dispatch.
"""
from __future__ import annotations
import json
from pathlib import Path
from context_sources import plain_int
from worker_runtime import normalize_usage, usage_complete

DEFAULTS = dict(max_selected_bytes=24_000, max_request_bytes=40_000,
                max_output_tokens=1200, max_task_tokens=80_000, max_calls=4)
RANGES = dict(max_selected_bytes=(256,24_000), max_request_bytes=(2048,64_000),
              max_output_tokens=(64,4096), max_task_tokens=(1,1_000_000), max_calls=(1,4))


class BudgetError(ValueError):
    pass


def limits(cfg):
    custom = cfg.get('context_limits', {})
    if not isinstance(custom, dict) or set(custom)-set(DEFAULTS):
        raise ValueError('Unknown context limit')
    result = dict(DEFAULTS, **custom)
    for key,value in result.items():plain_int(value,*RANGES[key],key)
    result['max_calls'] = min(result['max_calls'],cfg.get('max_calls_per_workspace',4))
    return result


def observed(journal_path):
    path = Path(journal_path)
    if not path.exists():return dict(calls=0,tokens=0,unknown=False)
    groups,seen = {},set()
    try:
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if not line.strip():continue
            e=json.loads(line)
            if e.get('schema')!='io-worker/v2' or not isinstance(e.get('call_id'),str):raise ValueError()
            pair=(e['call_id'],e.get('seq'))
            if pair in seen:continue
            seen.add(pair);groups.setdefault(e['call_id'],[]).append(e)
    except (ValueError,TypeError,KeyError,AttributeError):
        return dict(calls=0,tokens=0,unknown=True)
    calls=tokens=0;unknown=False
    for events in groups.values():
        names=[e.get('event') for e in events]
        if 'worker_dispatched' not in names:continue
        calls+=1
        responses=[e for e in events if e.get('event')=='worker_response']
        u=normalize_usage(responses[-1].get('usage')) if responses else {}
        if not usage_complete(u):unknown=True
        else:tokens+=u['input_tokens']+u['output_tokens']
        if not any(x in names for x in ('worker_completed','worker_error')):unknown=True
    return dict(calls=calls,tokens=tokens,unknown=unknown)


def before_dispatch(journal, cfg, selected_bytes, request_bytes):
    cap=limits(cfg)
    if selected_bytes>cap['max_selected_bytes'] or request_bytes>cap['max_request_bytes']:
        raise BudgetError('Selected/request bytes exceed approved budget; narrow selectors')
    state=observed(journal.path)
    if state['unknown']:
        raise BudgetError('Prior dispatched usage is unknown/incomplete; stop and review')
    if state['calls']>=cap['max_calls']:
        raise BudgetError('Worker call budget exhausted')
    if state['tokens']>=cap['max_task_tokens']:
        raise BudgetError('Reported task-token budget exhausted')
    return cap,state
