"""Selected-evidence inference using an already approved transport; no auto-provider."""
from __future__ import annotations
import hashlib
from pathlib import Path
import subprocess

import io_delegate as delegate
import context_backend as backend
import context_orchestrator as compute
import model_policy
from context_cache import ResultCache
from context_budget import BudgetError, limits, before_dispatch
from context_selection import select, selected_job, validate_answer, grouped_question
from context_sources import exact_keys, encoded
from worker_runtime import Journal, TransportError, normalize_usage, usage_complete


def safe_config(path, root):
    raw_path = Path(path).absolute()
    info = raw_path.lstat()
    if raw_path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Worker configuration itself cannot be a symlink or reparse point')
    path = raw_path.resolve(strict=True)
    root = Path(root).resolve(strict=True)
    if root == path or root in path.parents:
        raise ValueError('Approved configuration must be outside project')
    cfg = delegate.load_config(str(path))
    backend.check_options(cfg)
    return path, cfg, hashlib.sha256(path.read_bytes()).hexdigest()


class SemanticEngine:
    def __init__(self, scope, audit, config, cache=True):
        self.scope, self.audit = scope, audit
        self.path, self.cfg, self.config_hash = safe_config(config, scope.root)
        self.cache = ResultCache(audit, enabled=cache)
        self.limits = limits(self.cfg)

    def check_config(self):
        path, cfg, digest = safe_config(self.path, self.scope.root)
        if digest != self.config_hash:
            raise ValueError('Approved configuration changed; restart after review')
        return cfg

    def prepare(self, arguments):
        exact_keys(arguments, ('selections',), ('question', 'questions'))
        if ('question' in arguments) == ('questions' in arguments):
            raise ValueError('Specify question OR related questions')
        if not isinstance(arguments['selections'], list) or not arguments['selections']:
            raise ValueError('Explicit selections required')
        names = []
        for selection in arguments['selections']:
            exact_keys(selection, ('path', 'select'))
            names.append(selection['path'])
        sources = self.scope.load(sorted(set(names)))
        bundle = select(sources, arguments['selections'], max_bytes=self.limits['max_selected_bytes'])
        question = arguments['question'] if 'question' in arguments else grouped_question(arguments['questions'])
        job = selected_job(bundle, question)
        return sources, bundle, job

    def run(self, arguments, execution=None):
        cfg = self.check_config()
        execution = execution if isinstance(execution, dict) else {}
        transport_cfg = dict(cfg)
        profile_meta = {
            'profile': 'base-worker',
            'model': cfg.get('model'),
            'reasoning_effort': cfg.get('reasoning_effort'),
            'adapter': cfg.get('adapter'),
            'output_token_cap_supported': cfg.get('adapter') == 'chat-completions',
        }
        if isinstance(execution.get('profile'), dict):
            transport_cfg, profile_meta = model_policy.apply_profile(
                cfg, execution.get('host'), execution['profile'],
                execution.get('max_output_tokens'))
        elif execution.get('tier') == 'T1':
            # Backward-compatible single cheap profile used by pre-v1.3 configs.
            transport_cfg, profile_meta = compute.cheap_worker_config(
                cfg, execution.get('max_output_tokens'))
        elif cfg.get('adapter') == 'host-cli':
            raise ValueError('host-cli requires an explicit model-policy profile')
        sources, bundle, job = self.prepare(arguments)
        metrics = dict(route='selected-semantic', source_bytes=bundle['source_bytes'],
                       selected_bytes=bundle['selected_bytes'], request_bytes=len(encoded(job)),
                       model_calls=0, cache='miss' if self.cache.enabled else 'disabled', usage=normalize_usage(None),
                       compute_tier=execution.get('tier'), worker_profile=profile_meta['profile'],
                       worker_model=profile_meta['model'],
                       worker_reasoning_effort=profile_meta['reasoning_effort'],
                       worker_adapter=profile_meta.get('adapter'),
                       worker_cost_index=profile_meta.get('cost_index'),
                       worker_profile_source=profile_meta.get('source'))
        if bundle['status'] != 'ok':
            return dict(status='insufficient_context', findings=[], sources=bundle['sources'],
                        coverage=bundle['coverage'], unknowns=['At least one selection found no usable region.'],
                        model_calls=0), metrics
        cache_operation = 'semantic_query:' + profile_meta['profile']
        key = self.cache.key(self.scope, sources, cache_operation, job, self.config_hash)
        cached = self.cache.get(key)
        if cached is not None:
            # Revalidate scope and current source hashes even on an exact hit.
            self.scope.unchanged(sources)
            cached['model_calls'] = 0
            cached['cache'] = 'hit'
            metrics.update(cache='hit', usage=dict(input_tokens=0,output_tokens=0,cached_input_tokens=0,
                                                  cache_write_input_tokens=0,reasoning_output_tokens=0))
            return cached, metrics
        journal = Journal(self.audit)
        response_seen = False
        usage = normalize_usage(None)
        dispatched = False
        def record(event, **values):
            nonlocal response_seen, usage, dispatched
            if event == 'worker_dispatched':
                dispatched = True; metrics['model_calls'] = 1
            if event == 'worker_response':
                response_seen = True; usage = normalize_usage(values.get('usage')); metrics['usage'] = usage
            return journal.emit(event, mode='bulk-read', transport='context-mcp', **values)
        try:
            record('worker_attempt')
            journal.acquire()
            cap, prior = before_dispatch(journal, cfg, bundle['selected_bytes'], len(encoded(job)))
            transport_cfg['reader_max_tokens'] = min(
                int(transport_cfg.get('reader_max_tokens', cfg.get('reader_max_tokens',1600))),
                cap['max_output_tokens'])
            metrics['output_token_cap_supported'] = profile_meta['output_token_cap_supported']
            metrics['task_token_cap_is_between_calls'] = True
            output, raw_metrics = backend.invoke(job, transport_cfg, self.scope.root, record)
            if not response_seen:
                record('worker_response', usage=normalize_usage(raw_metrics.get('usage')),
                       usage_complete=usage_complete(normalize_usage(raw_metrics.get('usage'))))
            result = validate_answer(output, bundle)
            self.scope.unchanged(sources)
            accepted = compute.accepted_worker_result(result)
            metrics['reported_task_tokens'] = prior['tokens']+usage['input_tokens']+usage['output_tokens'] if usage_complete(usage) else None
            metrics['token_budget_crossed'] = metrics['reported_task_tokens'] is not None and metrics['reported_task_tokens'] > cap['max_task_tokens']
            record('worker_completed', status=result['status'], accepted=accepted, usage=usage)
            result['model_calls'] = int(dispatched)
            if accepted:
                self.cache.put(key, result)
            result['cache'] = metrics['cache']
            return result, metrics
        except (OSError, ValueError, TypeError, KeyError, delegate.DelegateError, TransportError, subprocess.SubprocessError) as exc:
            record('worker_error', code=type(exc).__name__, dispatched=dispatched, usage=usage,
                   usage_complete=usage_complete(usage) if dispatched else True)
            # Never return a provider error body, source text or credential details.
            reason = str(exc) if type(exc) is ValueError or isinstance(exc, delegate.DelegateError) else type(exc).__name__
            return dict(status='budget_exceeded' if isinstance(exc,BudgetError) else 'error', reason=reason, model_calls=int(dispatched),
                        action='Stop on transport error. Do not retry or change provider/permissions automatically.'), metrics
        finally:
            journal.close()
