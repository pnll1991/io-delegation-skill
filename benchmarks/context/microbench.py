#!/usr/bin/env python3
"""Compare one selected-evidence operation, not a complete principal-agent task.

Default is a no-model plan. --live explicitly authorizes the approved configs.
A local HTTP test server in unit tests is a stub, not measured model savings.
"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import sys
import time

SCRIPTS = Path(__file__).resolve().parents[2]/'skills/io-delegation/scripts'
sys.path.insert(0, str(SCRIPTS))
from context_sources import SourceScope, encoded
from context_engine import SemanticEngine


def sequence(request, mode='single', changed_question=None):
    """Explicit phases; repeating the same question is NOT a provider cache test."""
    if mode not in ('single', 'cycle'):
        raise ValueError('Unknown microcomparison mode')
    phases = [('cold', copy.deepcopy(request))]
    if mode == 'cycle':
        if not isinstance(changed_question, str) or not changed_question.strip():
            raise ValueError('cycle requires an explicit changed question')
        if changed_question == request.get('question'):
            raise ValueError('changed question must be different')
        other = copy.deepcopy(request)
        other.pop('questions', None)
        other['question'] = changed_question
        phases += [('repeat-exact', copy.deepcopy(request)), ('changed-question', other)]
    return phases


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--request', type=Path, required=True, help='JSON with selections and question OR questions')
    p.add_argument('--config', type=Path, action='append', required=True, help='Already reviewed config; repeat for another backend')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--mode', choices=['single','cycle'], default='single')
    p.add_argument('--changed-question', help='Different operator-selected question for cycle mode')
    p.add_argument('--no-cache', action='store_true', help='Disable local result reuse; provider cache is independent')
    p.add_argument('--live', action='store_true', help='Authorize the small comparison; uses model quota')
    a = p.parse_args(argv)
    request = json.loads(a.request.read_text(encoding='utf-8-sig'))
    phases = sequence(request, a.mode, a.changed_question)
    names = sorted(set(x['path'] for x in request['selections']))
    scope = SourceScope(a.repo, files=names)
    out = a.output.resolve()
    if out == scope.root or scope.root in out.parents:
        raise ValueError('Output must be outside the project')
    if out.exists() and any(out.iterdir()): raise ValueError('Use a new output directory')
    out.mkdir(parents=True, exist_ok=True)
    (out/'.gitignore').write_text('*\n', encoding='utf-8')
    results = []
    for index, config in enumerate(a.config):
        audit = out/f'backend-{index}'; audit.mkdir()
        engine = SemanticEngine(scope, audit, config, cache=not a.no_cache)
        for phase, query in phases:
            sources, bundle, job = engine.prepare(query)
            row = dict(backend=index, phase=phase, adapter=engine.cfg['adapter'], model=engine.cfg.get('model'),
                       source_bytes=bundle['source_bytes'], selected_bytes=bundle['selected_bytes'],
                       explicit_job_bytes=len(encoded(job)), live=a.live,
                       byte_counts_are_not_tokens=True, provider_cache_guaranteed=False)
            if not a.live:
                row.update(model_calls=0, status='planned')
            else:
                started = time.monotonic(); answer, metrics = engine.run(query)
                row.update(metrics, status=answer['status'], seconds=time.monotonic()-started)
                (audit/(phase+'.answer.json')).write_bytes(encoded(answer)+b'\n')
            results.append(row)
            (out/'results.json').write_bytes(encoded(results)+b'\n')
            print(json.dumps(row), flush=True)
            if a.live and row['status'] not in ('ok', 'partial'):
                print('Stopped: inspect diagnostics before further paid calls.', file=sys.stderr)
                return 2
    return 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('Microcomparison stopped: '+type(exc).__name__, file=sys.stderr); raise SystemExit(2)
