import copy
import json
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error

from aad.io import read_json, save_json
from aad.provider import BudgetExhausted
from aad.v5_glm_provider import ENDPOINT, MODEL, GLM5Provider, GLMTransportError
from aad.v5_provider import V5Provider
from aad.glm_stream import collect_stream
from scripts.v5_glm52_128 import verify_protocol, MAX_CALLS

CODE = 'int main(){}\n// AAD_V5_COMPLETE'


class Response:
    def __init__(self, model=MODEL, finish='stop'):
        self.payload = {'model': model, 'choices': [{'finish_reason': finish,
            'message': {'content': CODE}}], 'usage': {'total_tokens': 20}}
        chunk = {'model': model, 'choices': [{'index': 0, 'finish_reason': finish,
            'delta': {'content': CODE, 'reasoning_content': 'test reasoning'}}], 'usage': {'total_tokens': 20}}
        self.stream = io.BytesIO(('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode())

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, *args):
        return json.dumps(self.payload).encode()

    def readline(self, *args):
        return self.stream.readline(*args)


class GLMTests(unittest.TestCase):
    def test_prompt_and_request_parity_except_model(self):
        for kind in ('program', 'edit'):
            with tempfile.TemporaryDirectory() as tmp:
                bodies = []
                for provider_type, model in ((V5Provider, 'deepseek-flash'), (GLM5Provider, MODEL)):
                    with patch('urllib.request.urlopen', return_value=Response(model)) as http:
                        provider = provider_type(Path(tmp) / model, model, 1, 0, key='test-secret')
                        provider.request(kind, {'instruction': 'test', 'operation': 'independent'})
                        req = http.call_args.args[0]
                        body = json.loads(req.data)
                        body.pop('model')
                        if model == MODEL:
                            self.assertTrue(body.pop('stream'))
                        bodies.append(body)
                        if model == MODEL:
                            self.assertEqual(req.full_url, ENDPOINT)
                            self.assertEqual(provider.ledger[0]['response_model'], MODEL)
                self.assertEqual(*bodies)

    def test_cap_includes_failed_calls_no_hidden_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            error = urllib.error.HTTPError(ENDPOINT, 401, 'Unauthorized', {}, None)
            with patch('urllib.request.urlopen', side_effect=error) as http:
                provider = GLM5Provider(tmp, MODEL, 1, 0, key='test-secret')
                with self.assertRaises(GLMTransportError):
                    provider.request('program', {})
                with self.assertRaises(BudgetExhausted):
                    provider.request('program', {})
                self.assertEqual(http.call_count, 1)
                self.assertEqual(provider.ledger[0]['status'], 'http_401')
                for path in Path(tmp).rglob('*.json'):
                    self.assertNotIn('test-secret', path.read_text())

    def test_budget_128_no_129th_request(self):
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.urlopen') as http:
            provider = GLM5Provider(tmp, MODEL, MAX_CALLS, 0, key='test')
            provider.ledger = [{'id': i, 'status': 'ok', 'reserved_tokens': 1} for i in range(128)]
            with self.assertRaises(BudgetExhausted):
                provider.request('program', {})
            http.assert_not_called()

    def test_ledger_saved_before_http_and_success_recoverable(self):
        with tempfile.TemporaryDirectory() as tmp:
            def transport(*args, **kwargs):
                self.assertEqual(read_json(Path(tmp) / 'llm/ledger.json')[0]['status'], 'started')
                return Response()
            with patch('urllib.request.urlopen', side_effect=transport):
                GLM5Provider(tmp, MODEL, 128, 0, key='test').request('program', {})
            resumed = GLM5Provider(tmp, MODEL, 128, 0, key='test')
            self.assertEqual(resumed.requests, 1)
            self.assertEqual(resumed.ledger[0]['status'], 'ok')
            self.assertTrue((Path(tmp) / 'llm/00000.response.json').exists())

    def test_model_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.urlopen', return_value=Response('glm-5.3')):
            provider = GLM5Provider(tmp, MODEL, 128, 0, key='test')
            with self.assertRaises(GLMTransportError):
                provider.request('program', {})
            self.assertEqual(provider.ledger[0]['status'], 'model_mismatch')

    def test_incomplete_response_counted_and_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.urlopen', return_value=Response(finish='length')):
            provider = GLM5Provider(tmp, MODEL, 128, 0, key='test')
            with self.assertRaises(ValueError):
                provider.request('program', {})
            self.assertEqual(provider.ledger[0]['status'], 'invalid_response')
            self.assertEqual(provider.requests, 1)

    def test_network_error_stops_and_counts(self):
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.urlopen', side_effect=TimeoutError):
            provider = GLM5Provider(tmp, MODEL, 128, 0, key='test')
            with self.assertRaises(GLMTransportError):
                provider.request('program', {})
            self.assertEqual(provider.ledger[0]['status'], 'network_error')

    def test_only_reads_zhipu_key(self):
        with tempfile.TemporaryDirectory() as tmp, patch('aad.v5_glm_provider.environment', return_value=None) as env:
            with self.assertRaisesRegex(ValueError, 'ZHIPU_API_KEY'):
                GLM5Provider(tmp, MODEL, 128, 0)
            env.assert_called_once_with('ZHIPU_API_KEY')

    def test_protocol_only_allows_model_and_budget_difference(self):
        train = [SimpleNamespace(name='a', fingerprint='hash-a')]
        val = [SimpleNamespace(name='b', fingerprint='hash-b')]
        config = {'model': MODEL, 'calls': 128, 'repeats': 3}
        reference = {'version': 5, 'config': {'model': 'deepseek-flash', 'calls': 256, 'repeats': 3},
                     'inputs': {'a': 'hash-a', 'b': 'hash-b'}, 'train': ['a'], 'validation': ['b']}
        verify_protocol(config, train, val, reference)
        for field, value in (('repeats', 1), ('calls', 256), ('model', 'glm-5.3')):
            changed = copy.deepcopy(config)
            changed[field] = value
            with self.assertRaises(ValueError):
                verify_protocol(changed, train, val, reference)
        self.assertEqual(reference['config']['calls'], 256)

    def test_stream_disconnection_rejected_and_progress_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            response = Response()
            raw = response.stream.getvalue().split(b'data: [DONE]')[0]
            with self.assertRaisesRegex(OSError, 'disconnected'):
                collect_stream(io.BytesIO(raw), Path(tmp), 0, 'test-secret')
            self.assertEqual(read_json(Path(tmp) / '00000.progress.json')['phase'], 'interrupted')
            self.assertFalse((Path(tmp) / '00000.response.json').exists())

    def test_stream_heartbeat_separate_usage_and_content(self):
        chunks = [{'model': MODEL, 'choices': [{'delta': {'reasoning_content': 'think'}}]},
                  {'choices': [{'delta': {'content': CODE}, 'finish_reason': 'stop'}]},
                  {'choices': [], 'usage': {'total_tokens': 99}}]
        raw = ': keepalive\n\n' + ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
        with tempfile.TemporaryDirectory() as tmp:
            result = collect_stream(io.BytesIO(raw.encode()), Path(tmp), 0, 'test-secret')
            self.assertEqual(result['choices'][0]['message']['content'], CODE)
            self.assertEqual(result['choices'][0]['message']['reasoning_content'], 'think')
            self.assertEqual(result['usage']['total_tokens'], 99)
            self.assertEqual(read_json(Path(tmp) / '00000.progress.json')['phase'], 'complete')

    def test_stream_error_redacts_secret(self):
        raw = b'data: {"error": {"message": "test-secret"}}\n\n'
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OSError) as caught:
                collect_stream(io.BytesIO(raw), Path(tmp), 0, 'test-secret')
            self.assertNotIn('test-secret', str(caught.exception))

    def test_stream_done_requires_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(OSError, 'finish_reason'):
                collect_stream(io.BytesIO(b'data: [DONE]\n\n'), Path(tmp), 0, 'test-secret')


if __name__ == '__main__':
    unittest.main()
