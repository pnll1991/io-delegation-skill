import importlib.util
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / 'benchmarks/v1-validation'
record_spec = importlib.util.spec_from_file_location('record', BASE / 'record.py')
record = importlib.util.module_from_spec(record_spec)
record_spec.loader.exec_module(record)
import sys
sys.modules['record'] = record
spec = importlib.util.spec_from_file_location('paired', BASE / 'paired.py')
paired = importlib.util.module_from_spec(spec)
spec.loader.exec_module(paired)


def row(run_id, arm, tokens=100, wall=1000, router=False, worker=0, worker_tokens=None, success=True):
    return {
        'schema': record.SCHEMA,
        'run_id': run_id,
        'task_id': 't1',
        'family': 'multi-file-factual',
        'repo': 'r',
        'commit': 'abc',
        'arm': arm,
        'host': 'codex',
        'success': success,
        'principal': {'raw_tokens': tokens, 'usage': None},
        'router': {'called': router, 'usage': None, 'latency_ms': None},
        'worker': {'calls': worker, 'accepted': worker, 'rejected': 0, 'usage': None, 'raw_tokens': worker_tokens},
        'context': {'source_bytes': 1000, 'selected_bytes': 100, 'result_bytes': 100},
        'timing': {'wall_ms': wall},
        'validator': {'status': 'pass' if success else 'fail'},
        'rework': 0,
        'errors': [],
    }


class PairedTests(unittest.TestCase):
    def test_pairing_and_delta(self):
        rows = [row('001-t-control-r1', 'control', 100), row('002-t-gateway-r1', 'gateway', 80, router=True)]
        out = paired.summarize(rows, 'control', 'gateway')
        family = out['families']['multi-file-factual']
        self.assertEqual(family['principal_token_delta']['median'], -20)
        self.assertAlmostEqual(family['principal_token_delta_pct']['median'], -20)
        self.assertEqual(family['jev_called_pairs'], 1)

    def test_agent_system_delta_includes_worker_only_when_called_and_accounted(self):
        rows=[
            row('001-t-control-r1','control',tokens=100),
            row('002-t-gateway-r1','gateway',tokens=70,router=True,worker=1,worker_tokens=20),
        ]
        family=paired.summarize(rows,'control','gateway')['families']['multi-file-factual']
        self.assertEqual(family['principal_token_delta']['median'],-30)
        self.assertEqual(family['agent_system_token_delta']['median'],-10)
        self.assertAlmostEqual(family['agent_system_token_delta_pct']['median'],-10)

    def test_unknown_worker_accounting_keeps_system_delta_unknown(self):
        rows=[
            row('001-t-control-r1','control',tokens=100),
            row('002-t-gateway-r1','gateway',tokens=70,router=True,worker=1,worker_tokens=None),
        ]
        family=paired.summarize(rows,'control','gateway')['families']['multi-file-factual']
        self.assertEqual(family['agent_system_token_delta']['n'],0)

    def test_missing_pair_excluded(self):
        out = paired.summarize([row('001-t-control-r1', 'control')], 'control', 'gateway')
        self.assertEqual(out['pair_count'], 0)
        self.assertEqual(out['incomplete_pair_count'], 1)

    def test_no_router_warning(self):
        rows = [row('001-t-control-r1', 'control'), row('002-t-gateway-r1', 'gateway', 90)]
        out = paired.summarize(rows, 'control', 'gateway')
        self.assertTrue(any('never called Jev' in warning for warning in out['warnings']))

    def test_no_worker_warning(self):
        rows = [row('001-t-control-r1', 'control'), row('002-t-gateway-r1', 'gateway', 90, router=True)]
        out = paired.summarize(rows, 'control', 'gateway')
        self.assertTrue(any('never called the worker' in warning for warning in out['warnings']))

    def test_failed_pair_not_in_efficiency_delta(self):
        rows = [row('001-t-control-r1', 'control'), row('002-t-gateway-r1', 'gateway', 90, success=False)]
        out = paired.summarize(rows, 'control', 'gateway')
        family = out['families']['multi-file-factual']
        self.assertEqual(family['valid_pairs'], 0)
        self.assertEqual(family['principal_token_delta']['n'], 0)


if __name__ == '__main__':
    unittest.main()
