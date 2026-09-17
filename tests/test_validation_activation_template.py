import json
from pathlib import Path
import unittest

BASE=Path(__file__).resolve().parents[1]/'benchmarks/v1-validation'
TEMPLATE=BASE/'activation.real.template.json'

class ActivationTemplateTests(unittest.TestCase):
    def setUp(self):
        self.data=json.loads(TEMPLATE.read_text(encoding='utf-8'))

    def test_shape_and_run_count(self):
        self.assertEqual(self.data['version'],1)
        self.assertEqual(self.data['repetitions'],5)
        self.assertEqual(len(self.data['tasks']),3)
        self.assertEqual(len(self.data['arms']),2)
        self.assertEqual(3*2*5,30)

    def test_arms_isolate_activation_only(self):
        auto,always=self.data['arms']
        self.assertEqual((auto['jev'],auto['worker']),(False,False))
        self.assertEqual((always['jev'],always['worker']),(False,False))
        a={k:v for k,v in auto.items() if k not in ('name','activation')}
        b={k:v for k,v in always.items() if k not in ('name','activation')}
        self.assertEqual(a,b)
        self.assertEqual({auto['activation'],always['activation']},{'auto','always'})

    def test_tasks_are_held_out_small_controls(self):
        ids={task['id'] for task in self.data['tasks']}
        self.assertEqual(ids,{'activation-camara-name','activation-steroid-description','activation-kuatrometric-port'})
        for task in self.data['tasks']:
            self.assertEqual(task['family'],'small-control')
            self.assertEqual(len(task.get('allow_files',[])),1)
            self.assertFalse(task.get('allow_prefixes'))
            self.assertTrue(task.get('task_hint'))

    def test_template_is_portable_and_typesafe_free(self):
        text=TEMPLATE.read_text(encoding='utf-8')
        self.assertNotIn('D:\\',text)
        self.assertNotIn('TYPESAFE',text)
        self.assertNotIn('router_config',self.data)
        self.assertNotIn('worker_config',self.data)

if __name__=='__main__': unittest.main()
