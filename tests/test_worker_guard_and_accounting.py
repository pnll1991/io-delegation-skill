"""Offline guard metadata and accounting edge cases; no model invocations."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/io-delegation/scripts'));sys.path.insert(0,str(ROOT/'benchmarks/codex-ab'))
import read_guard as guard
import worker_runtime as rt
import codex_io_ab as ab

class GuardTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.root=Path(self.t.name)
  (self.root/'large.txt').write_text('x\n'*400);(self.root/'mini.txt').write_text('x'*70000)
 def test_large_read_blocked(self):
  r=guard.evaluate({'tool_name':'Bash','tool_input':{'command':'cat large.txt'}},self.root,guard.DEFAULTS)
  self.assertEqual(r['decision'],'deny')
 def test_bounded_range_allowed(self):
  r=guard.evaluate({'tool_name':'Bash','tool_input':{'command':'head -n 20 large.txt'}},self.root,guard.DEFAULTS)
  self.assertEqual(r['decision'],'pass');self.assertTrue(r['covered'])
 def test_minified_range_still_bounded_by_bytes(self):
  r=guard.evaluate({'tool_name':'Read','tool_input':{'file_path':'mini.txt','limit':1}},self.root,guard.DEFAULTS)
  self.assertEqual(r['decision'],'deny')
 def test_generic_preflight_does_not_certify_host(self):
  row=dict(code='within_budget',covered=True,decision='pass',would_block=False)
  guard.audit_event(self.root,{'session_id':'synthetic'},row,'generic')
  self.assertFalse((self.root/'.io-delegation-hooks/events.jsonl').exists())
 def test_registered_host_metadata_only(self):
  folder=self.root/'.io-delegation-hooks';folder.mkdir();(folder/'policy.json').write_text('{}')
  row=dict(code='line_budget',covered=True,decision='deny',would_block=True)
  guard.audit_event(self.root,{'session_id':'PRIVATE_SESSION','tool_input':{'command':'PRIVATE_COMMAND'}},row,'codex')
  text=(folder/'events.jsonl').read_text();self.assertNotIn('PRIVATE',text)
  self.assertTrue(json.loads(text)['host_event'])
 def test_malformed_details_do_not_crash(self):
  u=rt.normalize_usage({'input_tokens':10,'output_tokens':2,'input_tokens_details':'bad'})
  self.assertTrue(rt.usage_complete(u));self.assertIsNone(u['cached_input_tokens'])
 def test_reasoning_is_not_added_twice(self):
  u,ok,_,_=rt.codex_stream(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':20,'reasoning_output_tokens':15}}).encode())
  self.assertTrue(ok);self.assertEqual(u['input_tokens']+u['output_tokens'],120)
 def test_missing_final_event_is_not_free(self):
  events=[dict(schema='io-worker/v2',call_id='x',seq=i,event=e) for i,e in enumerate(['worker_attempt','worker_dispatched'])]
  u=ab.worker_usage(events)
  self.assertEqual(u['worker_usage_unknown_calls'],1);self.assertFalse(u['worker_accounting_complete'])
 def test_dedup_does_not_duplicate_consumption(self):
  events=[dict(schema='io-worker/v2',call_id='x',seq=i,event=e) for i,e in enumerate(['worker_attempt','worker_dispatched','worker_response','worker_completed'])]
  events[2]['usage']=dict(input_tokens=10,output_tokens=3,cached_input_tokens=5,cache_write_input_tokens=0,reasoning_output_tokens=1)
  events[3]['accepted']=True
  u=ab.worker_usage(events+events)
  self.assertEqual(u['worker_calls'],1);self.assertEqual(u['worker_input_tokens'],10);self.assertEqual(u['worker_cached_input_tokens'],5)

if __name__=='__main__':unittest.main()
