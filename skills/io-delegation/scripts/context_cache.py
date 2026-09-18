"""Exact, bounded local result cache. Source/authorization checks happen BEFORE reads.

Stores authorized results (possibly source excerpts), not credentials or prompts.
Directory is operator-owned outside the agent workspace. No cross-repo reuse.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from context_sources import encoded, fingerprint

VERSION = 'context-cache/1'
MAX_ENTRY = 64_000
MAX_ENTRIES = 128
MAX_TOTAL = 4_000_000
TTL_SECONDS = 1800


def implementation_hash():
    h = hashlib.sha256()
    folder = Path(__file__).resolve().parent
    for name in ('context_sources.py','context_ops.py','context_selection.py','context_engine.py',
                 'context_orchestrator.py','context_cache.py','context_mcp.py','context_backend.py',
                 'context_budget.py','io_delegate.py','worker_runtime.py'):
        p = folder/name
        h.update(name.encode());h.update(p.read_bytes())
    return h.hexdigest()


def no_links(path):
    for p in (path, *path.parents):
        if not p.exists() and not p.is_symlink(): continue
        info = p.lstat()
        if p.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Symlink/reparse point in private cache path')


class ResultCache:
    def __init__(self, audit, enabled=True, ttl=TTL_SECONDS):
        self.folder = Path(audit)/'context-cache'
        self.enabled = enabled
        self.ttl = ttl
        if type(enabled) is not bool or type(ttl) is not int or not 1 <= ttl <= 86400:
            raise ValueError('Invalid cache options')
        self.code_hash = implementation_hash()
        if enabled:
            no_links(self.folder);self.folder.mkdir(mode=0o700, exist_ok=True)

    def key(self, scope, sources, operation, request, config_hash=None):
        return fingerprint(dict(version=VERSION, implementation=self.code_hash,
             namespace=scope.identity, operation=operation, request=request, config=config_hash,
             sources=[dict(path=f['path'], sha256=f['sha256']) for f in sources]))

    def path(self, key):
        if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{64}', key):
            raise ValueError('Invalid cache key')
        p = self.folder/(key+'.json');no_links(p)
        return p

    def get(self, key):
        if not self.enabled: return None
        p = self.path(key)
        if not p.is_file(): return None
        try:
            fd = os.open(p, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
            with os.fdopen(fd, 'rb') as f: raw = f.read(MAX_ENTRY+1)
            if len(raw) > MAX_ENTRY: return None
            item = json.loads(raw)
            if (not isinstance(item, dict) or item.get('key') != key or
                type(item.get('created')) not in (int, float) or
                not 0 <= time.time()-item['created'] <= self.ttl or
                item.get('digest') != fingerprint(item.get('value'))):
                return None
            return item['value']
        except (FileNotFoundError, ValueError, TypeError, KeyError):
            return None

    def put(self, key, value):
        if not self.enabled: return False
        p = self.path(key)
        raw = encoded(dict(key=key, created=time.time(), value=value, digest=fingerprint(value)))
        if len(raw) > MAX_ENTRY: return False
        fd, name = tempfile.mkstemp(prefix='.context-', dir=self.folder)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(raw);f.flush();os.fsync(f.fileno())
            self.path(key) # Recheck before atomic replacement; never follow an existing link.
            os.replace(name, p)
        finally:
            if os.path.exists(name): os.unlink(name)
        self.prune()
        return True

    def prune(self):
        no_links(self.folder)
        rows = []
        for p in self.folder.iterdir():
            if not re.fullmatch('[0-9a-f]{64}\\.json', p.name): continue
            no_links(p)
            if p.is_file(): rows.append((p.stat().st_mtime, p, p.stat().st_size))
        total = sum(row[2] for row in rows)
        rows.sort(key=lambda x:x[0])
        while rows and (len(rows) > MAX_ENTRIES or total > MAX_TOTAL):
            _, p, size = rows.pop(0);no_links(p);p.unlink(missing_ok=True);total -= size
