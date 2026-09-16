import importlib.util
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/io-delegation/scripts'))
from context_selection import select, selected_job
from context_sources import encoded
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('micro',ROOT/'benchmarks/context/microbench.py')
micro=importlib.util.module_from_spec(spec);spec.loader.exec_module(micro)


class PrefixTests(unittest.TestCase):
    def bundle(self):
        sources=[dict(path='a.txt',content='VALUE=3\n',sha256='abc',bytes=8)]
        return select(sources,[dict(path='a.txt',select=dict(kind='lines',start=1,end=1))])

    def test_variable_question_after_stable_sources(self):
        a=selected_job(self.bundle(),'What value?')['messages']
        b=selected_job(self.bundle(),'What symbol?')['messages']
        self.assertEqual(a[0],b[0])
        self.assertEqual(a[1]['content'].split('"task":')[0],b[1]['content'].split('"task":')[0])
        self.assertLess(a[1]['content'].find('VALUE=3'),a[1]['content'].find('"task":'))

    def test_prompt_has_no_incidental_workspace_identity(self):
        text=encoded(selected_job(self.bundle(),'What value?')).decode()
        self.assertNotIn('/tmp/',text);self.assertNotIn('audit_id',text);self.assertNotIn('call_id',text)
        self.assertNotIn('sha256',text)

    def test_selector_order_does_not_change_fragments(self):
        source=[dict(path='a.txt',content='ABCDEF',sha256='abc',bytes=6)]
        rs=[dict(path='a.txt',select=dict(kind='span',start=0,end=2)),dict(path='a.txt',select=dict(kind='span',start=4,end=6))]
        self.assertEqual(selected_job(select(source,rs),'Q?'),selected_job(select(source,list(reversed(rs))),'Q?'))

    def test_microcycle_is_explicit_and_does_not_mutate_input(self):
        q=dict(selections=[],question='One?')
        phases=micro.sequence(q,'cycle','Two?')
        self.assertEqual([x[0] for x in phases],['cold','repeat-exact','changed-question'])
        self.assertEqual(phases[0][1],phases[1][1]);self.assertEqual(q['question'],'One?')
        self.assertEqual(phases[2][1]['question'],'Two?')
        for other in (None,'','One?'):
            with self.assertRaises(ValueError):micro.sequence(q,'cycle',other)


if __name__=='__main__':unittest.main()
