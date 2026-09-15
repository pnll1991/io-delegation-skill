"""Offline integration/regression tests. No live models, accounts or external APIs."""
from __future__ import annotations
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/io-delegation/scripts'))
sys.path.insert(0,str(ROOT/'benchmarks/codex-ab'))
import io_delegate as d
import worker_runtime as rt
import codex_io_ab as ab
import setup_worker as setup
import validate_html_inventory as val
import bounded_search


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        (self.root/'sample.py').write_text('LIMIT = 7\n',encoding='utf-8')
    def cfg(self,code,**kw):
        cfg=dict(approved=True,adapter='command',argv=[sys.executable,'-c',code]); cfg.update(kw)
        p=self.root/'worker.local.json'; p.write_text(json.dumps(cfg),encoding='utf-8-sig'); return p
    def response(self,**kw):
        ans=dict(status='ok',read_paths=['sample.py'],unknowns=[],findings=[dict(path='sample.py',symbol='LIMIT',evidence='LIMIT = 7',fact='LIMIT is 7')]); ans.update(kw)
        return dict(output=json.dumps(ans),usage=dict(input_tokens=50,output_tokens=10))
    def invoke(self,cfg,**kw):
        args=['bulk-read','--root',str(self.root),'--paths','sample.py','--question','What is LIMIT?']
        if cfg: args+=['--config',str(cfg)]
        out,err=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err): rc=d.main(args)
        es,errors=ab.load_events(self.root/'.io-delegation/worker-events.jsonl')
        return rc,out.getvalue(),err.getvalue(),ab.worker_usage(es,errors)
    def test_working_worker_dispatch_is_counted(self):
        cfg=self.cfg('import sys;sys.stdin.read();print('+repr(json.dumps(self.response()))+')')
        rc,out,err,u=self.invoke(cfg)
        self.assertEqual(rc,0,err); self.assertEqual(u['worker_calls'],1); self.assertEqual(u['worker_accepted'],1)
        self.assertEqual(u['worker_input_tokens'],50); self.assertTrue(u['worker_accounting_complete'])
        self.assertNotIn('LIMIT = 7',(self.root/'.io-delegation/worker-events.jsonl').read_text())
        self.assertEqual(json.loads(err)['event'],'worker_response')
    def test_no_config_is_attempt_not_dispatch(self):
        rc,out,err,u=self.invoke(None)
        self.assertEqual(rc,2); self.assertEqual(u['worker_attempts'],1); self.assertEqual(u['worker_calls'],0)
        self.assertTrue(u['worker_accounting_complete'])
    def test_unapproved_never_dispatches(self):
        cfg=self.cfg('raise Exception()',approved=False)
        rc,_,_,u=self.invoke(cfg); self.assertEqual(rc,2); self.assertEqual(u['worker_calls'],0)
    def test_placeholder_models_rejected(self):
        for adapter in ('codex-cli','chat-completions'):
            cfg=self.root/'bad.json'; cfg.write_text(json.dumps(dict(approved=True,adapter=adapter,model='YOUR_MODEL',url='http://127.0.0.1/x')))
            with self.assertRaises(d.DelegateError): d.load_config(str(cfg))
    def test_invalid_evidence_retains_usage(self):
        r=self.response(); a=json.loads(r['output']); a['findings'][0]['evidence']='not here'; r['output']=json.dumps(a)
        rc,_,_,u=self.invoke(self.cfg('print('+repr(json.dumps(r))+')'))
        self.assertEqual(rc,2); self.assertEqual(u['worker_accepted'],0); self.assertEqual(u['worker_input_tokens'],50)
        self.assertTrue(u['worker_accounting_complete'])
    def test_insufficient_is_not_accepted(self):
        r=self.response(status='insufficient_context',findings=[],unknowns=['Need another file'])
        rc,_,_,u=self.invoke(self.cfg('print('+repr(json.dumps(r))+')'))
        self.assertEqual(rc,0); self.assertEqual(u['worker_accepted'],0)
    def test_timeout_unknown_not_zero(self):
        rc,_,_,u=self.invoke(self.cfg('import time;time.sleep(4)',timeout_seconds=0.1))
        self.assertEqual(rc,2); self.assertEqual(u['worker_calls'],1)
        self.assertEqual(u['worker_usage_unknown_calls'],1); self.assertFalse(u['worker_accounting_complete'])
    def test_failed_transport_private_output_not_logged(self):
        rc,out,err,u=self.invoke(self.cfg('import sys;print("PRIVATE_MARKER",file=sys.stderr);sys.exit(7)'))
        self.assertEqual(rc,2); self.assertNotIn('PRIVATE_MARKER',out+err); self.assertIn('exit 7',err)
        self.assertFalse(u['worker_accounting_complete'])
    def test_missing_executable_not_dispatched(self):
        cfg=self.cfg(''); m=ab.read_json(cfg); m['argv']=['not-a-real-executable-abc']; ab.write_json(cfg,m)
        rc,_,_,u=self.invoke(cfg); self.assertEqual(rc,2); self.assertEqual(u['worker_calls'],0)
    def test_budget_stops_second_call(self):
        cfg=self.cfg('print('+repr(json.dumps(self.response()))+')',max_calls_per_workspace=1)
        self.assertEqual(self.invoke(cfg)[0],0)
        rc,_,_,u=self.invoke(cfg); self.assertEqual(rc,2); self.assertEqual(u['worker_calls'],1); self.assertEqual(u['worker_attempts'],2)
    def test_duplicates_not_counted_twice(self):
        self.invoke(self.cfg('print('+repr(json.dumps(self.response()))+')'))
        es,e=ab.load_events(self.root/'.io-delegation/worker-events.jsonl')
        self.assertEqual(ab.worker_usage(es+es)['worker_calls'],1)
    def test_symlink_telemetry_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            try: (self.root/'.io-delegation').symlink_to(tmp,target_is_directory=True)
            except OSError: self.skipTest('symlink unavailable')
            rc,_,_,u=self.invoke(None); self.assertEqual(rc,2); self.assertEqual(list(Path(tmp).iterdir()),[])
    def test_jsonl_without_turn_complete_unknown(self):
        p=self.root/'codex.jsonl'; p.write_text('{"type":"turn.started"}\n')
        r=ab.usage_from_jsonl(p); self.assertFalse(r['principal_usage_complete']); self.assertIsNone(r['input_tokens'])
    def test_total_not_double_count_reasoning_cached(self):
        p=self.root/'codex.jsonl'; p.write_text(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=100,cached_input_tokens=70,cache_write_input_tokens=0,output_tokens=10,reasoning_output_tokens=8)))+'\n')
        r=ab.usage_from_jsonl(p); self.assertEqual(r['input_tokens']+r['output_tokens'],110)
    def test_prices_not_assumed(self):
        self.assertIsNone(ab.price(dict(input_tokens=100,output_tokens=10),None))
    def test_subagent_usage_not_claimed_complete(self):
        p=self.root/'codex.jsonl'; p.write_text(json.dumps(dict(type='item.completed',item=dict(id='a',type='collab_tool_call')))+'\n')
        self.assertFalse(ab.usage_from_jsonl(p)['worker_accounting_complete'])
    def test_malformed_journal_incomplete(self):
        self.assertFalse(ab.worker_usage([],1)['worker_accounting_complete'])
    def test_changed_source_rejects_with_usage(self):
        def fn(*args):
            (self.root/'sample.py').write_text('LIMIT = 9\n')
            return self.response()['output'],dict(usage=dict(input_tokens=20,output_tokens=5))
        with mock.patch.object(d,'invoke',side_effect=fn): rc,_,err,u=self.invoke(self.cfg('pass'))
        self.assertEqual(rc,2); self.assertIn('corpus cambió',err)
    def test_code_writer_only_candidate(self):
        cfg=self.cfg('print('+repr(json.dumps(dict(output='assert True\n',usage=dict(input_tokens=3,output_tokens=2))))+')')
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            rc=d.main(['code-write','--root',str(self.root),'--config',str(cfg),'--reference','sample.py','--spec','new test','--target','new_test.py'])
        self.assertEqual(rc,0); self.assertFalse((self.root/'new_test.py').exists()); self.assertTrue((self.root/'.io-delegation/candidates/new_test.py').is_file())
    def test_bounded_minified_search(self):
        (self.root/'big.html').write_text('a'*20000+'<title>Hello</title>'+'b'*20000)
        result=bounded_search.search(self.root,['big.html'],'<title>',1000)
        self.assertLess(len(json.dumps(result).encode()),1000); self.assertEqual(result['match_count'],1)
    def test_literal_json_braces_not_formatted(self):
        v='print({"a":1}); {python}'; self.assertEqual(ab.format_value(v,{'python':'PY'}),'print({"a":1}); PY')
    def test_argv_with_spaces_and_unicode(self):
        ok,_=ab.check_commands(self.root,[[sys.executable,'-c','import sys;assert sys.argv[1]=="café path"','café path']],10,{})
        self.assertTrue(ok)
    def test_smoke_real_runner_with_stub_no_models(self):
        stub=self.root/'stub.py'
        stub.write_text("import json,sys\nj=json.load(sys.stdin);u=json.loads(j['messages'][1]['content']);f=u['files'][0];v=f['content'].strip();print(json.dumps({'output':json.dumps({'status':'ok','findings':[{'path':f['path'],'symbol':'WORKER_CHECK','evidence':v,'fact':v}],'unknowns':[],'read_paths':[f['path']]}),'usage':{'input_tokens':80,'output_tokens':20}}))")
        cfg=self.root/'worker.local.json'; ab.write_json(cfg,dict(approved=True,adapter='command',argv=[sys.executable,str(stub)]))
        dest=self.root/'smoke';dest.mkdir();r=setup.smoke(cfg,dest)
        self.assertTrue(r['ok']);self.assertEqual(r['stats']['worker_calls'],1)
    def test_smoke_fails_stops_bad_adapter(self):
        cfg=self.cfg('print("bad")');dest=self.root/'smoke';dest.mkdir()
        self.assertFalse(setup.smoke(cfg,dest)['ok'])


