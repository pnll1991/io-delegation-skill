"""Offline subprocess tests using a fake CLI; never invoke authenticated Codex."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/io-delegation/scripts'));sys.path.insert(0,str(ROOT/'benchmarks/codex-ab'))
import io_delegate as d
import worker_runtime as rt
import codex_io_ab as ab
import setup_worker as sw

FAKE = r'''#!/usr/bin/env python3
import json,sys,pathlib,subprocess,os
args=sys.argv[1:]
if '--version' in args:
 print('codex-cli TEST-STUB');sys.exit(0)
if '--help' in args:
 print('--json --sandbox --skip-git-repo-check --ephemeral --ignore-user-config --output-last-message --output-schema');sys.exit(0)
if args[:2]==['login','status']:
 print('TEST AUTH ONLY');sys.exit(0)
prompt=sys.stdin.read()
root=pathlib.Path(args[args.index('-C')+1])
usage=dict(input_tokens=100,cached_input_tokens=40,cache_write_input_tokens=0,output_tokens=10,reasoning_output_tokens=2)
print(json.dumps(dict(type='thread.started',thread_id='test-thread')))
if '--sandbox' in args:
 j=json.loads(prompt[prompt.index('{"task"'):]);f=j['files'][0];v=f['content'].strip()
 result=dict(status='ok',findings=[dict(path=f['path'],symbol='probe',evidence=v,fact=v)],unknowns=[],read_paths=[f['path']])
 output=pathlib.Path(args[args.index('--output-last-message')+1]);output.write_text(json.dumps(result))
else:
 if '.io-delegation/worker.local.json' in prompt and not os.environ.get('TEST_IGNORE_WORKER'):
  cmd=[sys.executable,str(root/'.agents/skills/io-delegation/scripts/io_delegate.py'),'bulk-read','--root',str(root),'--config',str(root/'.io-delegation/worker.local.json'),'--paths','sample.py','--question','What is LIMIT?']
  cp=subprocess.run(cmd,cwd=root,capture_output=True,text=True)
  print(json.dumps(dict(type='item.completed',item=dict(id='1',type='command_execution',aggregated_output=cp.stderr,exit_code=cp.returncode))))
  if cp.returncode:sys.exit(2)
 (root/'benchmark-output').mkdir(exist_ok=True)
 (root/'benchmark-output/result.json').write_text('{"answer":7}')
print(json.dumps(dict(type='turn.completed',usage=usage)))
'''

@unittest.skipIf(os.name=='nt','Fake executable is POSIX; Windows transport is tested with argv-list command adapters')
class CLIIntegration(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.bin=self.root/'bin';self.bin.mkdir();self.exe=self.bin/'codex';self.exe.write_text(FAKE);self.exe.chmod(0o755)
  self.env=mock.patch.dict(os.environ,{'PATH':str(self.bin)+os.pathsep+os.environ.get('PATH','')});self.env.start();self.addCleanup(self.env.stop)
  self.cfg=self.root/'worker.local.json';ab.write_json(self.cfg,dict(approved=True,adapter='codex-cli',executable=str(self.exe),model='test-model',reasoning_effort='low',timeout_seconds=15,max_calls_per_workspace=2))
 def test_cli_smoke_end_to_end(self):
  dest=self.root/'smoke';dest.mkdir();receipt=sw.smoke(self.cfg,dest)
  self.assertTrue(receipt['ok']);self.assertEqual(receipt['stats']['worker_calls'],1)
 def test_cli_command_is_safe_and_isolated(self):
  job=d.build_job('bulk-read','What is LIMIT?',[dict(path='sample.py',sha256='x',content='LIMIT = 7')])
  captured=[];original=rt.run_process
  def spy(argv,**kw):
   captured.append((argv,kw));return original(argv,**kw)
  with mock.patch.object(rt,'run_process',side_effect=spy): output,metrics=rt.invoke_codex(job,ab.read_json(self.cfg))
  command,options=captured[-1]
  self.assertIn('read-only',command);self.assertIn('features.shell_tool=false',command)
  self.assertIn('--ignore-user-config',command);self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',command)
  self.assertNotIn('LIMIT = 7',' '.join(command));self.assertIn(b'LIMIT = 7',options['payload'])
  self.assertFalse(Path(options['cwd']).exists());self.assertEqual(metrics['usage']['input_tokens'],100)
 def test_benchmark_worker_really_invoked_and_accounted(self):
  repo=self.root/'target';repo.mkdir();(repo/'sample.py').write_text('LIMIT = 7\n')
  ab.git(repo,'init');ab.git(repo,'add','sample.py');ab.git(repo,'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','base')
  smoke_dir=self.root/'smoke';smoke_dir.mkdir();self.assertTrue(sw.smoke(self.cfg,smoke_dir)['ok'])
  checker=self.root/'check.py';checker.write_text("import json,pathlib;assert json.loads(pathlib.Path('benchmark-output/result.json').read_text())=={'answer':7}")
  m=dict(repo=str(repo),repetitions=1,strategies=[dict(name='baseline',model='test-model'),dict(name='skill-worker-required',model='test-model',worker_mode='required',worker_transport='shell',worker_config=str(self.cfg),worker_receipt=str(smoke_dir/'worker-smoke.json'),setup_commands=[['{python}',str(ROOT/'install.py'),'--agent','codex','--project','{worktree}']])],tasks=[dict(id='fixture',user_prompt='Create benchmark-output/result.json with answer=7.',acceptance=[[sys.executable,str(checker)]],regression=[[sys.executable,str(checker)]],capture_paths=['benchmark-output/result.json'])])
  manifest=self.root/'manifest.json';ab.write_json(manifest,m)
  with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(ab.main([str(manifest),'--output',str(self.root/'results')]),0)
  rows=ab.read_json(self.root/'results/runs.json');r=next(x for x in rows if 'worker' in x['strategy'])
  self.assertTrue(r['valid_success']);self.assertTrue(r['comparison_valid']);self.assertEqual(r['worker_calls'],1)
  self.assertEqual(r['raw_tokens'],110);self.assertEqual(r['system_raw_tokens'],220)
  self.assertIsNone(r['principal_cost_usd']);self.assertEqual(ab.git(repo,'status','--porcelain').strip(),'')
  self.assertEqual(len(list((self.root/'results/runs').glob('*/captured-0-result.json'))),2)
  with self.assertRaises(ValueError):ab.main([str(manifest),'--output',str(self.root/'results')])
 def test_stale_receipt_stops_without_model(self):
  repo=self.root/'target';repo.mkdir();ab.git(repo,'init');ab.git(repo,'-c','user.name=Test','-c','user.email=test@example.invalid','commit','--allow-empty','-m','base')
  receipt=self.root/'receipt.json';ab.write_json(receipt,dict(ok=True,config_sha256='stale',engine_sha256='stale'))
  with self.assertRaisesRegex(ValueError,'stale'):
   ab.prepare(dict(repo=str(repo)),self.root/'manifest.json',[dict(name='worker',worker_mode='required',worker_transport='mcp',worker_config=str(self.cfg),worker_receipt=str(receipt),worker_boundary_receipt=str(receipt))],[dict(id='x',user_prompt='x',acceptance=['true'],regression=['true'])])

if __name__=='__main__':unittest.main()
