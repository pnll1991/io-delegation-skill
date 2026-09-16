#!/usr/bin/env python3
"""Run labeled routing cases against the optional TypeSafe Jev decision router."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import time

REPO = Path(__file__).resolve().parents[2]
ROUTER_SCRIPT = REPO / 'skills/io-delegation/scripts/decision_router.py'
spec = importlib.util.spec_from_file_location('decision_router', ROUTER_SCRIPT)
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)


def load_cases(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, list) or not value:
        raise ValueError('cases must be a non-empty JSON array')
    ids = set()
    for row in value:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
            raise ValueError('every case needs a non-empty id')
        if row['id'] in ids:
            raise ValueError('case ids must be unique')
        ids.add(row['id'])
        if row.get('expected') not in router.ROUTES:
            raise ValueError(f"invalid expected route in {row['id']}")
        if not isinstance(row.get('task'), str) or not isinstance(row.get('paths'), list):
            raise ValueError(f"invalid task or paths in {row['id']}")
    return value


def run_case(root: Path, cfg: dict, case: dict, dry_run: bool) -> dict:
    state = router.build_state(
        root,
        case['task'],
        case['paths'],
        operation=case.get('operation', 'unknown'),
        search_results=case.get('search_results'),
        known_symbols=case.get('known_symbols'),
    )
    if dry_run:
        return {
            'id': case['id'],
            'expected': case['expected'],
            'status': 'dry_run',
            'state': state,
        }
    started = time.perf_counter()
    result = router.call_typesafe(router.build_payload(state, cfg), cfg)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        'id': case['id'],
        'expected': case['expected'],
        'status': result['status'],
        'route': result['route'],
        'model_route': result['model_route'],
        'confidence': result['confidence'],
        'delegation_useful': result['delegation_useful'],
        'reasoning_required': result['reasoning_required'],
        'probabilities': result['probabilities'],
        'usage': result['usage'],
        'elapsed_ms': elapsed_ms,
        'raw_match': result['model_route'] == case['expected'],
        'effective_match': result['route'] == case['expected'],
    }


def summarize(rows: list[dict], dry_run: bool) -> dict:
    if dry_run:
        return {'cases': len(rows), 'mode': 'dry_run'}
    confidences = [row['confidence'] for row in rows]
    latencies = [row['elapsed_ms'] for row in rows]
    input_tokens = [row['usage']['input_tokens'] for row in rows
                    if row['usage']['input_tokens'] is not None]
    output_tokens = [row['usage']['output_tokens'] for row in rows
                     if row['usage']['output_tokens'] is not None]
    raw_matches = sum(row['raw_match'] for row in rows)
    effective_matches = sum(row['effective_match'] for row in rows)
    fallbacks = sum(row['route'] == 'current_rules' for row in rows)
    routes = {}
    for row in rows:
        routes[row['model_route']] = routes.get(row['model_route'], 0) + 1
    return {
        'cases': len(rows),
        'mode': 'live',
        'raw_match_rate': raw_matches / len(rows),
        'effective_match_rate': effective_matches / len(rows),
        'low_confidence_fallbacks': fallbacks,
        'mean_confidence': statistics.fmean(confidences),
        'mean_latency_ms': statistics.fmean(latencies),
        'p95_latency_ms': sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)],
        'input_tokens': sum(input_tokens) if input_tokens else None,
        'output_tokens': sum(output_tokens) if output_tokens else None,
        'model_route_counts': routes,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(REPO))
    parser.add_argument('--config', required=True)
    parser.add_argument('--cases', default=str(Path(__file__).with_name('cases.json')))
    parser.add_argument('--output')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        root = Path(args.root).resolve(strict=True)
        cfg = router.load_config(args.config)
        cases = load_cases(Path(args.cases))
        rows = [run_case(root, cfg, case, args.dry_run) for case in cases]
        report = {'summary': summarize(rows, args.dry_run), 'results': rows}
        text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding='utf-8')
        else:
            sys.stdout.write(text)
        return 0
    except (OSError, ValueError, router.RouterError) as exc:
        print(json.dumps({'component': 'typesafe-router-benchmark', 'status': 'error',
                          'error': str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
