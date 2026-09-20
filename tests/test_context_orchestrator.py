from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/io-delegation/scripts'))
import context_orchestrator as compute


def response(**scores):
    values=dict(cheap_model_sufficient=.91,risk_high=.08,uncertainty_high=.12,
                reasoning_required=.10,parallelism_useful=.35)
    values.update(scores)
    return dict(model='jev-test',answers={
        name:dict(type='noul',noul=value) for name,value in values.items()
    },usage=dict(input_tokens=44,output_tokens=9))


class ComputeOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.cfg=self.root/'compute.json'
        self.cfg.write_text(json.dumps(dict(version=1,approved=True,provider='typesafe',
            model='jev-latest',api_key_env='TYPESAFE_API_KEY',timeout_seconds=5,
            min_confidence=.75,url='http://127.0.0.1:8765/mock')))

    def test_parse_scores_and_usage(self):
        cfg=compute.load_config(self.cfg)
        row=compute.parse_response(response(),cfg)
        self.assertEqual(row['scores']['cheap_model_sufficient'],.91)
        self.assertEqual(row['usage'],dict(input_tokens=44,output_tokens=9))

    def test_policy_gates_on_sufficiency_risk_and_reasoning_not_metadata_uncertainty(self):
        scores=compute.parse_response(response(),compute.load_config(self.cfg))['scores']
        self.assertEqual(compute.decide(scores,'factual',True)['tier'],'T1')
        for key,value in [('cheap_model_sufficient',.4),('risk_high',.8),
                          ('reasoning_required',.8)]:
            bad=dict(scores);bad[key]=value
            self.assertEqual(compute.decide(bad,'factual',True)['tier'],'T2')
        noisy=dict(scores);noisy['uncertainty_high']=.99
        self.assertEqual(compute.decide(noisy,'factual',True)['tier'],'T1')

    def test_sensitive_operations_and_missing_worker_force_principal(self):
        scores=compute.parse_response(response(),compute.load_config(self.cfg))['scores']
        for op in ('debugging','architecture','security','editing','generation'):
            self.assertEqual(compute.decide(scores,op,True)['decision'],'principal')
        self.assertEqual(compute.decide(scores,'factual',False)['reason'],'worker_unavailable')

    def test_worker_acceptance_requires_verified_findings_and_no_unknowns(self):
        good=dict(status='ok',findings=[{'fact':'x'}],unknowns=[])
        self.assertTrue(compute.accepted_worker_result(good))
        self.assertFalse(compute.accepted_worker_result(dict(good,unknowns=['missing'])))
        self.assertFalse(compute.accepted_worker_result(dict(good,status='insufficient_context')))

    def test_codex_cheap_profile_can_lower_model_and_effort(self):
        cfg=dict(adapter='codex-cli',model='strong',reasoning_effort='high',
                 compute_profiles={'cheap':{'model':'cheap','reasoning_effort':'low'}})
        derived,meta=compute.cheap_worker_config(cfg,900)
        self.assertEqual((derived['model'],derived['reasoning_effort']),('cheap','low'))
        self.assertEqual(meta['profile'],'cheap')
        self.assertFalse(meta['output_token_cap_supported'])

    def test_chat_cheap_profile_enforces_output_cap(self):
        cfg=dict(adapter='chat-completions',model='strong',reader_max_tokens=1600,
                 compute_profiles={'cheap':{'model':'cheap','max_output_tokens':1200}})
        derived,meta=compute.cheap_worker_config(cfg,900)
        self.assertEqual(derived['model'],'cheap')
        self.assertEqual(derived['reader_max_tokens'],900)
        self.assertTrue(meta['output_token_cap_supported'])

    def test_real_loopback_request_sends_metadata_not_source(self):
        project=self.root/'project';project.mkdir()
        names=['alpha.py','beta.py','gamma.py']
        for i,name in enumerate(names):
            (project/name).write_text(f'SECRET_SOURCE_{i}=123\n'+'x'*900,encoding='utf-8')
        captured=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                captured.append(body)
                raw=json.dumps(response()).encode()
                self.send_response(200)
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            row=json.loads(self.cfg.read_text())
            row['url']=f'http://127.0.0.1:{server.server_port}/v1/systemone'
            self.cfg.write_text(json.dumps(row))
            scorer=compute.JevComputeOrchestrator(project,self.cfg)
            with patch.dict(os.environ,{'TYPESAFE_API_KEY':'fixture-secret'},clear=False):
                result=scorer.run('Compare configured values',names,operation='factual')
            self.assertEqual(result['tier'],'T1')
            wire=json.dumps(captured[0])
            for name in names:self.assertNotIn(name,wire)
            self.assertNotIn('SECRET_SOURCE_',wire)
            self.assertIn('Compare configured values',wire)
            self.assertEqual(captured[0]['state']['corpus']['file_count'],3)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_invalid_compute_policy_rejected(self):
        row=json.loads(self.cfg.read_text());row['compute_policy']={'risk_high_max':.9}
        self.cfg.write_text(json.dumps(row))
        with self.assertRaises(compute.OrchestratorError):compute.load_config(self.cfg)


if __name__=='__main__':unittest.main()
