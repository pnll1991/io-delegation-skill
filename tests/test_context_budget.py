import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/io-delegation/scripts'))
from context_budget import limits, observed
from context_mcp import ContextService
from worker_runtime import TransportError
from context_backend import reader_schema
import test_context_semantic as semantic
from test_context_semantic import reply


class BudgetTests(semantic.SemanticTests):
    # Inherits transport invariants, then exercises budget-specific faults.
    def reconfigure(self,**extra):
        cfg=json.loads(self.config.read_text());cfg.update(extra);self.config.write_text(json.dumps(cfg))
        self.service=ContextService(self.root,self.audit,files=['a.txt'],config=self.config,cache=False)

    def test_unknown_limits_rejected(self):
        for cfg in [dict(context_limits={'x':1}),dict(context_limits={'max_calls':True}),dict(context_limits={'max_output_tokens':0})]:
            with self.assertRaises(ValueError):limits(cfg)

    def test_reported_token_budget_preserves_crossing_call_then_stops(self):
        self.reconfigure(context_limits={'max_task_tokens':30})
        with patch('io_delegate.invoke',side_effect=reply) as invoke:
            first=self.run_call();second=self.run_call()
        self.assertEqual(first['status'],'ok');self.assertEqual(second['status'],'budget_exceeded');self.assertEqual(invoke.call_count,1)
        state=observed(self.audit/'.io-delegation/worker-events.jsonl');self.assertEqual(state['tokens'],35)

    def test_unknown_dispatched_usage_freezes_next_call(self):
        def timeout(job,cfg,root,record):
            record('worker_dispatched',adapter='synthetic');raise TransportError('timeout')
        with patch('io_delegate.invoke',side_effect=timeout) as invoke:
            r1=self.run_call();r2=self.run_call()
        self.assertEqual((r1['status'],r2['status']),('error','budget_exceeded'));self.assertEqual(invoke.call_count,1)
        self.assertTrue(observed(self.audit/'.io-delegation/worker-events.jsonl')['unknown'])

    def test_rejected_evidence_is_not_free(self):
        self.reconfigure(context_limits={'max_task_tokens':30})
        def bad(job,cfg,root,record):
            out,meta=reply(job,cfg,root,record);obj=json.loads(out);obj['findings'][0]['evidence']='fabricated';return json.dumps(obj),meta
        with patch('io_delegate.invoke',side_effect=bad) as invoke:
            self.assertEqual(self.run_call()['status'],'error');self.assertEqual(self.run_call()['status'],'budget_exceeded')
        self.assertEqual(invoke.call_count,1)
        self.assertEqual(observed(self.audit/'.io-delegation/worker-events.jsonl')['tokens'],35)

    def test_generation_limit_passed_to_minimal_backend(self):
        self.reconfigure(context_limits={'max_output_tokens':64})
        with patch('io_delegate.invoke',side_effect=reply) as invoke:self.run_call()
        self.assertEqual(invoke.call_args.args[1]['reader_max_tokens'],64)

    def test_large_selection_rejected_before_dispatch(self):
        (self.root/'a.txt').write_text('X'*1000)
        self.reconfigure(context_limits={'max_selected_bytes':256})
        self.args['selections'][0]['select']=dict(kind='span',start=0,end=1000)
        with patch('io_delegate.invoke',side_effect=AssertionError('model')):r=self.run_call()
        self.assertEqual(r['model_calls'],0);self.assertEqual(r['status'],'error')

    def test_stale_inflight_record_is_not_zero(self):
        path=self.audit/'x.jsonl'
        path.write_text(json.dumps(dict(schema='io-worker/v2',event='worker_dispatched',call_id='x',seq=0))+'\n')
        self.assertTrue(observed(path)['unknown'])

    def test_json_schema_matches_validator_lengths(self):
        s=reader_schema(bounded=True);finding=s['properties']['findings']
        self.assertEqual(finding['maxItems'],12);self.assertEqual(finding['items']['properties']['evidence']['maxLength'],240)
        self.assertNotIn('maxItems',reader_schema(bounded=False)['properties']['findings'])


if __name__=='__main__':unittest.main()
