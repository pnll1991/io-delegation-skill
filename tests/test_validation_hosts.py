import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / 'benchmarks/v1-validation'
record_spec = importlib.util.spec_from_file_location('record', BASE / 'record.py')
record = importlib.util.module_from_spec(record_spec)
record_spec.loader.exec_module(record)
sys.modules['record'] = record
spec = importlib.util.spec_from_file_location('host_validate', BASE / 'host_validate.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class HostValidationTests(unittest.TestCase):
    def test_claude_command_is_headless_without_skip_permissions(self):
        command = mod.build_command('claude', '.', 'hello', model='sonnet')
        text = ' '.join(command)
        self.assertIn('-p', command)
        self.assertIn('--output-format', command)
        self.assertIn('--allowedTools', command)
        self.assertNotIn('--dangerously-skip-permissions', command)
        self.assertIn('mcp__io_context__query', text)

    def test_cursor_command_is_headless(self):
        command = mod.build_command('cursor', '.', 'hello')
        self.assertIn('-p', command)
        self.assertIn('--output-format', command)
        self.assertIn('--workspace', command)
        self.assertNotIn('--approve-mcps', command)

    def test_host_mcp_checks(self):
        self.assertEqual(mod.mcp_check_command('claude')[1:4], ['mcp', 'get', 'io_context'])
        self.assertEqual(mod.mcp_check_command('cursor')[1:4], ['mcp', 'list-tools', 'io_context'])

    def test_suite_needs_four_cases(self):
        with self.assertRaises(ValueError):
            mod.validate_suite({'version': 1, 'cases': []})

    def test_suite_duplicate_ids_rejected(self):
        case = {'id': 'x', 'prompt': 'p', 'validator': [['python', '-c', 'pass']], 'expected_route':'principal'}
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            mod.validate_suite({'version': 1, 'cases': [case, case, case, case]})

    def test_suite_requires_expected_route(self):
        cases=[{'id':str(i),'prompt':'p','validator':[['python','-c','pass']]} for i in range(4)]
        with self.assertRaisesRegex(ValueError,'expected_route'):
            mod.validate_suite({'version':1,'cases':cases})

    def test_usage_parser_unknown_is_none(self):
        self.assertIsNone(mod.parse_usage(b'{"result":"ok"}'))

    def test_usage_parser_finds_nested_usage(self):
        usage = mod.parse_usage(b'{"result":{"usage":{"input_tokens":10,"output_tokens":2}}}')
        self.assertEqual(usage['input_tokens'], 10)
        self.assertEqual(usage['output_tokens'], 2)

    def test_observed_context_comes_from_new_audit_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            audit=Path(folder); (audit/'.io-delegation').mkdir()
            before=mod.audit_snapshot(audit)
            rows=[
                {'schema':'io-context/v1','operation_id':'a','event':'operation_completed','operation':'extract','status':'ok','source_bytes':50,'selected_bytes':0,'result_bytes':20,'model_calls':0},
                {'schema':'io-context/v1','operation_id':'b','event':'operation_completed','operation':'query','status':'ok','route':'principal','source_bytes':100,'selected_bytes':30,'result_bytes':40,'model_calls':0,'router_calls':1,'router_route':'principal','router_confidence':0.9,'router_input_tokens':10,'router_output_tokens':2,'router_elapsed_ms':7},
            ]
            (audit/'context-events.jsonl').write_text('\n'.join(json.dumps(row) for row in rows)+'\n',encoding='utf-8')
            observed=mod.observed_context(audit,before)
            self.assertEqual(observed['route'],'principal')
            self.assertTrue(observed['router_called'])
            self.assertEqual(observed['router_usage']['input_tokens'],10)
            self.assertEqual(observed['source_bytes'],150)
            self.assertEqual(observed['selected_bytes'],30)
            self.assertEqual(observed['malformed_delta'],0)

    def test_no_context_operation_is_observed_principal(self):
        with tempfile.TemporaryDirectory() as folder:
            audit=Path(folder); (audit/'.io-delegation').mkdir()
            before=mod.audit_snapshot(audit)
            observed=mod.observed_context(audit,before)
            self.assertEqual(observed['route'],'principal')
            self.assertFalse(observed['router_called'])


if __name__ == '__main__':
    unittest.main()
