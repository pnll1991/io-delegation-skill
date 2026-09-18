import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / 'benchmarks/v1-validation'
for name in ('record', 'experiment'):
    spec = importlib.util.spec_from_file_location(name, BASE / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[name] = module
record = sys.modules['record']
experiment = sys.modules['experiment']
spec = importlib.util.spec_from_file_location('validation_run', BASE / 'run.py')
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


def manifest(root):
    return {
        'version': 1,
        'seed': 7,
        'repetitions': 2,
        'repositories': {'r': {'path': str(root), 'commit': 'HEAD'}},
        'arms': [{'name': 'control', 'gateway': False, 'jev': False, 'worker': False}],
        'tasks': [{
            'id': 't1', 'family': 'small-control', 'repo': 'r',
            'prompt': 'run the tests', 'allow_files': ['a.py'],
            'validator': [['python', '-c', 'raise SystemExit(0)']],
            'expected_route': 'bypass',
        }],
    }


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        (self.root / 'a.py').write_text('x=1\n', encoding='utf-8')
        self.output = Path(self.temp.name) / 'out'
        self.data = manifest(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_plan_ids_are_stable(self):
        first, _ = run.planned_rows(self.data)
        second, _ = run.planned_rows(self.data)
        self.assertEqual(first, second)
        self.assertEqual(first[0]['run_id'], '0001-t1-control-r1')
        self.assertEqual(first[1]['run_id'], '0002-t1-control-r2')

    def test_initialize_and_verify_resume(self):
        rows, _ = run.planned_rows(self.data)
        run.initialize_output(self.output, self.data, rows)
        run.verify_resume(self.output, self.data, rows)

    def test_changed_manifest_refuses_resume(self):
        rows, _ = run.planned_rows(self.data)
        run.initialize_output(self.output, self.data, rows)
        changed = json.loads(json.dumps(self.data))
        changed['seed'] = 8
        with self.assertRaisesRegex(ValueError, 'manifest differs'):
            run.verify_resume(self.output, changed, rows)

    def test_changed_plan_refuses_resume(self):
        rows, _ = run.planned_rows(self.data)
        run.initialize_output(self.output, self.data, rows)
        changed = list(rows)
        changed[0] = dict(changed[0], run_id='different')
        with self.assertRaisesRegex(ValueError, 'plan differs'):
            run.verify_resume(self.output, self.data, changed)

    def test_interrupted_directory_is_archived(self):
        path = self.output / 'runs' / '0001-t1-control-r1'
        path.mkdir(parents=True)
        (path / 'partial.txt').write_text('keep', encoding='utf-8')
        target = run.archive_interrupted(self.output, '0001-t1-control-r1')
        self.assertFalse(path.exists())
        self.assertEqual((target / 'partial.txt').read_text(encoding='utf-8'), 'keep')

    def test_schema_only_planning_does_not_need_git(self):
        experiment.validate_manifest(self.data)
        rows, _ = run.planned_rows(self.data)
        self.assertEqual(len(rows), 2)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
