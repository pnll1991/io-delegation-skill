import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import io_gateway as gateway


class GatewayCLITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.project=self.base/'project';self.project.mkdir()
        (self.project/'src').mkdir();(self.project/'src/app.py').write_text('X=1\n')
        self.home=self.base/'home'
        self.old=(gateway.USER_ROOT,gateway.PROJECTS,gateway.AUDITS,gateway.CONFIGS,gateway.BACKUPS)
        gateway.USER_ROOT=self.home
        gateway.PROJECTS=self.home/'projects';gateway.AUDITS=self.home/'audits'
        gateway.CONFIGS=self.home/'configs';gateway.BACKUPS=self.home/'backups'
        self.addCleanup(self.restore)

    def restore(self):
        gateway.USER_ROOT,gateway.PROJECTS,gateway.AUDITS,gateway.CONFIGS,gateway.BACKUPS=self.old

    def cli(self,*args):
        with patch('sys.stdout',new_callable=io.StringIO) as out:
            code=gateway.main(list(args))
        return code,out.getvalue()

    def test_codex_setup_installs_gateway_and_observe_guard(self):
        code,text=self.cli('setup','--project',str(self.project),'--agent','codex',
                           '--jev','off','--guard','observe','--no-doctor')
        self.assertEqual(code,0,text)
        self.assertTrue((self.project/'.agents/skills/io-delegation/SKILL.md').is_file())
        config=(self.project/'.codex/config.toml').read_text()
        self.assertIn('[mcp_servers.io_context]',config)
        self.assertIn('"query"',config)
        policy=gateway.read_json(self.project/'.io-delegation-hooks/policy.json')
        self.assertEqual(policy['mode'],'observe')
        state=gateway.load_state(self.project)
        self.assertEqual(state['allow_prefixes'],['src'])
        self.assertIsNone(state['router_config'])

    def test_doctor_starts_real_mcp_without_model(self):
        self.cli('setup','--project',str(self.project),'--agent','codex',
                 '--jev','off','--guard','off','--no-doctor')
        code,text=self.cli('doctor','--project',str(self.project))
        self.assertEqual(code,0,text)
        self.assertIn('MCP handshake: search, extract, query',text)

    def test_all_agents_reference_env_without_persisting_key(self):
        secret='TEST_SECRET_VALUE_ABC123'
        with patch.dict(os.environ,{'TYPESAFE_API_KEY':secret},clear=False):
            code,text=self.cli('setup','--project',str(self.project),'--agent','all',
                               '--jev','auto','--guard','off','--no-doctor')
        self.assertEqual(code,0,text)
        state=gateway.load_state(self.project);self.assertTrue(state['router_config'])
        self.assertIn('${TYPESAFE_API_KEY}',(self.project/'.mcp.json').read_text())
        self.assertIn('${env:TYPESAFE_API_KEY}',(self.project/'.cursor/mcp.json').read_text())
        self.assertIn('TYPESAFE_API_KEY',(self.project/'.codex/config.toml').read_text())
        for root in (self.project,self.home):
            for path in root.rglob('*'):
                if path.is_file():
                    self.assertNotIn(secret,path.read_text(encoding='utf-8',errors='ignore'))

    def test_auto_scope_excludes_generated_and_agent_directories(self):
        for name in ('node_modules','.agents','dist'):
            (self.project/name).mkdir(exist_ok=True)
        (self.project/'docs').mkdir();(self.project/'README.md').write_text('x')
        prefixes,files=gateway.auto_scope(self.project)
        self.assertEqual(prefixes,['docs','src'])
        self.assertEqual(files,['README.md'])

    def test_existing_modified_skill_requires_update(self):
        dest=self.project/'.agents/skills/io-delegation';dest.mkdir(parents=True)
        (dest/'SKILL.md').write_text('custom')
        with patch('sys.stderr',new_callable=io.StringIO) as err:
            code=gateway.main(['setup','--project',str(self.project),'--agent','codex',
                               '--jev','off','--guard','off','--no-doctor'])
        self.assertEqual(code,2)
        self.assertIn('--update',err.getvalue())


if __name__=='__main__': unittest.main()
