import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'benchmarks/v1-validation/orchestration_ab.py'
spec=importlib.util.spec_from_file_location('orchestration_ab',SCRIPT)
ab=importlib.util.module_from_spec(spec);spec.loader.exec_module(ab)


def pair(i,baseline=1000,orchestrated=700,quality=True,tier='T1',escalated=False):
    return dict(id=f'p{i}',family='fixture',
        baseline=dict(quality_pass=quality,accounting_complete=True,
                      principal_tokens=baseline,worker_tokens=0,control_tokens=0,wall_ms=100),
        orchestrated=dict(quality_pass=quality,accounting_complete=True,
                          principal_tokens=500,worker_tokens=max(0,orchestrated-550),
                          control_tokens=50,wall_ms=90),
        tier=tier,escalated=escalated,initial_profile='luna-medium',
        final_profile='luna-high' if escalated else 'luna-medium',
        model_attempts=2 if escalated else 1)


class OrchestrationABTests(unittest.TestCase):
    def test_release_gate_passes_only_with_broad_clean_savings(self):
        data=dict(schema=ab.SCHEMA,pairs=[pair(i) for i in range(20)])
        result=ab.evaluate(data)
        self.assertTrue(result['release_gate']['pass'])
        self.assertAlmostEqual(result['median_system_token_delta'],-.3)
        self.assertEqual(result['tiers']['T1'],20)

    def test_quality_regression_blocks_release(self):
        rows=[pair(i) for i in range(20)]
        rows[0]['orchestrated']['quality_pass']=False
        result=ab.evaluate(dict(schema=ab.SCHEMA,pairs=rows))
        self.assertFalse(result['release_gate']['pass'])
        self.assertEqual(result['quality_regressions'],['p0'])

    def test_unknown_usage_is_never_free(self):
        rows=[pair(i) for i in range(20)]
        rows[2]['orchestrated']['accounting_complete']=False
        rows[2]['orchestrated']['principal_tokens']=None
        result=ab.evaluate(dict(schema=ab.SCHEMA,pairs=rows))
        self.assertFalse(result['release_gate']['pass'])
        self.assertEqual(result['accounting_incomplete'],['p2'])

    def test_escalations_and_tiers_reported(self):
        rows=[pair(0,tier='T1'),pair(1,tier='T2',escalated=True)]
        result=ab.evaluate(dict(schema=ab.SCHEMA,pairs=rows))
        self.assertEqual(result['tiers'],{'T0':0,'T1':1,'T2':1})
        self.assertEqual(result['escalation_rate'],.5)
        self.assertEqual(result['final_profiles'],{'luna-medium':1,'luna-high':1})
        self.assertEqual(result['model_attempts'],3)

    def test_invalid_or_duplicate_pairs_rejected(self):
        row=pair(0)
        with self.assertRaises(ab.EvidenceError):
            ab.evaluate(dict(schema=ab.SCHEMA,pairs=[row,row]))
        bad=pair(1);bad['orchestrated']['control_tokens']=-1
        with self.assertRaises(ab.EvidenceError):
            ab.evaluate(dict(schema=ab.SCHEMA,pairs=[bad]))


if __name__=='__main__':unittest.main()
