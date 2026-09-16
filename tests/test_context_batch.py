import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/io-delegation/scripts'))
from context_mcp import ContextService
from context_selection import grouped_question


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        base=Path(self.temp.name);root=base/'p';root.mkdir();audit=base/'audit';audit.mkdir()
        (root/'x.html').write_text('<title>X</title>\n<h1>A<br>B</h1>')
        self.service=ContextService(root,audit,files=['x.html'])

    def test_batch_reads_once_and_source_table_once(self):
        projections=[dict(kind='html',fields=['title','h1']),dict(kind='lines',start=1,end=1)]
        with patch.object(self.service.scope,'load',wraps=self.service.scope.load) as load,patch('io_delegate.invoke',side_effect=AssertionError('model')):
            r=self.service.call('extract',dict(paths=['x.html'],projections=projections))
        self.assertFalse(r['isError']);self.assertEqual(load.call_count,2) # load + unchanged check, not each projection
        data=json.loads(r['content'][0]['text']);self.assertEqual(len(data['sources']),1)
        self.assertEqual(data['data'][0]['rows'][0]['values']['h1'],'A B')
        self.assertEqual(data['data'][1]['rows'][0]['text'],'<title>X</title>\n')

    def test_bad_batch_never_calls_model(self):
        p=dict(kind='html',fields=['title'])
        for args in [dict(projections=[]),dict(projections=[p,p]),dict(projection=p,projections=[p]),dict(projections=[p]*9)]:
            r=self.service.call('extract',dict(paths=['x.html'],**args));self.assertTrue(r['isError'])

    def test_group_questions_bounded(self):
        q=grouped_question(['Where is the limit?','Which handler reads it?'])
        self.assertIn('Q1:',q);self.assertIn('Q2:',q)
        for value in ([],['x']*2,['x']*5,['x'*801],['']):
            with self.assertRaises(ValueError):grouped_question(value)

    def test_same_raw_hash_not_repeated_per_projection(self):
        p=dict(kind='html',fields=['title'])
        r=json.loads(self.service.call('extract',dict(paths=['x.html'],projections=[p,dict(kind='html',fields=['h1'])]))['content'][0]['text'])
        h=r['sources']['s0']['sha256'];self.assertEqual(json.dumps(r).count(h),1)


if __name__=='__main__':unittest.main()
