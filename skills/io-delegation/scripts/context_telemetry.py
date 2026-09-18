"""Metadata-only accounting. Tool items and completed turns are NOT inference calls.

Unknown usage is retained as unknown; known rejected usage stays in the sum.
The CLI may not expose internal model requests: model_requests remains None.
"""
from __future__ import annotations
import json
from pathlib import Path
from worker_runtime import USAGE_KEYS, normalize_usage, usage_complete

MAX_LOG_BYTES = 32_000_000


def events(path):
    path=Path(path)
    if not path.is_file():return [],0
    if path.stat().st_size>MAX_LOG_BYTES:raise ValueError('Log exceeds analysis budget')
    rows=[];errors=0
    with path.open(encoding='utf-8-sig',errors='replace') as f:
        for line in f:
            if not line.strip():continue
            try:
                item=json.loads(line)
                if not isinstance(item,dict):raise ValueError()
                rows.append(item)
            except ValueError:errors+=1
    return rows,errors


def valid_usage(u):
    if not usage_complete(u):return False
    return not ((u.get('cached_input_tokens') is not None and u['cached_input_tokens']>u['input_tokens']) or
                (u.get('reasoning_output_tokens') is not None and u['reasoning_output_tokens']>u['output_tokens']))


def trajectory(path):
    rows,malformed=events(path)
    totals=dict.fromkeys(USAGE_KEYS,0)
    turns=0;complete=malformed==0;failure=False;items={};semantic=0;native=0
    for e in rows:
        if e.get('type') in ('error','turn.failed'):failure=True
        if e.get('type')=='turn.completed':
            turns+=1;u=normalize_usage(e.get('usage'));complete &= valid_usage(u)
            for k in USAGE_KEYS:
                totals[k]=totals[k]+u[k] if totals[k] is not None and u[k] is not None else None
        if e.get('type')!='item.completed' or not isinstance(e.get('item'),dict):continue
        item=e['item'];iid=item.get('id')
        if iid is None:iid='unidentified-'+str(len(items))
        items[iid]=item
    commands=mcp=tool_bytes=0
    for item in items.values():
        kind=item.get('type')
        if kind=='command_execution':
            commands+=1;tool_bytes+=len(str(item.get('aggregated_output','')).encode('utf-8'))
        elif kind=='mcp_tool_call':
            mcp+=1
            tool=str(item.get('tool',item.get('name','')))
            semantic+=tool in ('semantic_query','bulk_read') or tool.endswith('__semantic_query') or tool.endswith('__bulk_read')
            tool_bytes+=len(json.dumps(item.get('result'),ensure_ascii=False).encode('utf-8'))
        elif kind in ('collab_tool_call','agent_tool_call'):native+=1
    complete=complete and turns>0 and not failure
    return dict(**totals,raw_tokens=totals['input_tokens']+totals['output_tokens'] if valid_usage(totals) and turns else None,
                usage_complete=bool(complete),completed_user_turns=turns,model_requests=None,
                model_requests_reason='Internal inference count is not established by turn.completed or tool events',
                command_items=commands,mcp_items=mcp,semantic_tool_items=semantic,native_subagent_items=native,
                tool_result_bytes=tool_bytes,malformed_events=malformed,turn_failed=failure)


def workers(path):
    rows,malformed=events(path);groups={};seen={};invalid=malformed
    for e in rows:
        if e.get('schema')!='io-worker/v2' or not isinstance(e.get('call_id'),str) or type(e.get('seq')) is not int:
            invalid+=1;continue
        key=(e['call_id'],e['seq'])
        if key in seen:
            if seen[key]!=e:invalid+=1
            continue
        seen[key]=e;groups.setdefault(e['call_id'],[]).append(e)
    totals=dict.fromkeys(USAGE_KEYS,0)
    calls=accepted=failed=unknown=0
    for es in groups.values():
        names=[e.get('event') for e in es]
        dispatched='worker_dispatched' in names
        calls+=dispatched
        accepted+=any(e.get('event')=='worker_completed' and e.get('accepted') is True for e in es)
        failed+=any(e.get('event')=='worker_error' or (e.get('event')=='worker_completed' and e.get('accepted') is not True) for e in es)
        if 'worker_attempt' not in names or len(set(names))!=len(names):invalid+=1
        if not any(n in names for n in ('worker_completed','worker_error')):invalid+=1
        if not dispatched:continue
        responses=[e for e in es if e.get('event')=='worker_response']
        u=normalize_usage(responses[-1].get('usage') if responses else None)
        if not valid_usage(u):unknown+=1
        for k in USAGE_KEYS:
            totals[k]=totals[k]+u[k] if totals[k] is not None and u[k] is not None else None
    complete=invalid==0 and unknown==0
    raw=totals['input_tokens']+totals['output_tokens'] if valid_usage(totals) else None
    return dict(attempts=len(groups),calls=calls,accepted=accepted,failures=failed,
                unknown_calls=unknown,invalid_records=invalid,accounting_complete=complete,
                raw_tokens=raw if complete else None,usage=totals)


