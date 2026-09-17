import importlib.util
import tempfile
import unittest
from pathlib import Path

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

    def test_unsafe_run_id_rejected(self):
        with self.assertRaises(ValueError):
            mod.safe_run_id('../escape')


if __name__ == '__main__':
    unittest.main()
