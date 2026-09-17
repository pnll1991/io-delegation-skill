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

    def test_cursor_global_config_uses_workspace_variable(self):
        code,text,err=self.cli('setup','--project',str(self.project),'--agent','cursor','--jev','off','--no-doctor')
        self.assertEqual(code,0,err)
        self.assertFalse((self.project/'.cursor/mcp.json').exists())
        row=gateway.read_json(self.host/'.cursor/mcp.json')
        entry=row['mcpServers']['io_context']
        self.assertIn('${workspaceFolder}',entry['args'])
        self.assertTrue(any(str(x).endswith('context_bootstrap.py') for x in entry['args']))

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
            code,_,err=self.setup_codex(); self.assertEqual(code,0,err)
        self.assertIn(fake_a.replace('\\','\\\\'),(self.host/'.codex/config.toml').read_text())
        with patch.object(gateway.sys,'executable',fake_b):
            code,_,err=self.setup_codex('--update'); self.assertEqual(code,0,err)
        text=(self.host/'.codex/config.toml').read_text()
        self.assertIn(fake_b.replace('\\','\\\\'),text); self.assertNotIn(fake_a.replace('\\','\\\\'),text)

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



    def test_unmanaged_codex_collision_is_zero_write_preflight(self):
        config=self.host/'.codex/config.toml'; config.parent.mkdir(parents=True)
        config.write_text('[mcp_servers.io_context]\ncommand = "other"\n',encoding='utf-8')
        code,text,err=self.setup_codex()
        self.assertEqual(code,2); self.assertIn('unmanaged io_context',err)
        self.assertFalse((self.project/'.io-delegation').exists())
        self.assertFalse((self.project/'.agents').exists())
        self.assertFalse(self.home.exists())
        self.assertEqual(config.read_text(encoding='utf-8'),'[mcp_servers.io_context]\ncommand = "other"\n')

    def test_default_setup_does_not_install_hooks(self):
        code,text,err=self.setup_codex()
        self.assertEqual(code,0,err)
        self.assertFalse((self.project/'.io-delegation-hooks').exists())
        self.assertEqual(gateway.load_state(self.project)['guard_mode'],'off')

    def test_last_project_can_purge_runtime(self):
        code,text,err=self.setup_codex(); self.assertEqual(code,0,err)
        self.assertTrue((self.home/'runtime').exists())
        code,text,err=self.cli('remove','--project',str(self.project),'--purge-runtime')
        self.assertEqual(code,0,err); self.assertFalse((self.home/'runtime').exists())


if __name__=='__main__': unittest.main()
