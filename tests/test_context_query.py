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
        self.sel=[dict(path='a.py',select=dict(kind='lines',start=1,end=1))]

    def service(self, worker=None):
        return ContextService(self.root,self.audit,files=['a.py','b.py','c.py'],
                              config=worker,router_config=self.router)

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
            argv=[sys.executable,'-c','pass'],timeout_seconds=5)))
        service=self.service(worker);service.query.router.run=lambda *a,**k:routed('bulk_read')
        self.sel=[dict(path=x,select=dict(kind='lines',start=1,end=1)) for x in ('a.py','b.py','c.py')]
        with patch('io_delegate.invoke',side_effect=worker_reply) as invoke:
            result=self.call(service)
        self.assertEqual(result['route'],'bulk_read')
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['model_calls'],1)
        self.assertEqual(invoke.call_count,1)
        self.assertNotIn('SECRET_LOCAL',json.dumps(invoke.call_args.args[0]))

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
            argv=[sys.executable,'-c','pass'],timeout_seconds=5)))
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
