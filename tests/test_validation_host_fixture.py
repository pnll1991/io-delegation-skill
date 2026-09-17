import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

BASE=Path(__file__).resolve().parents[1]/'benchmarks/v1-validation'
spec=importlib.util.spec_from_file_location('host_fixture',BASE/'host_fixture.py')
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
SUITE=BASE/'host_suite.fixture.json'

class HostFixtureTests(unittest.TestCase):
    def test_fixture_is_deterministic(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            a=mod.create(root/'a'); b=mod.create(root/'b')
            self.assertEqual(a['commit'],b['commit'])
            self.assertEqual(a['files'],['alpha.txt','beta.txt','delta.txt','gamma.txt'])

    def test_bulk_source_size_exceeds_local_threshold(self):
        total=sum(len(mod.FILES[name].encode()) for name in ('beta.txt','gamma.txt','delta.txt'))
        self.assertGreaterEqual(total,2048)

    def test_suite_covers_four_routes_read_only(self):
        data=json.loads(SUITE.read_text(encoding='utf-8'))
        self.assertEqual(data['version'],1)
        self.assertEqual(len(data['cases']),4)
        routes={case['expected_route'] for case in data['cases']}
        self.assertEqual(routes,{'deterministic','targeted_read','bulk_read','principal'})
        for case in data['cases']:
            self.assertIn('expected_json',case)
            self.assertNotIn('validator',case)
            self.assertIn('Do not use built-in file, shell, web, or subagent tools.',case['prompt'])

    def test_fixture_contains_no_secrets_or_machine_paths(self):
        text=SUITE.read_text(encoding='utf-8')
        self.assertNotIn('D:\\',text)
        self.assertNotIn('TYPESAFE_API_KEY',text)
        self.assertFalse(any(name.startswith('.') for name in mod.FILES))

if __name__=='__main__': unittest.main()
