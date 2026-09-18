from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile
import unittest

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

    def test_policy_selects_cheap_worker_only_when_all_gates_pass(self):
        scores=compute.parse_response(response(),compute.load_config(self.cfg))['scores']
        self.assertEqual(compute.decide(scores,'factual',True)['tier'],'T1')
        for key,value in [('cheap_model_sufficient',.4),('risk_high',.8),
                          ('uncertainty_high',.8),('reasoning_required',.8)]:
            bad=dict(scores);bad[key]=value
            self.assertEqual(compute.decide(bad,'factual',True)['tier'],'T2')

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

    def test_invalid_compute_policy_rejected(self):
        row=json.loads(self.cfg.read_text());row['compute_policy']={'risk_high_max':.9}
        self.cfg.write_text(json.dumps(row))
        with self.assertRaises(compute.OrchestratorError):compute.load_config(self.cfg)


if __name__=='__main__':unittest.main()
