from __future__ import annotations
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'benchmarks/typesafe-router/run.py'
spec = importlib.util.spec_from_file_location('typesafe_router_benchmark', SCRIPT)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


class TypeSafeRouterBenchmarkTests(unittest.TestCase):
    def test_cases_are_valid_and_reference_existing_files(self):
        cases = bench.load_cases(ROOT / 'benchmarks/typesafe-router/cases.json')
        self.assertGreaterEqual(len(cases), 4)
        for case in cases:
            with self.subTest(case=case['id']):
                for path in case['paths']:
                    self.assertTrue((ROOT / path).is_file(), path)

    def test_dry_run_executes_all_cases_without_api_key(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'router.json'
            config.write_text(json.dumps({"version": 1, "approved": True}))
            out = io.StringIO()
            with mock.patch('sys.stdout', out), mock.patch.dict('os.environ', {}, clear=True):
                code = bench.main(['--root', str(ROOT), '--config', str(config), '--dry-run'])
        self.assertEqual(code, 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report['summary']['mode'], 'dry_run')
        self.assertEqual(report['summary']['cases'], len(report['results']))
        self.assertTrue(all(row['status'] == 'dry_run' for row in report['results']))


if __name__ == '__main__':
    unittest.main()
