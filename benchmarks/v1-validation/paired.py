#!/usr/bin/env python3
"""Paired causal summaries for Context Gateway validation records."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import statistics

import record

REP_RE = re.compile(r'-r(\d+)$')


def repetition(row):
    match = REP_RE.search(row['run_id'])
    if not match:
        raise ValueError('run_id lacks -rN repetition suffix')
    return int(match.group(1))


def get(row, *path):
    current = row
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def agent_system_tokens(row):
    principal=get(row,'principal','raw_tokens')
    calls=get(row,'worker','calls') or 0
    worker=get(row,'worker','raw_tokens')
    if type(principal) is not int: return None
    if not calls: return principal
    return principal+worker if type(worker) is int else None


def pair_rows(rows, left, right):
    grouped = {}
    for row in rows:
        key = (row['repo'], row['task_id'], repetition(row))
        grouped.setdefault(key, {})[row['arm']] = row
    pairs = []
    incomplete = []
    for key, arms in sorted(grouped.items()):
        if left in arms and right in arms:
            pairs.append((key, arms[left], arms[right]))
        elif left in arms or right in arms:
            incomplete.append(key)
    return pairs, incomplete


def distribution(values):
    vals = sorted(
        value for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    )
    if not vals:
        return {'n': 0, 'p25': None, 'median': None, 'p75': None, 'min': None, 'max': None}
    if len(vals) == 1:
        p25 = p75 = vals[0]
    else:
        q = statistics.quantiles(vals, n=4, method='inclusive')
        p25, p75 = q[0], q[2]
    return {
        'n': len(vals), 'p25': p25, 'median': statistics.median(vals),
        'p75': p75, 'min': vals[0], 'max': vals[-1],
    }


def delta(left, right):
    if not isinstance(left, (int, float)) or isinstance(left, bool):
        return None
    if not isinstance(right, (int, float)) or isinstance(right, bool):
        return None
    return right - left


def percent_delta(left, right):
    if not isinstance(left, (int, float)) or isinstance(left, bool) or left == 0:
        return None
    if not isinstance(right, (int, float)) or isinstance(right, bool):
        return None
    return (right / left - 1) * 100


def summarize(rows, left, right):
    pairs, incomplete = pair_rows(rows, left, right)
    families = {}
    for key, lhs, rhs in pairs:
        family = rhs['family']
        item = families.setdefault(family, {
            'pairs': 0,
            'valid_pairs': 0,
            'principal_token_delta': [],
            'principal_token_delta_pct': [],
            'agent_system_token_delta': [],
            'agent_system_token_delta_pct': [],
            'wall_ms_delta': [],
            'selected_bytes_delta': [],
            'jev_called_pairs': 0,
            'worker_called_pairs': 0,
            'right_successes': 0,
            'left_successes': 0,
        })
        item['pairs'] += 1
        item['left_successes'] += lhs.get('success') is True
        item['right_successes'] += rhs.get('success') is True
        if lhs.get('success') is True and rhs.get('success') is True:
            item['valid_pairs'] += 1
            lt = get(lhs, 'principal', 'raw_tokens')
            rt = get(rhs, 'principal', 'raw_tokens')
            item['principal_token_delta'].append(delta(lt, rt))
            item['principal_token_delta_pct'].append(percent_delta(lt, rt))
            ls=agent_system_tokens(lhs); rs=agent_system_tokens(rhs)
            item['agent_system_token_delta'].append(delta(ls,rs))
            item['agent_system_token_delta_pct'].append(percent_delta(ls,rs))
            item['wall_ms_delta'].append(delta(get(lhs, 'timing', 'wall_ms'), get(rhs, 'timing', 'wall_ms')))
            item['selected_bytes_delta'].append(delta(get(lhs, 'context', 'selected_bytes'), get(rhs, 'context', 'selected_bytes')))
        item['jev_called_pairs'] += get(rhs, 'router', 'called') is True
        item['worker_called_pairs'] += bool(get(rhs, 'worker', 'calls') or 0)

    output = {
        'schema': 'io-context-validation-paired/v1',
        'left_arm': left,
        'right_arm': right,
        'pair_count': len(pairs),
        'incomplete_pair_count': len(incomplete),
        'families': {},
        'warnings': [],
    }
    for family, item in sorted(families.items()):
        output['families'][family] = {
            'pairs': item['pairs'],
            'valid_pairs': item['valid_pairs'],
            'left_successes': item['left_successes'],
            'right_successes': item['right_successes'],
            'principal_token_delta': distribution(item['principal_token_delta']),
            'principal_token_delta_pct': distribution(item['principal_token_delta_pct']),
            'agent_system_token_delta': distribution(item['agent_system_token_delta']),
            'agent_system_token_delta_pct': distribution(item['agent_system_token_delta_pct']),
            'wall_ms_delta': distribution(item['wall_ms_delta']),
            'selected_bytes_delta': distribution(item['selected_bytes_delta']),
            'jev_called_pairs': item['jev_called_pairs'],
            'worker_called_pairs': item['worker_called_pairs'],
        }
    right_rows = [row for row in rows if row['arm'] == right]
    if right_rows and not any(get(row, 'router', 'called') is True for row in right_rows):
        output['warnings'].append('Right arm never called Jev; do not attribute deltas to Jev.')
    if right_rows and not any((get(row, 'worker', 'calls') or 0) > 0 for row in right_rows):
        output['warnings'].append('Right arm never called the worker; do not attribute deltas to the worker.')
    if incomplete:
        output['warnings'].append('Incomplete pairs are excluded from paired deltas.')
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('records', type=Path)
    parser.add_argument('--left', required=True)
    parser.add_argument('--right', required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    rows = record.load(args.records)
    result = summarize(rows, args.left, args.right)
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    else:
        print(text, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