class HTMLTests(unittest.TestCase):
    def test_void_br_and_nested_em(self):
        p=val.PageParser();p.feed('<title>A &amp; B</title><h1>Hello<br><em>world</em></h1><p>not H1</p><h1>other</h1>')
        self.assertEqual(p.title,'A & B');self.assertEqual(p.h1,'Hello world')
    def test_self_closed_br(self):
        p=val.PageParser();p.feed('<h1>A<br/>B</h1>');self.assertEqual(p.h1,'A B')
    def test_single_entity_decode(self):
        p=val.PageParser();p.feed('<title>&amp;lt;</title>');self.assertEqual(p.title,'&lt;')
    def test_order_ignored_values_checked(self):
        a=[dict(path='x',title='a',h1='b'),dict(path='y',title='c',h1='d')]
        self.assertEqual(val.differences(list(reversed(a)),a),[])
        with self.assertRaises(ValueError): val.normalize([a[0],a[0]])
    def test_missing_and_wrong_field_reported(self):
        e=[dict(path='x',title='a',h1='b')];a=[dict(path='x',title='bad',h1='b')]
        self.assertEqual(val.differences(a,e)[0]['field'],'title')
    def test_bom_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'a.json';p.write_text('{"x":1}',encoding='utf-8-sig');self.assertEqual(ab.read_json(p),{'x':1})


if __name__=='__main__':unittest.main()
