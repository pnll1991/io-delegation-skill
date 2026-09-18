import importlib.util
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
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
        command = mod.build_command('claude', '.', 'hello', model='sonnet', mcp_config='mcp.json')
        text = ' '.join(command)
        self.assertIn('-p', command)
        self.assertIn('--output-format', command)
        self.assertIn('--allowedTools', command)
        self.assertIn('--strict-mcp-config', command)
        self.assertIn('--mcp-config', command)
        self.assertIn('--tools', command)
        self.assertEqual(command[command.index('--tools')+1], '')
        self.assertIn('--permission-prompts', command)
        self.assertEqual(command[command.index('--permission-prompts')+1], 'none')
        self.assertIn('--no-session-persistence', command)
        self.assertNotIn('--dangerously-skip-permissions', command)
        self.assertIn('mcp__io_context__query', text)

    def test_cursor_command_is_headless(self):
        command = mod.build_command('cursor', '.', 'hello')
        self.assertIn('-p', command)
        self.assertIn('--output-format', command)
        self.assertIn('--workspace', command)
        self.assertNotIn('--approve-mcps', command)

    def test_cursor_project_config_is_mcp_only_and_cleanup_safe(self):
        old=__import__('os').environ.get('IO_DELEGATION_HOME')
        try:
            with tempfile.TemporaryDirectory() as folder:
                base=Path(folder); project=base/'project'; project.mkdir()
                home=base/'home'; bootstrap=home/'runtime/io-delegation/scripts/context_bootstrap.py'
                bootstrap.parent.mkdir(parents=True); bootstrap.write_text('pass\n',encoding='utf-8')
                __import__('os').environ['IO_DELEGATION_HOME']=str(home)
                paths=mod.managed_cursor_project_config(project)
                mcp=json.loads(paths[0].read_text(encoding='utf-8'))
                cli=json.loads(paths[1].read_text(encoding='utf-8'))
                self.assertEqual(set(mcp['mcpServers']),{'io_context'})
                self.assertEqual(mcp['mcpServers']['io_context']['args'],[str(bootstrap.resolve())])
                self.assertEqual(cli['permissions']['allow'],['Mcp(io_context:*)'])
                self.assertEqual(set(cli['permissions']['deny']),{'Shell(*)','Read(**)','Write(**)','WebFetch(*)'})
                mod.cleanup_cursor_project_config(paths)
                self.assertFalse((project/'.cursor').exists())
        finally:
            if old is None: __import__('os').environ.pop('IO_DELEGATION_HOME',None)
            else: __import__('os').environ['IO_DELEGATION_HOME']=old

    def test_cursor_project_config_refuses_existing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            project=Path(folder); (project/'.cursor').mkdir(); (project/'.cursor/cli.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'refuses to overwrite'):
                mod.managed_cursor_project_config(project)
            self.assertFalse((project/'.cursor/mcp.json').exists())

    def test_host_mcp_checks(self):
        self.assertEqual(mod.mcp_check_command('claude')[1:4], ['mcp', 'get', 'io_context'])
        self.assertEqual(mod.mcp_check_command('cursor')[1:4], ['mcp', 'list-tools', 'io_context'])
        self.assertEqual(mod.mcp_check_command('codex')[1:4], ['mcp', 'list', '--json'])

    def test_host_auth_checks_fail_closed(self):
        self.assertEqual(mod.auth_check_command('claude')[1:], ['auth', 'status', '--json'])
        self.assertEqual(mod.auth_check_command('cursor')[1:], ['status'])
        self.assertEqual(mod.auth_check_command('codex')[1:], ['login', 'status'])
        self.assertTrue(mod.auth_check_ok('claude', 0, '{"loggedIn":true}', ''))
        self.assertFalse(mod.auth_check_ok('claude', 1, '{"loggedIn":false}', ''))
        self.assertFalse(mod.auth_check_ok('cursor', 0, 'Not logged in', ''))
        self.assertTrue(mod.auth_check_ok('cursor', 0, 'Logged in as test@example.invalid', ''))
        self.assertTrue(mod.auth_check_ok('codex', 0, 'Logged in using ChatGPT', ''))
        self.assertFalse(mod.auth_check_ok('codex', 0, 'Not logged in', ''))

    def test_codex_command_is_read_only_and_exclusive_mcp(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); project=base/'project'; marker=project/'.io-delegation/project.json'
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({'project_id':'abc123abc123'}),encoding='utf-8')
            home=base/'home'; runtime=home/'runtime/io-delegation/scripts/context_bootstrap.py'
            runtime.parent.mkdir(parents=True); runtime.write_text('pass\n',encoding='utf-8')
            states=home/'projects'; states.mkdir(parents=True)
            (states/'abc123abc123.json').write_text(json.dumps({
                'project_id':'abc123abc123','audit_root':str(base/'audit'),
                'credential_env_names':['WORKER_KEY']}),encoding='utf-8')
            with patch.dict('os.environ',{'IO_DELEGATION_HOME':str(home)},clear=False):
                command=mod.build_command('codex',project,'hello',model='gpt-test')
            text=' '.join(map(str,command))
            self.assertIn('--ignore-user-config',command)
            self.assertIn('--sandbox',command); self.assertIn('read-only',command)
            self.assertIn('features.shell_tool=false',text)
            self.assertIn('features.multi_agent=false',text)
            self.assertIn('mcp_servers.io_context.command=',text)
            self.assertIn('mcp_servers.io_context.enabled_tools=',text)
            self.assertIn('WORKER_KEY',text)
            self.assertEqual(command[-1],'-')

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


    def test_cursor_command_uses_ask_mode(self):
        command=mod.build_command('cursor','.','hello')
        self.assertIn('--mode',command)
        self.assertEqual(command[command.index('--mode')+1],'ask')

    def test_suite_accepts_read_only_expected_json(self):
        cases=[{'id':str(i),'prompt':'p','expected_json':{'value':i},'expected_route':'principal'} for i in range(4)]
        self.assertEqual(len(mod.validate_suite({'version':1,'cases':cases})['cases']),4)

    def test_answer_validator_parses_claude_result_string(self):
        raw=json.dumps({'type':'result','result':'{"value":7}'}).encode()
        ok,checks=mod.answer_validator(raw,{'value':7})
        self.assertTrue(ok); self.assertEqual(checks[0]['candidate_count'],1)

    def test_answer_validator_parses_nested_cursor_text(self):
        raw=json.dumps({'result':{'message':{'content':'```json\n{"value":7}\n```'}}}).encode()
        ok,_=mod.answer_validator(raw,{'value':7})
        self.assertTrue(ok)

    def test_answer_validator_mismatch_reports_no_answer_content(self):
        secret='DO_NOT_ECHO_THIS'
        raw=json.dumps({'result':'{"secret":"'+secret+'"}'}).encode()
        ok,checks=mod.answer_validator(raw,{'value':7})
        self.assertFalse(ok)
        self.assertNotIn(secret,json.dumps(checks))

    def test_usage_parser_supports_cursor_camel_case(self):
        raw=b'{"usage":{"inputTokens":10,"outputTokens":2,"cacheReadTokens":4,"cacheWriteTokens":1}}'
        usage=mod.parse_usage(raw)
        self.assertEqual(usage['input_tokens'],10)
        self.assertEqual(usage['output_tokens'],2)
        self.assertEqual(usage['cached_input_tokens'],4)
        self.assertEqual(usage['cache_write_input_tokens'],1)

    def test_usage_parser_supports_codex_jsonl(self):
        raw=(b'{"type":"turn.started"}\n'
             b'{"type":"turn.completed","usage":{"input_tokens":30,"output_tokens":4,"cached_input_tokens":12}}\n')
        usage=mod.parse_usage(raw)
        self.assertEqual(usage['input_tokens'],30)
        self.assertEqual(usage['output_tokens'],4)
        self.assertEqual(usage['cached_input_tokens'],12)


    def test_main_fails_when_host_suite_has_failed_cases(self):
        suite={'version':1,'cases':[
            {'id':str(i),'prompt':'p','expected_json':{'value':i},'expected_route':'principal'}
            for i in range(4)
        ]}
        summary={'runs':4,'overall':{'gateway-host':{'success_rate':0.75}}}
        with patch.object(mod,'read_json',return_value=suite), \
             patch.object(mod,'preflight',return_value=(Path('.'),Path('.'))), \
             patch.object(mod,'run_suite',return_value=summary):
            self.assertEqual(mod.main([
                '--host','codex','--project','.','--suite','ignored.json',
                '--output','ignored-output'
            ]),3)

    def test_managed_claude_mcp_config_contains_only_io_context(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder)/'io-home'
            bootstrap=home/'runtime/io-delegation/scripts/context_bootstrap.py'
            bootstrap.parent.mkdir(parents=True); bootstrap.write_text('pass\n',encoding='utf-8')
            target=Path(folder)/'mcp.json'
            with patch.dict('os.environ',{'IO_DELEGATION_HOME':str(home)},clear=False):
                mod.managed_claude_mcp_config(target)
            row=json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual(set(row['mcpServers']),{'io_context'})
            server=row['mcpServers']['io_context']
            self.assertEqual(server['type'],'stdio')
            self.assertEqual(server['args'],[str(bootstrap.resolve())])
            self.assertNotIn('env',server)


if __name__ == '__main__':
    unittest.main()
