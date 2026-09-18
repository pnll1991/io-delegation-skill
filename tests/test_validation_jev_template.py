import importlib.util
import json
from pathlib import Path
import unittest

BASE=Path(__file__).resolve().parents[1]/'benchmarks/v1-validation'
script=BASE/'experiment.py'
spec=importlib.util.spec_from_file_location('v1_experiment_for_jev_template',script)
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class JevTemplateTests(unittest.TestCase):
    def setUp(self):
        self.path=BASE/'jev.real.template.json'
        self.data=json.loads(self.path.read_text(encoding='utf-8'))

    def test_manifest_shape_and_run_count(self):
        mod.validate_manifest(self.data)
        self.assertEqual(self.data['repetitions'],5)
        self.assertEqual(len(self.data['tasks']),4)
        self.assertEqual(len(mod.plan(self.data)),40)
        self.assertEqual({task['family'] for task in self.data['tasks']},{'targeted','multi-file-factual','principal','small-control'})

    def test_arms_differ_only_in_jev_and_name(self):
        left,right=self.data['arms']
        self.assertFalse(left['jev']); self.assertTrue(right['jev'])
        for key in ('gateway','worker','activation'):
            self.assertEqual(left[key],right[key])
        self.assertTrue(left['worker']); self.assertTrue(left['gateway'])

    def test_activated_cases_require_one_query_and_small_bypasses(self):
        by_id={task['id']:task for task in self.data['tasks']}
        for task_id in ('jev-targeted-package','jev-multifile-ipc','jev-security-server'):
            prompt=by_id[task_id]['prompt']
            self.assertIn('io_context.query exactly once',prompt)
            self.assertNotEqual(by_id[task_id]['expected_route'],'bypass')
        small=by_id['jev-small-negative']
        self.assertEqual(small['expected_route'],'bypass')
        self.assertEqual(small['allow_files'],['package.json'])

    def test_template_contains_no_machine_local_paths(self):
        text=self.path.read_text(encoding='utf-8')
        self.assertNotIn('D:\\',text)
        for name in ('CAMARA_REPO','KUATROMETRIC_REPO','STEROID_REPO','IO_ROUTER_CONFIG','IO_WORKER_CONFIG'):
            self.assertIn('${'+name+'}',text)

if __name__=='__main__': unittest.main()
