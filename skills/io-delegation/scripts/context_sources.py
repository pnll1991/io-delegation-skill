"""Authorized, bounded UTF-8 source snapshots. No model calls or code execution."""
from __future__ import annotations
from functools import lru_cache
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Iterable

import io_delegate as legacy
from worker_mcp import relative_name, reject_links

VERSION = 'context-sources/1'
MAX_FILES = 128
MAX_SCAN = 4096
MAX_BYTES = 8_000_000


def encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(',', ':')).encode('utf-8')


def fingerprint(value) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def plain_int(value, low: int, high: int, label: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} must be an integer in {low}..{high}')
    return value


def exact_keys(value, required: Iterable[str], optional: Iterable[str] = ()) -> None:
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise ValueError('Missing or unexpected fields')


def glob_matches(name: str, pattern: str) -> bool:
    """Path glob with ** matching zero or more segments; no shell or regex input."""
    parts, patterns = tuple(name.split('/')), tuple(pattern.split('/'))
    @lru_cache(None)
    def match(i, j):
        if j == len(patterns):
            return i == len(parts)
        if patterns[j] == '**':
            return match(i, j+1) or (i < len(parts) and match(i+1, j))
        return i < len(parts) and fnmatch.fnmatchcase(parts[i], patterns[j]) and match(i+1, j+1)
    return match(0, 0)


class SourceScope:
    def __init__(self, root, prefixes=(), files=()):
        supplied = Path(root).absolute()
        # Reject a root symlink/reparse point rather than silently broadening a scope.
        info = supplied.lstat()
        if supplied.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Root cannot be a symlink or reparse point')
        self.root = supplied.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('Root must be a directory')
        self.prefixes = tuple(sorted(set(relative_name(x) for x in prefixes)))
        self.files = frozenset(relative_name(x) for x in files)
        if not self.prefixes and not self.files:
            raise ValueError('Explicit source allowlist required')
        for name in (*self.prefixes, *self.files):
            if any(x in name for x in '*?[]'):
                raise ValueError('Allowlists contain literal paths, never globs')
            legacy.scoped_path(self.root, name)
        self.identity = fingerprint(dict(root=str(self.root), prefixes=self.prefixes,
                                         files=sorted(self.files), version=VERSION))

    def check(self, name: str) -> Path:
        name = relative_name(name)
        if name not in self.files and not any(name.startswith(p+'/') for p in self.prefixes):
            raise ValueError('Path outside approved scope')
        path = self.root/name
        legacy.scoped_path(self.root, name)
        reject_links(path, self.root)
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError('Source must be a regular file')
        return path

    def _candidates(self):
        candidates = set(self.files)
        scanned = 0
        for prefix in self.prefixes:
            top = self.root/prefix
            reject_links(top, self.root)
            if not top.is_dir():
                raise ValueError('Source prefix must name a directory')
            for folder, dirs, files in os.walk(top, followlinks=False):
                scanned += len(dirs)+len(files)
                if scanned > MAX_SCAN:
                    raise ValueError('Discovery budget exceeded; narrow the allowlist')
                keep = []
                for d in sorted(dirs):
                    p = Path(folder)/d
                    try:
                        legacy.scoped_path(self.root, p.relative_to(self.root).as_posix())
                        reject_links(p, self.root)
                    except (ValueError, OSError, legacy.DelegateError):
                        continue
                    keep.append(d)
                dirs[:] = keep
                for f in files:
                    p = Path(folder)/f
                    name = p.relative_to(self.root).as_posix()
                    try:
                        self.check(name)
                    except (ValueError, OSError, legacy.DelegateError):
                        continue
                    candidates.add(name)
        return sorted(candidates)

    def expand(self, paths):
        if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_FILES:
            raise ValueError('Provide 1..128 paths or scoped globs')
        selected, candidates = set(), None
        for raw in paths:
            pattern = relative_name(raw)
            if '[' in pattern or ']' in pattern:
                raise ValueError('Only *, ? and ** glob syntax is supported')
            if '*' in pattern or '?' in pattern:
                if candidates is None:
                    candidates = self._candidates()
                matches = [n for n in candidates if glob_matches(n, pattern)]
                if not matches:
                    raise ValueError('Pattern has no permitted matches')
                selected.update(matches)
            else:
                self.check(pattern)
                selected.add(pattern)
            if len(selected) > MAX_FILES:
                raise ValueError('Too many source files; narrow the request')
        return sorted(selected)

    def load(self, paths):
        result, total = [], 0
        for name in self.expand(paths):
            path = self.check(name)
            flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
            fd = os.open(path, flags)
            with os.fdopen(fd, 'rb') as handle:
                before = os.fstat(handle.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError('Source must be a regular file')
                raw = handle.read(legacy.MAX_FILE_BYTES+1)
                after = os.fstat(handle.fileno())
            if len(raw) > legacy.MAX_FILE_BYTES or b'\0' in raw:
                raise ValueError('Source too large or binary')
            self.check(name)
            current = path.stat()
            signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
            if signature(before) != signature(after) or signature(after) != signature(current):
                raise ValueError('Source changed while reading')
            total += len(raw)
            if total > MAX_BYTES:
                raise ValueError('Total source byte budget exceeded')
            text = raw.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
            result.append(dict(path=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), content=text))
        return result

    def unchanged(self, sources):
        now = self.load([f['path'] for f in sources])
        if [(f['path'], f['sha256']) for f in now] != [(f['path'], f['sha256']) for f in sources]:
            raise ValueError('Source changed; discard result')


def source_table(sources):
    return {f's{i}': {k: f[k] for k in ('path', 'sha256', 'bytes')}
            for i, f in enumerate(sources)}
