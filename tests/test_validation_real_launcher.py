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
        self.assertTrue(out['cmd'][0].endswith('benchmarks/v1-validation/validators.py'))

    def test_missing_env_is_error(self):
        with self.assertRaisesRegex(ValueError,'MISSING_X'):
            mod.expand('${MISSING_X}',{})

if __name__=='__main__': unittest.main()
