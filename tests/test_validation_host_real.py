import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

BASE=Path(__file__).resolve().parents[1]/'benchmarks/v1-validation'
spec=importlib.util.spec_from_file_location('host_real',BASE/'host_real.py')
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class HostRealTests(unittest.TestCase):
    def test_setup_is_local_router_off_and_explicit_scope(self):
        cmd=mod.setup_command('cursor',Path('P'),Path('worker.json'))
        text=' '.join(map(str,cmd))
        self.assertIn('--agent cursor',text)
        self.assertIn('--jev off',text)
        self.assertIn('--guard off',text)
        self.assertIn('--activation always',text)
        self.assertEqual(cmd.count('--allow-file'),4)
        for name in mod.FILES: self.assertIn(name,cmd)

    def test_claude_agent_mapping(self):
        cmd=mod.setup_command('claude',Path('P'),Path('worker.json'))
        self.assertIn('claude-code',cmd)

    def test_codex_agent_mapping(self):
        cmd=mod.setup_command('codex',Path('P'),Path('worker.json'))
        self.assertIn('codex',cmd)
        validate=mod.host_validate_command('codex',Path('P'),BASE/'host_suite.fixture.json',Path('O'))
        self.assertIn('--host',validate)
        self.assertIn('codex',validate)

    def test_remove_purges_fixture_data_not_runtime(self):
        cmd=mod.remove_command(Path('P'))
        self.assertIn('--purge-data',cmd)
        self.assertNotIn('--purge-runtime',cmd)

    def test_doctor_command_is_json_and_project_scoped(self):
        cmd=mod.doctor_command(Path('P'))
        text=' '.join(map(str,cmd))
        self.assertIn('doctor',cmd)
        self.assertIn('--project P',text)
        self.assertIn('--json',cmd)

    def test_host_validate_command_uses_common_suite(self):
        cmd=mod.host_validate_command('cursor',Path('P'),BASE/'host_suite.fixture.json',Path('O'))
        text=' '.join(map(str,cmd))
        self.assertIn('host_validate.py',text)
        self.assertIn('host_suite.fixture.json',text)
        self.assertIn('--host cursor',text)

    def test_credential_env_names_are_names_not_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'worker.json'
            path.write_text(json.dumps({'api_key_env':'WORKER_TEST_KEY'}),encoding='utf-8')
            with mock.patch.dict(os.environ,{'WORKER_TEST_KEY':'VERY_SECRET_VALUE'},clear=False):
                self.assertEqual(mod.credential_env_names(path),['WORKER_TEST_KEY'])
                self.assertNotIn('VERY_SECRET_VALUE',mod.credential_env_names(path))

    def test_project_state_path_uses_stable_marker_id(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); project=base/'project'; marker=project/'.io-delegation/project.json'
            marker.parent.mkdir(parents=True); marker.write_text(json.dumps({'project_id':'abc123abc123'}),encoding='utf-8')
            home=base/'home'; states=home/'projects'; states.mkdir(parents=True)
            expected=states/'abc123abc123.json'; expected.write_text('{}',encoding='utf-8')
            with mock.patch.dict(os.environ,{'IO_DELEGATION_HOME':str(home)},clear=False):
                self.assertEqual(mod.project_state_path(project),expected)

    def test_output_must_be_new(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); out=base/'out'; out.mkdir(); (out/'x').write_text('x')
            worker=base/'worker.json'; worker.write_text('{}')
            with self.assertRaisesRegex(ValueError,'new/empty'):
                mod.execute('cursor',worker,out)

if __name__=='__main__': unittest.main()
