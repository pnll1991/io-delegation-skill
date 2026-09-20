import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/io-delegation/scripts'))
from context_mcp import ContextService, tools
from decision_router import RouterError


def routed(route, confidence=.9):
    probs={x:.02 for x in ('deterministic','targeted_read','bulk_read','principal')}
    probs[route]=.94
    return dict(status='ok',model='jev-test',route=route,model_route=route,
                confidence=confidence,probabilities=probs,delegation_useful=.7,
                reasoning_required=.2,min_confidence=.75,
                usage=dict(input_tokens=100,output_tokens=10),elapsed_ms=12)


def worker_reply(job,cfg,root,record):
    data=json.loads(job['messages'][1]['content'])
    record('worker_dispatched',adapter='test',model='fixture')
    usage=dict(input_tokens=30,output_tokens=8,cached_input_tokens=0,
               cache_write_input_tokens=0,reasoning_output_tokens=0)
    record('worker_response',usage=usage,usage_complete=True)
    findings=[dict(path=f['path'],symbol='X',evidence=f['content'].strip(),fact='Observed')
              for f in data['files']]
    return json.dumps(dict(status='ok',findings=findings,unknowns=[],
                           read_paths=[f['path'] for f in data['files']])),dict(usage=usage)


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'p';self.root.mkdir()
        self.audit=self.base/'audit';self.audit.mkdir()
        (self.root/'a.py').write_text('X=1\nSECRET_LOCAL=2\n')
        (self.root/'b.py').write_text('X=2\n')
        (self.root/'c.py').write_text('X=3\n')
        self.router=self.base/'router.json'
        self.router.write_text(json.dumps(dict(version=1,approved=True,provider='typesafe',
            model='jev-latest',api_key_env='TYPESAFE_API_KEY',timeout_seconds=5,
            min_confidence=.75,url='http://127.0.0.1:8765/mock')))
        self.orchestrator=self.base/'orchestrator.json'
        self.orchestrator.write_text(self.router.read_text())
        self.sel=[dict(path='a.py',select=dict(kind='lines',start=1,end=1))]

    def service(self, worker=None, orchestrator=None, host=None, model_preset=None,
                model_mode=None):
        return ContextService(self.root,self.audit,files=['a.py','b.py','c.py'],
                              config=worker,router_config=self.router,
                              orchestrator_config=orchestrator,host=host,
                              model_preset=model_preset,model_mode=model_mode)

    def call(self, service, **extra):
        args=dict(selections=self.sel,question='Review this evidence',operation='factual')
        args.update(extra)
        return json.loads(service.call('query',args)['content'][0]['text'])

    def test_query_advertised_with_router_without_worker(self):
        service=self.service()
        self.assertEqual([x['name'] for x in tools(service)],['search','extract','query'])

    def test_principal_returns_only_selected_evidence(self):
        service=self.service();service.query.router.run=lambda *a,**k:routed('principal')
        result=self.call(service,operation='security')
        self.assertEqual(result['route'],'principal')
        self.assertEqual(result['router']['route'],'principal')
        self.assertIn('X=1',result['evidence'][0]['content'])
        self.assertNotIn('SECRET_LOCAL',json.dumps(result))
        self.assertEqual(result['model_calls'],0)

    def test_bulk_without_worker_falls_back_to_fragments(self):
        service=self.service();service.query.router.run=lambda *a,**k:routed('bulk_read')
        result=self.call(service)
        self.assertEqual(result['route'],'targeted_read')
        self.assertEqual(result['recommended_route'],'bulk_read')
        self.assertEqual(result['reason'],'semantic_worker_unavailable')

    def test_bulk_with_worker_dispatches_selected_context(self):
        worker=self.base/'worker.json'
        worker.write_text(json.dumps(dict(approved=True,adapter='command',
            argv=[sys.executable,'-c','pass'],timeout_seconds=5,context_auto_dispatch=True)))
        service=self.service(worker);service.query.router.run=lambda *a,**k:routed('bulk_read')
        self.sel=[dict(path=x,select=dict(kind='lines',start=1,end=1)) for x in ('a.py','b.py','c.py')]
        with patch('io_delegate.invoke',side_effect=worker_reply) as invoke:
            result=self.call(service)
        self.assertEqual(result['route'],'bulk_read')
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['model_calls'],1)
        self.assertEqual(invoke.call_count,1)
        self.assertNotIn('SECRET_LOCAL',json.dumps(invoke.call_args.args[0]))

    def test_worker_config_does_not_auto_dispatch_without_experimental_opt_in(self):
        worker=self.base/'worker.json'
        worker.write_text(json.dumps(dict(approved=True,adapter='command',
            argv=[sys.executable,'-c','pass'],timeout_seconds=5)))
        service=self.service(worker);service.query.router.run=lambda *a,**k:routed('bulk_read')
        self.sel=[dict(path=x,select=dict(kind='lines',start=1,end=1)) for x in ('a.py','b.py','c.py')]
        with patch('io_delegate.invoke',side_effect=worker_reply) as invoke:
            result=self.call(service)
        self.assertEqual(result['route'],'targeted_read')
        self.assertEqual(result['recommended_route'],'bulk_read')
        self.assertEqual(result['reason'],'semantic_worker_opt_in_required')
        self.assertEqual(result['model_calls'],0)
        self.assertEqual(invoke.call_count,0)

    def test_orchestrator_dispatches_cheap_worker_without_legacy_auto_dispatch(self):
        worker=self.base/'worker-cheap.json'
        worker.write_text(json.dumps(dict(approved=True,adapter='command',
            argv=[sys.executable,'-c','pass'],timeout_seconds=5)))
        service=self.service(worker,self.orchestrator)
        service.query.router.run=lambda *a,**k:routed('bulk_read')
        service.query.orchestrator.run=lambda *a,**k:dict(
            status='ok',model='jev-test',tier='T1',decision='cheap_worker',
            reason='cheap_first_policy',scores=dict(cheap_model_sufficient=.95,risk_high=.05,
            uncertainty_high=.05,reasoning_required=.05,parallelism_useful=.2),
            usage=dict(input_tokens=40,output_tokens=8),elapsed_ms=7)
        self.sel=[dict(path=x,select=dict(kind='lines',start=1,end=1)) for x in ('a.py','b.py','c.py')]
        with patch('io_delegate.invoke',side_effect=worker_reply) as invoke:
            result=self.call(service)
        self.assertEqual(result['route'],'bulk_read')
        self.assertEqual(result['orchestration']['initial_tier'],'T1')
        self.assertFalse(result['orchestration']['escalated'])
        self.assertEqual(result['orchestration']['model_escalations'],0)
        self.assertEqual(len(result['orchestration']['attempts']),1)
        self.assertEqual(result['model_calls'],1)
        self.assertEqual(invoke.call_count,1)

    def test_invalid_cheap_result_escalates_to_principal(self):
        worker=self.base/'worker-escalate.json'
        worker.write_text(json.dumps(dict(approved=True,adapter='command',
            argv=[sys.executable,'-c','pass'],timeout_seconds=5)))
        service=self.service(worker,self.orchestrator)
        service.query.router.run=lambda *a,**k:routed('bulk_read')
        service.query.orchestrator.run=lambda *a,**k:dict(
            status='ok',model='jev-test',tier='T1',decision='cheap_worker',
            reason='cheap_first_policy',scores=dict(cheap_model_sufficient=.95,risk_high=.05,
            uncertainty_high=.05,reasoning_required=.05,parallelism_useful=.2),
            usage=dict(input_tokens=40,output_tokens=8),elapsed_ms=7)
        self.sel=[dict(path=x,select=dict(kind='lines',start=1,end=1)) for x in ('a.py','b.py','c.py')]
        def insufficient(job,cfg,root,record):
            record('worker_dispatched',adapter='test',model='cheap')
            usage=dict(input_tokens=30,output_tokens=8,cached_input_tokens=0,
                       cache_write_input_tokens=0,reasoning_output_tokens=0)
            record('worker_response',usage=usage,usage_complete=True)
            return json.dumps(dict(status='insufficient_context',findings=[],unknowns=['missing'],
                                   read_paths=[])),dict(usage=usage)
        with patch('io_delegate.invoke',side_effect=insufficient) as invoke:
            result=self.call(service)
        self.assertEqual(result['route'],'principal')
        self.assertEqual(result['reason'],'cheap_worker_escalation')
        self.assertTrue(result['orchestration']['escalated'])
        self.assertEqual(result['orchestration']['tier'],'T2')
        self.assertEqual(result['model_calls'],1)
        self.assertEqual(invoke.call_count,1)

    def test_compute_gate_can_skip_worker_and_go_directly_to_principal(self):
        worker=self.base/'worker-gated.json'
        worker.write_text(json.dumps(dict(approved=True,adapter='command',
            argv=[sys.executable,'-c','pass'],timeout_seconds=5)))
        service=self.service(worker,self.orchestrator)
        service.query.router.run=lambda *a,**k:routed('bulk_read')
        service.query.orchestrator.run=lambda *a,**k:dict(
            status='ok',model='jev-test',tier='T2',decision='principal',
            reason='compute_gate_rejected',scores=dict(cheap_model_sufficient=.2,risk_high=.7,
            uncertainty_high=.6,reasoning_required=.8,parallelism_useful=.1),
            usage=dict(input_tokens=40,output_tokens=8),elapsed_ms=7)
        with patch('io_delegate.invoke',side_effect=AssertionError('worker should not run')):
            result=self.call(service)
        self.assertEqual(result['route'],'principal')
        self.assertEqual(result['model_calls'],0)
        self.assertEqual(result['orchestration']['initial_tier'],'T2')

    def test_existing_codex_cli_without_model_policy_keeps_approved_model(self):
        worker=self.base/'legacy-codex-worker.json'
        worker.write_text(json.dumps(dict(approved=True,adapter='codex-cli',
            model='approved-existing-model',reasoning_effort='low',timeout_seconds=5)))
        service=self.service(worker,self.orchestrator,host='codex')
        service.query.router.run=lambda *a,**k:routed('bulk_read')
        service.query.orchestrator.run=lambda *a,**k:dict(
            status='ok',model='jev-test',tier='T1',decision='cheap_worker',
            reason='cheap_first_policy',scores=dict(cheap_model_sufficient=.95,risk_high=.05,
            uncertainty_high=.05,reasoning_required=.05,parallelism_useful=.1),
            usage=dict(input_tokens=20,output_tokens=4),elapsed_ms=3)
        self.sel=[dict(path=x,select=dict(kind='lines',start=1,end=1)) for x in ('a.py','b.py','c.py')]
        seen=[]
        def reply(job,cfg,root,record):
            seen.append((cfg['model'],cfg.get('reasoning_effort')))
            return worker_reply(job,cfg,root,record)
        with patch('io_delegate.invoke',side_effect=reply):
            result=self.call(service)
        self.assertEqual(result['route'],'bulk_read')
        self.assertEqual(seen,[('approved-existing-model','low')])
        self.assertNotIn('initial_profile',result['orchestration'])

    def host_worker(self, **policy):
        worker=self.base/'host-worker.json'
        row=dict(approved=True,adapter='host-cli',timeout_seconds=5)
        row['model_policy']={'mode':'auto', **policy}
        worker.write_text(json.dumps(row))
        return worker

    def scorer(self, **changes):
        row=dict(status='ok',model='jev-test',
                 scores=dict(cheap_model_sufficient=.92,risk_high=.04,
                             uncertainty_high=.08,reasoning_required=.08,
                             parallelism_useful=.1),
                 usage=dict(input_tokens=31,output_tokens=7),elapsed_ms=5)
        row.update(changes)
        return row

    def test_codex_balanced_policy_prefers_luna_high_for_easy_bulk(self):
        worker=self.host_worker()
        service=self.service(worker,self.orchestrator,host='codex')
        service.query.router.run=lambda *a,**k:routed('bulk_read')
        service.query.orchestrator.run=lambda *a,**k:self.scorer()
        self.sel=[dict(path=x,select=dict(kind='lines',start=1,end=1)) for x in ('a.py','b.py','c.py')]
        seen=[]
        def reply(job,cfg,root,record):
            seen.append(dict(cfg))
            return worker_reply(job,cfg,root,record)
        with patch('io_delegate.invoke',side_effect=reply):
            result=self.call(service)
        self.assertEqual(result['route'],'bulk_read')
        self.assertEqual(seen[0]['adapter'],'codex-cli')
        self.assertEqual(seen[0]['model'],'gpt-5.6-luna')
        self.assertEqual(seen[0]['reasoning_effort'],'high')
        self.assertEqual(result['orchestration']['initial_profile'],'luna-high')

    def test_codex_policy_can_jump_directly_to_astra_low(self):
        worker=self.host_worker()
        service=self.service(worker,self.orchestrator,host='codex')
        service.query.router.run=lambda *a,**k:routed('bulk_read')
        hard=self.scorer(scores=dict(cheap_model_sufficient=.02,risk_high=.70,
                                     uncertainty_high=.80,reasoning_required=.96,
                                     parallelism_useful=.4))
        service.query.orchestrator.run=lambda *a,**k:hard
        self.sel=[dict(path=x,select=dict(kind='lines',start=1,end=1)) for x in ('a.py','b.py','c.py')]
        seen=[]
        def reply(job,cfg,root,record):
            seen.append(dict(cfg));return worker_reply(job,cfg,root,record)
        with patch('io_delegate.invoke',side_effect=reply):
            result=self.call(service)
        self.assertEqual(seen[0]['model'],'gpt-6-astra')
        self.assertEqual(seen[0]['reasoning_effort'],'low')
        self.assertEqual(result['orchestration']['initial_profile'],'astra-low')

    def test_balanced_retries_luna_high_once_with_terra(self):
        worker=self.host_worker()
        service=self.service(worker,self.orchestrator,host='codex')
        service.query.router.run=lambda *a,**k:routed('bulk_read')
        service.query.orchestrator.run=lambda *a,**k:self.scorer()
        self.sel=[dict(path=x,select=dict(kind='lines',start=1,end=1)) for x in ('a.py','b.py','c.py')]
        seen=[]
        def reply(job,cfg,root,record):
            seen.append(cfg['model']+':'+cfg.get('reasoning_effort',''))
            raw,meta=worker_reply(job,cfg,root,record)
            if len(seen)==1:
                obj=json.loads(raw)
                obj['unknowns']=['Need stronger synthesis']
                raw=json.dumps(obj)
            return raw,meta
        with patch('io_delegate.invoke',side_effect=reply):
            result=self.call(service)
        self.assertEqual(seen,['gpt-5.6-luna:high','gpt-5.6-terra:medium'])
        self.assertEqual(result['route'],'bulk_read')
        self.assertEqual(result['orchestration']['final_profile'],'terra-medium')
        self.assertEqual(result['orchestration']['model_escalations'],1)
        self.assertEqual(len(result['orchestration']['attempts']),2)
        self.assertTrue(result['orchestration']['escalated'])

    def test_transport_failure_does_not_walk_expensive_ladder(self):
        worker=self.host_worker()
        service=self.service(worker,self.orchestrator,host='codex')
        service.query.router.run=lambda *a,**k:routed('bulk_read')
        service.query.orchestrator.run=lambda *a,**k:self.scorer()
        self.sel=[dict(path=x,select=dict(kind='lines',start=1,end=1)) for x in ('a.py','b.py','c.py')]
        from worker_runtime import TransportError
        calls=[]
        def fail(job,cfg,root,record):
            calls.append(cfg['model'])
            record('worker_dispatched',adapter='test',model=cfg['model'])
            raise TransportError('timeout')
        with patch('io_delegate.invoke',side_effect=fail):
            result=self.call(service)
        self.assertEqual(calls,['gpt-5.6-luna'])
        self.assertEqual(result['route'],'principal')
        self.assertEqual(result['reason'],'model_worker_escalation_to_principal')

    def test_cursor_policy_uses_cursor_profile_and_no_astra(self):
        worker=self.host_worker()
        service=self.service(worker,self.orchestrator,host='cursor')
        service.query.router.run=lambda *a,**k:routed('bulk_read')
        medium=self.scorer(scores=dict(cheap_model_sufficient=.38,risk_high=.12,
                                       uncertainty_high=.20,reasoning_required=.62,
                                       parallelism_useful=.1))
        service.query.orchestrator.run=lambda *a,**k:medium
        self.sel=[dict(path=x,select=dict(kind='lines',start=1,end=1)) for x in ('a.py','b.py','c.py')]
        seen=[]
        def reply(job,cfg,root,record):
            seen.append(dict(cfg));return worker_reply(job,cfg,root,record)
        with patch('io_delegate.invoke',side_effect=reply):
            result=self.call(service)
        self.assertEqual(seen[0]['adapter'],'cursor-cli')
        self.assertEqual(seen[0]['model'],'gpt-5.6-sol')
        self.assertNotEqual(seen[0]['model'],'gpt-6-astra')
        self.assertEqual(result['orchestration']['host'],'cursor')

    def test_suggest_mode_returns_recommendation_without_dynamic_dispatch(self):
        worker=self.host_worker(mode='suggest')
        service=self.service(worker,self.orchestrator,host='codex')
        service.query.router.run=lambda *a,**k:routed('bulk_read')
        service.query.orchestrator.run=lambda *a,**k:self.scorer()
        self.sel=[dict(path=x,select=dict(kind='lines',start=1,end=1)) for x in ('a.py','b.py','c.py')]
        with patch('io_delegate.invoke',side_effect=AssertionError('suggest must not dispatch host-cli')):
            result=self.call(service)
        self.assertEqual(result['route'],'principal')
        self.assertEqual(result['reason'],'model_suggestion_only')
        self.assertEqual(result['model_suggestion']['profile'],'luna-high')
        self.assertEqual(result['orchestration']['mode'],'suggest')
        self.assertEqual(result['model_calls'],0)

    def test_manual_mode_requires_explicit_model_for_host_cli(self):
        worker=self.host_worker(mode='manual')
        service=self.service(worker,self.orchestrator,host='codex')
        service.query.router.run=lambda *a,**k:routed('bulk_read')
        service.query.orchestrator.run=lambda *a,**k:self.scorer()
        self.sel=[dict(path=x,select=dict(kind='lines',start=1,end=1)) for x in ('a.py','b.py','c.py')]
        with patch('io_delegate.invoke',side_effect=AssertionError('manual host-cli must not dispatch')):
            result=self.call(service)
        self.assertEqual(result['route'],'principal')
        self.assertEqual(result['reason'],'manual_mode_requires_explicit_model')
        self.assertEqual(result['model_calls'],0)

    def test_manual_mode_preserves_explicit_codex_model(self):
        worker=self.base/'manual-codex.json'
        worker.write_text(json.dumps(dict(
            approved=True,adapter='codex-cli',model='my-approved-model',
            reasoning_effort='low',timeout_seconds=5,
            model_policy={'mode':'manual'})))
        service=self.service(worker,self.orchestrator,host='codex')
        service.query.router.run=lambda *a,**k:routed('bulk_read')
        service.query.orchestrator.run=lambda *a,**k:self.scorer()
        self.sel=[dict(path=x,select=dict(kind='lines',start=1,end=1)) for x in ('a.py','b.py','c.py')]
        seen=[]
        def reply(job,cfg,root,record):
            seen.append((cfg['model'],cfg.get('reasoning_effort')))
            return worker_reply(job,cfg,root,record)
        with patch('io_delegate.invoke',side_effect=reply):
            result=self.call(service)
        self.assertEqual(seen,[('my-approved-model','low')])
        self.assertEqual(result['route'],'bulk_read')
        self.assertEqual(result['orchestration']['mode'],'manual')

    def test_real_loopback_router_call_uses_metadata_and_routes_principal(self):
        captured=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));captured.append(body)
                answer=dict(model='jev-test',answers={
                    'route':dict(type='choice',choice='principal',confidence=.9,
                                 probabilities=dict(deterministic=.02,targeted_read=.06,bulk_read=.02,principal=.9)),
                    'delegation_useful':dict(type='noul',noul=.1),
                    'reasoning_required':dict(type='noul',noul=.92)},
                    usage=dict(input_tokens=101,output_tokens=11))
                raw=json.dumps(answer).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            cfg=json.loads(self.router.read_text());cfg['url']=f'http://127.0.0.1:{server.server_port}/mock';self.router.write_text(json.dumps(cfg))
            service=self.service()
            with patch.dict('os.environ',{'TYPESAFE_API_KEY':'fixture'},clear=False):
                result=self.call(service,operation='security')
            self.assertEqual(result['router']['route'],'principal');self.assertEqual(result['route'],'principal')
            payload=json.dumps(captured[0]);self.assertNotIn('SECRET_LOCAL',payload);self.assertNotIn('a.py',payload)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_bulk_route_can_select_more_than_principal_output_budget(self):
        for name, prefix in [('a.py','AAA'),('b.py','BBB'),('c.py','CCC')]:
            (self.root/name).write_text(prefix + ('x' * 4497) + '\n')
        worker=self.base/'worker-large.json'
        worker.write_text(json.dumps(dict(approved=True,adapter='command',
            argv=[sys.executable,'-c','pass'],timeout_seconds=5,context_auto_dispatch=True)))
        service=self.service(worker);service.query.router.run=lambda *a,**k:routed('bulk_read',.96)
        self.sel=[dict(path=x,select=dict(kind='lines',start=1,end=1)) for x in ('a.py','b.py','c.py')]
        def reply(job,cfg,root,record):
            data=json.loads(job['messages'][1]['content']);record('worker_dispatched',adapter='test',model='fixture')
            usage=dict(input_tokens=40,output_tokens=8,cached_input_tokens=0,cache_write_input_tokens=0,reasoning_output_tokens=0)
            record('worker_response',usage=usage,usage_complete=True)
            findings=[dict(path=f['path'],symbol='X',evidence=f['content'][:12],fact='Observed') for f in data['files']]
            return json.dumps(dict(status='ok',findings=findings,unknowns=[],read_paths=[f['path'] for f in data['files']])),dict(usage=usage)
        with patch('io_delegate.invoke',side_effect=reply):
            result=self.call(service)
        self.assertEqual(result['route'],'bulk_read')
        events=[json.loads(x) for x in (self.audit/'context-events.jsonl').read_text().splitlines()]
        completed=[x for x in events if x.get('event')=='operation_completed'][-1]
        self.assertGreater(completed['selected_bytes'],12_000)

    def test_router_error_falls_back_to_local_security_rule(self):
        service=self.service()
        def fail(*a,**k): raise RouterError('offline')
        service.query.router.run=fail
        result=self.call(service,operation='security')
        self.assertEqual(result['route'],'principal')
        self.assertEqual(result['router']['status'],'error')
        self.assertEqual(result['router']['fallback'],'local_rules')

    def test_query_telemetry_records_route_not_source_text(self):
        service=self.service();service.query.router.run=lambda *a,**k:routed('principal',.88)
        self.call(service,operation='security')
        log=(self.audit/'context-events.jsonl').read_text(encoding='utf-8')
        self.assertIn('router_confidence',log);self.assertIn('principal',log)
        self.assertNotIn('SECRET_LOCAL',log);self.assertNotIn('X=1',log)


if __name__=='__main__': unittest.main()
