import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

BASE=Path(__file__).resolve().parents[1]/'benchmarks/v1-validation'
spec=importlib.util.spec_from_file_location('host_parity_real',BASE/'host_parity_real.py')
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class HostParityRealTests(unittest.TestCase):
    def test_model_mapping_is_host_scoped(self):
        self.assertEqual(mod.parse_models(['codex=a','claude=b']),{'codex':'a','claude':'b'})
        with self.assertRaises(ValueError): mod.parse_models(['unknown=x'])
        with self.assertRaises(ValueError): mod.parse_models(['codex=a','codex=b'])

    def test_security_aggregate_requires_all_three_clean_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for host in mod.HOSTS:
                path=root/host; path.mkdir()
                (path/'security.json').write_text(json.dumps({
                    'schema':'io-context-security-audit/v1','clean':True,
                    'secret_hits':[],'forbidden_event_fields':[],'malformed_jsonl':[]}),encoding='utf-8')
            out=mod.aggregate_security(root)
            self.assertTrue(out['clean']); self.assertEqual(out['missing_host_reports'],[])
            (root/'cursor/security.json').unlink()
            out=mod.aggregate_security(root)
            self.assertFalse(out['clean']); self.assertEqual(out['missing_host_reports'],['cursor'])

    def test_security_aggregate_preserves_host_attribution_without_secret_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for host in mod.HOSTS:
                path=root/host; path.mkdir()
                hit=[] if host!='claude' else [{'env':'FAKE_KEY','path':'x'}]
                (path/'security.json').write_text(json.dumps({
                    'schema':'io-context-security-audit/v1','clean':not hit,
                    'secret_hits':hit,'forbidden_event_fields':[],'malformed_jsonl':[]}),encoding='utf-8')
            out=mod.aggregate_security(root)
            self.assertFalse(out['clean'])
            self.assertEqual(out['secret_hits'][0]['host'],'claude')
            self.assertEqual(out['secret_hits'][0]['env'],'FAKE_KEY')

if __name__=='__main__': unittest.main()
