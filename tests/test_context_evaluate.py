import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/io-delegation/scripts'))
sys.path.insert(0,str(ROOT/'benchmarks/context'))
import evaluate
import fixtures
import verify_context
import migrate_manifest
from context_ops import project
from worker_runtime import ProcessResult


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.base=Path(self.temp.name)
        self.path=fixtures.create(self.base/'suite','synthetic-test-only')
        self.manifest=json.loads(self.path.read_text());self.manifest['codex_command']=['synthetic-codex']

    def synthetic_executor(self,argv,**kw):
        # Integration driver only, NOT model-generated output or measured tokens.
        root=Path(kw['cwd']);prompt=kw['payload'].decode();case=next(c for c in fixtures.EXPECTED if f'benchmark-output/{c}.json' in prompt)
        result=fixtures.EXPECTED[case]
        settings={}
        for i,x in enumerate(argv):
            if x=='-c' and argv[i+1].startswith('mcp_servers.io_context.'):
                key,val=argv[i+1].split('=',1);settings[key]=json.loads(val)
        events=[]
        if settings:
            cmd=[settings['mcp_servers.io_context.command'],*settings['mcp_servers.io_context.args']]
            semantic='--config' in settings['mcp_servers.io_context.args']
            tool='query' if semantic else 'extract'
            request=(dict(
                selections=[
                    dict(path='data/rules.txt',select=dict(kind='lines',start=1,end=1)),
                    dict(path='data/classification.txt',select=dict(kind='lines',start=1,end=1)),
                    dict(path='data/page.html',select=dict(kind='span',start=0,end=256)),
                ],
                question='What configuration is supplied across these selected fragments?',
                operation='factual',search_results=3,known_symbols=1
            ) if semantic else dict(paths=['data/values.json'],projection=dict(kind='json',pointers=['/service/limit'])))
            messages=[dict(jsonrpc='2.0',id=1,method='initialize',params={}),dict(jsonrpc='2.0',id=2,method='tools/call',params=dict(name=tool,arguments=request))]
            cp=subprocess.run(cmd,input=''.join(json.dumps(m)+'\n' for m in messages),capture_output=True,text=True,timeout=10)
            self.assertEqual(cp.returncode,0,cp.stderr);self.assertFalse(json.loads(cp.stdout.splitlines()[-1])['result']['isError'])
            events.append(dict(type='item.completed',item=dict(id='m',type='mcp_tool_call',tool=tool,result={})))
        out=root/'benchmark-output';out.mkdir();(out/f'{case}.json').write_text(json.dumps(result))
        events.append(dict(type='turn.completed',usage=dict(input_tokens=100,output_tokens=5,cached_input_tokens=20,reasoning_output_tokens=1)))
        return ProcessResult(0,(''.join(json.dumps(e)+'\n' for e in events)).encode(),b'')

    def test_real_local_boundary_default_no_models(self):
        result=verify_context.verify(self.base/'boundary')
        self.assertTrue(result['ok']);self.assertEqual(result['model_calls'],0)
        self.assertFalse(result['live_boundary_verified']);self.assertEqual(result['tools'],['search','extract','query'])

    def test_adoption_gate_does_not_hide_failed_cost_or_unbalanced_tasks(self):
        rows=[dict(task='x',arm=a,task_success=True,system_accounting_complete=True,system_raw_tokens=t,worker_calls=0,cache_hits=0,seconds=1) for a,t in [('baseline',100),('deterministic',70)]]
        self.assertEqual(evaluate.comparison(rows,'baseline','deterministic')['decision'],'sample_threshold_met')
        rows[1]['task_success']=False
        self.assertEqual(evaluate.comparison(rows,'baseline','deterministic')['decision'],'reject')
        rows[1]['task']='y'
        self.assertEqual(evaluate.comparison(rows,'baseline','deterministic')['decision'],'pending')

    def test_validator_fingerprint_changes(self):
        validator=self.base/'validator.py';validator.write_text('pass')
        task=dict(acceptance=[[str(validator)]],regression=[[str(validator)]])
        before=evaluate.validator_hashes([task]);validator.write_text('raise SystemExit(1)')
        self.assertNotEqual(evaluate.validator_hashes([task]),before)

    def test_migration_preserves_task_and_does_not_run_models(self):
        cfg=self.base/'approved.local.json';cfg.write_text(json.dumps(dict(approved=True,adapter='command',argv=[sys.executable,'-c','pass'])))
        t=self.manifest['tasks'][0]
        source=self.base/'old.json'
        source.write_text(json.dumps(dict(repo=self.manifest['repo'],base_commit=self.manifest['base_commit'],
            strategies=[dict(name='baseline',model='synthetic-test-only',worker_mode='disabled',config_overrides=['model_reasoning_effort=\"medium\"']),
                        dict(name='worker',model='synthetic-test-only',worker_mode='required',worker_config=str(cfg),worker_allow_prefixes=['data'],config_overrides=['model_reasoning_effort=\"medium\"'])],
            tasks=[dict(id=t['id'],user_prompt=t['prompt'],acceptance=t['acceptance'],regression=t['regression'],capture_paths=t['outputs'])])))
        before=source.read_bytes();m=migrate_manifest.migrate(source,self.base/'migrated.json')
        self.assertEqual(m['arms'],list(evaluate.ARMS));self.assertEqual(source.read_bytes(),before)
        self.assertEqual(m['tasks'][0]['prompt'],t['prompt']);self.assertEqual(m['worker_config'],str(cfg))
        with self.assertRaises(ValueError):migrate_manifest.migrate(source,self.base/'migrated.json')

    def test_live_command_ignores_user_config(self):
        seen=[]
        def capture(argv,**kw):
            seen.append(list(argv));return self.synthetic_executor(argv,**kw)
        manifest=json.loads(json.dumps(self.manifest))
        manifest['arms']=['baseline'];manifest['tasks']=[manifest['tasks'][0]]
        rows=evaluate.run(manifest,self.base/'isolated-live',True,capture)
        self.assertEqual(len(rows),1);self.assertIn('--ignore-user-config',seen[0]);self.assertIn('--ephemeral',seen[0])
        self.assertIn('skills.include_instructions=false',seen[0]);self.assertIn('apps',seen[0]);self.assertIn('plugins',seen[0])
        if sys.platform == 'win32': self.assertIn('windows.sandbox="elevated"',seen[0])

    def test_default_preflight_no_inference(self):
        def forbidden(*a,**kw):raise AssertionError('model called')
        self.assertEqual(evaluate.run(self.manifest,self.base/'plan',executor=forbidden),[])
        self.assertEqual(json.loads((self.base/'plan/preflight.json').read_text())['model_calls'],0)

    def test_full_simulated_abc_with_real_git_mcp_and_validators(self):
        stub = "import json,sys; j=json.load(sys.stdin); fs=json.loads(j['messages'][1]['content'])['files']; findings=[{'path':f['path'],'symbol':'config','evidence':f['content'].strip()[:120],'fact':'Selected configuration is present'} for f in fs]; a={'status':'ok','findings':findings,'unknowns':[],'read_paths':[f['path'] for f in fs]}; print(json.dumps({'output':json.dumps(a),'usage':{'input_tokens':25,'output_tokens':10}}))"
        cfg=self.base/'approved.local.json';cfg.write_text(json.dumps(dict(
            approved=True,adapter='command',argv=[sys.executable,'-c',stub],
            context_auto_dispatch=True)))
        self.manifest.update(arms=list(evaluate.ARMS),worker_config=str(cfg))
        rows=evaluate.run(self.manifest,self.base/'runs',True,self.synthetic_executor)
        self.assertEqual(len(rows),9);self.assertTrue(all(r['task_success'] for r in rows));self.assertTrue(all(r['system_accounting_complete'] for r in rows))
        self.assertTrue(all(r['system_raw_tokens']==(140 if r['arm']=='semantic-optional' else 105) for r in rows))
        self.assertEqual(sum(r['worker_calls'] for r in rows),3)
        self.assertEqual(evaluate.git(Path(self.manifest['repo']),'status','--porcelain=v1'),'')

    def test_preserve_previous_results(self):
        out=self.base/'results';out.mkdir();(out/'keep').write_text('old')
        with self.assertRaises(ValueError):evaluate.run(self.manifest,out)
        self.assertEqual((out/'keep').read_text(),'old')

    def test_semantic_arm_requires_approved_configuration(self):
        self.manifest['arms']=list(evaluate.ARMS)
        with self.assertRaises(ValueError):evaluate.prepare(self.manifest)

    def test_failed_task_cost_stays_in_numerator(self):
        rows=[dict(arm='baseline',task_success=s,system_accounting_complete=True,system_raw_tokens=t,worker_calls=0,cache_hits=0,seconds=1) for s,t in [(True,100),(False,50)]]
        summary=evaluate.summarize(rows)[0];self.assertEqual(summary['system_token_CPTS'],150);self.assertEqual(summary['success_rate'],0.5)
        rows[1]['system_accounting_complete']=False;self.assertIsNone(evaluate.summarize(rows)[0]['system_token_CPTS'])

    def test_validators_independent_of_parser(self):
        text=(Path(self.manifest['repo'])/'data/page.html').read_text()
        self.assertEqual(project(text,dict(kind='html',fields=list(fixtures.EXPECTED['html'])))['values'],fixtures.EXPECTED['html'])
        self.assertTrue(all(x['positive'] and x['negative'] for x in json.loads((self.path.parent/'validator-selftests.json').read_text())))


if __name__=='__main__':unittest.main()
