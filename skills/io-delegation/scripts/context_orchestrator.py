"""Jev-guided compute orchestration for cheap-first model delegation.

Jev scores task suitability only. Deterministic local policy chooses T1 cheap worker
or T2 strong principal. T0 deterministic/local routing remains in context_query.
No source bodies or file names are sent to Jev.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import time

import decision_router as jev
import model_policy

TIERS = ('T0', 'T1', 'T2')
SENSITIVE_OPERATIONS = {'debugging', 'architecture', 'security', 'editing', 'generation'}
DEFAULT_POLICY = {
    'cheap_sufficient_min': 0.78,
    'risk_high_max': 0.30,
    'uncertainty_high_max': 0.35,
    'reasoning_required_max': 0.40,
    'max_cheap_output_tokens': 900,
}
RANGES = {
    'cheap_sufficient_min': (0.5, 1.0),
    'risk_high_max': (0.0, 0.5),
    'uncertainty_high_max': (0.0, 0.5),
    'reasoning_required_max': (0.0, 0.5),
    'max_cheap_output_tokens': (64, 4096),
}
QUESTIONS = {
    'cheap_model_sufficient': {
        'type': 'noul',
        'instructions': (
            'Could a cheaper read-only worker answer this bounded factual task accurately '
            'from the selected evidence, without architecture, debugging, security judgment '
            'or exact source editing?'
        ),
        'criteria': {
            'true': 'The task is bounded factual extraction/comparison and a compact evidence-backed answer is sufficient.',
            'false': 'The task requires deep reasoning, design/security judgment, causal debugging, editing, or broad unresolved context.'
        }
    },
    'risk_high': {
        'type': 'noul',
        'instructions': 'Would a wrong or incomplete cheap-worker answer carry material correctness, security, data, or implementation risk?',
        'criteria': {
            'true': 'A mistaken answer could cause unsafe, destructive, security-sensitive, architectural, or correctness-critical action.',
            'false': 'The output is factual/read-only evidence that the principal can independently verify.'
        }
    },
    'uncertainty_high': {
        'type': 'noul',
        'instructions': 'Is the task materially underspecified or likely to need context beyond the explicit selected corpus?',
        'criteria': {
            'true': 'Important facts, scope, or dependencies are likely missing.',
            'false': 'The selected corpus and task are sufficiently bounded.'
        }
    },
    'reasoning_required': {
        'type': 'noul',
        'instructions': 'Does correctness primarily require causal reasoning, debugging, architecture, security judgment, or exact editing?',
        'criteria': {
            'true': 'The final answer needs non-trivial judgment rather than bounded factual extraction.',
            'false': 'Evidence-backed extraction/comparison is enough.'
        }
    },
    'parallelism_useful': {
        'type': 'noul',
        'instructions': 'Would independent parallel workers likely help because the selected corpus contains separable factual subproblems?',
        'criteria': {
            'true': 'The work naturally splits into independent factual shards.',
            'false': 'The task is small, sequential, or requires one coherent reasoning chain.'
        }
    },
}


class OrchestratorError(jev.RouterError):
    pass


def _probability(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise OrchestratorError(f'Invalid Jev compute score: {name}')
    return float(value)


def load_config(path):
    cfg = jev.load_config(str(path))
    policy = cfg.get('compute_policy', {})
    if not isinstance(policy, dict) or set(policy) - set(DEFAULT_POLICY):
        raise OrchestratorError('Unknown compute policy option.')
    merged = dict(DEFAULT_POLICY, **policy)
    for key, value in merged.items():
        lo, hi = RANGES[key]
        if key == 'max_cheap_output_tokens':
            if type(value) is not int or not lo <= value <= hi:
                raise OrchestratorError(f'{key} must be an integer between {lo} and {hi}.')
        elif type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
            raise OrchestratorError(f'{key} must be between {lo} and {hi}.')
    return {**cfg, 'compute_policy': merged}


def build_payload(state, cfg):
    return {'state': state, 'model': cfg['model'], 'questions': QUESTIONS}


def parse_response(value, cfg):
    if not isinstance(value, dict) or not isinstance(value.get('answers'), dict):
        raise OrchestratorError('TypeSafe compute response is missing answers.')
    answers = value['answers']
    scores = {}
    for name in QUESTIONS:
        row = answers.get(name)
        if not isinstance(row, dict) or row.get('type') != 'noul':
            raise OrchestratorError(f'TypeSafe compute answer has invalid shape: {name}')
        scores[name] = _probability(row.get('noul'), name)
    usage = value.get('usage') if isinstance(value.get('usage'), dict) else {}
    def usage_int(name):
        item = usage.get(name)
        return item if type(item) is int and item >= 0 else None
    return {
        'status': 'ok',
        'model': value.get('model') if isinstance(value.get('model'), str) else cfg['model'],
        'scores': scores,
        'usage': {'input_tokens': usage_int('input_tokens'), 'output_tokens': usage_int('output_tokens')},
    }


def decide(scores, operation, worker_available, policy=None):
    policy = dict(DEFAULT_POLICY, **(policy or {}))
    if not worker_available:
        return {'tier': 'T2', 'decision': 'principal', 'reason': 'worker_unavailable'}
    if operation in SENSITIVE_OPERATIONS:
        return {'tier': 'T2', 'decision': 'principal', 'reason': 'sensitive_operation'}
    gates = (
        scores['cheap_model_sufficient'] >= policy['cheap_sufficient_min'],
        scores['risk_high'] <= policy['risk_high_max'],
        scores['uncertainty_high'] <= policy['uncertainty_high_max'],
        scores['reasoning_required'] <= policy['reasoning_required_max'],
    )
    if all(gates):
        return {'tier': 'T1', 'decision': 'cheap_worker', 'reason': 'cheap_first_policy'}
    return {'tier': 'T2', 'decision': 'principal', 'reason': 'compute_gate_rejected'}


def accepted_worker_result(result):
    return (
        isinstance(result, dict)
        and result.get('status') == 'ok'
        and isinstance(result.get('findings'), list)
        and bool(result['findings'])
        and result.get('unknowns') == []
    )


def cheap_worker_config(cfg, max_output_tokens=None):
    """Backward-compatible adapter for the pre-model_policy single cheap profile."""
    profiles = cfg.get('compute_profiles', {}) or {}
    profile = profiles.get('cheap') if isinstance(profiles, dict) else None
    profile = profile if isinstance(profile, dict) else {}
    if cfg.get('adapter') == 'command' and not profile:
        derived = dict(cfg)
        return derived, {
            'profile': 'base-worker',
            'model': derived.get('model'),
            'reasoning_effort': derived.get('reasoning_effort'),
            'adapter': 'command',
            'cursor_model': None,
            'output_token_cap_supported': False,
            'capability': None,
            'cost_index': None,
            'source': 'legacy-base-worker',
        }
    model = profile.get('model', cfg.get('model'))
    if not isinstance(model, str) or not model.strip():
        raise ValueError('Cheap worker profile requires a valid model')
    effort = profile.get('reasoning_effort', cfg.get('reasoning_effort', 'medium'))
    legacy = {
        'id': 'cheap' if profile else 'base-worker',
        'model': model,
        'effort': effort,
        'capability': .5,
        'cost_index': .5,
        'source': 'legacy-compute-profile',
    }
    if 'max_output_tokens' in profile:
        legacy['max_output_tokens'] = profile['max_output_tokens']
    host = 'codex' if cfg.get('adapter') == 'codex-cli' else (
        'cursor' if cfg.get('adapter') == 'cursor-cli' else None)
    return model_policy.apply_profile(cfg, host, legacy, max_output_tokens)


class JevComputeOrchestrator:
    def __init__(self, root, config):
        self.root = Path(root).resolve(strict=True)
        self.path = Path(config).absolute()
        info = self.path.lstat()
        if self.path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Orchestrator configuration itself cannot be a symlink or reparse point')
        self.path = self.path.resolve(strict=True)
        if self.path == self.root or self.root in self.path.parents:
            raise ValueError('Orchestrator configuration must be outside project')
        self.cfg = load_config(self.path)
        import hashlib
        self.digest = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def _config(self):
        import hashlib
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != self.digest:
            raise ValueError('Orchestrator configuration changed; restart after review')
        return self.cfg

    def run(self, task, paths, operation='unknown', search_results=None, known_symbols=None):
        cfg = self._config()
        state = jev.build_state(self.root, task, paths, operation=operation,
                                search_results=search_results, known_symbols=known_symbols)
        started = time.monotonic()
        raw = jev.request_typesafe(build_payload(state, cfg), cfg, user_agent='io-delegation/compute-orchestrator')
        result = parse_response(raw, cfg)
        result['elapsed_ms'] = round((time.monotonic() - started) * 1000)
        result.update(decide(result['scores'], operation, True, cfg['compute_policy']))
        return result
