import importlib.util
from pathlib import Path
import unittest

BASE=Path(__file__).resolve().parents[1]/'benchmarks/v1-validation'
spec=importlib.util.spec_from_file_location('v1_preflight_real',BASE/'preflight_real.py')
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class RealPreflightTests(unittest.TestCase):
    def test_minimum_mix_is_twenty(self):
        self.assertEqual(sum(mod.MIN_FAMILIES.values()),20)

    def test_validator_namespace_parses_repeatable_flags(self):
        cmd=['python',str(BASE/'validators.py'),'--root','.','--output','benchmark-output/x.json','--mode','literal-files','--literal','abc','--ext','.ts','--ext','.tsx']
        ns=mod.validator_namespace(cmd,Path('/tmp/example'))
        self.assertEqual(ns.mode,'literal-files'); self.assertEqual(ns.literal,'abc')
        self.assertEqual(ns.ext,['.ts','.tsx']); self.assertEqual(ns.output,'benchmark-output/x.json')

if __name__=='__main__': unittest.main()
