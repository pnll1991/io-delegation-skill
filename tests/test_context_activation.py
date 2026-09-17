import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/io-delegation/scripts/context_activation.py'
spec = importlib.util.spec_from_file_location('context_activation', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'src').mkdir()
        self.state = {'allow_prefixes': ['src'], 'allow_files': [], 'activation_mode': 'auto'}

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, size=20):
        (self.root / 'src' / name).write_text('x' * size, encoding='utf-8')

    def test_small_scope_bypasses(self):
        self.write('a.py', 100)
        row = mod.decide(self.root, self.state)
        self.assertEqual(row['decision'], 'bypass')
        self.assertEqual(row['reason'], 'small_scope')

    def test_large_file_enables(self):
        self.write('a.py', 140_000)
        row = mod.decide(self.root, self.state)
        self.assertEqual(row['decision'], 'enable')
        self.assertIn(row['reason'], ('large_file', 'minified_source'))

    def test_many_files_enable(self):
        for index in range(12):
            self.write(f'f{index}.py', 100)
        row = mod.decide(self.root, self.state)
        self.assertEqual(row['decision'], 'enable')
        self.assertEqual(row['reason'], 'broad_scope')

    def test_trivial_hint_bypasses_large_repo(self):
        for index in range(20):
            self.write(f'f{index}.py', 1000)
        row = mod.decide(self.root, self.state, 'run the tests')
        self.assertEqual(row['decision'], 'bypass')
        self.assertEqual(row['reason'], 'trivial_task_hint')

    def test_multifile_hint_enables_small_repo(self):
        self.write('a.py', 100)
        row = mod.decide(self.root, self.state, 'compare all modules across the repo')
        self.assertEqual(row['decision'], 'enable')
        self.assertEqual(row['reason'], 'multi_file_task_hint')

    def test_security_is_flag_not_delegation(self):
        self.write('a.py', 100)
        row = mod.decide(self.root, self.state, 'security review credentials')
        self.assertTrue(row['principal_intent'])
        self.assertEqual(row['decision'], 'bypass')

    def test_force_overrides(self):
        self.write('a.py', 100)
        self.assertEqual(mod.decide(self.root, self.state, force='on')['decision'], 'enable')
        self.assertEqual(mod.decide(self.root, self.state, force='off')['decision'], 'bypass')

    def test_boundary_configurable(self):
        self.state['activation_limits'] = {'small_files': 1, 'small_bytes': 10}
        self.write('a.py', 20)
        self.assertEqual(mod.decide(self.root, self.state)['decision'], 'enable')


if __name__ == '__main__':
    unittest.main()
