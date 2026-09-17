import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'benchmarks/v1-validation'
SPEC=importlib.util.spec_from_file_location('host_fixture',BASE/'host_fixture.py')
fixture=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(fixture)
SUITE=json.loads((BASE/'host_suite.fixture.json').read_text(encoding='utf-8'))

class HostFixtureTests(unittest.TestCase):
    def test_fixture_commit_is_deterministic(self):
        with tempfile.TemporaryDirectory() as folder:
            first=fixture.create(Path(folder)/'a')
            second=fixture.create(Path(folder)/'b')
        self.assertEqual(first['commit'],second['commit'])
        self.assertEqual(first['files'],['alpha.txt','beta.txt','delta.txt','gamma.txt'])

    def test_bulk_sources_cross_local_threshold(self):
        total=sum(len(fixture.FILES[name].encode()) for name in ('beta.txt','gamma.txt','delta.txt'))
        self.assertGreaterEqual(total,2048)

    def test_suite_covers_exact_four_routes(self):
        self.assertEqual(len(SUITE['cases']),4)
        self.assertEqual({x['expected_route'] for x in SUITE['cases']},
                         {'deterministic','targeted_read','principal','bulk_read'})
    def test_suite_is_read_only_json_contract(self):
        for case in SUITE['cases']:
            with self.subTest(case=case['id']):
                self.assertIsInstance(case.get('expected_json'),dict)
                self.assertNotIn('validator',case)
                self.assertIn('Do not use built-in file, shell, web, or subagent tools.',case['prompt'])
                if case['expected_route']=='deterministic':
                    self.assertIn('io_context.search',case['prompt'])
                else:
                    self.assertIn('io_context.query',case['prompt'])

    def test_fixture_contains_no_credentials_or_sensitive_paths(self):
        text='\n'.join(fixture.FILES.values())
        self.assertNotIn('KEY=',text)
        self.assertNotIn('TOKEN=',text)
        self.assertNotIn('.env',text)

if __name__=='__main__': unittest.main()
