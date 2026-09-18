import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import io_gateway as gateway


class GatewayCLITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name); self.project=self.base/'project'; self.project.mkdir()
        (self.project/'src').mkdir(); (self.project/'src/app.py').write_text('X=1\n')
        self.home=self.base/'io-home'; self.host=self.base/'host-home'; self.host.mkdir()
        self.old_root=gateway.USER_ROOT; self.old_host=gateway.HOST_HOME
        gateway.USER_ROOT=self.home; gateway.HOST_HOME=self.host
        self.env=patch.dict(os.environ,{'IO_DELEGATION_HOME':str(self.home),'IO_DELEGATION_HOST_HOME':str(self.host)},clear=False)
        self.env.start(); self.addCleanup(self.env.stop); self.addCleanup(self.restore)

    def restore(self):
        gateway.USER_ROOT=self.old_root; gateway.HOST_HOME=self.old_host

    def cli(self,*args):
        with patch('sys.stdout',new_callable=io.StringIO) as out, patch('sys.stderr',new_callable=io.StringIO) as err:
            code=gateway.main(list(args))
        return code,out.getvalue(),err.getvalue()

    def setup_codex(self,*extra):
        return self.cli('setup','--project',str(self.project),'--agent','codex','--jev','off','--no-doctor',*extra)

    def test_setup_dry_run_writes_nothing(self):
        code,text,err=self.cli('setup','--project',str(self.project),'--agent','codex','--jev','off','--dry-run')
        self.assertEqual(code,0,err); self.assertIn('no files changed',text)
        self.assertFalse((self.project/'.io-delegation').exists())
        self.assertFalse((self.project/'.agents').exists())
        self.assertFalse(self.home.exists())
        self.assertFalse((self.host/'.codex').exists())

    def test_default_setup_does_not_auto_enable_jev_from_environment(self):
        with patch.dict(os.environ,{'TYPESAFE_API_KEY':'fixture'},clear=False):
            code,text,err=self.cli('setup','--project',str(self.project),'--agent','codex','--dry-run')
        self.assertEqual(code,0,err)
        self.assertIn('Jev: off',text)
        self.assertIn('Compaction: automatic (project-scoped: codex)',text)
        self.assertFalse(self.home.exists())

    def test_compaction_dry_run_supports_codex_without_claude(self):
        code,text,err=self.cli('setup','--project',str(self.project),'--agent','codex',
                               '--compaction','on','--dry-run')
        self.assertEqual(code,0,err)
        self.assertIn('Compaction: automatic (project-scoped: codex)',text)
        self.assertFalse((self.project/'.codex/hooks.json').exists())
        self.assertFalse((self.project/'.io-delegation').exists())

    def test_compaction_dry_run_supports_cursor_without_claude(self):
        code,text,err=self.cli('setup','--project',str(self.project),'--agent','cursor',
                               '--compaction','on','--dry-run')
        self.assertEqual(code,0,err)
        self.assertIn('Compaction: automatic (project-scoped: cursor)',text)
        self.assertFalse((self.project/'.cursor/hooks.json').exists())

    def test_compaction_policy_is_project_local_and_secret_free(self):
        gateway.ensure_marker(self.project)
        secret='NEVER_WRITE_THIS_KEY'
        with patch.dict(os.environ,{'CUSTOM_JEV_KEY':secret},clear=False):
            path,action=gateway.sync_compaction_policy(
                self.project,'on','CUSTOM_JEV_KEY',['codex','cursor'])
        self.assertEqual(action,'written')
        row=json.loads(path.read_text(encoding='utf-8'))
        self.assertTrue(row['enabled'])
        self.assertEqual(row['approved_data_scope'],gateway.COMPACTION_DATA_SCOPE)
        self.assertEqual(row['api_key_env'],'CUSTOM_JEV_KEY')
        self.assertEqual(row['hosts'],['codex','cursor'])
        self.assertNotIn(secret,path.read_text(encoding='utf-8'))

    def test_compaction_plugin_install_uses_io_delegation_marketplace(self):
        calls=[]
        class Result:
            returncode=0; stdout=''; stderr=''
        def fake_run(argv,**kwargs):
            calls.append(argv); return Result()
        with patch.object(gateway,'claude_cli',return_value='claude'), \
             patch.object(gateway,'claude_compaction_installed',return_value=False), \
             patch.object(gateway.subprocess,'run',side_effect=fake_run):
            status=gateway.ensure_claude_compaction_plugin()
        self.assertEqual(status,'installed')
        self.assertEqual(calls[0],['claude','plugin','marketplace','add',gateway.COMPACTION_MARKETPLACE])
        self.assertEqual(calls[1],['claude','plugin','install',gateway.COMPACTION_PLUGIN_REF])

    def test_function_hooks_flag_preserves_existing_claude_settings(self):
        path=self.host/'.claude/settings.json'; path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'permissions':{'allow':['Read']}}),encoding='utf-8')
        action=gateway.ensure_claude_function_hooks_flag()
        self.assertEqual(action,'written')
        row=json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(row['permissions'],{'allow':['Read']})
        self.assertEqual(row['env']['CLAUDE_CODE_ENABLE_FUNCTION_HOOKS'],'1')

    def test_failed_setup_removes_new_compaction_policy(self):
        real_save=gateway.save_state
        calls={'n':0}
        def flaky_save(state):
            calls['n']+=1
            if calls['n']==2:
                raise OSError('fixture final save failure')
            return real_save(state)
        registrations={'codex':(None,'unchanged'),'cursor':(None,'unchanged'),
                       'claude-code':(None,'unchanged')}
        with patch.object(gateway,'ensure_claude_compaction_plugin',return_value='installed'), \
             patch.object(gateway,'ensure_claude_function_hooks_flag',return_value='written'), \
             patch.object(gateway,'sync_global_mcp',return_value=registrations), \
             patch.object(gateway,'save_state',side_effect=flaky_save):
            code,text,err=self.cli('setup','--project',str(self.project),'--agent','claude-code',
                                   '--compaction','on','--jev','off','--no-doctor')
        self.assertEqual(code,2)
        self.assertIn('fixture final save failure',err)
        self.assertFalse(gateway.compaction_policy_path(self.project).exists())

    def test_codex_compaction_setup_installs_project_hooks(self):
        code,text,err=self.setup_codex('--compaction','on')
        self.assertEqual(code,0,err)
        hooks=gateway.read_json(self.project/'.codex/hooks.json')
        self.assertIn('PreCompact',hooks['hooks'])
        self.assertIn('SessionStart',hooks['hooks'])
        state=gateway.load_state(self.project)
        self.assertEqual(state['compaction_hosts'],['codex'])
        self.assertEqual(state['compaction_preference'],'on')
        policy=gateway.read_json(self.project/'.io-delegation/compaction.json')
        self.assertEqual(policy['hosts'],['codex'])
        status=json.loads(self.cli('status','--project',str(self.project),'--json')[1])
        self.assertTrue(status['compaction_adapters']['codex'])

    def test_cursor_compaction_setup_installs_recovery_hooks(self):
        code,text,err=self.cli('setup','--project',str(self.project),'--agent','cursor','--jev','off',
                               '--compaction','on','--no-doctor')
        self.assertEqual(code,0,err)
        hooks=gateway.read_json(self.project/'.cursor/hooks.json')
        self.assertIn('preCompact',hooks['hooks'])
        self.assertIn('postToolUse',hooks['hooks'])
        self.assertIn('stop',hooks['hooks'])
        self.assertEqual(hooks['hooks']['stop'][-1]['loop_limit'],1)

    def test_explicit_compaction_off_persists_until_reenabled(self):
        code,text,err=self.setup_codex('--compaction','off')
        self.assertEqual(code,0,err)
        state=gateway.load_state(self.project)
        self.assertEqual(state['compaction_mode'],'off')
        self.assertEqual(state['compaction_preference'],'off')
        self.assertFalse((self.project/'.codex/hooks.json').exists())

        code,text,err=self.setup_codex()
        self.assertEqual(code,0,err)
        self.assertEqual(gateway.load_state(self.project)['compaction_mode'],'off')

        code,text,err=self.setup_codex('--compaction','on')
        self.assertEqual(code,0,err)
        state=gateway.load_state(self.project)
        self.assertEqual(state['compaction_mode'],'on')
        self.assertEqual(state['compaction_preference'],'on')
        self.assertTrue((self.project/'.codex/hooks.json').is_file())

    def test_legacy_off_state_migrates_to_automatic_compaction(self):
        code,text,err=self.setup_codex('--compaction','off')
        self.assertEqual(code,0,err)
        state=gateway.load_state(self.project)
        path=Path(state['_state_path'])
        row=gateway.read_json(path)
        row.pop('compaction_preference',None)
        gateway.atomic_write(path,gateway.encoded(row))

        code,text,err=self.setup_codex()
        self.assertEqual(code,0,err)
        state=gateway.load_state(self.project)
        self.assertEqual(state['compaction_mode'],'on')
        self.assertEqual(state['compaction_preference'],'on')

    def test_codex_setup_uses_global_mcp_and_real_doctor(self):
        code,text,err=self.setup_codex()
        self.assertEqual(code,0,err)
        self.assertTrue((self.project/'.agents/skills/io-delegation/SKILL.md').is_file())
        self.assertFalse((self.project/'.codex/config.toml').exists())
        config=(self.host/'.codex/config.toml').read_text(encoding='utf-8')
        self.assertIn(gateway.CODEX_BEGIN,config); self.assertIn('context_bootstrap.py',config)
        state=gateway.load_state(self.project); self.assertEqual(state['allow_prefixes'],['src'])
        self.assertTrue((self.project/'.io-delegation/project.json').is_file())
        code,text,err=self.cli('doctor','--project',str(self.project))
        self.assertEqual(code,0,err); self.assertIn('MCP handshake: search, extract, query',text)

    def test_worker_config_auto_enables_cheap_first_orchestration(self):
        worker=self.base/'cheap-worker.json'
        worker.write_text(json.dumps(dict(approved=True,adapter='command',
            argv=['worker-fixture'],timeout_seconds=5)))
        code,text,err=self.setup_codex('--worker-config',str(worker))
        self.assertEqual(code,0,err)
        state=gateway.load_state(self.project)
        self.assertEqual(state['orchestration_preference'],'auto')
        self.assertTrue(state['orchestrator_config'])
        self.assertTrue(Path(state['orchestrator_config']).is_file())
        self.assertFalse(str(Path(state['orchestrator_config']).resolve()).startswith(str(self.project.resolve())))
        status=json.loads(self.cli('status','--project',str(self.project),'--json')[1])
        self.assertTrue(status['compute_orchestration'])
        self.assertIn('TYPESAFE_API_KEY',status['credential_envs'])

    def test_model_preset_defaults_balanced_and_persists(self):
        worker=self.base/'host-policy-worker.json'
        worker.write_text(json.dumps(dict(approved=True,adapter='host-cli',timeout_seconds=5)))
        code,text,err=self.setup_codex('--worker-config',str(worker))
        self.assertEqual(code,0,err)
        state=gateway.load_state(self.project)
        self.assertEqual(state['model_policy_preset'],'balanced')
        config=(self.host/'.codex/config.toml').read_text(encoding='utf-8')
        self.assertIn('--host',config);self.assertIn('codex',config)
        status=json.loads(self.cli('status','--project',str(self.project),'--json')[1])
        self.assertEqual(status['model_policy_preset'],'balanced')
        self.assertEqual(status['model_policy']['codex']['max_profile'],'astra-low')
        self.assertEqual(status['model_policy']['codex']['order'][-1],'astra-low')

        code,text,err=self.setup_codex('--model-preset','cost')
        self.assertEqual(code,0,err)
        self.assertEqual(gateway.load_state(self.project)['model_policy_preset'],'cost')
        code,text,err=self.setup_codex()
        self.assertEqual(code,0,err)
        self.assertEqual(gateway.load_state(self.project)['model_policy_preset'],'cost')

    def test_cursor_status_policy_has_no_astra_by_default(self):
        worker=self.base/'host-policy-cursor.json'
        worker.write_text(json.dumps(dict(approved=True,adapter='host-cli',timeout_seconds=5)))
        code,text,err=self.cli('setup','--project',str(self.project),'--agent','cursor','--jev','off',
                               '--worker-config',str(worker),'--no-doctor')
        self.assertEqual(code,0,err)
        status=json.loads(self.cli('status','--project',str(self.project),'--json')[1])
        row=status['model_policy']['cursor']
        self.assertEqual(row['max_profile'],'sol-high')
        self.assertNotIn('astra-low',row['order'])

    def test_user_model_policy_override_is_visible_in_status_and_doctor(self):
        worker=self.base/'custom-policy.json'
        worker.write_text(json.dumps(dict(
            approved=True,adapter='host-cli',timeout_seconds=5,
            model_policy={'hosts':{'codex':{
                'max_profile':'sol-medium',
                'blocked_profiles':['terra-medium']
            }}}
        )))
        code,text,err=self.setup_codex('--worker-config',str(worker),'--model-preset','balanced')
        self.assertEqual(code,0,err)
        status=json.loads(self.cli('status','--project',str(self.project),'--json')[1])
        self.assertEqual(status['model_policy']['codex']['order'],
                         ['luna-medium','luna-high','sol-medium'])
        code,text,err=self.cli('doctor','--project',str(self.project))
        self.assertEqual(code,0,err)
        self.assertIn('model policy codex',text)
        self.assertIn('max sol-medium',text)

    def test_orchestration_off_is_persistent_escape_hatch(self):
        worker=self.base/'cheap-worker-off.json'
        worker.write_text(json.dumps(dict(approved=True,adapter='command',
            argv=['worker-fixture'],timeout_seconds=5)))
        code,text,err=self.setup_codex('--worker-config',str(worker))
        self.assertEqual(code,0,err)
        previous=Path(gateway.load_state(self.project)['orchestrator_config'])
        self.assertTrue(previous.is_file())

        code,text,err=self.setup_codex('--orchestration','off')
        self.assertEqual(code,0,err)
        state=gateway.load_state(self.project)
        self.assertEqual(state['orchestration_preference'],'off')
        self.assertIsNone(state['orchestrator_config'])
        self.assertFalse(previous.exists())

        code,text,err=self.setup_codex()
        self.assertEqual(code,0,err)
        self.assertIsNone(gateway.load_state(self.project)['orchestrator_config'])

    def test_orchestration_on_requires_worker(self):
        code,text,err=self.setup_codex('--orchestration','on')
        self.assertEqual(code,2)
        self.assertIn('requires an approved --worker-config',err)
        self.assertFalse((self.project/'.io-delegation').exists())

    def test_failed_setup_restores_generated_orchestrator_config(self):
        worker=self.base/'rollback-worker.json'
        worker.write_text(json.dumps(dict(approved=True,adapter='command',
            argv=['worker-fixture'],timeout_seconds=5)))
        code,text,err=self.setup_codex('--worker-config',str(worker))
        self.assertEqual(code,0,err)
        state=gateway.load_state(self.project)
        config=Path(state['orchestrator_config'])
        before=config.read_bytes()

        real_save=gateway.save_state
        calls={'n':0}
        def flaky_save(row):
            calls['n']+=1
            if calls['n']==2:
                raise OSError('orchestrator rollback fixture')
            return real_save(row)

        with patch.object(gateway,'save_state',side_effect=flaky_save):
            code,text,err=self.setup_codex('--typesafe-env','OTHER_TYPESAFE_KEY')
        self.assertEqual(code,2)
        self.assertIn('orchestrator rollback fixture',err)
        self.assertEqual(config.read_bytes(),before)
        self.assertEqual(gateway.load_state(self.project)['compaction_api_key_env'],'TYPESAFE_API_KEY')

    def test_legacy_state_recovers_compute_credential_name(self):
        worker=self.base/'legacy-compute-worker.json'
        worker.write_text(json.dumps(dict(approved=True,adapter='command',
            argv=['worker-fixture'],timeout_seconds=5)))
        code,text,err=self.setup_codex('--worker-config',str(worker))
        self.assertEqual(code,0,err)
        state=gateway.load_state(self.project)
        path=Path(state['_state_path'])
        row=gateway.read_json(path)
        row.pop('credential_env_names',None)
        gateway.atomic_write(path,gateway.encoded(row))
        status=json.loads(self.cli('status','--project',str(self.project),'--json')[1])
        self.assertIn('TYPESAFE_API_KEY',status['credential_envs'])

    def test_doctor_reports_compute_orchestration_without_calling_jev(self):
        worker=self.base/'doctor-compute-worker.json'
        worker.write_text(json.dumps(dict(approved=True,adapter='command',
            argv=['worker-fixture'],timeout_seconds=5)))
        code,text,err=self.setup_codex('--worker-config',str(worker))
        self.assertEqual(code,0,err)
        code,text,err=self.cli('doctor','--project',str(self.project))
        self.assertEqual(code,0,err)
        self.assertIn('Jev compute orchestration',text)
        self.assertIn('Jev compute key',text)
        self.assertIn('model worker',text)

    def test_worker_status_distinguishes_configured_from_auto_dispatch(self):
        worker=self.base/'worker.json'
        worker.write_text(json.dumps(dict(approved=True,adapter='command',argv=['worker-fixture'])))
        code,_,err=self.setup_codex('--worker-config',str(worker))
        self.assertEqual(code,0,err)
        code,text,err=self.cli('status','--project',str(self.project),'--json')
        self.assertEqual(code,0,err)
        data=json.loads(text)
        self.assertTrue(data['semantic_worker']); self.assertFalse(data['worker_auto_dispatch'])
        code,text,err=self.cli('doctor','--project',str(self.project))
        self.assertEqual(code,0,err); self.assertIn('worker auto-dispatch: off',text)

        cfg=json.loads(worker.read_text()); cfg['context_auto_dispatch']=True
        worker.write_text(json.dumps(cfg))
        code,text,err=self.cli('status','--project',str(self.project),'--json')
        self.assertEqual(code,0,err); self.assertTrue(json.loads(text)['worker_auto_dispatch'])
        code,text,err=self.cli('doctor','--project',str(self.project))
        self.assertEqual(code,0,err); self.assertIn('experimental opt-in enabled',text)

    def test_doctor_rejects_public_tool_superset(self):
        code,_,err=self.setup_codex()
        self.assertEqual(code,0,err)
        with patch('io_gateway.mcp_probe',return_value=['search','extract','query','semantic_query']):
            code,text,err=self.cli('doctor','--project',str(self.project))
        self.assertEqual(code,2,err)
        self.assertIn('[fail] MCP handshake: search, extract, query, semantic_query',text)

    def test_cursor_global_config_relies_on_workspace_cwd(self):
        code,text,err=self.cli('setup','--project',str(self.project),'--agent','cursor','--jev','off','--no-doctor')
        self.assertEqual(code,0,err)
        self.assertFalse((self.project/'.cursor/mcp.json').exists())
        row=gateway.read_json(self.host/'.cursor/mcp.json')
        entry=row['mcpServers']['io_context']
        self.assertEqual(len(entry['args']),3)
        self.assertTrue(str(entry['args'][0]).endswith('context_bootstrap.py'))
        self.assertEqual(entry['args'][1:],['--host','cursor'])
        self.assertNotIn('${workspaceFolder}',json.dumps(entry))

    def test_secret_value_is_never_persisted(self):
        secret='TEST_SECRET_VALUE_ABC123'
        with patch.dict(os.environ,{'TYPESAFE_API_KEY':secret},clear=False):
            code,text,err=self.cli('setup','--project',str(self.project),'--agent','codex','--agent','cursor',
                                   '--jev','auto','--no-doctor')
        self.assertEqual(code,0,err)
        state=gateway.load_state(self.project); self.assertTrue(state['router_config'])
        for root in (self.project,self.home,self.host):
            if not root.exists(): continue
            for path in root.rglob('*'):
                if path.is_file():
                    self.assertNotIn(secret,path.read_text(encoding='utf-8',errors='ignore'))
        self.assertIn('TYPESAFE_API_KEY',(self.host/'.codex/config.toml').read_text())
        cursor=(self.host/'.cursor/mcp.json').read_text()
        self.assertIn('${env:TYPESAFE_API_KEY}',cursor)

    def test_moved_project_keeps_identity_and_gateway_works(self):
        self.setup_codex()
        before=gateway.load_state(self.project)['project_id']
        moved=self.base/'moved-project'; self.project.rename(moved); self.project=moved
        code,text,err=self.cli('doctor','--project',str(moved))
        self.assertEqual(code,0,err); self.assertIn('project path: moved',text)
        code,text,err=self.cli('setup','--project',str(moved),'--agent','codex','--jev','off','--no-doctor')
        self.assertEqual(code,0,err)
        state=gateway.load_state(moved); self.assertEqual(state['project_id'],before)
        self.assertEqual(state['project'],str(moved.resolve()))

    def test_remove_dry_run_then_surgical_remove(self):
        code,_,err=self.cli('setup','--project',str(self.project),'--agent','codex','--agent','cursor',
                            '--jev','off','--no-doctor')
        self.assertEqual(code,0,err)
        codex=self.host/'.codex/config.toml'; codex.write_text('user_setting = true\n\n'+codex.read_text(),encoding='utf-8')
        code,text,err=self.cli('remove','--project',str(self.project),'--dry-run')
        self.assertEqual(code,0,err); self.assertTrue((self.project/'.agents/skills/io-delegation').exists())
        code,text,err=self.cli('remove','--project',str(self.project))
        self.assertEqual(code,0,err)
        self.assertFalse((self.project/'.io-delegation').exists())
        self.assertFalse((self.project/'.agents/skills/io-delegation').exists())
        self.assertIn('user_setting = true',codex.read_text())
        self.assertNotIn(gateway.CODEX_BEGIN,codex.read_text())
        cursor=gateway.read_json(self.host/'.cursor/mcp.json',{})
        self.assertNotIn('io_context',cursor.get('mcpServers',{}))

    def test_modified_skill_is_preserved_on_remove(self):
        self.setup_codex()
        skill=self.project/'.agents/skills/io-delegation/SKILL.md'
        skill.write_text(skill.read_text(encoding='utf-8')+'\ncustom\n',encoding='utf-8')
        code,text,err=self.cli('remove','--project',str(self.project))
        self.assertEqual(code,0,err); self.assertTrue(skill.exists())
        self.assertIn('Preserved modified skill',text)

    def test_backup_restore_refuses_after_user_change(self):
        path=self.host/'config.txt'; path.write_text('before',encoding='utf-8')
        meta=gateway.backup_snapshot(path,'unit'); path.write_text('after',encoding='utf-8'); gateway.finalize_backup(meta,path)
        ident=gateway.read_json(meta)['id']
        path.write_text('user-change',encoding='utf-8')
        with self.assertRaises(ValueError): gateway.restore_backup(ident)
        gateway.restore_backup(ident,force=True)
        self.assertEqual(path.read_text(encoding='utf-8'),'before')

    def test_rerun_updates_python_path_in_global_config(self):
        fake_a=str(self.base/'python-a.exe'); fake_b=str(self.base/'python-b.exe')
        with patch.object(gateway.sys,'executable',fake_a):
            code,_,err=self.setup_codex('--compaction','off'); self.assertEqual(code,0,err)
        expected_a=str(Path(fake_a).resolve()).replace('\\','\\\\')
        self.assertIn(expected_a,(self.host/'.codex/config.toml').read_text())
        with patch.object(gateway.sys,'executable',fake_b):
            code,_,err=self.setup_codex('--compaction','off','--update'); self.assertEqual(code,0,err)
        text=(self.host/'.codex/config.toml').read_text()
        expected_b=str(Path(fake_b).resolve()).replace('\\','\\\\')
        self.assertIn(expected_b,text); self.assertNotIn(expected_a,text)

    def test_tracked_hook_config_requires_explicit_override(self):
        if not shutil.which('git'): self.skipTest('git unavailable')
        subprocess.run(['git','init'],cwd=self.project,check=True,capture_output=True)
        path=self.project/'.codex/hooks.json'; path.parent.mkdir(); path.write_text('{}')
        subprocess.run(['git','add','.codex/hooks.json'],cwd=self.project,check=True,capture_output=True)
        code,text,err=self.cli('setup','--project',str(self.project),'--agent','codex','--jev','off',
                               '--guard','observe','--no-doctor')
        self.assertEqual(code,2); self.assertIn('tracked by git',err)

    def test_existing_modified_skill_requires_update(self):
        dest=self.project/'.agents/skills/io-delegation'; dest.mkdir(parents=True)
        (dest/'SKILL.md').write_text('custom')
        code,text,err=self.setup_codex()
        self.assertEqual(code,2); self.assertIn('--update',err)


    def test_shared_global_server_survives_one_project_removal(self):
        other=self.base/'other'; other.mkdir(); (other/'src').mkdir(); (other/'src/b.py').write_text('Y=2\n')
        code,_,err=self.setup_codex(); self.assertEqual(code,0,err)
        code,_,err=self.cli('setup','--project',str(other),'--agent','codex','--jev','off','--no-doctor'); self.assertEqual(code,0,err)
        first=gateway.load_state(self.project)['project_id']; second=gateway.load_state(other)['project_id']; self.assertNotEqual(first,second)
        code,_,err=self.cli('remove','--project',str(self.project)); self.assertEqual(code,0,err)
        self.assertTrue(gateway.codex_registered())
        code,text,err=self.cli('doctor','--project',str(other)); self.assertEqual(code,0,err)
        code,_,err=self.cli('remove','--project',str(other)); self.assertEqual(code,0,err)
        self.assertFalse(gateway.codex_registered())

    def test_global_bootstrap_is_inactive_outside_configured_projects(self):
        gateway.install_runtime()
        outside=self.base/'outside'; outside.mkdir()
        messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},
                  {'jsonrpc':'2.0','method':'notifications/initialized'},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}}]
        payload=''.join(json.dumps(x)+'\n' for x in messages)
        cp=subprocess.run([gateway.sys.executable,str(gateway.runtime_bootstrap())],cwd=outside,input=payload,
                          capture_output=True,text=True,encoding='utf-8',timeout=10)
        self.assertEqual(cp.returncode,0,cp.stderr)
        replies=[json.loads(x) for x in cp.stdout.splitlines()]
        tools=next(x['result']['tools'] for x in replies if x.get('id')==2)
        self.assertEqual(tools,[])



    def test_claude_user_registration_uses_structured_add_json(self):
        calls=[]
        class Result:
            returncode=0; stdout=''; stderr=''
        def fake_run(argv,**kwargs):
            calls.append(argv); return Result()
        states=[{'project_id':'x','agents':['claude-code'],'credential_env_names':[]}]
        with patch.object(gateway,'claude_cli',return_value='claude'), \
             patch.object(gateway,'claude_server_owned',return_value=(False,None)), \
             patch.object(gateway.subprocess,'run',side_effect=fake_run):
            target,status=gateway.update_claude_user(states)
        self.assertEqual(status,'written'); self.assertEqual(target,'claude:user')
        argv=calls[-1]; self.assertEqual(argv[:3],['claude','mcp','add-json'])
        self.assertIn('--scope',argv); self.assertIn('user',argv)
        definition=json.loads(argv[4])
        self.assertEqual(definition['type'],'stdio')
        self.assertTrue(definition['args'][0].endswith('context_bootstrap.py'))
        self.assertEqual(definition['args'][1:],['--host','claude-code'])



    def test_unmanaged_codex_collision_is_zero_write_preflight(self):
        config=self.host/'.codex/config.toml'; config.parent.mkdir(parents=True)
        config.write_text('[mcp_servers.io_context]\ncommand = "other"\n',encoding='utf-8')
        code,text,err=self.setup_codex()
        self.assertEqual(code,2); self.assertIn('unmanaged io_context',err)
        self.assertFalse((self.project/'.io-delegation').exists())
        self.assertFalse((self.project/'.agents').exists())
        self.assertFalse(self.home.exists())
        self.assertEqual(config.read_text(encoding='utf-8'),'[mcp_servers.io_context]\ncommand = "other"\n')

    def test_default_setup_installs_compaction_but_not_read_guard(self):
        code,text,err=self.setup_codex()
        self.assertEqual(code,0,err)
        hooks=gateway.read_json(self.project/'.codex/hooks.json')
        self.assertIn('PreCompact',hooks['hooks'])
        self.assertTrue((self.project/'.io-delegation-hooks/codex.compaction.json').is_file())
        self.assertFalse((self.project/'.io-delegation-hooks/policy.json').exists())
        state=gateway.load_state(self.project)
        self.assertEqual(state['compaction_mode'],'on')
        self.assertEqual(state['compaction_preference'],'on')
        self.assertEqual(state['guard_mode'],'off')

    def test_last_project_can_purge_runtime(self):
        code,text,err=self.setup_codex(); self.assertEqual(code,0,err)
        self.assertTrue((self.home/'runtime').exists())
        code,text,err=self.cli('remove','--project',str(self.project),'--purge-runtime')
        self.assertEqual(code,0,err); self.assertFalse((self.home/'runtime').exists())


    def _bootstrap_tools(self):
        messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},
                  {'jsonrpc':'2.0','method':'notifications/initialized'},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}}]
        payload=''.join(json.dumps(x)+'\n' for x in messages)
        cp=subprocess.run([gateway.sys.executable,str(gateway.runtime_bootstrap())],cwd=self.project,
                          input=payload,capture_output=True,text=True,encoding='utf-8',timeout=10)
        self.assertEqual(cp.returncode,0,cp.stderr)
        replies=[json.loads(x) for x in cp.stdout.splitlines()]
        return next(x['result']['tools'] for x in replies if x.get('id')==2)

    def test_auto_activation_bypasses_small_project_but_doctor_forces_probe(self):
        code,_,err=self.setup_codex(); self.assertEqual(code,0,err)
        state=gateway.load_state(self.project); self.assertEqual(state['activation_mode'],'auto')
        self.assertEqual(self._bootstrap_tools(),[])
        code,text,err=self.cli('doctor','--project',str(self.project))
        self.assertEqual(code,0,err); self.assertIn('MCP handshake: search, extract, query',text)
        code,text,err=self.cli('gate','--project',str(self.project),'--json')
        self.assertEqual(code,0,err); self.assertEqual(json.loads(text)['decision'],'bypass')

    def test_activation_always_exposes_gateway(self):
        code,_,err=self.setup_codex('--activation','always'); self.assertEqual(code,0,err)
        names={row['name'] for row in self._bootstrap_tools()}
        self.assertTrue({'search','extract','query'} <= names)


if __name__=='__main__': unittest.main()
