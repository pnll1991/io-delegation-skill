"""Smart context routing for the public `query` MCP tool.

The router sees task text plus aggregate metadata, never source contents or names.
Selected source fragments stay local unless an approved semantic worker is used.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import time

import decision_router as jev
from context_selection import MAX_SELECTED_BYTES, grouped_question, select
from context_sources import exact_keys, source_table
from context_engine import SemanticEngine

ROUTER_OPERATIONS = {
    'unknown', 'exploration', 'factual', 'generation',
    'debugging', 'architecture', 'security', 'editing'
}


def _external_config(path, root):
    path = Path(path).absolute()
    for component in (path, *path.parents):
        info = component.lstat()
        if component.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Router configuration cannot contain symlinks or reparse points')
    resolved = path.resolve(strict=True)
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
    if operation in {'debugging', 'architecture', 'security', 'editing'}:
        return 'principal'
    if operation == 'generation':
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


class QueryEngine:
    def __init__(self, scope, audit, worker_config=None, router_config=None, cache=True):
        self.scope = scope
        self.semantic = (SemanticEngine(scope, audit, worker_config, cache=cache)
                         if worker_config is not None else None)
        self.router = JevRouter(scope.root, router_config) if router_config is not None else None

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
            'router_elapsed_ms': None, 'cache': 'disabled'
        }
        route = None
        router_result = None
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

        if route in (None, 'current_rules'):
            route = _heuristic(operation, paths, source_bytes, self.semantic is not None)
        metrics['route'] = route

        if route == 'bulk_read' and self.semantic:
            semantic_args = {'selections': selections}
            if 'question' in arguments:
                semantic_args['question'] = arguments['question']
            else:
                semantic_args['questions'] = arguments['questions']
            result, semantic_metrics = self.semantic.run(semantic_args)
            metrics.update({k: v for k, v in semantic_metrics.items()
                            if k not in {'route', 'source_bytes'}})
            metrics['model_calls'] = semantic_metrics.get('model_calls', 0)
            result['route'] = 'bulk_read'
            result['recommended_route'] = 'bulk_read'
        elif route == 'deterministic':
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
                                   reason='semantic_worker_unavailable')
                metrics['route'] = 'targeted_read'
            else:
                result = _evidence(bundle, route)
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
        return result, metrics
