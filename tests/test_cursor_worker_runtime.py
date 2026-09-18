"""Offline Cursor CLI worker tests using a fake executable; never call Cursor."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/io-delegation/scripts'))
import io_delegate as d
import worker_runtime as rt

FAKE=r'''#!/usr/bin/env python3
import json,sys,pathlib
args=sys.argv[1:]
if '--version' in args:
 print('cursor-agent TEST-STUB');sys.exit(0)
if '--help' in args:
 print('--print --output-format --model --mode --workspace --sandbox --disable-auto-update');sys.exit(0)
root=pathlib.Path(args[args.index('--workspace')+1])
prompt=(root/'input.txt').read_text()
start=prompt.rfind('{"task"')
if start < 0:
 start=prompt.rfind('{"files"')
data=json.loads(prompt[start:])
f=data['files'][0];v=f['content'].strip()
answer=dict(status='ok',findings=[dict(path=f['path'],symbol='probe',evidence=v,fact=v)],
            unknowns=[],read_paths=[f['path']])
print(json.dumps(dict(type='result',result=json.dumps(answer),
 usage=dict(inputTokens=80,outputTokens=12,cacheReadTokens=20,cacheWriteTokens=4))))
'''

@unittest.skipIf(os.name=='nt','POSIX fake launcher; Windows .cmd construction has unit coverage')
class CursorCLIIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.exe=self.root/'agent';self.exe.write_text(FAKE);self.exe.chmod(0o755)
        self.cfg=dict(approved=True,adapter='cursor-cli',executable=str(self.exe),
                      model='gpt-5.6-luna',cursor_model='gpt-5.6-luna[effort=high]',
                      reasoning_effort='high',timeout_seconds=15)

    def job(self):
        return d.build_job('bulk-read','What is LIMIT?',
                           [dict(path='sample.py',sha256='x',content='LIMIT = 7')])

    def test_cursor_worker_is_ask_mode_isolated_and_accounted(self):
        captured=[];original=rt.run_process
        def spy(argv,**kw):
            if kw.get('cwd') is not None:
                folder=Path(kw['cwd'])
                if (folder/'input.txt').is_file():
                    captured.append(dict(argv=list(argv),prompt=(folder/'input.txt').read_text(),
                                         policy=json.loads((folder/'.cursor/cli.json').read_text())))
            return original(argv,**kw)
        with mock.patch.object(rt,'run_process',side_effect=spy):
            output,metrics=rt.invoke_cursor(self.job(),self.cfg)
        row=captured[-1];command=' '.join(row['argv'])
        self.assertIn('--mode ask',command)
        self.assertIn('--sandbox enabled',command)
        self.assertIn('gpt-5.6-luna[effort=high]',command)
        self.assertNotIn('LIMIT = 7',command)
        self.assertIn('LIMIT = 7',row['prompt'])
        deny=row['policy']['permissions']['deny']
        self.assertIn('Shell(*)',deny);self.assertIn('Write(**)',deny)
        self.assertIn('WebFetch(*)',deny);self.assertIn('Mcp(*:*)',deny)
        self.assertEqual(metrics['usage']['input_tokens'],80)
        self.assertEqual(metrics['usage']['cached_input_tokens'],20)
        self.assertEqual(metrics['usage']['cache_write_input_tokens'],4)
        self.assertIn('"status": "ok"',output)

    def test_cursor_json_parser_accepts_stream_result(self):
        raw=(json.dumps({'type':'thinking','text':'x'})+'\n'+
             json.dumps({'type':'result','result':'OK',
                         'usage':{'inputTokens':3,'outputTokens':6}})+'\n').encode()
        output,usage,row=rt.cursor_result(raw)
        self.assertEqual(output,'OK')
        self.assertEqual(usage['input_tokens'],3)
        self.assertEqual(usage['output_tokens'],6)

    def test_cursor_preflight_requires_safety_flags(self):
        bad=self.root/'bad-agent'
        bad.write_text("#!/usr/bin/env python3\nimport sys\nprint('x')\n");bad.chmod(0o755)
        with self.assertRaises(rt.TransportError):
            rt.cursor_preflight(str(bad))

    def test_windows_cmd_wrapper_does_not_use_shell_true(self):
        with mock.patch.object(rt.os,'name','nt'):
            cmd=rt._script_command(r'C:\\Tools\\agent.cmd',['--version'])
        self.assertIn('/c',cmd)
        self.assertEqual(cmd[-2:], [r'C:\\Tools\\agent.cmd','--version'])


class CursorUsageTests(unittest.TestCase):
    def test_normalize_cursor_camel_case_usage(self):
        usage=rt.normalize_usage(dict(inputTokens=10,outputTokens=4,
                                      cacheReadTokens=7,cacheWriteTokens=2))
        self.assertEqual(usage['input_tokens'],10)
        self.assertEqual(usage['output_tokens'],4)
        self.assertEqual(usage['cached_input_tokens'],7)
        self.assertEqual(usage['cache_write_input_tokens'],2)


if __name__=='__main__':
    unittest.main()
