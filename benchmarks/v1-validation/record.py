#!/usr/bin/env python3
"""Versioned metadata-only run records for Context Gateway validation."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import statistics

SCHEMA = 'io-context-validation/v1'
MAX_RECORD_BYTES = 1_000_000
SECRET_ENV = re.compile(r'(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)', re.I)
ALLOWED_TOP = {
    'schema', 'run_id', 'task_id', 'family', 'repo', 'commit', 'arm', 'host',
    'started_at', 'ended_at', 'success', 'activation', 'route', 'principal',
    'router', 'worker', 'context', 'timing', 'validator', 'conditions', 'rework', 'errors',
    'notes', 'tags',
}
FORBIDDEN_KEYS = {
    'source', 'source_text', 'raw_source', 'file_contents', 'contents',
    'credential', 'credentials', 'api_key', 'password', 'secret',
}
TOKEN_KEYS = (
    'input_tokens', 'output_tokens', 'cached_input_tokens',
    'cache_write_input_tokens', 'reasoning_output_tokens',
)
CONDITION_VALUES={
    'principal_process': {'cold','warm','unknown'},
    'context_cache': {'cold','warm','disabled','unknown'},
    'provider_cache': {'reported','unreported','unknown'},
    'worktree': {'fresh','reused','unknown'},
}


def _int_or_none(value):
    return value is None or (type(value) is int and value >= 0)


def _num_or_none(value):
    return value is None or (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value) and value >= 0
    )


def secret_values(env=None):
    env = env or os.environ
    values = []
    for key, value in env.items():
        if SECRET_ENV.search(str(key)) and isinstance(value, str) and len(value) >= 6:
            values.append(value)
    return sorted(set(values), key=len, reverse=True)


def redact(value, secrets=None):
    secrets = secrets if secrets is not None else secret_values()
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, '[REDACTED]')
        return value
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: redact(item, secrets) for key, item in value.items()}
    return value


def _walk_keys(value, path=''):
    if isinstance(value, dict):
        for key, item in value.items():
            low = str(key).lower()
            if low in FORBIDDEN_KEYS:
                raise ValueError(f'Forbidden persisted field: {path}{key}')
            _walk_keys(item, path + str(key) + '.')
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk_keys(item, path + str(index) + '.')


def _usage(value, label):
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError(label + ' must be object or null')
    for key in TOKEN_KEYS:
        if not _int_or_none(value.get(key)):
            raise ValueError(f'{label}.{key} invalid')


def _conditions(value):
    if value is None: return
    if not isinstance(value,dict): raise ValueError('conditions invalid')
    extra=set(value)-set(CONDITION_VALUES)
    if extra: raise ValueError('unknown condition fields: '+', '.join(sorted(extra)))
    for key,allowed in CONDITION_VALUES.items():
        if key in value and value[key] not in allowed:
            raise ValueError('conditions.'+key+' invalid')


def validate(record):
    if not isinstance(record, dict):
        raise ValueError('record must be an object')
    extra = set(record) - ALLOWED_TOP
    if extra:
        raise ValueError('unknown top-level fields: ' + ', '.join(sorted(extra)))
    if record.get('schema') != SCHEMA:
        raise ValueError('unsupported schema')
    for key in ('run_id', 'task_id', 'family', 'repo', 'commit', 'arm', 'host'):
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise ValueError(key + ' required')
    if record.get('success') not in (True, False, None):
        raise ValueError('success invalid')
    for key in ('started_at', 'ended_at'):
        if record.get(key) is not None and not _num_or_none(record[key]):
            raise ValueError(key + ' invalid')

    activation = record.get('activation')
    if activation is not None:
        if not isinstance(activation, dict) or activation.get('decision') not in ('enable', 'bypass', None):
            raise ValueError('activation invalid')
    route = record.get('route')
    if route is not None:
        if not isinstance(route, dict):
            raise ValueError('route invalid')
        confidence = route.get('confidence')
        if confidence is not None and (not _num_or_none(confidence) or confidence > 1):
            raise ValueError('route.confidence invalid')

    principal = record.get('principal')
    if principal is not None:
        if not isinstance(principal, dict):
            raise ValueError('principal invalid')
        _usage(principal.get('usage'), 'principal.usage')
        if not _int_or_none(principal.get('raw_tokens')):
            raise ValueError('principal.raw_tokens invalid')
    router = record.get('router')
    if router is not None:
        if not isinstance(router, dict) or router.get('called') not in (True, False, None):
            raise ValueError('router invalid')
        _usage(router.get('usage'), 'router.usage')
        if not _num_or_none(router.get('latency_ms')):
            raise ValueError('router.latency_ms invalid')
    worker = record.get('worker')
    if worker is not None:
        if not isinstance(worker, dict):
            raise ValueError('worker invalid')
        for key in ('calls', 'accepted', 'rejected'):
            if not _int_or_none(worker.get(key)):
                raise ValueError('worker.' + key + ' invalid')
        _usage(worker.get('usage'), 'worker.usage')
        if not _int_or_none(worker.get('raw_tokens')):
            raise ValueError('worker.raw_tokens invalid')
    context = record.get('context')
    if context is not None:
        if not isinstance(context, dict):
            raise ValueError('context invalid')
        for key in ('source_bytes', 'selected_bytes', 'result_bytes'):
            if not _int_or_none(context.get(key)):
                raise ValueError('context.' + key + ' invalid')
    timing = record.get('timing')
    if timing is not None and (
        not isinstance(timing, dict) or not _num_or_none(timing.get('wall_ms'))
    ):
        raise ValueError('timing invalid')
    validator = record.get('validator')
    if validator is not None and (
        not isinstance(validator, dict)
        or validator.get('status') not in ('pass', 'fail', 'error', 'timeout', 'human_review', None)
    ):
        raise ValueError('validator invalid')
    _conditions(record.get('conditions'))
    if not _int_or_none(record.get('rework')):
        raise ValueError('rework invalid')
    if record.get('errors') is not None and not isinstance(record['errors'], list):
        raise ValueError('errors invalid')

    _walk_keys(record)
    raw = json.dumps(record, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError('record exceeds size budget')
    return record


def sanitized(record, secrets=None):
    row = redact(record, secrets)
    validate(row)
    return row


def append(path, record, secrets=None):
    row = sanitized(record, secrets)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n')
    return row


def load(path):
    path = Path(path)
    rows = []
    if not path.is_file():
        return rows
    if path.stat().st_size > 64_000_000:
        raise ValueError('run file exceeds analysis budget')
    with path.open(encoding='utf-8-sig') as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise ValueError(f'malformed JSONL line {number}') from exc
            validate(row)
            rows.append(row)
    ids = [row['run_id'] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate run_id')
    return rows


def _quartiles(values):
    vals = sorted(
        value for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value)
    )
    if not vals:
        return {'p25': None, 'median': None, 'p75': None}
    if len(vals) == 1:
        return {'p25': vals[0], 'median': vals[0], 'p75': vals[0]}
    quartiles = statistics.quantiles(vals, n=4, method='inclusive')
    return {'p25': quartiles[0], 'median': statistics.median(vals), 'p75': quartiles[2]}


def _get(row, *path):
    current = row
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _selection_ratio(row):
    source = _get(row, 'context', 'source_bytes')
    selected = _get(row, 'context', 'selected_bytes')
    if type(source) is int and source > 0 and type(selected) is int:
        return selected / source
    return None


def _agent_system_tokens(row):
    principal=_get(row,'principal','raw_tokens')
    calls=_get(row,'worker','calls') or 0
    worker=_get(row,'worker','raw_tokens')
    if type(principal) is not int: return None
    if not calls: return principal
    return principal+worker if type(worker) is int else None


def _route_counts(items, key):
    counts={}
    for item in items:
        value=_get(item,'route',key)
        if value is not None: counts[value]=counts.get(value,0)+1
    return counts


def _condition_counts(items):
    result={}
    for key in CONDITION_VALUES:
        counts={}
        for item in items:
            value=_get(item,'conditions',key) or 'unknown'
            counts[value]=counts.get(value,0)+1
        result[key]=counts
    return result


def _arm_summary(items):
    router_calls=sum(_get(item,'router','called') is True for item in items)
    fallbacks=sum(bool(_get(item,'route','fallback')) for item in items)
    bulk=sum(_get(item,'route','model_route') == 'bulk_read' for item in items)
    followed=sum(_get(item,'route','model_route') == 'bulk_read' and (_get(item,'worker','calls') or 0)>0 for item in items)
    return {
        'runs': len(items),
        'success_rate': sum(item.get('success') is True for item in items) / len(items),
        'principal_tokens': _quartiles([_get(item, 'principal', 'raw_tokens') for item in items]),
        'agent_system_tokens': _quartiles([_agent_system_tokens(item) for item in items]),
        'wall_ms': _quartiles([_get(item, 'timing', 'wall_ms') for item in items]),
        'selected_bytes': _quartiles([_get(item, 'context', 'selected_bytes') for item in items]),
        'source_bytes': _quartiles([_get(item, 'context', 'source_bytes') for item in items]),
        'selected_source_ratio': _quartiles([_selection_ratio(item) for item in items]),
        'route_counts': _route_counts(items,'effective'),
        'model_route_counts': _route_counts(items,'model_route'),
        'router_calls': router_calls,
        'worker_calls': sum((_get(item, 'worker', 'calls') or 0) for item in items),
        'fallbacks': fallbacks,
        'fallback_rate': fallbacks/router_calls if router_calls else None,
        'bulk_recommendations': bulk,
        'worker_followed_bulk': followed,
        'worker_follow_rate': followed/bulk if bulk else None,
        'unknown_principal_usage': sum(_get(item, 'principal', 'raw_tokens') is None for item in items),
        'unknown_router_usage': sum(
            _get(item, 'router', 'called') is True and _get(item, 'router', 'usage') is None
            for item in items
        ),
        'unknown_worker_usage': sum(
            (_get(item, 'worker', 'calls') or 0) > 0 and _get(item, 'worker', 'usage') is None
            for item in items
        ),
        'conditions': _condition_counts(items),
    }


def summarize(rows):
    groups = {}
    for row in rows:
        groups.setdefault((row['family'], row['arm']), []).append(row)
    result = {
        'schema': 'io-context-validation-summary/v1',
        'runs': len(rows),
        'families': {},
        'overall': {},
    }
    for (family, arm), items in sorted(groups.items()):
        result['families'].setdefault(family, {})[arm] = _arm_summary(items)
    for arm in sorted(set(row['arm'] for row in rows)):
        result['overall'][arm] = _arm_summary([row for row in rows if row['arm'] == arm])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    validate_cmd = sub.add_parser('validate')
    validate_cmd.add_argument('path')
    summary_cmd = sub.add_parser('summary')
    summary_cmd.add_argument('path')
    summary_cmd.add_argument('--output')
    args = parser.parse_args(argv)
    if args.command == 'validate':
        rows = load(args.path)
        print(json.dumps({'valid': True, 'runs': len(rows)}))
        return 0
    rows = load(args.path)
    output = summarize(rows)
    text = json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
    else:
        print(text, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
