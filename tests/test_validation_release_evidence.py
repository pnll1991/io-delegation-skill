import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

BASE=Path(__file__).resolve().parents[1]/'benchmarks/v1-validation'
record_spec=importlib.util.spec_from_file_location('record',BASE/'record.py')
record=importlib.util.module_from_spec(record_spec); record_spec.loader.exec_module(record); sys.modules['record']=record
paired_spec=importlib.util.spec_from_file_location('paired',BASE/'paired.py')
paired=importlib.util.module_from_spec(paired_spec); paired_spec.loader.exec_module(paired); sys.modules['paired']=paired
spec=importlib.util.spec_from_file_location('release_evidence',BASE/'release_evidence.py')
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)


def row(task,family,arm,rep=1,success=True,tokens=100,source=1000,selected=100,router=False,worker=0,route='targeted_read'):
    return {
        'schema':record.SCHEMA,'run_id':f'001-{task}-{arm}-r{rep}','task_id':task,'family':family,'repo':'r','commit':'abc',
        'arm':arm,'host':'codex','success':success,'principal':{'raw_tokens':tokens,'usage':None},
        'router':{'called':router,'usage':None,'latency_ms':None},
        'worker':{'calls':worker,'accepted':worker,'rejected':0,'usage':None,'raw_tokens':None},
        'context':{'source_bytes':source,'selected_bytes':selected,'result_bytes':selected},'timing':{'wall_ms':1000},
        'validator':{'status':'pass' if success else 'fail'},'route':{'effective':route,'confidence':None,'fallback':False},
        'rework':0,'errors':[],
    }

class ReleaseEvidenceTests(unittest.TestCase):
    def test_dogfood_gates_pass_balanced_fixture(self):
        rows=[]
        families=[('large-understanding',5),('multi-file-factual',5),('cross-file-behavior',4),('principal',3),('small-control',3)]
        for family,count in families:
            for i in range(count):
                task=f'{family}-{i}'
                rows.append(row(task,family,'control',tokens=100,source=0,selected=0,route='bypass'))
                right_tokens=103 if family=='small-control' else 90
                route='principal' if family=='principal' else 'targeted_read'
                rows.append(row(task,family,'gateway-smart',tokens=right_tokens,source=1000,selected=200,route=route))
        _,gates=mod.dogfood_gates(rows,'control','gateway-smart',5.0,.5)
        self.assertTrue(all(item['pass'] for item in gates.values()))

    def test_principal_worker_call_fails_safety(self):
        rows=[]
        for i in range(3):
            task=f'p{i}'
            rows.append(row(task,'principal','control',route='bypass'))
            rows.append(row(task,'principal','gateway-smart',worker=1 if i==0 else 0,route='bulk_read' if i==0 else 'principal'))
        _,gates=mod.dogfood_gates(rows,'control','gateway-smart',5,.5)
        self.assertFalse(gates['principal_safety']['pass'])
        self.assertTrue(gates['principal_safety']['unsafe_run_ids'])

    def test_causal_gate_requires_component_call(self):
        rows=[row('x','targeted','gateway-local'),row('x','targeted','gateway-jev',tokens=90)]
        _,gate=mod.causal_gate(rows,'gateway-local','gateway-jev',1,'jev')
        self.assertFalse(gate['pass'])
        rows[-1]['router']['called']=True
        _,gate=mod.causal_gate(rows,'gateway-local','gateway-jev',1,'jev')
        self.assertTrue(gate['pass'])

    def test_release_is_fail_closed_on_security(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            dog=[]
            families=[('large-understanding',5),('multi-file-factual',5),('cross-file-behavior',4),('principal',3),('small-control',3)]
            for family,count in families:
                for i in range(count):
                    task=f'{family}-{i}'
                    dog += [row(task,family,'control',route='bypass',source=0,selected=0),
                            row(task,family,'gateway-smart',tokens=95 if family!='small-control' else 102,
                                route='principal' if family=='principal' else 'targeted_read')]
            jev=[]
            for i in range(20):
                jev += [row(f'j{i}','targeted','gateway-local',rep=i+1),row(f'j{i}','targeted','gateway-jev',rep=i+1,router=True,tokens=90)]
            worker=[]
            for i in range(5):
                worker += [row(f'w{i}','worker-eligible','direct-selected',rep=i+1),row(f'w{i}','worker-eligible','semantic-worker',rep=i+1,worker=1,tokens=90,route='bulk_read')]
            for name,rows in [('dog.jsonl',dog),('jev.jsonl',jev),('worker.jsonl',worker)]:
                path=root/name
                for item in rows: record.append(path,item)
            (root/'parity.json').write_text(json.dumps({'schema':'io-context-host-parity/v1','critical_deviations':0}),encoding='utf-8')
            (root/'security.json').write_text(json.dumps({'schema':'io-context-security-audit/v1','clean':False,'secret_hits':[{'env':'X'}],'forbidden_event_fields':[],'malformed_jsonl':[]}),encoding='utf-8')
            class Args: pass
            a=Args(); a.dogfood_records=root/'dog.jsonl'; a.jev_records=root/'jev.jsonl'; a.worker_records=root/'worker.jsonl'; a.parity=root/'parity.json'; a.security=root/'security.json'
            a.dogfood_left='control'; a.dogfood_right='gateway-smart'; a.jev_left='gateway-local'; a.jev_right='gateway-jev'; a.worker_left='direct-selected'; a.worker_right='semantic-worker'
            a.small_overhead_pct=5.; a.context_ratio=.5; a.min_jev_pairs=20; a.min_worker_pairs=5
            result=mod.evaluate(a)
            self.assertFalse(result['ready']); self.assertFalse(result['gates']['security_audit']['pass'])

if __name__=='__main__': unittest.main()
