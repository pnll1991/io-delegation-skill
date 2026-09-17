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
from context_engine import SemanticEngine
from io_delegate import DelegateError


def reply(job, cfg, root, record):
    data=json.loads(job['messages'][1]['content']);files=data['files']
    record('worker_dispatched',adapter='test-only',model='synthetic')
    usage=dict(input_tokens=25,output_tokens=10,cached_input_tokens=0,cache_write_input_tokens=0,reasoning_output_tokens=1)
    record('worker_response',usage=usage,usage_complete=True)
    findings=[dict(path=f['path'],symbol='VALUE',evidence=f['content'].strip(),fact='Value equals 3') for f in files]
    obj=dict(status='ok',findings=findings,unknowns=[],read_paths=[f['path'] for f in files])
    return json.dumps(obj),dict(usage=usage)


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'p';self.root.mkdir();self.audit=self.base/'audit';self.audit.mkdir()
        (self.root/'a.txt').write_text('VALUE=3\nnot selected\n')
        self.config=self.base/'worker.local.json'
        self.config.write_text(json.dumps(dict(approved=True,adapter='command',argv=[sys.executable,'-c','pass'],timeout_seconds=5)))
        self.service=ContextService(self.root,self.audit,files=['a.txt'],config=self.config)
        self.args=dict(selections=[dict(path='a.txt',select=dict(kind='lines',start=1,end=1))],question='What value?')

    def run_call(self):
        result,_metrics=self.service.engine.run(self.args)
        return result

    def test_semantic_engine_is_internal_and_requires_approval(self):
        self.assertEqual([t['name'] for t in tools(self.service)],['search','extract','query'])
        self.assertNotIn('semantic_query',self.service.tool_names())
        self.config.write_text('{"approved":false}')
        with self.assertRaises(DelegateError):ContextService(self.root,self.audit,files=['a.txt'],config=self.config)

    def test_selected_corpus_only_and_model_accounted(self):
        with patch('io_delegate.invoke',side_effect=reply) as invoke:r=self.run_call()
        self.assertEqual(r['status'],'ok');self.assertEqual(r['model_calls'],1)
        job=invoke.call_args.args[0];self.assertNotIn('not selected',json.dumps(job))
        journal=(self.audit/'.io-delegation/worker-events.jsonl').read_text()
        self.assertIn('worker_dispatched',journal);self.assertNotIn('VALUE=3',journal)

    def test_ambiguous_input_and_config_change_no_model(self):
        self.config.write_text(self.config.read_text()+' ')
        with patch('io_delegate.invoke',side_effect=AssertionError('model')):r=self.run_call()
        self.assertEqual(r['status'],'error')

    def test_missing_selection_abstains_without_dispatch(self):
        self.args['selections'][0]['select']=dict(kind='literal',needle='UNSEEN')
        with patch('io_delegate.invoke',side_effect=AssertionError('model')):r=self.run_call()
        self.assertEqual(r['status'],'insufficient_context');self.assertEqual(r['model_calls'],0)

    def test_evidence_in_unselected_region_is_rejected(self):
        def bad(job,cfg,root,record):
            raw,m=reply(job,cfg,root,record);obj=json.loads(raw);obj['findings'][0]['evidence']='not selected';return json.dumps(obj),m
        with patch('io_delegate.invoke',side_effect=bad):r=self.run_call()
        self.assertEqual(r['status'],'error');self.assertEqual(r['model_calls'],1)
        self.assertIn('25',(self.audit/'.io-delegation/worker-events.jsonl').read_text())

    def test_source_change_rejects_after_call(self):
        def changed(job,cfg,root,record):
            output=reply(job,cfg,root,record);(self.root/'a.txt').write_text('VALUE=4');return output
        with patch('io_delegate.invoke',side_effect=changed):r=self.run_call()
        self.assertEqual(r['status'],'error')

    def test_grouped_questions_one_inference(self):
        self.args['questions']=['What value?','What symbol?'];del self.args['question']
        with patch('io_delegate.invoke',side_effect=reply) as invoke:r=self.run_call()
        self.assertEqual(invoke.call_count,1);self.assertEqual(r['status'],'ok')
        self.assertIn('Q2:',invoke.call_args.args[0]['messages'][1]['content'])

    def test_minimal_chat_transport_real_local_http_no_agent(self):
        captured=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));captured.append(body)
                job=dict(messages=body['messages']);output,metrics=reply(job,{},None,lambda *a,**k:None)
                obj=dict(choices=[dict(finish_reason='stop',message=dict(content=output))],usage=dict(prompt_tokens=25,completion_tokens=10))
                raw=json.dumps(obj).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            self.config.write_text(json.dumps(dict(approved=True,adapter='chat-completions',url=f'http://127.0.0.1:{server.server_port}/v1/chat/completions',model='test-fixture',reader_max_tokens=200,timeout_seconds=5)))
            self.service=ContextService(self.root,self.audit,files=['a.txt'],config=self.config)
            with patch('io_delegate.invoke_codex',side_effect=AssertionError('agent launched')):r=self.run_call()
            self.assertEqual(r['status'],'ok');self.assertEqual(captured[0]['max_tokens'],200)
            self.assertEqual(set(captured[0]),{'model','messages','stream','max_tokens','temperature'})
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
