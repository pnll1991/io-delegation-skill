import importlib.util
from pathlib import Path
import sys, unittest

BASE=Path(__file__).resolve().parents[1]/'benchmarks/v1-validation'
sys.path.insert(0,str(BASE))
spec=importlib.util.spec_from_file_location('activation_evidence',BASE/'activation_evidence.py')
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

def row(arm,run_id,success=True,tokens=100,wall=1000,decision='bypass',worker=0,router=False,source=0):
    return {
        'schema':'io-context-validation/v1','run_id':run_id,'task_id':'t','family':'small-control',
        'repo':'r','commit':'abc','arm':arm,'host':'codex','started_at':1.0,'ended_at':2.0,
        'success':success,'activation':{'decision':decision,'reason':'test','signals':{}},
        'route':{'effective':'bypass' if decision=='bypass' else 'targeted_read','confidence':None,'fallback':False},
        'principal':{'model':'m','usage':None,'raw_tokens':tokens},
        'router':{'called':router,'usage':None,'latency_ms':None},
        'worker':{'calls':worker,'accepted':worker,'rejected':0,'usage':None,'raw_tokens':None},
        'context':{'source_bytes':source,'selected_bytes':source,'result_bytes':source},
        'timing':{'wall_ms':wall},'validator':{'status':'pass' if success else 'fail'},
        'rework':0,'errors':[],'notes':'','tags':[]
    }

class ActivationEvidenceTests(unittest.TestCase):
    def test_clean_pair_passes(self):
        rows=[row('gate-always','001-t-gate-always-r1',tokens=100,wall=1000,decision='enable'),
              row('gate-auto','002-t-gate-auto-r1',tokens=80,wall=900)]
        out=mod.evaluate(rows,required_pairs=1,min_valid_pairs=1)
        self.assertTrue(out['pass'])
        self.assertAlmostEqual(out['principal_token_delta_pct']['median'],-20.0)
        self.assertEqual(out['wall_time_delta_pct']['median'],-10.0)

    def test_false_enable_fails(self):
        rows=[row('gate-always','001-t-gate-always-r1',decision='enable'),
              row('gate-auto','002-t-gate-auto-r1',decision='enable')]
        out=mod.evaluate(rows,required_pairs=1,min_valid_pairs=1)
        self.assertFalse(out['gates']['all_small_controls_bypass'])

    def test_bypass_with_gateway_call_fails(self):
        rows=[row('gate-always','001-t-gate-always-r1',decision='enable'),
              row('gate-auto','002-t-gate-auto-r1',worker=1)]
        out=mod.evaluate(rows,required_pairs=1,min_valid_pairs=1)
        self.assertFalse(out['gates']['bypass_has_zero_router_worker_calls'])

    def test_quality_regression_fails(self):
        rows=[row('gate-always','001-t-gate-always-r1',success=True,decision='enable'),
              row('gate-auto','002-t-gate-auto-r1',success=False)]
        out=mod.evaluate(rows,required_pairs=1,min_valid_pairs=1)
        self.assertFalse(out['gates']['functional_quality'])
        self.assertFalse(out['gates']['valid_efficiency_sample'])

    def test_context_on_bypass_fails(self):
        rows=[row('gate-always','001-t-gate-always-r1',decision='enable'),
              row('gate-auto','002-t-gate-auto-r1',source=10)]
        out=mod.evaluate(rows,required_pairs=1,min_valid_pairs=1)
        self.assertFalse(out['gates']['bypass_has_zero_gateway_context'])

if __name__=='__main__': unittest.main()
