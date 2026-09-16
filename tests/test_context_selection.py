import hashlib
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/io-delegation/scripts'))
from context_selection import select, selected_job, validate_answer, merge_ranges
from io_delegate import DelegateError


def source(path, text):
    raw=text.encode();return dict(path=path,content=text,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))


class SelectionTests(unittest.TestCase):
    def test_merge_only_overlapping_or_adjacent(self):
        self.assertEqual(merge_ranges([[0,4],[3,5],[10,12],[5,7]]),[[0,7],[10,12]])

    def test_minified_selection_bytes(self):
        text='x'*90000+'RETRY_LIMIT=3;'+'y'*90000
        b=select([source('a.js',text)],[dict(path='a.js',select=dict(kind='literal',needle='RETRY_LIMIT',window=30))])
        self.assertLess(b['selected_bytes'],100);self.assertGreater(b['source_bytes'],180000)
        self.assertTrue(b['coverage']['sources']['s0']['partial'])

    def test_absence_does_not_dispatchable_ok(self):
        b=select([source('a.txt','present')],[dict(path='a.txt',select=dict(kind='literal',needle='ABSENT'))])
        self.assertEqual(b['status'],'insufficient_context');self.assertEqual(b['fragments'],[])

    def test_omitted_occurrences_declared(self):
        b=select([source('a.txt','X.'*100)],[dict(path='a.txt',select=dict(kind='literal',needle='X',window=0,max_regions=1))])
        self.assertEqual(b['coverage']['omitted_matches'],99)

    def test_python_symbol_retains_negation_imports_constants_decorators(self):
        text='import os\nLIMIT=3\n\n@decorator\ndef allowed(x):\n    if not x:\n        return False\n    return LIMIT\n\ndef unrelated():\n    return 99\n'
        b=select([source('a.py',text)],[dict(path='a.py',select=dict(kind='python_symbol',name='allowed'))])
        content='\n'.join(x['content'] for x in b['fragments'])
        for s in ['import os','LIMIT=3','@decorator','if not x','return False']:self.assertIn(s,content)
        self.assertNotIn('unrelated',content)
        self.assertTrue(b['coverage']['notes'])

    def test_method_keeps_containing_class(self):
        text='class A:\n    enabled=False\n    def f(self):\n        return self.enabled\n'
        b=select([source('a.py',text)],[dict(path='a.py',select=dict(kind='python_symbol',name='A.f'))])
        self.assertIn('enabled=False',b['fragments'][0]['content'])

    def test_python_ambiguous_symbol_abstains(self):
        text='def f(): pass\ndef f(): pass\n'
        b=select([source('a.py',text)],[dict(path='a.py',select=dict(kind='python_symbol',name='f'))])
        self.assertEqual(b['status'],'insufficient_context')

    def test_javascript_symbol_is_not_fake_ast(self):
        with self.assertRaises(ValueError):select([source('a.js','function f(){}')],[dict(path='a.js',select=dict(kind='python_symbol',name='f'))])

    def test_over_budget_fails_not_truncates(self):
        with self.assertRaises(ValueError):select([source('a.txt','x'*24001)],[dict(path='a.txt',select=dict(kind='span',start=0,end=24001))])

    def test_unicode_offsets_reference_original(self):
        b=select([source('a.txt','áé\nVALUE=3\nNOPE=9')],[dict(path='a.txt',select=dict(kind='lines',start=2,end=2))])
        obj=dict(status='ok',findings=[dict(path='r0',symbol='VALUE',evidence='VALUE=3',fact='Value is 3')],unknowns=[],read_paths=['r0'])
        r=validate_answer(json.dumps(obj),b)
        self.assertEqual(r['findings'][0]['start'],3)
        obj['findings'][0]['evidence']='NOPE=9'
        with self.assertRaises(DelegateError):validate_answer(json.dumps(obj),b)

    def test_no_evidence_across_gap(self):
        b=select([source('a.txt','ab-----cd')],[dict(path='a.txt',select=dict(kind='span',start=0,end=2)),dict(path='a.txt',select=dict(kind='span',start=7,end=9))])
        obj=dict(status='ok',findings=[dict(path='r0',symbol='x',evidence='abcd',fact='x')],unknowns=[],read_paths=['r0','r1'])
        with self.assertRaises(DelegateError):validate_answer(json.dumps(obj),b)

    def test_job_no_random_ids_or_full_corpus(self):
        b=select([source('a.txt','value\nHIDDEN')],[dict(path='a.txt',select=dict(kind='lines',start=1,end=1))])
        job=selected_job(b,'What value?')
        self.assertNotIn('HIDDEN',json.dumps(job));self.assertNotIn(b['sources']['s0']['sha256'],json.dumps(job))

    def test_bad_shapes_and_empty_ok(self):
        b=select([source('a.txt','value')],[dict(path='a.txt',select=dict(kind='span',start=0,end=5))])
        with self.assertRaises(ValueError):validate_answer('{"status":"ok","findings":[],"unknowns":[],"read_paths":["r0"]}',b)
        with self.assertRaises(ValueError):select([source('a.txt','value')],[dict(path='a.txt',select=dict(kind='span',start=False,end=4))])


if __name__=='__main__':unittest.main()
