from __future__ import annotations
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/io-delegation/scripts/decision_router.py'
spec = importlib.util.spec_from_file_location('decision_router', SCRIPT)
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)


def config(**changes):
    return {"version": 1, "approved": True, "provider": "typesafe",
            "model": "jev-latest", "api_key_env": "TYPESAFE_API_KEY",
            "timeout_seconds": 10.0, "min_confidence": 0.75, **changes}


def response(confidence=0.91, choice='bulk_read'):
    probs = {"deterministic": 0.02, "targeted_read": 0.05, "bulk_read": 0.9, "principal": 0.03}
    if choice != 'bulk_read':
        probs = {k: 0.03 for k in router.ROUTES}
        probs[choice] = 0.91
    return {"model": "jev-latest", "answers": {
        "route": {"type": "choice", "choice": choice, "probabilities": probs, "confidence": confidence},
        "delegation_useful": {"type": "noul", "noul": 0.94},
        "reasoning_required": {"type": "noul", "noul": 0.08},
    }, "usage": {"input_tokens": 123, "output_tokens": 7}}


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'a.py').write_text('TOP_SECRET_SOURCE\n' * 10)
        (self.root / 'b.js').write_text('other\n' * 100)

    def test_state_sends_metadata_not_source_or_file_names(self):
        state = router.build_state(self.root, 'Locate worker initialization', ['a.py', 'b.js'])
        encoded = json.dumps(state)
        self.assertNotIn('TOP_SECRET_SOURCE', encoded)
        self.assertNotIn('a.py', encoded)
        self.assertNotIn('b.js', encoded)
        self.assertEqual(state['corpus']['file_count'], 2)
        self.assertEqual(state['corpus']['extensions'], {'.py': 1, '.js': 1})

    def test_sensitive_file_rejected(self):
        (self.root / '.env').write_text('KEY=secret')
        with self.assertRaises(router.RouterError):
            router.build_state(self.root, 'task', ['.env'])

    def test_parse_valid_response(self):
        row = router.parse_response(response(), config())
        self.assertEqual(row['route'], 'bulk_read')
        self.assertEqual(row['confidence'], 0.91)
        self.assertEqual(row['usage']['input_tokens'], 123)

    def test_low_confidence_falls_back_to_current_rules(self):
        row = router.parse_response(response(confidence=0.5), config())
        self.assertEqual(row['model_route'], 'bulk_read')
        self.assertEqual(row['route'], 'current_rules')

    def test_unknown_choice_rejected(self):
        bad = response()
        bad['answers']['route']['choice'] = 'delete_everything'
        with self.assertRaises(router.RouterError):
            router.parse_response(bad, config())

    def test_probability_shape_rejected(self):
        bad = response()
        bad['answers']['route']['probabilities'].pop('principal')
        with self.assertRaises(router.RouterError):
            router.parse_response(bad, config())

    def test_custom_remote_endpoint_rejected(self):
        with self.assertRaises(router.RouterError):
            router.endpoint({"url": "https://example.com/router"})

    def test_loopback_endpoint_allowed_for_tests(self):
        self.assertEqual(router.endpoint({"url": "http://127.0.0.1:8765/mock"}),
                         'http://127.0.0.1:8765/mock')

    def test_config_requires_explicit_approval(self):
        p = self.root / 'router.json'
        p.write_text(json.dumps({"version": 1, "approved": False}))
        with self.assertRaises(router.RouterError):
            router.load_config(str(p))

    def test_missing_api_key_fails_before_network(self):
        payload = router.build_payload(router.build_state(self.root, 'task', ['a.py']), config())
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(router.RouterError):
            router.call_typesafe(payload, config())

    def test_request_contains_metadata_only_and_bearer_auth(self):
        state = router.build_state(self.root, 'Locate worker initialization', ['a.py', 'b.js'])
        payload = router.build_payload(state, config())
        body = json.dumps(response()).encode()

        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, limit):
                return body

        class FakeOpener:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return FakeResponse()

        fake = FakeOpener()
        with mock.patch.dict(os.environ, {'TYPESAFE_API_KEY': 'secret-key'}, clear=True), \
             mock.patch.object(router.urllib.request, 'build_opener', return_value=fake):
            row = router.call_typesafe(payload, config())
        sent = fake.request.data.decode()
        self.assertNotIn('TOP_SECRET_SOURCE', sent)
        self.assertNotIn('a.py', sent)
        self.assertEqual(fake.request.get_header('Authorization'), 'Bearer secret-key')
        self.assertEqual(row['route'], 'bulk_read')

    def test_dry_run_never_requires_api_key(self):
        cfg_path = self.root / 'router.json'
        cfg_path.write_text(json.dumps({"version": 1, "approved": True}))
        out = io.StringIO()
        with mock.patch('sys.stdout', out):
            code = router.main(['--root', str(self.root), '--config', str(cfg_path),
                                '--task', 'task', '--paths', 'a.py', '--dry-run'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())['status'], 'dry_run')


if __name__ == '__main__':
    unittest.main()
