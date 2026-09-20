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
import model_policy
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
                 orchestrator_config=None, cache=True, host=None, model_preset=None,
                 model_mode=None):
        self.scope = scope
        self.host = host
        self.model_preset = model_preset
        self.model_mode = model_mode
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

    @staticmethod
    def _retryable_worker_failure(result, semantic_metrics):
        if result.get('status') == 'ok':
            # Evidence exists but uncertainty remained; a stronger configured profile may
            # be worth one bounded retry.
            return bool(result.get('findings')) and bool(result.get('unknowns'))
        if result.get('status') != 'error' or semantic_metrics.get('model_calls', 0) != 1:
            return False
        reason = str(result.get('reason', ''))
        # Transport/config/budget failures should not turn into an expensive ladder.
        blocked = (
            'TransportError', 'BudgetError', 'timeout', 'executable', 'config',
            'credential', 'api key', 'worker_output_limit', 'turn_failed'
        )
        return not any(x.lower() in reason.lower() for x in blocked)

    @staticmethod
    def _merge_semantic_metrics(metrics, semantic_metrics):
        metrics['model_calls'] += semantic_metrics.get('model_calls', 0)
        for key, value in semantic_metrics.items():
            if key not in {'route', 'source_bytes', 'compute_tier', 'model_calls'}:
                metrics[key] = value

    def _run_model_policy(self, arguments, selections, sources, direct_limit,
                          question, paths, operation, compute_result, metrics):
        try:
            plan = model_policy.choose(
                self.semantic.cfg, self.host, compute_result['scores'], operation,
                preset_override=self.model_preset, mode_override=self.model_mode)
        except model_policy.ModelPolicyError:
            bundle, result = self._principal_bundle(
                sources, selections, direct_limit, 'model_policy_error')
            metrics.update(route='principal', selected_bytes=bundle['selected_bytes'],
                           compute_tier='T2', compute_decision='policy_error')
            return result, None, []

        metrics.update(
            model_policy_preset=plan.get('preset'),
            model_demand=round(float(plan.get('demand', 0)), 4),
            model_tier=plan.get('model_tier'),
            compute_decision=plan.get('decision'),
        )
        if plan['decision'] != 'worker':
            bundle, result = self._principal_bundle(
                sources, selections, direct_limit, plan['reason'])
            metrics.update(route='principal', selected_bytes=bundle['selected_bytes'],
                           compute_tier='T2')
            return result, plan, []

        current = dict(plan['profile'])
        initial_profile = current['id']
        attempts = []
        escalations = 0
        max_escalations = int(plan.get('max_escalations', 0))
        max_output = self.orchestrator.cfg['compute_policy']['max_cheap_output_tokens']

        while True:
            execution = {
                'tier': 'T1', 'profile': current, 'host': self.host,
                'max_output_tokens': max_output,
            }
            worker_result, semantic_metrics = self.semantic.run(
                _semantic_args(arguments, selections), execution=execution)
            self._merge_semantic_metrics(metrics, semantic_metrics)
            accepted = compute.accepted_worker_result(worker_result)
            attempts.append({
                'profile': current['id'],
                'model': semantic_metrics.get('worker_model', current.get('model')),
                'effort': semantic_metrics.get('worker_reasoning_effort', current.get('effort')),
                'adapter': semantic_metrics.get('worker_adapter'),
                'status': worker_result.get('status', 'error'),
                'accepted': bool(accepted),
            })
            metrics.update(
                compute_tier='T1', model_tier=plan.get('model_tier'),
                final_model_profile=current['id'],
                initial_model_profile=initial_profile,
                model_attempts=len(attempts),
                model_escalations=escalations,
            )
            if accepted:
                worker_result['route'] = 'bulk_read'
                worker_result['recommended_route'] = 'bulk_read'
                worker_result['model_calls'] = metrics['model_calls']
                return worker_result, plan, attempts

            if (escalations >= max_escalations
                    or not self._retryable_worker_failure(worker_result, semantic_metrics)):
                break
            next_row = model_policy.next_profile(
                self.semantic.cfg, self.host, current['id'],
                preset_override=self.model_preset, mode_override=self.model_mode,
                scores=compute_result['scores'])
            if not next_row:
                break
            current = dict(next_row['profile'])
            plan['model_tier'] = next_row['model_tier']
            escalations += 1
            metrics.update(escalated=True, model_escalations=escalations,
                           model_tier=next_row['model_tier'])

        bundle, result = self._principal_bundle(
            sources, selections, direct_limit, 'model_worker_escalation_to_principal')
        metrics.update(route='principal', selected_bytes=bundle['selected_bytes'],
                       compute_tier='T2', compute_decision='principal_after_worker',
                       escalated=bool(attempts), model_attempts=len(attempts),
                       model_escalations=escalations,
                       initial_model_profile=initial_profile,
                       final_model_profile=current['id'])
        result['model_calls'] = metrics['model_calls']
        result['worker_attempts'] = attempts
        return result, plan, attempts

    def _run_fixed_worker(self, arguments, selections, sources, direct_limit, metrics):
        """Run a user-selected static worker model; never switch model identity."""
        execution = {
            'tier': 'T1',
            'max_output_tokens': self.orchestrator.cfg['compute_policy']['max_cheap_output_tokens'],
        }
        worker_result, semantic_metrics = self.semantic.run(
            _semantic_args(arguments, selections), execution=execution)
        self._merge_semantic_metrics(metrics, semantic_metrics)
        metrics['compute_tier'] = 'T1'
        if compute.accepted_worker_result(worker_result):
            worker_result['route'] = 'bulk_read'
            worker_result['recommended_route'] = 'bulk_read'
            worker_result['model_calls'] = metrics['model_calls']
            return worker_result
        bundle, result = self._principal_bundle(
            sources, selections, direct_limit,
            'fixed_worker_escalation', recommended='principal')
        metrics.update(route='principal', selected_bytes=bundle['selected_bytes'],
                       compute_tier='T2', compute_decision='principal_after_fixed_worker',
                       escalated=True)
        result['model_calls'] = metrics['model_calls']
        result['worker_attempt'] = {
            'status': worker_result.get('status', 'error'),
            'accepted': False,
        }
        return result

    @staticmethod
    def _suggestion(plan):
        if not plan:
            return None
        profile = plan.get('profile') or {}
        return {
            'decision': plan.get('decision'),
            'reason': plan.get('reason'),
            'preset': plan.get('preset'),
            'demand': plan.get('demand'),
            'profile': profile.get('id'),
            'model': profile.get('model'),
            'effort': profile.get('effort'),
            'max_profile': plan.get('max_profile'),
        }

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
            'model_tier': None, 'model_policy_preset': self.model_preset,
            'model_policy_mode': self.model_mode,
            'model_demand': None, 'model_attempts': 0, 'model_escalations': 0,
            'initial_model_profile': None, 'final_model_profile': None,
            'host': self.host, 'cache': 'disabled'
        }
        route = None
        router_result = None
        compute_result = None
        plan = None
        attempts = []

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
                    orchestrator_input_tokens=compute_result['usage']['input_tokens'],
                    orchestrator_output_tokens=compute_result['usage']['output_tokens'],
                    orchestrator_elapsed_ms=compute_result['elapsed_ms'])
            except (OSError, ValueError, jev.RouterError, compute.OrchestratorError):
                metrics.update(orchestrator_calls=1, compute_tier='T2',
                               compute_decision='error')

            adapter = self.semantic.cfg.get('adapter') if self.semantic else None
            use_model_policy = bool(
                compute_result and (
                    self.semantic.cfg.get('model_policy') is not None
                    or adapter == 'host-cli'
                )
            )
            policy_mode = None
            if use_model_policy:
                try:
                    resolved_policy = model_policy.resolve(
                        self.semantic.cfg, self.host,
                        preset_override=self.model_preset,
                        mode_override=self.model_mode)
                    policy_mode = resolved_policy['mode']
                    metrics['model_policy_mode'] = policy_mode
                except model_policy.ModelPolicyError:
                    bundle, result = self._principal_bundle(
                        sources, selections, direct_limit, 'model_policy_error')
                    metrics.update(route='principal', selected_bytes=bundle['selected_bytes'],
                                   compute_tier='T2', compute_decision='policy_error')
                    use_model_policy = False
                    policy_mode = 'error'

            if use_model_policy and policy_mode == 'auto':
                result, plan, attempts = self._run_model_policy(
                    arguments, selections, sources, direct_limit,
                    question, paths, operation, compute_result, metrics)

            elif use_model_policy and policy_mode == 'suggest':
                try:
                    plan = model_policy.choose(
                        self.semantic.cfg, self.host, compute_result['scores'], operation,
                        preset_override=self.model_preset, mode_override=self.model_mode)
                    metrics.update(
                        model_policy_preset=plan.get('preset'),
                        model_policy_mode='suggest',
                        model_demand=round(float(plan.get('demand', 0)), 4),
                        model_tier=plan.get('model_tier'),
                        compute_decision='suggestion',
                    )
                except model_policy.ModelPolicyError:
                    plan = None
                suggestion = self._suggestion(plan)
                if adapter == 'host-cli':
                    bundle, result = self._principal_bundle(
                        sources, selections, direct_limit,
                        'model_suggestion_only', recommended='principal')
                    metrics.update(route='principal', selected_bytes=bundle['selected_bytes'],
                                   compute_tier='T2')
                elif compute_result.get('decision') == 'cheap_worker':
                    result = self._run_fixed_worker(
                        arguments, selections, sources, direct_limit, metrics)
                else:
                    bundle, result = self._principal_bundle(
                        sources, selections, direct_limit,
                        compute_result.get('reason', 'compute_gate_rejected'),
                        recommended='principal')
                    metrics.update(route='principal', selected_bytes=bundle['selected_bytes'],
                                   compute_tier='T2')
                if suggestion:
                    result['model_suggestion'] = suggestion

            elif use_model_policy and policy_mode == 'manual':
                metrics['model_policy_mode'] = 'manual'
                if adapter == 'host-cli':
                    bundle, result = self._principal_bundle(
                        sources, selections, direct_limit,
                        'manual_mode_requires_explicit_model', recommended='principal')
                    metrics.update(route='principal', selected_bytes=bundle['selected_bytes'],
                                   compute_tier='T2', compute_decision='manual')
                elif compute_result.get('decision') == 'cheap_worker':
                    metrics['compute_decision'] = 'manual_fixed_model'
                    result = self._run_fixed_worker(
                        arguments, selections, sources, direct_limit, metrics)
                else:
                    bundle, result = self._principal_bundle(
                        sources, selections, direct_limit,
                        compute_result.get('reason', 'compute_gate_rejected'),
                        recommended='principal')
                    metrics.update(route='principal', selected_bytes=bundle['selected_bytes'],
                                   compute_tier='T2', compute_decision='manual')

            elif policy_mode != 'error' and compute_result and compute_result.get('decision') == 'cheap_worker':
                # Compatibility path for command/chat configs created before model_policy.
                result = self._run_fixed_worker(
                    arguments, selections, sources, direct_limit, metrics)

            elif policy_mode != 'error':
                reason = ('orchestrator_error'
                          if metrics['compute_decision'] == 'error'
                          else 'compute_gate_rejected')
                bundle, result = self._principal_bundle(
                    sources, selections, direct_limit, reason, recommended='principal')
                metrics.update(route='principal', selected_bytes=bundle['selected_bytes'],
                               compute_tier='T2')

        elif route == 'bulk_read' and self.semantic and self.worker_auto_dispatch:
            result, semantic_metrics = self.semantic.run(_semantic_args(arguments, selections))
            self._merge_semantic_metrics(metrics, semantic_metrics)
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
            orchestration = {
                'tier': metrics['compute_tier'],
                'initial_tier': compute_result.get('tier'),
                'decision': metrics['compute_decision'],
                'scores': compute_result['scores'],
                'model': compute_result['model'],
                'usage': compute_result['usage'],
                'escalated': metrics['escalated'],
                'host': self.host,
            }
            if plan:
                orchestration.update({
                    'reason': plan.get('reason'),
                    'mode': plan.get('mode'),
                    'preset': plan.get('preset'),
                    'demand': plan.get('demand'),
                    'model_tier': metrics.get('model_tier'),
                    'initial_profile': metrics.get('initial_model_profile'),
                    'final_profile': metrics.get('final_model_profile'),
                    'attempts': attempts,
                    'max_profile': plan.get('max_profile'),
                })
            else:
                orchestration['reason'] = compute_result.get('reason')
            if metrics.get('worker_profile'):
                orchestration['worker_profile'] = metrics['worker_profile']
                orchestration['worker_model'] = metrics.get('worker_model')
                orchestration['worker_reasoning_effort'] = metrics.get('worker_reasoning_effort')
            result['orchestration'] = orchestration
        elif self.orchestrator and metrics['compute_decision'] == 'error':
            result['orchestration'] = {
                'tier': 'T2', 'decision': 'principal',
                'reason': 'orchestrator_error', 'escalated': False,
                'fallback': 'principal', 'host': self.host,
            }
        return result, metrics

