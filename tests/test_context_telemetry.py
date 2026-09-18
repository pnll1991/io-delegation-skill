import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/io-delegation/scripts'))
from context_telemetry import trajectory,workers,operations,combined


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def log(self,name,rows):
        p=self.root/name;p.parent.mkdir(exist_ok=True,parents=True);p.write_text(''.join(json.dumps(r)+'\n' for r in rows));return p
    def completed(self,**usage):return dict(type='turn.completed',usage=usage)
    def worker_rows(self,fail=False):
        return [dict(schema='io-worker/v2',call_id='a',seq=i,event=e,**kw) for i,(e,kw) in enumerate([
          ('worker_attempt',{}),('worker_dispatched',{}),('worker_response',dict(usage=dict(input_tokens=50,output_tokens=5))),
          ('worker_error',{}) if fail else ('worker_completed',dict(accepted=True))])]
    def test_cached_and_reasoning_not_double_counted(self):
        p=self.log('main',[self.completed(input_tokens=100,cached_input_tokens=90,output_tokens=20,reasoning_output_tokens=10)])
        r=trajectory(p);self.assertEqual(r['raw_tokens'],120);self.assertTrue(r['usage_complete']);self.assertIsNone(r['model_requests'])
    def test_command_count_not_inference_count(self):
        item=dict(type='item.completed',item=dict(id='x',type='command_execution',aggregated_output='hello'))
        p=self.log('main',[item,item,self.completed(input_tokens=20,output_tokens=1)])
        r=trajectory(p);self.assertEqual(r['command_items'],1);self.assertEqual(r['completed_user_turns'],1);self.assertIsNone(r['model_requests'])
    def test_missing_usage_not_free(self):
        r=trajectory(self.log('main',[dict(type='turn.failed')]))
        self.assertIsNone(r['raw_tokens']);self.assertFalse(r['usage_complete'])
    def test_invalid_usage_subcounts(self):
        r=trajectory(self.log('main',[self.completed(input_tokens=10,cached_input_tokens=11,output_tokens=1)]));self.assertFalse(r['usage_complete'])
    def test_rejected_worker_usage_is_counted(self):
        r=workers(self.log('journal',self.worker_rows(True)));self.assertEqual(r['raw_tokens'],55);self.assertEqual(r['failures'],1)
    def test_unreported_worker_usage_unknown(self):
        es=self.worker_rows();del es[2]
        r=workers(self.log('journal',es));self.assertIsNone(r['raw_tokens']);self.assertEqual(r['unknown_calls'],1)
    def test_conflicting_duplicates_invalidate_journal(self):
        es=self.worker_rows();es.append(dict(es[2],usage=dict(input_tokens=0,output_tokens=0)))
        self.assertFalse(workers(self.log('journal',es))['accounting_complete'])
    def test_cache_hit_is_not_worker_dispatch(self):
        es=[dict(schema='io-context/v1',operation_id='x',event='operation_started',operation='semantic_query'),
            dict(schema='io-context/v1',operation_id='x',event='operation_completed',operation='semantic_query',cache='hit',model_calls=0,result_bytes=50)]
        r=operations(self.log('ops',es));self.assertEqual(r['cache_hits'],1);self.assertEqual(r['model_calls'],0)
    def test_operations_account_for_router_and_orchestrator_tokens(self):
        rows=[
            dict(schema='io-context/v1',operation_id='x',event='operation_started',operation='query'),
            dict(schema='io-context/v1',operation_id='x',event='operation_completed',operation='query',
                 cache='disabled',model_calls=1,result_bytes=50,router_calls=1,
                 router_input_tokens=10,router_output_tokens=2,orchestrator_calls=1,
                 orchestrator_input_tokens=8,orchestrator_output_tokens=2,
                 compute_tier='T1',escalated=False)
        ]
        r=operations(self.log('ops-control',rows))
        self.assertEqual(r['control_tokens'],22)
        self.assertTrue(r['control_usage_complete'])
        self.assertEqual(r['orchestrator_calls'],1)
        self.assertEqual(r['compute_tiers'],{'T1':1})

    def test_operations_report_model_profiles_and_multi_escalations(self):
        rows=[
            dict(schema='io-context/v1',operation_id='x',event='operation_started',operation='query'),
            dict(schema='io-context/v1',operation_id='x',event='operation_completed',operation='query',
                 cache='disabled',model_calls=3,result_bytes=50,orchestrator_calls=1,
                 orchestrator_input_tokens=8,orchestrator_output_tokens=2,
                 compute_tier='T1',model_escalations=2,escalated=True,
                 final_model_profile='terra-medium')
        ]
        r=operations(self.log('ops-model-policy',rows))
        self.assertEqual(r['model_profiles'],{'terra-medium':1})
        self.assertEqual(r['escalations'],2)

    def test_missing_orchestrator_usage_is_not_free(self):
        rows=[
            dict(schema='io-context/v1',operation_id='x',event='operation_started',operation='query'),
            dict(schema='io-context/v1',operation_id='x',event='operation_completed',operation='query',
                 cache='disabled',model_calls=0,result_bytes=50,orchestrator_calls=1,
                 orchestrator_input_tokens=None,orchestrator_output_tokens=None,compute_tier='T2')
        ]
        r=operations(self.log('ops-unknown-control',rows))
        self.assertFalse(r['control_usage_complete'])
        self.assertIsNone(r['control_tokens'])

    def test_semantic_without_operation_records_incomplete(self):
        main=self.log('main',[dict(type='item.completed',item=dict(id='x',type='mcp_tool_call',tool='semantic_query')),self.completed(input_tokens=20,output_tokens=5)])
        r=combined(main,self.root);self.assertFalse(r['system_accounting_complete']);self.assertIsNone(r['system_raw_tokens'])
    def test_native_subagents_not_claimed_fully_attributed(self):
        main=self.log('main',[dict(type='item.completed',item=dict(id='x',type='collab_tool_call')),self.completed(input_tokens=20,output_tokens=5)])
        self.assertFalse(combined(main,self.root)['system_accounting_complete'])


if __name__=='__main__':unittest.main()
