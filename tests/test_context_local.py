"""Deterministic v0.4 contracts; no inference or credentials required."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'skills/io-delegation/scripts'))
from context_mcp import ContextService, serve, tools, codex_arguments
from context_sources import SourceScope, glob_matches
from context_ops import project


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.root = self.base/'project'; self.root.mkdir()
        self.audit = self.base/'audit'; self.audit.mkdir()
        (self.root/'src').mkdir()
        self.write('src/page.html', '<title>A &amp;amp; B</title><h1>Uno<br><em>dos</em></h1><h1>Other</h1>')
        self.service = ContextService(self.root, self.audit, prefixes=['src'])

    def write(self, name, text):
        p = self.root/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text, encoding='utf-8')

    def call(self, name, **args):
        with patch('io_delegate.invoke', side_effect=AssertionError('deterministic operation dispatched a model')):
            return json.loads(self.service.call(name, args)['content'][0]['text'])

    def test_html_entities_once_br_and_first_h1(self):
        r = self.call('extract', paths=['src/page.html'], projection=dict(kind='html', fields=['title','h1','description']))
        self.assertEqual(r['data'][0]['values'], dict(title='A &amp; B', h1='Uno dos', description=None))
        self.assertEqual(r['data'][0]['duplicates'], {'h1':2}); self.assertEqual(r['status'], 'partial')
        self.assertEqual(r['model_calls'], 0)

    def test_html_no_scripts_comments_or_template_text_inside_h1(self):
        r=project('<h1>A<!--bad--><script>bad()</script><style>x</style><template>x</template>B</h1>', dict(kind='html',fields=['h1']))
        self.assertEqual(r['values']['h1'], 'AB')

    def test_html_empty_is_not_missing(self):
        r=project('<title></title>', dict(kind='html',fields=['title','h1']))
        self.assertEqual(r['values']['title'], ''); self.assertEqual(r['missing'], ['h1'])

    def test_html_self_closed_br_and_attrs(self):
        r=project('<h1>A<br/>B</h1><link rel="alternate canonical" href="/x?a=1&amp;b=2"><meta name="description" content="x &amp;amp; y">',dict(kind='html',fields=['h1','canonical','description']))
        self.assertEqual(r['values'], dict(h1='A B',canonical='/x?a=1&b=2',description='x &amp; y'))

    def test_html_bad_projection_rejected(self):
        for p in (dict(kind='html',fields=['wat']),dict(kind='html',fields=['h1','h1']),dict(kind='html',fields=['h1'],command='run')):
            with self.assertRaises(ValueError): project('<h1>X</h1>',p)

    def test_json_pointer_null_zero_false_and_escaping(self):
        text='{"a/b":{"~k":[0,false,null]},"":7}'
        r=project(text,dict(kind='json',pointers=['/a~1b/~0k/0','/a~1b/~0k/1','/a~1b/~0k/2','/none','/']))
        self.assertEqual(list(r['values'].values()),[0,False,None,None,7]);self.assertEqual(r['missing'],['/none'])

    def test_json_duplicates_and_nonfinite_rejected(self):
        for text in ('{"a":1,"a":2}', '{"a":NaN}'):
            with self.assertRaises(ValueError): project(text,dict(kind='json',pointers=['/a']))

    def test_bad_pointer_rejected(self):
        for p in ('foo','/~x'):
            with self.assertRaises(ValueError): project('{}',dict(kind='json',pointers=[p]))

    def test_bom_and_crlf(self):
        (self.root/'src/a.json').write_bytes(b'\xef\xbb\xbf{\r\n"a":1\r\n}')
        r=self.call('extract',paths=['src/a.json'],projection=dict(kind='json',pointers=['/a']))
        self.assertEqual(r['data'][0]['values']['/a'],1)

    def test_scoped_glob_includes_zero_directories(self):
        self.write('src/deep/page.html','<title>D</title>')
        r=self.call('extract',paths=['src/**/page.html'],projection=dict(kind='html',fields=['title']))
        self.assertEqual(len(r['data']),2)
        self.assertTrue(glob_matches('src/page.html','src/**/page.html'))
        self.assertFalse(glob_matches('src2/page.html','src/**/page.html'))

    def test_discovery_only_no_content(self):
        r=self.call('search',paths=['src/*.html'])
        self.assertNotIn('Uno',json.dumps(r));self.assertEqual(r['match_count'],1)

    def test_minified_output_bounded_with_exact_count(self):
        self.write('src/min.html', 'prefix'+'MATCH'*9000)
        r=self.call('search',paths=['src/min.html'],needle='MATCH',max_matches=2,window=8)
        self.assertEqual(r['match_count'],9000);self.assertEqual(r['omitted'],8998)
        self.assertLess(len(json.dumps(r)),2000)

    def test_no_match_is_only_explicit_scope(self):
        r=self.call('search',paths=['src/page.html'],needle='ABSENT')
        self.assertEqual(r['match_count'],0);self.assertIn('only',r['coverage']['scope'])

    def test_limits_and_boolean_rejected(self):
        for args in ({'max_matches':True},{'window':-1},{'needle':''}):
            r=self.call('search',paths=['src/page.html'],**args);self.assertEqual(r['status'],'error')

    def test_spans_and_lines_use_same_normalized_text(self):
        (self.root/'src/a.txt').write_bytes(('\u00e1\r\nb\r\nc').encode('utf-8'))
        r=self.call('extract',paths=['src/a.txt'],projection=dict(kind='lines',start=2,end=2))
        self.assertEqual(r['data'][0]['text'],'b\n');self.assertEqual(r['data'][0]['start'],2)
        r=self.call('extract',paths=['src/a.txt'],projection=dict(kind='span',start=0,end=1))
        self.assertEqual(r['data'][0]['text'],'á')

    def test_source_budget_and_output_budget(self):
        self.write('src/big.txt','x'*490_000)
        r=self.call('extract',paths=['src/big.txt'],projection=dict(kind='lines',start=1,end=1))
        self.assertEqual(r['status'],'budget_exceeded')
        self.write('src/too-big.txt','x'*500_001)
        self.assertEqual(self.call('search',paths=['src/too-big.txt'])['status'],'error')

    def test_paths_and_secrets_rejected(self):
        self.write('src/.env','SECRET=private')
        self.write('other.txt','private')
        for path in ['../other.txt','/etc/passwd','C:/secret','src/page.html:stream','other.txt','src/.env','src/./page.html']:
            with self.subTest(path=path):
                self.assertEqual(self.call('search',paths=[path])['status'],'error')

    def test_glob_does_not_publish_secret_name(self):
        self.write('src/.env','SECRET=private')
        r=self.call('search',paths=['src/*'])
        self.assertNotIn('.env',json.dumps(r))

    def test_symlink_rejected(self):
        try: (self.root/'src/link').symlink_to(self.root/'src/page.html')
        except OSError: self.skipTest('symlink not available')
        self.assertEqual(self.call('search',paths=['src/link'])['status'],'error')

    def test_unauthorized_ancestor_or_neighbor(self):
        self.write('src2/a.txt','private')
        self.assertEqual(self.call('search',paths=['src2/*'])['status'],'error')
        with self.assertRaises(ValueError): ContextService(self.root,self.root,prefixes=['src'])
        with self.assertRaises(ValueError): SourceScope(self.root)

    def test_unknown_fields_rejected_no_model(self):
        r=self.call('search',paths=['src/page.html'],provider='remote');self.assertEqual(r['status'],'error')

    def test_telemetry_contains_no_corpus(self):
        self.call('search',paths=['src/page.html'],needle='Uno')
        log=(self.audit/'context-events.jsonl').read_text()
        for value in ('Uno','page.html','<title>'): self.assertNotIn(value,log)

    def test_tools_only_local_when_unconfigured(self):
        self.assertEqual([t['name'] for t in tools(self.service)],['search','extract'])

    def test_tool_schema_guides_valid_projection_shapes(self):
        extract_tool=next(t for t in tools(self.service) if t['name']=='extract')
        variants=extract_tool['inputSchema']['properties']['projection']['oneOf']
        by_kind={v['properties']['kind']['enum'][0]:v for v in variants}
        self.assertEqual(set(by_kind),{'html','json','lines','span'})
        self.assertEqual(by_kind['html']['properties']['fields']['items']['enum'],['title','h1','canonical','description'])
        self.assertEqual(by_kind['html']['required'],['kind','fields'])
        self.assertEqual(by_kind['json']['required'],['kind','pointers'])
        self.assertEqual(by_kind['lines']['required'],['kind','start','end'])

    def test_stdio_real_server(self):
        cmd=[sys.executable,'-I',str(ROOT/'skills/io-delegation/scripts/context_mcp.py'),'--root',str(self.root),'--audit-root',str(self.audit),'--allow-prefix','src']
        requests=[dict(jsonrpc='2.0',id=1,method='initialize',params={}),dict(jsonrpc='2.0',id=2,method='tools/call',params=dict(name='extract',arguments=dict(paths=['src/page.html'],projection=dict(kind='html',fields=['title']))))]
        cp=subprocess.run(cmd,input='\n'.join(map(json.dumps,requests))+'\n',capture_output=True,text=True,timeout=10)
        self.assertEqual(cp.returncode,0,cp.stderr)
        reply=json.loads(cp.stdout.splitlines()[-1]);self.assertFalse(reply['result']['isError'])

    def test_request_cap_and_uninitialized(self):
        out=io.BytesIO();self.assertEqual(serve(self.service,io.BytesIO(b'x'*64001),out),2)
        out=io.BytesIO();serve(self.service,io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'),out)
        self.assertIn('error',json.loads(out.getvalue()))

    def test_startup_settings_no_permission_bypass(self):
        args=codex_arguments(self.root,self.audit,prefixes=['src'])
        self.assertNotIn('bypass',' '.join(args));self.assertNotIn('danger-full-access',' '.join(args))


if __name__=='__main__': unittest.main()
