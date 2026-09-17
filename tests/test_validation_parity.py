import importlib.util
import sys
import unittest
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]/'benchmarks/v1-validation'
record_spec=importlib.util.spec_from_file_location('record',BASE/'record.py')
record=importlib.util.module_from_spec(record_spec); record_spec.loader.exec_module(record); sys.modules['record']=record
spec=importlib.util.spec_from_file_location('parity',BASE/'parity.py')
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)


def row(host,success=True,route='targeted_read',task='t1'):
    return {
        'schema':record.SCHEMA,'run_id':f'{host}-{task}-r1','task_id':task,'family':'targeted',
        'repo':'r','commit':'abc','arm':'gateway-host','host':host,'success':success,
        'principal':{'raw_tokens':100,'usage':None},
        'router':{'called':False,'usage':None,'latency_ms':None},
        'worker':{'calls':0,'accepted':0,'rejected':0,'usage':None,'raw_tokens':None},
        'context':{'source_bytes':1000,'selected_bytes':100,'result_bytes':80},
        'timing':{'wall_ms':1000},'validator':{'status':'pass' if success else 'fail'},
        'route':{'effective':route,'confidence':None,'fallback':False},'rework':0,'errors':[],
    }

class ParityTests(unittest.TestCase):
    def test_matching_hosts_have_no_deviation(self):
        result=mod.summarize({'codex':[row('codex')],'claude':[row('claude')],'cursor':[row('cursor')]})
        self.assertEqual(result['critical_deviations'],0)
        self.assertEqual(result['matrix'][0]['deviations'],[])

    def test_route_mismatch_is_critical(self):
        result=mod.summarize({'codex':[row('codex')],'claude':[row('claude',route='principal')],'cursor':[row('cursor')]})
        kinds=[item['kind'] for item in result['deviations']]
        self.assertIn('route_mismatch',kinds)
        self.assertGreater(result['critical_deviations'],0)

    def test_missing_host_is_critical(self):
        result=mod.summarize({'codex':[row('codex')],'claude':[row('claude')]})
        self.assertEqual(result['matrix'][0]['hosts']['cursor'],None)
        self.assertTrue(any(item['kind']=='missing_host' for item in result['deviations']))

    def test_markdown_mentions_route_and_worker(self):
        result=mod.summarize({'codex':[row('codex')],'claude':[row('claude')],'cursor':[row('cursor')]})
        text=mod.markdown(result)
        self.assertIn('targeted_read',text); self.assertIn('worker=0',text)

    def test_complete_lifecycle_is_required_when_supplied(self):
        records={'codex':[row('codex')],'claude':[row('claude')],'cursor':[row('cursor')]}
        lifecycle={host:{'host':host,'lifecycle':{phase:'pass' for phase in mod.LIFECYCLE_PHASES},'error':None}
                   for host in mod.DEFAULT_HOSTS}
        result=mod.summarize(records,lifecycles=lifecycle)
        self.assertTrue(result['lifecycle_complete'])
        self.assertEqual(result['critical_deviations'],0)

    def test_missing_or_failed_lifecycle_is_critical(self):
        records={'codex':[row('codex')],'claude':[row('claude')],'cursor':[row('cursor')]}
        lifecycle={host:{'host':host,'lifecycle':{phase:'pass' for phase in mod.LIFECYCLE_PHASES},'error':None}
                   for host in ('codex','claude')}
        lifecycle['claude']['lifecycle']['security']='fail'
        result=mod.summarize(records,lifecycles=lifecycle)
        kinds=[item['kind'] for item in result['deviations']]
        self.assertIn('lifecycle_failure',kinds)
        self.assertIn('missing_lifecycle',kinds)
        self.assertFalse(result['lifecycle_complete'])
        self.assertGreater(result['critical_deviations'],0)

if __name__=='__main__': unittest.main()