def operations(path):
    rows,invalid=events(path);started=set();done={}
    for e in rows:
        if e.get('schema')!='io-context/v1' or not isinstance(e.get('operation_id'),str):invalid+=1;continue
        iid=e['operation_id']
        if e.get('event')=='operation_started':started.add(iid)
        elif e.get('event')=='operation_completed':
            if iid in done and e!=done[iid]:invalid+=1
            done[iid]=e
        else:invalid+=1
    counts={};tiers={};cache_hits=source=selected=returned=dispatch=orchestrator_calls=escalations=0
    control_tokens=0;control_usage_complete=True
    for e in done.values():
        key=e.get('operation');counts[key]=counts.get(key,0)+1
        cache_hits+=e.get('cache')=='hit'
        for k in ('source_bytes','selected_bytes','result_bytes','model_calls','router_calls','orchestrator_calls'):
            if k in e and (type(e[k]) is not int or e[k]<0):invalid+=1
        source+=e.get('source_bytes',0);selected+=e.get('selected_bytes',0)
        returned+=e.get('result_bytes',0);dispatch+=e.get('model_calls',0)
        orchestrator_calls+=e.get('orchestrator_calls',0)
        if 'escalated' in e and type(e['escalated']) is not bool:invalid+=1
        escalations+=e.get('escalated') is True
        tier=e.get('compute_tier')
        if tier is not None:
            if tier not in ('T0','T1','T2'):invalid+=1
            else:tiers[tier]=tiers.get(tier,0)+1
        for prefix in ('router','orchestrator'):
            calls=e.get(prefix+'_calls',0)
            if calls:
                inp=e.get(prefix+'_input_tokens');out=e.get(prefix+'_output_tokens')
                if type(inp) is int and inp>=0 and type(out) is int and out>=0:
                    control_tokens+=inp+out
                else:
                    control_usage_complete=False
    return dict(counts=counts,completed=len(done),cache_hits=cache_hits,source_bytes=source,
                selected_bytes=selected,result_bytes=returned,model_calls=dispatch,
                orchestrator_calls=orchestrator_calls,escalations=escalations,compute_tiers=tiers,
                control_tokens=control_tokens if control_usage_complete else None,
                control_usage_complete=control_usage_complete,
                complete=not invalid and started==set(done),invalid_records=invalid,
                byte_counts_are_not_tokens=True)


def combined(main_path, audit_root):
    audit=Path(audit_root)
    main=trajectory(main_path);worker=workers(audit/'.io-delegation/worker-events.jsonl')
    ops=operations(audit/'context-events.jsonl')
    complete=(main['usage_complete'] and worker['accounting_complete'] and ops['complete']
              and ops['control_usage_complete'] and not main['native_subagent_items'])
    # A semantic tool can legitimately hit cache; use the operation journal, not tool count, for dispatch.
    if ops['model_calls']!=worker['calls']:complete=False
    if main['semantic_tool_items'] and ops['counts'].get('semantic_query',0)<main['semantic_tool_items']:complete=False
    raw=(main['raw_tokens']+worker['raw_tokens']+ops['control_tokens']
         if complete and main['raw_tokens'] is not None and worker['raw_tokens'] is not None
         and ops['control_tokens'] is not None else None)
    return dict(main=main,worker=worker,operations=ops,system_accounting_complete=bool(complete),system_raw_tokens=raw)
