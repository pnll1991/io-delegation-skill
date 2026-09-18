import importlib.util
import unittest
from pathlib import Path

SCRIPT=Path(__file__).resolve().parents[1]/'benchmarks/typesafe-router/calibrate.py'
spec=importlib.util.spec_from_file_location('typesafe_calibrate',SCRIPT)
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class CalibrationTests(unittest.TestCase):
    def test_split_has_one_case_per_route_on_each_side(self):
        cases=mod.bench.load_cases(Path(__file__).resolve().parents[1]/'benchmarks/typesafe-router/cases.json')
        calibration,holdout=mod.split_cases(cases)
        self.assertEqual({x['expected'] for x in calibration},set(mod.router.ROUTES))
        self.assertEqual({x['expected'] for x in holdout},set(mod.router.ROUTES))
        self.assertTrue({x['id'] for x in calibration}.isdisjoint({x['id'] for x in holdout}))

    def test_choose_threshold_prefers_max_error_free_coverage(self):
        rows=[
            {'id':'a','confidence':.96,'model_route':'principal','expected':'principal'},
            {'id':'b','confidence':.90,'model_route':'bulk_read','expected':'bulk_read'},
            {'id':'c','confidence':.70,'model_route':'principal','expected':'targeted_read'},
        ]
        chosen,_=mod.choose_threshold(rows)
        self.assertEqual(chosen['accepted_errors'],0)
        self.assertEqual(chosen['accepted'],2)
        self.assertLessEqual(chosen['threshold'],.90)
        self.assertGreater(chosen['threshold'],.70)

    def test_holdout_score_reports_errors_without_retuning(self):
        rows=[
            {'id':'x','confidence':.91,'model_route':'bulk_read','expected':'principal'},
            {'id':'y','confidence':.80,'model_route':'targeted_read','expected':'targeted_read'},
        ]
        score=mod.score(rows,.85)
        self.assertEqual(score['accepted'],1)
        self.assertEqual(score['accepted_errors'],1)
        self.assertEqual(score['error_ids'],['x'])

    def test_usage_summary_keeps_domains_separate(self):
        rows=[{'usage':{'input_tokens':10,'output_tokens':2},'elapsed_ms':5},
              {'usage':{'input_tokens':20,'output_tokens':3},'elapsed_ms':7}]
        out=mod.usage_summary(rows)
        self.assertEqual(out['input_tokens'],30)
        self.assertEqual(out['output_tokens'],5)
        self.assertEqual(out['median_latency_ms'],6)

if __name__=='__main__': unittest.main()
