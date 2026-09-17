from collections import Counter
import importlib.util
import json
from pathlib import Path
import unittest

BASE=Path(__file__).resolve().parents[1]/'benchmarks/v1-validation'
spec=importlib.util.spec_from_file_location('v1_experiment',BASE/'experiment.py')
experiment=importlib.util.module_from_spec(spec); spec.loader.exec_module(experiment)

class DogfoodTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=json.loads((BASE/'dogfood.real.template.json').read_text(encoding='utf-8'))

    def test_manifest_shape_and_mix(self):
        experiment.validate_manifest(self.data)
        counts=Counter(x['family'] for x in self.data['tasks'])
        self.assertEqual(len(self.data['tasks']),20); self.assertEqual(len(self.data['repositories']),3)
        self.assertEqual(counts,{'large-understanding':5,'multi-file-factual':5,'cross-file-behavior':4,'principal':3,'small-control':3})

    def test_paths_are_environment_placeholders(self):
        text=json.dumps(self.data)
        self.assertIn('${CAMARA_REPO}',text); self.assertIn('${IO_ROUTER_CONFIG}',text)
        self.assertNotIn('D:\\\\',text); self.assertNotIn('/Users/',text); self.assertNotIn('/home/',text)

    def test_unique_outputs_and_scopes(self):
        outputs=[]
        for task in self.data['tasks']:
            self.assertTrue(task.get('allow_files') or task.get('allow_prefixes'))
            cmd=task['validator'][0]; self.assertIn('{validation_root}/validators.py',cmd)
            outputs.append(cmd[cmd.index('--output')+1])
        self.assertEqual(len(outputs),len(set(outputs)))

if __name__=='__main__': unittest.main()
