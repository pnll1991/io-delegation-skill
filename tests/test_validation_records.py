import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'benchmarks/v1-validation/record.py'
spec = importlib.util.spec_from_file_location('v1_record', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def row(run_id='r1', arm='gateway'):
    return {
        'schema': mod.SCHEMA,
        'run_id': run_id,
        'task_id': 't1',
        'family': 'targeted',
        'repo': 'x/y',
        'commit': 'abc',
        'arm': arm,
        'host': 'codex',
        'started_at': 1.0,
        'ended_at': 2.0,
        'success': True,
        'activation': {'decision': 'enable', 'reason': 'test', 'signals': {}},
        'route': {'effective': 'targeted_read', 'confidence': None, 'fallback': False},
        'principal': {
            'model': 'm',
            'usage': {
                'input_tokens': 10,
                'output_tokens': 2,
                'cached_input_tokens': None,
                'cache_write_input_tokens': None,
                'reasoning_output_tokens': None,
            },
            'raw_tokens': 12,
        },
        'router': {'called': False, 'usage': None, 'latency_ms': None},
        'worker': {
            'calls': 0, 'accepted': 0, 'rejected': 0,
            'usage': None, 'raw_tokens': None,
        },
        'context': {'source_bytes': 100, 'selected_bytes': 20, 'result_bytes': 20},
        'timing': {'wall_ms': 1000},
        'validator': {'status': 'pass', 'exit_code': 0},
        'conditions': {
            'principal_process': 'cold', 'context_cache': 'cold',
            'provider_cache': 'reported', 'worktree': 'fresh',
        },
        'rework': 0,
        'errors': [],
        'notes': '',
        'tags': [],
    }


class RecordTests(unittest.TestCase):
    def test_valid_roundtrip_and_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'runs.jsonl'
            mod.append(path, row('r1'))
            mod.append(path, row('r2', 'control'))
            rows = mod.load(path)
            self.assertEqual(len(rows), 2)
            summary = mod.summarize(rows)
            self.assertEqual(summary['runs'], 2)
            self.assertEqual(summary['overall']['gateway']['principal_tokens']['median'], 12)
            self.assertAlmostEqual(summary['overall']['gateway']['selected_source_ratio']['median'], .2)
            self.assertEqual(summary['overall']['gateway']['conditions']['principal_process']['cold'],1)
            self.assertEqual(summary['overall']['gateway']['conditions']['worktree']['fresh'],1)

    def test_unknown_tokens_remain_null(self):
        value = row()
        value['principal']['raw_tokens'] = None
        value['principal']['usage'] = None
        mod.validate(value)
        summary = mod.summarize([value])['overall']['gateway']
        self.assertIsNone(summary['principal_tokens']['median'])
        self.assertEqual(summary['unknown_principal_usage'], 1)

    def test_duplicate_ids_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'runs.jsonl'
            mod.append(path, row())
            mod.append(path, row())
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                mod.load(path)

    def test_forbidden_source_content_rejected(self):
        value = row()
        value['context']['source_text'] = 'secret'
        with self.assertRaisesRegex(ValueError, 'Forbidden'):
            mod.validate(value)

    def test_fake_secret_redacted(self):
        secret = 'FAKE_SECRET_ABC123'
        value = row()
        value['notes'] = 'token=' + secret
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'runs.jsonl'
            mod.append(path, value, [secret])
            text = path.read_text(encoding='utf-8')
            self.assertNotIn(secret, text)
            self.assertIn('[REDACTED]', text)

    def test_bad_token_value_rejected(self):
        value = row()
        value['principal']['usage']['input_tokens'] = -1
        with self.assertRaises(ValueError):
            mod.validate(value)

    def test_cache_write_usage_is_supported(self):
        value = row()
        value['principal']['usage']['cache_write_input_tokens'] = 3
        mod.validate(value)

    def test_conditions_are_bounded_enums(self):
        value=row(); mod.validate(value)
        value['conditions']['context_cache']='sometimes'
        with self.assertRaisesRegex(ValueError,'conditions.context_cache'):
            mod.validate(value)
        value=row(); value['conditions']['mystery']='x'
        with self.assertRaisesRegex(ValueError,'unknown condition'):
            mod.validate(value)

    def test_missing_conditions_summarize_as_unknown(self):
        value=row(); value.pop('conditions')
        summary=mod.summarize([value])['overall']['gateway']['conditions']
        self.assertEqual(summary['principal_process']['unknown'],1)
        self.assertEqual(summary['context_cache']['unknown'],1)

    def test_selected_source_math_fixture(self):
        value = row()
        value['context'] = {'source_bytes': 1000, 'selected_bytes': 250, 'result_bytes': 100}
        mod.validate(value)
        summary = mod.summarize([value])['overall']['gateway']
        self.assertEqual(value['context']['selected_bytes'] / value['context']['source_bytes'], .25)
        self.assertEqual(summary['selected_source_ratio']['median'], .25)

    def test_zero_source_has_unknown_ratio(self):
        value = row()
        value['context'] = {'source_bytes': 0, 'selected_bytes': 0, 'result_bytes': 0}
        self.assertIsNone(mod.summarize([value])['overall']['gateway']['selected_source_ratio']['median'])

    def test_summary_reports_routes_fallbacks_worker_follow_and_agent_system_tokens(self):
        value=row()
        value['route']['model_route']='bulk_read'
        value['router']['called']=True
        value['worker'].update({'calls':1,'accepted':1,'raw_tokens':5})
        summary=mod.summarize([value])['overall']['gateway']
        self.assertEqual(summary['route_counts'],{'targeted_read':1})
        self.assertEqual(summary['model_route_counts'],{'bulk_read':1})
        self.assertEqual(summary['fallback_rate'],0)
        self.assertEqual(summary['worker_follow_rate'],1)
        self.assertEqual(summary['agent_system_tokens']['median'],17)

    def test_windows_path_serializes(self):
        value = row()
        value['repo'] = r'D:\repo\x'
        self.assertIn('repo', json.dumps(mod.sanitized(value), ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
