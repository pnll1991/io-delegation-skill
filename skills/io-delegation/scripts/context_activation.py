#!/usr/bin/env python3
"""Cheap deterministic gate for deciding whether Context Gateway should load."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

SAFE_EXTS = {
    '.py', '.js', '.jsx', '.ts', '.tsx', '.json', '.toml', '.yaml', '.yml',
    '.md', '.html', '.css', '.scss', '.go', '.rs', '.java', '.kt', '.rb',
    '.php', '.cs', '.cpp', '.c', '.h', '.hpp', '.sh', '.ps1', '.sql', '.vue', '.svelte',
}
EXCLUDED = {
    '.git', '.agents', '.claude', '.cursor', '.io-delegation', '.io-delegation-hooks',
    'node_modules', '.venv', 'venv', 'dist', 'build', 'coverage', 'benchmark-output',
    '__pycache__', '.next', '.nuxt', '.cache',
}
DEFAULTS = {
    'small_files': 4,
    'small_bytes': 64_000,
    'enable_files': 12,
    'enable_bytes': 512_000,
    'large_file_bytes': 128_000,
    'scan_files': 4_000,
    'sample_bytes': 8_192,
}
TRIVIAL_RE = re.compile(
    r'\b(git\s+status|run\s+(the\s+)?tests?|pytest\b|npm\s+test\b|pnpm\s+test\b|'
    r'yarn\s+test\b|count\s+files?)\b', re.I,
)
MULTI_RE = re.compile(
    r'\b(compare|across|multi[- ]?file|repository|repo\b|find\s+all|audit|inventory|'
    r'cross[- ]?file|all\s+modules?)\b', re.I,
)
PRINCIPAL_RE = re.compile(
    r'\b(security|vulnerab|credential|secret|architecture|debug|root\s+cause|'
    r'race\s+condition|threat)\b', re.I,
)


def _limits(state):
    row = dict(DEFAULTS)
    custom = state.get('activation_limits') if isinstance(state, dict) else None
    if isinstance(custom, dict):
        for key in row:
            value = custom.get(key)
            if type(value) is int and value > 0:
                row[key] = value
    return row


def _candidate(path):
    return (
        path.is_file()
        and not path.is_symlink()
        and path.suffix.lower() in SAFE_EXTS
        and not path.name.lower().startswith('.env')
    )


def _iter_scope(root, state, max_files):
    count = 0
    for rel in state.get('allow_files', ()):
        path = root / rel
        if _candidate(path):
            yield path
            count += 1
            if count >= max_files:
                return
    for rel in state.get('allow_prefixes', ()):
        base = root / rel
        if not base.is_dir() or base.is_symlink():
            continue
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [
                d for d in dirs
                if d not in EXCLUDED and not (Path(dirpath) / d).is_symlink()
            ]
            for name in files:
                path = Path(dirpath) / name
                if _candidate(path):
                    yield path
                    count += 1
                    if count >= max_files:
                        return


def collect_signals(root, state):
    root = Path(root).resolve(strict=True)
    limits = _limits(state)
    files = total = largest = minified = 0
    truncated = False
    for path in _iter_scope(root, state, limits['scan_files'] + 1):
        files += 1
        if files > limits['scan_files']:
            truncated = True
            break
        try:
            size = path.stat().st_size
        except OSError:
            continue
        total += size
        largest = max(largest, size)
        if size >= 32_000:
            try:
                with path.open('rb') as handle:
                    sample = handle.read(limits['sample_bytes'])
                if sample and sample.count(b'\n') <= 2:
                    minified += 1
            except OSError:
                pass
    return {
        'file_count': min(files, limits['scan_files']),
        'total_bytes': total,
        'largest_file_bytes': largest,
        'minified_files': minified,
        'scan_truncated': truncated,
    }


def decide(root, state, task_hint=None, force=None):
    mode = str(state.get('activation_mode', 'auto')) if isinstance(state, dict) else 'auto'
    env_force = force or os.environ.get('IO_DELEGATION_FORCE')
    if env_force in ('on', 'enable', '1', 'true'):
        return {
            'decision': 'enable', 'reason': 'forced_on',
            'signals': collect_signals(root, state), 'principal_intent': False,
        }
    if env_force in ('off', 'bypass', '0', 'false'):
        return {'decision': 'bypass', 'reason': 'forced_off', 'signals': {}, 'principal_intent': False}
    if mode == 'off':
        return {'decision': 'bypass', 'reason': 'mode_off', 'signals': {}, 'principal_intent': False}

    signals = collect_signals(root, state)
    hint = (task_hint or os.environ.get('IO_DELEGATION_TASK_HINT') or '').strip()
    principal = bool(PRINCIPAL_RE.search(hint))
    if mode == 'always':
        return {'decision': 'enable', 'reason': 'mode_always', 'signals': signals, 'principal_intent': principal}
    if hint and TRIVIAL_RE.search(hint) and not MULTI_RE.search(hint):
        return {'decision': 'bypass', 'reason': 'trivial_task_hint', 'signals': signals, 'principal_intent': principal}
    if hint and MULTI_RE.search(hint):
        return {'decision': 'enable', 'reason': 'multi_file_task_hint', 'signals': signals, 'principal_intent': principal}

    limits = _limits(state)
    if signals['file_count'] <= limits['small_files'] and signals['total_bytes'] <= limits['small_bytes']:
        return {'decision': 'bypass', 'reason': 'small_scope', 'signals': signals, 'principal_intent': principal}
    if signals['minified_files']:
        return {'decision': 'enable', 'reason': 'minified_source', 'signals': signals, 'principal_intent': principal}
    if signals['largest_file_bytes'] >= limits['large_file_bytes']:
        return {'decision': 'enable', 'reason': 'large_file', 'signals': signals, 'principal_intent': principal}
    if (
        signals['file_count'] >= limits['enable_files']
        or signals['total_bytes'] >= limits['enable_bytes']
        or signals['scan_truncated']
    ):
        return {'decision': 'enable', 'reason': 'broad_scope', 'signals': signals, 'principal_intent': principal}
    return {'decision': 'enable', 'reason': 'conservative_default', 'signals': signals, 'principal_intent': principal}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--state', required=True)
    parser.add_argument('--task')
    parser.add_argument('--force', choices=['on', 'off'])
    args = parser.parse_args(argv)
    state = json.loads(Path(args.state).read_text(encoding='utf-8-sig'))
    print(json.dumps(decide(args.root, state, args.task, args.force), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
