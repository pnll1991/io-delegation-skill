"""Integración HTTP con servidor stub en loopback; nunca llama a un modelo."""
from __future__ import annotations
import contextlib
import http.server
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
from test_io_delegate import d


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers['Content-Length'])
        self.server.received = json.loads(self.rfile.read(length))
        self.server.auth = self.headers.get('Authorization')
        self.send_response(self.server.status)
        if self.server.status == 302:
            self.send_header('Location', 'https://example.invalid/must-not-follow')
        self.end_headers()
        self.wfile.write(json.dumps(self.server.reply).encode())

    def log_message(self, *args):
        pass


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.status = 200
        self.server.reply = {'choices': [{'message': {'content': 'worker response'},
                                         'finish_reason': 'stop'}],
                             'usage': {'prompt_tokens': 44, 'completion_tokens': 5}}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup_server)
        self.cfg = {'adapter': 'chat-completions', 'model': 'stub-not-real-model',
                    'url': f'http://127.0.0.1:{self.server.server_port}/v1/chat/completions'}
        self.job = d.build_job('bulk-read', 'Synthetic question', [])

    def cleanup_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_http_request_shape_and_usage(self):
        output, meta = d.invoke(self.job, self.cfg, self.root)
        self.assertEqual(output, 'worker response')
        self.assertEqual(meta['usage']['input_tokens'], 44)
        self.assertFalse(self.server.received['stream'])
        self.assertNotIn('tools', self.server.received)
        self.assertEqual(self.server.received['model'], 'stub-not-real-model')
        self.assertEqual(self.server.received['max_tokens'], 1600)

    def test_alternative_token_limit_and_no_temperature(self):
        self.cfg.update(token_limit_field='max_completion_tokens', temperature=None)
        d.invoke(self.job, self.cfg, self.root)
        self.assertIn('max_completion_tokens', self.server.received)
        self.assertNotIn('temperature', self.server.received)
        self.assertNotIn('max_tokens', self.server.received)

    def test_environment_key_used_only_as_header(self):
        self.cfg['api_key_env'] = 'TEST_SYNTHETIC_KEY'
        with mock.patch.dict('os.environ', {'TEST_SYNTHETIC_KEY': 'synthetic-test-value'}):
            output, meta = d.invoke(self.job, self.cfg, self.root)
        self.assertEqual(self.server.auth, 'Bearer synthetic-test-value')
        self.assertNotIn('synthetic-test-value', json.dumps(self.server.received))
        self.assertNotIn('synthetic-test-value', json.dumps(meta))

    def test_truncated_generation_rejected(self):
        self.server.reply['choices'][0]['finish_reason'] = 'length'
        with self.assertRaises(d.DelegateError):
            d.invoke(self.job, self.cfg, self.root)

    def test_tool_call_response_rejected(self):
        self.server.reply['choices'][0]['finish_reason'] = 'tool_calls'
        with self.assertRaises(d.DelegateError):
            d.invoke(self.job, self.cfg, self.root)

    def test_missing_choices_rejected(self):
        self.server.reply = {'other': 'private'}
        with self.assertRaises(d.DelegateError):
            d.invoke(self.job, self.cfg, self.root)

    def test_redirect_not_followed(self):
        self.server.status = 302
        with self.assertRaises(d.DelegateError):
            d.invoke(self.job, self.cfg, self.root)

    def test_response_limit(self):
        self.server.reply['choices'][0]['message']['content'] = 'x' * (d.MAX_RESPONSE_BYTES + 1)
        with self.assertRaises(d.DelegateError):
            d.invoke(self.job, self.cfg, self.root)

    def test_http_unauthorized(self):
        self.server.status = 401
        with self.assertRaises(urllib.error.HTTPError):
            d.invoke(self.job, self.cfg, self.root)

    def test_malformed_response_shapes_are_controlled(self):
        for reply in [[], None, {"choices": "bad"}, {"choices": ["bad"]},
                      {"choices": [{"finish_reason": "stop", "message": "bad"}]}]:
            with self.subTest(reply=reply):
                self.server.reply = reply
                with self.assertRaises(d.DelegateError):
                    d.invoke(self.job, self.cfg, self.root)
