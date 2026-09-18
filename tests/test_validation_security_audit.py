import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE=Path(__file__).resolve().parents[1]/'benchmarks/v1-validation'
record_spec=importlib.util.spec_from_file_location('record',BASE/'record.py')
record=importlib.util.module_from_spec(record_spec); record_spec.loader.exec_module(record); sys.modules['record']=record
spec=importlib.util.spec_from_file_location('security_audit',BASE/'security_audit.py')
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class SecurityAuditTests(unittest.TestCase):
    def test_fake_secret_hit_never_echoes_secret(self):
        secret='FAKE_SECRET_123456'
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'state.json').write_text('{"x":"'+secret+'"}',encoding='utf-8')
            with patch.dict(os.environ,{'FAKE_KEY':secret},clear=False):
                result=mod.audit([root],['FAKE_KEY'])
            self.assertFalse(result['clean']); self.assertEqual(result['secret_hits'][0]['env'],'FAKE_KEY')
            self.assertNotIn(secret,json.dumps(result))

    def test_forbidden_context_content_field_is_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            row={'schema':'io-context/v1','operation_id':'x','event':'operation_completed','operation':'query','content':'raw source'}
            (root/'context-events.jsonl').write_text(json.dumps(row)+'\n',encoding='utf-8')
            result=mod.audit([root])
            self.assertFalse(result['clean'])
            self.assertTrue(any(item['key']=='content' for item in result['forbidden_event_fields']))

    def test_clean_metadata_event_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            row={'schema':'io-context/v1','operation_id':'x','event':'operation_completed','operation':'query','source_bytes':10,'selected_bytes':5,'result_bytes':4}
            (root/'context-events.jsonl').write_text(json.dumps(row)+'\n',encoding='utf-8')
            result=mod.audit([root])
            self.assertTrue(result['clean'])

    def test_malformed_runs_jsonl_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'runs.jsonl').write_text('{bad\n',encoding='utf-8')
            result=mod.audit([root])
            self.assertFalse(result['clean']); self.assertEqual(result['malformed_jsonl'][0]['records'],1)

if __name__=='__main__': unittest.main()
