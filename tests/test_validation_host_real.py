import importlib.util
from pathlib import Path
import tempfile
import unittest

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

    def test_remove_purges_fixture_data_not_runtime(self):
        cmd=mod.remove_command(Path('P'))
        self.assertIn('--purge-data',cmd)
        self.assertNotIn('--purge-runtime',cmd)

    def test_host_validate_command_uses_common_suite(self):
        cmd=mod.host_validate_command('cursor',Path('P'),BASE/'host_suite.fixture.json',Path('O'))
        text=' '.join(map(str,cmd))
        self.assertIn('host_validate.py',text)
        self.assertIn('host_suite.fixture.json',text)
        self.assertIn('--host cursor',text)

    def test_output_must_be_new(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); out=base/'out'; out.mkdir(); (out/'x').write_text('x')
            worker=base/'worker.json'; worker.write_text('{}')
            with self.assertRaisesRegex(ValueError,'new/empty'):
                mod.execute('cursor',worker,out)

if __name__=='__main__': unittest.main()
