from __future__ import annotations
import ast
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('benchmark', ROOT / 'benchmarks/run.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class BenchmarkTests(unittest.TestCase):
    def test_constants_gold_matches_source(self):
        tree = ast.parse((ROOT / b.SOURCE).read_text(encoding='utf-8'))
        literals = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                literals.update({t.id: node.value.value for t in node.targets if isinstance(t, ast.Name)})
        for name, expected in b.cases()[0]['expected'].items():
            self.assertEqual(literals[name], expected)

    def test_all_selectors_exist(self):
        for case in b.cases():
            self.assertTrue(b.evidence(case))
            for item in b.evidence(case):
                self.assertLessEqual(item['start_line'], item['end_line'])
                self.assertTrue(item['content'])

    def test_exact_source_slices(self):
        case = b.cases()[0]
        self.assertEqual(len(b.evidence(case)), 3)
        self.assertIn('MAX_FILES = 12', b.evidence(case)[0]['content'])
        self.assertNotIn('def invoke', str(b.evidence(case)))

    def test_cold_overhead_is_not_hidden(self):
        case = b.cases()[0]
        cold = b.messages(case, 'skill_cold')
        focused = b.messages(case, 'focused_no_skill')
        self.assertIn((ROOT / b.SKILL).read_text(encoding='utf-8'), cold[0]['content'])
        self.assertEqual(cold[1], focused[1])
        self.assertNotIn('expected', cold[1]['content'])

    def test_already_focused_control_is_identical(self):
        case = b.cases()[-1]
        self.assertEqual(b.messages(case, 'whole_file'), b.messages(case, 'focused_no_skill'))

    def test_strict_quality_gate(self):
        self.assertTrue(b.score('{"a":1}', {'a': 1}))
        self.assertFalse(b.score('{"a":true}', {'a': 1}))
        self.assertFalse(b.score('{"a":1,"extra":2}', {'a': 1}))
        self.assertFalse(b.score('Maybe {"a":1}', {'a': 1}))
        self.assertFalse(b.score('{"a":NaN}', {'a': 1}))

    def test_combined_counts_do_not_drop_failed_calls(self):
        rows = [{'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120, 'passed': False},
                {'input_tokens': 30, 'output_tokens': 5, 'total_tokens': 35, 'passed': True}]
        self.assertEqual(b.totals(rows)['total_tokens'], 155)

    def test_reduction_keeps_negative_results(self):
        self.assertEqual(b.reduction(100, 150), -50.0)
        self.assertIsNone(b.reduction(0, 20))


    def test_worker_assignments_allow_numeric_separators_without_execution(self):
        summary = {'findings': [
            {'symbol': 'MAX_FILES', 'evidence': 'MAX_FILES = 12'},
            {'symbol': 'MAX_SUMMARY_BYTES', 'evidence': 'MAX_SUMMARY_BYTES = 6_000'},
            {'symbol': 'MAX_CODE_BYTES', 'evidence': 'MAX_CODE_BYTES = 64_000'}]}
        self.assertTrue(b.constants_covered(summary, b.cases()[0]['expected']))
        summary['findings'][0]['evidence'] = 'MAX_FILES = 112'
        self.assertFalse(b.constants_covered(summary, b.cases()[0]['expected']))
        summary['findings'][0]['evidence'] = 'MAX_FILES = int("12")'
        self.assertFalse(b.constants_covered(summary, b.cases()[0]['expected']))
        self.assertFalse(b.constants_covered({'findings': []}, b.cases()[0]['expected']))
