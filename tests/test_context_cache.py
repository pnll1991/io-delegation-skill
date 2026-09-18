import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/io-delegation/scripts'))
from context_mcp import ContextService
from context_cache import ResultCache
from test_context_semantic import reply, internal_semantic


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'p';self.root.mkdir();self.audit=self.base/'audit';self.audit.mkdir()
        (self.root/'a.txt').write_text('VALUE=3\nother\n')
        self.config=self.base/'worker.local.json';self.config.write_text(json.dumps(dict(approved=True,adapter='command',argv=[sys.executable,'-c','pass'],timeout_seconds=5)))
        self.service=ContextService(self.root,self.audit,files=['a.txt'],config=self.config)
        self.args=dict(selections=[dict(path='a.txt',select=dict(kind='lines',start=1,end=1))],question='What value?')

    def semantic(self,service=None):
        return internal_semantic(service or self.service,self.args)

    def test_repeat_exact_no_new_dispatch_or_worker_tokens(self):
        with patch('io_delegate.invoke',side_effect=reply) as invoke:
            first=self.semantic();second=self.semantic()
        self.assertEqual(invoke.call_count,1);self.assertEqual(first['model_calls'],1)
        self.assertEqual(second['model_calls'],0);self.assertEqual(second['cache'],'hit')
        events=[json.loads(x) for x in (self.audit/'.io-delegation/worker-events.jsonl').read_text().splitlines()]
        self.assertEqual(sum(x['event']=='worker_dispatched' for x in events),1)

    def test_source_change_invalidates_even_unselected_region(self):
        with patch('io_delegate.invoke',side_effect=reply) as invoke:
            self.semantic();(self.root/'a.txt').write_text('VALUE=3\nchanged unseen\n');r=self.semantic()
        self.assertEqual(invoke.call_count,2);self.assertEqual(r['cache'],'miss')

    def test_question_change_invalidates(self):
        with patch('io_delegate.invoke',side_effect=reply) as invoke:
            self.semantic();self.args['question']='Which symbol?';self.semantic()
        self.assertEqual(invoke.call_count,2)

    def test_config_change_requires_review_not_cached_answer(self):
        with patch('io_delegate.invoke',side_effect=reply):self.semantic()
        self.config.write_text(self.config.read_text()+' ')
        with patch('io_delegate.invoke',side_effect=AssertionError('model')):r=self.semantic()
        self.assertEqual(r['status'],'error')

    def test_narrowed_scope_not_cache_bypass(self):
        with patch('io_delegate.invoke',side_effect=reply):self.semantic()
        (self.root/'b.txt').write_text('other')
        narrowed=ContextService(self.root,self.audit,files=['b.txt'],config=self.config)
        with patch('io_delegate.invoke',side_effect=AssertionError('model')):r=self.semantic(narrowed)
        self.assertEqual(r['status'],'error')

    def test_different_repo_never_hits(self):
        with patch('io_delegate.invoke',side_effect=reply) as invoke:
            self.semantic();root=self.base/'p2';root.mkdir();(root/'a.txt').write_text((self.root/'a.txt').read_text())
            s=ContextService(root,self.audit,files=['a.txt'],config=self.config);self.semantic(s)
        self.assertEqual(invoke.call_count,2)

    def test_errors_and_insufficient_are_not_cached(self):
        def bad(job,cfg,root,record):
            out,meta=reply(job,cfg,root,record);obj=json.loads(out);obj['findings'][0]['evidence']='not present';return json.dumps(obj),meta
        with patch('io_delegate.invoke',side_effect=bad) as invoke:self.semantic();self.semantic()
        self.assertEqual(invoke.call_count,2)

    def test_local_cache_correct_and_changed_source_miss(self):
        args=dict(paths=['a.txt'],projection=dict(kind='lines',start=1,end=1))
        first=json.loads(self.service.call('extract',args)['content'][0]['text'])
        second=json.loads(self.service.call('extract',args)['content'][0]['text'])
        self.assertEqual((first['cache'],second['cache']),('miss','hit'))
        (self.root/'a.txt').write_text('VALUE=4')
        third=json.loads(self.service.call('extract',args)['content'][0]['text']);self.assertEqual(third['cache'],'miss')

    def test_corrupt_or_expired_entry_is_a_miss(self):
        cache=self.service.cache;k='a'*64;cache.put(k,dict(value=3))
        path=cache.path(k);item=json.loads(path.read_text());item['value']['value']=4;path.write_text(json.dumps(item))
        self.assertIsNone(cache.get(k));cache.put(k,dict(value=3))
        item=json.loads(path.read_text());item['created']=time.time()-4000;path.write_text(json.dumps(item));self.assertIsNone(cache.get(k))

    def test_symlinked_cache_never_read_or_written(self):
        cache=self.service.cache;k='b'*64;p=cache.path(k)
        try:p.symlink_to(self.root/'a.txt')
        except OSError:self.skipTest('symlinks unavailable')
        with self.assertRaises(ValueError):cache.get(k)
        with self.assertRaises(ValueError):cache.put(k,dict(value=1))

    def test_key_traversal_and_entry_limit(self):
        cache=self.service.cache
        with self.assertRaises(ValueError):cache.get('../x')
        self.assertFalse(cache.put('c'*64,dict(value='x'*70000)))

    def test_cache_disabled_does_not_reuse(self):
        service=ContextService(self.root,self.audit,files=['a.txt'],config=self.config,cache=False)
        with patch('io_delegate.invoke',side_effect=reply) as invoke:self.semantic(service);self.semantic(service)
        self.assertEqual(invoke.call_count,2)

    def test_eviction_bounded(self):
        cache=self.service.cache
        with patch('context_cache.MAX_ENTRIES',2):
            for i in range(3):cache.put(str(i)*64,dict(value=i))
        self.assertEqual(len(list(cache.folder.glob('*.json'))),2)


if __name__=='__main__':unittest.main()
