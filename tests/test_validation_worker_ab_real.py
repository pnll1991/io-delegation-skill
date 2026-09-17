import importlib.util
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'benchmarks/v1-validation'
TEMPLATE=BASE/'worker_ab.real.template.json'
SPEC=importlib.util.spec_from_file_location('worker_ab_real',BASE/'worker_ab_real.py')
mod=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(mod)

class WorkerABRealTests(unittest.TestCase):
    def setUp(self):
        self.data=json.loads(TEMPLATE.read_text(encoding='utf-8'))

    def test_shape_is_five_tasks_five_repetitions(self):
        self.assertEqual(self.data['version'],1)
        self.assertEqual(self.data['repetitions'],5)
        self.assertEqual(len(self.data['tasks']),5)
        self.assertEqual(len({t['id'] for t in self.data['tasks']}),5)

    def test_template_is_portable_and_jev_free(self):
        raw=TEMPLATE.read_text(encoding='utf-8')
        self.assertIn('${IO_WORKER_CONFIG}',raw)
        self.assertNotIn('TYPESAFE',raw)
        self.assertNotIn('D:\\',raw)
    def test_tasks_have_bounded_literal_selections(self):
        for task in self.data['tasks']:
            with self.subTest(task=task['id']):
                self.assertGreaterEqual(len(task['selections']),3)
                self.assertLessEqual(len(task['selections']),12)
                self.assertEqual(set(task['allow_files']),{s['path'] for s in task['selections']})
                for selection in task['selections']:
                    self.assertEqual(selection['select']['kind'],'literal')
                    self.assertNotIn('[',selection['path'])
                    self.assertNotIn(']',selection['path'])

    def test_expansion_keeps_validation_root_local(self):
        value={'worker':'${IO_WORKER_CONFIG}','validator':'{validation_root}/validators.py'}
        out=mod.expand(value,{'IO_WORKER_CONFIG':'/tmp/worker.json'})
        self.assertEqual(out['worker'],'/tmp/worker.json')
        self.assertTrue(out['validator'].replace('\\','/').endswith('benchmarks/v1-validation/validators.py'))

    def test_missing_environment_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'IO_WORKER_CONFIG'):
            mod.expand('${IO_WORKER_CONFIG}',{})

if __name__=='__main__': unittest.main()
