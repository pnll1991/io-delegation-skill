import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'benchmarks/v1-validation/experiment.py'
spec = importlib.util.spec_from_file_location('v1_experiment', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def manifest(root):
    return {
        'version': 1,
        'seed': 123,
        'repetitions': 2,
        'repositories': {'r': {'path': str(root), 'commit': 'HEAD'}},
        'arms': [
            {'name': 'control', 'gateway': False, 'jev': False, 'worker': False},
            {'name': 'gateway', 'gateway': True, 'jev': False, 'worker': False},
        ],
        'tasks': [
            {
                'id': 't1',
                'family': 'targeted',
                'repo': 'r',
                'prompt': 'Read src/a.py and write the requested answer.',
                'allow_files': ['src/a.py'],
                'validator': [['python', '-c', 'raise SystemExit(0)']],
                'expected_route': 'targeted_read',
            },
            {
                'id': 't2',
                'family': 'small-control',
                'repo': 'r',
                'prompt': 'Run the tests.',
                'allow_files': ['src/a.py'],
                'validator': [['python', '-c', 'raise SystemExit(0)']],
                'expected_route': 'bypass',
            },
        ],
    }


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'src').mkdir()
        (self.root / 'src/a.py').write_text('VALUE=1\n', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_manifest_validates(self):
        self.assertEqual(mod.validate_manifest(manifest(self.root))['version'], 1)

    def test_duplicate_task_rejected(self):
        data = manifest(self.root)
        data['tasks'].append(dict(data['tasks'][0]))
        with self.assertRaisesRegex(ValueError, 'unique'):
            mod.validate_manifest(data)

    def test_sensitive_scope_rejected(self):
        data = manifest(self.root)
        data['tasks'][0]['allow_files'] = ['.env']
        with self.assertRaisesRegex(ValueError, 'sensitive'):
            mod.validate_manifest(data)

    def test_jev_requires_gateway(self):
        data = manifest(self.root)
        data['arms'][0]['jev'] = True
        with self.assertRaisesRegex(ValueError, 'requires gateway'):
            mod.validate_manifest(data)

    def test_plan_is_reproducible(self):
        data = manifest(self.root)
        first = [(t['id'], a['name'], r) for t, a, r in mod.plan(data, seed=55)]
        second = [(t['id'], a['name'], r) for t, a, r in mod.plan(data, seed=55)]
        self.assertEqual(first, second)
        self.assertEqual(len(first), 8)

    def test_plan_filtering(self):
        data = manifest(self.root)
        rows = mod.plan(data, selected_tasks=['t1'], selected_arms=['gateway'], repetitions=3, seed=1)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(task['id'] == 't1' and arm['name'] == 'gateway' for task, arm, _ in rows))

    def test_check_validator(self):
        ok, rows = mod.run_checks(
            self.root,
            [['python', '-c', 'raise SystemExit(0)']],
            {'worktree': self.root},
        )
        self.assertTrue(ok)
        self.assertEqual(rows[0]['exit_code'], 0)
        self.assertNotIn('stdout_tail', rows[0])

    def test_validator_output_is_bounded_and_redacted(self):
        secret='VALIDATOR_FAKE_SECRET_123456'
        command=['python','-c',
                 'import os,sys; print("x"*6000+os.environ["TEST_VALIDATOR_KEY"]); print("bad",file=sys.stderr); raise SystemExit(4)']
        with patch.dict(os.environ, {'TEST_VALIDATOR_KEY':secret}, clear=False):
            ok,rows=mod.run_checks(self.root,[command],{'worktree':self.root})
        self.assertFalse(ok)
        self.assertEqual(rows[0]['exit_code'],4)
        self.assertNotIn(secret,rows[0].get('stdout_tail',''))
        self.assertIn('[REDACTED]',rows[0].get('stdout_tail',''))
        self.assertLessEqual(len(rows[0].get('stdout_tail','')),mod.VALIDATOR_TAIL_BYTES+32)
        self.assertEqual(len(rows[0]['stdout_sha256']),64)
        self.assertIn('bad',rows[0].get('stderr_tail',''))

    def test_inactive_gateway_registration_advertises_zero_tools(self):
        args=mod.inactive_codex_arguments()
        settings={args[i+1].split('=',1)[0]:json.loads(args[i+1].split('=',1)[1])
                  for i in range(0,len(args),2)}
        self.assertEqual(settings['mcp_servers.io_context.enabled_tools'],['search','extract','query'])
        command=[settings['mcp_servers.io_context.command'],*settings['mcp_servers.io_context.args']]
        request=[
            {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'test','version':'1'}}},
            {'jsonrpc':'2.0','method':'notifications/initialized'},
            {'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}},
        ]
        payload=(''.join(json.dumps(x)+'\n' for x in request)).encode('utf-8')
        cp=mod.run_process(command,payload=payload,timeout=5)
        self.assertEqual(cp.returncode,0)
        rows=[json.loads(line) for line in cp.stdout.splitlines()]
        tools=next(row['result']['tools'] for row in rows if row.get('id')==2)
        self.assertEqual(tools,[])

    def test_codex_base_command_isolates_user_state_equally(self):
        with patch('shutil.which',return_value='/synthetic/codex'):
            command=mod.codex_base_command(self.root,'workspace-write')
        text=' '.join(map(str,command))
        self.assertIn('--ephemeral',command)
        self.assertIn('--ignore-user-config',command)
        self.assertIn('--skip-git-repo-check',command)
        self.assertEqual(command.count('--disable'),2)
        self.assertIn('apps',command); self.assertIn('plugins',command)
        self.assertIn('approval_policy=\"never\"',text)
        self.assertIn('web_search=\"disabled\"',text)
        self.assertIn('features.shell_tool=true',text)
        self.assertIn('features.unified_exec=false',text)
        self.assertIn('features.multi_agent=false',text)
        self.assertIn('features.memories=false',text)
        self.assertIn('features.skill_mcp_dependency_install=false',text)
        self.assertIn('sandbox_workspace_write.network_access=false',text)
        if os.name == 'nt': self.assertIn('windows.sandbox=\"unelevated\"',text)
        self.assertIn('workspace-write',command)

    def test_run_conditions_are_explicit(self):
        principal={'cached_input_tokens':123}
        enabled={'decision':'enable'}
        bypass={'decision':'bypass'}
        self.assertEqual(mod.run_conditions(principal,enabled),{
            'principal_process':'cold','context_cache':'cold','provider_cache':'reported','worktree':'fresh'})
        self.assertEqual(mod.run_conditions({},bypass)['context_cache'],'disabled')
        self.assertEqual(mod.run_conditions({},bypass)['provider_cache'],'unreported')

    def test_usage_object_keeps_cache_write_tokens(self):
        usage={'input_tokens':10,'output_tokens':2,'cached_input_tokens':4,'cache_write_input_tokens':3,'reasoning_output_tokens':1}
        self.assertEqual(mod.usage_object(usage)['cache_write_input_tokens'],3)

    def test_unsafe_run_id_rejected(self):
        with self.assertRaises(ValueError):
            mod.safe_run_id('../escape')


if __name__ == '__main__':
    unittest.main()
