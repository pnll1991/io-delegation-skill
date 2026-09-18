"""Smart context routing and cheap-first compute orchestration for public query.

Routing and compute scoring see task text plus aggregate metadata, never source
contents or names. Selected source fragments stay local unless an approved worker
is dispatched.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import time

import decision_router as jev
import context_orchestrator as compute
from context_selection import MAX_SELECTED_BYTES, grouped_question, select
from context_sources import exact_keys, source_table
from context_engine import SemanticEngine

ROUTER_OPERATIONS = {
    'unknown', 'exploration', 'factual', 'generation',
    'debugging', 'architecture', 'security', 'editing'
}
DEFAULT_WORKER_AUTO_DISPATCH = False


def _external_config(path, root):
    path = Path(path).absolute()
    info = path.lstat()
    if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Router configuration itself cannot be a symlink or reparse point')
    resolved = path.resolve(strict=True)
    root = Path(root).resolve(strict=True)
    if resolved == root or root in resolved.parents:
        raise ValueError('Router configuration must be outside project')
    return resolved


class JevRouter:
    def __init__(self, root, config):
        self.root = Path(root).resolve(strict=True)
        self.path = _external_config(config, self.root)
        self.cfg = jev.load_config(str(self.path))
        self.digest = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def _config(self):
        current = hashlib.sha256(self.path.read_bytes()).hexdigest()
        if current != self.digest:
            raise ValueError('Router configuration changed; restart after review')
        return self.cfg

    def run(self, task, paths, operation='unknown', search_results=None, known_symbols=None):
        cfg = self._config()
        state = jev.build_state(self.root, task, paths, operation=operation,
                                search_results=search_results, known_symbols=known_symbols)
        started = time.monotonic()
        result = jev.call_typesafe(jev.build_payload(state, cfg), cfg)
        result['elapsed_ms'] = round((time.monotonic() - started) * 1000)
        return result


def _question(arguments):
    if ('question' in arguments) == ('questions' in arguments):
        raise ValueError('Specify question OR related questions')
    return arguments['question'] if 'question' in arguments else grouped_question(arguments['questions'])


def _prepare(scope, arguments):
    exact_keys(arguments, ('selections',),
               ('question', 'questions', 'operation', 'search_results', 'known_symbols'))
    question = _question(arguments)
    operation = arguments.get('operation', 'unknown')
    if operation not in ROUTER_OPERATIONS:
        raise ValueError('Unknown query operation')
    selections = arguments['selections']
    if not isinstance(selections, list) or not selections:
        raise ValueError('Explicit selections required')
    names = []
    for item in selections:
        exact_keys(item, ('path', 'select'))
        names.append(item['path'])
    paths = sorted(set(names))
    sources = scope.load(paths)
    return sources, selections, paths, question, operation


def _heuristic(operation, paths, source_bytes, worker_available):
    if operation in {'debugging', 'architecture', 'security', 'editing', 'generation'}:
        return 'principal'
    if len(paths) >= 3 and worker_available and source_bytes >= 2048:
        return 'bulk_read'
    return 'targeted_read'


def _evidence(bundle, route, *, recommended=None, reason=None):
    fragments = [{k: item[k] for k in ('ref', 'source', 'start', 'end', 'content')}
                 for item in bundle['fragments']]
    result = {
        'status': 'ok' if bundle['status'] == 'ok' else 'insufficient_context',
        'route': route,
        'recommended_route': recommended or route,
        'evidence': fragments,
        'sources': bundle['sources'],
        'coverage': bundle['coverage'],
        'selected_bytes': bundle['selected_bytes'],
        'action': ('Reason in the principal agent from this bounded evidence.'
                   if route == 'principal' else
                   'Use these bounded fragments directly; no semantic worker was called.'),
        'model_calls': 0,
    }
    if reason:
        result['reason'] = reason
    return result


def _semantic_args(arguments, selections):
    row = {'selections': selections}
    if 'question' in arguments:
        row['question'] = arguments['question']
    else:
        row['questions'] = arguments['questions']
    return row


class QueryEngine:
    def __init__(self, scope, audit, worker_config=None, router_config=None,
                 orchestrator_config=None, cache=True):
        self.scope = scope
        self.semantic = (SemanticEngine(scope, audit, worker_config, cache=cache)
                         if worker_config is not None else None)
        self.worker_auto_dispatch = bool(
            self.semantic and self.semantic.cfg.get(
                'context_auto_dispatch', DEFAULT_WORKER_AUTO_DISPATCH
            ) is True
        )
        self.router = JevRouter(scope.root, router_config) if router_config is not None else None
        self.orchestrator = (
            compute.JevComputeOrchestrator(scope.root, orchestrator_config)
            if orchestrator_config is not None else None
        )

    def _principal_bundle(self, sources, selections, direct_limit, reason, recommended='principal'):
        bundle = select(sources, selections, max_bytes=direct_limit)
        return bundle, _evidence(bundle, 'principal', recommended=recommended, reason=reason)

    def run(self, arguments):
        worker_limit = self.semantic.limits['max_selected_bytes'] if self.semantic else MAX_SELECTED_BYTES
        direct_limit = min(12_000, worker_limit)
        sources, selections, paths, question, operation = _prepare(self.scope, arguments)
        source_bytes = sum(item['bytes'] for item in sources)
        metrics = {
            'route': 'query', 'source_bytes': source_bytes,
            'selected_bytes': 0, 'model_calls': 0,
            'router_calls': 0, 'router_route': None, 'router_confidence': None,
            'router_input_tokens': None, 'router_output_tokens': None,
            'router_elapsed_ms': None,
            'orchestrator_calls': 0, 'compute_tier': None, 'compute_decision': None,
            'orchestrator_input_tokens': None, 'orchestrator_output_tokens': None,
            'orchestrator_elapsed_ms': None, 'escalated': False,
            'cache': 'disabled'
        }
        route = None
        router_result = None
        compute_result = None
        if self.router:
            try:
                router_result = self.router.run(
                    question, paths, operation=operation,
                    search_results=arguments.get('search_results'),
                    known_symbols=arguments.get('known_symbols'))
                metrics.update(router_calls=1, router_route=router_result['model_route'],
                               router_confidence=router_result['confidence'],
                               router_input_tokens=router_result['usage']['input_tokens'],
                               router_output_tokens=router_result['usage']['output_tokens'],
                               router_elapsed_ms=router_result['elapsed_ms'])
                route = router_result['route']
            except (OSError, ValueError, jev.RouterError):
                metrics['router_calls'] = 1
                metrics['router_route'] = 'error'

        orchestrated_worker = bool(self.semantic and self.orchestrator)
        if route in (None, 'current_rules'):
            route = _heuristic(
                operation, paths, source_bytes,
                self.worker_auto_dispatch or orchestrated_worker)
        metrics['route'] = route

        if route == 'bulk_read' and orchestrated_worker:
            try:
                compute_result = self.orchestrator.run(
                    question, paths, operation=operation,
                    search_results=arguments.get('search_results'),
                    known_symbols=arguments.get('known_symbols'))
                metrics.update(
                    orchestrator_calls=1,
                    compute_tier=compute_result['tier'],
                    compute_decision=compute_result['decision'],
                    orchestrator_input_tokens=compute_result['usage']['input_tokens'],
                    orchestrator_output_tokens=compute_result['usage']['output_tokens'],
                    orchestrator_elapsed_ms=compute_result['elapsed_ms'])
            except (OSError, ValueError, jev.RouterError, compute.OrchestratorError):
                metrics.update(orchestrator_calls=1, compute_tier='T2',
                               compute_decision='error')

            if compute_result and compute_result['decision'] == 'cheap_worker':
                execution = {
                    'tier': 'T1',
                    'max_output_tokens': self.orchestrator.cfg['compute_policy']['max_cheap_output_tokens'],
                }
                worker_result, semantic_metrics = self.semantic.run(
                    _semantic_args(arguments, selections), execution=execution)
                metrics.update({k: v for k, v in semantic_metrics.items()
                                if k not in {'route', 'source_bytes', 'compute_tier'}})
                metrics['model_calls'] = semantic_metrics.get('model_calls', 0)
                metrics['compute_tier'] = 'T1'
                if compute.accepted_worker_result(worker_result):
                    result = worker_result
                    result['route'] = 'bulk_read'
                    result['recommended_route'] = 'bulk_read'
                else:
                    bundle, result = self._principal_bundle(
                        sources, selections, direct_limit,
                        'cheap_worker_escalation', recommended='principal')
                    metrics.update(route='principal', selected_bytes=bundle['selected_bytes'],
                                   compute_tier='T2', compute_decision='escalated',
                                   escalated=True)
                    result['model_calls'] = semantic_metrics.get('model_calls', 0)
                    result['worker_attempt'] = {
                        'status': worker_result.get('status', 'error'),
                        'accepted': False,
                    }
            else:
                reason = ('orchestrator_error'
                          if metrics['compute_decision'] == 'error'
                          else 'compute_gate_rejected')
                bundle, result = self._principal_bundle(
                    sources, selections, direct_limit, reason, recommended='principal')
                metrics.update(route='principal', selected_bytes=bundle['selected_bytes'],
                               compute_tier='T2')

        elif route == 'bulk_read' and self.semantic and self.worker_auto_dispatch:
            result, semantic_metrics = self.semantic.run(_semantic_args(arguments, selections))
            metrics.update({k: v for k, v in semantic_metrics.items()
                            if k not in {'route', 'source_bytes'}})
            metrics['model_calls'] = semantic_metrics.get('model_calls', 0)
            result['route'] = 'bulk_read'
            result['recommended_route'] = 'bulk_read'

        elif route == 'deterministic':
            metrics['compute_tier'] = 'T0'
            result = {
                'status': 'ok', 'route': 'deterministic',
                'recommended_route': 'deterministic',
                'action': 'Use a deterministic tool; no semantic worker was called.',
                'sources': source_table(sources),
                'coverage': {'scope': 'route-only'},
                'selected_bytes': 0, 'model_calls': 0,
            }

        else:
            bundle = select(sources, selections, max_bytes=direct_limit)
            metrics['selected_bytes'] = bundle['selected_bytes']
            if route == 'bulk_read':
                result = _evidence(bundle, 'targeted_read', recommended='bulk_read',
                                   reason=('semantic_worker_opt_in_required'
                                           if self.semantic else 'semantic_worker_unavailable'))
                metrics['route'] = 'targeted_read'
            else:
                result = _evidence(bundle, route)
                metrics['compute_tier'] = 'T2' if route == 'principal' else 'T0'

        self.scope.unchanged(sources)

        if router_result:
            result['router'] = {
                'model': router_result['model'],
                'route': router_result['model_route'],
                'confidence': router_result['confidence'],
                'delegation_useful': router_result['delegation_useful'],
                'reasoning_required': router_result['reasoning_required'],
                'usage': router_result['usage'],
            }
        elif self.router and metrics['router_route'] == 'error':
            result['router'] = {'status': 'error', 'fallback': 'local_rules'}

        if compute_result:
            result['orchestration'] = {
                'tier': metrics['compute_tier'],
                'initial_tier': compute_result['tier'],
                'decision': metrics['compute_decision'],
                'reason': compute_result['reason'],
                'scores': compute_result['scores'],
                'model': compute_result['model'],
                'usage': compute_result['usage'],
                'escalated': metrics['escalated'],
            }
            if metrics.get('worker_profile'):
                result['orchestration']['worker_profile'] = metrics['worker_profile']
                result['orchestration']['worker_model'] = metrics.get('worker_model')
        elif self.orchestrator and metrics['compute_decision'] == 'error':
            result['orchestration'] = {
                'tier': 'T2', 'decision': 'principal',
                'reason': 'orchestrator_error', 'escalated': False,
                'fallback': 'principal',
            }
        return result, metrics
