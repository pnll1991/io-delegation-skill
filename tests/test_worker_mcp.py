"""Offline regression tests. Real subprocesses; synthetic worker, NOT live model savings."""
from __future__ import annotations
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT/'skills/io-delegation/scripts'
sys.path.insert(0,str(SCRIPTS)); sys.path.insert(0,str(ROOT/'benchmarks/codex-ab'))
import worker_mcp as bridge
import codex_io_ab as bench
import verify_worker_mcp as verify
import io_delegate as delegate
from worker_runtime import ProcessResult

# Real child process implementing only the approved command-adapter contract.
STUB = '''import json,sys,time
job=json.load(sys.stdin); data=json.loads(job['messages'][1]['content'])
if 'TIMEOUT' in data['task']: time.sleep(2)
findings=[dict(path=f['path'],symbol='WORKER_CHECK',evidence=f['content'].strip(),fact=f['content'].strip()) for f in data['files']]
if 'BAD_EVIDENCE' in data['task']: findings[0]['evidence']='NOT IN SOURCE'
result=dict(status='ok',findings=findings,unknowns=[],read_paths=[f['path'] for f in data['files']])
print(json.dumps(dict(output=json.dumps(result),usage=dict(input_tokens=25,output_tokens=10,cached_input_tokens=0,cache_write_input_tokens=0,reasoning_output_tokens=2))))
'''


class WorkerMCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base/'project'; self.root.mkdir()
        self.audit = self.base/'audit'; self.audit.mkdir()
        (self.root/'probe.txt').write_text('WORKER_CHECK = "secret-synthetic-marker"\n',encoding='utf-8')
        self.stub = self.base/'worker stub.py'; self.stub.write_text(STUB)
        self.config = self.base/'worker.local.json'
        self.cfg = dict(approved=True,adapter='command',argv=[sys.executable,str(self.stub)],
                        timeout_seconds=5,max_calls_per_workspace=4)
        self.config.write_text(json.dumps(self.cfg))
        self.service = bridge.WorkerService(self.root,self.config,self.audit,files=['probe.txt'])

    def events(self):
        return bench.load_events(self.audit/'.io-delegation/worker-events.jsonl')[0]

    def request(self, **overrides):
        return dict(paths=['probe.txt'],question='What is WORKER_CHECK?',**overrides)

    def test_success_real_adapter_and_literal_evidence(self):
        response = self.service.bulk_read(self.request())
        self.assertFalse(response['isError'])
        result = json.loads(response['content'][0]['text'])
        self.assertEqual(result['read_paths'],['probe.txt'])
        self.assertIn('secret-synthetic-marker',result['findings'][0]['evidence'])
        stats=bench.worker_usage(self.events())
        self.assertEqual((stats['worker_attempts'],stats['worker_calls'],stats['worker_accepted']),(1,1,1))
        self.assertEqual(stats['worker_input_tokens'],25)
        self.assertEqual(stats['worker_output_tokens'],10)
        self.assertTrue(stats['worker_accounting_complete'])

    def test_metadata_has_no_source_or_question(self):
        self.service.bulk_read(self.request())
        raw=json.dumps(self.events())
        self.assertNotIn('secret-synthetic-marker',raw)
        self.assertNotIn('What is',raw)
        self.assertNotIn('probe.txt',raw)

    def test_bad_evidence_rejected_usage_retained(self):
        response=self.service.bulk_read(dict(paths=['probe.txt'],question='BAD_EVIDENCE'))
        self.assertTrue(response['isError'])
        stats=bench.worker_usage(self.events())
        self.assertEqual(stats['worker_accepted'],0)
        self.assertEqual(stats['worker_calls'],1)
        self.assertEqual(stats['worker_input_tokens'],25)
        self.assertTrue(stats['worker_accounting_complete'])

    def test_22_paths_rejected_before_model(self):
        response=self.service.bulk_read(dict(paths=['probe.txt']*22,question='test'))
        self.assertTrue(response['isError'])
        self.assertIn('1..12',response['content'][0]['text'])
        stats=bench.worker_usage(self.events())
        self.assertEqual(stats['worker_attempts'],1)
        self.assertEqual(stats['worker_calls'],0)

    def test_duplicate_paths_rejected(self):
        self.assertTrue(self.service.bulk_read(dict(paths=['probe.txt']*2,question='test'))['isError'])
        self.assertEqual(bench.worker_usage(self.events())['worker_calls'],0)

    def test_all_paths_outside_allowlist_rejected(self):
        (self.root/'other.txt').write_text('not allowed')
        self.assertTrue(self.service.bulk_read(dict(paths=['other.txt'],question='test'))['isError'])
        self.assertEqual(bench.worker_usage(self.events())['worker_calls'],0)

    def test_traversal_absolute_drive_ads_and_arguments_rejected(self):
        for value in ['../outside.txt','C:/secret.txt','probe.txt:stream','/etc/passwd','a\\b','--config','./probe.txt','a//b']:
            with self.subTest(path=value):
                with self.assertRaises(ValueError): bridge.relative_name(value)

    def test_extra_model_or_root_cannot_change_provider(self):
        for key in ('config','root','model','command','argv'):
            args=self.request(); args[key]='arbitrary'
            self.assertTrue(self.service.bulk_read(args)['isError'])
        self.assertEqual(bench.worker_usage(self.events())['worker_calls'],0)

    def test_unapproved_configuration_rejected_at_startup(self):
        self.cfg['approved']=False; self.config.write_text(json.dumps(self.cfg))
        with self.assertRaises(delegate.DelegateError):
            bridge.WorkerService(self.root,self.config,self.audit,files=['probe.txt'])

    def test_configuration_change_after_startup_rejected(self):
        self.config.write_text(json.dumps(dict(self.cfg,model='changed')))
        self.assertTrue(self.service.bulk_read(self.request())['isError'])
        self.assertEqual(bench.worker_usage(self.events())['worker_calls'],0)

    def test_audit_and_config_cannot_be_inside_agent_workspace(self):
        for config,audit in ((self.config,self.root), (self.root/'probe.txt',self.audit)):
            with self.assertRaises(ValueError):
                bridge.WorkerService(self.root,config,audit,files=['probe.txt'])

    def test_explicit_allowlist_required(self):
        with self.assertRaises(ValueError): bridge.WorkerService(self.root,self.config,self.audit)

    def test_prefix_boundary_does_not_allow_neighbor(self):
        (self.root/'src').mkdir();(self.root/'src2').mkdir()
        (self.root/'src2/other.py').write_text('oops')
        service=bridge.WorkerService(self.root,self.config,self.audit,prefixes=['src'])
        self.assertTrue(service.bulk_read(dict(paths=['src2/other.py'],question='test'))['isError'])

    def test_symlink_refused_without_worker_dispatch(self):
        link=self.root/'link.txt'
        try: link.symlink_to(self.root/'probe.txt')
        except (OSError,NotImplementedError): self.skipTest('Symlink creation unavailable')
        service=bridge.WorkerService(self.root,self.config,self.audit,files=['link.txt'])
        self.assertTrue(service.bulk_read(dict(paths=['link.txt'],question='test'))['isError'])
        self.assertEqual(bench.worker_usage(self.events())['worker_calls'],0)

    def test_calls_are_capped_and_restarts_do_not_reset_usage(self):
        self.cfg['max_calls_per_workspace']=1;self.config.write_text(json.dumps(self.cfg))
        service=bridge.WorkerService(self.root,self.config,self.audit,files=['probe.txt'])
        self.assertFalse(service.bulk_read(self.request())['isError'])
        service=bridge.WorkerService(self.root,self.config,self.audit,files=['probe.txt'])
        self.assertTrue(service.bulk_read(self.request())['isError'])
        self.assertEqual(bench.worker_usage(self.events())['worker_calls'],1)

    def test_timeout_is_not_zero_cost(self):
        self.cfg['timeout_seconds']=0.15;self.config.write_text(json.dumps(self.cfg))
        service=bridge.WorkerService(self.root,self.config,self.audit,files=['probe.txt'])
        self.assertTrue(service.bulk_read(dict(paths=['probe.txt'],question='TIMEOUT'))['isError'])
        stats=bench.worker_usage(self.events())
        self.assertEqual(stats['worker_calls'],1)
        self.assertEqual(stats['worker_usage_unknown_calls'],1)
        self.assertFalse(stats['worker_accounting_complete'])

    def test_protocol_handshake_is_real_process_and_calls_no_model(self):
        args=bridge.codex_mcp_arguments(self.root,self.config,self.audit,files=['probe.txt'])
        ok,cp=verify.handshake(args)
        self.assertTrue(ok,cp.stderr)
        self.assertEqual(self.events(),[])
        for line in cp.stdout.splitlines(): self.assertEqual(json.loads(line)['jsonrpc'],'2.0')

    def test_stdio_pipeline_real_server_and_real_command_adapter(self):
        args=bridge.codex_mcp_arguments(self.root,self.config,self.audit,files=['probe.txt'])
        messages=[dict(jsonrpc='2.0',id=1,method='initialize',params={'protocolVersion':'2025-06-18'}),
                  dict(jsonrpc='2.0',method='notifications/initialized'),
                  dict(jsonrpc='2.0',id=2,method='tools/call',params={'name':'bulk_read','arguments':self.request()})]
        cp=subprocess.run(verify.server_command(args),input=b''.join(bridge.json_bytes(m)+b'\n' for m in messages),capture_output=True,timeout=15)
        self.assertEqual(cp.returncode,0,cp.stderr)
        replies=[json.loads(l) for l in cp.stdout.splitlines()]
        self.assertEqual(len(replies),2)
        self.assertFalse(replies[-1]['result']['isError'])
        self.assertEqual(bench.worker_usage(self.events())['worker_accepted'],1)

    def test_request_size_bounded(self):
        out=io.BytesIO()
        self.assertEqual(bridge.serve(self.service,io.BytesIO(b'x'*(bridge.MAX_RPC_BYTES+1)),out),2)
        self.assertEqual(self.events(),[])

    def test_unknown_methods_and_bad_json_no_dispatch(self):
        messages=[b'bad json\n',bridge.json_bytes(dict(jsonrpc='2.0',id=1,method='initialize'))+b'\n',
                  bridge.json_bytes(dict(jsonrpc='2.0',id=2,method='tools/call',params={'name':'run_shell'}))+b'\n']
        out=io.BytesIO(); bridge.serve(self.service,io.BytesIO(b''.join(messages)),out)
        replies=[json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual(replies[-1]['error']['code'],-32601)
        self.assertEqual(self.events(),[])

    def test_cli_options_are_scoped_and_do_not_lower_sandbox(self):
        args=bridge.codex_mcp_arguments(self.root,self.config,self.audit,files=['probe.txt'])
        values=' '.join(args)
        self.assertNotIn('danger-full-access',values)
        self.assertNotIn('bypass',values)
        self.assertNotIn('network_access',values)
        settings=dict(x.split('=',1) for x in args[1::2])
        self.assertTrue(json.loads(settings['mcp_servers.io_delegation.required']))
        self.assertEqual(json.loads(settings['mcp_servers.io_delegation.enabled_tools']),['bulk_read'])
        self.assertEqual(verify.server_command(args)[0],sys.executable)
        self.assertIn('-I',verify.server_command(args))

    def test_windows_paths_round_trip_through_toml(self):
        try: import tomllib
        except ImportError: self.skipTest('tomllib requires Python 3.11')
        windows=r'C:\Users\Some User\Python\python.exe'
        with patch.object(bridge.sys,'executable',windows):
            args=bridge.codex_mcp_arguments(self.root,self.config,self.audit,files=['probe.txt'])
        parsed=tomllib.loads('\n'.join(args[1::2]))
        self.assertEqual(parsed['mcp_servers']['io_delegation']['command'],windows)

    def test_boundary_receipt_is_mandatory_not_a_direct_smoke(self):
        with self.assertRaisesRegex(ValueError,'boundary test'):
            bench.check_boundary_receipt({},self.config)
        path=self.base/'receipt.json'; bench.write_json(path,{'ok':True})
        with self.assertRaises(ValueError):
            bench.check_boundary_receipt({'worker_boundary_receipt':str(path)},self.config)

    def test_good_and_stale_boundary_receipts(self):
        data=dict(ok=True,transport='mcp',config_sha256=bench.config_hash(self.config),
                  engine_sha256=bench.engine_hash(),bridge_sha256=bench.bridge_hash())
        path=self.base/'receipt.json';bench.write_json(path,data)
        bench.check_boundary_receipt({'worker_boundary_receipt':str(path)},self.config)
        data['bridge_sha256']='outdated';bench.write_json(path,data)
        with self.assertRaises(ValueError): bench.check_boundary_receipt({'worker_boundary_receipt':str(path)},self.config)

    def test_known_failure_classified_without_invented_worker_usage(self):
        item=dict(id='x',type='command_execution',command='python.exe io_delegate.py bulk-read',
                  aggregated_output='Test-Path : Access is denied\nCommandNotFoundException')
        path=self.base/'log.jsonl';path.write_text(json.dumps({'type':'item.completed','item':item})+'\n')
        self.assertEqual(bench.transport_diagnostics(path)['worker_launch_errors'],1)
        self.assertEqual(bench.usage_from_jsonl(path)['worker_calls'],0)

    def test_boundary_zero_worker_never_passes(self):
        (self.audit/'codex.jsonl').write_text(json.dumps({'type':'turn.completed','usage':{'input_tokens':30,'output_tokens':5}})+'\n')
        receipt=verify.probe_result(ProcessResult(0,b'synthetic nonce',b''),self.audit,self.config,'nonce')
        self.assertFalse(receipt['ok'])

    def test_new_manifest_does_not_mutate_or_repeat_original(self):
        source=dict(repetitions=3,strategies=[{'name':'baseline','worker_mode':'disabled'},
                    {'name':'skill-worker-required','worker_mode':'required'}])
        path=verify.next_manifest(source,self.audit); out=json.loads(path.read_text())
        self.assertEqual(source['repetitions'],3)
        self.assertEqual(out['repetitions'],1)
        self.assertEqual(out['strategies'][1]['worker_transport'],'mcp')


    def make_benchmark_fixture(self, skip_worker=False, repetitions=1):
        # POSIX executable shim models Codex events, NOT model behaviour or billing.
        if os.name == 'nt': self.skipTest('POSIX fake Codex launcher; MCP subprocess tests remain portable')
        if not __import__('shutil').which('git'): self.skipTest('Git unavailable')
        bin_dir=self.base/'bin';bin_dir.mkdir()
        executable=bin_dir/'codex'
        source = r"""import json,sys,subprocess
from pathlib import Path
args=sys.argv[1:]
if '--version' in args: print('codex synthetic');raise SystemExit(0)
root=Path(args[args.index('-C')+1]);sys.stdin.read()
settings={}
for i,arg in enumerate(args[:-1]):
    if arg=='-c':
        key,value=args[i+1].split('=',1)
        if key.startswith('mcp_servers.io_delegation.'):settings[key]=json.loads(value)
print(json.dumps({'type':'thread.started','thread_id':'synthetic'}))
if settings and not SKIP_WORKER:
    command=[settings['mcp_servers.io_delegation.command'],*settings['mcp_servers.io_delegation.args']]
    messages=[dict(jsonrpc='2.0',id=1,method='initialize',params={'protocolVersion':'2025-06-18'}),
              dict(jsonrpc='2.0',method='notifications/initialized'),
              dict(jsonrpc='2.0',id=2,method='tools/call',params={'name':'bulk_read','arguments':{'paths':['probe.txt'],'question':'What is WORKER_CHECK?'}})]
    result=subprocess.run(command,input=('\n'.join(json.dumps(m) for m in messages)+'\n').encode(),capture_output=True,timeout=20)
    assert result.returncode==0,result.stderr
    reply=json.loads(result.stdout.splitlines()[-1])['result'];assert not reply['isError'],reply
    print(json.dumps({'type':'item.completed','item':{'id':'call1','type':'mcp_tool_call','server':'io_delegation','tool':'bulk_read','result':reply,'status':'completed'}}))
(root/'answer.json').write_text('[1]',encoding='utf-8')
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':20,'output_tokens':10,'cached_input_tokens':0,'cache_write_input_tokens':0,'reasoning_output_tokens':1}}))
""".replace('SKIP_WORKER',str(skip_worker))
        executable.write_text('#!'+sys.executable+'\n'+source);executable.chmod(0o700)
        for args in (['init'],['add','probe.txt'],['-c','user.name=Test','-c','user.email=test@example.invalid','commit','--no-gpg-sign','-m','fixture']):
            cp=subprocess.run(['git',*args],cwd=self.root,capture_output=True)
            self.assertEqual(cp.returncode,0,cp.stderr)
        direct=self.base/'direct.json';boundary=self.base/'boundary.json'
        receipt=dict(ok=True,transport='mcp',config_sha256=bench.config_hash(self.config),
                     engine_sha256=bench.engine_hash(),bridge_sha256=bench.bridge_hash())
        bench.write_json(direct,receipt);bench.write_json(boundary,receipt)
        worker=dict(name='skill-worker-required',worker_mode='required',worker_transport='mcp',
            worker_config=str(self.config),worker_receipt=str(direct),worker_boundary_receipt=str(boundary),
            worker_allow_files=['probe.txt'],model='synthetic')
        task=dict(id='test-task',user_prompt='Extract a fact from probe.txt then write answer.json.',
            acceptance=[[sys.executable,'-c',"import json;assert json.load(open('answer.json'))==[1]"]],
            regression=[[sys.executable,'-c',"import subprocess;assert subprocess.check_output(['git','diff','--name-only'])==b''"]],
            capture_paths=['answer.json'])
        manifest=dict(repo=str(self.root),repetitions=repetitions,strategies=[dict(name='baseline',model='synthetic'),worker],tasks=[task])
        manifest_path=self.base/'manifest.json';bench.write_json(manifest_path,manifest)
        return manifest_path,bin_dir

    def test_full_harness_mcp_and_worker_accounting_end_to_end(self):
        manifest,bin_dir=self.make_benchmark_fixture()
        out=self.base/'ab'
        with patch.dict(os.environ,{'PATH':str(bin_dir)+os.pathsep+os.environ.get('PATH','')}),contextlib.redirect_stdout(io.StringIO()):
            rc=bench.main([str(manifest),'--output',str(out)])
        self.assertEqual(rc,0)
        results=json.loads((out/'runs.json').read_text())
        baseline=next(r for r in results if r['strategy']=='baseline')
        worker=next(r for r in results if r['strategy']=='skill-worker-required')
        self.assertTrue(baseline['valid_success']);self.assertEqual(baseline['system_raw_tokens'],30)
        self.assertTrue(worker['valid_success']);self.assertTrue(worker['comparison_valid'])
        self.assertEqual(worker['raw_tokens'],30);self.assertEqual(worker['system_raw_tokens'],65)
        self.assertEqual((worker['worker_attempts'],worker['worker_calls'],worker['worker_accepted']),(1,1,1))
        self.assertEqual(worker['worker_mcp_tool_calls'],1)
        self.assertEqual(len(list((out/'runs').glob('*/captured-0-answer.json'))),2)
        self.assertFalse((self.root/'answer.json').exists())

    def test_required_contract_failure_stops_remaining_runs(self):
        manifest,bin_dir=self.make_benchmark_fixture(skip_worker=True,repetitions=3)
        out=self.base/'ab'
        with patch.dict(os.environ,{'PATH':str(bin_dir)+os.pathsep+os.environ.get('PATH','')}),contextlib.redirect_stdout(io.StringIO()):
            rc=bench.main([str(manifest),'--strategy','skill-worker-required','--output',str(out)])
        self.assertEqual(rc,2)
        results=json.loads((out/'runs.json').read_text())
        self.assertEqual(len(results),1)
        self.assertTrue(results[0]['task_success'])
        self.assertFalse(results[0]['valid_success'])
        self.assertEqual(results[0]['failure_reason'],'worker_required_not_accepted')

    def test_old_direct_receipt_blocks_before_principal_execution(self):
        manifest,bin_dir=self.make_benchmark_fixture()
        data=json.loads(manifest.read_text());del data['strategies'][1]['worker_boundary_receipt'];bench.write_json(manifest,data)
        out=self.base/'ab'
        with patch.dict(os.environ,{'PATH':str(bin_dir)+os.pathsep+os.environ.get('PATH','')}):
            with self.assertRaisesRegex(ValueError,'boundary test'):
                bench.main([str(manifest),'--output',str(out)])
        self.assertFalse(out.exists())


if __name__=='__main__': unittest.main()
