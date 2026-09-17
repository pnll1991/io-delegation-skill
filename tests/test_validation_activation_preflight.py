import importlib.util
from pathlib import Path
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'benchmarks/v1-validation/activation_preflight.py'
spec=importlib.util.spec_from_file_location('activation_preflight',SCRIPT)
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class ActivationPreflightTests(unittest.TestCase):
    def test_expected_family_contract(self):
        self.assertEqual(mod.expected_decision('small-control'),'bypass')
        for family in ('large-understanding','multi-file-factual','cross-file-behavior'):
            self.assertEqual(mod.expected_decision(family),'enable')
        self.assertIsNone(mod.expected_decision('principal'))

    def test_summary_passes_clean_mix(self):
        rows=[
            {'task_id':'s','family':'small-control','decision':'bypass','expected':'bypass','principal_intent':False},
            {'task_id':'l','family':'large-understanding','decision':'enable','expected':'enable','principal_intent':False},
            {'task_id':'m','family':'multi-file-factual','decision':'enable','expected':'enable','principal_intent':False},
            {'task_id':'c','family':'cross-file-behavior','decision':'enable','expected':'enable','principal_intent':False},
            {'task_id':'p','family':'principal','decision':'enable','expected':None,'principal_intent':True},
        ]
        out=mod.summarize(rows)
        self.assertTrue(out['pass'])
        self.assertEqual(out['small_bypassed'],1)
        self.assertEqual(out['broad_enabled'],3)
        self.assertEqual(out['principal_intent_detected'],1)

    def test_summary_preserves_false_bypass(self):
        rows=[{'task_id':'x','family':'large-understanding','decision':'bypass',
               'expected':'enable','principal_intent':False}]
        out=mod.summarize(rows)
        self.assertFalse(out['pass'])
        self.assertEqual(out['mismatches'],['x'])

    def test_principal_not_scored_as_activation_error(self):
        rows=[{'task_id':'p','family':'principal','decision':'bypass',
               'expected':None,'principal_intent':True}]
        self.assertTrue(mod.summarize(rows)['pass'])

if __name__=='__main__': unittest.main()
