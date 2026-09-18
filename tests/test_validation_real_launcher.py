import importlib.util
from pathlib import Path
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'benchmarks/v1-validation/run_real.py'
spec=importlib.util.spec_from_file_location('v1_real',SCRIPT)
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class RealLauncherTests(unittest.TestCase):
    def test_expands_env_and_validation_root(self):
        value={'repo':'${REPO_ROOT}','cmd':['{validation_root}/validators.py','${REPO_ROOT}']}
        out=mod.expand(value,{'REPO_ROOT':'/tmp/repo'})
        self.assertEqual(out['repo'],'/tmp/repo')
        self.assertEqual(out['cmd'][1],'/tmp/repo')
        validator_path=out['cmd'][0].replace('\\','/')
        self.assertTrue(validator_path.endswith('benchmarks/v1-validation/validators.py'))

    def test_prepare_injects_authorized_scope_into_validator(self):
        value={
            'tasks':[{
                'allow_prefixes':['src','lib'],
                'allow_files':['package.json'],
                'validator':[['{validation_root}/validators.py','--root','.','--output','x.json','--mode','extension-counts']],
            }]
        }
        out=mod.prepare(value,{})
        command=out['tasks'][0]['validator'][0]
        self.assertEqual(command.count('--scope-prefix'),2)
        self.assertEqual(command.count('--scope-file'),1)
        self.assertIn('src',command); self.assertIn('lib',command); self.assertIn('package.json',command)
        again=mod.inject_scope_flags(out)
        self.assertEqual(again['tasks'][0]['validator'][0],command)

    def test_missing_env_is_error(self):
        with self.assertRaisesRegex(ValueError,'MISSING_X'):
            mod.expand('${MISSING_X}',{})

if __name__=='__main__': unittest.main()
